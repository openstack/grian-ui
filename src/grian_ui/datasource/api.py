#
# SPDX-License-Identifier: Apache-2.0

"""
Datasource API for telemetry data.
Provides an abstraction layer for different telemetry backends.
"""

import importlib

from abc import ABC
from abc import abstractmethod

from grian_ui import config


def get_datasource(request):
    """
    Get the configured datasource backend.

    Returns the datasource implementation based on GRIAN_PLUGIN['datasource']
    setting. Defaults to 'fake' if not configured.
    """
    datasource_type = config.get_datasource_type()
    module_path = f"grian_ui.datasource.{datasource_type}"

    try:
        datasource_module = importlib.import_module(module_path)
        return datasource_module.get_client(request)
    except ImportError:
        # Fallback to fake datasource
        import grian_ui.datasource.fake as fake_datasource

        return fake_datasource.get_client(request)


class DatasourceClient(ABC):
    """Abstract base class for datasource clients."""

    @abstractmethod
    def get_telemetry_overview(self) -> dict:
        """Get overview telemetry data."""
        pass

    @abstractmethod
    def get_volumes_count(self) -> int:
        """Get volumes count telemetry data."""
        pass

    @abstractmethod
    def get_snapshots_count(self) -> int:
        """Get snapshots count telemetry data."""
        pass

    @abstractmethod
    def get_storage_pools(self) -> list:
        """Get storage pools information."""
        pass

    @abstractmethod
    def get_capacity_predictions(self) -> list:
        """Get capacity predictions for storage pools."""
        pass

    @abstractmethod
    def get_reporting_instances(self, window=None) -> dict:
        """
        Get the instances that reported a cpu sample in ``window`` (a
        duration, ``instance_health.STALE_AFTER`` by default).

        Returns:
            dict: instance id -> ``{"name", "project_id"}``, taken from the
            metric labels. See :mod:`grian_ui.instance_health`.
        """
        pass

    @abstractmethod
    def get_instance_symptoms(
        self, thresholds: dict, rate_window: str
    ) -> dict:
        """
        Get the instances above a warning threshold, per metric symptom.

        Only instances above a warning threshold are returned, so the size
        of the result follows the number of problems, not of instances.

        Returns:
            dict: ``{"cpu": {id: percent}, "memory": {id: MiB/s},
            "disk": {id: {"value": ms, "device": str}},
            "network": {id: packets/s}}``
        """
        pass

    @abstractmethod
    def get_instance_last_seen(self, instance_ids: list) -> dict:
        """
        Get the time of the last cpu sample of at most a page of instances.

        Returns:
            dict: instance id -> epoch seconds (missing if not seen in the
            lookback window)
        """
        pass

    @abstractmethod
    def get_instance_trends(
        self,
        instance_id: str,
        range_key: str,
        rate_window: str,
        at: float = None,
    ) -> dict:
        """
        Get the four symptom measures of one instance over a range.

        ``at`` is the evaluation time (epoch), the end of the chart grid.

        Returns:
            dict: ``{"cpu": [[ts, v], ...], "memory": [...], "disk": [...],
            "network": [...]}``
        """
        pass

    @abstractmethod
    def get_symptom_trends(
        self,
        symptom: str,
        instance_ids: list,
        range_key: str,
        rate_window: str,
        at: float = None,
    ) -> dict:
        """
        Get one symptom measure of at most a page of instances over a range.

        ``at`` is the evaluation time (epoch), the end of the chart grid.

        Returns:
            dict: instance id -> ``[[ts, v], ...]``
        """
        pass
