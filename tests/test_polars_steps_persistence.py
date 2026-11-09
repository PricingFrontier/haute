"""Low-code Polars steps: codegen, sidecars, saving and recovery.

Stepped transforms round-trip through generated code and their sidecars, the save service writes
them, submodel flattening keeps them, recovery keeps a hand-edited body over stale steps, and
frame-scope validation refuses a global ``df`` at its authored line.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._code_extraction import (
    INCOMPLETE_STEPS_MESSAGE,
    extract_user_code,
)
from haute._config_io import collect_node_configs, node_emits_sidecar
from haute._flatten import flatten_graph
from haute._graph_utils import _sanitize_func_name as _sanitize_label
from haute._polars_steps import (
    PolarsStepError,
    render_polars_steps,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ConfigError
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file, parse_pipeline_source
from haute.routes._helpers import parse_pipeline_to_graph, save_sidecar
from tests import _polars_steps_helpers
from tests._polars_steps_helpers import (
    GOLDEN_CODE,
    GOLDEN_STEPS,
    RUFF_FORMAT,
    _capped_context,
    _explore,
    _frames,
    _lifted,
    _project_graph,
    _reparsed,
    _save,
    _stepped,
    _stepped_input,
    _surface_graph,
    _write_sidecars,
    cond,
    num,
    source,
    step,
)
from tests.conftest import (
    make_edge,
)
from tests.test_codegen_execution_equivalence import _collect, _write_and_import

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
project_root = _polars_steps_helpers.project_root


# ---------------------------------------------------------------------------
# Codegen, parse, sidecars
# ---------------------------------------------------------------------------


def test_codegen_parse_round_trip_reproduces_rendered_code(tmp_path: Path) -> None:
    """A transform's steps live in its code alone: no ``config=``, no sidecar, and the
    load rebuilds them from the body with ids from their kind and place (READ-22)."""
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    assert "@pipeline.polars\ndef t(" in code
    assert "polars(config" not in code
    assert "\n    df = quotes\n" in code
    _write_sidecars(tmp_path, graph)
    assert not (tmp_path / "config" / "polars").exists()

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == _lifted(GOLDEN_STEPS)
    assert [s["id"] for s in node.data.config["steps"]] == [
        "source-1",
        "variable-2",
        "filter-3",
        "with_column-4",
        "with_column-5",
        "join-6",
        "group_by-7",
    ]
    assert node.data.config["code"] == GOLDEN_CODE
    assert node.data.editor.steps_discarded is None
    assert graph_to_code(parsed, pipeline_name="main") == code


def test_a_stepped_transform_keeps_its_column_settings_as_decorator_keywords(
    tmp_path: Path,
) -> None:
    """A stepped transform's column settings are decorator keywords, as for any transform."""
    quotes, rates = _frames(tmp_path)
    stepped = _stepped("t", GOLDEN_STEPS, selected_columns=["premium"], column_renames={"a": "b"})
    graph = PipelineGraph(
        nodes=[quotes, rates, stepped],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    decorator = next(line for line in code.splitlines() if line.startswith("@pipeline.polars"))
    assert "config=" not in decorator
    assert 'selected_columns=["premium"]' in decorator
    assert 'column_renames={"a": "b"}' in decorator
    _write_sidecars(tmp_path, graph)
    assert not (tmp_path / "config" / "polars").exists()

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["selected_columns"] == ["premium"]
    assert node.data.config["column_renames"] == {"a": "b"}
    assert node.data.config["steps"] == _lifted(GOLDEN_STEPS)


def test_free_code_executes_between_low_code_steps_and_round_trips(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    snippet = 'df = df.with_columns(\n    (pl.col("premium") * factor).alias("adjusted")\n)\n'
    steps = [
        source(),
        step("v", "variable", name="factor", value=num(2)),
        step("c", "free_code", code=snippet),
        step("sel", "select", columns=["adjusted"]),
        step("l", "limit", n=2),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    expected_code = render_polars_steps(steps, ["quotes"], start="input").code
    generated = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(generated, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == _lifted(steps)
    assert [s["kind"] for s in node.data.config["steps"]] == [s["kind"] for s in steps]
    assert node.data.config["code"] == expected_code
    assert node.data.editor.steps_discarded is None
    result = execute_graph(parsed, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [c.name for c in result.columns] == ["adjusted"]
    original = execute_graph(graph, target_node_id="quotes", execution_context=_capped_context())[
        "quotes"
    ]
    assert result.preview == [{"adjusted": row["premium"] * 2} for row in original.preview[:2]]


def test_free_code_round_trip_keeps_helper_returns_and_trailing_comments(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    snippet = "def unchanged(frame):\n    return frame\n\ndf = unchanged(df)\n# End\n\n"
    steps = [source(), step("c", "free_code", code=snippet)]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(graph_to_code(graph, pipeline_name="main"), _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == _lifted(steps)
    # The trailing comment stays at the end of the code step.
    assert node.data.config["steps"][-1]["code"].endswith("df = unchanged(df)\n# End")
    assert node.data.config["code"] == render_polars_steps(steps, start="input").code


@pytest.mark.parametrize(
    ("snippet", "line"),
    [("df = df.head(2)\nraise ValueError('free code failed')", 3), ("# Invalid frame\ndf = 1", 4)],
)
def test_free_code_runtime_errors_use_rendered_lines(
    tmp_path: Path, snippet: str, line: int
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [source(), step("c", "free_code", code=snippet), step("l", "limit", n=1)]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert result.error_line == line


def test_parse_keeps_the_steps_of_a_hand_edited_body(tmp_path: Path) -> None:
    """A hand edit keeps the other steps: an edited statement a step still renders is that
    step with its new value, and one no step renders is code (READ-22)."""
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("    ipt_rate = 0.12\n", "    ipt_rate = 0.2\n").replace(
        "    df = df.join(\n        rates,",
        "    df = df.unique()\n    df = df.join(\n        rates,",
    )
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    expected = _lifted(GOLDEN_STEPS)
    expected[1] = {**expected[1], "value": num(0.2)}
    expected.insert(5, {"id": "free_code-6", "kind": "free_code", "code": "df = df.unique()"})
    expected[6:] = [{**s, "id": f"{s['kind']}-{i + 7}"} for i, s in enumerate(expected[6:])]
    assert node.data.config["steps"] == expected
    assert node.data.config["code"] == GOLDEN_CODE.replace("0.12", "0.2").replace(
        "df = df.join(", "df = df.unique()\ndf = df.join("
    )
    assert node.data.editor.steps_discarded is None
    # PCFG-R08: the editor state travels beside the config, which holds no editor key.
    assert not any(key.startswith("_") for key in node.data.config)

    from haute._pipeline_recovery import load_pipeline_editor_document

    (tmp_path / "haute.toml").write_text('[project]\nname = "main"\n', encoding="utf-8")
    (tmp_path / "main.py").write_text(edited, encoding="utf-8")
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    shown = next(item for item in document.nodes if item.authored_id == "t")
    assert shown.editor == node.data.editor
    assert not any(key.startswith("_") for key in shown.config or {})


def test_editor_state_is_derived_and_kept_beside_the_config() -> None:
    """A stale steps error a request carries back is recomputed from the steps; the
    parse's findings survive a config replacement, as they did inside the config."""
    findings = {"steps_discarded": "Steps were discarded because x."}
    echoed = NodeData(
        nodeType="polars",
        config={"steps": [source()]},
        editor={"steps_error": "stale", **findings},
    )
    assert echoed.editor.steps_error is None
    assert echoed.editor.steps_discarded == findings["steps_discarded"]

    node = GraphNode(id="t", data=echoed)
    broken = node.with_config({"steps": [step("f", "filter", match="all", conditions=[])]})
    assert broken.data.editor.steps_error is not None
    assert broken.data.editor.steps_discarded == findings["steps_discarded"]
    assert broken.with_config({"code": "df = quotes"}).data.editor.steps_error is None


def test_a_statement_in_an_older_style_loads_as_code(tmp_path: Path) -> None:
    """A statement with other quotes or brackets than the renderer writes is code: a step
    is rebuilt only from exactly its rendering, so the code keeps its text (READ-22)."""
    quotes, _rates = _frames(tmp_path)
    steps = [
        source(),
        step("f", "filter", match="all", conditions=[cond("premium", "gt", value=num(100))]),
        step("l", "limit", n=2),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    styled = 'df = df.filter(pl.col("premium") > 100)'
    assert styled in code
    older_statement = "df = df.filter((pl.col('premium') > 100))"
    older = code.replace(styled, older_statement)

    parsed = parse_pipeline_source(older, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == [
        {"id": "source-1", "kind": "source", "input": "quotes"},
        {"id": "free_code-2", "kind": "free_code", "code": older_statement},
        {"id": "limit-3", "kind": "limit", "n": 2},
    ]
    assert node.data.editor.steps_discarded is None
    assert node.data.config["code"] == f"df = quotes\n{older_statement}\ndf = df.head(2)"


#: The golden steps as Haute 0.5 wrote them: lists for keys and aggregations,
#: which is a different program from the renderer's current calls.
EARLIER_GOLDEN_CODE = "\n".join(
    [
        "df = quotes",
        "ipt_rate = 0.12",
        "df = df.filter((pl.col('premium') > 100) & (pl.col('region').is_in(['north', 'south'])))",
        "df = df.with_columns((pl.col('premium') * ipt_rate).alias('gross'))",
        "df = df.with_columns((pl.when((pl.col('gross') >= 500)).then(pl.lit('high'))"
        ".otherwise(pl.lit('low'))).alias('band'))",
        "df = df.join(rates, left_on=['region'], right_on=['region'], how='left', suffix='_rate', "
        "maintain_order='left_right')",
        "df = df.group_by(['band'], maintain_order=True).agg([pl.col('gross').sum()"
        ".alias('gross_total'), pl.len().alias('rows')])",
    ]
)


def test_a_body_saved_by_an_earlier_renderer_loads_its_respelled_statements_as_code(
    tmp_path: Path,
) -> None:
    """The renderer's earlier spelling is no longer a step's: the statements it spells
    differently load as one code step, the ones it spells alike stay steps (READ-22)."""
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    current = textwrap.indent(GOLDEN_CODE, "    ")
    assert current in code
    earlier = code.replace(current, textwrap.indent(EARLIER_GOLDEN_CODE, "    "))

    parsed = parse_pipeline_source(earlier, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    respelled = "\n".join(EARLIER_GOLDEN_CODE.split("\n")[2:])
    assert node.data.config["steps"] == [
        *_lifted(GOLDEN_STEPS)[:2],
        {"id": "free_code-3", "kind": "free_code", "code": respelled},
    ]
    assert node.data.editor.steps_discarded is None
    assert node.data.config["code"] == EARLIER_GOLDEN_CODE


def test_parse_reads_a_sort_saved_before_sorts_kept_their_ties_order_as_free_code(
    tmp_path: Path,
) -> None:
    """Canonical only: a body whose sort predates the stable sort is not the steps' body."""
    quotes, rates = _frames(tmp_path)
    steps = [
        source(),
        step("o", "sort", keys=[{"column": "premium", "descending": True}], nulls_last=False),
    ]
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", steps)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    stable = 'df = df.sort("premium", descending=True, maintain_order=True)'
    assert stable in code
    unordered = code.replace(stable, 'df = df.sort("premium", descending=True)')

    parsed = parse_pipeline_source(unordered, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == [
        {"id": "source-1", "kind": "source", "input": "quotes"},
        {
            "id": "free_code-2",
            "kind": "free_code",
            "code": 'df = df.sort("premium", descending=True)',
        },
    ]
    assert node.data.editor.steps_discarded is None
    assert 'df = df.sort("premium", descending=True)' in node.data.config["code"]
    assert stable not in node.data.config["code"]


@pytest.mark.parametrize(
    ("steps", "edit"),
    [
        # A list beside another argument is not the same call as the names passed one by one.
        (
            [source(), step("x", "select", columns=["premium", "region"])],
            ('df = df.select("premium", "region")', "df = df.select(['premium'], 'region')"),
        ),
        # Authored code must match exactly, even where Polars would read it the same.
        (
            [source(), step("c", "free_code", code="df = df.select(['premium'])")],
            ("df = df.select(['premium'])", "df = df.select('premium')"),
        ),
    ],
)
def test_a_statement_no_step_renders_loads_as_code_beside_the_start(
    tmp_path: Path, steps: list[dict[str, Any]], edit: tuple[str, str]
) -> None:
    """An edited statement no step renders is code, as written; the start stays a step."""
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    old, new = edit
    assert old in code
    parsed = parse_pipeline_source(code.replace(old, new), _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == [
        {"id": "source-1", "kind": "source", "input": "quotes"},
        {"id": "free_code-2", "kind": "free_code", "code": new},
    ]
    assert node.data.editor.steps_discarded is None


def test_an_edited_comment_keeps_the_steps(tmp_path: Path) -> None:
    """A comment belongs to the step below it, so editing it keeps that step (READ-22)."""
    quotes, _rates = _frames(tmp_path)
    steps = [source(), step("c", "free_code", code="# keep two rows\ndf = df.head(2)")]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("# keep two rows", "# keep the first two rows")
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == [
        {"id": "source-1", "kind": "source", "input": "quotes"},
        {"id": "limit-2", "kind": "limit", "n": 2, "comment": "# keep the first two rows\n"},
    ]
    assert node.data.editor.steps_discarded is None


def test_free_code_trailing_whitespace_survives_ruff_format_with_its_steps(
    tmp_path: Path,
) -> None:
    """Rendering takes off the whitespace ``ruff format`` takes off, the spaces ending a
    line outside a string, so a reformatted file keeps its steps; a multiline string
    keeps its own trailing spaces and an indented block its structure."""
    quotes, _rates = _frames(tmp_path)
    snippet = (
        "# keep two rows   \n"
        'note = """first  \n'
        "   \n"
        'second"""   \n'
        "if True:   \n"
        "    df = df.head(2)  # inside   \n"
        "df = df.with_columns(note=pl.lit(note))\t\n"
    )
    steps = [source(), step("c", "free_code", code=snippet)]
    rendered = render_polars_steps(steps, ["quotes"], start="input").code
    assert rendered == (
        "df = quotes\n"
        "# keep two rows\n"
        'note = """first  \n'
        "   \n"
        'second"""\n'
        "if True:\n"
        "    df = df.head(2)  # inside\n"
        "df = df.with_columns(note=pl.lit(note))"
    )
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    generated = graph_to_code(graph, pipeline_name="main")
    reformatted = subprocess.run(
        RUFF_FORMAT, input=generated, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert reformatted == generated
    _write_sidecars(tmp_path, graph)

    # The file as saved, then reformatted (a file whose lines end in whitespace:
    # test_a_line_ending_in_whitespace_keeps_the_transform_s_steps).
    for body in (generated, reformatted):
        parsed = parse_pipeline_source(body, _base_dir=tmp_path)
        node = next(n for n in parsed.nodes if n.id == "t")
        assert node.data.config["steps"] == _lifted(steps)
        assert node.data.editor.steps_discarded is None
    result = execute_graph(parsed, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [row["note"] for row in result.preview] == ["first  \n   \nsecond"] * 2
    # The generated function holds the same string: no line of it was indented.
    (tmp_path / "haute.toml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    module = _write_and_import(graph, tmp_path)
    frame = _collect(module.pipeline.run(source="live"))
    assert frame["note"].to_list() == ["first  \n   \nsecond"] * 2


@pytest.mark.parametrize(
    ("line", "padded"),
    [
        ("    # keep two rows\n", "    # keep two rows   \n"),
        ("    ipt_rate = 0.12\n", "    ipt_rate = 0.12   \n"),
    ],
)
def test_a_line_ending_in_whitespace_keeps_the_transform_s_steps(
    tmp_path: Path, line: str, padded: str
) -> None:
    """A hand edit that leaves whitespace at the end of a comment or statement keeps the
    rest of the steps: only a body whose lines cannot be split into statements loads as
    code alone (specs/pipeline-config/high-level.md, Stepped transforms)."""
    quotes, rates = _frames(tmp_path)
    steps = [*GOLDEN_STEPS[:2], {**GOLDEN_STEPS[2], "comment": "# keep two rows\n"}]
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", [*steps, *GOLDEN_STEPS[3:]])],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    assert line in code

    parsed = parse_pipeline_source(code.replace(line, padded), _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    kinds = [s["kind"] for s in node.data.config.get("steps", [])]
    assert kinds[:1] == ["source"]
    assert {"filter", "join", "group_by"} <= set(kinds)


def test_a_hand_written_string_continuing_at_column_zero_extracts_as_written() -> None:
    """Dedenting a body leaves a multiline string's lines as they are, so a string that
    continues at column 0, as a hand-written file holds it, keeps its value."""
    body = '    note = """first\n  second\n"""\n    df = up.with_columns(note=pl.lit(note))\n'
    assert extract_user_code(body, kind="polars", param_names=("up",)) == (
        'note = """first\n  second\n"""\ndf = up.with_columns(note=pl.lit(note))'
    )


def test_floats_with_an_exponent_render_as_ruff_writes_them() -> None:
    steps = [
        source(),
        step("v", "variable", name="cap", value=num(1e20)),
        step("v2", "variable", name="floor", value=num(1.5e-7)),
        step(
            "f",
            "filter",
            match="all",
            conditions=[cond("premium", "lt", value=num(2.5e300))],
        ),
    ]
    code = render_polars_steps(steps, ["quotes"], start="input").code
    assert "cap = 1e20\n" in code
    assert "floor = 1.5e-07\n" in code
    assert "2.5e300" in code
    assert "e+" not in code
    module = "def node():\n" + "\n".join(f"    {line}" for line in code.splitlines()) + "\n"
    formatted = subprocess.run(
        RUFF_FORMAT, input=module, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert formatted == module


def test_a_leading_bare_string_step_is_a_fixed_point_with_its_value(tmp_path: Path) -> None:
    """A frame-mode body may open with a free-code string; it is never the docstring,
    so formatting and the parse leave its value and the steps as they were."""
    data = tmp_path / "quotes.parquet"
    pl.DataFrame({"premium": [1.0, 2.0]}).write_parquet(data)
    steps = [step("c", "free_code", code='"Prämie  "\ndf = df.head(1)')]
    node = GraphNode(
        id="quotes",
        data=NodeData(
            label="quotes",
            nodeType=NodeType.DATA_INPUT,
            config={
                "input_type": "file",
                "format": "parquet",
                "mode": "scan",
                "path": str(data),
                "arguments": {},
                "steps": steps,
            },
        ),
    )
    graph = PipelineGraph(nodes=[node], edges=[])
    generated = graph_to_code(graph, pipeline_name="main")
    reformatted = subprocess.run(
        RUFF_FORMAT, input=generated, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert reformatted == generated
    assert '    """"""\n    "Prämie  "\n' in generated
    _write_sidecars(tmp_path, graph)
    parsed = next(n for n in parse_pipeline_source(generated, _base_dir=tmp_path).nodes)
    assert parsed.data.config["steps"] == _lifted(steps, parsed.data.nodeType)
    assert parsed.data.config["code"] == '"Prämie  "\ndf = df.head(1)'


def test_parse_keeps_incomplete_steps_saved_through_the_placeholder(tmp_path: Path) -> None:
    """An unfinished list is saved as the raising placeholder, its steps in ``.haute.json``;
    the editor's load gives them back behind empty code (READ-22)."""
    quotes, _rates = _frames(tmp_path)
    incomplete = [source(), step("f", "filter", match="all", conditions=[])]
    graph = PipelineGraph(
        nodes=[quotes, _stepped("t", incomplete)], edges=[make_edge("quotes", "t")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    assert INCOMPLETE_STEPS_MESSAGE in code
    _write_sidecars(tmp_path, graph)
    pipeline = tmp_path / "main.py"
    pipeline.write_text(code, encoding="utf-8")
    save_sidecar(pipeline, graph)

    # The module alone holds no steps: the placeholder loads as a transform with no code.
    bare = next(n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "t")
    assert "steps" not in bare.data.config
    assert bare.data.config["code"] == ""

    node = next(n for n in parse_pipeline_to_graph(pipeline).nodes if n.id == "t")
    assert node.data.config["steps"] == incomplete
    assert node.data.config["code"] == ""
    assert node.data.editor.steps_error == "Step 2: Add at least one condition."


def test_parse_refuses_a_transform_that_names_a_steps_sidecar(tmp_path: Path) -> None:
    """``config=`` on a transform is the form before READ-22, however its file reads: the
    parse refuses it, saying where the steps go, before it reads the file."""
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, _stepped("t", [source()])], edges=[make_edge("quotes", "t")]
    )
    code = graph_to_code(graph, pipeline_name="main").replace(
        "@pipeline.polars\n", '@pipeline.polars(config="config/polars/t.json")\n'
    )
    _write_sidecars(tmp_path, graph)
    sidecar = tmp_path / "config" / "polars" / "t.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"steps": {"kind": "source"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="Write the steps in the function.s body"):
        parse_pipeline_source(code, _base_dir=tmp_path)


def test_collect_node_configs_writes_no_polars_file() -> None:
    """A transform has no sidecar, stepped or not: its steps live in its code (READ-22)."""
    stepped = _stepped("stepped", [source()])
    coded = GraphNode(
        id="coded", data=NodeData(label="coded", nodeType="polars", config={"code": "df = quotes"})
    )
    graph = PipelineGraph(nodes=[stepped, coded], edges=[])
    assert collect_node_configs(graph) == {}
    assert not node_emits_sidecar(stepped) and not node_emits_sidecar(coded)


# ---------------------------------------------------------------------------
# Save service
# ---------------------------------------------------------------------------


def test_save_writes_the_same_code_for_steps_and_for_code_and_no_polars_file(
    project_root: Path,
) -> None:
    """Steps and their code save to the same module, and neither leaves a file to retire."""
    main = project_root / "main.py"
    assert _save(project_root, _project_graph(project_root, GOLDEN_STEPS)) == []
    stepped = main.read_text(encoding="utf-8")
    assert "polars(config" not in stepped
    assert not (project_root / "config" / "polars").exists()

    _save(project_root, _project_graph(project_root, None, code=GOLDEN_CODE))
    assert main.read_text(encoding="utf-8") == stepped
    assert not (project_root / "config" / "polars").exists()


def test_hand_edit_then_load_then_save_keeps_the_edit_and_the_steps(project_root: Path) -> None:
    """A hand-edited file loads with its steps, the edit included, and saves back as it is."""
    _save(project_root, _project_graph(project_root, GOLDEN_STEPS))
    main = project_root / "main.py"
    edited = main.read_text(encoding="utf-8").replace("ipt_rate = 0.12", "ipt_rate = 0.5")
    main.write_bytes(edited.encode("utf-8"))

    loaded = parse_pipeline_file(main)
    node = next(n for n in loaded.nodes if n.id == "t")
    expected = _lifted(GOLDEN_STEPS)
    expected[1] = {**expected[1], "value": num(0.5)}
    assert node.data.config["steps"] == expected

    _save(project_root, loaded)
    assert main.read_text(encoding="utf-8") == edited
    assert not (project_root / "config" / "polars").exists()


def test_save_rejects_case_colliding_stepped_transforms(project_root: Path) -> None:
    quotes, _rates = _frames(project_root)
    graph = PipelineGraph(
        nodes=[quotes, _stepped("Foo", [source()]), _stepped("foo", [source()])],
        edges=[make_edge("quotes", "Foo"), make_edge("quotes", "foo")],
    )
    with pytest.raises(Exception, match="(?i)collid|conflict|same"):
        _save(project_root, graph)
    assert not (project_root / "config" / "polars").exists()


def test_save_warns_about_incomplete_step_list(project_root: Path) -> None:
    incomplete = [source(), step("f", "filter", match="all", conditions=[])]
    warnings = _save(project_root, _project_graph(project_root, incomplete))
    assert any("incomplete step list (Step 2: Add at least one condition.)" in w for w in warnings)
    unknown = _save(project_root, _project_graph(project_root, [source("policies")]))
    assert any("Unknown input 'policies'" in w for w in unknown)


def test_node_scoped_save_updates_the_transform_s_code(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute.schemas import PipelineNodeSaveRequest

    # Scoped saves exist for degraded documents: a broken input upstream makes
    # the stepped transform "blocked" and scoped-editable.
    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (tmp_path / "a.json").write_text(
        json.dumps(
            {
                "input_type": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "",
                "arguments": {},
                "code": "",
                "cacheMode": "snapshot",
            }
        ),
        encoding="utf-8",
    )
    steps = [source("source_a"), step("l", "limit", n=5)]
    main = tmp_path / "main.py"
    main.write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n'
        "@pipeline.polars\n"
        "def transform(source_a: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    df: pl.LazyFrame\n    df = source_a\n    df = df.head(5)\n    return df\n",
        encoding="utf-8",
    )
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "transform")
    assert target.availability == "blocked"
    assert target.scoped_editable is True
    assert target.config["steps"] == _lifted(steps)

    new_steps = [source("source_a"), step("l", "limit", n=3)]
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config={**target.config, "steps": new_steps},
    )
    apply_scoped_node_save(project_root=tmp_path, request=request)

    saved = main.read_text(encoding="utf-8")
    assert "df = df.head(3)" in saved
    assert "polars(config" not in saved
    assert not (tmp_path / "config" / "polars").exists()
    reloaded = load_pipeline_editor_document(main, project_root=tmp_path)
    node = next(item for item in reloaded.nodes if item.authored_id == "transform")
    assert node.config["steps"] == _lifted(new_steps)
    assert node.editor.steps_discarded is None


# ---------------------------------------------------------------------------
# Submodel flattening
# ---------------------------------------------------------------------------


def test_flatten_rewrites_stepped_consumer_inputs() -> None:
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
    consumer = GraphNode(
        id="consumer",
        data=NodeData(label="Consumer", nodeType="polars", config={"steps": [source("results")]}),
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

    result = flatten_graph(graph)
    flat = result.node_map["consumer"].data.config
    assert flat["steps"] == [source("Internal_Output")]
    assert "input_mapping" not in flat
    assert flat["code"] == "df = Internal_Output"
    generated = graph_to_code_multi(result, pipeline_name="main")["main.py"]
    assert "def Consumer(Internal_Output: pl.LazyFrame)" in generated
    assert "df = Internal_Output" in generated


def test_flatten_rewrites_internal_stepped_consumer_and_executes(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    inner = GraphNode(
        id="inner",
        data=NodeData(
            label="Inner",
            nodeType="polars",
            config={"steps": [source("policy"), step("l", "limit", n=2)]},
        ),
    )
    child = PipelineGraph(nodes=[inner], edges=[])
    definition = SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child,
        input_ports=[
            SubmodelInputPort(
                name="policy", targets=[SubmodelEndpoint(node_id="inner", handle_id="frame")]
            )
        ],
        output_ports=[
            SubmodelOutputPort(
                name="result", source=SubmodelEndpoint(node_id="inner", handle_id="scored")
            )
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
    consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="Consumer",
            nodeType="polars",
            config={"steps": [source("result"), step("s2", "select", columns=["premium"])]},
        ),
    )
    graph = PipelineGraph(
        nodes=[quotes, instance, consumer],
        edges=[
            GraphEdge(id="in", source="quotes", target="instance_a", targetHandle="in__policy"),
            GraphEdge(id="out", source="instance_a", target="consumer", sourceHandle="out__result"),
        ],
        submodels={"definition_scoring": definition},
    )

    flat = flatten_graph(graph)
    inner_flat = next(n for n in flat.nodes if n.data.label == "Inner")
    consumer_flat = flat.node_map["consumer"]
    assert inner_flat.data.config["steps"][0]["input"] == "quotes"
    assert "input_mapping" not in inner_flat.data.config
    assert consumer_flat.data.config["steps"][0]["input"] == _sanitize_label(inner_flat.data.label)
    assert "input_mapping" not in consumer_flat.data.config

    results = execute_graph(flat, target_node_id="consumer")
    assert results["consumer"].status == "ok", results["consumer"].error
    assert [c.name for c in results["consumer"].columns] == ["premium"]
    assert [row["premium"] for row in results["consumer"].preview] == [50.0, 200.0]


# ---------------------------------------------------------------------------
# Recovery preserves a hand-edited body over stale steps
# ---------------------------------------------------------------------------


def _recovered_settings(root: Path, authored_id: str) -> tuple[dict[str, Any], list[Any]]:
    """The raw settings recovery would offer for one node, plus its change notes."""
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._recovery_sources import read_raw_node_settings

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    _node_type, raw, changes = read_raw_node_settings(root, document, target)
    return raw, changes


@pytest.mark.parametrize("surface", ("explore", "dataInput"))
def test_recovery_keeps_a_hand_edited_body_over_stale_steps(
    project_root: Path, surface: str
) -> None:
    """Recovery is the ``.py``-is-truth path too: an edited body is never regenerated."""
    quotes, _rates = _frames(project_root)
    if surface == "explore":
        node, node_id = _explore([step("l", "limit", n=2)]), "report"
    else:
        node, node_id = _stepped_input(quotes, [step("l", "limit", n=2)]), "quotes"
    nodes = [node] if surface == "dataInput" else [quotes, node]
    edges = [] if surface == "dataInput" else [make_edge("quotes", "report")]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(project_root, graph)
    # The author edited the generated body by hand; the steps are now stale.
    (project_root / "main.py").write_text(
        code.replace("df.head(2)", "df.head(3)"), encoding="utf-8"
    )

    raw, changes = _recovered_settings(project_root, node_id)
    assert raw["code"] == "df = df.head(3)", surface
    assert not any(key.startswith("_") for key in raw), surface
    # Its code is the only copy of its steps (READ-43, READ-44): the edit is the step it
    # renders.
    assert raw["steps"] == _lifted([step("l", "limit", n=3)], surface), surface
    assert not [change for change in changes if change.path == "/steps"], surface

    # The recovered candidate materialises to the edited body, not the steps.
    recovered = GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node.data.nodeType, config=raw),
    )
    assert recovered.data.config["code"] == "df = df.head(3)", surface


@pytest.mark.parametrize("surface", ("explore", "dataInput"))
def test_recovery_keeps_steps_when_the_body_still_matches(project_root: Path, surface: str) -> None:
    quotes, _rates = _frames(project_root)
    steps = [step("l", "limit", n=2)]
    if surface == "explore":
        node, node_id = _explore(steps), "report"
        graph = PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "report")])
    else:
        node, node_id = _stepped_input(quotes, steps), "quotes"
        graph = PipelineGraph(nodes=[node], edges=[])

    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(project_root, graph)
    (project_root / "main.py").write_text(code, encoding="utf-8")

    raw, _changes = _recovered_settings(project_root, node_id)
    assert raw["steps"] == _lifted(steps, surface), surface
    assert not any(key.startswith("_") for key in raw), surface


def test_recovery_declares_an_explore_whose_decorator_held_its_steps_over_an_uncalled_body(
    project_root: Path,
) -> None:
    """An Explore node in the form before READ-43, its steps on its decorator above a body
    the decorator never calls, is recovered as a declaration with no steps.

    Its code is now the only copy of its steps, so the decorator's are dropped; the body
    has no place in a hook, so it is dropped too, as any node's is:
    ``test_recover_never_moves_a_body_its_decorator_never_calls_into_a_hook``.
    """
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair import build_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    quotes, _rates = _frames(project_root)
    graph = PipelineGraph(
        nodes=[quotes, _explore([step("l", "limit", n=2)])], edges=[make_edge("quotes", "report")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    old_form = code.replace(
        '@pipeline.explore(config="config/explore/report.json")\n'
        "def report(df: pl.LazyFrame) -> pl.LazyFrame:\n",
        '@pipeline.explore(steps=[{"id": "l", "kind": "limit", "n": 2}])\ndef report(quotes):\n',
    )
    assert old_form != code
    # The old form had no Explore sidecar: only the source's is written.
    _write_sidecars(project_root, PipelineGraph(nodes=[quotes], edges=[]))
    (project_root / "main.py").write_text(old_form, encoding="utf-8", newline="\n")

    raw, changes = _recovered_settings(project_root, "report")
    assert raw["steps"] == []
    assert not raw.get("code")
    assert not any(key.startswith("_") for key in raw)
    reported = [(c.path, c.outcome) for c in changes if c.path in {"/code", "/steps"}]
    assert reported == [("/code", "removed"), ("/steps", "removed")]

    document = load_pipeline_editor_document(project_root / "main.py", project_root=project_root)
    target = next(item for item in document.nodes if item.authored_id == "report")
    plan = build_recover_unavailable_node_plan(
        project_root=project_root,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )
    source = next(edit for edit in plan.edits if edit.path.name == "main.py")
    assert source.after is not None
    regenerated = source.after.decode("utf-8")
    assert (
        '@pipeline.explore(config="config/explore/report.json")\ndef report(quotes): ...\n'
    ) in regenerated
    assert "steps=" not in regenerated
    assert "df.head(2)" not in regenerated
    # The settings it names are written beside it, with no copy of the steps.
    (sidecar,) = [edit for edit in plan.edits if edit.path.parent.name == "explore"]
    assert sidecar.path.name == "report.json"
    assert sidecar.after is not None
    assert "steps" not in json.loads(sidecar.after.decode("utf-8"))


def test_recovery_plans_a_malformed_step_container_without_raising(project_root: Path) -> None:
    """Recovery never raises on a bad field: the unusable list goes, the body stays, and
    the steps are rebuilt from it (READ-44).

    A `ConfigError` here would escape the recovery route, which handles only
    `PipelineRepairError` and `OSError`, and the user would get an HTTP 500
    instead of a plan.
    """
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair import build_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    quotes, _rates = _frames(project_root)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[])
    (project_root / "main.py").write_text(
        graph_to_code(graph, pipeline_name="main"), encoding="utf-8"
    )
    _write_sidecars(project_root, graph)
    sidecar = project_root / "config" / "data_input" / "quotes.json"
    persisted = json.loads(sidecar.read_text(encoding="utf-8"))
    # A step list that is not a list at all, beside a readable rest of config.
    sidecar.write_text(json.dumps({**persisted, "steps": {"kind": "limit"}}), encoding="utf-8")

    document = load_pipeline_editor_document(project_root / "main.py", project_root=project_root)
    target = next(node for node in document.nodes if node.authored_id == "quotes")

    # The raw read degrades instead of raising.
    raw, changes = _recovered_settings(project_root, "quotes")
    assert raw["steps"] == _lifted([step("l", "limit", n=2)], "dataInput")
    assert raw["code"] == "df = df.head(2)"
    assert any(change.path == "/steps" and change.outcome == "removed" for change in changes)

    # And the route's own plan builder returns a plan rather than an exception.
    plan = build_recover_unavailable_node_plan(
        project_root=project_root,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )
    assert plan.response.changes
    # The authored body must survive. The node regenerated from the recovered
    # config (code only) is exactly the hook already on disk, so the plan leaves
    # the source alone rather than rewriting it.
    assert not any(edit.path.name == "main.py" for edit in plan.edits)
    assert "    df = df.head(2)\n    return df\n" in (project_root / "main.py").read_text(
        encoding="utf-8"
    )
    sidecar_edit = next((edit for edit in plan.edits if edit.path.name == "quotes.json"), None)
    if sidecar_edit is not None and sidecar_edit.after is not None:
        assert "steps" not in json.loads(sidecar_edit.after.decode("utf-8"))


@pytest.mark.parametrize("authoring", ["code", "steps"])
@pytest.mark.parametrize("row_count", [1, 2])
def test_deploy_injected_data_input_applies_post_load_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authoring: str,
    row_count: int,
) -> None:
    from haute.deploy._scorer import score_graph

    parquet_file = tmp_path / "source.parquet"
    raw_frame = pl.DataFrame(
        {
            "x": [10, 20][:row_count],
            "unused": [100, 200][:row_count],
        }
    )
    raw_frame.write_parquet(parquet_file)
    authored_code = (
        'df = df.with_columns((pl.col("x") * scale).alias("x"))\n'
        'df = df.with_columns(pl.col("x").alias("doubled"))'
    )
    src_config: dict[str, Any] = {
        "input_type": "file",
        "format": "parquet",
        "mode": "scan",
        "path": str(parquet_file),
    }
    if authoring == "code":
        src_config["code"] = authored_code
    else:
        src_config["steps"] = [step("fc", "free_code", code=authored_code)]

    out_config: dict[str, Any] = {
        "output_mapping": [
            {
                "source_port": "src",
                "source_column": "doubled",
                "output_path": "$[:].doubled",
                "enabled": True,
            }
        ],
    }
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(label="src", nodeType=NodeType.DATA_INPUT, config=src_config),
            ),
            GraphNode(
                id="out",
                data=NodeData(label="out", nodeType=NodeType.OUTPUT, config=out_config),
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="src", target="out"),
        ],
        preamble="scale = 2",
    )

    canvas_result = execute_graph(graph)["src"]
    assert canvas_result.status == "ok", canvas_result.error
    assert [row["doubled"] for row in canvas_result.preview] == [20, 40][:row_count]

    def _fail_resolve(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "resolve_data_input must not be called during live-injected deployment"
        )

    monkeypatch.setattr("haute._input_providers.resolve_data_input", _fail_resolve)

    scored = score_graph(
        graph=graph,
        input_df=raw_frame,
        input_node_ids=["src"],
        output_node_id="out",
        output_fields=["doubled"],
    )
    assert isinstance(scored, pl.DataFrame)
    assert scored["doubled"].to_list() == [20, 40][:row_count]
    assert scored.columns == ["doubled"]


@pytest.mark.parametrize(
    "authoring_config",
    [{"code": ""}, {"steps": []}],
    ids=["code", "steps"],
)
def test_empty_model_score_steps_preserve_projection(
    authoring_config: dict[str, Any],
) -> None:
    import haute.projection as projection
    from haute._contracts import _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY

    src_node = GraphNode(
        id="src",
        data=NodeData(
            label="src",
            nodeType=NodeType.DATA_INPUT,
            config={
                "input_type": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "src.parquet",
            },
        ),
    )
    edge = GraphEdge(id="e1", source="src", target="model")

    model_data = NodeData(
        label="model",
        nodeType=NodeType.MODEL_SCORE,
        config={
            "source_type": "registered",
            "registered_model": "test_model",
            "output_column": "prediction",
            _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: ["x"],
            **authoring_config,
        },
    )
    plan = projection.compute_prepared_plan(
        ["src", "model"],
        {"src": ["model"], "model": []},
        {"src": src_node, "model": GraphNode(id="model", data=model_data)},
        {"model": ["prediction"]},
        relevant_edges=[edge],
    )

    assert plan.needed_by_node["src"] == frozenset({"x"})
    assert not plan.opaque_boundaries


@pytest.mark.parametrize("preceding_limit", [False, True])
def test_frame_free_code_rejects_global_df_with_authored_line(preceding_limit: bool) -> None:
    snippet = "# a comment\nglobal df\ndf = df.head(2)"
    fc_step = step("fc", "free_code", code=snippet)
    if preceding_limit:
        steps = [step("l", "limit", n=2), fc_step]
        expected_index = 1
    else:
        steps = [fc_step]
        expected_index = 0
    with pytest.raises(PolarsStepError) as exc_info:
        render_polars_steps(steps, None, start="frame")
    assert exc_info.value.step_index == expected_index
    msg = str(exc_info.value)
    assert "Invalid Python on line 2" in msg
    assert (
        "name 'df' is assigned to before global declaration" in msg
        or "name 'df' is used prior to global declaration" in msg
    )


def test_frame_scope_validation_preserves_rendered_line_ranges() -> None:
    code = 'a = 1\ndf = df.with_columns(pl.lit(a).alias("a"))'
    steps = [step("fc", "free_code", code=code), step("l", "limit", n=2)]
    rendered = render_polars_steps(steps, None, start="frame")
    assert rendered.code == f"{code}\ndf = df.head(2)"
    assert rendered.step_lines == ((1, 2), (3, 3))


def test_frame_global_df_saves_as_incomplete_placeholder(tmp_path: Path) -> None:
    offending_steps = [step("fc", "free_code", code="# a comment\nglobal df\ndf = df.head(2)")]
    graph, node_id = _surface_graph(tmp_path, "dataInput", offending_steps)
    validated_node = next(n for n in graph.nodes if n.id == node_id)
    assert validated_node.data.editor.steps_error is not None
    generated = graph_to_code(graph, pipeline_name="main")
    assert INCOMPLETE_STEPS_MESSAGE in generated
    compile(generated, "<generated>", "exec")
    _code, parsed_node = _reparsed(tmp_path, graph, node_id)
    assert parsed_node.data.config["steps"] == offending_steps


def test_a_step_variable_cannot_take_the_reserved_global_constants_name() -> None:
    with pytest.raises(PolarsStepError, match="'global_constants' is not a valid name"):
        render_polars_steps(
            [source(), step("v", "variable", name="global_constants", value=num(1))],
            ["quotes"],
            start="input",
        )
