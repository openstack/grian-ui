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


class GetComputeHostThresholdsTests(base.TestCase):
    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_defaults(self, mock_config):
        mock_config.return_value = {"datasource": "fake"}
        self.assertEqual(
            config.get_compute_host_thresholds(),
            config.DEFAULT_COMPUTE_HOST_THRESHOLDS,
        )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_defaults_are_not_mutated(self, mock_config):
        mock_config.return_value = {
            "compute_host_thresholds": {"cpu_warning": 70}
        }
        config.get_compute_host_thresholds()
        self.assertEqual(
            config.DEFAULT_COMPUTE_HOST_THRESHOLDS["cpu_warning"], 75
        )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_partial_override(self, mock_config):
        mock_config.return_value = {
            "compute_host_thresholds": {
                "cpu_warning": 80,
                "iowait_critical": 25.5,
            }
        }
        result = config.get_compute_host_thresholds()
        self.assertEqual(result["cpu_warning"], 80)
        self.assertEqual(result["iowait_critical"], 25.5)
        self.assertEqual(result["cpu_critical"], 90)

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_on_non_dict(self, mock_config):
        mock_config.return_value = {"compute_host_thresholds": [80]}
        self.assertRaises(
            config.ConfigurationError, config.get_compute_host_thresholds
        )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_on_unknown_key(self, mock_config):
        mock_config.return_value = {"compute_host_thresholds": {"ram": 80}}
        self.assertRaises(
            config.ConfigurationError, config.get_compute_host_thresholds
        )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_on_invalid_value(self, mock_config):
        for value in (-1, 100, 101, "80", True, None):
            mock_config.return_value = {
                "compute_host_thresholds": {"cpu_warning": value}
            }
            self.assertRaises(
                config.ConfigurationError, config.get_compute_host_thresholds
            )

    @mock.patch("grian_ui.config._get_grian_plugin_config")
    def test_raises_when_warning_above_critical(self, mock_config):
        mock_config.return_value = {
            "compute_host_thresholds": {"iowait_warning": 30}
        }
        self.assertRaises(
            config.ConfigurationError, config.get_compute_host_thresholds
        )
