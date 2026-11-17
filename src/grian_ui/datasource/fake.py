#
# SPDX-License-Identifier: Apache-2.0

"""
Fake datasource implementation for development and testing.
Provides realistic sample telemetry data.
"""

import math
import random  # nosec
import time

from datetime import datetime

from grian_ui import compute_health
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

    # Compute host health ------------------------------------------------

    def get_compute_host_snapshot(self) -> dict:
        """
        Get sample compute host data.

        24 reporting hosts (two critical, two warning) and two stale hosts.
        """
        now = time.time()
        hosts = [
            {"name": h["name"], "cpu_avg": h["cpu"], "iowait_avg": h["iowait"]}
            for h in _FAKE_HOSTS
        ]
        stale = [
            {"name": "compute-25", "last_seen": now - 18 * 60},
            {"name": "compute-26", "last_seen": now - 95 * 60},
        ]
        return {"hosts": hosts, "stale": stale}

    def get_compute_host_details(self, names: list) -> dict:
        """Get sample CPU breakdown for the given hosts."""
        now = time.time()
        details = {}
        for host in _FAKE_HOSTS:
            if host["name"] not in names:
                continue
            cpu, iowait = host["cpu"], host["iowait"]
            user = round((cpu - iowait) * 0.82, 1)
            details[host["name"]] = {
                "cpu": cpu,
                "user": user,
                "kernel": round(cpu - iowait - user, 1),
                "iowait": iowait,
                "idle": round(100 - cpu, 1),
                "frequency": host["frequency"],
                "last_seen": now - host["age"],
            }
        return details

    def get_compute_host_trends(self, names: list, range_key: str) -> dict:
        """Get sample CPU trends for the given hosts."""
        return {
            host["name"]: _fake_series(host["cpu"], host["seed"], range_key)
            for host in _FAKE_HOSTS
            if host["name"] in names
        }

    def get_compute_fleet_trend(self, range_key: str) -> dict:
        """Get sample fleet median / p95 / max trends."""
        series = [
            _fake_series(h["cpu"], h["seed"], range_key) for h in _FAKE_HOSTS
        ]
        trend = {"p50": [], "p95": [], "max": []}
        for points in zip(*series):
            timestamp = points[0][0]
            values = [p[1] for p in points]
            trend["p50"].append(
                [timestamp, compute_health.quantile(values, 0.5)]
            )
            trend["p95"].append(
                [timestamp, compute_health.quantile(values, 0.95)]
            )
            trend["max"].append([timestamp, max(values)])
        return trend


def _make_fake_hosts():
    rng = random.Random(42)  # nosec
    hosts = []
    for i in range(1, 25):
        hosts.append(
            {
                "name": f"compute-{i:02d}",
                "cpu": round(rng.uniform(12, 62), 1),  # nosec
                "iowait": round(rng.uniform(0, 4), 1),  # nosec
                "frequency": 2100 + 100 * rng.randint(0, 10),  # nosec
                "age": rng.randint(5, 70),  # nosec
                "seed": i,
            }
        )
    # A few hosts that need attention.
    hosts[2].update(cpu=93.5)  # critical CPU
    hosts[9].update(cpu=48.0, iowait=23.0)  # critical I/O wait
    hosts[6].update(cpu=81.2)  # warning CPU
    hosts[13].update(cpu=37.5, iowait=13.5)  # warning I/O wait
    return hosts


_FAKE_HOSTS = _make_fake_hosts()


def _fake_series(level, seed, range_key):
    """Deterministic wavy series around ``level`` for the given range."""
    points = []
    for i, timestamp in enumerate(compute_health.range_grid(range_key)):
        value = (
            level + 6 * math.sin((i + seed * 7) / 5.0) + 2 * math.cos(i * seed)
        )
        points.append([float(timestamp), round(min(100, max(0, value)), 1)])
    return points


def get_client(request=None) -> DatasourceClient:
    """
    Get a fake telemetry client instance.

    Args:
        request: HTTP request (unused for fake client, accepted for interface compatibility)

    Returns:
        FakeTelemetryClient instance
    """
    return FakeTelemetryClient()
