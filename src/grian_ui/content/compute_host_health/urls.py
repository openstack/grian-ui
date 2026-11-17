from django.urls import re_path

from grian_ui.content.compute_host_health import views


urlpatterns = [
    re_path(r"^$", views.ComputeHostHealthIndexView.as_view(), name="index"),
    re_path(r"^summary/$", views.SummaryView.as_view(), name="summary"),
    re_path(
        r"^distribution/$",
        views.DistributionView.as_view(),
        name="distribution",
    ),
    re_path(
        r"^fleet-trend/$", views.FleetTrendView.as_view(), name="fleet_trend"
    ),
    re_path(r"^top-hosts/$", views.TopHostsView.as_view(), name="top_hosts"),
    re_path(r"^host/$", views.HostLookupView.as_view(), name="host"),
    re_path(
        r"^host-suggestions/$",
        views.HostSuggestionsView.as_view(),
        name="host_suggestions",
    ),
]
