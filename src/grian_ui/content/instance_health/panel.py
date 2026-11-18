import horizon

from django.utils.translation import gettext_lazy as _


class InstanceHealth(horizon.Panel):
    name = _("Instance Health")
    slug = "instance_health"
    permissions = ("openstack.roles.admin",)
    urls = "grian_ui.content.instance_health.urls"
