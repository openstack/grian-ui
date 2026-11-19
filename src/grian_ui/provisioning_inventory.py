#
# SPDX-License-Identifier: Apache-2.0

"""
Compute and Identity API data of the Provisioning dashboard.

A failed build has no boot time sample, so failed builds come from the
Compute API: instances in ERROR that never launched. Instance flavors and
project names are not on the boot time metric either.

Every call is bounded: one page of at most FAILED_QUERY_LIMIT ERROR
instances per range, one lookup per instance among the slowest boots (each
instance once, see the views), the hypervisor list (to link the
nova-compute host of a boot to the hypervisor hostname Compute Host Health
uses) and the project list. The views share every result as a snapshot
per region for minutes, so the dashboard never lists every server.

The ``fake`` datasource uses :class:`FakeProvisioningInventory`.
"""

import abc
import datetime

from grian_ui import config


#: Compute API microversion: 2.47 embeds the flavor (with its name) in the
#: server, so failed builds and slow boots need no flavor lookups.
NOVA_MICROVERSION = "2.47"
#: Hypervisors read per Compute API request (os-hypervisors pages).
HYPERVISOR_PAGE = 1000
#: Longest fault message kept per failed build.
FAULT_MAX_LENGTH = 300


def epoch(value):
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


def iso(timestamp):
    """Epoch to the ISO 8601 form the Compute API filters take (UTC)."""
    return (
        datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        .replace(microsecond=0, tzinfo=None)
        .isoformat()
        + "Z"
    )


def flavor_name(server):
    """Flavor name of a novaclient Server (microversion >= 2.47)."""
    flavor = getattr(server, "flavor", None)
    if isinstance(flavor, dict):
        return flavor.get("original_name") or None
    return None


def compact_failed(server):
    """Keep the fields the dashboard needs from a failed novaclient Server."""
    fault = getattr(server, "fault", None)
    message = None
    if isinstance(fault, dict) and fault.get("message"):
        message = str(fault["message"]).strip()[:FAULT_MAX_LENGTH]
    return {
        "id": server.id,
        "name": getattr(server, "name", None),
        "project_id": getattr(server, "tenant_id", None),
        "flavor": flavor_name(server),
        "created": epoch(getattr(server, "created", None)),
        "fault": message,
    }


def add_hypervisor(hosts, hypervisor):
    """Add a novaclient Hypervisor to a service host -> names map."""
    service = getattr(hypervisor, "service", None)
    host = service.get("host") if isinstance(service, dict) else None
    name = getattr(hypervisor, "hypervisor_hostname", None)
    if host and name:
        names = hosts.setdefault(host, [])
        if name not in names:
            names.append(name)


def is_failed_build(server):
    """An instance in ERROR that never launched (not one that failed later)."""
    return getattr(server, "status", None) == "ERROR" and not getattr(
        server, "OS-SRV-USG:launched_at", None
    )


class ProvisioningInventory(abc.ABC):
    """Failed builds, flavors and project names."""

    @abc.abstractmethod
    def failed_builds(self, since, limit) -> dict:
        """
        Instances created since ``since`` (epoch) that failed to build.

        Returns:
            dict: ``{"builds": [...], "truncated": bool}``, the newest
            first; ``truncated`` when the Compute API returned ``limit``
            ERROR instances, so there may be more
        """

    @abc.abstractmethod
    def flavor(self, instance_id):
        """Flavor name of an instance (None when unknown or deleted)."""

    @abc.abstractmethod
    def hypervisor_hosts(self) -> dict:
        """
        nova-compute service host -> hypervisor hostnames.

        Returns:
            dict: ``{"compute-01": ["compute-01.example.com"]}``; a service
            host can run several hypervisors (e.g. Ironic)
        """

    @abc.abstractmethod
    def project_names(self) -> dict:
        """Project id -> name."""


class NovaProvisioningInventory(ProvisioningInventory):
    """Inventory read through Horizon's Nova and Keystone API layer."""

    def __init__(self, request):
        self.request = request

    def _client(self):
        # imported lazily: unit tests run without horizon
        from openstack_dashboard.api import _nova

        # Horizon's private client factory: the public helpers
        # (api.nova.get_novaclient_with_locked_status) cap the microversion
        # at 2.42, whose servers carry no flavor names
        return _nova.novaclient(self.request, version=NOVA_MICROVERSION)

    def failed_builds(self, since, limit):
        # changes-since filters on the last update, which for a failed
        # build is its failure: never older than its creation
        servers = self._client().servers.list(
            detailed=True,
            search_opts={
                "all_tenants": True,
                "status": "ERROR",
                "changes-since": iso(since),
            },
            limit=limit,
            sort_keys=["created_at"],
            sort_dirs=["desc"],
        )
        builds = [
            compact_failed(s)
            for s in servers
            if is_failed_build(s)
            and (epoch(getattr(s, "created", None)) or 0) >= since
        ]
        return {"builds": builds, "truncated": len(servers) >= limit}

    def flavor(self, instance_id):
        from novaclient import exceptions as nova_exceptions

        try:
            return flavor_name(self._client().servers.get(instance_id))
        except nova_exceptions.NotFound:
            # deleted since it booted
            return None

    def hypervisor_hosts(self):
        client = self._client()
        hosts, marker = {}, None
        while True:
            # the detailed list carries the service host
            page = client.hypervisors.list(
                detailed=True, marker=marker, limit=HYPERVISOR_PAGE
            )
            for hypervisor in page:
                add_hypervisor(hosts, hypervisor)
            if len(page) < HYPERVISOR_PAGE:
                return hosts
            marker = page[-1].id

    def project_names(self):
        from openstack_dashboard.api import keystone

        projects, _more = keystone.tenant_list(self.request, admin=True)
        return {p.id: p.name for p in projects}


class FakeProvisioningInventory(ProvisioningInventory):
    """Inventory matching the fake boots, for development and tests."""

    def failed_builds(self, since, limit):
        from grian_ui.datasource import fake_provisioning

        builds = [
            dict(b)
            for b in fake_provisioning.fake_failed_builds()
            if b["created"] >= since
        ]
        builds.sort(key=lambda b: b["created"], reverse=True)
        return {"builds": builds[:limit], "truncated": len(builds) >= limit}

    def flavor(self, instance_id):
        from grian_ui.datasource import fake_provisioning

        return fake_provisioning.fake_flavor(instance_id)

    def hypervisor_hosts(self):
        from grian_ui.datasource import fake_provisioning

        return {
            host: list(names)
            for host, names in fake_provisioning.fake_hypervisors().items()
        }

    def project_names(self):
        from grian_ui.datasource import fake_provisioning

        return dict(fake_provisioning.FAKE_PROJECTS)


def get_inventory(request):
    """Inventory for the configured datasource."""
    if config.get_datasource_type() == config.DATASOURCE_FAKE:
        return FakeProvisioningInventory()
    return NovaProvisioningInventory(request)
