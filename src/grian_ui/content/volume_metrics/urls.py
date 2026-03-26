from django.urls import re_path

from grian_ui.content.volume_metrics import views


urlpatterns = [
    re_path(r"^$", views.VolumesMetricsIndexView.as_view(), name="index"),
    re_path(
        r"^metrics-update/$",
        views.MetricsUpdateView.as_view(),
        name="metrics_update",
    ),
    re_path(
        r"^chart-update/$",
        views.ChartUpdateView.as_view(),
        name="chart_update",
    ),
    re_path(
        r"^capacity-prediction/$",
        views.CapacityPredictionView.as_view(),
        name="capacity_prediction",
    ),
]
