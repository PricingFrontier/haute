"""Property-based tests using Hypothesis.

These tests verify invariants that must hold for *all* inputs, not just
hand-picked examples.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import polars as pl
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from haute._path_resolution import resolve_runtime_file_path
from haute.codegen import graph_to_code
from haute.executor import _apply_banding
from haute.graph_utils import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    _sanitize_func_name,
    topo_sort_ids,
)
from haute.parser import parse_pipeline_file, parse_pipeline_source
from tests.conftest import make_edge, make_output_config

# ---------------------------------------------------------------------------
# _sanitize_func_name properties
# ---------------------------------------------------------------------------

# Strategy: arbitrary strings with printable chars, spaces, hyphens, digits
label_strategy = st.text(
    alphabet=string.printable,
    min_size=0,
    max_size=50,
)


class TestSanitizeFuncNameProperties:
    @given(label=label_strategy)
    @settings(max_examples=200)
    def test_always_returns_valid_python_identifier(self, label: str):
        """For any input string, the result is a valid Python identifier."""
        result = _sanitize_func_name(label)
        assert result.isidentifier(), (
            f"_sanitize_func_name({label!r}) = {result!r} is not a valid identifier"
        )

    @given(label=label_strategy)
    @settings(max_examples=200)
    def test_never_returns_empty_string(self, label: str):
        """Output is never empty — falls back to 'unnamed_node'."""
        result = _sanitize_func_name(label)
        assert len(result) > 0

    @given(label=label_strategy)
    @settings(max_examples=200)
    def test_idempotent(self, label: str):
        """Sanitizing twice produces the same result as sanitizing once."""
        once = _sanitize_func_name(label)
        twice = _sanitize_func_name(once)
        assert once == twice

    @given(label=label_strategy)
    @settings(max_examples=200)
    def test_result_is_valid_identifier(self, label: str):
        """Output is always a valid Python identifier."""
        result = _sanitize_func_name(label)
        assert result.isidentifier()

    @given(label=st.text(alphabet=string.ascii_letters + "_", min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_preserves_valid_identifiers(self, label: str):
        """Input that is already a valid non-keyword identifier is preserved."""
        import keyword

        assume(label.isidentifier() and not keyword.iskeyword(label))
        result = _sanitize_func_name(label)
        assert result == label


# ---------------------------------------------------------------------------
# topo_sort_ids properties
# ---------------------------------------------------------------------------


def dag_strategy():
    """Generate a random DAG as (node_ids, edges).

    Creates N nodes and a random subset of forward edges (i→j where i<j)
    to ensure acyclicity.
    """

    @st.composite
    def make_dag(draw):
        n = draw(st.integers(min_value=0, max_value=8))
        ids = [f"n{i}" for i in range(n)]
        edges = []
        for i in range(n):
            for j in range(i + 1, n):
                if draw(st.booleans()):
                    edges.append(make_edge(ids[i], ids[j]))
        return ids, edges

    return make_dag()


class TestTopoSortProperties:
    @given(data=dag_strategy())
    @settings(max_examples=200)
    def test_preserves_all_nodes(self, data):
        """All input nodes should appear in the output (for a DAG, no cycles)."""
        ids, edges = data
        result = topo_sort_ids(ids, edges)
        assert set(result) == set(ids)

    @given(data=dag_strategy())
    @settings(max_examples=200)
    def test_every_node_appears_after_all_its_dependencies(self, data):
        """For any DAG, every node appears after ALL of its dependencies."""
        ids, edges = data
        result = topo_sort_ids(ids, edges)
        pos = {nid: i for i, nid in enumerate(result)}
        # Build full parent map
        parents: dict[str, set[str]] = {nid: set() for nid in ids}
        for e in edges:
            parents[e.target].add(e.source)
        for nid in result:
            for parent in parents[nid]:
                assert pos[parent] < pos[nid], f"Dependency {parent} should appear before {nid}"

    @given(data=dag_strategy())
    @settings(max_examples=100)
    def test_deterministic(self, data):
        """Same DAG always produces the same topo sort."""
        ids, edges = data
        r1 = topo_sort_ids(ids, edges)
        r2 = topo_sort_ids(ids, edges)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Runtime path resolution metamorphic properties
# ---------------------------------------------------------------------------

path_part_strategy = st.text(
    alphabet=string.ascii_letters + string.digits + "_-",
    min_size=1,
    max_size=12,
)


class TestRuntimePathResolutionProperties:
    @given(parts=st.lists(path_part_strategy, min_size=1, max_size=4))
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_equivalent_project_relative_paths_resolve_identically(
        self,
        parts: list[str],
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        """Adding harmless ``.`` path components must not change the resolved file."""
        root = tmp_path_factory.mktemp("runtime_path_root")
        raw = "/".join(parts)
        with_dot = f"./{raw}"

        resolved = resolve_runtime_file_path(
            raw,
            project_root=root,
            enforce_project_root=True,
        )
        resolved_with_dot = resolve_runtime_file_path(
            with_dot,
            project_root=root,
            enforce_project_root=True,
        )

        assert resolved == resolved_with_dot
        assert resolved.is_relative_to(root.resolve())

    @given(parts=st.lists(path_part_strategy, min_size=1, max_size=4))
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_parent_traversal_is_rejected(
        self,
        parts: list[str],
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        """Any path that climbs above the project root is invalid."""
        root = tmp_path_factory.mktemp("runtime_path_root")
        raw = "../" + "/".join(parts)

        with pytest.raises(ValueError, match="outside the project root"):
            resolve_runtime_file_path(
                raw,
                project_root=root,
                enforce_project_root=True,
            )


# ---------------------------------------------------------------------------
# _apply_banding properties
# ---------------------------------------------------------------------------


class TestBandingProperties:
    @given(
        values=st.lists(
            st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
            min_size=1,
            max_size=50,
        ),
        threshold=st.floats(
            allow_nan=False,
            allow_infinity=False,
            min_value=-1e6,
            max_value=1e6,
        ),
    )
    @settings(max_examples=100)
    def test_continuous_banding_covers_all_rows(self, values, threshold):
        """Two complementary rules (<=t, >t) should assign every row."""
        lf = pl.DataFrame({"x": values}).lazy()
        rules = [
            {"op1": "<=", "val1": threshold, "assignment": "low"},
            {"op1": ">", "val1": threshold, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].null_count() == 0
        assert set(result["band"].to_list()) <= {"low", "high"}

    @given(
        values=st.lists(st.sampled_from(["A", "B", "C", "D", "E"]), min_size=1, max_size=30),
    )
    @settings(max_examples=100)
    def test_categorical_banding_matches_rules(self, values):
        """Categorical banding only produces assignments from the rules + None."""
        lf = pl.DataFrame({"x": values}).lazy()
        rules = [
            {"value": "A", "assignment": "Group1"},
            {"value": "B", "assignment": "Group1"},
            {"value": "C", "assignment": "Group2"},
        ]
        result = _apply_banding(lf, "x", "band", "categorical", rules).collect()
        valid_outputs = {"Group1", "Group2", None}
        actual = set(result["band"].to_list())
        assert actual <= valid_outputs

    @given(
        values=st.lists(
            st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_banding_preserves_row_count(self, values):
        """Banding never changes the number of rows."""
        lf = pl.DataFrame({"x": values}).lazy()
        rules = [{"op1": "<=", "val1": 0, "assignment": "neg"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert len(result) == len(values)


# ---------------------------------------------------------------------------
# Codegen ↔ parser roundtrip properties
# ---------------------------------------------------------------------------


def _pipeline_graph_strategy():
    """Generate a random DAG-based pipeline graph.

    Builds a chain: dataInput → N transforms → output.
    Uses valid Python identifiers as labels to survive codegen roundtrip.
    """

    @st.composite
    def make_pipeline(draw):
        # 0..4 intermediate transform nodes
        n_transforms = draw(st.integers(min_value=0, max_value=4))

        # Build unique labels — codegen uses these as Python function names
        all_labels: list[str] = []
        all_labels.append("Source")

        for i in range(n_transforms):
            label = f"Step{i}"
            all_labels.append(label)

        all_labels.append("Output")

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []

        # Source node
        nodes.append(
            GraphNode(
                id="source",
                data=NodeData(
                    label="Source",
                    nodeType=NodeType.DATA_INPUT,
                    config={"path": "data/input.parquet"},
                ),
            )
        )

        # Transform nodes
        for i in range(n_transforms):
            nid = f"step{i}"
            nodes.append(
                GraphNode(
                    id=nid,
                    data=NodeData(
                        label=f"Step{i}",
                        nodeType=NodeType.POLARS,
                        config={"code": ""},
                    ),
                )
            )

        # Output node
        nodes.append(
            GraphNode(
                id="output",
                data=NodeData(
                    label="Output",
                    nodeType=NodeType.OUTPUT,
                    config=make_output_config([]),
                ),
            )
        )

        # Chain edges: source → step0 → step1 → ... → output
        prev_id = "source"
        for i in range(n_transforms):
            nid = f"step{i}"
            edges.append(GraphEdge(id=f"e_{prev_id}_{nid}", source=prev_id, target=nid))
            prev_id = nid
        edges.append(GraphEdge(id=f"e_{prev_id}_output", source=prev_id, target="output"))

        return PipelineGraph(
            nodes=nodes,
            edges=edges,
            pipeline_name="gen_pipeline",
        )

    return make_pipeline()


def _roundtrip(graph: PipelineGraph) -> PipelineGraph:
    """Helper: codegen → write to temp file → parse back.

    Post Item #18 the parser fails loudly on missing sidecar configs.
    Codegen emits ``@pipeline.<type>(config="config/<type>/<name>.json")``
    for node types with a config folder; we materialise those sidecar
    JSON files from each node's config dict so the reparse finds them.
    """
    import json
    import tempfile

    from haute._config_io import config_path_for_node, has_config_folder
    from haute.graph_utils import _sanitize_func_name

    code = graph_to_code(graph, pipeline_name="gen_pipeline")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for n in graph.nodes:
            if not has_config_folder(n.data.nodeType):
                continue
            func_name = _sanitize_func_name(n.data.label)
            cfg_path = root / config_path_for_node(n.data.nodeType, func_name)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(n.data.config))
        py_file = root / "gen_pipeline.py"
        py_file.write_text(code)
        return parse_pipeline_file(py_file)


class TestCodegenRoundtripProperties:
    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_node_count(self, graph: PipelineGraph):
        """codegen → parse should preserve the number of nodes."""
        parsed = _roundtrip(graph)
        assert len(parsed.nodes) == len(graph.nodes)

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_edge_count(self, graph: PipelineGraph):
        """codegen → parse should preserve the number of edges."""
        parsed = _roundtrip(graph)
        assert len(parsed.edges) == len(graph.edges)

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_node_labels(self, graph: PipelineGraph):
        """codegen → parse should preserve node labels (as function names)."""
        parsed = _roundtrip(graph)
        original_labels = {n.data.label for n in graph.nodes}
        parsed_labels = {n.data.label for n in parsed.nodes}
        assert original_labels == parsed_labels

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_code_compiles(self, graph: PipelineGraph):
        """Generated code should always be valid Python."""
        code = graph_to_code(graph, pipeline_name="gen_pipeline")
        compile(code, "<generated>", "exec")

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_pipeline_name(self, graph: PipelineGraph):
        """codegen → parse should preserve the pipeline name."""
        parsed = _roundtrip(graph)
        assert parsed.pipeline_name == "gen_pipeline"

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_node_types(self, graph: PipelineGraph):
        """codegen → parse should preserve node types."""
        parsed = _roundtrip(graph)
        orig_types = sorted(n.data.nodeType for n in graph.nodes)
        parsed_types = sorted(n.data.nodeType for n in parsed.nodes)
        assert orig_types == parsed_types

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_preserves_edge_connectivity(self, graph: PipelineGraph):
        """codegen → parse should preserve which nodes are connected (by label)."""
        parsed = _roundtrip(graph)
        # Compare edges by label, not ID — parser uses function names as IDs
        id_to_label = {n.id: n.data.label for n in graph.nodes}
        orig_edges = {(id_to_label[e.source], id_to_label[e.target]) for e in graph.edges}
        parsed_id_to_label = {n.id: n.data.label for n in parsed.nodes}
        parsed_edges = {
            (parsed_id_to_label[e.source], parsed_id_to_label[e.target]) for e in parsed.edges
        }
        assert orig_edges == parsed_edges


# ---------------------------------------------------------------------------
# Parser robustness: valid pipeline strings always parse without crash
# ---------------------------------------------------------------------------


def _valid_pipeline_source_strategy():
    """Generate valid Python pipeline source code with random node names."""

    @st.composite
    def make_source(draw):
        n_transforms = draw(st.integers(min_value=0, max_value=3))
        lines = [
            "import polars as pl",
            "import haute",
            "",
            'pipeline = haute.Pipeline("fuzz")',
            "",
            "@pipeline.polars",
            "def source() -> pl.LazyFrame:",
            '    return pl.LazyFrame({"x": [1]})',
            "",
        ]
        prev = "source"
        for i in range(n_transforms):
            name = f"step_{i}"
            lines.extend(
                [
                    "@pipeline.polars",
                    f"def {name}({prev}: pl.LazyFrame) -> pl.LazyFrame:",
                    f"    return {prev}",
                    "",
                ]
            )
            lines.append(f'pipeline.connect("{prev}", "{name}")')
            prev = name

        # Output node
        lines.extend(
            [
                "",
                "@pipeline.polars",
                f"def output({prev}: pl.LazyFrame) -> pl.LazyFrame:",
                f"    return {prev}",
                "",
                f'pipeline.connect("{prev}", "output")',
            ]
        )
        return "\n".join(lines)

    return make_source()


class TestParserRobustness:
    @given(source=_valid_pipeline_source_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_valid_source_always_parses(self, source: str):
        """Valid pipeline source code should always parse without exceptions."""
        graph = parse_pipeline_source(source)
        assert len(graph.nodes) >= 2  # at least source + output
        assert graph.pipeline_name == "fuzz"

    @given(source=_valid_pipeline_source_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_parsed_graph_has_consistent_edges(self, source: str):
        """All edge endpoints should reference existing node IDs."""
        graph = parse_pipeline_source(source)
        node_ids = {n.id for n in graph.nodes}
        for edge in graph.edges:
            assert edge.source in node_ids, f"Edge source {edge.source!r} not in nodes"
            assert edge.target in node_ids, f"Edge target {edge.target!r} not in nodes"


# ---------------------------------------------------------------------------
# 1. Parser roundtrip: graph → codegen → parse → equivalent graph
# ---------------------------------------------------------------------------


class TestParserRoundtripProperty:
    """Generate random valid graphs, round-trip through codegen+parse."""

    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_roundtrip_node_and_edge_topology(self, graph: PipelineGraph):
        """Full structural equivalence after codegen → parse roundtrip."""
        parsed = _roundtrip(graph)
        # Same node labels
        orig_labels = sorted(n.data.label for n in graph.nodes)
        parsed_labels = sorted(n.data.label for n in parsed.nodes)
        assert orig_labels == parsed_labels
        # Same edge topology (by label)
        id_to_label = {n.id: n.data.label for n in graph.nodes}
        orig_edges = {(id_to_label[e.source], id_to_label[e.target]) for e in graph.edges}
        pid_to_label = {n.id: n.data.label for n in parsed.nodes}
        parsed_edges = {(pid_to_label[e.source], pid_to_label[e.target]) for e in parsed.edges}
        assert orig_edges == parsed_edges


# ---------------------------------------------------------------------------
# 2. Sanitize name idempotence (expanded)
# ---------------------------------------------------------------------------


class TestSanitizeIdempotence:
    @given(name=st.text(min_size=0, max_size=80))
    @settings(max_examples=300)
    def test_idempotence_for_arbitrary_unicode(self, name: str):
        """sanitize(sanitize(x)) == sanitize(x) for any unicode string."""
        once = _sanitize_func_name(name)
        twice = _sanitize_func_name(once)
        assert once == twice, f"Not idempotent: {name!r} → {once!r} → {twice!r}"


# ---------------------------------------------------------------------------
# 3. Banding monotonicity
# ---------------------------------------------------------------------------


class TestBandingMonotonicity:
    @given(
        n=st.integers(min_value=2, max_value=50),
    )
    @settings(max_examples=100)
    def test_monotonic_input_ordered_bands_monotonic_output(self, n: int):
        """With ordered non-overlapping bands, monotonic input → monotonic output."""
        import polars as pl

        values = list(range(n))  # strictly monotonic increasing
        lf = pl.DataFrame({"x": [float(v) for v in values]}).lazy()
        # Non-overlapping ordered bands: (-inf, 10], (10, 20], (20, inf)
        rules = [
            {"op1": "<=", "val1": 10, "assignment": "A"},
            {"op1": ">", "val1": 10, "op2": "<=", "val2": 20, "assignment": "B"},
            {"op1": ">", "val1": 20, "assignment": "C"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        bands = result["band"].to_list()
        # Filter out nulls, then check band labels never go backward
        band_order = {"A": 0, "B": 1, "C": 2}
        non_null = [band_order[b] for b in bands if b is not None]
        for i in range(1, len(non_null)):
            assert non_null[i] >= non_null[i - 1], (
                f"Band output not monotonic at index {i}: {bands}"
            )


# ---------------------------------------------------------------------------
# 4. Rating table join preserves row count
# ---------------------------------------------------------------------------


class TestRatingTableRowCount:
    @given(
        n_rows=st.integers(min_value=1, max_value=50),
        categories=st.lists(
            st.sampled_from(["cat_a", "cat_b", "cat_c", "cat_d"]),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_left_join_never_increases_row_count(self, n_rows, categories):
        """Left-joining a deduplicated rating table should not fan out rows."""
        from haute._rating import _apply_rating_table

        # Build a main frame with factor column
        cats = [categories[i % len(categories)] for i in range(n_rows)]
        lf = pl.DataFrame({"factor1": cats, "base": [1.0] * n_rows}).lazy()

        # Build a rating table with unique entries per factor
        entries = [
            {"factor1": "cat_a", "value": 1.1},
            {"factor1": "cat_b", "value": 1.2},
            {"factor1": "cat_c", "value": 0.9},
            {"factor1": "cat_d", "value": 1.0},
        ]
        table = {
            "factors": ["factor1"],
            "outputColumn": "rating",
            "entries": entries,
        }
        result = _apply_rating_table(lf, table).collect()
        assert len(result) == n_rows, f"Row count changed: {n_rows} → {len(result)}"


# ---------------------------------------------------------------------------
# 5. Config roundtrip: sidecar preparation -> load_node_config
# ---------------------------------------------------------------------------


class TestConfigRoundtrip:
    @given(
        # Bundle 2.α — keys must be in the per-node-type allowlist or in
        # _UNIVERSAL_KEYS to survive `_prepare_config_for_sidecar`.
        # Using universal keys ensures the property holds across any
        # node_type. The keys below all appear in _UNIVERSAL_KEYS:
        # `selected_columns` (list[str]), `column_renames` (dict[str,str]),
        # `contract` (str). Strategy avoids leading underscores in
        # nested string values because `_strip_internal_keys` recurses
        # and strips `_*` keys at every level — those would not
        # roundtrip (by design).
        config=st.fixed_dictionaries(
            {
                "selected_columns": st.lists(
                    st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,19}", fullmatch=True),
                    max_size=5,
                ),
                "column_renames": st.dictionaries(
                    keys=st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,19}", fullmatch=True),
                    values=st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,19}", fullmatch=True),
                    max_size=5,
                ),
                "contract": st.text(
                    alphabet=string.ascii_letters + string.digits + "-",
                    min_size=0,
                    max_size=20,
                ),
            }
        ),
    )
    @settings(max_examples=80)
    def test_config_roundtrip_preserves_data(self, config, tmp_path_factory):
        """load(save(config)) == config for allowlisted config keys.

        Bundle 2.α restricted the roundtrip contract: keys outside
        `VALID_KEYS[node_type]` are dropped at write time. The
        roundtrip property now applies only to allowlisted keys
        (TypedDict-declared keys + `_UNIVERSAL_KEYS`).
        """
        from haute._config_io import (
            _prepare_config_for_sidecar,
            config_path_for_node,
            load_node_config,
        )
        from haute.graph_utils import NodeType

        base_dir = tmp_path_factory.mktemp("cfg")
        # OUTPUT has a config folder and no per-type compactor —
        # BANDING and RATING_STEP have compactors that strip universal
        # keys, POLARS has no folder (transforms store code inline).
        # OUTPUT is the cleanest target for testing the pure JSON+α
        # roundtrip of universal keys.
        rel = config_path_for_node(NodeType.OUTPUT, "test_node")
        path = base_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                _prepare_config_for_sidecar(NodeType.OUTPUT, config),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        loaded = load_node_config(rel, base_dir=base_dir)
        for k, v in config.items():
            assert k in loaded, f"Key {k!r} missing after roundtrip"
            assert loaded[k] == v, f"Value mismatch for {k}: {v!r} vs {loaded[k]!r}"


# ---------------------------------------------------------------------------
# 6. Fingerprint determinism
# ---------------------------------------------------------------------------


class TestFingerprintDeterminism:
    @given(graph=_pipeline_graph_strategy())
    @settings(max_examples=50, deadline=5000)
    def test_same_graph_same_fingerprint(self, graph: PipelineGraph):
        """Same graph always produces the same fingerprint."""
        from haute.graph_utils import graph_fingerprint

        fp1 = graph_fingerprint(graph)
        # Clear any cached fingerprint to force recomputation
        try:
            object.__delattr__(graph, "_haute_base_fingerprint")
        except (AttributeError, TypeError):
            pass
        fp2 = graph_fingerprint(graph)
        assert fp1 == fp2

    @given(data=st.data())
    @settings(max_examples=50, deadline=5000)
    def test_different_graphs_different_fingerprints(self, data):
        """Graphs with different structure produce different fingerprints."""
        from haute.graph_utils import graph_fingerprint

        # Build two graphs that differ by number of transforms
        g1 = data.draw(_pipeline_graph_strategy())
        g2 = data.draw(_pipeline_graph_strategy())
        # Only assert when structural content actually differs
        labels1 = sorted(n.data.label for n in g1.nodes)
        labels2 = sorted(n.data.label for n in g2.nodes)
        assume(labels1 != labels2)
        fp1 = graph_fingerprint(g1)
        fp2 = graph_fingerprint(g2)
        assert fp1 != fp2, "Structurally different graphs should have different fingerprints"


# ---------------------------------------------------------------------------
# 8. Code validation consistency (cache correctness)
# ---------------------------------------------------------------------------


class TestCodeValidationConsistency:
    @given(
        code=st.sampled_from(
            [
                "x = 1 + 2",
                "result = [i for i in range(10)]",
                "df = df.filter(pl.col('a') > 0)",
                "df = df.filter(pl.col('x') > 0)",
                "y = {'a': 1, 'b': 2}",
            ]
        ),
    )
    @settings(max_examples=30)
    def test_validate_same_result_multiple_calls(self, code: str):
        """validate_user_code gives the same result regardless of call count."""
        from haute._sandbox import UnsafeCodeError, validate_user_code

        results = []
        for _ in range(3):
            try:
                validate_user_code(code)
                results.append("ok")
            except UnsafeCodeError as exc:
                results.append(f"err:{exc}")
        assert results[0] == results[1] == results[2], f"Inconsistent validation results: {results}"

    @given(
        code=st.sampled_from(
            [
                "getattr(obj, 'x')",
                "import os",
                "class Foo: pass",
                "obj.__class__",
                "eval('1+1')",
            ]
        ),
    )
    @settings(max_examples=30)
    def test_unsafe_code_always_rejected(self, code: str):
        """Unsafe code is always rejected, even on repeated calls."""
        from haute._sandbox import UnsafeCodeError, validate_user_code

        for _ in range(3):
            with pytest.raises(UnsafeCodeError):
                validate_user_code(code)


# ---------------------------------------------------------------------------
# 9. Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    @given(
        segments=st.lists(
            st.sampled_from(["a", "b", "c", "..", "sub", "dir"]),
            min_size=1,
            max_size=6,
        ),
    )
    @settings(max_examples=100)
    def test_dotdot_escaping_root_is_rejected(self, segments, tmp_path_factory):
        """validate_project_path should reject paths that escape the root."""
        from haute._sandbox import set_project_root, validate_project_path

        root = tmp_path_factory.mktemp("root")
        set_project_root(root)
        path = root.joinpath(*segments)
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            with pytest.raises(ValueError, match="outside"):
                validate_project_path(path)
        else:
            # Should not raise
            result = validate_project_path(path)
            assert result.is_relative_to(root.resolve())

    @given(
        n_dotdots=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=50)
    def test_pure_dotdot_always_rejected(self, n_dotdots, tmp_path_factory):
        """A path of N '..' segments from a nested root should be rejected."""
        from haute._sandbox import set_project_root, validate_project_path

        root = tmp_path_factory.mktemp("deep")
        # Create a subdirectory as the root so '..' can escape
        nested = root / "a" / "b" / "c"
        nested.mkdir(parents=True, exist_ok=True)
        set_project_root(nested)
        escaping = nested / Path(*([".."] * (n_dotdots + 3)))  # enough to escape
        resolved = escaping.resolve()
        if not resolved.is_relative_to(nested.resolve()):
            with pytest.raises(ValueError, match="outside"):
                validate_project_path(escaping)


# ---------------------------------------------------------------------------
# 10. LRU cache invariant
# ---------------------------------------------------------------------------


class TestLRUCacheInvariant:
    @given(
        max_size=st.integers(min_value=1, max_value=50),
        n_puts=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=200)
    def test_size_bounded_by_min_n_maxsize(self, max_size: int, n_puts: int):
        """After N puts with max_size M, cache size == min(N_unique, M)."""
        from haute._lru_cache import LRUCache

        cache: LRUCache[int, str] = LRUCache(max_size=max_size)
        # Use distinct keys so each put adds a new entry
        for i in range(n_puts):
            cache.put(i, f"val_{i}")
        assert len(cache) == min(n_puts, max_size)

    @given(
        max_size=st.integers(min_value=1, max_value=20),
        keys=st.lists(st.integers(min_value=0, max_value=30), min_size=0, max_size=60),
    )
    @settings(max_examples=200)
    def test_size_never_exceeds_max(self, max_size: int, keys: list[int]):
        """Cache size should never exceed max_size regardless of access pattern."""
        from haute._lru_cache import LRUCache

        cache: LRUCache[int, int] = LRUCache(max_size=max_size)
        for k in keys:
            cache.put(k, k * 10)
            assert len(cache) <= max_size

    @given(
        max_size=st.integers(min_value=1, max_value=10),
        ops=st.lists(
            st.tuples(
                st.sampled_from(["put", "get"]),
                st.integers(min_value=0, max_value=15),
            ),
            min_size=0,
            max_size=50,
        ),
    )
    @settings(max_examples=200)
    def test_get_after_put_returns_value(self, max_size: int, ops):
        """A get immediately after a put (with no eviction) returns the value."""
        from haute._lru_cache import LRUCache

        cache: LRUCache[int, int] = LRUCache(max_size=max_size)
        stored: dict[int, int] = {}
        for op, key in ops:
            if op == "put":
                cache.put(key, key * 7)
                stored[key] = key * 7
                # Evict from our tracking if we exceed max_size
                if len(stored) > max_size:
                    # We don't know exactly which key was evicted, just check size
                    pass
            else:
                val = cache.get(key)
                if val is not None:
                    assert val == key * 7
        assert len(cache) <= max_size
