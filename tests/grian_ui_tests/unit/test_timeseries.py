#
# SPDX-License-Identifier: Apache-2.0

from grian_ui import timeseries
from grian_ui_tests.unit import base


class RangeTests(base.TestCase):
    def test_known_range(self):
        self.assertEqual(timeseries.get_range("1h"), ("1h", "1h", "1m"))
        self.assertEqual(timeseries.get_range("7d"), ("7d", "7d", "1h"))

    def test_unknown_range_falls_back_to_default(self):
        key, window, step = timeseries.get_range("bogus")
        self.assertEqual(key, timeseries.DEFAULT_RANGE)
        self.assertEqual((window, step), timeseries.RANGES[key])

    def test_none_range_falls_back_to_default(self):
        self.assertEqual(
            timeseries.get_range(None)[0], timeseries.DEFAULT_RANGE
        )

    def test_duration_seconds(self):
        self.assertEqual(timeseries.duration_seconds("5m"), 300)
        self.assertEqual(timeseries.duration_seconds("24h"), 86400)
        self.assertEqual(timeseries.duration_seconds("7d"), 604800)


class FormatAgeTests(base.TestCase):
    def test_format_age(self):
        self.assertEqual(timeseries.format_age(None), "")
        self.assertEqual(timeseries.format_age(-5), "0 seconds ago")
        self.assertEqual(timeseries.format_age(1), "1 second ago")
        self.assertEqual(timeseries.format_age(45), "45 seconds ago")
        self.assertEqual(timeseries.format_age(60), "1 minute ago")
        self.assertEqual(timeseries.format_age(18 * 60 + 5), "18 minutes ago")
        self.assertEqual(timeseries.format_age(3601), "1 hour ago")
        self.assertEqual(timeseries.format_age(9000), "2 hours ago")
        self.assertEqual(timeseries.format_age(86401), "1 day ago")
        self.assertEqual(timeseries.format_age(3 * 86400), "3 days ago")


class FillGapsTests(base.TestCase):
    def test_grid_and_gaps(self):
        now = 10_000 * 60 + 30  # 30 s past a minute boundary
        end = 10_000 * 60
        points = [[end - 120.0, 5.0], [end + 2.0, 7.0]]  # jittered timestamp
        timestamps, values = timeseries.fill_gaps(points, "1h", now)
        # subqueries are left-open: range/step points ending at "end"
        self.assertEqual(len(timestamps), 60)
        self.assertEqual(timestamps[-1], end)
        self.assertEqual(timestamps[0], end - 59 * 60)
        self.assertEqual(values[-1], 7.0)
        self.assertEqual(values[-3], 5.0)
        self.assertIsNone(values[-2])
        self.assertEqual(sum(v is not None for v in values), 2)

    def test_empty(self):
        timestamps, values = timeseries.fill_gaps([], "6h", 0)
        self.assertEqual(len(values), 72)
        self.assertTrue(all(v is None for v in values))

    def test_gap_filled_series(self):
        now = 10_000 * 60
        series = timeseries.gap_filled_series([[now, 3.0]], "1h", now)
        self.assertEqual(len(series), 60)
        self.assertEqual(series[-1], [now, 3.0])
        self.assertEqual(series[0], [now - 59 * 60, None])

    def test_every_range_step_divides_its_window(self):
        for key, (window, step) in timeseries.RANGES.items():
            self.assertEqual(
                timeseries.duration_seconds(window)
                % timeseries.duration_seconds(step),
                0,
                key,
            )

    def test_prometheus_2_range_start_point_is_ignored(self):
        # Prometheus 2 also returns the point at the start of the range,
        # one step before the first grid slot
        end = 10_000 * 60
        start = end - 60 * 60
        timestamps, values = timeseries.fill_gaps(
            [[start, 1.0], [start + 60, 2.0]], "1h", end
        )
        self.assertEqual(timestamps[0], start + 60)
        self.assertEqual(values[0], 2.0)
        self.assertEqual(len(values), 60)
        self.assertNotIn(1.0, values)
