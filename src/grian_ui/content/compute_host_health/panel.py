import horizon

from django.utils.translation import gettext_lazy as _


class ComputeHostHealth(horizon.Panel):
    name = _("Compute Host Health")
    slug = "compute_host_health"
    permissions = ("openstack.roles.admin",)
    urls = "grian_ui.content.compute_host_health.urls"
