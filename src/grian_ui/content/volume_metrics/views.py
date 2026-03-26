from django.utils.translation import gettext_lazy as _
from django.views import generic

from grian_ui.datasource.api import get_datasource


class VolumesMetricsIndexView(generic.TemplateView):
    template_name = "admin/volume_metrics/index.html"
    page_title = _("Volume Metrics")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        client = get_datasource(self.request)

        # Get telemetry data from datasource
        try:
            context["overview"] = client.get_telemetry_overview()
            context["volumes"] = client.get_volumes_count()
            context["snapshots"] = client.get_snapshots_count()
            context["storage_pools"] = client.get_storage_pools()
            context["predictions"] = client.get_capacity_predictions()
        except Exception as e:
            context["error_message"] = f"Error loading telemetry data: {e}"
            context["data_loaded"] = False
            # Provide defaults on error
            context["overview"] = {}
            context["volumes"] = 0
            context["snapshots"] = 0
            context["storage_pools"] = []
            context["predictions"] = []

        return context


class MetricsUpdateView(generic.TemplateView):
    """HTMX endpoint for updating metrics cards."""

    template_name = "admin/volume_metrics/_metrics_cards.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = get_datasource(self.request)

        try:
            context["overview"] = client.get_telemetry_overview()
            context["volumes"] = client.get_volumes_count()
            context["snapshots"] = client.get_snapshots_count()
        except Exception:
            # Provide defaults on error
            context["overview"] = {}
            context["volumes"] = 0
            context["snapshots"] = 0

        return context


class ChartUpdateView(generic.TemplateView):
    """HTMX endpoint for updating the storage chart."""

    template_name = "admin/volume_metrics/_storage_chart.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = get_datasource(self.request)

        try:
            context["volumes"] = client.get_volumes_count()
            context["snapshots"] = client.get_snapshots_count()
        except Exception:
            # Provide defaults on error
            context["volumes"] = 0
            context["snapshots"] = 0

        return context


class CapacityPredictionView(generic.TemplateView):
    """HTMX endpoint for capacity prediction visualization."""

    template_name = "admin/volume_metrics/_capacity_prediction.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = get_datasource(self.request)

        try:
            context["predictions"] = client.get_capacity_predictions()
        except Exception:
            # Provide defaults on error
            context["predictions"] = []

        return context
