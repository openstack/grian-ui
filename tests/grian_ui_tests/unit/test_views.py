#
# SPDX-License-Identifier: Apache-2.0

from unittest import mock

from grian_ui.content.volume_metrics import views
from grian_ui_tests.unit import base


def _make_mock_client():
    client = mock.MagicMock()
    client.get_telemetry_overview.return_value = {"cpu_usage_avg": 50.0}
    client.get_volumes_count.return_value = 10
    client.get_snapshots_count.return_value = 5
    client.get_storage_pools.return_value = [{"name": "pool-01"}]
    client.get_capacity_predictions.return_value = [
        {"name": "pool-01", "will_run_out": False}
    ]
    return client


class VolumesMetricsIndexViewTests(base.TestCase):
    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data(self, mock_get_ds):
        client = _make_mock_client()
        mock_get_ds.return_value = client

        view = views.VolumesMetricsIndexView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["overview"], {"cpu_usage_avg": 50.0})
        self.assertEqual(context["volumes"], 10)
        self.assertEqual(context["snapshots"], 5)
        self.assertEqual(context["storage_pools"], [{"name": "pool-01"}])
        self.assertEqual(len(context["predictions"]), 1)
        self.assertIn("page_title", context)

    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data_on_error(self, mock_get_ds):
        client = mock.MagicMock()
        client.get_telemetry_overview.side_effect = Exception("fail")
        mock_get_ds.return_value = client

        view = views.VolumesMetricsIndexView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertIn("error_message", context)
        self.assertFalse(context["data_loaded"])
        self.assertEqual(context["overview"], {})
        self.assertEqual(context["volumes"], 0)
        self.assertEqual(context["snapshots"], 0)
        self.assertEqual(context["storage_pools"], [])
        self.assertEqual(context["predictions"], [])

    def test_template_name(self):
        self.assertEqual(
            views.VolumesMetricsIndexView.template_name,
            "admin/volume_metrics/index.html",
        )


class MetricsUpdateViewTests(base.TestCase):
    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data(self, mock_get_ds):
        client = _make_mock_client()
        mock_get_ds.return_value = client

        view = views.MetricsUpdateView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["overview"], {"cpu_usage_avg": 50.0})
        self.assertEqual(context["volumes"], 10)
        self.assertEqual(context["snapshots"], 5)

    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data_on_error(self, mock_get_ds):
        client = mock.MagicMock()
        client.get_telemetry_overview.side_effect = Exception("fail")
        mock_get_ds.return_value = client

        view = views.MetricsUpdateView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["overview"], {})
        self.assertEqual(context["volumes"], 0)
        self.assertEqual(context["snapshots"], 0)

    def test_template_name(self):
        self.assertEqual(
            views.MetricsUpdateView.template_name,
            "admin/volume_metrics/_metrics_cards.html",
        )


class ChartUpdateViewTests(base.TestCase):
    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data(self, mock_get_ds):
        client = _make_mock_client()
        mock_get_ds.return_value = client

        view = views.ChartUpdateView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["volumes"], 10)
        self.assertEqual(context["snapshots"], 5)

    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data_on_error(self, mock_get_ds):
        client = mock.MagicMock()
        client.get_volumes_count.side_effect = Exception("fail")
        mock_get_ds.return_value = client

        view = views.ChartUpdateView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["volumes"], 0)
        self.assertEqual(context["snapshots"], 0)

    def test_template_name(self):
        self.assertEqual(
            views.ChartUpdateView.template_name,
            "admin/volume_metrics/_storage_chart.html",
        )


class CapacityPredictionViewTests(base.TestCase):
    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data(self, mock_get_ds):
        client = _make_mock_client()
        mock_get_ds.return_value = client

        view = views.CapacityPredictionView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(len(context["predictions"]), 1)

    @mock.patch("grian_ui.content.volume_metrics.views.get_datasource")
    def test_get_context_data_on_error(self, mock_get_ds):
        client = mock.MagicMock()
        client.get_capacity_predictions.side_effect = Exception("fail")
        mock_get_ds.return_value = client

        view = views.CapacityPredictionView()
        view.request = mock.MagicMock()
        view.kwargs = {}

        context = view.get_context_data()

        self.assertEqual(context["predictions"], [])

    def test_template_name(self):
        self.assertEqual(
            views.CapacityPredictionView.template_name,
            "admin/volume_metrics/_capacity_prediction.html",
        )
