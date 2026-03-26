#
# SPDX-License-Identifier: Apache-2.0

from unittest import mock

from grian_ui import config
from grian_ui_tests.unit import base


class GetGrianPluginConfigTests(base.TestCase):
    @mock.patch("grian_ui.config.settings")
    def test_returns_default_when_not_configured(self, mock_settings):
        del mock_settings.GRIAN_PLUGIN
        result = config._get_grian_plugin_config()
        self.assertEqual(result, {"datasource": "fake"})

    @mock.patch("grian_ui.config.settings")
    def test_returns_configured_dict(self, mock_settings):
        mock_settings.GRIAN_PLUGIN = {"datasource": "prometheus"}
        result = config._get_grian_plugin_config()
        self.assertEqual(result, {"datasource": "prometheus"})

    @mock.patch("grian_ui.config.settings")
    def test_raises_on_non_dict(self, mock_settings):
        mock_settings.GRIAN_PLUGIN = "not_a_dict"
        self.assertRaisesRegex(
            config.ConfigurationError,
            "must be a dict",
            config._get_grian_plugin_config,
        )


class GetDatasourceTypeTests(base.TestCase):
    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_returns_fake(self, mock_config):
        mock_config.return_value = {"datasource": "fake"}
        self.assertEqual(config.get_datasource_type(), "fake")

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_returns_prometheus(self, mock_config):
        mock_config.return_value = {"datasource": "prometheus"}
        self.assertEqual(config.get_datasource_type(), "prometheus")

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_defaults_to_fake_when_key_missing(self, mock_config):
        mock_config.return_value = {}
        self.assertEqual(config.get_datasource_type(), "fake")

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_on_invalid_type(self, mock_config):
        mock_config.return_value = {"datasource": "invalid"}
        self.assertRaisesRegex(
            config.ConfigurationError,
            "Invalid datasource type",
            config.get_datasource_type,
        )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_on_non_string(self, mock_config):
        mock_config.return_value = {"datasource": 123}
        self.assertRaisesRegex(
            config.ConfigurationError,
            "must be a string",
            config.get_datasource_type,
        )


class GetOpenstackKeystoneUrlTests(base.TestCase):
    @mock.patch("grian_ui.config.settings")
    def test_returns_url(self, mock_settings):
        mock_settings.OPENSTACK_KEYSTONE_URL = "http://keystone:5000/v3"
        self.assertEqual(
            config.get_openstack_keystone_url(), "http://keystone:5000/v3"
        )

    @mock.patch("grian_ui.config.settings")
    def test_raises_when_not_set(self, mock_settings):
        del mock_settings.OPENSTACK_KEYSTONE_URL
        self.assertRaisesRegex(
            config.ConfigurationError,
            "OPENSTACK_KEYSTONE_URL is not configured",
            config.get_openstack_keystone_url,
        )

    @mock.patch("grian_ui.config.settings")
    def test_raises_when_empty(self, mock_settings):
        mock_settings.OPENSTACK_KEYSTONE_URL = ""
        self.assertRaisesRegex(
            config.ConfigurationError,
            "OPENSTACK_KEYSTONE_URL is not configured",
            config.get_openstack_keystone_url,
        )


class GetOpenstackEndpointTypeTests(base.TestCase):
    @mock.patch("grian_ui.config.settings")
    def test_returns_configured_value(self, mock_settings):
        mock_settings.OPENSTACK_ENDPOINT_TYPE = "internalURL"
        self.assertEqual(config.get_openstack_endpoint_type(), "internalURL")

    @mock.patch("grian_ui.config.settings")
    def test_defaults_to_public(self, mock_settings):
        del mock_settings.OPENSTACK_ENDPOINT_TYPE
        self.assertEqual(config.get_openstack_endpoint_type(), "publicURL")
