"""Constants that only formulas read become global constants (READ-28).

Spec: specs/roadmap/readable-output.md, READ-28, carried out by the one-off conversion
``scripts/one_off/read_28_promote_constants.py``, which these tests load and which goes with
them once the owner has run it. A Constant node joined across every row of one stepped
transform's input, for that transform's formulas alone, is promoted; otherwise it stays and
the failed condition is reported.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import polars as pl
import pytest

from haute._types import GlobalConstant
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph
from tests.conftest import build_test_input_snapshot, make_source_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

SCRIPT = Path(__file__).parents[1] / "scripts" / "one_off" / "read_28_promote_constants.py"


@pytest.fixture(scope="module")
def conversion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("read_28_promote_constants", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def col(name: str) -> dict[str, Any]:
    return {"kind": "column", "name": name}


def gross(load: str = "load") -> dict[str, Any]:
    """``gross = premium * <load>``, a formula reading the constant."""
    return {
        "id": "w",
        "kind": "with_column",
        "name": "gross",
        "expr": {"type": "binary", "left": col("premium"), "op": "*", "right": col(load)},
    }


DROP = {"id": "d", "kind": "drop", "columns": ["load"]}


def node(node_id: str, node_type: str, **config: Any) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _graph(
    tmp_path: Path,
    steps: list[dict[str, Any]],
    *,
    value: Any = "1.1",
    join: dict[str, Any] | None = None,
    extra: list[GraphNode] = (),
    extra_edges: list[GraphEdge] = (),
    taken: list[GlobalConstant] = (),
) -> PipelineGraph:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"premium": [100.0, 250.0]}).write_parquet(path)
    quotes = make_source_node("quotes", str(path))
    quotes.data.config["format"] = "parquet"
    quotes.data.config["mode"] = "scan"
    build_test_input_snapshot(quotes.data.config)
    nodes = [
        quotes,
        node("rates", "constant", values=[{"name": "load", "value": value}]),
        node("joined", "edgeJoin", **(join or {"how": "cross", "maintainOrder": "left_right"})),
        node("priced", "polars", steps=[{"id": "s", "kind": "source", "input": "joined"}, *steps]),
        *extra,
    ]
    edges = [
        GraphEdge(id="e1", source="quotes", target="joined", targetHandle="base"),
        GraphEdge(id="e2", source="rates", target="joined", targetHandle="join"),
        GraphEdge(id="e3", source="joined", target="priced"),
        *extra_edges,
    ]
    return PipelineGraph(nodes=nodes, edges=edges, global_constants=list(taken))


def test_a_constant_only_formulas_read_becomes_a_global_constant(
    conversion: ModuleType, tmp_path: Path
) -> None:
    after, promoted, left = conversion.promote(_graph(tmp_path, [gross(), DROP]))

    assert (promoted, left) == (["rates"], [])
    assert [n.id for n in after.nodes] == ["quotes", "priced"]
    assert [(e.source, e.target) for e in after.edges] == [("quotes", "priced")]
    steps = next(n for n in after.nodes if n.id == "priced").data.config["steps"]
    assert steps[0]["input"] == "quotes"
    assert steps[1]["expr"]["right"] == {"kind": "constant", "name": "load"}
    assert [step["kind"] for step in steps] == ["source", "with_column"]
    assert after.global_constants == [GlobalConstant(name="load", type="float", value=1.1)]


def test_a_text_constant_is_promoted_as_text(conversion: ModuleType, tmp_path: Path) -> None:
    copied = {
        "id": "w",
        "kind": "with_column",
        "name": "region",
        "expr": {"type": "operand", "operand": col("load")},
    }
    after, promoted, _ = conversion.promote(_graph(tmp_path, [copied, DROP], value="north"))

    assert promoted == ["rates"]
    assert after.global_constants == [GlobalConstant(name="load", type="text", value="north")]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("name collision", "the global constant 'load' is taken"),
        ("reaches the output", "the constant column 'load' reaches priced's output"),
        ("two consumers", "it has 2 consumers, not one"),
        ("keyed left join", "its consumer joined is not a cross join"),
        ("keyed inner join", "its consumer joined is not a cross join"),
        ("inside an aggregation", "priced's step 2 reads a constant in an aggregation"),
        ("code after the join", "priced's step 3 is code, which Haute cannot read"),
        ("overwritten", "priced's step 2 writes the column 'load'"),
    ],
)
def test_a_constant_stays_naming_the_condition_that_failed(
    conversion: ModuleType, tmp_path: Path, case: str, reason: str
) -> None:
    keyed = {"on": ["premium"], "maintainOrder": "left_right"}
    window = {
        "id": "w",
        "kind": "with_column",
        "name": "total_load",
        "expr": {"type": "window", "agg": "sum", "of": col("load"), "over": ["premium"]},
    }
    graph = {
        "name collision": lambda: _graph(
            tmp_path, [gross(), DROP], taken=[GlobalConstant(name="load", type="float", value=2.0)]
        ),
        "reaches the output": lambda: _graph(tmp_path, [gross()]),
        "two consumers": lambda: _graph(
            tmp_path,
            [gross(), DROP],
            extra=[node("other", "polars", code="df = rates")],
            extra_edges=[GraphEdge(id="e4", source="rates", target="other")],
        ),
        "keyed left join": lambda: _graph(tmp_path, [gross(), DROP], join={"how": "left", **keyed}),
        "keyed inner join": lambda: _graph(
            tmp_path, [gross(), DROP], join={"how": "inner", **keyed}
        ),
        "inside an aggregation": lambda: _graph(tmp_path, [window, DROP]),
        "code after the join": lambda: _graph(
            tmp_path, [gross(), {"id": "c", "kind": "free_code", "code": "df = df"}, DROP]
        ),
        # Overwritten on every row here; a step that overwrites it on rows the sample lacks
        # is refused alike, since eligibility is read from the steps, never the sample.
        "overwritten": lambda: _graph(
            tmp_path,
            [
                {
                    "id": "o",
                    "kind": "with_column",
                    "name": "load",
                    "expr": {
                        "type": "binary",
                        "left": col("load"),
                        "op": "*",
                        "right": col("load"),
                    },
                },
                gross(),
                DROP,
            ],
        ),
    }[case]()

    after, promoted, left = conversion.promote(graph)

    assert promoted == []
    assert left == [f"rates stays: {reason}"]
    assert after is graph


def test_a_promotion_gives_every_node_the_same_output(
    conversion: ModuleType, tmp_path: Path
) -> None:
    before = _graph(tmp_path, [gross(), DROP])
    after, _, _ = conversion.promote(before)

    old, new = conversion._outputs(before), conversion._outputs(after)

    assert new["priced"] == old["priced"]
    assert new["priced"][0] == "ok"
