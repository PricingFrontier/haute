"""Runtime-input fingerprints follow the *resolved* MLflow backend.

MLflow-sourced ``MODEL_SCORE`` and ``OPTIMISER_APPLY`` nodes used to key the
dataframe and preview caches on config strings alone, so a repointed server
URL, a rewritten local folder, or a re-resolved auto destination kept the key
identical and a warm entry replayed predictions loaded from the *previous*
backend.  The entry now records ``resolve_backend(...).identity`` (secret-free)
or an "unresolved" marker when configuration is missing
(``specs/roadmap/mlflow-destinations.md`` MLF-D03, Task B7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._sandbox import set_project_root
from haute.errors import MlflowConfigError
from haute.execution import _runtime_input_fingerprint_entry, dataframe_graph_input_fingerprint
from haute.modelling._mlflow_settings import TrackingConfig
from tests.conftest import make_graph

_BASE_TOML = '[project]\nname = "main"\npipeline = "rating/main.py"\n'

_MODEL_SCORE_RUN_CONFIG: dict[str, object] = {
    "sourceType": "run",
    "run_id": "run-1",
    "artifact_path": "model/model.cbm",
    "task": "regression",
    "output_column": "prediction",
}

_OPTIMISER_APPLY_RUN_CONFIG: dict[str, object] = {
    "sourceType": "run",
    "run_id": "run-1",
    "artifact_path": "optimiser/artifact.json",
    "optimised_value_column": "optimised",
}


def _write_settings(root: Path, *, folder: Path | None = None) -> None:
    """Rewrite the workspace ``haute.toml``, optionally pinning ``[mlflow] folder``."""
    table = "" if folder is None else f'\n[mlflow]\nfolder = "{folder.as_posix()}"\n'
    (root / "haute.toml").write_text(f"{_BASE_TOML}{table}", encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root with no ambient MLflow credentials of any form."""
    for var in (
        "MLFLOW_TRACKING_URI",
        "MLFLOW_ENABLE_DB_SDK",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(var, raising=False)
    _write_settings(tmp_path)
    set_project_root(tmp_path)
    return tmp_path


def _graph(node_type: str, config: dict[str, object]) -> Any:
    return make_graph(
        {
            "nodes": [
                {"id": "n1", "data": {"label": "n1", "nodeType": node_type, "config": config}}
            ],
            "edges": [],
        }
    )


def _fingerprint(graph: Any) -> str:
    return dataframe_graph_input_fingerprint(graph, target_node_id="n1", source="live")


def _entry_files(graph: Any) -> dict[str, object]:
    entry = _runtime_input_fingerprint_entry(graph, graph.node_map["n1"])
    files = entry["files"]
    assert isinstance(files, dict)
    return files


def test_model_score_runtime_fingerprint_follows_backend_identity(workspace: Path) -> None:
    """Two local folders are two backends for both MLflow-sourced node types."""
    for node_type, config in (
        ("modelScore", _MODEL_SCORE_RUN_CONFIG),
        ("optimiserApply", _OPTIMISER_APPLY_RUN_CONFIG),
    ):
        graph = _graph(node_type, dict(config))

        _write_settings(workspace, folder=workspace / "runs-a")
        folder_a = _fingerprint(graph)
        _write_settings(workspace, folder=workspace / "runs-b")
        folder_b = _fingerprint(graph)

        assert folder_a != folder_b, node_type
        _write_settings(workspace, folder=workspace / "runs-a")
        assert _fingerprint(graph) == folder_a, node_type


def test_auto_switch_changes_runtime_fingerprint(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auto rule re-resolving from local to Databricks misses the cache."""
    graph = _graph("modelScore", dict(_MODEL_SCORE_RUN_CONFIG))

    local_auto = _fingerprint(graph)

    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-not-a-real-token")
    with patch(
        "mlflow.utils.databricks_utils.get_databricks_host_creds",
        return_value=MagicMock(host="https://adb.example.net"),
    ):
        databricks_auto = _fingerprint(graph)
        files = _entry_files(graph)

    assert local_auto != databricks_auto
    assert "dapi-not-a-real-token" not in str(files["mlflow_backend"])


def test_removed_prerequisites_after_warm_change_the_fingerprint_and_execution_raises(
    workspace: Path,
) -> None:
    """A warm entry from a resolvable server can never be served once it is gone."""
    graph = _graph(
        "modelScore",
        dict(_MODEL_SCORE_RUN_CONFIG) | {"mlflow_destination": "server"},
    )

    with patch(
        "haute.modelling._mlflow_settings._resolve_server",
        return_value=TrackingConfig("server", "http://stub:5000", "http://stub:5000", "toml"),
    ):
        resolvable = _fingerprint(graph)

    unconfigured = _fingerprint(graph)
    assert resolvable != unconfigured
    assert _entry_files(graph)["mlflow_backend"] == {
        "unresolved": (
            "MLflow server is not configured: set [mlflow] tracking_uri in haute.toml "
            "or an http(s) MLFLOW_TRACKING_URI in the environment (.env)."
        )
    }

    _name, score, _is_source = _build_node_fn(graph.node_map["n1"], source_names=["df"])
    with pytest.raises(MlflowConfigError, match="MLflow server is not configured"):
        score(pl.DataFrame({"x": [1.0]}).lazy())


def test_file_backed_optimiser_apply_fingerprint_has_no_backend_entry(workspace: Path) -> None:
    """A file-sourced artifact has no backend to resolve, so no entry is added."""
    artifact = workspace / "optimiser.json"
    artifact.write_text("{}", encoding="utf-8")
    file_graph = _graph(
        "optimiserApply",
        {
            "sourceType": "file",
            "artifact_path": artifact.as_posix(),
            "optimised_value_column": "optimised",
        },
    )

    assert "mlflow_backend" not in _entry_files(file_graph)
    assert "mlflow_backend" in _entry_files(
        _graph("optimiserApply", dict(_OPTIMISER_APPLY_RUN_CONFIG))
    )
