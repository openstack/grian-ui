#
# SPDX-License-Identifier: Apache-2.0

"""
Instance health views.

The index page is a light shell; every section is an HTMX partial that
loads and refreshes itself. The fleet *snapshot* joins two shared values
(see :mod:`grian_ui.instance_health`): the Nova inventory, refreshed every
INVENTORY_CACHE_TTL seconds because listing every server is the most
expensive call, and the metric symptoms, refreshed with the page. Each is
computed once and shared through the Horizon cache by the partials and by
the administrators of a region; each process joins them once per pair.
Each partial renders a bounded slice of it: cards, a fixed set of states
and symptoms, and at most LIST_LIMIT instances.
"""

import collections
import logging
import threading
import time
import uuid

from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import generic

from grian_ui import cache
from grian_ui import config
from grian_ui import instance_health
from grian_ui import inventory
from grian_ui import timeseries
from grian_ui.datasource.api import get_datasource


LOG = logging.getLogger(__name__)

#: Refresh interval of the partials, in seconds (used by the templates).
REFRESH_SECONDS = 30
#: Seconds the metric symptoms (and their join with the inventory) are
#: shared. Slightly shorter than the refresh interval so every refresh gets
#: new data, computed once.
SNAPSHOT_CACHE_TTL = REFRESH_SECONDS - 5
#: Seconds the Nova inventory (every server of every project) is shared:
#: lifecycle changes show up to this late.
INVENTORY_CACHE_TTL = 120
#: Seconds the project names are shared.
PROJECT_NAMES_CACHE_TTL = 300
#: Bump when the format of a cached value changes.
CACHE_VERSION = 2
CACHE_NAMESPACE = "instance_health"
#: Extra servers fetched per Nova status for a state list, to make up for
#: the servers that belong to another state bucket.
NOVA_EXTRA_ROWS = 10


#: Configuration errors already logged (each is logged once per process).
_LOGGED_CONFIG_ERRORS = set()

#: Joins of the two shared inputs, per process: a pure function of them,
#: rebuilt when one changes. Not in the shared cache, which keeps a last
#: copy of every key for minutes, and a pair is never asked for again.
_JOINS = collections.OrderedDict()
_JOINS_LOCK = threading.Lock()
#: Joins kept per process (the latest pairs of a few regions).
JOINS_KEPT = 8


def _check_settings():
    """
    Raise ConfigurationError for the settings the shared computations read.

    Only the request that runs a computation sees its cause: checking first
    gives every section (and the index) the same message.
    """
    config.get_instance_health_thresholds()
    config.get_instance_health_rate_window()
    if config.get_datasource_type() == config.DATASOURCE_PROMETHEUS:
        config.get_openstack_keystone_url()


def _silence():
    """The No telemetry window, or STALE_AFTER with an invalid setting."""
    try:
        return instance_health.silence_window(
            config.get_instance_health_rate_window()
        )
    except config.ConfigurationError:
        return instance_health.STALE_AFTER


def _instance_url(instance_id, tab=None):
    """Admin > Instances detail page, or None outside Horizon."""
    try:
        url = reverse("horizon:admin:instances:detail", args=[instance_id])
    except NoReverseMatch:
        return None
    return f"{url}?tab=instance_details__{tab}" if tab else url


class _InstanceHealthMixin:
    """Shared helpers: lazy clients, cached snapshot, filters, errors."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Unique per response: element ids inside partials must not repeat
        # across swaps, or htmx "settles" the new element with the old
        # element's attributes after a chart is drawn, which resets the
        # canvas size and clears the chart.
        context["uid"] = uuid.uuid4().hex[:12]
        return context

    def get_client(self):
        if getattr(self, "_client", None) is None:
            self._client = get_datasource(self.request)
        return self._client

    def get_inventory(self):
        if getattr(self, "_inventory", None) is None:
            self._inventory = inventory.get_inventory(self.request)
        return self._inventory

    def get_or_compute(self, name, ttl, compute):
        """
        cache.get_or_compute, after checking the settings.

        The cache marks a last good copy served after a failure, not one
        served while another request is still refreshing it (a slow or
        hanging backend). Values are stamped with ``stored_at`` when their
        computation ends: one older than its lifetime plus a refresh
        interval is marked ``is_old_copy`` here: probably still being
        refreshed, not a failure. (The age compares the clocks of the
        Horizon nodes sharing the cache: keep them in sync.)
        """
        _check_settings()

        def stamped():
            return dict(compute(), stored_at=time.time())

        value = cache.get_or_compute(
            cache.cache_key(
                self.request, CACHE_NAMESPACE, name, CACHE_VERSION
            ),
            ttl,
            stamped,
        )
        stored_at = value.get("stored_at")
        if (
            not value.get("is_stale_copy")
            and stored_at is not None
            and time.time() - stored_at > ttl + REFRESH_SECONDS
        ):
            value = dict(value, is_old_copy=True)
        return value

    def get_project_names(self):
        """Project id -> name; empty (IDs are shown) when Keystone fails."""

        def compute():
            return {"names": self.get_inventory().project_names()}

        try:
            return self.get_or_compute(
                "projects", PROJECT_NAMES_CACHE_TTL, compute
            )["names"]
        except cache.StillComputing:
            return {}
        except cache.DataUnavailable as exc:
            # grian_ui.cache already logged the failure with its traceback
            LOG.warning("Project names unavailable: %s", exc)
            return {}

    def get_inventory_value(self):
        """The Nova inventory, shared for INVENTORY_CACHE_TTL seconds."""

        def compute():
            return {
                "servers": self.get_inventory().list_servers(),
                "fetched_at": time.time(),
            }

        return self.get_or_compute("inventory", INVENTORY_CACHE_TTL, compute)

    def get_metrics_value(self):
        """Reporting instances and metric symptoms, shared per refresh."""

        def compute():
            client = self.get_client()
            window = config.get_instance_health_rate_window()
            symptoms = client.get_instance_symptoms(
                config.get_instance_health_thresholds(), window
            )
            flagged = set()
            for found in symptoms.values():
                flagged.update(found)
            # names and projects only serve when Nova is down, and only
            # for the instances with a symptom: keep the value small (the
            # shared cache refuses items over cache_max_item_bytes)
            reporting = {
                instance_id: labels if instance_id in flagged else {}
                for instance_id, labels in (
                    client.get_reporting_instances(
                        instance_health.silence_window(window)
                    ).items()
                )
            }
            return {
                "reporting": reporting,
                "symptoms": symptoms,
                "fetched_at": time.time(),
            }

        return self.get_or_compute("metrics", SNAPSHOT_CACHE_TTL, compute)

    def get_snapshot(self):
        """
        The shared snapshot: the inventory joined with the metrics.

        Each source is used as soon as it is available: when one fails
        (Nova or the metrics) or is still computed for the first time, the
        snapshot is built from the other and says so (``nova_ok`` /
        ``metrics_ok``, ``loading`` lists the sources still computed).
        StillComputing is raised when no source has data yet and one is
        still computed (the sections show "loading" and retry); the error
        when both failed.
        """
        sources, errors, loading = {}, [], []
        for name, get in (
            ("inventory", self.get_inventory_value),
            ("metrics", self.get_metrics_value),
        ):
            try:
                sources[name] = get()
            except cache.StillComputing as exc:
                # the other source can still be shown (and its own
                # computation started) while this one is computed
                sources[name] = None
                loading.append(name)
                errors.append(exc)
            except cache.DataUnavailable as exc:
                sources[name] = None
                errors.append(exc)
        if not any(sources.values()):
            if loading:
                raise cache.StillComputing(
                    "still being computed: " + ", ".join(loading)
                )
            raise errors[-1]
        inventory_value, metrics_value = (
            sources["inventory"],
            sources["metrics"],
        )
        nova_at = inventory_value["fetched_at"] if inventory_value else None
        metrics_at = metrics_value["fetched_at"] if metrics_value else None

        # one join per pair of inputs (and region), in this process
        key = (
            cache.cache_key(
                self.request, CACHE_NAMESPACE, "snapshot", CACHE_VERSION
            ),
            nova_at,
            metrics_at,
        )
        with _JOINS_LOCK:
            snapshot = _JOINS.get(key)
        if snapshot is None:
            snapshot = instance_health.build_snapshot(
                inventory_value["servers"] if inventory_value else None,
                metrics_value["reporting"] if metrics_value else None,
                metrics_value["symptoms"] if metrics_value else None,
                config.get_instance_health_thresholds(),
                time.time(),
                project_names=self.get_project_names(),
                silence=instance_health.silence_window(
                    config.get_instance_health_rate_window()
                ),
                reporting_at=metrics_at,
            )
            snapshot = dict(
                snapshot, fetched_at=metrics_at, nova_fetched_at=nova_at
            )
            with _JOINS_LOCK:
                _JOINS[key] = snapshot
                while len(_JOINS) > JOINS_KEPT:
                    _JOINS.popitem(last=False)
        # as old as its inputs: a failed refresh (the cache's last good
        # copy), or one that takes long (an old copy, see get_or_compute)
        values = [value for value in sources.values() if value]
        return dict(
            snapshot,
            is_stale_copy=any(v.get("is_stale_copy", False) for v in values),
            is_old_copy=any(v.get("is_old_copy", False) for v in values),
            loading=loading,
        )

    def get_range_key(self):
        key, _window, _step = timeseries.get_range(
            self.request.GET.get("range")
        )
        return key

    def get_filters(self):
        """Validated filters from the request (invalid values are ignored)."""
        get = self.request.GET
        symptom = get.get("symptom")
        state = get.get("state")
        try:
            severity = int(get.get("severity", ""))
        except ValueError:
            severity = instance_health.HEALTHY
        if severity not in instance_health.SEVERITY_KEYS:
            severity = instance_health.HEALTHY
        return {
            "symptom": symptom if symptom in instance_health.SYMPTOMS else "",
            "state": state if state in instance_health.LISTABLE_STATES else "",
            "project": (get.get("project") or "").strip()[:64],
            "severity": severity,
            "q": (get.get("q") or "").strip()[:100],
        }

    def handle_error(self, context, exc):
        if isinstance(exc, cache.ComputationFailed) and isinstance(
            exc.__cause__, config.ConfigurationError
        ):
            exc = exc.__cause__
        # the screen reader announces the error, not an earlier outcome,
        # and an error is not "loading" (no quick retry)
        for key in ("announce", "loading"):
            context.pop(key, None)
        if isinstance(exc, config.ConfigurationError):
            if str(exc) not in _LOGGED_CONFIG_ERRORS:
                _LOGGED_CONFIG_ERRORS.add(str(exc))
                LOG.error("Invalid instance health setting: %s", exc)
            # the index shows the message once, at the top of the page
            context["error_message"] = _(
                "Not available: invalid configuration (see the top of the "
                "page)."
            )
            return context
        if isinstance(exc, cache.StillComputing):
            # another request is still computing the shared data: show a
            # loading state, the template retries shortly
            LOG.info("Instance health data not ready yet: %s", exc)
            context["loading"] = True
            context["error_message"] = _("Still loading instance health data…")
            return context
        if isinstance(exc, cache.DataUnavailable):
            # grian_ui.cache already logged the failure with its traceback
            LOG.warning("Instance health data unavailable: %s", exc)
        else:
            # details (backend messages) go to the log, not to the page
            LOG.exception("Error loading instance health data: %s", exc)
        context["error_message"] = _(
            "Error loading instance health data. See the Horizon logs for "
            "details."
        )
        return context


def _with_live_state(row, live_state):
    """
    The snapshot's row of an instance listed live in ``live_state``.

    A state list comes from the Compute API, the snapshot's inventory can be
    minutes old: when the states differ, its lifecycle issues and fault are
    out of date (and its metric issues only count while the instance runs).
    """
    if (
        live_state not in instance_health.STATE_NOVA_STATUSES
        or row["state"] == live_state
    ):
        return row
    running = live_state in instance_health.RUNNING_STATES
    issues = [
        issue
        for issue in row["issues"]
        if running
        and (
            issue["symptom"] in instance_health.METRIC_SYMPTOMS
            or (
                issue["symptom"] == instance_health.TELEMETRY
                and live_state == instance_health.STATE_ACTIVE
            )
        )
    ]
    return dict(row, state=live_state, fault=None, issues=issues)


def _number(value):
    return instance_health.format_number(value)


def _trend_label(symptom, values, unit):
    """Text alternative of a sparkline: the symptom and its latest value."""
    label = instance_health.SYMPTOM_LABELS[symptom]
    if not values:
        return str(label)
    return _("%(symptom)s, latest %(value)s %(unit)s") % {
        "symptom": label,
        "value": _number(values[-1]),
        "unit": unit,
    }


def _trend_text(values, unit, warning, critical):
    """Text alternative of a detail chart."""
    known = [v for v in values if v is not None]
    thresholds = _(
        "warning above %(w)s %(unit)s, critical above %(c)s %(unit)s"
    ) % {
        "w": _number(warning),
        "c": _number(critical),
        "unit": unit,
    }
    if not known:
        return _("No data in the range; %(thresholds)s.") % {
            "thresholds": thresholds
        }
    return _(
        "Latest %(latest)s %(unit)s, lowest %(low)s, highest %(high)s; "
        "%(thresholds)s."
    ) % {
        "latest": _number(known[-1]),
        "low": _number(min(known)),
        "high": _number(max(known)),
        "unit": unit,
        "thresholds": thresholds,
    }


def _list_announcement(shown, total, exact=True):
    """What a screen reader hears after the user changes a filter."""
    if not total:
        return _("No instances match.")
    if not exact:
        # like the footer: more instances may match
        return _("First %(shown)s matching instances shown.") % {
            "shown": shown
        }
    return _("%(shown)s of %(total)s matching instances shown.") % {
        "shown": shown,
        "total": total,
    }


def _symptom_tiles(snapshot, active):
    sources = {key: {"metrics"} for key in instance_health.METRIC_SYMPTOMS}
    sources[instance_health.STATE] = {"inventory"}
    sources[instance_health.TELEMETRY] = {"inventory", "metrics"}
    tiles = []
    for key in instance_health.SYMPTOMS:
        needs_nova = key in (instance_health.STATE, instance_health.TELEMETRY)
        available = (
            snapshot["nova_ok"] if needs_nova else snapshot["metrics_ok"]
        )
        if key == instance_health.TELEMETRY:
            available = snapshot["nova_ok"] and snapshot["metrics_ok"]
        tiles.append(
            dict(
                snapshot["symptoms"][key],
                key=key,
                label=instance_health.SYMPTOM_LABELS[key],
                help=instance_health.SYMPTOM_HELP[key],
                icon=instance_health.SYMPTOM_ICONS[key],
                available=available,
                # not available yet: a source it needs is still computed
                loading=not available
                and bool(sources[key] & set(snapshot.get("loading", []))),
                active=key == active,
            )
        )
    return tiles


def _state_segments(snapshot, active):
    total = sum(s["count"] for s in snapshot["states"]) or 1
    return [
        dict(
            state,
            label=instance_health.STATE_LABELS[state["key"]],
            percent=round(100.0 * state["count"] / total, 1),
            listable=state["key"] in instance_health.LISTABLE_STATES,
            active=state["key"] == active,
        )
        for state in snapshot["states"]
    ]


class InstanceHealthIndexView(_InstanceHealthMixin, generic.TemplateView):
    template_name = "admin/instance_health/index.html"
    page_title = _("Instance Health")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        context["ranges"] = list(timeseries.RANGES)
        context["default_range"] = timeseries.DEFAULT_RANGE
        context["refresh_seconds"] = REFRESH_SECONDS
        context["list_limit"] = instance_health.LIST_LIMIT
        context["stale_after"] = _silence()
        context["inventory_minutes"] = INVENTORY_CACHE_TTL // 60
        context["store_limit"] = instance_health.ATTENTION_STORE_LIMIT
        errors = []
        for key, get in (
            ("thresholds", config.get_instance_health_thresholds),
            ("rate_window", config.get_instance_health_rate_window),
        ):
            try:
                context[key] = get()
            except config.ConfigurationError as exc:
                errors.append(str(exc))
        try:
            # read when the datasource client is built: shown here, once
            if config.get_datasource_type() == config.DATASOURCE_PROMETHEUS:
                config.get_openstack_keystone_url()
        except config.ConfigurationError as exc:
            errors.append(str(exc))
        context["error_messages"] = errors
        return context


class SummaryView(_InstanceHealthMixin, generic.TemplateView):
    """Summary cards, the state bar and the symptom tiles."""

    template_name = "admin/instance_health/_summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.get_filters()
        try:
            snapshot = self.get_snapshot()
            context["snapshot"] = snapshot
            context["filters"] = filters
            context["stale_after"] = _silence()
            context["tiles"] = _symptom_tiles(snapshot, filters["symptom"])
            context["segments"] = _state_segments(snapshot, filters["state"])
            # same rule as the list: instances flagged "No telemetry";
            # the others without samples just started running
            silent = snapshot["symptoms"][instance_health.TELEMETRY]["total"]
            context["not_reporting"] = (
                silent
                if snapshot["metrics_ok"] and snapshot["nova_ok"]
                else None
            )
            context["starting"] = (
                max(snapshot["running"] - snapshot["reporting"] - silent, 0)
                if context["not_reporting"] is not None
                else 0
            )
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            context["data_old"] = snapshot.get("is_old_copy", False)
            context["partial_loading"] = snapshot.get("loading", [])
            context["inventory_minutes"] = INVENTORY_CACHE_TTL // 60
            # epoch seconds, shown in the browser's time zone
            for key, field in (
                ("metrics_ts", "fetched_at"),
                ("nova_ts", "nova_fetched_at"),
            ):
                at = snapshot.get(field)
                context[key] = str(int(at)) if at else None
        except Exception as exc:
            self.handle_error(context, exc)
        return context


class AttentionView(_InstanceHealthMixin, generic.TemplateView):
    """At most LIST_LIMIT instances, filtered, with their worst trend."""

    template_name = "admin/instance_health/_attention.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = self.get_filters()
        range_key = self.get_range_key()
        context.update(
            filters=filters,
            range_key=range_key,
            list_limit=instance_health.LIST_LIMIT,
        )
        try:
            snapshot = self.get_snapshot()
            context["snapshot"] = snapshot
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            context["data_old"] = snapshot.get("is_old_copy", False)
            context["partial_loading"] = snapshot.get("loading", [])
            projects = instance_health.project_choices(snapshot)
            if filters["project"] and filters["project"] not in dict(projects):
                # keep the selected project listed when it has no issues
                names = self.get_project_names()
                projects.append(
                    (
                        filters["project"],
                        names.get(filters["project"]) or filters["project"],
                    )
                )
            context["projects"] = projects
            context["project_options"] = [list(p) for p in projects]
            state = filters["state"]
            context["state_label"] = instance_health.STATE_LABELS.get(state)
            context["symptom_label"] = instance_health.SYMPTOM_LABELS.get(
                filters["symptom"]
            )
            # a healthy state is listed from Nova, unless a severity is
            # selected: the snapshot holds every flagged instance
            from_nova = bool(
                state
                and state not in instance_health.PROBLEM_STATES
                and not filters["severity"]
            )
            context["from_nova"] = from_nova
            if from_nova:
                rows, total, exact = self._rows_from_nova(snapshot, filters)
                context["nova_list_failed"] = rows is None
                rows = rows or []
            else:
                rows, total = instance_health.filter_rows(
                    snapshot,
                    symptom=filters["symptom"] or None,
                    state=state or None,
                    project=filters["project"] or None,
                    min_severity=filters["severity"],
                    query=filters["q"],
                )
                exact = True
            context["rows"] = self._decorate(rows, snapshot["metrics_ok"])
            context["total"] = total
            context["total_exact"] = exact
            context["trends"] = self._trends(context["rows"], range_key)
            for row in context["rows"]:
                symptom = row["trend_symptom"]
                if symptom:
                    unit = instance_health.SYMPTOM_THRESHOLDS[symptom][1]
                    known = [
                        v
                        for v in context["trends"].get(row["id"], [])
                        if v is not None
                    ]
                    row["trend_label"] = _trend_label(symptom, known, unit)
            context["announce"] = _list_announcement(
                len(context["rows"]), context["total"], context["total_exact"]
            )
        except Exception as exc:
            self.handle_error(context, exc)
        return context

    def _rows_from_nova(self, snapshot, filters):
        """
        Instances in a healthy state, listed by Nova (bounded queries).

        Nova's status filter is broader than the state buckets (ACTIVE also
        returns instances that are not running or in a task), so the
        servers are classified again and a few extra are fetched.

        Returns:
            tuple: (rows, total, whether total is exact); rows is None when
            the Compute API could not list them (the list says so, and its
            filters stay usable)
        """
        limit = instance_health.LIST_LIMIT
        fetched = limit + NOVA_EXTRA_ROWS
        state = filters["state"]
        servers = []
        complete = True
        query = filters["q"]
        # a full instance ID is looked up as such, anything else by name
        by_id = inventory.is_instance_id(query.lower())
        for status in instance_health.STATE_NOVA_STATUSES[state]:
            try:
                found = self.get_inventory().list_servers_by_status(
                    status,
                    project_id=filters["project"] or None,
                    name=None if by_id else query or None,
                    instance_id=query.lower() if by_id else None,
                    limit=fetched,
                )
            except Exception:
                LOG.exception("Error listing %s instances from Nova", status)
                return None, 0, False
            # fewer than asked for: every server with this status is here
            complete = complete and len(found) < fetched
            servers += found
        servers = [
            s for s in servers if instance_health.classify_state(s) == state
        ]
        matching = len(servers)
        servers.sort(key=lambda s: ((s["name"] or "").lower(), s["id"]))
        rows = instance_health.rows_for_servers(
            servers,
            snapshot,
            config.get_instance_health_thresholds(),
            self.get_project_names(),
            time.time(),
        )
        rows = rows[:limit]
        if complete:
            # the live list holds every match: its count is exact (the
            # inventory can be up to INVENTORY_CACHE_TTL old)
            return rows, matching, True
        narrowed = bool(filters["project"] or filters["q"])
        if narrowed:
            return rows, len(rows), False
        state_total = next(
            (s["count"] for s in snapshot["states"] if s["key"] == state),
            None,
        )
        if state_total is None:
            # no inventory (still loading, or failed): the total is unknown
            return rows, len(rows), False
        # more live matches than the inventory counted: it is out of date,
        # and the real total is at least that many
        return rows, max(state_total, matching), state_total >= matching

    def _decorate(self, rows, metrics_ok):
        """Copy rows (they come from the cache) and add display fields."""
        rows = [
            dict(row, issues=instance_health.with_texts(row["issues"]))
            for row in rows
        ]
        ids = [r["id"] for r in rows]
        last_seen = {}
        if ids and metrics_ok:
            try:
                last_seen = self.get_client().get_instance_last_seen(ids)
            except Exception:
                LOG.exception("Error loading instance last samples")
        now = time.time()
        for row in rows:
            row["severity_key"] = instance_health.SEVERITY_KEYS[
                row["severity"]
            ]
            row["url"] = _instance_url(row["id"])
            seen = last_seen.get(row["id"])
            row["last_seen_age"] = (
                timeseries.format_age(now - seen) if seen else None
            )
            row["state_label"] = (
                instance_health.STATE_LABELS.get(row["state"])
                if row["state"]
                else None
            )
            worst = next(
                (
                    i
                    for i in row["issues"]
                    if i["symptom"] in instance_health.METRIC_SYMPTOMS
                ),
                None,
            )
            row["trend_symptom"] = worst["symptom"] if worst else None
            row["silent"] = any(
                i["symptom"] == instance_health.TELEMETRY
                for i in row["issues"]
            )
        return rows

    def _trends(self, rows, range_key):
        """Worst metric symptom of each row over the range (sparklines)."""
        by_symptom = {}
        for row in rows:
            if row["trend_symptom"]:
                by_symptom.setdefault(row["trend_symptom"], []).append(
                    row["id"]
                )
        trends = {}
        if not by_symptom:
            return trends
        window = config.get_instance_health_rate_window()
        now = time.time()
        for symptom, ids in by_symptom.items():
            try:
                found = self.get_client().get_symptom_trends(
                    symptom, ids, range_key, window, at=now
                )
            except Exception:
                LOG.exception("Error loading %s trends", symptom)
                continue
            for instance_id, points in found.items():
                trends[instance_id] = timeseries.fill_gaps(
                    points, range_key, now
                )[1]
        return trends


class InstanceDetailView(_InstanceHealthMixin, generic.TemplateView):
    """Issues and the four symptom trends of one instance."""

    template_name = "admin/instance_health/_instance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        instance_id = (self.request.GET.get("id") or "").strip().lower()
        range_key = self.get_range_key()
        context.update(instance_id=instance_id, range_key=range_key)
        if not inventory.is_instance_id(instance_id):
            context["invalid"] = True
            return context
        try:
            thresholds = config.get_instance_health_thresholds()
            snapshot = self.get_snapshot()
            # the state list the row was opened from, live from Nova
            live_state = self.request.GET.get("state")
            if live_state not in instance_health.STATE_NOVA_STATUSES:
                live_state = None
            row = instance_health.find_row(snapshot, instance_id)
            if row is not None:
                row = _with_live_state(row, live_state)
                row = dict(
                    row, issues=instance_health.with_texts(row["issues"])
                )
            context["row"] = row
            context["urls"] = {
                "detail": _instance_url(instance_id),
                "log": _instance_url(instance_id, "log"),
            }
            context["thresholds"] = thresholds
            context["data_stale"] = snapshot.get("is_stale_copy", False)
            context["data_old"] = snapshot.get("is_old_copy", False)
            state = row["state"] if row is not None else live_state
            if state is not None and state not in (
                instance_health.RUNNING_STATES
            ):
                # not running (stopped, error, active but not running):
                # no metrics to draw
                context["not_running"] = True
                return context
            if not snapshot["metrics_ok"]:
                return context
            # evaluated at the time the chart grid is built from
            now = time.time()
            try:
                trends = self.get_client().get_instance_trends(
                    instance_id,
                    range_key,
                    config.get_instance_health_rate_window(),
                    at=now,
                )
            except Exception:
                # the issues and the fault above stay useful without charts
                LOG.exception("Error loading the trends of %s", instance_id)
                context["trends_unavailable"] = True
                return context
            context["has_trends"] = any(trends.values())
            charts = []
            for symptom in instance_health.METRIC_SYMPTOMS:
                prefix, unit = instance_health.SYMPTOM_THRESHOLDS[symptom]
                series = timeseries.gap_filled_series(
                    trends.get(symptom, []), range_key, now
                )
                warning = thresholds[f"{prefix}_warning"]
                critical = thresholds[f"{prefix}_critical"]
                charts.append(
                    {
                        "key": symptom,
                        "label": str(instance_health.SYMPTOM_LABELS[symptom]),
                        "unit": unit,
                        "warning": warning,
                        "critical": critical,
                        "series": series,
                        # the chart's text alternative
                        "summary": _trend_text(
                            [v for _t, v in series], unit, warning, critical
                        ),
                    }
                )
            context["chart"] = {"range": range_key, "charts": charts}
        except Exception as exc:
            self.handle_error(context, exc)
        return context
