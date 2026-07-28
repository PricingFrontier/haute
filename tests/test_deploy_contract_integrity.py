"""Phase 1 Package 1E — deploy contract integrity tests.

TDD suite covering:

* #14 — Static Data Input schema/readability drift must raise at prune/bundle time
* #16 — ``validate_deploy`` must raise ``DeployError`` when test quotes fail
* #17 — Bundle includes ``feature_contract.json``; scorer verifies at load

These tests are written ahead of implementation and must fail loudly until
the deploy pipeline starts honouring the feature contract and treating
test-quote failures as fatal.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._mlflow_io import _artifact_cache_path
from haute.deploy._config import DeployConfig, ResolvedDeploy
from haute.errors import DeployError, FeatureMismatchError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    assert_contracts_match,
    build_contract,
    load_contract,
    save_contract,
)
from tests.conftest import make_output_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

FIXTURE_DIR = Path("tests/fixtures")
PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"


def _write_cached_model(tmp_path: Path, run_id: str, artifact_path: str) -> Path:
    cached = _artifact_cache_path(tmp_path / ".cache" / "models", run_id, artifact_path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"fake model")
    return cached


# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests.test_deploy_config but local to avoid
# depending on private test utilities from another file).
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType = NodeType.POLARS,
    config: dict | None = None,
    label: str | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=label or node_id, nodeType=node_type, config=config or {}),
    )


def _make_resolved(
    *,
    nodes: list[GraphNode] | None = None,
    edges: list[GraphEdge] | None = None,
    input_node_ids: list[str] | None = None,
    output_node_id: str = "output",
    artifacts: dict[str, Path] | None = None,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
    config: DeployConfig | None = None,
) -> ResolvedDeploy:
    graph = PipelineGraph(nodes=nodes or [], edges=edges or [])
    return ResolvedDeploy(
        config=config
        or DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        ),
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=input_node_ids or ["api_in"],
        output_node_id=output_node_id,
        artifacts=artifacts or {},
        input_schema=input_schema or {"col": "Int64"},
        output_schema=output_schema or {"result": "Float64"},
    )


# ===========================================================================
# Item #14 — Pruner/bundler must detect static-source schema drift
# ===========================================================================


class TestStaticDataSourceSchemaDrift:
    """A retained Data Input must satisfy its canonical schema at bundle time."""

    def test_pruner_surfaces_static_source_schema_mismatch(self, tmp_path: Path) -> None:
        """The bundler must execute a bounded readability probe against the schema."""
        from haute.deploy._bundler import collect_artifacts
        from haute.deploy._pruner import prune_for_deploy

        # The declared schema order disagrees with the file. Polars can build
        # a LazyFrame schema from this declaration without touching a row, so
        # bundle validation must force a bounded readability probe.
        bad_csv = tmp_path / "area_factors.csv"
        bad_csv.write_text("factor,area\n1.2,A\n1.3,B\n")

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "static_ds",
                        "data": {
                            "label": "static_ds",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "csv",
                                "mode": "scan",
                                "cacheMode": "snapshot",
                                "path": str(bad_csv),
                                "arguments": {
                                    "schema": {
                                        "area": "String",
                                        "factor": "Float64",
                                    }
                                },
                            },
                        },
                    },
                    {
                        "id": "output",
                        "data": {
                            "label": "output",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "static_ds", "target": "output"},
                ],
            }
        )

        pruned, _kept, _removed = prune_for_deploy(graph, "output")

        with pytest.raises(DeployError) as exc_info:
            collect_artifacts(pruned, [], tmp_path)

        msg = str(exc_info.value)
        assert "static_ds" in msg or "area_factors" in msg or "column" in msg.lower()

    def test_static_source_schema_declaration_mismatch_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """Deploy bundling must validate static source schema declarations."""
        from haute.deploy._bundler import collect_artifacts

        source_path = tmp_path / "static.csv"
        source_path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "static_ds",
                        "data": {
                            "label": "static_ds",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "csv",
                                "mode": "scan",
                                "cacheMode": "snapshot",
                                "path": str(source_path),
                                "arguments": {
                                    "schema": {
                                        "quote_id": "String",
                                    }
                                },
                            },
                        },
                    }
                ],
                "edges": [],
            }
        )

        with pytest.raises(DeployError, match="static_ds"):
            collect_artifacts(graph, [], tmp_path)

    def test_static_plain_json_source_rejected_by_bounded_deploy_validation(
        self,
        tmp_path: Path,
    ) -> None:
        """Plain JSON static sources should fail before deployed batch scoring."""
        from haute.deploy._bundler import collect_artifacts

        source_path = tmp_path / "static.json"
        source_path.write_text(json.dumps([{"quote_id": "001"}]), encoding="utf-8")
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "static_ds",
                        "data": {
                            "label": "static_ds",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "json",
                                "mode": "read",
                                "cacheMode": "snapshot",
                                "path": str(source_path),
                                "arguments": {},
                            },
                        },
                    }
                ],
                "edges": [],
            }
        )

        with pytest.raises(DeployError) as exc_info:
            collect_artifacts(graph, [], tmp_path)

        message = str(exc_info.value).lower()
        assert "static_ds" in message
        assert "json" in message
        assert "bounded" in message


# ===========================================================================
# Item #16 — validate_deploy must fail on test-quote errors
# ===========================================================================


class TestValidateDeployFailsOnTestQuotes:
    """``validate_deploy`` currently captures test-quote errors into result
    dicts; the CLI layer manually checks them.  Fail-loud policy: the
    validation function itself must raise ``DeployError`` when any test
    quote fails scoring.
    """

    def test_test_quote_scoring_failure_raises_deploy_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a JSON quote is missing required features for scoring,
        validate_deploy must raise ``DeployError`` — not return an empty
        errors list while the broken quote silently fails downstream.
        """
        from haute.deploy._validators import validate_deploy

        # Set up a test_quotes directory with a deliberately bad quote.
        tq_dir = tmp_path / "quotes"
        tq_dir.mkdir()
        (tq_dir / "bad.json").write_text(
            json.dumps([{"input": {"wrong_column": 1, "also_wrong": 2}}])
        )

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="broken-model",
            test_quotes_dir=tq_dir,
        )

        inp = _make_node("api_in", node_type=NodeType.API_INPUT)
        out = _make_node("output", node_type=NodeType.OUTPUT, config=make_output_config([]))
        edge = GraphEdge(id="e1", source="api_in", target="output", sourceHandle="api_in")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["api_in"],
            output_node_id="output",
            input_schema={"required_col": "Int64"},
            output_schema={"prediction": "Float64"},
            config=config,
        )

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)

        msg = str(exc_info.value)
        assert "bad.json" in msg or "test quote" in msg.lower() or "quote" in msg.lower()

    def test_validate_deploy_succeeds_when_no_test_quotes(self, tmp_path: Path) -> None:
        """Without a test_quotes directory, validate_deploy succeeds."""
        from haute.deploy._validators import validate_deploy

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="ok-model",
            test_quotes_dir=None,
        )
        inp = _make_node("api_in", node_type=NodeType.API_INPUT)
        out = _make_node("output", node_type=NodeType.OUTPUT, config=make_output_config([]))
        edge = GraphEdge(id="e1", source="api_in", target="output", sourceHandle="api_in")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["api_in"],
            output_node_id="output",
            config=config,
        )
        assert validate_deploy(resolved) is None

    def test_validate_deploy_aggregates_structural_and_test_errors(self, tmp_path: Path) -> None:
        """If there are BOTH structural errors AND failing test quotes, the
        DeployError must include both sources so the operator sees the full
        picture at once — not a trickle of one error per rerun.
        """
        from haute.deploy._validators import validate_deploy

        tq_dir = tmp_path / "quotes"
        tq_dir.mkdir()
        (tq_dir / "bad.json").write_text(json.dumps([{"input": {"nope": 1}}]))

        # Missing output node → structural error.  Plus a broken test quote.
        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="both-fail",
            test_quotes_dir=tq_dir,
        )
        inp = _make_node("api_in", node_type=NodeType.API_INPUT)
        resolved = _make_resolved(
            nodes=[inp],
            edges=[],
            input_node_ids=["api_in"],
            output_node_id="ghost_output",  # not in graph
            input_schema={"nope": "Int64"},
            output_schema={"prediction": "Float64"},
            config=config,
        )

        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        msg = str(exc_info.value)
        assert "ghost_output" in msg
        # The quote failure context should also be surfaced — aggregated
        # errors give the operator a full picture in one go.
        assert "bad.json" in msg or "quote" in msg.lower()


# ===========================================================================
# Item #17 — feature_contract.json bundled; scorer verifies at load time
# ===========================================================================


class TestFeatureContractBundled:
    """After bundling, the deploy artifact layout contains a
    ``feature_contract.json`` whose hash matches the training contract.
    At scoring time the deploy scorer rebuilds the contract from live
    inputs and calls ``assert_contracts_match``; drift must raise
    :class:`FeatureMismatchError`.
    """

    def test_bundler_writes_feature_contract_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After ``collect_artifacts`` runs on a graph with a modelScore node,
        ``feature_contract.json`` is present in the returned artifact map
        with a content hash matching the training-time contract.
        """
        from haute.deploy._bundler import collect_artifacts

        monkeypatch.chdir(tmp_path)

        # Pre-populate the MLflow disk cache and write a fake contract
        # alongside the model artifact as training-time would.
        cached_model = _write_cached_model(tmp_path, "run_contract", "model.cbm")
        cache_dir = cached_model.parent

        training_contract = build_contract(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="ClaimCount",
            target_type="Int64",
            task="regression",
        )
        save_contract(training_contract, cache_dir / CONTRACT_FILENAME)

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "ms_contract",
                        "data": {
                            "label": "ms_contract",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_contract",
                                "artifact_path": "model.cbm",
                            },
                        },
                    },
                ],
            }
        )

        artifacts = collect_artifacts(graph, [], tmp_path)
        # Must bundle the feature contract alongside the .cbm; the key name
        # is not fixed but the filename must be feature_contract.json.
        contract_paths = [p for p in artifacts.values() if p.name == CONTRACT_FILENAME]
        assert contract_paths, (
            f"Bundle must include {CONTRACT_FILENAME}; got: "
            f"{sorted(p.name for p in artifacts.values())}"
        )
        # Hash matches the training contract.
        loaded = load_contract(contract_paths[0])
        assert_contracts_match(training_contract, loaded)

    def test_scorer_raises_when_runtime_contract_mismatches(self, tmp_path: Path) -> None:
        """At load time the deploy scorer rebuilds the runtime contract from
        the live input data; if it disagrees with the bundled contract
        the scorer must raise ``FeatureMismatchError``.
        """
        from haute.deploy._scorer import score_graph

        training_contract = build_contract(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        contract_path = tmp_path / CONTRACT_FILENAME
        save_contract(training_contract, contract_path)

        # Runtime data: region typed as Int64 instead of String → drift.
        live_df = pl.DataFrame(
            {
                "age": [25, 30],
                "region": [1, 2],  # should have been strings
            }
        )

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    {
                        "id": "api_in",
                        "data": {
                            "label": "api_in",
                            "nodeType": "apiInput",
                            "config": {},
                        },
                    },
                    {
                        "id": "ms_contract",
                        "data": {
                            "label": "ms_contract",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run_contract",
                                "artifact_path": "model.cbm",
                                "feature_contract_path": str(contract_path),
                            },
                        },
                    },
                    {
                        "id": "output",
                        "data": {
                            "label": "output",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "api_in",
                        "target": "ms_contract",
                        "sourceHandle": "api_in",
                    },
                    {"id": "e2", "source": "ms_contract", "target": "output"},
                ],
            }
        )

        # The scorer must detect the drift via assert_contracts_match and raise.
        with pytest.raises(FeatureMismatchError):
            score_graph(
                graph=graph,
                input_df=live_df,
                input_node_ids=["api_in"],
                output_node_id="output",
                artifact_paths={
                    # Bundled contract — scorer looks this up when loading.
                    f"ms_contract__{CONTRACT_FILENAME}": str(contract_path),
                },
            )

    def test_training_writes_contract_next_to_model(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After ``TrainingJob.run`` the ``output_dir`` contains both the
        .cbm and the per-model ``{name}.feature_contract.json`` (4b.9 —
        models sharing one output_dir keep distinct contracts) with a
        hash consistent with the training features.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        import numpy as np

        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])

        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(42)
        n = 80
        df = pl.DataFrame(
            {
                "age": rng.randint(18, 70, n).astype(np.float64),
                "region": rng.choice(["north", "south"], n),
                "ClaimCount": rng.poisson(0.2, n).astype(np.float64),
                "Exposure": np.ones(n),
            }
        )
        job = TrainingJob(
            name="contract_model",
            data=df,
            target="ClaimCount",
            weight="Exposure",
            params={"iterations": 1, "depth": 1, "verbose": 0},
            output_dir=str(tmp_path),
        )
        result = job.run()

        from haute.modelling._training_job import model_contract_filename

        contract_path = Path(tmp_path) / model_contract_filename("contract_model")
        assert contract_path.is_file(), (
            f"Training must write {model_contract_filename('contract_model')} next to "
            f"the model so scorers can be pointed at it; got: "
            f"{sorted(p.name for p in tmp_path.iterdir())}"
        )
        contract = load_contract(contract_path)
        assert set(contract.features) == set(result.features)
        assert set(contract.categorical_features) == set(result.cat_features)
        assert contract.task == "regression"
        assert contract.target_name == "ClaimCount"
