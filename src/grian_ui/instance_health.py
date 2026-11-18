#
# SPDX-License-Identifier: Apache-2.0

"""
Instance health evaluation.

Pure functions shared by the datasources and the Instance Health views.
They join the Compute (Nova) inventory with the symptoms found in the
Ceilometer metrics, and turn them into a *snapshot*: state counts, symptom
counts and a capped, ranked list of the instances needing attention. The
snapshot is cached and every partial renders a bounded slice of it, so the
amount of data sent to the browser does not grow with the cloud.

Inputs:

``servers`` (from :mod:`grian_ui.inventory`, ``None`` if Nova failed)::

    [
        {
            "id",
            "name",
            "project_id",
            "host",
            "status",
            "power_state",
            "task_state",
            "updated",
            "launched_at",
            "fault",
        },
        ...,
    ]

``reporting`` (``DatasourceClient.get_reporting_instances``, ``None`` if
the metrics failed): instances with a recent ``cpu`` sample::

    {instance_id: {"name": str, "project_id": str}}

``symptoms`` (``DatasourceClient.get_instance_symptoms``): only instances
above a warning threshold::

    {
        "cpu": {id: percent},
        "memory": {id: MiB / s},
        "disk": {id: {"value": ms, "device": "vda"}},
        "network": {id: pkt / s},
    }
"""

import math

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from grian_ui import timeseries


#: An instance without a cpu sample in this window is not reporting (the
#: shortest window: see silence_window).
STALE_AFTER = "10m"


def silence_window(rate_window):
    """
    How long without a cpu sample before a running instance is silent.

    STALE_AFTER, or the rate window when longer: the rate window is at
    least twice the polling interval (see the admin guide), so slower
    polling does not make healthy instances look silent. It is also the
    grace period after an instance starts (its first poll can take up to
    a polling interval).
    """
    if timeseries.duration_seconds(rate_window) > timeseries.duration_seconds(
        STALE_AFTER
    ):
        return rate_window
    return STALE_AFTER


#: How far back to look for the last sample of a silent instance.
LAST_SEEN_LOOKBACK = "24h"
#: Maximum attention rows kept in the cached snapshot. Counts stay exact.
ATTENTION_STORE_LIMIT = 500
#: Maximum rows rendered in the instance list.
LIST_LIMIT = 25

CRITICAL = 3
WARNING = 2
HEALTHY = 0
SEVERITY_KEYS = {CRITICAL: "critical", WARNING: "warning", HEALTHY: "ok"}

# Symptoms, in display order.
STATE = "state"
CPU = "cpu"
MEMORY = "memory"
DISK = "disk"
NETWORK = "network"
TELEMETRY = "telemetry"
SYMPTOMS = (STATE, CPU, MEMORY, DISK, NETWORK, TELEMETRY)
#: Symptoms measured from metrics (the others need Nova).
METRIC_SYMPTOMS = (CPU, MEMORY, DISK, NETWORK)
SYMPTOM_LABELS = {
    STATE: gettext_lazy("State problems"),
    CPU: gettext_lazy("CPU contention"),
    MEMORY: gettext_lazy("Memory pressure"),
    DISK: gettext_lazy("Slow disk"),
    NETWORK: gettext_lazy("Network drops/errors"),
    TELEMETRY: gettext_lazy("No telemetry"),
}
SYMPTOM_HELP = {
    STATE: gettext_lazy("error, crashed, stuck"),
    CPU: gettext_lazy("waiting for a host CPU"),
    MEMORY: gettext_lazy("swapping"),
    DISK: gettext_lazy("high I/O latency"),
    NETWORK: gettext_lazy("packets lost"),
    TELEMETRY: gettext_lazy("running, not reporting"),
}
SYMPTOM_ICONS = {
    STATE: "exclamation-circle",
    CPU: "tachometer",
    MEMORY: "exchange",
    DISK: "database",
    NETWORK: "sitemap",
    TELEMETRY: "eye-slash",
}
#: Threshold keys of each metric symptom (``<prefix>_warning`` and
#: ``<prefix>_critical``) and the unit of its value.
SYMPTOM_THRESHOLDS = {
    CPU: ("cpu_wait", "%"),
    MEMORY: ("swap", "MiB/s"),
    DISK: ("disk_latency", "ms"),
    NETWORK: ("packet_loss", "pkt/s"),
}

# Nova power states (nova.compute.power_state).
POWER_RUNNING = 1
POWER_CRASHED = 6
POWER_STATE_NAMES = {
    0: gettext_lazy("no state"),
    1: gettext_lazy("running"),
    3: gettext_lazy("paused"),
    4: gettext_lazy("shut down"),
    6: gettext_lazy("crashed"),
    7: gettext_lazy("suspended"),
}

# Instance states shown in the state bar, in display order.
STATE_ACTIVE = "active"
STATE_ACTIVE_NOT_RUNNING = "active_not_running"
STATE_IN_TASK = "in_task"
STATE_SHUTOFF = "shutoff"
STATE_PAUSED = "paused"
STATE_SUSPENDED = "suspended"
STATE_SHELVED = "shelved"
STATE_ERROR = "error"
STATE_OTHER = "other"
STATES = (
    STATE_ACTIVE,
    STATE_SHUTOFF,
    STATE_PAUSED,
    STATE_SUSPENDED,
    STATE_SHELVED,
    STATE_IN_TASK,
    STATE_ACTIVE_NOT_RUNNING,
    STATE_ERROR,
    STATE_OTHER,
)
STATE_LABELS = {
    STATE_ACTIVE: gettext_lazy("Active (running)"),
    STATE_SHUTOFF: gettext_lazy("Shut off"),
    STATE_PAUSED: gettext_lazy("Paused"),
    STATE_SUSPENDED: gettext_lazy("Suspended"),
    STATE_SHELVED: gettext_lazy("Shelved"),
    STATE_IN_TASK: gettext_lazy("Building or in a task"),
    STATE_ACTIVE_NOT_RUNNING: gettext_lazy("Active, not running"),
    STATE_ERROR: gettext_lazy("Error"),
    STATE_OTHER: gettext_lazy("Other"),
}
#: States in which an instance runs, so its metric symptoms count (an
#: instance in a task, e.g. migrating, usually still runs).
RUNNING_STATES = (STATE_ACTIVE, STATE_IN_TASK)
#: States whose instances are always in the attention list.
PROBLEM_STATES = (STATE_ERROR, STATE_ACTIVE_NOT_RUNNING)
#: Nova statuses to list the instances of the other states (one query per
#: status). States missing here cannot be listed.
STATE_NOVA_STATUSES = {
    STATE_ACTIVE: ("ACTIVE",),
    STATE_SHUTOFF: ("SHUTOFF",),
    STATE_PAUSED: ("PAUSED",),
    STATE_SUSPENDED: ("SUSPENDED",),
    STATE_SHELVED: ("SHELVED", "SHELVED_OFFLOADED"),
}
LISTABLE_STATES = PROBLEM_STATES + tuple(STATE_NOVA_STATUSES)

_IN_TASK_STATUSES = {
    "BUILD",
    "REBUILD",
    "RESIZE",
    "VERIFY_RESIZE",
    "REVERT_RESIZE",
    "MIGRATING",
    "REBOOT",
    "HARD_REBOOT",
    "PASSWORD",
}


def classify_state(server):
    """State bar bucket of a server."""
    status = (server.get("status") or "").upper()
    if status == "ERROR":
        return STATE_ERROR
    if status in _IN_TASK_STATUSES or server.get("task_state"):
        return STATE_IN_TASK
    if status == "ACTIVE":
        if server.get("power_state") == POWER_RUNNING:
            return STATE_ACTIVE
        return STATE_ACTIVE_NOT_RUNNING
    if status == "SHUTOFF":
        return STATE_SHUTOFF
    if status == "PAUSED":
        return STATE_PAUSED
    if status == "SUSPENDED":
        return STATE_SUSPENDED
    if status in ("SHELVED", "SHELVED_OFFLOADED"):
        return STATE_SHELVED
    return STATE_OTHER


def format_number(value):
    """Format a number with at most one decimal and no trailing '.0'."""
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


_fmt = format_number


# Issue codes. Issues keep a code and parameters, not text: the snapshot
# is shared by every administrator, so texts are translated when rendered
# (issue_text), in each user's language.
ISSUE_ERROR = "error"
ISSUE_CRASHED = "crashed"
ISSUE_NOT_RUNNING = "not_running"
ISSUE_STUCK = "stuck"
ISSUE_NO_SAMPLES = "no_samples"


def _issue(symptom, severity, code, measure=None, **params):
    """An issue: ``measure`` ranks it, ``params`` format its text."""
    return {
        "symptom": symptom,
        "severity": severity,
        "code": code,
        "value": measure,
        "params": params,
    }


def issue_text(issue):
    """Translated text of an issue."""
    code, params = issue["code"], dict(issue["params"])
    if "value" in params:
        params["value"] = _fmt(params["value"])
    if code == ISSUE_ERROR:
        return _("Error state")
    if code == ISSUE_CRASHED:
        return _("Crashed")
    if code == ISSUE_NOT_RUNNING:
        power = params.get("power")
        name = POWER_STATE_NAMES.get(power)
        return _("Active, but the power state is %(power)s") % {
            "power": name if name else _("unknown (%s)") % power
        }
    if code == ISSUE_STUCK:
        return _("In task %(task)s for %(minutes)s min") % params
    if code == ISSUE_NO_SAMPLES:
        return _("No samples in the last %(window)s") % params
    if code == CPU:
        return _("CPU wait %(value)s%%") % params
    if code == MEMORY:
        return _("Swapping %(value)s MiB/s") % params
    if code == DISK:
        if params.get("device"):
            return _("%(device)s latency %(value)s ms") % params
        return _("Disk latency %(value)s ms") % params
    if code == NETWORK:
        return _("%(value)s dropped or failed packets/s") % params
    return code


def with_texts(issues):
    """Copies of ``issues`` with their translated ``text``."""
    return [dict(issue, text=issue_text(issue)) for issue in issues]


def state_issues(server, thresholds, now):
    """Lifecycle problems of a server (needs Nova data)."""
    issues = []
    status = (server.get("status") or "").upper()
    power = server.get("power_state")
    if status == "ERROR":
        issues.append(_issue(STATE, CRITICAL, ISSUE_ERROR))
    elif power == POWER_CRASHED:
        issues.append(_issue(STATE, CRITICAL, ISSUE_CRASHED))
    elif status == "ACTIVE" and not server.get("task_state"):
        if power != POWER_RUNNING:
            issues.append(
                _issue(STATE, WARNING, ISSUE_NOT_RUNNING, power=power)
            )

    task = server.get("task_state")
    updated = server.get("updated")
    if task and updated is not None:
        minutes = (now - updated) / 60
        if minutes > thresholds["stuck_task_minutes"]:
            issues.append(
                _issue(
                    STATE,
                    WARNING,
                    ISSUE_STUCK,
                    minutes,
                    task=task,
                    minutes=int(minutes),
                )
            )
    return issues


def metric_issues(instance_id, symptoms, thresholds):
    """Issues from the metric symptoms of one instance."""
    issues = []
    for symptom, (prefix, _unit) in SYMPTOM_THRESHOLDS.items():
        entry = (symptoms.get(symptom) or {}).get(instance_id)
        if entry is None:
            continue
        device = None
        if isinstance(entry, dict):
            value, device = entry.get("value"), entry.get("device")
        else:
            value = entry
        if value is None or not math.isfinite(value):
            continue
        if value > thresholds[f"{prefix}_critical"]:
            severity = CRITICAL
        elif value > thresholds[f"{prefix}_warning"]:
            severity = WARNING
        else:
            continue
        params = {"value": value}
        if symptom == DISK:
            params["device"] = device
        issues.append(_issue(symptom, severity, symptom, value, **params))
    return issues


def running_since(server):
    """
    When the server last started running (epoch), or None.

    ``launched_at`` does not change on start, resume or unpause, but
    ``updated`` does.
    """
    times = [
        t for t in (server.get("launched_at"), server.get("updated")) if t
    ]
    return max(times) if times else None


def telemetry_issue(server, reporting, now, window=STALE_AFTER):
    """Issue when a running server has no samples in ``window``."""
    if reporting is None or classify_state(server) != STATE_ACTIVE:
        return None
    if server["id"] in reporting:
        return None
    since = running_since(server)
    if since is not None and now - since < timeseries.duration_seconds(window):
        return None
    return _issue(TELEMETRY, WARNING, ISSUE_NO_SAMPLES, window=window)


def _severity(issues):
    return max((i["severity"] for i in issues), default=HEALTHY)


def _ratio(issue, thresholds):
    """How far an issue is past its critical threshold (for ranking)."""
    if issue["symptom"] not in SYMPTOM_THRESHOLDS or issue["value"] is None:
        return 0
    prefix = SYMPTOM_THRESHOLDS[issue["symptom"]][0]
    critical = thresholds[f"{prefix}_critical"]
    return issue["value"] / critical if critical > 0 else issue["value"]


def _sort_key(row):
    # worst first: severity, definitive state problems, number of issues,
    # how far past the threshold, then name for a stable order
    return (
        -row["severity"],
        not any(i["symptom"] == STATE for i in row["issues"]),
        -len(row["issues"]),
        -row["rank"],
        (row["name"] or "").lower(),
        row["id"],
    )


def _row(instance_id, name, project_id, server, issues, thresholds):
    issues = sorted(
        issues, key=lambda i: (-i["severity"], -_ratio(i, thresholds))
    )
    return {
        "id": instance_id,
        "name": name,
        "project_id": project_id,
        "host": server.get("host") if server else None,
        "status": server.get("status") if server else None,
        "state": classify_state(server) if server else None,
        "task_state": server.get("task_state") if server else None,
        "power_state": server.get("power_state") if server else None,
        "fault": server.get("fault") if server else None,
        "issues": issues,
        "severity": _severity(issues),
        "rank": max((_ratio(i, thresholds) for i in issues), default=0),
    }


def build_snapshot(
    servers,
    reporting,
    symptoms,
    thresholds,
    now,
    project_names=None,
    limit=ATTENTION_STORE_LIMIT,
    silence=STALE_AFTER,
    reporting_at=None,
):
    """
    Join the inventory and the metrics into a bounded snapshot.

    Args:
        servers: Nova servers, or None when the Compute API failed
        reporting: reporting instances, or None when the metrics failed
        symptoms: metric symptoms, or None when the metrics failed
        project_names: project id -> name
        silence: the window of silence_window (No telemetry)
        reporting_at: when ``reporting`` was fetched (an old copy can be
            served): No telemetry's grace period is measured against it

    Returns:
        dict: counts, per-state and per-symptom totals and at most
        ``limit`` attention rows, worst first
    """
    project_names = project_names or {}
    symptoms = symptoms if symptoms is not None else {}
    rows = []
    states = dict.fromkeys(STATES, 0)

    if servers is not None:
        running = reporting_running = 0
        for server in servers:
            state = classify_state(server)
            states[state] += 1
            if state == STATE_ACTIVE:
                running += 1
                if reporting is not None and server["id"] in reporting:
                    reporting_running += 1
            issues = state_issues(server, thresholds, now)
            if state in RUNNING_STATES:
                # rates stay non-zero for a while after an instance stops:
                # stopped instances are not flagged for them
                issues += metric_issues(server["id"], symptoms, thresholds)
            silent = telemetry_issue(
                server,
                reporting,
                reporting_at if reporting_at is not None else now,
                silence,
            )
            if silent:
                issues.append(silent)
            if issues:
                rows.append(
                    _row(
                        server["id"],
                        server.get("name"),
                        server.get("project_id"),
                        server,
                        issues,
                        thresholds,
                    )
                )
        total = len(servers)
        if reporting is None:
            reporting_running = None
    else:
        # Compute API unavailable: metric symptoms only, named from the
        # metric labels
        running = total = None
        reporting_running = len(reporting) if reporting is not None else None
        ids = set()
        for symptom in METRIC_SYMPTOMS:
            ids.update((symptoms.get(symptom) or {}).keys())
        for instance_id in ids:
            issues = metric_issues(instance_id, symptoms, thresholds)
            if issues:
                labels = (reporting or {}).get(instance_id, {})
                rows.append(
                    _row(
                        instance_id,
                        labels.get("name"),
                        labels.get("project_id"),
                        None,
                        issues,
                        thresholds,
                    )
                )

    rows.sort(key=_sort_key)
    symptom_counts = {
        s: {"critical": 0, "warning": 0, "total": 0} for s in SYMPTOMS
    }
    hosts = set()
    critical = warning = 0
    for row in rows:
        row["project_name"] = project_names.get(row["project_id"])
        if row["severity"] == CRITICAL:
            critical += 1
        elif row["severity"] == WARNING:
            warning += 1
        if row["host"]:
            hosts.add(row["host"])
        worst = {}
        for issue in row["issues"]:
            worst[issue["symptom"]] = max(
                worst.get(issue["symptom"], HEALTHY), issue["severity"]
            )
        for symptom, severity in worst.items():
            counts = symptom_counts[symptom]
            counts["total"] += 1
            counts["critical" if severity == CRITICAL else "warning"] += 1

    metrics_ok = reporting is not None
    return {
        "nova_ok": servers is not None,
        "metrics_ok": metrics_ok,
        "total": total,
        "running": running,
        "reporting": reporting_running,
        "states": [{"key": key, "count": states[key]} for key in STATES]
        if servers is not None
        else [],
        "symptoms": symptom_counts,
        "critical": critical,
        "warning": warning,
        "hosts_affected": len(hosts),
        "attention_total": len(rows),
        "truncated": len(rows) > limit,
        "rows": rows[:limit],
    }


def filter_rows(
    snapshot,
    symptom=None,
    state=None,
    project=None,
    min_severity=HEALTHY,
    query=None,
    limit=LIST_LIMIT,
):
    """
    Filter the snapshot's attention rows.

    Args:
        symptom: one of SYMPTOMS, or None for every symptom
        state: one of PROBLEM_STATES, or None
        project: project id, or None
        min_severity: CRITICAL, WARNING or HEALTHY
        query: case-insensitive name substring or instance id prefix

    Returns:
        tuple: (at most ``limit`` rows, total matching rows)
    """
    query = (query or "").strip().lower()
    matched = []
    for row in snapshot.get("rows", []):
        if symptom and not any(i["symptom"] == symptom for i in row["issues"]):
            continue
        if state and row["state"] != state:
            continue
        if project and row["project_id"] != project:
            continue
        # with a symptom selected, its own severity (like the tile counts)
        severity = row["severity"]
        if symptom:
            severity = max(
                i["severity"] for i in row["issues"] if i["symptom"] == symptom
            )
        if severity < min_severity:
            continue
        if query and not (
            query in (row["name"] or "").lower()
            or row["id"].lower().startswith(query)
        ):
            continue
        matched.append(row)
    return matched[:limit], len(matched)


def find_row(snapshot, instance_id):
    """The attention row of an instance, or None."""
    for row in snapshot.get("rows", []):
        if row["id"] == instance_id:
            return row
    return None


def project_choices(snapshot):
    """``[(project_id, label), ...]`` of the projects in the attention rows."""
    seen = {}
    for row in snapshot.get("rows", []):
        project_id = row["project_id"]
        if project_id and project_id not in seen:
            seen[project_id] = row.get("project_name") or project_id
    return sorted(seen.items(), key=lambda item: item[1].lower())


def rows_for_servers(servers, snapshot, thresholds, project_names, now):
    """
    Rows for servers listed from Nova (state filter of a healthy state).

    The row describes the live server (its status, state and lifecycle
    issues: the snapshot's inventory can be minutes old). Its metric
    issues, and No telemetry, are taken from the snapshot when the
    instance is in it and still runs.
    """
    rows = []
    for server in servers:
        issues = state_issues(server, thresholds, now)
        cached = find_row(snapshot, server["id"])
        state = classify_state(server)
        if cached is not None and state in RUNNING_STATES:
            issues += [
                issue
                for issue in cached["issues"]
                if issue["symptom"] in METRIC_SYMPTOMS
                or (issue["symptom"] == TELEMETRY and state == STATE_ACTIVE)
            ]
        row = _row(
            server["id"],
            server.get("name"),
            server.get("project_id"),
            server,
            issues,
            thresholds,
        )
        row["project_name"] = project_names.get(server.get("project_id"))
        rows.append(row)
    return rows
