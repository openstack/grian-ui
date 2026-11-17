#
# SPDX-License-Identifier: Apache-2.0

"""
Compute host health views.

The index page is a light shell; every section is an HTMX partial that
loads and refreshes itself. Each partial renders a bounded amount of data
(cards, at most 10 hosts, a few bands, 3 fleet series) whatever the number
of compute hosts.

Fleet-wide data (the per-host snapshot and the fleet trend) is cached in the
Horizon cache and shared by the partials and by the administrators of a
region. A short lock prevents the partials of one refresh, which fire at the
same moment, from all missing the cache and querying in parallel.
"""

import logging
import re
import time
import uuid

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from django.views import generic

from grian_ui import compute_health
from grian_ui import config
from grian_ui.datasource.api import get_datasource


LOG = logging.getLogger(__name__)

#: Refresh interval of the partials, in seconds (used by the templates).
REFRESH_SECONDS = 30
#: Seconds the per-host snapshot is shared. Slightly shorter than the
#: refresh interval so every refresh gets new data, computed once.
SNAPSHOT_CACHE_TTL = REFRESH_SECONDS - 5
#: Seconds the fleet trend is shared, per range (coarser ranges change
#: slower).
FLEET_TREND_CACHE_TTL = {"1h": 25, "6h": 60, "24h": 300, "7d": 900}
#: How long a request computing a cached value holds the lock.
LOCK_SECONDS = 15
#: How long other requests wait for that value before falling back to the
#: last good copy (they never recompute in parallel while the lock is held).
#: Without a last good copy they wait up to LOCK_SECONDS.
LOCK_WAIT_SECONDS = 5
#: How long a failed computation is remembered, so waiters and the next
#: partials of the same refresh do not retry it.
FAILURE_TTL = 5
#: How long the last good copy of a value is kept as a fallback.
LAST_GOOD_TTL = 600
#: Bump when the format of a cached value changes, so values written by an
#: older version (memcached survives Horizon restarts) are never read.
CACHE_VERSION = 2
_LOCK_POLL_SECONDS = 0.1


class DataUnavailable(Exception):
    """Raised when shared data could not be computed or waited for."""


def _region(request):
    region = getattr(request.user, "services_region", None) or "default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(region))


def _cache_key(request, name):
    return (
        f"grian_ui:compute_host_health:v{CACHE_VERSION}:{name}:"
        f"{config.get_datasource_type()}:{_region(request)}"
    )


def get_or_compute(key, ttl, compute):
    """
    Return the cached dict for ``key``, computing it at most once at a time.

    The request that takes the lock (``cache.add`` is atomic in memcached and
    the local memory backend) computes the value; the others wait for it
    instead of running the same queries in parallel. When the value cannot
    be obtained in time:

    * after a failed computation (remembered for FAILURE_TTL), or when a
      waiter times out, the last good copy is returned, marked with
      ``"is_stale_copy": True`` (stale-while-revalidate);
    * without a last good copy, the failure is raised (``DataUnavailable``
      for waiters) and a waiter keeps waiting up to LOCK_SECONDS;
    * if the lock disappears without a value or a failure marker (it
      expired), one waiter takes it over.

    A slow or struggling backend is therefore never hit by a burst of
    identical queries.
    """
    value = cache.get(key)
    if value is not None:
        return value

    lock_key, failed_key = f"{key}:lock", f"{key}:failed"
    last_key = f"{key}:last"

    def last_good():
        last = cache.get(last_key)
        return dict(last, is_stale_copy=True) if last is not None else None

    start = time.monotonic()
    while True:
        if cache.get(failed_key):
            fallback = last_good()
            if fallback is not None:
                return fallback
            raise DataUnavailable("recent computation failed")
        if cache.add(lock_key, True, LOCK_SECONDS):
            try:
                # the previous holder may have finished since our last poll
                value = cache.get(key)
                if value is not None:
                    return value
                try:
                    value = compute()
                except Exception:
                    cache.set(failed_key, True, FAILURE_TTL)
                    fallback = last_good()
                    if fallback is None:
                        raise
                    LOG.exception(
                        "Error refreshing %s, serving the last good copy", key
                    )
                    return fallback
                cache.set(key, value, ttl)
                cache.set(last_key, value, LAST_GOOD_TTL)
                return value
            finally:
                cache.delete(lock_key)

        waited = time.monotonic() - start
        if waited >= LOCK_WAIT_SECONDS:
            fallback = last_good()
            if fallback is not None:
                return fallback
            if waited >= LOCK_SECONDS:
                raise DataUnavailable("timed out waiting for shared data")
        time.sleep(_LOCK_POLL_SECONDS)
        value = cache.get(key)
        if value is not None:
            return value


class _ComputeHostHealthMixin:
    """Shared helpers: lazy datasource client, cached data, errors."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Unique per response: element ids inside partials must not repeat
        # across swaps, or htmx "settles" the new element with the old
        # element's attributes after the chart is drawn, which resets the
        # canvas size and clears the chart.
        context["uid"] = uuid.uuid4().hex[:12]
        return context

    def get_client(self):
        if getattr(self, "_client", None) is None:
            self._client = get_datasource(self.request)
        return self._client

    def get_snapshot(self):
        def compute():
            snapshot = self.get_client().get_compute_host_snapshot()
            return dict(snapshot, fetched_at=time.time())

        return get_or_compute(
            _cache_key(self.request, "snapshot"), SNAPSHOT_CACHE_TTL, compute
        )

    def get_fleet_trend(self, range_key):
        """
        Fleet trend for ``range_key`` with the time it was fetched.

        Returns:
            dict: ``{"series": {"p50": ..., "p95": ..., "max": ...},
            "fetched_at": epoch}``; grids must be built from ``fetched_at``
            so a cached trend does not grow a false gap at the end.
        """

        def compute():
            return {
                "series": self.get_client().get_compute_fleet_trend(range_key),
                "fetched_at": time.time(),
            }

        return get_or_compute(
            _cache_key(self.request, f"fleet:{range_key}"),
            FLEET_TREND_CACHE_TTL[range_key],
            compute,
        )

    def get_range_key(self):
        key, _window, _step = compute_health.get_range(
            self.request.GET.get("range")
        )
        return key

    def handle_error(self, context, exc):
        # details (backend messages) go to the log, not to the page
        LOG.exception("Error loading compute host health data: %s", exc)
        context["error_message"] = _(
            "Error loading telemetry data. See the Horizon logs for details."
        )
        return context


def _with_ages(rows, now):
    for row in rows:
        if row.get("last_seen") is not None:
            row["last_seen_age"] = compute_health.format_age(
                now - row["last_seen"]
            )
    return rows


def _gap_filled_series(points, range_key, now):
    timestamps, values = compute_health.fill_gaps(points, range_key, now)
    return [[t, v] for t, v in zip(timestamps, values)]


class ComputeHostHealthIndexView(
    _ComputeHostHealthMixin, generic.TemplateView
):
    template_name = "admin/compute_host_health/index.html"
    page_title = _("Compute Host Health")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        context["ranges"] = list(compute_health.RANGES)
        context["default_range"] = compute_health.DEFAULT_RANGE
        context["refresh_seconds"] = REFRESH_SECONDS
        context["stale_after"] = compute_health.STALE_AFTER
        context["stale_lookback"] = compute_health.STALE_LOOKBACK
        try:
            context["thresholds"] = config.get_compute_host_thresholds()
        except config.ConfigurationError as exc:
            context["error_message"] = str(exc)
        return context


class SummaryView(_ComputeHostHealthMixin, generic.TemplateView):
    """Summary cards and the capped attention list."""

    template_name = "admin/compute_host_health/_summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            thresholds = config.get_compute_host_thresholds()
            snapshot = self.get_snapshot()
            summary = compute_health.summarize(snapshot, thresholds)
            _with_ages(summary["attention"], time.time())
            context["summary"] = summary
            context["thresholds"] = thresholds
            context["top_limit"] = compute_health.TOP_LIMIT
            context["stale_lookback"] = compute_health.STALE_LOOKBACK
            context["fetched_at"] = snapshot.get("fetched_at")
            context["fetched_ts"] = str(int(snapshot.get("fetched_at") or 0))
            context["data_stale"] = snapshot.get("is_stale_copy", False)
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class DistributionView(_ComputeHostHealthMixin, generic.TemplateView):
    """Number of hosts per CPU band."""

    template_name = "admin/compute_host_health/_distribution.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            thresholds = config.get_compute_host_thresholds()
            snapshot = self.get_snapshot()
            summary = compute_health.summarize(snapshot, thresholds)
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            context["bands"] = summary["bands"]
            context["total"] = summary["banded"]
            context["chart"] = {
                "labels": [b["label"] for b in summary["bands"]],
                "counts": [b["count"] for b in summary["bands"]],
                "levels": [b["level"] for b in summary["bands"]],
                "total": summary["banded"],
            }
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class FleetTrendView(_ComputeHostHealthMixin, generic.TemplateView):
    """Fleet median, 95th percentile and max CPU over time."""

    template_name = "admin/compute_host_health/_fleet_trend.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            thresholds = config.get_compute_host_thresholds()
            fleet = self.get_fleet_trend(range_key)
            trend, fetched_at = fleet["series"], fleet["fetched_at"]
            context["has_data"] = any(trend.values())
            context["data_stale"] = fleet.get("is_stale_copy", False)
            context["chart"] = {
                "range": range_key,
                "series": {
                    stat: _gap_filled_series(points, range_key, fetched_at)
                    for stat, points in trend.items()
                },
                "warning": thresholds["cpu_warning"],
                "critical": thresholds["cpu_critical"],
            }
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class TopHostsView(_ComputeHostHealthMixin, generic.TemplateView):
    """At most TOP_LIMIT hosts: busiest CPU, I/O wait, stale or a band."""

    template_name = "admin/compute_host_health/_top_hosts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        by = self.request.GET.get("by")
        if by not in compute_health.TOP_BY_CHOICES:
            by = compute_health.TOP_BY_CPU
        try:
            band = int(self.request.GET.get("band", ""))
        except ValueError:
            band = None
        range_key = self.get_range_key()
        context.update(by=by, band=band, range_key=range_key)

        try:
            thresholds = config.get_compute_host_thresholds()
            snapshot = self.get_snapshot()
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            rows, total = compute_health.select_top(
                snapshot, thresholds, by=by, band=band
            )
            names = [
                r["name"]
                for r in rows
                if r["status"]["key"] != compute_health.STATUS_STALE
            ]
            details, trends = {}, {}
            if names:
                client = self.get_client()
                details = client.get_compute_host_details(names)
                trends = client.get_compute_host_trends(names, range_key)
            for row in rows:
                detail = details.get(row["name"])
                if detail:
                    row["detail"] = detail
                    if row["last_seen"] is None:
                        row["last_seen"] = detail.get("last_seen")
            now = time.time()
            _with_ages(rows, now)

            bands = compute_health.build_bands(thresholds)
            context["band_label"] = (
                bands[band]["label"]
                if band is not None and 0 <= band < len(bands)
                else None
            )
            context["rows"] = rows
            context["total"] = total
            context["trends"] = {
                name: compute_health.fill_gaps(points, range_key, now)[1]
                for name, points in trends.items()
            }
            context["thresholds"] = thresholds
            context["top_limit"] = compute_health.TOP_LIMIT
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class HostLookupView(_ComputeHostHealthMixin, generic.TemplateView):
    """Status, CPU breakdown and trend of one host."""

    template_name = "admin/compute_host_health/_host_lookup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = (self.request.GET.get("name") or "").strip()
        range_key = self.get_range_key()
        context.update(
            name=name, range_key=range_key, refresh_seconds=REFRESH_SECONDS
        )
        if not name:
            return context

        try:
            thresholds = config.get_compute_host_thresholds()
            snapshot = self.get_snapshot()
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            host = compute_health.find_host(snapshot, name)
            context["found"] = host is not None
            if host is None:
                context["stale_lookback"] = compute_health.STALE_LOOKBACK
                return context

            now = time.time()
            if host.get("stale"):
                context["status"] = compute_health.stale_status()
                context["last_seen_age"] = compute_health.format_age(
                    now - host["last_seen"]
                )
                return context

            client = self.get_client()
            detail = client.get_compute_host_details([name]).get(name, {})
            trend = client.get_compute_host_trends([name], range_key).get(
                name, []
            )
            fleet = self.get_fleet_trend(range_key)
            context["status"] = compute_health.evaluate_host(host, thresholds)
            context["host"] = host
            context["detail"] = detail
            if detail.get("last_seen") is not None:
                context["last_seen_age"] = compute_health.format_age(
                    now - detail["last_seen"]
                )
            context["chart"] = {
                "name": name,
                "range": range_key,
                "host": _gap_filled_series(trend, range_key, now),
                "fleet_median": _gap_filled_series(
                    fleet["series"].get("p50", []),
                    range_key,
                    fleet["fetched_at"],
                ),
                "warning": thresholds["cpu_warning"],
                "critical": thresholds["cpu_critical"],
            }
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class HostSuggestionsView(_ComputeHostHealthMixin, generic.TemplateView):
    """
    Host name suggestions for the lookup box (``<option>`` elements).

    Served from the cached snapshot: no extra queries, at most
    SUGGESTION_LIMIT names whatever the number of hosts.
    """

    template_name = "admin/compute_host_health/_host_suggestions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context["suggestions"] = compute_health.suggest_hosts(
                self.get_snapshot(),
                config.get_compute_host_thresholds(),
                self.request.GET.get("name"),
            )
        except Exception:
            # suggestions are a convenience: never show an error here
            LOG.exception("Error loading compute host suggestions")
            context["suggestions"] = []
        return context
