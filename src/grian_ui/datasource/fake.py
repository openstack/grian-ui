#
# SPDX-License-Identifier: Apache-2.0

"""
Fake datasource implementation for development and testing.
Provides realistic sample telemetry data.
"""

import random  # nosec

from datetime import datetime

from grian_ui.datasource.api import DatasourceClient


class FakeTelemetryClient(DatasourceClient):
    """Fake telemetry client that generates sample data."""

    def get_telemetry_overview(self) -> dict:
        """
        Get telemetry overview data.

        Returns sample data for development/testing purposes.
        In a real implementation, this would aggregate multiple queries.
        """
        return {
            "total_metrics": 1247,
            "active_instances": 18,
            "alerts_count": 3,
            "cpu_usage_avg": round(random.uniform(15.0, 85.0), 1),  # nosec
            "memory_usage_avg": round(random.uniform(25.0, 75.0), 1),  # nosec
            "network_throughput": round(random.uniform(50.0, 200.0), 1),  # nosec
            "disk_io_rate": round(random.uniform(10.0, 100.0), 1),  # nosec
            "uptime_percentage": round(random.uniform(98.5, 99.9), 2),  # nosec
            "total_storage": 10240,  # GB
            "allocated_storage": round(random.uniform(6000, 9000)),  # nosec GB
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_volumes_count(self) -> int:
        """
        Get volumes count telemetry data.

        Returns hardcoded placeholder value for development/testing.
        """
        return 120

    def get_snapshots_count(self) -> int:
        """
        Get snapshots count telemetry data.

        Returns hardcoded placeholder value for development/testing.
        """
        return 45

    def get_storage_pools(self) -> list:
        """
        Get storage pools information.

        Returns sample pool data for development/testing.
        """
        return [
            {
                "name": "storage-pool-01",
                "total": 5120.0,
                "allocated": 4096.0,
                "free": 1024.0,
                "provisioned": 5120.0,
            },
            {
                "name": "storage-pool-02",
                "total": 5120.0,
                "allocated": 4096.0,
                "free": 1024.0,
                "provisioned": 8192.0,
            },
        ]

    def get_capacity_predictions(self) -> list:
        """
        Get capacity predictions for storage pools.

        Returns sample prediction data for development/testing.
        """
        return [
            {
                "name": "storage-pool-01",
                "current_free": 1024.0,
                "predicted_value": 512.0,
                "days_until_full": None,
                "will_run_out": False,
            },
            {
                "name": "storage-pool-02",
                "current_free": 1024.0,
                "predicted_value": -200.0,
                "days_until_full": 24.5,
                "will_run_out": True,
            },
        ]


def get_client(request=None) -> DatasourceClient:
    """
    Get a fake telemetry client instance.

    Args:
        request: HTTP request (unused for fake client, accepted for interface compatibility)

    Returns:
        FakeTelemetryClient instance
    """
    return FakeTelemetryClient()
