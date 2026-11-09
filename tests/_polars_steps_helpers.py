"""Shared builders, payloads and fixtures for the low-code Polars step suites.

The step and expression builders, golden step payloads and project fixtures here are used by
several ``test_polars_steps_*`` modules, so each module imports them from this one place.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._config_io import collect_node_configs
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._polars_steps import (
    RenderedSteps,
    render_polars_steps,
)
from haute._polars_steps_lift import canonical_steps
from haute._types import (
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute.codegen import graph_to_code
from haute.routes._helpers import parse_pipeline_to_graph, save_sidecar
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests._project import use_project
from tests.conftest import (
    build_test_input_snapshot,
    current_source_revision,
    make_edge,
    make_source_node,
)

# ---------------------------------------------------------------------------
# Step builders
# ---------------------------------------------------------------------------


def num(value: float) -> dict[str, Any]:
    return {"kind": "literal", "type": "number", "value": value}


def text(value: str) -> dict[str, Any]:
    return {"kind": "literal", "type": "text", "value": value}


def col(name: str) -> dict[str, Any]:
    return {"kind": "column", "name": name}


def var(name: str) -> dict[str, Any]:
    return {"kind": "variable", "name": name}


def cond(column: str, operator: str, **rest: Any) -> dict[str, Any]:
    return {"column": column, "operator": operator, **rest}


def source(name: str = "quotes", step_id: str = "s") -> dict[str, Any]:
    return {"id": step_id, "kind": "source", "input": name}


def step(step_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"id": step_id, "kind": kind, **fields}


GOLDEN_STEPS: list[dict[str, Any]] = [
    source(),
    step("v", "variable", name="ipt_rate", value=num(0.12)),
    step(
        "f",
        "filter",
        match="all",
        conditions=[
            cond("premium", "gt", value=num(100)),
            cond("region", "is_in", values=[text("north"), text("south")]),
        ],
    ),
    step(
        "w",
        "with_column",
        name="gross",
        expr={"type": "binary", "left": col("premium"), "op": "*", "right": var("ipt_rate")},
    ),
    step(
        "c",
        "with_column",
        name="band",
        expr={
            "type": "conditional",
            "branches": [
                {
                    "match": "any",
                    "conditions": [cond("gross", "ge", value=num(500))],
                    "then": text("high"),
                }
            ],
            "otherwise": text("low"),
        },
    ),
    step(
        "j",
        "join",
        input="rates",
        how="left",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_rate",
    ),
    step(
        "g",
        "group_by",
        keys=["band"],
        aggregations=[
            {"column": "gross", "agg": "sum", "name": "gross_total"},
            {"column": "gross", "agg": "len", "name": "rows"},
        ],
    ),
]


GOLDEN_CODE = "\n".join(
    [
        "df = quotes",
        "ipt_rate = 0.12",
        "df = df.filter(",
        '    (pl.col("premium") > 100) & pl.col("region").is_in(["north", "south"])',
        ")",
        'df = df.with_columns((pl.col("premium") * ipt_rate).alias("gross"))',
        "df = df.with_columns(",
        '    pl.when(pl.col("gross") >= 500)',
        '    .then(pl.lit("high"))',
        '    .otherwise(pl.lit("low"))',
        '    .alias("band")',
        ")",
        "df = df.join(",
        "    rates,",
        '    left_on=["region"],',
        '    right_on=["region"],',
        '    how="left",',
        '    suffix="_rate",',
        '    maintain_order="left_right",',
        ")",
        'df = df.group_by("band", maintain_order=True).agg(',
        '    pl.col("gross").sum().alias("gross_total"),',
        '    pl.len().alias("rows"),',
        ")",
    ]
)


#: Each golden step's inclusive line range: a statement that would pass 88
#: columns in the node body (84 at column 0) is laid out over several lines,
#: as ``ruff format`` lays it out there.
GOLDEN_STEP_LINES = ((1, 1), (2, 2), (3, 5), (6, 6), (7, 12), (13, 20), (21, 24))


def step_text(rendered: RenderedSteps, index: int) -> str:
    """The code step *index* rendered, read through its line range."""
    start, end = rendered.step_lines[index]
    return "\n".join(rendered.code.split("\n")[start - 1 : end])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


#: ``ruff format`` as the renderer's style targets it: its default line length
#: and quote style, with no project configuration.
RUFF_FORMAT = [sys.executable, "-m", "ruff", "format", "--isolated", "--line-length", "88", "-"]


# ---------------------------------------------------------------------------
# Vocabulary extensions: ordered windows, dates, strings, joins, summaries
# ---------------------------------------------------------------------------


def null() -> dict[str, Any]:
    return {"kind": "literal", "type": "null"}


def window(
    name: str,
    agg: str,
    of: str | dict[str, Any] | None,
    over: list[str],
    order_by: list[tuple[str, bool]] | None = None,
    descending: bool | None = None,
    where: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A window step; *of* names a column, or is an operand, or None for a count."""
    expr: dict[str, Any] = {"type": "window", "agg": agg, "over": over}
    if of is not None:
        expr["of"] = col(of) if isinstance(of, str) else of
    if order_by is not None:
        expr["order_by"] = [{"column": c, "descending": d} for c, d in order_by]
    if descending is not None:
        expr["descending"] = descending
    if where is not None:
        expr["where"] = {"match": "all", "conditions": where}
    return step(f"w_{name}", "with_column", name=name, expr=expr)


def _extended_frame(tmp_path: Path) -> GraphNode:
    """Unsorted partitions, rank ties, interior nulls and an id without a digit."""
    path = tmp_path / "policies.parquet"
    pl.DataFrame(
        {
            "qid": ["c3", "c1", "c2", "c4", "c5", "c6", "cx"],
            "region": ["north", "north", "south", "east", "north", "north", "south"],
            "premium": [50.0, 800.0, 200.0, None, None, 800.0, 100.0],
        }
    ).write_parquet(path)
    return _ready_source("policies", path)


# ---------------------------------------------------------------------------
# Nested expressions
# ---------------------------------------------------------------------------


def binary(left: dict[str, Any], op: str, right: dict[str, Any]) -> dict[str, Any]:
    return {"type": "binary", "left": left, "op": op, "right": right}


def fn(name: str, operand: dict[str, Any], *args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "fn": name, "operand": operand, "args": list(args)}


# ---------------------------------------------------------------------------
# Window row filters and values, operand arguments, divide, branches, sum across
# ---------------------------------------------------------------------------


def branches(*arms: dict[str, Any], otherwise: dict[str, Any]) -> dict[str, Any]:
    return {"type": "conditional", "branches": list(arms), "otherwise": otherwise}


# ---------------------------------------------------------------------------
# Materialisation invariant
# ---------------------------------------------------------------------------


def _stepped(node_id: str, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType="polars", config={"steps": steps, **extra}),
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _capped_context() -> ExecutionContext:
    """An admitted preview context with a memory cap, so a join after a filter runs."""
    return create_admitted_execution_context(
        operation="polars-steps-test",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )


def _ready_source(node_id: str, path: Path) -> GraphNode:
    node = make_source_node(node_id, str(path))
    node.data.config["format"] = "parquet"
    node.data.config["mode"] = "scan"
    build_test_input_snapshot(node.data.config)
    return node


def _frames(tmp_path: Path) -> tuple[GraphNode, GraphNode]:
    quotes = tmp_path / "quotes.parquet"
    rates = tmp_path / "rates.parquet"
    pl.DataFrame(
        {
            "premium": [50.0, 200.0, 800.0, None],
            "region": ["north", "south", "north", "east"],
            "north": ["c1", "c2", "c3", "c4"],
        }
    ).write_parquet(quotes)
    pl.DataFrame({"region": ["north", "south"], "rate": [1.1, 1.2]}).write_parquet(rates)
    return _ready_source("quotes", quotes), _ready_source("rates", rates)


# ---------------------------------------------------------------------------
# Codegen, parse, sidecars
# ---------------------------------------------------------------------------


def _write_sidecars(root: Path, graph: PipelineGraph) -> None:
    for rel, content in collect_node_configs(graph).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _lifted(
    steps: list[dict[str, Any]], node_type: NodeType | str = NodeType.POLARS
) -> list[dict[str, Any]]:
    """The steps a stepped node's code loads back as (READ-22, READ-44): their canonical
    form."""
    return canonical_steps(NodeType(node_type), steps)


# ---------------------------------------------------------------------------
# Save service
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from haute._git import set_working_branch
    from tests._git_helpers import init_repo

    init_repo(tmp_path)
    (tmp_path / "haute.toml").write_text("", encoding="utf-8", newline="\n")
    set_working_branch("pricing/test-user/steps", tmp_path, create=True, cwd=tmp_path)
    use_project(tmp_path)
    return tmp_path


def _save(root: Path, graph: PipelineGraph) -> list[str]:
    req = SavePipelineRequest(
        name="main",
        graph=graph,
        source_file="main.py",
        base_revision=current_source_revision(root / "main.py", root),
    )
    return list(SavePipelineService(root).save(req).warnings)


def _project_graph(
    root: Path, steps: list[dict[str, Any]] | None, *, code: str | None = None
) -> PipelineGraph:
    quotes, rates = _frames(root)
    config: dict[str, Any] = {"steps": steps} if steps is not None else {"code": code or ""}
    node = GraphNode(id="t", data=NodeData(label="t", nodeType="polars", config=config))
    return PipelineGraph(
        nodes=[quotes, rates, node], edges=[make_edge("quotes", "t"), make_edge("rates", "t")]
    )


# ---------------------------------------------------------------------------
# Render endpoint
# ---------------------------------------------------------------------------


def _render_body(
    steps: list[dict[str, Any]], input_names: list[str], *, start: str = "input"
) -> dict[str, Any]:
    return {"steps": steps, "input_names": input_names, "start": start}


# ---------------------------------------------------------------------------
# Frame-start surfaces: Data Input
# ---------------------------------------------------------------------------


def _stepped_input(node: GraphNode, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """A ready Data Input whose post-load code is authored as *steps*."""
    return node.with_config({**node.data.config, "steps": steps, **extra})


# ---------------------------------------------------------------------------
# Frame-start surfaces: External File, Rating Step, Model Score, Scenario Expander
# ---------------------------------------------------------------------------


def _external_file(tmp_path: Path, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """An External File over a pickled dict, with post-load steps."""
    obj_path = tmp_path / "factors.pkl"
    with obj_path.open("wb") as handle:
        pickle.dump({"factor": 2.0}, handle)
    config = {"path": str(obj_path), "file_type": "pickle", "steps": steps, **extra}
    return GraphNode(id="ext", data=NodeData(label="ext", nodeType="externalFile", config=config))


def _rating_step(steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    config = {
        "tables": [
            {
                "factors": ["region"],
                "output_column": "rate_factor",
                "default_value": "1.0",
                "entries": [{"region": "north", "value": "1.25"}],
            }
        ],
        "combined_outputs": [],
        "steps": steps,
        **extra,
    }
    return GraphNode(id="rated", data=NodeData(label="rated", nodeType="ratingStep", config=config))


def _scenario_expander(steps: list[dict[str, Any]]) -> GraphNode:
    config = {
        "column_name": "scenario_value",
        "min_value": 0.0,
        "max_value": 1.0,
        "step_count": 3,
        "step_column": "scenario_index",
        "steps": steps,
    }
    return GraphNode(
        id="grid", data=NodeData(label="grid", nodeType="scenarioExpander", config=config)
    )


def _reparsed(tmp_path: Path, graph: PipelineGraph, node_id: str) -> tuple[str, GraphNode]:
    """*graph* saved under *tmp_path*, with its ``.haute.json``, and loaded back as a run
    loads it: an unfinished step list comes back from ``.haute.json`` (READ-44)."""
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    (tmp_path / "main.py").write_text(code, encoding="utf-8")
    save_sidecar(tmp_path / "main.py", graph)
    parsed = parse_pipeline_to_graph(tmp_path / "main.py")
    return code, next(n for n in parsed.nodes if n.id == node_id)


def _surface_graph(
    tmp_path: Path, surface: str, steps: list[dict[str, Any]]
) -> tuple[PipelineGraph, str]:
    """A source plus one stepped frame surface, ready to generate and run."""
    quotes, _rates = _frames(tmp_path)
    if surface == "dataInput":
        return PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[]), "quotes"
    node = {
        "externalFile": lambda: _external_file(tmp_path, steps),
        "ratingStep": lambda: _rating_step(steps),
        "scenarioExpander": lambda: _scenario_expander(steps),
    }[surface]()
    return (
        PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", node.id)]),
        node.id,
    )


def _explore(steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """An Explore node whose analysis frame is authored as steps."""
    return GraphNode(
        id="report",
        data=NodeData(label="report", nodeType="explore", config={"steps": steps, **extra}),
    )


# ---------------------------------------------------------------------------
# Polars 2: text dates, typed membership, and what Polars 2 refuses (PL2-06)
# ---------------------------------------------------------------------------


def _run_steps(steps: list[dict[str, Any]], frame: pl.DataFrame) -> pl.DataFrame:
    """Render *steps* over an input named ``quotes`` and run the code on *frame*."""
    code = render_polars_steps(steps, ["quotes"], start="input").code
    scope: dict[str, Any] = {"pl": pl, "quotes": frame.lazy()}
    exec(code, scope)
    result = scope["df"]
    assert isinstance(result, pl.LazyFrame)
    return result.collect(engine="streaming")
