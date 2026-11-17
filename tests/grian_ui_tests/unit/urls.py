#
# SPDX-License-Identifier: Apache-2.0

"""
URLconf for unit tests: mirrors horizon's "horizon:admin:<panel>" URL
namespaces so the panel templates can be rendered without horizon.
"""

from django.urls import include
from django.urls import path

from grian_ui.content.compute_host_health import urls as chh_urls


admin = (
    [
        path(
            "compute_host_health/",
            include((chh_urls.urlpatterns, "compute_host_health")),
        )
    ],
    "admin",
)
horizon = ([path("admin/", include(admin))], "horizon")

urlpatterns = [path("dashboard/", include(horizon))]
