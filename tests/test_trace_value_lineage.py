"""The traced value's lineage: which steps compute or carry what the value depends on."""

from __future__ import annotations

import json
from types import SimpleNamespace

import polars as pl
import pytest

from haute._trace_lineage import JoinInputs, trace_value_lineage
from haute._types import GraphNode, NodeData, NodeType
from haute.trace import SchemaDiff, TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

SCENARIO_CODE = (
    'df = df.with_columns((pl.col("premium") * pl.col("scenario_value")).alias("premium"))\n'
    'df = df.with_columns((pl.col("premium") - pl.col("burn_cost")).alias("predicted_income"))\n'
    "df = df.with_columns(\n"
    '    (1.0 - pl.col("premium") / pl.col("competitor_premium") / 10.0)\n'
    '    .alias("predicted_volume")\n'
    ")"
)


def _node(node_id: str, node_type: NodeType, config: dict) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _left_join(node_id: str) -> GraphNode:
    return _node(node_id, NodeType.EDGE_JOIN, {"how": "left", "on": ["quote_id"]})


def _pricing_graph(tmp_path) -> object:
    """The demo's shape: joined-in data, a competitor price, a cost, scenarios, an online apply.

    ``insights`` is joined in but nothing reads its ``market_note``; ``prices``
    supplies ``premium`` from the join side of ``price_join``; ``policies`` holds
    no row for either quote, as for an unsold quote.
    """
    frames = {
        "quotes": pl.DataFrame({"quote_id": ["q1", "q2"], "age": [30.0, 50.0]}),
        "insights": pl.DataFrame({"quote_id": ["q1", "q2"], "market_note": ["a", "b"]}),
        "prices": pl.DataFrame({"quote_id": ["q1", "q2"], "premium": [100.0, 200.0]}),
        "policies": pl.DataFrame({"quote_id": ["other"], "sale_flag": [1.0]}),
    }
    for name, frame in frames.items():
        frame.write_parquet(tmp_path / f"{name}.parquet")
    artifact_path = tmp_path / "online.json"
    artifact_path.write_text(
        json.dumps(
            {
                "version": "online_v1",
                "mode": "online",
                "lambdas": {"predicted_volume": 0.5},
                "objective": "predicted_income",
                "constraints": {"predicted_volume": {"min": 0.9}},
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
            }
        ),
        encoding="utf-8",
    )
    return make_graph(
        {
            "nodes": [
                *(make_source_node(name, str(tmp_path / f"{name}.parquet")) for name in frames),
                _left_join("insights_join"),
                _left_join("price_join"),
                _left_join("sale_join"),
                make_transform_node(
                    "competitor",
                    "df = price_join.with_columns(\n"
                    '    (pl.col("age") * 10.0).alias("competitor_premium")\n'
                    ")",
                ),
                make_transform_node(
                    "cost",
                    'df = competitor.with_columns((pl.col("premium") * 0.5).alias("burn_cost"))',
                ),
                _node(
                    "scenarios",
                    NodeType.SCENARIO_EXPANDER,
                    {
                        "quote_id": "quote_id",
                        "column_name": "scenario_value",
                        "step_column": "scenario_index",
                        "min_value": 0.9,
                        "max_value": 1.1,
                        "stepCount": 3,
                        "code": SCENARIO_CODE,
                    },
                ),
                _node(
                    "apply",
                    NodeType.OPTIMISER_APPLY,
                    {
                        "sourceType": "file",
                        "artifact_path": str(artifact_path),
                        "optimised_value_column": "optimal_premium",
                    },
                ),
            ],
            "edges": [
                make_edge("quotes", "insights_join", target_handle="base"),
                make_edge("insights", "insights_join", target_handle="join"),
                make_edge("insights_join", "price_join", target_handle="base"),
                make_edge("prices", "price_join", target_handle="join"),
                make_edge("price_join", "competitor"),
                make_edge("competitor", "cost"),
                make_edge("cost", "sale_join", target_handle="base"),
                make_edge("policies", "sale_join", target_handle="join"),
                make_edge("sale_join", "scenarios"),
                make_edge("scenarios", "apply"),
            ],
        }
    )


def _steps_by_id(result: TraceResult) -> dict[str, TraceStep]:
    return {step.node_id: step for step in result.steps}


def test_an_optimised_value_is_relevant_through_every_step_it_was_computed_from(tmp_path):
    result = execute_trace(
        _pricing_graph(tmp_path),
        target_node_id="apply",
        column="optimal_premium",
        row_index=0,
    )

    steps = _steps_by_id(result)
    assert steps["apply"].node_detail is not None
    assert steps["apply"].node_detail["status"] == "ok", steps["apply"].node_detail
    contributed = {node_id: step.contributed_columns for node_id, step in steps.items()}
    assert contributed == {
        "quotes": ["age", "quote_id"],
        "insights": [],
        "prices": ["premium"],
        "insights_join": [],
        "price_join": [],
        # The competitor price feeds the volume constraint two steps later.
        "competitor": ["competitor_premium"],
        "cost": ["burn_cost"],
        "sale_join": [],
        "scenarios": [
            "predicted_income",
            "predicted_volume",
            "premium",
            "scenario_index",
            "scenario_value",
        ],
        "apply": ["optimal_premium"],
    }
    relevant = {node_id for node_id, step in steps.items() if step.column_relevant}
    # Joins carry the value's inputs; the joined-in insights feed nothing.
    assert relevant == set(steps) - {"insights"}
    # No policy joined this quote, and the value never reads a policy column.
    assert "policies" not in steps
    assert result.omissions == []


def _policy_graph(tmp_path) -> object:
    pl.DataFrame({"quote_id": ["q1"], "x": [2.0]}).write_parquet(tmp_path / "quotes.parquet")
    pl.DataFrame({"quote_id": ["other"], "sale_flag": [1.0]}).write_parquet(
        tmp_path / "policies.parquet"
    )
    return make_graph(
        {
            "nodes": [
                make_source_node("quotes", str(tmp_path / "quotes.parquet")),
                make_source_node("policies", str(tmp_path / "policies.parquet")),
                _left_join("sale_join"),
                make_transform_node(
                    "calc",
                    "df = sale_join.with_columns(\n"
                    '    (pl.col("x") * 2).alias("from_x"),\n'
                    '    (pl.col("sale_flag") + 1).alias("from_sale"),\n'
                    ")",
                ),
            ],
            "edges": [
                make_edge("quotes", "sale_join", target_handle="base"),
                make_edge("policies", "sale_join", target_handle="join"),
                make_edge("sale_join", "calc"),
            ],
        }
    )


@pytest.mark.parametrize(
    ("column", "omitted"),
    [("from_sale", ["policies"]), ("from_x", [])],
    ids=["reads-the-joined-side", "never-reads-it"],
)
def test_a_join_that_found_no_row_is_reported_only_when_the_value_reads_it(
    tmp_path, column, omitted
):
    result = execute_trace(_policy_graph(tmp_path), target_node_id="calc", column=column)

    assert [omission.node_id for omission in result.omissions] == omitted
    assert all(omission.reason == "join_no_match" for omission in result.omissions)


def _step(node_id: str, *, added=(), passed=(), detail: dict | None = None) -> TraceStep:
    return TraceStep(
        node_id=node_id,
        node_name=node_id,
        node_type="modelScore" if detail else "dataInput",
        schema_diff=SchemaDiff(
            columns_added=list(added),
            columns_removed=[],
            columns_modified=[],
            columns_passed=list(passed),
        ),
        input_values={},
        output_values={},
        node_detail=detail,
    )


def _graph_node(node_type: NodeType) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(nodeType=node_type, config={}, label=""))


def _model_chain_lineage(competitor_detail: dict):
    """quotes -> competitor model -> conversion model, as in the demo's scoring."""
    features = ["age", "competitor_premium"]
    steps = [
        _step("quotes", added=["age", "region", "unrelated"]),
        _step("rivals", added=["rival_note"]),
        _step(
            "competitor",
            added=["competitor_premium"],
            passed=["age", "region", "unrelated", "rival_note"],
            detail=competitor_detail,
        ),
        _step(
            "conversion",
            added=["conversion"],
            passed=["age", "region", "unrelated", "rival_note", "competitor_premium"],
            detail={
                "detail_type": "model_score",
                "prediction_column": "conversion",
                "feature_columns": features,
            },
        ),
    ]
    return trace_value_lineage(
        column="conversion",
        target_node_id="conversion",
        steps=steps,
        order=["quotes", "rivals", "competitor", "conversion"],
        parents_of={"competitor": ["quotes", "rivals"], "conversion": ["competitor"]},
        node_map={
            "quotes": _graph_node(NodeType.DATA_INPUT),
            "rivals": _graph_node(NodeType.DATA_INPUT),
            "competitor": _graph_node(NodeType.MODEL_SCORE),
            "conversion": _graph_node(NodeType.MODEL_SCORE),
        },
        attempted=(),
        output_columns=lambda node_id, _handle: {
            "quotes": {"age", "region", "unrelated"},
            "rivals": {"rival_note"},
            "competitor": {"age", "region", "unrelated", "rival_note", "competitor_premium"},
        }.get(node_id),
        source_handles={},
        join_inputs={},
    )


def test_a_model_feature_leads_to_the_model_that_predicted_it():
    lineage = _model_chain_lineage(
        {
            "detail_type": "model_score",
            "prediction_column": "competitor_premium",
            "feature_columns": ["region"],
        }
    )

    assert dict(lineage.contributed) == {
        "conversion": ("conversion",),
        "competitor": ("competitor_premium",),
        "quotes": ("age", "region"),
    }
    assert lineage.reached == {"conversion", "competitor", "quotes"}


def test_a_model_whose_explanation_failed_depends_on_every_input():
    lineage = _model_chain_lineage(
        {"detail_type": "model_score", "error": "model score enrichment failed: boom"}
    )

    assert lineage.contributed["quotes"] == ("age", "region", "unrelated")
    assert "rivals" in lineage.reached


RATING_DETAIL = {
    "detail_type": "rating_step",
    "tables": [
        {"output_column": "age_factor", "factors": [{"column": "age"}]},
        {"output_column": "area_factor", "factors": [{"column": "area"}]},
    ],
    "combined_outputs": [
        {"column": "rate", "input_values": {"age_factor": 1.1, "area_factor": 0.9}}
    ],
}
BANDING_DETAIL = {
    "detail_type": "banding",
    "factors": [{"input_column": "age", "output_column": "age_band"}],
}


@pytest.mark.parametrize(
    ("node_type", "detail", "column", "computed", "read"),
    [
        (NodeType.RATING_STEP, RATING_DETAIL, "age_factor", ("age_factor",), ("age",)),
        (
            NodeType.RATING_STEP,
            RATING_DETAIL,
            "rate",
            ("age_factor", "area_factor", "rate"),
            ("age", "area"),
        ),
        (NodeType.BANDING, BANDING_DETAIL, "age_band", ("age_band",), ("age",)),
    ],
    ids=["rating-table", "rating-combined-output", "banding-factor"],
)
def test_a_rating_or_banding_output_reads_its_factor_columns(
    node_type, detail, column, computed, read
):
    produced = ["age_factor", "area_factor", "rate", "age_band"]
    steps = [
        _step("quotes", added=["age", "area", "other"]),
        _step("rate", added=produced, passed=["age", "area", "other"], detail=detail),
    ]
    lineage = trace_value_lineage(
        column=column,
        target_node_id="rate",
        steps=steps,
        order=["quotes", "rate"],
        parents_of={"rate": ["quotes"]},
        node_map={"quotes": _graph_node(NodeType.DATA_INPUT), "rate": _graph_node(node_type)},
        attempted=(),
        output_columns=lambda node_id, _handle: {"quotes": {"age", "area", "other"}}.get(node_id),
        source_handles={},
        join_inputs={},
    )

    assert lineage.contributed["rate"] == computed
    assert lineage.contributed["quotes"] == read


def _chain_trace(tmp_path, source: pl.DataFrame, codes: list[tuple[str, str]], column: str):
    """Trace *column* at the end of a chain of Polars nodes over one source."""
    source.write_parquet(tmp_path / "src.parquet")
    ids = ["src", *(node_id for node_id, _code in codes)]
    graph = make_graph(
        {
            "nodes": [
                make_source_node("src", str(tmp_path / "src.parquet")),
                *(make_transform_node(node_id, code) for node_id, code in codes),
            ],
            "edges": [make_edge(parent, child) for parent, child in zip(ids, ids[1:])],
        }
    )
    return _steps_by_id(execute_trace(graph, target_node_id=ids[-1], column=column))


def test_a_helper_column_the_code_drops_still_leads_to_its_inputs(tmp_path):
    steps = _chain_trace(
        tmp_path,
        pl.DataFrame({"a": [1.0], "b": [2.0]}),
        [
            (
                "calc",
                'df = src.with_columns((pl.col("a") * 2).alias("tmp"))\n'
                'df = df.with_columns((pl.col("tmp") + 1).alias("y")).drop("tmp")',
            )
        ],
        "y",
    )

    assert steps["calc"].contributed_columns == ["tmp", "y"]
    assert steps["src"].contributed_columns == ["a"]


def test_an_assignment_that_kept_the_input_value_still_replaces_it(tmp_path):
    steps = _chain_trace(
        tmp_path,
        pl.DataFrame({"a": [1.0], "x": [5]}),
        [("calc", 'df = src.with_columns(pl.lit(5).alias("x"))')],
        "x",
    )

    assert steps["calc"].contributed_columns == ["x"]
    # The literal, not the source's equal x, is the value's origin.
    assert steps["src"].column_relevant is False


def test_a_column_reassigned_after_it_was_read_depends_on_every_input(tmp_path):
    steps = _chain_trace(
        tmp_path,
        pl.DataFrame({"a": [1.0], "b": [2.0], "c": [3.0]}),
        [
            (
                "calc",
                'df = src.with_columns(pl.col("a").alias("x"))\n'
                'df = df.with_columns((pl.col("x") * 2).alias("y"))\n'
                'df = df.with_columns(pl.col("b").alias("x"))',
            )
        ],
        "y",
    )

    # y read the first x (from a); the last assignment of x reads b.
    assert {"a", "b"} <= set(steps["src"].contributed_columns)


def test_a_window_partition_column_leads_to_the_step_that_computed_it(tmp_path):
    steps = _chain_trace(
        tmp_path,
        pl.DataFrame({"age": [31, 45], "premium": [100.0, 200.0]}),
        [
            ("bands", 'df = src.with_columns((pl.col("age") // 10).alias("band"))'),
            ("calc", 'df = bands.with_columns(pl.col("premium").sum().over("band").alias("y"))'),
        ],
        "y",
    )

    assert steps["bands"].contributed_columns == ["band"]
    assert steps["src"].contributed_columns == ["age", "premium"]


def test_a_ratio_constraint_leads_to_its_numerator_and_denominator(tmp_path):
    pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1"],
            "scenario_index": [0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0],
            "claims_base": [55.0, 60.0, 70.0],
            "predicted_premium": [100.0, 100.0, 100.0],
            "unrelated": [1.0, 2.0, 3.0],
        }
    ).write_parquet(tmp_path / "scored.parquet")
    artifact_path = tmp_path / "ratio.json"
    artifact_path.write_text(
        json.dumps(
            {
                "version": "ratio_v1",
                "mode": "online",
                "lambdas": {"loss_ratio": 0.2},
                "objective": "predicted_income",
                "constraints": {
                    "loss_ratio": {
                        "max": 0.60,
                        "numerator": "predicted_claims",
                        "denominator": "predicted_premium",
                    }
                },
                "quote_id": "quote_id",
                "scenario_index": "scenario_index",
                "scenario_value": "scenario_value",
            }
        ),
        encoding="utf-8",
    )
    graph = make_graph(
        {
            "nodes": [
                make_source_node("scored", str(tmp_path / "scored.parquet")),
                make_transform_node(
                    "claims",
                    'df = scored.with_columns(pl.col("claims_base").alias("predicted_claims"))',
                ),
                _node(
                    "apply",
                    NodeType.OPTIMISER_APPLY,
                    {
                        "sourceType": "file",
                        "artifact_path": str(artifact_path),
                        "optimised_value_column": "optimal_premium",
                    },
                ),
            ],
            "edges": [make_edge("scored", "claims"), make_edge("claims", "apply")],
        }
    )

    steps = _steps_by_id(execute_trace(graph, target_node_id="apply", column="optimal_premium"))

    assert steps["apply"].node_detail is not None
    assert steps["apply"].node_detail["status"] == "ok", steps["apply"].node_detail
    assert steps["claims"].contributed_columns == ["predicted_claims"]
    assert {"claims_base", "predicted_premium"} <= set(steps["scored"].contributed_columns)
    assert "unrelated" not in steps["scored"].contributed_columns


def _code_node(node_type: NodeType, config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(nodeType=node_type, config=config or {}, label=""))


def _join_lineage(join_config: dict, *, frames: dict, base: tuple, join: tuple, **options):
    """left -> join <- right -> calc reading ``key_or_value`` from the join."""
    calc_code = 'df = joined.with_columns((pl.col("read") * 2).alias("y"))'
    steps = [
        _step(base[0], added=sorted(frames[base])),
        *([] if options.get("join_unresolved") else [_step(join[0], added=sorted(frames[join]))]),
        _step("joined", added=[]),
        _step("calc", added=["y"], passed=["read"]),
    ]
    order = [base[0], join[0], "joined", "calc"]
    return trace_value_lineage(
        column="y",
        target_node_id="calc",
        steps=steps,
        order=order,
        parents_of={"joined": [base[0], join[0]], "calc": ["joined"]},
        node_map={
            base[0]: _code_node(NodeType.API_INPUT),
            join[0]: _code_node(NodeType.DATA_INPUT),
            "joined": _code_node(NodeType.EDGE_JOIN, join_config),
            "calc": _code_node(NodeType.POLARS, {"code": calc_code}),
        },
        attempted={join[0]} if options.get("join_unresolved") else (),
        output_columns=lambda node_id, handle: frames.get((node_id, handle)),
        source_handles={(base[0], "joined"): [base[1]], (join[0], "joined"): [join[1]]},
        join_inputs={"joined": JoinInputs(base=base, join=join)},
    )


@pytest.mark.parametrize("join_unresolved", [False, True], ids=["correlated", "unresolved"])
def test_a_multi_frame_base_is_read_through_the_frame_its_edge_names(join_unresolved):
    frames = {
        ("api", "policies"): {"quote_id", "x"},
        ("api", "drivers"): {"quote_id", "read"},
        ("rates", None): {"quote_id", "read"},
    }
    lineage = _join_lineage(
        {"how": "left", "on": ["quote_id"]},
        frames=frames,
        base=("api", "policies"),
        join=("rates", None),
        join_unresolved=join_unresolved,
    )

    # The policies frame the join reads has no ``read``: the join side supplied it.
    assert "rates" in lineage.reached
    assert "api" not in lineage.reached
    if not join_unresolved:
        assert lineage.contributed["rates"] == ("read",)


def test_a_coalesced_full_join_key_leads_to_both_sides_under_their_own_names():
    frames = {("left", None): {"read", "x"}, ("right", None): {"right_key", "z"}}
    lineage = _join_lineage(
        {"how": "full", "leftOn": "read", "rightOn": "right_key", "coalesce": True},
        frames=frames,
        base=("left", None),
        join=("right", None),
    )

    assert lineage.contributed["left"] == ("read",)
    assert lineage.contributed["right"] == ("right_key",)
