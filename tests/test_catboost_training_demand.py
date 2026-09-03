from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionProfile
from haute._types import ModellingConfig
from haute.modelling._training_job import TrainingJob
from haute.routes import _training_preparation as training_preparation
from haute.routes._train_service import _training_required_columns_by_node
from haute.routes._training_preparation import (
    TrainingPreparationRequest,
    prepare_training_data,
)
from haute.schemas import TrainRequest
from tests.conftest import make_graph


def _all_except_parts(demand: object) -> tuple[frozenset[str], frozenset[str]]:
    """Return structural AllExcept demand fields without pinning its module."""
    assert type(demand).__name__ == "AllExcept"
    required = getattr(demand, "required_columns", None)
    if required is None:
        required = getattr(demand, "include_columns", None)
    excluded = getattr(demand, "exclude_columns", None)
    if excluded is None:
        excluded = getattr(demand, "excluded_columns", None)
    assert required is not None
    assert excluded is not None
    return frozenset(required), frozenset(excluded)


def _training_request(config: dict[str, Any]) -> TrainRequest:
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "train",
                    "data": {
                        "label": "train",
                        "nodeType": "modelling",
                        "config": config,
                    },
                }
            ],
            "edges": [],
        }
    )
    return TrainRequest(graph=graph, node_id="train")


def test_modelling_config_schema_declares_explicit_feature_columns() -> None:
    assert "feature_columns" in ModellingConfig.__annotations__


def test_catboost_explicit_feature_columns_yield_exact_training_projection_seed() -> None:
    seed = _training_required_columns_by_node(
        "train",
        {
            "algorithm": "catboost",
            "target": "claim_count",
            "feature_columns": ["driver_age", "territory", "vehicle_age"],
            "exclude": ["policy_id", "debug_payload"],
            "weight": "exposure",
            "offset": "log_exposure",
            "evaluation": {
                "schema_version": 1,
                "strategy": "group",
                "group_column": "household_id",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "id_columns": ["quote_id"],
        },
    )

    assert seed == {
        "train": frozenset(
            {
                "claim_count",
                "driver_age",
                "territory",
                "vehicle_age",
                "exposure",
                "log_exposure",
                "household_id",
                "quote_id",
            }
        )
    }


def test_catboost_without_feature_columns_yields_all_except_training_demand() -> None:
    demand_by_node = _training_required_columns_by_node(
        "train",
        {
            "algorithm": "catboost",
            "target": "claim_count",
            "exclude": ["policy_id", "debug_payload"],
            "weight": "exposure",
            "offset": "log_exposure",
            "evaluation": {
                "schema_version": 1,
                "strategy": "temporal",
                "date_column": "quote_date",
                "validation": {
                    "method": "cross_validation",
                    "fold_count": 3,
                    "window": "expanding",
                },
            },
            "fold_column": "fold_id",
            "id_columns": ["quote_id", "customer_id"],
        },
    )

    assert demand_by_node is not None
    required, excluded = _all_except_parts(demand_by_node["train"])
    assert required == frozenset(
        {
            "claim_count",
            "exposure",
            "log_exposure",
            "quote_date",
            "fold_id",
            "quote_id",
            "customer_id",
        }
    )
    assert excluded == frozenset(
        {
            "claim_count",
            "exposure",
            "log_exposure",
            "quote_date",
            "fold_id",
            "quote_id",
            "customer_id",
            "policy_id",
            "debug_payload",
        }
    )


def test_catboost_schema_derived_features_exclude_metadata_and_keep_categorical_order(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "training.parquet"
    pl.DataFrame(
        {
            "driver_age": [31, 42, 53],
            "territory": ["north", "south", "north"],
            "claim_count": [0.0, 1.0, 0.0],
            "exposure": [1.0, 0.5, 1.0],
            "vehicle_age": [2.0, 7.0, 4.0],
            "vehicle_type": ["car", "van", "car"],
            "log_exposure": [0.0, -0.69, 0.0],
            "household_id": ["h1", "h1", "h2"],
            "fold_id": [0, 1, 0],
            "quote_id": ["q1", "q2", "q3"],
            "debug_payload": ["wide-a", "wide-b", "wide-c"],
        }
    ).write_parquet(data_path)

    prepared = TrainingJob(
        name="catboost_all_except",
        data=str(data_path),
        target="claim_count",
        weight="exposure",
        offset="log_exposure",
        exclude=["debug_payload", "fold_id", "quote_id"],
        evaluation={
            "schema_version": 1,
            "strategy": "group",
            "group_column": "household_id",
            "seed": 42,
            "validation": {"method": "single", "size": 0.2},
        },
        algorithm="catboost",
    )._prepare_data(lambda _msg, _frac: None)

    assert prepared.features == [
        "driver_age",
        "territory",
        "vehicle_age",
        "vehicle_type",
    ]
    assert prepared.cat_features == ["territory", "vehicle_type"]


def test_catboost_schema_features_exclude_fold_and_id_metadata_without_manual_exclude(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "training_with_metadata.parquet"
    pl.DataFrame(
        {
            "driver_age": [31, 42, 53],
            "territory": ["north", "south", "north"],
            "claim_count": [0.0, 1.0, 0.0],
            "fold_id": [0, 1, 0],
            "quote_id": ["q1", "q2", "q3"],
        }
    ).write_parquet(data_path)

    prepared = TrainingJob(
        name="catboost_metadata",
        data=str(data_path),
        target="claim_count",
        algorithm="catboost",
        fold_column="fold_id",
        id_columns=["quote_id"],
    )._prepare_data(lambda _msg, _frac: None)

    assert prepared.features == ["driver_age", "territory"]
    assert prepared.cat_features == ["territory"]


def _preparation_request(
    body: TrainRequest,
    parquet_path: Path,
    **overrides: Any,
) -> TrainingPreparationRequest:
    return TrainingPreparationRequest(
        graph=body.graph,
        node_id="train",
        job_id="job",
        source=body.source,
        parquet_path=str(parquet_path),
        config=dict(body.graph.node_map["train"].data.config),
        project_root=str(parquet_path.parent),
        **overrides,
    )


def _prepare(request: TrainingPreparationRequest) -> Any:
    context = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    try:
        return prepare_training_data(request, execution_context=context)
    finally:
        context.release_admission(preserve_primary_error=True)


def test_missing_target_fails_before_training_sink_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body = _training_request({"algorithm": "catboost", "target": "missing_target"})

    def fake_execute_lazy(*_args: Any, **_kwargs: Any):
        return {"train": pl.DataFrame({"feature": [1.0]}).lazy()}, ["train"], {}, {}

    def fail_bounded_sink(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("training sink write should not run after schema validation fails")

    monkeypatch.setattr(training_preparation, "execute_lazy_graph", fake_execute_lazy)
    monkeypatch.setattr("haute._polars_utils.bounded_sink", fail_bounded_sink)

    parquet_path = tmp_path / "prepared.parquet"
    outcome = _prepare(
        _preparation_request(
            body,
            parquet_path,
            exclude=None,
            keep_columns=["missing_target"],
        )
    )

    failure = outcome.failure
    assert failure is not None
    assert failure.terminal_reason == "contract_error"
    assert failure.http_status_code == 422
    assert "missing_target" in str(failure.http_detail)
    assert not parquet_path.exists()


def test_missing_explicit_feature_fails_before_training_sink_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    body = _training_request(
        {
            "algorithm": "catboost",
            "target": "claim_count",
            "feature_columns": ["driver_age", "missing_feature"],
        }
    )

    def fake_execute_lazy(*_args: Any, **_kwargs: Any):
        return (
            {"train": pl.DataFrame({"claim_count": [1.0], "driver_age": [33.0]}).lazy()},
            ["train"],
            {},
            {},
        )

    def fail_bounded_sink(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("training sink write should not run after schema validation fails")

    monkeypatch.setattr(training_preparation, "execute_lazy_graph", fake_execute_lazy)
    monkeypatch.setattr("haute._polars_utils.bounded_sink", fail_bounded_sink)

    parquet_path = tmp_path / "prepared.parquet"
    outcome = _prepare(
        _preparation_request(
            body,
            parquet_path,
            exclude=None,
            keep_columns=["claim_count"],
            required_columns_by_node={
                "train": frozenset({"claim_count", "driver_age", "missing_feature"})
            },
        )
    )

    failure = outcome.failure
    assert failure is not None
    assert failure.terminal_reason == "contract_error"
    assert failure.http_status_code == 422
    assert "missing_feature" in str(failure.http_detail)
    assert not parquet_path.exists()
