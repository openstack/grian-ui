import horizon

from django.utils.translation import gettext_lazy as _


class Provisioning(horizon.Panel):
    name = _("Provisioning")
    slug = "provisioning"
    permissions = ("openstack.roles.admin",)
    urls = "grian_ui.content.provisioning.urls"
