"""Regression tests for first-class trace gaps and provenance."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest
import structlog
from pydantic import ValidationError

import haute.trace as trace_mod
from haute._trace_correlation import CorrelationWork, _correlate_rows_posthoc
from haute.schemas import TraceOmissionResponse, TraceResultResponse
from haute.trace import (
    SchemaDiff,
    TraceStep,
    _build_trace_omissions,
    execute_trace,
    trace_result_to_dict,
)
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


def test_correlation_telemetry_is_emitted_when_correlation_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.parquet"
    pl.DataFrame({"policy_id": [1]}).write_parquet(source_path)
    graph = make_graph(
        {
            "nodes": [make_source_node("source", str(source_path))],
            "edges": [],
        }
    )

    def fail_correlation(
        *args: object,
        work: CorrelationWork | None = None,
        **kwargs: object,
    ) -> dict[str, dict[str, object] | None]:
        assert work is not None
        work.candidate_frames_considered = 3
        work.match_scans = 2
        work.rows_scanned = 20
        work.key_columns_scanned = 4
        work.comparison_cells = 40
        work.ambiguity_count = 1
        raise RuntimeError("injected correlation failure")

    monkeypatch.setattr(trace_mod, "_correlate_rows_posthoc", fail_correlation)
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(RuntimeError, match="injected correlation failure"):
            execute_trace(graph, target_node_id="source")

    events = [record for record in logs if record.get("event") == "trace_correlation_completed"]
    assert events == [
        {
            "component": "trace",
            "execution_origin": "fresh_execution",
            "duration_ms": events[0]["duration_ms"],
            "candidate_frames_considered": 3,
            "match_scans": 2,
            "rows_scanned": 20,
            "key_columns_scanned": 4,
            "comparison_cells": 40,
            "ambiguity_count": 1,
            "event": "trace_correlation_completed",
            "log_level": "info",
        }
    ]
    assert events[0]["duration_ms"] >= 0


def test_ambiguous_relevant_parent_is_preserved_as_an_omission(tmp_path) -> None:
    source_path = tmp_path / "source.parquet"
    pl.DataFrame(
        {
            "region": ["north", "north", "south"],
            "premium": [10, 20, 40],
        }
    ).write_parquet(source_path)
    graph = make_graph(
        {
            "nodes": [
                make_source_node("source", str(source_path)),
                make_transform_node(
                    "aggregate",
                    "df = source.group_by('region').agg(pl.col('premium').sum())",
                ),
            ],
            "edges": [make_edge("source", "aggregate")],
        }
    )

    result = execute_trace(
        graph,
        row_index=0,
        target_node_id="aggregate",
        column="premium",
        row_values={"region": "north", "premium": 30},
    )

    assert [step.node_id for step in result.steps] == ["aggregate"]
    assert result.steps[0].topological_rank == 1
    assert len(result.omissions) == 1
    omission = result.omissions[0]
    assert omission.node_id == "source"
    assert omission.topological_rank == 0
    assert omission.reason == "relaxed_match_ambiguous"
    assert result.correlation_diagnostics[omission.diagnostic_index]["node_id"] == "source"
    assert result.nodes_in_trace == len(result.steps) + len(result.omissions) == 2

    payload = trace_result_to_dict(result)
    assert payload["omissions"] == [
        {
            "node_id": "source",
            "node_name": "source",
            "node_type": "dataInput",
            "topological_rank": 0,
            "reason": "relaxed_match_ambiguous",
            "diagnostic_index": omission.diagnostic_index,
        }
    ]
    assert payload["steps"][0]["topological_rank"] == 1
    for retired_field in (
        "execution_ms",
        "taken_branch",
        "taken_branch_index",
        "null_explanation",
        "expression_chain",
        "rename_info",
    ):
        assert retired_field not in payload["steps"][0]


def test_trace_provenance_distinguishes_all_execution_origins(tmp_path) -> None:
    from haute import trace

    source_path = tmp_path / "source.parquet"
    pl.DataFrame({"value": [7]}).write_parquet(source_path)
    graph = make_graph(
        {
            "nodes": [make_source_node("source", str(source_path))],
            "edges": [],
            "source_file": "pricing/example.py",
        }
    )
    trace._cache.clear()

    fresh = execute_trace(graph, target_node_id="source", row_limit=10)
    cached = execute_trace(graph, target_node_id="source", row_limit=10)
    trace._cache.clear()
    preview = execute_trace(
        graph,
        target_node_id="source",
        row_limit=10,
        preview={"eager_outputs": {"source": pl.DataFrame({"value": [7]})}},
    )

    assert fresh.execution_origin == "fresh_execution"
    assert cached.execution_origin == "trace_cache"
    assert preview.execution_origin == "preview_cache"
    assert fresh.pipeline_source == "pricing/example.py"
    assert datetime.fromisoformat(fresh.generated_at).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(cached.generated_at).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(preview.generated_at).utcoffset().total_seconds() == 0


def test_trace_omission_schema_requires_linkable_evidence() -> None:
    with pytest.raises(ValidationError):
        TraceOmissionResponse.model_validate(
            {
                "node_id": "source",
                "node_name": "Source",
                "node_type": "dataInput",
            }
        )


def test_benign_column_pruning_does_not_create_an_omission() -> None:
    target_step = TraceStep(
        node_id="target",
        node_name="Target",
        node_type="polars",
        schema_diff=SchemaDiff(
            columns_added=["premium"],
            columns_removed=[],
            columns_modified=[],
            columns_passed=["base"],
        ),
        input_values={"base": 100},
        output_values={"premium": 120},
        expression={
            "expression_text": "base * 1.2",
            "expression_type": "arithmetic",
            "referenced_columns": ["base"],
        },
    )
    node_map = {
        "unrelated": SimpleNamespace(data=SimpleNamespace(label="Unrelated", nodeType="dataInput")),
        "target": SimpleNamespace(data=SimpleNamespace(label="Target", nodeType="polars")),
    }

    omissions = _build_trace_omissions(
        unresolved_rows={"unrelated": ("no_matching_row", 0)},
        order=["unrelated", "target"],
        node_map=node_map,
        eager_outputs={
            "unrelated": pl.DataFrame({"claims_only": [1]}),
            "target": pl.DataFrame({"base": [100], "premium": [120]}),
        },
        steps=[target_step],
        column="premium",
    )

    assert omissions == []


def test_unresolved_assigning_step_keeps_all_attempted_ancestor_omissions() -> None:
    node_map = {
        node_id: SimpleNamespace(data=SimpleNamespace(label=node_id, nodeType="polars"))
        for node_id in ("unresolved-origin", "unresolved-upstream", "target")
    }

    omissions = _build_trace_omissions(
        unresolved_rows={
            "unresolved-upstream": ("no_matching_row", 0),
            "unresolved-origin": ("no_matching_row", 1),
        },
        order=["unresolved-upstream", "unresolved-origin", "target"],
        node_map=node_map,
        eager_outputs={
            "unresolved-upstream": pl.DataFrame({"unrelated_name": [1]}),
            "unresolved-origin": pl.DataFrame({"another_name": [2]}),
            "target": pl.DataFrame({"premium": [120]}),
        },
        steps=[],
        column="premium",
    )

    assert [omission.node_id for omission in omissions] == [
        "unresolved-upstream",
        "unresolved-origin",
    ]


def test_ambiguous_source_frame_is_unresolved_instead_of_guessing() -> None:
    node_map = {
        node_id: SimpleNamespace(
            id=node_id,
            data=SimpleNamespace(nodeType="apiInput", config={}),
        )
        for node_id in ("source", "target")
    }
    diagnostics: list[dict[str, object]] = []
    unresolved: dict[str, tuple[str, int]] = {}
    work = CorrelationWork()

    rows = _correlate_rows_posthoc(
        {
            "source": {
                "first": pl.DataFrame({"policy_id": ["P1"], "premium": [100]}),
                "second": pl.DataFrame({"policy_id": ["P1"], "premium": [100]}),
            },
            "target": pl.DataFrame({"policy_id": ["P1"], "premium": [100]}),
        },
        order=["source", "target"],
        parents_of={"target": ["source"]},
        target_node_id="target",
        row_index=0,
        node_map=node_map,
        diagnostics=diagnostics,
        unresolved=unresolved,
        source_frames_of={("source", "target"): ["first", "second"]},
        traced_column="premium",
        work=work,
    )

    assert rows["source"] is None
    assert unresolved["source"][0] == "multiple_source_frames_matched"
    assert diagnostics[unresolved["source"][1]]["code"] == "ambiguous_source_frame"
    assert work.candidate_frames_considered == 2
    assert work.ambiguity_count == 1


@pytest.mark.parametrize(
    ("generated_at", "message"),
    [
        ("not-a-timestamp", "ISO-8601"),
        ("2026-07-23T12:00:00", "UTC offset"),
        ("2026-07-23T13:00:00+01:00", "UTC offset"),
    ],
)
def test_trace_result_schema_requires_a_valid_utc_timestamp(
    generated_at: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        TraceResultResponse.model_validate(
            {
                "target_node_id": "target",
                "row_index": 0,
                "steps": [],
                "omissions": [],
                "correlation_diagnostics": [],
                "generated_at": generated_at,
                "execution_origin": "fresh_execution",
            }
        )


def test_trace_result_schema_requires_omissions_and_typed_waterfall_errors() -> None:
    payload = {
        "target_node_id": "target",
        "row_index": 0,
        "steps": [],
        "correlation_diagnostics": [],
        "generated_at": "2026-07-23T12:00:00+00:00",
        "execution_origin": "fresh_execution",
    }

    with pytest.raises(ValidationError, match="omissions"):
        TraceResultResponse.model_validate(payload)

    with pytest.raises(ValidationError, match="error_type"):
        TraceResultResponse.model_validate(
            {**payload, "omissions": [], "waterfall": {"error": "cannot reconcile"}}
        )

    with pytest.raises(ValidationError, match="references a diagnostic"):
        TraceResultResponse.model_validate(
            {
                **payload,
                "omissions": [
                    {
                        "node_id": "source",
                        "node_name": "Source",
                        "node_type": "dataInput",
                        "topological_rank": 0,
                        "reason": "duplicate_exact_match",
                        "diagnostic_index": 0,
                    }
                ],
                "correlation_diagnostics": [
                    {
                        "code": "ambiguous_match",
                        "severity": "warning",
                        "reason": "duplicate_exact_match",
                        "message": "multiple rows matched",
                        "node_id": "different-source",
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="references missing diagnostic"):
        TraceResultResponse.model_validate(
            {
                **payload,
                "omissions": [
                    {
                        "node_id": "source",
                        "node_name": "Source",
                        "node_type": "dataInput",
                        "topological_rank": 0,
                        "reason": "duplicate_exact_match",
                        "diagnostic_index": 0,
                    }
                ],
            }
        )


def test_trace_result_schema_accepts_multiple_matching_omission_diagnostics() -> None:
    payload = {
        "target_node_id": "target",
        "row_index": 0,
        "steps": [],
        "omissions": [
            {
                "node_id": "source-a",
                "node_name": "Source A",
                "node_type": "dataInput",
                "topological_rank": 0,
                "reason": "duplicate_exact_match",
                "diagnostic_index": 0,
            },
            {
                "node_id": "source-b",
                "node_name": "Source B",
                "node_type": "dataInput",
                "topological_rank": 1,
                "reason": "relaxed_match_ambiguous",
                "diagnostic_index": 1,
            },
        ],
        "correlation_diagnostics": [
            {
                "code": "ambiguous_match",
                "severity": "warning",
                "reason": "duplicate_exact_match",
                "message": "multiple rows matched",
                "node_id": "source-a",
            },
            {
                "code": "ambiguous_match",
                "severity": "warning",
                "reason": "relaxed_match_ambiguous",
                "message": "multiple relaxed rows matched",
                "node_id": "source-b",
            },
        ],
        "generated_at": "2026-07-23T12:00:00+00:00",
        "execution_origin": "fresh_execution",
    }

    result = TraceResultResponse.model_validate(payload)

    assert [omission.node_id for omission in result.omissions] == [
        "source-a",
        "source-b",
    ]
