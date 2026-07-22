"""Regression tests for byte-aware trace cache eviction.

CODE_REVIEW.md (MEDIUM, Caching/chunking): the trace result cache at
``trace.py:210`` was count-bounded only, so a handful of large
materialized-frame entries could blow past the byte budget the preview
cache respects.  These tests pin the remediation (item 2.9): the trace
cache is bounded by retained bytes AND entry count, reuses the preview
cache's frame-size estimator, evicts LRU-first, and deterministically
rejects single entries larger than the whole budget (the same
admit-or-reject-at-store policy the dataframe-execution cache applies
to oversized artifacts) — without ever changing trace results.

Twin module: tests/test_preview_cache_byte_awareness.py.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import polars as pl
import pytest

from haute._fingerprint_cache import FingerprintCache
from haute.executor import (
    PREVIEW_CACHE_MAX_BYTES,
    _estimate_preview_cache_entry_bytes,
)
from haute.trace import _cache as _trace_cache
from haute.trace import execute_trace
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

_TRACE_SLOTS = ("eager_outputs", "order", "parents_of", "node_map", "source_ids")


def _trace_entry(eager_outputs: dict[str, Any]) -> dict[str, Any]:
    """Full slot kwargs in the exact shape ``execute_trace`` stores."""
    order = list(eager_outputs)
    return {
        "eager_outputs": dict(eager_outputs),
        "order": order,
        "parents_of": {nid: [] for nid in order},
        "node_map": {},
        "source_ids": set(order),
    }


def _trace_shaped_cache(max_bytes: int, max_entries: int = 8) -> FingerprintCache:
    """Build a cache configured exactly like ``haute.trace._cache``."""
    return FingerprintCache(
        slots=_TRACE_SLOTS,
        max_entries=max_entries,
        max_bytes=max_bytes,
        size_of=_estimate_preview_cache_entry_bytes,
        size_sensitive_slots=("eager_outputs",),
    )


def _simple_graph(tmp_path, code: str = "df = df.with_columns(z=pl.col('x') + pl.col('y'))"):
    p = tmp_path / "data.parquet"
    pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(p)
    return make_graph(
        {
            "nodes": [
                make_source_node("src", str(p)),
                make_transform_node("t", code),
            ],
            "edges": [make_edge("src", "t")],
        }
    )


class TestTraceCacheByteBudgetWiring:
    def test_trace_cache_declares_byte_budget_matching_module_constant(self) -> None:
        from haute.trace import TRACE_CACHE_MAX_BYTES

        stats = _trace_cache.stats()

        assert stats["max_bytes"] == TRACE_CACHE_MAX_BYTES
        assert TRACE_CACHE_MAX_BYTES > 0
        # The byte budget supplements the entry bound — it does not replace it.
        assert stats["max_entries"] == 8

    def test_trace_cache_reuses_preview_byte_estimator(self) -> None:
        # One estimator for both materialized-frame caches; a second
        # hand-rolled estimator would be allowed to drift.
        assert _trace_cache._size_of is _estimate_preview_cache_entry_bytes
        assert _trace_cache._size_sensitive_slots == frozenset({"eager_outputs"})

    @pytest.mark.skipif(
        os.environ.get("HAUTE_TRACE_CACHE_MAX_BYTES") is not None,
        reason="operator explicitly overrode the trace cache byte budget",
    )
    def test_trace_cache_budget_defaults_to_preview_budget(self) -> None:
        from haute.trace import TRACE_CACHE_MAX_BYTES

        assert TRACE_CACHE_MAX_BYTES == PREVIEW_CACHE_MAX_BYTES


class TestTraceCacheByteBounding:
    """Seed the real trace cache singleton with few-but-large entries."""

    def test_few_large_entries_exceeding_budget_evict_oldest(self) -> None:
        budget = _trace_cache.stats()["max_bytes"]
        assert budget is not None, (
            "trace cache is count-bounded only: materialized trace frames "
            "accumulate past the preview byte budget (CODE_REVIEW trace.py:210)"
        )

        # Each frame ~40% of the budget: any two fit, three bust the budget.
        rows = (budget * 2 // 5) // 8  # Int64 column ≈ 8 bytes per row
        df = pl.DataFrame({"x": pl.int_range(0, rows, eager=True)})
        assert 2 * df.estimated_size() <= budget
        assert 3 * df.estimated_size() > budget

        for i in range(3):
            _trace_cache.store(f"fp-{i}", **_trace_entry({"node": df}))

        # Byte-bound assertions — RED while the cache was count-bounded only
        # (max_entries=8 happily retained all three oversized-total entries).
        assert _trace_cache.try_get("fp-0") is None, "oldest entry must be evicted"
        assert _trace_cache.try_get("fp-1") is not None
        assert _trace_cache.try_get("fp-2") is not None, (
            "the just-written trace must survive its own insertion"
        )

        stats = _trace_cache.stats()
        assert stats["bytes"] == 2 * df.estimated_size()
        assert stats["bytes"] <= budget


class TestTraceCacheOversizedEntry:
    """A single entry larger than the whole budget is rejected at store time.

    Same policy as the dataframe-execution cache's oversized artifacts:
    never admitted, with an existing entry retained, loud telemetry — and the trace
    request itself still succeeds (only the re-click loses its cache hit).
    """

    def test_single_entry_larger_than_whole_budget_is_rejected(self) -> None:
        df = pl.DataFrame({"x": [1, 2, 3, 4]})
        cache = _trace_shaped_cache(max_bytes=df.estimated_size() - 1)

        cache.store("fp", **_trace_entry({"node": df}))

        assert cache.try_get("fp") is None
        stats = cache.stats()
        assert stats["entries"] == 0
        assert stats["bytes"] == 0

    def test_oversized_replacement_retains_existing_entry(self) -> None:
        small = pl.DataFrame({"x": [1]})
        big = pl.DataFrame({"x": list(range(100))})
        cache = _trace_shaped_cache(max_bytes=small.estimated_size())

        cache.store("fp", **_trace_entry({"node": small}))
        assert cache.try_get("fp") is not None

        assert cache.store("fp", **_trace_entry({"node": big})) is False

        # A rejected replacement never destroys the previously retained
        # value; callers learn explicitly that the new value was not stored.
        retained = cache.try_get("fp")
        assert retained is not None
        assert retained["eager_outputs"]["node"].equals(small)
        assert cache.stats()["bytes"] == small.estimated_size()

    def test_oversized_rejection_is_loud(self, caplog: pytest.LogCaptureFixture) -> None:
        df = pl.DataFrame({"x": [1, 2, 3]})
        cache = _trace_shaped_cache(max_bytes=1)

        with caplog.at_level(logging.WARNING, logger="haute._lru_cache"):
            cache.store("fp", **_trace_entry({"node": df}))

        assert any(
            record.message == "lru_cache_oversized_entry_not_cached" for record in caplog.records
        )


class TestTraceCacheEvictionDiscipline:
    def test_exact_budget_fit_is_retained(self) -> None:
        df_a = pl.DataFrame({"x": [1, 2, 3]})
        df_b = pl.DataFrame({"x": [4, 5, 6]})
        budget = df_a.estimated_size() + df_b.estimated_size()
        cache = _trace_shaped_cache(max_bytes=budget)

        cache.store("a", **_trace_entry({"n": df_a}))
        cache.store("b", **_trace_entry({"n": df_b}))

        assert cache.try_get("a") is not None
        assert cache.try_get("b") is not None
        assert cache.stats()["bytes"] == budget  # exactly at budget: no eviction

        # One more byte of demand evicts the LRU entry ("a", accessed first).
        cache.store("c", **_trace_entry({"n": pl.DataFrame({"x": [7]})}))
        assert cache.try_get("a") is None
        assert cache.try_get("b") is not None
        assert cache.try_get("c") is not None
        assert cache.stats()["bytes"] <= budget

    def test_eviction_is_lru_and_preserves_most_recently_used(self) -> None:
        df = pl.DataFrame({"x": [1, 2, 3]})
        cache = _trace_shaped_cache(max_bytes=2 * df.estimated_size())

        cache.store("first", **_trace_entry({"n": df}))
        cache.store("second", **_trace_entry({"n": df}))
        # Promote "first" to MRU — "second" becomes the LRU candidate.
        assert cache.try_get("first") is not None

        cache.store("third", **_trace_entry({"n": df}))

        assert cache.try_get("second") is None, "LRU entry must be the one evicted"
        assert cache.try_get("first") is not None, "recently-used trace must survive"
        assert cache.try_get("third") is not None, (
            "the just-written trace must survive its own insertion"
        )

    def test_count_bound_still_enforced_alongside_byte_budget(self) -> None:
        df = pl.DataFrame({"x": [1]})
        cache = _trace_shaped_cache(max_bytes=1_000_000, max_entries=2)

        for fp in ("a", "b", "c"):
            cache.store(fp, **_trace_entry({"n": df}))

        assert len(cache) == 2
        assert cache.try_get("a") is None
        assert cache.try_get("b") is not None
        assert cache.try_get("c") is not None


class TestTraceEntryByteEstimation:
    def test_entry_size_counts_only_materialized_frames(self) -> None:
        df = pl.DataFrame({"x": [1, 2, 3]})
        entry = _trace_entry({"t": df})
        # Metadata slots (order/parents_of/node_map/source_ids) carry no
        # byte weight — only the materialized frames are budgeted.
        entry["node_map"] = {"t": object()}

        assert _estimate_preview_cache_entry_bytes(entry) == df.estimated_size()

    def test_entry_size_counts_multi_port_source_bundles(self) -> None:
        df_a = pl.DataFrame({"x": [1, 2]})
        df_b = pl.DataFrame({"y": ["alpha", "beta", "gamma"]})
        entry = _trace_entry({"api": {"policies": df_a, "drivers": df_b}})

        assert _estimate_preview_cache_entry_bytes(entry) == (
            df_a.estimated_size() + df_b.estimated_size()
        )


class TestExecuteTraceUnderByteBudget:
    def test_trace_result_unaffected_when_frames_exceed_budget(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        graph = _simple_graph(tmp_path)
        # Every real frame is oversized for a 1-byte budget: each store is
        # rejected, so every trace click re-executes — slower, never wrong.
        tiny = _trace_shaped_cache(max_bytes=1)
        monkeypatch.setattr("haute.trace._cache", tiny)

        first = execute_trace(graph, row_index=0, target_node_id="t", column="z")
        assert first.output_value == 11

        stats = tiny.stats()
        assert stats["entries"] == 0
        assert stats["bytes"] == 0

        second = execute_trace(graph, row_index=1, target_node_id="t", column="z")
        assert second.output_value == 22

    def test_trace_populates_byte_accounted_cache_and_survives(self, tmp_path) -> None:
        graph = _simple_graph(tmp_path)

        r0 = execute_trace(graph, row_index=0, target_node_id="t", column="z")
        assert r0.output_value == 11

        stats = _trace_cache.stats()
        assert stats["entries"] == 1
        assert 0 < stats["bytes"] <= stats["max_bytes"]

        # The flagship click-different-cells flow: the just-stored entry is
        # reused for the next row instead of being re-stored or evicted.
        r1 = execute_trace(graph, row_index=1, target_node_id="t", column="z")
        assert r1.output_value == 22
        after = _trace_cache.stats()
        assert after["entries"] == 1
        assert after["bytes"] == stats["bytes"]
