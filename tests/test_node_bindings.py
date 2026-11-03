"""One rule binds every node's parameters to its inputs (READ-01).

Spec: specs/pipeline-config/high-level.md, "Binding parameters to inputs".

The parser and a standalone run bind each node by
:func:`haute._node_bindings.bind_node_inputs`, so a hand-edited pipeline file is
refused alike in both, and one that binds runs alike under ``haute run``, a
plain ``import main`` and the deploy scorer.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import textwrap
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._builders import _build_node_fn
from haute._config_io import collect_node_configs
from haute._execution_admission import create_admitted_execution_context
from haute._node_bindings import NodeInput, bind_node_inputs
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code
from haute.deploy._scorer import QuoteRequest, score_graph
from haute.errors import ExecutionError, ParseError
from haute.execution import ExecutionProfile, execute_lazy_graph
from haute.parser import parse_pipeline_file
from tests.conftest import build_test_api_input_snapshots, make_ready_file_input_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, node_type: NodeType, config: dict) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _edge(
    src: str, tgt: str, *, source_port: str | None = None, target_port: str | None = None
) -> GraphEdge:
    return GraphEdge(
        id=f"e_{src}_{tgt}_{source_port or 'd'}_{target_port or 'd'}",
        source=src,
        target=tgt,
        sourceHandle=source_port,
        targetHandle=target_port,
    )


def _constant(nid: str, name: str, value: object) -> GraphNode:
    return _node(nid, NodeType.CONSTANT, {"values": [{"name": name, "value": value}]})


def _explicit(code: str, graph: PipelineGraph) -> str:
    """*code* with a ``connect()`` for every edge, in the graph's order, as a file written by
    hand states them; codegen leaves out the ones the signatures say (READ-30)."""
    lines = code.splitlines(keepends=True)
    dropped: set[int] = set()
    for statement in ast.parse(code).body:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and ast.unparse(statement.value.func) == "pipeline.connect"
        ):
            dropped.update(range(statement.lineno - 1, statement.end_lineno or statement.lineno))
    kept = "".join(line for index, line in enumerate(lines) if index not in dropped)
    calls = []
    for edge in graph.edges:
        ports = "".join(
            f', {name}="{port}"'
            for name, port in (
                ("source_port", edge.sourceHandle),
                ("target_port", edge.targetHandle),
            )
            if port
        )
        calls.append(f'pipeline.connect("{edge.source}", "{edge.target}"{ports})')
    return kept.rstrip("\n") + "\n\n\n" + "\n".join(calls) + "\n"


def _write_project(graph: PipelineGraph, project: Path, edit: Callable[[str], str]) -> Path:
    """Generate *graph* into *project*, state each edge with a ``connect()`` as a file written
    by hand does, edit its code by hand, and return the file."""
    code = _explicit(graph_to_code(graph, pipeline_name="bindings"), graph)
    for rel_path, content in collect_node_configs(graph).items():
        config_file = project / rel_path
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content, encoding="utf-8")
    edited = edit(code)
    assert edited != code or edit is _unchanged, "the hand edit changed nothing"
    pipeline_file = project / f"bindings_{uuid.uuid4().hex[:8]}.py"
    pipeline_file.write_text(edited, encoding="utf-8")
    return pipeline_file


def _unchanged(code: str) -> str:
    return code


def _replace(*pairs: tuple[str, str]) -> Callable[[str], str]:
    def edit(code: str) -> str:
        for old, new in pairs:
            assert code.count(old) == 1, old
            code = code.replace(old, new)
        return code

    return edit


def _import(pipeline_file: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(pipeline_file.stem, pipeline_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[pipeline_file.stem] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(pipeline_file.stem, None)
    return module


def _haute_run(graph: PipelineGraph, node_id: str, source: str) -> pl.DataFrame:
    """The node's frame from the engine ``haute run`` and the editor run."""
    context = create_admitted_execution_context(
        operation="node_bindings_reference", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        outputs, _, _, _ = execute_lazy_graph(
            graph, _build_node_fn, source=source, execution_context=context
        )
        frame = outputs[node_id]
        return frame.collect() if isinstance(frame, pl.LazyFrame) else frame
    finally:
        context.release_admission(preserve_primary_error=True)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(original)


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


class TestBindNodeInputs:
    def test_inputs_come_in_the_order_the_parameters_read_them(self) -> None:
        order = bind_node_inputs(
            node_id="target",
            node_type=NodeType.OUTPUT,
            positional=["b", "a"],
            inputs=[NodeInput("b"), NodeInput("a")],
        )
        assert order == [0, 1]

    def test_an_implied_input_takes_its_parameter_place(self) -> None:
        order = bind_node_inputs(
            node_id="target",
            node_type=NodeType.POLARS,
            positional=["b", "a"],
            inputs=[NodeInput("a"), NodeInput("b", stated=False)],
        )
        assert order == [1, 0]

    def test_a_frame_hook_takes_its_first_input_as_df(self) -> None:
        order = bind_node_inputs(
            node_id="target",
            node_type=NodeType.RATING_STEP,
            positional=["df", "extra"],
            inputs=[NodeInput("rows"), NodeInput("extra")],
        )
        assert order == [0, 1]

    def test_a_data_input_hook_names_no_input(self) -> None:
        assert (
            bind_node_inputs(
                node_id="quotes", node_type=NodeType.DATA_INPUT, positional=["df"], inputs=[]
            )
            == []
        )

    def test_ported_inputs_bind_by_port_whatever_their_order(self) -> None:
        order = bind_node_inputs(
            node_id="joined",
            node_type=NodeType.EDGE_JOIN,
            positional=["policies", "regions"],
            inputs=[NodeInput("regions", "join"), NodeInput("policies", "base")],
        )
        assert order == [1, 0]

    def test_a_mapped_parameter_reads_its_connected_input(self) -> None:
        order = bind_node_inputs(
            node_id="target",
            node_type=NodeType.POLARS,
            positional=["policies"],
            inputs=[NodeInput("renamed")],
            input_mapping={"policies": "renamed"},
        )
        assert order == [0]

    def test_a_frame_hook_that_names_its_first_input_is_refused(self) -> None:
        with pytest.raises(ParseError, match="parameter 'rows' names the first input") as refused:
            bind_node_inputs(
                node_id="target",
                node_type=NodeType.RATING_STEP,
                positional=["df", "rows"],
                inputs=[NodeInput("rows")],
            )
        assert refused.value.context["unbound_parameters"] == ["rows"]


# ---------------------------------------------------------------------------
# Four refusals, in the parser and a standalone run alike, for a declaration,
# a frame hook with two inputs, and an External File.
# ---------------------------------------------------------------------------

_RATING_TABLES = [
    {
        "name": "Region Factor",
        "factors": ["region"],
        "outputColumn": "region_factor",
        "entries": [{"region": "north", "value": 0.9}, {"region": "south", "value": 1.1}],
    }
]

_TARGETS: dict[str, tuple[GraphNode, str]] = {
    # The target node and its generated signature.
    "declaration": (
        _node(
            "target",
            NodeType.OUTPUT,
            {
                "outputMapping": [
                    {
                        "source_port": "rows",
                        "source_column": "region",
                        "output_path": "$[:].region",
                        "enabled": True,
                    },
                    {
                        "source_port": "extra",
                        "source_column": "x",
                        "output_path": "$[:].x",
                        "enabled": True,
                    },
                ]
            },
        ),
        "def target(rows, extra): ...",
    ),
    "frame_hook": (
        _node(
            "target",
            NodeType.RATING_STEP,
            {"tables": _RATING_TABLES, "code": "df = df.join(extra, how='cross')"},
        ),
        "def target(df: pl.LazyFrame, extra: pl.LazyFrame) -> pl.LazyFrame:",
    ),
    "external_file": (
        _node(
            "target",
            NodeType.EXTERNAL_FILE,
            {
                "path": "factor.json",
                "fileType": "json",
                "code": "df = rows.join(extra, how='cross')",
            },
        ),
        "def target(rows: pl.LazyFrame, extra: pl.LazyFrame, *, obj) -> pl.LazyFrame:",
    ),
}

_CONNECTS = 'pipeline.connect("rows", "target")\npipeline.connect("extra", "target")'


def _mismatch(kind: str, mismatch: str) -> Callable[[str], str]:
    signature = _TARGETS[kind][1]
    if mismatch == "parameter_names_no_input":
        return _replace((signature, signature.replace("extra", "extra, typo", 1)))
    if mismatch == "input_named_by_no_parameter":
        return _replace(
            (
                signature,
                signature.replace(", extra: pl.LazyFrame", "").replace(", extra", ""),
            )
        )
    if mismatch == "parameter_disagrees_with_connect":
        return _replace((signature, signature.replace("extra", "other", 1)))
    assert mismatch == "connects_out_of_order"
    return _replace(
        (_CONNECTS, 'pipeline.connect("extra", "target")\npipeline.connect("rows", "target")')
    )


#: What each refusal names: the parameter, or the input no parameter names.
_NAMED = {
    "parameter_names_no_input": "parameter 'typo' names no input",
    "input_named_by_no_parameter": "input 'extra' is connected but no parameter names it",
    "parameter_disagrees_with_connect": "parameter 'other' names an input no connect() states, "
    "while connect() connects 'extra'",
    "connects_out_of_order": "its connect() calls come in the order 'extra', 'rows', but its "
    "parameters read 'rows', 'extra'",
}


def _mismatch_graph() -> Callable[[GraphNode], PipelineGraph]:
    def build(target: GraphNode) -> PipelineGraph:
        return PipelineGraph(
            nodes=[
                _constant("rows", "region", "south"),
                _constant("extra", "x", 1),
                _constant("other", "y", 2),
                target,
            ],
            edges=[_edge("rows", "target"), _edge("extra", "target")],
        )

    return build


@pytest.mark.parametrize("mismatch", list(_NAMED))
@pytest.mark.parametrize("kind", list(_TARGETS))
def test_each_mismatch_is_refused_naming_the_node_and_parameter(
    project: Path, kind: str, mismatch: str
) -> None:
    (project / "factor.json").write_text('{"factor": 3}', encoding="utf-8")
    pipeline_file = _write_project(
        _mismatch_graph()(_TARGETS[kind][0]), project, _mismatch(kind, mismatch)
    )

    with pytest.raises(ParseError) as parsed:
        parse_pipeline_file(pipeline_file)
    assert parsed.value.context["node_id"] == "target"
    assert "The parameters of node 'target' do not match its inputs" in parsed.value.message
    assert _NAMED[mismatch] in parsed.value.message

    # A standalone run refuses the same file by the same rule, before any node runs.
    with pytest.raises(ExecutionError) as ran:
        _import(pipeline_file).pipeline.run(source="batch")
    assert ran.value.message == parsed.value.message
    assert ran.value.context["node_id"] == "target"


def test_a_generated_file_binds_for_every_kind(project: Path) -> None:
    (project / "factor.json").write_text('{"factor": 3}', encoding="utf-8")
    for target, _signature in _TARGETS.values():
        pipeline_file = _write_project(_mismatch_graph()(target), project, _unchanged)
        graph = parse_pipeline_file(pipeline_file)
        assert [(edge.source, edge.target) for edge in graph.edges] == [
            ("rows", "target"),
            ("extra", "target"),
        ]


# ---------------------------------------------------------------------------
# A file that binds runs alike in every context
# ---------------------------------------------------------------------------

_RECORDS = [{"quote_id": 1, "base": 100}, {"quote_id": 2, "base": 200}]


def _priced_graph(project: Path) -> PipelineGraph:
    """A Quote Input and a constant into a Polars node and an Output, each with two inputs."""
    request = project / "request.json"
    request.write_text(json.dumps(_RECORDS), encoding="utf-8")
    quote_config = {
        "path": str(request),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [
                    {"name": "quote_id", "path": "$[:].quote_id", "type": "int", "selected": True},
                    {"name": "base", "path": "$[:].base", "type": "int", "selected": True},
                ],
            }
        ],
    }
    build_test_api_input_snapshots(request, quote_config)
    mapping = [("priced", "quote_id"), ("priced", "base"), ("priced", "rate")]
    return PipelineGraph(
        nodes=[
            _node("quotes", NodeType.API_INPUT, quote_config),
            _constant("rates", "rate", 1.5),
            _node(
                "priced",
                NodeType.POLARS,
                # Rows in the order of its inputs, so a frame given to the wrong
                # parameter shows.
                {"code": "df = pl.concat([quotes, rates], how='diagonal')"},
            ),
            _node(
                "result",
                NodeType.OUTPUT,
                {
                    "outputMapping": [
                        {
                            "source_port": port,
                            "source_column": column,
                            "output_path": f"$[:].{column}",
                            "enabled": True,
                        }
                        for port, column in mapping
                    ]
                },
            ),
        ],
        edges=[
            _edge("quotes", "priced", source_port="quotes"),
            _edge("rates", "priced"),
            _edge("priced", "result"),
            _edge("rates", "result"),
        ],
    )


_HAND_EDITS: dict[str, Callable[[str], str]] = {
    "as_generated": _unchanged,
    # Both nodes' parameters and connect() calls reversed together.
    "reversed_connections": _replace(
        (
            "def priced(quotes: pl.LazyFrame, rates: pl.LazyFrame)",
            "def priced(rates: pl.LazyFrame, quotes: pl.LazyFrame)",
        ),
        ("def result(priced, rates)", "def result(rates, priced)"),
        (
            'pipeline.connect("quotes", "priced", source_port="quotes")\n'
            'pipeline.connect("rates", "priced")\n',
            'pipeline.connect("rates", "priced")\n'
            'pipeline.connect("quotes", "priced", source_port="quotes")\n',
        ),
        (
            'pipeline.connect("priced", "result")\npipeline.connect("rates", "result")',
            'pipeline.connect("rates", "result")\npipeline.connect("priced", "result")',
        ),
    ),
    # The rates edges are implied by the parameters that name them.
    "implied_connections": _replace(
        ('pipeline.connect("rates", "priced")\n', ""),
        ('pipeline.connect("rates", "result")', ""),
    ),
}


@pytest.mark.parametrize("hand_edit", list(_HAND_EDITS))
def test_haute_run_standalone_and_deploy_give_the_same_answer(
    project: Path, hand_edit: str
) -> None:
    pipeline_file = _write_project(_priced_graph(project), project, _HAND_EDITS[hand_edit])

    graph = parse_pipeline_file(pipeline_file)
    run = _haute_run(graph, "result", source="live")
    standalone = _import(pipeline_file).pipeline.run(source="live")
    deployed = score_graph(graph, QuoteRequest(tuple(_RECORDS)), ["quotes"], "result")

    assert run.to_dicts() == [
        {"quote_id": 1, "base": 100, "rate": None},
        {"quote_id": 2, "base": 200, "rate": None},
        {"quote_id": None, "base": None, "rate": 1.5},
    ]
    assert_frame_equal(standalone, run)
    assert_frame_equal(deployed, run)


def test_a_parsed_node_has_its_edges_in_its_parameters_order(project: Path) -> None:
    pipeline_file = _write_project(
        _priced_graph(project),
        project,
        _replace(
            (
                "def priced(quotes: pl.LazyFrame, rates: pl.LazyFrame)",
                "def priced(rates: pl.LazyFrame, quotes: pl.LazyFrame)",
            ),
            ('pipeline.connect("rates", "priced")\n', ""),
        ),
    )
    graph = parse_pipeline_file(pipeline_file)
    assert [edge.source for edge in graph.edges if edge.target == "priced"] == ["rates", "quotes"]


def test_ported_inputs_connected_join_first_run_alike(project: Path) -> None:
    policies = _node(
        "policies",
        NodeType.CONSTANT,
        {"values": [{"name": "key", "value": 1}, {"name": "premium", "value": 100}]},
    )
    regions = _node(
        "regions",
        NodeType.CONSTANT,
        {"values": [{"name": "key", "value": 1}, {"name": "region", "value": "north"}]},
    )
    joined = _node(
        "joined",
        NodeType.EDGE_JOIN,
        {"how": "left", "maintainOrder": "left_right", "on": ["key"]},
    )
    graph = PipelineGraph(
        nodes=[policies, regions, joined],
        edges=[
            _edge("policies", "joined", target_port="base"),
            _edge("regions", "joined", target_port="join"),
        ],
    )
    pipeline_file = _write_project(
        graph,
        project,
        _replace(
            (
                'pipeline.connect("policies", "joined", target_port="base")\n'
                'pipeline.connect("regions", "joined", target_port="join")',
                'pipeline.connect("regions", "joined", target_port="join")\n'
                'pipeline.connect("policies", "joined", target_port="base")',
            ),
        ),
    )

    parsed = parse_pipeline_file(pipeline_file)
    run = _haute_run(parsed, "joined", source="batch")
    standalone = _import(pipeline_file).pipeline.run(source="batch")

    assert run.to_dicts() == [{"key": 1, "premium": 100, "region": "north"}]
    assert_frame_equal(standalone, run)


def test_a_data_input_hook_with_no_connections_binds(project: Path) -> None:
    (project / ".git").mkdir()
    (project / "haute.toml").write_text('[project]\nname = "hooks"\n', encoding="utf-8")
    pl.DataFrame({"policy_id": [1, 2, 3]}).write_parquet(project / "policies.parquet")
    hook = _node(
        "policies",
        NodeType.DATA_INPUT,
        make_ready_file_input_config(
            "policies.parquet", code="df = df.filter(pl.col('policy_id') > 1)"
        ),
    )
    pipeline_file = _write_project(PipelineGraph(nodes=[hook], edges=[]), project, _unchanged)
    assert "def policies(df: pl.LazyFrame)" in pipeline_file.read_text(encoding="utf-8")

    graph = parse_pipeline_file(pipeline_file)
    standalone = _import(pipeline_file).pipeline.run(source="batch")

    assert graph.edges == []
    assert standalone["policy_id"].to_list() == [2, 3]
    assert_frame_equal(standalone, _haute_run(graph, "policies", source="batch"))


def test_a_submodel_node_reads_its_port_and_inner_inputs_by_name(project: Path) -> None:
    """Its parameters name an inner input before its port input, against file order."""
    (project / "config" / "constant").mkdir(parents=True)
    (project / "config" / "constant" / "rows.json").write_text(
        '{"values": {"x": 4}}', encoding="utf-8"
    )
    (project / "config" / "constant" / "factor.json").write_text(
        '{"values": {"f": 3}}', encoding="utf-8"
    )
    (project / "modules").mkdir()
    (project / "modules" / "__init__.py").write_text("", encoding="utf-8")
    (project / "modules" / "scaling.py").write_text(
        textwrap.dedent(
            '''\
            """Scale rows by a factor."""

            import polars as pl

            import haute

            submodel = haute.Submodel(
                "scaling",
                definition_id="definition_scaling",
                input_ports=[{"name": "rows", "targets": [{"nodeId": "scaled", "handleId": None}]}],
                output_ports=[{"name": "scaled", "source": {"nodeId": "scaled", "handleId": None}}],
                pipeline_dir="..",
            )


            @submodel.constant(config="config/constant/factor.json")
            def factor(): ...


            @submodel.polars
            def scaled(factor: pl.LazyFrame, rows: pl.LazyFrame) -> pl.LazyFrame:
                return pl.concat([rows, factor], how="diagonal")


            submodel.connect("factor", "scaled")
            '''
        ),
        encoding="utf-8",
    )
    (project / "main.py").write_text(
        textwrap.dedent(
            '''\
            """Pipeline: scaled"""

            from modules import scaling

            import haute
            import polars as pl

            pipeline = haute.Pipeline("scaled")


            @pipeline.constant(config="config/constant/rows.json")
            def source(): ...


            @pipeline.polars
            def consumer(scaled: pl.LazyFrame) -> pl.LazyFrame:
                return scaled


            pipeline.submodel(scaling.submodel, "scaling")
            pipeline.connect("source", "scaling", target_port="rows")
            pipeline.connect("scaling", "consumer", source_port="scaled")
            '''
        ),
        encoding="utf-8",
    )

    graph = parse_pipeline_file(project / "main.py", flatten=True)
    run = _haute_run(graph, "consumer", source="live")
    standalone = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from main import pipeline; "
            "print(json.dumps(pipeline.run(source='live').to_dicts()))",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )

    assert run.to_dicts() == [{"x": 4, "f": None}, {"x": None, "f": 3}]
    assert json.loads(standalone.stdout) == run.to_dicts()


# ---------------------------------------------------------------------------
# connect() only where the signatures cannot say it (READ-30)
# ---------------------------------------------------------------------------


def test_generated_code_states_only_the_edges_its_signatures_cannot(project: Path) -> None:
    graph = _priced_graph(project)
    code = graph_to_code(graph, pipeline_name="bindings")

    # The Quote Input's table needs its port; the rates edges are said by the signatures.
    assert code.count("pipeline.connect(") == 1
    assert 'pipeline.connect("quotes", "priced", source_port="quotes")' in code


def test_the_generated_form_runs_alike_in_every_context(project: Path) -> None:
    graph = _priced_graph(project)
    pipeline_file = project / f"bindings_{uuid.uuid4().hex[:8]}.py"
    for rel_path, content in collect_node_configs(graph).items():
        (project / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (project / rel_path).write_text(content, encoding="utf-8")
    pipeline_file.write_text(graph_to_code(graph, pipeline_name="bindings"), encoding="utf-8")

    parsed = parse_pipeline_file(pipeline_file)
    run = _haute_run(parsed, "result", source="live")
    standalone = _import(pipeline_file).pipeline.run(source="live")
    deployed = score_graph(parsed, QuoteRequest(tuple(_RECORDS)), ["quotes"], "result")

    assert sorted((e.source, e.target) for e in parsed.edges) == sorted(
        (e.source, e.target) for e in graph.edges
    )
    assert_frame_equal(standalone, run)
    assert_frame_equal(deployed, run)


def test_a_frame_hook_keeps_the_connect_of_the_input_its_decorator_takes() -> None:
    graph = _mismatch_graph()(_TARGETS["frame_hook"][0])
    code = graph_to_code(graph, pipeline_name="bindings")
    assert 'pipeline.connect("rows", "target")' in code
    assert 'pipeline.connect("extra", "target")' not in code
