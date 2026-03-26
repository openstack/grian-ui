import horizon

from django.utils.translation import gettext_lazy as _


class VolumeMetrics(horizon.Panel):
    name = _("Volume Metrics")
    slug = "volume_metrics"
    permissions = ("openstack.roles.admin",)
    urls = "grian_ui.content.volume_metrics.urls"
