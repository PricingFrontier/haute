"""Tests for MODEL_SCORE codegen and parser round-trip."""

from __future__ import annotations

from haute._parser_helpers import _build_node_config
from haute.codegen import graph_to_code
from tests.conftest import make_edge, make_graph

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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                                "code": (
                                    "result = result.with_columns("
                                    'doubled=pl.col("prediction") * 2)'
                                ),
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
        assert "return result" in code
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
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
