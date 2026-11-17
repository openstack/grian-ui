#
# SPDX-License-Identifier: Apache-2.0

from grian_ui.datasource import api
from grian_ui.datasource import fake
from grian_ui_tests.unit import base


class GetClientTests(base.TestCase):
    def test_returns_fake_client(self):
        client = fake.get_client()
        self.assertIsInstance(client, fake.FakeTelemetryClient)

    def test_accepts_request_argument(self):
        client = fake.get_client(request=None)
        self.assertIsInstance(client, fake.FakeTelemetryClient)

    def test_implements_datasource_interface(self):
        client = fake.get_client()
        self.assertIsInstance(client, api.DatasourceClient)


class FakeTelemetryClientTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.client = fake.FakeTelemetryClient()

    def test_get_telemetry_overview_returns_dict(self):
        result = self.client.get_telemetry_overview()
        self.assertIsInstance(result, dict)

    def test_get_telemetry_overview_keys(self):
        result = self.client.get_telemetry_overview()
        expected_keys = {
            "total_metrics",
            "active_instances",
            "alerts_count",
            "cpu_usage_avg",
            "memory_usage_avg",
            "network_throughput",
            "disk_io_rate",
            "uptime_percentage",
            "total_storage",
            "allocated_storage",
            "last_updated",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_get_telemetry_overview_static_values(self):
        result = self.client.get_telemetry_overview()
        self.assertEqual(result["total_metrics"], 1247)
        self.assertEqual(result["active_instances"], 18)
        self.assertEqual(result["alerts_count"], 3)
        self.assertEqual(result["total_storage"], 10240)

    def test_get_volumes_count(self):
        self.assertEqual(self.client.get_volumes_count(), 120)

    def test_get_snapshots_count(self):
        self.assertEqual(self.client.get_snapshots_count(), 45)

    def test_get_storage_pools_returns_list(self):
        result = self.client.get_storage_pools()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_get_storage_pools_structure(self):
        pools = self.client.get_storage_pools()
        for pool in pools:
            self.assertIn("name", pool)
            self.assertIn("total", pool)
            self.assertIn("allocated", pool)
            self.assertIn("free", pool)
            self.assertIn("provisioned", pool)

    def test_get_storage_pools_values(self):
        pools = self.client.get_storage_pools()
        self.assertEqual(pools[0]["name"], "storage-pool-01")
        self.assertEqual(pools[1]["name"], "storage-pool-02")
        self.assertEqual(pools[0]["total"], 5120.0)

    def test_get_capacity_predictions_returns_list(self):
        result = self.client.get_capacity_predictions()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_get_capacity_predictions_structure(self):
        predictions = self.client.get_capacity_predictions()
        for pred in predictions:
            self.assertIn("name", pred)
            self.assertIn("current_free", pred)
            self.assertIn("predicted_value", pred)
            self.assertIn("days_until_full", pred)
            self.assertIn("will_run_out", pred)

    def test_get_capacity_predictions_values(self):
        predictions = self.client.get_capacity_predictions()
        self.assertFalse(predictions[0]["will_run_out"])
        self.assertIsNone(predictions[0]["days_until_full"])
        self.assertTrue(predictions[1]["will_run_out"])
        self.assertEqual(predictions[1]["days_until_full"], 24.5)


class FakeComputeHostTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.client = fake.get_client()

    def test_snapshot_shape(self):
        snapshot = self.client.get_compute_host_snapshot()
        self.assertEqual(len(snapshot["hosts"]), 24)
        self.assertEqual(len(snapshot["stale"]), 2)
        for host in snapshot["hosts"]:
            self.assertEqual(set(host), {"name", "cpu_avg", "iowait_avg"})
        for host in snapshot["stale"]:
            self.assertEqual(set(host), {"name", "last_seen"})

    def test_snapshot_contains_hosts_needing_attention(self):
        from grian_ui import compute_health
        from grian_ui import config

        summary = compute_health.summarize(
            self.client.get_compute_host_snapshot(),
            config.DEFAULT_COMPUTE_HOST_THRESHOLDS,
        )
        self.assertEqual(summary["critical"], 2)
        self.assertEqual(summary["warning"], 2)
        self.assertEqual(summary["stale"], 2)

    def test_details_only_for_requested_hosts(self):
        details = self.client.get_compute_host_details(
            ["compute-03", "unknown"]
        )
        self.assertEqual(list(details), ["compute-03"])
        detail = details["compute-03"]
        self.assertAlmostEqual(
            detail["user"] + detail["kernel"] + detail["iowait"],
            detail["cpu"],
            places=0,
        )

    def test_host_trends(self):
        trends = self.client.get_compute_host_trends(["compute-01"], "1h")
        points = trends["compute-01"]
        self.assertEqual(len(points), 60)
        timestamps = [p[0] for p in points]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertTrue(all(0 <= p[1] <= 100 for p in points))

    def test_fleet_trend(self):
        trend = self.client.get_compute_fleet_trend("6h")
        self.assertEqual(set(trend), {"p50", "p95", "max"})
        self.assertEqual(len(trend["p50"]), 72)
        for p50, p95, top in zip(trend["p50"], trend["p95"], trend["max"]):
            self.assertLessEqual(p50[1], p95[1])
            self.assertLessEqual(p95[1], top[1])
