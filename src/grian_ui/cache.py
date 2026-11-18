#
# SPDX-License-Identifier: Apache-2.0

"""
Shared, single-flight caching for dashboard data.

Dashboards are made of HTMX partials that refresh at the same moment, and
several administrators may watch the same region. Fleet-wide data (one
snapshot of every host or instance) is therefore computed once and shared
through the Horizon cache. A lock keeps the partials of one refresh from
all missing the cache and querying the backend in parallel, and the last
good copy is served while a slow refresh runs or when a refresh fails.

This needs a cache shared by every Horizon process, such as memcached (the
Horizon default). With a per-process cache (``LocMemCache``) each process
computes its own copy, and the partials of one page may show different
snapshots.

Values too large for the shared cache (memcached stores at most 1 MiB per
item by default; see ``GRIAN_PLUGIN['cache_max_item_bytes']``) or that it
fails to store are kept in a per-process store instead, with the same
rules: computed once per process, same waits, same failure handling.

pymemcache can hide failures: with ``ignore_exc=True`` refused writes look
stored and, with memcached down, every ``add`` fails as if a lock were
held; with ``ignore_exc=False`` its HashClient does the same for
``retry_timeout`` after a connection error. Sizes are therefore checked
before storing, values are read back once after storing, and a lock that
cannot be taken although none exists is treated as an unusable cache.
"""

import hashlib
import logging
import pickle  # nosec B403: only values this module stored itself
import re
import threading
import time
import uuid

from django.core.cache import cache

from grian_ui import config


LOG = logging.getLogger(__name__)

#: Lifetime of the lock taken by the request computing a value. The holder
#: renews it every HEARTBEAT_SECONDS while it computes, for at most
#: MAX_COMPUTE_SECONDS, so a slow computation keeps it, while a holder that
#: died or hangs loses it (at the latest MAX_COMPUTE_SECONDS + LOCK_SECONDS
#: after it started).
LOCK_SECONDS = 30
HEARTBEAT_SECONDS = 10
MAX_COMPUTE_SECONDS = 120
#: How long other requests wait for the value being computed before they
#: use the last good copy. Short computations then give every partial of a
#: refresh the same value.
FRESH_WAIT_SECONDS = 1
#: How long requests without any copy wait before giving up with
#: StillComputing. Kept short: waiting blocks a Horizon worker thread.
LOCK_WAIT_SECONDS = 5
#: How long a failed computation is remembered, so waiters and the next
#: partials of the same refresh do not retry it.
FAILURE_TTL = 5
#: How long the last good copy of a value outlives the value itself.
LAST_GOOD_TTL = 600
_LOCK_POLL_SECONDS = 0.1
#: Clock and sleep of the waits and the heartbeat.
_now = time.monotonic
_sleep = time.sleep


class DataUnavailable(Exception):
    """The shared data is not available (base class)."""


class StillComputing(DataUnavailable):
    """
    Another request is computing the value and there is no copy yet.

    Transient: callers should show a "loading" state and retry shortly.
    """


class ComputationFailed(DataUnavailable):
    """The last computation failed and there is no copy to fall back to."""


#: Each kind of cache error is logged as a warning at most this often (the
#: others at debug level), so an unreachable memcached does not flood the
#: logs with one line per operation.
WARNING_INTERVAL_SECONDS = 60
_last_warnings = {}
_warnings_guard = threading.Lock()


def _warn(message, *args):
    now = _now()
    with _warnings_guard:
        due = now - _last_warnings.get(message, -WARNING_INTERVAL_SECONDS)
        if due >= WARNING_INTERVAL_SECONDS:
            _last_warnings[message] = now
            level = logging.WARNING
        else:
            level = logging.DEBUG
    LOG.log(level, message, *args)


#: Returned by _SharedStore.get(..., on_error=_ERROR) when the cache fails.
_ERROR = object()


class _SharedStore:
    """The Horizon cache. Errors are logged and never raised."""

    def get(self, key, on_error=None):
        try:
            return cache.get(key)
        except Exception as exc:
            _warn("Could not read %s from the cache: %s", key, exc)
            return on_error

    def get_many(self, keys):
        try:
            return cache.get_many(keys)
        except Exception as exc:
            _warn("Could not read %s from the cache: %s", keys, exc)
            return {}

    def set(self, key, value, ttl):
        """True if stored, False if refused, None if the cache failed."""
        try:
            # set_many reports keys some backends refused; pymemcache
            # raises instead, or hides the failure (see the module
            # docstring)
            failed = cache.set_many({key: value}, ttl)
        except Exception as exc:
            _warn("Could not cache %s: %s", key, exc)
            return None
        return not failed

    def add(self, key, value, ttl):
        """``cache.add``; None when the cache is not usable."""
        try:
            return cache.add(key, value, ttl)
        except Exception as exc:
            _warn("Could not lock %s in the cache: %s", key, exc)
            return None

    def delete(self, key):
        try:
            cache.delete(key)
        except Exception as exc:
            _warn("Could not delete %s from the cache: %s", key, exc)

    def touch(self, key, ttl):
        try:
            cache.touch(key, ttl)
        except Exception as exc:
            _warn("Could not renew %s: %s", key, exc)

    def close(self):
        """Close this thread's connection (heartbeat threads)."""
        try:
            cache.close()
        except Exception as exc:
            # nothing to undo: the connection is dropped with the thread
            LOG.debug("Could not close the cache connection: %s", exc)


#: Clock of the per-process store (separate from the wait timing).
_store_now = time.monotonic


class _LocalStore:
    """
    Per-process store with expiry, for values the shared cache refuses.

    Values are pickled, like a real cache, so callers that modify what they
    get never change what other threads see.
    """

    def __init__(self):
        self._data = {}
        self._guard = threading.Lock()

    def _live(self, key, now):
        entry = self._data.get(key)
        if entry is not None and now >= entry[0]:
            del self._data[key]
            return None
        return entry

    def get(self, key, on_error=None):
        with self._guard:
            entry = self._live(key, _store_now())
        if entry is None:
            return None
        # only bytes this store pickled itself, in this process
        return pickle.loads(entry[1])  # noqa: S301  # nosec B301

    def set(self, key, value, ttl):
        return self.set_value(value, {key: ttl})

    def set_value(self, value, ttls):
        """Store one value under several keys (pickled once)."""
        data = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        with self._guard:
            now = _store_now()
            # drop expired entries so values nobody reads do not pile up
            for stale in [k for k, e in self._data.items() if now >= e[0]]:
                del self._data[stale]
            for key, ttl in ttls.items():
                self._data[key] = (now + ttl, data)
        return True

    def add(self, key, value, ttl):
        data = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        with self._guard:
            now = _store_now()
            if self._live(key, now) is not None:
                return False
            self._data[key] = (now + ttl, data)
            return True

    def delete(self, key):
        with self._guard:
            self._data.pop(key, None)

    def touch(self, key, ttl):
        with self._guard:
            now = _store_now()
            entry = self._live(key, now)
            if entry is not None:
                self._data[key] = (now + ttl, entry[1])

    def clear(self):
        with self._guard:
            self._data.clear()

    def close(self):
        pass


_SHARED = _SharedStore()
_LOCAL = _LocalStore()


class _Heartbeat:
    """
    Renew a lock while its holder computes.

    Only the holder's own lock is renewed (it stops if the lock expired and
    someone else took it), and for at most MAX_COMPUTE_SECONDS.
    """

    def __init__(self, store, key, token):
        self._store = store
        self._key = key
        self._token = token
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        start = _now()
        try:
            while not self._stop.wait(HEARTBEAT_SECONDS):
                if _now() - start > MAX_COMPUTE_SECONDS:
                    LOG.warning(
                        "Computing %s takes longer than %s seconds, no "
                        "longer renewing its lock",
                        self._key,
                        MAX_COMPUTE_SECONDS,
                    )
                    return
                # not atomic: at worst, another holder's lock gets one
                # extra renewal before this heartbeat stops
                holder = self._store.get(self._key, on_error=_ERROR)
                if holder is _ERROR or holder is None:
                    # a transient cache error (clients with ignore_exc=True
                    # report it as a miss): try the next beat; touching a
                    # missing key does nothing
                    continue
                if holder != self._token:
                    return
                self._store.touch(self._key, LOCK_SECONDS)
        finally:
            self._store.close()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def _key_part(text):
    """Readable, key-safe and unique form of a free-form cache key part."""
    text = str(text)
    # "-" only ever separates the digest, so no text can collide with the
    # hashed form of another
    readable = re.sub(r"[^A-Za-z0-9_.]", "_", text)[:40]
    if readable == text:
        return readable
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{readable}-{digest}"


def _region(request):
    return _key_part(
        getattr(request.user, "services_region", None) or "default"
    )


def cache_key(request, namespace, name, version=1):
    """
    Cache key for data shared by every administrator of a region.

    The value is shared by every user of the region, so only cache data
    computed for users who may see all of it: the Telemetry panels are
    admin-only and query all projects.

    Args:
        namespace: the dashboard, e.g. ``"compute_host_health"``
        name: the value, e.g. ``"snapshot"``
        version: bump when the format of the cached value changes, so values
            written by an older version (memcached survives Horizon
            restarts) are never read
    """
    return (
        f"grian_ui:{_key_part(namespace)}:v{int(version)}:{_key_part(name)}:"
        f"{config.get_datasource_type()}:{_region(request)}"
    )


def _checked(key, value):
    """
    Check the compute() contract; return the pickled size.

    The value is pickled again when it is stored (once per computation,
    not per request): the price of knowing its size before memcached sees
    it, since some clients hide refusals.
    """
    if not isinstance(value, dict):
        raise TypeError(
            f"get_or_compute({key!r}): compute() must return a dict, "
            f"got {type(value).__name__}"
        )
    try:
        return len(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))
    except Exception as exc:
        raise TypeError(
            f"get_or_compute({key!r}): compute() must return a picklable "
            f"dict: {exc}"
        ) from exc


def get_or_compute(key, ttl, compute):
    """
    Return the cached dict for ``key``, computing it at most once at a time.

    ``compute`` must return a picklable dict. The request that takes the lock
    (``cache.add``, atomic in memcached and the local memory backend)
    computes the value; the others never query the backend in parallel:

    * they wait up to FRESH_WAIT_SECONDS for the new value, then return
      the last good copy as it is (stale-while-revalidate);
    * without a copy, they wait up to LOCK_WAIT_SECONDS, then raise
      StillComputing (show "loading" and retry);
    * after a failed computation (remembered for FAILURE_TTL), they return
      the last good copy marked with ``"is_stale_copy": True`` (show "could
      not be refreshed"), or raise ComputationFailed. So does the holder,
      with the original exception as the cause. Until a computation
      succeeds again (or ``ttl + LAST_GOOD_TTL`` passes), requests without
      a copy raise ComputationFailed rather than StillComputing while a
      retry runs.

    A copy served while a refresh runs is not marked, and can be up to
    ``ttl + LAST_GOOD_TTL`` seconds old: values should carry their own
    timestamp (e.g. ``fetched_at``) if their age matters.

    The lock holds a random token, is renewed while the holder computes
    (for at most MAX_COMPUTE_SECONDS) and only released by its owner
    (barring a lock that expires during the release, see _Flight.hold). If
    the holder dies or hangs, the lock expires and the next request takes
    it over. If the cache fails while the holder releases the lock, the lock
    is left to expire (LOCK_SECONDS). The last good copy is kept for
    ``ttl + LAST_GOOD_TTL`` seconds, also for values kept per process.

    Cache errors never fail the call. A value the shared cache refuses
    (too large, or memcached out of memory) moves to a per-process store
    with the same rules for ``ttl`` seconds (see the module docstring), and
    without a usable shared cache each process computes its own copy.
    """
    local_key = f"{key}:local"
    found = _SHARED.get_many([key, local_key])
    if found.get(local_key):
        return _single_flight(_LOCAL, key, ttl, compute)
    if found.get(key) is not None:
        return found[key]
    # a copy this process kept after the marker expired is still valid
    value = _LOCAL.get(key)
    if value is not None:
        return value
    return _single_flight(_SHARED, key, ttl, compute)


def _single_flight(store, key, ttl, compute):
    return _Flight(store, key, ttl, compute).run()


class _Flight:
    """One get_or_compute call against one store (see get_or_compute)."""

    def __init__(self, store, key, ttl, compute):
        self.store, self.key, self.ttl, self.compute = store, key, ttl, compute
        self.lock_key, self.failed_key = f"{key}:lock", f"{key}:failed"
        self.last_key, self.local_key = f"{key}:last", f"{key}:local"
        self.failing_key = f"{key}:failing"

    def moved_to_local(self):
        return self.store is _SHARED and _SHARED.get(self.local_key)

    def on_local(self):
        return _single_flight(_LOCAL, self.key, self.ttl, self.compute)

    def last_copy(self):
        last = self.store.get(self.last_key)
        if last is None:
            # a value that moved between stores keeps its last copy in the
            # other one (the shared copy is removed when a value moves to
            # the per-process store, so it is never older than this one)
            other = _LOCAL if self.store is _SHARED else _SHARED
            last = other.get(self.last_key)
        return last

    def after_failure(self):
        last = self.last_copy()
        if last is not None:
            return dict(last, is_stale_copy=True)
        raise ComputationFailed("the last computation failed")

    def run(self):
        value = self.store.get(self.key)
        if value is not None:
            return value
        start = _now()
        phantom_locks = 0
        while True:
            if self.moved_to_local():
                # the holder found the value too large for the shared cache
                return self.on_local()
            if self.store.get(self.failed_key):
                return self.after_failure()
            token = uuid.uuid4().hex
            locked = self.store.add(self.lock_key, token, LOCK_SECONDS)
            if locked is None:
                # the shared cache cannot coordinate requests
                return self.on_local()
            if locked:
                return self.hold(token)
            if self.store is _SHARED and self.store.get(self.lock_key) is None:
                # "locked" but no lock: a cache that hides its failures
                phantom_locks += 1
                if phantom_locks >= 2:
                    _warn(
                        "The shared cache does not keep locks (%s); "
                        "computing per Horizon process",
                        self.lock_key,
                    )
                    return self.on_local()
            else:
                phantom_locks = 0
            value = self.wait_once(_now() - start)
            if value is not None:
                return value

    def hold(self, token):
        """Compute the value while holding the lock."""
        try:
            with _Heartbeat(self.store, self.lock_key, token):
                # the previous holder may have finished, failed or moved
                # the value since our last check
                value = self.store.get(self.key)
                if value is not None:
                    return value
                if self.store.get(self.failed_key):
                    return self.after_failure()
                if self.moved_to_local():
                    return self.on_local()
                return self.compute_and_store()
        finally:
            # memcached has no compare-and-delete; checking first keeps a
            # holder whose lock expired from releasing the next one, except
            # if it expires between the get and the delete (the heartbeat
            # keeps it alive until then, so only after MAX_COMPUTE_SECONDS)
            if self.store.get(self.lock_key) == token:
                self.store.delete(self.lock_key)

    def compute_and_store(self):
        try:
            value = self.compute()
        except Exception as exc:
            self.store.set(self.failed_key, True, FAILURE_TTL)
            self.store.set(self.failing_key, True, self.ttl + LAST_GOOD_TTL)
            last = self.last_copy()
            if last is None:
                LOG.exception("Error computing %s", self.key)
                raise ComputationFailed(
                    f"computing {self.key} failed: {exc}"
                ) from exc
            LOG.exception(
                "Error refreshing %s, serving the last good copy", self.key
            )
            return dict(last, is_stale_copy=True)
        try:
            size = _checked(self.key, value)
        except TypeError:
            # a broken compute(): waiters must not all run it again
            self.store.set(self.failed_key, True, FAILURE_TTL)
            self.store.set(self.failing_key, True, self.ttl + LAST_GOOD_TTL)
            raise
        _store(self.store, self.key, self.ttl, value, size)
        self.store.delete(self.failing_key)
        return value

    def wait_once(self, waited):
        """
        Wait one poll for another request's value.

        Returns the value, the last copy once FRESH_WAIT_SECONDS passed, or
        None to keep waiting; raises when there is nothing to wait for.
        """
        if waited >= FRESH_WAIT_SECONDS:
            last = self.last_copy()
            if last is not None:
                # being refreshed by another request: not a failure
                return last
            if self.store.get(self.failing_key):
                # retrying after a failure: report the failure, not
                # "loading", until a computation succeeds
                raise ComputationFailed("the last computation failed")
            if waited >= LOCK_WAIT_SECONDS:
                raise StillComputing("still being computed by another request")
        _sleep(_LOCK_POLL_SECONDS)
        return self.store.get(self.key)


def _max_item_bytes():
    try:
        return config.get_cache_max_item_bytes()
    except config.ConfigurationError as exc:
        _warn("%s; using the default", exc)
        return config.DEFAULT_CACHE_MAX_ITEM_BYTES


def _store(store, key, ttl, value, size):
    last_key = f"{key}:last"
    ttls = {key: ttl, last_key: ttl + LAST_GOOD_TTL}
    if store is _LOCAL:
        _LOCAL.set_value(value, ttls)
        return
    limit = _max_item_bytes()
    if size > limit:
        _warn(
            "%s is too large for the shared cache (%s > %s bytes); caching "
            "it per Horizon process for %s seconds",
            key,
            size,
            limit,
            ttl,
        )
    elif store.set(key, value, ttl) and store.get(key) is not None:
        # read back once per computation: clients with ignore_exc=True
        # report refused writes (out of memory, a lower -I) as stored
        store.set(last_key, value, ttl + LAST_GOOD_TTL)
        return
    else:
        _warn(
            "Could not store %s in the shared cache; caching it per Horizon "
            "process for %s seconds",
            key,
            ttl,
        )
    # keep it in this process for a while, and tell the other processes to
    # do the same
    _LOCAL.set_value(value, ttls)
    # an older copy left in the shared cache must not hide this one
    _SHARED.delete(last_key)
    _SHARED.set(f"{key}:local", True, ttl)
