#
# SPDX-License-Identifier: Apache-2.0

from unittest import mock

from grian_ui.datasource import api
from grian_ui_tests.unit import base


def _make_metric(value, labels=None):
    """Create a mock Prometheus metric result."""
    metric = mock.MagicMock()
    metric.value = value
    metric.labels = labels or {}
    return metric


def _make_client(query_map=None):
    """Create a PrometheusTelemetryClient with a mocked prom client."""
    with (
        mock.patch("grian_ui.datasource.prometheus.config") as mock_config,
        mock.patch("grian_ui.datasource.prometheus.v3"),
        mock.patch("grian_ui.datasource.prometheus.session"),
        mock.patch(
            "grian_ui.datasource.prometheus.obs_client_utils"
        ) as mock_obs,
    ):
        mock_config.get_openstack_keystone_url.return_value = (
            "http://keystone:5000/v3"
        )
        mock_config.get_openstack_endpoint_type.return_value = "publicURL"

        request = mock.MagicMock()
        request.user.token.id = "test-token"
        request.user.project_id = "test-project"
        request.user.domain_id = "test-domain"
        request.user.services_region = "RegionOne"

        from grian_ui.datasource import prometheus

        client = prometheus.get_client(request)

        prom_client = mock_obs.get_prom_client_from_keystone.return_value
        if query_map:
            prom_client.query.side_effect = lambda query: query_map.get(
                query, []
            )

        return client, prom_client


class PrometheusTelemetryClientInitTests(base.TestCase):
    @mock.patch("grian_ui.datasource.prometheus.obs_client_utils")
    @mock.patch("grian_ui.datasource.prometheus.session")
    @mock.patch("grian_ui.datasource.prometheus.v3")
    @mock.patch("grian_ui.datasource.prometheus.config")
    def test_init_creates_client(
        self, mock_config, mock_v3, mock_session, mock_obs
    ):
        mock_config.get_openstack_keystone_url.return_value = (
            "http://keystone:5000/v3"
        )
        mock_config.get_openstack_endpoint_type.return_value = "publicURL"

        request = mock.MagicMock()
        request.user.token.id = "test-token"
        request.user.project_id = "test-project"
        request.user.domain_id = "test-domain"
        request.user.services_region = "RegionOne"

        from grian_ui.datasource import prometheus

        client = prometheus.PrometheusTelemetryClient(request)

        mock_v3.Token.assert_called_once_with(  # nosec B106
            auth_url="http://keystone:5000/v3",
            token="test-token",
            project_id="test-project",
            domain_id="test-domain",
        )
        mock_obs.get_prom_client_from_keystone.assert_called_once()
        self.assertIsInstance(client, api.DatasourceClient)


class GetTelemetryOverviewTests(base.TestCase):
    def test_returns_metrics(self):
        query_map = {
            "sum(ceilometer_vcpus)": [_make_metric(64)],
            "sum(ceilometer_memory_usage)": [_make_metric(32768)],
            "sum(ceilometer_volume_provider_pool_capacity_total)": [
                _make_metric(10240)
            ],
            "sum(ceilometer_volume_provider_pool_capacity_allocated)": [
                _make_metric(7000)
            ],
        }
        client, _ = _make_client(query_map)

        result = client.get_telemetry_overview()

        self.assertEqual(result["cpu_usage_avg"], 64.0)
        self.assertEqual(result["memory_usage_avg"], 32.0)
        self.assertEqual(result["total_storage"], 10240)
        self.assertEqual(result["allocated_storage"], 7000)

    def test_empty_results_return_zeros(self):
        client, prom = _make_client()
        prom.query.return_value = []

        result = client.get_telemetry_overview()

        self.assertEqual(result["cpu_usage_avg"], 0.0)
        self.assertEqual(result["memory_usage_avg"], 0.0)
        self.assertEqual(result["total_storage"], 0)
        self.assertEqual(result["allocated_storage"], 0)

    def test_exception_returns_partial_data_with_error(self):
        client, prom = _make_client()
        prom.query.side_effect = Exception("connection refused")

        result = client.get_telemetry_overview()

        self.assertIn("error", result)
        self.assertIn("connection refused", result["error"])


class GetVolumesCountTests(base.TestCase):
    def test_returns_count(self):
        client, prom = _make_client()
        prom.query.return_value = [_make_metric(42)]

        result = client.get_volumes_count()

        self.assertEqual(result, 42)
        prom.query.assert_called_once_with(
            query="count(last_over_time(ceilometer_volume_size[10m]))"
        )

    def test_empty_result_returns_zero(self):
        client, prom = _make_client()
        prom.query.return_value = []

        self.assertEqual(client.get_volumes_count(), 0)

    def test_none_result_returns_zero(self):
        client, prom = _make_client()
        prom.query.return_value = None

        self.assertEqual(client.get_volumes_count(), 0)


class GetSnapshotsCountTests(base.TestCase):
    def test_returns_count(self):
        client, prom = _make_client()
        prom.query.return_value = [_make_metric(15)]

        result = client.get_snapshots_count()

        self.assertEqual(result, 15)
        prom.query.assert_called_once_with(
            query="count(last_over_time(ceilometer_volume_snapshot_size[10m]))"
        )

    def test_empty_result_returns_zero(self):
        client, prom = _make_client()
        prom.query.return_value = []

        self.assertEqual(client.get_snapshots_count(), 0)


class GetStoragePoolsTests(base.TestCase):
    def test_returns_pools(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
            'ceilometer_volume_provider_pool_capacity_allocated{resource="pool-01"}': [
                _make_metric(4096.0)
            ],
            'ceilometer_volume_provider_pool_capacity_free{resource="pool-01"}': [
                _make_metric(1024.0)
            ],
            'ceilometer_volume_provider_pool_capacity_provisioned{resource="pool-01"}': [
                _make_metric(6000.0)
            ],
        }
        client, _ = _make_client(query_map)

        result = client.get_storage_pools()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "pool-01")
        self.assertEqual(result[0]["total"], 5120.0)
        self.assertEqual(result[0]["allocated"], 4096.0)
        self.assertEqual(result[0]["free"], 1024.0)
        self.assertEqual(result[0]["provisioned"], 6000.0)

    def test_empty_result_returns_empty_list(self):
        client, prom = _make_client()
        prom.query.return_value = None

        self.assertEqual(client.get_storage_pools(), [])

    def test_exception_returns_empty_list(self):
        client, prom = _make_client()
        prom.query.side_effect = Exception("timeout")

        self.assertEqual(client.get_storage_pools(), [])

    def test_missing_sub_metrics_default_to_zero(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
        }
        client, _ = _make_client(query_map)

        result = client.get_storage_pools()

        self.assertEqual(result[0]["allocated"], 0.0)
        self.assertEqual(result[0]["free"], 0.0)
        self.assertEqual(result[0]["provisioned"], 0.0)


class GetCapacityPredictionsTests(base.TestCase):
    def test_pool_predicted_to_fill(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        predict_query = (
            'predict_linear(ceilometer_volume_provider_pool_capacity_free'
            '{resource="pool-01"}[7d], 30 * 86400)'
        )
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
            'ceilometer_volume_provider_pool_capacity_free{resource="pool-01"}': [
                _make_metric(1000.0)
            ],
            predict_query: [_make_metric(-500.0)],
        }
        client, _ = _make_client(query_map)

        result = client.get_capacity_predictions()

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["will_run_out"])
        self.assertIsNotNone(result[0]["days_until_full"])
        self.assertEqual(result[0]["predicted_value"], -500.0)
        self.assertEqual(result[0]["current_free"], 1000.0)

    def test_pool_not_predicted_to_fill(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        predict_query = (
            'predict_linear(ceilometer_volume_provider_pool_capacity_free'
            '{resource="pool-01"}[7d], 30 * 86400)'
        )
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
            'ceilometer_volume_provider_pool_capacity_free{resource="pool-01"}': [
                _make_metric(2000.0)
            ],
            predict_query: [_make_metric(1500.0)],
        }
        client, _ = _make_client(query_map)

        result = client.get_capacity_predictions()

        self.assertFalse(result[0]["will_run_out"])
        self.assertIsNone(result[0]["days_until_full"])

    def test_no_prediction_data(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
            'ceilometer_volume_provider_pool_capacity_free{resource="pool-01"}': [
                _make_metric(2000.0)
            ],
        }
        client, _ = _make_client(query_map)

        result = client.get_capacity_predictions()

        self.assertFalse(result[0]["will_run_out"])
        self.assertIsNone(result[0]["predicted_value"])

    def test_empty_total_returns_empty_list(self):
        client, prom = _make_client()
        prom.query.return_value = None

        self.assertEqual(client.get_capacity_predictions(), [])

    def test_exception_returns_empty_list(self):
        client, prom = _make_client()
        prom.query.side_effect = Exception("timeout")

        self.assertEqual(client.get_capacity_predictions(), [])

    def test_days_until_full_calculation(self):
        pool_metric = _make_metric(5120.0, {"resource": "pool-01"})
        predict_query = (
            'predict_linear(ceilometer_volume_provider_pool_capacity_free'
            '{resource="pool-01"}[7d], 30 * 86400)'
        )
        query_map = {
            "ceilometer_volume_provider_pool_capacity_total": [pool_metric],
            'ceilometer_volume_provider_pool_capacity_free{resource="pool-01"}': [
                _make_metric(1500.0)
            ],
            predict_query: [_make_metric(-1500.0)],
        }
        client, _ = _make_client(query_map)

        result = client.get_capacity_predictions()

        # decline_per_day = (1500 - (-1500)) / 30 = 100
        # days_until_full = 1500 / 100 = 15.0
        self.assertEqual(result[0]["days_until_full"], 15.0)


class NumberTests(base.TestCase):
    def test_number(self):
        from grian_ui.datasource import prometheus

        self.assertEqual(prometheus.number("42.04"), 42.04)
        self.assertEqual(prometheus.number("42.04", 1), 42.0)
        self.assertEqual(prometheus.number(3), 3.0)
        self.assertIsNone(prometheus.number("NaN"))
        self.assertIsNone(prometheus.number("+Inf"))
        self.assertIsNone(prometheus.number("-Inf"))
        self.assertIsNone(prometheus.number(None))
        self.assertIsNone(prometheus.number("abc"))


class QueryMatrixTests(base.TestCase):
    def _matrix(self, series):
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

    def test_decodes_series_and_drops_non_finite_values(self):
        client, prom = _make_client()
        prom._get.return_value = self._matrix(
            [
                ({"a": "1"}, [[100, "1.04"], [160, "NaN"], [220, "2"]]),
                ({"a": "2"}, []),
            ]
        )
        result = client.query_matrix("up[1h:1m]")
        prom._get.assert_called_once_with("query", {"query": "up[1h:1m]"})
        self.assertEqual(
            result,
            [({"a": "1"}, [[100.0, 1.04], [220.0, 2.0]]), ({"a": "2"}, [])],
        )

    def test_evaluation_time(self):
        client, prom = _make_client()
        prom._get.return_value = self._matrix([])
        client.query_matrix("up[1h:1m]", time=1700000000.25)
        prom._get.assert_called_once_with(
            "query", {"query": "up[1h:1m]", "time": "1700000000.250"}
        )

    def test_ndigits(self):
        client, prom = _make_client()
        prom._get.return_value = self._matrix([({}, [[1, "1.23456"]])])
        self.assertEqual(
            client.query_matrix("x", ndigits=3), [({}, [[1.0, 1.235]])]
        )

    def test_ignores_vector_results(self):
        client, prom = _make_client()
        prom._get.return_value = {
            "status": "success",
            "data": {"resultType": "vector", "result": []},
        }
        with self.assertLogs("grian_ui.datasource.prometheus", "WARNING"):
            self.assertEqual(client.query_matrix("up"), [])


class SessionTimeoutTests(base.TestCase):
    def test_aetos_requests_time_out(self):
        from grian_ui.datasource import prometheus

        with (
            mock.patch("grian_ui.datasource.prometheus.session") as sess,
            mock.patch("grian_ui.datasource.prometheus.config"),
            mock.patch("grian_ui.datasource.prometheus.v3"),
            mock.patch("grian_ui.datasource.prometheus.obs_client_utils"),
        ):
            prometheus.PrometheusTelemetryClient(mock.MagicMock())
        sess.Session.assert_called_once()
        self.assertEqual(
            sess.Session.call_args.kwargs["timeout"], prometheus.QUERY_TIMEOUT
        )
