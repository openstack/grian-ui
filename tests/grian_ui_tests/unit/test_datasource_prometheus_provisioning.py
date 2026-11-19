#
# SPDX-License-Identifier: Apache-2.0

from unittest import mock

from grian_ui.datasource import prometheus_provisioning
from grian_ui_tests.unit import base
from grian_ui_tests.unit.test_datasource_prometheus import _make_client
from grian_ui_tests.unit.test_datasource_prometheus import _make_metric


def _boot_metric(resource, value, **labels):
    defaults = {
        "resource": resource,
        "resource_name": f"vm-{resource}",
        "project": "p1",
        "vm_instance": "compute-01",
    }
    return _make_metric(value, dict(defaults, **labels))


class BootQueriesTests(base.TestCase):
    def test_queries(self):
        seconds, first_seen = prometheus_provisioning.boot_queries("7d")
        self.assertEqual(
            seconds,
            "max by (resource, resource_name, project, vm_instance) "
            "(last_over_time(ceilometer_compute_instance_booting_time[7d]))",
        )
        self.assertEqual(
            first_seen,
            "min by (resource) (min_over_time(timestamp("
            "ceilometer_compute_instance_booting_time)[7d:5m]))",
        )

    def test_unknown_range_uses_the_default(self):
        _seconds, first_seen = prometheus_provisioning.boot_queries("1y")
        self.assertIn("[7d:5m]", first_seen)


class GetBootsTests(base.TestCase):
    def _prom(self, first_seen, seconds):
        prom = mock.MagicMock()

        def query(query):
            if query.startswith("min by (resource)"):
                return first_seen
            return seconds

        prom.query.side_effect = query
        return prom

    def test_joins_seconds_and_first_seen(self):
        prom = self._prom(
            [_make_metric("1700000000.5", {"resource": "a"})],
            [_boot_metric("a", "12.5")],
        )
        boots = prometheus_provisioning.get_boots(prom, "24h")
        self.assertEqual(
            boots,
            [
                {
                    "id": "a",
                    "name": "vm-a",
                    "project_id": "p1",
                    "host": "compute-01",
                    "seconds": 12.5,
                    "at": 1700000000.5,
                }
            ],
        )
        self.assertEqual(prom.query.call_count, 2)

    def test_one_boot_per_instance(self):
        # the same boot exported twice (e.g. by two sg-core replicas with
        # other labels) counts once, with the higher value
        prom = self._prom(
            [_make_metric("1700000000", {"resource": "a"})],
            [
                _boot_metric("a", "10"),
                _boot_metric("a", "12", vm_instance="compute-01 "),
                _boot_metric("a", "11"),
            ],
        )
        boots = prometheus_provisioning.get_boots(prom, "24h")
        self.assertEqual(len(boots), 1)
        self.assertEqual(boots[0]["seconds"], 12.0)

    def test_skips_unusable_series(self):
        prom = self._prom(
            [
                _make_metric("1700000000", {"resource": "a"}),
                _make_metric("1700000000", {"resource": "b"}),
                _make_metric("1700000000", {"resource": "c"}),
                _make_metric("NaN", {"resource": "d"}),
                _make_metric("1700000000", {}),
            ],
            [
                _boot_metric("a", "NaN"),
                _boot_metric("b", "-1"),
                _make_metric("5", {"resource_name": "no-resource"}),
                # no first seen time
                _boot_metric("d", "5"),
                _boot_metric("e", "5"),
                # missing labels become None
                _make_metric("4", {"resource": "c"}),
            ],
        )
        boots = prometheus_provisioning.get_boots(prom, "24h")
        self.assertEqual(
            boots,
            [
                {
                    "id": "c",
                    "name": None,
                    "project_id": None,
                    "host": None,
                    "seconds": 4.0,
                    "at": 1700000000.0,
                }
            ],
        )

    def test_empty(self):
        prom = self._prom(None, None)
        self.assertEqual(prometheus_provisioning.get_boots(prom, "7d"), [])


class ClientTests(base.TestCase):
    def test_client_method(self):
        client, prom = _make_client()

        def query(query):
            if query.startswith("min by (resource)"):
                return [_make_metric("1700000000", {"resource": "a"})]
            return [_boot_metric("a", "3.5")]

        prom.query.side_effect = query
        boots = client.get_provisioning_boots("14d")
        self.assertEqual(boots[0]["seconds"], 3.5)
        queries = [c.kwargs["query"] for c in prom.query.call_args_list]
        self.assertTrue(any("[14d:10m]" in q for q in queries))
