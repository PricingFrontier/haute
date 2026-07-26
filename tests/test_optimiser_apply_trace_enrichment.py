"""Trace enrichment tests for optimiserApply explainability."""

from __future__ import annotations

import json

import polars as pl
import pytest

from haute._types import GraphNode, NodeData, NodeType
from haute.trace import TraceResult, TraceStep, execute_trace
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def _online_artifact(version: str = "online_v1") -> dict:
    return {
        "version": version,
        "mode": "online",
        "lambdas": {"predicted_volume": 0.5},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }


def _ratio_artifact(version: str = "ratio_v1") -> dict:
    return {
        "version": version,
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


def _no_constraint_artifact(version: str = "unconstrained_v1") -> dict:
    return {
        "version": version,
        "mode": "online",
        "lambdas": {},
        "objective": "predicted_income",
        "constraints": {},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
    }


def _ratebook_artifact(version: str = "rb_v1") -> dict:
    return {
        "version": version,
        "mode": "ratebook",
        "lambdas": {"predicted_volume": 0.3},
        "objective": "predicted_income",
        "constraints": {"predicted_volume": {"min": 0.9}},
        "factor_tables": {
            "region": [
                {"__factor_group__": "London", "optimal_scenario_value": 1.05},
                {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
            ],
            "age_band": [
                {"__factor_group__": "young", "optimal_scenario_value": 1.10},
                {"__factor_group__": "old", "optimal_scenario_value": 0.95},
            ],
        },
        "factor_dtypes": {
            "region": [{"column": "region", "dtype": {"kind": "String"}}],
            "age_band": [{"column": "age_band", "dtype": {"kind": "String"}}],
        },
    }


def _scored_online_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2", None],
            "scenario_index": [0, 1, 2, 0, 1, 2, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1, 1.1],
            "predicted_income": [90.0, 100.0, 110.0, 45.0, 50.0, 55.0, 999.0],
            "predicted_volume": [1.0, 0.9, 0.7, 1.0, 0.95, 0.8, 0.0],
        }
    )


def _scored_ratio_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1"],
            "scenario_index": [0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0],
            "predicted_claims": [55.0, 60.0, 70.0],
            "predicted_premium": [100.0, 100.0, 100.0],
        }
    )


def _write_json(path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _optimiser_apply_node(config: dict) -> GraphNode:
    return GraphNode(
        id="apply",
        data=NodeData(
            label="apply",
            nodeType=NodeType.OPTIMISER_APPLY,
            config=config,
        ),
    )


def _step_by_id(result: TraceResult, node_id: str) -> TraceStep:
    for step in result.steps:
        if step.node_id == node_id:
            return step
    raise KeyError(node_id)


def test_online_execute_trace_attaches_candidate_explanation(tmp_path):
    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())
    scored_path = tmp_path / "scored.parquet"
    _scored_online_df().write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "online"
    assert detail["status"] == "ok"
    assert detail["output"]["column"] == "optimal_scenario_value"
    assert detail["output"]["value"] == pytest.approx(1.1)
    assert detail["columns"] == {
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "objective": "predicted_income",
    }
    assert detail["constraints"]["predicted_volume"]["lambda"] == pytest.approx(0.5)

    candidates = detail["candidates"]
    assert [row["scenario_index"] for row in candidates] == [0, 1, 2]
    assert {row["quote_id"] for row in candidates} == {"q1"}
    assert all("decision_score" in row for row in candidates)
    assert all("linearised_predicted_volume" in row for row in candidates)
    assert all("lambda_term_predicted_volume" in row for row in candidates)

    assert detail["selected"]["scenario_index"] == 2
    assert detail["selected"]["selected"] is True
    assert detail["baseline"]["scenario_index"] == 1
    assert detail["baseline"]["is_baseline"] is True


def test_online_trace_prefers_artifact_configured_quote_id_column(tmp_path):
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact = _online_artifact()
    artifact["quote_id"] = "policy_id"
    artifact_path = _write_json(tmp_path / "custom_quote_id.json", artifact)
    scored = _scored_online_df().rename({"quote_id": "policy_id"})

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={
            # The legacy literal key deliberately points at a different quote.
            "quote_id": "q1",
            "policy_id": "q2",
            "optimal_scenario_value": 1.1,
        },
        input_frames=[scored],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "ok"
    assert detail["quote_id_column"] == "policy_id"
    assert detail["quote_id_value"] == "q2"
    assert {candidate["policy_id"] for candidate in detail["candidates"]} == {"q2"}


def test_online_execute_trace_uses_price_contour_ratio_linearisation(tmp_path):
    artifact_path = _write_json(tmp_path / "ratio.json", _ratio_artifact())
    scored_path = tmp_path / "ratio_scored.parquet"
    _scored_ratio_df().write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["constraints"]["loss_ratio"]["lambda"] == pytest.approx(0.2)
    selected = detail["selected"]
    assert selected["scenario_index"] == 2
    assert "linearised_loss_ratio" in selected
    assert "lambda_term_loss_ratio" in selected
    assert selected["linearised_constraints"]["loss_ratio"] != selected["constraints"].get(
        "loss_ratio"
    )


def test_online_execute_trace_handles_unconstrained_artifact(tmp_path):
    artifact_path = _write_json(tmp_path / "unconstrained.json", _no_constraint_artifact())
    scored_path = tmp_path / "scored.parquet"
    _scored_online_df().drop("predicted_volume").write_parquet(scored_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("scored", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimal_scenario_value",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["constraints"] == {}
    assert detail["lambdas"] == {}
    assert detail["selected"]["decision_score"] == pytest.approx(detail["selected"]["objective"])
    assert detail["baseline"]["scenario_index"] == 1


def test_ratebook_execute_trace_explains_configured_input_factor_ladder(tmp_path):
    artifact_path = _write_json(tmp_path / "ratebook.json", _ratebook_artifact())
    scored_path = tmp_path / "scored.parquet"
    banded_path = tmp_path / "banded.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["London"],
            "age_band": ["old"],
            "base_price": [100.0],
        }
    ).write_parquet(scored_path)
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["Manchester"],
            "age_band": ["young"],
            "base_price": [100.0],
        }
    ).write_parquet(banded_path)

    graph = _g(
        {
            "nodes": [
                _source_node("scored", str(scored_path)),
                _source_node("banded", str(banded_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                        "ratebook_input": "banded",
                        "optimised_value_column": "selected_factor",
                    }
                ),
            ],
            "edges": [_edge("scored", "apply"), _edge("banded", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="selected_factor",
    )

    apply_step = _step_by_id(result, "apply")
    detail = apply_step.node_detail
    assert detail is not None
    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "ratebook"
    assert detail["status"] == "ok"
    assert apply_step.output_values["region"] == "Manchester"
    assert detail["output"]["column"] == "selected_factor"
    assert detail["output"]["value"] == pytest.approx(0.98 * 1.10)
    assert detail["base_value"] == pytest.approx(1.0)
    assert detail["final_value"] == pytest.approx(0.98 * 1.10)

    ladder = detail["factor_ladder"]
    assert [step["factor"] for step in ladder] == ["region", "age_band"]
    assert ladder[0]["input_value"] == "Manchester"
    assert ladder[0]["factor_value"] == pytest.approx(0.98)
    assert ladder[0]["running_product_before"] == pytest.approx(1.0)
    assert ladder[0]["running_product_after"] == pytest.approx(0.98)
    assert ladder[1]["input_value"] == "young"
    assert ladder[1]["factor_value"] == pytest.approx(1.10)
    assert ladder[1]["running_product_before"] == pytest.approx(0.98)
    assert ladder[1]["running_product_after"] == pytest.approx(0.98 * 1.10)


_SEP = "\x1f"  # price-contour's interaction (unit) separator


def _composite_ratebook_artifact(version: str = "rb_comp_v1") -> dict:
    return {
        "version": version,
        "mode": "ratebook",
        "lambdas": {},
        "constraints": {},
        "factor_tables": {
            "channel:age_band": [
                {"__factor_group__": f"online{_SEP}18-25", "optimal_scenario_value": 1.05},
                {"__factor_group__": f"phone{_SEP}18-25", "optimal_scenario_value": 0.98},
            ],
            "region": [
                {"__factor_group__": "London", "optimal_scenario_value": 1.20},
            ],
        },
        "factor_dtypes": {
            "channel:age_band": [
                {"column": "channel", "dtype": {"kind": "String"}},
                {"column": "age_band", "dtype": {"kind": "String"}},
            ],
            "region": [{"column": "region", "dtype": {"kind": "String"}}],
        },
    }


def test_ratebook_execute_trace_explains_composite_factor_ladder(tmp_path):
    """3b.2 end-to-end: the engine applies a composite group via the
    multi-column join and the trace ladder reconciles with that output."""
    artifact_path = _write_json(tmp_path / "ratebook_comp.json", _composite_ratebook_artifact())
    banded_path = tmp_path / "banded.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "channel": ["online", "phone"],
            "age_band": ["18-25", "18-25"],
            "region": ["London", "London"],
            "base_price": [100.0, 100.0],
        }
    ).write_parquet(banded_path)

    graph = _g(
        {
            "nodes": [
                _source_node("banded", str(banded_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("banded", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=1,
        target_node_id="apply",
        column="optimised_factor",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["output"]["value"] == pytest.approx(0.98 * 1.20)

    ladder = detail["factor_ladder"]
    assert [step["factor"] for step in ladder] == ["channel:age_band", "region"]
    composite = ladder[0]
    assert composite["input_value"] == {"channel": "phone", "age_band": "18-25"}
    assert composite["factor_value"] == pytest.approx(0.98)
    assert composite["matched"] is True
    assert composite["unseen"] is False
    assert composite["factor_column"] == "channel:age_band_optimised_factor"
    assert ladder[1]["input_value"] == "London"
    assert ladder[1]["factor_value"] == pytest.approx(1.20)


def test_ratebook_execute_trace_flags_unseen_level_as_neutral(tmp_path):
    """3b.5: an unseen level traces as an explicit neutral miss — flagged
    per row, reconciling with the engine's loud-neutral 1.0."""
    artifact_path = _write_json(tmp_path / "ratebook_comp.json", _composite_ratebook_artifact())
    banded_path = tmp_path / "banded.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "channel": ["phone"],
            "age_band": ["66+"],  # combination never seen by the solver
            "region": ["London"],
            "base_price": [100.0],
        }
    ).write_parquet(banded_path)

    graph = _g(
        {
            "nodes": [
                _source_node("banded", str(banded_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("banded", "apply")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="apply",
        column="optimised_factor",
    )

    detail = _step_by_id(result, "apply").node_detail
    assert detail is not None
    assert detail["status"] == "ok"
    assert detail["output"]["value"] == pytest.approx(1.0 * 1.20)

    composite = detail["factor_ladder"][0]
    assert composite["unseen"] is True
    assert composite["default_used"] is True  # frontend warning-chip contract
    assert composite["matched"] is False
    assert composite["factor_value"] == pytest.approx(1.0)
    assert detail["factor_ladder"][1]["unseen"] is False


def test_ratebook_execute_trace_float_keyed_levels_agree_with_engine(tmp_path):
    """3b.5 mirror: a Float64 frame column (25.0) must trace as MATCHED
    against the string level "25" — the str() mirror said default here while
    the engine join matched.  Reconciliation through execute_trace proves
    ladder and engine agree on the same rows."""
    artifact = {
        "version": "v1",
        "mode": "ratebook",
        "lambdas": {},
        "constraints": {},
        "factor_tables": {
            "age": [
                {"__factor_group__": "25", "optimal_scenario_value": 2.0},
                {"__factor_group__": "30.5", "optimal_scenario_value": 3.0},
            ],
        },
        "factor_dtypes": {
            "age": [{"column": "age", "dtype": {"kind": "Float64"}}],
        },
    }
    artifact_path = _write_json(tmp_path / "ratebook_float.json", artifact)
    banded_path = tmp_path / "banded.parquet"
    pl.DataFrame({"quote_id": ["q1", "q2"], "age": [25.0, 30.5]}).write_parquet(banded_path)

    graph = _g(
        {
            "nodes": [
                _source_node("banded", str(banded_path)),
                _optimiser_apply_node(
                    {
                        "sourceType": "file",
                        "artifact_path": artifact_path,
                    }
                ),
            ],
            "edges": [_edge("banded", "apply")],
        }
    )

    for row_index, expected in [(0, 2.0), (1, 3.0)]:
        result = execute_trace(
            graph,
            row_index=row_index,
            target_node_id="apply",
            column="optimised_factor",
        )
        detail = _step_by_id(result, "apply").node_detail
        assert detail is not None
        assert detail["status"] == "ok", detail.get("error")
        ladder_step = detail["factor_ladder"][0]
        assert ladder_step["matched"] is True
        assert ladder_step["unseen"] is False
        assert ladder_step["factor_value"] == pytest.approx(expected)


def test_ratebook_enrichment_missing_component_column_errors_clearly(tmp_path):
    """A composite table whose component column is absent from the input row
    must produce a structured error naming the missing column(s)."""
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "ratebook_comp.json", _composite_ratebook_artifact())
    banded = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "channel": ["online"],
            # age_band column missing entirely
            "region": ["London"],
        }
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
        },
        input_row={},
        output_row={"quote_id": "q1", "channel": "online", "region": "London"},
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "age_band" in detail["error"]


def test_online_enrichment_surfaces_reconciliation_error(tmp_path):
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "missing", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["detail_type"] == "optimiser_apply"
    assert detail["mode"] == "online"
    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "missing" in detail["error"]


def test_online_enrichment_surfaces_missing_output_column(tmp_path):
    """Output row missing the configured optimised_value_column must fail loudly."""
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "optimised_value_column": "selected_value",
        },
        input_row={},
        output_row={"quote_id": "q1"},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "selected_value" in detail["error"]


def test_online_apply_rejects_explicit_empty_optimised_value_column(tmp_path):
    """An explicit empty online output column is misconfiguration, not defaulting."""
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "optimised_value_column": "",
        },
        input_row={},
        output_row={"quote_id": "q1", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "optimised_value_column" in detail["error"]


def test_online_enrichment_rejects_explicit_empty_quote_id_artifact(tmp_path):
    """An artifact that explicitly sets quote_id='' must fail rather than silently fall back.

    The previous code used ``str(artifact.get('quote_id', 'quote_id') or 'quote_id')``
    which silently rewrote a deliberate empty string to the default. That hides
    config bugs — an explicit empty value is a misconfiguration we want to surface.
    """
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact = _online_artifact()
    artifact["quote_id"] = ""
    artifact_path = _write_json(tmp_path / "online_empty_qid.json", artifact)

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "q1", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"


def test_ratebook_enrichment_surfaces_factor_reconciliation_mismatch(tmp_path):
    """If the output row's per-factor column disagrees with the artifact factor,
    the trace must fail loudly so the user sees the divergence."""
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "ratebook.json", _ratebook_artifact())
    banded = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["Manchester"],
            "age_band": ["young"],
            "base_price": [100.0],
        }
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
        },
        input_row={},
        output_row={
            "quote_id": "q1",
            "region": "Manchester",
            "age_band": "young",
            # age_band optimal is 1.10 in the artifact; output disagrees:
            "age_band_optimised_factor": 9.99,
            "optimised_factor": 0.98 * 1.10,
        },
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "age_band_optimised_factor" in detail["error"]


def test_ratebook_enrichment_fails_when_no_factor_tables_can_be_reconciled(tmp_path):
    """An empty ladder cannot truthfully claim a reconciled explanation."""
    from haute._trace_enrichment import enrich_optimiser_apply

    empty_artifact = _ratebook_artifact()
    empty_artifact["factor_tables"] = {}
    artifact_path = _write_json(tmp_path / "ratebook_empty.json", empty_artifact)

    banded = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["Manchester"],
            "age_band": ["young"],
            "base_price": [100.0],
        }
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
        },
        input_row={},
        # No optimised_factor in the output because no factors were applied.
        output_row={"quote_id": "q1", "region": "Manchester", "age_band": "young"},
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    assert detail["status"] == "error"
    assert detail["mode"] == "ratebook"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "factor tables" in detail["error"].lower()


def test_ratebook_input_match_falls_back_to_python_on_polars_type_mismatch(
    tmp_path,
):
    """When a shared column is typed differently from the output_row value,
    the Polars filter raises but the Python scan can still match via the
    tolerant ``_trace_values_match`` (which string-casts).  The trace must
    fall through gracefully instead of bubbling the Polars exception.
    """
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "ratebook.json", _ratebook_artifact())
    # quote_id is Int64 in the frame, but a stringified value in the output
    # row — Polars rejects ``pl.col("quote_id") == "1"`` on Int64 with a
    # ComputeError/InvalidOperationError.  The Python scan tolerates it
    # because ``_trace_values_match`` casts both sides to str.
    banded = pl.DataFrame(
        {
            "quote_id": [1],
            "region": ["Manchester"],
            "age_band": ["young"],
        },
        schema={"quote_id": pl.Int64, "region": pl.Utf8, "age_band": pl.Utf8},
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
        },
        input_row={},
        output_row={
            # Stringly-typed quote_id forces the type mismatch.
            "quote_id": "1",
            "region": "Manchester",
            "age_band": "young",
            "region_optimised_factor": 0.98,
            "age_band_optimised_factor": 1.10,
            "optimised_factor": 0.98 * 1.10,
        },
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    # A blocker for the user would be a ``ComputeError`` here; a graceful
    # outcome is either a successful trace via the Python fallback, or — if
    # the type mismatch is genuinely irreconcilable — an
    # ``OptimiserApplyTraceError`` with our domain message.
    assert detail["mode"] == "ratebook"
    assert detail["error_type"] != "ComputeError"
    assert detail["error_type"] != "InvalidOperationError"
    assert detail["error_type"] != "SchemaError"


def test_ratebook_match_entry_uses_last_duplicate_to_match_runtime(tmp_path):
    """``_apply_rating_table`` deduplicates with ``keep="last"``, so when the
    artifact carries two entries for the same level, the trace explanation must
    pick the same (last) entry — otherwise the reconciliation check disagrees
    with the actual selected factor.
    """
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact = _ratebook_artifact()
    # London appears twice — the second (last) entry wins at runtime.
    artifact["factor_tables"]["region"] = [
        {"__factor_group__": "London", "optimal_scenario_value": 1.05},
        {"__factor_group__": "Manchester", "optimal_scenario_value": 0.98},
        {"__factor_group__": "London", "optimal_scenario_value": 1.20},
    ]
    artifact["factor_tables"]["age_band"] = [
        {"__factor_group__": "young", "optimal_scenario_value": 1.10},
    ]
    artifact_path = _write_json(tmp_path / "ratebook_dup.json", artifact)
    banded = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["London"],
            "age_band": ["young"],
        }
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
        },
        input_row={},
        output_row={
            "quote_id": "q1",
            "region": "London",
            "age_band": "young",
            "region_optimised_factor": 1.20,
            "age_band_optimised_factor": 1.10,
            "optimised_factor": 1.20 * 1.10,
        },
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    assert detail["status"] == "ok"
    london = detail["factor_ladder"][0]
    assert london["factor_value"] == pytest.approx(1.20)


def test_optimiser_apply_emits_friendly_error_when_price_contour_missing(tmp_path, monkeypatch):
    """A missing price_contour install must produce a deploy-time-friendly error
    rather than a bare ``ImportError`` string.

    Trace enrichment runs in two contexts: production (where price_contour is
    always present) and devboxes/test environments (where it may not be). The
    error message must clearly state the dependency is missing so a deploy
    operator can fix the environment without parsing Python tracebacks.
    """
    import builtins

    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "price_contour":
            # CPython sets ``name`` on import-not-found automatically; passing
            # it explicitly mirrors that production behaviour so the test
            # exercises the real wrapping path (otherwise ``exc.name`` is
            # ``None`` and the rendered message reads "uv add None").
            raise ImportError("No module named 'price_contour'", name="price_contour")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "q1", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    # The user-facing message should name the missing library so a deploy
    # operator can fix the environment without grepping the traceback.
    assert "'price_contour'" in detail["error"]
    # The hint must include the install command — ``exc.name`` flows into the
    # rendered ``uv add ...`` so a regression in the wrapping (e.g. dropping
    # ``exc.name`` for a generic placeholder) is caught.
    assert "uv add price_contour" in detail["error"]


def test_optimiser_apply_rejects_explicit_empty_mode(tmp_path):
    """An artifact with ``mode=""`` must not silently run the online branch.

    The previous ``str(... or "online")`` pattern collapsed "key absent" with
    "key present but blank" — which silently ran online code on what could
    have been a misconfigured ratebook artifact.  Explicit empty values are
    misconfiguration; surface them.
    """
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact = _online_artifact()
    artifact["mode"] = ""
    artifact_path = _write_json(tmp_path / "online_empty_mode.json", artifact)

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "q1", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "mode" in detail["error"]


def test_ratebook_apply_rejects_explicit_empty_optimised_value_column(tmp_path):
    """The same tightening as the online branch — explicit empty config wins
    a loud error rather than a silent fallback to ``optimised_factor``.
    """
    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "ratebook.json", _ratebook_artifact())
    banded = pl.DataFrame(
        {
            "quote_id": ["q1"],
            "region": ["Manchester"],
            "age_band": ["young"],
        }
    )

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
            "ratebook_input": "banded",
            "optimised_value_column": "",
        },
        input_row={},
        output_row={
            "quote_id": "q1",
            "region": "Manchester",
            "age_band": "young",
            "optimised_factor": 0.98 * 1.10,
        },
        input_frames=[banded],
        source_names=["banded"],
        source_ids=["banded"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    assert "optimised_value_column" in detail["error"]


def test_optimiser_apply_import_error_without_name_still_renders_safely(tmp_path, monkeypatch):
    """Defensive: an ``ImportError`` without ``name`` (rare; e.g. a chained or
    re-raised one) must not produce a literal ``None`` in the user message.
    """
    import builtins

    from haute._trace_enrichment import enrich_optimiser_apply

    artifact_path = _write_json(tmp_path / "online.json", _online_artifact())
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "price_contour":
            # Deliberately omit ``name=`` to simulate a re-raised ImportError
            # whose ``name`` attribute was never set.
            raise ImportError("No module named 'price_contour'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    detail = enrich_optimiser_apply(
        {
            "sourceType": "file",
            "artifact_path": artifact_path,
        },
        input_row={},
        output_row={"quote_id": "q1", "optimal_scenario_value": 1.1},
        input_frames=[_scored_online_df()],
        source_names=["scored"],
        source_ids=["scored"],
    )

    assert detail["status"] == "error"
    assert detail["error_type"] == "OptimiserApplyTraceError"
    # Falling back to a generic placeholder is fine — what's NOT fine is
    # exposing the literal ``None`` from ``exc.name`` to the user.
    assert "'None'" not in detail["error"]
    assert "None library" not in detail["error"]
