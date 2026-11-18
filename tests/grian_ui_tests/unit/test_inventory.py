#
# SPDX-License-Identifier: Apache-2.0

import sys
import types

from unittest import mock

from grian_ui import inventory
from grian_ui.datasource import fake
from grian_ui_tests.unit import base


UUID = "f6fb292b-1740-404b-81f7-c32413db8f65"


def _nova_server(**attrs):
    server = types.SimpleNamespace(
        id=UUID, name="vm", tenant_id="p1", status="ACTIVE", updated=None
    )
    for key, value in attrs.items():
        setattr(server, key, value)
    return server


class HelpersTests(base.TestCase):
    def test_is_instance_id(self):
        self.assertTrue(inventory.is_instance_id(UUID))
        for value in (None, "", UUID.upper(), UUID + "x", 'a"|b', "1234"):
            self.assertFalse(inventory.is_instance_id(value), value)

    def test_epoch(self):
        self.assertEqual(inventory._epoch("1970-01-01T00:01:00Z"), 60.0)
        # Nova's launched_at has no timezone: UTC
        self.assertEqual(inventory._epoch("1970-01-01T00:01:00.000000"), 60.0)
        self.assertIsNone(inventory._epoch(None))
        self.assertIsNone(inventory._epoch("yesterday"))

    def test_compact_server(self):
        server = _nova_server(
            **{
                "OS-EXT-SRV-ATTR:hypervisor_hostname": "node1.example",
                "OS-EXT-SRV-ATTR:host": "node1",
                "OS-EXT-STS:power_state": 1,
                "OS-EXT-STS:task_state": None,
                "updated": "1970-01-01T00:02:00Z",
                "OS-SRV-USG:launched_at": "1970-01-01T00:01:00.000000",
                "fault": {"message": "x" * 1000, "code": 500},
            }
        )
        compact = inventory.compact_server(server)
        self.assertEqual(compact["id"], UUID)
        self.assertEqual(compact["project_id"], "p1")
        self.assertEqual(compact["host"], "node1.example")
        self.assertEqual(compact["power_state"], 1)
        self.assertEqual(compact["updated"], 120.0)
        self.assertEqual(compact["launched_at"], 60.0)
        self.assertEqual(len(compact["fault"]), inventory.FAULT_MAX_LENGTH)

    def test_compact_server_minimal(self):
        compact = inventory.compact_server(_nova_server())
        self.assertIsNone(compact["host"])
        self.assertIsNone(compact["power_state"])
        self.assertIsNone(compact["fault"])

    def test_compact_server_falls_back_to_service_host(self):
        server = _nova_server(**{"OS-EXT-SRV-ATTR:host": "node1"})
        self.assertEqual(inventory.compact_server(server)["host"], "node1")


class NovaInventoryTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.nova = mock.MagicMock()
        self.keystone = mock.MagicMock()
        api = types.ModuleType("openstack_dashboard.api")
        api.nova = self.nova
        api.keystone = self.keystone
        package = types.ModuleType("openstack_dashboard")
        package.api = api
        patcher = mock.patch.dict(
            sys.modules,
            {
                "openstack_dashboard": package,
                "openstack_dashboard.api": api,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.nova.get_novaclient_with_locked_status.return_value
        self.request = mock.MagicMock()
        self.inventory = inventory.NovaInventory(self.request)

    def test_list_servers_follows_every_page(self):
        self.client.servers.list.return_value = [_nova_server()]
        servers = self.inventory.list_servers()
        self.nova.get_novaclient_with_locked_status.assert_called_once_with(
            self.request
        )
        self.client.servers.list.assert_called_once_with(
            detailed=True, search_opts={"all_tenants": True}, limit=-1
        )
        self.assertEqual([s["id"] for s in servers], [UUID])

    def test_list_servers_by_status_is_bounded_and_escaped(self):
        self.client.servers.list.return_value = []
        self.inventory.list_servers_by_status(
            "SHUTOFF", project_id="p1", name="web.1(", limit=25
        )
        kwargs = self.client.servers.list.call_args.kwargs
        self.assertEqual(kwargs["limit"], 25)
        self.assertEqual(
            kwargs["search_opts"],
            {
                "all_tenants": True,
                "status": "SHUTOFF",
                "project_id": "p1",
                "name": r"web\.1\(",
            },
        )
        self.assertEqual(kwargs["sort_keys"], ["display_name", "uuid"])

    def test_list_servers_by_status_without_filters(self):
        self.client.servers.list.return_value = []
        self.inventory.list_servers_by_status("PAUSED")
        self.assertEqual(
            self.client.servers.list.call_args.kwargs["search_opts"],
            {"all_tenants": True, "status": "PAUSED"},
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
    def test_servers_match_the_fake_telemetry(self):
        servers = inventory.FakeInventory().list_servers()
        reporting = fake.get_client().get_reporting_instances()
        ids = {s["id"] for s in servers}
        self.assertTrue(set(reporting) <= ids)
        self.assertTrue(all(inventory.is_instance_id(i) for i in ids))

    def test_list_by_status(self):
        inv = inventory.FakeInventory()
        shutoff = inv.list_servers_by_status("SHUTOFF")
        self.assertEqual(
            [s["name"] for s in shutoff], ["api-0001", "api-0002"]
        )
        self.assertEqual(
            len(inv.list_servers_by_status("SHUTOFF", limit=1)), 1
        )
        self.assertEqual(
            [
                s["name"]
                for s in inv.list_servers_by_status("SHUTOFF", name="02")
            ],
            ["api-0002"],
        )
        project = shutoff[0]["project_id"]
        self.assertEqual(
            len(inv.list_servers_by_status("SHUTOFF", project_id=project)), 2
        )
        self.assertEqual(
            inv.list_servers_by_status("SHUTOFF", project_id="x"), []
        )

    def test_project_names(self):
        self.assertEqual(
            inventory.FakeInventory().project_names(), fake.FAKE_PROJECTS
        )


class GetInventoryTests(base.TestCase):
    @mock.patch("grian_ui.inventory.config.get_datasource_type")
    def test_selection(self, mock_type):
        mock_type.return_value = "fake"
        self.assertIsInstance(
            inventory.get_inventory(None), inventory.FakeInventory
        )
        mock_type.return_value = "prometheus"
        self.assertIsInstance(
            inventory.get_inventory(mock.sentinel.req), inventory.NovaInventory
        )
