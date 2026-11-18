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


class GetThresholdsTests(base.TestCase):
    DEFAULTS = {"cpu_warning": 5, "cpu_critical": 20, "drops_warning": 0}

    def _get(self, overrides, maximum=None):
        with mock.patch(
            "grian_ui.config._get_grian_plugin_config",
            return_value={"datasource": "fake", "th": overrides},
        ):
            return config.get_thresholds("th", self.DEFAULTS, maximum)

    def test_defaults_when_not_configured(self):
        result = self._get(None)
        self.assertEqual(result, self.DEFAULTS)
        result["cpu_warning"] = 99
        # the caller's defaults are never modified
        self.assertEqual(self.DEFAULTS["cpu_warning"], 5)

    def test_per_key_override(self):
        result = self._get({"cpu_warning": 10, "drops_warning": 2.5})
        self.assertEqual(
            result,
            {"cpu_warning": 10, "cpu_critical": 20, "drops_warning": 2.5},
        )

    def test_not_a_dict(self):
        self.assertRaisesRegex(
            config.ConfigurationError, r"\['th'\] must be a dict", self._get, 5
        )

    def test_unknown_key(self):
        self.assertRaisesRegex(
            config.ConfigurationError,
            "Unknown key 'bogus'.*Valid keys: cpu_warning",
            self._get,
            {"bogus": 1},
        )

    def test_invalid_values(self):
        for value in (-1, "10", None, True, float("nan"), float("inf")):
            self.assertRaisesRegex(
                config.ConfigurationError,
                "must be a number of at least 0",
                self._get,
                {"cpu_warning": value},
            )

    def test_maximum(self):
        self.assertEqual(
            self._get({"cpu_critical": 99.9}, 100)["cpu_critical"], 99.9
        )
        self.assertRaisesRegex(
            config.ConfigurationError,
            "must be a number from 0 to less than 100, got 100",
            self._get,
            {"cpu_critical": 100},
            100,
        )

    def test_warning_above_critical(self):
        self.assertRaisesRegex(
            config.ConfigurationError,
            "'cpu_warning'\\] must not be greater than 'cpu_critical'",
            self._get,
            {"cpu_warning": 30},
        )

    def test_warning_without_critical_pair(self):
        # drops_warning has no critical counterpart: no ordering check
        self.assertEqual(
            self._get({"drops_warning": 1000})["drops_warning"], 1000
        )


class CacheMaxItemBytesTests(base.TestCase):
    def _get(self, **values):
        with mock.patch(
            "grian_ui.config._get_grian_plugin_config",
            return_value=dict({"datasource": "fake"}, **values),
        ):
            return config.get_cache_max_item_bytes()

    def test_default(self):
        self.assertEqual(self._get(), config.DEFAULT_CACHE_MAX_ITEM_BYTES)

    def test_override(self):
        self.assertEqual(self._get(cache_max_item_bytes=4_000_000), 4_000_000)

    def test_invalid(self):
        for value in (0, -1, 1.5, "1000", True, None):
            self.assertRaisesRegex(
                config.ConfigurationError,
                "cache_max_item_bytes",
                self._get,
                cache_max_item_bytes=value,
            )


class InstanceHealthConfigTests(base.TestCase):
    def _plugin(self, **values):
        return mock.patch(
            "grian_ui.config._get_grian_plugin_config",
            return_value=dict({"datasource": "fake"}, **values),
        )

    def test_threshold_defaults(self):
        with self._plugin():
            self.assertEqual(
                config.get_instance_health_thresholds(),
                config.DEFAULT_INSTANCE_HEALTH_THRESHOLDS,
            )

    def test_threshold_override(self):
        with self._plugin(instance_health_thresholds={"swap_warning": 1}):
            thresholds = config.get_instance_health_thresholds()
        self.assertEqual(thresholds["swap_warning"], 1)
        self.assertEqual(thresholds["swap_critical"], 10)

    def test_threshold_order(self):
        with self._plugin(instance_health_thresholds={"cpu_wait_warning": 50}):
            self.assertRaises(
                config.ConfigurationError,
                config.get_instance_health_thresholds,
            )

    def test_rate_window(self):
        with self._plugin():
            self.assertEqual(config.get_instance_health_rate_window(), "10m")
        for value in ("15m", "1h", "6h", "360m"):
            with self._plugin(instance_health_rate_window=value):
                self.assertEqual(
                    config.get_instance_health_rate_window(), value
                )

    def test_invalid_rate_window(self):
        for value in (
            "0m",
            "7h",
            "361m",
            "30s",
            "10",
            "1d",
            10,
            None,
            "10m] or up[",
            " 10m",
            # "$" alone would accept a trailing newline
            "10m\n",
        ):
            with self._plugin(instance_health_rate_window=value):
                self.assertRaisesRegex(
                    config.ConfigurationError,
                    "instance_health_rate_window",
                    config.get_instance_health_rate_window,
                )
