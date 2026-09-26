"""Cross-family release acceptance (MOD-F05).

Every shipped native family, CatBoost included, is held to the same binary
classification contract and scores identically through every path a trained
model takes: eager and batched scoring, the shared MLflow pyfunc package, and
the deployed scorer's cached loader.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import load_local_model
from haute._model_explainability import prediction_tolerance
from haute._model_scorer import score_frame
from haute.errors import HauteValidationError
from haute.modelling._training_job import TrainingJob, model_contract_filename

EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 5,
    "validation": {"method": "single", "size": 0.2},
}
PARAMS = {
    "catboost": {"iterations": 20, "depth": 3},
    "xgboost": {"num_boost_round": 20, "eta": 0.3, "max_depth": 3},
    "lightgbm": {
        "num_iterations": 20,
        "learning_rate": 0.3,
        "num_leaves": 7,
        "min_data_in_leaf": 5,
    },
    "ebm": {"max_rounds": 60, "interactions": 0},
}
FAMILIES = sorted(PARAMS)


def frame(n: int = 500, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice(["east", "north", "south"], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.2, 1.0, n)
    rate = np.exp(-2 + 0.01 * (age - 40) + np.where(region == "north", 0.4, 0.0))
    positive = rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 8))
    return pl.DataFrame(
        {
            "region": region.tolist(),
            "age": age,
            "exposure": exposure,
            "claims": rng.poisson(rate * exposure).astype(float),
            "flag": np.where(positive, "yes", "no").tolist(),
            "is_claim": positive.tolist(),
            "tier": rng.choice(["a", "b", "c"], n).tolist(),
            "row": np.arange(n),
        }
    )


def train(tmp_path: Path, family: str, **kwargs: object):
    return TrainingJob(
        name=family,
        data=kwargs.pop("data", frame()),
        target=kwargs.pop("target", "claims"),
        algorithm=family,
        loss_function=kwargs.pop("loss", "Poisson"),
        params=PARAMS[family],
        metrics=kwargs.pop("metrics", ["poisson_deviance"]),
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        feature_columns=["region", "age"],
        **kwargs,
    ).run()


def _deploy_graph(artifact_path: str, task: str) -> object:
    from tests.conftest import make_graph, make_output_config

    # Output assembly merges identical elements, so each row carries its id.
    fields = ["row", "pred", "pred_proba"] if task == "classification" else ["row", "pred"]
    return make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "ms",
                    "data": {
                        "label": "ms",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": "run",
                            "artifact_path": artifact_path,
                            "task": task,
                            "output_column": "pred",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(fields),
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "ms", "sourceHandle": "src"},
                {"id": "e2", "source": "ms", "target": "out"},
            ],
        }
    )


def every_path(
    result, data: pl.DataFrame, task: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, pl.DataFrame]:
    """Score *data* through every serving path; each returns prediction (and proba).

    ``deployed`` runs the bundled deploy graph (``score_graph`` with the model and
    its contract remapped as the bundler writes them); ``model_score`` is the Model
    Score node runtime that generated pipeline code calls, loading a local MLflow run.
    """
    import mlflow

    from haute._mlflow_utils import mlflow_fluent_operation, set_tracking_uri_preserving_env
    from haute._model_scorer import ModelScorer
    from haute._sandbox import set_project_root
    from haute.deploy._scorer import _clear_deploy_artifact_caches, score_graph
    from haute.modelling._feature_contract import CONTRACT_FILENAME
    from haute.modelling._mlflow_log import resolve_tracking_backend
    from haute.modelling._native_pyfunc import NativePyfuncModel, package_native_model

    model_path = Path(result.model_path)
    contract = model_path.parent / model_contract_filename(model_path.stem)
    scoring = load_local_model(str(model_path), task=task)
    proba = task == "classification"

    def through(scoring_model, *, batch: bool) -> pl.DataFrame:
        return score_frame(
            model=scoring_model.raw_model,
            lf=data.lazy(),
            features=list(scoring_model.feature_names),
            cat_feature_names=scoring_model.cat_feature_names,
            flavor=scoring_model.flavor,
            task=task,
            batch=batch,
            offset_column=scoring_model.offset_column,
        ).collect()

    def renamed(frame: pl.DataFrame, column: str) -> pl.DataFrame:
        columns = {column: "prediction"}
        if proba:
            columns[f"{column}_proba"] = "prediction_proba"
        return frame.select(list(columns)).rename(columns)

    # Deployed scoring reads bundle files only from inside the project root.
    set_project_root(model_path.parent)
    _clear_deploy_artifact_caches()
    deployed = score_graph(
        graph=_deploy_graph(model_path.name, task),
        input_df=data,
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths={
            f"ms__{model_path.name}": str(model_path),
            f"ms__{CONTRACT_FILENAME}": str(contract),
        },
    )
    _clear_deploy_artifact_caches()

    package = package_native_model(model_path, contract, tmp_path / f"package_{task}")
    served = NativePyfuncModel(str(package)).predict(data.to_pandas())
    if proba:
        pyfunc = pl.DataFrame(
            {
                "prediction": served["pred_label"].tolist(),
                "prediction_proba": served["pred_proba"].to_numpy(),
            }
        )
    else:
        pyfunc = pl.DataFrame({"prediction": np.asarray(served, dtype=np.float64)})

    # The MLflow artifact cache nests hashed folders under the working directory;
    # the model's own folder keeps those paths within Windows' length limit.
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(model_path.parent)
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow, resolve_tracking_backend("")[0])
        with mlflow.start_run() as run:
            mlflow.log_artifact(str(model_path))
            mlflow.log_artifact(str(contract))
    node = ModelScorer(
        source_type="run",
        run_id=run.info.run_id,
        artifact_path=model_path.name,
        task=task,
        output_col="pred",
    ).score(data.lazy())
    return {
        "eager": through(scoring, batch=False),
        "batch": through(scoring, batch=True),
        "deployed": renamed(deployed.sort("row"), "pred"),
        "model_score": renamed(node.collect() if hasattr(node, "collect") else node, "pred"),
        "pyfunc": pyfunc,
    }


def assert_close(values: np.ndarray, reference: np.ndarray) -> None:
    tolerance = np.array([prediction_tolerance(float(v)) for v in reference])
    assert np.all(np.abs(values - reference) <= tolerance)


@pytest.mark.parametrize("family", FAMILIES)
def test_regression_scores_identically_on_every_path(
    tmp_path: Path, family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = frame(seed=3)
    result = train(tmp_path, family, offset="exposure")
    paths = every_path(result, data, "regression", tmp_path, monkeypatch)
    reference = paths["eager"]["prediction"].to_numpy()
    for name, scored in paths.items():
        assert scored.height == data.height, name
        assert_close(scored["prediction"].to_numpy(), reference)
    # The offset is part of every path: doubling exposure doubles the prediction.
    doubled = every_path(
        result,
        data.with_columns(pl.col("exposure") * 2),
        "regression",
        tmp_path / "doubled",
        monkeypatch,
    )
    assert_close(doubled["deployed"]["prediction"].to_numpy(), 2 * reference)


@pytest.mark.parametrize("family", FAMILIES)
def test_classification_labels_follow_the_probability_on_every_path(
    tmp_path: Path, family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = frame(seed=4)
    result = train(
        tmp_path,
        family,
        target="flag",
        loss="Logloss",
        task="classification",
        positive_class="yes",
        metrics=["auc"],
    )
    paths = every_path(result, data, "classification", tmp_path, monkeypatch)
    reference = paths["eager"]["prediction_proba"].to_numpy()
    assert 0 < reference.min() and reference.max() < 1
    for name, scored in paths.items():
        proba = scored["prediction_proba"].to_numpy()
        assert_close(proba, reference)
        expected = np.where(proba > 0.5, "yes", "no").tolist()
        assert scored["prediction"].to_list() == expected, name


@pytest.mark.parametrize("family", FAMILIES)
def test_a_boolean_target_is_positive_at_true(tmp_path: Path, family: str) -> None:
    result = train(
        tmp_path, family, target="is_claim", loss="Logloss", task="classification", metrics=["auc"]
    )
    scoring = load_local_model(result.model_path, task="classification")
    scored = score_frame(
        model=scoring.raw_model,
        lf=frame(seed=6).lazy(),
        features=list(scoring.feature_names),
        cat_feature_names=scoring.cat_feature_names,
        flavor=scoring.flavor,
        task="classification",
    ).collect()
    labels = scored["prediction"].to_list()
    assert set(labels) <= {True, False}
    assert labels == (scored["prediction_proba"].to_numpy() > 0.5).tolist()


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    ("target", "extra", "message"),
    [
        ("flag", {}, "choose which one is the positive class"),
        ("tier", {"positive_class": "a"}, "exactly two"),
    ],
)
def test_invalid_binary_targets_fail_before_fitting(
    tmp_path: Path, family: str, target: str, extra: dict, message: str
) -> None:
    with pytest.raises(HauteValidationError, match=message):
        train(
            tmp_path,
            family,
            target=target,
            loss="Logloss",
            task="classification",
            metrics=["auc"],
            **extra,
        )
    assert not list(tmp_path.glob(f"{family}.*"))


class _CancelledError(Exception):
    pass


@pytest.mark.parametrize("family", ["xgboost", "lightgbm", "ebm"])
def test_a_native_fit_stops_at_the_first_progress_report_after_cancellation(
    tmp_path: Path, family: str
) -> None:
    """The worker cancels a running fit by raising from its progress callback:
    XGBoost and LightGBM report every round (so a fit stops mid-boosting), EBM
    before its fit starts. Nothing is saved from a cancelled fit."""
    reports: list[int] = []

    def cancel_after_first(iteration: int, total: int, metrics: dict, row: dict | None) -> None:
        reports.append(iteration)
        raise _CancelledError

    with pytest.raises(_CancelledError):
        TrainingJob(
            name=family,
            data=frame(),
            target="claims",
            algorithm=family,
            loss_function="Poisson",
            params=PARAMS[family],
            metrics=["poisson_deviance"],
            output_dir=str(tmp_path),
            evaluation=EVALUATION,
            feature_columns=["region", "age"],
        ).run(on_iteration=cancel_after_first)
    assert len(reports) == 1
    assert reports[0] <= 1
    assert not list(tmp_path.glob(f"{family}.*"))


# Four real trainings through the service; under CI coverage one CatBoost run
# (fit, diagnostics, SHAP) takes about 18 s, beyond the 60 s default.
@pytest.mark.parametrize("family", ["catboost", "lightgbm", "xgboost"])
@pytest.mark.parametrize("refit", [False, True], ids=["validation-fit", "final-refit"])
def test_a_boosted_fit_reports_its_loss_history_rows_as_it_trains(
    tmp_path: Path, family: str, refit: bool
) -> None:
    """The live chart reads the same prefixed rows the Loss tab does (MDL-02)."""
    readouts: list[dict[str, float]] = []
    rows: list[dict[str, float] | None] = []

    def record(
        iteration: int, total: int, metrics: dict[str, float], row: dict[str, float] | None
    ) -> None:
        readouts.append(metrics)
        rows.append(row)

    result = TrainingJob(
        name=family,
        data=frame(),
        target="claims",
        algorithm=family,
        loss_function="Poisson",
        params=PARAMS[family],
        metrics=["poisson_deviance"],
        output_dir=str(tmp_path),
        evaluation=EVALUATION,
        refit_on_development=refit,
        feature_columns=["region", "age"],
    ).run(on_iteration=record)

    # A refit trains the validation fit's weighted round count, not the configured 20.
    assert len(rows) > 1
    for number, row in enumerate(rows, start=1):
        assert row is not None
        assert row["iteration"] == number
        assert any(key.startswith("train_") for key in row)
        # Only a fit with an evaluation set (the kept validation fit) has eval rows.
        assert any(key.startswith("eval_") for key in row) is not refit
    # The readout keeps each engine's own metric names; only the rows are prefixed.
    assert not any(key.startswith(("train_", "eval_")) for key in readouts[-1])
    assert result.loss_history == rows


@pytest.mark.timeout(240)
@pytest.mark.parametrize("family", FAMILIES)
def test_native_training_lifecycle_keeps_the_last_good_model(
    tmp_path: Path,
    family: str,
    monkeypatch: pytest.MonkeyPatch,
    training_artifact_root: Path,
) -> None:
    """Train, cancel, fail and train again through the real service and a real
    native fit: a cancelled or failed run publishes nothing, the last completed
    run's model still loads and scores (its result restores as it was), and a
    newer success publishes its own model."""
    from haute.modelling._algorithms import ALGORITHM_REGISTRY
    from haute.routes._job_store import JobStore
    from haute.routes._train_service import TrainService
    from haute.schemas import TrainRequest
    from tests.conftest import make_edge, make_graph
    from tests.test_training_worker_protocol import _inline_protocol_runner

    store = JobStore()
    cancel_job_id: str | None = None

    def protocol_runner(function, request, **kwargs):
        result = _inline_protocol_runner(function, request, **kwargs)
        if request.request_id == cancel_job_id:
            assert service.cancel(request.request_id)["status"] == "cancelled"
        return result

    service = TrainService(store, protocol_runner=protocol_runner)
    launched = []
    original_launch = service._supervisor.launch_protocol

    def capture_launch(*args, **kwargs):
        thread = original_launch(*args, **kwargs)
        launched.append(thread)
        return thread

    monkeypatch.setattr(service._supervisor, "launch_protocol", capture_launch)
    config = {
        "name": "quoted",
        "target": "claims",
        "algorithm": family,
        "loss_function": "Poisson",
        "params": PARAMS[family],
        "feature_columns": ["region", "age"],
        "metrics": ["poisson_deviance"],
        "evaluation": EVALUATION,
    }
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {"path": "data.parquet"},
                    },
                },
                {
                    "id": "quoted",
                    "data": {"label": "quoted", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [make_edge("source", "quoted").model_dump()],
        }
    )
    body = TrainRequest(graph=graph, node_id="quoted")

    def execute_and_sink(_body, _preamble_ns, _row_limit, job_id, **kwargs):
        prepared = tmp_path / f"prep_{job_id}.parquet"
        frame().write_parquet(prepared)
        return str(prepared)

    monkeypatch.setattr(service, "_compile_preamble", lambda _graph: None)
    monkeypatch.setattr(service, "_estimate_ram", lambda *a, **k: (None, None, 100, 3))
    monkeypatch.setattr(service, "_check_gpu_vram_before_launch", lambda *a, **k: None)
    monkeypatch.setattr(service, "_execute_and_sink", execute_and_sink)

    def run_once() -> dict:
        response = service.start(body)
        assert response.status == "started"
        service._join_preparation(response.job_id)
        launched[-1].join_and_raise(timeout=120)
        return store.require_job(response.job_id)

    def scores(job: dict) -> np.ndarray:
        scoring = load_local_model(job["result"].model_path)
        return (
            score_frame(
                model=scoring.raw_model,
                lf=frame(seed=9).lazy(),
                features=list(scoring.feature_names),
                cat_feature_names=scoring.cat_feature_names,
                flavor=scoring.flavor,
            )
            .collect()["prediction"]
            .to_numpy()
        )

    first = run_once()
    assert first["status"] == "completed"
    first_scores = scores(first)

    # A cancelled run publishes nothing and leaves the completed model intact.
    response = service.start(body)
    cancel_job_id = response.job_id
    service._join_preparation(response.job_id)
    launched[-1].join_and_raise(timeout=120)
    cancelled_run = store.require_job(response.job_id)
    assert cancelled_run["status"] == "cancelled"
    assert "artifact_handles" not in cancelled_run
    np.testing.assert_array_equal(scores(first), first_scores)
    cancel_job_id = None

    # A failing native fit publishes nothing either.
    def failing_fit(self, *args, **kwargs):
        raise RuntimeError("injected native failure")

    with monkeypatch.context() as patched:
        patched.setattr(ALGORITHM_REGISTRY[family], "fit", failing_fit)
        failed = run_once()
    assert failed["status"] == "error"
    assert "artifact_handles" not in failed
    np.testing.assert_array_equal(scores(first), first_scores)

    # A newer success publishes its own model, which scores.
    second = run_once()
    assert second["status"] == "completed"
    assert Path(second["result"].model_path) != Path(first["result"].model_path)
    assert np.isfinite(scores(second)).all()


def test_only_a_haute_binary_contract_declares_the_probability_column(tmp_path: Path) -> None:
    from haute._builders import _model_score_columns
    from haute.modelling._feature_contract import build_contract, save_contract

    result = train(
        tmp_path,
        "xgboost",
        target="flag",
        loss="Logloss",
        task="classification",
        positive_class="yes",
        metrics=["auc"],
    )
    model_path = Path(result.model_path)
    haute_contract = model_path.parent / model_contract_filename(model_path.stem)
    config = {"task": "classification", "output_column": "pred"}
    produced, _ = _model_score_columns({**config, "feature_contract_path": str(haute_contract)})
    assert produced == {"pred", "pred_proba"}
    # A contract without Haute class labels (a prediction-only classifier) promises none.
    bare = tmp_path / "bare.json"
    save_contract(
        build_contract(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="String",
            task="classification",
        ),
        bare,
    )
    assert _model_score_columns({**config, "feature_contract_path": str(bare)})[0] == {"pred"}
    assert _model_score_columns(config)[0] == {"pred"}


def test_a_prediction_only_classifier_deploys_its_labels(tmp_path: Path) -> None:
    """A classifier without ``predict_proba`` scores labels only; its deployed
    graph must not demand a probability column the model never writes."""
    from unittest.mock import patch

    from sklearn.svm import LinearSVC

    from haute._mlflow_io import ScoringModel
    from haute._sandbox import set_project_root
    from haute.deploy._scorer import _clear_deploy_artifact_caches, score_graph
    from haute.modelling._feature_contract import (
        CONTRACT_FILENAME,
        build_contract,
        save_contract,
    )
    from tests.conftest import make_graph, make_output_config

    data = frame(seed=8)
    classifier = LinearSVC().fit(data.select("age").to_numpy(), data["flag"].to_list())
    model_file = tmp_path / "svc.cbm"
    model_file.write_bytes(b"labels-only model")
    contract = tmp_path / CONTRACT_FILENAME
    save_contract(
        build_contract(
            features=["age"],
            feature_types={"age": "Float64"},
            categorical_features=[],
            target_name="flag",
            target_type="String",
            task="classification",
        ),
        contract,
    )
    scoring = ScoringModel(
        model=classifier, feature_names=["age"], cat_feature_names=frozenset(), flavor="pyfunc"
    )
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "ms",
                    "data": {
                        "label": "ms",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "run",
                            "run_id": "run",
                            "artifact_path": "svc.cbm",
                            "task": "classification",
                            "output_column": "pred",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["row", "pred"]),
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "ms", "sourceHandle": "src"},
                {"id": "e2", "source": "ms", "target": "out"},
            ],
        }
    )
    set_project_root(tmp_path)
    _clear_deploy_artifact_caches()
    with patch("haute._mlflow_io.load_local_model", return_value=scoring):
        scored = score_graph(
            graph=graph,
            input_df=data.select("row", "age"),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={
                "ms__svc.cbm": str(model_file),
                f"ms__{CONTRACT_FILENAME}": str(contract),
            },
        )
    _clear_deploy_artifact_caches()
    expected = classifier.predict(data.select("age").to_numpy()).tolist()
    assert scored.sort("row")["pred"].to_list() == expected


@pytest.mark.parametrize("family", FAMILIES)
def test_the_exported_training_script_trains_the_same_model(tmp_path: Path, family: str) -> None:
    """Executing the generated training script trains the model the canvas
    config trains: the same saved predictions, bit for bit."""
    from haute.modelling._export import generate_training_script
    from haute.modelling._train_config import build_training_job_kwargs

    data_path = tmp_path / "data.parquet"
    frame().write_parquet(data_path)

    def config(output: Path) -> dict:
        return {
            "name": family,
            "target": "claims",
            "algorithm": family,
            "task": "regression",
            "loss_function": "Poisson",
            "offset": "exposure",
            "params": PARAMS[family],
            "feature_columns": ["region", "age"],
            "metrics": ["poisson_deviance"],
            "evaluation": EVALUATION,
            "output_dir": str(output),
        }

    namespace = {"__name__": "exported_training_script"}
    script = generate_training_script(config(tmp_path / "script"), str(data_path))
    exec(compile(script, "<exported_training_script>", "exec"), namespace)  # noqa: S102
    exported = namespace["job"].run()
    direct = TrainingJob(
        **build_training_job_kwargs(config(tmp_path / "direct"), data=str(data_path))
    ).run()

    probe = frame(seed=10)

    def predict(path: str) -> np.ndarray:
        scoring = load_local_model(path)
        return (
            score_frame(
                model=scoring.raw_model,
                lf=probe.lazy(),
                features=list(scoring.feature_names),
                cat_feature_names=scoring.cat_feature_names,
                flavor=scoring.flavor,
                offset_column=scoring.offset_column,
            )
            .collect()["prediction"]
            .to_numpy()
        )

    assert np.array_equal(predict(exported.model_path), predict(direct.model_path))
    assert exported.fit_evidence == direct.fit_evidence
