"""Tests for the haute.deploy package.

Tests the target-agnostic layers (pruner, bundler, schema, scorer, validators,
config), the served-model wrapper, schema inference from data inputs, and the
MLflow deploy orchestration (progress reporting, artifact copy, registered
version selection, build-directory cleanup). MLflow-specific tests are
integration-level and require mlflow installed.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import polars as pl
import pytest

import haute
from haute._mlflow_utils import ResolvedBackend, resolve_backend
from haute._project_context import context_thread, current_project
from haute.deploy._request_frame import RequestReader
from haute.deploy._request_rows import RequestRows
from haute.deploy._served_haute import ServedHaute
from haute.graph_utils import PipelineGraph
from haute.parser import parse_pipeline_file
from tests._deploy_helpers import make_resolved_deploy as _make_resolved
from tests._mlflow_fakes import FakeArtifactClient
from tests._project import use_project
from tests._source_files import REPO_ROOT
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


DATA_DIR = FIXTURE_DIR / "data"


@pytest.fixture(autouse=True)
def _repository_project() -> None:
    """The fixture pipeline is the repository's: its relative paths resolve there."""
    use_project(REPO_ROOT)


def _fake_backend(digest: str = "a" * 16) -> ResolvedBackend:
    """A ResolvedBackend with a pinned digest, for identity assertions."""
    uri = f"file:///backend/{digest}"
    return ResolvedBackend(
        mode="local",
        tracking_uri=uri,
        registry_uri=uri,
        identity=f"local:{digest}|registry={uri}",
        digest=digest,
    )


@dataclass
class _Store:
    """A real local MLflow store: the auto destination of the test's project."""

    root: Path
    backend: ResolvedBackend
    client: Any
    experiment_id: str
    registered: set[str] = field(default_factory=set)

    def log_run(self, *artifacts: str, tag: bytes = b"model", contract: bool = True) -> str:
        """A finished run that logged each declared CatBoost *artifact* and its contract."""
        from haute.modelling._feature_contract import build_contract, save_contract
        from haute.modelling._training_job import model_contract_filename

        run_id = self.client.create_run(self.experiment_id).info.run_id
        for artifact in artifacts:
            relative = PurePosixPath(artifact)
            folder = str(relative.parent) if "/" in artifact else None
            work = self.root / "logged" / run_id / (folder or "")
            work.mkdir(parents=True, exist_ok=True)
            _declared_catboost(work / relative.name, tag)
            self.client.log_artifact(run_id, str(work / relative.name), folder)
            if contract:
                logged = work / model_contract_filename(relative.stem)
                save_contract(
                    build_contract(
                        features=["0"],
                        feature_types={"0": "Float64"},
                        categorical_features=[],
                        target_name="y",
                        target_type="Float64",
                        task="regression",
                    ),
                    logged,
                )
                self.client.log_artifact(run_id, str(logged), folder)
        self.client.set_terminated(run_id)
        return run_id

    def register(self, name: str, run_id: str, artifact: str) -> str:
        """Register *artifact* of *run_id* as the next version of *name*, as MLflow records it."""
        if name not in self.registered:
            self.client.create_registered_model(name)
            self.registered.add(name)
        location = self.client.get_run(run_id).info.artifact_uri
        return str(self.client.create_model_version(name, f"{location}/{artifact}", run_id).version)

    def versions(self, name: str, count: int) -> list[str]:
        """*count* versions of *name*, each registered from its own run's ``model.cbm``."""
        runs = [self.log_run("model.cbm", tag=f"v{index}".encode()) for index in range(count)]
        assert [self.register(name, run_id, "model.cbm") for run_id in runs] == [
            str(index + 1) for index in range(count)
        ]
        return runs

    def stored(self, run_id: str, artifact: str) -> bytes:
        """The bytes of *artifact* as the store keeps them."""
        from mlflow.utils.file_utils import local_file_uri_to_path

        location = self.client.get_run(run_id).info.artifact_uri
        return (Path(local_file_uri_to_path(location)) / artifact).read_bytes()


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Store:
    """A local MLflow store at the destination a node without one resolves to."""
    from mlflow.tracking import MlflowClient

    use_project(tmp_path)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    backend = resolve_backend("")
    client = MlflowClient(tracking_uri=backend.tracking_uri, registry_uri=backend.registry_uri)
    return _Store(tmp_path, backend, client, client.create_experiment("deploy"))


class _Registry(FakeArtifactClient):
    """A fake store whose registered versions come from given runs and sources."""

    def __init__(
        self,
        runs: dict[str, dict[str, int | None]],
        versions: dict[str, tuple[str | None, str]],
    ) -> None:
        super().__init__(runs)
        self.versions = versions
        self.version_reads: list[tuple[str, str]] = []
        self.searches: list[str] = []

    def search_model_versions(self, filter_string: str) -> list[Any]:
        from types import SimpleNamespace

        self.searches.append(filter_string)
        return [SimpleNamespace(version=version) for version in self.versions]

    def get_model_version(self, name: str, version: str) -> Any:
        from types import SimpleNamespace

        self.version_reads.append((name, version))
        run_id, source = self.versions[version]
        return SimpleNamespace(run_id=run_id, source=source)


def _resolve_registered(client: Any, name: str, version: str) -> Any:
    """What a registered reference resolves to when the store is *client*."""
    from haute._mlflow_model import resolve_model
    from haute._model_source import RegisteredModelSource

    source = RegisteredModelSource(
        registered_model=name, version=version, alias="", mlflow_destination=""
    )
    with patch("haute._mlflow_utils.pinned_client", return_value=client):
        return resolve_model(source, backend=_fake_backend())


def _declared_catboost(path: Path, tag: bytes) -> None:
    from catboost import CatBoostRegressor

    model = CatBoostRegressor(iterations=2, depth=1, verbose=0, allow_writing_files=False)
    model.fit([[1.0], [2.0], [3.0]], [1.0, 2.0, 3.0])
    model.get_metadata()["haute_offset_column"] = ""
    model.get_metadata()["test_payload"] = tag.decode()
    model.save_model(str(path))


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


# Deploy resolves the dedicated Databricks MLflow pair before any MLflow call. An
# ambient profile URI, DATABRICKS_CONFIG_PROFILE, or MLFLOW_ENABLE_DB_SDK would change
# or reject that resolution, so the patch block clears them.
_MLFLOW_DATABRICKS_PAIR = {
    "DATABRICKS_MLFLOW_HOST": "https://myhost.databricks.com",
    "DATABRICKS_MLFLOW_TOKEN": "mlflow-test-token",
}


_AMBIENT_MLFLOW_DATABRICKS_ENV = (
    "MLFLOW_TRACKING_URI",
    "DATABRICKS_CONFIG_PROFILE",
    "MLFLOW_ENABLE_DB_SDK",
)


@contextmanager
def mock_mlflow_deploy():
    """Patch all 10 MLflow/deploy targets used by deploy_to_mlflow().

    Also sets the dedicated Databricks MLflow pair deploy resolves first.
    Yields an MLflowMocks dataclass so callers can assert on specific mocks.
    """
    with (
        patch.dict("os.environ", _MLFLOW_DATABRICKS_PAIR),
        patch("mlflow.set_tracking_uri") as m_tracking,
        patch("mlflow.set_registry_uri") as m_registry,
        patch("mlflow.set_experiment") as m_experiment,
        patch("mlflow.start_run") as m_run,
        patch("mlflow.log_dict") as m_log_dict,
        patch("mlflow.pyfunc.log_model") as m_log_model,
        patch("mlflow.tracking.MlflowClient") as m_client,
        patch("haute.deploy._mlflow._check_databricks_connectivity") as m_conn,
        patch("haute.deploy._mlflow._check_registry_schema"),
        patch("haute.deploy._mlflow._build_signature") as m_sig,
        patch("haute.deploy._mlflow._create_or_update_serving_endpoint") as m_ep,
        # The tests run on an editable haute, which a served model cannot install.
        patch(
            "haute.deploy._mlflow.served_haute",
            return_value=ServedHaute(f"haute=={haute.__version__}"),
        ),
    ):
        for name in _AMBIENT_MLFLOW_DATABRICKS_ENV:
            os.environ.pop(name, None)
        # log_model answers with the version it registered.
        m_log_model.return_value.registered_model_version = 1
        # Deploy logs through a destination-bound client: an active experiment
        # and the run it creates.
        m_client.return_value.get_experiment_by_name.return_value = MagicMock(
            experiment_id="1", lifecycle_stage="active"
        )
        m_client.return_value.create_run.return_value.info.run_id = "run-1"
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
        assert output_id == "quote_response"

    def test_find_deploy_input_nodes(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_deploy_input_nodes

        inputs = find_deploy_input_nodes(full_graph)
        assert inputs == ["quote_input"]

    def test_prune_for_deploy(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy

        output_id = find_output_node(full_graph)
        pruned, kept, removed = prune_for_deploy(full_graph, output_id)

        kept_set = set(kept)
        removed_set = set(removed)

        # Live scoring path: quote_input → policies (switch) → area_lookup → quote_response
        assert "quote_input" in kept_set
        assert "policies" in kept_set
        assert "quote_response" in kept_set

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
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "d.parquet",
                                "arguments": {},
                            },
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

    @staticmethod
    def _switch_graph(*, inner_map: dict[str, str], outer_map: dict[str, str]) -> Any:
        """``live_src`` and ``inner`` (a switch over ``a``/``b``) feed ``outer``, a switch
        feeding ``out``; ``stray`` is a switch with no path to ``out``."""

        def node(nid: str, node_type: str, config: dict[str, Any] | None = None) -> dict:
            return {"id": nid, "data": {"label": nid, "nodeType": node_type, "config": config}}

        def edge(source: str, target: str) -> dict[str, str]:
            return {"id": f"{source}__{target}", "source": source, "target": target}

        return _g(
            {
                "nodes": [
                    node("live_src", "polars", {}),
                    node("a", "polars", {}),
                    node("b", "polars", {}),
                    node("inner", "liveSwitch", {"input_scenario_map": inner_map}),
                    node("outer", "liveSwitch", {"input_scenario_map": outer_map}),
                    node("stray", "liveSwitch", {"input_scenario_map": {"a": "batch"}}),
                    node("out", "output", make_output_config([])),
                ],
                "edges": [
                    edge("a", "inner"),
                    edge("b", "inner"),
                    edge("live_src", "outer"),
                    edge("inner", "outer"),
                    edge("outer", "out"),
                    edge("a", "stray"),
                ],
            }
        )

    def test_prune_ignores_a_switch_the_output_does_not_reach_through_live_branches(
        self,
    ) -> None:
        # DEP-R13: `stray` reaches nothing and `inner` sits on outer's non-live branch, so
        # neither is inspected: their missing "live" entries are not the deployment's.
        from haute.deploy._pruner import prune_for_deploy

        graph = self._switch_graph(
            inner_map={"a": "batch", "b": "batch"},
            outer_map={"live_src": "live", "inner": "batch"},
        )

        pruned, kept, removed = prune_for_deploy(graph, "out")

        assert kept == ["live_src", "out", "outer"]
        assert removed == ["a", "b", "inner", "stray"]
        assert [edge.id for edge in pruned.edges] == ["live_src__outer", "outer__out"]

    def test_prune_refuses_a_reachable_switch_with_no_live_input(self) -> None:
        # DEP-R13: a descriptive ValueError, never a bare StopIteration.
        from haute.deploy._pruner import prune_for_deploy

        graph = self._switch_graph(
            inner_map={"a": "batch", "b": "batch"},
            outer_map={"live_src": "batch", "inner": "live"},
        )

        with pytest.raises(ValueError, match="'inner' maps no input to the 'live' scenario"):
            prune_for_deploy(graph, "out")

    def test_prune_refuses_a_live_input_that_matches_no_connected_edge(self) -> None:
        from haute.deploy._pruner import prune_for_deploy

        graph = self._switch_graph(
            inner_map={"a": "batch", "b": "batch"},
            outer_map={"renamed_source": "live", "inner": "batch"},
        )

        with pytest.raises(ValueError, match="'renamed_source' does not match any connected"):
            prune_for_deploy(graph, "out")

    def test_prune_ignores_an_edge_whose_end_the_graph_does_not_hold(self) -> None:
        from haute.deploy._pruner import prune_for_deploy

        graph = _g(
            {
                "nodes": [
                    {"id": "out", "data": {"nodeType": "output", "config": make_output_config([])}}
                ],
                "edges": [{"id": "dangling", "source": "ghost", "target": "out"}],
            }
        )

        pruned, kept, removed = prune_for_deploy(graph, "out")

        assert (kept, removed, pruned.edges) == (["out"], [], [])

    def test_find_output_node_refuses_two_and_counts_a_repeated_id_once(self) -> None:
        from haute.deploy._pruner import find_output_node

        def output(node_id: str) -> dict:
            return {"id": node_id, "data": {"nodeType": "output", "config": make_output_config([])}}

        assert find_output_node(_g({"nodes": [output("out"), output("out")], "edges": []})) == (
            "out"
        )
        with pytest.raises(ValueError, match=r"Multiple output nodes found: \['a', 'b'\]"):
            find_output_node(_g({"nodes": [output("a"), output("b")], "edges": []}))

    def test_find_source_nodes_names_the_inputs_and_constants(self) -> None:
        from haute.deploy._pruner import find_source_nodes

        graph = _g(
            {
                "nodes": [
                    {"id": node_id, "data": {"nodeType": node_type, "config": {}}}
                    for node_id, node_type in (
                        ("quote", "apiInput"),
                        ("rates", "dataInput"),
                        ("base", "constant"),
                        ("calc", "polars"),
                    )
                ],
                "edges": [],
            }
        )

        assert find_source_nodes(graph) == ["quote", "rates", "base"]

    def test_prune_missing_output_raises(self, full_graph: dict) -> None:
        from haute.deploy._pruner import prune_for_deploy

        with pytest.raises(ValueError, match="not found.*graph"):
            prune_for_deploy(full_graph, "nonexistent_node")

    def test_find_output_no_output_raises(self) -> None:
        from haute.deploy._pruner import find_output_node

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "a",
                        "data": {
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "d.parquet",
                                "arguments": {},
                            },
                        },
                    },
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

        artifacts = collect_artifacts(pruned, ["quote_input"], FIXTURE_DIR)

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

    def test_collect_model_score_artifact(self, store: _Store) -> None:
        """MODEL_SCORE nodes download .cbm from MLflow and include in artifacts."""
        from haute.deploy._bundler import collect_artifacts

        run_id = store.log_run("model.cbm")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms1",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": run_id,
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], store.root)
        assert sorted(artifacts) == ["ms1__feature_contract.json", "ms1__model.cbm"]
        assert artifacts["ms1__model.cbm"].read_bytes() == store.stored(run_id, "model.cbm")
        assert artifacts["ms1__feature_contract.json"].read_bytes() == store.stored(
            run_id, "model.feature_contract.json"
        )

    def test_a_run_without_an_artifact_path_is_resolved_when_bundled(self, store: _Store) -> None:
        """The bundle carries the model the run's discovery finds, served under its file name."""
        from haute.deploy._bundler import collect_artifacts

        run_id = store.log_run("model/model.cbm")
        config = {"source_type": "run", "run_id": run_id}
        graph = _g({"nodes": [{"id": "ms1", "data": {"nodeType": "modelScore", "config": config}}]})

        served: dict[str, dict[str, Any]] = {}
        artifacts = collect_artifacts(graph, [], store.root, served_configs=served)

        assert sorted(artifacts) == ["ms1__feature_contract.json", "ms1__model.cbm"]
        assert artifacts["ms1__model.cbm"].read_bytes() == store.stored(run_id, "model/model.cbm")
        assert served == {"ms1": {**config, "artifact_path": "model.cbm"}}
        assert graph.nodes[0].data.config == config

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
                            "config": {"source_type": "run"},
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

        data_file = tmp_path / "lookup.parquet"
        pl.DataFrame({"a": [1], "b": [2]}).write_parquet(data_file)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "static_ds",
                        "data": {
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": str(data_file),
                                "arguments": {},
                            },
                        },
                    },
                ],
            }
        )

        # input_node_ids=[] means static_ds is NOT a deploy input
        artifacts = collect_artifacts(graph, [], tmp_path)
        assert len(artifacts) == 1
        name = next(iter(artifacts))
        assert name == "static_ds__lookup.parquet"
        assert artifacts[name].is_file()

    # -- Registered model tests (B1 fix) ------------------------------------

    def test_registered_model_resolved_and_bundled(self, store: _Store) -> None:
        """MODEL_SCORE with source_type='registered' resolves via MLflow and bundles."""
        from haute.deploy._bundler import collect_artifacts

        runs = store.versions("my-prod-model", 3)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_reg",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "my-prod-model",
                                "version": "2",
                            },
                        },
                    },
                ],
            }
        )

        model_sources: dict[str, dict[str, Any]] = {}
        artifacts = collect_artifacts(graph, [], store.root, model_sources=model_sources)

        assert model_sources == {
            "ms_reg": {
                "registered_model": "my-prod-model",
                "alias": "",
                "version": "2",
                "run_id": runs[1],
            }
        }
        assert sorted(artifacts) == ["ms_reg__feature_contract.json", "ms_reg__model.cbm"]
        assert artifacts["ms_reg__model.cbm"].read_bytes() == store.stored(runs[1], "model.cbm")

    def test_registered_model_latest_version(self, store: _Store) -> None:
        """source_type='registered' with version='latest' resolves to the highest version."""
        from haute.deploy._bundler import collect_artifacts

        runs = store.versions("my-model", 2)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_latest",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "my-model",
                                "version": "latest",
                            },
                        },
                    },
                ],
            }
        )

        model_sources: dict[str, dict[str, Any]] = {}
        artifacts = collect_artifacts(graph, [], store.root, model_sources=model_sources)

        assert (model_sources["ms_latest"]["version"], model_sources["ms_latest"]["run_id"]) == (
            "2",
            runs[1],
        )
        assert sorted(artifacts) == ["ms_latest__feature_contract.json", "ms_latest__model.cbm"]
        assert artifacts["ms_latest__model.cbm"].read_bytes() == store.stored(runs[1], "model.cbm")

    def test_registered_model_empty_version(self, store: _Store) -> None:
        """source_type='registered' with empty version (defaults to latest)."""
        from haute.deploy._bundler import collect_artifacts

        runs = store.versions("my-model", 2)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_empty_ver",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "my-model",
                                "version": "",
                            },
                        },
                    },
                ],
            }
        )

        model_sources: dict[str, dict[str, Any]] = {}
        artifacts = collect_artifacts(graph, [], store.root, model_sources=model_sources)

        assert model_sources["ms_empty_ver"]["version"] == "2"
        assert sorted(artifacts) == [
            "ms_empty_ver__feature_contract.json",
            "ms_empty_ver__model.cbm",
        ]
        assert artifacts["ms_empty_ver__model.cbm"].read_bytes() == store.stored(
            runs[1], "model.cbm"
        )

    def test_registered_model_skipped_without_model_name(self):
        """source_type='registered' with no registered_model is silently skipped."""
        from haute.deploy._bundler import collect_artifacts

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_no_name",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
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

    def test_registered_model_resolve_error_propagates(self, store: _Store) -> None:
        """An error resolving the registered model propagates to the caller."""
        from haute.deploy._bundler import collect_artifacts

        store.client.create_registered_model("nonexistent-model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_err",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "nonexistent-model",
                                "version": "latest",
                            },
                        },
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="No versions found"):
            collect_artifacts(graph, [], store.root)

    def test_mixed_run_and_registered_models(self, store: _Store) -> None:
        """Pipeline with both run-based and registered MODEL_SCORE nodes."""
        from haute.deploy._bundler import collect_artifacts

        direct = store.log_run("direct.cbm", tag=b"direct model")
        resolved = store.log_run("registered.cbm", tag=b"registered model")
        assert store.register("prod-model", direct, "direct.cbm") == "1"
        assert store.register("prod-model", resolved, "registered.cbm") == "2"

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_run",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": direct,
                                "artifact_path": "direct.cbm",
                            },
                        },
                    },
                    {
                        "id": "ms_reg",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "prod-model",
                                "version": "2",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], store.root)

        assert len(artifacts) == 4  # each model and its run's contract
        assert artifacts["ms_run__direct.cbm"].read_bytes() == store.stored(direct, "direct.cbm")
        assert artifacts["ms_reg__registered.cbm"].read_bytes() == store.stored(
            resolved, "registered.cbm"
        )
        assert "ms_run__feature_contract.json" in artifacts
        assert "ms_reg__feature_contract.json" in artifacts

    def test_run_based_model_still_works_with_explicit_source_type(self, store: _Store) -> None:
        """source_type='run' explicitly set still works (no regression)."""
        from haute.deploy._bundler import collect_artifacts

        run_id = store.log_run("model.cbm", tag=b"explicit run model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_explicit_run",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "run",
                                "run_id": run_id,
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], store.root)
        assert sorted(artifacts) == [
            "ms_explicit_run__feature_contract.json",
            "ms_explicit_run__model.cbm",
        ]
        assert artifacts["ms_explicit_run__model.cbm"].read_bytes() == store.stored(
            run_id, "model.cbm"
        )

    def test_model_score_without_source_type_is_not_bundled(self, store: _Store) -> None:
        """MODEL_SCORE without source_type is untouched, as in the preview: nothing bundles."""
        from haute.deploy._bundler import collect_artifacts

        run_id = store.log_run("model.cbm", tag=b"default model")

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_default",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "run_id": run_id,
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        assert collect_artifacts(graph, [], store.root) == {}

    # -- One backend per node (MLF-D03) -------------------------------------

    def test_bundler_resolves_one_backend_per_model_score_node(self, store: _Store) -> None:
        """The node's destination is resolved once and threaded through the bundle.

        A settings save mid-bundle would make a second resolution return a
        different backend; the model resolution and the download must both keep
        the first one, and the download must land in that backend's digest
        partition.
        """
        from haute import _mlflow_io, _mlflow_model
        from haute.deploy._bundler import collect_artifacts

        backend_a = store.backend
        backend_b = _fake_backend("b" * 16)
        [run_id] = store.versions("prod-model", 1)

        resolutions: list[str] = []

        def counting_resolve_backend(destination: str = "", project_root=None) -> ResolvedBackend:
            resolutions.append(destination)
            return backend_a if len(resolutions) == 1 else backend_b

        seen: dict[str, list[ResolvedBackend]] = {"resolution": [], "download": []}
        real_resolve = _mlflow_model.resolve_model
        real_fetch = _mlflow_io.fetch_resolved_files

        def spy_resolve(source, *, backend):
            seen["resolution"].append(backend)
            return real_resolve(source, backend=backend)

        def spy_fetch(mlflow_mod, resolved):
            seen["download"].append(resolved.backend)
            return real_fetch(mlflow_mod, resolved)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "ms_one_backend",
                        "data": {
                            "nodeType": "modelScore",
                            "config": {
                                "source_type": "registered",
                                "registered_model": "prod-model",
                                "version": "1",
                                "mlflow_destination": "local",
                            },
                        },
                    },
                ],
            }
        )

        with (
            patch("haute._mlflow_utils.resolve_backend", new=counting_resolve_backend),
            patch("haute._mlflow_model.resolve_model", new=spy_resolve),
            patch("haute._mlflow_io.fetch_resolved_files", new=spy_fetch),
        ):
            artifacts = collect_artifacts(graph, [], store.root)

        assert resolutions == ["local"]
        assert seen == {"resolution": [backend_a], "download": [backend_a]}

        bundled = artifacts["ms_one_backend__model.cbm"]
        assert bundled.read_bytes() == store.stored(run_id, "model.cbm")
        assert backend_a.digest in bundled.parts
        assert backend_b.digest not in bundled.parts

    def test_registered_resolution_uses_destination(self):
        """A registered lookup runs on the backend it is given and never re-resolves one."""
        from haute._mlflow_model import resolve_model
        from haute._model_source import RegisteredModelSource

        backend = _fake_backend("c" * 16)
        client = _Registry({"run_x": {"model.cbm": 3}}, {"2": ("run_x", "runs:/run_x/model.cbm")})
        pinned: list[ResolvedBackend] = []

        def pinned_client(given: ResolvedBackend) -> Any:
            pinned.append(given)
            return client

        # The source names another destination: the backend given is the one asked.
        source = RegisteredModelSource(
            registered_model="my-model", version="2", alias="", mlflow_destination="databricks"
        )
        with (
            patch("haute._mlflow_utils.pinned_client", new=pinned_client),
            patch("haute._mlflow_utils.resolve_backend") as mock_resolve_backend,
        ):
            resolved = resolve_model(source, backend=backend)

        assert (resolved.run_id, resolved.artifact.path) == ("run_x", "model.cbm")
        assert pinned == [backend]
        assert resolved.backend is backend
        mock_resolve_backend.assert_not_called()


class TestResolveRegisteredModel:
    """A registered reference resolved by the model resolution, through its version's source."""

    def test_resolves_specific_version(self):
        """Specific version number resolves correctly, with no search of the versions."""
        client = _Registry(
            {"run_v1": {"model.cbm": 1}, "run_for_v2": {"model.cbm": 2}},
            {
                "1": ("run_v1", "runs:/run_v1/model.cbm"),
                "2": ("run_for_v2", "runs:/run_for_v2/model.cbm"),
            },
        )

        resolved = _resolve_registered(client, "my-model", "2")

        assert (resolved.run_id, resolved.artifact.path, resolved.version) == (
            "run_for_v2",
            "model.cbm",
            "2",
        )
        assert client.version_reads == [("my-model", "2")]
        assert client.searches == []

    def test_resolves_latest_version(self):
        """'latest' version resolves to highest version number."""
        client = _Registry(
            {"run_old": {"model.cbm": 1}, "run_for_latest": {"model.cbm": 2}},
            {
                "2": ("run_old", "runs:/run_old/model.cbm"),
                "10": ("run_for_latest", "runs:/run_for_latest/model.cbm"),
                "9": ("run_old", "runs:/run_old/model.cbm"),
            },
        )

        resolved = _resolve_registered(client, "my-model", "latest")

        assert (resolved.run_id, resolved.artifact.path, resolved.version) == (
            "run_for_latest",
            "model.cbm",
            "10",
        )

    def test_resolves_empty_version_as_latest(self):
        """Empty version string resolves as latest."""
        client = _Registry(
            {"run_old": {"model.cbm": 1}, "run_for_empty": {"model.cbm": 2}},
            {
                "1": ("run_old", "runs:/run_old/model.cbm"),
                "3": ("run_for_empty", "runs:/run_for_empty/model.cbm"),
            },
        )

        resolved = _resolve_registered(client, "my-model", "")

        assert (resolved.run_id, resolved.version) == ("run_for_empty", "3")

    def test_no_versions_raises_value_error(self):
        """Raises ValueError when no versions exist for the model."""
        client = _Registry({}, {})

        with pytest.raises(ValueError, match="No versions found"):
            _resolve_registered(client, "nonexistent-model", "latest")

    def test_no_run_id_on_model_version_raises(self):
        """A model version with no associated run is refused, naming the version."""
        from haute.errors import ModelResolutionError

        client = _Registry({}, {"1": ("", "s3://bucket/model.cbm")})

        with pytest.raises(ModelResolutionError, match="registered model 'my-model' has no run"):
            _resolve_registered(client, "my-model", "1")

    def test_no_run_id_none_on_model_version_raises(self):
        """Refused when run_id is None (not just empty string)."""
        from haute.errors import ModelResolutionError

        client = _Registry({}, {"1": (None, "s3://bucket/model.cbm")})

        with pytest.raises(ModelResolutionError, match="registered model 'my-model' has no run"):
            _resolve_registered(client, "my-model", "1")

    def test_find_artifact_error_propagates(self):
        """A version whose run holds no model Haute scores is refused, naming the run."""
        from haute.errors import ModelResolutionError

        client = _Registry(
            {"run_no_artifact": {"readme.txt": 1}},
            {"1": ("run_no_artifact", "runs:/run_no_artifact")},
        )

        with pytest.raises(ModelResolutionError) as refused:
            _resolve_registered(client, "my-model", "1")
        assert "MLflow run 'run_no_artifact'" in str(refused.value)
        assert "holds no model Haute can score" in str(refused.value)

    def test_pyfunc_artifact_resolved(self, tmp_path: Path):
        """A version registered from a pyfunc folder resolves to it; a bundle refuses it."""
        from haute.deploy._bundler import collect_artifacts
        from haute.errors import DeployError

        client = _Registry(
            {"run_pyfunc": {"model/MLmodel": 1, "model/model.cb": 2}},
            {"1": ("run_pyfunc", "runs:/run_pyfunc/model")},
        )

        resolved = _resolve_registered(client, "pyfunc-model", "1")

        assert (resolved.run_id, resolved.artifact.path) == ("run_pyfunc", "model")
        assert resolved.family.flavor == "pyfunc"
        assert not resolved.loads_from_file

        use_project(tmp_path)
        config = {"source_type": "registered", "registered_model": "pyfunc-model", "version": "1"}
        graph = _g({"nodes": [{"id": "ms", "data": {"nodeType": "modelScore", "config": config}}]})
        with (
            patch("haute._mlflow_utils.pinned_client", return_value=client),
            pytest.raises(
                DeployError, match=r"Node 'ms' scores the MLflow pyfunc model folder 'model'"
            ),
        ):
            collect_artifacts(graph, [], tmp_path)

    def test_rsglm_artifact_resolved(self):
        """Registered model with .rsglm artifact is resolved correctly."""
        client = _Registry(
            {"run_glm": {"model.rsglm": 7}},
            {"1": ("run_glm", "runs:/run_glm/model.rsglm")},
        )

        resolved = _resolve_registered(client, "glm-model", "1")

        assert (resolved.run_id, resolved.artifact.path) == ("run_glm", "model.rsglm")
        assert resolved.family.flavor == "rustystats"
        assert resolved.artifact.identity == "size:7"
        assert resolved.contract is None


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
            input_node_ids=["quote_input"],
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
            input_node_ids=["quote_input"],
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
        # Trained by Haute without an offset.
        mock_model.get_metadata.return_value = {"haute_offset_column": ""}

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
                                "source_type": "run",
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

        with (
            patch(
                "haute._mlflow_io._load_catboost_model",
                return_value=mock_model,
            ),
            patch(
                "haute._mlflow_io.load_mlflow_model",
                side_effect=AssertionError("bundled scoring must not contact MLflow"),
            ) as remote_loader,
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
        remote_loader.assert_not_called()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_infer_input_schema(self, full_graph: dict) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_input_schema

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        schema = infer_input_schema(pruned, "quote_input")

        assert isinstance(schema, dict)
        assert len(schema) > 0
        assert "VehPower" in schema
        assert "Area" in schema

    def test_infer_output_schema(self, full_graph: dict, tmp_path: Path) -> None:
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._schema import infer_output_schema

        output_id = find_output_node(full_graph)
        pruned, _kept, _removed = prune_for_deploy(full_graph, output_id)

        schema = infer_output_schema(pruned, output_id, ["quote_input"], project_root=tmp_path)

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
    def test_validate_passes_for_good_config(self, full_graph: dict, tmp_path: Path) -> None:
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._config import DeployConfig, ResolvedDeploy
        from haute.deploy._project_modules import ProjectModules
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._request_rows import RequestRows
        from haute.deploy._schema import infer_input_schema, infer_output_schema
        from haute.deploy._validators import validate_deploy
        from tests._deploy_helpers import artifact_digests, resolved_paths

        output_id = find_output_node(full_graph)
        pruned, _kept, removed = prune_for_deploy(full_graph, output_id)
        artifacts = collect_artifacts(pruned, ["quote_input"], FIXTURE_DIR)
        input_schema = infer_input_schema(pruned, "quote_input")
        output_schema = infer_output_schema(
            pruned, output_id, ["quote_input"], project_root=tmp_path
        )

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )
        resolved = ResolvedDeploy(
            config=config,
            **resolved_paths(config),
            full_graph=full_graph,
            pruned_graph=pruned,
            input_node_ids=["quote_input"],
            output_node_id=output_id,
            artifacts=artifacts,
            input_schema=input_schema,
            output_schema=output_schema,
            project_modules=ProjectModules(utility=None, unbundled_imports=()),
            request_rows=RequestRows(multi_quote="in_request_order"),
            artifact_digests=artifact_digests(artifacts),
            removed_node_ids=removed,
        )

        validate_deploy(resolved)

    def test_score_test_quotes(self, full_graph: dict, tmp_path: Path) -> None:
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._config import DeployConfig, ResolvedDeploy
        from haute.deploy._project_modules import ProjectModules
        from haute.deploy._pruner import find_output_node, prune_for_deploy
        from haute.deploy._request_rows import RequestRows
        from haute.deploy._schema import infer_input_schema, infer_output_schema
        from haute.deploy._validators import score_test_quotes
        from tests._deploy_helpers import artifact_digests, resolved_paths

        output_id = find_output_node(full_graph)
        pruned, _kept, removed = prune_for_deploy(full_graph, output_id)
        artifacts = collect_artifacts(pruned, ["quote_input"], FIXTURE_DIR)
        input_schema = infer_input_schema(pruned, "quote_input")
        output_schema = infer_output_schema(
            pruned, output_id, ["quote_input"], project_root=tmp_path
        )

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
            test_quotes_dir=FIXTURE_DIR / "quotes",
        )
        resolved = ResolvedDeploy(
            config=config,
            **resolved_paths(config),
            full_graph=full_graph,
            pruned_graph=pruned,
            input_node_ids=["quote_input"],
            output_node_id=output_id,
            artifacts=artifacts,
            input_schema=input_schema,
            output_schema=output_schema,
            project_modules=ProjectModules(utility=None, unbundled_imports=()),
            request_rows=RequestRows(multi_quote="in_request_order"),
            artifact_digests=artifact_digests(artifacts),
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
        assert config.pipeline_file == Path("examples/reference/main.py")
        assert config.databricks.experiment_name == "/Shared/haute/motor-pricing"
        assert config.databricks.serving_workload_size == "Small"

    def test_override(self) -> None:
        from haute.deploy._config import DeployConfig

        config = DeployConfig.from_toml(Path("haute.toml"))
        overridden = config.override(model_name="custom-name")
        assert overridden.model_name == "custom-name"
        assert config.model_name == "motor-pricing"  # original unchanged

    def test_assistant_tables_share_the_closed_project_schema(self, tmp_path: Path) -> None:
        from haute.deploy._config import DeployConfig

        path = tmp_path / "haute.toml"
        path.write_text(
            """
[project]
name = "test"

[assistant]
provider = "databricks"
model = "databricks-model"

[assistant.egress]
trust = "organization"
max_sensitivity = "internal"
allow_project_knowledge = true
allow_executable_source = false
allow_row_samples = false
allow_aggregate_statistics = false
""".lstrip(),
            encoding="utf-8",
        )

        assert DeployConfig.from_toml(path).model_name == "test"

        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "allow_row_samples = false",
                "allow_rows = false",
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=r"\[assistant\.egress\] unknown key 'allow_rows'"):
            DeployConfig.from_toml(path)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_a_resolved_deployment_validates_in_its_own_project_with_none_installed(
        self, tmp_path: Path
    ) -> None:
        """A caller of the deploy API need not install a project: validation and its
        test quotes run in the project the deployment was resolved in."""
        import shutil

        from haute._project_context import _CURRENT_PROJECT
        from haute.deploy._config import DeployConfig, resolve_config
        from haute.deploy._validators import validate_deploy

        shutil.copytree(
            FIXTURE_DIR, tmp_path / "fixtures", ignore=shutil.ignore_patterns("__pycache__")
        )
        use_project(tmp_path)
        resolved = resolve_config(
            DeployConfig(
                pipeline_file=Path("fixtures/pipeline.py"),
                model_name="test-model",
                test_quotes_dir=tmp_path / "fixtures" / "quotes",
            )
        )
        try:
            _CURRENT_PROJECT.set(None)  # a script calling the API installs nothing
            results = validate_deploy(resolved)
        finally:
            resolved.close()

        # A failed test quote raises: these were scored in the resolved project.
        assert [result["file"] for result in results] == ["basic.json"]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_resolve_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Integration test — resolves using fixture pipeline (has output node).

        A copy of the fixtures: resolution writes its schema cache under the project.
        """
        import shutil

        from haute.deploy._config import DeployConfig, resolve_config

        shutil.copytree(
            FIXTURE_DIR, tmp_path / "fixtures", ignore=shutil.ignore_patterns("__pycache__")
        )
        use_project(tmp_path)
        config = DeployConfig(
            pipeline_file=Path("fixtures/pipeline.py"),
            model_name="test-model",
        )
        resolved = resolve_config(config)

        assert resolved.output_node_id == "quote_response"
        assert "quote_input" in resolved.input_node_ids
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

    def test_concurrent_deploys_each_serve_the_version_they_registered(
        self, deploy_pipeline_file: Path
    ) -> None:
        """Deploys share the build directory, so they run one at a time, and each
        serves the version its own log_model registered."""
        from haute.deploy._mlflow import deploy_to_mlflow

        events: list[str] = []
        registered: list[int] = []
        first_logged = threading.Event()

        def log_model(**_: object) -> MagicMock:
            name = threading.current_thread().name
            events.append(f"{name}:register")
            registered.append(len(registered) + 1)
            if name == "first":
                first_logged.set()
            return MagicMock(registered_model_version=registered[-1])

        results: dict[str, int] = {}
        errors: list[BaseException] = []

        def deploy(name: str) -> None:
            try:
                resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
                results[name] = deploy_to_mlflow(resolved).model_version
            except BaseException as exc:  # surfaced below
                errors.append(exc)

        with mock_mlflow_deploy() as mocks:
            mocks.log_model.side_effect = log_model
            # Each deploy runs in the test's project, as a deploy command does.
            first = context_thread(deploy, args=("first",), name="first")
            second = context_thread(deploy, args=("second",), name="second")
            first.start()
            assert first_logged.wait(30)
            second.start()
            first.join(30)
            second.join(30)

        assert not errors
        assert results == {"first": "1", "second": "2"}
        assert events == ["first:register", "second:register"]

    def test_deploy_sets_tracking_uri(self, deploy_pipeline_file: Path) -> None:
        """deploy_to_mlflow() must call mlflow.set_tracking_uri('databricks')."""
        from haute.deploy._mlflow import DeployResult, deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy() as mocks:
            result = deploy_to_mlflow(resolved)

            mocks.set_tracking_uri.assert_called_once_with("databricks")
            mocks.set_registry_uri.assert_called_once_with("databricks-uc")
            # Everything but the model log goes through a client bound to it.
            mocks.client.assert_any_call(tracking_uri="databricks", registry_uri="databricks-uc")
            mocks.client.return_value.log_dict.assert_called_once()
            mocks.client.return_value.set_terminated.assert_called_once_with("run-1", "FINISHED")
            mocks.log_dict.assert_not_called()
            # Behavioral: result is a well-formed DeployResult
            assert isinstance(result, DeployResult)
            assert result.model_name == "test-model"
            assert result.model_version == "1"
            assert result.build_dir is None

    def test_deploy_to_mlflow_runs_in_the_resolved_project_with_none_installed(
        self, deploy_pipeline_file: Path
    ) -> None:
        """A script calling ``deploy_to_mlflow`` directly need not install a project."""
        from haute._project_context import _CURRENT_PROJECT
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        _CURRENT_PROJECT.set(None)

        with mock_mlflow_deploy():
            result = deploy_to_mlflow(resolved)

        assert result.model_version == "1"

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

            mocks.client.return_value.get_experiment_by_name.assert_called_once_with(
                "/Shared/haute/test-staging"
            )
            mocks.set_experiment.assert_not_called()
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
                progress=ANY,
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
            config=config,
            uc_model_name="main.pricing.my-model",
            model_version=1,
            progress=lambda _message: None,
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
            patch("haute.deploy._mlflow.wait_for_endpoint") as wait,
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
                progress=lambda _message: None,
            )

            mock_ws.serving_endpoints.create.assert_called_once()
            # The deploy returns only once the endpoint serves the new version.
            assert wait.call_args.args == (mock_ws, "my-endpoint")
            assert wait.call_args.kwargs["version"] == "2"
            assert url == "https://myhost/serving-endpoints/my-endpoint/invocations"
            created = mock_ws.serving_endpoints.create.call_args.kwargs["config"]
            assert created.served_entities[0].environment_vars == _served_polars_settings()

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
            patch("haute.deploy._mlflow.wait_for_endpoint") as wait,
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
                progress=lambda _message: None,
            )

            assert wait.call_args.kwargs["version"] == "3"

            mock_ws.serving_endpoints.update_config.assert_called_once()
            mock_ws.serving_endpoints.create.assert_not_called()
            assert url == "https://myhost/serving-endpoints/my-endpoint/invocations"
            updated = mock_ws.serving_endpoints.update_config.call_args.kwargs
            assert updated["served_entities"][0].environment_vars == _served_polars_settings()


def _served_polars_settings() -> dict[str, str]:
    """Haute's Polars settings, as every process it starts runs under them."""
    return {
        "POLARS_OOC_MEMORY_BUDGET_FRACTION": "1000000",
        "POLARS_OOC_MEMORY_BUDGET_MB": "1000000000",
        "POLARS_OOC_DISK_BUDGET_MB": "1024",
        "POLARS_OOC_SPILL_DIR": "/tmp/haute/.haute_cache/polars-spill",
        "POLARS_UNKNOWN_EXTENSION_TYPE_BEHAVIOR": "load_as_storage",
    }


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
            if n.id == "quote_input":
                quotes_node = n
                break

        assert quotes_node is not None, "quote_input node not found"
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


@pytest.mark.sandbox_strict
class TestProgressCallback:
    """The optional progress callback (line 76) must receive step messages."""

    def test_progress_callback_receives_messages(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        messages: list[str] = []

        with mock_mlflow_deploy():
            deploy_to_mlflow(resolved, progress=messages.append)

        # The callback body (the guarded `progress(msg)`) must have run.
        assert messages
        assert any("Databricks" in m for m in messages)

    def test_no_progress_callback_does_not_raise(self, deploy_pipeline_file: Path) -> None:
        """When progress is None the guard short-circuits and no error occurs."""
        from haute.deploy._mlflow import DeployResult, deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy():
            result = deploy_to_mlflow(resolved, progress=None)

        assert isinstance(result, DeployResult)


@pytest.mark.sandbox_strict
class TestArtifactCopy:
    """Resolved artifacts (line 109) must be merged into the log_model artifacts."""

    def test_resolved_artifacts_are_passed_to_log_model(
        self, deploy_pipeline_file: Path, tmp_path: Path
    ) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        artifact_file = tmp_path / "model.cbm"
        artifact_file.write_text("artifact", encoding="utf-8")
        resolved = _make_resolved(
            pipeline_file=deploy_pipeline_file,
            artifacts={"my_model": artifact_file},
        )

        staged: dict[str, bytes] = {}

        def read_staged(**kwargs: object) -> MagicMock:
            artifacts = kwargs["artifacts"]
            assert isinstance(artifacts, dict)
            staged.update({name: Path(path).read_bytes() for name, path in artifacts.items()})
            return MagicMock(registered_model_version=1)

        with mock_mlflow_deploy() as mocks:
            mocks.log_model.side_effect = read_staged
            deploy_to_mlflow(resolved)

        artifacts = mocks.log_model.call_args.kwargs["artifacts"]
        # The manifest is always present; the resolved artifact goes in as a copy staged
        # under the digest resolution recorded, removed once the model is logged.
        assert "deploy_manifest" in artifacts
        assert staged["my_model"] == artifact_file.read_bytes()
        assert Path(artifacts["my_model"]).name == "my_model"
        assert not Path(artifacts["my_model"]).exists()


@pytest.mark.sandbox_strict
class TestVersionSelection:
    """When the registry already has versions (line 138), the max is selected."""

    def test_a_concurrent_registration_does_not_change_the_served_version(
        self, deploy_pipeline_file: Path
    ) -> None:
        """The version served is the one this deploy's log_model registered.

        Another job registering the same name in between owns the registry's
        highest version; serving that would price on another model.
        """
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        competing = MagicMock()
        competing.version = "9"

        with (
            mock_mlflow_deploy() as mocks,
            patch("haute.deploy._mlflow.search_versions", return_value=[competing]) as search,
        ):
            mocks.log_model.return_value.registered_model_version = 7
            result = deploy_to_mlflow(resolved)

        assert result.model_version == "7"
        assert result.model_uri.endswith("/7")
        assert mocks.create_or_update_endpoint.call_args.kwargs["model_version"] == 7
        search.assert_not_called()

    def test_no_versions_fails_instead_of_synthesising_one(
        self, deploy_pipeline_file: Path
    ) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow
        from haute.errors import DeployError

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)

        with mock_mlflow_deploy() as mocks:
            mocks.log_model.return_value.registered_model_version = None
            with pytest.raises(DeployError, match="registered model version") as exc_info:
                deploy_to_mlflow(resolved)

        assert "main.pricing.test-model" in str(exc_info.value)


@pytest.mark.sandbox_strict
class TestBuildDirCleanup:
    """DEP-R12A: a deploy stages in a directory of its own under the project's
    ``.haute_build`` and removes only that one, shipped or failed, unless kept."""

    def test_failure_removes_only_its_own_build_dir(
        self, deploy_pipeline_file: Path, tmp_path: Path
    ) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        # The project is the pipeline's own directory, the test's scratch directory.
        builds = tmp_path / ".haute_build"
        assert resolved.project_root == tmp_path.resolve()
        builds.mkdir()
        (builds / "notes.txt").write_text("the user's own file")

        with mock_mlflow_deploy() as mocks:
            mocks.log_model.side_effect = RuntimeError("boom")
            with pytest.raises(RuntimeError, match="boom"):
                deploy_to_mlflow(resolved)

        assert [path.name for path in builds.iterdir()] == ["notes.txt"]

    def test_success_removes_the_build_dir_unless_kept(self, deploy_pipeline_file: Path) -> None:
        from haute.deploy._mlflow import deploy_to_mlflow

        resolved = _make_resolved(pipeline_file=deploy_pipeline_file)
        builds = resolved.project_root / ".haute_build"

        with mock_mlflow_deploy():
            shipped = deploy_to_mlflow(resolved)
            kept = deploy_to_mlflow(resolved, keep_build=True)

        assert shipped.build_dir is None
        assert kept.build_dir is not None
        assert kept.build_dir.parent == builds
        assert (kept.build_dir / "deploy_manifest.json").is_file()
        assert list(builds.iterdir()) == [kept.build_dir]


# ===========================================================================
# deploy/_model_code.py — additional edge cases
# ===========================================================================


@pytest.mark.sandbox_strict
class TestHauteModelEdgeCases:
    """Additional tests for HauteModel."""

    def test_load_context_multiple_artifacts(self, tmp_path):
        """load_context maps every artifact declared by the manifest."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "polars_version": pl.__version__,
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "input_schema": {},
            "omittable_inputs": [],
            "request_rows": {
                "multi_quote": "in_request_order",
                "refusals": [],
                "checked_lookups": [],
            },
            "output_fields": None,
            "artifacts": {
                "model.pkl": "/orig/model.pkl",
                "scaler.pkl": "/orig/scaler.pkl",
                "encoder.pkl": "/orig/encoder.pkl",
            },
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {
            "deploy_manifest": str(manifest_path),
            "model.pkl": "/served/model.pkl",
            "scaler.pkl": "/served/scaler.pkl",
            "encoder.pkl": "/served/encoder.pkl",
        }
        model.load_context(ctx)

        assert model._artifact_paths == {
            "model.pkl": "/served/model.pkl",
            "scaler.pkl": "/served/scaler.pkl",
            "encoder.pkl": "/served/encoder.pkl",
        }

    def test_load_context_empty_artifacts(self, tmp_path):
        """Manifest with no artifacts results in empty artifact_paths."""
        from haute.deploy._model_code import HauteModel

        manifest = {
            "polars_version": pl.__version__,
            "pruned_graph": {"nodes": [], "edges": []},
            "input_node_ids": ["src"],
            "output_node_id": "out",
            "input_schema": {},
            "omittable_inputs": [],
            "request_rows": {
                "multi_quote": "in_request_order",
                "refusals": [],
                "checked_lookups": [],
            },
            "artifacts": {},
        }
        manifest_path = tmp_path / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        model = HauteModel()
        ctx = MagicMock()
        ctx.artifacts = {"deploy_manifest": str(manifest_path)}
        model.load_context(ctx)

        assert model._artifact_paths == {}

    def test_predict_input_converted_to_polars(self):
        """predict() converts pandas input to polars for score_graph."""
        import pandas as pd

        from haute.deploy._model_code import HauteModel

        model = HauteModel()
        model._graph = MagicMock()
        model._project = current_project()
        model._input_node_ids = ["src"]
        model._output_node_id = "out"
        model._artifact_paths = {}
        model._output_fields = None
        model._request_rows = RequestRows("in_request_order")
        model._reader = RequestReader(records=False, schema={"a": pl.Int64(), "b": pl.Float64()})

        input_pd = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
        mock_result = pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})

        with patch("haute.deploy._scorer.score_graph", return_value=mock_result) as mock:
            result = model.predict(MagicMock(), input_pd)

        # Verify input was converted to polars
        call_kw = mock.call_args.kwargs
        assert isinstance(call_kw["input_df"], pl.DataFrame)
        assert call_kw["input_df"].columns == ["a", "b"]
        assert call_kw["input_df"].shape == (2, 2)

        # Verify output was converted back to pandas
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["a", "b"]


# ===========================================================================
# deploy/_schema.py — _find_node and edge cases
# ===========================================================================


@pytest.mark.sandbox_strict
class TestFindNode:
    """Direct tests for _find_node."""

    def test_finds_existing_node(self):
        """_find_node returns the correct node when it exists."""
        from haute.deploy._schema import _find_node

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": "/data"},
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
            }
        )

        node = _find_node(graph, "src")
        assert node.id == "src"
        assert node.data.config.get("path") == "/data"

    def test_raises_for_missing_node(self):
        """_find_node raises ConfigError for non-existent node ID."""
        from haute.deploy._schema import _find_node
        from haute.errors import ConfigError

        graph = _g({"nodes": []})
        with pytest.raises(ConfigError, match="not found"):
            _find_node(graph, "nonexistent")

    def test_raises_message_includes_node_id(self):
        """ConfigError carries the missing node ID in context."""
        from haute.deploy._schema import _find_node
        from haute.errors import ConfigError

        graph = _g({"nodes": []})
        with pytest.raises(ConfigError) as exc_info:
            _find_node(graph, "my_missing_node")
        assert exc_info.value.context["node_id"] == "my_missing_node"


@pytest.mark.sandbox_strict
class TestInferOutputSchemaEdgeCases:
    """Edge cases for infer_output_schema."""

    def test_no_input_node_ids_raises(self, monkeypatch, tmp_path):
        """infer_output_schema with empty input_node_ids raises ValueError."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
            }
        )

        with pytest.raises(ValueError, match="No API input nodes"):
            infer_output_schema(graph, "out", [], project_root=tmp_path)

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_cache_write_failure_does_not_raise(self, monkeypatch, tmp_path):
        """If cache writing fails, infer_output_schema still returns the schema."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

        pq_path = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0]}).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(pq_path)},
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "out"}],
            }
        )

        mock_result = pl.DataFrame({"result": [42.0]})

        # The cache's one write fails as a read-only or full disk would.
        with (
            patch("haute.deploy._scorer.score_graph", return_value=mock_result),
            patch(
                "haute.deploy._schema.atomic_write_text", side_effect=OSError("read-only")
            ) as write,
        ):
            result = infer_output_schema(graph, "out", ["src"], project_root=tmp_path)

        # Should still return the computed schema despite cache write failure
        write.assert_called_once()
        assert result == {"result": "Float64"}

    def test_input_node_with_no_path_for_sample_raises(self, monkeypatch, tmp_path):
        """infer_output_schema raises when input node has no path for sample row."""
        from haute.deploy._schema import infer_output_schema

        use_project(tmp_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {},  # no path
                        },
                    },
                    {
                        "id": "out",
                        "data": {"label": "out", "nodeType": "output", "config": {}},
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "out"}],
            }
        )

        with pytest.raises(ValueError, match="no path"):
            infer_output_schema(graph, "out", ["src"], project_root=tmp_path)


@pytest.mark.sandbox_strict
class TestInferInputSchemaEdgeCases:
    """Extra edge cases for infer_input_schema."""

    def test_csv_file_schema(self, tmp_path):
        """infer_input_schema can read schema from CSV files."""
        from haute.deploy._schema import infer_input_schema

        csv_path = tmp_path / "input.csv"
        pl.DataFrame({"name": ["alice"], "score": [95.5]}).write_csv(csv_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(csv_path)},
                        },
                    },
                ],
            }
        )

        schema = infer_input_schema(graph, "src")
        assert "name" in schema
        assert "score" in schema
        assert isinstance(schema["name"], str)

    def test_parquet_multiple_columns(self, tmp_path):
        """infer_input_schema returns all columns from a multi-column parquet."""
        from haute.deploy._schema import infer_input_schema

        pq_path = tmp_path / "input.parquet"
        pl.DataFrame(
            {"age": [25], "premium": [100.5], "region": ["A"], "active": [True]}
        ).write_parquet(pq_path)

        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "apiInput",
                            "config": {"path": str(pq_path)},
                        },
                    },
                ],
            }
        )

        schema = infer_input_schema(graph, "src")
        assert len(schema) == 4
        assert set(schema.keys()) == {"age", "premium", "region", "active"}
