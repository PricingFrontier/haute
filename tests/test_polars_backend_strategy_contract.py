from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from haute._execution_context import ExecutionAdmission, ExecutionContext, ExecutionProfile
from haute._execution_schemas import MAX_JSON_SAFE_INTEGER
from haute._ram_estimate import MaterialisationEstimate
from haute.chunking import ChunkPlanRequest, chunk_plan
from haute.errors import BoundedMemoryUnsupportedError, GroupByExecutionUnsupportedError
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
    make_ready_file_input_config,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_STATUS_BY_STRATEGY = {
    ExecutionStrategy.PROJECTED: ExecutionStrategyStatus.PROJECTED,
    ExecutionStrategy.SCHEMA_ALL_EXCEPT: ExecutionStrategyStatus.PROJECTED,
    ExecutionStrategy.FULL_WIDTH_ADMITTED_EAGER: ExecutionStrategyStatus.ADMITTED_EAGER,
    ExecutionStrategy.UNPROJECTED_STREAMING_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
    ExecutionStrategy.MATERIALISATION_BOUNDARY: ExecutionStrategyStatus.BOUNDARY,
    ExecutionStrategy.UNSUPPORTED: ExecutionStrategyStatus.REJECTED,
    ExecutionStrategy.NOT_PLANNED: ExecutionStrategyStatus.NOT_PLANNED,
}


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


def test_canonical_topological_ranks_use_lexical_tie_breaks() -> None:
    children = {"z": ["out"], "a": ["out"], "out": []}

    expected = {"a": 0, "z": 1, "out": 2}
    assert dict(_canonical_topological_ranks(["z", "a", "out"], children)) == expected
    assert dict(_canonical_topological_ranks(["out", "a", "z"], children)) == expected


def test_non_strict_opaque_fan_out_reports_that_the_seed_cannot_apply() -> None:
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


@pytest.mark.parametrize(
    "profile",
    [
        ExecutionProfile.LAZY_SINK,
        ExecutionProfile.TRAINING_PREP,
        ExecutionProfile.OPTIMISER_SETUP,
        ExecutionProfile.AUTO_RANGE,
        ExecutionProfile.DEPLOY_BATCH,
        ExecutionProfile.CHUNKED_MAP_REDUCE,
    ],
)
def test_group_by_bounded_profiles_reject_before_considering_admission(
    profile: ExecutionProfile,
) -> None:
    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        plan_execution_strategy(
            ProjectionRequest(
                graph=_group_by_graph(),
                target_node_id="out",
                profile=profile,
            ),
            execution_context=_context(profile),
            materialisation_estimate=MaterialisationEstimate.available(0),
        )

    exc = error.value
    assert isinstance(exc, BoundedMemoryUnsupportedError)
    assert exc.reason_code == "profile_requires_bounded_execution"
    assert exc.node_id == "agg"
    assert exc.operator == "group_by"
    assert exc.profile == profile.value
    assert exc.estimated_peak_bytes is None
    assert exc.headroom_bytes is None


def test_group_by_never_enters_the_generic_chunk_runner() -> None:
    with pytest.raises(GroupByExecutionUnsupportedError) as error:
        chunk_plan(
            ChunkPlanRequest(
                graph=_group_by_graph(),
                target_node_id="out",
                chunk_size=10,
            )
        )

    assert error.value.reason_code == "profile_requires_bounded_execution"


def test_automatic_group_by_estimate_targets_the_boundary_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import execution

    estimated_nodes: list[str] = []

    def estimate(
        _graph, node_ids: Iterable[str], *, source: str, edge_demands
    ) -> Iterable[tuple[str, MaterialisationEstimate]]:
        assert source == "live"
        assert edge_demands
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
    [
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.DEPLOY_LIVE,
    ],
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
    [
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.EXPLORE_ANALYSIS,
        ExecutionProfile.DEPLOY_LIVE,
    ],
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
