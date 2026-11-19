from django.urls import re_path

from grian_ui.content.provisioning import views


urlpatterns = [
    re_path(r"^$", views.ProvisioningIndexView.as_view(), name="index"),
    re_path(r"^summary/$", views.SummaryView.as_view(), name="summary"),
    re_path(
        r"^boot-times/$", views.BootTimesView.as_view(), name="boot_times"
    ),
    re_path(r"^hosts/$", views.HostsView.as_view(), name="hosts"),
    re_path(
        r"^failed-builds/$",
        views.FailedBuildsView.as_view(),
        name="failed_builds",
    ),
    re_path(
        r"^slowest-boots/$",
        views.SlowestBootsView.as_view(),
        name="slowest_boots",
    ),
]
