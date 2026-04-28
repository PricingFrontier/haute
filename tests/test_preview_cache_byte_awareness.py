"""Regression tests for byte-aware preview cache eviction."""

from __future__ import annotations

import polars as pl
import pytest

from haute._fingerprint_cache import FingerprintCache
from haute.executor import (
    PREVIEW_CACHE_MAX_BYTES,
    _estimate_preview_cache_entry_bytes,
    _positive_int_from_env,
    _preview_cache,
)
from haute.trace import _cache as _trace_cache


def _entry_size(entry: dict) -> int:
    return int(entry["payload"]["bytes"])


class TestFingerprintCacheByteLimit:
    def test_large_entry_evicts_older_entries_below_count_cap(self) -> None:
        cache = FingerprintCache(
            slots=("payload",),
            max_entries=10,
            max_bytes=100,
            size_of=_entry_size,
        )

        cache.store("small-old", payload={"bytes": 30})
        cache.store("small-new", payload={"bytes": 20})
        cache.store("large", payload={"bytes": 60})

        assert cache.try_get("small-old") is None
        assert cache.try_get("small-new") is not None
        assert cache.try_get("large") is not None
        assert cache.stats() == {
            "entries": 2,
            "max_entries": 10,
            "bytes": 80,
            "max_bytes": 100,
        }

    def test_small_entries_can_coexist_until_byte_cap(self) -> None:
        cache = FingerprintCache(
            slots=("payload",),
            max_entries=10,
            max_bytes=100,
            size_of=_entry_size,
        )

        cache.store("a", payload={"bytes": 10})
        cache.store("b", payload={"bytes": 20})
        cache.store("c", payload={"bytes": 30})

        assert cache.try_get("a") is not None
        assert cache.try_get("b") is not None
        assert cache.try_get("c") is not None
        assert cache.stats()["bytes"] == 60

    def test_oversized_entry_is_not_cached_and_replaces_stale_entry(self) -> None:
        cache = FingerprintCache(
            slots=("payload",),
            max_entries=10,
            max_bytes=50,
            size_of=_entry_size,
        )

        cache.store("same-fp", payload={"bytes": 25})
        cache.store("same-fp", payload={"bytes": 75})

        assert cache.try_get("same-fp") is None
        assert cache.stats()["bytes"] == 0

    def test_invalid_byte_limits_fail_loudly(self) -> None:
        with pytest.raises(ValueError, match="max_bytes must be >= 1"):
            FingerprintCache(
                slots=("payload",),
                max_bytes=0,
                size_of=_entry_size,
            )

        with pytest.raises(ValueError, match="size_of is required"):
            FingerprintCache(slots=("payload",), max_bytes=100)

    def test_trace_cache_remains_entry_bounded_only(self) -> None:
        stats = _trace_cache.stats()

        assert stats["max_bytes"] is None
        assert stats["bytes"] == 0

    def test_update_slot_remeasures_and_evicts_lru_when_entry_grows(self) -> None:
        cache = FingerprintCache(
            slots=("payload", "meta"),
            max_entries=10,
            max_bytes=100,
            size_of=_entry_size,
        )

        cache.store("old", payload={"bytes": 30})
        cache.store("target", payload={"bytes": 40})
        cache.update_slot("payload", {"bytes": 80}, fingerprint="target")

        assert cache.try_get("old") is None
        target = cache.try_get("target")
        assert target is not None
        assert target["payload"] == {"bytes": 80}
        assert target["meta"] == {}
        assert cache.stats()["bytes"] == 80

    def test_update_slot_oversized_replacement_removes_stale_entry(self) -> None:
        cache = FingerprintCache(
            slots=("payload",),
            max_entries=10,
            max_bytes=50,
            size_of=_entry_size,
        )

        cache.store("same-fp", payload={"bytes": 25})
        cache.update_slot("payload", {"bytes": 75}, fingerprint="same-fp")

        assert cache.try_get("same-fp") is None
        assert cache.stats()["bytes"] == 0

    def test_update_slot_preserves_size_for_non_size_sensitive_slots(self) -> None:
        calls = 0

        def entry_size(entry: dict) -> int:
            nonlocal calls
            calls += 1
            return int(entry["payload"]["bytes"])

        cache = FingerprintCache(
            slots=("payload", "meta"),
            max_entries=10,
            max_bytes=100,
            size_of=entry_size,
            size_sensitive_slots=("payload",),
        )

        cache.store("target", payload={"bytes": 40}, meta={"version": 1})
        assert calls == 1

        cache.update_slot("meta", {"version": 2}, fingerprint="target")

        assert calls == 1
        assert cache.stats()["bytes"] == 40
        assert cache.try_get("target") == {
            "payload": {"bytes": 40},
            "meta": {"version": 2},
        }


class TestPreviewCacheSizing:
    def test_preview_entry_size_uses_materialized_dataframe_bytes(self) -> None:
        df_a = pl.DataFrame({"x": [1, 2, 3]})
        df_b = pl.DataFrame({"label": ["alpha", "beta"]})
        entry = {
            "eager_outputs": {"a": df_a, "b": df_b},
            "errors": {},
            "order": ["a", "b"],
            "timings": {},
            "memory_bytes": {},
            "error_lines": {},
            "available_columns": {},
        }

        assert _estimate_preview_cache_entry_bytes(entry) == (
            df_a.estimated_size() + df_b.estimated_size()
        )

    def test_preview_entry_size_rejects_unexpected_payloads(self) -> None:
        with pytest.raises(TypeError, match="expected Polars DataFrame"):
            _estimate_preview_cache_entry_bytes({"eager_outputs": {"bad": object()}})

    def test_preview_cache_has_explicit_byte_budget(self) -> None:
        stats = _preview_cache.stats()

        assert stats["max_bytes"] == PREVIEW_CACHE_MAX_BYTES
        assert PREVIEW_CACHE_MAX_BYTES > 0

    def test_preview_cache_byte_budget_env_parser_fails_loudly(self, monkeypatch) -> None:
        monkeypatch.setenv("HAUTE_TEST_PREVIEW_CACHE_BYTES", "0")
        with pytest.raises(RuntimeError, match="must be a positive integer"):
            _positive_int_from_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 100)

        monkeypatch.setenv("HAUTE_TEST_PREVIEW_CACHE_BYTES", "not-an-int")
        with pytest.raises(RuntimeError, match="must be a positive integer"):
            _positive_int_from_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 100)

    def test_preview_cache_byte_budget_env_parser_uses_default(self, monkeypatch) -> None:
        monkeypatch.delenv("HAUTE_TEST_PREVIEW_CACHE_BYTES", raising=False)

        assert _positive_int_from_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 123) == 123
