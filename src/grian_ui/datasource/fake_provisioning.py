#
# SPDX-License-Identifier: Apache-2.0

"""
Sample data of the Provisioning dashboard (``fake`` datasource).

A 40 host cloud with about 3,000 boots over 14 days. ``compute-017``
turned slow two days ago (its image cache broke), so it is "Slow" on the
last 24 hours while its 7 day median still looks normal. Seven builds
failed in the last 6 days. The data is deterministic, relative to now.
"""

import math
import time

from grian_ui import provisioning


FAKE_HOST_COUNT = 40
FAKE_BOOT_COUNT = 3000
FAKE_SLOW_HOST = "compute-017"
#: Seconds since the slow host turned slow.
FAKE_SLOW_SINCE = int(2.2 * 86400)
FAKE_FLAVORS = ("m1.small", "m1.medium", "m1.large", "m1.xlarge")
FAKE_PROJECTS = {
    f"{i:032x}": name
    for i, name in enumerate(
        (
            "web-shop",
            "data-lake",
            "ci-runners",
            "ml-training",
            "erp",
            "analytics",
        ),
        start=1,
    )
}


def _noise(i, k):
    """Deterministic value in [0, 1) for ``i`` and ``k``."""
    value = math.sin(i * 12.9898 + k * 78.233) * 43758.5453
    return value - math.floor(value)


def _instance_id(i):
    return f"00000000-0000-4000-8000-{i:012d}"


def fake_flavor(instance_id):
    """Flavor of a fake instance (None for unknown ids)."""
    try:
        i = int(instance_id.rsplit("-", 1)[1])
    except (AttributeError, IndexError, ValueError):
        return None
    return FAKE_FLAVORS[int(_noise(i, 3) * len(FAKE_FLAVORS))]


def fake_boots(now=None):
    """The fake boots of the last 14 days, oldest first."""
    now = time.time() if now is None else now
    span = provisioning.range_seconds("14d")
    projects = list(FAKE_PROJECTS)
    boots = []
    for i in range(FAKE_BOOT_COUNT):
        at = now - span * (1 - i / FAKE_BOOT_COUNT) + 60
        host = f"compute-{int(_noise(i, 1) * FAKE_HOST_COUNT) + 1:03d}"
        flavor = fake_flavor(_instance_id(i))
        seconds = 8 + 6 * _noise(i, 2)
        if flavor == "m1.xlarge":
            seconds += 4
        if _noise(i, 4) < 0.03:
            seconds += 20 + 30 * _noise(i, 5)
        if host == FAKE_SLOW_HOST and at > now - FAKE_SLOW_SINCE:
            seconds = 38 + 25 * _noise(i, 6)
        project = projects[int(_noise(i, 7) * len(projects))]
        boots.append(
            {
                "id": _instance_id(i),
                "name": f"{FAKE_PROJECTS[project]}-{i}",
                "project_id": project,
                "host": host,
                "seconds": round(seconds, 2),
                "at": at,
            }
        )
    return boots


def boots_in_range(range_key, now=None):
    """The fake boots of a range, like the Prometheus datasource."""
    now = time.time() if now is None else now
    start = now - provisioning.range_seconds(range_key)
    return [b for b in fake_boots(now) if b["at"] >= start]


def fake_hypervisors():
    """
    Service host -> hypervisor hostnames: every fake host but the last,
    whose hypervisor is missing (its name is shown without a link).
    """
    return {
        f"compute-{i:03d}": [f"compute-{i:03d}.example.com"]
        for i in range(1, FAKE_HOST_COUNT)
    }


def fake_failed_builds(now=None):
    """Seven failed builds in the last 6 days, oldest first."""
    now = time.time() if now is None else now
    faults = (
        ["No valid host was found."] * 4
        + ["Build of instance aborted: Failed to allocate the network(s)."] * 2
        + [
            "Volume did not finish being created even after we waited "
            "187 seconds or 61 attempts."
        ]
    )
    projects = list(FAKE_PROJECTS)
    builds = []
    for i, fault in enumerate(faults):
        no_host = fault.startswith("No valid host")
        builds.append(
            {
                "id": f"00000000-0000-4000-9000-{i:012d}",
                "name": f"ml-train-{10 + i}" if no_host else f"app-{i}",
                "project_id": projects[3] if no_host else projects[i % 3],
                "flavor": "gpu.a100x8" if no_host else "m1.large",
                "created": now - 6 * 86400 + i * 80000,
                "fault": fault,
            }
        )
    return builds
