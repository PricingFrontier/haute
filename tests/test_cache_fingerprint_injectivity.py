"""Injectivity + completeness guards for the graph-fingerprint encoder.

These pin the invariants named in the W1-cache audit cluster:

  * ``canonical_json`` is deterministic for unordered containers even when
    they contain ``NaN`` floats (F163).
  * The node-line and ``graph_fingerprint`` joins are **injective** — a node
    id (or extra key) that contains the internal field/record separators
    ``|`` / ``\\n`` can never collide with a logically-different graph (F164).
  * Every output-affecting input participates in the digest — a "fingerprint
    completeness" table (F563).
  * A ``utility`` module resolved via ``sys.path`` (not merely the pipeline
    dir / cwd) is still hashed, so edits to it invalidate caches (F013).

Written before the fix lands: the collision / non-determinism cases fail
against the unframed encoder and pass once framing + total-order sorting are
in place.
"""

from __future__ import annotations

import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._cache import (
    _sort_key,
    canonical_json,
    graph_fingerprint,
    preamble_execution_fingerprint,
)
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _node(nid: str, config: dict | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.POLARS, config=config or {}),
    )


# ---------------------------------------------------------------------------
# F163 — NaN-set determinism
# ---------------------------------------------------------------------------


class TestNaNSetDeterminism:
    def test_set_with_nan_canonicalises_insertion_order_independent(self) -> None:
        """A set mixing finite floats and several distinct ``NaN`` objects must
        canonicalise to a single, insertion-order-independent string.

        Distinct ``float('nan')`` objects all hash to 0, so their set-iteration
        order depends on insertion order — the exact vector that made the old
        ``_sort_key`` (which sorted NaN with all-False comparisons) produce
        different canonical JSON per insertion order.
        """
        finite = [-1.0, 0.5, 1.0, 2.0, 3.0, 10.0]
        outputs: set[str] = set()
        for _ in range(200):
            members = [*finite, float("nan"), float("nan"), float("nan")]
            random.shuffle(members)
            outputs.add(canonical_json(set(members)))
        assert len(outputs) == 1, f"NaN set canonicalisation is non-deterministic: {outputs}"

    def test_sort_key_totally_orders_nan_against_finite_and_inf(self) -> None:
        """``_sort_key`` must yield a total order: sorting any permutation of a
        list that includes NaN, +/-inf and finite numbers is stable."""
        base = [-1e9, -1.0, 0.0, 1.0, 1e9, float("-inf"), float("inf"), float("nan")]
        reference = sorted(base, key=_sort_key)
        for _ in range(100):
            shuffled = base[:]
            random.shuffle(shuffled)
            resorted = sorted(shuffled, key=_sort_key)
            # Compare via canonical bytes so NaN != NaN doesn't defeat ==.
            assert canonical_json(resorted) == canonical_json(reference)


# ---------------------------------------------------------------------------
# F164 — separator injectivity
# ---------------------------------------------------------------------------


class TestSeparatorInjectivity:
    def test_extra_key_newline_is_injective(self) -> None:
        """``("a\\nb",)`` and ``("a", "b")`` are distinct inputs — an unframed
        ``"\\n".join`` collides them into the same digest material."""
        g = PipelineGraph(nodes=[_node("n1")])
        assert graph_fingerprint(g, "a\nb") != graph_fingerprint(g, "a", "b")

    def test_node_id_pipe_newline_is_injective(self) -> None:
        """A single node whose id embeds the ``|``/``\\n`` separators must not
        collide with a two-node graph whose serialized lines concatenate to the
        same bytes under the old unframed scheme."""
        two_nodes = PipelineGraph(nodes=[_node("a"), _node("b")])
        one_node = PipelineGraph(nodes=[_node("a|polars|{}\nb")])
        assert graph_fingerprint(two_nodes) != graph_fingerprint(one_node)

    @given(
        ids=st.lists(
            st.text(alphabet=st.sampled_from(list("ab|\n{}:,")), min_size=1, max_size=6),
            min_size=1,
            max_size=4,
            unique=True,
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_distinct_node_id_sets_never_collide(self, ids: list[str]) -> None:
        """For any two graphs built from distinct node-id sets drawn from an
        alphabet that includes the internal separators, fingerprints differ."""
        g1 = PipelineGraph(nodes=[_node(i) for i in ids])
        # A logically different graph: drop the last id (still non-empty set).
        if len(ids) > 1:
            g2 = PipelineGraph(nodes=[_node(i) for i in ids[:-1]])
            assert graph_fingerprint(g1) != graph_fingerprint(g2)


# ---------------------------------------------------------------------------
# F563 — fingerprint completeness
# ---------------------------------------------------------------------------


class TestFingerprintCompleteness:
    def _base(self) -> PipelineGraph:
        return PipelineGraph(
            nodes=[
                _node("n1", {"code": "df = df"}),
                _node("n2", {"code": "df = df.head()"}),
            ],
            edges=[GraphEdge(id="e1", source="n1", target="n2")],
        )

    def test_every_output_affecting_input_changes_fingerprint(self) -> None:
        base = self._base()
        fp = graph_fingerprint(base)

        mutations: dict[str, PipelineGraph] = {
            "node_id": base.model_copy(
                update={"nodes": [_node("nX", {"code": "df = df"}), base.nodes[1]]}
            ),
            "node_type": base.model_copy(
                update={
                    "nodes": [
                        GraphNode(
                            id="n1",
                            data=NodeData(
                                label="n1", nodeType=NodeType.OUTPUT, config={"code": "df = df"}
                            ),
                        ),
                        base.nodes[1],
                    ]
                }
            ),
            "node_config": base.model_copy(
                update={"nodes": [_node("n1", {"code": "df = df.tail()"}), base.nodes[1]]}
            ),
            "edge_source": base.model_copy(
                update={"edges": [GraphEdge(id="e1", source="n2", target="n2")]}
            ),
            "edge_target": base.model_copy(
                update={"edges": [GraphEdge(id="e1", source="n1", target="n1")]}
            ),
            "edge_source_handle": base.model_copy(
                update={
                    "edges": [GraphEdge(id="e1", source="n1", target="n2", sourceHandle="frame_b")]
                }
            ),
            "edge_target_handle": base.model_copy(
                update={
                    "edges": [GraphEdge(id="e1", source="n1", target="n2", targetHandle="left")]
                }
            ),
            "preamble": base.model_copy(update={"preamble": "X = 1\n"}),
        }
        for name, mutated in mutations.items():
            assert graph_fingerprint(mutated) != fp, f"{name} did not change the fingerprint"

        # Extra keys are output-affecting too.
        assert graph_fingerprint(base, "target=n1") != fp

    def test_edge_reordering_does_not_change_fingerprint(self) -> None:
        """Edges are an unordered set — reordering must not perturb the digest."""
        e1 = GraphEdge(id="e1", source="n1", target="n2")
        e2 = GraphEdge(id="e2", source="n2", target="n1")
        nodes = [_node("n1"), _node("n2")]
        g_ab = PipelineGraph(nodes=nodes, edges=[e1, e2])
        g_ba = PipelineGraph(nodes=nodes, edges=[e2, e1])
        assert graph_fingerprint(g_ab) == graph_fingerprint(g_ba)


# ---------------------------------------------------------------------------
# F013 — utility resolved via sys.path
# ---------------------------------------------------------------------------


class TestUtilitySyspathResolution:
    def test_utility_on_syspath_only_is_fingerprinted(self, tmp_path, monkeypatch) -> None:
        """A ``utility`` module that lives only on ``sys.path`` (not in the
        pipeline dir or cwd) is what the preamble actually imports — its edits
        must invalidate the fingerprint."""
        pipeline_dir = tmp_path / "pdir"
        pipeline_dir.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)

        libs = tmp_path / "libs"
        libs.mkdir()
        util = libs / "utility.py"
        util.write_text("SCALE = 10\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(libs))

        preamble = "import utility\n"
        fp_before = preamble_execution_fingerprint(preamble, pipeline_dir=str(pipeline_dir))
        util.write_text("SCALE = 1000\n", encoding="utf-8")
        fp_after = preamble_execution_fingerprint(preamble, pipeline_dir=str(pipeline_dir))

        assert fp_before is not None
        assert fp_before != fp_after

    def test_utility_created_on_syspath_invalidates_fingerprint(
        self, tmp_path, monkeypatch
    ) -> None:
        """Creating ``utility`` after an initial 'missing' fingerprint must
        change the digest — resolution consults live import machinery state."""
        pipeline_dir = tmp_path / "pdir"
        pipeline_dir.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)

        libs = tmp_path / "libs"
        libs.mkdir()
        monkeypatch.syspath_prepend(str(libs))

        preamble = "import utility\n"
        fp_missing = preamble_execution_fingerprint(preamble, pipeline_dir=str(pipeline_dir))
        (libs / "utility.py").write_text("SCALE = 7\n", encoding="utf-8")
        fp_present = preamble_execution_fingerprint(preamble, pipeline_dir=str(pipeline_dir))

        assert fp_missing is not None
        assert fp_missing != fp_present
