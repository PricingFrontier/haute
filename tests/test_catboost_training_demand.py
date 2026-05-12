from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import HTTPException

from haute._types import ModellingConfig
from haute.modelling._training_job import TrainingJob
from haute.routes import _train_service as train_service
from haute.routes._job_store import JobStore
from haute.routes._train_service import TrainService, _training_required_columns_by_node
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
            "split": {"strategy": "group", "group_column": "household_id"},
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
            "split": {"strategy": "temporal", "date_column": "quote_date"},
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
        split={"strategy": "group", "group_column": "household_id"},
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


def test_missing_target_fails_before_training_sink_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TrainService(JobStore())
    body = _training_request({"algorithm": "catboost", "target": "missing_target"})
    job_id = service._store.create_job({"status": "running"})

    def fake_execute_lazy(*_args: Any, **_kwargs: Any):
        return {"train": pl.DataFrame({"feature": [1.0]}).lazy()}, ["train"], {}, {}

    def fail_bounded_sink(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("training sink write should not run after schema validation fails")

    monkeypatch.setattr(train_service, "execute_lazy_graph", fake_execute_lazy)
    monkeypatch.setattr("haute._polars_utils.bounded_sink", fail_bounded_sink)

    with pytest.raises(HTTPException) as exc_info:
        service._execute_and_sink(
            body,
            preamble_ns=None,
            row_limit=None,
            job_id=job_id,
            exclude=None,
            keep_columns=["missing_target"],
        )

    assert exc_info.value.status_code == 422
    assert "missing_target" in str(exc_info.value.detail)


def test_missing_explicit_feature_fails_before_training_sink_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = TrainService(JobStore())
    body = _training_request(
        {
            "algorithm": "catboost",
            "target": "claim_count",
            "feature_columns": ["driver_age", "missing_feature"],
        }
    )
    job_id = service._store.create_job({"status": "running"})

    def fake_execute_lazy(*_args: Any, **_kwargs: Any):
        return {
            "train": pl.DataFrame(
                {"claim_count": [1.0], "driver_age": [33.0]}
            ).lazy()
        }, ["train"], {}, {}

    def fail_bounded_sink(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("training sink write should not run after schema validation fails")

    monkeypatch.setattr(train_service, "execute_lazy_graph", fake_execute_lazy)
    monkeypatch.setattr("haute._polars_utils.bounded_sink", fail_bounded_sink)

    with pytest.raises(HTTPException) as exc_info:
        service._execute_and_sink(
            body,
            preamble_ns=None,
            row_limit=None,
            job_id=job_id,
            exclude=None,
            keep_columns=["claim_count"],
            required_columns_by_node={
                "train": {"claim_count", "driver_age", "missing_feature"}
            },
        )

    assert exc_info.value.status_code == 422
    assert "missing_feature" in str(exc_info.value.detail)
