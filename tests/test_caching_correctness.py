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

Code-review remediation W1.2 (finding C3) — ``_graph_base_fingerprint``
serialised edges as ``"{source}->{target}"`` only, omitting
``sourceHandle``/``targetHandle``.  Rewiring which PORT of a multi-port
apiInput (or which edge-join role) feeds a consumer therefore produced an
identical fingerprint, and the preview/trace/dataframe caches silently
served the old wiring's data.  Post-fix, both handles are part of the edge
serialization (see ``TestFingerprintEdgeHandleSensitivity``).

Code-review remediation W2.13 — two canonical-JSON encoders with divergent
rules lived in ``_cache.py`` (``_canonicalise`` + spaced ``json.dumps``)
and ``_dataframe_execution_cache.py`` (``_normalise_execution_policy`` +
compact ``json.dumps``).  They disagreed on set-member ordering (numeric
vs JSON-text-lexicographic), accepted container types (dict-only vs any
Mapping/Iterable), empty-string mapping keys, and serialization
separators.  Post-fix there is exactly ONE encoder —
``haute._cache.canonical_json`` — used by every digest site in both
modules (see ``TestCanonicalJsonEncoder`` / ``TestCanonicalJsonProperties``
/ ``TestCanonicalEncoderUnification`` here and the policy-fingerprint
contract tests in ``test_dataframe_execution_cache.py``).  Because the
node-config digest bytes changed (compact separators), ``ALGO_VERSION``
bumped 4 → 5.

All tests in this module are expected to fail pre-fix and pass post-fix
(with the exception of the regression-guard tests, which must continue to
pass post-fix).
"""

from __future__ import annotations

import copy
import json
import os
import time as _time
from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from haute._cache import canonical_json, graph_fingerprint, preamble_imports_utility
from haute._io import _load_cached, load_external_object
from haute._optimiser_io import _load_artifact_cached, load_optimiser_artifact
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _object_cache_size() -> int:
    return _load_cached.cache_info().currsize


def _artifact_cache_size() -> int:
    return _load_artifact_cached.cache_info().currsize


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ensure a clean slate between tests for both object caches."""
    _load_cached.cache_clear()
    _load_artifact_cached.cache_clear()
    yield
    _load_cached.cache_clear()
    _load_artifact_cached.cache_clear()


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


def _make_wired_graph(edges: list[GraphEdge]) -> PipelineGraph:
    """Two-node graph whose only variation across tests is the edge wiring."""
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(label="Quotes", nodeType="apiInput", config={}),
            ),
            GraphNode(
                id="rate",
                data=NodeData(label="Rate", nodeType="polars", config={}),
            ),
        ],
        edges=edges,
    )


class TestFingerprintEdgeHandleSensitivity:
    """Rewiring WHICH PORT an edge connects must change the fingerprint.

    ``sourceHandle``/``targetHandle`` select which port of a multi-port
    apiInput (or which edge-join role) feeds a consumer.  Pre-fix,
    ``_graph_base_fingerprint`` serialised edges as ``"{source}->{target}"``
    only, so rewiring port ``policies`` → ``drivers`` between the same two
    nodes produced an IDENTICAL fingerprint and the preview/trace/dataframe
    caches silently served the old wiring's data.
    """

    def test_target_handle_rewire_changes_fingerprint(self) -> None:
        """Same nodes, same edge endpoints — only ``targetHandle`` differs."""
        g_policies = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", targetHandle="policies")],
        )
        g_drivers = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", targetHandle="drivers")],
        )
        assert graph_fingerprint(g_policies) != graph_fingerprint(g_drivers)

    def test_source_handle_rewire_changes_fingerprint(self) -> None:
        """Same nodes, same edge endpoints — only ``sourceHandle`` differs."""
        g_policies = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", sourceHandle="policies")],
        )
        g_drivers = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", sourceHandle="drivers")],
        )
        assert graph_fingerprint(g_policies) != graph_fingerprint(g_drivers)

    @pytest.mark.parametrize("handle_field", ["sourceHandle", "targetHandle"])
    def test_none_handle_differs_from_named_handle(self, handle_field: str) -> None:
        """A port-less edge and a port-wired edge are different wirings."""
        g_none = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate")],
        )
        g_named = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", **{handle_field: "policies"})],
        )
        assert graph_fingerprint(g_none) != graph_fingerprint(g_named)

    @pytest.mark.parametrize("literal", ["null", "None"])
    def test_none_handle_differs_from_handle_named_like_null(self, literal: str) -> None:
        """``None`` must be distinguishable from ANY real handle string.

        A naive ``str(handle)`` interpolation would collide ``None`` with a
        port literally named ``"None"`` (and a naive JSON-text splice would
        collide it with ``"null"``).  The serialization must keep the absent
        handle distinct from both.
        """
        g_none = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate")],
        )
        g_literal = _make_wired_graph(
            [GraphEdge(id="e1", source="quotes", target="rate", targetHandle=literal)],
        )
        assert graph_fingerprint(g_none) != graph_fingerprint(g_literal)

    def test_edge_insertion_order_is_irrelevant(self) -> None:
        """Shuffled edge insertion order must not move the fingerprint.

        Uses the edge-join shape — two parallel edges between the SAME node
        pair where only the handles differ — so the sort cannot fall back on
        ``(source, target)`` alone to break the tie deterministically.
        """
        e_base = GraphEdge(id="e1", source="quotes", target="rate", targetHandle="base")
        e_join = GraphEdge(id="e2", source="quotes", target="rate", targetHandle="join")
        e_plain = GraphEdge(id="e3", source="quotes", target="rate")

        fingerprints = {
            graph_fingerprint(_make_wired_graph(list(order)))
            for order in (
                (e_base, e_join, e_plain),
                (e_join, e_plain, e_base),
                (e_plain, e_base, e_join),
            )
        }
        assert len(fingerprints) == 1

    def test_algo_version_bumped_for_handle_aware_serialization(self) -> None:
        """The digest material changed — pre-handle cache entries must be
        invalidated via an ``ALGO_VERSION`` bump (3 → 4), not left to
        coincidence.  Guards against reverting the bump while keeping the
        serialization change.
        """
        from haute._cache import ALGO_VERSION

        assert ALGO_VERSION >= 4


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
        # xxh64 hex digest after the Wave 9C ``v<N>:`` prefix — the
        # exact algorithm is an implementation detail, but the digest
        # portion must be a non-empty hex string.
        assert fp1
        _, _, digest = fp1.partition(":")
        assert all(c in "0123456789abcdef" for c in digest)


# ===========================================================================
# W2.13 — one canonical-JSON encoder for all digest material
# ===========================================================================


class TestCanonicalJsonEncoder:
    """Unit pins of the ONE canonical encoding, byte for byte.

    Each pinned string is the single behavior that resolved a divergence
    between the two retired encoders (``_cache._canonicalise`` + spaced
    dumps vs ``_dataframe_execution_cache._normalise_execution_policy`` +
    compact dumps).
    """

    def test_compact_sorted_ascii_serialization(self) -> None:
        """Compact separators, code-point-sorted keys, ASCII escapes."""
        value = {"b": 1, "a": {"y": 2.5, "x": [1, True, None, "é"]}}
        assert canonical_json(value) == '{"a":{"x":[1,true,null,"\\u00e9"],"y":2.5},"b":1}'

    def test_set_members_sort_numerically_not_by_json_text(self) -> None:
        """The retired dfexec encoder sorted by JSON text: [0, 1, 10, 2]."""
        assert canonical_json({"s": {0, 1, 2, 10}}) == '{"s":[0,1,2,10]}'

    def test_set_members_order_none_bool_number_string_by_type_tag(self) -> None:
        """The retired dfexec encoder produced ["a", 5, false, true, null]."""
        assert canonical_json({"s": {None, False, True, 5, "a"}}) == (
            '{"s":[null,false,true,5,"a"]}'
        )

    def test_set_strings_sort_by_code_point_not_escape_text(self) -> None:
        """The retired dfexec encoder sorted by escaped text: ["é", "z"]."""
        assert canonical_json({"s": {"é", "z"}}) == '{"s":["z","\\u00e9"]}'

    def test_set_nested_containers_sort_by_canonical_encoding(self) -> None:
        assert canonical_json({"s": {(1, 2), (1, "x")}}) == '{"s":[[1,"x"],[1,2]]}'

    def test_frozenset_equals_set(self) -> None:
        assert canonical_json(frozenset({3, 1, 2})) == canonical_json({1, 2, 3})

    def test_any_mapping_encodes_like_a_plain_dict(self) -> None:
        """The retired graph encoder rejected non-dict Mappings outright."""
        plain = {"a": 1, "b": [2, 3]}
        assert canonical_json(MappingProxyType(plain)) == canonical_json(plain)
        assert canonical_json(OrderedDict(reversed(plain.items()))) == canonical_json(plain)

    def test_tuple_encodes_as_array_in_element_order(self) -> None:
        assert canonical_json((1, "two", None)) == '[1,"two",null]'

    def test_empty_string_key_is_valid_digest_material(self) -> None:
        """The retired dfexec encoder raised ``ValueError`` for ``""`` keys.

        Unified rule: the empty string is a legal, deterministic JSON
        object key (user node configs can legitimately carry one — e.g. a
        rename map for a column literally named ``""``), so the encoder
        accepts it everywhere.
        """
        assert canonical_json({"": 1}) == '{"":1}'

    def test_bool_and_int_have_distinct_encodings(self) -> None:
        assert canonical_json(True) == "true"
        assert canonical_json(1) == "1"

    def test_int_and_float_of_equal_value_stay_distinct(self) -> None:
        assert canonical_json(1) == "1"
        assert canonical_json(1.0) == "1.0"

    def test_float_text_forms_are_shortest_repr(self) -> None:
        assert canonical_json(0.1) == "0.1"
        assert canonical_json(-0.0) == "-0.0"
        assert canonical_json(1e300) == "1e+300"

    def test_non_finite_floats_encode_deterministically(self) -> None:
        """Digest material, not interchange JSON: ``inf`` (e.g. an open
        banding upper bound) must not crash fingerprinting and must
        serialize to a fixed token."""
        assert canonical_json(float("inf")) == "Infinity"
        assert canonical_json(float("-inf")) == "-Infinity"

    def test_non_string_mapping_keys_raise_type_error(self) -> None:
        with pytest.raises(TypeError, match="non-string key"):
            canonical_json({1: "x"})

    @pytest.mark.parametrize(
        "value",
        [
            iter([1, 2, 3]),
            (i for i in range(3)),
            range(3),
        ],
        ids=["iterator", "generator", "range"],
    )
    def test_arbitrary_iterables_raise_type_error(self, value: object) -> None:
        """The retired dfexec encoder silently consumed ANY iterable —
        which would let one-shot iterators or NumPy arrays masquerade as
        digest material.  Unified rule: only ``list``/``tuple``."""
        with pytest.raises(TypeError, match="no deterministic canonical form"):
            canonical_json(value)

    @pytest.mark.parametrize(
        "value",
        [b"\x00\x01", complex(1, 2), object()],
        ids=["bytes", "complex", "object"],
    )
    def test_unsupported_types_raise_type_error_naming_the_type(self, value: object) -> None:
        with pytest.raises(TypeError, match=type(value).__name__):
            canonical_json(value)


# Scalars the canonical encoder accepts, including non-ASCII text and
# non-finite floats (NaN is excluded: ``nan != nan`` breaks value-level
# equality assertions, and set membership of NaN is identity-based).
_canonical_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**63), max_value=2**63),
    st.floats(allow_nan=False),
    st.text(),
)

_canonical_values = st.recursive(
    _canonical_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(), children, max_size=4),
    ),
    max_leaves=25,
)


class TestCanonicalJsonProperties:
    """Property tests: the encoder is a pure, order-independent function."""

    @given(value=_canonical_values)
    @settings(max_examples=60, deadline=None)
    def test_deterministic_across_calls_and_copies(self, value: object) -> None:
        assert canonical_json(value) == canonical_json(copy.deepcopy(value))

    @given(mapping=st.dictionaries(st.text(), _canonical_values, max_size=6))
    @settings(max_examples=60, deadline=None)
    def test_dict_key_insertion_order_is_irrelevant(self, mapping: dict[str, object]) -> None:
        reversed_insertion = dict(reversed(list(mapping.items())))
        assert canonical_json(reversed_insertion) == canonical_json(mapping)
        assert canonical_json(MappingProxyType(mapping)) == canonical_json(mapping)

    @given(value=_canonical_values)
    @settings(max_examples=60, deadline=None)
    def test_round_trip_re_encodes_to_identical_bytes(self, value: object) -> None:
        """Decoding the canonical text and re-encoding it is a fixed point."""
        encoded = canonical_json(value)
        assert canonical_json(json.loads(encoded)) == encoded

    @given(members=st.sets(_canonical_scalars, max_size=8))
    @settings(max_examples=60, deadline=None)
    def test_set_insertion_order_is_irrelevant(self, members: set[object]) -> None:
        ordered = list(members)
        forward: set[object] = set()
        for member in ordered:
            forward.add(member)
        backward: set[object] = set()
        for member in reversed(ordered):
            backward.add(member)
        assert canonical_json({"s": forward}) == canonical_json({"s": backward})

    @given(items=st.lists(_canonical_values, max_size=6))
    @settings(max_examples=60, deadline=None)
    def test_tuple_and_list_encode_identically(self, items: list[object]) -> None:
        assert canonical_json(tuple(items)) == canonical_json(items)


class TestCanonicalEncoderUnification:
    """The single encoder is what ``_graph_base_fingerprint`` embeds.

    Pre-unification the node-config part of the digest material was
    serialised with ``json.dumps(..., sort_keys=True)`` (default spaced
    separators) while every other digest site used compact separators —
    two serialization rules for one digest, and a second normaliser with
    different set-ordering rules lived in ``_dataframe_execution_cache``.
    """

    def test_node_config_digest_material_uses_compact_canonical_encoding(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The bytes fed to the digest must carry the canonical (compact,
        key-sorted, set-canonicalised) encoding — not the spaced one."""
        import haute._cache as cache_mod

        captured: list[bytes] = []
        real_hash = cache_mod.content_hash_bytes

        def _capturing(data: bytes) -> str:
            captured.append(data)
            return real_hash(data)

        monkeypatch.setattr(cache_mod, "content_hash_bytes", _capturing)
        g = _make_graph({"k": 1, "tags": {"b", "a"}})
        cache_mod._graph_base_fingerprint(g)

        assert captured, "digest material never reached content_hash_bytes"
        material = captured[0].decode()
        # The checked graph-structure object frames every field injectively.
        # The nested record marker is explicit, while config values retain
        # compact, key-sorted, set-canonicalised encoding.
        assert (
            '{"cache_record_schema":{"record":"graph_node","version":1},'
            '"config":{"k":1,"tags":["a","b"]},"id":"n1","label":"A","nodeType":"polars"}'
        ) in material, f"Node/config is not canonically encoded: {material!r}"
        # The spaced ``json.dumps`` form must never appear.
        assert '{"k": 1' not in material

    def test_algo_version_bumped_for_unified_canonical_encoder(self) -> None:
        """W2.13: the digest material changed again — node configs now
        serialize through the single canonical encoder (compact
        separators), so encoder unification must invalidate v4 cache
        entries via an ``ALGO_VERSION`` bump (4 → 5), not by luck.
        """
        from haute._cache import ALGO_VERSION

        assert ALGO_VERSION >= 5


# ===========================================================================
# Utility preamble import detection
# ===========================================================================


class TestPreambleUtilityImportDetection:
    """Utility-sensitive preambles must opt into utility file fingerprinting.

    Literal imports are straightforward to detect from the AST. Dynamic imports
    matter just as much for cache correctness because they can still read the
    project ``utility`` module while leaving no top-level import statement.
    """

    @pytest.mark.parametrize(
        "source",
        [
            '__import__("utility.helpers")\n',
            'import importlib\nhelpers = importlib.import_module("utility.helpers")\n',
            'exec("import utility.helpers")\n',
            'exec("from utility.helpers import VALUE")\n',
        ],
    )
    def test_dynamic_utility_imports_are_detected(self, source: str) -> None:
        assert preamble_imports_utility(source) is True

    @pytest.mark.parametrize(
        "source",
        [
            '__import__("json")\n',
            'import importlib\njson_module = importlib.import_module("json")\n',
            'exec("import json")\n',
            'helper_name = "utility.helpers"\n',
        ],
    )
    def test_non_import_utility_mentions_do_not_mark_utility_sensitive(
        self,
        source: str,
    ) -> None:
        assert preamble_imports_utility(source) is False


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
        assert _object_cache_size() == 1


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
        assert _artifact_cache_size() == 1


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
        assert _object_cache_size() == 1

    def test_many_optimiser_reads_produce_one_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "opt.json"
        path.write_text(json.dumps({"mode": "ratebook"}))
        for _ in range(5):
            load_optimiser_artifact(str(path))
        assert _artifact_cache_size() == 1
