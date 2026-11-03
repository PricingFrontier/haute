"""A Polars transform's steps live only in its code in the pipeline file (READ-22).

Spec: specs/pipeline-config/high-level.md, Stepped transforms. Loading rebuilds the steps from
the body (``lift_steps``): a statement a step renders exactly becomes that step and a run of
other statements one code step; no ``config=`` and no ``config/polars/`` file exist; the
column settings are decorator keywords; an unfinished step list waits in ``.haute.json``
behind the raising placeholder.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._config_io import collect_node_configs
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._polars_steps import render_polars_steps
from haute.codegen import graph_to_code
from haute.errors import ConfigError
from haute.executor import execute_graph
from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_source
from haute.routes._helpers import parse_pipeline_to_graph, save_sidecar
from tests.conftest import build_test_input_snapshot, make_edge, make_source_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def num(value: float) -> dict[str, Any]:
    return {"kind": "literal", "type": "number", "value": value}


def col(name: str) -> dict[str, Any]:
    return {"kind": "column", "name": name}


STEPS: list[dict[str, Any]] = [
    {"id": "s", "kind": "source", "input": "quotes"},
    {
        "id": "f",
        "kind": "filter",
        "match": "all",
        "conditions": [{"column": "premium", "operator": "gt", "value": num(100)}],
    },
    {
        "id": "w",
        "kind": "with_column",
        "name": "gross",
        "expr": {"type": "binary", "left": col("premium"), "op": "*", "right": num(1.12)},
    },
    {"id": "l", "kind": "limit", "n": 2},
]


def _quotes(tmp_path: Path) -> GraphNode:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"premium": [50.0, 200.0, 800.0]}).write_parquet(path)
    node = make_source_node("quotes", str(path))
    node.data.config["format"] = "parquet"
    node.data.config["mode"] = "scan"
    build_test_input_snapshot(node.data.config)
    return node


def _graph(tmp_path: Path, steps: list[dict[str, Any]], **config: Any) -> PipelineGraph:
    transform = GraphNode(
        id="t", data=NodeData(label="t", nodeType="polars", config={"steps": steps, **config})
    )
    return PipelineGraph(nodes=[_quotes(tmp_path), transform], edges=[make_edge("quotes", "t")])


def _written(tmp_path: Path, graph: PipelineGraph) -> str:
    """*graph* saved: its module and every node's sidecar written under *tmp_path*."""
    for rel, content in collect_node_configs(graph).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    code = graph_to_code(graph, pipeline_name="main")
    (tmp_path / "main.py").write_bytes(code.encode("utf-8"))
    return code


def _loaded(tmp_path: Path, code: str | None = None) -> GraphNode:
    source = code if code is not None else (tmp_path / "main.py").read_text(encoding="utf-8")
    graph = parse_pipeline_source(source, _base_dir=tmp_path)
    return next(node for node in graph.nodes if node.id == "t")


def _kinds(node: GraphNode) -> list[str]:
    return [step["kind"] for step in node.data.config["steps"]]


def test_a_transform_is_saved_as_code_alone_and_loads_back_byte_for_byte(tmp_path: Path) -> None:
    code = _written(tmp_path, _graph(tmp_path, STEPS))

    assert "@pipeline.polars\ndef t(quotes: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "polars(config" not in code
    assert not (tmp_path / "config" / "polars").exists()
    node = _loaded(tmp_path)
    assert _kinds(node) == ["source", "filter", "with_column", "limit"]
    assert [step["id"] for step in node.data.config["steps"]] == [
        "source-1",
        "filter-2",
        "with_column-3",
        "limit-4",
    ]
    again = graph_to_code(parse_pipeline_source(code, _base_dir=tmp_path), pipeline_name="main")
    assert again == code


def test_a_hand_edit_to_one_statement_keeps_the_other_steps(tmp_path: Path) -> None:
    code = _written(tmp_path, _graph(tmp_path, STEPS))
    edited = code.replace("df = df.head(2)", "df = df.head(2)  # two rows will do")
    assert edited != code

    node = _loaded(tmp_path, edited)

    assert _kinds(node) == ["source", "filter", "with_column", "free_code"]
    assert node.data.config["steps"][-1]["code"] == "df = df.head(2)  # two rows will do"


def test_two_statements_it_does_not_recognise_load_as_one_code_step(tmp_path: Path) -> None:
    code = _written(tmp_path, _graph(tmp_path, STEPS))
    edited = code.replace(
        "    df = df.head(2)\n",
        "    df = df.head(2)\n    df = df.tail(1)\n    df = df.reverse()\n",
    )

    node = _loaded(tmp_path, edited)

    assert _kinds(node) == ["source", "filter", "with_column", "limit", "free_code"]
    assert node.data.config["steps"][-1]["code"] == "df = df.tail(1)\ndf = df.reverse()"


def test_the_column_settings_are_decorator_keywords(tmp_path: Path) -> None:
    graph = _graph(tmp_path, STEPS, selected_columns=["premium", "gross"])
    code = _written(tmp_path, graph)

    assert "@pipeline.polars(selected_columns=[" in code
    node = _loaded(tmp_path)
    assert node.data.config["selected_columns"] == ["premium", "gross"]
    assert _kinds(node) == ["source", "filter", "with_column", "limit"]


def test_a_config_keyword_on_a_transform_is_refused_naming_the_conversion(
    tmp_path: Path,
) -> None:
    code = _written(tmp_path, _graph(tmp_path, STEPS)).replace(
        "@pipeline.polars\n", '@pipeline.polars(config="config/polars/t.json")\n'
    )
    with pytest.raises(ConfigError, match="read_22_polars_code.py"):
        _loaded(tmp_path, code)


UNFINISHED = [
    STEPS[0],
    STEPS[1],
    {"id": "u", "kind": "filter", "match": "all", "conditions": []},
    STEPS[2],
]


def test_an_unfinished_middle_step_waits_in_the_sidecar_and_the_node_fails_to_run(
    tmp_path: Path,
) -> None:
    graph = _graph(tmp_path, UNFINISHED)
    transform = next(node for node in graph.nodes if node.id == "t")
    assert transform.data.editor.steps_error is not None
    code = _written(tmp_path, graph)
    pipeline = tmp_path / "main.py"
    save_sidecar(pipeline, graph)

    assert "raise NotImplementedError" in code
    assert "df.filter" not in code
    sidecar = json.loads(pipeline.with_suffix(".haute.json").read_text(encoding="utf-8"))
    assert sidecar["unfinished_steps"] == {"t": UNFINISHED}

    # The editor's document, which the canvas opens, gives the node its steps back, the
    # complete ones around the unfinished; so does the graph a run loads.
    document = load_pipeline_editor_document(pipeline, project_root=tmp_path)
    opened = next(node for node in document.nodes if node.recovery_id == "t")
    assert opened.config is not None
    assert opened.config["steps"] == UNFINISHED
    assert opened.editor.steps_error is not None
    loaded = next(node for node in parse_pipeline_to_graph(pipeline).nodes if node.id == "t")
    assert loaded.data.config["steps"] == UNFINISHED
    # Anything that runs the module runs the placeholder: `haute run`, a standalone run.
    (tmp_path / "haute.toml").write_bytes(b'[project]\nname = "t"\npipeline = "main.py"\n')
    (tmp_path / ".git").mkdir()
    namespace = runpy.run_path(str(pipeline), run_name="pipeline_under_test")
    with pytest.raises(NotImplementedError, match="incomplete"):
        namespace["pipeline"].run(source="live")
    # The editor's preview, on the graph the editor loads, fails at the unfinished step.
    result = execute_graph(parse_pipeline_to_graph(pipeline), target_node_id="t")["t"]
    assert result.status == "error"
    assert "Step 3" in str(result.error)

    # Completed, the steps move into the code and leave the sidecar.
    finished = _graph(tmp_path, [*UNFINISHED[:2], *UNFINISHED[3:]])
    code = _written(tmp_path, finished)
    save_sidecar(pipeline, finished)
    assert "unfinished_steps" not in json.loads(
        pipeline.with_suffix(".haute.json").read_text(encoding="utf-8")
    )
    assert _kinds(_loaded(tmp_path)) == ["source", "filter", "with_column"]


def test_the_rendered_code_is_what_the_steps_render(tmp_path: Path) -> None:
    node = _loaded(tmp_path, _written(tmp_path, _graph(tmp_path, STEPS)))
    assert node.data.config["code"] == render_polars_steps(STEPS, ["quotes"], start="input").code


def test_the_conversion_refuses_a_submodel_node_on_a_config_file_changing_nothing(
    tmp_path: Path,
) -> None:
    """``scripts/one_off/read_22_polars_code.py`` converts pipeline files only; a submodel's
    Polars node on a ``config=`` file stops it before any file changes, not skipped."""
    import importlib.util

    script = Path(__file__).parents[1] / "scripts" / "one_off" / "read_22_polars_code.py"
    spec = importlib.util.spec_from_file_location("read_22_polars_code", script)
    assert spec is not None and spec.loader is not None
    conversion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conversion)
    pipeline = tmp_path / "main.py"
    pipeline.write_bytes(b'@pipeline.polars(config="config/polars/t.json")\ndef t(): ...\n')
    sidecar = tmp_path / "config" / "polars" / "t.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b'{"steps": []}\n')
    module = tmp_path / "modules" / "rating.py"
    module.parent.mkdir()
    module.write_bytes(b'@submodel.polars(config="config/polars/r.json")\ndef r(): ...\n')

    with pytest.raises(SystemExit, match="rating.py"):
        conversion.convert([tmp_path])

    assert pipeline.read_bytes().startswith(b'@pipeline.polars(config="config/polars/t.json")')
    assert sidecar.is_file()
