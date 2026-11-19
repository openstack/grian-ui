#
# SPDX-License-Identifier: Apache-2.0

import sys
import types

from unittest import mock

from django.test import override_settings
from novaclient import exceptions as nova_exceptions

from grian_ui import provisioning_inventory as inventory
from grian_ui.datasource import fake_provisioning
from grian_ui_tests.unit import base


def _server(**attrs):
    server = types.SimpleNamespace(
        id="i1",
        name="vm",
        tenant_id="p1",
        status="ERROR",
        created="2026-11-18T09:59:34Z",
        flavor={"original_name": "m1.tiny", "vcpus": 1},
        fault={"message": " No valid host was found. ", "code": 500},
        **{"OS-SRV-USG:launched_at": None},
    )
    for key, value in attrs.items():
        setattr(server, key, value)
    return server


class HelpersTests(base.TestCase):
    def test_epoch(self):
        self.assertEqual(inventory.epoch("1970-01-01T00:01:00Z"), 60.0)
        self.assertEqual(inventory.epoch("1970-01-01T00:01:00.000000"), 60.0)
        self.assertIsNone(inventory.epoch(None))
        self.assertIsNone(inventory.epoch("yesterday"))

    def test_iso(self):
        self.assertEqual(inventory.iso(60.7), "1970-01-01T00:01:00Z")

    def test_flavor_name(self):
        self.assertEqual(inventory.flavor_name(_server()), "m1.tiny")
        # older microversions only have the flavor id
        self.assertIsNone(inventory.flavor_name(_server(flavor={"id": "1"})))
        self.assertIsNone(inventory.flavor_name(_server(flavor=None)))

    def test_compact_failed(self):
        compact = inventory.compact_failed(
            _server(fault={"message": "x" * 1000, "details": "trace"})
        )
        self.assertEqual(compact["id"], "i1")
        self.assertEqual(compact["project_id"], "p1")
        self.assertEqual(compact["flavor"], "m1.tiny")
        self.assertEqual(len(compact["fault"]), inventory.FAULT_MAX_LENGTH)
        # the traceback is never kept
        self.assertNotIn("details", compact)
        compact = inventory.compact_failed(_server(fault=None))
        self.assertIsNone(compact["fault"])
        compact = inventory.compact_failed(_server())
        self.assertEqual(compact["fault"], "No valid host was found.")

    def test_is_failed_build(self):
        self.assertTrue(inventory.is_failed_build(_server()))
        # failed later (e.g. a resize): it launched once
        launched = _server(**{"OS-SRV-USG:launched_at": "2026-11-18T10:00:00"})
        self.assertFalse(inventory.is_failed_build(launched))
        self.assertFalse(inventory.is_failed_build(_server(status="ACTIVE")))


class NovaInventoryTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.nova_module = mock.MagicMock()
        self.keystone = mock.MagicMock()
        api = types.ModuleType("openstack_dashboard.api")
        api._nova = self.nova_module
        api.keystone = self.keystone
        package = types.ModuleType("openstack_dashboard")
        package.api = api
        patcher = mock.patch.dict(
            sys.modules,
            {"openstack_dashboard": package, "openstack_dashboard.api": api},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.nova_module.novaclient.return_value
        self.request = mock.MagicMock()
        self.inventory = inventory.NovaProvisioningInventory(self.request)

    def test_failed_builds(self):
        since = inventory.epoch("2026-11-18T00:00:00Z")
        self.client.servers.list.return_value = [
            _server(id="new"),
            # launched, then went to ERROR: not a failed build
            _server(id="later", **{"OS-SRV-USG:launched_at": "2026-11-18"}),
            # created before the range, updated in it
            _server(id="old", created="2026-11-17T23:00:00Z"),
        ]
        found = self.inventory.failed_builds(since, 100)
        self.assertEqual([b["id"] for b in found["builds"]], ["new"])
        self.assertFalse(found["truncated"])
        self.nova_module.novaclient.assert_called_once_with(
            self.request, version="2.47"
        )
        self.client.servers.list.assert_called_once_with(
            detailed=True,
            search_opts={
                "all_tenants": True,
                "status": "ERROR",
                "changes-since": "2026-11-18T00:00:00Z",
            },
            limit=100,
            sort_keys=["created_at"],
            sort_dirs=["desc"],
        )

    def test_failed_builds_truncated(self):
        self.client.servers.list.return_value = [_server(), _server()]
        found = self.inventory.failed_builds(0, 2)
        self.assertTrue(found["truncated"])

    def test_flavors(self):
        def get(instance_id):
            if instance_id == "gone":
                raise nova_exceptions.NotFound(404)
            return _server(id=instance_id)

        self.client.servers.get.side_effect = get
        self.assertEqual(self.inventory.flavor("a"), "m1.tiny")
        self.assertIsNone(self.inventory.flavor("gone"))

    def test_flavors_other_errors_raise(self):
        self.client.servers.get.side_effect = RuntimeError("down")
        with self.assertRaises(RuntimeError):
            self.inventory.flavor("a")

    def _hypervisor(self, hid, name, host):
        return types.SimpleNamespace(
            id=hid, hypervisor_hostname=name, service={"host": host, "id": 3}
        )

    def test_hypervisor_hosts(self):
        self.client.hypervisors.list.return_value = [
            self._hypervisor(1, "devstack-grian.local", "devstack-grian"),
            self._hypervisor(2, "node-a", "ironic-1"),
            self._hypervisor(3, "node-b", "ironic-1"),
            # no service: skipped
            types.SimpleNamespace(id=4, hypervisor_hostname="x", service=None),
        ]
        self.assertEqual(
            self.inventory.hypervisor_hosts(),
            {
                "devstack-grian": ["devstack-grian.local"],
                "ironic-1": ["node-a", "node-b"],
            },
        )
        self.client.hypervisors.list.assert_called_once_with(
            detailed=True, marker=None, limit=inventory.HYPERVISOR_PAGE
        )

    def test_hypervisor_pages(self):
        with mock.patch.object(inventory, "HYPERVISOR_PAGE", 2):
            self.client.hypervisors.list.side_effect = [
                [self._hypervisor(1, "a", "a"), self._hypervisor(2, "b", "b")],
                [self._hypervisor(3, "c", "c")],
            ]
            hosts = self.inventory.hypervisor_hosts()
        self.assertEqual(sorted(hosts), ["a", "b", "c"])
        self.assertEqual(
            self.client.hypervisors.list.call_args_list[1].kwargs["marker"], 2
        )

    def test_project_names(self):
        self.keystone.tenant_list.return_value = (
            [types.SimpleNamespace(id="p1", name="admin")],
            False,
        )
        self.assertEqual(self.inventory.project_names(), {"p1": "admin"})
        self.keystone.tenant_list.assert_called_once_with(
            self.request, admin=True
        )


class FakeInventoryTests(base.TestCase):
    def test_failed_builds(self):
        fake = inventory.FakeProvisioningInventory()
        found = fake.failed_builds(0, 100)
        self.assertEqual(len(found["builds"]), 7)
        self.assertFalse(found["truncated"])
        created = [b["created"] for b in found["builds"]]
        self.assertEqual(created, sorted(created, reverse=True))
        found = fake.failed_builds(0, 3)
        self.assertEqual(len(found["builds"]), 3)
        self.assertTrue(found["truncated"])

    def test_flavors_and_projects(self):
        fake = inventory.FakeProvisioningInventory()
        instance = fake_provisioning.fake_boots()[0]["id"]
        self.assertIn(fake.flavor(instance), fake_provisioning.FAKE_FLAVORS)
        self.assertEqual(fake.project_names(), fake_provisioning.FAKE_PROJECTS)
        hosts = fake.hypervisor_hosts()
        self.assertEqual(hosts["compute-017"], ["compute-017.example.com"])
        # one fake host has no hypervisor
        self.assertNotIn("compute-040", hosts)


class GetInventoryTests(base.TestCase):
    def test_fake(self):
        self.assertIsInstance(
            inventory.get_inventory(mock.MagicMock()),
            inventory.FakeProvisioningInventory,
        )

    @override_settings(GRIAN_PLUGIN={"datasource": "prometheus"})
    def test_nova(self):
        self.assertIsInstance(
            inventory.get_inventory(mock.MagicMock()),
            inventory.NovaProvisioningInventory,
        )
