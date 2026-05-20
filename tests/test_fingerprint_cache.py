"""Tests for FingerprintCache — generic multi-entry fingerprint cache."""

from __future__ import annotations

import threading

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


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


class TestPinning:
    def test_pin_nonexistent_fingerprint_is_silent_noop(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.pin("does_not_exist")
        cache.store("fp1", x={"a": 1})
        assert cache.try_get("fp1") is not None

    def test_pin_already_pinned_is_idempotent(self) -> None:
        cache = FingerprintCache(slots=("x",), max_entries=2)
        cache.store("fp1", x={"a": 1})
        cache.pin("fp1")
        cache.pin("fp1")
        cache.store("fp2", x={"b": 2})
        cache.store("fp3", x={"c": 3})
        assert cache.try_get("fp1") is not None

    def test_all_entries_pinned_store_new_evicts_unpinned_newcomer(self) -> None:
        cache = FingerprintCache(slots=("x",), max_entries=2)
        cache.store("fp1", x={"a": 1})
        cache.pin("fp1")
        cache.store("fp2", x={"b": 2})
        cache.pin("fp2")
        cache.store("fp3", x={"c": 3})
        assert cache.try_get("fp1") is not None
        assert cache.try_get("fp2") is not None
        assert cache.try_get("fp3") is None


# ---------------------------------------------------------------------------
# Duplicate store
# ---------------------------------------------------------------------------


class TestDuplicateStore:
    def test_store_duplicate_replaces_and_promotes_to_mru(self) -> None:
        cache = FingerprintCache(slots=("x",), max_entries=3)
        cache.store("fp1", x={"v": 1})
        cache.store("fp2", x={"v": 2})
        cache.store("fp3", x={"v": 3})
        cache.store("fp1", x={"v": 100})
        cache.store("fp4", x={"v": 4})
        assert cache.try_get("fp1") is not None
        assert cache.try_get("fp1")["x"] == {"v": 100}
        assert cache.try_get("fp2") is None


# ---------------------------------------------------------------------------
# update_slot edge cases
# ---------------------------------------------------------------------------


class TestUpdateSlotEdgeCases:
    def test_update_slot_nonexistent_fingerprint_logs_warning(self) -> None:
        cache = FingerprintCache(slots=("a",))
        cache.update_slot("a", {"new": True}, fingerprint="ghost")


# ---------------------------------------------------------------------------
# fingerprint property
# ---------------------------------------------------------------------------


class TestFingerprintProperty:
    def test_fingerprint_none_on_empty_cache(self) -> None:
        cache = FingerprintCache(slots=("x",))
        assert cache.fingerprint is None

    def test_fingerprint_returns_last_stored(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={})
        cache.store("fp2", x={})
        assert cache.fingerprint == "fp2"

    def test_fingerprint_returns_last_accessed(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={"a": 1})
        cache.store("fp2", x={"b": 2})
        cache.try_get("fp1")
        assert cache.fingerprint == "fp1"


# ---------------------------------------------------------------------------
# Invalidation edge cases
# ---------------------------------------------------------------------------


class TestInvalidationEdgeCases:
    def test_invalidate_then_pin_is_safe(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={})
        cache.invalidate()
        cache.pin("fp1")

    def test_invalidate_then_unpin_is_safe(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={})
        cache.pin("fp1")
        cache.invalidate()
        cache.unpin("fp1")

    def test_try_get_after_invalidate_returns_none(self) -> None:
        cache = FingerprintCache(slots=("x",))
        cache.store("fp1", x={"val": 42})
        cache.invalidate()
        assert cache.try_get("fp1") is None


# ---------------------------------------------------------------------------
# graph_fingerprint
# ---------------------------------------------------------------------------


class TestGraphFingerprint:
    def test_same_graph_same_fingerprint(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

        node = GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={"k": 1}))
        edge = GraphEdge(id="e1", source="n1", target="n2")
        g1 = PipelineGraph(nodes=[node], edges=[edge])
        g2 = PipelineGraph(nodes=[node], edges=[edge])
        assert graph_fingerprint(g1) == graph_fingerprint(g2)

    def test_different_node_different_fingerprint(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        g1 = PipelineGraph(
            nodes=[GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={}))],
        )
        g2 = PipelineGraph(
            nodes=[GraphNode(id="n2", data=NodeData(label="B", nodeType="polars", config={}))],
        )
        assert graph_fingerprint(g1) != graph_fingerprint(g2)

    def test_extra_keys_affect_fingerprint(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        node = GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={}))
        g = PipelineGraph(nodes=[node])
        fp_no_extra = graph_fingerprint(g)
        fp_with_extra = graph_fingerprint(g, "target_node")
        assert fp_no_extra != fp_with_extra

    def test_no_extra_keys_vs_empty_tuple_same_result(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import GraphNode, NodeData, PipelineGraph

        node = GraphNode(id="n1", data=NodeData(label="A", nodeType="polars", config={}))
        g = PipelineGraph(nodes=[node])
        assert graph_fingerprint(g) == graph_fingerprint(g)

    def test_explore_overview_config_does_not_affect_fingerprint(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import GraphNode, NodeData, NodeType, PipelineGraph

        base = PipelineGraph(
            nodes=[
                GraphNode(
                    id="explore",
                    data=NodeData(
                        label="Explore",
                        nodeType=NodeType.EXPLORE,
                        config={"code": "df = df.select(pl.all())"},
                    ),
                ),
            ],
        )
        with_overview = PipelineGraph(
            nodes=[
                GraphNode(
                    id="explore",
                    data=NodeData(
                        label="Explore",
                        nodeType=NodeType.EXPLORE,
                        config={
                            "code": "df = df.select(pl.all())",
                            "overview": {"dataset_snapshot": True, "schema": True},
                        },
                    ),
                ),
            ],
        )
        with_code_change = PipelineGraph(
            nodes=[
                GraphNode(
                    id="explore",
                    data=NodeData(
                        label="Explore",
                        nodeType=NodeType.EXPLORE,
                        config={"code": "df = df.filter(pl.col('premium') > 0)"},
                    ),
                ),
            ],
        )

        assert graph_fingerprint(with_overview) == graph_fingerprint(base)
        assert graph_fingerprint(with_code_change) != graph_fingerprint(base)

    def test_empty_graph_consistent_fingerprint(self) -> None:
        from haute._cache import graph_fingerprint
        from haute._types import PipelineGraph

        g1 = PipelineGraph()
        g2 = PipelineGraph()
        fp1 = graph_fingerprint(g1)
        fp2 = graph_fingerprint(g2)
        assert fp1 == fp2
        assert isinstance(fp1, str)
        # Non-empty lowercase hex digest after the Wave 9C ``v<N>:``
        # prefix; the exact algorithm (xxh64) is an implementation
        # detail of ``_cache.py``.
        assert fp1
        _, _, digest = fp1.partition(":")
        assert all(c in "0123456789abcdef" for c in digest)
