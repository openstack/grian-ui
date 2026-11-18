#
# SPDX-License-Identifier: Apache-2.0

import threading
import time

from unittest import mock

from django.core.cache import cache

from grian_ui import cache as grian_cache
from grian_ui_tests.unit import base


MODULE = "grian_ui.cache"


class CacheKeyTests(base.TestCase):
    def test_region_is_sanitized(self):
        request = mock.MagicMock()
        request.user.services_region = "Region One/2"
        key = grian_cache.cache_key(request, "panel", "snapshot", version=3)
        self.assertRegex(key, r":Region_One_2-[0-9a-f]{8}$")
        self.assertTrue(key.startswith("grian_ui:panel:v3:snapshot:"))

    def test_regions_that_sanitize_alike_get_different_keys(self):
        keys = set()
        for region in ("Region One", "Region_One"):
            request = mock.MagicMock()
            request.user.services_region = region
            keys.add(grian_cache.cache_key(request, "panel", "snapshot"))
        self.assertEqual(len(keys), 2)

    def test_literal_text_cannot_collide_with_a_hashed_part(self):
        hashed = grian_cache._key_part("a b")
        self.assertNotEqual(grian_cache._key_part(hashed), hashed)

    def test_namespace_and_name_are_key_safe(self):
        request = mock.MagicMock()
        request.user.services_region = "RegionOne"
        key = grian_cache.cache_key(request, "my panel", "fleet:6h")
        self.assertNotIn(" ", key)
        self.assertRegex(key, r"^grian_ui:my_panel-[0-9a-f]{8}:v1:fleet_6h-")

    def test_long_region_keeps_the_key_short(self):
        request = mock.MagicMock()
        request.user.services_region = "r" * 1000
        key = grian_cache.cache_key(request, "panel", "snapshot")
        self.assertLess(len(key), 120)

    def test_missing_region(self):
        request = mock.MagicMock()
        request.user.services_region = None
        key = grian_cache.cache_key(request, "panel", "snapshot")
        self.assertTrue(key.endswith(":default"))
        self.assertIn(":v1:", key)

    def test_datasource_is_part_of_the_key(self):
        request = mock.MagicMock()
        request.user.services_region = "RegionOne"
        key = grian_cache.cache_key(request, "panel", "snapshot")
        self.assertIn(":fake:", key)


class _CacheTestCase(base.TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        grian_cache._LOCAL.clear()
        grian_cache._last_warnings.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(grian_cache._LOCAL.clear)
        self.addCleanup(grian_cache._last_warnings.clear)

    def get(self, compute, key="k", ttl=30):
        return grian_cache.get_or_compute(key, ttl, compute)

    def clock(self, *times):
        """Patch the wait clock with the given readings."""
        readings = list(times)

        def now():
            # the last reading repeats, so extra clock reads (logging,
            # heartbeats) cannot break a test
            return readings.pop(0) if len(readings) > 1 else readings[0]

        return mock.patch(f"{MODULE}._now", side_effect=now)


class GetOrComputeTests(_CacheTestCase):
    def test_computes_once_and_caches(self):
        compute = mock.Mock(return_value={"v": 1})
        self.assertEqual(self.get(compute), {"v": 1})
        self.assertEqual(self.get(compute), {"v": 1})
        compute.assert_called_once()
        self.assertIsNone(cache.get("k:lock"))

    def test_cache_hit_is_one_round_trip(self):
        cache.set("k", {"v": 1}, 30)
        # (LocMemCache implements get_many with get: count our calls)
        shared = grian_cache._SHARED
        with (
            mock.patch.object(shared, "get", wraps=shared.get) as get,
            mock.patch.object(
                shared, "get_many", wraps=shared.get_many
            ) as get_many,
        ):
            self.assertEqual(self.get(mock.Mock()), {"v": 1})
        get_many.assert_called_once_with(["k", "k:local"])
        get.assert_not_called()

    def test_compute_must_return_a_picklable_dict(self):
        with self.assertRaises(TypeError) as ctx:
            self.get(lambda: {"lock": threading.Lock()})
        self.assertIn("picklable", str(ctx.exception))
        self.assertIsNone(cache.get("k"))
        self.assertIsNone(cache.get("k:lock"))
        self.assertIsNone(grian_cache._LOCAL.get("k"))

    def test_broken_compute_is_not_rerun_by_every_waiter(self):
        compute = mock.Mock(return_value=["not", "a", "dict"])
        self.assertRaises(TypeError, self.get, compute)
        # the next partials fail fast instead of running compute again
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        compute.assert_called_once()

    def test_compute_must_return_a_dict(self):
        for value in (None, [1], "x"):
            cache.clear()
            self.assertRaises(TypeError, self.get, lambda v=value: v)
            self.assertIsNone(cache.get("k"))
            self.assertIsNone(cache.get("k:lock"))

    def test_waits_for_the_holder_without_a_copy(self):
        cache.add("k:lock", "other", 30)  # another request is computing
        compute = mock.Mock(return_value={"v": "mine"})

        def holder_finishes(_seconds):
            cache.set("k", {"v": "theirs"}, 30)
            cache.delete("k:lock")

        with mock.patch(f"{MODULE}._sleep", side_effect=holder_finishes):
            self.assertEqual(self.get(compute), {"v": "theirs"})
        compute.assert_not_called()

    def test_fast_refresh_gives_every_request_the_new_value(self):
        cache.set("k:last", {"v": "old"}, 600)
        cache.add("k:lock", "other", 30)

        def holder_finishes(_seconds):
            cache.set("k", {"v": "new"}, 30)
            cache.delete("k:lock")

        with mock.patch(f"{MODULE}._sleep", side_effect=holder_finishes):
            self.assertEqual(self.get(mock.Mock()), {"v": "new"})

    def test_slow_refresh_serves_the_last_copy_unmarked(self):
        cache.set("k:last", {"v": "old"}, 600)
        cache.add("k:lock", "other", 30)
        compute = mock.Mock(return_value={"v": "new"})
        with (
            mock.patch(f"{MODULE}._sleep") as sleep,
            self.clock(0, 0.5, grian_cache.FRESH_WAIT_SECONDS),
        ):
            value = self.get(compute)
        # being refreshed is not a failure: no "could not be refreshed"
        self.assertEqual(value, {"v": "old"})
        # waited at most FRESH_WAIT_SECONDS
        self.assertEqual(sleep.call_count, 1)
        compute.assert_not_called()

    def test_gives_up_after_the_short_wait(self):
        cache.add("k:lock", "other", 30)
        compute = mock.Mock(return_value={"v": 1})
        with (
            mock.patch(f"{MODULE}._sleep") as sleep,
            self.clock(0, 1, grian_cache.LOCK_WAIT_SECONDS),
        ):
            self.assertRaises(grian_cache.StillComputing, self.get, compute)
        self.assertEqual(sleep.call_count, 1)
        compute.assert_not_called()

    def test_waiter_takes_over_an_expired_lock(self):
        cache.add("k:lock", "other", 30)
        compute = mock.Mock(return_value={"v": 1})

        def lock_expires(_seconds):
            cache.delete("k:lock")  # no value, no failure marker

        with mock.patch(f"{MODULE}._sleep", side_effect=lock_expires):
            self.assertEqual(self.get(compute), {"v": 1})
        compute.assert_called_once()

    def test_rechecks_value_after_winning_the_lock(self):
        compute = mock.Mock(return_value={"v": "mine"})
        real_add = cache.add

        def add_after_previous_holder_finished(key, *args, **kwargs):
            cache.set("k", {"v": "theirs"}, 30)
            return real_add(key, *args, **kwargs)

        with mock.patch.object(
            cache, "add", side_effect=add_after_previous_holder_finished
        ):
            self.assertEqual(self.get(compute), {"v": "theirs"})
        compute.assert_not_called()
        self.assertIsNone(cache.get("k:lock"))

    def test_expired_holder_does_not_release_the_next_lock(self):
        def slow_compute():
            # our lock expired and another request took it meanwhile
            cache.set("k:lock", "someone-else", 30)
            return {"v": 1}

        self.assertEqual(self.get(slow_compute), {"v": 1})
        self.assertEqual(cache.get("k:lock"), "someone-else")

    def test_last_good_copy_outlives_the_value(self):
        with mock.patch.object(
            cache, "set_many", wraps=cache.set_many
        ) as set_many:
            self.get(lambda: {"v": 1}, ttl=900)
        ttls = {
            next(iter(c.args[0])): c.args[1] for c in set_many.call_args_list
        }
        self.assertEqual(ttls["k"], 900)
        self.assertEqual(ttls["k:last"], 900 + grian_cache.LAST_GOOD_TTL)


class FailureTests(_CacheTestCase):
    def test_failure_is_remembered_and_lock_released(self):
        compute = mock.Mock(side_effect=RuntimeError("down"))
        # the holder fails like the waiters, with the original error as cause
        with self.assertRaises(grian_cache.ComputationFailed) as ctx:
            self.get(compute)
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        self.assertIsNone(cache.get("k:lock"))
        self.assertTrue(cache.get("k:failed"))
        # the next partials of the same refresh fail fast, as a failure
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        compute.assert_called_once()

    def test_retries_once_the_failure_is_forgotten(self):
        compute = mock.Mock(side_effect=[RuntimeError("down"), {"v": 1}])
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        cache.delete("k:failed")  # FAILURE_TTL expired
        self.assertEqual(self.get(compute), {"v": 1})
        self.assertEqual(compute.call_count, 2)

    def test_holder_failure_without_a_copy_is_logged(self):
        compute = mock.Mock(side_effect=RuntimeError("down"))
        with self.assertLogs(grian_cache.LOG, level="ERROR") as logs:
            self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        self.assertIn("Error computing k", "\n".join(logs.output))
        self.assertIn("RuntimeError: down", "\n".join(logs.output))

    def test_outage_reports_failure_not_loading_while_retrying(self):
        compute = mock.Mock(side_effect=RuntimeError("down"))
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        cache.delete("k:failed")  # FAILURE_TTL expired
        cache.add("k:lock", "other", 30)  # a slow retry is running
        with (
            mock.patch(f"{MODULE}._sleep") as sleep,
            self.clock(0, 0.5, grian_cache.FRESH_WAIT_SECONDS),
        ):
            self.assertRaises(
                grian_cache.ComputationFailed, self.get, mock.Mock()
            )
        # reported after the short fresh wait, not after LOCK_WAIT_SECONDS
        self.assertEqual(sleep.call_count, 1)

    def test_success_clears_the_failing_state(self):
        compute = mock.Mock(side_effect=[RuntimeError("down"), {"v": 1}])
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        self.assertTrue(cache.get("k:failing"))
        cache.delete("k:failed")
        self.assertEqual(self.get(compute), {"v": 1})
        self.assertIsNone(cache.get("k:failing"))

    def test_failure_and_still_computing_are_distinct(self):
        self.assertTrue(
            issubclass(grian_cache.StillComputing, grian_cache.DataUnavailable)
        )
        self.assertTrue(
            issubclass(
                grian_cache.ComputationFailed, grian_cache.DataUnavailable
            )
        )
        self.assertFalse(
            issubclass(
                grian_cache.ComputationFailed, grian_cache.StillComputing
            )
        )

    def test_recent_failure_serves_the_last_copy_marked_stale(self):
        cache.set("k:last", {"v": "old"}, 600)
        cache.set("k:failed", True, 5)
        compute = mock.Mock(return_value={"v": "new"})
        self.assertEqual(
            self.get(compute), {"v": "old", "is_stale_copy": True}
        )
        compute.assert_not_called()

    def test_holder_failure_serves_the_last_copy_marked_stale(self):
        cache.set("k:last", {"v": "old"}, 600)
        compute = mock.Mock(side_effect=RuntimeError("down"))
        with self.assertLogs(grian_cache.LOG, level="ERROR"):
            value = self.get(compute)
        self.assertEqual(value, {"v": "old", "is_stale_copy": True})
        self.assertTrue(cache.get("k:failed"))

    def test_failure_seen_after_winning_the_lock(self):
        compute = mock.Mock(return_value={"v": 1})
        real_add = cache.add

        def add_after_previous_holder_failed(key, *args, **kwargs):
            cache.set("k:failed", True, 5)
            return real_add(key, *args, **kwargs)

        with mock.patch.object(
            cache, "add", side_effect=add_after_previous_holder_failed
        ):
            self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        compute.assert_not_called()
        self.assertIsNone(cache.get("k:lock"))


class LargeValueTests(_CacheTestCase):
    @staticmethod
    def _refusing_set_many(real):
        def set_many(data, ttl):
            # memcached refuses the big value under every key
            refused = [
                k
                for k, v in data.items()
                if isinstance(v, dict) and v.get("big")
            ]
            real({k: v for k, v in data.items() if k not in refused}, ttl)
            return refused

        return set_many

    def test_refused_value_moves_to_the_per_process_store(self):
        compute = mock.Mock(return_value={"big": True})
        with (
            mock.patch.object(
                cache,
                "set_many",
                side_effect=self._refusing_set_many(cache.set_many),
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING") as logs,
        ):
            self.assertEqual(self.get(compute), {"big": True})
            # the next requests of this process get the stored copy
            self.assertEqual(self.get(compute), {"big": True})
            self.assertEqual(self.get(compute), {"big": True})
        compute.assert_called_once()
        self.assertIn("Could not store k", "\n".join(logs.output))
        self.assertTrue(cache.get("k:local"))
        self.assertIsNone(cache.get("k:failed"))
        self.assertIsNone(cache.get("k:lock"))

    def test_raising_backend_is_handled_like_a_refusal(self):
        compute = mock.Mock(return_value={"big": True})
        with (
            mock.patch.object(
                cache,
                "set_many",
                side_effect=RuntimeError("object too large for cache"),
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.assertEqual(self.get(compute), {"big": True})
        self.assertEqual(grian_cache._LOCAL.get("k"), {"big": True})

    def test_waiter_in_the_holders_process_finds_the_moved_value(self):
        # a holder in this process moved the value while we waited
        cache.add("k:lock", "other", 30)
        compute = mock.Mock(return_value={"big": "mine"})

        def holder_moves_value(_seconds):
            grian_cache._LOCAL.set("k", {"big": "theirs"}, 30)
            cache.set("k:local", True, 600)
            cache.delete("k:lock")

        with mock.patch(f"{MODULE}._sleep", side_effect=holder_moves_value):
            self.assertEqual(self.get(compute), {"big": "theirs"})
        compute.assert_not_called()

    def test_holder_sees_the_move_after_winning_the_lock(self):
        compute = mock.Mock(return_value={"big": "mine"})
        real_add = cache.add

        def add_after_value_moved(key, *args, **kwargs):
            if key == "k:lock":
                grian_cache._LOCAL.set("k", {"big": "theirs"}, 30)
                cache.set("k:local", True, 30)
            return real_add(key, *args, **kwargs)

        with mock.patch.object(
            cache, "add", side_effect=add_after_value_moved
        ):
            self.assertEqual(self.get(compute), {"big": "theirs"})
        compute.assert_not_called()
        self.assertIsNone(cache.get("k:lock"))

    def test_marker_lasts_the_value_ttl(self):
        with (
            mock.patch.object(
                cache,
                "set_many",
                side_effect=self._refusing_set_many(cache.set_many),
            ) as set_many,
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.get(lambda: {"big": True}, ttl=25)
        ttls = {
            next(iter(c.args[0])): c.args[1] for c in set_many.call_args_list
        }
        # the shared cache is tried again after one TTL
        self.assertEqual(ttls["k:local"], 25)

    def test_unreadable_marker_uses_the_shared_cache(self):
        real_get = cache.get

        def get(key, *args, **kwargs):
            if key.endswith(":local"):
                raise OSError("timeout")
            return real_get(key, *args, **kwargs)

        with (
            mock.patch.object(cache, "get", side_effect=get),
            mock.patch.object(
                cache, "get_many", side_effect=OSError("timeout")
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.assertEqual(self.get(lambda: {"v": 1}), {"v": 1})
        self.assertEqual(cache.get("k"), {"v": 1})

    def _move_to_local(self):
        with (
            mock.patch.object(
                cache,
                "set_many",
                side_effect=self._refusing_set_many(cache.set_many),
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.get(lambda: {"big": "v1"}, ttl=25)
        # one TTL later: the marker and the value expired, the last copy
        # (ttl + LAST_GOOD_TTL) did not
        cache.delete("k:local")
        grian_cache._LOCAL.delete("k")

    def test_last_copy_survives_the_marker_while_refreshing(self):
        self._move_to_local()
        cache.add("k:lock", "other", 30)  # another request refreshes
        with (
            mock.patch(f"{MODULE}._sleep"),
            self.clock(0, 0.5, grian_cache.FRESH_WAIT_SECONDS),
        ):
            self.assertEqual(self.get(mock.Mock()), {"big": "v1"})

    def test_last_copy_survives_the_marker_after_a_failure(self):
        self._move_to_local()
        compute = mock.Mock(side_effect=RuntimeError("down"))
        with self.assertLogs(grian_cache.LOG, level="ERROR"):
            value = self.get(compute)
        self.assertEqual(value, {"big": "v1", "is_stale_copy": True})

    def test_too_large_value_is_never_sent_to_the_shared_cache(self):
        # pymemcache raises on oversized items, or hides the refusal with
        # ignore_exc=True: check the size before storing
        compute = mock.Mock(return_value={"rows": "x" * 500})
        with (
            mock.patch(f"{MODULE}._max_item_bytes", return_value=100),
            mock.patch.object(
                cache, "set_many", wraps=cache.set_many
            ) as set_many,
            self.assertLogs(grian_cache.LOG, level="WARNING") as logs,
        ):
            self.assertEqual(self.get(compute), {"rows": "x" * 500})
            self.assertEqual(self.get(compute), {"rows": "x" * 500})
        compute.assert_called_once()
        stored = [next(iter(c.args[0])) for c in set_many.call_args_list]
        self.assertNotIn("k", stored)
        self.assertNotIn("k:last", stored)
        self.assertIn("k:local", stored)
        self.assertIn("too large for the shared cache", "\n".join(logs.output))

    def test_size_limit_setting(self):
        with mock.patch(
            f"{MODULE}.config.get_cache_max_item_bytes", return_value=42
        ):
            self.assertEqual(grian_cache._max_item_bytes(), 42)
        with (
            mock.patch(
                f"{MODULE}.config.get_cache_max_item_bytes",
                side_effect=grian_cache.config.ConfigurationError("bad"),
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING") as logs,
        ):
            self.assertEqual(
                grian_cache._max_item_bytes(),
                grian_cache.config.DEFAULT_CACHE_MAX_ITEM_BYTES,
            )
        # one line, no traceback
        self.assertEqual(len(logs.records), 1)
        self.assertIsNone(logs.records[0].exc_info)

    def test_cache_hiding_its_failures_falls_back_to_the_process(self):
        # pymemcache with ignore_exc=True and memcached down: add() fails
        # as if a lock were held, and get() finds nothing
        compute = mock.Mock(return_value={"v": 1})
        with (
            mock.patch.object(cache, "add", return_value=False),
            mock.patch.object(cache, "get", return_value=None),
            mock.patch.object(cache, "get_many", return_value={}),
            mock.patch.object(cache, "set_many", return_value=[]),
            mock.patch(f"{MODULE}._sleep") as sleep,
            self.assertLogs(grian_cache.LOG, level="WARNING") as logs,
        ):
            self.assertEqual(self.get(compute), {"v": 1})
            self.assertEqual(self.get(compute), {"v": 1})
        compute.assert_called_once()
        # detected without waiting for LOCK_WAIT_SECONDS
        self.assertLessEqual(sleep.call_count, 1)
        self.assertIn("does not keep locks", "\n".join(logs.output))

    def test_silently_refused_write_moves_to_the_process(self):
        # pymemcache with ignore_exc=True: a refused write (out of memory,
        # a lower -I) returns no failed keys but stores nothing
        real = cache.set_many

        def drop_value(data, ttl):
            real(
                {k: v for k, v in data.items() if k not in ("k", "k:last")},
                ttl,
            )
            return []

        compute = mock.Mock(return_value={"v": 1})
        with (
            mock.patch.object(cache, "set_many", side_effect=drop_value),
            self.assertLogs(grian_cache.LOG, level="WARNING") as logs,
        ):
            self.assertEqual(self.get(compute), {"v": 1})
            self.assertEqual(self.get(compute), {"v": 1})
        compute.assert_called_once()
        self.assertEqual(grian_cache._LOCAL.get("k"), {"v": 1})
        self.assertTrue(cache.get("k:local"))
        self.assertIn("Could not store k", "\n".join(logs.output))

    def test_single_released_lock_is_not_a_broken_cache(self):
        # the lock is released between our add() and get() once: normal
        # contention, keep waiting for the value
        cache.add("k:lock", "other", 30)
        real_get = cache.get
        reads = {"lock": 0}

        def get(key, *args, **kwargs):
            if key == "k:lock":
                reads["lock"] += 1
                if reads["lock"] == 1:
                    return None  # released just now...
            return real_get(key, *args, **kwargs)

        def sleep(_seconds):
            # ...and the next holder finishes
            cache.set("k", {"v": "theirs"}, 30)

        compute = mock.Mock(return_value={"v": "mine"})
        with (
            mock.patch.object(cache, "get", side_effect=get),
            mock.patch(f"{MODULE}._sleep", side_effect=sleep),
        ):
            self.assertEqual(self.get(compute), {"v": "theirs"})
        compute.assert_not_called()
        self.assertIsNone(grian_cache._LOCAL.get("k"))

    def test_phantom_count_resets_on_a_real_lock(self):
        # phantom, a real lock, phantom: normal contention, not a broken
        # cache (two phantoms in a row are needed)
        real_get = cache.get
        lock_reads = iter([None, "other", None])
        polls = {"n": 0}

        def get(key, *args, **kwargs):
            if key == "k:lock":
                return next(lock_reads, "other")
            return real_get(key, *args, **kwargs)

        def sleep(_seconds):
            polls["n"] += 1
            if polls["n"] == 3:
                cache.set("k", {"v": "theirs"}, 30)

        compute = mock.Mock(return_value={"v": "mine"})
        with (
            mock.patch.object(cache, "add", return_value=False),
            mock.patch.object(cache, "get", side_effect=get),
            mock.patch(f"{MODULE}._sleep", side_effect=sleep),
        ):
            self.assertEqual(self.get(compute), {"v": "theirs"})
        compute.assert_not_called()
        self.assertIsNone(grian_cache._LOCAL.get("k"))

    def test_valid_local_copy_is_used_after_the_marker_expires(self):
        grian_cache._LOCAL.set("k", {"big": "mine"}, 30)
        compute = mock.Mock(return_value={"big": "new"})
        with mock.patch(f"{MODULE}._sleep") as sleep:
            self.assertEqual(self.get(compute), {"big": "mine"})
        compute.assert_not_called()
        sleep.assert_not_called()

    def test_moving_a_value_removes_the_older_shared_copy(self):
        self.get(lambda: {"v": 1}, ttl=25)  # small enough, shared
        cache.delete("k")  # expired; the shared last copy is still there
        with (
            mock.patch.object(
                cache,
                "set_many",
                side_effect=self._refusing_set_many(cache.set_many),
            ),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.get(lambda: {"big": 2}, ttl=25)
        self.assertIsNone(cache.get("k:last"))
        # marker expired and the refresh fails: the newer copy is served
        cache.delete("k:local")
        grian_cache._LOCAL.delete("k")
        with self.assertLogs(grian_cache.LOG, level="ERROR"):
            value = self.get(mock.Mock(side_effect=RuntimeError("down")))
        self.assertEqual(value, {"big": 2, "is_stale_copy": True})

    def test_per_process_waiter_falls_back_to_the_shared_copy(self):
        cache.set("k:local", True, 600)  # another process moved the value
        cache.set("k:last", {"v": "shared"}, 600)
        grian_cache._LOCAL.add("k:lock", "other-thread", 30)
        with (
            mock.patch(f"{MODULE}._sleep"),
            self.clock(0, 0.5, grian_cache.FRESH_WAIT_SECONDS),
        ):
            self.assertEqual(self.get(mock.Mock()), {"v": "shared"})

    def test_failed_marker_write_is_harmless(self):
        real = cache.set_many

        def set_many(data, ttl):
            if any(k.endswith(":local") for k in data):
                raise OSError("down")
            return self._refusing_set_many(real)(data, ttl)

        compute = mock.Mock(return_value={"big": True})
        with (
            mock.patch.object(cache, "set_many", side_effect=set_many),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.assertEqual(self.get(compute), {"big": True})
        # this process still has its copy
        self.assertEqual(grian_cache._LOCAL.get("k"), {"big": True})

    def test_other_process_computes_its_own_copy_once(self):
        cache.set("k:local", True, 600)  # set by another process
        compute = mock.Mock(return_value={"big": True})
        self.assertEqual(self.get(compute), {"big": True})
        self.assertEqual(self.get(compute), {"big": True})
        compute.assert_called_once()

    def test_per_process_waiter_with_a_copy_does_not_block(self):
        cache.set("k:local", True, 600)
        grian_cache._LOCAL.set("k:last", {"big": "old"}, 600)
        grian_cache._LOCAL.add("k:lock", "other-thread", 30)
        with (
            mock.patch(f"{MODULE}._sleep") as sleep,
            self.clock(0, 0.5, grian_cache.FRESH_WAIT_SECONDS),
        ):
            value = self.get(mock.Mock())
        self.assertEqual(value, {"big": "old"})
        self.assertEqual(sleep.call_count, 1)

    def test_per_process_failure_is_remembered(self):
        cache.set("k:local", True, 600)
        compute = mock.Mock(side_effect=RuntimeError("down"))
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        self.assertRaises(grian_cache.ComputationFailed, self.get, compute)
        compute.assert_called_once()

    def test_per_process_failure_serves_the_last_copy_marked_stale(self):
        cache.set("k:local", True, 600)
        grian_cache._LOCAL.set("k:last", {"big": "old"}, 600)
        compute = mock.Mock(side_effect=RuntimeError("down"))
        with self.assertLogs(grian_cache.LOG, level="ERROR"):
            value = self.get(compute)
        self.assertEqual(value, {"big": "old", "is_stale_copy": True})

    def test_per_process_threads_compute_once(self):
        cache.set("k:local", True, 600)
        waiting = set()
        all_waiting = threading.Event()
        guard = threading.Lock()
        real_sleep = time.sleep

        def sleep(seconds):
            with guard:
                waiting.add(threading.get_ident())
                if len(waiting) >= 2:
                    all_waiting.set()
            real_sleep(seconds)

        calls = []

        def compute():
            calls.append(1)
            # only return once both other threads are waiting
            self.assertTrue(all_waiting.wait(5))
            return {"big": True}

        results = []
        with mock.patch(f"{MODULE}._sleep", side_effect=sleep):
            threads = [
                threading.Thread(
                    target=lambda: results.append(self.get(compute))
                )
                for _ in range(3)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [{"big": True}] * 3)


class LocalStoreTests(base.TestCase):
    def setUp(self):
        super().setUp()
        self.store = grian_cache._LocalStore()

    def test_values_are_copies(self):
        value = {"rows": [{"a": 1}]}
        self.store.set("k", value, 30)
        value["rows"][0]["a"] = 2  # the caller keeps changing its dict
        got = self.store.get("k")
        got["rows"][0]["a"] = 3  # a reader changes what it got
        self.assertEqual(self.store.get("k"), {"rows": [{"a": 1}]})

    def test_value_and_last_copy_share_one_pickle(self):
        with mock.patch(
            f"{MODULE}.pickle.dumps", wraps=grian_cache.pickle.dumps
        ) as dumps:
            self.store.set_value({"v": 1}, {"k": 30, "k:last": 630})
        dumps.assert_called_once()
        self.assertEqual(self.store.get("k"), self.store.get("k:last"))

    def test_expiry_and_purge_on_write(self):
        with mock.patch(f"{MODULE}._store_now", return_value=1000):
            self.store.set("old", {"v": 1}, 10)
        with mock.patch(f"{MODULE}._store_now", return_value=1011):
            self.assertIsNone(self.store.get("old"))
            self.store.set("old2", {"v": 1}, 1)
        with mock.patch(f"{MODULE}._store_now", return_value=1013):
            self.store.set("new", {"v": 2}, 10)
        # expired entries nobody reads again are dropped on write
        self.assertEqual(list(self.store._data), ["new"])

    def test_add_and_touch(self):
        with mock.patch(f"{MODULE}._store_now", return_value=1000):
            self.assertTrue(self.store.add("lock", "a", 10))
            self.assertFalse(self.store.add("lock", "b", 10))
            self.store.touch("lock", 30)
        with mock.patch(f"{MODULE}._store_now", return_value=1020):
            self.assertFalse(self.store.add("lock", "b", 10))
        with mock.patch(f"{MODULE}._store_now", return_value=1031):
            self.assertTrue(self.store.add("lock", "b", 10))


class BrokenCacheTests(_CacheTestCase):
    def test_repeated_cache_errors_are_rate_limited(self):
        with (
            mock.patch.object(cache, "get", side_effect=OSError("down")),
            self.assertLogs(grian_cache.LOG, level="DEBUG") as logs,
        ):
            with mock.patch(f"{MODULE}._now", return_value=1000):
                grian_cache._SHARED.get("a")
                grian_cache._SHARED.get("b")
            with mock.patch(
                f"{MODULE}._now",
                return_value=1000 + grian_cache.WARNING_INTERVAL_SECONDS,
            ):
                grian_cache._SHARED.get("c")
        levels = [r.levelname for r in logs.records]
        self.assertEqual(levels, ["WARNING", "DEBUG", "WARNING"])

    def test_unreachable_cache_still_returns_the_value(self):
        broken = OSError("memcached down")
        with (
            mock.patch.object(cache, "get", side_effect=broken),
            mock.patch.object(cache, "set_many", side_effect=broken),
            mock.patch.object(cache, "add", side_effect=broken),
            mock.patch.object(cache, "delete", side_effect=broken),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.assertEqual(self.get(lambda: {"v": 1}), {"v": 1})

    def test_cache_failing_after_compute_keeps_the_value(self):
        real_get = cache.get
        state = {"computed": False}

        def get(key, *args, **kwargs):
            if state["computed"]:
                raise OSError("memcached went away")
            return real_get(key, *args, **kwargs)

        def compute():
            state["computed"] = True
            return {"v": 1}

        with (
            mock.patch.object(cache, "get", side_effect=get),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self.assertEqual(self.get(compute), {"v": 1})
        # the owner could not be checked, so the lock is left to expire
        self.assertIsNotNone(cache.get("k:lock"))


class _FakeEvent:
    """Event whose wait() returns False ``beats`` times, then True."""

    def __init__(self, beats):
        self.beats = beats

    def wait(self, _timeout):
        self.beats -= 1
        return self.beats < 0

    def set(self):
        pass


class HeartbeatTests(_CacheTestCase):
    def _run(self, store, beats, owner="tok"):
        heartbeat = grian_cache._Heartbeat(store, "k:lock", owner)
        heartbeat._stop = _FakeEvent(beats)
        heartbeat._run()

    def test_renews_its_own_lock_and_closes_the_connection(self):
        store = mock.MagicMock()
        store.get.return_value = "tok"
        self._run(store, beats=3)
        self.assertEqual(
            store.touch.call_args_list,
            [mock.call("k:lock", grian_cache.LOCK_SECONDS)] * 3,
        )
        store.close.assert_called_once()

    def test_stops_when_the_lock_changed_hands(self):
        store = mock.MagicMock()
        store.get.return_value = "someone-else"
        self._run(store, beats=3)
        store.touch.assert_not_called()
        store.close.assert_called_once()

    def test_stops_after_the_maximum_compute_time(self):
        store = mock.MagicMock()
        store.get.return_value = "tok"
        readings = iter([0, 10, grian_cache.MAX_COMPUTE_SECONDS + 1])
        with (
            mock.patch(f"{MODULE}._now", side_effect=lambda: next(readings)),
            self.assertLogs(grian_cache.LOG, level="WARNING"),
        ):
            self._run(store, beats=5)
        # renewed once (at 10 s), then gave up
        store.touch.assert_called_once()
        store.close.assert_called_once()

    def test_read_error_skips_one_beat(self):
        store = mock.MagicMock()
        # an error, then a miss (how ignore_exc=True reports errors)
        store.get.side_effect = [grian_cache._ERROR, None, "tok", "tok"]
        self._run(store, beats=4)
        # the lock is still renewed after the transient errors
        self.assertEqual(store.touch.call_count, 2)

    def test_cache_errors_in_the_heartbeat_are_logged(self):
        cache.set("k:lock", "tok", 30)
        with (
            mock.patch.object(cache, "touch", side_effect=OSError("down")),
            mock.patch.object(cache, "close", side_effect=OSError("down")),
            self.assertLogs(grian_cache.LOG, level="DEBUG") as logs,
        ):
            self._run(grian_cache._SHARED, beats=1)
        output = "\n".join(logs.output)
        self.assertIn("WARNING:grian_ui.cache:Could not renew k:lock", output)
        # closing the thread's connection fails quietly
        self.assertIn(
            "DEBUG:grian_ui.cache:Could not close the cache connection", output
        )

    def test_lock_is_renewed_while_computing(self):
        # real threads, with a wide margin: renewals every 0.05 s keep a
        # 1 s lock alive through a 2 s computation
        seen = {}

        def slow_compute():
            time.sleep(2)
            seen["lock"] = cache.get("k:lock")
            return {"v": 1}

        with (
            mock.patch(f"{MODULE}.LOCK_SECONDS", 1),
            mock.patch(f"{MODULE}.HEARTBEAT_SECONDS", 0.05),
        ):
            self.assertEqual(self.get(slow_compute), {"v": 1})
        self.assertIsNotNone(seen["lock"])
        self.assertIsNone(cache.get("k:lock"))


class ConcurrencyTests(_CacheTestCase):
    def _run_concurrently(self, compute, n=3):
        results = []

        def worker():
            start = time.monotonic()
            try:
                outcome = grian_cache.get_or_compute("k", 30, compute)
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
            results.append((outcome, time.monotonic() - start))

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _overlapping(self, result, waiters=2):
        """
        A compute that only returns once the waiters are polling, so the
        test proves they waited instead of running one after another.
        """
        waiting = set()
        all_waiting = threading.Event()
        guard = threading.Lock()
        real_sleep = time.sleep

        def sleep(seconds):
            with guard:
                waiting.add(threading.get_ident())
                if len(waiting) >= waiters:
                    all_waiting.set()
            real_sleep(seconds)

        calls = []

        def compute():
            calls.append(1)
            # distinct threads, not just polls
            self.assertTrue(all_waiting.wait(5))
            if isinstance(result, Exception):
                raise result
            return result

        patcher = mock.patch(f"{MODULE}._sleep", side_effect=sleep)
        return patcher, compute, calls

    def test_concurrent_requests_compute_once(self):
        patcher, compute, calls = self._overlapping({"v": 1})
        with patcher:
            results = self._run_concurrently(compute)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r[0] for r in results], [{"v": 1}] * 3)

    def test_concurrent_failure_computes_once_and_fails_fast(self):
        patcher, compute, calls = self._overlapping(RuntimeError("down"))
        with patcher, self.assertLogs(grian_cache.LOG, level="ERROR"):
            results = self._run_concurrently(compute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            sorted(r[0] for r in results), ["ComputationFailed"] * 3
        )
        # nobody waits for the full LOCK_WAIT_SECONDS
        self.assertLess(
            max(r[1] for r in results), grian_cache.LOCK_WAIT_SECONDS
        )
