"""Tests for haute._lru_cache — LRUCache with optional TTL."""

from __future__ import annotations

import threading
import time

import pytest

from haute._lru_cache import LRUCache


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestLRUCacheInit:
    def test_default_max_size(self) -> None:
        cache: LRUCache[str, int] = LRUCache()
        assert cache._max_size == 128

    def test_custom_max_size(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=5)
        assert cache._max_size == 5

    def test_zero_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            LRUCache(max_size=0)

    def test_negative_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            LRUCache(max_size=-1)

    def test_ttl_stored(self) -> None:
        cache: LRUCache[str, int] = LRUCache(ttl=30.0)
        assert cache._ttl == 30.0

    def test_ttl_default_none(self) -> None:
        cache: LRUCache[str, int] = LRUCache()
        assert cache._ttl is None


# ---------------------------------------------------------------------------
# Basic get / put
# ---------------------------------------------------------------------------


class TestGetPut:
    def test_put_and_get(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_miss_returns_none(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        assert cache.get("nonexistent") is None

    def test_overwrite_existing_key(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        cache.put("a", 99)
        assert cache.get("a") == 99
        assert len(cache) == 1

    def test_multiple_keys(self) -> None:
        cache: LRUCache[str, str] = LRUCache(max_size=10)
        cache.put("x", "hello")
        cache.put("y", "world")
        assert cache.get("x") == "hello"
        assert cache.get("y") == "world"

    def test_tuple_keys(self) -> None:
        cache: LRUCache[tuple[str, float], str] = LRUCache(max_size=4)
        key = ("path", 1234.5)
        cache.put(key, "value")
        assert cache.get(key) == "value"


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


class TestEviction:
    def test_evicts_lru_when_full(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert len(cache) == 2

    def test_get_promotes_entry(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # promote "a" — "b" is now LRU
        cache.put("c", 3)  # should evict "b"
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_put_overwrite_promotes_entry(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)  # overwrite promotes "a"
        cache.put("c", 3)  # should evict "b"
        assert cache.get("a") == 10
        assert cache.get("b") is None

    def test_capacity_one(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=1)
        cache.put("a", 1)
        assert cache.get("a") == 1
        cache.put("b", 2)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert len(cache) == 1

    def test_many_evictions(self) -> None:
        cache: LRUCache[int, int] = LRUCache(max_size=3)
        for i in range(100):
            cache.put(i, i * 10)
        assert len(cache) == 3
        # Only the last 3 should remain
        assert cache.get(97) == 970
        assert cache.get(98) == 980
        assert cache.get(99) == 990


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


class TestTTL:
    def test_entry_expires_after_ttl(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=0.05)
        cache.put("k", 42)
        assert cache.get("k") == 42
        time.sleep(0.08)
        assert cache.get("k") is None

    def test_entry_valid_before_ttl(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", 42)
        assert cache.get("k") == 42

    def test_ttl_eviction_removes_from_data(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=0.05)
        cache.put("k", 1)
        time.sleep(0.08)
        cache.get("k")  # triggers lazy eviction
        assert len(cache) == 0

    def test_no_ttl_entries_never_expire(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        cache.put("k", 1)
        # Without TTL, entries persist indefinitely
        assert cache.get("k") == 1


# ---------------------------------------------------------------------------
# __contains__ / __len__ / __repr__ / clear
# ---------------------------------------------------------------------------


class TestDunderMethods:
    def test_contains_true(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        assert "a" in cache

    def test_contains_false(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        assert "missing" not in cache

    def test_contains_does_not_promote(self) -> None:
        """__contains__ should not promote entry (unlike get)."""
        cache: LRUCache[str, int] = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        _ = "a" in cache  # should NOT promote "a"
        cache.put("c", 3)  # should evict "a" (LRU)
        assert cache.get("a") is None

    def test_len(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        assert len(cache) == 0
        cache.put("a", 1)
        assert len(cache) == 1
        cache.put("b", 2)
        assert len(cache) == 2

    def test_repr(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=5, ttl=10.0)
        cache.put("a", 1)
        r = repr(cache)
        assert "max_size=5" in r
        assert "ttl=10.0" in r
        assert "entries=1" in r

    def test_clear(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None
        assert cache.get("b") is None


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_puts(self) -> None:
        """Multiple threads writing concurrently should not corrupt the cache."""
        cache: LRUCache[int, int] = LRUCache(max_size=50)
        errors: list[Exception] = []

        def writer(start: int) -> None:
            try:
                for i in range(start, start + 100):
                    cache.put(i, i * 2)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t * 100,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) <= 50
        # Data integrity: every retrievable value must be correct (key * 2)
        for key in range(500):
            val = cache.get(key)
            if val is not None:
                assert val == key * 2, f"key={key} expected {key * 2}, got {val}"

    def test_concurrent_get_put(self) -> None:
        """Mixed get/put from multiple threads should not raise."""
        cache: LRUCache[int, int] = LRUCache(max_size=20)
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(50):
                    cache.put(tid * 50 + i, i)
                    val = cache.get(tid * 50 + i)
                    # Value may be evicted by another thread, but if present it must be correct
                    if val is not None and val != i:
                        errors.append(
                            Exception(f"tid={tid} key={tid * 50 + i}: expected {i}, got {val}")
                        )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(cache) <= 20, f"Cache size {len(cache)} exceeds max_size=20"


# ---------------------------------------------------------------------------
# B17: None values vs cache misses (sentinel fix)
# ---------------------------------------------------------------------------


class TestNoneValues:
    """Verify that ``None`` stored as a value is distinguishable from a miss."""

    def test_get_returns_none_for_stored_none(self) -> None:
        """A key explicitly stored with value=None should return None, not be
        confused with a miss."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=4)
        cache.put("k", None)
        assert cache.get("k") is None  # should be a hit, not a miss
        assert "k" in cache  # key is present

    def test_get_returns_none_for_missing_key(self) -> None:
        """A key that was never stored should also return None (miss)."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=4)
        assert cache.get("missing") is None
        assert "missing" not in cache

    def test_none_value_not_confused_with_miss(self) -> None:
        """The critical distinction: after storing None, the key must remain
        in the cache and not be evicted/skipped by a `get`."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=2)
        cache.put("a", None)
        cache.put("b", 42)
        # "a" has None value but should still be in cache
        assert len(cache) == 2
        assert cache.get("a") is None
        assert "a" in cache
        assert cache.get("b") == 42

    def test_none_value_promotes_on_get(self) -> None:
        """Getting a None-valued entry should promote it (LRU behavior),
        so a subsequent put evicts the other key instead."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=2)
        cache.put("a", None)
        cache.put("b", 1)
        cache.get("a")  # promote "a"; "b" is now LRU
        cache.put("c", 2)  # should evict "b", not "a"
        assert "a" in cache
        assert "b" not in cache
        assert cache.get("a") is None

    def test_overwrite_none_with_value(self) -> None:
        """A None value can be overwritten with a real value."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=4)
        cache.put("k", None)
        assert cache.get("k") is None
        cache.put("k", 99)
        assert cache.get("k") == 99

    def test_overwrite_value_with_none(self) -> None:
        """A real value can be overwritten with None."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=4)
        cache.put("k", 42)
        assert cache.get("k") == 42
        cache.put("k", None)
        assert cache.get("k") is None
        assert "k" in cache

    def test_none_value_with_ttl(self) -> None:
        """None values should be subject to TTL expiry like any other value."""
        cache: LRUCache[str, int | None] = LRUCache(max_size=10, ttl=0.05)
        cache.put("k", None)
        assert "k" in cache
        assert cache.get("k") is None  # hit before TTL
        time.sleep(0.08)
        assert cache.get("k") is None  # expired — but return value is same
        assert "k" not in cache  # key has been evicted
