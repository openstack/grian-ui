#
# SPDX-License-Identifier: Apache-2.0

"""
Fake datasource implementation for development and testing.
Provides realistic sample telemetry data.
"""

import math
import random  # nosec
import time
import uuid

from datetime import datetime

from grian_ui import instance_health
from grian_ui import timeseries
from grian_ui.datasource.api import DatasourceClient


FAKE_PROJECTS = {
    "7bb2692e0d994decacab9a372f166676": "admin",
    "b8753cd7d01b4455b774b46991f36f83": "demo",
    "1f0c6a3e9d2b4c58a7e4f6b1c2d3e4f5": "web-prod",
    "2a1b3c4d5e6f47a8b9c0d1e2f3a4b5c6": "ci-runners",
}
_ADMIN, _DEMO, _WEB, _CI = FAKE_PROJECTS

# name, project, host, status, power state, task state, minutes since the
# last update, metric symptoms (only for reporting instances)
_FAKE_FLEET = [
    ("web-0001", _WEB, "compute-01", "ACTIVE", 1, None, 900, {"cpu": 28.0}),
    ("web-0002", _WEB, "compute-01", "ACTIVE", 1, None, 900, {}),
    (
        "web-0003",
        _WEB,
        "compute-02",
        "ACTIVE",
        1,
        None,
        900,
        {"network": 20.7},
    ),
    (
        "db-0001",
        _ADMIN,
        "compute-02",
        "ACTIVE",
        1,
        None,
        900,
        {"memory": 11.2, "disk": (140.0, "vdb")},
    ),
    ("db-0002", _ADMIN, "compute-02", "ERROR", 0, None, 60, None),
    (
        "cache-0001",
        _DEMO,
        "compute-01",
        "ACTIVE",
        1,
        None,
        900,
        {"disk": (24.0, "vda")},
    ),
    ("cache-0002", _DEMO, "compute-03", "ACTIVE", 1, None, 900, {"cpu": 8.0}),
    ("etl-0001", _CI, "compute-03", "ACTIVE", 1, None, 900, {"memory": 5.1}),
    ("etl-0002", _CI, "compute-03", "ACTIVE", 1, None, 900, None),
    ("ci-0001", _CI, "compute-01", "ACTIVE", 4, None, 30, None),
    ("ci-0002", _CI, "compute-02", "BUILD", 0, "spawning", 50, None),
    ("ci-0003", _CI, "compute-03", "BUILD", 0, "spawning", 2, None),
    ("api-0001", _WEB, "compute-01", "SHUTOFF", 4, None, 900, None),
    ("api-0002", _WEB, "compute-02", "SHUTOFF", 4, None, 900, None),
    ("api-0003", _DEMO, "compute-03", "PAUSED", 3, None, 900, None),
    ("api-0004", _DEMO, "compute-03", "SUSPENDED", 7, None, 900, None),
    ("worker-0001", _ADMIN, "compute-01", "ACTIVE", 1, None, 900, {}),
    ("worker-0002", _ADMIN, "compute-02", "ACTIVE", 1, None, 900, {}),
]


def _fake_id(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.grian.example"))


def fake_servers(now=None):
    """Compact Nova servers matching the fake telemetry."""
    now = time.time() if now is None else now
    servers = []
    for name, project, host, status, power, task, minutes, _m in _FAKE_FLEET:
        servers.append(
            {
                "id": _fake_id(name),
                "name": name,
                "project_id": project,
                "host": host,
                "status": status,
                "power_state": power,
                "task_state": task,
                "updated": now - minutes * 60,
                "launched_at": None if status == "BUILD" else now - 86400,
                "fault": (
                    "No valid host was found. There are not enough hosts "
                    "available."
                    if status == "ERROR"
                    else None
                ),
            }
        )
    return servers


def _fake_symptoms():
    """instance id -> symptoms, for the reporting fake instances."""
    return {
        _fake_id(name): symptoms
        for name, *_rest, symptoms in _FAKE_FLEET
        if symptoms is not None
    }


def _symptom_value(entry):
    return entry[0] if isinstance(entry, tuple) else entry


def _fake_trend(instance_id, end_value, range_key):
    """A deterministic wavy series ending at ``end_value``."""
    timestamps = timeseries.range_grid(range_key)
    seed = int(instance_id[:8], 16)
    n = len(timestamps)
    points = []
    for i, timestamp in enumerate(timestamps):
        ramp = 0.3 + 0.7 * i / max(n - 1, 1)
        wave = 1 + 0.15 * math.sin(i / 5 + seed % 7)
        points.append([timestamp, round(end_value * ramp * wave, 2)])
    if points:
        points[-1][1] = end_value
    return points


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

    def get_reporting_instances(self, window=None) -> dict:
        names = {_fake_id(f[0]): f for f in _FAKE_FLEET}
        return {
            instance_id: {
                "name": names[instance_id][0],
                "project_id": names[instance_id][1],
            }
            for instance_id in _fake_symptoms()
        }

    def get_instance_symptoms(self, thresholds, rate_window) -> dict:
        symptoms = {s: {} for s in instance_health.METRIC_SYMPTOMS}
        for instance_id, found in _fake_symptoms().items():
            for symptom, entry in found.items():
                prefix = instance_health.SYMPTOM_THRESHOLDS[symptom][0]
                if _symptom_value(entry) <= thresholds[f"{prefix}_warning"]:
                    continue
                if symptom == instance_health.DISK:
                    symptoms[symptom][instance_id] = {
                        "value": entry[0],
                        "device": entry[1],
                    }
                else:
                    symptoms[symptom][instance_id] = entry
        return symptoms

    def get_instance_last_seen(self, instance_ids) -> dict:
        now = time.time()
        reporting = _fake_symptoms()
        last_seen = {}
        for instance_id in instance_ids:
            if instance_id in reporting:
                last_seen[instance_id] = now - 12
            elif instance_id == _fake_id("etl-0002"):
                last_seen[instance_id] = now - 35 * 60
        return last_seen

    def get_instance_trends(
        self, instance_id, range_key, rate_window, at=None
    ):
        found = _fake_symptoms().get(instance_id) or {}
        return {
            symptom: _fake_trend(
                instance_id, _symptom_value(found.get(symptom, 0.0)), range_key
            )
            if instance_id in _fake_symptoms()
            else []
            for symptom in instance_health.METRIC_SYMPTOMS
        }

    def get_symptom_trends(
        self, symptom, instance_ids, range_key, rate_window, at=None
    ):
        symptoms = _fake_symptoms()
        return {
            instance_id: _fake_trend(
                instance_id,
                _symptom_value(symptoms[instance_id][symptom]),
                range_key,
            )
            for instance_id in instance_ids
            if symptom in (symptoms.get(instance_id) or {})
        }


def get_client(request=None) -> DatasourceClient:
    """
    Get a fake telemetry client instance.

    Args:
        request: HTTP request (unused for fake client, accepted for interface compatibility)

    Returns:
        FakeTelemetryClient instance
    """
    return FakeTelemetryClient()
