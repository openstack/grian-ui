#
# SPDX-License-Identifier: Apache-2.0

from grian_ui import config
from grian_ui import instance_health as ih
from grian_ui_tests.unit import base


THRESHOLDS = dict(config.DEFAULT_INSTANCE_HEALTH_THRESHOLDS)
NOW = 1_800_000_000.0
DAY = 86400


def _server(name, status="ACTIVE", power=1, task=None, **kwargs):
    server = {
        "id": kwargs.pop(
            "id", f"{name:0<8}"[:8] + "-0000-4000-8000-000000000000"
        ),
        "name": name,
        "project_id": kwargs.pop("project_id", "p1"),
        "host": kwargs.pop("host", "compute-01"),
        "status": status,
        "power_state": power,
        "task_state": task,
        "updated": kwargs.pop("updated", NOW - DAY),
        "launched_at": kwargs.pop("launched_at", NOW - DAY),
        "fault": kwargs.pop("fault", None),
    }
    server.update(kwargs)
    return server


def _reporting(*servers):
    return {
        s["id"]: {"name": s["name"], "project_id": s["project_id"]}
        for s in servers
    }


class ClassifyStateTests(base.TestCase):
    def test_states(self):
        cases = [
            (_server("a"), ih.STATE_ACTIVE),
            (_server("a", power=4), ih.STATE_ACTIVE_NOT_RUNNING),
            (_server("a", power=6), ih.STATE_ACTIVE_NOT_RUNNING),
            (
                _server("a", status="ACTIVE", task="rebooting"),
                ih.STATE_IN_TASK,
            ),
            (_server("a", status="BUILD", power=0), ih.STATE_IN_TASK),
            (_server("a", status="VERIFY_RESIZE"), ih.STATE_IN_TASK),
            (_server("a", status="SHUTOFF", power=4), ih.STATE_SHUTOFF),
            (_server("a", status="PAUSED", power=3), ih.STATE_PAUSED),
            (_server("a", status="SUSPENDED", power=7), ih.STATE_SUSPENDED),
            (_server("a", status="SHELVED_OFFLOADED"), ih.STATE_SHELVED),
            (_server("a", status="ERROR", task="spawning"), ih.STATE_ERROR),
            (_server("a", status="RESCUE"), ih.STATE_OTHER),
            (_server("a", status=None), ih.STATE_OTHER),
        ]
        for server, expected in cases:
            self.assertEqual(ih.classify_state(server), expected, server)

    def test_every_state_has_a_label(self):
        self.assertEqual(set(ih.STATE_LABELS), set(ih.STATES))

    def test_listable_states_are_states(self):
        self.assertTrue(set(ih.LISTABLE_STATES) <= set(ih.STATES))


class StateIssuesTests(base.TestCase):
    def _issues(self, server):
        return ih.state_issues(server, THRESHOLDS, NOW)

    def test_healthy(self):
        self.assertEqual(self._issues(_server("a")), [])

    def test_stopped_on_purpose_is_not_an_issue(self):
        for status, power in (
            ("SHUTOFF", 4),
            ("PAUSED", 3),
            ("SUSPENDED", 7),
            ("SHELVED_OFFLOADED", 4),
        ):
            self.assertEqual(self._issues(_server("a", status, power)), [])

    def test_error_is_critical(self):
        (issue,) = self._issues(_server("a", "ERROR", 0))
        self.assertEqual(
            (issue["symptom"], issue["severity"]), (ih.STATE, ih.CRITICAL)
        )

    def test_crashed_is_critical(self):
        (issue,) = self._issues(_server("a", "ACTIVE", ih.POWER_CRASHED))
        self.assertEqual(issue["severity"], ih.CRITICAL)
        self.assertEqual(ih.issue_text(issue), "Crashed")

    def test_active_not_running_is_warning(self):
        (issue,) = self._issues(_server("a", "ACTIVE", 4))
        self.assertEqual(issue["severity"], ih.WARNING)
        self.assertIn("shut down", ih.issue_text(issue))

    def test_unknown_power_state(self):
        (issue,) = self._issues(_server("a", "ACTIVE", 42))
        self.assertIn("unknown (42)", ih.issue_text(issue))

    def test_stuck_task(self):
        minutes = THRESHOLDS["stuck_task_minutes"]
        server = _server(
            "a", "BUILD", 0, "spawning", updated=NOW - (minutes + 5) * 60
        )
        (issue,) = self._issues(server)
        self.assertEqual(issue["severity"], ih.WARNING)
        self.assertIn("spawning", ih.issue_text(issue))
        self.assertIn(str(minutes + 5), ih.issue_text(issue))

    def test_task_within_threshold_is_fine(self):
        server = _server("a", "BUILD", 0, "spawning", updated=NOW - 60)
        self.assertEqual(self._issues(server), [])

    def test_active_in_task_is_not_reported_as_not_running(self):
        server = _server("a", "ACTIVE", 4, "powering-on", updated=NOW - 60)
        self.assertEqual(self._issues(server), [])

    def test_task_without_updated_time(self):
        server = _server("a", "BUILD", 0, "spawning", updated=None)
        self.assertEqual(self._issues(server), [])


class MetricIssuesTests(base.TestCase):
    def test_thresholds_are_exclusive(self):
        symptoms = {
            "cpu": {"i": 5.0},
            "memory": {"i": 0.0},
            "disk": {"i": {"value": 20.0, "device": "vda"}},
            "network": {"i": 0},
        }
        self.assertEqual(ih.metric_issues("i", symptoms, THRESHOLDS), [])

    def test_warning_and_critical(self):
        symptoms = {
            "cpu": {"i": 5.1},
            "memory": {"i": 10.5},
            "disk": {"i": {"value": 140.0, "device": "vdb"}},
            "network": {"i": 0.2},
        }
        issues = {
            i["symptom"]: i
            for i in ih.metric_issues("i", symptoms, THRESHOLDS)
        }
        self.assertEqual(issues["cpu"]["severity"], ih.WARNING)
        self.assertEqual(ih.issue_text(issues["cpu"]), "CPU wait 5.1%")
        self.assertEqual(issues["memory"]["severity"], ih.CRITICAL)
        self.assertEqual(issues["disk"]["severity"], ih.CRITICAL)
        self.assertEqual(ih.issue_text(issues["disk"]), "vdb latency 140 ms")
        self.assertEqual(issues["disk"]["params"]["device"], "vdb")
        self.assertEqual(issues["network"]["severity"], ih.WARNING)

    def test_disk_without_device(self):
        symptoms = {"disk": {"i": {"value": 30.0, "device": None}}}
        (issue,) = ih.metric_issues("i", symptoms, THRESHOLDS)
        self.assertEqual(ih.issue_text(issue), "Disk latency 30 ms")

    def test_non_finite_and_missing_values_are_ignored(self):
        symptoms = {
            "cpu": {"i": float("inf")},
            "memory": {"i": None},
            "network": {"other": 50},
        }
        self.assertEqual(ih.metric_issues("i", symptoms, THRESHOLDS), [])

    def test_custom_thresholds(self):
        thresholds = dict(
            THRESHOLDS, cpu_wait_warning=50, cpu_wait_critical=60
        )
        self.assertEqual(
            ih.metric_issues("i", {"cpu": {"i": 40}}, thresholds), []
        )


class TelemetryIssueTests(base.TestCase):
    def test_reporting(self):
        server = _server("a")
        self.assertIsNone(ih.telemetry_issue(server, _reporting(server), NOW))

    def test_not_reporting_is_warning(self):
        issue = ih.telemetry_issue(_server("a"), {}, NOW)
        self.assertEqual(
            (issue["symptom"], issue["severity"]), (ih.TELEMETRY, ih.WARNING)
        )

    def test_new_instance_grace(self):
        server = _server("a", launched_at=NOW - 60, updated=NOW - 60)
        self.assertIsNone(ih.telemetry_issue(server, {}, NOW))

    def test_grace_after_power_on(self):
        # launched long ago, started a minute ago: updated changed
        server = _server("a", launched_at=NOW - DAY, updated=NOW - 60)
        self.assertIsNone(ih.telemetry_issue(server, {}, NOW))

    def test_running_since(self):
        self.assertEqual(ih.running_since({"launched_at": 5, "updated": 9}), 9)
        self.assertEqual(ih.running_since({"launched_at": 5}), 5)
        self.assertIsNone(ih.running_since({}))

    def test_only_running_instances(self):
        for server in (
            _server("a", "SHUTOFF", 4),
            _server("a", "ACTIVE", 4),
            _server("a", "BUILD", 0, "spawning"),
        ):
            self.assertIsNone(ih.telemetry_issue(server, {}, NOW))

    def test_unknown_when_metrics_failed(self):
        self.assertIsNone(ih.telemetry_issue(_server("a"), None, NOW))


class BuildSnapshotTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.ok = _server("ok", host="compute-01")
        self.cpu = _server("cpu", host="compute-02")
        self.disk = _server("disk", host="compute-02")
        self.error = _server(
            "error", "ERROR", 0, host="compute-03", fault="No valid host"
        )
        self.off = _server("off", "SHUTOFF", 4)
        self.silent = _server("silent", host="compute-04")
        self.servers = [
            self.ok,
            self.cpu,
            self.disk,
            self.error,
            self.off,
            self.silent,
        ]
        self.reporting = _reporting(self.ok, self.cpu, self.disk)
        self.symptoms = {
            "cpu": {self.cpu["id"]: 25.0},
            "disk": {self.disk["id"]: {"value": 30.0, "device": "vda"}},
            "memory": {},
            "network": {},
        }

    def _build(self, **kwargs):
        args = dict(
            servers=self.servers,
            reporting=self.reporting,
            symptoms=self.symptoms,
            thresholds=THRESHOLDS,
            now=NOW,
            project_names={"p1": "Project One"},
        )
        args.update(kwargs)
        return ih.build_snapshot(**args)

    def test_counts(self):
        snap = self._build()
        self.assertTrue(snap["nova_ok"])
        self.assertTrue(snap["metrics_ok"])
        self.assertEqual(snap["total"], 6)
        self.assertEqual(snap["running"], 4)
        self.assertEqual(snap["reporting"], 3)
        self.assertEqual(snap["critical"], 2)  # cpu, error
        self.assertEqual(snap["warning"], 2)  # disk, silent
        self.assertEqual(snap["attention_total"], 4)
        self.assertEqual(snap["hosts_affected"], 3)
        states = {s["key"]: s["count"] for s in snap["states"]}
        self.assertEqual(states[ih.STATE_ACTIVE], 4)
        self.assertEqual(states[ih.STATE_SHUTOFF], 1)
        self.assertEqual(states[ih.STATE_ERROR], 1)
        self.assertEqual([s["key"] for s in snap["states"]], list(ih.STATES))
        self.assertEqual(
            snap["symptoms"][ih.CPU], {"critical": 1, "warning": 0, "total": 1}
        )
        self.assertEqual(snap["symptoms"][ih.TELEMETRY]["warning"], 1)
        self.assertEqual(snap["symptoms"][ih.STATE]["critical"], 1)

    def test_order_worst_first(self):
        names = [r["name"] for r in self._build()["rows"]]
        # critical state problems first, then critical metrics, then warnings
        # (the disk warning is further past its threshold than no telemetry)
        self.assertEqual(names, ["error", "cpu", "disk", "silent"])

    def test_more_issues_and_bigger_ratio_rank_higher(self):
        a, b, c = _server("a"), _server("b"), _server("c")
        symptoms = {
            "cpu": {a["id"]: 6.0, b["id"]: 15.0, c["id"]: 6.0},
            "network": {c["id"]: 1.0},
        }
        snap = ih.build_snapshot(
            [a, b, c], _reporting(a, b, c), symptoms, THRESHOLDS, NOW
        )
        self.assertEqual([r["name"] for r in snap["rows"]], ["c", "b", "a"])

    def test_rows_carry_display_data(self):
        row = next(r for r in self._build()["rows"] if r["name"] == "error")
        self.assertEqual(row["project_name"], "Project One")
        self.assertEqual(row["host"], "compute-03")
        self.assertEqual(row["state"], ih.STATE_ERROR)
        self.assertEqual(row["fault"], "No valid host")
        self.assertEqual(row["severity"], ih.CRITICAL)

    def test_issues_sorted_worst_first(self):
        symptoms = dict(self.symptoms, network={self.cpu["id"]: 1.0})
        row = next(
            r
            for r in self._build(symptoms=symptoms)["rows"]
            if r["name"] == "cpu"
        )
        self.assertEqual(
            [i["symptom"] for i in row["issues"]], ["cpu", "network"]
        )

    def test_store_limit_keeps_exact_counts(self):
        snap = self._build(limit=2)
        self.assertEqual(len(snap["rows"]), 2)
        self.assertEqual(snap["attention_total"], 4)
        self.assertTrue(snap["truncated"])
        self.assertEqual(snap["critical"], 2)

    def test_metrics_failed(self):
        snap = self._build(reporting=None, symptoms=None)
        self.assertFalse(snap["metrics_ok"])
        self.assertIsNone(snap["reporting"])
        self.assertEqual([r["name"] for r in snap["rows"]], ["error"])
        self.assertEqual(snap["symptoms"][ih.TELEMETRY]["total"], 0)

    def test_nova_failed_uses_metric_labels(self):
        snap = self._build(servers=None)
        self.assertFalse(snap["nova_ok"])
        self.assertIsNone(snap["total"])
        self.assertIsNone(snap["running"])
        self.assertEqual(snap["reporting"], 3)
        self.assertEqual(snap["states"], [])
        self.assertEqual([r["name"] for r in snap["rows"]], ["cpu", "disk"])
        row = snap["rows"][0]
        self.assertIsNone(row["host"])
        self.assertIsNone(row["state"])
        self.assertEqual(row["project_name"], "Project One")
        self.assertEqual(snap["hosts_affected"], 0)

    def test_metric_symptom_of_a_deleted_instance_is_dropped(self):
        symptoms = dict(self.symptoms, network={"gone": 50.0})
        snap = self._build(symptoms=symptoms)
        self.assertNotIn("gone", [r["id"] for r in snap["rows"]])

    def test_empty_cloud(self):
        snap = ih.build_snapshot([], {}, {}, THRESHOLDS, NOW)
        self.assertEqual(snap["total"], 0)
        self.assertEqual(snap["rows"], [])
        self.assertEqual(snap["attention_total"], 0)


class FilterRowsTests(base.TestCase):
    def setUp(self):
        super().setUp()
        servers = [
            _server("web-1", project_id="pa"),
            _server("web-2", project_id="pb"),
            _server("db-1", "ERROR", 0, project_id="pa"),
            _server("db-2", "ACTIVE", 4, project_id="pb"),
        ]
        symptoms = {"cpu": {servers[0]["id"]: 30.0, servers[1]["id"]: 6.0}}
        self.snap = ih.build_snapshot(
            servers, _reporting(*servers), symptoms, THRESHOLDS, NOW
        )
        self.servers = servers

    def _names(self, **kwargs):
        rows, total = ih.filter_rows(self.snap, **kwargs)
        return [r["name"] for r in rows], total

    def test_all(self):
        self.assertEqual(
            self._names(), (["db-1", "web-1", "db-2", "web-2"], 4)
        )

    def test_symptom(self):
        self.assertEqual(self._names(symptom=ih.CPU), (["web-1", "web-2"], 2))
        self.assertEqual(self._names(symptom=ih.STATE), (["db-1", "db-2"], 2))

    def test_state(self):
        self.assertEqual(
            self._names(state=ih.STATE_ACTIVE_NOT_RUNNING), (["db-2"], 1)
        )

    def test_project_and_severity(self):
        self.assertEqual(self._names(project="pb"), (["db-2", "web-2"], 2))
        self.assertEqual(
            self._names(min_severity=ih.CRITICAL), (["db-1", "web-1"], 2)
        )

    def test_query_name_or_id_prefix(self):
        self.assertEqual(self._names(query=" WEB "), (["web-1", "web-2"], 2))
        prefix = self.servers[2]["id"][:6]
        self.assertEqual(self._names(query=prefix)[0], ["db-1"])
        self.assertEqual(self._names(query="nope"), ([], 0))

    def test_limit_keeps_total(self):
        rows, total = ih.filter_rows(self.snap, limit=1)
        self.assertEqual((len(rows), total), (1, 4))


class HelpersTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.a = _server("a", "ERROR", 0, project_id="p2")
        self.b = _server("b", "ERROR", 0, project_id="p1")
        self.c = _server("c", "ERROR", 0, project_id=None)
        self.snap = ih.build_snapshot(
            [self.a, self.b, self.c],
            {},
            {},
            THRESHOLDS,
            NOW,
            project_names={"p1": "zeta", "p2": "Alpha"},
        )

    def test_find_row(self):
        self.assertEqual(ih.find_row(self.snap, self.a["id"])["name"], "a")
        self.assertIsNone(ih.find_row(self.snap, "missing"))

    def test_project_choices_sorted_by_label(self):
        self.assertEqual(
            ih.project_choices(self.snap), [("p2", "Alpha"), ("p1", "zeta")]
        )

    def test_rows_for_servers_reuse_snapshot_issues(self):
        off = _server("off", "SHUTOFF", 4, project_id="p1")
        rows = ih.rows_for_servers(
            [self.a, off], self.snap, THRESHOLDS, {"p1": "zeta"}, NOW
        )
        self.assertEqual(rows[0]["severity"], ih.CRITICAL)
        self.assertEqual(rows[1]["severity"], ih.HEALTHY)
        self.assertEqual(rows[1]["issues"], [])
        self.assertEqual(rows[1]["project_name"], "zeta")
        self.assertEqual(rows[1]["state"], ih.STATE_SHUTOFF)


class IssueTextTests(base.TestCase):
    def test_snapshot_keeps_codes_not_texts(self):
        server = _server("a", "ERROR", 0)
        snap = ih.build_snapshot([server], {}, {}, THRESHOLDS, NOW)
        (issue,) = snap["rows"][0]["issues"]
        self.assertNotIn("text", issue)
        self.assertEqual(issue["code"], ih.ISSUE_ERROR)

    def test_with_texts_copies(self):
        issues = [ih._issue(ih.CPU, ih.WARNING, ih.CPU, 7.25, value=7.25)]
        texts = ih.with_texts(issues)
        self.assertEqual(texts[0]["text"], "CPU wait 7.2%")
        self.assertNotIn("text", issues[0])

    def test_texts(self):
        cases = [
            (
                ih._issue(
                    ih.STATE,
                    ih.WARNING,
                    ih.ISSUE_STUCK,
                    40,
                    task="spawning",
                    minutes=40,
                ),
                "In task spawning for 40 min",
            ),
            (
                ih._issue(
                    ih.TELEMETRY, ih.WARNING, ih.ISSUE_NO_SAMPLES, window="10m"
                ),
                "No samples in the last 10m",
            ),
            (
                ih._issue(ih.MEMORY, ih.WARNING, ih.MEMORY, 3, value=3),
                "Swapping 3 MiB/s",
            ),
            (
                ih._issue(ih.NETWORK, ih.WARNING, ih.NETWORK, 0.5, value=0.5),
                "0.5 dropped or failed packets/s",
            ),
            (ih._issue(ih.STATE, ih.WARNING, "unknown-code"), "unknown-code"),
        ]
        for issue, text in cases:
            self.assertEqual(ih.issue_text(issue), text)
