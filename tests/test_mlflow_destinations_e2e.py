"""End-to-end per-node MLflow destination coverage (MLF-D03, Task B5).

Every test here runs against **real local file stores** — no tracking server,
no network, no real credentials.  The shape each test pins is the one the
package exists for: a node without ``mlflow_destination`` reads from and writes
to the local folder even while a remote destination is fully configured, a
node that names a destination uses *that* one, and a named destination that is
unavailable fails loudly instead of quietly consulting another backend.

Databricks is configured by declaring ``DATABRICKS_MLFLOW_HOST`` /
``DATABRICKS_MLFLOW_TOKEN`` for a deliberately unroutable ``.invalid`` host with
a placeholder token, and
by patching ``mlflow.utils.databricks_utils.get_databricks_host_creds`` so the
secret-free backend identity can be minted without a single outbound request.
Anything that tried to *use* that backend would fail; the tests additionally
forbid Databricks resolution outright while a local-folder load runs, so
"never consulted another backend" is asserted rather than assumed.

Fixture pattern follows ``tests/test_mlflow_log_button_roundtrip.py`` (real
local file store rooted in ``tmp_path``, ``set_project_root``, a seeded
completed job driven through the FastAPI test client).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from tests.conftest import make_edge, make_file_input_config, make_graph, make_output_config
from tests.job_store_support import seed_job
from tests.training_artifacts_support import publish_trained_job

# These integration tests need the real core MLflow package. Keep the guard for
# deliberately partial test environments.
mlflow = pytest.importorskip(
    "mlflow",
    reason="core mlflow dependency is unavailable",
)

from haute._sandbox import set_project_root  # noqa: E402 — after importorskip by design
from haute.errors import MlflowConfigError  # noqa: E402
from haute.executor import execute_graph  # noqa: E402
from haute.modelling._feature_contract import build_contract, save_contract  # noqa: E402
from haute.modelling._training_job import model_contract_filename  # noqa: E402
from haute.schemas import TrainResponse  # noqa: E402

FEATURES = ["x", "c"]
CAT_FEATURES = ["c"]
TARGET = "y"

DATABRICKS_MLFLOW_HOST = "https://adb.example.invalid"
DATABRICKS_MLFLOW_TOKEN = "not-a-real-token"  # noqa: S105 — placeholder, never a credential

MODEL_ARTIFACT = "freq.cbm"
OPTIMISER_ARTIFACT = "optimiser_result.json"


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """A tmp project root with no ambient MLflow / Databricks configuration.

    The developer machine running this suite may legitimately carry real
    Databricks credentials; every one of them is removed here so a test can
    never reach a real workspace.
    """
    for name in (
        "MLFLOW_TRACKING_URI",
        "MLFLOW_REGISTRY_URI",
        "MLFLOW_ENABLE_DB_SDK",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_FILE",
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)  # conftest restores the original project root.
    # Keep every disk-cached artifact inside this test's tmp tree.
    cache_root = tmp_path / ".cache" / "models"
    monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: cache_root)
    yield tmp_path


def _configure_databricks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a resolvable Databricks destination without any network.

    ``backend_identity`` mints the secret-free identity from the very call
    MLflow's own stores use, so that one call is stubbed; nothing else about
    the Databricks backend is reachable, which is the point.
    """
    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", DATABRICKS_MLFLOW_HOST)
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", DATABRICKS_MLFLOW_TOKEN)
    monkeypatch.setattr(
        "mlflow.utils.databricks_utils.get_databricks_host_creds",
        lambda *_args, **_kwargs: SimpleNamespace(host=DATABRICKS_MLFLOW_HOST),
    )


@contextmanager
def _databricks_resolution_forbidden() -> Iterator[None]:
    """Make any Databricks destination resolution an immediate test failure."""

    def _forbidden() -> Any:
        raise AssertionError(
            "the Databricks destination was resolved while an explicitly "
            "local-folder operation was running"
        )

    with patch(
        "haute.modelling._mlflow_settings._resolve_databricks",
        side_effect=_forbidden,
    ):
        yield


def _assert_databricks_configured_but_not_chosen() -> None:
    from haute._mlflow_utils import resolve_backend

    databricks = resolve_backend("databricks")
    assert databricks.identity.startswith(f"databricks:{DATABRICKS_MLFLOW_HOST}|profile=")
    unchosen = resolve_backend("")
    assert unchosen.mode == "local", f"a node without a destination resolved {unchosen.mode!r}"


# ---------------------------------------------------------------------------
# Model fixtures — real CatBoost models with a string categorical feature
# ---------------------------------------------------------------------------


def _training_frame(n: int = 80, seed: int = 7, scale: float = 2.0) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    x = rng.rand(n)
    c = rng.choice(["red", "blue", "green"], size=n)
    y = scale * x + np.where(c == "red", 0.5, 0.0) + rng.rand(n) * 0.01
    return pl.DataFrame({"x": x, "c": c, "y": y})


def _write_contract(model_path: Path) -> None:
    from haute.modelling._feature_contract import ModelIdentity

    glm = model_path.suffix == ".rsglm"
    contract = build_contract(
        features=FEATURES,
        feature_types={"x": "Float64", "c": "String"},
        categorical_features=CAT_FEATURES,
        target_name=TARGET,
        target_type="Float64",
        task="regression",
        model=ModelIdentity(
            algorithm="glm" if glm else "catboost",
            link="identity",
            engine_name="rustystats" if glm else "catboost",
            engine_version="0",
            haute_version="0",
            glm_family="gaussian" if glm else None,
        ),
    )
    save_contract(contract, model_path.parent / model_contract_filename(model_path.stem))


def _train_catboost(model_dir: Path, *, seed: int = 7, scale: float = 2.0) -> tuple[Path, Any]:
    """Train a tiny real CatBoost model; distinct *seed*/*scale* → distinct predictions."""
    from catboost import CatBoostRegressor

    model_dir.mkdir(parents=True, exist_ok=True)
    df = _training_frame(seed=seed, scale=scale)
    train = df.select(FEATURES).to_pandas()
    model = CatBoostRegressor(
        iterations=8,
        depth=2,
        verbose=0,
        allow_writing_files=False,
        cat_features=CAT_FEATURES,
        random_seed=seed,
    )
    model.fit(train, df[TARGET].to_numpy())
    model_path = model_dir / MODEL_ARTIFACT
    model.save_model(str(model_path))
    _write_contract(model_path)
    return model_path, model


def _completed_result(model_path: str) -> TrainResponse:
    artifact_path = str(Path(__file__).resolve())
    return TrainResponse(
        status="completed",
        diagnostic_metrics={"rmse": 0.1},
        final_test_metrics={},
        model_path=model_path,
        development_rows=80,
        final_test_rows=0,
        diagnostics_set="development",
        features=FEATURES,
        cat_features=CAT_FEATURES,
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "none",
            "validation_fit_count": 0,
            "fit_count": 1,
            "development_rows": 80,
            "final_test_rows": 0,
            "selection_fits": [],
            "selection_metrics": {},
            "plan_sha256": "a" * 64,
            "results_sha256": "b" * 64,
            "plan_path": artifact_path,
            "results_path": artifact_path,
            "report_path": artifact_path,
            "summary": {"development_rows": 80, "test_rows": 0, "validation_fit_count": 0},
        },
    )


@pytest.fixture(autouse=True)
def _job_owned_training_artifacts(training_artifact_root: Path) -> Path:
    return training_artifact_root


@contextmanager
def _seeded_training_job(job_id: str, model_path: Path) -> Iterator[None]:
    from haute.routes import _training_artifacts
    from haute.routes.modelling import _store

    publish_trained_job(
        _store,
        job_id,
        root=_training_artifacts.training_artifact_root(),
        model_file=model_path,
        result=_completed_result(str(model_path)),
        # The snapshot deliberately names a *different* destination: the
        # log request is authoritative and must never consult it.
        config={
            "algorithm": "catboost",
            "task": "regression",
            "target": TARGET,
            "mlflow_destination": "databricks",
        },
        node_label="freq",
    )
    try:
        yield
    finally:
        _store.delete_job(job_id)


def _log_model_to_local(client: Any, job_id: str, model_path: Path, experiment: str) -> str:
    """Log a seeded CatBoost job to the Local destination; return the run ID."""
    with _seeded_training_job(job_id, model_path):
        resp = client.post(
            "/api/modelling/mlflow/log",
            json={"job_id": job_id, "destination": "local", "experiment_name": experiment},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backend"] == "local"
    assert body["tracking_uri"].startswith("file:")
    return str(body["run_id"])


# ---------------------------------------------------------------------------
# Optimiser fixtures
# ---------------------------------------------------------------------------

OPTIMISER_CONFIG: dict[str, Any] = {
    "mode": "online",
    "objective": "predicted_income",
    "constraints": {"predicted_volume": {"min": 0.9}},
    "quote_id": "quote_id",
    "scenario_index": "scenario_index",
    "scenario_value": "scenario_value",
    # As with training, the snapshot names a destination the request overrides.
    "mlflow_destination": "databricks",
}


def _scored_frame() -> pl.DataFrame:
    """Two quotes x three scenario steps — the standard online-apply input."""
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
            "scenario_index": [0, 1, 2, 0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1],
            "predicted_income": [90.0, 100.0, 110.0, 45.0, 50.0, 55.0],
            "predicted_volume": [1.0, 0.9, 0.7, 1.0, 0.95, 0.8],
        }
    )


def _seed_optimiser_job(store: Any, job_id: str, config: dict[str, Any] | None = None) -> None:
    # Logging publishes from the completion summaries alone.
    seed_job(
        store,
        job_id,
        {
            "status": "completed",
            "result": {
                "mode": "online",
                "lambdas": {"predicted_volume": 0.0},
                "total_objective": 165.0,
                "baseline_objective": 135.0,
                "constraints": {"predicted_volume": 0.9},
                "baseline_constraints": {"predicted_volume": 1.0},
                "converged": True,
                "iterations": 10,
            },
            "publish_summary": {
                "params": {"mode": "online"},
                "metrics": {"total_objective": 165.0},
                "artifacts": {},
            },
            "config": dict(config or OPTIMISER_CONFIG),
            "node_label": "opt",
            "created_at": time.time(),
            "completed_at": time.time(),
        },
    )


def _log_optimiser_to_local(client: Any, job_id: str, experiment: str) -> str:
    resp = client.post(
        "/api/optimiser/mlflow/log",
        json={"job_id": job_id, "destination": "local", "experiment_name": experiment},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backend"] == "local"
    assert body["tracking_uri"].startswith("file:")
    return str(body["run_id"])


def _download_json(
    run_id: str,
    tracking_uri: str,
    name: str = OPTIMISER_ARTIFACT,
) -> dict[str, Any]:
    local = mlflow.artifacts.download_artifacts(
        f"runs:/{run_id}/{name}",
        tracking_uri=tracking_uri,
    )
    with open(local, encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _model_score_config(run_id: str, destination: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "sourceType": "run",
        "run_id": run_id,
        "artifact_path": MODEL_ARTIFACT,
        "task": "regression",
        "output_column": "prediction",
    }
    if destination is not None:
        config["mlflow_destination"] = destination
    return config


def _optimiser_apply_config(run_id: str, destination: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "sourceType": "run",
        "run_id": run_id,
        "version": "latest",
        "version_column": "__optimiser_version__",
    }
    if destination is not None:
        config["mlflow_destination"] = destination
    return config


def _model_score_graph(data_path: Path, run_id: str, destination: str | None) -> Any:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config(str(data_path)),
                    },
                },
                {
                    "id": "score",
                    "data": {
                        "label": "score",
                        "nodeType": "modelScore",
                        "config": _model_score_config(run_id, destination),
                    },
                },
            ],
            "edges": [make_edge("source", "score").model_dump()],
        }
    )


# ---------------------------------------------------------------------------
# 1. Train + log to Local, then score from Local, while Databricks is configured
# ---------------------------------------------------------------------------


class TestModelScoreFromExplicitLocal:
    def test_train_log_local_then_score_from_local_while_databricks_is_configured(
        self,
        client: Any,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _configure_databricks(monkeypatch)
        _assert_databricks_configured_but_not_chosen()

        model_path, native_model = _train_catboost(project)
        run_id = _log_model_to_local(client, "e2e_model_local", model_path, "e2e_model")

        score_df = pl.DataFrame(
            {
                "x": [0.1, 0.4, 0.9, 0.25],
                "c": ["red", "blue", "green", "red"],
            }
        )
        data_path = project / "score.parquet"
        score_df.write_parquet(data_path)

        graph = _model_score_graph(data_path, run_id, None)
        with _databricks_resolution_forbidden():
            results = execute_graph(graph, target_node_id="score")

        node = results["score"]
        assert node.status == "ok", node.error
        assert "prediction" in [column.name for column in node.columns]
        assert node.row_count == score_df.height

        expected = np.asarray(native_model.predict(score_df.to_pandas())).ravel()
        actual = np.asarray([row["prediction"] for row in node.preview])
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=0.0)

        # Databricks is still configured, and still not chosen, after the local load.
        _assert_databricks_configured_but_not_chosen()


# ---------------------------------------------------------------------------
# 2. Log an optimiser artifact to Local, then apply it from Local
# ---------------------------------------------------------------------------


class TestOptimiserApplyFromExplicitLocal:
    def test_optimiser_log_local_then_apply_from_local_while_databricks_is_configured(
        self,
        client: Any,
        clean_job_store: Any,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute._node_apply import apply_optimiser_apply_from_config

        _configure_databricks(monkeypatch)
        _assert_databricks_configured_but_not_chosen()

        _seed_optimiser_job(clean_job_store, "e2e_opt_local")
        run_id = _log_optimiser_to_local(client, "e2e_opt_local", "e2e_optimiser")

        local_uri = (project / "mlruns").as_uri()
        logged = _download_json(run_id, local_uri)
        assert logged["lambdas"] == {"predicted_volume": 0.0}

        with _databricks_resolution_forbidden():
            applied = apply_optimiser_apply_from_config(
                _scored_frame().lazy(),
                config=_optimiser_apply_config(run_id, None),
                source_names=["scored"],
            ).collect()

        assert applied["__optimiser_version__"].to_list() == [logged["version"]] * 2
        # Zero lambdas → each quote takes its highest-objective scenario step.
        assert applied["quote_id"].to_list() == ["q1", "q2"]
        assert applied["optimal_scenario_value"].to_list() == pytest.approx([1.1, 1.1])
        assert applied["optimal_objective"].to_list() == pytest.approx([110.0, 55.0])

        _assert_databricks_configured_but_not_chosen()


# ---------------------------------------------------------------------------
# 3. Deployed optimiser scoring loads from Local while Databricks is configured
# ---------------------------------------------------------------------------


class TestDeployedOptimiserApplyFromExplicitLocal:
    def test_deployed_optimiser_scoring_uses_local_while_databricks_is_configured(
        self,
        client: Any,
        clean_job_store: Any,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.deploy._scorer import score_graph

        _configure_databricks(monkeypatch)
        _assert_databricks_configured_but_not_chosen()

        _seed_optimiser_job(clean_job_store, "e2e_opt_deploy")
        run_id = _log_optimiser_to_local(client, "e2e_opt_deploy", "e2e_deploy")
        logged = _download_json(run_id, (project / "mlruns").as_uri())

        input_df = _scored_frame()
        placeholder = project / "deploy_input.parquet"
        input_df.write_parquet(placeholder)

        output_fields = [
            "quote_id",
            "optimal_scenario_value",
            "optimal_objective",
            "__optimiser_version__",
        ]
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataInput",
                            "config": make_file_input_config(str(placeholder)),
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "opt",
                            "nodeType": "optimiserApply",
                            "config": _optimiser_apply_config(run_id, None),
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config(output_fields, source_port="opt"),
                        },
                    },
                ],
                "edges": [
                    make_edge("src", "opt").model_dump(),
                    make_edge("opt", "out").model_dump(),
                ],
            }
        )

        with _databricks_resolution_forbidden():
            scored = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="out",
            )

        assert scored["quote_id"].to_list() == ["q1", "q2"]
        assert scored["__optimiser_version__"].to_list() == [logged["version"]] * 2
        assert scored["optimal_scenario_value"].to_list() == pytest.approx([1.1, 1.1])
        assert scored["optimal_objective"].to_list() == pytest.approx([110.0, 55.0])

        _assert_databricks_configured_but_not_chosen()


# ---------------------------------------------------------------------------
# 4. An unavailable explicit destination fails without consulting another
# ---------------------------------------------------------------------------


class TestUnavailableExplicitDestination:
    def test_unavailable_explicit_destination_fails_without_consulting_another_backend(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _configure_databricks(monkeypatch)
        _assert_databricks_configured_but_not_chosen()

        score_df = pl.DataFrame({"x": [0.1, 0.4], "c": ["red", "blue"]})
        data_path = project / "score.parquet"
        score_df.write_parquet(data_path)

        graph = _model_score_graph(data_path, "run-that-only-exists-remotely", "server")

        local_loader = MagicMock(name="load_local_model")

        def _forbidden_databricks() -> Any:
            raise AssertionError("Databricks was resolved for an explicit 'server' load")

        def _forbidden_local(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Local was resolved for an explicit 'server' load")

        with (
            patch("haute._mlflow_io.load_local_model", local_loader),
            patch(
                "haute.modelling._mlflow_settings._resolve_databricks",
                side_effect=_forbidden_databricks,
            ),
            patch(
                "haute.modelling._mlflow_settings._resolve_local",
                side_effect=_forbidden_local,
            ),
            pytest.raises(MlflowConfigError, match="MLflow server is not configured"),
        ):
            execute_graph(graph, target_node_id="score")

        local_loader.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Generated scripts honour the selected destination for both read nodes
# ---------------------------------------------------------------------------

RUNNER_TEMPLATE = '''"""Standalone driver: run the generated pipeline file from disk."""

import runpy

import polars as pl

from haute.modelling import _mlflow_settings
from haute.modelling._mlflow_settings import TrackingConfig

# The server destination must be a *second*, non-local backend.  Offline, the
# only way to hold two live backends at once is to back the "server"
# destination with a file store, exactly as Task A4's tests do in-process.
_SERVER = TrackingConfig("server", {server_uri!r}, "http://stub.invalid:5000", "toml")
_mlflow_settings._resolve_server = lambda stored=None: _SERVER

namespace = runpy.run_path("pipeline.py", run_name="haute_generated_pipeline")
result = namespace["pipeline"].run()
if isinstance(result, pl.LazyFrame):
    result = result.collect()
result.write_parquet("pipeline_output.parquet")
'''


def _pipeline_input_frame() -> pl.DataFrame:
    """Scenario rows carrying the model's own feature columns."""
    return pl.DataFrame(
        {
            "quote_id": ["q1", "q1", "q1", "q2", "q2", "q2"],
            "scenario_index": [0, 1, 2, 0, 1, 2],
            "scenario_value": [0.9, 1.0, 1.1, 0.9, 1.0, 1.1],
            "predicted_volume": [1.0, 0.95, 0.9, 1.0, 0.95, 0.9],
            "x": [0.10, 0.45, 0.80, 0.20, 0.55, 0.90],
            "c": ["red", "blue", "green", "blue", "red", "green"],
        }
    )


def _expected_optimiser_rows(model: Any, version: str) -> dict[str, list[Any]]:
    """What the pipeline must emit when it reads *model* and *version*.

    The optimiser artifact's objective is the model score node's output
    column, so the emitted ``optimal_objective`` is literally the chosen
    row's prediction — which differs between the two seeded backends.
    """
    frame = _pipeline_input_frame()
    predictions = np.asarray(model.predict(frame.select(FEATURES).to_pandas())).ravel()
    scored = frame.with_columns(pl.Series("prediction", predictions))
    best = (
        scored.sort("prediction", descending=True)
        .group_by("quote_id", maintain_order=False)
        .first()
        .sort("quote_id")
    )
    return {
        "quote_id": best["quote_id"].to_list(),
        "optimal_scenario_value": best["scenario_value"].to_list(),
        "optimal_objective": best["prediction"].to_list(),
        "__optimiser_version__": [version] * best.height,
    }


def _copy_store(source: Path, target: Path) -> None:
    """Clone a file-store tree, repointing every absolute URI it records.

    MLflow's file store writes the run's ``artifact_uri`` and the
    experiment's ``artifact_location`` as absolute URIs, so a plain copy
    would keep serving the original store's artifacts.
    """
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    source_uri = source.as_uri()
    target_uri = target.as_uri()
    for meta in target.rglob("meta.yaml"):
        text = meta.read_text(encoding="utf-8")
        if source_uri in text:
            meta.write_text(text.replace(source_uri, target_uri), encoding="utf-8")


def _run_artifact_dir(store: Path, run_id: str) -> Path:
    matches = [path for path in store.rglob(f"{run_id}/artifacts") if path.is_dir()]
    assert len(matches) == 1, f"expected one artifacts dir for {run_id}, found {matches}"
    return matches[0]


def _materialise_pipeline(
    project_root: Path,
    graph: Any,
    input_df: pl.DataFrame,
    *,
    local_folder: Path,
) -> None:
    from haute._config_io import collect_node_configs
    from haute.codegen import graph_to_code

    # ``get_project_root`` requires the project to be a git repository.
    (project_root / ".git").mkdir(exist_ok=True)
    (project_root / "haute.toml").write_text(
        f'[project]\nname = "destinations-e2e"\n\n[mlflow]\nfolder = "{local_folder.as_posix()}"\n',
        encoding="utf-8",
    )
    code = graph_to_code(graph, pipeline_name="destinations_e2e")
    for rel_path, content in collect_node_configs(graph).items():
        config_file = project_root / rel_path
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content, encoding="utf-8")
    input_df.write_parquet(project_root / "data.parquet")
    (project_root / "pipeline.py").write_text(code, encoding="utf-8")


def _run_generated_pipeline(project_root: Path, server_store: Path) -> pl.DataFrame:
    """Execute the generated ``pipeline.py`` in a fresh interpreter."""
    runner = project_root / "run_generated_pipeline.py"
    runner.write_text(RUNNER_TEMPLATE.format(server_uri=server_store.as_uri()), encoding="utf-8")
    output = project_root / "pipeline_output.parquet"
    if output.exists():
        output.unlink()
    completed = subprocess.run(  # noqa: S603 — fixed argv, test-owned script
        [sys.executable, str(runner)],
        cwd=str(project_root),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, (
        f"generated pipeline failed ({completed.returncode})\n"
        f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
    )
    return pl.read_parquet(output)


class TestGeneratedScriptDestinations:
    def test_generated_scripts_honour_the_selected_destination_for_both_read_nodes(
        self,
        client: Any,
        clean_job_store: Any,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        local_store = project / "store_local"
        server_store = project / "store_server"
        server_config_factory: Callable[..., Any] = lambda stored=None: __import__(  # noqa: E731
            "haute.modelling._mlflow_settings", fromlist=["TrackingConfig"]
        ).TrackingConfig("server", server_store.as_uri(), "http://stub.invalid:5000", "toml")

        # --- Seed store A (Local) through the real log routes ---------------
        (project / "haute.toml").write_text(
            '[project]\nname = "destinations-e2e"\n\n'
            f'[mlflow]\nfolder = "{local_store.as_posix()}"\n',
            encoding="utf-8",
        )
        local_model_path, local_model = _train_catboost(project / "model_a", seed=7, scale=2.0)
        model_run = _log_model_to_local(client, "e2e_gen_model", local_model_path, "e2e_gen")
        _seed_optimiser_job(
            clean_job_store,
            "e2e_gen_opt",
            {**OPTIMISER_CONFIG, "objective": "prediction"},
        )
        optimiser_run = _log_optimiser_to_local(client, "e2e_gen_opt", "e2e_gen_opt")

        local_uri = local_store.as_uri()
        local_artifact = _download_json(optimiser_run, local_uri)

        # --- Clone into store B and make its contents distinguishable -------
        _copy_store(local_store, server_store)
        server_model_path, server_model = _train_catboost(project / "model_b", seed=99, scale=5.0)
        # Write targets are spelled from the tmp-rooted store so the sandbox
        # lint can see they never leave it; the run directories mirror the
        # cloned local store exactly.
        model_run_rel = _run_artifact_dir(local_store, model_run).relative_to(local_store)
        optimiser_run_rel = _run_artifact_dir(local_store, optimiser_run).relative_to(local_store)
        shutil.copyfile(server_model_path, server_store / model_run_rel / MODEL_ARTIFACT)
        server_artifact = dict(local_artifact)
        server_artifact["version"] = "server_side_version"
        server_artifact["lambdas"] = {"predicted_volume": 0.25}
        (server_store / optimiser_run_rel / OPTIMISER_ARTIFACT).write_text(
            json.dumps(server_artifact, indent=2),
            encoding="utf-8",
        )

        input_df = _pipeline_input_frame()

        def _build_graph(destination: str | None) -> Any:
            return make_graph(
                {
                    "nodes": [
                        {
                            "id": "source",
                            "data": {
                                "label": "source",
                                "nodeType": "dataInput",
                                "config": make_file_input_config("data.parquet"),
                            },
                        },
                        {
                            "id": "scorer",
                            "data": {
                                "label": "scorer",
                                "nodeType": "modelScore",
                                "config": _model_score_config(model_run, destination),
                            },
                        },
                        {
                            "id": "applier",
                            "data": {
                                "label": "applier",
                                "nodeType": "optimiserApply",
                                "config": _optimiser_apply_config(optimiser_run, destination),
                            },
                        },
                    ],
                    "edges": [
                        make_edge("source", "scorer").model_dump(),
                        make_edge("scorer", "applier").model_dump(),
                    ],
                }
            )

        # --- Destination absent: the local store A is used although B is configured
        local_root = project / "local"
        local_root.mkdir()
        with patch(
            "haute.modelling._mlflow_settings._resolve_server",
            server_config_factory,
        ):
            _materialise_pipeline(
                local_root,
                _build_graph(None),
                input_df,
                local_folder=local_store,
            )
        assert "mlflow_destination" not in json.loads(
            (local_root / "config" / "model_scoring" / "scorer.json").read_text(encoding="utf-8")
        )
        local_out = _run_generated_pipeline(local_root, server_store)
        expected_local = _expected_optimiser_rows(local_model, str(local_artifact["version"]))
        assert local_out.sort("quote_id")["quote_id"].to_list() == expected_local["quote_id"]
        assert (
            local_out.sort("quote_id")["__optimiser_version__"].to_list()
            == (expected_local["__optimiser_version__"])
        )
        assert local_out.sort("quote_id")["optimal_scenario_value"].to_list() == pytest.approx(
            expected_local["optimal_scenario_value"]
        )
        assert local_out.sort("quote_id")["optimal_objective"].to_list() == pytest.approx(
            expected_local["optimal_objective"]
        )

        # --- Explicit "server": store B is used ------------------------------
        server_root = project / "server"
        server_root.mkdir()
        with patch(
            "haute.modelling._mlflow_settings._resolve_server",
            server_config_factory,
        ):
            _materialise_pipeline(
                server_root,
                _build_graph("server"),
                input_df,
                local_folder=local_store,
            )
        server_out = _run_generated_pipeline(server_root, server_store)
        expected_server = _expected_optimiser_rows(server_model, "server_side_version")
        assert (
            server_out.sort("quote_id")["__optimiser_version__"].to_list()
            == (expected_server["__optimiser_version__"])
        )
        assert server_out.sort("quote_id")["optimal_objective"].to_list() == pytest.approx(
            expected_server["optimal_objective"]
        )
        assert expected_server["optimal_objective"] != pytest.approx(
            expected_local["optimal_objective"]
        )
