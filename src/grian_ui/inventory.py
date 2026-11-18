#
# SPDX-License-Identifier: Apache-2.0

"""
Instance inventory from the OpenStack APIs (Nova, Keystone).

Ceilometer stops reporting an instance when it stops running, so stopped,
errored or stuck instances only exist in Nova. Metrics also carry project
IDs, not names. This module reads both through Horizon's API layer, with
the user's token, and returns compact dicts (see
:mod:`grian_ui.instance_health` for the format).

The ``fake`` datasource uses :class:`FakeInventory`, whose instances match
the fake telemetry.
"""

import abc
import datetime
import re

from grian_ui import config


#: Longest fault message kept per server.
FAULT_MAX_LENGTH = 300

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def is_instance_id(value):
    """True if ``value`` looks like a Nova instance UUID."""
    return bool(_UUID_RE.match(value or ""))


def _epoch(value):
    """Nova timestamp (``2026-11-18T09:58:12Z``, naive means UTC) to epoch."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def compact_server(server):
    """Keep the fields the dashboard needs from a novaclient Server."""
    fault = getattr(server, "fault", None)
    message = None
    if isinstance(fault, dict) and fault.get("message"):
        message = str(fault["message"])[:FAULT_MAX_LENGTH]
    return {
        "id": server.id,
        "name": getattr(server, "name", None),
        "project_id": getattr(server, "tenant_id", None),
        # hypervisor hostname: the name Compute Host Health uses
        "host": getattr(server, "OS-EXT-SRV-ATTR:hypervisor_hostname", None)
        or getattr(server, "OS-EXT-SRV-ATTR:host", None),
        "status": getattr(server, "status", None),
        "power_state": getattr(server, "OS-EXT-STS:power_state", None),
        "task_state": getattr(server, "OS-EXT-STS:task_state", None),
        "updated": _epoch(getattr(server, "updated", None)),
        "launched_at": _epoch(getattr(server, "OS-SRV-USG:launched_at", None)),
        "fault": message,
    }


class Inventory(abc.ABC):
    """Instance and project inventory."""

    @abc.abstractmethod
    def list_servers(self) -> list:
        """Every server of every project (compact dicts)."""

    @abc.abstractmethod
    def list_servers_by_status(
        self, status, project_id=None, name=None, instance_id=None, limit=25
    ) -> list:
        """
        At most ``limit`` servers with a Nova status, sorted by name.

        Args:
            status: Nova status, e.g. ``"SHUTOFF"``
            project_id: only this project
            name: only servers whose name contains this text
            instance_id: only the server with this ID
        """

    @abc.abstractmethod
    def project_names(self) -> dict:
        """Project id -> name."""


class NovaInventory(Inventory):
    """Inventory read through Horizon's Nova and Keystone API wrappers."""

    def __init__(self, request):
        self.request = request

    def _client(self):
        # imported lazily: unit tests run without horizon
        from openstack_dashboard.api import nova

        return nova.get_novaclient_with_locked_status(self.request)

    def list_servers(self):
        # limit=-1 makes novaclient follow every page (one request per
        # osapi_max_limit servers, 1000 by default)
        servers = self._client().servers.list(
            detailed=True, search_opts={"all_tenants": True}, limit=-1
        )
        return [compact_server(s) for s in servers]

    def list_servers_by_status(
        self, status, project_id=None, name=None, instance_id=None, limit=25
    ):
        search_opts = {"all_tenants": True, "status": status}
        if project_id:
            search_opts["project_id"] = project_id
        if name:
            # Nova matches the name as a regular expression
            search_opts["name"] = re.escape(name)
        if instance_id:
            search_opts["uuid"] = instance_id
        servers = self._client().servers.list(
            detailed=True,
            search_opts=search_opts,
            limit=limit,
            sort_keys=["display_name", "uuid"],
            sort_dirs=["asc", "asc"],
        )
        return [compact_server(s) for s in servers]

    def project_names(self):
        from openstack_dashboard.api import keystone

        projects, _more = keystone.tenant_list(self.request, admin=True)
        return {p.id: p.name for p in projects}


class FakeInventory(Inventory):
    """Inventory matching the fake telemetry, for development and tests."""

    def list_servers(self):
        from grian_ui.datasource import fake

        return [dict(s) for s in fake.fake_servers()]

    def list_servers_by_status(
        self, status, project_id=None, name=None, instance_id=None, limit=25
    ):
        name = (name or "").lower()
        servers = [
            s
            for s in self.list_servers()
            if s["status"] == status
            and (not project_id or s["project_id"] == project_id)
            and (not name or name in (s["name"] or "").lower())
            and (not instance_id or s["id"] == instance_id)
        ]
        servers.sort(key=lambda s: ((s["name"] or "").lower(), s["id"]))
        return servers[:limit]

    def project_names(self):
        from grian_ui.datasource import fake

        return dict(fake.FAKE_PROJECTS)


def get_inventory(request):
    """Inventory for the configured datasource."""
    if config.get_datasource_type() == config.DATASOURCE_FAKE:
        return FakeInventory()
    return NovaInventory(request)
