"""Contract tests for commit 6 — port-aware edge UI.

Strict TDD reds per the commit 6 spec in `MULTI_FRAME_PLAN.md:615-660`.
Pairs with frontend component tests under
`frontend/src/__tests__/nodes/`.

What this file covers (backend):
  - codegen emits `pipeline.connect("a", "b", source_port="p")` when an
    edge carries a non-empty `sourceHandle`.
  - codegen emits the bare `pipeline.connect("a", "b")` form when the
    edge has no `sourceHandle` (single-port shorthand preserved).
  - parser round-trips both forms back into edges with the correct
    `sourceHandle` value (str or None).
"""

from __future__ import annotations

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


def _minimal_graph_with_sourcehandle(handle: str | None) -> PipelineGraph:
    """A 2-node graph with one edge; the edge's sourceHandle is *handle*.

    Two polars nodes — the parser doesn't need on-disk config files for
    polars nodes, so we can keep the test self-contained and skip the
    config-file scaffold an apiInput round-trip would need.
    """
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(
                    label="quotes",
                    nodeType=NodeType.POLARS,
                    config={"code": "pl.LazyFrame()"},
                ),
            ),
            GraphNode(
                id="processing",
                data=NodeData(
                    label="processing",
                    nodeType=NodeType.POLARS,
                    config={"code": "df"},
                ),
            ),
        ],
        edges=[
            GraphEdge(
                id="e1",
                source="quotes",
                target="processing",
                sourceHandle=handle,
            ),
        ],
    )


def _minimal_graph_with_targethandle(handle: str | None) -> PipelineGraph:
    graph = _minimal_graph_with_sourcehandle(None)
    graph.edges[0].targetHandle = handle
    return graph


def test_codegen_emits_source_port_kwarg_when_sourcehandle_is_set() -> None:
    """For an edge with `sourceHandle="policies"`, codegen emits
    `pipeline.connect("quotes", "processing", source_port="policies")`.
    """
    from haute.codegen import graph_to_code

    graph = _minimal_graph_with_sourcehandle("policies")
    code = graph_to_code(graph, pipeline_name="t")
    assert 'pipeline.connect("quotes", "processing", source_port="policies")' in code, (
        f"expected source_port kwarg in generated code; got:\n{code}"
    )


def test_codegen_emits_bare_connect_when_sourcehandle_is_none() -> None:
    """Single-port edges (no `sourceHandle`) use the bare two-arg form."""
    from haute.codegen import graph_to_code

    graph = _minimal_graph_with_sourcehandle(None)
    code = graph_to_code(graph, pipeline_name="t")
    assert 'pipeline.connect("quotes", "processing")' in code, (
        f"expected bare connect form; got:\n{code}"
    )
    assert "source_port" not in code, (
        f"single-port edge must not emit source_port kwarg; got:\n{code}"
    )


def test_parser_round_trips_source_port_kwarg(tmp_path) -> None:
    """`parse_pipeline_file` recovers `sourceHandle` from the codegen output.

    Pipeline → code → file → parse → graph; the resulting edge has
    `sourceHandle == "policies"` (no normalisation, no loss). Per the
    commit 6 spec: "Saving and reloading the graph preserves each
    edge's sourceHandle value."
    """
    from haute.codegen import graph_to_code
    from haute.parser import parse_pipeline_file

    graph = _minimal_graph_with_sourcehandle("policies")
    code = graph_to_code(graph, pipeline_name="t")
    py_path = tmp_path / "t.py"
    py_path.write_text(code)
    parsed = parse_pipeline_file(py_path)
    matching = [e for e in parsed.edges if e.source == "quotes" and e.target == "processing"]
    assert len(matching) == 1
    assert matching[0].sourceHandle == "policies", (
        f"sourceHandle must round-trip; got {matching[0].sourceHandle!r}"
    )


def test_parser_round_trips_bare_connect_to_null_sourcehandle(tmp_path) -> None:
    """Bare `connect("a", "b")` round-trips with `sourceHandle is None`."""
    from haute.codegen import graph_to_code
    from haute.parser import parse_pipeline_file

    graph = _minimal_graph_with_sourcehandle(None)
    code = graph_to_code(graph, pipeline_name="t")
    py_path = tmp_path / "t.py"
    py_path.write_text(code)
    parsed = parse_pipeline_file(py_path)
    matching = [e for e in parsed.edges if e.source == "quotes" and e.target == "processing"]
    assert len(matching) == 1
    assert matching[0].sourceHandle is None


def test_parser_round_trips_source_port_with_special_chars(tmp_path) -> None:
    """source_port values with quote/backslash/unicode chars survive round-trip.

    Adversarial review C2: bare f-string interpolation of the port name
    would emit invalid Python for labels like `a"b` or `back\\slash`.
    The codegen uses ``json.dumps`` to escape the literal; this test
    pins that behaviour.
    """
    from haute.codegen import graph_to_code
    from haute.parser import parse_pipeline_file

    for tricky in ('a"b', "back\\slash", "with space", "unicode-é-é"):
        graph = _minimal_graph_with_sourcehandle(tricky)
        code = graph_to_code(graph, pipeline_name="t")
        py_path = tmp_path / "t.py"
        py_path.write_text(code)
        parsed = parse_pipeline_file(py_path)
        matching = [e for e in parsed.edges if e.source == "quotes" and e.target == "processing"]
        assert len(matching) == 1
        assert matching[0].sourceHandle == tricky, (
            f"sourceHandle must round-trip {tricky!r} verbatim; "
            f"got {matching[0].sourceHandle!r}\nemitted code:\n{code}"
        )


def test_codegen_emits_target_port_kwarg_when_targethandle_is_set() -> None:
    from haute.codegen import graph_to_code

    graph = _minimal_graph_with_targethandle("base")
    code = graph_to_code(graph, pipeline_name="t")

    assert 'pipeline.connect("quotes", "processing", target_port="base")' in code


def test_parser_round_trips_target_port_kwarg(tmp_path) -> None:
    from haute.codegen import graph_to_code
    from haute.parser import parse_pipeline_file

    graph = _minimal_graph_with_targethandle("join")
    code = graph_to_code(graph, pipeline_name="t")
    py_path = tmp_path / "t.py"
    py_path.write_text(code)

    parsed = parse_pipeline_file(py_path)

    matching = [e for e in parsed.edges if e.source == "quotes" and e.target == "processing"]
    assert len(matching) == 1
    assert matching[0].targetHandle == "join"


def test_generated_multiport_file_executes_as_plain_python(tmp_path) -> None:
    """A codegen file with a multi-port connect imports and runs cleanly.

    The AST parser never executes generated files, so a `connect()`
    signature that rejects `source_port` only surfaces when the file is
    run as plain Python (`import rating.main`, `Pipeline.run()`). This
    pins the "everything on disk is plain runnable Python" promise.
    """
    import runpy

    from haute.codegen import graph_to_code

    # A constant source (self-contained, no df input) feeding a polars node,
    # so the generated file is runnable end-to-end without on-disk data.
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="quotes",
                data=NodeData(
                    label="quotes",
                    nodeType=NodeType.CONSTANT,
                    config={"values": [{"name": "x", "value": "1"}]},
                ),
            ),
            GraphNode(
                id="processing",
                data=NodeData(
                    label="processing",
                    nodeType=NodeType.POLARS,
                    config={"code": "df"},
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="quotes", target="processing", sourceHandle="policies"),
        ],
    )
    code = graph_to_code(graph, pipeline_name="t")
    assert 'source_port="policies"' in code
    py_path = tmp_path / "t.py"
    py_path.write_text(code)

    ns = runpy.run_path(str(py_path))  # raises TypeError if connect() rejects source_port
    pipeline = ns["pipeline"]
    assert pipeline.edges == [("quotes", "processing")]
    assert pipeline.edge_ports == ["policies"]
    assert pipeline.run() is not None
