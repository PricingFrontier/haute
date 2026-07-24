"""Tests for the haute.deploy package.

Tests the target-agnostic layers (pruner, bundler, schema, scorer, validators,
config). MLflow-specific tests are integration-level and require mlflow installed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._mlflow_io import _artifact_cache_path
from haute.graph_utils import PipelineGraph
from haute.parser import parse_pipeline_file

if TYPE_CHECKING:
    pass
from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"
DATA_DIR = FIXTURE_DIR / "data"


def _write_cached_model(
    tmp_path: Path,
    run_id: str,
    artifact_path: str,
    payload: bytes = b"fake model",
) -> Path:
    cached = _artifact_cache_path(tmp_path / ".cache" / "models", run_id, artifact_path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(payload)
    return cached


@pytest.fixture()
def deploy_pipeline_file(tmp_path: Path) -> Path:
    """Scratch pipeline path for mocked deploy tests that write build artifacts."""
    pipeline_file = tmp_path / "pipeline.py"
    pipeline_file.write_text("# mocked deploy pipeline\n", encoding="utf-8")
    return pipeline_file


@pytest.fixture()
def full_graph() -> PipelineGraph:
    """Parse the fixture pipeline into a PipelineGraph."""
    return parse_pipeline_file(PIPELINE_FILE)


@dataclass
class MLflowMocks:
    """Named references to every mock in the MLflow deploy patch block."""

    set_tracking_uri: MagicMock
    set_registry_uri: MagicMock
    set_experiment: MagicMock
    start_run: MagicMock
    log_dict: MagicMock
    log_model: MagicMock
    client: MagicMock
    check_connectivity: MagicMock
    build_signature: MagicMock
    create_or_update_endpoint: MagicMock


@contextmanager
def mock_mlflow_deploy():
    """Patch all 10 MLflow/deploy targets used by deploy_to_mlflow().

    Yields an MLflowMocks dataclass so callers can assert on specific mocks.
    """
    with (
        patch("mlflow.set_tracking_uri") as m_tracking,
        patch("mlflow.set_registry_uri") as m_registry,
        patch("mlflow.set_experiment") as m_experiment,
        patch("mlflow.start_run") as m_run,
        patch("mlflow.log_dict") as m_log_dict,
        patch("mlflow.pyfunc.log_model") as m_log_model,
        patch("mlflow.tracking.MlflowClient") as m_client,
        patch("haute.deploy._mlflow._check_databricks_connectivity") as m_conn,
        patch("haute.deploy._mlflow._build_signature") as m_sig,
        patch("haute.deploy._mlflow._create_or_update_serving_endpoint") as m_ep,
    ):
        m_client.return_value.search_model_versions.return_value = []
        m_run.return_value.__enter__ = MagicMock()
        m_run.return_value.__exit__ = MagicMock(return_value=False)

        yield MLflowMocks(
            set_tracking_uri=m_tracking,
            set_registry_uri=m_registry,
            set_experiment=m_experiment,
            start_run=m_run,
            log_dict=m_log_dict,
            log_model=m_log_model,
            client=m_client,
            check_connectivity=m_conn,
            build_signature=m_sig,
            create_or_update_endpoint=m_ep,
        )


# ---------------------------------------------------------------------------
# Pruner tests
# ---------------------------------------------------------------------------


class TestPruner:
    def test_find_output_node(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node

        output_id = find_output_node(full_graph)
        assert output_id == "output"

    def test_find_deploy_input_nodes(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_deploy_input_nodes

        inputs = find_deploy_input_nodes(full_graph)
        assert inputs == ["quotes"]

    def test_prune_for_deploy(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy

        output_id = find_output_node(full_graph)
        pruned, kept, removed = prune_for_deploy(full_graph, output_id)

        kept_set = set(kept)
        removed_set = set(removed)

        # Live scoring path: quotes → policies (switch) → area_lookup → output
        assert "quotes" in kept_set
        assert "policies" in kept_set
        assert "output" in kept_set

        # Batch source pruned (liveSwitch keeps only live branch)
        assert "batch_quotes" in removed_set

        # Sink nodes should be pruned
        assert "results_write" in removed_set

        # Pruned graph should have fewer nodes
        assert len(pruned.nodes) < len(full_graph.nodes)
        assert len(pruned.nodes) == len(kept)

    def test_prune_drops_batch_branch_at_live_switch(self) -> None:
        """liveSwitch nodes should only keep the live (first) input branch."""
        from haute.deploy._pruner import prune_for_deploy

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "live_src",
                        "data": {
                            "label": "live_src",
                            "nodeType": "apiInput",
                            "config": {"path": "d.json"},
                        },
                    },
                    {
                        "id": "batch_src",
                        "data": {
                            "label": "batch_src",
                            "nodeType": "dataInput",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "switch",
                        "data": {
                            "label": "switch",
                            "nodeType": "liveSwitch",
                            "config": {
                                "input_scenario_map": {
                                    "quotes": "live",
                                    "batch_src": "test_batch",
                                },
                                "inputs": ["quotes", "batch_src"],
                            },
                        },
                    },
                    {"id": "score", "data": {"label": "score", "nodeType": "polars", "config": {}}},
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "live_src",
                        "target": "switch",
                        "sourceHandle": "quotes",
                    },
                    {"id": "e2", "source": "batch_src", "target": "switch"},
                    {"id": "e3", "source": "switch", "target": "score"},
                    {"id": "e4", "source": "score", "target": "out"},
                ],
            }
        )
        pruned, kept, removed = prune_for_deploy(graph, "out")
        kept_set = set(kept)
        assert "live_src" in kept_set
        assert "switch" in kept_set
        assert "score" in kept_set
        assert "out" in kept_set
        assert "batch_src" in set(removed)

    def test_prune_routes_two_frames_from_one_api_input_independently(self) -> None:
        """One source node can contribute both the live and batch switch branches."""
        from haute.deploy._pruner import prune_for_deploy

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "request",
                        "data": {
                            "label": "Quote Request",
                            "nodeType": "apiInput",
                            "config": {"path": "quotes.json"},
                        },
                    },
                    {
                        "id": "switch",
                        "data": {
                            "label": "Route Input",
                            "nodeType": "liveSwitch",
                            "config": {
                                "input_scenario_map": {
                                    "quotes": "live",
                                    "drivers": "batch",
                                },
                                "inputs": ["quotes", "drivers"],
                            },
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "Response",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "request__quotes__switch",
                        "source": "request",
                        "target": "switch",
                        "sourceHandle": "quotes",
                    },
                    {
                        "id": "request__drivers__switch",
                        "source": "request",
                        "target": "switch",
                        "sourceHandle": "drivers",
                    },
                    {"id": "switch__out", "source": "switch", "target": "out"},
                ],
            }
        )

        pruned, kept, removed = prune_for_deploy(graph, "out")

        assert [edge.id for edge in pruned.edges] == [
            "request__quotes__switch",
            "switch__out",
        ]
        assert kept == ["out", "request", "switch"]
        assert removed == []

    @pytest.mark.parametrize(
        ("fallback_inputs", "unmatched_name"),
        [
            pytest.param(None, "<missing>", id="absent-fallback"),
            pytest.param(["not_connected"], "not_connected", id="unmatched-fallback"),
        ],
    )
    def test_prune_live_switch_without_live_mapping_rejects_invalid_fallback(
        self,
        fallback_inputs: list[str] | None,
        unmatched_name: str,
    ) -> None:
        """A missing live branch cannot silently erase every switch input edge."""
        from haute.deploy._pruner import prune_for_deploy

        switch_config: dict[str, object] = {
            "input_scenario_map": {"connected_source": "batch"},
        }
        if fallback_inputs is not None:
            switch_config["inputs"] = fallback_inputs

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "connected_source",
                        "data": {
                            "label": "connected_source",
                            "nodeType": "dataInput",
                            "config": {},
                        },
                    },
                    {
                        "id": "route_switch",
                        "data": {
                            "label": "Route Switch",
                            "nodeType": "liveSwitch",
                            "config": switch_config,
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "out",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "connected_source__route_switch",
                        "source": "connected_source",
                        "target": "route_switch",
                    },
                    {
                        "id": "route_switch__out",
                        "source": "route_switch",
                        "target": "out",
                    },
                ],
            }
        )

        with pytest.raises(ValueError) as exc_info:
            prune_for_deploy(graph, "out")

        detail = str(exc_info.value)
        assert "route_switch" in detail
        assert "inputs[0]" in detail
        assert unmatched_name in detail

    def test_prune_missing_output_raises(self, full_graph: dict) -> None:
        from haute.deploy._pruner import prune_for_deploy

        with pytest.raises(ValueError, match="not found.*graph"):
            prune_for_deploy(full_graph, "nonexistent_node")

    def test_find_output_no_output_raises(self) -> None:
        from haute.deploy._pruner import find_output_node

        graph = _g(
            {
                "nodes": [
                    {"id": "a", "data": {"nodeType": "dataInput", "config": {}}},
                ]
            }
        )
        with pytest.raises(ValueError, match="[Nn]o output node"):
            find_output_node(graph)


# ---------------------------------------------------------------------------
# Bundler tests
# ---------------------------------------------------------------------------


class TestBundler:
    def test_collect_artifacts(self, full_graph: dict) -> None:
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._pruner import find_output_node, prune_for_deploy

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        artifacts = collect_artifacts(pruned, ["quotes"], FIXTURE_DIR)

        # All artifact paths should exist
        for name, path in artifacts.items():
            assert path.is_file(), f"Artifact {name} not found at {path}"

    def test_missing_artifact_raises(self) -> None:
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ext1",
                        "data": {
                            "nodeType": "externalFile",
                            "config": {"path": "nonexistent/model.pkl"},
                        },
                    },
                ]
            }
        )

        with pytest.raises(FileNotFoundError, match="[Aa]rtifact.*not found"):
            collect_artifacts(graph, [], Path())

    def test_collect_model_score_artifact(self, tmp_path, monkeypatch):
        """MODEL_SCORE nodes download .cbm from MLflow and include in artifacts."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        # Pre-populate the disk cache so download is skipped.
        _write_cached_model(tmp_path, "run_abc", "model.cbm")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms1",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_abc",
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], tmp_path)
        assert len(artifacts) == 1
        name = next(iter(artifacts))
        assert name == "ms1__model.cbm"
        assert artifacts[name].is_file()

    def test_model_score_skipped_without_run_id(self):
        """MODEL_SCORE nodes without run_id are silently skipped."""
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms1",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {"sourceType": "run"},
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], Path())
        assert len(artifacts) == 0

    def test_external_file_no_path_skipped(self):
        """externalFile node with no path should be silently skipped."""
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ext_no_path",
                        "data": {
                            "nodeType": "externalFile",
                            "config": {},
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], Path())
        assert len(artifacts) == 0

    def test_datasource_not_input_collects_artifact(self, tmp_path):
        """dataInput node NOT listed as input should be collected as an artifact."""
        from haute.deploy._bundler import collect_artifacts

        data_file = tmp_path / "lookup.csv"
        data_file.write_text("a,b\n1,2\n")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "static_ds",
                        "data": {
                            "nodeType": "dataInput",
                            "config": {"path": str(data_file)},
                        },
                    },
                ],
            }
        )

        # input_node_ids=[] means static_ds is NOT a deploy input
        artifacts = collect_artifacts(graph, [], tmp_path)
        assert len(artifacts) == 1
        name = next(iter(artifacts))
        assert name == "static_ds__lookup.csv"
        assert artifacts[name].is_file()

    # -- Registered model tests (B1 fix) ------------------------------------

    def test_registered_model_resolved_and_bundled(self, tmp_path, monkeypatch):
        """MODEL_SCORE with sourceType='registered' resolves via MLflow and bundles."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        # Pre-populate the disk cache so _download_model_artifact succeeds.
        _write_cached_model(tmp_path, "resolved_run_123", "model.cbm", b"fake registered model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_reg",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "my-prod-model",
                                "version": "3",
                            },
                        },
                    },
                ],
            }
        )

        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            return_value=("resolved_run_123", "model.cbm"),
        ) as mock_resolve:
            artifacts = collect_artifacts(graph, [], tmp_path)

        mock_resolve.assert_called_once_with("my-prod-model", "3")
        assert len(artifacts) == 1
        name = next(iter(artifacts))
        assert name == "ms_reg__model.cbm"
        assert artifacts[name].is_file()

    def test_registered_model_latest_version(self, tmp_path, monkeypatch):
        """sourceType='registered' with version='latest' resolves correctly."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        _write_cached_model(tmp_path, "run_latest", "model.cbm", b"latest model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_latest",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "my-model",
                                "version": "latest",
                            },
                        },
                    },
                ],
            }
        )

        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            return_value=("run_latest", "model.cbm"),
        ) as mock_resolve:
            artifacts = collect_artifacts(graph, [], tmp_path)

        mock_resolve.assert_called_once_with("my-model", "latest")
        assert len(artifacts) == 1

    def test_registered_model_empty_version(self, tmp_path, monkeypatch):
        """sourceType='registered' with empty version (defaults to latest)."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        _write_cached_model(tmp_path, "run_empty_ver", "model.cbm", b"model data")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_empty_ver",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "my-model",
                                "version": "",
                            },
                        },
                    },
                ],
            }
        )

        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            return_value=("run_empty_ver", "model.cbm"),
        ) as mock_resolve:
            artifacts = collect_artifacts(graph, [], tmp_path)

        mock_resolve.assert_called_once_with("my-model", "")
        assert len(artifacts) == 1

    def test_registered_model_skipped_without_model_name(self):
        """sourceType='registered' with no registered_model is silently skipped."""
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_no_name",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "",
                                "version": "1",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], Path())
        assert len(artifacts) == 0

    def test_registered_model_resolve_error_propagates(self):
        """Errors from _resolve_registered_model propagate to caller."""
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_err",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "nonexistent-model",
                                "version": "1",
                            },
                        },
                    },
                ],
            }
        )

        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            side_effect=ValueError("No versions found for registered model 'nonexistent-model'."),
        ):
            with pytest.raises(ValueError, match="No versions found"):
                collect_artifacts(graph, [], Path())

    def test_mixed_run_and_registered_models(self, tmp_path, monkeypatch):
        """Pipeline with both run-based and registered MODEL_SCORE nodes."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        # Cache for run-based model and registered model (resolved to run_resolved).
        _write_cached_model(tmp_path, "run_direct", "direct.cbm", b"direct model")
        _write_cached_model(tmp_path, "run_resolved", "registered.cbm", b"registered model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_run",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_direct",
                                "artifact_path": "direct.cbm",
                            },
                        },
                    },
                    {
                        "id": "ms_reg",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "prod-model",
                                "version": "2",
                            },
                        },
                    },
                ],
            }
        )

        with patch(
            "haute.deploy._bundler._resolve_registered_model",
            return_value=("run_resolved", "registered.cbm"),
        ):
            artifacts = collect_artifacts(graph, [], tmp_path)

        assert len(artifacts) == 2
        assert "ms_run__direct.cbm" in artifacts
        assert "ms_reg__registered.cbm" in artifacts
        assert artifacts["ms_run__direct.cbm"].is_file()
        assert artifacts["ms_reg__registered.cbm"].is_file()

    def test_run_based_model_still_works_with_explicit_source_type(self, tmp_path, monkeypatch):
        """sourceType='run' explicitly set still works (no regression)."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        _write_cached_model(tmp_path, "run_explicit", "model.cbm", b"explicit run model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_explicit_run",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_explicit",
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], tmp_path)
        assert len(artifacts) == 1
        assert "ms_explicit_run__model.cbm" in artifacts

    def test_model_score_defaults_to_run_source_type(self, tmp_path, monkeypatch):
        """MODEL_SCORE without sourceType defaults to 'run'."""
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        _write_cached_model(tmp_path, "run_default", "model.cbm", b"default model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_default",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "run_id": "run_default",
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], tmp_path)
        assert len(artifacts) == 1
        assert "ms_default__model.cbm" in artifacts


class TestResolveRegisteredModel:
    """Tests for _resolve_registered_model helper function."""

    def _make_mock_mv(self, run_id: str = "run_abc"):
        """Create a mock ModelVersion with the given run_id."""
        mv = MagicMock()
        mv.run_id = run_id
        return mv

    def _mock_context(
        self,
        mock_client,
        resolve_version_rv=None,
        resolve_version_se=None,
        find_artifact_rv=("model.cbm", "catboost"),
        find_artifact_se=None,
    ):
        """Build a combined patch context for _resolve_registered_model tests.

        Mocks: mlflow.set_tracking_uri, mlflow.tracking.MlflowClient,
        resolve_tracking_backend, resolve_version, _find_model_artifact.
        """
        from contextlib import ExitStack

        stack = ExitStack()
        patches = [
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.tracking.MlflowClient", return_value=mock_client),
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("http://tracking", "local"),
            ),
        ]
        if resolve_version_se is not None:
            patches.append(
                patch("haute._mlflow_utils.resolve_version", side_effect=resolve_version_se)
            )
        elif resolve_version_rv is not None:
            patches.append(
                patch("haute._mlflow_utils.resolve_version", return_value=resolve_version_rv)
            )
        if find_artifact_se is not None:
            patches.append(
                patch("haute._mlflow_io._find_model_artifact", side_effect=find_artifact_se)
            )
        elif find_artifact_rv is not None:
            patches.append(
                patch("haute._mlflow_io._find_model_artifact", return_value=find_artifact_rv)
            )
        for p in patches:
            stack.enter_context(p)
        return stack

    def test_resolves_specific_version(self):
        """Specific version number resolves correctly."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_for_v2")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(mock_client, resolve_version_rv="2"):
            run_id, artifact_path = _resolve_registered_model("my-model", "2")

        assert run_id == "run_for_v2"
        assert artifact_path == "model.cbm"
        mock_client.get_model_version.assert_called_once_with("my-model", "2")

    def test_resolves_latest_version(self):
        """'latest' version resolves to highest version number."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_for_latest")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(mock_client, resolve_version_rv="5"):
            run_id, artifact_path = _resolve_registered_model("my-model", "latest")

        assert run_id == "run_for_latest"
        assert artifact_path == "model.cbm"

    def test_resolves_empty_version_as_latest(self):
        """Empty version string resolves as latest."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_for_empty")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(mock_client, resolve_version_rv="3"):
            run_id, artifact_path = _resolve_registered_model("my-model", "")

        assert run_id == "run_for_empty"

    def test_no_versions_raises_value_error(self):
        """Raises ValueError when no versions exist for the model."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_client = MagicMock()

        with self._mock_context(
            mock_client,
            resolve_version_se=ValueError("No versions found"),
        ):
            with pytest.raises(ValueError, match="No versions found"):
                _resolve_registered_model("nonexistent-model", "latest")

    def test_no_run_id_on_model_version_raises(self):
        """Raises ValueError when the model version has no associated run."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("")  # empty run_id
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(mock_client, resolve_version_rv="1"):
            with pytest.raises(ValueError, match="has no associated run_id"):
                _resolve_registered_model("my-model", "1")

    def test_no_run_id_none_on_model_version_raises(self):
        """Raises ValueError when run_id is None (not just empty string)."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = MagicMock()
        mock_mv.run_id = None
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(mock_client, resolve_version_rv="1"):
            with pytest.raises(ValueError, match="has no associated run_id"):
                _resolve_registered_model("my-model", "1")

    def test_find_artifact_error_propagates(self):
        """FileNotFoundError from _find_model_artifact propagates."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_no_artifact")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(
            mock_client,
            resolve_version_rv="1",
            find_artifact_se=FileNotFoundError("No .cbm artifact found"),
        ):
            with pytest.raises(FileNotFoundError, match="No .cbm artifact found"):
                _resolve_registered_model("my-model", "1")

    def test_pyfunc_artifact_resolved(self):
        """Registered model with pyfunc artifact is resolved correctly."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_pyfunc")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(
            mock_client,
            resolve_version_rv="1",
            find_artifact_rv=("model", "pyfunc"),
        ):
            run_id, artifact_path = _resolve_registered_model("pyfunc-model", "1")

        assert run_id == "run_pyfunc"
        assert artifact_path == "model"

    def test_rsglm_artifact_resolved(self):
        """Registered model with .rsglm artifact is resolved correctly."""
        from haute.deploy._bundler import _resolve_registered_model

        mock_mv = self._make_mock_mv("run_glm")
        mock_client = MagicMock()
        mock_client.get_model_version.return_value = mock_mv

        with self._mock_context(
            mock_client,
            resolve_version_rv="1",
            find_artifact_rv=("model.rsglm", "rustystats"),
        ):
            run_id, artifact_path = _resolve_registered_model("glm-model", "1")

        assert run_id == "run_glm"
        assert artifact_path == "model.rsglm"


# ---------------------------------------------------------------------------
# Scorer tests
# ---------------------------------------------------------------------------


class TestScorer:
    def test_score_single_row(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._scorer import score_graph

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        sample = pl.read_parquet(DATA_DIR / "policies.parquet", n_rows=1)
        result = score_graph(
            graph=pruned,
            input_df=sample,
            input_node_ids=["quotes"],
            output_node_id=output_id,
        )

        assert isinstance(result, pl.DataFrame)
        assert len(result) == 1
        # The pipeline adds area_factor and premium columns
        assert "premium" in result.columns
        assert "area_factor" in result.columns
        # All output values should be non-null for valid input
        assert result["premium"].null_count() == 0

    def test_score_multiple_rows(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._scorer import score_graph

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        sample = pl.read_parquet(DATA_DIR / "policies.parquet", n_rows=5)
        result = score_graph(
            graph=pruned,
            input_df=sample,
            input_node_ids=["quotes"],
            output_node_id=output_id,
        )

        assert isinstance(result, pl.DataFrame)
        assert len(result) == 5
        # Verify computed columns are present and populated
        assert "premium" in result.columns
        assert "area_factor" in result.columns
        assert result["premium"].null_count() == 0
        # Input columns should survive through to output
        assert "VehPower" in result.columns
        assert "Area" in result.columns

    def test_model_score_intercept_uses_bundled_path(self, tmp_path):
        """MODEL_SCORE scorer intercept loads from remapped local path."""
        from unittest.mock import MagicMock

        import numpy as np

        from haute.deploy._scorer import score_graph

        # Write a fake .cbm to the bundled artifacts dir
        cbm_path = tmp_path / "model.cbm"
        cbm_path.write_bytes(b"fake")

        mock_model = MagicMock()
        mock_model.feature_names_ = ["x1"]
        mock_model.predict.return_value = np.array([42.0, 43.0])

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "nodeType": "apiInput",
                            "config": {"path": ""},
                        },
                    },
                    {
                        "id": "ms",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "r1",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "src",
                        "target": "ms",
                        "sourceHandle": "src",
                    }
                ],
            }
        )

        input_df = pl.DataFrame({"x1": [1.0, 2.0]})
        remap = {"ms__model.cbm": str(cbm_path)}

        with patch(
            "haute._mlflow_io._load_catboost_model",
            return_value=mock_model,
        ):
            result = score_graph(
                graph=graph,
                input_df=input_df,
                input_node_ids=["src"],
                output_node_id="ms",
                artifact_paths=remap,
            )

        assert "pred" in result.columns
        assert result["pred"].to_list() == [42.0, 43.0]
        # Model loaded from bundled path, not from MLflow
        mock_model.predict.assert_called_once()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_infer_input_schema(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_input_schema

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        schema = infer_input_schema(pruned, "quotes")

        assert isinstance(schema, dict)
        assert len(schema) > 0
        assert "VehPower" in schema
        assert "Area" in schema

    def test_infer_output_schema(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_output_schema

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        schema = infer_output_schema(pruned, output_id, ["quotes"])

        assert isinstance(schema, dict)
        assert len(schema) > 0
        # The fixture pipeline produces premium from VehPower * area_factor * Exposure,
        # so the output must contain at least the source columns plus the computed premium.
        assert "premium" in schema, "Output schema must include the 'premium' column"
        assert "VehPower" in schema, "Output schema must include input columns"
        # All dtype values must be non-empty Polars dtype strings
        for col_name, dtype_str in schema.items():
            assert isinstance(dtype_str, str) and len(dtype_str) > 0, (
                f"Column '{col_name}' has invalid dtype: {dtype_str!r}"
            )


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------


class TestValidators:
    def test_validate_passes_for_good_config(self, full_graph: dict) -> None:
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._config import DeployConfig, ResolvedDeploy
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_input_schema, infer_output_schema
        from haute.deploy._validators import validate_deploy

        output_id = find_output_node(full_graph)
        pruned, _kept, removed = prune_for_deploy(full_graph, output_id)
        artifacts = collect_artifacts(pruned, ["quotes"], FIXTURE_DIR)
        input_schema = infer_input_schema(pruned, "quotes")
        output_schema = infer_output_schema(pruned, output_id, ["quotes"])

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )
        resolved = ResolvedDeploy(
            config=config,
            full_graph=full_graph,
            pruned_graph=pruned,
            input_node_ids=["quotes"],
            output_node_id=output_id,
            artifacts=artifacts,
            input_schema=input_schema,
            output_schema=output_schema,
            removed_node_ids=removed,
        )

        validate_deploy(resolved)

    def test_score_test_quotes(self, full_graph: dict) -> None:
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._config import DeployConfig, ResolvedDeploy
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_input_schema, infer_output_schema
        from haute.deploy._validators import score_test_quotes

        output_id = find_output_node(full_graph)
        pruned, _kept, removed = prune_for_deploy(full_graph, output_id)
        artifacts = collect_artifacts(pruned, ["quotes"], FIXTURE_DIR)
        input_schema = infer_input_schema(pruned, "quotes")
        output_schema = infer_output_schema(pruned, output_id, ["quotes"])

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
            test_quotes_dir=FIXTURE_DIR / "quotes",
        )
        resolved = ResolvedDeploy(
            config=config,
            full_graph=full_graph,
            pruned_graph=pruned,
            input_node_ids=["quotes"],
            output_node_id=output_id,
            artifacts=artifacts,
            input_schema=input_schema,
            output_schema=output_schema,
            removed_node_ids=removed,
        )

        results = score_test_quotes(resolved)
        assert len(results) > 0, "Expected at least one test quote file"
        for r in results:
            assert r["status"] == "ok", f"Test quote {r['file']} failed: {r['error']}"
            assert r["rows"] > 0


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_from_toml(self) -> None:
        from haute.deploy._config import DeployConfig

        config = DeployConfig.from_toml(Path("haute.toml"))
        assert config.model_name == "motor-pricing"
        assert config.pipeline_file == Path("rating/main.py")
        assert config.databricks.experiment_name == "/Shared/haute/motor-pricing"
        assert config.databricks.serving_workload_size == "Small"

    def test_override(self) -> None:
        from haute.deploy._config import DeployConfig

        config = DeployConfig.from_toml(Path("haute.toml"))
        overridden = config.override(model_name="custom-name")
        assert overridden.model_name == "custom-name"
        assert config.model_name == "motor-pricing"  # original unchanged

    def test_resolve_config(self) -> None:
        """Integration test — resolves using fixture pipeline (has output node)."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )
        resolved = resolve_config(config)

        assert resolved.output_node_id == "output"
        assert "quotes" in resolved.input_node_ids
        assert len(resolved.input_schema) > 0
        assert len(resolved.output_schema) > 0
        assert len(resolved.removed_node_ids) > 0


# ---------------------------------------------------------------------------
# Pure deploy logic — extracted functions
# ---------------------------------------------------------------------------


class TestBuildUcModelName:
    """Tests for the pure UC model name construction."""

    def test_default_config(self) -> None:
        from haute.deploy._config import DeployConfig
        from haute.deploy._mlflow import build_uc_model_name

        config = DeployConfig(pipeline_file=PIPELINE_FILE, model_name="my-model")
        # Default DatabricksConfig: catalog="main", schema="pricing"
        assert build_uc_model_name(config) == "main.pricing.my-model"

    def test_custom_catalog_schema(self) -> None:
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import build_uc_model_name

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            databricks=DatabricksConfig(catalog="workspace", schema="pricing"),
        )
        assert build_uc_model_name(config) == "workspace.pricing.my-model"

    def test_suffix_appended(self) -> None:
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import build_uc_model_name

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            endpoint_suffix="-staging",
            databricks=DatabricksConfig(catalog="ws", schema="default"),
        )
        assert build_uc_model_name(config) == "ws.default.my-model-staging"

    def test_no_suffix(self) -> None:
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import build_uc_model_name

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            databricks=DatabricksConfig(catalog="ws", schema="default"),
        )
        assert build_uc_model_name(config) == "ws.default.my-model"


class TestBuildExperimentName:
    """Tests for the pure experiment name construction."""

    def test_default_experiment(self) -> None:
        from haute.deploy._config import DeployConfig
        from haute.deploy._mlflow import build_experiment_name

        config = DeployConfig(pipeline_file=PIPELINE_FILE, model_name="my-model")
        assert build_experiment_name(config) == config.databricks.experiment_name

    def test_suffix_appended(self) -> None:
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import build_experiment_name

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            endpoint_suffix="-staging",
            databricks=DatabricksConfig(experiment_name="/Shared/haute/test"),
        )
        assert build_experiment_name(config) == "/Shared/haute/test-staging"

    def test_no_suffix(self) -> None:
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import build_experiment_name

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            databricks=DatabricksConfig(experiment_name="/Shared/haute/test"),
        )
        assert build_experiment_name(config) == "/Shared/haute/test"


# ---------------------------------------------------------------------------
# Parser apiInput round-trip test
# ---------------------------------------------------------------------------


class TestDatabricksTracking:
    """Regression tests: deploy must target Databricks, not local MLflow."""

    def test_deploy_sets_tracking_uri(self, deploy_pipeline_file: Path) -> None:
        """deploy_to_mlflow() must call mlflow.set_tracking_uri('databricks')."""
        from haute.deploy._mlflow import DeployResult, deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy() as mocks:
            result = deploy_to_mlflow(resolved)

            mocks.set_tracking_uri.assert_called_once_with("databricks")
            mocks.set_registry_uri.assert_called_once_with("databricks-uc")
            # Behavioral: result is a well-formed DeployResult
            assert isinstance(result, DeployResult)
            assert result.model_name == "test-model"
            assert result.model_version >= 1
            assert result.manifest_path.name == "deploy_manifest.json"
            assert result.manifest_path.parent == deploy_pipeline_file.parent / ".haute_build"

    def test_deploy_uses_uc_model_name(self, deploy_pipeline_file: Path) -> None:
        """Model must be registered with catalog.schema.model_name format."""
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import deploy_to_mlflow

        config = DeployConfig(
            pipeline_file=deploy_pipeline_file,
            model_name="my-model",
            databricks=DatabricksConfig(catalog="workspace", schema="default"),
        )
        resolved = _make_resolved(config)

        with mock_mlflow_deploy() as mocks:
            result = deploy_to_mlflow(resolved)

            # Verify the UC three-level namespace was used in log_model
            log_call = mocks.log_model.call_args
            assert log_call.kwargs["registered_model_name"] == "workspace.default.my-model"

            # Verify the model URI uses the UC name
            assert "workspace.default.my-model" in result.model_uri

    def test_experiment_name_includes_suffix_for_staging(
        self,
        deploy_pipeline_file: Path,
    ) -> None:
        """Staging deploys must use a suffixed experiment and model name."""
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import deploy_to_mlflow

        config = DeployConfig(
            pipeline_file=deploy_pipeline_file,
            model_name="test-model",
            endpoint_suffix="-staging",
            databricks=DatabricksConfig(
                experiment_name="/Shared/haute/test",
                catalog="workspace",
                schema="default",
            ),
        )
        resolved = _make_resolved(config)

        with mock_mlflow_deploy() as mocks:
            deploy_to_mlflow(resolved)

            mocks.set_experiment.assert_called_once_with("/Shared/haute/test-staging")
            # Model must also be registered with the suffix
            log_call = mocks.log_model.call_args
            assert (
                log_call.kwargs["registered_model_name"] == "workspace.default.test-model-staging"
            )


class TestServingEndpoint:
    """Regression tests: deploy must create/update the Databricks serving endpoint."""

    def test_deploy_calls_create_or_update_endpoint(
        self,
        deploy_pipeline_file: Path,
    ) -> None:
        """deploy_to_mlflow() must call _create_or_update_serving_endpoint."""
        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import deploy_to_mlflow

        config = DeployConfig(
            pipeline_file=deploy_pipeline_file,
            model_name="my-model",
            endpoint_name="my-endpoint",
            databricks=DatabricksConfig(catalog="ws", schema="default"),
        )
        resolved = _make_resolved(config)

        with mock_mlflow_deploy() as mocks:
            mocks.create_or_update_endpoint.return_value = (
                "https://host/serving-endpoints/my-endpoint/invocations"
            )

            result = deploy_to_mlflow(resolved)

            mocks.create_or_update_endpoint.assert_called_once_with(
                config=config,
                uc_model_name="ws.default.my-model",
                model_version=1,
            )
            assert result.endpoint_url == "https://host/serving-endpoints/my-endpoint/invocations"

    def test_endpoint_returns_none_when_no_endpoint_name(self) -> None:
        """If endpoint_name is not set, _create_or_update_serving_endpoint returns None."""
        from haute.deploy._config import DeployConfig
        from haute.deploy._mlflow import _create_or_update_serving_endpoint

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            endpoint_name=None,
        )
        result = _create_or_update_serving_endpoint(
            config=config, uc_model_name="main.pricing.my-model", model_version=1
        )
        assert result is None

    def test_endpoint_creates_new_endpoint(self) -> None:
        """When endpoint doesn't exist, it should be created."""
        from unittest.mock import patch

        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import _create_or_update_serving_endpoint

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            endpoint_name="my-endpoint",
            databricks=DatabricksConfig(
                serving_workload_size="Small",
                serving_scale_to_zero=True,
            ),
        )

        with (
            patch("databricks.sdk.WorkspaceClient") as mock_ws_cls,
            patch.dict(
                "os.environ",
                {
                    "DATABRICKS_RATING_HOST": "https://myhost",
                    "DATABRICKS_RATING_TOKEN": "test-token",
                },
            ),
        ):
            mock_ws = mock_ws_cls.return_value
            from databricks.sdk.errors import NotFound

            mock_ws.serving_endpoints.get.side_effect = NotFound("not found")

            url = _create_or_update_serving_endpoint(
                config=config,
                uc_model_name="main.pricing.my-model",
                model_version=2,
            )

            mock_ws.serving_endpoints.create.assert_called_once()
            assert url == "https://myhost/serving-endpoints/my-endpoint/invocations"

    def test_endpoint_updates_existing_endpoint(self) -> None:
        """When endpoint already exists, it should update the config."""
        from unittest.mock import MagicMock, patch

        from haute.deploy._config import DatabricksConfig, DeployConfig
        from haute.deploy._mlflow import _create_or_update_serving_endpoint

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="my-model",
            endpoint_name="my-endpoint",
            databricks=DatabricksConfig(
                serving_workload_size="Medium",
                serving_scale_to_zero=False,
            ),
        )

        with (
            patch("databricks.sdk.WorkspaceClient") as mock_ws_cls,
            patch.dict(
                "os.environ",
                {
                    "DATABRICKS_RATING_HOST": "https://myhost",
                    "DATABRICKS_RATING_TOKEN": "test-token",
                },
            ),
        ):
            mock_ws = mock_ws_cls.return_value
            mock_ws.serving_endpoints.get.return_value = MagicMock()

            url = _create_or_update_serving_endpoint(
                config=config,
                uc_model_name="main.pricing.my-model",
                model_version=3,
            )

            mock_ws.serving_endpoints.update_config.assert_called_once()
            mock_ws.serving_endpoints.create.assert_not_called()
            assert url == "https://myhost/serving-endpoints/my-endpoint/invocations"


class TestModelsFromCode:
    """Regression tests: deploy must use models-from-code, not CloudPickle."""

    def test_model_code_path_is_string(self) -> None:
        """_MODEL_CODE_PATH must be a str file path, not a Python object."""
        from haute.deploy._mlflow import _MODEL_CODE_PATH

        assert isinstance(_MODEL_CODE_PATH, str)
        assert _MODEL_CODE_PATH.endswith("_model_code.py")

    def test_model_code_file_exists(self) -> None:
        """The models-from-code script must exist on disk."""
        from pathlib import Path

        from haute.deploy._mlflow import _MODEL_CODE_PATH

        assert Path(_MODEL_CODE_PATH).is_file()

    def test_model_code_contains_set_model(self) -> None:
        """The script must call set_model() so MLflow can discover the model."""
        from pathlib import Path

        from haute.deploy._mlflow import _MODEL_CODE_PATH

        source = Path(_MODEL_CODE_PATH).read_text(encoding="utf-8")
        assert "set_model(" in source

    def test_log_model_receives_file_path(self, deploy_pipeline_file: Path) -> None:
        """log_model(python_model=...) must receive a file path, not an object."""
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy() as mocks:
            deploy_to_mlflow(resolved)

            log_call = mocks.log_model.call_args
            python_model_arg = log_call.kwargs["python_model"]
            assert isinstance(python_model_arg, str), (
                f"Expected file path (str), got {type(python_model_arg)}"
            )
            assert python_model_arg.endswith("_model_code.py")


class TestHauteModel:
    def test_inherits_from_python_model(self) -> None:
        """HauteModel must inherit from mlflow.pyfunc.PythonModel (MLflow 3.x)."""
        from mlflow.pyfunc import PythonModel

        from haute.deploy._model_code import HauteModel

        assert issubclass(HauteModel, PythonModel)


class TestParserApiInput:
    def test_api_input_node_type(self) -> None:
        """api_input=True in decorator should produce apiInput nodeType."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        quotes_node = None
        for n in graph.nodes:
            if n.id == "quotes":
                quotes_node = n
                break

        assert quotes_node is not None, "quotes node not found"
        assert quotes_node.data.nodeType == "apiInput", (
            f"Expected apiInput nodeType, got: {quotes_node.data.nodeType}"
        )

    def test_live_switch_node_type(self) -> None:
        """live_switch=True in decorator should produce liveSwitch nodeType."""
        graph = parse_pipeline_file(PIPELINE_FILE)
        policies_node = None
        for n in graph.nodes:
            if n.id == "policies":
                policies_node = n
                break

        assert policies_node is not None, "policies node not found"
        assert policies_node.data.nodeType == "liveSwitch", (
            f"Expected liveSwitch nodeType, got: {policies_node.data.nodeType}"
        )
        config = policies_node.data.config
        assert config["input_scenario_map"] == {"quotes": "live", "batch_quotes": "test_batch"}
        assert config["inputs"] == ["quotes", "batch_quotes"]
