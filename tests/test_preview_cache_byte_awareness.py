"""Regression tests for byte-aware preview cache eviction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import polars as pl
import pytest

from haute._env import int_env
from haute._lru_cache import LRUCache
from haute.executor import (
    PREVIEW_CACHE_MAX_BYTES,
    _estimate_preview_cache_entry_bytes,
    _preview_cache,
)
from haute.trace import _cache as _trace_cache


def _entry_size(entry: dict) -> int:
    return int(entry["payload"]["bytes"])


class TestPreviewCacheByteLimit:
    def test_large_entry_evicts_older_entries_below_count_cap(self) -> None:
        cache = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=_entry_size,
        )

        cache.put("small-old", {"payload": {"bytes": 30}})
        cache.put("small-new", {"payload": {"bytes": 20}})
        cache.put("large", {"payload": {"bytes": 60}})

        assert cache.get("small-old") is None
        assert cache.get("small-new") is not None
        assert cache.get("large") is not None
        assert cache.stats() == {
            "entries": 2,
            "max_entries": 10,
            "pinned_entries": 0,
            "bytes": 80,
            "max_bytes": 100,
        }

    def test_small_entries_can_coexist_until_byte_cap(self) -> None:
        cache = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=_entry_size,
        )

        cache.put("a", {"payload": {"bytes": 10}})
        cache.put("b", {"payload": {"bytes": 20}})
        cache.put("c", {"payload": {"bytes": 30}})

        assert cache.get("a") is not None
        assert cache.get("b") is not None
        assert cache.get("c") is not None
        assert cache.stats()["bytes"] == 60

    def test_store_reports_oversized_rejection_and_retains_stale_entry(self) -> None:
        cache = LRUCache(
            max_size=10,
            max_bytes=50,
            size_of=_entry_size,
        )

        assert cache.put("same-fp", {"payload": {"bytes": 25}}) is True
        assert cache.put("same-fp", {"payload": {"bytes": 75}}) is False

        assert cache.get("same-fp") == {"payload": {"bytes": 25}}
        assert cache.stats()["bytes"] == 25

    def test_last_completed_concurrent_accepted_same_key_store_wins(self) -> None:
        slow_measure_started = Event()
        release_slow_measure = Event()

        def controlled_size(entry: dict) -> int:
            payload = entry["payload"]
            if payload.get("label") == "slow":
                slow_measure_started.set()
                assert release_slow_measure.wait(timeout=5)
            return int(payload["bytes"])

        cache = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=controlled_size,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            slow_store = pool.submit(
                cache.put,
                "same-fp",
                {"payload": {"bytes": 40, "label": "slow"}},
            )
            assert slow_measure_started.wait(timeout=5)
            assert cache.put(
                "same-fp",
                {"payload": {"bytes": 30, "label": "fast"}},
            )
            release_slow_measure.set()
            assert slow_store.result(timeout=5)

        assert cache.get("same-fp") == {"payload": {"bytes": 40, "label": "slow"}}
        assert cache.stats()["bytes"] == 40

    def test_concurrent_rejected_same_key_store_preserves_accepted_value(self) -> None:
        rejected_measure_started = Event()
        release_rejected_measure = Event()

        def controlled_size(entry: dict) -> int:
            payload = entry["payload"]
            if payload.get("label") == "rejected":
                rejected_measure_started.set()
                assert release_rejected_measure.wait(timeout=5)
            return int(payload["bytes"])

        cache = LRUCache(
            max_size=10,
            max_bytes=100,
            size_of=controlled_size,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            rejected_store = pool.submit(
                cache.put,
                "same-fp",
                {"payload": {"bytes": 101, "label": "rejected"}},
            )
            assert rejected_measure_started.wait(timeout=5)
            assert cache.put(
                "same-fp",
                {"payload": {"bytes": 30, "label": "accepted"}},
            )
            release_rejected_measure.set()
            assert rejected_store.result(timeout=5) is False

        assert cache.get("same-fp") == {"payload": {"bytes": 30, "label": "accepted"}}
        assert cache.stats()["bytes"] == 30

    def test_invalid_byte_limits_fail_loudly(self) -> None:
        with pytest.raises(ValueError, match="max_bytes must be >= 1"):
            LRUCache(
                max_bytes=0,
                size_of=_entry_size,
            )

        with pytest.raises(ValueError, match="size_of is required"):
            LRUCache(max_size=10, max_bytes=100)

    def test_trace_cache_is_byte_bounded(self) -> None:
        # Remediation 2.9: the trace cache holds the same class of payload
        # as the preview cache and respects the same byte-budget discipline.
        # Full coverage lives in tests/test_trace_cache_byte_awareness.py.
        from haute.trace import TRACE_CACHE_MAX_BYTES

        stats = _trace_cache.stats()

        assert stats["max_bytes"] == TRACE_CACHE_MAX_BYTES
        assert TRACE_CACHE_MAX_BYTES > 0


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
            int_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 100)

        monkeypatch.setenv("HAUTE_TEST_PREVIEW_CACHE_BYTES", "not-an-int")
        with pytest.raises(RuntimeError, match="must be a positive integer"):
            int_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 100)

    def test_preview_cache_byte_budget_env_parser_uses_default(self, monkeypatch) -> None:
        monkeypatch.delenv("HAUTE_TEST_PREVIEW_CACHE_BYTES", raising=False)

        assert int_env("HAUTE_TEST_PREVIEW_CACHE_BYTES", 123) == 123
