"""The modelling ``/estimate`` route.

Estimating a training job reports its RAM and row counts and refuses invalid requests.
"""

from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest

from haute._ram_estimate import RamEstimate
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _MISSING_STEP_COUNT_PAYLOAD,
    _expander_without_step_count,
    _fast_training_params,
    _make_joined_modelling_graph,
    _make_modelling_graph,
    _xgboost_gpu_graph,
)
from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
training_data = _modelling_route_helpers.training_data


# ---------------------------------------------------------------------------
# Phase 1A: Endpoint validation gaps
# ---------------------------------------------------------------------------


class TestEstimateEndpoint:
    """Tests for /estimate — RAM + row estimation."""

    def test_estimate_success(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rows" in data
        assert "safe_row_limit" in data
        assert "estimated_mb" in data
        assert "training_mb" in data

    @pytest.mark.parametrize(
        ("validate", "rows", "unbounded"),
        [(None, 100_000 * 100_000, ["competitor_join"]), ("m:1", 100_000, [])],
        ids=["undeclared", "many-to-one"],
    )
    def test_an_undeclared_join_is_an_unproven_bound_naming_the_join(
        self, client, tmp_path, validate: str | None, rows: int, unbounded: list[str]
    ) -> None:
        """A join's row product is a worst case, never a count to downsample from (MDL-01)."""
        graph = _make_joined_modelling_graph(tmp_path, 100_000, validate)

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_rows"] == rows
        assert data["unbounded_join_node_ids"] == unbounded
        # Neither says "Will downsample": the product is unproven, and 100,000 rows fit.
        assert (data["was_downsampled"], data["warning"]) == (False, None)
        if unbounded:
            # Ten billion rows cannot fit, so training would sample to this limit.
            assert data["safe_row_limit"] is not None
        else:
            assert data["safe_row_limit"] is None

    def test_estimate_gpu_unknown_vram_returns_advisory_warning(self, client, training_data):
        """Unknown VRAM surfaces as gpu_warning in the response, without the
        switch-to-CPU refusal suffix reserved for observed-insufficient VRAM."""
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        with patch("haute._host_memory.available_vram_bytes", return_value=None):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["gpu_warning"] is not None
        assert "could not be detected" in data["gpu_warning"]
        assert "Switch task_type" not in data["gpu_warning"]
        assert data["gpu_vram_available_mb"] is None
        assert data["gpu_vram_estimated_mb"] is not None

    def test_estimate_includes_exact_bounded_evaluation_preview(
        self,
        client,
        training_data,
    ):
        graph = _make_modelling_graph(
            training_data,
            evaluation={
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "test": {"size": 0.2},
                "validation": {"method": "single", "size": 0.2},
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        assert resp.json()["evaluation_preview"] == {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "single",
            "development_rows": 48,
            "final_test_rows": 12,
            "validation_fit_count": 1,
            "min_selection_train_rows": 36,
            "max_selection_train_rows": 36,
            "min_selection_validation_rows": 12,
            "max_selection_validation_rows": 12,
        }

    @pytest.mark.parametrize(
        "terms",
        [
            {"ghost": {"type": "linear"}},
            {"x1": {"type": "linear"}, "ratio": {"type": "expression", "expr": "x1 / missing"}},
        ],
    )
    def test_evaluation_preview_ignores_unfinished_terms(self, client, training_data, terms):
        """The preview reads only the target and the evaluation key, so a GLM
        term on a column that is not upstream does not fail the estimate."""
        graph = _make_modelling_graph(training_data, algorithm="glm", params={})
        config = graph["nodes"][1]["data"]["config"]
        config.update({"family": "gaussian", "terms": terms})

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 200, resp.text
        preview = resp.json()["evaluation_preview"]
        assert preview is not None
        assert preview["development_rows"] == 60

    def test_estimate_maps_evaluation_preview_validation_failure_to_422(
        self,
        client,
        tmp_path,
    ):
        """A data-dependent preflight failure is a 422 with its reason, never a 500."""
        path = tmp_path / "null_target.parquet"
        pl.DataFrame(
            {
                "x1": [1.0, 2.0, 3.0],
                "y": pl.Series([None, None, None], dtype=pl.Float64),
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(str(path), feature_columns=["x1"])

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        refusal = resp.json()["detail"]
        assert refusal["error_code"] == "evaluation_preview_failed"
        detail = refusal["message"]
        assert detail.startswith("Evaluation preview failed: ")
        assert "only null values" in detail

    def test_estimate_maps_contract_mismatch_to_422(self, client, training_data):
        """A node whose output breaks its declared contract is a 422, never a 500."""
        base = _make_modelling_graph(training_data)
        train_config = next(n for n in base["nodes"] if n["id"] == "train")["data"]["config"]
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": make_ready_file_input_config(training_data),
                        },
                    },
                    {
                        "id": "prep",
                        "data": {
                            "label": "prep",
                            "nodeType": "polars",
                            "config": {
                                "code": "df = source",
                                "contract": {
                                    "inputs": [],
                                    "outputs": ["x1", "x2", "y", "phantom"],
                                },
                            },
                        },
                    },
                    {
                        "id": "train",
                        "data": {"label": "train", "nodeType": "modelling", "config": train_config},
                    },
                ],
                "edges": [
                    make_edge("source", "prep").model_dump(),
                    make_edge("prep", "train").model_dump(),
                ],
            }
        ).model_dump()

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        refusal = resp.json()["detail"]
        assert refusal["error_code"] == "evaluation_preview_failed"
        detail = refusal["message"]
        assert detail.startswith("Evaluation preview failed: ")
        # Its contract claims it reads no input, so the source is scanned without the
        # target; the declared output the run reads ('y') is the one it did not create.
        assert "'prep' did not create the column 'y'" in detail

    def test_estimate_maps_polars_missing_column_to_422(self, client, training_data):
        """Post-load code naming a column the source lacks is a 422, never a 500."""
        graph = _make_modelling_graph(training_data)
        source = next(n for n in graph["nodes"] if n["id"] == "source")
        source["data"]["config"]["code"] = "df = df.with_columns(flag = pl.col('missing'))"

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422
        refusal = resp.json()["detail"]
        assert refusal["error_code"] == "evaluation_preview_failed"
        detail = refusal["message"]
        assert detail.startswith("Evaluation preview failed: ")
        assert "missing" in detail

    def test_estimate_reports_a_rejected_upstream_node_config_as_its_contract_payload(
        self, client, training_data
    ):
        """A Scenario Expander without its grid size upstream of the model is a
        public config rejection: its payload, not a stringified ValueError."""
        graph = _make_modelling_graph(training_data)
        graph["nodes"].append(_expander_without_step_count())
        graph["edges"] = [
            make_edge("source", "expander").model_dump(),
            make_edge("expander", "train").model_dump(),
        ]

        resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == _MISSING_STEP_COUNT_PAYLOAD

    def test_estimate_evaluation_preview_accepts_upstream_group_by(
        self,
        client,
        training_data,
    ):
        graph = _make_modelling_graph(training_data)
        graph["nodes"].insert(
            1,
            {
                "id": "grouped_features",
                "data": {
                    "label": "grouped_features",
                    "nodeType": "polars",
                    "config": {
                        "code": (
                            "df = source.group_by('x1').agg("
                            "pl.col('x2').mean().alias('x2'), "
                            "pl.col('y').mean().alias('y'))"
                        )
                    },
                },
            },
        )
        graph["edges"] = [
            make_edge("source", "grouped_features").model_dump(),
            make_edge("grouped_features", "train").model_dump(),
        ]

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["evaluation_preview"] is not None

    def test_estimate_preview_includes_group_counts(
        self,
        client,
        tmp_path,
    ):
        path = tmp_path / "group-preview.parquet"
        pl.DataFrame(
            {
                "entity": ["a", "a", "b", "b", "c", "c", "d", "d"],
                "x": list(range(8)),
                "y": [float(value) for value in range(8)],
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(
            str(path),
            feature_columns=["x"],
            evaluation={
                "schema_version": 1,
                "strategy": "group",
                "group_column": "entity",
                "seed": 42,
                "test": {"size": 0.25},
                "validation": {
                    "method": "cross_validation",
                    "fold_count": 2,
                },
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        preview = resp.json()["evaluation_preview"]
        assert preview["development_rows"] == 6
        assert preview["final_test_rows"] == 2
        assert preview["development_group_count"] == 3
        assert preview["final_test_group_count"] == 1
        assert preview["min_selection_validation_rows"] == 2
        assert preview["max_selection_validation_rows"] == 4

    def test_estimate_preview_includes_temporal_date_ranges(
        self,
        client,
        tmp_path,
    ):
        path = tmp_path / "temporal-preview.parquet"
        pl.DataFrame(
            {
                "month": pl.date_range(
                    pl.date(2024, 1, 1),
                    pl.date(2024, 6, 1),
                    interval="1mo",
                    eager=True,
                ),
                "x": list(range(6)),
                "y": [float(value) for value in range(6)],
            }
        ).write_parquet(path)
        graph = _make_modelling_graph(
            str(path),
            feature_columns=["x"],
            evaluation={
                "schema_version": 1,
                "strategy": "temporal",
                "date_column": "month",
                "test": {"start": "2024-05-01"},
                "validation": {
                    "method": "single",
                    "start": "2024-03-01",
                },
            },
        )

        resp = client.post(
            "/api/modelling/estimate",
            json={"graph": graph, "node_id": "train"},
        )

        assert resp.status_code == 200
        preview = resp.json()["evaluation_preview"]
        assert preview["development_date_range"] == {
            "start": "2024-01-01",
            "end": "2024-04-01",
        }
        assert preview["final_test_date_range"] == {
            "start": "2024-05-01",
            "end": "2024-06-01",
        }

    def test_estimate_gpu_vram_path(self, client, training_data):
        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        with patch("haute._host_memory.available_vram_bytes", return_value=1):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("gpu_vram_estimated_mb") is not None
        assert data.get("gpu_warning") is not None

    def test_estimate_sizes_gpu_vram_at_the_rows_training_uses(self, client, training_data):
        """FMOU-R01: a row limit below the total sizes VRAM at the limited rows, and says so."""
        from haute.routes import modelling

        graph = _make_modelling_graph(
            training_data,
            params=_fast_training_params(task_type="GPU"),
        )
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 10
        with (
            patch("haute._host_memory.available_vram_bytes", return_value=10**12),
            patch(
                "haute.routes.modelling._check_gpu_vram", wraps=modelling._check_gpu_vram
            ) as vram_check,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_rows"] > 10
        assert vram_check.call_args.args[0] == 10
        assert data["gpu_vram_rows"] == 10
        assert data["gpu_vram_estimated_mb"] is not None

    def test_estimate_xgboost_gpu_vram_path(self, client, training_data):
        graph = _xgboost_gpu_graph(training_data)
        with patch("haute._host_memory.available_vram_bytes", return_value=1):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("gpu_vram_estimated_mb") is not None
        assert "Train on CPU" in data["gpu_warning"]

    @pytest.mark.parametrize(
        ("algorithm", "device", "message"),
        [
            ("lightgbm", "gpu", "LightGBM trains on CPU only"),
            # The config model's device Literal refuses an unknown device first.
            ("xgboost", "cuda", "setting 'device': Input should be 'cpu' or 'gpu'"),
        ],
    )
    def test_train_refuses_a_bad_device_before_preparation_with_explicit_metrics(
        self, client, training_data, algorithm, device, message
    ):
        graph = _xgboost_gpu_graph(training_data)
        node = next(node for node in graph["nodes"] if node["id"] == "train")
        node["data"]["config"].update(
            {
                "algorithm": algorithm,
                "device": device,
                "metrics": ["rmse"],
                "params": {"num_iterations": 5}
                if algorithm == "lightgbm"
                else {"num_boost_round": 5},
            }
        )
        with patch(
            "haute.routes._training_lifecycle.TrainService._launch_training_protocol"
        ) as launch:
            resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 400, resp.text
        assert message in resp.json()["detail"]
        launch.assert_not_called()

    def test_estimate_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/estimate", json={"graph": graph, "node_id": "nonexistent"}
        )
        assert resp.status_code == 404

    def test_estimate_failure_is_an_error_not_an_empty_estimate(self, client, training_data):
        """An estimator exception is a failure the user sees, never the empty
        estimate that stands for a size the estimator cannot prove."""
        graph = _make_modelling_graph(training_data)
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            side_effect=RuntimeError("probe failed"),
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 500

    @pytest.mark.parametrize(
        ("unavailable", "expected_rows", "expected_reason"),
        [
            (
                RamEstimate.row_count_unprovable("explode_items", 8 * 1024**3),
                None,
                {"reason": "row_count_unprovable", "blocking_node_id": "explode_items"},
            ),
            (
                RamEstimate.schema_unresolvable(250_000, 8 * 1024**3),
                250_000,
                {"reason": "schema_unresolvable", "blocking_node_id": None},
            ),
        ],
    )
    def test_estimate_the_estimator_cannot_size_says_why_without_figures(
        self, client, training_data, unavailable, expected_rows, expected_reason
    ):
        """An unavailable estimate carries its reason and no memory figure or
        VRAM check, even with GPU training and a user row limit configured."""
        graph = _make_modelling_graph(training_data)
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["params"] = {"task_type": "GPU"}
                node["data"]["config"]["row_limit"] = 500
        with (
            patch("haute._ram_estimate.estimate_safe_training_rows", return_value=unavailable),
            patch("haute.routes.modelling._check_gpu_vram") as vram_check,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["unavailable"] == expected_reason
        assert data["total_rows"] == expected_rows
        assert (data["estimated_mb"], data["training_mb"], data["bytes_per_row"]) == (
            None,
            None,
            None,
        )
        assert data["available_mb"] == 8192.0
        assert data["safe_row_limit"] == 500
        assert (data["was_downsampled"], data["warning"]) == (False, None)
        assert data["gpu_vram_estimated_mb"] is None
        vram_check.assert_not_called()

    def test_available_estimate_reports_figures_and_no_reason(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        available = RamEstimate(
            safe_row_limit=None,
            total_rows=1_000,
            estimated_bytes=3 * 1024**2,
            available_bytes=8 * 1024**3,
            bytes_per_row=3_145.7,
            was_downsampled=False,
            warning=None,
            probe_columns=4,
        )
        with patch("haute._ram_estimate.estimate_safe_training_rows", return_value=available):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["unavailable"] is None
        assert (data["total_rows"], data["estimated_mb"], data["training_mb"]) == (1_000, 3.0, 3.0)
        assert data["bytes_per_row"] == 3_145.7

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"estimated_mb": None}, "requires a row total and memory figures"),
            ({"total_rows": None}, "requires a row total and memory figures"),
            (
                {"unbounded_join_node_ids": ["join"], "warning": "downsampled"},
                "worst-case row bound has no downsampling verdict",
            ),
            (
                {"unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None}},
                "has no memory figures",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "was_downsampled": True,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None},
                },
                "no downsampling verdict or warning",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "gpu_vram_estimated_mb": 12.0,
                    "gpu_vram_rows": 100,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": None},
                },
                "no GPU VRAM check",
            ),
            ({"gpu_vram_estimated_mb": 12.0}, "states the rows it is sized at"),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "row_count_unprovable", "blocking_node_id": "j"},
                },
                "only a row_count_unprovable estimate lacks a row total",
            ),
            (
                {
                    "total_rows": None,
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "row_count_unprovable", "blocking_node_id": None},
                },
                "names the blocking node",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "schema_unresolvable", "blocking_node_id": "j"},
                },
                "names no blocking node",
            ),
            (
                {
                    "estimated_mb": None,
                    "training_mb": None,
                    "bytes_per_row": None,
                    "unavailable": {"reason": "cardinality", "blocking_node_id": None},
                },
                "row_count_unprovable",
            ),
        ],
    )
    def test_estimate_response_rejects_figures_that_disagree_with_availability(
        self, overrides, message
    ):
        from pydantic import ValidationError

        from haute.schemas import TrainEstimateResponse

        payload = {
            "total_rows": 100,
            "estimated_mb": 1.0,
            "training_mb": 1.0,
            "available_mb": 8192.0,
            "bytes_per_row": 10.0,
            "unavailable": None,
            "unbounded_join_node_ids": [],
        }
        with pytest.raises(ValidationError, match=message):
            TrainEstimateResponse.model_validate({**payload, **overrides})

    def test_estimate_suppresses_ram_warning_when_user_limit_binds(self, client, training_data):
        """When user's row_limit is lower than the RAM-safe limit, suppress the RAM warning."""
        graph = _make_modelling_graph(training_data)
        # Inject a user row_limit that is lower than the RAM-safe limit
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 500

        mock_est = RamEstimate(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1_000_000_000,
            available_bytes=20_000_000_000,
            bytes_per_row=100.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["safe_row_limit"] == 500
        assert data["warning"] is None
        assert data["was_downsampled"] is False

    def test_estimate_keeps_ram_warning_when_ram_limit_binds(self, client, training_data):
        """When RAM-safe limit is lower than user's row_limit, keep the RAM warning."""
        graph = _make_modelling_graph(training_data)
        for node in graph["nodes"]:
            if node["id"] == "train":
                node["data"]["config"]["row_limit"] = 20_000_000

        mock_est = RamEstimate(
            safe_row_limit=9_000_000,
            warning="Dataset downsampled to 9,000,000 of 10,000,000 rows",
            total_rows=10_000_000,
            probe_columns=5,
            estimated_bytes=1_000_000_000,
            available_bytes=20_000_000_000,
            bytes_per_row=100.0,
            was_downsampled=True,
        )
        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            return_value=mock_est,
        ):
            resp = client.post("/api/modelling/estimate", json={"graph": graph, "node_id": "train"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["safe_row_limit"] == 9_000_000
        assert data["warning"] is not None
        assert data["was_downsampled"] is True
