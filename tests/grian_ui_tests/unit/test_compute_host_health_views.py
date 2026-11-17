#
# SPDX-License-Identifier: Apache-2.0

import threading
import time

from unittest import mock

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory

from grian_ui.content.compute_host_health import views
from grian_ui.datasource import fake
from grian_ui_tests.unit import base


VIEWS = "grian_ui.content.compute_host_health.views"


class _ViewTestCase(base.TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = mock.patch(f"{VIEWS}.get_datasource")
        self.mock_get_ds = patcher.start()
        self.addCleanup(patcher.stop)
        # wrap the fake datasource so calls can be asserted
        self.client = mock.MagicMock(wraps=fake.get_client())
        self.mock_get_ds.return_value = self.client

    def render_context(self, view_class, **params):
        request = RequestFactory().get("/", params)
        request.user = mock.MagicMock(services_region="RegionOne")
        view = view_class()
        view.setup(request)
        return view.get_context_data()


class IndexViewTests(_ViewTestCase):
    def test_context(self):
        context = self.render_context(views.ComputeHostHealthIndexView)
        self.assertEqual(context["ranges"], ["1h", "6h", "24h", "7d"])
        self.assertEqual(context["default_range"], "6h")
        self.assertEqual(context["thresholds"]["cpu_critical"], 90)
        # the shell does not query the datasource
        self.mock_get_ds.assert_not_called()

    @mock.patch(f"{VIEWS}.config.get_compute_host_thresholds")
    def test_configuration_error(self, mock_thresholds):
        mock_thresholds.side_effect = views.config.ConfigurationError("bad")
        context = self.render_context(views.ComputeHostHealthIndexView)
        self.assertEqual(context["error_message"], "bad")


class SummaryViewTests(_ViewTestCase):
    def test_summary(self):
        context = self.render_context(views.SummaryView)
        summary = context["summary"]
        self.assertEqual(summary["known"], 26)
        self.assertEqual(summary["attention_total"], 6)
        stale_rows = [
            r for r in summary["attention"] if r["status"]["key"] == "stale"
        ]
        self.assertTrue(all(r["last_seen_age"] for r in stale_rows))

    def test_snapshot_is_cached_between_partials(self):
        self.render_context(views.SummaryView)
        self.render_context(views.DistributionView)
        self.render_context(views.TopHostsView)
        self.assertEqual(self.client.get_compute_host_snapshot.call_count, 1)

    def test_error_is_generic_and_logged(self):
        self.client.get_compute_host_snapshot.side_effect = Exception("boom")
        with self.assertLogs(views.LOG, level="ERROR") as logs:
            context = self.render_context(views.SummaryView)
        self.assertNotIn("boom", str(context["error_message"]))
        self.assertIn("Horizon logs", str(context["error_message"]))
        self.assertIn("boom", logs.output[0])
        self.assertNotIn("summary", context)

    def test_fetched_at(self):
        context = self.render_context(views.SummaryView)
        self.assertAlmostEqual(context["fetched_at"], time.time(), delta=5)

    def test_snapshot_ttl_is_shorter_than_refresh(self):
        # every refresh gets new data, computed once
        self.assertLess(views.SNAPSHOT_CACHE_TTL, views.REFRESH_SECONDS)


class DistributionViewTests(_ViewTestCase):
    def test_chart(self):
        context = self.render_context(views.DistributionView)
        chart = context["chart"]
        self.assertEqual(len(chart["labels"]), 5)
        self.assertEqual(sum(chart["counts"]), 24)
        self.assertEqual(chart["total"], 24)


class FleetTrendViewTests(_ViewTestCase):
    def test_range_parameter(self):
        context = self.render_context(views.FleetTrendView, range="24h")
        self.assertEqual(context["range_key"], "24h")
        self.client.get_compute_fleet_trend.assert_called_once_with("24h")
        self.assertTrue(context["has_data"])
        self.assertEqual(context["chart"]["critical"], 90)

    def test_invalid_range(self):
        context = self.render_context(views.FleetTrendView, range="1y")
        self.assertEqual(context["range_key"], "6h")


class TopHostsViewTests(_ViewTestCase):
    def test_default_busiest_cpu(self):
        context = self.render_context(views.TopHostsView)
        self.assertEqual(context["by"], "cpu")
        self.assertEqual(len(context["rows"]), 10)
        self.assertEqual(context["total"], 24)
        self.assertEqual(context["rows"][0]["name"], "compute-03")
        self.assertIn("detail", context["rows"][0])
        self.assertEqual(len(context["trends"]), 10)
        # per-host queries are limited to the displayed hosts
        names = self.client.get_compute_host_details.call_args.args[0]
        self.assertEqual(len(names), 10)

    def test_stale_does_not_query_details(self):
        context = self.render_context(views.TopHostsView, by="stale")
        self.assertEqual(
            [r["name"] for r in context["rows"]], ["compute-26", "compute-25"]
        )
        self.client.get_compute_host_details.assert_not_called()
        self.assertTrue(context["rows"][0]["last_seen_age"].endswith("ago"))

    def test_band(self):
        context = self.render_context(views.TopHostsView, by="band", band="4")
        self.assertEqual(context["band_label"], "≥ 90%")
        self.assertEqual([r["name"] for r in context["rows"]], ["compute-03"])

    def test_invalid_parameters(self):
        context = self.render_context(views.TopHostsView, by="evil", band="x")
        self.assertEqual(context["by"], "cpu")
        self.assertIsNone(context["band"])


class HostLookupViewTests(_ViewTestCase):
    def test_empty_name(self):
        context = self.render_context(views.HostLookupView)
        self.assertEqual(context["name"], "")
        self.mock_get_ds.assert_not_called()

    def test_unknown_host(self):
        context = self.render_context(views.HostLookupView, name="nope")
        self.assertFalse(context["found"])
        self.client.get_compute_host_details.assert_not_called()

    def test_reporting_host(self):
        context = self.render_context(
            views.HostLookupView, name="compute-10", range="1h"
        )
        self.assertTrue(context["found"])
        self.assertEqual(context["status"]["key"], "critical")
        self.assertEqual(context["chart"]["range"], "1h")
        self.assertEqual(len(context["chart"]["host"]), 60)
        self.assertTrue(context["last_seen_age"])

    def test_stale_host(self):
        context = self.render_context(views.HostLookupView, name="compute-25")
        self.assertEqual(context["status"]["key"], "stale")
        self.assertEqual(context["last_seen_age"], "18 minutes ago")
        self.client.get_compute_host_details.assert_not_called()


class HostSuggestionsViewTests(_ViewTestCase):
    def test_suggestions_from_cached_snapshot(self):
        self.render_context(views.SummaryView)  # warms the cache
        context = self.render_context(
            views.HostSuggestionsView, name="compute-1"
        )
        names = [r["name"] for r in context["suggestions"]]
        self.assertEqual(names[:3], ["compute-10", "compute-11", "compute-12"])
        self.assertEqual(self.client.get_compute_host_snapshot.call_count, 1)

    def test_rendered_options_are_escaped_and_bounded(self):
        context = self.render_context(views.HostSuggestionsView, name="")
        html = render_to_string(
            views.HostSuggestionsView.template_name, context
        )
        self.assertEqual(html.count("<option "), 20)
        self.assertIn('<option value="compute-03">Critical</option>', html)

    def test_error_returns_no_suggestions(self):
        self.client.get_compute_host_snapshot.side_effect = Exception("down")
        with self.assertLogs(views.LOG, level="ERROR"):
            context = self.render_context(views.HostSuggestionsView, name="c")
        self.assertEqual(context["suggestions"], [])


class CacheKeyTests(base.TestCase):
    def test_region_is_sanitized(self):
        request = mock.MagicMock()
        request.user.services_region = "Region One/2"
        key = views._cache_key(request, "snapshot")
        self.assertTrue(key.endswith(":Region_One_2"))
        self.assertIn(":snapshot:", key)
        # versioned so values cached by older code are never read
        self.assertIn(f":v{views.CACHE_VERSION}:", key)


class GetOrComputeTests(base.TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_computes_once_and_caches(self):
        compute = mock.Mock(return_value={"v": 1})
        self.assertEqual(views.get_or_compute("k", 30, compute), {"v": 1})
        self.assertEqual(views.get_or_compute("k", 30, compute), {"v": 1})
        compute.assert_called_once()
        self.assertIsNone(cache.get("k:lock"))

    def test_waits_for_lock_holder(self):
        cache.add("k:lock", True, 15)  # another request is computing
        compute = mock.Mock(return_value={"v": "mine"})

        def holder_finishes(_seconds):
            cache.set("k", {"v": "theirs"}, 30)
            cache.delete("k:lock")

        with mock.patch(f"{VIEWS}.time.sleep", side_effect=holder_finishes):
            value = views.get_or_compute("k", 30, compute)
        self.assertEqual(value, {"v": "theirs"})
        compute.assert_not_called()

    def test_holder_failure_makes_waiters_fail_fast(self):
        cache.add("k:lock", True, 15)
        compute = mock.Mock(return_value={"v": 1})

        def holder_fails(_seconds):
            cache.set("k:failed", True, 5)
            cache.delete("k:lock")

        with mock.patch(
            f"{VIEWS}.time.sleep", side_effect=holder_fails
        ) as sleep:
            self.assertRaises(
                views.DataUnavailable, views.get_or_compute, "k", 30, compute
            )
        compute.assert_not_called()
        self.assertEqual(sleep.call_count, 1)

    def test_waiter_takes_over_an_expired_lock(self):
        cache.add("k:lock", True, 15)
        compute = mock.Mock(return_value={"v": 1})

        def lock_expires(_seconds):
            cache.delete("k:lock")  # no value, no failure marker

        with mock.patch(f"{VIEWS}.time.sleep", side_effect=lock_expires):
            self.assertEqual(views.get_or_compute("k", 30, compute), {"v": 1})
        compute.assert_called_once()

    def test_timeout_raises_without_recomputing(self):
        cache.add("k:lock", True, 15)  # holder never finishes
        compute = mock.Mock(return_value={"v": 1})
        clock = iter([0, 0, 100])
        with (
            mock.patch(f"{VIEWS}.time.sleep"),
            mock.patch(
                f"{VIEWS}.time.monotonic", side_effect=lambda: next(clock)
            ),
        ):
            self.assertRaises(
                views.DataUnavailable, views.get_or_compute, "k", 30, compute
            )
        compute.assert_not_called()

    def test_failure_is_remembered_and_lock_released(self):
        compute = mock.Mock(side_effect=RuntimeError("down"))
        self.assertRaises(RuntimeError, views.get_or_compute, "k", 30, compute)
        self.assertIsNone(cache.get("k:lock"))
        self.assertTrue(cache.get("k:failed"))
        # the next partial of the same refresh fails fast
        self.assertRaises(
            views.DataUnavailable, views.get_or_compute, "k", 30, compute
        )
        compute.assert_called_once()

    def test_success_stores_last_good_copy(self):
        views.get_or_compute("k", 30, lambda: {"v": 1})
        self.assertEqual(cache.get("k:last"), {"v": 1})

    def test_recent_failure_serves_last_good_copy(self):
        cache.set("k:last", {"v": "old"}, 600)
        cache.set("k:failed", True, 5)
        compute = mock.Mock(return_value={"v": "new"})
        value = views.get_or_compute("k", 30, compute)
        self.assertEqual(value, {"v": "old", "is_stale_copy": True})
        compute.assert_not_called()

    def test_holder_failure_serves_last_good_copy(self):
        cache.set("k:last", {"v": "old"}, 600)
        compute = mock.Mock(side_effect=RuntimeError("down"))
        with self.assertLogs(views.LOG, level="ERROR"):
            value = views.get_or_compute("k", 30, compute)
        self.assertEqual(value, {"v": "old", "is_stale_copy": True})
        self.assertTrue(cache.get("k:failed"))

    def test_slow_holder_serves_last_good_copy_to_waiters(self):
        cache.set("k:last", {"v": "old"}, 600)
        cache.add("k:lock", True, 15)
        compute = mock.Mock(return_value={"v": "new"})
        clock = iter([0, views.LOCK_WAIT_SECONDS + 1])
        with mock.patch(
            f"{VIEWS}.time.monotonic", side_effect=lambda: next(clock)
        ):
            value = views.get_or_compute("k", 30, compute)
        self.assertEqual(value, {"v": "old", "is_stale_copy": True})
        compute.assert_not_called()

    def test_without_last_good_copy_waits_as_long_as_the_lock(self):
        cache.add("k:lock", True, 15)
        compute = mock.Mock(return_value={"v": 1})
        clock = iter([0, views.LOCK_WAIT_SECONDS + 1, views.LOCK_SECONDS])
        with (
            mock.patch(f"{VIEWS}.time.sleep") as sleep,
            mock.patch(
                f"{VIEWS}.time.monotonic", side_effect=lambda: next(clock)
            ),
        ):
            self.assertRaises(
                views.DataUnavailable, views.get_or_compute, "k", 30, compute
            )
        # still waiting after LOCK_WAIT_SECONDS, gave up at LOCK_SECONDS
        self.assertEqual(sleep.call_count, 1)
        compute.assert_not_called()

    def test_rechecks_value_after_winning_the_lock(self):
        compute = mock.Mock(return_value={"v": "mine"})
        real_add = cache.add

        def add_after_previous_holder_finished(key, *args, **kwargs):
            cache.set("k", {"v": "theirs"}, 30)
            return real_add(key, *args, **kwargs)

        with mock.patch.object(
            cache, "add", side_effect=add_after_previous_holder_finished
        ):
            value = views.get_or_compute("k", 30, compute)
        self.assertEqual(value, {"v": "theirs"})
        compute.assert_not_called()
        self.assertIsNone(cache.get("k:lock"))

    def _run_concurrently(self, compute, n=3):
        results = []

        def worker():
            start = time.monotonic()
            try:
                outcome = views.get_or_compute("k", 30, compute)
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
            results.append((outcome, time.monotonic() - start))

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_concurrent_requests_compute_once(self):
        calls = []

        def slow_compute():
            calls.append(1)
            time.sleep(0.3)
            return {"v": 1}

        results = self._run_concurrently(slow_compute)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r[0] for r in results], [{"v": 1}] * 3)

    def test_concurrent_failure_computes_once_and_fails_fast(self):
        calls = []

        def failing_compute():
            calls.append(1)
            time.sleep(0.3)
            raise RuntimeError("aetos down")

        results = self._run_concurrently(failing_compute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            sorted(r[0] for r in results),
            ["DataUnavailable", "DataUnavailable", "RuntimeError"],
        )
        # nobody waits for the full LOCK_WAIT_SECONDS
        self.assertLess(max(r[1] for r in results), 2)


class FleetTrendCacheTests(_ViewTestCase):
    def test_shared_between_fleet_chart_and_lookup(self):
        self.render_context(views.FleetTrendView, range="6h")
        self.render_context(
            views.HostLookupView, name="compute-01", range="6h"
        )
        self.client.get_compute_fleet_trend.assert_called_once_with("6h")

    def test_cached_per_range(self):
        self.render_context(views.FleetTrendView, range="6h")
        self.render_context(views.FleetTrendView, range="24h")
        self.assertEqual(self.client.get_compute_fleet_trend.call_count, 2)

    def test_every_range_has_a_ttl(self):
        self.assertEqual(
            set(views.FLEET_TREND_CACHE_TTL), set(views.compute_health.RANGES)
        )


class TemplateRenderTests(_ViewTestCase):
    """Render the partials with real contexts (fake datasource)."""

    def render(self, view_class, **params):
        context = self.render_context(view_class, **params)
        return render_to_string(view_class.template_name, context)

    def test_summary(self):
        html = self.render(views.SummaryView)
        self.assertIn("Hosts reporting", html)
        self.assertIn("6 hosts need attention", html)
        self.assertIn('data-chh-host="compute-03"', html)
        self.assertIn("chh-clock", html)

    def test_summary_single_host_down_keeps_cards(self):
        self.client.get_compute_host_snapshot.return_value = {
            "hosts": [],
            "stale": [{"name": "a", "last_seen": time.time() - 900}],
        }
        html = self.render(views.SummaryView)
        self.assertIn("No compute host is reporting", html)
        self.assertIn("The only known host stopped reporting.", html)
        self.assertNotIn("telemetry pipeline is down", html)
        # the cards and attention list are still rendered
        self.assertIn("Hosts reporting", html)
        self.assertIn('data-chh-host="a"', html)

    def test_summary_pipeline_down(self):
        now = time.time()
        self.client.get_compute_host_snapshot.return_value = {
            "hosts": [],
            "stale": [
                {"name": f"h{i}", "last_seen": now - 900 - i * 30}
                for i in range(4)
            ],
        }
        html = self.render(views.SummaryView)
        self.assertIn("telemetry pipeline is down", html)
        self.assertIn("Hosts reporting", html)

    def test_summary_shows_stale_data_warning(self):
        request_key = (
            "grian_ui:compute_host_health:v{}:snapshot:fake:RegionOne".format(
                views.CACHE_VERSION
            )
        )
        cache.set(
            request_key + ":last",
            {
                "hosts": [{"name": "a", "cpu_avg": 10.0, "iowait_avg": 0.0}],
                "stale": [],
                "fetched_at": time.time() - 120,
            },
            600,
        )
        cache.set(request_key + ":failed", True, 5)
        html = self.render(views.SummaryView)
        self.assertIn("could not be refreshed", html)
        self.assertRegex(html, r'class="chh-clock" data-ts="\d+"')
        # every partial served the stale snapshot says so
        for view_class, params in (
            (views.DistributionView, {}),
            (views.TopHostsView, {}),
            (views.HostLookupView, {"name": "a"}),
        ):
            self.assertIn(
                "Could not be refreshed", self.render(view_class, **params)
            )
        self.client.get_compute_host_snapshot.assert_not_called()

    def test_summary_empty(self):
        self.client.get_compute_host_snapshot.return_value = {
            "hosts": [],
            "stale": [],
        }
        html = self.render(views.SummaryView)
        self.assertIn("No compute host metrics available", html)

    def test_summary_error(self):
        self.client.get_compute_host_snapshot.side_effect = Exception("x")
        with self.assertLogs(views.LOG, level="ERROR"):
            html = self.render(views.SummaryView)
        self.assertIn("alert-danger", html)

    def test_distribution(self):
        html = self.render(views.DistributionView)
        self.assertRegex(html, r'id="chh-dist-data-[0-9a-f]{12}"')
        # keyboard-accessible band buttons
        self.assertIn("chh.setTop('band', 4)", html)

    def test_element_ids_are_unique_per_response(self):
        """
        Regression: re-rendered partials must not reuse element ids.

        htmx "settles" a new element with the attributes of the old element
        that has the same id, after the inline script drew the chart; that
        reset the canvas size and cleared the chart on every range change.
        """
        import re

        for view_class, params in (
            (views.FleetTrendView, {"range": "6h"}),
            (views.DistributionView, {}),
            (views.TopHostsView, {}),
            (views.HostLookupView, {"name": "compute-10"}),
        ):
            first = set(
                re.findall(r'id="([^"]+)"', self.render(view_class, **params))
            )
            second = set(
                re.findall(r'id="([^"]+)"', self.render(view_class, **params))
            )
            self.assertTrue(first, view_class.__name__)
            self.assertFalse(first & second, view_class.__name__)

    def test_fleet_trend_has_gaps_as_null(self):
        html = self.render(views.FleetTrendView, range="1h")
        self.assertRegex(html, r'id="chh-fleet-data-[0-9a-f]{12}"')
        self.assertNotIn("NaN", html)

    def test_top_hosts(self):
        html = self.render(views.TopHostsView)
        self.assertIn('data-chh-spark="compute-03"', html)
        self.assertIn("Showing 10 of 24 matching hosts.", html)
        # widths are not localized (CSS needs a dot)
        self.assertRegex(html, r"width: \d+(\.\d)?%;")

    def test_top_hosts_stale(self):
        html = self.render(views.TopHostsView, by="stale")
        self.assertIn("compute-26", html)
        self.assertIn("minutes ago", html)

    def test_host_lookup(self):
        html = self.render(views.HostLookupView, name="compute-10", range="1h")
        self.assertRegex(html, r'id="chh-host-data-[0-9a-f]{12}"')
        self.assertIn(
            "/dashboard/admin/compute_host_health/host/?name=compute-10", html
        )

    def test_host_lookup_unknown_is_escaped(self):
        html = self.render(views.HostLookupView, name="<b>x</b>")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)

    def test_host_lookup_empty(self):
        html = self.render(views.HostLookupView)
        self.assertIn("chh.destroyChart('host')", html)
