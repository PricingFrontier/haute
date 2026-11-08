"""MOD-M01 engagement: the fit mode and row limit a training run plans from its estimate.

A GLM whose RAM row limit binds trains on every row out of core instead of being
downsampled; every other case keeps today's row limit. The estimate route and the
started run share ``training_row_plan``, so they report the same plan.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from haute._ram_estimate import RamEstimate, TrainingEstimateUnavailableReason
from haute.routes._training_preparation import training_row_plan
from haute.schemas import FitEvidencePayload, TrainEstimateResponse
from tests.conftest import make_edge, make_graph, make_ready_file_input_config

_EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}
_GLM = {"algorithm": "glm", "target": "y", "terms": {"x1": {"type": "linear"}}}
_CATBOOST = {"algorithm": "catboost", "target": "y"}


def _estimate(
    safe_row_limit: int | None,
    *,
    total_rows: int = 10_000,
    unbounded_join_node_ids: tuple[str, ...] = (),
) -> RamEstimate:
    proven_limit = safe_row_limit is not None and not unbounded_join_node_ids
    return RamEstimate(
        safe_row_limit=safe_row_limit,
        total_rows=total_rows,
        estimated_bytes=total_rows * 100,
        available_bytes=10**9,
        bytes_per_row=100.0,
        was_downsampled=proven_limit,
        warning=f"Dataset downsampled to {safe_row_limit:,} rows" if proven_limit else None,
        probe_columns=3,
        unbounded_join_node_ids=unbounded_join_node_ids,
    )


class TestTrainingRowPlan:
    def test_a_glm_the_ram_limit_binds_on_fits_out_of_core_on_every_row(self) -> None:
        plan = training_row_plan(_GLM, _estimate(2_000))

        assert plan.fit_mode == "out_of_core"
        assert plan.row_limit is None
        assert plan.ram_limit_binds
        assert not plan.ram_downsamples

    def test_an_out_of_core_glm_keeps_the_users_larger_limit(self) -> None:
        plan = training_row_plan({**_GLM, "row_limit": 5_000}, _estimate(2_000))

        assert plan.fit_mode == "out_of_core"
        assert plan.row_limit == 5_000

    def test_a_users_smaller_limit_keeps_a_glm_in_memory(self) -> None:
        plan = training_row_plan({**_GLM, "row_limit": 1_000}, _estimate(2_000))

        assert plan.fit_mode == "in_memory"
        assert plan.row_limit == 1_000
        assert not plan.ram_limit_binds

    @pytest.mark.parametrize("config", [_GLM, _CATBOOST], ids=["glm", "catboost"])
    def test_an_estimate_that_fits_trains_in_memory_on_every_row(self, config) -> None:
        plan = training_row_plan(config, _estimate(None))

        assert plan.fit_mode == "in_memory"
        assert plan.row_limit is None
        assert not plan.ram_limit_binds

    def test_an_unavailable_estimate_trains_a_glm_in_memory(self) -> None:
        unavailable = RamEstimate(
            safe_row_limit=None,
            total_rows=10_000,
            estimated_bytes=None,
            available_bytes=10**9,
            bytes_per_row=None,
            was_downsampled=False,
            warning=None,
            unavailable_reason=TrainingEstimateUnavailableReason.SCHEMA_UNRESOLVABLE,
        )

        plan = training_row_plan(_GLM, unavailable)

        assert plan.fit_mode == "in_memory"
        assert plan.row_limit is None

    def test_a_catboost_job_the_ram_limit_binds_on_still_downsamples(self) -> None:
        plan = training_row_plan(_CATBOOST, _estimate(2_000))

        assert plan.fit_mode == "in_memory"
        assert plan.row_limit == 2_000
        assert plan.ram_downsamples

    def test_a_glm_over_an_unproven_join_bound_fits_out_of_core(self) -> None:
        plan = training_row_plan(_GLM, _estimate(2_000, unbounded_join_node_ids=("join",)))

        assert plan.fit_mode == "out_of_core"
        assert plan.row_limit is None


def _glm_graph(data_path: str, **config_overrides: object) -> dict:
    config: dict[str, object] = {
        "target": "y",
        "algorithm": "glm",
        "task": "regression",
        "family": "gaussian",
        "terms": {"x1": {"type": "linear"}, "x2": {"type": "linear"}},
        "evaluation": _EVALUATION,
        "metrics": ["rmse"],
        **config_overrides,
    }
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
                    },
                },
                {
                    "id": "train",
                    "data": {"label": "train", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [make_edge("source", "train").model_dump()],
        }
    ).model_dump()


@pytest.fixture()
def glm_rows(tmp_path) -> str:
    rng = np.random.RandomState(11)
    rows = 800
    path = tmp_path / "glm_rows.parquet"
    x1, x2 = rng.randn(rows), rng.randn(rows)
    pl.DataFrame(
        {"x1": x1, "x2": x2, "y": 1.0 + 0.5 * x1 - 0.25 * x2 + rng.randn(rows)}
    ).write_parquet(path)
    return str(path)


def _poll(client, job_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/modelling/train/status/{job_id}").json()
        if status["status"] not in ("pending", "running"):
            return status
        time.sleep(0.05)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


class TestEstimateRoute:
    def test_a_binding_glm_estimate_plans_chunks_and_downsamples_nothing(
        self, client, glm_rows
    ) -> None:
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=_estimate(2_000, total_rows=800_000),
        ):
            response = client.post(
                "/api/modelling/estimate",
                json={"graph": _glm_graph(glm_rows), "node_id": "train"},
            )

        assert response.status_code == 200, response.text
        estimate = response.json()
        assert estimate["fit_mode"] == "out_of_core"
        assert estimate["safe_row_limit"] is None
        assert estimate["was_downsampled"] is False
        assert estimate["warning"] is None
        # The in-memory figure stays: it is why the fit is chunked.
        assert estimate["training_mb"] == round(800_000 * 100 / 1024**2, 1)
        # The preview is drawn at the rows the run trains on: every one of them.
        preview = estimate["evaluation_preview"]
        assert preview is not None
        assert preview["development_rows"] == 800

    def test_a_binding_catboost_estimate_still_downsamples(self, client, glm_rows) -> None:
        graph = _glm_graph(glm_rows)
        config = graph["nodes"][1]["data"]["config"]
        for key in ("family", "terms"):
            config.pop(key)
        config.update(
            algorithm="catboost",
            loss_function="RMSE",
            feature_columns=["x1", "x2"],
            params={"iterations": 5},
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=_estimate(500, total_rows=800),
        ):
            response = client.post(
                "/api/modelling/estimate", json={"graph": graph, "node_id": "train"}
            )

        assert response.status_code == 200, response.text
        estimate = response.json()
        assert estimate["fit_mode"] == "in_memory"
        assert estimate["safe_row_limit"] == 500
        assert estimate["was_downsampled"] is True


class TestOutOfCoreRun:
    def test_a_glm_the_ram_limit_binds_on_trains_every_row_out_of_core(
        self, client, glm_rows, monkeypatch
    ) -> None:
        # Too little RAM for 800 rows: the limit floors at 500, which would sample.
        monkeypatch.setattr("haute._ram_estimate.available_ram_bytes", lambda: 1)

        estimate = client.post(
            "/api/modelling/estimate", json={"graph": _glm_graph(glm_rows), "node_id": "train"}
        ).json()
        response = client.post(
            "/api/modelling/train", json={"graph": _glm_graph(glm_rows), "node_id": "train"}
        )

        assert response.status_code == 200, response.text
        status = _poll(client, response.json()["job_id"])
        assert status["status"] == "completed", status
        result = status["result"]
        assert estimate["fit_mode"] == "out_of_core"
        assert result["fit_evidence"]["mode"] == "out_of_core"
        assert result["fit_evidence"]["passes"] >= 2
        assert status["warning"] is None
        assert result["development_rows"] == estimate["evaluation_preview"]["development_rows"]
        assert result["development_rows"] == 800

    def test_a_glm_that_fits_trains_in_memory_and_says_so(self, client, glm_rows) -> None:
        response = client.post(
            "/api/modelling/train", json={"graph": _glm_graph(glm_rows), "node_id": "train"}
        )

        status = _poll(client, response.json()["job_id"])
        assert status["status"] == "completed", status
        evidence = status["result"]["fit_evidence"]
        assert evidence["mode"] == "in_memory"
        assert evidence["max_iter"] == 25
        assert evidence["passes"] is None


class TestContracts:
    def test_an_out_of_core_estimate_downsamples_nothing(self) -> None:
        figures = {
            "total_rows": 800,
            "estimated_mb": 1.0,
            "training_mb": 1.0,
            "available_mb": 1.0,
            "bytes_per_row": 100.0,
            "unavailable": None,
            "unbounded_join_node_ids": [],
            "fit_mode": "out_of_core",
        }
        TrainEstimateResponse(**figures)
        with pytest.raises(ValidationError, match="downsamples nothing"):
            TrainEstimateResponse(**figures, was_downsampled=True)
        with pytest.raises(ValidationError, match="downsamples nothing"):
            TrainEstimateResponse(**figures, warning="Dataset downsampled to 500 rows")

    @pytest.mark.parametrize(
        "evidence",
        [
            {"threads": 1, "mode": "in_memory", "max_iter": 25, "tol": 1e-8, "max_half_steps": 25},
            {
                "threads": 1,
                "mode": "out_of_core",
                "max_iter": 25,
                "tol": 1e-8,
                "max_half_steps": 25,
                "passes": 9,
                "encode_rows": 4096,
                "max_bytes": None,
            },
            {"threads": 4},
        ],
        ids=["in-memory", "out-of-core", "no-mode"],
    )
    def test_fit_evidence_accepts_a_consistent_mode(self, evidence) -> None:
        FitEvidencePayload(**evidence)

    @pytest.mark.parametrize(
        "evidence",
        [
            {"threads": 1, "mode": "in_memory"},
            {"threads": 1, "max_iter": 25},
            {
                "threads": 1,
                "mode": "in_memory",
                "max_iter": 25,
                "tol": 1e-8,
                "max_half_steps": 25,
                "passes": 3,
            },
            {
                "threads": 1,
                "mode": "out_of_core",
                "max_iter": 25,
                "tol": 1e-8,
                "max_half_steps": 25,
            },
            {
                "threads": 1,
                "mode": "in_memory",
                "max_iter": 25,
                "tol": 1e-8,
                "max_half_steps": 25,
                "max_bytes": 10,
            },
        ],
        ids=[
            "mode-without-caps",
            "caps-without-mode",
            "in-memory-passes",
            "out-of-core-no-passes",
            "in-memory-plan",
        ],
    )
    def test_fit_evidence_rejects_an_inconsistent_mode(self, evidence) -> None:
        with pytest.raises(ValidationError):
            FitEvidencePayload(**evidence)
