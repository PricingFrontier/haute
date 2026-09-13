"""Tests for MODEL_SCORE codegen and parser round-trip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl

from haute._config_builder import _build_node_config
from haute._mlflow_io import ScoringModel
from haute.codegen import graph_to_code
from tests.conftest import make_edge, make_file_input_config, make_graph

SCORER_SIDECAR = "config/model_scoring/scorer.json"


def _model_score_graph(extra_config: dict[str, Any] | None = None):
    """Two-node graph: parquet data input feeding a run-based model score."""
    config: dict[str, Any] = {
        "sourceType": "run",
        "run_id": "run123",
        "artifact_path": "model.cbm",
        "task": "regression",
        "output_column": "prediction",
    }
    config.update(extra_config or {})
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
                        "config": config,
                    },
                },
            ],
            "edges": [make_edge("source", "scorer").model_dump()],
        }
    )


def _write_sidecars(graph, base_dir: Path) -> dict[str, str]:
    """Write every node sidecar under *base_dir*, returning path → JSON text."""
    from haute._config_io import collect_node_configs

    configs = collect_node_configs(graph)
    for rel_path, content in configs.items():
        cfg_file = base_dir / rel_path
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(content, encoding="utf-8")
    return configs


def _stub_scoring_model(predictions: Any = None) -> ScoringModel:
    """Minimal CatBoost-flavoured ScoringModel over a mock estimator."""
    model = MagicMock()
    model.feature_names_ = ["a", "b"]
    model.classes_ = np.array([0, 1])
    model.predict.return_value = np.array(predictions if predictions is not None else [0.5])
    del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=["a", "b"],
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


class TestModelScoreCodegen:
    def test_codegen_run_based(self):
        """Generates thin delegation code for run-based model scoring."""
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
                        "id": "score",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "abc123",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "prediction",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        code = graph_to_code(graph)

        # Decorator post-processed to config= path
        assert 'config="config/model_scoring/scorer.json"' in code
        # Thin delegation body
        assert "score_from_config" in code
        assert "from haute.graph_utils import score_from_config" in code
        # No verbose boilerplate
        assert "load_mlflow_model" not in code
        assert "model.predict" not in code
        assert "pyarrow" not in code
        # Upstream is named "source", passed to score_from_config
        assert "score_from_config(source," in code
        # Generated code must be valid Python
        compile(code, "<test_run_based>", "exec")

    def test_codegen_registered(self):
        """Generates thin delegation code for registered model scoring."""
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
                        "id": "score",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "registered",
                                "registered_model": "my-model",
                                "version": "3",
                                "task": "regression",
                                "output_column": "pred",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        code = graph_to_code(graph)

        assert "score_from_config" in code
        assert 'config="config/model_scoring/scorer.json"' in code
        compile(code, "<test_registered>", "exec")

    def test_codegen_classification_same_template(self):
        """Classification task uses the same thin delegation (no inline proba)."""
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
                        "id": "score",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "abc",
                                "artifact_path": "model.cbm",
                                "task": "classification",
                                "output_column": "prediction",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        code = graph_to_code(graph)

        # Classification uses same thin template — proba handled by library
        assert "score_from_config" in code
        assert "predict_proba" not in code
        compile(code, "<test_classification>", "exec")

    def test_codegen_regression_no_proba(self):
        """Regression task uses thin delegation with no proba code."""
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
                        "id": "score",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "abc",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "prediction",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        code = graph_to_code(graph)

        assert "predict_proba" not in code
        compile(code, "<test_regression>", "exec")

    def test_codegen_with_user_code(self):
        """User post-processing code appears after sentinel in thin body."""
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
                        "id": "score",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "abc",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "prediction",
                                "code": ('df = df.with_columns(doubled=pl.col("prediction") * 2)'),
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "score").model_dump()],
            }
        )
        code = graph_to_code(graph)

        assert "score_from_config" in code
        assert "doubled" in code
        assert 'df = df.with_columns(doubled=pl.col("prediction") * 2)' in code
        assert "return df" in code
        assert "return result" not in code
        compile(code, "<test_user_code>", "exec")


# ---------------------------------------------------------------------------
# Parser helpers — _build_node_config
# ---------------------------------------------------------------------------


class TestBuildNodeConfigModelScore:
    def test_build_config_run_based(self):
        """Builds correct config from run-based decorator kwargs."""
        kwargs = {
            "model_score": True,
            "source_type": "run",
            "run_id": "abc123",
            "artifact_path": "model.cbm",
            "task": "regression",
            "output_column": "prediction",
        }
        config = _build_node_config("modelScore", kwargs, "", ["df"])

        assert config["sourceType"] == "run"
        assert config["run_id"] == "abc123"
        assert config["artifact_path"] == "model.cbm"
        assert config["task"] == "regression"
        assert config["output_column"] == "prediction"

    def test_build_config_registered(self):
        """Builds correct config from registered model decorator kwargs."""
        kwargs = {
            "model_score": True,
            "source_type": "registered",
            "registered_model": "my-model",
            "version": "2",
            "task": "classification",
            "output_column": "pred",
        }
        config = _build_node_config("modelScore", kwargs, "", ["df"])

        assert config["sourceType"] == "registered"
        assert config["registered_model"] == "my-model"
        assert config["version"] == "2"
        assert config["task"] == "classification"
        assert config["output_column"] == "pred"


# ---------------------------------------------------------------------------
# Parser round-trip
# ---------------------------------------------------------------------------


class TestParserRoundTrip:
    def test_codegen_parses_back(self, tmp_path):
        """Generated code can be parsed back into a graph."""
        from haute._config_io import collect_node_configs
        from haute.parser import parse_pipeline_source

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
                        "id": "scorer",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run123",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "prediction",
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "scorer").model_dump()],
            }
        )
        code = graph_to_code(graph)

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        # Parse it back
        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        node_map = {n.data.label: n for n in parsed.nodes}

        assert "scorer" in node_map
        scorer = node_map["scorer"]
        assert scorer.data.nodeType == "modelScore"
        assert scorer.data.config.get("sourceType") == "run"
        assert scorer.data.config.get("run_id") == "run123"
        # Auto-generated scaffolding must NOT leak into config["code"]
        assert scorer.data.config.get("code", "") == ""

    def test_roundtrip_preserves_user_code(self, tmp_path):
        """User post-processing code survives codegen → parse round-trip."""
        from haute._config_io import collect_node_configs
        from haute.parser import parse_pipeline_source

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
                        "id": "scorer",
                        "data": {
                            "label": "scorer",
                            "nodeType": "modelScore",
                            "config": {
                                "sourceType": "run",
                                "run_id": "run123",
                                "artifact_path": "model.cbm",
                                "task": "regression",
                                "output_column": "prediction",
                                "code": 'df = df.with_columns(doubled=pl.col("prediction") * 2)',
                            },
                        },
                    },
                ],
                "edges": [make_edge("source", "scorer").model_dump()],
            }
        )
        code = graph_to_code(graph)

        # Post Item #18: parser requires sidecar config files to exist.
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)
        node_map = {n.data.label: n for n in parsed.nodes}
        scorer = node_map["scorer"]
        assert "doubled" in scorer.data.config.get("code", "")


# ---------------------------------------------------------------------------
# Destination persistence (MLF-D03): the sidecar carries it, not the decorator
# ---------------------------------------------------------------------------


class TestModelScoreDestinationPersistence:
    def test_mlflow_destination_persists_in_sidecar_not_decorator(self, tmp_path):
        """The node destination round-trips through the sidecar only."""
        from haute.parser import parse_pipeline_source

        graph = _model_score_graph({"mlflow_destination": "local"})
        code = graph_to_code(graph)

        # Codegen rewrites every config-backed decorator to config=<sidecar>,
        # so the generated module never carries the field as a kwarg.
        assert f'config="{SCORER_SIDECAR}"' in code
        assert "mlflow_destination" not in code

        present_dir = tmp_path / "present"
        present_dir.mkdir()
        configs = _write_sidecars(graph, present_dir)
        sidecar = json.loads(configs[SCORER_SIDECAR])
        assert sidecar["mlflow_destination"] == "local"

        parsed = parse_pipeline_source(code, _base_dir=present_dir)
        scorer = {n.data.label: n for n in parsed.nodes}["scorer"]
        assert scorer.data.nodeType == "modelScore"
        assert scorer.data.config.get("mlflow_destination") == "local"

    def test_absent_mlflow_destination_appears_on_neither_side(self, tmp_path):
        """An unset destination stays absent in the sidecar and the parsed config."""
        from haute.parser import parse_pipeline_source

        graph = _model_score_graph()
        code = graph_to_code(graph)
        assert "mlflow_destination" not in code

        absent_dir = tmp_path / "absent"
        absent_dir.mkdir()
        configs = _write_sidecars(graph, absent_dir)
        sidecar = json.loads(configs[SCORER_SIDECAR])
        assert "mlflow_destination" not in sidecar

        parsed = parse_pipeline_source(code, _base_dir=absent_dir)
        scorer = {n.data.label: n for n in parsed.nodes}["scorer"]
        assert "mlflow_destination" not in scorer.data.config

    def test_score_from_config_forwards_destination(self, tmp_path):
        """A sidecar destination reaches the shared loader as destination=."""
        from haute._model_scorer import score_from_config

        config_path = tmp_path / SCORER_SIDECAR
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "run123",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "prediction",
                    "mlflow_destination": "local",
                }
            ),
            encoding="utf-8",
        )

        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        with patch(
            "haute._mlflow_io.load_mlflow_model",
            return_value=_stub_scoring_model(np.array([0.25])),
        ) as mock_load:
            result = score_from_config(lf, config=str(config_path), base_dir=str(tmp_path))
            collected = result.collect()

        assert "prediction" in collected.columns
        assert mock_load.call_args.kwargs["destination"] == "local"

    def test_score_from_config_defaults_destination_to_auto(self, tmp_path):
        """An absent sidecar destination forwards the auto sentinel."""
        from haute._model_scorer import score_from_config

        config_path = tmp_path / SCORER_SIDECAR
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "run123",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "prediction",
                }
            ),
            encoding="utf-8",
        )

        lf = pl.DataFrame({"a": [1.0], "b": [2.0]}).lazy()
        with patch(
            "haute._mlflow_io.load_mlflow_model",
            return_value=_stub_scoring_model(np.array([0.25])),
        ) as mock_load:
            score_from_config(lf, config=str(config_path), base_dir=str(tmp_path)).collect()

        assert mock_load.call_args.kwargs["destination"] == ""
