#
# SPDX-License-Identifier: Apache-2.0

from grian_ui import config
from grian_ui import instance_health as ih
from grian_ui.datasource import prometheus
from grian_ui_tests.unit import base
from grian_ui_tests.unit.test_datasource_prometheus import _make_client
from grian_ui_tests.unit.test_datasource_prometheus import _make_metric


A = "f6fb292b-1740-404b-81f7-c32413db8f65"
B = "c726b640-2a18-41e3-9fac-af2730c12410"
THRESHOLDS = dict(config.DEFAULT_INSTANCE_HEALTH_THRESHOLDS)


def _matrix(series):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": labels, "values": values}
                for labels, values in series
            ],
        },
    }


class ResourceTests(base.TestCase):
    def test_instance_id_from_resource(self):
        self.assertEqual(prometheus.instance_id_from_resource(A), A)
        self.assertEqual(prometheus.instance_id_from_resource(f"{A}-vda"), A)
        # interfaces: "<libvirt name>-<uuid>-<vnic>", the vnic has a dash
        self.assertEqual(
            prometheus.instance_id_from_resource(
                f"instance-0000000a-{A}-tap07f6ff0c-d4"
            ),
            A,
        )
        self.assertEqual(
            prometheus.instance_id_from_resource(f"instance-{B}-{A}-tap1-d4"),
            A,
        )
        self.assertIsNone(prometheus.instance_id_from_resource("pool-01"))
        self.assertIsNone(prometheus.instance_id_from_resource(None))

    def test_matchers(self):
        matchers = prometheus._instance_matchers([B, A, A])
        # sorted and de-duplicated (B < A)
        self.assertEqual(matchers["instance"], f'resource=~"{B}|{A}"')
        self.assertEqual(matchers["disk"], f'resource=~"({B}|{A})-.+"')
        self.assertEqual(matchers["interface"], f'resource=~".*({B}|{A}).*"')

    def test_matchers_reject_injection(self):
        self.assertIsNone(prometheus._instance_matchers(['"} or up{a="']))
        self.assertIsNone(prometheus._instance_matchers([]))
        matchers = prometheus._instance_matchers([A, ".*"])
        self.assertEqual(matchers["instance"], f'resource=~"{A}"')


class SymptomExprTests(base.TestCase):
    def test_cpu_is_percent_of_vcpus(self):
        expr = prometheus.instance_symptom_expr(ih.CPU, "10m")
        self.assertIn('rate({__name__=~"ceilometer_cpu_delay"}[10m])', expr)
        self.assertIn("/ 1e9 / on (instance_id)", expr)
        self.assertIn("ceilometer_vcpus", expr)
        self.assertTrue(expr.startswith("100 * sum by (instance_id)"))

    def test_memory_and_network_sum_all_series(self):
        memory = prometheus.instance_symptom_expr(ih.MEMORY, "15m")
        self.assertIn("ceilometer_memory_swap_(in|out)", memory)
        self.assertIn("[15m]", memory)
        network = prometheus.instance_symptom_expr(ih.NETWORK, "10m")
        self.assertIn(
            "ceilometer_network_(incoming|outgoing)_packets_(drop|error)",
            network,
        )

    def test_disk_is_worst_device(self):
        expr = prometheus.instance_symptom_expr(ih.DISK, "10m")
        self.assertTrue(expr.startswith("max by (instance_id)"))
        self.assertIn("_latency", expr)
        self.assertIn("_requests", expr)

    def test_matchers_are_applied_per_resource_format(self):
        matchers = prometheus._instance_matchers([A])
        cpu = prometheus.instance_symptom_expr(ih.CPU, "10m", matchers)
        self.assertEqual(cpu.count(matchers["instance"]), 2)
        net = prometheus.instance_symptom_expr(ih.NETWORK, "10m", matchers)
        self.assertIn(matchers["interface"], net)
        disk = prometheus.instance_symptom_expr(ih.DISK, "10m", matchers)
        self.assertEqual(disk.count(matchers["disk"]), 2)

    def test_not_a_metric_symptom(self):
        self.assertRaises(
            ValueError, prometheus.instance_symptom_expr, ih.STATE, "10m"
        )


class ReportingInstancesTests(base.TestCase):
    def test_parse(self):
        client, prom = _make_client()
        prom.query.return_value = [
            _make_metric(
                1,
                {
                    "resource": A,
                    "project": "p1",
                    "resource_name": f"web:1:instance-{A}",
                },
            ),
            _make_metric(
                1, {"resource": B, "project": "p2", "resource_name": "plain"}
            ),
            _make_metric(1, {"resource": "not-an-instance"}),
        ]
        reporting = client.get_reporting_instances()
        self.assertEqual(
            reporting,
            {
                A: {"name": "web:1", "project_id": "p1"},
                B: {"name": "plain", "project_id": "p2"},
            },
        )
        query = prom.query.call_args.kwargs["query"]
        self.assertEqual(
            query,
            "group by (resource, resource_name, project) "
            "(last_over_time(ceilometer_cpu[10m]))",
        )

    def test_window(self):
        client, prom = _make_client()
        prom.query.return_value = []
        client.get_reporting_instances("30m")
        self.assertIn("[30m]", prom.query.call_args.kwargs["query"])


class InstanceSymptomsTests(base.TestCase):
    def test_symptoms(self):
        client, prom = _make_client()

        def query(query):
            if "cpu_delay" in query:
                return [
                    _make_metric("12.5", {"instance_id": A}),
                    _make_metric("NaN", {"instance_id": B}),
                    # IDs end up in the page: UUIDs only
                    _make_metric("50", {"instance_id": "x');alert(1)//"}),
                ]
            if "swap" in query:
                return [_make_metric("3", {"instance_id": B})]
            if "packets" in query:
                return []
            if "latency" in query:
                return [
                    # the worse device first: the worst one is kept
                    _make_metric("80", {"resource": f"{A}-vdb"}),
                    _make_metric("30", {"resource": f"{A}-vda"}),
                    _make_metric("+Inf", {"resource": f"{B}-vda"}),
                    _make_metric("50", {"resource": "garbage"}),
                ]
            raise AssertionError(query)

        prom.query.side_effect = query
        symptoms = client.get_instance_symptoms(THRESHOLDS, "10m")
        self.assertEqual(symptoms[ih.CPU], {A: 12.5})
        self.assertEqual(symptoms[ih.MEMORY], {B: 3.0})
        self.assertEqual(symptoms[ih.NETWORK], {})
        self.assertEqual(
            symptoms[ih.DISK], {A: {"value": 80.0, "device": "vdb"}}
        )

    def test_queries_are_filtered_by_the_warning_thresholds(self):
        client, prom = _make_client()
        prom.query.return_value = []
        thresholds = dict(
            THRESHOLDS, cpu_wait_warning=7, disk_latency_warning=25
        )
        client.get_instance_symptoms(thresholds, "10m")
        queries = [c.kwargs["query"] for c in prom.query.call_args_list]
        self.assertEqual(len(queries), 4)
        self.assertTrue(any(q.endswith(") > 7") for q in queries))
        self.assertTrue(any(q.endswith(") > 25") for q in queries))
        self.assertEqual(sum(q.endswith(") > 0") for q in queries), 2)


class LastSeenTests(base.TestCase):
    def test_last_seen(self):
        client, prom = _make_client()
        prom.query.return_value = [_make_metric("1700000000", {"resource": A})]
        self.assertEqual(
            client.get_instance_last_seen([A, "bad"]), {A: 1700000000.0}
        )
        query = prom.query.call_args.kwargs["query"]
        self.assertIn(f'resource=~"{A}"', query)
        self.assertIn("[24h:1m]", query)

    def test_invalid_ids_do_not_query(self):
        client, prom = _make_client()
        self.assertEqual(client.get_instance_last_seen(["bad"]), {})
        prom.query.assert_not_called()


class TrendTests(base.TestCase):
    def test_instance_trends(self):
        client, prom = _make_client()
        prom._get.return_value = _matrix(
            [
                ({"field": "cpu", "instance_id": A}, [[60, "12.345"]]),
                ({"field": "disk", "instance_id": A}, [[60, "3"]]),
                ({"field": "bogus"}, [[60, "1"]]),
            ]
        )
        trends = client.get_instance_trends(A, "1h", "10m")
        self.assertEqual(
            trends,
            {
                "cpu": [[60.0, 12.35]],
                "memory": [],
                "disk": [[60.0, 3.0]],
                "network": [],
            },
        )
        query = prom._get.call_args.args[1]["query"]
        self.assertTrue(query.endswith(")[1h:1m]"))
        for symptom in ih.METRIC_SYMPTOMS:
            self.assertIn(f'"field", "{symptom}"', query)

    def test_trends_are_evaluated_at_the_grid_time(self):
        client, prom = _make_client()
        prom._get.return_value = _matrix([])
        client.get_instance_trends(A, "1h", "10m", at=1700000123.0)
        self.assertEqual(prom._get.call_args.args[1]["time"], "1700000123.000")
        client.get_symptom_trends(ih.CPU, [A], "1h", "10m", at=1700000456.0)
        self.assertEqual(prom._get.call_args.args[1]["time"], "1700000456.000")

    def test_instance_trends_invalid_id(self):
        client, prom = _make_client()
        self.assertEqual(
            client.get_instance_trends("x", "1h", "10m"),
            {s: [] for s in ih.METRIC_SYMPTOMS},
        )
        prom._get.assert_not_called()

    def test_symptom_trends(self):
        client, prom = _make_client()
        prom._get.return_value = _matrix(
            [
                ({"instance_id": A}, [[300, "1"]]),
                ({}, [[300, "2"]]),
            ]
        )
        trends = client.get_symptom_trends(ih.NETWORK, [A], "6h", "10m")
        self.assertEqual(trends, {A: [[300.0, 1.0]]})
        self.assertTrue(
            prom._get.call_args.args[1]["query"].endswith(")[6h:5m]")
        )

    def test_symptom_trends_unknown_range_and_symptom(self):
        client, prom = _make_client()
        prom._get.return_value = _matrix([])
        client.get_symptom_trends(ih.CPU, [A], "bogus", "10m")
        self.assertTrue(
            prom._get.call_args.args[1]["query"].endswith(")[6h:5m]")
        )
        prom._get.reset_mock()
        self.assertEqual(
            client.get_symptom_trends(ih.STATE, [A], "1h", "10m"), {}
        )
        prom._get.assert_not_called()


class SilenceWindowTests(base.TestCase):
    def test_silence_window_follows_a_longer_rate_window(self):
        self.assertEqual(ih.silence_window("5m"), "10m")
        self.assertEqual(ih.silence_window("10m"), "10m")
        self.assertEqual(ih.silence_window("30m"), "30m")
        self.assertEqual(ih.silence_window("1h"), "1h")

    def test_vcpus_lookback_follows_it(self):
        # the rate over the rate window, the vCPUs over the silence window
        expr = prometheus.instance_symptom_expr(ih.CPU, "5m")
        self.assertEqual((expr.count("[5m]"), expr.count("[10m]")), (1, 1))
        expr = prometheus.instance_symptom_expr(ih.CPU, "30m")
        self.assertEqual(expr.count("[30m]"), 2)


class IdleDiskTests(base.TestCase):
    def test_charts_read_an_idle_disk_as_zero(self):
        expr = prometheus.disk_latency_by_device_expr("10m", idle_zero=True)
        self.assertIn(" > 0) / 1e6) or (0 * sum by (resource)", expr)
        # the symptom query keeps the plain ratio (NaN is filtered out)
        plain = prometheus.instance_symptom_expr(ih.DISK, "10m")
        self.assertNotIn(" or (0 * ", plain)

    def test_trend_queries_use_it(self):
        client, prom = _make_client()
        prom._get.return_value = _matrix([])
        client.get_instance_trends(A, "6h", "10m")
        self.assertIn(" or (0 * ", prom._get.call_args.args[1]["query"])
        client.get_symptom_trends(ih.DISK, [A], "6h", "10m")
        self.assertIn(" or (0 * ", prom._get.call_args.args[1]["query"])
