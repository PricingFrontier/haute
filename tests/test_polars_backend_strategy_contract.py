from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._execution_schemas import MAX_JSON_SAFE_INTEGER
from haute._native_memory_limit import native_memory_backend_scope
from haute._ram_estimate import MaterialisationEstimate, estimate_materialisation_boundaries
from haute.chunking import ChunkPlanRequest, chunk_plan
from haute.errors import (
    ChunkPlanUnsupportedError,
    ContractMismatchError,
    GroupByExecutionUnsupportedError,
)
from haute.execution import (
    BoundedDiagnosticCollection,
    DiagnosticDetailState,
    ExecutionBoundedness,
    ExecutionStrategy,
    ExecutionStrategyDiagnostic,
    ExecutionStrategyStatus,
    ProjectionRequest,
    execute_lazy_graph,
    plan_execution_strategy,
    plan_prepared_execution_strategy,
)
from haute.executor import _build_node_fn, execute_graph
from haute.projection import _canonical_topological_ranks, prepare_graph
from haute.schemas import (
    ExecutionColumnWidthsCollectionPayload,
    ExecutionMetricsPayload,
    ExecutionStrategyDiagnosticPayload,
    ExecutionStreamabilityEvidencePayload,
    TrainingFeatureColumnReasonCollectionPayload,
    TrainingFeatureNameCollectionPayload,
    TrainingFeatureSelectionDiagnosticPayload,
)
from haute.trace import execute_trace
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
    make_output_config,
    make_ready_file_input_config,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_STATUS_BY_STRATEGY = {
    ExecutionStrategy.PROJECTED: ExecutionStrategyStatus.PROJECTED,
    ExecutionStrategy.SCHEMA_ALL_EXCEPT: ExecutionStrategyStatus.PROJECTED,
    ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: ExecutionStrategyStatus.ADMITTED_EAGER,
    ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
    ExecutionStrategy.MATERIALISATION_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
    ExecutionStrategy.FULL_WIDTH_CONSERVATIVE: ExecutionStrategyStatus.WARNED,
    ExecutionStrategy.UNSUPPORTED: ExecutionStrategyStatus.REJECTED,
    ExecutionStrategy.NOT_PLANNED: ExecutionStrategyStatus.NOT_PLANNED,
}


def test_prepared_planner_ignores_a_missing_parent_when_deriving_input_names() -> None:
    result = plan_prepared_execution_strategy(
        [],
        {"missing": ["child"]},
        {},
        profile=ExecutionProfile.LAZY_SINK,
    )
    assert result is not None


def test_join_headroom_rejection_mentions_the_validate_contract() -> None:
    """An unbounded join is reported as unproven, and says how to bound it."""
    from haute.execution import MANY_TO_MANY_JOIN_DETAIL, _materialisation_rejection

    rejection = _materialisation_rejection(
        node_id="join-node",
        operator="join",
        profile=ExecutionProfile.LAZY_SINK,
        reason_code="materialisation_estimate_unavailable",
        estimated_peak_bytes=None,
        headroom_bytes=10,
        estimate_detail=f"join-node:{MANY_TO_MANY_JOIN_DETAIL}",
    )
    assert rejection.remediation.endswith(
        "The join has no declared validate= contract, so only the many-to-many row "
        "product bounds it; declare validate='m:1', '1:m', or '1:1' where a key side "
        "is unique to get a real estimate."
    )
    # A rejection with a bounded estimate has no contract to declare.
    bounded = _materialisation_rejection(
        node_id="join-node",
        operator="join",
        profile=ExecutionProfile.LAZY_SINK,
        reason_code="materialisation_exceeds_headroom",
        estimated_peak_bytes=20,
        headroom_bytes=10,
    )
    assert "validate=" not in bounded.remediation


def test_snapshot_source_signature_fails_closed_on_invalid_config(monkeypatch) -> None:
    from haute.execution import _snapshot_source_signature

    def invalid_source_signature(*_args, **_kwargs):
        raise ValueError("bad source")

    monkeypatch.setattr(
        "haute._input_providers.source_signature",
        invalid_source_signature,
    )
    assert _snapshot_source_signature(make_graph({"nodes": [], "edges": []}), {}) is None


def _available(items: list[dict[str, object]]) -> BoundedDiagnosticCollection:
    return BoundedDiagnosticCollection.available(items)


@pytest.mark.parametrize(("strategy", "status"), _STATUS_BY_STRATEGY.items())
def test_v1_strategy_status_mapping_is_closed_and_json_safe(
    strategy: ExecutionStrategy,
    status: ExecutionStrategyStatus,
) -> None:
    diagnostic = ExecutionStrategyDiagnostic.create(
        strategy=strategy,
        profile=ExecutionProfile.PREVIEW_EAGER,
        boundedness=ExecutionBoundedness.UNKNOWN,
        reason_code="test_reason",
        boundaries=_available([]),
        reasons=_available([]),
        provenance=_available([]),
    )

    payload = diagnostic.to_dict()

    assert payload["schema_version"] == 1
    assert payload["status"] == status.value
    assert payload["strategy"] == strategy.value
    assert payload["detail_state"] == "available"
    assert (
        ExecutionStrategyDiagnosticPayload.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
            exclude_defaults=True,
        )
        == payload
    )
    json.dumps(payload)


def test_bounded_diagnostic_collections_sort_before_truncating_and_retain_duplicates() -> None:
    reasons = [
        {
            "topological_rank": 1,
            "node_id": "z",
            "reason_code": f"reason_{index:02d}",
            "operator": "polars",
        }
        for index in reversed(range(33))
    ]
    reasons.append(dict(reasons[-1]))

    collection = BoundedDiagnosticCollection.from_items(
        reasons,
        cap=32,
        sort_key="reasons",
    )

    assert collection.state is DiagnosticDetailState.TRUNCATED
    assert collection.total_count == 34
    assert len(collection.items) == 32
    assert [item["reason_code"] for item in collection.items] == sorted(
        item["reason_code"] for item in reasons
    )[:32]


def test_truncated_boundaries_retain_each_present_boundary_kind() -> None:
    boundaries = [
        {
            "topological_rank": index,
            "node_id": f"materialise_{index:02d}",
            "operator": "group_by",
            "boundary_kind": "materialisation-boundary",
        }
        for index in range(33)
    ]
    boundaries.append(
        {
            "topological_rank": 99,
            "node_id": "unprojected_source",
            "operator": "apiInput",
            "boundary_kind": "unprojected-streaming-boundary",
        }
    )

    collection = BoundedDiagnosticCollection.from_items(
        boundaries,
        cap=32,
        sort_key="boundaries",
        retain_one_by="boundary_kind",
    )

    assert collection.state is DiagnosticDetailState.TRUNCATED
    assert collection.total_count == 34
    assert len(collection.items) == 32
    assert collection.items[-1]["node_id"] == "unprojected_source"
    assert {item["boundary_kind"] for item in collection.items} == {
        "materialisation-boundary",
        "unprojected-streaming-boundary",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("status", "mystery"),
        ("strategy", "mystery"),
        ("boundedness", "mystery"),
        ("detail_state", "mystery"),
        ("profile", "mystery"),
    ],
)
def test_v1_dto_rejects_unsupported_versions_and_unknown_enums(field: str, value: object) -> None:
    payload = ExecutionStrategyDiagnostic.create(
        strategy=ExecutionStrategy.PROJECTED,
        profile=ExecutionProfile.PREVIEW_EAGER,
        boundedness=ExecutionBoundedness.BOUNDED,
        reason_code="projection_seed",
        boundaries=_available([]),
        reasons=_available([]),
        provenance=_available([]),
    ).to_dict()
    payload[field] = value

    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)


def test_v1_dto_rejects_inconsistent_and_over_cap_collections() -> None:
    payload = ExecutionStrategyDiagnostic.create(
        strategy=ExecutionStrategy.PROJECTED,
        profile=ExecutionProfile.PREVIEW_EAGER,
        boundedness=ExecutionBoundedness.BOUNDED,
        reason_code="projection_seed",
        boundaries=_available([]),
        reasons=_available([]),
        provenance=_available([]),
    ).to_dict()
    payload["reasons"] = {
        "state": "available",
        "total_count": 1,
        "items": [],
    }
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (
            lambda payload: (
                payload["boundaries"]["items"].append(
                    {
                        "topological_rank": True,
                        "node_id": "node",
                        "operator": "select",
                        "boundary_kind": "unprojected-streaming-boundary",
                    }
                ),
                payload["boundaries"].update(total_count=1),
            ),
            "topological_rank",
        ),
        (
            lambda payload: (
                payload["reasons"].update(
                    state="truncated",
                    total_count=2**53,
                ),
                payload.update(detail_state="truncated"),
            ),
            "total_count",
        ),
        (
            lambda payload: payload.update(estimated_peak_bytes=2**53),
            "estimated_peak_bytes",
        ),
    ],
)
def test_v1_dto_rejects_values_that_cannot_be_exact_browser_integers(
    mutate: object,
    field: str,
) -> None:
    payload = ExecutionStrategyDiagnostic.create(
        strategy=ExecutionStrategy.PROJECTED,
        profile=ExecutionProfile.PREVIEW_EAGER,
        boundedness=ExecutionBoundedness.BOUNDED,
        reason_code="projection_seed",
        boundaries=_available([]),
        reasons=_available([]),
        provenance=_available([]),
    ).to_dict()
    mutate(payload)  # type: ignore[operator]

    with pytest.raises(ValidationError) as error:
        ExecutionStrategyDiagnosticPayload.model_validate(payload)
    assert field in str(error.value)

    payload["reasons"] = {
        "state": "available",
        "total_count": 33,
        "items": [{"reason_code": f"r{index:02d}"} for index in range(33)],
    }
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)


@pytest.mark.parametrize(
    "calibration_fields",
    [
        {"estimated_peak_bytes": -1},
        {"estimated_peak_bytes": True},
        {"estimate_admission_basis": "mystery"},
        {"estimated_peak_bytes": 10, "raw_estimated_peak_bytes": 10},
        {
            "estimated_peak_bytes": 10,
            "raw_estimated_peak_bytes": 10,
            "estimate_calibration_factor_basis_points": 9_999,
            "estimate_admission_basis": "provided",
        },
        {
            "estimated_peak_bytes": 81,
            "raw_estimated_peak_bytes": 10,
            "estimate_calibration_factor_basis_points": 80_001,
            "estimate_admission_basis": "provided",
        },
        {
            "estimated_peak_bytes": 11,
            "raw_estimated_peak_bytes": 10,
            "estimate_calibration_factor_basis_points": 10_000,
            "estimate_admission_basis": "provided",
        },
    ],
)
def test_calibrated_strategy_evidence_is_complete_upward_bounded_and_exact(
    calibration_fields: dict[str, object],
) -> None:
    base = {
        "strategy": ExecutionStrategy.PROJECTED,
        "profile": ExecutionProfile.PREVIEW_EAGER,
        "boundedness": ExecutionBoundedness.BOUNDED,
        "reason_code": "projection_seed",
        "boundaries": _available([]),
        "reasons": _available([]),
        "provenance": _available([]),
    }
    with pytest.raises(ValueError):
        ExecutionStrategyDiagnostic.create(**base, **calibration_fields)

    valid_payload = ExecutionStrategyDiagnostic.create(**base).to_dict()
    valid_payload.update(calibration_fields)
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(valid_payload)


@pytest.mark.parametrize(
    "calibration_fields",
    [
        {"estimated_bytes": 10, "raw_estimated_bytes": 10},
        {
            "estimated_bytes": 10,
            "raw_estimated_bytes": 10,
            "estimate_calibration_factor_basis_points": 9_999,
            "estimate_admission_basis": "provided",
        },
        {
            "estimated_bytes": 81,
            "raw_estimated_bytes": 10,
            "estimate_calibration_factor_basis_points": 80_001,
            "estimate_admission_basis": "provided",
        },
        {
            "estimated_bytes": 11,
            "raw_estimated_bytes": 10,
            "estimate_calibration_factor_basis_points": 10_000,
            "estimate_admission_basis": "provided",
        },
    ],
)
def test_calibrated_metric_evidence_uses_the_same_closed_contract(
    calibration_fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExecutionMetricsPayload.model_validate(
            {**calibration_fields, "cache_proof": _empty_cache_proof()}
        )


def _empty_cache_proof() -> dict[str, object]:
    return {
        "hits": 0,
        "misses": 0,
        "direct_fallbacks": 0,
        "miss_reason_counts": {
            "metadata_source_mismatch": 0,
            "artifact_integrity_schema_failure": 0,
            "unreadable_artifact": 0,
            "proof_unavailable": 0,
        },
    }


def test_execution_metrics_payload_requires_cache_proof_evidence() -> None:
    with pytest.raises(ValidationError):
        ExecutionMetricsPayload.model_validate({})
    with pytest.raises(ValidationError):
        ExecutionMetricsPayload.model_validate(
            {"cache_proof": {**_empty_cache_proof(), "misses": 3}}
        )


def test_calibrated_payloads_accept_exact_upward_evidence() -> None:
    diagnostic = ExecutionStrategyDiagnostic.create(
        strategy=ExecutionStrategy.PROJECTED,
        profile=ExecutionProfile.PREVIEW_EAGER,
        boundedness=ExecutionBoundedness.BOUNDED,
        reason_code="projection_seed",
        boundaries=_available([]),
        reasons=_available([]),
        provenance=_available([]),
    ).to_dict()
    diagnostic.update(
        {
            "estimated_peak_bytes": 11,
            "raw_estimated_peak_bytes": 10,
            "estimate_calibration_factor_basis_points": 11_000,
            "estimate_admission_basis": "provided",
        }
    )

    assert ExecutionStrategyDiagnosticPayload.model_validate(diagnostic).estimated_peak_bytes == 11
    assert (
        ExecutionMetricsPayload.model_validate(
            {
                "estimated_bytes": 11,
                "raw_estimated_bytes": 10,
                "estimate_calibration_factor_basis_points": 11_000,
                "estimate_admission_basis": "provided",
                "cache_proof": _empty_cache_proof(),
            }
        ).estimated_bytes
        == 11
    )


def test_v1_dto_rejects_every_inconsistent_collection_state_and_order() -> None:
    def valid_payload() -> dict[str, object]:
        return ExecutionStrategyDiagnostic.create(
            strategy=ExecutionStrategy.PROJECTED,
            profile=ExecutionProfile.PREVIEW_EAGER,
            boundedness=ExecutionBoundedness.BOUNDED,
            reason_code="projection_seed",
            boundaries=_available([]),
            reasons=_available([]),
            provenance=_available([]),
        ).to_dict()

    invalid_reasons = [
        {"state": "unavailable", "total_count": 0, "items": []},
        {"state": "available", "total_count": None, "items": []},
        {
            "state": "truncated",
            "total_count": 1,
            "items": [{"reason_code": "one"}],
        },
        {
            "state": "available",
            "total_count": 2,
            "items": [
                {"reason_code": "later", "topological_rank": 1},
                {"reason_code": "earlier", "topological_rank": 0},
            ],
        },
    ]
    for reasons in invalid_reasons:
        payload = valid_payload()
        payload["reasons"] = reasons
        with pytest.raises(ValidationError):
            ExecutionStrategyDiagnosticPayload.model_validate(payload)

    payload = valid_payload()
    payload["reasons"] = {
        "state": "available",
        "total_count": 2,
        "items": [
            {
                "reason_code": "ranked",
                "topological_rank": MAX_JSON_SAFE_INTEGER,
                "node_id": "z",
            },
            {
                "reason_code": "unranked",
                "topological_rank": None,
                "node_id": "a",
            },
        ],
    }
    assert ExecutionStrategyDiagnosticPayload.model_validate(payload).reasons.total_count == 2

    payload = valid_payload()
    payload["boundaries"] = {
        "state": "available",
        "total_count": 2,
        "items": [
            {
                "topological_rank": 1,
                "node_id": "later",
                "operator": "polars",
                "boundary_kind": "materialisation-boundary",
            },
            {
                "topological_rank": 0,
                "node_id": "earlier",
                "operator": "polars",
                "boundary_kind": "materialisation-boundary",
            },
        ],
    }
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)

    payload = valid_payload()
    payload["provenance"] = {
        "state": "available",
        "total_count": 2,
        "items": [
            {"column": "z", "origin_kind": "seed"},
            {"column": "a", "origin_kind": "seed"},
        ],
    }
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)

    payload = valid_payload()
    payload["status"] = "boundary"
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)

    payload = valid_payload()
    payload["provenance"] = {"state": "unavailable", "total_count": None, "items": []}
    with pytest.raises(ValidationError):
        ExecutionStrategyDiagnosticPayload.model_validate(payload)

    with pytest.raises(ValidationError):
        ExecutionStreamabilityEvidencePayload.model_validate(
            {"state": "available", "total_count": 2, "items": ["z", "a"]}
        )
    with pytest.raises(ValidationError):
        ExecutionColumnWidthsCollectionPayload.model_validate(
            {
                "state": "available",
                "total_count": 2,
                "items": [{"node_id": "z"}, {"node_id": "a"}],
            }
        )


def test_training_feature_dto_rejects_duplicate_and_inconsistent_diagnostics() -> None:
    with pytest.raises(ValidationError):
        TrainingFeatureNameCollectionPayload.model_validate(
            {"state": "available", "total_count": 2, "items": ["x", "x"]}
        )
    with pytest.raises(ValidationError):
        TrainingFeatureColumnReasonCollectionPayload.model_validate(
            {
                "state": "available",
                "total_count": 2,
                "items": [
                    {"column": "x", "reason": "target"},
                    {"column": "x", "reason": "not_selected"},
                ],
            }
        )

    payload = {
        "mode": "explicit",
        "feature_count": 2,
        "detail_state": "available",
        "features": {"state": "available", "total_count": 1, "items": ["x"]},
        "retained_metadata": {"state": "available", "total_count": 0, "items": []},
        "excluded_columns": {"state": "available", "total_count": 0, "items": []},
    }
    with pytest.raises(ValidationError):
        TrainingFeatureSelectionDiagnosticPayload.model_validate(payload)

    payload["feature_count"] = 2
    payload["features"] = {"state": "truncated", "total_count": 2, "items": ["x"]}
    with pytest.raises(ValidationError):
        TrainingFeatureSelectionDiagnosticPayload.model_validate(payload)


def _group_by_graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config("missing.parquet"),
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = df.group_by('segment').agg("
                                "pl.col('premium').sum().alias('premium'))"
                            )
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "agg",
                                    "source_column": "premium",
                                    "output_path": "$[:].premium",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


def _opaque_fan_out_graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config("missing.parquet"),
                    },
                },
                {
                    "id": "left",
                    "data": {
                        "label": "left",
                        "nodeType": "polars",
                        "config": {"code": "df = df"},
                    },
                },
                {
                    "id": "right",
                    "data": {
                        "label": "right",
                        "nodeType": "polars",
                        "config": {"code": "df = df"},
                    },
                },
            ],
            "edges": [
                make_edge("source", "left").model_dump(),
                make_edge("source", "right").model_dump(),
            ],
        }
    )


def _fan_in_source(node_id: str) -> dict:
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": "dataInput",
            "config": make_file_input_config(f"{node_id}.parquet"),
        },
    }


def _multi_parent_polars_graph():
    """A multi-parent Polars node with no fan-in ownership contract."""
    return make_graph(
        {
            "nodes": [
                _fan_in_source("left"),
                _fan_in_source("right"),
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "polars",
                        "config": {"code": "df = combine(left, right)"},
                    },
                },
            ],
            "edges": [
                make_edge("left", "join").model_dump(),
                make_edge("right", "join").model_dump(),
            ],
        }
    )


def _dynamic_suffix_fan_in_join_graph():
    """A declared fan-in join whose ``suffix`` is not a literal."""
    return make_graph(
        {
            "nodes": [
                _fan_in_source("left"),
                _fan_in_source("right"),
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "suffix = pick_suffix()\n"
                                "df = left.join(right, on='quote_id', suffix=suffix)"
                            ),
                            "contract": {
                                "inputs": ["quote_id", "premium"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "left": ["quote_id", "premium"],
                                    "right": ["quote_id", "premium"],
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                make_edge("left", "join").model_dump(),
                make_edge("right", "join").model_dump(),
            ],
        }
    )


_UNPROVABLE_FAN_IN_GRAPHS = {
    "multi_parent_polars": _multi_parent_polars_graph,
    "dynamic_suffix_join": _dynamic_suffix_fan_in_join_graph,
}

_UNPROVABLE_FAN_IN_REASONS = {
    "multi_parent_polars": "polars_lineage_unsupported",
    "dynamic_suffix_join": "fan_in_join_dynamic_arguments",
}


# ``combine(left, right)`` is an opaque helper, so that node materialises
# nothing the planner can name. The declared join does call a boundary operator
# (EXEC-P07), so it is a materialisation boundary whose ports are unreadable.
_UNPROVABLE_FAN_IN_IS_BOUNDARY = {
    "multi_parent_polars": False,
    "dynamic_suffix_join": True,
}


def _plan_unprovable_fan_in(
    profile: ExecutionProfile,
    graph,
    *,
    execution_context: ExecutionContext | None = None,
):
    return plan_execution_strategy(
        ProjectionRequest(
            graph=graph,
            target_node_id="join",
            profile=profile,
            required_columns_by_node={"join": {"premium"}},
        ),
        execution_context=execution_context,
        materialisation_estimate=None,
    )


@pytest.mark.parametrize("graph_name", sorted(_UNPROVABLE_FAN_IN_GRAPHS))
@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_unprovable_fan_in_keeps_a_full_width_boundary_on_every_profile(
    profile: ExecutionProfile,
    graph_name: str,
) -> None:
    """The fan-in projection stays full-width whatever the memory strategy is."""
    graph = _UNPROVABLE_FAN_IN_GRAPHS[graph_name]()
    is_boundary = _UNPROVABLE_FAN_IN_IS_BOUNDARY[graph_name]

    if is_boundary:
        # A join is a materialisation boundary, and these sources are unreadable,
        # so its estimate is unavailable: a hard worker cap bounds the run.
        with native_memory_backend_scope("rlimit"):
            result = _plan_unprovable_fan_in(profile, graph, execution_context=_context(profile))
        assert result.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
        assert result.status is ExecutionStrategyStatus.WARNED
        assert result.diagnostic.blocking_node_id == "join"
        assert result.diagnostic.blocking_operator == "join"
    else:
        result = _plan_unprovable_fan_in(profile, graph)
        assert result.strategy is ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY

    # Whichever memory strategy applies, the fan-in projection is unchanged.
    assert result.projection_plan.needed_by_node["left"] is None
    assert result.projection_plan.needed_by_node["right"] is None
    assert _UNPROVABLE_FAN_IN_REASONS[graph_name] in {
        item.get("reason_code") for item in result.diagnostic.reasons.items
    }


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_unprovable_fan_in_boundary_without_a_native_cap_is_rejected(
    profile: ExecutionProfile,
) -> None:
    """No cap and no estimate leaves no bounded envelope to run the join inside."""
    graph = _UNPROVABLE_FAN_IN_GRAPHS["dynamic_suffix_join"]()

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_unprovable_fan_in(profile, graph, execution_context=_context(profile))

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert error.value.operator == "join"
    assert error.value.node_id == "join"


@pytest.mark.parametrize("graph_name", sorted(_UNPROVABLE_FAN_IN_GRAPHS))
def test_unprovable_fan_in_plans_differ_only_in_the_configured_profile(
    graph_name: str,
) -> None:
    graph = _UNPROVABLE_FAN_IN_GRAPHS[graph_name]()
    is_boundary = _UNPROVABLE_FAN_IN_IS_BOUNDARY[graph_name]

    payloads = []
    for profile in ExecutionProfile:
        if is_boundary:
            with native_memory_backend_scope("rlimit"):
                result = _plan_unprovable_fan_in(
                    profile, graph, execution_context=_context(profile)
                )
        else:
            result = _plan_unprovable_fan_in(profile, graph)
        payload = result.diagnostic.to_dict()
        assert payload.pop("profile") == profile.value
        payloads.append(
            (
                dict(result.projection_plan.needed_by_node),
                result.projection_plan.diagnostics.to_dict(),
                payload,
            )
        )

    assert all(payload == payloads[0] for payload in payloads)


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_contradictory_declared_contract_still_raises_on_every_profile(
    profile: ExecutionProfile,
) -> None:
    graph = make_graph(
        {
            "nodes": [
                _fan_in_source("left"),
                _fan_in_source("right"),
                {
                    "id": "join",
                    "data": {
                        "label": "join",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='quote_id', how='left')",
                            "contract": {
                                "inputs": ["quote_id"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "left": ["quote_id"],
                                    "right": None,
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                make_edge("left", "join").model_dump(),
                make_edge("right", "join").model_dump(),
            ],
        }
    )

    with pytest.raises(ContractMismatchError):
        _plan_unprovable_fan_in(profile, graph)


def test_canonical_topological_ranks_use_lexical_tie_breaks() -> None:
    children = {"z": ["out"], "a": ["out"], "out": []}

    expected = {"a": 0, "z": 1, "out": 2}
    assert dict(_canonical_topological_ranks(["z", "a", "out"], children)) == expected
    assert dict(_canonical_topological_ranks(["out", "a", "z"], children)) == expected


def test_opaque_fan_out_reports_that_the_seed_cannot_apply() -> None:
    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_opaque_fan_out_graph(),
            target_node_id=None,
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"source": {"premium"}},
        )
    )

    assert result.strategy is ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY
    source_reason = next(
        item for item in result.diagnostic.reasons.items if item.get("node_id") == "source"
    )
    assert source_reason["reason_code"] == "projection_seed_blocked_by_opaque_fan_out"


def test_strategy_diagnostic_reports_applied_seed_and_conservative_boundary_provenance() -> None:
    seeded = plan_execution_strategy(
        ProjectionRequest(
            graph=_opaque_fan_out_graph(),
            target_node_id="left",
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"left": {"premium"}},
        )
    )

    assert {
        "column": "premium",
        "origin_kind": "seed",
        "source_node_id": "left",
        "source_column": "premium",
    } in [dict(item) for item in seeded.diagnostic.provenance.items]

    blocked = plan_execution_strategy(
        ProjectionRequest(
            graph=_opaque_fan_out_graph(),
            target_node_id=None,
            profile=ExecutionProfile.PREVIEW_EAGER,
            required_columns_by_node={"source": {"premium"}},
        )
    )
    assert any(
        item["column"] == "*"
        and item["origin_kind"] == "conservative_boundary"
        and item["source_node_id"] == "source"
        for item in blocked.diagnostic.provenance.items
    )


def test_strategy_diagnostic_reports_edge_join_keys_as_join_key_provenance() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "base",
                    "data": {
                        "label": "base",
                        "nodeType": "polars",
                        "config": {
                            "contract": {
                                "inputs": [],
                                "outputs": ["base_key", "premium"],
                            }
                        },
                    },
                },
                {
                    "id": "lookup",
                    "data": {
                        "label": "lookup",
                        "nodeType": "polars",
                        "config": {
                            "contract": {
                                "inputs": [],
                                "outputs": ["lookup_key", "factor"],
                            }
                        },
                    },
                },
                {
                    "id": "joined",
                    "data": {
                        "label": "joined",
                        "nodeType": "edgeJoin",
                        "config": {
                            "how": "left",
                            "leftOn": ["base_key"],
                            "rightOn": ["lookup_key"],
                            "contract": "opaque",
                        },
                    },
                },
            ],
            "edges": [
                make_edge("base", "joined", target_handle="base").model_dump(),
                make_edge("lookup", "joined", target_handle="join").model_dump(),
            ],
        }
    )

    result = plan_execution_strategy(
        ProjectionRequest(
            graph=graph,
            target_node_id="joined",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"joined": {"premium", "factor"}},
        )
    )

    provenance = [dict(item) for item in result.diagnostic.provenance.items]
    assert {
        "column": "base_key",
        "origin_kind": "join_key",
        "source_node_id": "joined",
        "source_column": "base_key",
    } in provenance
    assert {
        "column": "lookup_key",
        "origin_kind": "join_key",
        "source_node_id": "joined",
        "source_column": "lookup_key",
    } in provenance


def test_prepared_and_request_planners_return_the_same_contract() -> None:
    graph = _opaque_fan_out_graph()
    request = ProjectionRequest(
        graph=graph,
        target_node_id=None,
        profile=ExecutionProfile.PREVIEW_EAGER,
        required_columns_by_node={"source": {"premium"}},
    )
    prepared = prepare_graph(graph, None, source="live")
    children = {node_id: [] for node_id in prepared.order}
    for child_id, parents in prepared.parents_of.items():
        for parent_id in parents:
            children[parent_id].append(child_id)

    request_result = plan_execution_strategy(request)
    prepared_result = plan_prepared_execution_strategy(
        prepared.order,
        children,
        prepared.node_map,
        profile=request.profile,
        required_columns_by_node=request.required_columns_by_node,
    )

    assert prepared_result.diagnostic.to_dict() == request_result.diagnostic.to_dict()
    assert prepared_result.needed_by_node == request_result.needed_by_node


def _receiver_graph(code: str, *, source_label: str = "claims"):
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": source_label,
                        "nodeType": "dataInput",
                        "config": make_file_input_config("missing.parquet"),
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": code},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "agg",
                                    "source_column": "premium",
                                    "output_path": "$[:].premium",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


_AGG = "agg(pl.col('premium').sum().alias('premium'))"


_NON_FRAME_RECEIVER_CODES = [
    # An expression-namespace method is never a frame receiver.
    "df = df.with_columns(pl.col('x').list.group_by('segment').alias('y'))",
    # A registered ``pl`` expression function builds an expression, not a frame.
    "stats = pl.col('premium').group_by('segment')\ndf = df.filter(pl.col('premium') > 0)",
    # A name definitely rebound to an expression is not a frame receiver.
    (
        "tmp = pl.col('premium')\n"
        "stats = tmp.group_by('segment')\n"
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "df = tmp"
    ),
    # A definite walrus rebinding to a non-frame removes the frame fact.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "(tmp := pl.col('premium'))\n"
        "stats = tmp.group_by('segment')\n"
        "df = df.filter(pl.col('premium') > 0)"
    ),
    # A chained rebinding to a non-frame removes every name in the chain.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "tmp = other = pl.col('premium')\n"
        "stats = tmp.group_by('segment')\n"
        "df = df.filter(pl.col('premium') > 0)"
    ),
    # A tuple swap moves the frame fact with the value: ``tmp`` now holds the
    # expression, so its group-by is not a boundary.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "other = pl.col('premium')\n"
        "tmp, other = other, tmp\n"
        "stats = tmp.group_by('segment')\n"
        "df = other.filter(pl.col('premium') > 0)"
    ),
    # A walrus later in the right-hand side binds ``other`` to the expression.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "tmp, other = tmp, (tmp := pl.col('premium'))\n"
        "stats = other.group_by('segment')\n"
        "df = tmp.filter(pl.col('premium') > 0)"
    ),
    # The first comparator of a chained comparison is always evaluated, so
    # its walrus definitely rebinds ``tmp`` to the expression.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "ok = 1 < (tmp := pl.col('premium'))\n"
        "stats = tmp.group_by('segment')\n"
        "df = df.filter(pl.col('premium') > 0)"
    ),
    # Dictionary displays evaluate key/value pairs in order: the walrus in the
    # first value runs before the second key reads ``tmp``.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "d = {'a': (tmp := pl.col('premium')), tmp.group_by('segment'): 2}\n"
        "df = df.filter(pl.col('premium') > 0)"
    ),
    # A function object is a provable non-frame.
    (
        "def tmp(part):\n"
        "    return part\n"
        "stats = tmp.group_by('segment')\n"
        "df = df.filter(pl.col('premium') > 0)"
    ),
    # A tuple of provable non-frames cannot be mutated into holding a frame.
    "t = (1, 2)\nstats = t[0].group_by('segment')\ndf = df.filter(pl.col('premium') > 0)",
    # Augmenting a provable non-frame with another keeps it a non-frame.
    "n = 0\nn += 1\nstats = n.group_by('segment')\ndf = df.filter(pl.col('premium') > 0)",
    # A walrus in the augmented right-hand side rebinds ``n`` to a non-frame
    # before the store, and the stored sum is a non-frame too.
    ("n = 0\nn += (n := 1)\nstats = n.group_by('segment')\ndf = df.filter(pl.col('premium') > 0)"),
    # A dtype attribute argument is not a frame receiver: no boundary.
    "df = df.with_columns(pl.col('premium').cast(pl.Int64))",
]


@pytest.mark.parametrize("code", _NON_FRAME_RECEIVER_CODES)
def test_group_by_on_a_non_frame_receiver_is_not_a_materialisation_boundary(
    code: str,
) -> None:
    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_receiver_graph(code),
            target_node_id="out",
            profile=ExecutionProfile.LAZY_SINK,
        )
    )

    assert result.strategy in {
        ExecutionStrategy.PROJECTED,
        ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY,
    }
    assert result.projection_plan.materialisation_boundaries == frozenset()


_FRAME_RECEIVER_CODES = [
    f"df = df.group_by('segment').{_AGG}",
    f"df = claims.group_by('segment').{_AGG}",
    f"tmp = df.filter(pl.col('premium') > 0)\ndf = tmp.group_by('segment').{_AGG}",
    # Rebinding the alias after its group-by cannot hide the boundary.
    (f"tmp = df.filter(pl.col('premium') > 0)\ndf = tmp.group_by('segment').{_AGG}\ntmp = 0"),
    # A frame bound inside a block is a may-frame afterwards.
    (f"if True:\n    tmp = df.filter(pl.col('premium') > 0)\ndf = tmp.group_by('segment').{_AGG}"),
    # A non-frame rebinding inside a block never removes a frame name.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "if False:\n"
        "    tmp = 0\n"
        f"df = tmp.group_by('segment').{_AGG}"
    ),
    # A walrus binding roots in the frame it wraps.
    f"df = (tmp := df.filter(pl.col('premium') > 0)).group_by('segment').{_AGG}",
    # Every name in a chained assignment becomes a frame.
    f"tmp = other = df.filter(pl.col('premium') > 0)\ndf = other.group_by('segment').{_AGG}",
    # Element-wise unpacking binds each name from its own value.
    f"tmp, n = df.filter(pl.col('premium') > 0), 3\ndf = tmp.group_by('segment').{_AGG}",
    # Unpacking from an unresolvable value marks the names as may-frames.
    f"tmp, n = build()\ndf = tmp.group_by('segment').{_AGG}",
    # A walrus rebinding in a branch that may not run cannot remove a frame.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "x = 1 if True else (tmp := 0)\n"
        f"df = tmp.group_by('segment').{_AGG}"
    ),
    # A loop target may hold a frame.
    f"for tmp in [df]:\n    df = tmp.group_by('segment').{_AGG}",
    # A tuple swap uses parallel-assignment semantics: ``other`` receives the
    # frame that ``tmp`` held before the assignment.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "other = 0\n"
        "tmp, other = other, tmp\n"
        f"df = other.group_by('segment').{_AGG}"
    ),
    # A chained self-referential assignment binds from the pre-assignment value.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "tmp = other = tmp.filter(pl.col('premium') < 10)\n"
        f"df = other.group_by('segment').{_AGG}"
    ),
    # The first element's fact is captured before the later walrus rebinds
    # ``tmp``, and the assignment restores the frame to ``tmp``.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "tmp, other = tmp, (tmp := 0)\n"
        f"df = tmp.group_by('segment').{_AGG}"
    ),
    # A short-circuit expression with a frame operand may yield the frame.
    f"tmp = 0 or df.filter(pl.col('premium') > 0)\ndf = tmp.group_by('segment').{_AGG}",
    # A conditional expression with a frame branch may yield the frame.
    f"tmp = 0 if False else df.filter(pl.col('premium') > 0)\ndf = tmp.group_by('segment').{_AGG}",
    # A comprehension target may hold a frame drawn from its iterable.
    f"df = [part.group_by('segment').{_AGG} for part in [df]][0]",
    # A later comparator of a chained comparison may be skipped, so its walrus
    # cannot remove the frame fact.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "ok = 1 > 2 > (tmp := 0)\n"
        f"df = tmp.group_by('segment').{_AGG}"
    ),
    # A lambda body may never run, so its walrus cannot remove the frame fact.
    (
        "tmp = df.filter(pl.col('premium') > 0)\n"
        "f = lambda: (tmp := 0)\n"
        f"df = tmp.group_by('segment').{_AGG}"
    ),
    # An unbound name may be a preamble frame.
    "stats = lookup.group_by('segment')\ndf = df.filter(pl.col('premium') > 0)",
    # A function parameter may hold a frame inside the body.
    (f"def aggregate(part):\n    return part.group_by('segment').{_AGG}\ndf = aggregate(df)"),
    # A lambda parameter may hold a frame inside the body.
    f"aggregate = lambda part: part.group_by('segment').{_AGG}\ndf = aggregate(df)",
    # The result of a call the analyser cannot see through may be a frame.
    f"tmp = build(df)\ndf = tmp.group_by('segment').{_AGG}",
    f"df = build(df).group_by('segment').{_AGG}",
    # An unregistered ``pl`` function may construct a frame.
    f"df = pl.concat([df, df])\ndf = df.group_by('segment').{_AGG}",
    # A container holding a frame yields a frame when subscripted.
    f"df = [df][0].group_by('segment').{_AGG}",
    # A mutable container may receive a frame after it is built.
    f"frames = []\nframes.append(df)\ndf = frames[0].group_by('segment').{_AGG}",
    # A subscript assignment marks the container's name as a may-frame.
    f"d = {{}}\nd['k'] = df\ndf = d['k'].group_by('segment').{_AGG}",
    # An augmented assignment with a frame operand makes the name a may-frame.
    f"t = ()\nt += (df,)\ndf = t[0].group_by('segment').{_AGG}",
    # The augmented target is read before its right-hand side runs, so a
    # walrus there cannot discard the frame the target already held.
    f"t = (df,)\nt += (t := ())\ndf = t[0].group_by('segment').{_AGG}",
    # An attribute assignment marks the object's name as a may-frame.
    (f"def cache():\n    return 0\ncache.frame = df\ndf = cache.frame.group_by('segment').{_AGG}"),
    # An unbound frame-class method called through ``pl`` takes the frame as
    # its first argument, so it is a boundary.
    f"df = pl.LazyFrame.group_by(df, 'segment').{_AGG}",
    "df = pl.DataFrame.sort(df, 'premium')",
    # A materialising method taken as a value is recorded where it is bound.
    f"g = df.group_by\ndf = g('segment').{_AGG}",
    "s = df.sort\ndf = s('premium')",
]


@pytest.mark.parametrize("code", _FRAME_RECEIVER_CODES)
def test_group_by_on_a_frame_receiver_is_a_materialisation_boundary(code: str) -> None:
    with pytest.raises(GroupByExecutionUnsupportedError):
        plan_execution_strategy(
            ProjectionRequest(
                graph=_receiver_graph(code),
                target_node_id="out",
                profile=ExecutionProfile.LAZY_SINK,
            ),
            materialisation_estimate=None,
        )


def test_prepared_planner_without_edges_detects_a_parent_label_frame_receiver() -> None:
    graph = _receiver_graph(
        "df = claims.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
    )
    prepared = prepare_graph(graph, "out", source="live")
    children: dict[str, list[str]] = {node_id: [] for node_id in prepared.order}
    for child_id, parents in prepared.parents_of.items():
        for parent_id in parents:
            children[parent_id].append(child_id)

    with pytest.raises(GroupByExecutionUnsupportedError):
        plan_prepared_execution_strategy(
            prepared.order,
            children,
            prepared.node_map,
            profile=ExecutionProfile.LAZY_SINK,
            materialisation_estimate=None,
        )


def _context(
    profile: ExecutionProfile,
    *,
    memory_limit_bytes: int = 100,
    headroom_bytes: int | None = 100,
) -> ExecutionContext:
    admission = ExecutionAdmission(
        operation="test",
        profile=profile,
        memory_limit_bytes=memory_limit_bytes,
        rss_at_admission_bytes=10,
        rss_limit_bytes=None if headroom_bytes is None else 10 + headroom_bytes,
        headroom_bytes=headroom_bytes,
        config_key="test",
    )
    return ExecutionContext(operation="test", profile=profile, admission=admission)


def test_group_by_in_chunk_suffix_is_rejected_as_a_physical_plan_constraint() -> None:
    with pytest.raises(ChunkPlanUnsupportedError, match="row-local"):
        chunk_plan(
            ChunkPlanRequest(
                graph=_group_by_graph(),
                target_node_id="out",
                chunk_size=10,
            )
        )


def test_group_by_is_allowed_in_a_pre_chunk_materialisation_prefix() -> None:
    plan = chunk_plan(
        ChunkPlanRequest(
            graph=_group_by_graph(),
            target_node_id="out",
            chunk_start_node_id="agg",
            chunk_size=10,
        )
    )

    assert plan.pre_chunk_node_ids == ("source",)
    assert plan.chunk_node_ids == ("agg", "out")
    assert plan.chunk_start_node_id == "agg"


def test_automatic_group_by_estimate_targets_the_boundary_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import execution

    estimated_nodes: list[str] = []

    def estimate(
        _graph,
        node_ids: Iterable[str],
        *,
        source: str,
        edge_demands,
        runtime_source_frames_by_node=None,
        boundary_operators=None,
    ) -> Iterable[tuple[str, MaterialisationEstimate]]:
        assert source == "live"
        assert edge_demands
        assert runtime_source_frames_by_node is None
        # The planner names the boundary operator so the estimator can apply
        # that operator's measured memory factor.
        assert boundary_operators == {"agg": ("group_by",)}
        requested = list(node_ids)
        estimated_nodes.extend(requested)
        return [(node_id, MaterialisationEstimate.available(0)) for node_id in requested]

    monkeypatch.setattr(execution, "estimate_materialisation_boundaries", estimate)

    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_group_by_graph(),
            target_node_id="out",
            profile=ExecutionProfile.PREVIEW_EAGER,
        ),
        execution_context=_context(ExecutionProfile.PREVIEW_EAGER),
    )

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert estimated_nodes == ["agg"]


@pytest.mark.parametrize(
    "profile",
    list(ExecutionProfile),
)
@pytest.mark.parametrize(
    ("context", "estimate", "reason"),
    [
        (None, MaterialisationEstimate.available(0), "execution_admission_unavailable"),
        (
            _context(ExecutionProfile.PREVIEW_EAGER, headroom_bytes=None),
            MaterialisationEstimate.available(0),
            "execution_admission_unavailable",
        ),
        (
            _context(ExecutionProfile.PREVIEW_EAGER),
            MaterialisationEstimate.unavailable("metadata_unavailable"),
            "materialisation_estimate_unavailable",
        ),
        (
            _context(ExecutionProfile.PREVIEW_EAGER),
            MaterialisationEstimate.available(101),
            "materialisation_exceeds_headroom",
        ),
    ],
)
def test_group_by_eligible_profiles_use_stable_rejection_precedence(
    profile: ExecutionProfile,
    context: ExecutionContext | None,
    estimate: MaterialisationEstimate,
    reason: str,
) -> None:
    if context is not None:
        context.profile = profile
        assert context.admission is not None
        object.__setattr__(context.admission, "profile", profile)
    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        plan_execution_strategy(
            ProjectionRequest(
                graph=_group_by_graph(),
                target_node_id="out",
                profile=profile,
            ),
            execution_context=context,
            materialisation_estimate=estimate,
        )

    assert error.value.reason_code == reason


@pytest.mark.parametrize("estimated", [0, 99, 100])
@pytest.mark.parametrize(
    "profile",
    list(ExecutionProfile),
)
def test_group_by_admits_only_an_estimated_materialisation_boundary(
    profile: ExecutionProfile,
    estimated: int,
) -> None:
    context = _context(profile)
    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_group_by_graph(),
            target_node_id="out",
            profile=profile,
        ),
        execution_context=context,
        materialisation_estimate=MaterialisationEstimate.available(estimated),
    )

    assert context.projection_plan is result
    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.status is ExecutionStrategyStatus.BOUNDARY
    assert result.projection_plan.materialisation_boundaries == frozenset({"agg"})
    assert result.projection_plan.opaque_boundaries == frozenset()
    assert result.diagnostic.estimated_peak_bytes == estimated
    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    group_by_boundary = next(
        item for item in result.diagnostic.boundaries.items if item["node_id"] == "agg"
    )
    assert group_by_boundary["boundary_kind"] == "materialisation-boundary"
    assert result.diagnostic.boundaries.total_count == 1


def test_group_by_strategy_keeps_an_unprovable_api_port_boundary_visible() -> None:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "Quote_Input_1",
                        "nodeType": "apiInput",
                        "config": {
                            "path": "quotes.json",
                            "tables": [
                                {
                                    "label": "claims",
                                    "path": "$[:].claims[:]",
                                    "emit": True,
                                    "columns": [
                                        {"name": "quote_id", "selected": True},
                                    ],
                                }
                            ],
                        },
                    },
                },
                {
                    "id": "claims_agg",
                    "data": {
                        "label": "claims_agg",
                        "nodeType": "polars",
                        "config": {
                            "contract": "opaque",
                            "code": "df = claims.group_by('missing').agg()",
                        },
                    },
                },
            ],
            "edges": [make_edge("api", "claims_agg", source_handle="claims").model_dump()],
        }
    )

    result = plan_execution_strategy(
        ProjectionRequest(
            graph=graph,
            target_node_id="claims_agg",
            profile=ExecutionProfile.PREVIEW_EAGER,
        ),
        execution_context=_context(ExecutionProfile.PREVIEW_EAGER),
        materialisation_estimate=MaterialisationEstimate.available(10),
    )

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.diagnostic.blocking_node_id == "claims_agg"
    assert result.diagnostic.boundaries.total_count == 2
    assert {
        (item["node_id"], item["boundary_kind"]) for item in result.diagnostic.boundaries.items
    } == {
        ("api", "unprojected-streaming-boundary"),
        ("claims_agg", "materialisation-boundary"),
    }


def _single_source_graph(path: Path):
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                }
            ],
            "edges": [],
        }
    )


def test_preview_entry_point_records_a_strategy_for_seedless_execution(tmp_path: Path) -> None:
    path = tmp_path / "preview.parquet"
    pl.DataFrame({"x": [1], "unused": [2]}).write_parquet(path)
    context = _context(ExecutionProfile.PREVIEW_EAGER)

    execute_graph(
        _single_source_graph(path),
        target_node_id="source",
        row_limit=1,
        target_preview_only=True,
        execution_context=context,
    )

    assert context.projection_plan is not None
    assert context.projection_plan.profile == ExecutionProfile.PREVIEW_EAGER.value
    metrics = context.metrics_payload(status="completed")
    assert metrics["execution_strategy"]["schema_version"] == 1
    assert metrics["execution_strategy"]["status"] in {"projected", "admitted_eager"}
    assert metrics["execution_strategy"]["remediation"]
    assert metrics["streamability"] in {"streaming", "materialising"}


def test_trace_entry_point_records_a_strategy_before_materialising(tmp_path: Path) -> None:
    path = tmp_path / "trace.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    context = _context(ExecutionProfile.PREVIEW_EAGER)

    execute_trace(
        _single_source_graph(path),
        target_node_id="source",
        row_limit=1,
        execution_context=context,
    )

    assert context.projection_plan is not None
    assert context.projection_plan.profile == ExecutionProfile.PREVIEW_EAGER.value


def test_lazy_entry_point_records_a_strategy_without_a_projection_seed(tmp_path: Path) -> None:
    path = tmp_path / "lazy.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    context = _context(ExecutionProfile.LAZY_SINK)

    execute_lazy_graph(
        _single_source_graph(path),
        _build_node_fn,
        target_node_id="source",
        execution_context=context,
    )

    assert context.projection_plan is not None
    assert context.projection_plan.profile == ExecutionProfile.LAZY_SINK.value


def test_lazy_source_reports_requested_and_physically_scanned_width(tmp_path: Path) -> None:
    path = tmp_path / "projected.parquet"
    pl.DataFrame({"x": [1], "unused": [2]}).write_parquet(path)
    context = _context(ExecutionProfile.LAZY_SINK)

    execute_lazy_graph(
        _single_source_graph(path),
        _build_node_fn,
        target_node_id="source",
        required_columns_by_node={"source": {"x"}},
        execution_context=context,
    )

    widths = context.metrics_payload()["column_widths"]
    source = next(item for item in widths["items"] if item["node_id"] == "source")
    assert source["requested_width"] == 1
    assert source["physically_scanned_width"] == 1


def test_strategy_provenance_snapshots_one_shot_projection_seeds(tmp_path: Path) -> None:
    path = tmp_path / "one-shot-seed.parquet"
    pl.DataFrame({"x": [1], "unused": [2]}).write_parquet(path)

    result = plan_execution_strategy(
        ProjectionRequest(
            graph=_single_source_graph(path),
            target_node_id="source",
            profile=ExecutionProfile.LAZY_SINK,
            required_columns_by_node={"source": (column for column in ["x"])},
        )
    )

    assert result.needed_by_node["source"] == frozenset({"x"})
    assert {
        (item["column"], item["origin_kind"], item["source_node_id"])
        for item in result.diagnostic.provenance.items
    } >= {("x", "seed", "source")}


# ---------------------------------------------------------------------------
# Provable Polars shapes admit a boundary identically across every profile
# ---------------------------------------------------------------------------

_GROUP_BY_PREMIUM = "df = df.group_by('segment').agg(pl.col('premium').sum().alias('premium'))"
_GROUP_BY_VALUE = "df = df.group_by('segment').agg(pl.col('value').sum().alias('total'))"

_PROVABLE_SHAPES: tuple[tuple[str, str, str, str], ...] = (
    ("control_filter", "df = df.filter(pl.col('premium') > 0)", _GROUP_BY_PREMIUM, "premium"),
    ("drop", "df = df.drop('extra')", _GROUP_BY_PREMIUM, "premium"),
    ("drop_nulls_subset", "df = df.drop_nulls(subset=['premium'])", _GROUP_BY_PREMIUM, "premium"),
    ("drop_nulls", "df = df.drop_nulls()", _GROUP_BY_PREMIUM, "premium"),
    ("with_row_index", "df = df.with_row_index('row_id')", _GROUP_BY_PREMIUM, "premium"),
    ("str_contains", "df = df.filter(pl.col('s').str.contains('x'))", _GROUP_BY_PREMIUM, "premium"),
    (
        "dt_truncate",
        "df = df.with_columns(pl.col('t').dt.truncate('1mo').alias('month'))",
        _GROUP_BY_PREMIUM,
        "premium",
    ),
    (
        "literal_unpivot",
        "df = df.unpivot(on=['premium', 'extra'], index=['segment'])",
        _GROUP_BY_VALUE,
        "total",
    ),
)

_SHAPE_PARAMS = [
    pytest.param(shape[1], shape[2], shape[3], id=shape[0]) for shape in _PROVABLE_SHAPES
]


def _write_shape_source(path: Path) -> None:
    rows = 20
    pl.DataFrame(
        {
            "segment": [f"seg-{index % 4}" for index in range(rows)],
            "premium": [None if index % 7 == 0 else float(index) for index in range(rows)],
            "extra": list(range(rows)),
            "s": [
                None if index % 5 == 0 else f"a{'x' if index % 2 else 'y'}{index}"
                for index in range(rows)
            ],
            "t": [
                None if index % 6 == 0 else date(2024, 1 + (index % 12), 1 + (index % 28))
                for index in range(rows)
            ],
        },
        schema={
            "segment": pl.String,
            "premium": pl.Float64,
            "extra": pl.Int64,
            "s": pl.String,
            "t": pl.Date,
        },
    ).write_parquet(str(path))


def _shape_group_by_graph(
    path: Path,
    transform_code: str,
    group_by_code: str,
    output_column: str,
):
    _write_shape_source(path)
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                },
                {
                    "id": "shape",
                    "data": {
                        "label": "shape",
                        "nodeType": "polars",
                        "config": {"code": transform_code},
                    },
                },
                {
                    "id": "agg",
                    "data": {
                        "label": "agg",
                        "nodeType": "polars",
                        "config": {"code": group_by_code},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "agg",
                                    "source_column": output_column,
                                    "output_path": f"$[:].{output_column}",
                                    "enabled": True,
                                }
                            ]
                        },
                    },
                },
            ],
            "edges": [
                make_edge("source", "shape").model_dump(),
                make_edge("shape", "agg").model_dump(),
                make_edge("agg", "out").model_dump(),
            ],
        }
    )


def _plan_shape(profile: ExecutionProfile, graph):
    return plan_execution_strategy(
        ProjectionRequest(
            graph=graph,
            target_node_id="out",
            profile=profile,
        ),
        execution_context=_context(
            profile,
            memory_limit_bytes=1 << 30,
            headroom_bytes=1 << 30,
        ),
    )


@pytest.mark.parametrize("profile", list(ExecutionProfile))
@pytest.mark.parametrize(("transform_code", "group_by_code", "output_column"), _SHAPE_PARAMS)
def test_provable_shapes_admit_a_real_estimated_boundary_on_every_profile(
    tmp_path: Path,
    profile: ExecutionProfile,
    transform_code: str,
    group_by_code: str,
    output_column: str,
) -> None:
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet", transform_code, group_by_code, output_column
    )

    result = _plan_shape(profile, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.status is ExecutionStrategyStatus.BOUNDARY
    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    assert isinstance(result.diagnostic.estimated_peak_bytes, int)
    assert result.diagnostic.estimated_peak_bytes > 0


@pytest.mark.parametrize(("transform_code", "group_by_code", "output_column"), _SHAPE_PARAMS)
def test_provable_shape_diagnostics_differ_only_in_the_configured_profile(
    tmp_path: Path,
    transform_code: str,
    group_by_code: str,
    output_column: str,
) -> None:
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet", transform_code, group_by_code, output_column
    )

    payloads = {}
    for profile in ExecutionProfile:
        payload = _plan_shape(profile, graph).diagnostic.to_dict()
        assert payload.pop("profile") == profile.value
        payloads[profile] = payload

    distinct = list(payloads.values())
    assert all(payload == distinct[0] for payload in distinct)


def _plan_group_by(
    profile: ExecutionProfile,
    *,
    context: ExecutionContext | None,
    estimate: MaterialisationEstimate | None,
):
    return plan_execution_strategy(
        ProjectionRequest(
            graph=_group_by_graph(),
            target_node_id="out",
            profile=profile,
        ),
        execution_context=context,
        materialisation_estimate=estimate,
    )


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_unavailable_estimate_runs_conservatively_under_a_native_cap(
    profile: ExecutionProfile,
) -> None:
    context = _context(profile)
    with native_memory_backend_scope("rlimit"):
        result = _plan_group_by(
            profile,
            context=context,
            estimate=MaterialisationEstimate.unavailable("metadata_unavailable"),
        )

    assert context.projection_plan is result
    assert result.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert result.status is ExecutionStrategyStatus.WARNED
    assert result.diagnostic.boundedness is ExecutionBoundedness.UNBOUNDED
    assert result.diagnostic.reason_code == "materialisation_estimate_unavailable_conservative"
    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    assert result.diagnostic.headroom_bytes == 100
    assert result.diagnostic.estimated_peak_bytes is None
    assert result.diagnostic.raw_estimated_peak_bytes is None
    assert result.diagnostic.estimate_calibration_factor_basis_points is None
    assert result.diagnostic.estimate_admission_basis is None
    assert result.projection_plan.materialisation_boundaries == frozenset({"agg"})
    assert tuple(result.diagnostic.assumptions) == (
        "proof_gap=metadata_unavailable",
        "reserved_envelope_bytes=100",
        "hard_cap_backend=rlimit",
        "disabled_optimisations=estimate_based_admission",
    )
    remediation = result.diagnostic.remediation
    assert remediation is not None
    assert "metadata_unavailable" in remediation
    assert len(remediation) <= 512
    payload = ExecutionStrategyDiagnosticPayload.model_validate(result.diagnostic.to_dict())
    assert payload.status == "warned"


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_absent_estimate_runs_conservatively_under_a_native_cap(
    profile: ExecutionProfile,
) -> None:
    with native_memory_backend_scope("rlimit"):
        result = _plan_group_by(profile, context=_context(profile), estimate=None)

    assert result.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert result.status is ExecutionStrategyStatus.WARNED
    # ``plan_execution_strategy`` derives the estimate itself when the caller
    # supplies none, so the proof gap is the estimator's, not "not requested".
    assert "proof_gap=materialisation_estimate_not_supplied" in result.diagnostic.assumptions


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_unavailable_estimate_without_a_native_cap_is_rejected(
    profile: ExecutionProfile,
) -> None:
    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_group_by(
            profile,
            context=_context(profile),
            estimate=MaterialisationEstimate.unavailable("metadata_unavailable"),
        )

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert "metadata_unavailable" in error.value.remediation
    assert "without a hard worker memory cap" in error.value.remediation


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_a_native_cap_does_not_admit_other_group_by_rejections(
    profile: ExecutionProfile,
) -> None:
    with native_memory_backend_scope("rlimit"):
        with pytest.raises(GroupByExecutionUnsupportedError) as missing_admission:
            _plan_group_by(
                profile,
                context=None,
                estimate=MaterialisationEstimate.unavailable("metadata_unavailable"),
            )
        with pytest.raises(GroupByExecutionUnsupportedError) as too_large:
            _plan_group_by(
                profile,
                context=_context(profile),
                estimate=MaterialisationEstimate.available(101),
            )

    assert missing_admission.value.reason_code == "execution_admission_unavailable"
    assert too_large.value.reason_code == "materialisation_exceeds_headroom"


def test_real_estimator_gap_is_conservative_under_a_cap_and_rejected_without_one(
    tmp_path: Path,
) -> None:
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet",
        "df = df.unpivot(index=['segment'])",
        _GROUP_BY_VALUE,
        "total",
    )

    with native_memory_backend_scope("rlimit"):
        result = _plan_shape(ExecutionProfile.PREVIEW_EAGER, graph)

    assert result.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert result.status is ExecutionStrategyStatus.WARNED
    proof_gap = next(
        item for item in result.diagnostic.assumptions if item.startswith("proof_gap=")
    )
    assert "dynamic_unpivot" in proof_gap

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_shape(ExecutionProfile.PREVIEW_EAGER, graph)

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert "dynamic_unpivot" in error.value.remediation


_NEW_BOUNDARY_SHAPES: tuple[tuple[str, str], ...] = (
    ("sort", "df = df.sort('premium')"),
    ("unique", "df = df.unique(subset=['segment'])"),
    ("reverse", "df = df.reverse()"),
    ("top_k", "df = df.top_k(5, by='premium')"),
    ("bottom_k", "df = df.bottom_k(5, by='premium')"),
    (
        "over",
        "df = df.with_columns(pl.col('premium').sum().over('segment').alias('segment_total'))",
    ),
)


@pytest.mark.parametrize("profile", list(ExecutionProfile))
@pytest.mark.parametrize(
    ("operator", "transform_code"),
    [pytest.param(op, code, id=op) for op, code in _NEW_BOUNDARY_SHAPES],
)
def test_global_operations_plan_an_estimated_materialisation_boundary(
    tmp_path: Path,
    profile: ExecutionProfile,
    operator: str,
    transform_code: str,
) -> None:
    """EXEC-P07: every measured materialising operator is an admitted boundary."""
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet", transform_code, _GROUP_BY_PREMIUM, "premium"
    )

    result = _plan_shape(profile, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.status is ExecutionStrategyStatus.BOUNDARY
    assert result.diagnostic.reason_code == "materialisation_admitted"
    assert result.diagnostic.blocking_node_id == "shape"
    assert result.diagnostic.blocking_operator == operator
    assert isinstance(result.diagnostic.estimated_peak_bytes, int)
    assert result.diagnostic.estimated_peak_bytes > 0
    assert "shape" in result.projection_plan.materialisation_boundaries


@pytest.mark.parametrize(
    ("operator", "transform_code"),
    [pytest.param(op, code, id=op) for op, code in _NEW_BOUNDARY_SHAPES],
)
def test_global_operation_boundary_plans_differ_only_in_the_configured_profile(
    tmp_path: Path,
    operator: str,
    transform_code: str,
) -> None:
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet", transform_code, _GROUP_BY_PREMIUM, "premium"
    )

    payloads = []
    for profile in ExecutionProfile:
        payload = _plan_shape(profile, graph).diagnostic.to_dict()
        assert payload.pop("profile") == profile.value
        payloads.append(payload)

    assert all(payload == payloads[0] for payload in payloads)


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_explode_is_conservative_under_a_cap_and_rejected_without_one(
    tmp_path: Path,
    profile: ExecutionProfile,
) -> None:
    """``explode`` expands rows by an unbounded factor, so it has no estimate."""
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet", "df = df.explode('l')", _GROUP_BY_PREMIUM, "premium"
    )

    with native_memory_backend_scope("rlimit"):
        capped = _plan_shape(profile, graph)

    assert capped.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert capped.status is ExecutionStrategyStatus.WARNED
    assert capped.diagnostic.blocking_operator == "explode"
    assert any("row_expansion_unbounded" in item for item in capped.diagnostic.assumptions)

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_shape(profile, graph)

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert error.value.operator == "explode"
    assert "row_expansion_unbounded" in error.value.remediation


def test_an_expression_method_named_like_a_frame_boundary_is_not_a_boundary(
    tmp_path: Path,
) -> None:
    """EXEC-P04's receiver rule survives: ``pl.col(...).list.sort()`` is an expression."""
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet",
        "df = df.with_columns(pl.col('l').list.sort().alias('l'))",
        _GROUP_BY_PREMIUM,
        "premium",
    )

    result = _plan_shape(ExecutionProfile.PREVIEW_EAGER, graph)

    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    assert "shape" not in result.projection_plan.materialisation_boundaries


@pytest.mark.parametrize(
    ("operator", "transform_code"),
    [
        ("shift", "df = df.shift(1)"),
        ("unpivot", "df = df.unpivot(on=['premium', 'extra'], index=['segment'])"),
    ],
)
def test_measured_streaming_operations_do_not_become_boundaries(
    tmp_path: Path,
    operator: str,
    transform_code: str,
) -> None:
    graph = _shape_group_by_graph(
        tmp_path / "rows.parquet",
        transform_code,
        "df = df.group_by('segment').agg(pl.col('value').sum().alias('total'))"
        if operator == "unpivot"
        else _GROUP_BY_PREMIUM,
        "total" if operator == "unpivot" else "premium",
    )

    with native_memory_backend_scope("rlimit"):
        result = _plan_shape(ExecutionProfile.PREVIEW_EAGER, graph)

    assert result.diagnostic.blocking_node_id == "agg"
    assert result.diagnostic.blocking_operator == "group_by"
    assert "shape" not in result.projection_plan.materialisation_boundaries


# ------------------------------------------------- EXEC-P07 cross joins (#3)


def _two_source_graph(left_path: Path, right_path: Path, code: str):
    return make_graph(
        {
            "nodes": [
                {
                    "id": name,
                    "data": {
                        "label": name,
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(path),
                    },
                }
                for name, path in (("left", left_path), ("right", right_path))
            ]
            + [
                {
                    "id": "op",
                    "data": {"label": "op", "nodeType": "polars", "config": {"code": code}},
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["premium"], source_port="op"),
                    },
                },
            ],
            "edges": [
                make_edge("left", "op").model_dump(),
                make_edge("right", "op").model_dump(),
                make_edge("op", "out").model_dump(),
            ],
        }
    )


def _write_two_join_sources(tmp_path: Path) -> tuple[Path, Path]:
    left_path = tmp_path / "left.parquet"
    right_path = tmp_path / "right.parquet"
    _write_shape_source(left_path)
    _write_shape_source(right_path)
    return left_path, right_path


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_cross_join_is_conservative_under_a_cap_and_rejected_without_one(
    tmp_path: Path,
    profile: ExecutionProfile,
) -> None:
    """A cross join is a boundary, but its peak was never measured."""
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(left_path, right_path, "df = left.join(right, how='cross')")

    with native_memory_backend_scope("rlimit"):
        capped = _plan_shape(profile, graph)

    assert capped.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert capped.status is ExecutionStrategyStatus.WARNED
    assert capped.diagnostic.blocking_operator == "join"
    assert any("cross_join_unmeasured" in item for item in capped.diagnostic.assumptions)

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_shape(profile, graph)

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert "cross_join_unmeasured" in error.value.remediation


_MANY_TO_MANY_JOIN_REMEDIATION = (
    "The join has no declared validate= contract, so only the many-to-many row "
    "product bounds it; declare validate='m:1', '1:m', or '1:1' where a key side "
    "is unique to get a real estimate."
)


def _plan_shape_with_headroom(profile: ExecutionProfile, graph, headroom_bytes: int):
    return plan_execution_strategy(
        ProjectionRequest(graph=graph, target_node_id="out", profile=profile),
        execution_context=_context(
            profile,
            memory_limit_bytes=headroom_bytes,
            headroom_bytes=headroom_bytes,
        ),
    )


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_an_undeclared_join_over_headroom_is_conservative_or_rejected(
    tmp_path: Path,
    profile: ExecutionProfile,
) -> None:
    """The row product is the absence of an estimate, not an over-run of one."""
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(left_path, right_path, "df = left.join(right, on='segment')")

    with native_memory_backend_scope("rlimit"):
        capped = _plan_shape_with_headroom(profile, graph, 1024)

    assert capped.strategy is ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    assert capped.status is ExecutionStrategyStatus.WARNED
    assert capped.diagnostic.blocking_operator == "join"
    assert "proof_gap=op:join_cardinality_many_to_many" in capped.diagnostic.assumptions
    assert capped.diagnostic.remediation.endswith(_MANY_TO_MANY_JOIN_REMEDIATION)

    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        _plan_shape_with_headroom(profile, graph, 1024)

    assert error.value.reason_code == "materialisation_estimate_unavailable"
    assert "op:join_cardinality_many_to_many" in error.value.remediation
    assert error.value.remediation.endswith(_MANY_TO_MANY_JOIN_REMEDIATION)


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_an_undeclared_join_within_headroom_is_still_admitted(
    tmp_path: Path,
    profile: ExecutionProfile,
) -> None:
    """The product is an over-estimate in the safe direction, so it admits."""
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(left_path, right_path, "df = left.join(right, on='segment')")

    result = _plan_shape(profile, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.diagnostic.reason_code == "materialisation_admitted"
    assert result.diagnostic.blocking_operator == "join"


@pytest.mark.parametrize("profile", list(ExecutionProfile))
def test_measured_join_kinds_still_plan_an_estimated_boundary(
    tmp_path: Path,
    profile: ExecutionProfile,
) -> None:
    """The cross-join gap must not withdraw the measured joins' admission."""
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(
        left_path, right_path, "df = left.join(right, on='segment', how='left', validate='m:1')"
    )

    result = _plan_shape(profile, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.diagnostic.blocking_operator == "join"
    assert isinstance(result.diagnostic.estimated_peak_bytes, int)
    assert result.diagnostic.estimated_peak_bytes > 0


# ------------------------------------ EXEC-P07 boundary-table coverage (#6)


_MULTI_INPUT_BOUNDARY_SHAPES: tuple[tuple[str, str], ...] = (
    ("join", "df = left.join(right, on='segment', how='left', validate='m:1')"),
    ("join_asof", "df = left.join_asof(right, on='t')"),
)


@pytest.mark.parametrize("profile", list(ExecutionProfile))
@pytest.mark.parametrize(
    ("operator", "code"),
    [pytest.param(op, code, id=op) for op, code in _MULTI_INPUT_BOUNDARY_SHAPES],
)
def test_multi_input_boundaries_plan_an_estimated_boundary_on_every_profile(
    tmp_path: Path,
    profile: ExecutionProfile,
    operator: str,
    code: str,
) -> None:
    """Multi-input boundaries belong in the positive cross-profile table too."""
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(left_path, right_path, code)

    result = _plan_shape(profile, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.status is ExecutionStrategyStatus.BOUNDARY
    assert result.diagnostic.blocking_node_id == "op"
    assert result.diagnostic.blocking_operator == operator
    assert isinstance(result.diagnostic.estimated_peak_bytes, int)
    assert result.diagnostic.estimated_peak_bytes > 0


def test_the_positive_boundary_tables_cover_every_registered_boundary_method() -> None:
    """The tables cannot silently miss an operator the registry admits."""
    from haute._polars_operations import materialising_frame_methods

    covered = (
        {operator for operator, _code in _NEW_BOUNDARY_SHAPES}
        | {operator for operator, _code in _MULTI_INPUT_BOUNDARY_SHAPES}
        # ``group_by``/``groupby`` have their own suite above; ``explode`` has no
        # estimate and is covered by its conservative/rejected test.
        | {"group_by", "groupby", "explode"}
    )
    assert materialising_frame_methods() <= covered


# --------------------------- EXEC-P07 nested-argument boundary costing (#3)


@pytest.mark.parametrize(
    ("code", "first_operator", "operators", "factor"),
    [
        pytest.param(
            "df = left.join_asof(right.unique(subset=['t']), on='t')",
            "unique",
            "unique,join_asof",
            350,
            id="inner_factor_higher_than_the_outer_call",
        ),
        pytest.param(
            "df = left.join_asof(right.top_k(5, by='t'), on='t')",
            "top_k",
            "top_k,join_asof",
            250,
            id="outer_factor_higher_than_the_inner_argument",
        ),
        pytest.param(
            "df = left.unique(subset=['segment']).join(right, on='segment', how='left',"
            " validate='m:1')",
            "unique",
            "unique,join",
            350,
            id="high_factor_on_the_receiver",
        ),
    ],
)
def test_a_nested_boundary_argument_is_blamed_first_and_costed_at_the_maximum(
    tmp_path: Path,
    code: str,
    first_operator: str,
    operators: str,
    factor: int,
) -> None:
    """A boundary nested in an argument runs before the call that receives it.

    ``join`` (150) never hides a heavier inner operator: the diagnostic blames
    the first operator evaluated, and the estimate carries the largest factor of
    the whole chain.
    """
    left_path, right_path = _write_two_join_sources(tmp_path)
    graph = _two_source_graph(left_path, right_path, code)

    result = _plan_shape(ExecutionProfile.PREVIEW_EAGER, graph)

    assert result.strategy is ExecutionStrategy.MATERIALISATION_BOUNDARY
    assert result.diagnostic.blocking_node_id == "op"
    assert result.diagnostic.blocking_operator == first_operator
    assumptions = list(result.diagnostic.assumptions)
    assert f"op: boundary_operator={first_operator}" in assumptions
    assert f"op: boundary_operators={operators}" in assumptions
    assert f"op: materialisation_factor_basis_points={factor}" in assumptions

    raw = result.diagnostic.raw_estimated_peak_bytes
    assert isinstance(raw, int) and raw > 0
    # The recorded factor is the one actually applied to the estimate.
    [(_, unfactored)] = list(
        estimate_materialisation_boundaries(graph, ["op"], boundary_operators={"op": ()})
    )
    assert unfactored.estimated_peak_bytes is not None
    assert raw == (unfactored.estimated_peak_bytes * factor + 99) // 100
