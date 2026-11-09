"""The modelling export, save, MLflow log and model-cache routes.

A trained model's script is exported, the model is saved to its destination (with a preview of it),
a scripted job writes into ``outputs/`` by default, the job is logged to MLflow, and the model
cache is cleared, through the client and directly.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from haute._project_context import context_thread
from haute.errors import RequestRefusedError
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _completed_train_response,
    _make_modelling_graph,
    _random_evaluation_config,
)
from tests._project import use_project
from tests.conftest import (
    make_edge,
    make_file_input_config,
    make_graph,
)
from tests.job_store_support import seed_job
from tests.training_artifacts_support import publish_trained_job

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
training_data = _modelling_route_helpers.training_data


def _write_trained_model(directory: Path, name: str = "frequency") -> Path:
    """Write a model file and a complete feature contract as training leaves them."""
    from haute.modelling._feature_contract import build_contract, save_contract
    from haute.modelling._training_job import model_contract_filename

    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"{name}.cbm"
    model_path.write_bytes(b"model-bytes")
    save_contract(
        build_contract(
            features=["x1"],
            feature_types={"x1": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        ),
        directory / model_contract_filename(name),
    )
    return model_path


class TestExportEndpoint:
    def test_export_generates_script(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "train",
                "data_path": "output/data.parquet",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "script" in data
        assert "filename" in data
        assert "TrainingJob" in data["script"]
        assert "feature_columns=['x1', 'x2']," in data["script"]
        assert "exclude=" not in data["script"]
        assert data["filename"].endswith(".py")

    def test_export_reaches_a_modelling_node_inside_a_submodel(self, client, training_data):
        inner = _make_modelling_graph(training_data)
        graph = {
            "source_file": inner.get("source_file", "main.py"),
            "nodes": [
                {
                    "id": "rating",
                    "data": {
                        "label": "rating",
                        "nodeType": "submodel",
                        "config": {"definition_id": "rating", "alias": "rating"},
                    },
                }
            ],
            "edges": [],
            "submodels": {
                "rating": {
                    "definition_id": "rating",
                    "file": "modules/rating.py",
                    "graph": {"nodes": inner["nodes"], "edges": inner["edges"]},
                    "input_ports": [],
                    "output_ports": [],
                }
            },
        }
        resp = client.post(
            "/api/modelling/export",
            json={"graph": graph, "node_id": "submodel_runtime/rating/train"},
        )
        assert resp.status_code == 200, resp.text
        assert "TrainingJob" in resp.json()["script"]
        assert "feature_columns=['x1', 'x2']," in resp.json()["script"]

    def test_export_missing_node(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "nonexistent",
            },
        )
        assert resp.status_code == 404

    def test_export_missing_target_returns_sanitized_400(self, client, training_data):
        graph = _make_modelling_graph(training_data)
        graph["nodes"][1]["data"]["config"].pop("target")
        resp = client.post(
            "/api/modelling/export",
            json={
                "graph": graph,
                "node_id": "train",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "target column" in detail
        assert "config panel" in detail
        assert "Traceback" not in detail
        assert "ValueError" not in detail


class TestMlflowLogEndpoint:
    def test_mlflow_log_job_not_found(self, client):
        resp = client.post(
            "/api/modelling/mlflow/log",
            json={
                "job_id": "nonexistent",
            },
        )
        assert resp.status_code == 404

    def test_mlflow_log_job_not_completed(self, client, training_data):
        """Start a job, then immediately try to log — should fail with 400."""
        from haute.routes.modelling import _store

        # Inject a fake running job
        seed_job(
            _store,
            "fake_running",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Training...",
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "fake_running",
                },
            )
            assert resp.status_code == 400
            assert "not completed" in resp.json()["detail"]
        finally:
            _store.delete_job("fake_running")


class TestSaveModelEndpoint:
    """Tests for ``POST /api/modelling/save`` and its destination preview."""

    @pytest.fixture()
    def _seeded_model(self, tmp_path, monkeypatch, training_artifact_root):
        """Publish a completed job that owns its trained model + contract."""
        from haute.routes.modelling import _store

        project_root = tmp_path / "project"
        project_root.mkdir()
        model_path = _write_trained_model(tmp_path / "trained")
        published = publish_trained_job(
            _store,
            "save_job",
            root=training_artifact_root,
            model_file=model_path,
            result=_completed_train_response(job_id="save_job", model_path=str(model_path)),
            config={},
        )
        use_project(project_root)

        try:
            yield SimpleNamespace(
                model_path=published / "frequency.cbm",
                contract_path=published / "frequency.feature_contract.json",
                contract_text=(published / "frequency.feature_contract.json").read_text(),
                project_root=project_root,
                models=project_root / "models",
            )
        finally:
            _store.delete_job("save_job")

    @staticmethod
    def _save(client, output_path: str, **extra: object):
        return client.post(
            "/api/modelling/save",
            json={"job_id": "save_job", "output_path": output_path, **extra},
        )

    def test_save_model_job_not_found(self, client):
        resp = client.post(
            "/api/modelling/save",
            json={"job_id": "nonexistent", "output_path": "frequency"},
        )
        assert resp.status_code == 404

    def test_save_model_job_not_completed(self, client):
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "fake_running_save",
            {
                "status": "running",
                "progress": 0.5,
                "message": "Training...",
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post(
                "/api/modelling/save",
                json={"job_id": "fake_running_save", "output_path": "frequency"},
            )
            assert resp.status_code == 400
        finally:
            _store.delete_job("fake_running_save")

    def test_bare_filename_saves_model_and_contract_under_models(self, client, _seeded_model):
        resp = self._save(client, "frequency")

        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "path": "models/frequency.cbm",
            "feature_contract_path": "models/frequency.feature_contract.json",
        }
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text
        assert _seeded_model.model_path.read_bytes() == b"model-bytes"
        assert _seeded_model.contract_path.read_text() == _seeded_model.contract_text

    def test_folder_paths_are_project_root_relative(self, client, _seeded_model):
        resp = self._save(client, "exports/severity.cbm")

        assert resp.status_code == 200
        assert resp.json()["path"] == "exports/severity.cbm"
        assert resp.json()["feature_contract_path"] == "exports/severity.feature_contract.json"
        assert (_seeded_model.project_root / "exports" / "severity.cbm").is_file()

    def test_existing_destination_requires_overwrite(self, client, _seeded_model):
        _seeded_model.models.mkdir()
        (_seeded_model.models / "frequency.cbm").write_bytes(b"stale-model")
        (_seeded_model.models / "frequency.feature_contract.json").write_text('{"stale": true}')

        refused = self._save(client, "frequency")

        assert refused.status_code == 409
        assert refused.json()["detail"] == {
            "error_code": "model_file_exists",
            "message": "Model file already exists: models/frequency.cbm",
        }
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"stale-model"

        replaced = self._save(client, "frequency", overwrite=True)

        assert replaced.status_code == 200
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text

    def test_competing_saves_never_mix_one_jobs_model_with_anothers_contract(
        self, tmp_path, _seeded_model, training_artifact_root
    ):
        """Two saves to one destination: the second waits for the first, then refuses."""
        import threading

        from haute.routes import modelling as modelling_routes
        from haute.schemas import SaveModelRequest

        other_model = _write_trained_model(tmp_path / "trained_other")
        (tmp_path / "trained_other" / "frequency.cbm").write_bytes(b"other-model-bytes")
        publish_trained_job(
            modelling_routes._store,
            "save_job_other",
            root=training_artifact_root,
            model_file=other_model,
            result=_completed_train_response(job_id="save_job_other", model_path=str(other_model)),
            config={},
        )
        real_copy = modelling_routes.atomic_copy_files
        first_copying = threading.Event()
        release_first = threading.Event()
        second_copying = threading.Event()
        copy_calls: list[int] = []

        def gated_copy(pairs):
            copy_calls.append(len(copy_calls))
            if len(copy_calls) == 1:
                first_copying.set()
                assert release_first.wait(10), "the test never released the first save"
            else:
                second_copying.set()
            real_copy(pairs)

        outcomes: dict[str, object] = {}

        def save(job_id: str) -> None:
            try:
                outcomes[job_id] = modelling_routes.save_model(
                    SaveModelRequest(job_id=job_id, output_path="frequency")
                )
            except HTTPException as exc:
                outcomes[job_id] = exc

        first = context_thread(save, args=("save_job",))
        second = context_thread(save, args=("save_job_other",))
        try:
            with patch.object(modelling_routes, "atomic_copy_files", gated_copy):
                try:
                    first.start()
                    assert first_copying.wait(10)
                    second.start()
                    # The second save must not reach publication while the first holds it.
                    assert not second_copying.wait(0.5)
                finally:
                    release_first.set()
                    for thread in (first, second):
                        if thread.ident is not None:
                            thread.join(10)
        finally:
            modelling_routes._store.delete_job("save_job_other")

        assert not first.is_alive() and not second.is_alive()
        assert getattr(outcomes["save_job"], "status", None) == "ok"
        refused = outcomes["save_job_other"]
        assert isinstance(refused, HTTPException) and refused.status_code == 409
        assert copy_calls == [0]
        assert (_seeded_model.models / "frequency.cbm").read_bytes() == b"model-bytes"
        assert (
            _seeded_model.models / "frequency.feature_contract.json"
        ).read_text() == _seeded_model.contract_text

    def test_an_existing_contract_alone_also_requires_overwrite(self, client, _seeded_model):
        _seeded_model.models.mkdir()
        (_seeded_model.models / "frequency.feature_contract.json").write_text('{"stale": true}')

        resp = self._save(client, "frequency")

        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "model_file_exists"
        assert not (_seeded_model.models / "frequency.cbm").exists()

    def test_suffix_mismatch_is_rejected(self, client, _seeded_model):
        resp = self._save(client, "frequency.pkl")

        assert resp.status_code == 400
        assert ".cbm" in resp.json()["detail"]
        assert not _seeded_model.models.exists()

    def test_missing_training_contract_is_gone(self, client, _seeded_model):
        _seeded_model.contract_path.unlink()

        resp = self._save(client, "frequency")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "training_artifacts_unavailable"
        assert str(_seeded_model.contract_path.parent) not in str(resp.json()["detail"])
        assert not _seeded_model.models.exists()

    def test_released_training_artifacts_are_gone(self, client, _seeded_model):
        from haute.routes._training_artifacts import TRAINING_ARTIFACTS_HANDLE_KEY
        from haute.routes.modelling import _store

        assert _store.detach_artifact_handle("save_job", TRAINING_ARTIFACTS_HANDLE_KEY)

        resp = self._save(client, "frequency")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "training_artifacts_unavailable"
        assert not _seeded_model.models.exists()

    @pytest.mark.parametrize("output_path", ["../outside.cbm", "models/../../outside.cbm"])
    def test_escaping_path_is_forbidden(self, client, _seeded_model, output_path):
        resp = self._save(client, output_path)

        assert resp.status_code == 403

    def test_copy_oserror_returns_sanitized_500(self, client, _seeded_model):
        with patch(
            "haute.routes.modelling.atomic_copy_files",
            side_effect=OSError("disk full"),
        ):
            resp = self._save(client, "frequency")

        assert resp.status_code == 500
        assert "models/frequency.cbm" not in resp.json()["detail"]

    def test_empty_output_path_is_invalid(self, client, _seeded_model):
        assert self._save(client, "").status_code == 422

    def test_destination_preview_resolves_without_writing(self, client, _seeded_model):
        resp = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "frequency", "algorithm": "glm"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"path": "models/frequency.rsglm", "suffix_mismatch": False}
        assert not _seeded_model.models.exists()

    def test_destination_preview_flags_a_suffix_mismatch(self, client, _seeded_model):
        resp = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "models/frequency.rsglm", "algorithm": "catboost"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"path": "models/frequency.rsglm", "suffix_mismatch": True}

    def test_destination_preview_rejects_an_escape_and_unknown_algorithm(
        self, client, _seeded_model
    ):
        escaped = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "../frequency", "algorithm": "catboost"},
        )
        unknown = client.post(
            "/api/modelling/save/destination",
            json={"output_path": "frequency", "algorithm": "unregistered"},
        )

        assert escaped.status_code == 403
        assert unknown.status_code == 422


# ---------------------------------------------------------------------------
# Phase 1A: Pure function tests
# ---------------------------------------------------------------------------


class TestOutputDirDefault:
    """A scripted TrainingJob writes into outputs/ by default."""

    def test_default_output_dir_without_source_file(self):
        from haute._project_context import graph_pipeline_dir
        from tests.conftest import make_graph

        graph = make_graph({"nodes": [], "edges": []})
        p_dir = graph_pipeline_dir(graph)
        assert p_dir is None

    def test_training_job_default_is_outputs(self):
        import inspect

        from haute.modelling._training_job import TrainingJob

        # output_dir parameter default (7th keyword-only param after name...model_name)
        # Verify via signature
        sig = inspect.signature(TrainingJob.__init__)
        assert sig.parameters["output_dir"].default == "outputs"


# ---------------------------------------------------------------------------
# Phase 1A: Endpoint validation gaps
# ---------------------------------------------------------------------------


class TestMlflowLogSuccess:
    """Tests for /mlflow/log success and exception paths."""

    @staticmethod
    @contextmanager
    def _published(
        job_id: str,
        root: Path,
        tmp_path: Path,
        *,
        config: dict[str, object] | None = None,
        **result_overrides: object,
    ):
        from haute.routes.modelling import _store

        model_path = _write_trained_model(tmp_path / job_id)
        publish_trained_job(
            _store,
            job_id,
            root=root,
            model_file=model_path,
            result=_completed_train_response(
                job_id=job_id, model_path=str(model_path), **result_overrides
            ),
            config=config or {"algorithm": "catboost", "task": "regression", "target": "y"},
            node_label="my_model",
        )
        try:
            yield
        finally:
            _store.delete_job(job_id)

    def test_mlflow_log_success(self, client, tmp_path, training_artifact_root):
        """A published job logs a candidate built from its own artifacts."""
        mock_log_result = SimpleNamespace(
            backend="local",
            experiment_name="my_model",
            run_id="abc123",
            run_url=None,
            tracking_uri="file:///tmp/mlruns",
        )
        with (
            self._published(
                "test_log",
                training_artifact_root,
                tmp_path,
                final_test_metrics={"gini": 0.85, "rmse": 0.12},
                diagnostic_metrics={"gini": 0.85, "rmse": 0.12},
            ),
            patch(
                "haute.modelling._mlflow_log.log_experiment",
                return_value=mock_log_result,
            ) as m_log,
        ):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "test_log"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["backend"] == "local"
        assert data["run_id"] == "abc123"
        candidate = m_log.call_args.kwargs["candidate"]
        assert candidate.metrics["final_test_gini"] == 0.85
        assert candidate.tags["haute.job_id"] == "test_log"
        assert candidate.artifacts.model.parent.parent.parent == training_artifact_root

    def test_fixed_catboost_logs_the_recorded_final_tree_count(
        self, client, tmp_path, training_artifact_root
    ):
        log_result = SimpleNamespace(
            backend="local",
            experiment_name="my_model",
            run_id="trees",
            run_url=None,
            tracking_uri="file:///tmp/mlruns",
        )
        with (
            self._published(
                "test_trees",
                training_artifact_root,
                tmp_path,
                config={
                    "algorithm": "catboost",
                    "task": "regression",
                    "target": "y",
                    "params": {"n_estimators": 20, "depth": 3, "early_stopping_rounds": 5},
                },
                final_tree_count=7,
            ),
            patch("haute.modelling._mlflow_log.log_experiment", return_value=log_result) as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "test_trees"})
        assert resp.status_code == 200
        candidate = m_log.call_args.kwargs["candidate"]
        assert candidate.params["param_iterations"] == 7
        assert candidate.params["param_depth"] == 3
        assert "param_n_estimators" not in candidate.params
        assert "param_early_stopping_rounds" not in candidate.params

    def test_mlflow_log_exception_returns_500(self, client, tmp_path, training_artifact_root):
        """If log_experiment raises, should return 500."""
        with (
            self._published("test_err", training_artifact_root, tmp_path),
            patch(
                "haute.modelling._mlflow_log.log_experiment",
                side_effect=RuntimeError("MLflow connection refused"),
            ),
        ):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "test_err"},
            )
        assert resp.status_code == 500
        assert "MLflow connection refused" not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    def test_mlflow_log_no_result_data(self, client):
        """Completed job with no result should return 400."""
        from haute.routes.modelling import _store

        seed_job(
            _store,
            "no_result",
            {
                "status": "completed",
                "result": None,
                "created_at": time.time(),
            },
        )
        try:
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "no_result"})
            assert resp.status_code == 400
            assert "no evaluation report" in resp.json()["detail"].lower()
        finally:
            _store.delete_job("no_result")


# ---------------------------------------------------------------------------
# /model-cache endpoint tests
# ---------------------------------------------------------------------------


class TestClearModelCache:
    def test_clears_cache_successfully(self, client):
        with patch(
            "haute._mlflow_io.clear_model_cache",
            return_value=3,
        ):
            resp = client.delete("/api/modelling/model-cache")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 3
        assert data["run_id"] is None

    def test_clears_specific_run_cache(self, client):
        with patch(
            "haute._mlflow_io.clear_model_cache",
            return_value=1,
        ):
            resp = client.delete("/api/modelling/model-cache?run_id=abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 1
        assert data["run_id"] == "abc123"


# ---------------------------------------------------------------------------
# Direct route function tests — bypasses TestClient for coverage
# ---------------------------------------------------------------------------


class TestExportScriptDirect:
    """Test export_script route function directly."""

    @pytest.mark.asyncio
    async def test_generates_script(self):
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph(
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
                        "id": "model",
                        "data": {
                            "label": "My model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "task": "regression",
                                "loss_function": "RMSE",
                                "feature_columns": ["x1", "x2"],
                                "params": {"iterations": 100},
                                "evaluation": _random_evaluation_config(),
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "model").model_dump()],
            }
        )
        body = ExportScriptRequest(graph=graph, node_id="model", data_path="output/data.parquet")
        result = await export_script(body)
        assert "TrainingJob" in result.script
        assert "feature_columns=['x1', 'x2']," in result.script
        assert "exclude=" not in result.script
        assert result.filename == "train_My_model.py"

    @pytest.mark.asyncio
    async def test_default_data_path(self):
        """When data_path is not provided, uses a default based on node name."""
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "m1",
                        "data": {
                            "label": "my_model",
                            "nodeType": "modelling",
                            "config": {
                                "target": "y",
                                "algorithm": "catboost",
                                "loss_function": "RMSE",
                                "feature_columns": ["x1"],
                                "params": {"iterations": 10},
                                "evaluation": _random_evaluation_config(),
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )
        body = ExportScriptRequest(graph=graph, node_id="m1")
        result = await export_script(body)
        assert "TrainingJob" in result.script
        # Where a Data Output with that bare name writes its file.
        assert "outputs/my_model.parquet" in result.script

    @pytest.mark.asyncio
    async def test_missing_node_raises_404(self):
        from haute.routes.modelling import export_script
        from haute.schemas import ExportScriptRequest

        graph = make_graph({"nodes": [], "edges": []})
        body = ExportScriptRequest(graph=graph, node_id="nonexistent")
        with pytest.raises(RequestRefusedError) as exc_info:
            await export_script(body)
        assert exc_info.value.status_code == 404


class TestClearModelCacheDirect:
    """Test clear_model_cache route function directly."""

    @pytest.mark.asyncio
    async def test_clears_all(self):
        from haute.routes.modelling import clear_model_cache

        with patch("haute._mlflow_io.clear_model_cache", return_value=5) as mock:
            result = await clear_model_cache(run_id=None)
            mock.assert_called_once_with(None)
            assert result.removed == 5
            assert result.run_id is None

    @pytest.mark.asyncio
    async def test_clears_specific_run(self):
        from haute.routes.modelling import clear_model_cache

        with patch("haute._mlflow_io.clear_model_cache", return_value=2) as mock:
            result = await clear_model_cache(run_id="run_xyz")
            mock.assert_called_once_with("run_xyz")
            assert result.removed == 2
            assert result.run_id == "run_xyz"
