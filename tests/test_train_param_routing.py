"""Wave-4b regression tests: algorithm-correct train params and seeded downsampling.

Covers three remediation items at the ``TrainService`` boundary:

- 4b.1 — GLM config keys (incl. ``offset``) must NOT be merged into CatBoost
  params: CatBoost's constructors have no ``**kwargs``, so the standard
  log-exposure frequency workflow (top-level ``offset`` config) crashed at fit.
- 4b.2 — the exported GLM training script must train the SAME model as live
  training (terms/family/link/regularization live at config top level and were
  silently dropped by the export, yielding a Gaussian all-features model).
- 4b.4 — the RAM/row-limit downsample must be a seeded random sample, not
  ``head(N)`` (order-biased: temporal/target-ordered data trained on the
  oldest slice only).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from tests.conftest import make_edge, make_graph, make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_TERMINAL_JOB_STATUSES = {
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
}


@pytest.fixture(autouse=True)
def _fast_optional_training_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert param routing and job state, not optional charts."""
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 60) -> dict:
    """Poll /train/status/{job_id} until a terminal status, return final status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/modelling/train/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in _TERMINAL_JOB_STATUSES:
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def _start_training(client: TestClient, graph: dict, node_id: str = "train") -> dict:
    resp = client.post("/api/modelling/train", json={"graph": graph, "node_id": node_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "started"
    return _poll_until_done(client, data["job_id"])


def _modelling_graph(data_path: str, config: dict[str, Any]) -> dict:
    """dataInput → modelling graph with a fully caller-controlled config."""
    config = dict(config)
    config.setdefault(
        "output_dir",
        str(Path(data_path).resolve().parent / "outputs"),
    )
    graph = make_graph(
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
    )
    return graph.model_dump()


class _CapturingTrainingJob:
    """Stands in for TrainingJob; records constructor kwargs and the sunk frame.

    The training temp parquet is deleted by the worker's ``finally`` block, so
    the frame must be read inside ``run()`` while the file still exists. The
    successful-run behaviour (model, contract, evaluation artifacts, valid
    completed result) is delegated to the shared worker-protocol stub.
    """

    captured: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def run(self, progress: Any, on_iteration: Any, **run_kwargs: Any) -> object:
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        type(self).captured.append(
            {"kwargs": self.kwargs, "frame": pl.read_parquet(self.kwargs["data"])}
        )
        delegate = _SuccessfulTrainingJob(**self.kwargs)
        return delegate.run(progress, on_iteration, **run_kwargs)


@pytest.fixture()
def capturing_job() -> type[_CapturingTrainingJob]:
    """Fresh capture state per test (the list is class-level)."""

    class _Job(_CapturingTrainingJob):
        captured: ClassVar[list[dict[str, Any]]] = []

    return _Job


@pytest.fixture()
def inline_training_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep patched training doubles in-process while exercising the protocol."""
    from haute.routes._train_service import TrainService
    from haute.routes.modelling import _store
    from tests.test_training_worker_protocol import _inline_protocol_runner

    monkeypatch.setattr(
        "haute.routes.modelling._train_service",
        TrainService(_store, protocol_runner=_inline_protocol_runner),
    )


@pytest.fixture()
def frequency_data(tmp_path) -> str:
    """Log-exposure frequency dataset: the standard insurance workflow."""
    rng = np.random.RandomState(7)
    n = 120
    x1 = rng.randn(n)
    x2 = rng.randn(n)
    exposure = rng.uniform(0.1, 1.0, n)
    lam = np.exp(0.4 * x1 - 1.0) * exposure
    df = pl.DataFrame(
        {
            "x1": x1,
            "x2": x2,
            "log_exposure": np.log(exposure),
            "claim_count": rng.poisson(lam).astype(np.float64),
        }
    )
    path = tmp_path / "frequency.parquet"
    df.write_parquet(path)
    return str(path)


@pytest.fixture()
def target_ordered_data(tmp_path) -> str:
    """400 rows ordered by the target — the downsample-bias worst case.

    ``y`` is the row index (0..399): any contiguous-prefix downsample yields a
    degenerate target distribution confined to the oldest slice.
    """
    n = 400
    df = pl.DataFrame(
        {
            "y": pl.Series(range(n), dtype=pl.Float64),
            "x": pl.Series([float(i % 13) for i in range(n)]),
        }
    )
    path = tmp_path / "ordered.parquet"
    df.write_parquet(path)
    return str(path)


# ---------------------------------------------------------------------------
# 4b.1 — algorithm-correct param routing
# ---------------------------------------------------------------------------


class TestCatBoostParamRouting:
    def test_catboost_log_exposure_frequency_workflow_trains(self, client, frequency_data):
        """RED repro for 4b.1: top-level ``offset`` config must not reach CatBoost.

        Pre-fix, ``offset`` was merged into train params and forwarded to
        ``CatBoostRegressor(**params)`` (no ``**kwargs``) → TypeError at fit and
        the job lands in ``error``. This is a real fit, no mocks.
        """
        config = {
            "target": "claim_count",
            "algorithm": "catboost",
            "task": "regression",
            "offset": "log_exposure",
            "loss_function": "Poisson",
            "params": {"iterations": 4, "depth": 2},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
        }
        graph = _modelling_graph(frequency_data, config)

        status = _start_training(client, graph)

        assert status["status"] == "completed", status.get("message")
        assert status["result"]["diagnostic_metrics"]
        assert status["result"]["development_rows"] > 0

    def test_catboost_receives_only_catboost_params(
        self,
        client,
        frequency_data,
        capturing_job,
        inline_training_worker,
    ):
        """Pin the exact params CatBoost training receives: config GLM keys must
        not leak into ``params`` while ``offset`` still arrives as its own kwarg."""
        config = {
            "target": "claim_count",
            "algorithm": "catboost",
            "task": "regression",
            "loss_function": "RMSE",
            "offset": "log_exposure",
            "weight": "x2",
            "params": {"iterations": 4, "depth": 2},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
        }
        graph = _modelling_graph(frequency_data, config)

        with patch("haute.modelling.TrainingJob", capturing_job):
            status = _start_training(client, graph)

        assert status["status"] == "completed"
        assert len(capturing_job.captured) == 1
        kwargs = capturing_job.captured[0]["kwargs"]
        data_path = kwargs.pop("data")
        assert data_path.endswith(".parquet")
        # Pin the COMPLETE TrainingJob kwargs a clean CatBoost config produces —
        # any drift in the shared builder shows up here.
        assert kwargs == {
            "name": "train",  # node id (no explicit config name)
            "target": "claim_count",
            "weight": "x2",
            "exclude": [],
            "feature_columns": None,
            "fold_column": None,
            "id_columns": None,
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 4, "depth": 2},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
            "mlflow_experiment": None,
            "model_name": None,
            "output_dir": kwargs["output_dir"],
            "loss_function": "RMSE",
            "variance_power": None,
            "offset": "log_exposure",
            "monotone_constraints": None,
            "feature_weights": None,
            "categorical_levels": None,
            "tuning": None,
        }
        staged_output = Path(kwargs["output_dir"])
        assert staged_output.name == "output"
        assert staged_output.parent.name.startswith(".haute-training-")

    def test_glm_receives_merged_glm_config_in_params(
        self,
        client,
        frequency_data,
        capturing_job,
        inline_training_worker,
    ):
        """GLM keeps the top-level→params merge: terms/family/link/regularization
        and friends must arrive in ``params`` for ``GLMAlgorithm.fit``."""
        config = {
            "target": "claim_count",
            "algorithm": "glm",
            "task": "regression",
            "family": "poisson",
            "link": "log",
            "terms": {"x1": {"type": "linear"}},
            "regularization": "ridge",
            "alpha": 0.5,
            "l1_ratio": 0.1,
            "intercept": True,
            "offset": "log_exposure",
            "params": {},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
        }
        graph = _modelling_graph(frequency_data, config)

        with patch("haute.modelling.TrainingJob", capturing_job):
            status = _start_training(client, graph)

        assert status["status"] == "completed"
        kwargs = capturing_job.captured[0]["kwargs"]
        assert kwargs["params"]["family"] == "poisson"
        assert kwargs["params"]["link"] == "log"
        assert kwargs["params"]["terms"] == {"x1": {"type": "linear"}}
        assert kwargs["params"]["regularization"] == "ridge"
        assert kwargs["params"]["alpha"] == 0.5
        assert kwargs["params"]["l1_ratio"] == 0.1
        assert kwargs["params"]["intercept"] is True
        assert kwargs["params"]["offset"] == "log_exposure"
        assert kwargs["offset"] == "log_exposure"
        assert kwargs["algorithm"] == "glm"

    def test_glm_real_fit_uses_configured_family_and_terms(self, client, frequency_data):
        """Clean-GLM no-regression pin: a real GLM route fit must train on the
        configured terms (intercept + x1), not an all-features auto model."""
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        config = {
            "target": "claim_count",
            "algorithm": "glm",
            "task": "regression",
            "family": "poisson",
            "link": "log",
            "terms": {"x1": {"type": "linear"}},
            "intercept": True,
            "params": {},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
        }
        graph = _modelling_graph(frequency_data, config)

        status = _start_training(client, graph)

        assert status["status"] == "completed", status.get("message")
        coef_features = {row["feature"] for row in status["result"]["glm_coefficients"]}
        # Only the configured term (plus intercept) — x2/log_exposure must NOT
        # have been auto-termed into the model.
        assert any("x1" in feature for feature in coef_features)
        assert not any("x2" in feature for feature in coef_features)
        assert not any("log_exposure" in feature for feature in coef_features)


# ---------------------------------------------------------------------------
# 4b.2 — exported GLM script trains the same model as live training
# ---------------------------------------------------------------------------


class TestExportedScriptEquivalence:
    def test_exported_glm_script_trains_same_model_as_live_route(
        self, client, frequency_data, tmp_path
    ):
        """RED repro for 4b.2: pre-fix the exported script dropped top-level
        terms/family/link, silently training a Gaussian all-features model.

        Trains the same config twice — once through the live route, once by
        executing the exported script — and compares the fitted coefficients.
        """
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        config = {
            "name": "freq_glm",
            "target": "claim_count",
            "algorithm": "glm",
            "task": "regression",
            "family": "poisson",
            "link": "log",
            "terms": {"x1": {"type": "linear"}},
            "intercept": True,
            "params": {},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
            "output_dir": str(tmp_path / "outputs_live"),
        }
        graph = _modelling_graph(frequency_data, config)
        status = _start_training(client, graph)
        assert status["status"] == "completed", status.get("message")
        live_coefs = {
            row["feature"]: row["coefficient"] for row in status["result"]["glm_coefficients"]
        }

        from haute.modelling import generate_training_script

        script = generate_training_script(
            {**config, "output_dir": str(tmp_path / "outputs_script")},
            frequency_data,
        )
        namespace: dict[str, Any] = {"__name__": "haute_exported_script"}
        exec(compile(script, "<exported_script>", "exec"), namespace)  # noqa: S102
        script_result = namespace["job"].run()
        script_coefs = {
            row["feature"]: row["coefficient"] for row in script_result.glm_coefficients
        }

        assert set(script_coefs) == set(live_coefs)
        for feature, live_value in live_coefs.items():
            assert script_coefs[feature] == pytest.approx(live_value, rel=1e-6, abs=1e-9), feature


# ---------------------------------------------------------------------------
# 4b.4 — seeded random downsample replaces head(N)
# ---------------------------------------------------------------------------


class TestRowLimitDownsample:
    def _graph_with_row_limit(self, data_path: str, row_limit: int) -> dict:
        config = {
            "target": "y",
            "algorithm": "catboost",
            "task": "regression",
            "loss_function": "RMSE",
            "row_limit": row_limit,
            "params": {"iterations": 4, "depth": 2},
            "evaluation": {
                "schema_version": 1,
                "strategy": "random",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "metrics": ["rmse"],
        }
        return _modelling_graph(data_path, config)

    def test_row_limit_sample_is_not_order_biased(
        self,
        client,
        target_ordered_data,
        capturing_job,
        inline_training_worker,
    ):
        """RED repro for 4b.4: on target-ordered data, ``head(120)`` confines the
        training target to y < 120. A uniform seeded sample of 120/400 rows
        misses an entire half with probability < 1e-14 — this asserts a
        distribution property, not luck."""
        graph = self._graph_with_row_limit(target_ordered_data, 120)

        with patch("haute.modelling.TrainingJob", capturing_job):
            status = _start_training(client, graph)

        assert status["status"] == "completed"
        frame = capturing_job.captured[0]["frame"]
        assert frame.height == 120
        y = frame["y"]
        assert (y < 200).sum() > 0, "sample lost the lower half of the target"
        assert (y >= 200).sum() > 0, "sample is confined to the oldest slice (head bias)"

    def test_row_limit_sample_is_deterministic_across_runs(
        self,
        client,
        target_ordered_data,
        capturing_job,
        inline_training_worker,
    ):
        """The downsample seed is a fixed constant: identical input → identical
        training rows on every run (reproducible training)."""
        graph = self._graph_with_row_limit(target_ordered_data, 120)

        with patch("haute.modelling.TrainingJob", capturing_job):
            first = _start_training(client, graph)
            second = _start_training(client, graph)

        assert first["status"] == "completed"
        assert second["status"] == "completed"
        first_y = capturing_job.captured[0]["frame"]["y"].to_list()
        second_y = capturing_job.captured[1]["frame"]["y"].to_list()
        assert first_y == second_y

    def test_row_limit_at_or_above_height_keeps_all_rows(
        self,
        client,
        target_ordered_data,
        capturing_job,
        inline_training_worker,
    ):
        graph = self._graph_with_row_limit(target_ordered_data, 1_000)

        with patch("haute.modelling.TrainingJob", capturing_job):
            status = _start_training(client, graph)

        assert status["status"] == "completed"
        assert capturing_job.captured[0]["frame"].height == 400


class TestSeededSampleHelper:
    """Unit contract of the lazy seeded-sample helper used by _execute_and_sink."""

    def test_exact_row_count(self):
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": list(range(1_000))})
        assert _seeded_training_sample(lf, 100).collect().height == 100

    def test_deterministic_for_same_input(self):
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": list(range(1_000))})
        first = _seeded_training_sample(lf, 100).collect()
        second = _seeded_training_sample(lf, 100).collect()
        assert first.equals(second)

    def test_preserves_relative_row_order(self):
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": list(range(1_000))})
        sampled = _seeded_training_sample(lf, 100).collect()
        assert sampled["y"].is_sorted()

    def test_limit_at_or_above_height_is_identity(self):
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": list(range(50))})
        assert _seeded_training_sample(lf, 50).collect().height == 50
        assert _seeded_training_sample(lf, 51).collect().height == 50

    def test_non_positive_limit_fails_loud(self):
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": [1, 2, 3]})
        with pytest.raises(ValueError, match="row_limit"):
            _seeded_training_sample(lf, 0)
        with pytest.raises(ValueError, match="row_limit"):
            _seeded_training_sample(lf, -5)

    def test_not_a_contiguous_prefix(self):
        """Distribution property: a 100/1000 uniform sample equals the first 100
        rows with probability ~1e-143 — this pins 'not head(N)' without flake."""
        from haute.routes._train_service import _seeded_training_sample

        lf = pl.LazyFrame({"y": list(range(1_000))})
        sampled = _seeded_training_sample(lf, 100).collect()
        assert sampled["y"].to_list() != list(range(100))
