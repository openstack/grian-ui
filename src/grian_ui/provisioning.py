#
# SPDX-License-Identifier: Apache-2.0

"""
Provisioning (instance boot time) evaluation.

Pure functions shared by the datasources and the Provisioning views. They
turn the list of boots of a time range into summary numbers, a host table
judged on the last 24 hours, the slowest boots and a bounded set of chart
points, so the amount of data cached and rendered does not grow with the
number of boots or hosts.

Boot format (returned by ``DatasourceClient.get_provisioning_boots``)::

    {
        "id": "07bbd2f8-...",  # instance UUID
        "name": "web-1",  # instance name, or None
        "project_id": "7bb2...",  # or None
        "host": "compute-01",  # nova-compute host, or None
        "seconds": 12.5,  # from the create request to "running"
        "at": 1700000000.0,  # when the boot was first seen (epoch)
    }
"""

import math

from django.utils.translation import gettext_lazy

from grian_ui import timeseries


#: Ranges: key -> (range, step). The step is the resolution of the boot
#: times ("when"): the time a boot was first seen is found with a subquery
#: at this step. 14 days needs a Prometheus retention of at least 14 days
#: (15 days by default).
RANGES = {
    "24h": ("24h", "1m"),
    "7d": ("7d", "5m"),
    "14d": ("14d", "10m"),
}
DEFAULT_RANGE = "7d"

#: Hosts are always judged on this window, whatever the range: a host that
#: turned slow recently would hide behind its older, normal boots.
STATUS_WINDOW = "24h"

#: Maximum rows of the host table and of the slowest boots table.
TOP_LIMIT = 10
#: Maximum failed builds listed (the newest first).
FAILED_LIMIT = 8
#: Maximum failed builds read from the Compute API per range.
FAILED_QUERY_LIMIT = 100
#: Maximum dots in the boot times chart.
MAX_POINTS = 2000
#: Below this many boots the cards show the slowest boot instead of the
#: 95th percentile, which needs enough boots to mean anything.
PERCENTILE_MIN_BOOTS = 20

STATUS_SLOW = "slow"
STATUS_SLOWER = "slower"
STATUS_NORMAL = "normal"
STATUS_FEW = "few"
STATUS_LABELS = {
    STATUS_SLOW: gettext_lazy("Slow"),
    STATUS_SLOWER: gettext_lazy("Slower"),
    STATUS_NORMAL: gettext_lazy("Normal"),
    STATUS_FEW: gettext_lazy("Too few boots"),
}


def get_range(key):
    """Return ``(key, range, step)``, falling back to the default range."""
    if key not in RANGES:
        key = DEFAULT_RANGE
    window, step = RANGES[key]
    return key, window, step


def range_seconds(key):
    """Length of a range in seconds."""
    _key, window, _step = get_range(key)
    return timeseries.duration_seconds(window)


def quantile(values, q):
    """
    Quantile of ``values`` (``None`` if empty), like PromQL ``quantile()``.

    Linear interpolation between the closest ranks.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = q * (len(ordered) - 1)
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def median(values):
    return quantile(values, 0.5)


def host_status(boots_24h, ratio, thresholds):
    """Status of a host from its last 24 hours of boots."""
    if boots_24h < thresholds["host_min_boots"] or ratio is None:
        return STATUS_FEW
    if ratio >= thresholds["host_ratio_critical"]:
        return STATUS_SLOW
    if ratio >= thresholds["host_ratio_warning"]:
        return STATUS_SLOWER
    return STATUS_NORMAL


_JUDGED = (STATUS_SLOW, STATUS_SLOWER, STATUS_NORMAL)


def _host_sort_key(row):
    # judged hosts first, the slowest compared to the cloud first; then the
    # others by their median over the range
    judged = row["status"] in _JUDGED
    return (
        not judged,
        -(row["ratio"] or 0) if judged else 0,
        -(row["median"] or 0),
        row["name"],
    )


def thin(boots, keep, max_points=MAX_POINTS):
    """
    At most ``max_points`` boots for the chart, sorted by time.

    Boots for which ``keep(boot)`` is true (on slow hosts) are kept first;
    the others are thinned evenly over time to fill the rest.

    Returns:
        tuple: (boots, thinned)
    """
    ordered = sorted(boots, key=lambda b: b["at"])
    if len(ordered) <= max_points:
        return ordered, False
    kept = [b for b in ordered if keep(b)]
    others = [b for b in ordered if not keep(b)]
    if len(kept) >= max_points:
        kept, others = _evenly(kept, max_points), []
    else:
        others = _evenly(others, max_points - len(kept))
    return sorted(kept + others, key=lambda b: b["at"]), True


def _evenly(items, count):
    """``count`` items spread evenly over ``items`` (keeps the first)."""
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


def summarize(boots, now, range_key, thresholds, limit=TOP_LIMIT):
    """
    Summarize the boots of a range.

    The result is bounded whatever the number of boots: at most ``limit``
    hosts and slowest boots, and MAX_POINTS chart points. Hosts are judged
    on the last STATUS_WINDOW only.

    Returns:
        dict: see the keys below
    """
    start = now - range_seconds(range_key)
    recent_start = now - timeseries.duration_seconds(STATUS_WINDOW)
    # no upper bound: a clock slightly behind Prometheus is harmless
    in_range = [b for b in boots if b["at"] >= start]
    seconds = [b["seconds"] for b in in_range]
    recent = [b for b in in_range if b["at"] >= recent_start]
    many = len(in_range) >= PERCENTILE_MIN_BOOTS

    by_host, recent_by_host = {}, {}
    for boot in in_range:
        if boot.get("host"):
            by_host.setdefault(boot["host"], []).append(boot["seconds"])
    for boot in recent:
        if boot.get("host"):
            recent_by_host.setdefault(boot["host"], []).append(boot["seconds"])

    cloud_median_24h = median([b["seconds"] for b in recent])
    hosts = []
    for name, values in by_host.items():
        values_24h = recent_by_host.get(name, [])
        median_24h = median(values_24h)
        ratio = (
            median_24h / cloud_median_24h
            if median_24h is not None and cloud_median_24h
            else None
        )
        hosts.append(
            {
                "name": name,
                "boots": len(values),
                "median": median(values),
                "slowest": max(values),
                "boots_24h": len(values_24h),
                "median_24h": median_24h,
                "ratio": ratio,
                "status": host_status(len(values_24h), ratio, thresholds),
            }
        )
    hosts.sort(key=_host_sort_key)
    slow = [h for h in hosts if h["status"] == STATUS_SLOW]
    slower = [h for h in hosts if h["status"] == STATUS_SLOWER]
    slow_names = {h["name"] for h in slow}

    points, thinned = thin(in_range, lambda b: b.get("host") in slow_names)
    slowest = sorted(in_range, key=lambda b: (-b["seconds"], b["at"]))
    return {
        "range": range_key,
        "start": start,
        "boots": len(in_range),
        "boots_24h": len(recent),
        "hosts_total": len(by_host),
        "without_host": sum(1 for b in in_range if not b.get("host")),
        "median": median(seconds),
        "p95": quantile(seconds, 0.95) if many else None,
        "slowest_seconds": max(seconds) if seconds else None,
        "many": many,
        "median_24h": cloud_median_24h,
        "hosts": hosts[:limit],
        "slow_hosts": [{"name": h["name"], "ratio": h["ratio"]} for h in slow],
        "slower_hosts": len(slower),
        "slowest": [dict(b) for b in slowest[:limit]],
        "points": [
            [
                b["at"],
                b["seconds"],
                b.get("name"),
                b.get("project_id"),
                b.get("host"),
                b.get("host") in slow_names,
            ]
            for b in points
        ],
        "points_total": len(in_range),
        "thinned": thinned,
    }


def failed_percent(failed, boots):
    """Failed builds as a percentage of create requests, or None."""
    total = failed + boots
    if not total:
        return None
    return 100.0 * failed / total


def failed_level(failed, boots, thresholds):
    """Card level of the failed builds: ok, warn or crit."""
    if not failed:
        return "ok"
    percent = failed_percent(failed, boots)
    if percent is not None and percent > thresholds["failed_percent_critical"]:
        return "crit"
    return "warn"
