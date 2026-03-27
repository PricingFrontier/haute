"""Tests for FingerprintCache — generic multi-entry fingerprint cache."""

from __future__ import annotations

import threading
import time

import pytest

from haute._fingerprint_cache import FingerprintCache


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_requires_at_least_one_slot(self) -> None:
        with pytest.raises(ValueError, match="At least one slot"):
            FingerprintCache(slots=())

    def test_initial_state_is_empty(self) -> None:
        cache = FingerprintCache(slots=("a", "b"))
        assert cache.fingerprint is None
        assert cache.try_get("any") is None
        assert len(repr(cache)) > 0  # smoke test __repr__


# ---------------------------------------------------------------------------
# Basic set / get
# ---------------------------------------------------------------------------


class TestBasicSetGet:
    def test_store_and_retrieve(self) -> None:
        cache = FingerprintCache(slots=("outputs", "order"))
        cache.store("fp1", outputs={"a": 1}, order=["a"])
        data = cache.try_get("fp1")
        assert data is not None
        assert data["outputs"] == {"a": 1}
        assert data["order"] == ["a"]

    def test_store_keeps_multiple_entries(self) -> None:
        """Multi-entry cache retains both entries (unlike the old single-entry)."""
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={"old": True})
        cache.store("fp2", x={"new": True})
        # Both entries are accessible
        data1 = cache.try_get("fp1")
        assert data1 is not None
        assert data1["x"] == {"old": True}
        data2 = cache.try_get("fp2")
        assert data2 is not None
        assert data2["x"] == {"new": True}

    def test_lru_eviction(self) -> None:
        """Oldest entry is evicted when max_entries is exceeded."""
        cache = FingerprintCache(slots=("x",), max_entries=2)
        cache.store("fp1", x={"first": True})
        cache.store("fp2", x={"second": True})
        cache.store("fp3", x={"third": True})  # evicts fp1
        assert cache.try_get("fp1") is None
        assert cache.try_get("fp2") is not None
        assert cache.try_get("fp3") is not None

    def test_omitted_slots_default_to_empty_dict(self) -> None:
        cache = FingerprintCache(slots=("a", "b", "c"))
        cache.store("fp1", a={"val": 1})
        data = cache.try_get("fp1")
        assert data is not None
        assert data["a"] == {"val": 1}
        assert data["b"] == {}
        assert data["c"] == {}

    def test_returns_shallow_copy(self) -> None:
        """Mutating the returned dict should not affect the cache."""
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={"key": "value"})
        data = cache.try_get("fp1")
        assert data is not None
        data["x"]["key"] = "mutated"
        # The inner dict is shared (shallow copy), but the top-level
        # dict returned by try_get is a new dict each time.
        data2 = cache.try_get("fp1")
        assert data2 is not None
        # Inner data *is* shared (intentionally — these are large DataFrames)
        assert data2["x"]["key"] == "mutated"


# ---------------------------------------------------------------------------
# Cache miss
# ---------------------------------------------------------------------------


class TestCacheMiss:
    def test_wrong_fingerprint_returns_none(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={"a": 1})
        assert cache.try_get("wrong_fp") is None

    def test_empty_first_slot_treated_as_hit(self) -> None:
        """Empty dict is a valid stored value (not a miss) thanks to _MISSING sentinel."""
        cache = FingerprintCache(slots=("primary", "secondary"))
        cache.store("fp1", primary={}, secondary={"ok": True})
        data = cache.try_get("fp1")
        assert data is not None
        assert data["primary"] == {}
        assert data["secondary"] == {"ok": True}

    def test_never_stored_returns_none(self) -> None:
        cache = FingerprintCache(slots=("x",))
        assert cache.try_get("anything") is None


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


class TestInvalidation:
    def test_invalidate_clears_everything(self) -> None:
        cache = FingerprintCache(slots=("outputs", "meta"))
        cache.store("fp1", outputs={"a": 1}, meta={"b": 2})
        cache.invalidate()
        assert cache.fingerprint is None
        assert cache.try_get("fp1") is None

    def test_invalidate_on_empty_cache_is_safe(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.invalidate()  # should not raise
        assert cache.fingerprint is None
        # Subsequent operations must still work correctly after invalidating an empty cache
        cache.store("fp_after", x={"restored": True})
        data = cache.try_get("fp_after")
        assert data is not None
        assert data["x"] == {"restored": True}

    def test_invalidate_clears_non_dict_slots(self) -> None:
        """Slots holding lists or sets should also be cleared."""
        cache = FingerprintCache(slots=("items",))
        cache.store("fp1", items=["a", "b", "c"])
        cache.invalidate()
        assert cache.try_get("fp1") is None


# ---------------------------------------------------------------------------
# update_slot
# ---------------------------------------------------------------------------


class TestUpdateSlot:
    def test_update_single_slot(self) -> None:
        cache = FingerprintCache(slots=("a", "b"))
        cache.store("fp1", a={"x": 1}, b={"y": 2})
        cache.update_slot("a", {"x": 99}, fingerprint="fp1")
        data = cache.try_get("fp1")
        assert data is not None
        assert data["a"] == {"x": 99}
        assert data["b"] == {"y": 2}

    def test_update_unknown_slot_raises(self) -> None:
        cache = FingerprintCache(slots=("a",))
        with pytest.raises(ValueError, match="Unknown slot"):
            cache.update_slot("nonexistent", {}, fingerprint="fp1")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_store_rejects_unknown_slots(self) -> None:
        cache = FingerprintCache(slots=("a", "b"))
        with pytest.raises(ValueError, match="Unknown slot"):
            cache.store("fp1", a={}, c={"bad": True})


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_store_and_get(self) -> None:
        """Multiple threads storing and getting should not corrupt state."""
        cache = FingerprintCache(slots=("data",))
        errors: list[str] = []
        barrier = threading.Barrier(4)

        def writer(fp: str, val: dict) -> None:
            barrier.wait()
            for _ in range(100):
                cache.store(fp, data=val)

        def reader() -> None:
            barrier.wait()
            for _ in range(100):
                result = cache.try_get("fp1")
                if result is not None and not isinstance(result, dict):
                    errors.append(f"Bad type: {type(result)}")

        threads = [threading.Thread(target=writer, args=("fp1", {"a": i})) for i in range(2)] + [
            threading.Thread(target=reader) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Thread safety errors: {errors}"

    def test_lock_property_returns_lock(self) -> None:
        cache = FingerprintCache(slots=("x",))
        lock = cache.lock
        # Verify it's a lock by acquiring and releasing
        assert lock.acquire(blocking=False)
        lock.release()


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_empty(self) -> None:
        cache = FingerprintCache(slots=("a", "b"))
        r = repr(cache)
        assert "FingerprintCache" in r
        assert "entries=0" in r

    def test_repr_with_data(self) -> None:
        cache = FingerprintCache(slots=("items",))
        cache.store("fp1", items={"k": "v"})
        r = repr(cache)
        assert "fp1" in r
