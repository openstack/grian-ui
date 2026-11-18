#
# SPDX-License-Identifier: Apache-2.0

import json
import re

from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory

from grian_ui import instance_health as ih
from grian_ui.content.instance_health import views
from grian_ui.datasource import fake
from grian_ui_tests.unit import base


VIEWS = "grian_ui.content.instance_health.views"


def _id(name):
    return fake._fake_id(name)


class _ViewTestCase(base.TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.factory = RequestFactory()

    def _request(self, path="/", **params):
        request = self.factory.get(path, params)
        request.user = mock.MagicMock()
        request.user.services_region = "RegionOne"
        return request

    def _context(self, view_class, **params):
        view = view_class()
        view.setup(self._request(**params))
        view.kwargs = {}
        return view.get_context_data()

    def _render(self, view_class, **params):
        request = self._request(**params)
        response = view_class.as_view()(request)
        response.render()
        return response.content.decode()


class IndexViewTests(_ViewTestCase):
    def test_context(self):
        context = self._context(views.InstanceHealthIndexView)
        self.assertEqual(context["default_range"], "6h")
        self.assertEqual(context["rate_window"], "10m")
        self.assertEqual(context["thresholds"]["cpu_wait_warning"], 5)
        self.assertNotIn("error_message", context)

    @mock.patch(f"{VIEWS}.config.get_instance_health_thresholds")
    def test_configuration_error(self, mock_thresholds):
        mock_thresholds.side_effect = views.config.ConfigurationError("bad")
        context = self._context(views.InstanceHealthIndexView)
        self.assertEqual(context["error_messages"], ["bad"])

    @mock.patch(f"{VIEWS}.config.get_instance_health_rate_window")
    @mock.patch(f"{VIEWS}.config.get_instance_health_thresholds")
    def test_every_configuration_error_is_shown(self, thresholds, window):
        thresholds.side_effect = views.config.ConfigurationError("bad")
        window.side_effect = views.config.ConfigurationError("worse")
        context = self._context(views.InstanceHealthIndexView)
        self.assertEqual(context["error_messages"], ["bad", "worse"])

    @mock.patch(f"{VIEWS}.config.get_instance_health_thresholds")
    def test_sections_point_to_the_configuration_error(self, thresholds):
        views._LOGGED_CONFIG_ERRORS.clear()
        self.addCleanup(views._LOGGED_CONFIG_ERRORS.clear)
        thresholds.side_effect = views.config.ConfigurationError("bad")
        with self.assertLogs(views.LOG, level="ERROR") as logs:
            for view in (views.SummaryView, views.AttentionView):
                context = self._context(view)
                self.assertIn(
                    "invalid configuration", str(context["error_message"])
                )
        # logged once, not by every section
        self.assertEqual(len(logs.output), 1)


class SnapshotTests(_ViewTestCase):
    def test_fake_snapshot(self):
        snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertTrue(snapshot["nova_ok"] and snapshot["metrics_ok"])
        self.assertEqual(snapshot["total"], len(fake._FAKE_FLEET))
        names = [r["name"] for r in snapshot["rows"]]
        # ERROR first; stopped instances are never flagged
        self.assertEqual(names[0], "db-0002")
        for name in ("api-0001", "api-0003", "api-0004", "ci-0003"):
            self.assertNotIn(name, names)
        for name in ("ci-0001", "ci-0002", "etl-0002", "web-0001"):
            self.assertIn(name, names)

    def test_snapshot_is_shared_between_partials(self):
        with (
            mock.patch(
                f"{VIEWS}.get_datasource", side_effect=fake.get_client
            ) as get_ds,
            mock.patch(
                f"{VIEWS}.inventory.FakeInventory.list_servers",
                autospec=True,
                side_effect=views.inventory.FakeInventory.list_servers,
            ) as list_servers,
        ):
            self._context(views.SummaryView)
            self._context(views.AttentionView)
        # one client for the shared metrics, one for the list's extras
        snapshot_calls = [c for c in get_ds.mock_calls if c[0] == ""]
        self.assertLessEqual(len(snapshot_calls), 2)
        # the Nova inventory is listed once
        self.assertEqual(list_servers.call_count, 1)
        for name in ("inventory", "metrics"):
            key = views.cache.cache_key(
                self._request(), "instance_health", name, views.CACHE_VERSION
            )
            self.assertIsNotNone(cache.get(key), name)

    def test_inventory_is_refreshed_less_often(self):
        self.assertEqual(views.INVENTORY_CACHE_TTL, 120)
        real = views.cache.get_or_compute
        ttls = {}

        def get_or_compute(key, ttl, compute):
            ttls[key.split(":")[3]] = ttl
            return real(key, ttl, compute)

        with mock.patch(
            f"{VIEWS}.cache.get_or_compute", side_effect=get_or_compute
        ):
            self._context(views.SummaryView)
        self.assertEqual(ttls["inventory"], 120)
        self.assertEqual(ttls["metrics"], views.SNAPSHOT_CACHE_TTL)

    def test_ttl_shorter_than_refresh(self):
        self.assertLess(views.SNAPSHOT_CACHE_TTL, views.REFRESH_SECONDS)

    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_nova_failure_keeps_metric_symptoms(self, list_servers):
        list_servers.side_effect = RuntimeError("nova down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            context = self._context(views.SummaryView)
        snapshot = context["snapshot"]
        self.assertFalse(snapshot["nova_ok"])
        self.assertTrue(snapshot["metrics_ok"])
        self.assertIn("web-0001", [r["name"] for r in snapshot["rows"]])
        tiles = {t["key"]: t for t in context["tiles"]}
        self.assertFalse(tiles[ih.STATE]["available"])
        self.assertFalse(tiles[ih.TELEMETRY]["available"])
        self.assertTrue(tiles[ih.CPU]["available"])

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms"
    )
    def test_metrics_failure_keeps_states(self, symptoms):
        symptoms.side_effect = RuntimeError("aetos down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            context = self._context(views.SummaryView)
        snapshot = context["snapshot"]
        self.assertTrue(snapshot["nova_ok"])
        self.assertFalse(snapshot["metrics_ok"])
        names = [r["name"] for r in snapshot["rows"]]
        self.assertEqual(set(names), {"db-0002", "ci-0001", "ci-0002"})
        tiles = {t["key"]: t for t in context["tiles"]}
        self.assertTrue(tiles[ih.STATE]["available"])
        self.assertFalse(tiles[ih.CPU]["available"])
        self.assertIsNone(context["not_reporting"])

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_reporting_instances"
    )
    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_both_failing_is_an_error(self, list_servers, reporting):
        list_servers.side_effect = RuntimeError("nova down")
        reporting.side_effect = RuntimeError("aetos down")
        with self.assertLogs(level="WARNING") as logs:
            context = self._context(views.SummaryView)
        self.assertIn("error_message", context)
        # backend details go to the log only
        self.assertNotIn("aetos", str(context["error_message"]))
        self.assertIn("aetos down", "\n".join(logs.output))

    @mock.patch(f"{VIEWS}.inventory.FakeInventory.project_names")
    def test_keystone_failure_shows_ids(self, project_names):
        project_names.side_effect = RuntimeError("keystone down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertTrue(snapshot["rows"])
        self.assertTrue(
            all(r["project_name"] is None for r in snapshot["rows"])
        )


class LoadingTests(_ViewTestCase):
    def test_snapshot_still_loading_retries(self):
        with mock.patch(
            f"{VIEWS}.cache.get_or_compute",
            side_effect=views.cache.StillComputing("busy"),
        ):
            context = self._context(views.SummaryView)
            html = self._render(views.AttentionView)
        self.assertTrue(context["loading"])
        self.assertIn("Still loading", str(context["error_message"]))
        self.assertIn("alert-info", html)
        # only this section reloads, not every section of the page
        self.assertNotIn("document.body", html)
        self.assertIn("htmx.trigger(target, 'ih-retry')", html)

    def test_failed_computation_is_an_error_not_loading(self):
        with (
            mock.patch(
                f"{VIEWS}.cache.get_or_compute",
                side_effect=views.cache.ComputationFailed("aetos down"),
            ),
            self.assertLogs(views.LOG, level="WARNING"),
        ):
            context = self._context(views.SummaryView)
        self.assertNotIn("loading", context)
        self.assertIn("Horizon logs", str(context["error_message"]))

    def test_detail_still_loading_reloads_the_row(self):
        with mock.patch(
            f"{VIEWS}.cache.get_or_compute",
            side_effect=views.cache.StillComputing("busy"),
        ):
            html = self._render(views.InstanceDetailView, id=_id("db-0001"))
        self.assertIn(f"ih.loadDetail('{_id('db-0001')}')", html)

    def test_trends_are_evaluated_at_the_grid_time(self):
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_trends",
            return_value={s: [] for s in ih.METRIC_SYMPTOMS},
        ) as trends:
            context = self._context(
                views.InstanceDetailView, id=_id("web-0001"), range="1h"
            )
        at = trends.call_args.kwargs["at"]
        grid_end = context["chart"]["charts"][0]["series"][-1][0]
        self.assertEqual(grid_end, int(at // 60 * 60))


class FilterParsingTests(_ViewTestCase):
    def _filters(self, **params):
        view = views.AttentionView()
        view.setup(self._request(**params))
        return view.get_filters()

    def test_defaults(self):
        self.assertEqual(
            self._filters(),
            {
                "symptom": "",
                "state": "",
                "project": "",
                "severity": 0,
                "q": "",
            },
        )

    def test_invalid_values_are_ignored(self):
        filters = self._filters(
            symptom="bogus", state="'><script>", severity="9", q="x" * 500
        )
        self.assertEqual(filters["symptom"], "")
        self.assertEqual(filters["state"], "")
        self.assertEqual(filters["severity"], 0)
        self.assertEqual(len(filters["q"]), 100)
        self.assertEqual(self._filters(severity="abc")["severity"], 0)

    def test_valid_values(self):
        filters = self._filters(
            symptom="cpu",
            state="shutoff",
            severity="3",
            project=" p1 ",
            q=" web ",
        )
        self.assertEqual(
            filters,
            {
                "symptom": "cpu",
                "state": "shutoff",
                "project": "p1",
                "severity": 3,
                "q": "web",
            },
        )


class AttentionViewTests(_ViewTestCase):
    def test_default_list(self):
        context = self._context(views.AttentionView)
        rows = context["rows"]
        self.assertEqual(context["total"], len(rows))
        self.assertTrue(context["total_exact"])
        self.assertEqual(rows[0]["name"], "db-0002")
        web = next(r for r in rows if r["name"] == "web-0001")
        self.assertEqual(web["trend_symptom"], ih.CPU)
        self.assertEqual(web["severity_key"], "critical")
        self.assertIn(web["id"], context["trends"])
        self.assertEqual(len(context["trends"][web["id"]]), 72)
        # outside horizon there is no instance detail URL
        self.assertIsNone(web["url"])
        silent = next(r for r in rows if r["name"] == "etl-0002")
        self.assertEqual(silent["last_seen_age"], "35 minutes ago")
        self.assertIsNone(silent["trend_symptom"])

    def test_symptom_filter(self):
        context = self._context(views.AttentionView, symptom="memory")
        self.assertEqual(
            {r["name"] for r in context["rows"]}, {"db-0001", "etl-0001"}
        )
        self.assertEqual(str(context["symptom_label"]), "Memory pressure")

    def test_problem_state_filter_uses_the_snapshot(self):
        with mock.patch(
            f"{VIEWS}.inventory.FakeInventory.list_servers_by_status"
        ) as by_status:
            context = self._context(views.AttentionView, state="error")
        by_status.assert_not_called()
        self.assertEqual([r["name"] for r in context["rows"]], ["db-0002"])

    def test_healthy_state_filter_queries_nova(self):
        with mock.patch.object(
            views.inventory.FakeInventory,
            "list_servers_by_status",
            autospec=True,
            side_effect=views.inventory.FakeInventory.list_servers_by_status,
        ) as by_status:
            context = self._context(views.AttentionView, state="shutoff")
        # a few extra servers make up for other buckets of the same status
        self.assertEqual(
            by_status.call_args.kwargs["limit"],
            ih.LIST_LIMIT + views.NOVA_EXTRA_ROWS,
        )
        self.assertEqual(
            [r["name"] for r in context["rows"]], ["api-0001", "api-0002"]
        )
        self.assertEqual(context["total"], 2)
        self.assertTrue(context["total_exact"])
        self.assertTrue(
            all(r["severity"] == ih.HEALTHY for r in context["rows"])
        )

    def test_active_state_keeps_snapshot_issues(self):
        context = self._context(views.AttentionView, state="active")
        rows = {r["name"]: r for r in context["rows"]}
        self.assertEqual(rows["web-0001"]["severity"], ih.CRITICAL)
        self.assertEqual(rows["web-0002"]["severity"], ih.HEALTHY)

    def test_state_filter_with_search_counts_a_complete_list(self):
        # Nova returned every match (fewer than asked for): exact
        context = self._context(views.AttentionView, state="shutoff", q="02")
        self.assertEqual([r["name"] for r in context["rows"]], ["api-0002"])
        self.assertEqual(context["total"], 1)
        self.assertTrue(context["total_exact"])

    def test_state_filter_with_severity(self):
        context = self._context(
            views.AttentionView, state="active", severity="3"
        )
        self.assertTrue(context["rows"])
        self.assertTrue(
            all(r["severity"] == ih.CRITICAL for r in context["rows"])
        )
        self.assertTrue(all(r["state"] == "active" for r in context["rows"]))
        # from the snapshot, which holds every flagged instance: exact
        self.assertFalse(context["from_nova"])
        self.assertTrue(context["total_exact"])

    def test_state_with_severity_finds_flagged_instances_late_in_the_list(
        self,
    ):
        # many healthy active instances first by name: Nova's first page
        # does not hold the critical one
        snapshot = self._context(views.SummaryView)["snapshot"]
        critical = [
            r
            for r in snapshot["rows"]
            if r["state"] == "active" and r["severity"] == ih.CRITICAL
        ]
        self.assertTrue(critical)
        healthy = [
            {
                "id": f"00000000-0000-0000-0000-{i:012d}",
                "name": f"a-{i:04d}",
                "status": "ACTIVE",
                "power_state": 1,
                "task_state": None,
                "project_id": "p",
                "host": "h",
            }
            for i in range(100)
        ]
        with mock.patch(
            f"{VIEWS}.inventory.FakeInventory.list_servers_by_status",
            return_value=healthy,
        ) as by_status:
            context = self._context(
                views.AttentionView, state="active", severity="3"
            )
        by_status.assert_not_called()
        self.assertEqual(
            {r["id"] for r in context["rows"]}, {r["id"] for r in critical}
        )

    def test_active_state_list_matches_its_bucket(self):
        # ci-0001 is ACTIVE in Nova but not running: not "Active (running)"
        context = self._context(views.AttentionView, state="active")
        names = [r["name"] for r in context["rows"]]
        self.assertNotIn("ci-0001", names)
        active = next(
            s["count"]
            for s in context["snapshot"]["states"]
            if s["key"] == "active"
        )
        self.assertEqual(context["total"], active)
        self.assertEqual(len(context["rows"]), active)

    def test_reporting_card_follows_the_list(self):
        context = self._context(views.SummaryView)
        # etl-0002 is flagged; nothing in the fake fleet just started
        self.assertEqual(context["not_reporting"], 1)
        self.assertEqual(context["starting"], 0)

    def test_selected_project_stays_listed(self):
        # a project without flagged instances, so not listed otherwise
        project = "0" * 32
        context = self._context(views.AttentionView, project=project)
        self.assertEqual(dict(context["projects"])[project], project)
        self.assertNotIn(
            project, [r["project_id"] for r in context["snapshot"]["rows"]]
        )

    def test_trend_errors_do_not_break_the_list(self):
        with (
            mock.patch(
                "grian_ui.datasource.fake.FakeTelemetryClient"
                ".get_symptom_trends",
                side_effect=RuntimeError("slow"),
            ),
            self.assertLogs(views.LOG, level="ERROR"),
        ):
            context = self._context(views.AttentionView)
        self.assertTrue(context["rows"])
        self.assertEqual(context["trends"], {})


class InstanceDetailViewTests(_ViewTestCase):
    def test_invalid_id(self):
        context = self._context(views.InstanceDetailView, id="../etc")
        self.assertTrue(context["invalid"])

    def test_instance_with_issues(self):
        context = self._context(
            views.InstanceDetailView, id=_id("db-0001"), range="1h"
        )
        self.assertEqual(context["row"]["name"], "db-0001")
        self.assertTrue(context["has_trends"])
        charts = {c["key"]: c for c in context["chart"]["charts"]}
        self.assertEqual(set(charts), set(ih.METRIC_SYMPTOMS))
        self.assertEqual(len(charts["memory"]["series"]), 60)
        self.assertEqual(charts["disk"]["critical"], 100)
        self.assertEqual(charts["cpu"]["unit"], "%")

    def test_not_running_problem_instance_skips_trends(self):
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_trends"
        ) as trends:
            context = self._context(
                views.InstanceDetailView, id=_id("db-0002")
            )
        trends.assert_not_called()
        self.assertEqual(context["row"]["name"], "db-0002")
        self.assertNotIn("chart", context)

    def test_instance_without_issues_queries_trends(self):
        # not in the snapshot (healthy or stopped): its trends are queried,
        # and a stopped instance has none
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_trends",
            return_value={s: [] for s in ih.METRIC_SYMPTOMS},
        ) as trends:
            context = self._context(
                views.InstanceDetailView, id=_id("api-0001")
            )
        trends.assert_called_once()
        self.assertIsNone(context["row"])
        self.assertFalse(context["has_trends"])

    def test_issue_texts_are_rendered_per_request(self):
        context = self._context(views.InstanceDetailView, id=_id("db-0001"))
        self.assertTrue(all("text" in i for i in context["row"]["issues"]))
        snapshot = self._context(views.SummaryView)["snapshot"]
        row = ih.find_row(snapshot, _id("db-0001"))
        self.assertTrue(all("text" not in i for i in row["issues"]))

    def test_uppercase_id_is_accepted(self):
        context = self._context(
            views.InstanceDetailView, id=_id("db-0001").upper()
        )
        self.assertEqual(context["row"]["name"], "db-0001")


class TemplateRenderTests(_ViewTestCase):
    def test_summary(self):
        html = self._render(views.SummaryView)
        self.assertIn("Instances needing attention", html)
        self.assertIn('data-ih-symptom="cpu"', html)
        self.assertIn('data-ih-state="shutoff"', html)
        # states that cannot be listed are not buttons
        self.assertNotIn('data-ih-state="in_task"', html)
        self.assertIn("Building or in a task", html)

    def test_summary_active_filters(self):
        html = self._render(views.SummaryView, symptom="disk")
        self.assertRegex(html, r'class="ih-sym active" data-ih-symptom="disk"')

    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_summary_nova_down(self, list_servers):
        list_servers.side_effect = RuntimeError("down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            html = self._render(views.SummaryView)
        self.assertIn("Could not reach the Compute API", html)
        self.assertNotIn("Instances by state", html)

    def test_attention(self):
        html = self._render(views.AttentionView)
        options = re.search(
            r'id="(ih-project-options-[0-9a-f]+)"[^>]*>(.*?)</script>',
            html,
            re.S,
        )
        labels = [o[1] for o in json.loads(options.group(2))]
        self.assertIn("web-prod", labels)
        self.assertNotIn("hx-swap-oob", html)
        self.assertIn("CPU wait 28%", html)
        self.assertIn("ih-issue-critical", html)
        self.assertIn('data-ih-detail-body="' + _id("db-0001") + '"', html)
        match = re.search(
            r'id="(ih-trends-[0-9a-f]+)"[^>]*>(.*?)</script>', html, re.S
        )
        self.assertIsNotNone(match)
        self.assertIn(_id("web-0001"), json.loads(match.group(2)))

    def test_attention_escapes_instance_names(self):
        snapshot = ih.build_snapshot(
            [
                dict(
                    fake.fake_servers()[4], name="<img src=x onerror=alert(1)>"
                )
            ],
            {},
            {},
            views.config.get_instance_health_thresholds(),
            0,
        )
        with mock.patch.object(
            views._InstanceHealthMixin, "get_snapshot", return_value=snapshot
        ):
            html = self._render(views.AttentionView)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)

    def test_element_ids_are_unique_per_response(self):
        first = self._render(views.AttentionView)
        second = self._render(views.AttentionView)
        id_a = re.search(r'id="(ih-trends-[0-9a-f]+)"', first).group(1)
        id_b = re.search(r'id="(ih-trends-[0-9a-f]+)"', second).group(1)
        self.assertNotEqual(id_a, id_b)

    def test_instance_detail(self):
        html = self._render(views.InstanceDetailView, id=_id("db-0002"))
        self.assertIn("Fault:", html)
        self.assertIn("No valid host was found", html)

    def test_instance_detail_charts(self):
        html = self._render(views.InstanceDetailView, id=_id("web-0001"))
        self.assertIn("ih-mini-", html)
        self.assertIn("ih-detail-data-", html)

    def test_instance_detail_telemetry(self):
        html = self._render(views.InstanceDetailView, id=_id("etl-0002"))
        self.assertIn("ceilometer-polling", html)


class JoinTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_a_new_inventory_rebuilds_the_snapshot(self):
        snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertIn("db-0002", [r["name"] for r in snapshot["rows"]])
        real = views._InstanceHealthMixin.get_inventory_value

        def refreshed(view):
            # a newer inventory, the same metrics: db-0002 was deleted
            value = real(view)
            return dict(
                value,
                servers=[
                    s for s in value["servers"] if s["name"] != "db-0002"
                ],
                fetched_at=value["fetched_at"] + 120,
            )

        with mock.patch.object(
            views._InstanceHealthMixin, "get_inventory_value", refreshed
        ):
            snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertNotIn("db-0002", [r["name"] for r in snapshot["rows"]])

    def test_new_metrics_rebuild_the_snapshot(self):
        snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertNotIn("web-0002", [r["name"] for r in snapshot["rows"]])
        real = views._InstanceHealthMixin.get_metrics_value

        def refreshed(view):
            # newer metrics, the same inventory: web-0002 now waits on CPU
            value = real(view)
            symptoms = dict(value["symptoms"])
            symptoms["cpu"] = dict(symptoms.get("cpu") or {})
            symptoms["cpu"][_id("web-0002")] = 50.0
            return dict(
                value, symptoms=symptoms, fetched_at=value["fetched_at"] + 25
            )

        with mock.patch.object(
            views._InstanceHealthMixin, "get_metrics_value", refreshed
        ):
            snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertIn("web-0002", [r["name"] for r in snapshot["rows"]])

    def test_the_same_inputs_reuse_the_join(self):
        self._context(views.SummaryView)
        with mock.patch.object(
            views.instance_health, "build_snapshot"
        ) as build:
            self._context(views.AttentionView)
        build.assert_not_called()

    def test_joins_stay_out_of_the_shared_cache(self):
        real = views.cache.get_or_compute
        names = []

        def get_or_compute(key, ttl, compute):
            names.append(key.split(":")[3])
            return real(key, ttl, compute)

        with mock.patch(
            f"{VIEWS}.cache.get_or_compute", side_effect=get_or_compute
        ):
            self._context(views.SummaryView)
            self._context(views.AttentionView)
        # only the inputs (and the project names) are shared
        self.assertEqual(set(names), {"inventory", "metrics", "projects"})
        self.assertLessEqual(len(views._JOINS), views.JOINS_KEPT)

    def test_metrics_keep_labels_only_for_flagged_instances(self):
        self._context(views.SummaryView)
        key = views.cache.cache_key(
            self._request(), "instance_health", "metrics", views.CACHE_VERSION
        )
        value = cache.get(key)
        flagged = set()
        for found in value["symptoms"].values():
            flagged.update(found)
        self.assertTrue(flagged)
        for instance_id, labels in value["reporting"].items():
            if instance_id in flagged:
                self.assertIn("name", labels)
            else:
                self.assertEqual(labels, {})


class FreshnessTests(_ViewTestCase):
    """Copies served while a slow backend is refreshed are marked too."""

    def _aged(self, part, age):
        real = views.cache.get_or_compute

        def get_or_compute(key, ttl, compute):
            value = real(key, ttl, compute)
            if f":{part}" in key and "stored_at" in value:
                value = dict(value, stored_at=views.time.time() - age)
            return value

        return mock.patch(
            f"{VIEWS}.cache.get_or_compute", side_effect=get_or_compute
        )

    def test_fresh_data_is_not_marked(self):
        context = self._context(views.SummaryView)
        self.assertFalse(context["data_stale"])
        self.assertFalse(context["data_old"])
        self.assertTrue(context["metrics_ts"].isdigit())
        self.assertTrue(context["nova_ts"].isdigit())

    def test_a_failed_refresh_is_a_failure(self):
        real = views.cache.get_or_compute

        def get_or_compute(key, ttl, compute):
            value = real(key, ttl, compute)
            if ":metrics" in key:
                value = dict(value, is_stale_copy=True)
            return value

        with mock.patch(
            f"{VIEWS}.cache.get_or_compute", side_effect=get_or_compute
        ):
            html = self._render(views.SummaryView)
        self.assertIn("Could not be refreshed", html)

    def test_old_metrics_are_marked(self):
        with self._aged("metrics", 10 * 60):
            for view in (views.SummaryView, views.AttentionView):
                with self.subTest(view=view.__name__):
                    context = self._context(view)
                    # probably still refreshing: not a failure
                    self.assertTrue(context["data_old"])
                    self.assertFalse(context["data_stale"])
            html = self._render(views.SummaryView)
        self.assertIn("Refreshing: the data shown may be a few", html)
        self.assertNotIn("Could not be refreshed", html)

    def test_inventory_is_aged_against_its_own_lifetime(self):
        # 2 minutes: within the inventory's lifetime, past the metrics' one
        with self._aged("inventory", 120):
            self.assertFalse(self._context(views.SummaryView)["data_old"])
        with self._aged("inventory", 10 * 60):
            self.assertTrue(self._context(views.SummaryView)["data_old"])

    def test_summary_shows_the_times_of_the_data(self):
        html = self._render(views.SummaryView)
        self.assertIn("Metrics from", html)
        self.assertIn("refreshed every 2 minutes", html)
        self.assertEqual(html.count('class="ih-clock" data-ih-time="'), 2)


class StoppedInstanceTests(_ViewTestCase):
    def test_stopped_instances_are_not_flagged_for_metrics(self):
        server = {
            "id": _id("x"),
            "name": "x",
            "status": "SHUTOFF",
            "power_state": 4,
            "task_state": None,
            "project_id": "p",
            "host": "h",
        }
        snapshot = ih.build_snapshot(
            [server],
            {},
            {"cpu": {_id("x"): 50.0}},
            views.config.get_instance_health_thresholds(),
            0,
        )
        self.assertEqual(snapshot["rows"], [])
        # the same rates on a running instance are flagged
        running = dict(server, status="ACTIVE", power_state=1)
        snapshot = ih.build_snapshot(
            [running],
            {_id("x"): {}},
            {"cpu": {_id("x"): 50.0}},
            views.config.get_instance_health_thresholds(),
            0,
        )
        self.assertEqual(snapshot["rows"][0]["issues"][0]["symptom"], "cpu")


class AccessibilityTests(_ViewTestCase):
    def test_controls_keep_their_ids(self):
        # htmx gives focus back to an element with the same id after a swap
        summary = self._render(views.SummaryView)
        self.assertIn('id="ih-sym-cpu"', summary)
        self.assertIn('id="ih-legend-active"', summary)
        self.assertIn('id="ih-seg-active"', summary)
        attention = self._render(views.AttentionView)
        row_id = _id("db-0002")
        self.assertIn(f'id="ih-toggle-{row_id}"', attention)

    def test_sparklines_have_a_text_alternative(self):
        html = self._render(views.AttentionView)
        self.assertRegex(html, r'aria-label="CPU contention, latest [\d.]+ %"')
        self.assertNotIn('aria-label="Trend of', html)

    def test_detail_charts_have_a_text_alternative(self):
        html = self._render(views.InstanceDetailView, id=_id("web-0001"))
        self.assertRegex(
            html, r'<p class="sr-only" id="ih-mini-[0-9a-f]+-cpu-text">Latest '
        )
        self.assertIn("warning above 5 %", html)

    def test_list_announcement(self):
        context = self._context(views.AttentionView)
        self.assertIn("matching instances shown", str(context["announce"]))
        html = self._render(views.AttentionView, q="no-such-instance")
        self.assertIn(
            "<span hidden data-ih-announce>No instances match.", html
        )

    def test_project_filter_is_kept_on_errors(self):
        with mock.patch(
            f"{VIEWS}.cache.get_or_compute",
            side_effect=views.cache.StillComputing("busy"),
        ):
            html = self._render(views.AttentionView)
        self.assertNotIn("ih.updateProjects", html)


class DetailTrendFailureTests(_ViewTestCase):
    def test_issues_stay_when_the_trends_fail(self):
        with (
            mock.patch(
                "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_trends",
                side_effect=RuntimeError("aetos down"),
            ),
            self.assertLogs(views.LOG, level="ERROR"),
        ):
            html = self._render(views.InstanceDetailView, id=_id("web-0001"))
        self.assertIn("The trends could not be loaded.", html)
        self.assertNotIn("alert-danger", html)
        self.assertIn("ih.destroyCharts('detail-", html)


class StyleTests(base.TestCase):
    def _source(self, name="index.html"):
        import os

        import grian_ui

        path = os.path.join(
            os.path.dirname(grian_ui.__file__),
            "templates",
            "admin",
            "instance_health",
            name,
        )
        with open(path) as f:
            return f.read()

    def test_decorative_icons_are_hidden(self):
        for name in (
            "index.html",
            "_summary.html",
            "_attention.html",
            "_instance.html",
        ):
            for icon in re.findall(r'<i class="fa [^>]*>', self._source(name)):
                self.assertIn('aria-hidden="true"', icon, name)

    def test_colours_meet_wcag_contrast(self):
        source = self._source()

        def colour(rule, prop="(?:color|background|background-color)"):
            match = re.search(
                # the property starts the declaration ("color", not
                # "border-color")
                re.escape(rule)
                + r" \{\s*(?:[^}]*?;\s*)??"
                + prop
                + r": (#[0-9a-f]{3,6});",
                source,
            )
            self.assertIsNotNone(match, rule)
            value = match.group(1)
            if len(value) == 4:
                value = "#" + "".join(c * 2 for c in value[1:])
            return value

        def luminance(hex_colour):
            def channel(i):
                c = int(hex_colour[i : i + 2], 16) / 255
                return (
                    c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                )

            return (
                0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
            )

        def contrast(a, b):
            la, lb = sorted((luminance(a), luminance(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)

        white = "#ffffff"
        # white text on the cards and badges
        for rule in (
            ".ih-card.ih-info .panel-heading",
            ".ih-card.ih-ok .panel-heading",
            ".ih-card.ih-warn .panel-heading",
            ".ih-card.ih-crit .panel-heading",
            ".ih-card.ih-unknown .panel-heading",
            "#ih-root .label-success",
            "#ih-root .label-warning",
            "#ih-root .label-danger",
            "#ih-root .btn-group .btn.active",
        ):
            self.assertGreaterEqual(contrast(colour(rule), white), 4.5, rule)
        # muted texts on white, on the panel footer and in the
        # highlighted rows, also on hover (Horizon's theme colours)
        for background in (
            "#ffffff",
            "#f5f5f5",
            "#f2dede",
            "#ebcccc",
            "#fcf8e3",
            "#faf2cc",
        ):
            for rule in (
                "#ih-root .text-muted",
                ".ih-muted",
                "#ih-root .text-warning",
                "#ih-root .ih-table a",
            ):
                self.assertGreaterEqual(
                    contrast(colour(rule, "color"), background),
                    4.5,
                    f"{rule} on {background}",
                )
        # the issue badges: their own text and background
        for rule in (".ih-issue-critical", ".ih-issue-warning"):
            self.assertGreaterEqual(
                contrast(colour(rule, "color"), colour(rule, "background")),
                4.5,
                rule,
            )
        # the card texts are not faded (opacity lowers their contrast)
        for rule in (".ih-card .ih-label", ".ih-card .ih-sub"):
            match = re.search(re.escape(rule) + r" \{([^}]*)\}", source)
            self.assertIsNotNone(match, rule)
            self.assertNotIn("opacity", match.group(1), rule)
        # the row toggle is an icon: 3:1
        self.assertGreaterEqual(
            contrast(colour(".ih-toggle", "color"), white), 3
        )


class PartialLoadingTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_metrics_are_shown_while_the_inventory_loads(self):
        with mock.patch.object(
            views._InstanceHealthMixin,
            "get_inventory_value",
            side_effect=views.cache.StillComputing("busy"),
        ):
            context = self._context(views.SummaryView)
            html = self._render(views.SummaryView)
        self.assertNotIn("error_message", context)
        self.assertEqual(context["partial_loading"], ["inventory"])
        self.assertTrue(context["snapshot"]["metrics_ok"])
        self.assertIn(
            "web-0001", [r["name"] for r in context["snapshot"]["rows"]]
        )
        self.assertIn("Loading the instance list", html)
        self.assertNotIn("Could not reach the Compute API", html)
        # the section reloads itself shortly
        self.assertIn("htmx.trigger(target, 'ih-retry')", html)

    def test_states_are_shown_while_the_metrics_load(self):
        with mock.patch.object(
            views._InstanceHealthMixin,
            "get_metrics_value",
            side_effect=views.cache.StillComputing("busy"),
        ):
            html = self._render(views.AttentionView)
        self.assertIn("db-0002", html)
        self.assertIn("ih-retry", html)
        self.assertIn("data-ih-loading", html)

    def test_both_loading_is_loading(self):
        busy = views.cache.StillComputing("busy")
        with (
            mock.patch.object(
                views._InstanceHealthMixin,
                "get_inventory_value",
                side_effect=busy,
            ),
            mock.patch.object(
                views._InstanceHealthMixin,
                "get_metrics_value",
                side_effect=busy,
            ),
        ):
            context = self._context(views.SummaryView)
        self.assertTrue(context["loading"])

    def test_metrics_are_fetched_while_the_inventory_loads(self):
        with (
            mock.patch.object(
                views._InstanceHealthMixin,
                "get_inventory_value",
                side_effect=views.cache.StillComputing("busy"),
            ),
            mock.patch.object(
                views._InstanceHealthMixin,
                "get_metrics_value",
                autospec=True,
                side_effect=views._InstanceHealthMixin.get_metrics_value,
            ) as metrics,
        ):
            self._context(views.SummaryView)
        metrics.assert_called_once()


class FilterConsistencyTests(_ViewTestCase):
    def test_severity_follows_the_selected_symptom(self):
        row = {
            "id": "x",
            "name": "x",
            "project_id": "p",
            "state": "active",
            "severity": ih.CRITICAL,
            "issues": [
                {"symptom": "cpu", "severity": ih.WARNING},
                {"symptom": "disk", "severity": ih.CRITICAL},
            ],
        }
        snapshot = {"rows": [row]}
        rows, _total = ih.filter_rows(
            snapshot, symptom="cpu", min_severity=ih.CRITICAL
        )
        self.assertEqual(rows, [])
        rows, _total = ih.filter_rows(
            snapshot, symptom="disk", min_severity=ih.CRITICAL
        )
        self.assertEqual(rows, [row])

    def test_only_silent_instances_have_a_red_last_sample(self):
        html = self._render(views.AttentionView, state="active")
        rows = html.split('<tr class="ih-row')[1:]
        self.assertTrue(rows)
        red = [r for r in rows if 'class="text-danger">' in r]
        # only etl-0002, which stopped reporting; not the healthy rows
        self.assertEqual(len(red), 1)
        self.assertIn("No samples", red[0])
        self.assertTrue(any("text-muted" in r and "ago" in r for r in rows))

    def test_caption_with_a_state_and_a_severity(self):
        html = self._render(views.AttentionView, state="active", severity="3")
        self.assertIn("Instances needing attention in state", html)
        self.assertNotIn("All instances in state", html)
        html = self._render(views.AttentionView, state="active")
        self.assertIn("All instances in state", html)

    def test_full_id_search_in_a_nova_listed_state(self):
        target = _id("api-0002")
        context = self._context(views.AttentionView, state="shutoff", q=target)
        self.assertEqual([r["id"] for r in context["rows"]], [target])
        context = self._context(
            views.AttentionView, state="shutoff", q=target.upper()
        )
        self.assertEqual([r["id"] for r in context["rows"]], [target])


class TelemetryAndFooterTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms"
    )
    def test_no_telemetry_tile_needs_both_sources(self, symptoms):
        symptoms.side_effect = RuntimeError("aetos down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            context = self._context(views.SummaryView)
        tiles = {t["key"]: t for t in context["tiles"]}
        self.assertTrue(context["snapshot"]["nova_ok"])
        self.assertFalse(tiles[ih.TELEMETRY]["available"])
        self.assertTrue(tiles[ih.STATE]["available"])

    @mock.patch(f"{VIEWS}.config.get_instance_health_rate_window")
    def test_a_long_rate_window_lengthens_the_silence(self, window):
        window.return_value = "30m"
        context = self._context(views.InstanceHealthIndexView)
        self.assertEqual(context["stale_after"], "30m")
        server = dict(fake.fake_servers()[0], status="ACTIVE", power_state=1)
        server.update(launched_at=1, updated=1)
        # silent for 20 minutes: not yet with a 30 minute window
        self.assertIsNone(ih.telemetry_issue(server, {}, 20 * 60, "30m"))
        issue = ih.telemetry_issue(server, {}, 40 * 60, "30m")
        self.assertEqual(issue["params"]["window"], "30m")

    def test_nova_listed_footer(self):
        # Nova returned as many as asked for: more may match
        many = [
            dict(
                fake.fake_servers()[0],
                id=f"00000000-0000-4000-8000-{i:012d}",
                name=f"off-02-{i:03d}",
                status="SHUTOFF",
                power_state=4,
                task_state=None,
            )
            for i in range(ih.LIST_LIMIT + views.NOVA_EXTRA_ROWS)
        ]
        with mock.patch.object(
            views.inventory.FakeInventory,
            "list_servers_by_status",
            return_value=many,
        ):
            html = self._render(views.AttentionView, state="shutoff", q="02")
        self.assertIn(
            f"Showing the first {ih.LIST_LIMIT} matching instances.", html
        )
        html = self._render(views.AttentionView, state="shutoff", q="nothing")
        self.assertNotIn("Showing the first", html)
        self.assertIn("No instances match these filters.", html)

    def test_clear_filters_is_a_button(self):
        html = self._render(views.AttentionView, severity="3")
        self.assertRegex(html, r'<button type="button"[^>]*id="ih-clear"')


class SilenceWindowViewTests(_ViewTestCase):
    """A long rate window lengthens No telemetry through the views."""

    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    @mock.patch(f"{VIEWS}.config.get_instance_health_rate_window")
    def test_reporting_and_no_telemetry_use_the_silence_window(self, window):
        window.return_value = "30m"
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient"
            ".get_reporting_instances",
            autospec=True,
            side_effect=fake.FakeTelemetryClient.get_reporting_instances,
        ) as reporting:
            snapshot = self._context(views.SummaryView)["snapshot"]
        # the reporting query looks back over the silence window
        self.assertEqual(reporting.call_args.args[1:], ("30m",))
        # etl-0002 has been silent for 35 minutes: flagged, for 30m
        row = next(r for r in snapshot["rows"] if r["name"] == "etl-0002")
        issue = next(i for i in row["issues"] if i["symptom"] == ih.TELEMETRY)
        self.assertEqual(issue["params"]["window"], "30m")

    @mock.patch(f"{VIEWS}.config.get_instance_health_rate_window")
    def test_a_short_rate_window_keeps_the_minimum_silence(self, window):
        window.return_value = "5m"
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient"
            ".get_reporting_instances",
            autospec=True,
            side_effect=fake.FakeTelemetryClient.get_reporting_instances,
        ) as reporting:
            self._context(views.SummaryView)
        self.assertEqual(reporting.call_args.args[1:], ("10m",))

    def test_settings_checked_first_are_shown_by_every_request(self):
        # read when the client is built, inside the shared computation:
        # checked first, so every request shows it, not only the one that
        # runs the computation
        views._LOGGED_CONFIG_ERRORS.clear()
        self.addCleanup(views._LOGGED_CONFIG_ERRORS.clear)
        with (
            mock.patch(
                f"{VIEWS}.config.get_datasource_type",
                return_value=views.config.DATASOURCE_PROMETHEUS,
            ),
            mock.patch(
                f"{VIEWS}.config.get_openstack_keystone_url",
                side_effect=views.config.ConfigurationError("no keystone"),
            ),
            self.assertLogs(level="ERROR"),
        ):
            for _attempt in range(2):
                context = self._context(views.SummaryView)
                self.assertIn(
                    "invalid configuration", str(context["error_message"])
                )


class ConfigurationAndStateTotalTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_a_wrapped_configuration_error_is_unwrapped(self):
        # raised inside both shared computations (after the settings
        # check): the request that ran them sees the cause
        views._LOGGED_CONFIG_ERRORS.clear()
        self.addCleanup(views._LOGGED_CONFIG_ERRORS.clear)
        error = views.config.ConfigurationError("bad backend")
        with (
            mock.patch(f"{VIEWS}.get_datasource", side_effect=error),
            mock.patch(f"{VIEWS}.inventory.get_inventory", side_effect=error),
            self.assertLogs(level="ERROR"),
        ):
            context = self._context(views.SummaryView)
        self.assertIn("invalid configuration", str(context["error_message"]))

    def _many_shut_off(self):
        extra = [
            dict(
                fake.fake_servers()[0],
                id=f"00000000-0000-4000-8000-{i:012d}",
                name=f"off-{i:03d}",
                status="SHUTOFF",
                power_state=4,
                task_state=None,
            )
            for i in range(40)
        ]
        real = views.inventory.FakeInventory.list_servers

        def list_servers(inventory):
            return real(inventory) + extra

        return mock.patch.object(
            views.inventory.FakeInventory, "list_servers", list_servers
        )

    def test_state_total_comes_from_the_inventory(self):
        with self._many_shut_off():
            context = self._context(views.AttentionView, state="shutoff")
        self.assertEqual(len(context["rows"]), ih.LIST_LIMIT)
        shut_off = next(
            s["count"]
            for s in context["snapshot"]["states"]
            if s["key"] == "shutoff"
        )
        self.assertGreater(shut_off, ih.LIST_LIMIT)
        self.assertEqual(context["total"], shut_off)
        self.assertTrue(context["total_exact"])

    def test_state_total_without_an_inventory_is_not_exact(self):
        with (
            self._many_shut_off(),
            mock.patch.object(
                views._InstanceHealthMixin,
                "get_inventory_value",
                side_effect=views.cache.StillComputing("busy"),
            ),
        ):
            context = self._context(views.AttentionView, state="shutoff")
        self.assertEqual(len(context["rows"]), ih.LIST_LIMIT)
        self.assertFalse(context["total_exact"])


class OldReportingTests(_ViewTestCase):
    def test_grace_is_measured_against_the_reporting_time(self):
        # launched 2 minutes before the (old) reporting data: not yet
        # polled then, whatever the time now
        launched = 1_000_000
        server = dict(
            fake.fake_servers()[0],
            status="ACTIVE",
            power_state=1,
            task_state=None,
            launched_at=launched,
            updated=launched,
        )
        thresholds = views.config.get_instance_health_thresholds()
        snapshot = ih.build_snapshot(
            [server],
            {},
            {},
            thresholds,
            launched + 10 * 60,
            reporting_at=launched + 2 * 60,
        )
        self.assertEqual(snapshot["rows"], [])
        # with fresh reporting data, the same instance is silent
        snapshot = ih.build_snapshot(
            [server],
            {},
            {},
            thresholds,
            launched + 12 * 60,
            reporting_at=launched + 12 * 60,
        )
        self.assertEqual(
            snapshot["rows"][0]["issues"][0]["symptom"], ih.TELEMETRY
        )

    def test_views_pass_the_metrics_time(self):
        with mock.patch.object(
            views.instance_health,
            "build_snapshot",
            wraps=views.instance_health.build_snapshot,
        ) as build:
            views._JOINS.clear()
            self.addCleanup(views._JOINS.clear)
            snapshot = self._context(views.SummaryView)["snapshot"]
        self.assertEqual(
            build.call_args.kwargs["reporting_at"], snapshot["fetched_at"]
        )


class DetailFocusTests(_ViewTestCase):
    def test_detail_links_have_stable_ids(self):
        instance_id = _id("db-0002")
        with mock.patch(f"{VIEWS}._instance_url", return_value="/x"):
            html = self._render(views.InstanceDetailView, id=instance_id)
        self.assertIn(f'id="ih-detail-link-{instance_id}"', html)
        self.assertIn(f'id="ih-log-link-{instance_id}"', html)

    def test_tiles_say_when_their_data_is_loading(self):
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)
        with mock.patch.object(
            views._InstanceHealthMixin,
            "get_inventory_value",
            side_effect=views.cache.StillComputing("busy"),
        ):
            context = self._context(views.SummaryView)
            html = self._render(views.SummaryView)
        tiles = {t["key"]: t for t in context["tiles"]}
        self.assertTrue(tiles[ih.STATE]["loading"])
        self.assertTrue(tiles[ih.TELEMETRY]["loading"])
        self.assertFalse(tiles[ih.CPU]["loading"])
        self.assertIn("Not available yet: its data is still loading", html)
        self.assertNotIn("a data source is unreachable", html)


class NovaListedStateTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def _shut_off(self, n):
        return [
            dict(
                fake.fake_servers()[0],
                id=f"00000000-0000-4000-8000-{i:012d}",
                name=f"off-{i:03d}",
                status="SHUTOFF",
                power_state=4,
                task_state=None,
            )
            for i in range(n)
        ]

    def test_a_complete_live_list_beats_an_older_inventory(self):
        # 5 shut off now, the 2-minute-old inventory knew 2
        with mock.patch.object(
            views.inventory.FakeInventory,
            "list_servers_by_status",
            return_value=self._shut_off(5),
        ):
            context = self._context(views.AttentionView, state="shutoff")
        self.assertEqual(len(context["rows"]), 5)
        self.assertEqual(context["total"], 5)
        self.assertTrue(context["total_exact"])

    def test_a_nova_list_failure_keeps_the_filters_usable(self):
        with (
            mock.patch.object(
                views.inventory.FakeInventory,
                "list_servers_by_status",
                side_effect=RuntimeError("nova down"),
            ),
            self.assertLogs(views.LOG, level="ERROR"),
        ):
            html = self._render(views.AttentionView, state="shutoff")
        self.assertIn("Could not list the instances in this state", html)
        self.assertIn('id="ih-clear"', html)
        self.assertIn("All instances in state", html)
        self.assertNotIn("alert-danger", html)

    def test_rows_listed_from_nova_have_project_names(self):
        context = self._context(views.AttentionView, state="shutoff")
        self.assertTrue(context["rows"])
        for row in context["rows"]:
            self.assertEqual(
                row["project_name"], fake.FAKE_PROJECTS[row["project_id"]]
            )

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms"
    )
    def test_no_last_sample_query_without_metrics(self, symptoms):
        symptoms.side_effect = RuntimeError("aetos down")
        with (
            mock.patch(
                "grian_ui.datasource.fake.FakeTelemetryClient"
                ".get_instance_last_seen"
            ) as last_seen,
            self.assertLogs("grian_ui.cache", level="ERROR"),
        ):
            self._context(views.AttentionView)
        last_seen.assert_not_called()


class AgeBoundaryTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def _aged(self, age):
        real = views.cache.get_or_compute

        def get_or_compute(key, ttl, compute):
            value = real(key, ttl, compute)
            if ":metrics" in key and "stored_at" in value:
                value = dict(value, stored_at=views.time.time() - age)
            return value

        return mock.patch(
            f"{VIEWS}.cache.get_or_compute", side_effect=get_or_compute
        )

    def test_old_from_lifetime_plus_a_refresh_interval(self):
        limit = views.SNAPSHOT_CACHE_TTL + views.REFRESH_SECONDS
        with self._aged(limit - 5):
            self.assertFalse(self._context(views.SummaryView)["data_old"])
        views._JOINS.clear()
        with self._aged(limit + 5):
            self.assertTrue(self._context(views.SummaryView)["data_old"])


class LowerBoundTotalTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_more_live_matches_than_counted_is_not_exact(self):
        # Nova fills the page (more may match) and holds more than the
        # 2-minute-old inventory counted (2 shut off)
        fetched = ih.LIST_LIMIT + views.NOVA_EXTRA_ROWS
        many = [
            dict(
                fake.fake_servers()[0],
                id=f"00000000-0000-4000-8000-{i:012d}",
                name=f"off-{i:03d}",
                status="SHUTOFF",
                power_state=4,
                task_state=None,
            )
            for i in range(fetched)
        ]
        with mock.patch.object(
            views.inventory.FakeInventory,
            "list_servers_by_status",
            return_value=many,
        ):
            context = self._context(views.AttentionView, state="shutoff")
            html = self._render(views.AttentionView, state="shutoff")
        self.assertEqual(context["total"], fetched)
        self.assertFalse(context["total_exact"])
        self.assertIn("Showing the first", html)


class EscapingTests(_ViewTestCase):
    """Names and faults are set by any project member: always escaped."""

    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_tenant_strings_are_escaped(self):
        hostile = '<script>alert(1)</script>"'
        real = views.inventory.FakeInventory.list_servers

        def list_servers(inventory):
            servers = real(inventory)
            for server in servers:
                if server["name"] == "db-0002":
                    server.update(name=hostile, fault=hostile)
            return servers

        with (
            mock.patch.object(
                views.inventory.FakeInventory, "list_servers", list_servers
            ),
            mock.patch.object(
                views.inventory.FakeInventory,
                "project_names",
                return_value={p: hostile for p in fake.FAKE_PROJECTS},
            ),
        ):
            pages = [
                self._render(views.AttentionView),
                self._render(views.InstanceDetailView, id=_id("db-0002")),
            ]
        for html in pages:
            self.assertNotIn("<script>alert(1)", html)
        attention, detail = pages
        escaped = "&lt;script&gt;alert(1)&lt;/script&gt;&quot;"
        # as in Horizon, where every row links to the instance
        with (
            mock.patch.object(
                views.inventory.FakeInventory, "list_servers", list_servers
            ),
            mock.patch(f"{VIEWS}._instance_url", return_value="/x/"),
        ):
            linked = self._render(views.AttentionView)
        self.assertNotIn("<script>alert(1)", linked)
        self.assertIn(f"><strong>{escaped}</strong></a>", linked)
        # the name in the text and in the toggle's label, the project name
        self.assertIn(f"<strong>{escaped}</strong>", attention)
        self.assertIn(f"Show details of {escaped}", attention)
        self.assertIn(f"<td>{escaped}</td>", attention)
        # the fault in the detail
        self.assertIn(escaped, detail)


class LiveRowTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_a_row_listed_from_nova_describes_the_live_server(self):
        # web-0001 is ACTIVE with CPU contention in the (older) snapshot,
        # and was shut off since
        snapshot = self._context(views.SummaryView)["snapshot"]
        cached = ih.find_row(snapshot, _id("web-0001"))
        self.assertEqual(cached["severity"], ih.CRITICAL)
        live = dict(
            next(s for s in fake.fake_servers() if s["name"] == "web-0001"),
            status="SHUTOFF",
            power_state=4,
            task_state=None,
        )
        rows = ih.rows_for_servers(
            [live],
            snapshot,
            views.config.get_instance_health_thresholds(),
            {},
            views.time.time(),
        )
        self.assertEqual(rows[0]["status"], "SHUTOFF")
        self.assertEqual(rows[0]["severity"], ih.HEALTHY)
        self.assertEqual(rows[0]["issues"], [])
        # still running: the snapshot's metric issues are kept
        rows = ih.rows_for_servers(
            [dict(live, status="ACTIVE", power_state=1)],
            snapshot,
            views.config.get_instance_health_thresholds(),
            {},
            views.time.time(),
        )
        self.assertEqual([i["symptom"] for i in rows[0]["issues"]], [ih.CPU])

    def test_no_zero_count_under_an_empty_list(self):
        html = self._render(views.AttentionView, state="shutoff", q="none")
        self.assertIn("No instances match these filters.", html)
        self.assertNotIn("Showing 0", html)


class LiveDetailTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_detail_follows_the_live_state_of_its_list(self):
        # db-0002 is in ERROR in the (older) snapshot; listed live as active
        instance_id = _id("db-0002")
        stale = self._context(views.InstanceDetailView, id=instance_id)
        self.assertEqual(stale["row"]["state"], ih.STATE_ERROR)
        live = self._context(
            views.InstanceDetailView, id=instance_id, state="active"
        )
        self.assertEqual(live["row"]["state"], ih.STATE_ACTIVE)
        self.assertIsNone(live["row"]["fault"])
        self.assertEqual(live["row"]["issues"], [])
        # a running instance gets its charts
        self.assertIn("chart", live)
        html = self._render(
            views.InstanceDetailView, id=instance_id, state="active"
        )
        self.assertNotIn("The Compute API reports an error", html)

    def test_detail_without_a_state_list_uses_the_snapshot(self):
        instance_id = _id("db-0002")
        context = self._context(
            views.InstanceDetailView, id=instance_id, state="error"
        )
        self.assertEqual(context["row"]["state"], ih.STATE_ERROR)

    def test_chart_text_alternative_names_both_units(self):
        html = self._render(views.InstanceDetailView, id=_id("web-0001"))
        self.assertIn("critical above 20 %", html)


class NotRunningDetailTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_flagged_instance_opened_from_a_stopped_list(self):
        # web-0001 is flagged (running) in the snapshot, shut off since
        html = self._render(
            views.InstanceDetailView, id=_id("web-0001"), state="shutoff"
        )
        self.assertIn("Not running: stopped instances do not report", html)
        self.assertNotIn("ih-mini-", html)

    def test_instance_not_in_the_snapshot_opened_from_a_stopped_list(self):
        with mock.patch(
            "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_trends"
        ) as trends:
            html = self._render(
                views.InstanceDetailView, id=_id("api-0001"), state="shutoff"
            )
        trends.assert_not_called()
        self.assertIn("Not running", html)


class IncompleteListTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_announcement_and_hint_for_a_count_that_is_not_exact(self):
        # Nova filled the page and a search narrows it: more may match
        many = [
            dict(
                fake.fake_servers()[0],
                id=f"00000000-0000-4000-8000-{i:012d}",
                name=f"off-{i:03d}",
                status="SHUTOFF",
                power_state=4,
                task_state=None,
            )
            for i in range(ih.LIST_LIMIT + views.NOVA_EXTRA_ROWS)
        ]
        with mock.patch.object(
            views.inventory.FakeInventory,
            "list_servers_by_status",
            return_value=many,
        ):
            html = self._render(views.AttentionView, state="shutoff", q="off")
        self.assertIn(
            f"<span hidden data-ih-announce>First {ih.LIST_LIMIT} matching "
            "instances shown.",
            html,
        )
        self.assertIn("Narrow the list with the filters above.", html)


class JoinMemoryTests(_ViewTestCase):
    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def test_only_the_latest_joins_are_kept(self):
        real = views._InstanceHealthMixin.get_metrics_value
        step = [0]

        def newer(view):
            step[0] += 1
            value = real(view)
            return dict(value, fetched_at=value["fetched_at"] + step[0])

        with (
            mock.patch.object(
                views._InstanceHealthMixin, "get_metrics_value", newer
            ),
            mock.patch.object(views, "JOINS_KEPT", 3),
        ):
            for _attempt in range(6):
                self._context(views.SummaryView)
        self.assertEqual(len(views._JOINS), 3)

    def test_few_joins_are_kept_per_process(self):
        # each join holds up to 500 rows: the latest pairs of a few regions
        self.assertLessEqual(views.JOINS_KEPT, 16)


def _no_symptoms(*args, **kwargs):
    return {s: {} for s in ih.METRIC_SYMPTOMS}


def _first_card_class(html):
    return re.search(r'class="panel ih-card ([\w-]+)"', html).group(1)


class PartialDataEmptyStateTests(_ViewTestCase):
    """Nothing flagged while a source is missing is not "all fine"."""

    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms",
        side_effect=_no_symptoms,
    )
    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_nova_down_and_no_metric_symptoms(self, list_servers, _symptoms):
        list_servers.side_effect = RuntimeError("nova down")
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            attention = self._render(views.AttentionView)
            summary = self._render(views.SummaryView)
        self.assertIn(
            "No metric symptoms (instance states unavailable).", attention
        )
        self.assertNotIn("No instances need attention.", attention)
        self.assertEqual(_first_card_class(summary), "ih-unknown")
        self.assertNotIn("fa-check-circle ih-icon", summary)

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms",
        side_effect=RuntimeError("aetos down"),
    )
    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_metrics_down_and_no_state_issues(self, list_servers, _symptoms):
        list_servers.return_value = [
            s for s in fake.fake_servers() if s["name"].startswith("worker-")
        ]
        with self.assertLogs("grian_ui.cache", level="ERROR"):
            attention = self._render(views.AttentionView)
            summary = self._render(views.SummaryView)
        self.assertIn(
            "No instance state issues (metrics unavailable).", attention
        )
        self.assertNotIn("No instances need attention.", attention)
        self.assertEqual(_first_card_class(summary), "ih-unknown")

    @mock.patch(
        "grian_ui.datasource.fake.FakeTelemetryClient.get_instance_symptoms",
        side_effect=_no_symptoms,
    )
    @mock.patch(f"{VIEWS}.inventory.FakeInventory.list_servers")
    def test_both_sources_and_nothing_flagged(self, list_servers, _symptoms):
        list_servers.return_value = [
            s for s in fake.fake_servers() if s["name"].startswith("worker-")
        ]
        attention = self._render(views.AttentionView)
        summary = self._render(views.SummaryView)
        self.assertIn("No instances need attention.", attention)
        self.assertEqual(_first_card_class(summary), "ih-ok")


class StateParagraphTests(_ViewTestCase):
    """The detail explains each state issue once, by its own kind."""

    def setUp(self):
        super().setUp()
        views._JOINS.clear()
        self.addCleanup(views._JOINS.clear)

    def _detail(self, name, **changes):
        servers = [
            dict(s, **changes) if s["name"] == name else s
            for s in fake.fake_servers()
        ]
        with mock.patch.object(
            views.inventory.FakeInventory, "list_servers", return_value=servers
        ):
            return self._render(views.InstanceDetailView, id=_id(name))

    def test_error_and_stuck_task(self):
        html = self._detail("db-0002", task_state="deleting")
        self.assertEqual(html.count("The Compute API reports an error"), 1)
        self.assertEqual(html.count("for longer than expected"), 1)
        self.assertIn("task “deleting”", html)

    def test_crashed_and_stuck_task(self):
        html = self._detail(
            "etl-0002", power_state=ih.POWER_CRASHED, task_state="rebooting"
        )
        self.assertEqual(html.count("for longer than expected"), 1)
        self.assertIn("task “rebooting”", html)
        self.assertIn("Crashed", html)
