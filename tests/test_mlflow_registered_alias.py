"""Registered model aliases as a Model Score / Optimiser Apply source (MLF-E07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from haute._config_validation import validate_node_config
from haute._types import NodeType
from haute.errors import ConfigError


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real local MLflow store holding model ``freq`` with two CatBoost versions."""
    from catboost import CatBoostRegressor
    from mlflow.tracking import MlflowClient

    from haute._sandbox import set_project_root

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    set_project_root(tmp_path)
    monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: tmp_path / ".cache")
    uri = (tmp_path / "mlruns").as_uri()
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment = client.create_experiment("aliases")
    client.create_registered_model("freq")
    frame = pd.DataFrame({"age": [20.0, 30.0, 40.0, 50.0]})
    for scale in (1.0, 10.0):
        run = client.create_run(experiment)
        model = CatBoostRegressor(iterations=5, depth=1, verbose=0, allow_writing_files=False)
        model.fit(frame, [scale, 2 * scale, 3 * scale, 4 * scale])
        model_file = tmp_path / f"model_{int(scale)}.cbm"
        model.save_model(str(model_file))
        client.log_artifact(run.info.run_id, str(model_file))
        client.set_terminated(run.info.run_id)
        client.create_model_version("freq", f"runs:/{run.info.run_id}", run.info.run_id)
    client.set_registered_model_alias("freq", "champion", "1")
    return client


class TestResolveVersion:
    def test_an_alias_resolves_to_the_version_it_targets(self, registry: Any) -> None:
        from haute._mlflow_utils import resolve_version

        assert resolve_version(registry, "freq", "", "champion") == "1"
        assert resolve_version(registry, "freq", "latest", "champion") == "1"
        registry.set_registered_model_alias("freq", "champion", "2")
        assert resolve_version(registry, "freq", "", "champion") == "2"

    def test_a_missing_alias_names_the_alias_and_model(self, registry: Any) -> None:
        from haute._mlflow_utils import resolve_version

        with pytest.raises(ValueError, match="'freq' has no alias 'challenger'"):
            resolve_version(registry, "freq", "", "challenger")

    def test_a_concrete_version_beside_an_alias_is_ambiguous(self, registry: Any) -> None:
        from haute._mlflow_utils import resolve_version

        with pytest.raises(ValueError, match="both version 2 and alias 'champion'"):
            resolve_version(registry, "freq", "2", "champion")


class TestLoadFollowsTheAlias:
    def test_moving_the_alias_loads_the_newly_targeted_model(self, registry: Any) -> None:
        from haute._mlflow_io import load_mlflow_model

        frame = pd.DataFrame({"age": [25.0, 45.0]})

        def champion_predictions() -> np.ndarray:
            model = load_mlflow_model(
                source_type="registered",
                registered_model="freq",
                alias="champion",
                task="regression",
            )
            return np.asarray(model.predict(frame))

        first = champion_predictions()
        registry.set_registered_model_alias("freq", "champion", "2")
        moved = champion_predictions()

        assert np.all(moved > first * 5), "the load kept serving the version the alias left"


def _mlflow_registered_version_signature(config: dict[str, object]) -> object:
    from haute.execution import _mlflow_registered_version_signature as signature

    return signature({"sourceType": "registered", "registered_model": "freq"} | config)


def _registered_score_fingerprint(config: dict[str, object]) -> str:
    from haute.execution import dataframe_graph_input_fingerprint
    from tests.conftest import make_graph

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "n1",
                    "data": {
                        "label": "n1",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": "registered",
                            "registered_model": "freq",
                            "task": "regression",
                            "output_column": "prediction",
                        }
                        | config,
                    },
                }
            ],
            "edges": [],
        }
    )
    return dataframe_graph_input_fingerprint(graph, target_node_id="n1", source="live")


class TestCacheIdentityFollowsTheAlias:
    """The dataframe and preview caches key on the version an alias targets now."""

    def test_moving_the_alias_misses_warm_caches(self, registry: Any) -> None:
        champion = {"alias": "champion"}
        before = _registered_score_fingerprint(champion)
        assert _registered_score_fingerprint(champion) == before

        registry.set_registered_model_alias("freq", "champion", "2")

        assert _registered_score_fingerprint(champion) != before
        assert _mlflow_registered_version_signature(champion) == "2"

    def test_a_newer_version_moves_latest(self, registry: Any) -> None:
        latest = _registered_score_fingerprint({"version": "latest"})
        assert _mlflow_registered_version_signature({"version": ""}) == "2"
        source = registry.get_model_version("freq", "2")

        registry.create_model_version("freq", source.source, source.run_id)

        assert _registered_score_fingerprint({"version": "latest"}) != latest
        assert _mlflow_registered_version_signature({"version": "latest"}) == "3"

    def test_a_concrete_version_needs_no_registry_lookup(self) -> None:
        with patch("haute._mlflow_utils.resolve_mlflow_source") as resolve:
            assert _mlflow_registered_version_signature({"version": "3"}) == "3"
        resolve.assert_not_called()

    def test_a_failed_lookup_names_no_server(self) -> None:
        with patch(
            "haute._mlflow_utils.resolve_mlflow_source",
            side_effect=ConnectionError("https://user:secret@mlflow.example.net unreachable"),
        ):
            marker = _mlflow_registered_version_signature({"alias": "champion"})

        assert isinstance(marker, dict)
        assert marker["unresolved"] == "ConnectionError"
        assert "secret" not in str(marker) and "example.net" not in str(marker)

    def test_a_preview_cached_after_a_failed_lookup_is_not_replayed_in_a_later_outage(
        self, registry: Any, tmp_path: Path
    ) -> None:
        """The lookup fails but the model load succeeds; the alias then moves and the
        lookup fails again. The second preview must not replay the first's rows."""
        import polars as pl

        from haute.executor import _preview_cache, execute_graph
        from tests.conftest import make_edge, make_graph, make_source_node

        pl.DataFrame({"age": [25.0, 45.0]}).write_parquet(tmp_path / "policies.parquet")
        graph = make_graph(
            {
                "nodes": [
                    make_source_node("policies", str(tmp_path / "policies.parquet")).model_dump(),
                    {
                        "id": "score",
                        "data": {
                            "label": "score",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "freq",
                                "alias": "champion",
                                "task": "regression",
                                "output_column": "prediction",
                            },
                        },
                    },
                ],
                "edges": [make_edge("policies", "score").model_dump()],
            }
        )

        def predictions() -> list[float]:
            result = execute_graph(graph, target_node_id="score")["score"]
            assert result.status == "ok", result.error
            return [row["prediction"] for row in result.preview]

        _preview_cache.clear()
        outage = patch(
            "haute._mlflow_utils.resolve_mlflow_source",
            side_effect=ConnectionError("registry unreachable"),
        )
        try:
            with outage:
                first = predictions()
            registry.set_registered_model_alias("freq", "champion", "2")
            with outage:
                second = predictions()
        finally:
            _preview_cache.clear()

        assert all(now > before * 5 for before, now in zip(first, second, strict=True)), (
            "a preview cached under a failed lookup was replayed after the alias moved"
        )


class TestAliasConfigValidation:
    @pytest.mark.parametrize("node_type", [NodeType.MODEL_SCORE, NodeType.OPTIMISER_APPLY])
    def test_an_alias_alone_is_valid(self, node_type: NodeType) -> None:
        config = {"sourceType": "registered", "registered_model": "freq", "alias": "champion"}
        assert validate_node_config(node_type, config)["alias"] == "champion"

    @pytest.mark.parametrize("node_type", [NodeType.MODEL_SCORE, NodeType.OPTIMISER_APPLY])
    @pytest.mark.parametrize("version", ["latest", "3"])
    def test_a_version_and_an_alias_together_are_rejected(
        self, node_type: NodeType, version: str
    ) -> None:
        config = {
            "sourceType": "registered",
            "registered_model": "freq",
            "alias": "champion",
            "version": version,
        }
        with pytest.raises(ConfigError, match="both a version and an alias"):
            validate_node_config(node_type, config)

    @pytest.mark.parametrize("alias", [" champion", 7])
    def test_a_malformed_alias_is_rejected(self, alias: object) -> None:
        with pytest.raises(ConfigError, match="invalid alias"):
            validate_node_config(NodeType.MODEL_SCORE, {"alias": alias})


class TestAliasDiscovery:
    def test_model_versions_list_the_aliases_targeting_each_version(
        self, client: Any, registry: Any
    ) -> None:
        registry.set_registered_model_alias("freq", "challenger", "2")
        response = client.get("/api/mlflow/model-versions", params={"model_name": "freq"})
        assert response.status_code == 200, response.text
        by_version = {row["version"]: row["aliases"] for row in response.json()}
        assert by_version == {"2": ["challenger"], "1": ["champion"]}


class TestAliasInOptimiserApply:
    def test_the_alias_reaches_source_resolution(self) -> None:
        from haute._optimiser_io import load_mlflow_optimiser_artifact

        backend = MagicMock(identity="local:x", tracking_uri="file:///x")
        with (
            patch("haute._mlflow_utils.resolve_backend", return_value=backend),
            patch(
                "haute._optimiser_io.resolve_mlflow_source",
                return_value=("run-1", "4", MagicMock(), MagicMock(), backend),
            ) as source,
            patch("haute._optimiser_io._load_mlflow_cached") as cached,
        ):
            cached.return_value = {"mode": "online"}
            cached.cache_info.return_value = MagicMock(hits=0)
            load_mlflow_optimiser_artifact(
                source_type="registered", registered_model="opt", alias="champion"
            )
        assert source.call_args.kwargs["alias"] == "champion"


class TestDeployRecordsTheResolvedAlias:
    def test_the_bundle_records_alias_version_and_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._mlflow_io import _artifact_cache_path
        from haute._mlflow_utils import resolve_backend
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._utils import model_source_line
        from tests.conftest import make_graph

        monkeypatch.chdir(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        cached = _artifact_cache_path(cache_root, resolve_backend("").digest, "run_7", "model.cbm")
        cached.parent.mkdir(parents=True)
        (cache_root / cached.relative_to(cache_root)).write_bytes(b"model")
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "score",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "freq",
                                "alias": "champion",
                            },
                        },
                    }
                ],
                "edges": [],
            }
        )
        model_sources: dict[str, dict[str, Any]] = {}
        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            return_value=("run_7", "model.cbm", "7"),
        ) as resolve:
            collect_artifacts(graph, [], tmp_path, model_sources=model_sources)

        resolve.assert_called_once_with("freq", "", backend=ANY, alias="champion")
        assert model_sources == {
            "score": {
                "registered_model": "freq",
                "alias": "champion",
                "version": "7",
                "run_id": "run_7",
            }
        }
        assert model_source_line("score", model_sources["score"]) == (
            "Model score: freq @champion -> version 7 (run run_7)"
        )
