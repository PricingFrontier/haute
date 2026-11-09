"""Low-code Polars steps on frame-start surfaces.

Data Input, External File, Rating Step, Model Score and Scenario Expander nodes carry steps that
render, parse back, run and explore like a transform's steps.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._code_extraction import (
    INCOMPLETE_STEPS_MESSAGE,
)
from haute._config_io import collect_node_configs, node_emits_sidecar
from haute._flatten import flatten_graph
from haute._node_specs import SteppedSurface
from haute._polars_io_registry import validate_data_input_config
from haute._polars_steps import (
    PolarsStepError,
    RenderedSteps,
    is_stepped_config,
    render_polars_steps,
    step_input_names,
    stepped_surface_for,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelOutputPort,
)
from haute.codegen import graph_to_code
from haute.errors import ConfigError, ParseError
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file, parse_pipeline_source
from haute.routes._helpers import parse_pipeline_to_graph
from tests import _polars_steps_helpers
from tests._polars_steps_helpers import (
    GOLDEN_CODE,
    GOLDEN_STEP_LINES,
    GOLDEN_STEPS,
    _capped_context,
    _explore,
    _external_file,
    _frames,
    _lifted,
    _rating_step,
    _ready_source,
    _render_body,
    _reparsed,
    _save,
    _scenario_expander,
    _stepped_input,
    _surface_graph,
    _write_sidecars,
    source,
    step,
)
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
)
from tests.test_codegen_execution_equivalence import _collect, _write_and_import

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
project_root = _polars_steps_helpers.project_root


# ---------------------------------------------------------------------------
# Frame-start surfaces: Data Input
# ---------------------------------------------------------------------------


def _error_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


def test_frame_start_renders_without_a_source_step() -> None:
    assert render_polars_steps([], [], start="frame") == RenderedSteps(code="", step_lines=())
    rendered = render_polars_steps([step("l", "limit", n=3)], [], start="frame")
    assert rendered.code == "df = df.head(3)"
    assert rendered.step_lines == ((1, 1),)

    with pytest.raises(PolarsStepError) as refused:
        render_polars_steps([step("l", "limit", n=3), source()], [], start="frame")
    assert refused.value.step_index == 1
    assert refused.value.message == "This node starts from df; remove the start step."

    join = step(
        "j",
        "join",
        input="rates",
        how="inner",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_r",
    )
    with pytest.raises(PolarsStepError, match="Unknown input 'rates'; connected inputs: none"):
        render_polars_steps([join], [], start="frame")

    # Every other kind renders byte-identically in both modes.
    frame = render_polars_steps(GOLDEN_STEPS[1:], ["quotes", "rates"], start="frame")
    assert frame.code == "\n".join(GOLDEN_CODE.splitlines()[1:])
    assert frame.step_lines == tuple((a - 1, b - 1) for a, b in GOLDEN_STEP_LINES[1:])

    with pytest.raises(TypeError):
        render_polars_steps(GOLDEN_STEPS)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="Unknown step start"):
        render_polars_steps(GOLDEN_STEPS, start="nowhere")  # type: ignore[arg-type]
    with pytest.raises(PolarsStepError, match="Choose the input to start from"):
        render_polars_steps([], start="input")


def test_stepped_surface_table_and_input_eligibility() -> None:
    assert stepped_surface_for(NodeType.POLARS) == SteppedSurface(start="input", inputs="edges")
    assert stepped_surface_for(NodeType.DATA_INPUT) == SteppedSurface(start="frame", inputs="none")
    assert step_input_names(NodeType.POLARS, ["quotes", "rates"]) == ["quotes", "rates"]
    assert step_input_names(NodeType.DATA_INPUT, ["quotes"]) == []
    with pytest.raises(ValueError, match="does not author steps"):
        step_input_names(NodeType.OUTPUT, [])
    assert is_stepped_config(NodeType.DATA_INPUT, {"steps": []})
    assert not is_stepped_config(NodeType.DATA_INPUT, {"code": ""})
    assert not is_stepped_config(NodeType.SCENARIO_EXPANDER, {"steps": 3})


def test_node_data_materialises_data_input_steps() -> None:
    base = make_file_input_config("quotes.parquet")

    def node(steps: object) -> GraphNode:
        return GraphNode(
            id="d",
            data=NodeData(
                label="d", nodeType="dataInput", config={**base, "steps": steps, "code": "stale"}
            ),
        )

    assert node([]).data.config["code"] == ""
    assert node([]).data.editor.steps_error is None
    assert node([step("l", "limit", n=2)]).data.config["code"] == "df = df.head(2)"
    broken = node([source()])
    assert broken.data.config["code"] == ""
    assert (
        broken.data.editor.steps_error == "Step 1: This node starts from df; remove the start step."
    )
    with pytest.raises(ValueError, match="must be a list"):
        node("not-a-list")

    # A Data Input has no eligible input names, so a reference to one is an
    # error at materialisation, not only at build time.
    join = step(
        "j",
        "join",
        input="rates",
        how="inner",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_r",
    )
    joined = node([join])
    assert joined.data.config["code"] == ""
    assert (
        joined.data.editor.steps_error == "Step 1: Unknown input 'rates'; connected inputs: none."
    )
    stacked = node([step("c", "concat", inputs=["rates"], how="vertical")])
    assert stacked.data.config["code"] == ""
    assert stacked.data.editor.steps_error.startswith("Step 1: Unknown input 'rates'")

    # A type outside the surface table keeps a `steps` key untouched; a
    # stepped type refuses anything but a list (the Scenario Expander's grid
    # size lives under `step_count`).
    other = GraphNode(id="o", data=NodeData(label="o", nodeType="output", config={"steps": 5}))
    assert other.data.config == {"steps": 5}
    with pytest.raises(ValueError, match="must be a list"):
        GraphNode(
            id="s",
            data=NodeData(label="s", nodeType="scenarioExpander", config={"steps": 5}),
        )


def test_data_input_validation_carries_steps_and_its_sidecar_writes_none() -> None:
    config = {**make_file_input_config("quotes.parquet"), "steps": [step("l", "limit", n=2)]}
    assert validate_data_input_config(config)["steps"] == config["steps"]
    databricks = {
        "input_type": "databricks",
        "http_path": "p",
        "table": "t",
        "arguments": {},
        "steps": [],
    }
    assert validate_data_input_config(databricks)["steps"] == []

    node = GraphNode(id="d", data=NodeData(label="d", nodeType="dataInput", config=config))
    assert node_emits_sidecar(node)
    written = json.loads(
        collect_node_configs(PipelineGraph(nodes=[node], edges=[]))["config/data_input/d.json"]
    )
    # Its code is the only copy of its steps (READ-44).
    assert "steps" not in written
    assert "code" not in written


def test_data_input_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    stepped = _stepped_input(quotes, [step("l", "limit", n=2)])
    graph = PipelineGraph(nodes=[stepped], edges=[])
    result = execute_graph(graph)["quotes"]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2

    code = graph_to_code(graph, pipeline_name="main")
    assert "@pipeline.data_input(" in code
    assert "config/data_input/quotes.json" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    _write_sidecars(tmp_path, graph)
    sidecar = json.loads(
        (tmp_path / "config" / "data_input" / "quotes.json").read_text(encoding="utf-8")
    )
    assert "steps" not in sidecar

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    assert node.data.config["steps"] == _lifted([step("l", "limit", n=2)], "dataInput")
    assert node.data.config["code"] == "df = df.head(2)"
    assert node.data.editor.steps_discarded is None
    reloaded = execute_graph(parsed)["quotes"]
    assert reloaded.status == "ok", reloaded.error
    assert len(reloaded.preview) == 2


def test_data_input_incomplete_steps_fail_on_every_path(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    join = step(
        "j",
        "join",
        input="rates",
        how="inner",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_r",
    )
    unknown = _stepped_input(quotes, [join])
    # Materialisation already refuses the reference (no eligible inputs).
    assert unknown.data.config["code"] == ""
    assert (
        unknown.data.editor.steps_error == "Step 1: Unknown input 'rates'; connected inputs: none."
    )
    graph = PipelineGraph(nodes=[unknown], edges=[])

    # Builder path: the executor names the step.
    result = execute_graph(graph)["quotes"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    assert "Step 1: Unknown input 'rates'; connected inputs: none." in str(result.error)

    # Generated module: the placeholder replaces the post-load lines, and the steps wait in
    # .haute.json, which gives them back to the graph a run loads (READ-44).
    code, node = _reparsed(tmp_path, graph, "quotes")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert "df.join" not in code
    parsed = parse_pipeline_to_graph(tmp_path / "main.py")
    assert node.data.config["steps"] == [join]
    assert node.data.editor.steps_discarded is None
    assert execute_graph(parsed)["quotes"].status == "error"

    broken = _stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])
    assert broken.data.editor.steps_error == "Step 1: Add at least one condition."
    broken_graph = PipelineGraph(nodes=[broken], edges=[])
    assert "Step 1: Add at least one condition." in str(execute_graph(broken_graph)["quotes"].error)
    _broken_code, reloaded = _reparsed(tmp_path, broken_graph, "quotes")
    assert reloaded.data.config["steps"] == broken.data.config["steps"]
    assert reloaded.data.config["code"] == ""
    assert reloaded.data.editor.steps_error == "Step 1: Add at least one condition."

    # `haute run` and a deploy parse the file themselves: the steps come back there too, so
    # the node fails naming its step, never reading the source unchanged, and a deploy
    # refuses it before preparing anything.
    from haute.deploy._scorer import _reject_incomplete_stepped_nodes

    run_graph = parse_pipeline_file(tmp_path / "main.py", flatten=True)
    ran = execute_graph(run_graph)["quotes"]
    assert ran.status == "error"
    assert "Step 1: Add at least one condition." in str(ran.error)
    with pytest.raises(ConfigError, match="Step 1: Add at least one condition."):
        _reject_incomplete_stepped_nodes(run_graph, {"quotes"})

    # A placeholder whose steps .haute.json does not hold would skip them: it is refused.
    (tmp_path / "main.haute.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ParseError, match="'quotes' is saved with unfinished steps.*not hold them"):
        parse_pipeline_file(tmp_path / "main.py", flatten=True)
    (tmp_path / "main.haute.json").unlink()
    with pytest.raises(ParseError, match="main.haute.json, but that file does not hold them"):
        parse_pipeline_file(tmp_path / "main.py")


def _bundled_scoring_graph(steps: list[dict[str, Any]]) -> PipelineGraph:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "static_ds",
                    "data": {
                        "label": "static_ds",
                        "nodeType": "dataInput",
                        "config": {
                            "input_type": "file",
                            "format": "csv",
                            "mode": "scan",
                            "path": "original/factors.csv",
                            "arguments": {},
                            "steps": steps,
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "output_mapping": [
                                {
                                    "source_port": "static_ds",
                                    "source_column": col,
                                    "output_path": f"$[:].{col}",
                                    "enabled": True,
                                }
                                for col in ("area", "factor")
                            ],
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "static_ds", "sourceHandle": "src"},
                {"id": "e2", "source": "static_ds", "target": "out"},
            ],
        }
    )


def test_data_input_steps_guard_the_deploy_bundled_snapshot_path(tmp_path: Path) -> None:
    from haute.deploy._scorer import score_graph

    ds_path = tmp_path / "factors.parquet"
    pl.DataFrame({"area": ["A", "B"], "factor": [1.1, 1.2]}).write_parquet(ds_path)
    # A bundled snapshot is its generation's parts, one artifact each, scanned
    # in part order — not a single file.
    remap = {"static_ds__snapshot.part-00000.parquet": str(ds_path)}
    input_df = pl.DataFrame({"x": [1.0]})

    scored = score_graph(
        graph=_bundled_scoring_graph([step("l", "limit", n=1)]),
        input_df=input_df,
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths=remap,
    )
    assert isinstance(scored, pl.DataFrame)
    assert scored["area"].to_list() == ["A"]

    with pytest.raises(Exception, match=r"Step 1: Add at least one condition") as failed:
        score_graph(
            graph=_bundled_scoring_graph([step("f", "filter", match="all", conditions=[])]),
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths=remap,
        )
    chain = _error_chain(failed.value)
    assert INCOMPLETE_STEPS_MESSAGE in chain
    assert "Step 1: Add at least one condition." in chain


def test_data_input_hand_edit_is_read_back_as_steps(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("df = df.head(2)", "df = df.head(3)")
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    # Its code is the only copy of its steps (READ-44): the edit is the step it renders.
    assert node.data.config["steps"] == _lifted([step("l", "limit", n=3)], "dataInput")
    assert node.data.config["code"] == "df = df.head(3)"
    assert node.data.editor.steps_discarded is None


def test_data_input_free_code_with_redundant_parentheses_reloads_in_step_mode(
    tmp_path: Path,
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("c", "free_code", code="df = (df.head(2))")]
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    assert "\n    df = (df.head(2))\n" in code
    _write_sidecars(tmp_path, graph)

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    # Read through extraction's finaliser, the code is the Limit step's (READ-44).
    assert node.data.config["steps"] == _lifted(steps, "dataInput")
    assert node.data.config["code"] == "df = df.head(2)"
    assert node.data.editor.steps_discarded is None
    result = execute_graph(parsed)["quotes"]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2


def test_save_warns_about_incomplete_data_input_steps(project_root: Path) -> None:
    quotes, rates = _frames(project_root)
    broken = _stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])
    warnings = _save(project_root, PipelineGraph(nodes=[broken, rates], edges=[]))
    assert any(
        "Data Input node 'quotes' has an incomplete step list (Step 1: Add at least one condition.)"
        in w
        for w in warnings
    ), warnings
    complete = _stepped_input(quotes, [step("l", "limit", n=1)])
    assert _save(project_root, PipelineGraph(nodes=[complete, rates], edges=[])) == []


def test_render_endpoint_requires_start_and_renders_frame_mode(client: TestClient) -> None:
    missing = client.post(
        "/api/pipeline/polars-steps/render", json={"steps": [], "input_names": []}
    )
    assert missing.status_code == 422

    empty = client.post(
        "/api/pipeline/polars-steps/render", json=_render_body([], [], start="frame")
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "ok": True,
        "code": "",
        "step_lines": [],
        "step_index": None,
        "message": "",
    }

    limited = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body([step("l", "limit", n=2)], [], start="frame"),
    )
    assert limited.json() == {
        "ok": True,
        "code": "df = df.head(2)",
        "step_lines": [[1, 1]],
        "step_index": None,
        "message": "",
    }

    refused = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body([source()], [], start="frame"),
    )
    assert refused.json() == {
        "ok": False,
        "code": "",
        "step_lines": [],
        "step_index": 0,
        "message": "This node starts from df; remove the start step.",
    }


def _corpus_translations_without_inputs() -> list[tuple[str, list[dict[str, Any]]]]:
    fixtures = Path(__file__).parent / "fixtures" / "polars_steps_corpus" / "translations.json"
    translations = json.loads(fixtures.read_text(encoding="utf-8"))
    cases: list[tuple[str, list[dict[str, Any]]]] = []
    for name, translation in translations.items():
        steps = translation["steps"]
        if not steps or steps[0]["kind"] != "source":
            continue
        rest = steps[1:]
        if any(s["kind"] in ("join", "concat") for s in rest):
            continue
        cases.append((name, rest))
    assert cases
    return cases


@pytest.mark.parametrize(("name", "steps"), _corpus_translations_without_inputs())
def test_corpus_translations_reload_unchanged_on_a_data_input(
    tmp_path: Path, name: str, steps: list[dict[str, Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    expected = render_polars_steps(steps, [], start="frame").code
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "quotes"
    )
    assert node.data.config["steps"] == _lifted(steps, "dataInput"), name
    assert node.data.config["code"] == expected, name
    assert node.data.editor.steps_discarded is None, name


def test_data_input_free_code_opening_with_a_string_reloads_in_step_mode(tmp_path: Path) -> None:
    """A rendering that starts with a string statement is not a docstring to the parser."""
    quotes, _rates = _frames(tmp_path)
    steps = [step("c", "free_code", code='"""Keep two rows."""\ndf = df.head(2)')]
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    assert '\n    """Keep two rows."""\n    df = df.head(2)\n' in code
    _write_sidecars(tmp_path, graph)

    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "quotes"
    )
    assert node.data.config["steps"] == _lifted(steps, "dataInput")
    assert node.data.config["code"] == '"""Keep two rows."""\ndf = df.head(2)'
    assert node.data.editor.steps_discarded is None


def test_data_input_generated_module_runs_standalone(tmp_path: Path) -> None:
    """The generated function itself applies the steps, and raises for an incomplete list."""
    quotes, _rates = _frames(tmp_path)
    # The module resolves its project root by walking up to a haute.toml inside a git repository.
    (tmp_path / "haute.toml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    module = _write_and_import(
        PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[]),
        tmp_path,
    )
    frame = _collect(module.pipeline.run(source="batch"))
    assert frame.height == 2
    assert frame["premium"].to_list() == [50.0, 200.0]

    broken = _write_and_import(
        PipelineGraph(
            nodes=[_stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])],
            edges=[],
        ),
        tmp_path,
    )
    with pytest.raises(NotImplementedError, match=INCOMPLETE_STEPS_MESSAGE.split(".")[0]):
        _collect(broken.pipeline.run(source="batch"))


# ---------------------------------------------------------------------------
# Frame-start surfaces: External File, Rating Step, Model Score, Scenario Expander
# ---------------------------------------------------------------------------


def _model_score(steps: list[dict[str, Any]]) -> GraphNode:
    config = {
        "source_type": "registered",
        "registered_model": "catalog.models.pricing",
        "version": "1",
        "task": "regression",
        "output_column": "prediction",
        "steps": steps,
    }
    return GraphNode(
        id="scored", data=NodeData(label="scored", nodeType="modelScore", config=config)
    )


def test_external_file_steps_reach_the_other_inputs_and_obj(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    join = step(
        "j",
        "join",
        input="rates",
        how="inner",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_r",
    )
    steps = [join, step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, rates, _external_file(tmp_path, steps)],
        edges=[make_edge("quotes", "ext"), make_edge("rates", "ext")],
    )
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "ok", result.error
    assert "rate" in [c.name for c in result.columns]
    assert len(result.preview) == 2

    code, node = _reparsed(tmp_path, graph, "ext")
    assert (
        "def ext(quotes: pl.LazyFrame, rates: pl.LazyFrame, *, obj) -> pl.LazyFrame:\n"
        "    df = quotes\n"
        "    df = df.join(\n"
    ) in code
    assert node.data.config["steps"] == _lifted(steps, "externalFile")
    assert (
        node.data.config["code"]
        == render_polars_steps(steps, ["quotes", "rates"], start="frame").code
    )
    assert node.data.editor.steps_discarded is None

    # Free code reaches the loaded object as `obj`, as the code box always could.
    reaching = [
        step("c", "free_code", code='df = df.with_columns(pl.lit(obj["factor"]).alias("factor"))')
    ]
    graph = PipelineGraph(
        nodes=[quotes, _external_file(tmp_path, reaching)], edges=[make_edge("quotes", "ext")]
    )
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "ok", result.error
    assert result.preview[0]["factor"] == 2.0


def test_external_file_unknown_input_and_input_mapping_fail_loudly(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    join = step(
        "j",
        "join",
        input="policies",
        how="inner",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_r",
    )
    unknown = _external_file(tmp_path, [join])
    # An edges surface renders without names at construction; the reference
    # is checked at build time against the connected edges.
    assert unknown.data.editor.steps_error is None
    graph = PipelineGraph(nodes=[quotes, unknown], edges=[make_edge("quotes", "ext")])
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    assert "Step 1: Unknown input 'policies'; connected inputs: quotes." in str(result.error)

    code, node = _reparsed(tmp_path, graph, "ext")
    assert "\n    df = quotes\n    raise NotImplementedError(" in code
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == [join]
    assert node.data.editor.steps_discarded is None

    mapped = _external_file(tmp_path, [step("l", "limit", n=1)], input_mapping={"quotes": "quotes"})
    graph = PipelineGraph(nodes=[quotes, mapped], edges=[make_edge("quotes", "ext")])
    with pytest.raises(ConfigError, match="cannot carry input_mapping"):
        execute_graph(graph, target_node_id="ext", execution_context=_capped_context())


def test_flatten_rewrites_a_stepped_external_file_input() -> None:
    child = PipelineGraph(
        nodes=[
            GraphNode(
                id="output", data=NodeData(label="Internal Output", nodeType="polars", config={})
            )
        ],
        edges=[],
    )
    definition = SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(name="results", source=SubmodelEndpoint(node_id="output"))
        ],
    )
    instance = GraphNode(
        id="instance_a",
        data=NodeData(
            label="score",
            nodeType="submodel",
            config={"definition_id": "definition_scoring", "alias": "score"},
        ),
    )
    join = step(
        "j",
        "join",
        input="results",
        how="inner",
        maintain_order="left_right",
        left_on=["k"],
        right_on=["k"],
        suffix="_r",
    )
    consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="Consumer",
            nodeType="externalFile",
            config={"path": "models/obj.pkl", "file_type": "pickle", "steps": [join]},
        ),
    )
    graph = PipelineGraph(
        nodes=[instance, consumer],
        edges=[
            GraphEdge(
                id="output", source="instance_a", target="consumer", sourceHandle="out__results"
            )
        ],
        submodels={"definition_scoring": definition},
    )
    flat = flatten_graph(graph).node_map["consumer"].data.config
    assert flat["steps"] == [{**join, "input": "Internal_Output"}]
    assert "input_mapping" not in flat


def test_rating_step_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(nodes=[quotes, _rating_step(steps)], edges=[make_edge("quotes", "rated")])
    result = execute_graph(graph, target_node_id="rated", execution_context=_capped_context())[
        "rated"
    ]
    assert result.status == "ok", result.error
    assert "rate_factor" in [c.name for c in result.columns]
    assert len(result.preview) == 2

    code, node = _reparsed(tmp_path, graph, "rated")
    assert "def rated(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    assert node.data.config["steps"] == _lifted(steps, node.data.nodeType)
    assert node.data.config["code"] == "df = df.head(2)"
    assert node.data.editor.steps_discarded is None

    broken = _rating_step([step("f", "filter", match="all", conditions=[])])
    assert broken.data.editor.steps_error == "Step 1: Add at least one condition."
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "rated")])
    result = execute_graph(graph, target_node_id="rated", execution_context=_capped_context())[
        "rated"
    ]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    code, node = _reparsed(tmp_path, graph, "rated")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == broken.data.config["steps"]
    assert node.data.editor.steps_error == "Step 1: Add at least one condition."


def test_scenario_expander_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=4)]
    graph = PipelineGraph(
        nodes=[quotes, _scenario_expander(steps)], edges=[make_edge("quotes", "grid")]
    )
    result = execute_graph(graph, target_node_id="grid", execution_context=_capped_context())[
        "grid"
    ]
    assert result.status == "ok", result.error
    assert "scenario_value" in [c.name for c in result.columns]
    assert len(result.preview) == 4  # 4 rows x 3 grid values, then the Limit

    code, node = _reparsed(tmp_path, graph, "grid")
    assert "def grid(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(4)\n    return df\n" in code
    assert node.data.config["steps"] == _lifted(steps, "scenarioExpander")
    assert node.data.config["step_count"] == 3
    assert node.data.editor.steps_discarded is None

    broken = _scenario_expander([source()])
    assert (
        broken.data.editor.steps_error == "Step 1: This node starts from df; remove the start step."
    )
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "grid")])
    assert (
        execute_graph(graph, target_node_id="grid", execution_context=_capped_context())[
            "grid"
        ].status
        == "error"
    )
    code, node = _reparsed(tmp_path, graph, "grid")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == [source()]


def test_model_score_steps_round_trip_and_fail_before_any_model_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.deploy import _scorer
    from haute.deploy._scorer import score_graph

    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, _model_score(steps)], edges=[make_edge("quotes", "scored")]
    )
    code, node = _reparsed(tmp_path, graph, "scored")
    assert "def scored(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    assert node.data.config["steps"] == _lifted(steps, node.data.nodeType)
    assert node.data.config["code"] == "df = df.head(2)"
    assert node.data.editor.steps_discarded is None

    broken = _model_score([step("f", "filter", match="all", conditions=[])])
    assert broken.data.editor.steps_error == "Step 1: Add at least one condition."
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "scored")])
    result = execute_graph(graph, target_node_id="scored", execution_context=_capped_context())[
        "scored"
    ]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    code, node = _reparsed(tmp_path, graph, "scored")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == broken.data.config["steps"]

    # Deploy: the plan is rejected before any model loading or scoring is attempted.
    deploy_graph = make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "scored",
                    "data": {
                        "label": "scored",
                        "nodeType": "modelScore",
                        "config": broken.data.config,
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "output_mapping": [
                                {
                                    "source_port": "scored",
                                    "source_column": "prediction",
                                    "output_path": "$[:].prediction",
                                    "enabled": True,
                                }
                            ],
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "scored", "sourceHandle": "src"},
                {"id": "e2", "source": "scored", "target": "out"},
            ],
        }
    )
    # A remapped model WITHOUT a bundled feature contract is exactly the case
    # whose preparation pass (`_attach_bundled_model_contract_inputs`) loads the
    # local model, so a guard placed after it would load before rejecting.
    model_path = tmp_path / "score.cbm"
    model_path.write_bytes(b"not a real model")
    loaded: list[str] = []
    monkeypatch.setattr(_scorer, "load_local_model_cached", lambda path, task: loaded.append(path))
    with pytest.raises(ConfigError, match=r"Step 1: Add at least one condition") as failed:
        score_graph(
            graph=deploy_graph,
            input_df=pl.DataFrame({"x": [1.0]}),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"scored__score.cbm": str(model_path)},
        )
    assert INCOMPLETE_STEPS_MESSAGE in str(failed.value)
    assert loaded == [], "the deploy plan loaded a model before rejecting the incomplete steps"


def test_recovery_and_save_report_incomplete_steps_on_every_surface(project_root: Path) -> None:
    from haute._node_config_recovery import validate_recovery_config

    pl.DataFrame({"premium": [1.0]}).write_parquet(project_root / "quotes.parquet")
    broken_steps = [step("f", "filter", match="all", conditions=[])]
    # Raw sidecar-shaped candidates are not node data and carry no derived steps
    # error, so recovery must render the steps itself to report the incomplete one.
    for node in (
        _rating_step(broken_steps),
        _scenario_expander(broken_steps),
        _model_score(broken_steps),
        _stepped_input(_ready_source("quotes", project_root / "quotes.parquet"), broken_steps),
    ):
        assert node.data.editor.steps_error is not None
        raw = {k: v for k, v in node.data.config.items() if k != "code"}
        issues = validate_recovery_config(node.data.nodeType, raw, input_names=None)
        assert any(issue.path == "/steps" and issue.code == "incomplete" for issue in issues), (
            node.data.nodeType
        )
        # A renderable list on the same raw shape reports nothing.
        ok_raw = {**raw, "steps": [step("l", "limit", n=2)]}
        assert not [
            issue
            for issue in validate_recovery_config(node.data.nodeType, ok_raw, input_names=None)
            if issue.path == "/steps"
        ], node.data.nodeType

    quotes, rates = _frames(project_root)
    warnings = _save(
        project_root,
        PipelineGraph(
            nodes=[quotes, rates, _rating_step(broken_steps)], edges=[make_edge("quotes", "rated")]
        ),
    )
    assert any(
        "Rating Step node 'rated' has an incomplete step list (Step 1: Add at least one condition.)"
        in w
        for w in warnings
    ), warnings


#: Every frame surface whose generated module runs without an external model.
_RUNNABLE_FRAME_SURFACES = ("dataInput", "externalFile", "ratingStep", "scenarioExpander")


@pytest.mark.parametrize("surface", _RUNNABLE_FRAME_SURFACES)
def test_frame_surface_generated_module_runs_standalone(tmp_path: Path, surface: str) -> None:
    """The generated function itself applies the steps, and raises for an incomplete list."""
    (tmp_path / "haute.toml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    graph, _node_id = _surface_graph(tmp_path, surface, [step("l", "limit", n=2)])
    module = _write_and_import(graph, tmp_path)
    frame = _collect(module.pipeline.run(source="batch"))
    assert frame.height == 2, surface

    broken, _ = _surface_graph(tmp_path, surface, [step("f", "filter", match="all", conditions=[])])
    broken_module = _write_and_import(broken, tmp_path)
    with pytest.raises(NotImplementedError, match=INCOMPLETE_STEPS_MESSAGE.split(".")[0]):
        _collect(broken_module.pipeline.run(source="batch"))


@pytest.mark.parametrize("surface", (*_RUNNABLE_FRAME_SURFACES, "modelScore"))
def test_frame_surface_free_code_with_redundant_parentheses_reloads_in_step_mode(
    tmp_path: Path, surface: str
) -> None:
    """Each surface's extractor normalises its own rendering, so reconcile must not discard it."""
    steps = [step("c", "free_code", code="df = (df.head(2))")]
    if surface == "modelScore":
        quotes, _rates = _frames(tmp_path)
        graph = PipelineGraph(
            nodes=[quotes, _model_score(steps)], edges=[make_edge("quotes", "scored")]
        )
        node_id = "scored"
    else:
        graph, node_id = _surface_graph(tmp_path, surface, steps)

    code = graph_to_code(graph, pipeline_name="main")
    assert "df = (df.head(2))" in code
    _write_sidecars(tmp_path, graph)
    node = next(n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == node_id)
    assert node.data.config["steps"] == _lifted(steps, node.data.nodeType), surface
    # Read through extraction's finaliser, the code is the Limit step's (READ-44).
    assert node.data.config["code"] == "df = df.head(2)", surface
    assert node.data.editor.steps_discarded is None, surface


#: Every surface whose steps start from a frame the surface already bound to df.
_FRAME_START_SURFACES = (*_RUNNABLE_FRAME_SURFACES, "modelScore", "explore")


_COMMENT_FIRST_CODE = "# keep two rows\ndf = df.head(2)"


def _frame_start_graph(
    tmp_path: Path, surface: str, steps: list[dict[str, Any]]
) -> tuple[PipelineGraph, str]:
    """:func:`_surface_graph`, extended to the surfaces that need a model or cards."""
    if surface not in ("modelScore", "explore"):
        return _surface_graph(tmp_path, surface, steps)
    quotes, _rates = _frames(tmp_path)
    node = _model_score(steps) if surface == "modelScore" else _explore(steps)
    return PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", node.id)]), node.id


@contextlib.contextmanager
def _scoring_model_stub() -> Iterator[None]:
    """Serve every registered model as a one-feature regressor, so Model Score resolves."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from haute._mlflow_io import ScoringModel

    raw = MagicMock()
    raw.feature_names_ = ["premium"]
    raw.predict.side_effect = lambda frame: np.ones(len(frame))
    raw.get_cat_feature_indices.return_value = []
    del raw.predict_proba
    model = ScoringModel(
        model=raw, feature_names=["premium"], cat_feature_names=frozenset(), flavor="catboost"
    )
    with patch("haute._mlflow_io.load_mlflow_model", return_value=model):
        yield


def _assert_comment_first_steps_kept(main: Path, node_id: str, surface: str) -> None:
    node = next(n for n in parse_pipeline_file(main).nodes if n.id == node_id)
    saved = [step("c", "free_code", code=_COMMENT_FIRST_CODE)]
    # Every surface's steps come from its code (READ-43, READ-44).
    assert node.data.config["steps"] == _lifted(saved, node.data.nodeType), surface
    assert node.data.config["code"] == _COMMENT_FIRST_CODE, surface
    assert node.data.editor.steps_discarded is None, surface


@pytest.mark.parametrize("surface", _FRAME_START_SURFACES)
def test_comment_first_free_code_keeps_its_steps_through_the_editor_save(
    project_root: Path, surface: str
) -> None:
    """The comment above the first statement is part of the body the steps must render."""
    steps = [step("c", "free_code", code=_COMMENT_FIRST_CODE)]
    graph, node_id = _frame_start_graph(project_root, surface, steps)
    _save(project_root, graph)
    _assert_comment_first_steps_kept(project_root / "main.py", node_id, surface)


@pytest.mark.parametrize("surface", _FRAME_START_SURFACES)
async def test_comment_first_free_code_keeps_its_steps_through_the_assistant_apply(
    project_root: Path, surface: str
) -> None:
    from haute.assistant._application import PipelineApplicationService

    graph, node_id = _frame_start_graph(project_root, surface, [])
    _save(project_root, graph)
    service = PipelineApplicationService(
        project_root=project_root,
        pipeline_root=project_root,
        mutations_readiness=lambda _root: (True, None),
        publish_document_update=lambda _source, _change: "f" * 64,
    )
    steps = [step("c", "free_code", code=_COMMENT_FIRST_CODE)]
    with _scoring_model_stub():
        plan = service.dry_run(
            "main.py",
            [{"op": "update_node", "node": node_id, "config": {"steps": steps}}],
            summary="Test plan.",
        ).plan
        await service.apply("main.py", plan.plan_hash)
    _assert_comment_first_steps_kept(project_root / "main.py", node_id, surface)


def test_model_score_steps_run_after_scoring(tmp_path: Path) -> None:
    """A Model Score's steps apply to the scored frame, as its post-processing code does."""
    quotes, _rates = _frames(tmp_path)
    scored = _model_score([step("l", "limit", n=2)])
    graph = PipelineGraph(nodes=[quotes, scored], edges=[make_edge("quotes", "scored")])
    with _scoring_model_stub():
        result = execute_graph(graph, target_node_id="scored", execution_context=_capped_context())[
            "scored"
        ]
    assert result.status == "ok", result.error
    assert "prediction" in [c.name for c in result.columns]
    assert len(result.preview) == 2


def test_explore_steps_execute_and_round_trip_through_its_code(tmp_path: Path) -> None:
    """An Explore node's steps live in its code alone, and its settings in its sidecar
    (READ-43): the load rebuilds the steps from the body."""
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(nodes=[quotes, _explore(steps)], edges=[make_edge("quotes", "report")])
    result = execute_graph(graph, target_node_id="report", execution_context=_capped_context())[
        "report"
    ]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2

    code = graph_to_code(graph, pipeline_name="main")
    assert '@pipeline.explore(config="config/explore/report.json")\n' in code
    assert "steps=" not in code
    assert "(df: pl.LazyFrame) -> pl.LazyFrame:\n    df = df.head(2)\n    return df\n" in code
    # The sidecar holds no copy of the steps.
    assert collect_node_configs(graph)["config/explore/report.json"] == "{}\n"

    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert node.data.config["steps"] == _lifted(steps, "explore")
    assert node.data.config["code"] == "df = df.head(2)"
    assert node.data.editor.steps_discarded is None


def test_explore_steps_keep_their_cards_and_fail_loudly_when_incomplete(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    from tests.test_explore_pivots import _pivot

    pivots = [_pivot()]
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, _explore(steps, pivots=pivots)], edges=[make_edge("quotes", "report")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert node.data.config["steps"] == _lifted(steps, "explore")
    assert [pivot["id"] for pivot in node.data.config["pivots"]] == [pivots[0]["id"]]

    broken = _explore([step("f", "filter", match="all", conditions=[])])
    assert broken.data.editor.steps_error == "Step 1: Add at least one condition."
    broken_graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "report")])
    result = execute_graph(
        broken_graph, target_node_id="report", execution_context=_capped_context()
    )["report"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)

    # The placeholder is the body, and the unfinished list waits in .haute.json (READ-43).
    broken_code, reloaded = _reparsed(tmp_path, broken_graph, "report")
    assert INCOMPLETE_STEPS_MESSAGE in broken_code
    assert reloaded.data.config["steps"] == broken.data.config["steps"]
    assert reloaded.data.config["code"] == ""
    assert reloaded.data.editor.steps_error == "Step 1: Add at least one condition."


def test_explore_hand_edit_is_read_back_as_steps(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, _explore([step("l", "limit", n=2)])], edges=[make_edge("quotes", "report")]
    )
    _write_sidecars(tmp_path, graph)
    code = graph_to_code(graph, pipeline_name="main")
    edited = code.replace("df = df.head(2)", "df = df.head(3)")
    assert edited != code
    node = next(
        n for n in parse_pipeline_source(edited, _base_dir=tmp_path).nodes if n.id == "report"
    )
    # Its code is the only copy of its steps (READ-43): the edit is the step it renders.
    assert node.data.config["steps"] == _lifted([step("l", "limit", n=3)], "explore")
    assert node.data.config["code"] == "df = df.head(3)"
    assert node.data.editor.steps_discarded is None
