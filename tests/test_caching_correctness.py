"""Tests for Phase 1 Package 1D — caching correctness.

Covers two orthogonal defects in the current cache layer:

Item #9 — ``_graph_base_fingerprint`` (``src/haute/_cache.py``) uses
``json.dumps(..., default=repr)`` to serialize node configs.  ``repr()`` is
non-deterministic for unordered containers (``set``, ``frozenset``) and
silently masks distinct objects whose ``__repr__`` happens to collide.
Post-fix, the fingerprint function must:

  * Canonicalize unordered containers (sort element list) so logically
    equal sets produce equal fingerprints.
  * Raise ``TypeError`` for types it cannot serialize deterministically
    rather than falling back to ``repr``.

Item #10 — ``load_external_object`` and ``load_optimiser_artifact`` key
their caches on ``(path, mtime, ...)``.  This is TOCTOU-racy: a same-second
overwrite does not bump mtime, so the cache serves stale content.  Post-fix,
both functions must key on the xxh64 **content hash** produced by
``haute._hashing.content_hash``.

All tests in this module are expected to fail pre-fix and pass post-fix
(with the exception of the regression-guard tests, which must continue to
pass post-fix).
"""

from __future__ import annotations

import json
import os
import time as _time
from pathlib import Path

import pytest

from haute._cache import graph_fingerprint
from haute._io import _object_cache, load_external_object
from haute._optimiser_io import _artifact_cache, load_optimiser_artifact
from haute._types import GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ensure a clean slate between tests for both object caches."""
    _object_cache.clear()
    _artifact_cache.clear()
    yield
    _object_cache.clear()
    _artifact_cache.clear()


def _make_graph(config: dict) -> PipelineGraph:
    """Build a minimal one-node graph with the given node config."""
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(label="A", nodeType="polars", config=config),
            ),
        ],
    )


# ===========================================================================
# Item #9 — graph_fingerprint determinism & collision safety
# ===========================================================================


class TestFingerprintSetOrderDeterminism:
    """A graph config containing a set of strings must always hash the same
    regardless of element insertion order.

    Pre-fix, ``json.dumps(..., default=repr)`` serialises a set via its
    ``repr()``, whose element order depends on hash seeding — so two
    logically equal sets can produce different digests.

    Post-fix, the serialiser canonicalises unordered containers, so the
    fingerprint of a graph whose config holds a set equals the fingerprint
    of a graph whose config holds the sorted-list equivalent.
    """

    def test_set_and_sorted_list_produce_equal_fingerprints(self) -> None:
        """A ``set`` must be canonicalised to its sorted-list form.

        This is the key pre-fix failure: ``repr({'a','b','c'})`` is
        ``"{'a', 'b', 'c'}"`` (order varies) and will not equal
        ``json.dumps(['a','b','c'])`` which is ``'["a", "b", "c"]'``.
        """
        g_set = _make_graph({"tags": {"alpha", "beta", "gamma"}})
        g_list = _make_graph({"tags": ["alpha", "beta", "gamma"]})
        assert graph_fingerprint(g_set) == graph_fingerprint(g_list)

    def test_set_insertion_order_independence(self) -> None:
        """Two sets built with reversed insertion order must hash equal.

        Even when a single process happens to iterate both sets in the
        same order (CPython string-hash quirk), the digest must match
        the canonical (sorted) form.
        """
        fwd: set[str] = set()
        for s in ("alpha", "beta", "gamma"):
            fwd.add(s)
        rev: set[str] = set()
        for s in ("gamma", "beta", "alpha"):
            rev.add(s)

        g1 = _make_graph({"cols": fwd})
        g2 = _make_graph({"cols": rev})
        assert graph_fingerprint(g1) == graph_fingerprint(g2)

    def test_frozenset_also_canonicalised(self) -> None:
        """``frozenset`` has the same ordering issue — must be canonical."""
        g_frozen = _make_graph({"keys": frozenset({"x", "y", "z"})})
        g_list = _make_graph({"keys": ["x", "y", "z"]})
        assert graph_fingerprint(g_frozen) == graph_fingerprint(g_list)

    def test_repeated_call_same_process_stable(self) -> None:
        """Calling ``graph_fingerprint`` twice in the same process on the same
        set-containing config must yield the same digest.

        This is a regression guard to ensure the canonicalisation is
        deterministic (not randomised per-call).
        """
        g = _make_graph({"tags": {"a", "b", "c"}})
        assert graph_fingerprint(g) == graph_fingerprint(g)


class TestFingerprintCollisionSafety:
    """Two semantically distinct configs must not share a fingerprint just
    because their ``repr()`` collides.
    """

    def test_distinct_objects_with_identical_repr_do_not_collide(self) -> None:
        """Two user classes with the same ``__repr__`` string must not
        produce the same fingerprint.

        Pre-fix, ``default=repr`` reduces both objects to the same string,
        so the digests match.  Post-fix, objects whose type is not handled
        explicitly raise ``TypeError`` (see ``TestFingerprintTypeErrorForUnknown``)
        — which is checked by the ``pytest.raises`` wrapper — so the
        fingerprints cannot silently collide.
        """

        class Ghost:
            def __repr__(self) -> str:
                return "IDENTICAL"

        class Phantom:
            def __repr__(self) -> str:
                return "IDENTICAL"

        g_ghost = _make_graph({"obj": Ghost()})
        g_phantom = _make_graph({"obj": Phantom()})

        # Post-fix: unsupported types raise.  We run each call separately
        # so both sides are exercised.
        with pytest.raises(TypeError):
            graph_fingerprint(g_ghost)
        with pytest.raises(TypeError):
            graph_fingerprint(g_phantom)

    def test_set_does_not_collide_with_string_of_same_repr(self) -> None:
        """A ``set`` value and a ``str`` whose text equals the set's ``repr``
        are logically different and must produce different fingerprints.

        Pre-fix, the set goes through ``default=repr`` and becomes the
        literal string ``"{'a'}"`` — identical to the string config.
        Post-fix, the set is canonicalised to a sorted list (JSON array),
        which has a different JSON encoding than the string.
        """
        g_set = _make_graph({"x": {"a"}})
        g_str = _make_graph({"x": "{'a'}"})
        assert graph_fingerprint(g_set) != graph_fingerprint(g_str)


class TestFingerprintTypeErrorForUnknown:
    """Unknown non-JSON-serializable types must raise ``TypeError`` loudly
    rather than silently fall back to ``repr``.

    This guarantees the developer hears about a drift in config shape
    immediately instead of wondering why two configs hash the same or
    differently across runs.
    """

    def test_arbitrary_class_instance_raises_type_error(self) -> None:
        """A config value that is a user-defined class instance must raise."""

        class NotJsonable:
            pass

        g = _make_graph({"bad": NotJsonable()})
        with pytest.raises(TypeError):
            graph_fingerprint(g)

    def test_bytes_value_raises_type_error(self) -> None:
        """``bytes`` is not JSON-serialisable and has no canonical text form."""
        g = _make_graph({"payload": b"\x00\x01\x02"})
        with pytest.raises(TypeError):
            graph_fingerprint(g)

    def test_function_value_raises_type_error(self) -> None:
        """Functions embedded in a config have no stable serialisation."""

        def _noop() -> None:
            return None

        g = _make_graph({"callback": _noop})
        with pytest.raises(TypeError):
            graph_fingerprint(g)

    def test_complex_number_raises_type_error(self) -> None:
        """``complex`` is not JSON-serialisable; pre-fix it silently reprs.

        Using ``complex`` guards against a fix that whitelists only known
        numeric types while still rejecting opaque ones.
        """
        g = _make_graph({"impedance": complex(1, 2)})
        with pytest.raises(TypeError):
            graph_fingerprint(g)

    def test_supported_json_types_still_work(self) -> None:
        """Regression guard: strings, numbers, bools, None, lists, and dicts
        must continue to serialise successfully after the fix.
        """
        g = _make_graph(
            {
                "s": "hello",
                "i": 42,
                "f": 3.14,
                "b": True,
                "n": None,
                "lst": [1, "two", False],
                "nested": {"a": [1, 2], "b": "ok"},
            },
        )
        # Must not raise; must produce a deterministic digest.
        fp1 = graph_fingerprint(g)
        fp2 = graph_fingerprint(g)
        assert fp1 == fp2
        assert len(fp1) == 64  # sha256 hex


# ===========================================================================
# Item #10 — content-hash cache keys (TOCTOU-safe)
# ===========================================================================


class TestLoadExternalObjectSameSecondOverwrite:
    """A file overwritten with different content but identical mtime must
    invalidate the cache.  Pre-fix, ``(path, mtime, ...)`` match serves
    stale content.  Post-fix, the key uses ``content_hash`` which differs
    because the bytes differ.
    """

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_same_mtime_different_content_invalidates(self, tmp_path: Path) -> None:
        """The TOCTOU bug: same-second overwrite keeps mtime but changes
        content.  Cache must see the new content."""
        path = tmp_path / "model.json"
        path.write_text(json.dumps({"v": 1}))

        # Populate the cache with version 1.
        r1 = load_external_object(str(path), "json")
        assert r1 == {"v": 1}

        # Snapshot mtime, overwrite with different content, then force
        # mtime back to what it was — simulating a same-second external
        # write where the OS didn't advance mtime.
        original_mtime = os.path.getmtime(str(path))
        path.write_text(json.dumps({"v": 2}))
        os.utime(str(path), (original_mtime, original_mtime))

        # Sanity: mtime is unchanged by the overwrite+utime sequence.
        # A tiny float-rounding drift is tolerated.
        assert abs(os.path.getmtime(str(path)) - original_mtime) < 1e-6

        # Post-fix: content hash differs, so cache misses and returns v2.
        # Pre-fix: mtime-based key is identical, so stale v1 is served.
        r2 = load_external_object(str(path), "json")
        assert r2 == {"v": 2}, (
            "Cache served stale content after same-second overwrite — "
            "key must be content-based, not mtime-based"
        )

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_truncate_to_same_length_same_mtime_invalidates(
        self,
        tmp_path: Path,
    ) -> None:
        """Different content of the same length and same mtime still
        invalidates the cache.

        Pre-fix, even a file-size check wouldn't catch this — only a
        content hash will.
        """
        path = tmp_path / "data.json"
        path.write_text('{"a": 1, "b": 2}')  # 16 bytes

        r1 = load_external_object(str(path), "json")
        assert r1 == {"a": 1, "b": 2}

        original_mtime = os.path.getmtime(str(path))
        path.write_text('{"a": 9, "b": 8}')  # also 16 bytes
        os.utime(str(path), (original_mtime, original_mtime))

        r2 = load_external_object(str(path), "json")
        assert r2 == {"a": 9, "b": 8}


class TestLoadExternalObjectMtimeChangeStillInvalidates:
    """Regression guard: the fix must preserve the existing mtime-based
    invalidation — i.e., content-hash keying must not *also* hide real
    changes that happen to bump mtime.
    """

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_mtime_bump_with_new_content_invalidates(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        path.write_text('{"v": 1}')

        r1 = load_external_object(str(path), "json")
        assert r1 == {"v": 1}

        # Delay briefly so mtime advances on coarse-grained filesystems,
        # then overwrite.  Use os.utime to force a far-future mtime so
        # the test is robust against mtime granularity issues.
        future = _time.time() + 10
        path.write_text('{"v": 2}')
        os.utime(str(path), (future, future))

        r2 = load_external_object(str(path), "json")
        assert r2 == {"v": 2}


class TestLoadExternalObjectUnchangedUsesCache:
    """Regression guard: reading the same unchanged file twice must hit the
    cache.  We verify by object identity — the second call should return
    the exact same object reference put into the cache.
    """

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_unchanged_file_returns_cached_object(self, tmp_path: Path) -> None:
        path = tmp_path / "stable.json"
        path.write_text('{"immutable": true}')

        r1 = load_external_object(str(path), "json")
        r2 = load_external_object(str(path), "json")

        # Object identity proves the cache was used (no re-parse).
        assert r1 is r2
        # Cache should contain exactly one entry.
        assert len(_object_cache) == 1


class TestLoadOptimiserArtifactSameSecondOverwrite:
    """Same TOCTOU fix required for the optimiser artifact loader."""

    def test_same_mtime_different_content_invalidates(self, tmp_path: Path) -> None:
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps({"version": 1, "mode": "online"}))

        r1 = load_optimiser_artifact(str(path))
        assert r1["version"] == 1

        original_mtime = os.path.getmtime(str(path))
        path.write_text(json.dumps({"version": 2, "mode": "online"}))
        os.utime(str(path), (original_mtime, original_mtime))

        # A tiny float-rounding drift in os.utime round-trip is tolerated.
        assert abs(os.path.getmtime(str(path)) - original_mtime) < 1e-6

        r2 = load_optimiser_artifact(str(path))
        assert r2["version"] == 2, (
            "Optimiser artifact cache served stale content after "
            "same-second overwrite — key must be content-based"
        )

    def test_truncate_to_same_length_same_mtime_invalidates(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "artifact.json"
        path.write_text('{"mode": "online", "lambdas": {"a": 1.0}}')

        r1 = load_optimiser_artifact(str(path))
        assert r1["lambdas"] == {"a": 1.0}

        original_mtime = os.path.getmtime(str(path))
        # New content of the same length (43 chars) and same mtime.
        path.write_text('{"mode": "online", "lambdas": {"a": 9.0}}')
        os.utime(str(path), (original_mtime, original_mtime))

        r2 = load_optimiser_artifact(str(path))
        assert r2["lambdas"] == {"a": 9.0}


class TestLoadOptimiserArtifactMtimeChangeStillInvalidates:
    """Regression guard for the optimiser-artifact cache."""

    def test_mtime_bump_with_new_content_invalidates(self, tmp_path: Path) -> None:
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps({"version": 1}))

        r1 = load_optimiser_artifact(str(path))
        assert r1["version"] == 1

        future = _time.time() + 10
        path.write_text(json.dumps({"version": 2}))
        os.utime(str(path), (future, future))

        r2 = load_optimiser_artifact(str(path))
        assert r2["version"] == 2


class TestLoadOptimiserArtifactUnchangedUsesCache:
    """Regression guard: unchanged file → cache hit.

    The optimiser loader deep-copies the cached dict on each call, so we
    can't compare by object identity.  Instead, we verify that the cache
    contains exactly one entry after two reads and that both reads return
    equal content.
    """

    def test_unchanged_file_produces_single_cache_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text(json.dumps({"mode": "online"}))

        r1 = load_optimiser_artifact(str(path))
        r2 = load_optimiser_artifact(str(path))

        assert r1 == r2
        # Two reads of the same unchanged file must yield exactly one
        # cache entry — if the key were non-deterministic, there'd be two.
        assert len(_artifact_cache) == 1


# ===========================================================================
# End-to-end cross-function guarantee
# ===========================================================================


class TestCacheKeyStability:
    """Sanity: repeated reads of the same path share a cache entry even
    across multiple interleaved calls.  Failing here would indicate a
    non-deterministic key (e.g. float mtime with rounding drift, or a
    content hash that isn't stable for the same bytes).
    """

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_many_reads_produce_one_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "stable.json"
        path.write_text('{"k": "v"}')
        for _ in range(5):
            load_external_object(str(path), "json")
        assert len(_object_cache) == 1

    def test_many_optimiser_reads_produce_one_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "opt.json"
        path.write_text(json.dumps({"mode": "ratebook"}))
        for _ in range(5):
            load_optimiser_artifact(str(path))
        assert len(_artifact_cache) == 1
