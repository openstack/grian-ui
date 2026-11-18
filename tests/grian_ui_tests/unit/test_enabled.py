#
# SPDX-License-Identifier: Apache-2.0

import importlib
import pkgutil

from grian_ui.local import enabled
from grian_ui_tests.unit import base


def _enabled_files():
    for module in pkgutil.iter_modules(enabled.__path__):
        yield (
            module.name,
            importlib.import_module(f"{enabled.__name__}.{module.name}"),
        )


class EnabledFilesTests(base.TestCase):
    def test_app_is_added_once_by_the_panel_group(self):
        # Horizon does not de-duplicate ADD_INSTALLED_APPS, and skips a
        # disabled panel's file entirely, so only the panel group adds it.
        adding = [
            name
            for name, module in _enabled_files()
            if getattr(module, "ADD_INSTALLED_APPS", None)
        ]
        self.assertEqual(adding, ["_1000_admin_telemetry_panel_group"])
        group = dict(_enabled_files())["_1000_admin_telemetry_panel_group"]
        self.assertEqual(group.ADD_INSTALLED_APPS, ["grian_ui"])

    def test_static_files_are_not_auto_discovered(self):
        # would add vendored htmx and Chart.js to every Horizon page
        for name, module in _enabled_files():
            self.assertFalse(
                getattr(module, "AUTO_DISCOVER_STATIC_FILES", False), name
            )

    def test_panels_belong_to_the_telemetry_group(self):
        for name, module in _enabled_files():
            if hasattr(module, "PANEL"):
                self.assertEqual(module.PANEL_GROUP, "telemetry", name)
                self.assertEqual(module.PANEL_DASHBOARD, "admin", name)
