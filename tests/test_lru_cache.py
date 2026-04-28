"""Tests for haute._lru_cache — LRUCache with optional TTL."""

from __future__ import annotations

import logging
import threading

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
# Byte-aware eviction
# ---------------------------------------------------------------------------


class TestByteAwareEviction:
    def test_byte_budget_requires_size_callback(self) -> None:
        with pytest.raises(ValueError, match="size_of is required"):
            LRUCache[str, int](max_bytes=100)

        with pytest.raises(ValueError, match="max_bytes is required"):
            LRUCache[str, int](size_of=lambda value: value)

    def test_evicts_lru_when_byte_budget_exceeded_below_count_cap(self) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda value: value,
        )

        cache.put("old", 40)
        cache.put("middle", 25)
        cache.put("new", 45)

        assert cache.get("old") is None
        assert cache.get("middle") == 25
        assert cache.get("new") == 45
        assert cache.stats()["bytes"] == 70

    def test_overwrite_replaces_previous_byte_weight(self) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda value: value,
        )

        cache.put("a", 40)
        cache.put("b", 40)
        cache.put("a", 10)

        assert cache.get("a") == 10
        assert cache.get("b") == 40
        assert cache.stats()["bytes"] == 50

    def test_evict_where_updates_byte_stats(self) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda value: value,
        )
        cache.put("a", 20)
        cache.put("b", 30)
        cache.put("c", 40)

        assert cache.evict_where(lambda key: key in {"a", "c"}) == [20, 40]

        assert cache.stats()["bytes"] == 30
        assert cache.get("b") == 30

    def test_ttl_expiry_updates_byte_stats(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            ttl=5.0,
            max_bytes=100,
            size_of=lambda value: value,
        )
        cache.put("a", 45)
        assert cache.stats()["bytes"] == 45

        now = 1006.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert cache.get("a") is None

        assert cache.stats()["bytes"] == 0
        assert len(cache) == 0

    def test_size_callback_must_return_plain_non_negative_int(self) -> None:
        cache_bool: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda _value: True,
        )
        with pytest.raises(ValueError, match="non-negative int"):
            cache_bool.put("a", 1)

        cache_negative: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda _value: -1,
        )
        with pytest.raises(ValueError, match="non-negative int"):
            cache_negative.put("a", 1)

    def test_oversized_put_warns_with_drop_details_and_removes_stale_entry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=50,
            size_of=lambda value: value,
        )
        cache.put("same-fp", 25)

        with caplog.at_level(logging.WARNING, logger="haute._lru_cache"):
            cache.put("same-fp", 75)

        assert cache.get("same-fp") is None
        assert cache.stats()["bytes"] == 0
        record = next(
            record
            for record in caplog.records
            if record.message == "lru_cache_oversized_entry_not_cached"
        )
        assert record.key == "same-fp"
        assert record.measured_size == 75
        assert record.max_bytes == 50
        assert record.replaced_existing is True

    def test_oversized_put_warns_for_new_key_without_caching(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=50,
            size_of=lambda value: value,
        )

        with caplog.at_level(logging.WARNING, logger="haute._lru_cache"):
            cache.put("too-large", 75)

        assert cache.get("too-large") is None
        assert cache.stats()["bytes"] == 0
        record = next(
            record
            for record in caplog.records
            if record.message == "lru_cache_oversized_entry_not_cached"
        )
        assert record.key == "too-large"
        assert record.measured_size == 75
        assert record.max_bytes == 50
        assert record.replaced_existing is False

    def test_ordinary_byte_budget_eviction_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache: LRUCache[str, int] = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=lambda value: value,
        )

        with caplog.at_level(logging.WARNING, logger="haute._lru_cache"):
            cache.put("old", 40)
            cache.put("new", 70)

        assert cache.get("old") is None
        assert cache.get("new") == 70
        assert "lru_cache_oversized_entry_not_cached" not in {
            record.message for record in caplog.records
        }


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


class TestTTL:
    def test_entry_expires_after_ttl(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", 42)
        assert cache.get("k") == 42
        now = 1006.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert cache.get("k") is None

    def test_entry_valid_before_ttl(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", 42)
        now = 1002.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert cache.get("k") == 42

    def test_ttl_eviction_removes_from_data(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", 1)
        now = 1006.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
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

    def test_concurrent_puts_no_data_loss(self) -> None:
        """Barrier-synchronised writers into a large-enough cache must not lose entries."""
        cache: LRUCache[int, int] = LRUCache(max_size=200)
        barrier = threading.Barrier(4)

        def writer(start: int) -> None:
            barrier.wait()
            for i in range(start, start + 50):
                cache.put(i, i)

        threads = [threading.Thread(target=writer, args=(t * 50,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(cache) == 200
        for i in range(200):
            assert cache.get(i) == i


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

    def test_none_value_with_ttl(self, monkeypatch) -> None:
        """None values should be subject to TTL expiry like any other value."""
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int | None] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", None)
        assert "k" in cache
        assert cache.get("k") is None  # hit before TTL
        now = 1006.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert cache.get("k") is None  # expired — but return value is same
        assert "k" not in cache  # key has been evicted


# ---------------------------------------------------------------------------
# TTL edge cases
# ---------------------------------------------------------------------------


class TestTTLEdgeCases:
    def test_ttl_zero_does_not_expire_within_same_tick(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=0)
        cache.put("k", 42)
        # TTL check uses strict >, so ttl=0 within the same monotonic tick is a hit
        assert cache.get("k") == 42


# ---------------------------------------------------------------------------
# __contains__ TTL interaction
# ---------------------------------------------------------------------------


class TestContainsTTL:
    def test_contains_does_not_check_ttl(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=5.0)
        cache.put("k", 42)
        now = 1006.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert "k" in cache
        assert cache.get("k") is None


# ---------------------------------------------------------------------------
# put timestamp update
# ---------------------------------------------------------------------------


class TestPutTimestamp:
    def test_put_same_key_updates_timestamp(self, monkeypatch) -> None:
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=10.0)
        cache.put("k", 1)
        now = 1006.0  # 6s elapsed — would expire if timestamp not refreshed
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache.put("k", 2)  # refreshes timestamp to 1006.0
        now = 1012.0  # 6s after refresh — within TTL of 10s from refresh
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        assert cache.get("k") == 2


# ---------------------------------------------------------------------------
# None keys
# ---------------------------------------------------------------------------


class TestNoneKeys:
    def test_put_with_none_key(self) -> None:
        cache: LRUCache[None, int] = LRUCache(max_size=4)
        cache.put(None, 99)
        assert cache.get(None) == 99
        assert len(cache) == 1

    def test_get_with_none_key_miss(self) -> None:
        cache: LRUCache[None, int] = LRUCache(max_size=4)
        assert cache.get(None) is None


# ---------------------------------------------------------------------------
# Clear then reuse
# ---------------------------------------------------------------------------


class TestClearReuse:
    def test_clear_then_put_then_get(self) -> None:
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        cache.put("c", 3)
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert len(cache) == 1


# ---------------------------------------------------------------------------
# Large cache
# ---------------------------------------------------------------------------


class TestLargeCache:
    def test_large_cache_basic_operations(self) -> None:
        cache: LRUCache[int, int] = LRUCache(max_size=1000)
        for i in range(1000):
            cache.put(i, i * 3)
        assert len(cache) == 1000
        for i in range(1000):
            assert cache.get(i) == i * 3
        cache.put(1000, 3000)
        assert cache.get(0) is None
        assert cache.get(1000) == 3000
        assert len(cache) == 1000


# ---------------------------------------------------------------------------
# evict_where — predicate-driven eviction primitive
# ---------------------------------------------------------------------------


class TestEvictWhere:
    """Tests for the ``evict_where`` predicate-driven eviction primitive."""

    def test_evicts_matching_keys(self) -> None:
        """Evicts all entries whose key satisfies the predicate."""
        cache: LRUCache[tuple[str, int], int] = LRUCache(max_size=10)
        cache.put(("a", 1), 10)
        cache.put(("a", 2), 20)
        cache.put(("b", 1), 30)

        evicted = cache.evict_where(lambda k: k[0] == "a")

        assert sorted(evicted) == [10, 20]
        assert ("a", 1) not in cache
        assert ("a", 2) not in cache
        assert ("b", 1) in cache

    def test_no_match_returns_empty(self) -> None:
        """Returns an empty list when nothing matches."""
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        cache.put("b", 2)

        evicted = cache.evict_where(lambda k: k == "nonexistent")

        assert evicted == []
        assert len(cache) == 2

    def test_evict_all(self) -> None:
        """``lambda _: True`` evicts every entry."""
        cache: LRUCache[str, int] = LRUCache(max_size=4)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        evicted = cache.evict_where(lambda _: True)

        assert sorted(evicted) == [1, 2, 3]
        assert len(cache) == 0

    def test_evict_clears_timestamps(self, monkeypatch) -> None:
        """Evicted entries are also removed from the timestamp side table."""
        import haute._lru_cache as _mod

        now = 1000.0
        monkeypatch.setattr(_mod._time, "monotonic", lambda: now)
        cache: LRUCache[str, int] = LRUCache(max_size=10, ttl=100.0)
        cache.put("a", 1)
        cache.put("b", 2)
        assert "a" in cache._timestamps
        assert "b" in cache._timestamps

        cache.evict_where(lambda k: k == "a")

        assert "a" not in cache._timestamps
        assert "b" in cache._timestamps

    def test_evict_unpins_evicted_keys(self) -> None:
        """Evicted entries are removed from the pin set as well."""
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.pin("a")
        assert "a" in cache._pinned

        evicted = cache.evict_where(lambda k: k == "a")

        assert evicted == [1]
        assert "a" not in cache._pinned
        assert "a" not in cache

    def test_evict_ignores_pinning(self) -> None:
        """A pinned entry IS evicted when it matches the predicate.

        A predicate-driven eviction is an explicit instruction — pinning is
        for LRU-capacity protection, not predicate-driven cleanup.
        """
        cache: LRUCache[str, int] = LRUCache(max_size=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.pin("a")

        evicted = cache.evict_where(lambda k: k == "a")

        assert evicted == [1]
        assert "a" not in cache

    def test_evict_preserves_order_for_remaining(self) -> None:
        """Non-evicted entries keep their LRU order."""
        cache: LRUCache[str, int] = LRUCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        # Evict middle entry
        cache.evict_where(lambda k: k == "b")
        # Add a fresh entry — cache still at 2, room for one more
        cache.put("d", 4)
        assert len(cache) == 3
        # Now push over capacity — the LRU should be "a" (oldest remaining)
        cache.put("e", 5)
        assert "a" not in cache
        assert "c" in cache
        assert "d" in cache
        assert "e" in cache

    def test_concurrent_evict_is_atomic(self) -> None:
        """Concurrent writes and evictions never lose or duplicate entries."""
        cache: LRUCache[int, int] = LRUCache(max_size=500)
        errors: list[Exception] = []
        barrier = threading.Barrier(5)

        def writer(start: int) -> None:
            try:
                barrier.wait()
                for i in range(start, start + 100):
                    cache.put(i, i)
            except Exception as exc:
                errors.append(exc)

        def evictor() -> None:
            try:
                barrier.wait()
                # Evict odd keys concurrently
                for _ in range(50):
                    cache.evict_where(lambda k: k % 2 == 1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t * 100,)) for t in range(4)]
        threads.append(threading.Thread(target=evictor))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Every surviving key must have a correct value (no torn writes)
        for i in range(400):
            val = cache.get(i)
            if val is not None:
                assert val == i

    def test_predicate_with_tuple_keys(self) -> None:
        """Supports the common (id, hash) tuple-key pattern."""
        cache: LRUCache[tuple[int, str], str] = LRUCache(max_size=10)
        cache.put((1, "aaa"), "one-a")
        cache.put((1, "bbb"), "one-b")
        cache.put((2, "aaa"), "two-a")

        target_id = 1
        evicted = cache.evict_where(lambda k: k[0] == target_id)

        assert sorted(evicted) == ["one-a", "one-b"]
        assert (2, "aaa") in cache
        assert len(cache) == 1
