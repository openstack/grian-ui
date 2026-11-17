#
# SPDX-License-Identifier: Apache-2.0

"""
Centralized configuration management for Grian UI.
Validates and provides typed access to Django settings.
"""

from django.conf import settings


# Valid datasource types
DATASOURCE_FAKE = "fake"
DATASOURCE_PROMETHEUS = "prometheus"
VALID_DATASOURCE_TYPES = (DATASOURCE_FAKE, DATASOURCE_PROMETHEUS)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""

    pass


def _get_grian_plugin_config():
    """Get and validate GRIAN_PLUGIN configuration."""
    config = getattr(settings, "GRIAN_PLUGIN", None)

    if config is None:
        # Return default configuration
        return {"datasource": DATASOURCE_FAKE}

    if not isinstance(config, dict):
        raise ConfigurationError(
            f"GRIAN_PLUGIN must be a dict, got {type(config).__name__}"
        )

    return config


def get_datasource_type():
    """
    Get the configured datasource type.

    Returns:
        str: The datasource type (e.g., 'fake', 'prometheus')

    Raises:
        ConfigurationError: If datasource type is invalid
    """
    config = _get_grian_plugin_config()
    datasource = config.get("datasource", DATASOURCE_FAKE)

    if not isinstance(datasource, str):
        raise ConfigurationError(
            f"GRIAN_PLUGIN['datasource'] must be a string, got {type(datasource).__name__}"
        )

    if datasource not in VALID_DATASOURCE_TYPES:
        raise ConfigurationError(
            f"Invalid datasource type '{datasource}'. "
            f"Valid types: {', '.join(VALID_DATASOURCE_TYPES)}"
        )

    return datasource


#: Default compute host health thresholds (percent, 15 minute averages).
DEFAULT_COMPUTE_HOST_THRESHOLDS = {
    "cpu_warning": 75,
    "cpu_critical": 90,
    "iowait_warning": 10,
    "iowait_critical": 20,
}


def get_compute_host_thresholds():
    """
    Get the compute host health thresholds.

    Defaults can be overridden per key with
    ``GRIAN_PLUGIN['compute_host_thresholds']``, for example::

        GRIAN_PLUGIN = {
            "datasource": "prometheus",
            "compute_host_thresholds": {"cpu_warning": 80},
        }

    Returns:
        dict: thresholds keyed like DEFAULT_COMPUTE_HOST_THRESHOLDS

    Raises:
        ConfigurationError: If an override is unknown or out of range
    """
    overrides = _get_grian_plugin_config().get("compute_host_thresholds")
    thresholds = dict(DEFAULT_COMPUTE_HOST_THRESHOLDS)
    if overrides is None:
        return thresholds

    if not isinstance(overrides, dict):
        raise ConfigurationError(
            "GRIAN_PLUGIN['compute_host_thresholds'] must be a dict, "
            f"got {type(overrides).__name__}"
        )

    for key, value in overrides.items():
        if key not in DEFAULT_COMPUTE_HOST_THRESHOLDS:
            raise ConfigurationError(
                f"Unknown compute host threshold '{key}'. Valid keys: "
                f"{', '.join(DEFAULT_COMPUTE_HOST_THRESHOLDS)}"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not 0 <= value < 100
        ):
            raise ConfigurationError(
                f"Compute host threshold '{key}' must be a number from 0 "
                f"to less than 100, got {value!r}"
            )
        thresholds[key] = value

    for metric in ("cpu", "iowait"):
        if thresholds[f"{metric}_warning"] > thresholds[f"{metric}_critical"]:
            raise ConfigurationError(
                f"Compute host threshold '{metric}_warning' must not be "
                f"greater than '{metric}_critical'"
            )

    return thresholds


def get_openstack_keystone_url():
    """Get OpenStack Keystone URL from settings."""
    url = getattr(settings, "OPENSTACK_KEYSTONE_URL", None)
    if not url:
        raise ConfigurationError(
            "OPENSTACK_KEYSTONE_URL is not configured in settings"
        )
    return url


def get_openstack_endpoint_type():
    """Get OpenStack endpoint type (interface) from settings."""
    return getattr(settings, "OPENSTACK_ENDPOINT_TYPE", "publicURL")
