"""Tests for modelling codegen and parser roundtrips."""

from __future__ import annotations

import ast

import polars as pl

from haute.graph_utils import GraphNode, NodeData, NodeType
from haute.codegen import _node_to_code, graph_to_code
from haute.modelling._export import generate_training_script
from haute.parser import parse_pipeline_source
from tests.conftest import make_edge, make_graph


# ---------------------------------------------------------------------------
# generate_training_script
# ---------------------------------------------------------------------------


class TestGenerateTrainingScript:
    def test_produces_valid_python(self):
        config = {
            "name": "frequency",
            "target": "ClaimCount",
            "weight": "Exposure",
            "exclude": ["IDpol"],
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 100, "depth": 4},
            "split": {"strategy": "random", "test_size": 0.2, "seed": 42},
            "metrics": ["gini", "rmse"],
        }
        script = generate_training_script(config, "output/frequency.parquet")
        # Should compile without error
        compile(script, "<test>", "exec")

    def test_contains_training_job(self):
        config = {"name": "test", "target": "y", "algorithm": "catboost"}
        script = generate_training_script(config, "data.parquet")
        assert "from haute.modelling import TrainingJob" in script
        assert "TrainingJob(" in script
        assert "name='test'" in script
        assert "target='y'" in script

    def test_includes_mlflow_when_set(self):
        config = {
            "name": "test",
            "target": "y",
            "mlflow_experiment": "/Shared/test",
            "model_name": "test_model",
        }
        script = generate_training_script(config, "data.parquet")
        assert "mlflow_experiment" in script
        assert "model_name" in script

    def test_excludes_mlflow_when_not_set(self):
        config = {"name": "test", "target": "y"}
        script = generate_training_script(config, "data.parquet")
        assert "mlflow_experiment" not in script
        assert "model_name" not in script

    def test_includes_loss_function(self):
        config = {"name": "test", "target": "y", "loss_function": "Poisson"}
        script = generate_training_script(config, "data.parquet")
        assert "loss_function='Poisson'" in script
        # variance_power should not appear for non-Tweedie
        assert "variance_power" not in script
        compile(script, "<test>", "exec")

    def test_includes_tweedie_with_variance_power(self):
        config = {
            "name": "test",
            "target": "y",
            "loss_function": "Tweedie",
            "variance_power": 1.7,
        }
        script = generate_training_script(config, "data.parquet")
        assert "loss_function='Tweedie'" in script
        assert "variance_power=1.7" in script
        compile(script, "<test>", "exec")

    def test_includes_offset(self):
        config = {"name": "test", "target": "y", "offset": "log_exposure"}
        script = generate_training_script(config, "data.parquet")
        assert "offset='log_exposure'" in script
        compile(script, "<test>", "exec")

    def test_includes_monotone_constraints(self):
        config = {
            "name": "test",
            "target": "y",
            "monotone_constraints": {"age": 1, "risk_score": -1},
        }
        script = generate_training_script(config, "data.parquet")
        assert "monotone_constraints=" in script
        assert "'age': 1" in script
        compile(script, "<test>", "exec")

    def test_includes_feature_weights(self):
        config = {
            "name": "test",
            "target": "y",
            "feature_weights": {"age": 2.0, "region": 0.5},
        }
        script = generate_training_script(config, "data.parquet")
        assert "feature_weights=" in script
        compile(script, "<test>", "exec")

    def test_includes_cv_folds(self):
        config = {"name": "test", "target": "y", "cv_folds": 5}
        script = generate_training_script(config, "data.parquet")
        assert "cv_folds=5" in script
        compile(script, "<test>", "exec")

    def test_default_output_dir_is_outputs(self):
        """When no output_dir in config, the generated script defaults to 'outputs'."""
        config = {"name": "test", "target": "y"}
        script = generate_training_script(config, "data.parquet")
        assert "output_dir='outputs'" in script

    def test_explicit_output_dir_is_preserved(self):
        config = {"name": "test", "target": "y", "output_dir": "custom/path"}
        script = generate_training_script(config, "data.parquet")
        assert "output_dir='custom/path'" in script

    def test_excludes_new_params_when_not_set(self):
        config = {"name": "test", "target": "y"}
        script = generate_training_script(config, "data.parquet")
        for param in ["loss_function", "variance_power", "offset",
                       "monotone_constraints", "feature_weights", "cv_folds"]:
            assert param not in script

    def test_reconstructs_full_config(self):
        """Generated script with all new params produces a job with matching config."""
        config = {
            "name": "severity",
            "target": "ClaimAmount",
            "weight": "Exposure",
            "exclude": ["IDpol"],
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 200, "depth": 6},
            "split": {"strategy": "random", "test_size": 0.2, "seed": 42},
            "metrics": ["gini", "rmse"],
            "loss_function": "Tweedie",
            "variance_power": 1.5,
            "offset": "log_exposure",
            "monotone_constraints": {"age": 1},
            "feature_weights": {"age": 2.0},
            "cv_folds": 3,
            "mlflow_experiment": "/Shared/severity",
            "model_name": "severity_model",
        }
        script = generate_training_script(config, "data.parquet")
        compile(script, "<test>", "exec")
        ns: dict = {}
        exec(script, {"__builtins__": __builtins__}, ns)
        job = ns["job"]
        assert job.loss_function == "Tweedie"
        assert job.variance_power == 1.5
        assert job.offset == "log_exposure"
        assert job.monotone_constraints == {"age": 1}
        assert job.feature_weights == {"age": 2.0}
        assert job.cv_folds == 3

    def test_reconstructs_equivalent_config(self):
        """Executing the generated script should produce a job with same config."""
        config = {
            "name": "frequency",
            "target": "ClaimCount",
            "weight": "Exposure",
            "exclude": ["IDpol"],
            "algorithm": "catboost",
            "task": "regression",
            "params": {"iterations": 100},
            "split": {"strategy": "random", "test_size": 0.2, "seed": 42},
            "metrics": ["gini", "rmse"],
        }
        script = generate_training_script(config, "data.parquet")
        ns: dict = {}
        exec(script, {"__builtins__": __builtins__}, ns)
        job = ns["job"]
        assert job.name == "frequency"
        assert job.target == "ClaimCount"
        assert job.weight == "Exposure"
        assert job.exclude == ["IDpol"]
        assert job.algorithm == "catboost"
        assert job.task == "regression"
        assert job.params == {"iterations": 100}


# ---------------------------------------------------------------------------
# codegen _node_to_code for MODELLING
# ---------------------------------------------------------------------------


class TestModellingNodeToCode:
    def test_modelling_node_generates_code(self):
        node = GraphNode(
            id="train_freq",
            data=NodeData(
                label="train_freq",
                nodeType=NodeType.MODELLING,
                config={
                    "target": "ClaimCount",
                    "algorithm": "catboost",
                    "task": "regression",
                },
            ),
        )
        code = _node_to_code(node, source_names=["frequency_set"])
        assert 'config="config/model_training/train_freq.json"' in code
        assert "def train_freq" in code

    def test_modelling_code_is_parseable(self):
        node = GraphNode(
            id="train",
            data=NodeData(
                label="train",
                nodeType=NodeType.MODELLING,
                config={"target": "y", "algorithm": "catboost"},
            ),
        )
        code = _node_to_code(node, source_names=["data"])
        # Wrap in a module to parse
        full = "import polars as pl\nimport haute\npipeline = haute.Pipeline('test')\n\n" + code
        ast.parse(full)

    def test_parser_roundtrip(self, tmp_path):
        """code → parse → codegen → parse should produce matching modelling node."""
        from haute._config_io import collect_node_configs

        source_node = GraphNode(
            id="data",
            data=NodeData(label="data", nodeType=NodeType.DATA_SOURCE, config={"path": "data.parquet"}),
        )
        modelling_node = GraphNode(
            id="train",
            data=NodeData(
                label="train",
                nodeType=NodeType.MODELLING,
                config={"target": "y", "algorithm": "catboost", "task": "regression"},
            ),
        )
        graph = make_graph({
            "nodes": [source_node.model_dump(), modelling_node.model_dump()],
            "edges": [make_edge("data", "train").model_dump()],
            "pipeline_name": "test",
        })
        code = graph_to_code(graph, pipeline_name="test")

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        # Parse the generated code back
        parsed_graph = parse_pipeline_source(code, "test.py", _base_dir=tmp_path)
        # Find the modelling node
        mod_nodes = [n for n in parsed_graph.nodes if n.data.nodeType == NodeType.MODELLING]
        assert len(mod_nodes) == 1
        assert mod_nodes[0].data.config.get("target") == "y"
        assert mod_nodes[0].data.config.get("algorithm") == "catboost"
