from django.urls import re_path

from grian_ui.content.instance_health import views


urlpatterns = [
    re_path(r"^$", views.InstanceHealthIndexView.as_view(), name="index"),
    re_path(r"^summary/$", views.SummaryView.as_view(), name="summary"),
    re_path(r"^attention/$", views.AttentionView.as_view(), name="attention"),
    re_path(
        r"^instance/$", views.InstanceDetailView.as_view(), name="instance"
    ),
]
