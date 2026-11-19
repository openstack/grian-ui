#
# SPDX-License-Identifier: Apache-2.0

"""
Provisioning views.

The index page is a light shell; every section is an HTMX partial that
loads and refreshes itself. Each partial renders a bounded amount of data
(cards, at most 10 hosts, 10 slowest boots, 8 failed builds and
MAX_POINTS chart dots) whatever the number of boots.

Every source is a snapshot shared through the Horizon cache by the
partials and by the administrators of a region (see grian_ui.cache), each
with its own lifetime:

* the summary of the boots of a range (Prometheus), every refresh;
* the failed builds of a range (Compute API), every COMPUTE_CACHE_TTL;
* the hypervisor list (Compute API), every HYPERVISOR_CACHE_TTL;
* the flavor of each slow instance (Compute API), once per instance for
  FLAVOR_CACHE_TTL;
* the project names (Identity API), every PROJECTS_CACHE_TTL.

Page refreshes only read these snapshots: the APIs are called when a
snapshot expires, at most once per region, however many administrators
watch the page. Each process joins the snapshots once per pair of inputs.
"""

import collections
import datetime
import logging
import threading
import time
import urllib.parse
import uuid

from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import generic

from grian_ui import cache
from grian_ui import config
from grian_ui import provisioning
from grian_ui import provisioning_inventory
from grian_ui.datasource.api import get_datasource


LOG = logging.getLogger(__name__)

#: Refresh interval of the partials, in seconds (used by the templates).
#: Boots are sparse events: a few minutes is plenty.
REFRESH_SECONDS = 300
#: Seconds the summary of a range is shared. Shorter than the refresh
#: interval so every refresh gets new data, computed once.
SUMMARY_CACHE_TTL = REFRESH_SECONDS - 30
#: Seconds the failed builds of a range (one Compute API call) are shared.
COMPUTE_CACHE_TTL = 300
#: Seconds the hypervisor list (service host -> hypervisor hostname, for
#: the links to Compute Host Health) is shared. It changes rarely.
HYPERVISOR_CACHE_TTL = 600
#: Seconds the flavor of an instance is shared: one Compute API call per
#: slow instance, not per refresh. Flavors only change on resize.
FLAVOR_CACHE_TTL = 3600
#: Seconds the project names are shared.
PROJECTS_CACHE_TTL = 600
#: Bump when the format of a cached value changes (see
#: grian_ui.cache.cache_key).
CACHE_VERSION = 2
CACHE_NAMESPACE = "provisioning"
#: URL name of the Compute Host Health panel, linked from host names when
#: it is enabled.
CHH_URL_NAME = "horizon:admin:compute_host_health:index"

#: Joins kept per process (see _ProvisioningMixin.joined).
JOINS_KEPT = 16
_JOINS = collections.OrderedDict()
_JOINS_LOCK = threading.Lock()

_LOGGED_CONFIG_ERRORS = set()

# Notes for optional data that is not available: (failed, still loading)
OPTIONAL_NOTES = {
    "failed": (
        _("The failed builds could not be loaded from the Compute API."),
        _("Loading the failed builds…"),
    ),
    "projects": (
        _("Project names could not be loaded: showing project IDs."),
        _("Loading the project names…"),
    ),
    "flavors": (
        _("Flavors could not be loaded from the Compute API."),
        _("Loading the flavors…"),
    ),
    "hypervisors": (
        _(
            "The hypervisor list could not be loaded from the Compute API: "
            "hosts are not linked to Compute Host Health."
        ),
        _("Loading the hypervisor list…"),
    ),
}


def _cache_key(request, name):
    return cache.cache_key(
        request, CACHE_NAMESPACE, name, version=CACHE_VERSION
    )


def _check_datasource_settings():
    """
    Raise ConfigurationError for settings read when the client is built.

    The client is built inside shared computations, and only the request
    that runs a computation sees its cause: checking first gives every
    section (and the index) the same message.
    """
    if config.get_datasource_type() == config.DATASOURCE_PROMETHEUS:
        config.get_openstack_keystone_url()


def _thresholds_key(thresholds):
    """Cache key part: summaries depend on the thresholds."""
    return "-".join(f"{thresholds[k]:g}" for k in sorted(thresholds))


def clock(timestamp):
    """
    A time for the templates, shown in the browser's time zone.

    Returns:
        dict or None: ``ts`` (epoch seconds, formatted by the page script
        like the other Telemetry panels), ``iso`` (for ``<time
        datetime>``) and ``utc``, the text shown without JavaScript
    """
    if timestamp is None:
        return None
    when = datetime.datetime.fromtimestamp(
        int(timestamp), datetime.timezone.utc
    )
    return {
        "ts": str(int(timestamp)),
        "iso": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc": when.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _short_id(value):
    return (value or "")[:8] or None


def chh_url():
    """URL of the Compute Host Health panel, or None when not enabled."""
    try:
        return reverse(CHH_URL_NAME)
    except NoReverseMatch:
        return None


def host_link(host, hypervisors, base_url):
    """
    Link to a host in Compute Host Health, or None.

    The boot time metric names the nova-compute service host; Compute Host
    Health names hypervisors. Only a service host with exactly one known
    hypervisor is linked.

    Returns:
        dict or None: ``{"url": ..., "hypervisor": ...}``
    """
    if not host or not base_url:
        return None
    names = hypervisors.get(host) or []
    if len(names) != 1:
        return None
    query = urllib.parse.urlencode({"host": names[0]})
    return {"url": f"{base_url}?{query}", "hypervisor": names[0]}


class _ProvisioningMixin:
    """Shared helpers: lazy clients, cached snapshots, errors."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Unique per response: element ids inside partials must not repeat
        # across swaps, or htmx "settles" the new element with the old
        # element's attributes after the chart is drawn.
        context["uid"] = uuid.uuid4().hex[:12]
        context["compute_minutes"] = COMPUTE_CACHE_TTL // 60
        context["hypervisor_minutes"] = HYPERVISOR_CACHE_TTL // 60
        return context

    def get_or_compute(self, name, ttl, compute):
        """
        cache.get_or_compute, after checking the datasource settings.

        The cache marks a last good copy served after a failure
        (``is_stale_copy``), not one served while another request is still
        refreshing it (a slow or hanging backend). Values are stamped with
        ``stored_at`` when their computation ends: one older than its
        lifetime plus a refresh interval is marked ``is_old_copy`` here:
        probably still being refreshed, possibly out of date. (The age
        compares the clocks of the Horizon nodes sharing the cache: keep
        them in sync.)
        """
        _check_datasource_settings()

        def stamped():
            return dict(compute(), stored_at=time.time())

        value = cache.get_or_compute(
            _cache_key(self.request, name), ttl, stamped
        )
        stored_at = value.get("stored_at")
        if (
            not value.get("is_stale_copy")
            and stored_at is not None
            and time.time() - stored_at > ttl + REFRESH_SECONDS
        ):
            value = dict(value, is_old_copy=True)
        return value

    def joined(self, name, inputs, build):
        """
        ``build()``, computed once per process for the same inputs.

        Inputs are the shared values (or None) the result is built from;
        their ``stored_at`` stamps identify them. Callers must not modify
        the result.
        """
        key = (
            _cache_key(self.request, name),
            tuple(
                (v.get("stored_at"), v.get("fetched_at")) if v else None
                for v in inputs
            ),
        )
        with _JOINS_LOCK:
            found = _JOINS.get(key)
        if found is None:
            found = build()
            with _JOINS_LOCK:
                _JOINS[key] = found
                while len(_JOINS) > JOINS_KEPT:
                    _JOINS.popitem(last=False)
        return found

    def get_client(self):
        if getattr(self, "_client", None) is None:
            self._client = get_datasource(self.request)
        return self._client

    def get_inventory(self):
        if getattr(self, "_inventory", None) is None:
            self._inventory = provisioning_inventory.get_inventory(
                self.request
            )
        return self._inventory

    def get_range_key(self):
        key, _window, _step = provisioning.get_range(
            self.request.GET.get("range")
        )
        return key

    # Shared snapshots ---------------------------------------------------

    def get_summary(self, range_key, thresholds):
        """
        Summary of the boots of ``range_key`` (see provisioning.summarize).

        One computation per range and thresholds, shared by every partial
        and administrator; the cached value is bounded.
        """

        def compute():
            now = time.time()
            boots = self.get_client().get_provisioning_boots(range_key)
            summary = provisioning.summarize(boots, now, range_key, thresholds)
            return dict(summary, fetched_at=now)

        return self.get_or_compute(
            f"summary:{range_key}:{_thresholds_key(thresholds)}",
            SUMMARY_CACHE_TTL,
            compute,
        )

    def get_failed(self, range_key):
        """Failed builds of ``range_key``: one Compute API call."""

        def compute():
            now = time.time()
            found = self.get_inventory().failed_builds(
                now - provisioning.range_seconds(range_key),
                provisioning.FAILED_QUERY_LIMIT,
            )
            return {
                "builds": found["builds"],
                "count": len(found["builds"]),
                "truncated": found["truncated"],
                "fetched_at": now,
            }

        return self.get_or_compute(
            f"failed:{range_key}", COMPUTE_CACHE_TTL, compute
        )

    def get_flavor(self, instance_id):
        """Flavor of one instance: one Compute API call per instance."""

        def compute():
            return {
                "flavor": self.get_inventory().flavor(instance_id),
                "fetched_at": time.time(),
            }

        return self.get_or_compute(
            f"flavor:{instance_id}", FLAVOR_CACHE_TTL, compute
        )

    def get_hypervisors(self):
        """Service host -> hypervisor hostnames: one Compute API call."""

        def compute():
            return {
                "hosts": self.get_inventory().hypervisor_hosts(),
                "fetched_at": time.time(),
            }

        return self.get_or_compute(
            "hypervisors", HYPERVISOR_CACHE_TTL, compute
        )

    def get_projects(self):
        def compute():
            return {
                "names": self.get_inventory().project_names(),
                "fetched_at": time.time(),
            }

        return self.get_or_compute("projects", PROJECTS_CACHE_TTL, compute)

    # Optional data and notes --------------------------------------------

    def get_optional(self, context, part, fetch):
        """
        Optional data: on DataUnavailable note it and return None.

        The section still renders; ``<part>_unavailable`` and
        ``<part>_loading`` describe what is missing, ``partial_notes``
        says it, and ``loading`` makes the section retry shortly.
        """
        try:
            value = fetch()
        except cache.DataUnavailable as exc:
            loading = isinstance(exc, cache.StillComputing)
            context[f"{part}_unavailable"] = True
            context[f"{part}_loading"] = loading
            context["loading"] = context.get("loading") or loading
            note = OPTIONAL_NOTES[part][loading]
            notes = context.setdefault("partial_notes", [])
            if note not in notes:
                notes.append(note)
            return None
        return value

    @staticmethod
    def note_age(context, source, value):
        """
        Note when ``value`` was fetched and whether it is out of date.

        ``data_stale``: a last good copy shown after a failure;
        ``data_old``: older than its lifetime plus a refresh interval,
        probably still being refreshed. ``<source>_at`` is when the value
        was fetched (see clock()).
        """
        if value.get("is_stale_copy"):
            context["data_stale"] = True
        elif value.get("is_old_copy"):
            context["data_old"] = True
        context[f"{source}_at"] = clock(value.get("fetched_at"))
        return value

    def project_names(self, context):
        """Project id -> name, or {} (IDs are shown) when unavailable."""
        found = self.get_optional(context, "projects", self.get_projects)
        return found["names"] if found else {}

    def host_links(self, context, hosts):
        """
        Host -> link to Compute Host Health, for the given hosts.

        Empty when Compute Host Health is not enabled (the hypervisor list
        is then never read) or the hypervisor list is unavailable.
        """
        base_url = chh_url()
        if not base_url or not any(hosts):
            return {}
        found = self.get_optional(context, "hypervisors", self.get_hypervisors)
        if found is None:
            return {}
        self.note_age(context, "hypervisors", found)
        return {
            host: host_link(host, found["hosts"], base_url)
            for host in hosts
            if host
        }

    def handle_error(self, context, exc):
        if isinstance(exc, cache.ComputationFailed) and isinstance(
            exc.__cause__, config.ConfigurationError
        ):
            # raised inside a shared computation (e.g. building the client)
            exc = exc.__cause__
        _forget_partial_state(context)
        if isinstance(exc, config.ConfigurationError):
            if str(exc) not in _LOGGED_CONFIG_ERRORS:
                _LOGGED_CONFIG_ERRORS.add(str(exc))
                LOG.error("Invalid provisioning setting: %s", exc)
            # the index shows the message once, at the top of the page
            context["error_message"] = _(
                "Not available: invalid configuration (see the top of the "
                "page)."
            )
            return context
        if isinstance(exc, cache.StillComputing):
            # another request is still computing the shared data: show a
            # loading state, the template retries shortly
            LOG.info("Provisioning data not ready yet: %s", exc)
            context["loading"] = True
            context["error_message"] = _("Still loading telemetry data…")
            return context
        if isinstance(exc, cache.DataUnavailable):
            # grian_ui.cache already logged the failure with its traceback
            LOG.warning("Provisioning data unavailable: %s", exc)
        else:
            # details (backend messages) go to the log, not to the page
            LOG.exception("Error loading provisioning data: %s", exc)
        context["error_message"] = _(
            "Error loading telemetry data. See the Horizon logs for details."
        )
        return context


def _forget_partial_state(context):
    """Drop the optional-data state of a section replaced by an error."""
    for key in list(context):
        if key.endswith(("_unavailable", "_loading", "_at")) or key in (
            "loading",
            "partial_notes",
            "data_stale",
            "data_old",
        ):
            del context[key]


class ProvisioningIndexView(_ProvisioningMixin, generic.TemplateView):
    template_name = "admin/provisioning/index.html"
    page_title = _("Provisioning")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        context["ranges"] = list(provisioning.RANGES)
        context["default_range"] = provisioning.DEFAULT_RANGE
        context["refresh_seconds"] = REFRESH_SECONDS
        context["refresh_minutes"] = REFRESH_SECONDS // 60
        context["flavor_minutes"] = FLAVOR_CACHE_TTL // 60
        context["projects_minutes"] = PROJECTS_CACHE_TTL // 60
        context["status_window"] = provisioning.STATUS_WINDOW
        context["top_limit"] = provisioning.TOP_LIMIT
        context["failed_limit"] = provisioning.FAILED_LIMIT
        context["failed_query_limit"] = provisioning.FAILED_QUERY_LIMIT
        context["max_points"] = provisioning.MAX_POINTS
        context["percentile_min_boots"] = provisioning.PERCENTILE_MIN_BOOTS
        context["chh_enabled"] = chh_url() is not None
        context["range_steps"] = [
            _("%(range)s: %(step)s") % {"range": key, "step": step}
            for key, (_window, step) in provisioning.RANGES.items()
        ]
        # every invalid setting at once, not one per page load
        errors = []
        try:
            context["thresholds"] = config.get_provisioning_thresholds()
        except config.ConfigurationError as exc:
            errors.append(str(exc))
        try:
            # read when the datasource client is built: shown here, once
            _check_datasource_settings()
        except config.ConfigurationError as exc:
            errors.append(str(exc))
        context["error_messages"] = errors
        return context


class SummaryView(_ProvisioningMixin, generic.TemplateView):
    """The four summary cards."""

    template_name = "admin/provisioning/_summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            thresholds = config.get_provisioning_thresholds()
            summary = self.get_summary(range_key, thresholds)
            self.note_age(context, "metrics", summary)
            context["summary"] = summary
            context["thresholds"] = thresholds
            failed = self.get_optional(
                context, "failed", lambda: self.get_failed(range_key)
            )
            if failed is not None:
                self.note_age(context, "compute", failed)
                context["failed"] = failed
                context["failed_percent"] = provisioning.failed_percent(
                    failed["count"], summary["boots"]
                )
                context["failed_level"] = provisioning.failed_level(
                    failed["count"], summary["boots"], thresholds
                )
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class BootTimesView(_ProvisioningMixin, generic.TemplateView):
    """One dot per boot (at most MAX_POINTS) and the cloud median."""

    template_name = "admin/provisioning/_boot_times.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            thresholds = config.get_provisioning_thresholds()
            summary = self.get_summary(range_key, thresholds)
            self.note_age(context, "metrics", summary)
            projects = self.get_optional(
                context, "projects", self.get_projects
            )
            names = projects["names"] if projects else {}

            def build():
                # compact [time, seconds, instance, project, host, slow
                # host] rows (up to MAX_POINTS of them); a new list: never
                # mutate cached data
                return [
                    [
                        round(at),
                        round(seconds, 1),
                        name or "",
                        names.get(project) or _short_id(project) or "",
                        host or "",
                        1 if slow else 0,
                    ]
                    for at, seconds, name, project, host, slow in summary[
                        "points"
                    ]
                ]

            context["summary"] = summary
            context["chart"] = {
                "range": range_key,
                "start": summary["start"],
                "end": summary["fetched_at"],
                "median": summary["median"],
                "points": self.joined(
                    f"points:{range_key}", (summary, projects), build
                ),
                "slow_hosts": [h["name"] for h in summary["slow_hosts"]],
            }
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class HostsView(_ProvisioningMixin, generic.TemplateView):
    """At most TOP_LIMIT hosts, the slowest compared to the cloud first."""

    template_name = "admin/provisioning/_hosts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            thresholds = config.get_provisioning_thresholds()
            summary = self.get_summary(range_key, thresholds)
            self.note_age(context, "metrics", summary)
            links = self.host_links(
                context, [h["name"] for h in summary["hosts"]]
            )
            rows = []
            for host in summary["hosts"]:
                row = dict(host, link=links.get(host["name"]))
                if host["ratio"] is not None:
                    # the bar is full at 3x the cloud median
                    row["ratio_width"] = min(100, round(host["ratio"] * 33))
                rows.append(row)
            context["rows"] = rows
            context["summary"] = summary
            context["thresholds"] = thresholds
            context["top_limit"] = provisioning.TOP_LIMIT
            context["status_window"] = provisioning.STATUS_WINDOW
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class FailedBuildsView(_ProvisioningMixin, generic.TemplateView):
    """The newest failed builds of the range (Compute API)."""

    template_name = "admin/provisioning/_failed.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            failed = self.get_failed(range_key)
            self.note_age(context, "compute", failed)
            names = self.project_names(context)
            context["rows"] = [
                dict(
                    build,
                    project=names.get(build["project_id"])
                    or build["project_id"],
                    created_at=clock(build["created"]),
                )
                for build in failed["builds"][: provisioning.FAILED_LIMIT]
            ]
            context["failed"] = failed
            context["failed_limit"] = provisioning.FAILED_LIMIT
            context["failed_query_limit"] = provisioning.FAILED_QUERY_LIMIT
            context["no_valid_host"] = any(
                (b["fault"] or "").startswith("No valid host")
                for b in failed["builds"]
            )
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class SlowestBootsView(_ProvisioningMixin, generic.TemplateView):
    """The TOP_LIMIT slowest boots of the range."""

    template_name = "admin/provisioning/_slowest.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        range_key = self.get_range_key()
        context["range_key"] = range_key
        try:
            thresholds = config.get_provisioning_thresholds()
            summary = self.get_summary(range_key, thresholds)
            self.note_age(context, "metrics", summary)
            boots = summary["slowest"]
            names = self.project_names(context)
            flavors = {}
            for boot in boots:
                found = self.get_optional(
                    context,
                    "flavors",
                    lambda boot=boot: self.get_flavor(boot["id"]),
                )
                if found is None:
                    # the Compute API is failing or busy: do not wait for
                    # each instance in turn
                    break
                flavors[boot["id"]] = found["flavor"]
            links = self.host_links(context, [b["host"] for b in boots])
            context["rows"] = [
                dict(
                    boot,
                    project=names.get(boot["project_id"])
                    or boot["project_id"],
                    flavor=flavors.get(boot["id"]),
                    link=links.get(boot["host"]),
                    booted_at=clock(boot["at"]),
                )
                for boot in boots
            ]
            context["top_limit"] = provisioning.TOP_LIMIT
        except Exception as exc:
            self.handle_error(context, exc)
        return context
