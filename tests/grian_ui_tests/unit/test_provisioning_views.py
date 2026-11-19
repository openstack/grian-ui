#
# SPDX-License-Identifier: Apache-2.0

import json
import re
import time

from unittest import mock

from django.core.cache import cache
from django.template.loader import get_template
from django.test import RequestFactory
from django.test import override_settings
from django.urls import NoReverseMatch

from grian_ui import config
from grian_ui import provisioning
from grian_ui import provisioning_inventory
from grian_ui.content.provisioning import views
from grian_ui.datasource import fake
from grian_ui.datasource import fake_provisioning
from grian_ui.local.enabled import _1006_admin_provisioning_panel as enabled
from grian_ui_tests.unit import base


VIEWS = "grian_ui.content.provisioning.views"
#: The real views.chh_url (_ViewTestCase patches it).
_REAL_CHH_URL = views.chh_url


def _boot(i, host, seconds, age, project=None):
    return {
        "id": f"00000000-0000-4000-8000-{i:012d}",
        "name": f"vm-{i}",
        "project_id": project or next(iter(fake_provisioning.FAKE_PROJECTS)),
        "host": host,
        "seconds": seconds,
        "at": time.time() - age,
    }


class _ViewTestCase(base.TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = mock.patch(f"{VIEWS}.get_datasource")
        self.mock_get_ds = patcher.start()
        self.addCleanup(patcher.stop)
        # wrap the fake datasource and inventory so calls can be asserted
        self.client = mock.MagicMock(wraps=fake.get_client())
        self.mock_get_ds.return_value = self.client
        patcher = mock.patch(f"{VIEWS}.provisioning_inventory.get_inventory")
        self.mock_get_inventory = patcher.start()
        self.addCleanup(patcher.stop)
        self.inventory = mock.MagicMock(
            wraps=provisioning_inventory.FakeProvisioningInventory()
        )
        self.mock_get_inventory.return_value = self.inventory
        # Compute Host Health absent unless a test says otherwise, whatever
        # the panels installed next to this one (their URLs resolve then)
        patcher = mock.patch(f"{VIEWS}.chh_url", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _request(self, **params):
        request = RequestFactory().get("/", params)
        request.user = mock.MagicMock(services_region="RegionOne")
        return request

    def render_context(self, view_class, **params):
        view = view_class()
        view.setup(self._request(**params))
        return view.get_context_data()

    def render(self, view_class, **params):
        response = view_class.as_view()(self._request(**params))
        response.render()
        return response.content.decode()


class EnabledFileTests(base.TestCase):
    def test_enabled_file(self):
        self.assertEqual(enabled.PANEL, "provisioning")
        self.assertEqual(enabled.PANEL_DASHBOARD, "admin")
        self.assertEqual(enabled.PANEL_GROUP, "telemetry")
        self.assertEqual(
            enabled.ADD_PANEL,
            "grian_ui.content.provisioning.panel.Provisioning",
        )


class IndexViewTests(_ViewTestCase):
    def test_context(self):
        context = self.render_context(views.ProvisioningIndexView)
        self.assertEqual(context["ranges"], ["24h", "7d", "14d"])
        self.assertEqual(context["default_range"], "7d")
        self.assertEqual(context["refresh_minutes"], 5)
        self.assertEqual(context["thresholds"]["host_ratio_critical"], 2.0)
        self.assertEqual(context["error_messages"], [])
        self.assertEqual(
            context["range_steps"], ["24h: 1m", "7d: 5m", "14d: 10m"]
        )
        # the shell queries nothing
        self.mock_get_ds.assert_not_called()
        self.mock_get_inventory.assert_not_called()

    def test_template(self):
        source = get_template("admin/provisioning/index.html").template.source
        for name in (
            "summary",
            "boot_times",
            "hosts",
            "failed_builds",
            "slowest_boots",
        ):
            self.assertIn(f"horizon:admin:provisioning:{name}", source)
        self.assertIn("GRIAN_PLUGIN['provisioning_thresholds']", source)
        self.assertIn('<details class="panel panel-default', source)
        self.assertNotIn("<details open", source)

    @override_settings(
        GRIAN_PLUGIN={"provisioning_thresholds": {"host_min_boots": 0}}
    )
    def test_configuration_error(self):
        context = self.render_context(views.ProvisioningIndexView)
        self.assertNotIn("thresholds", context)
        self.assertEqual(len(context["error_messages"]), 1)
        self.assertIn("host_min_boots", context["error_messages"][0])

    def test_every_configuration_error_is_shown(self):
        with (
            mock.patch(
                f"{VIEWS}.config.get_provisioning_thresholds",
                side_effect=config.ConfigurationError("bad"),
            ),
            mock.patch(
                f"{VIEWS}.config.get_datasource_type",
                return_value=config.DATASOURCE_PROMETHEUS,
            ),
            mock.patch(
                f"{VIEWS}.config.get_openstack_keystone_url",
                side_effect=config.ConfigurationError("no keystone"),
            ),
        ):
            context = self.render_context(views.ProvisioningIndexView)
        self.assertEqual(context["error_messages"], ["bad", "no keystone"])

    def test_renders_without_horizon_blocks(self):
        # base.html is horizon's: only check the template compiles
        get_template("admin/provisioning/index.html")


class SummaryViewTests(_ViewTestCase):
    def test_summary(self):
        context = self.render_context(views.SummaryView)
        self.assertEqual(context["range_key"], "7d")
        summary = context["summary"]
        self.assertGreater(summary["boots"], 1000)
        self.assertEqual(
            summary["slow_hosts"][0]["name"], fake_provisioning.FAKE_SLOW_HOST
        )
        self.assertEqual(context["failed"]["count"], 7)
        self.assertEqual(context["failed_level"], "warn")
        self.assertAlmostEqual(
            context["failed_percent"], 700 / (summary["boots"] + 7)
        )
        self.assertNotIn("data_stale", context)
        self.assertNotIn("data_old", context)
        self.assertRegex(context["metrics_at"]["ts"], r"^\d{10}$")
        self.assertIsNotNone(context["compute_at"])

    def test_rendered(self):
        html = self.render(views.SummaryView)
        self.assertIn("Instances booted", html)
        self.assertIn("95th percentile", html)
        self.assertIn("1 slow host: compute-017", html)
        self.assertIn("prov-card prov-crit", html)
        self.assertIn("% of create requests", html)
        self.assertIn("Metrics from", html)
        self.assertIn("Compute API data from", html)
        self.assertIn("(refreshed every 5 minutes)", html)

    def test_range(self):
        context = self.render_context(views.SummaryView, range="24h")
        self.assertEqual(context["range_key"], "24h")
        self.assertEqual(context["failed"]["count"], 1)
        context = self.render_context(views.SummaryView, range="bogus")
        self.assertEqual(context["range_key"], "7d")

    def test_few_boots_show_the_slowest_boot(self):
        self.client.get_provisioning_boots.side_effect = None
        self.client.get_provisioning_boots.return_value = [
            _boot(1, "h1", 3.2, 3 * 86400),
            _boot(2, "h1", 3.8, 3 * 86400),
        ]
        html = self.render(views.SummaryView)
        self.assertIn("Slowest boot", html)
        self.assertIn("3.8 s", html)
        self.assertIn("only 2 boots: indicative", html)
        self.assertNotIn("95th percentile", html)

    def test_no_boots(self):
        self.client.get_provisioning_boots.return_value = []
        self.inventory.failed_builds.side_effect = None
        self.inventory.failed_builds.return_value = {
            "builds": [],
            "truncated": False,
        }
        html = self.render(views.SummaryView)
        self.assertIn("no boots in this range", html)
        self.assertIn(">none<", html)

    def test_only_failures_are_critical(self):
        self.client.get_provisioning_boots.return_value = []
        context = self.render_context(views.SummaryView)
        self.assertEqual(context["failed_level"], "crit")

    def test_truncated_failed_builds(self):
        self.inventory.failed_builds.side_effect = None
        self.inventory.failed_builds.return_value = {
            "builds": fake_provisioning.fake_failed_builds(),
            "truncated": True,
        }
        html = self.render(views.SummaryView)
        self.assertIn(">7+<", html)

    def test_compute_api_failure_keeps_the_cards(self):
        self.inventory.failed_builds.side_effect = RuntimeError("nova down")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            context = self.render_context(views.SummaryView)
        self.assertIn("summary", context)
        self.assertNotIn("failed", context)
        self.assertTrue(context["failed_unavailable"])
        self.assertFalse(context["failed_loading"])
        html = self.render(views.SummaryView)
        self.assertIn("Compute API unavailable", html)
        self.assertIn("prov-card prov-unknown", html)

    def test_summary_is_shared_by_the_partials(self):
        self.render_context(views.SummaryView)
        self.render_context(views.BootTimesView)
        self.render_context(views.HostsView)
        self.render_context(views.SlowestBootsView)
        self.client.get_provisioning_boots.assert_called_once_with("7d")
        self.render_context(views.FailedBuildsView)
        self.inventory.failed_builds.assert_called_once()
        # one summary per range
        self.render_context(views.HostsView, range="24h")
        self.assertEqual(self.client.get_provisioning_boots.call_count, 2)

    def test_thresholds_are_part_of_the_cache_key(self):
        self.render_context(views.HostsView)
        with override_settings(
            GRIAN_PLUGIN={
                "datasource": "fake",
                "provisioning_thresholds": {"host_ratio_critical": 9},
            }
        ):
            context = self.render_context(views.HostsView)
        self.assertEqual(self.client.get_provisioning_boots.call_count, 2)
        self.assertEqual(context["summary"]["slow_hosts"], [])

    def test_datasource_error(self):
        self.client.get_provisioning_boots.side_effect = RuntimeError(
            "secret backend detail"
        )
        with self.assertLogs("grian_ui.cache", "ERROR"):
            html = self.render(views.SummaryView)
        self.assertIn("Error loading telemetry data", html)
        self.assertNotIn("secret backend detail", html)
        self.assertIn("data-prov-nodata", html)

    def test_still_computing(self):
        with mock.patch(
            f"{VIEWS}.cache.get_or_compute",
            side_effect=views.cache.StillComputing("busy"),
        ):
            context = self.render_context(views.SummaryView)
            html = self.render(views.SummaryView)
        self.assertTrue(context["loading"])
        self.assertIn("Still loading", html)
        self.assertIn("prov-retry-", html)

    def test_configuration_error(self):
        with override_settings(
            GRIAN_PLUGIN={"provisioning_thresholds": {"bogus": 1}}
        ):
            with self.assertLogs(VIEWS, "ERROR"):
                html = self.render(views.SummaryView)
        self.assertIn("invalid configuration", html)
        self.mock_get_ds.assert_not_called()

    def test_configuration_error_inside_the_computation(self):
        self.mock_get_ds.side_effect = config.ConfigurationError("no url")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            context = self.render_context(views.SummaryView)
        self.assertIn("invalid configuration", str(context["error_message"]))

    def test_last_good_copy_is_marked(self):
        self.render_context(views.SummaryView)
        key = views._cache_key(
            self._request(),
            "summary:7d:"
            + views._thresholds_key(config.get_provisioning_thresholds()),
        )
        value = cache.get(key)
        old = time.time() - views.SUMMARY_CACHE_TTL - views.REFRESH_SECONDS
        cache.set(key, dict(value, stored_at=old - 1), 60)
        context = self.render_context(views.SummaryView)
        # possibly out of date (still being refreshed), not a failure
        self.assertTrue(context["data_old"])
        self.assertNotIn("data_stale", context)
        html = self.render(views.SummaryView)
        self.assertIn("may be out of date", html)

    def test_copy_after_a_failure_is_marked(self):
        real = views.cache.get_or_compute

        def stale(key, ttl, compute):
            return dict(real(key, ttl, compute), is_stale_copy=True)

        with mock.patch(f"{VIEWS}.cache.get_or_compute", stale):
            context = self.render_context(views.SummaryView)
            html = self.render(views.SummaryView)
        self.assertTrue(context["data_stale"])
        self.assertIn("Could not be refreshed", html)


class BootTimesViewTests(_ViewTestCase):
    def test_chart(self):
        context = self.render_context(views.BootTimesView, range="14d")
        chart = context["chart"]
        self.assertEqual(chart["range"], "14d")
        self.assertEqual(len(chart["points"]), provisioning.MAX_POINTS)
        self.assertEqual(chart["slow_hosts"], ["compute-017"])
        at, seconds, name, project, host, slow = chart["points"][0]
        self.assertIsInstance(at, int)
        # project names, not IDs
        self.assertIn(project, fake_provisioning.FAKE_PROJECTS.values())
        self.assertTrue(any(p[5] for p in chart["points"]))

    def test_rendered(self):
        html = self.render(views.BootTimesView, range="14d")
        self.assertIn("thinned to 2000 dots", html)
        data = re.search(
            r'<script id="prov-boots-data-\w+" type="application/json">'
            r"(.*?)</script>",
            html,
            re.S,
        )
        chart = json.loads(data.group(1))
        self.assertEqual(len(chart["points"]), 2000)
        self.assertIn("Boot on a slow host (compute-017)", html)

    def test_names_are_escaped(self):
        self.client.get_provisioning_boots.return_value = [
            dict(_boot(1, "h1", 3.0, 60), name="</script><b>x")
        ]
        html = self.render(views.BootTimesView)
        self.assertNotIn("</script><b>x", html)

    def test_project_ids_without_identity_api(self):
        self.inventory.project_names.side_effect = RuntimeError("down")
        self.client.get_provisioning_boots.return_value = [
            _boot(1, "h1", 3.0, 60, project="0123456789abcdef")
        ]
        with self.assertLogs("grian_ui.cache", "ERROR"):
            context = self.render_context(views.BootTimesView)
        self.assertEqual(context["chart"]["points"][0][3], "01234567")
        self.assertTrue(context["projects_unavailable"])
        html = self.render(views.BootTimesView)
        self.assertIn("showing project IDs", html)

    def test_no_boots(self):
        self.client.get_provisioning_boots.return_value = []
        html = self.render(views.BootTimesView, range="24h")
        self.assertIn("No instance booted in the last 24h", html)
        self.assertIn("prov.destroyChart('boots')", html)

    def test_error(self):
        self.client.get_provisioning_boots.side_effect = RuntimeError("x")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            html = self.render(views.BootTimesView)
        self.assertIn("Error loading telemetry data", html)
        self.assertIn("prov.destroyChart('boots')", html)


class HostsViewTests(_ViewTestCase):
    def test_rows(self):
        context = self.render_context(views.HostsView)
        rows = context["rows"]
        self.assertEqual(len(rows), provisioning.TOP_LIMIT)
        self.assertEqual(rows[0]["name"], "compute-017")
        self.assertEqual(rows[0]["status"], "slow")
        self.assertEqual(rows[0]["ratio_width"], 100)
        # cached rows are not mutated
        self.assertNotIn("ratio_width", context["summary"]["hosts"][0])

    def test_rendered(self):
        html = self.render(views.HostsView)
        self.assertIn('<tr class="danger">', html)
        self.assertIn("40 hosts booted instances in this range.", html)
        self.assertIn("Showing the 10 slowest.", html)
        self.assertIn("slower than the cloud", html)

    def test_too_few_boots(self):
        self.client.get_provisioning_boots.return_value = [
            _boot(1, "devstack", 3.0, 2 * 86400),
            _boot(2, "devstack", 3.5, 2 * 86400),
        ]
        html = self.render(views.HostsView)
        self.assertIn("too few boots in 24h", html)
        self.assertIn("1 host booted instances in this range.", html)
        self.assertNotIn("slower than the cloud", html)

    def test_empty(self):
        self.client.get_provisioning_boots.return_value = []
        html = self.render(views.HostsView)
        self.assertIn("No boots in this range.", html)


class FailedBuildsViewTests(_ViewTestCase):
    def test_rows(self):
        context = self.render_context(views.FailedBuildsView)
        rows = context["rows"]
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["name"], "app-6")
        self.assertEqual(rows[0]["project"], "web-shop")
        self.assertTrue(context["no_valid_host"])
        html = self.render(views.FailedBuildsView)
        self.assertIn("No valid host was found.", html)
        self.assertIn("capacity signal", html)

    def test_limit(self):
        builds = [
            dict(b, id=f"{b['id']}-{i}")
            for i in range(2)
            for b in fake_provisioning.fake_failed_builds()
        ]
        self.inventory.failed_builds.side_effect = None
        self.inventory.failed_builds.return_value = {
            "builds": builds,
            "truncated": True,
        }
        context = self.render_context(views.FailedBuildsView)
        self.assertEqual(len(context["rows"]), provisioning.FAILED_LIMIT)
        html = self.render(views.FailedBuildsView)
        self.assertIn("Showing the newest 8 of 14.", html)
        self.assertIn("There may be more", html)

    def test_none(self):
        self.inventory.failed_builds.side_effect = None
        self.inventory.failed_builds.return_value = {
            "builds": [],
            "truncated": False,
        }
        html = self.render(views.FailedBuildsView)
        self.assertIn("No failed builds in the last 7d.", html)

    def test_since_matches_the_range(self):
        before = time.time()
        self.render_context(views.FailedBuildsView, range="14d")
        since, limit = self.inventory.failed_builds.call_args.args
        self.assertAlmostEqual(since, before - 14 * 86400, delta=5)
        self.assertEqual(limit, provisioning.FAILED_QUERY_LIMIT)

    def test_compute_api_error(self):
        self.inventory.failed_builds.side_effect = RuntimeError("secret")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            html = self.render(views.FailedBuildsView)
        self.assertIn("Error loading telemetry data", html)
        self.assertNotIn("secret", html)


class SlowestBootsViewTests(_ViewTestCase):
    def test_rows(self):
        context = self.render_context(views.SlowestBootsView)
        rows = context["rows"]
        self.assertEqual(len(rows), provisioning.TOP_LIMIT)
        seconds = [r["seconds"] for r in rows]
        self.assertEqual(seconds, sorted(seconds, reverse=True))
        self.assertIn(rows[0]["flavor"], fake_provisioning.FAKE_FLAVORS)
        self.assertIn(
            rows[0]["project"], fake_provisioning.FAKE_PROJECTS.values()
        )
        # one flavor lookup per instance, not per refresh
        self.assertEqual(
            self.inventory.flavor.call_count, provisioning.TOP_LIMIT
        )
        self.render_context(views.SlowestBootsView)
        self.render_context(views.SlowestBootsView, range="14d")
        looked_up = [c.args[0] for c in self.inventory.flavor.call_args_list]
        self.assertEqual(len(looked_up), len(set(looked_up)))
        self.assertLess(len(looked_up), 2 * provisioning.TOP_LIMIT)

    def test_flavor_failure_keeps_the_table(self):
        self.inventory.flavor.side_effect = RuntimeError("down")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            context = self.render_context(views.SlowestBootsView)
        self.assertEqual(len(context["rows"]), provisioning.TOP_LIMIT)
        self.assertIsNone(context["rows"][0]["flavor"])
        # a failing Compute API is not called for each instance in turn
        self.assertEqual(self.inventory.flavor.call_count, 1)
        html = self.render(views.SlowestBootsView)
        self.assertIn("Flavors could not be loaded", html)

    def test_no_boots_no_lookups(self):
        self.client.get_provisioning_boots.return_value = []
        html = self.render(views.SlowestBootsView)
        self.assertIn("No boots in this range.", html)
        self.inventory.flavor.assert_not_called()

    def test_flavors_loading(self):
        def busy(self, instance_id):
            raise views.cache.StillComputing("busy")

        with mock.patch.object(views._ProvisioningMixin, "get_flavor", busy):
            context = self.render_context(views.SlowestBootsView)
        self.assertTrue(context["loading"])
        self.assertTrue(context["flavors_loading"])


CHH = "/dashboard/admin/compute_host_health/"


class HostLinkTests(_ViewTestCase):
    def test_host_link(self):
        hypervisors = {"cmp-1": ["cmp-1.example.com"], "ironic": ["a", "b"]}
        self.assertEqual(
            views.host_link("cmp-1", hypervisors, CHH),
            {
                "url": CHH + "?host=cmp-1.example.com",
                "hypervisor": "cmp-1.example.com",
            },
        )
        # no mapping, several hypervisors, no host, no panel: no link
        self.assertIsNone(views.host_link("cmp-2", hypervisors, CHH))
        self.assertIsNone(views.host_link("ironic", hypervisors, CHH))
        self.assertIsNone(views.host_link(None, hypervisors, CHH))
        self.assertIsNone(views.host_link("cmp-1", hypervisors, None))

    def test_link_is_encoded(self):
        link = views.host_link("h", {"h": ["a&b=c"]}, CHH)
        self.assertEqual(link["url"], CHH + "?host=a%26b%3Dc")

    def test_chh_url(self):
        # setUp replaces chh_url: test the real function, both ways,
        # whether or not Compute Host Health is installed here
        with mock.patch(f"{VIEWS}.reverse", return_value=CHH) as reverse:
            self.assertEqual(_REAL_CHH_URL(), CHH)
        reverse.assert_called_once_with(
            "horizon:admin:compute_host_health:index"
        )
        with mock.patch(
            f"{VIEWS}.reverse", side_effect=NoReverseMatch("not installed")
        ):
            self.assertIsNone(_REAL_CHH_URL())

    def test_without_chh_hosts_are_plain(self):
        html = self.render(views.HostsView)
        self.assertNotIn("<a href", html)
        html = self.render(views.SlowestBootsView)
        self.assertNotIn("<a href", html)
        # the hypervisor list is not even read
        self.inventory.hypervisor_hosts.assert_not_called()
        context = self.render_context(views.ProvisioningIndexView)
        self.assertFalse(context["chh_enabled"])

    @mock.patch(f"{VIEWS}.chh_url", return_value=CHH)
    def test_hosts_link_to_chh(self, _chh):
        context = self.render_context(views.HostsView)
        slow = context["rows"][0]
        self.assertEqual(slow["name"], "compute-017")
        self.assertEqual(
            slow["link"]["url"], CHH + "?host=compute-017.example.com"
        )
        html = self.render(views.HostsView)
        self.assertIn(
            '<a href="' + CHH + '?host=compute-017.example.com"', html
        )
        self.assertIn("Hypervisor list from", html)
        self.assertIn("(refreshed every 10 minutes)", html)
        html = self.render(views.SlowestBootsView)
        self.assertIn(CHH + "?host=compute-", html)
        self.inventory.hypervisor_hosts.assert_called_once()

    @mock.patch(f"{VIEWS}.chh_url", return_value=CHH)
    def test_host_without_hypervisor_is_plain(self, _chh):
        self.client.get_provisioning_boots.return_value = [
            dict(_boot(1, "compute-040", 3.0, 60)),
            dict(_boot(2, "compute-001", 3.0, 60)),
        ]
        context = self.render_context(views.HostsView)
        links = {r["name"]: r["link"] for r in context["rows"]}
        self.assertIsNone(links["compute-040"])
        self.assertIsNotNone(links["compute-001"])
        html = self.render(views.HostsView)
        self.assertRegex(html, r'<th scope="row">compute-040\s*</th>')

    @mock.patch(f"{VIEWS}.chh_url", return_value=CHH)
    def test_hypervisor_list_failure_keeps_the_table(self, _chh):
        self.inventory.hypervisor_hosts.side_effect = RuntimeError("down")
        with self.assertLogs("grian_ui.cache", "ERROR"):
            context = self.render_context(views.HostsView)
        self.assertEqual(len(context["rows"]), provisioning.TOP_LIMIT)
        self.assertTrue(all(r["link"] is None for r in context["rows"]))
        html = self.render(views.HostsView)
        self.assertIn("hosts are not linked to Compute Host Health", html)
        self.assertNotIn("<a href", html)


class RefreshLoadTests(_ViewTestCase):
    """Page refreshes read the shared snapshots: no extra API calls."""

    PARTIALS = (
        views.SummaryView,
        views.BootTimesView,
        views.HostsView,
        views.FailedBuildsView,
        views.SlowestBootsView,
    )

    @mock.patch(f"{VIEWS}.chh_url", return_value=CHH)
    def test_refreshes_do_not_multiply_api_calls(self, _chh):
        for _refresh in range(4):
            for view in self.PARTIALS:
                self.render(view)
        self.client.get_provisioning_boots.assert_called_once_with("7d")
        self.inventory.failed_builds.assert_called_once()
        self.inventory.hypervisor_hosts.assert_called_once()
        self.inventory.project_names.assert_called_once()
        self.assertEqual(
            self.inventory.flavor.call_count, provisioning.TOP_LIMIT
        )

    @mock.patch(f"{VIEWS}.chh_url", return_value=CHH)
    def test_ranges_share_what_does_not_depend_on_them(self, _chh):
        for range_key in ("24h", "7d", "14d", "7d"):
            for view in self.PARTIALS:
                self.render(view, range=range_key)
        # one boots query and failed builds call per range
        self.assertEqual(self.client.get_provisioning_boots.call_count, 3)
        self.assertEqual(self.inventory.failed_builds.call_count, 3)
        # one hypervisor list and project list for every range
        self.inventory.hypervisor_hosts.assert_called_once()
        self.inventory.project_names.assert_called_once()
        looked_up = [c.args[0] for c in self.inventory.flavor.call_args_list]
        self.assertEqual(len(looked_up), len(set(looked_up)))

    def test_chart_points_are_joined_once_per_input_pair(self):
        with mock.patch.object(
            views._ProvisioningMixin,
            "joined",
            autospec=True,
            side_effect=views._ProvisioningMixin.joined,
        ):
            views._JOINS.clear()
            first = self.render_context(views.BootTimesView)["chart"]
            second = self.render_context(views.BootTimesView)["chart"]
        self.assertIs(first["points"], second["points"])


class ClockTests(_ViewTestCase):
    """Times are formatted in the browser, like the other panels."""

    CLOCK = re.compile(
        r'<time class="prov-clock" datetime="(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)" '
        r'data-prov-time="(\d+)"( data-prov-minutes)?>'
        r"(\d{4}-\d\d-\d\d \d\d:\d\d UTC)</time>"
    )

    def test_clock(self):
        self.assertIsNone(views.clock(None))
        self.assertEqual(
            views.clock(60.7),
            {
                "ts": "60",
                "iso": "1970-01-01T00:01:00Z",
                "utc": "1970-01-01 00:01 UTC",
            },
        )

    def test_footers(self):
        html = self.render(views.SummaryView)
        clocks = self.CLOCK.findall(html)
        # metrics and Compute API data, to the second
        self.assertEqual(len(clocks), 2)
        for _iso, ts, minutes, _utc in clocks:
            self.assertAlmostEqual(int(ts), time.time(), delta=60)
            self.assertEqual(minutes, "")
        self.assertIn("Metrics from <time", html)
        self.assertIn("Compute API data from <time", html)

    def test_table_times_are_to_the_minute(self):
        for view in (views.SlowestBootsView, views.FailedBuildsView):
            html = self.render(view)
            clocks = self.CLOCK.findall(html)
            minutes = [c for c in clocks if c[2]]
            self.assertTrue(minutes, view.__name__)
        # no server-side formatting in the user's or server's time zone
        self.assertNotIn("a.m.", html)
        self.assertNotIn("p.m.", html)

    def test_page_formats_swapped_partials(self):
        source = get_template("admin/provisioning/index.html").template.source
        self.assertIn("prov.showClocks = function", source)
        self.assertIn("htmx:afterSwap", source)
        self.assertIn("toLocaleTimeString", source)
