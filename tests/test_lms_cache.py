"""Tests for the LMS assignment TTL cache.

Validates that ``collect_lms_assignments_for_user`` is cached per-user,
the TTL works, and ``invalidate_lms_cache_for_user`` actually busts
the cache. We test the cache primitives directly so the test doesn't
need a real LMS connection.
"""

from __future__ import annotations

import time

import pytest

# These are module-private helpers on App.py — we deliberately import them
# to exercise the cache invariants. If they move, this test should move too.
from App import (
    _LMS_CACHE,
    _LMS_CACHE_TTL_SECONDS,
    _lms_cache_get,
    _lms_cache_put,
    invalidate_lms_cache_for_user,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a clean cache."""
    _LMS_CACHE.clear()
    yield
    _LMS_CACHE.clear()


def test_cache_miss_returns_none():
    assert _lms_cache_get(123) is None


def test_cache_put_then_get_round_trip():
    payload = [{"title": "Read chapter 7", "source": "canvas"}]
    _lms_cache_put(42, payload)
    cached = _lms_cache_get(42)
    assert cached is not None
    assert cached[0]["title"] == "Read chapter 7"


def test_cache_isolated_per_user():
    _lms_cache_put(1, [{"title": "A"}])
    _lms_cache_put(2, [{"title": "B"}])
    assert _lms_cache_get(1)[0]["title"] == "A"
    assert _lms_cache_get(2)[0]["title"] == "B"


def test_invalidate_busts_cache():
    _lms_cache_put(99, [{"title": "X"}])
    assert _lms_cache_get(99) is not None
    invalidate_lms_cache_for_user(99)
    assert _lms_cache_get(99) is None


def test_invalidate_other_user_does_not_touch_mine():
    _lms_cache_put(1, [{"title": "A"}])
    _lms_cache_put(2, [{"title": "B"}])
    invalidate_lms_cache_for_user(1)
    assert _lms_cache_get(1) is None
    assert _lms_cache_get(2) is not None


def test_cache_ttl_expires():
    """Past-TTL entries should be treated as misses (and self-evicted)."""
    # Insert a deliberately-stale entry by reaching into the cache dict.
    # The helper compares `time.time() - ts > TTL`, so any `ts` far enough
    # in the past will trigger eviction.
    _LMS_CACHE[7] = (0.0, [{"title": "stale"}])  # epoch — way past TTL
    assert _lms_cache_get(7) is None
    assert 7 not in _LMS_CACHE  # self-evicted


def test_ttl_is_at_least_one_minute():
    """Sanity: a too-aggressive TTL would defeat the whole purpose."""
    assert _LMS_CACHE_TTL_SECONDS >= 60


def test_invalidate_on_unknown_user_is_safe():
    """invalidate_lms_cache_for_user must not raise on unknown user."""
    invalidate_lms_cache_for_user(123456789)  # no entry → should no-op
