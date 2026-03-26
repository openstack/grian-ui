#
# SPDX-License-Identifier: Apache-2.0

import importlib

from unittest import mock

from grian_ui.datasource import api
from grian_ui_tests.unit import base


class GetDatasourceTests(base.TestCase):
    @mock.patch("grian_ui.datasource.api.config")
    def test_loads_fake_datasource(self, mock_config):
        mock_config.get_datasource_type.return_value = "fake"
        request = mock.MagicMock()

        result = api.get_datasource(request)

        from grian_ui.datasource.fake import FakeTelemetryClient

        self.assertIsInstance(result, FakeTelemetryClient)

    @mock.patch("grian_ui.datasource.api.config")
    def test_loads_configured_module_path(self, mock_config):
        mock_config.get_datasource_type.return_value = "fake"
        request = mock.MagicMock()

        with mock.patch(
            "grian_ui.datasource.api.importlib.import_module",
            wraps=importlib.import_module,
        ) as mock_import:
            api.get_datasource(request)

        mock_import.assert_any_call("grian_ui.datasource.fake")

    @mock.patch("grian_ui.datasource.api.config")
    def test_falls_back_to_fake_on_import_error(self, mock_config):
        mock_config.get_datasource_type.return_value = "nonexistent"
        request = mock.MagicMock()

        result = api.get_datasource(request)

        from grian_ui.datasource.fake import FakeTelemetryClient

        self.assertIsInstance(result, FakeTelemetryClient)


class DatasourceClientInterfaceTests(base.TestCase):
    def test_cannot_instantiate_abstract_class(self):
        with self.assertRaises(TypeError):
            api.DatasourceClient()
