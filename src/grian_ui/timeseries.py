#
# SPDX-License-Identifier: Apache-2.0

"""
Time series helpers shared by the Telemetry dashboards.

The dashboards draw trends from Prometheus subqueries (``expr[range:step]``)
because Aetos only exposes the instant query endpoint. These helpers define
the selectable ranges, align the returned points to the subquery grid so
charts show gaps instead of stretching partial data, and format ages.
"""

import time

from django.utils.translation import ngettext


#: Trend ranges: key -> (range, step). Steps keep about 60-170 points.
RANGES = {
    "1h": ("1h", "1m"),
    "6h": ("6h", "5m"),
    "24h": ("24h", "15m"),
    "7d": ("7d", "1h"),
}
DEFAULT_RANGE = "6h"

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def get_range(key):
    """Return ``(key, range, step)``, falling back to the default range."""
    if key not in RANGES:
        key = DEFAULT_RANGE
    window, step = RANGES[key]
    return key, window, step


def duration_seconds(duration):
    """Convert a simple Prometheus duration ("5m", "24h") to seconds."""
    return int(duration[:-1]) * _DURATION_UNITS[duration[-1]]


def range_grid(range_key, now=None):
    """
    Timestamps a subquery ``expr[range:step]`` evaluated at ``now`` returns.

    Subquery steps are aligned to multiples of the step, so there are
    ``range / step`` points ending at the last step boundary at or before
    ``now``. (Prometheus 2 also returns a point at the start of the range
    when that start falls exactly on a step boundary; it is outside the grid
    and ignored.) Evaluate the query at the same ``now``: see
    ``PrometheusTelemetryClient.query_matrix(time=...)``.
    """
    _key, window, step = get_range(range_key)
    step_s = duration_seconds(step)
    count = duration_seconds(window) // step_s
    end = int((now if now is not None else time.time()) // step_s * step_s)
    return [end - (count - 1 - i) * step_s for i in range(count)]


def fill_gaps(points, range_key, now=None):
    """
    Align ``[[timestamp, value], ...]`` to the range's step grid.

    Missing steps become ``None`` so charts show gaps instead of stretching
    partial data across the whole range.

    Returns:
        tuple: (timestamps, values) of equal length
    """
    timestamps = range_grid(range_key, now)
    step_s = timestamps[1] - timestamps[0]
    by_slot = {}
    for timestamp, value in points:
        by_slot[int(round(timestamp / step_s)) * step_s] = value
    return timestamps, [by_slot.get(t) for t in timestamps]


def gap_filled_series(points, range_key, now=None):
    """Like :func:`fill_gaps`, as ``[[timestamp, value_or_None], ...]``."""
    timestamps, values = fill_gaps(points, range_key, now)
    return [[t, v] for t, v in zip(timestamps, values)]


def format_age(seconds):
    """Human readable age in whole units: '45 seconds ago', '1 hour ago'."""
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    if seconds < 60:
        n = seconds
        text = ngettext("%(n)s second ago", "%(n)s seconds ago", n)
    elif seconds < 3600:
        n = seconds // 60
        text = ngettext("%(n)s minute ago", "%(n)s minutes ago", n)
    elif seconds < 86400:
        n = seconds // 3600
        text = ngettext("%(n)s hour ago", "%(n)s hours ago", n)
    else:
        n = seconds // 86400
        text = ngettext("%(n)s day ago", "%(n)s days ago", n)
    return text % {"n": n}
