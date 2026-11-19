#
# SPDX-License-Identifier: Apache-2.0

from django.test import override_settings

from grian_ui import config
from grian_ui import provisioning
from grian_ui.datasource import fake
from grian_ui.datasource import fake_provisioning
from grian_ui_tests.unit import base


NOW = 1_800_000_000.0
DAY = 86400
THRESHOLDS = dict(config.DEFAULT_PROVISIONING_THRESHOLDS)


def _boot(i, host, seconds, age, project="p1"):
    return {
        "id": f"id-{i}",
        "name": f"vm-{i}",
        "project_id": project,
        "host": host,
        "seconds": seconds,
        "at": NOW - age,
    }


class RangeTests(base.TestCase):
    def test_get_range(self):
        self.assertEqual(provisioning.get_range("24h"), ("24h", "24h", "1m"))
        self.assertEqual(provisioning.get_range("14d"), ("14d", "14d", "10m"))
        # unknown keys fall back to the default
        self.assertEqual(provisioning.get_range("1y")[0], "7d")
        self.assertEqual(provisioning.get_range(None)[0], "7d")

    def test_range_seconds(self):
        self.assertEqual(provisioning.range_seconds("24h"), DAY)
        self.assertEqual(provisioning.range_seconds("14d"), 14 * DAY)


class QuantileTests(base.TestCase):
    def test_empty(self):
        self.assertIsNone(provisioning.quantile([], 0.5))
        self.assertIsNone(provisioning.median([]))

    def test_interpolates_like_promql(self):
        self.assertEqual(provisioning.median([1, 2, 3, 4]), 2.5)
        self.assertEqual(provisioning.quantile([10], 0.95), 10)
        self.assertAlmostEqual(
            provisioning.quantile(list(range(1, 101)), 0.95), 95.05
        )


class HostStatusTests(base.TestCase):
    def test_rules(self):
        status = provisioning.host_status
        self.assertEqual(status(3, 2.0, THRESHOLDS), "slow")
        self.assertEqual(status(3, 1.99, THRESHOLDS), "slower")
        self.assertEqual(status(3, 1.5, THRESHOLDS), "slower")
        self.assertEqual(status(3, 1.49, THRESHOLDS), "normal")
        # too few boots, or nothing to compare with
        self.assertEqual(status(2, 5.0, THRESHOLDS), "few")
        self.assertEqual(status(5, None, THRESHOLDS), "few")


class ThinTests(base.TestCase):
    def _boots(self, n, host="h"):
        return [_boot(i, host, 1.0, n - i) for i in range(n)]

    def test_small_lists_are_kept(self):
        boots = self._boots(5)
        kept, thinned = provisioning.thin(boots, lambda b: False, 5)
        self.assertEqual(len(kept), 5)
        self.assertFalse(thinned)

    def test_thins_evenly_and_keeps_slow_hosts(self):
        boots = self._boots(100) + [
            _boot(1000 + i, "slow", 50.0, 10 + i) for i in range(5)
        ]
        kept, thinned = provisioning.thin(
            boots, lambda b: b["host"] == "slow", 20
        )
        self.assertTrue(thinned)
        self.assertEqual(len(kept), 20)
        self.assertEqual(sum(1 for b in kept if b["host"] == "slow"), 5)
        # sorted by time, and spread over the whole range
        times = [b["at"] for b in kept]
        self.assertEqual(times, sorted(times))
        others = [b for b in kept if b["host"] == "h"]
        self.assertEqual(others[0]["id"], "id-0")
        self.assertGreater(int(others[-1]["id"][3:]), 90)

    def test_too_many_slow_boots_are_thinned_too(self):
        boots = self._boots(50, host="slow")
        kept, thinned = provisioning.thin(boots, lambda b: True, 10)
        self.assertTrue(thinned)
        self.assertEqual(len(kept), 10)


class SummarizeTests(base.TestCase):
    def test_empty(self):
        summary = provisioning.summarize([], NOW, "7d", THRESHOLDS)
        self.assertEqual(summary["boots"], 0)
        self.assertIsNone(summary["median"])
        self.assertIsNone(summary["slowest_seconds"])
        self.assertEqual(summary["hosts"], [])
        self.assertEqual(summary["points"], [])
        self.assertFalse(summary["many"])

    def test_boots_outside_the_range_are_ignored(self):
        boots = [_boot(1, "h1", 5.0, 2 * DAY), _boot(2, "h1", 7.0, 100)]
        summary = provisioning.summarize(boots, NOW, "24h", THRESHOLDS)
        self.assertEqual(summary["boots"], 1)
        self.assertEqual(summary["start"], NOW - DAY)
        # a boot slightly after "now" (clock skew) is kept
        boots.append(_boot(3, "h1", 6.0, -5))
        summary = provisioning.summarize(boots, NOW, "24h", THRESHOLDS)
        self.assertEqual(summary["boots"], 2)

    def test_few_boots_show_the_slowest(self):
        boots = [_boot(i, "h1", 2.0 + i, 3600) for i in range(5)]
        summary = provisioning.summarize(boots, NOW, "7d", THRESHOLDS)
        self.assertFalse(summary["many"])
        self.assertIsNone(summary["p95"])
        self.assertEqual(summary["slowest_seconds"], 6.0)
        self.assertEqual(summary["median"], 4.0)
        # the slowest boots, slowest first, as new dicts
        self.assertEqual(
            [b["seconds"] for b in summary["slowest"]],
            [6.0, 5.0, 4.0, 3.0, 2.0],
        )
        self.assertIsNot(summary["slowest"][0], boots[4])

    def test_many_boots_show_the_95th_percentile(self):
        boots = [_boot(i, "h1", float(i + 1), 3600) for i in range(20)]
        summary = provisioning.summarize(boots, NOW, "7d", THRESHOLDS)
        self.assertTrue(summary["many"])
        self.assertAlmostEqual(summary["p95"], 19.05)

    def test_slow_host_is_judged_on_the_last_24_hours(self):
        boots = []
        # a week of normal boots on every host
        for i in range(60):
            boots.append(_boot(i, f"h{i % 3}", 10.0, 2 * DAY + i * 3600))
        # the last day: h0 slow, h1 slower, h2 normal, h3 too few boots
        for i in range(4):
            boots.append(_boot(100 + i, "h0", 40.0, 3600 + i))
            boots.append(_boot(200 + i, "h1", 16.0, 3600 + i))
            for n, host in enumerate(("h2", "h4", "h5", "h6")):
                boots.append(_boot(300 + 10 * n + i, host, 10.0, 3600 + i))
        boots.append(_boot(400, "h3", 90.0, 3600))
        summary = provisioning.summarize(boots, NOW, "7d", THRESHOLDS)
        by_name = {h["name"]: h for h in summary["hosts"]}
        self.assertEqual(by_name["h0"]["status"], "slow")
        # h0's median over the week still looks normal
        self.assertEqual(by_name["h0"]["median"], 10.0)
        self.assertEqual(by_name["h0"]["boots_24h"], 4)
        self.assertEqual(by_name["h1"]["status"], "slower")
        self.assertEqual(by_name["h2"]["status"], "normal")
        self.assertEqual(by_name["h3"]["status"], "few")
        # judged hosts first, the slowest compared to the cloud first
        self.assertEqual(
            [h["name"] for h in summary["hosts"]],
            ["h0", "h1", "h2", "h4", "h5", "h6", "h3"],
        )
        self.assertEqual(summary["slow_hosts"][0]["name"], "h0")
        self.assertEqual(summary["slower_hosts"], 1)
        self.assertEqual(summary["boots_24h"], 25)
        # the slow host's dots are flagged for the chart
        flagged = {p[4] for p in summary["points"] if p[5]}
        self.assertEqual(flagged, {"h0"})

    def test_thresholds_change_the_status(self):
        boots = [_boot(i, "h0", 16.0, 60) for i in range(3)]
        boots += [_boot(10 + i, "h1", 10.0, 60) for i in range(6)]
        strict = dict(THRESHOLDS, host_ratio_warning=1.1)
        summary = provisioning.summarize(boots, NOW, "24h", strict)
        self.assertEqual(summary["hosts"][0]["status"], "slower")
        lenient = dict(THRESHOLDS, host_min_boots=4)
        summary = provisioning.summarize(boots, NOW, "24h", lenient)
        by_name = {h["name"]: h for h in summary["hosts"]}
        self.assertEqual(by_name["h0"]["status"], "few")

    def test_bounded(self):
        boots = [
            _boot(i, f"h{i % 50}", 1.0 + i % 7, i * 30) for i in range(5000)
        ]
        summary = provisioning.summarize(boots, NOW, "7d", THRESHOLDS)
        self.assertEqual(summary["boots"], 5000)
        self.assertEqual(summary["hosts_total"], 50)
        self.assertEqual(len(summary["hosts"]), provisioning.TOP_LIMIT)
        self.assertEqual(len(summary["slowest"]), provisioning.TOP_LIMIT)
        self.assertEqual(len(summary["points"]), provisioning.MAX_POINTS)
        self.assertTrue(summary["thinned"])
        self.assertEqual(summary["points_total"], 5000)

    def test_boots_without_host(self):
        boots = [_boot(1, None, 5.0, 60), _boot(2, "h1", 6.0, 60)]
        summary = provisioning.summarize(boots, NOW, "24h", THRESHOLDS)
        self.assertEqual(summary["boots"], 2)
        self.assertEqual(summary["without_host"], 1)
        self.assertEqual([h["name"] for h in summary["hosts"]], ["h1"])


class FailedTests(base.TestCase):
    def test_percent(self):
        self.assertIsNone(provisioning.failed_percent(0, 0))
        self.assertEqual(provisioning.failed_percent(1, 3), 25.0)

    def test_level(self):
        level = provisioning.failed_level
        self.assertEqual(level(0, 10, THRESHOLDS), "ok")
        self.assertEqual(level(1, 99, THRESHOLDS), "warn")
        self.assertEqual(level(1, 5, THRESHOLDS), "crit")
        # only failures: 100%
        self.assertEqual(level(2, 0, THRESHOLDS), "crit")


class ThresholdsTests(base.TestCase):
    def test_defaults(self):
        self.assertEqual(
            config.get_provisioning_thresholds(),
            config.DEFAULT_PROVISIONING_THRESHOLDS,
        )

    @override_settings(
        GRIAN_PLUGIN={
            "provisioning_thresholds": {
                "host_ratio_warning": 1.2,
                "host_min_boots": 5.0,
            }
        }
    )
    def test_override(self):
        thresholds = config.get_provisioning_thresholds()
        self.assertEqual(thresholds["host_ratio_warning"], 1.2)
        self.assertEqual(thresholds["host_ratio_critical"], 2.0)
        self.assertEqual(thresholds["host_min_boots"], 5)
        self.assertIsInstance(thresholds["host_min_boots"], int)

    def test_invalid(self):
        for overrides in (
            {"host_min_boots": 0},
            {"host_min_boots": 2.5},
            {"host_ratio_warning": 3},
            {"median_seconds_warning": -1},
            {"unknown": 1},
        ):
            with (
                self.subTest(overrides=overrides),
                override_settings(
                    GRIAN_PLUGIN={"provisioning_thresholds": overrides}
                ),
                self.assertRaises(config.ConfigurationError),
            ):
                config.get_provisioning_thresholds()


class FakeDataTests(base.TestCase):
    def test_fake_client(self):
        boots = fake.get_client().get_provisioning_boots("24h")
        self.assertTrue(boots)
        self.assertEqual(
            set(boots[0]),
            {"id", "name", "project_id", "host", "seconds", "at"},
        )

    def test_fake_cloud_has_one_slow_host(self):
        summary = provisioning.summarize(
            fake_provisioning.boots_in_range("14d", NOW),
            NOW,
            "14d",
            THRESHOLDS,
        )
        self.assertEqual(summary["hosts_total"], 40)
        self.assertEqual(
            [h["name"] for h in summary["slow_hosts"]],
            [fake_provisioning.FAKE_SLOW_HOST],
        )
        self.assertTrue(summary["many"])
        self.assertTrue(summary["thinned"])
        # its 14 day median still looks normal
        slow = summary["hosts"][0]
        self.assertLess(slow["median"], 2 * summary["median"])

    def test_ranges_are_nested(self):
        day = fake_provisioning.boots_in_range("24h", NOW)
        week = fake_provisioning.boots_in_range("7d", NOW)
        self.assertLess(len(day), len(week))
        self.assertTrue(all(b["at"] >= NOW - DAY for b in day))

    def test_failed_builds(self):
        builds = fake_provisioning.fake_failed_builds(NOW)
        self.assertEqual(len(builds), 7)
        self.assertTrue(
            all(NOW - 6 * DAY <= b["created"] < NOW for b in builds)
        )

    def test_flavors(self):
        self.assertIn(
            fake_provisioning.fake_flavor(
                "00000000-0000-4000-8000-000000000001"
            ),
            fake_provisioning.FAKE_FLAVORS,
        )
        self.assertIsNone(fake_provisioning.fake_flavor("bad"))
        self.assertIsNone(fake_provisioning.fake_flavor(None))
