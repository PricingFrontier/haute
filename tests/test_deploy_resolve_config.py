"""Deploy configuration: ``_load_env`` and ``resolve_config`` edge cases.

The deploy environment file is loaded, and ``resolve_config`` applies environment overrides and
refuses incomplete or inconsistent deploy settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute.deploy._request_rows import RequestRows
from haute.errors import DeployError
from tests._deploy_internals_helpers import (
    PIPELINE_FILE,
)
from tests._project import use_project

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ===========================================================================
# 4. _config.py — _load_env, _apply_env_overrides, resolve_config edge cases
# ===========================================================================


class TestLoadEnv:
    """Tests for _load_env()."""

    def test_load_env_no_file(self, tmp_path):
        """No .env file should be a no-op."""
        from haute.deploy._config import _load_env

        # Should not raise
        _load_env(tmp_path)

    def test_load_env_with_dotenv(self, tmp_path, monkeypatch):
        """The required python-dotenv parser loads values without overwriting env."""
        from haute.deploy._config import _load_env

        env_file = tmp_path / ".env"
        env_file.write_text('HAUTE_TEST_ENV="from file"\nHAUTE_TEST_KEEP=overwritten\n')
        monkeypatch.delenv("HAUTE_TEST_ENV", raising=False)
        monkeypatch.setenv("HAUTE_TEST_KEEP", "original")

        _load_env(tmp_path)

        assert os.environ["HAUTE_TEST_ENV"] == "from file"
        assert os.environ["HAUTE_TEST_KEEP"] == "original"


_STUB_EXECUTION_POLICY = {
    "schema_version": 1,
    "profile": "deploy_batch",
    "status": "projected",
    "strategy": "projected",
    "reason_code": "projection_available",
    "blocking_node_id": None,
    "blocking_operator": None,
    "remediation": "No change is needed.",
}


class TestResolveConfigEdgeCases:
    """Tests for resolve_config() edge cases."""

    @staticmethod
    def _minimal_graph() -> MagicMock:
        from haute._types import NodeType

        source = MagicMock()
        source.id = "quote_input"
        source.data.label = "quote_input"
        source.data.nodeType = NodeType.API_INPUT
        source.data.config = {}
        output = MagicMock()
        output.id = "quote_response"
        output.data.label = "quote_response"
        output.data.nodeType = NodeType.OUTPUT
        output.data.config = {}
        graph = MagicMock()
        graph.nodes = [source, output]
        graph.edges = []
        graph.preamble = ""
        return graph

    def test_resolve_config_no_source_nodes_raises(self):
        """Graph with no source nodes at all should raise ValueError."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        mock_graph = MagicMock()
        mock_graph.nodes = [MagicMock()]

        # Top-level imports in _config.py are bound to the module, so we patch them there.
        # Local imports in resolve_config() are patched at the source module.
        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=[]),
        ):
            with pytest.raises(ValueError, match="No source nodes"):
                resolve_config(config)

    def test_resolve_config_multiple_unnamed_sources_raises(self):
        """Multiple source nodes with no apiInput nodes should raise ValueError."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        mock_graph = MagicMock()
        mock_graph.nodes = [MagicMock()]

        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=["src1", "src2"]),
        ):
            with pytest.raises(ValueError, match="Multiple source nodes"):
                resolve_config(config)

    def test_resolve_config_single_source_fallback(self):
        """Single source node is used as deploy input when no apiInput exists."""
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
        )

        from haute._types import NodeType

        mock_graph = MagicMock()
        source = MagicMock()
        source.id = "single_src"
        source.data.nodeType = NodeType.DATA_INPUT
        mock_graph.nodes = [source]
        mock_graph.preamble = ""

        with (
            patch("haute.parser.parse_pipeline_file", return_value=mock_graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(mock_graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=["single_src"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"col": "Int64"}),
            patch(
                "haute.deploy._schema.infer_deploy_execution_policy",
                return_value=_STUB_EXECUTION_POLICY,
            ),
            patch("haute.deploy._schema.infer_output_schema", return_value={"out": "Float64"}),
            patch(
                "haute.deploy._request_rows.request_rows",
                return_value=RequestRows("in_request_order"),
            ),
        ):
            resolved = resolve_config(config)

        assert resolved.input_node_ids == ["single_src"]

    @staticmethod
    def _conservative_deploy_graph(tmp_path):
        """A real apiInput graph whose group-by estimate cannot be proven."""
        from tests.test_deploy_batch_scoring import (
            _conservative_graph,
            _write_policy_sample,
        )

        return _write_policy_sample(tmp_path, _conservative_graph())

    @staticmethod
    def _tmp_pipeline_file(tmp_path):
        """A pipeline path inside the temp project root (parsing is patched)."""
        pipeline = tmp_path / "pipeline.py"
        pipeline.write_text("# conservative deploy fixture", encoding="utf-8")
        return pipeline

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_resolve_config_bundles_an_unprovable_group_by_for_a_container_target(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A cache-miss resolve must reach policy inference, not die in the dry-run.

        The output-schema dry-run runs uncapped, so the conservative graph's
        group-by is rejected there; the capped-worker fallback keeps resolution
        alive and the recorded policy is the capped worker's warning.
        """
        from haute.deploy._config import ContainerConfig, DeployConfig, resolve_config

        # The capped dry-run really spawns; the child derives its project root
        # from the working directory it inherits.
        use_project(tmp_path)
        graph = self._conservative_deploy_graph(tmp_path)
        config = DeployConfig(
            pipeline_file=self._tmp_pipeline_file(tmp_path),
            project_dir=tmp_path,
            model_name="test-model",
            target="container",
            container=ContainerConfig(base_image="python:3.11.9-slim"),
        )

        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.deploy._config.find_output_node", return_value="quote_response"),
            patch(
                "haute.deploy._config.prune_for_deploy",
                return_value=(graph, ["quote_input", "shape", "agg", "quote_response"], []),
            ),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=["quote_input"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"segment": "String"}),
        ):
            resolved = resolve_config(config)

        assert resolved.execution_policy["status"] == "warned"
        assert resolved.execution_policy["strategy"] == "full-width-conservative"
        assert resolved.execution_policy["runtime"] == "hard_capped_worker"
        assert resolved.output_schema
        assert "total" in resolved.output_schema

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_resolve_config_refuses_an_unprovable_group_by_for_databricks(
        self,
        tmp_path,
        monkeypatch,
    ):
        """The pyfunc target scores batches in process, so it cannot be capped."""
        from haute.deploy._config import DeployConfig, resolve_config
        from haute.errors import DeployError

        graph = self._conservative_deploy_graph(tmp_path)
        config = DeployConfig(
            pipeline_file=self._tmp_pipeline_file(tmp_path),
            project_dir=tmp_path,
            model_name="test-model",
            target="databricks",
        )

        def forbidden_runner(*_args, **_kwargs):
            raise AssertionError("policy inference must refuse before any spawn")

        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.deploy._config.find_output_node", return_value="quote_response"),
            patch(
                "haute.deploy._config.prune_for_deploy",
                return_value=(graph, ["quote_input", "shape", "agg", "quote_response"], []),
            ),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=["quote_input"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"segment": "String"}),
            patch("haute.deploy._schema.run_isolated_worker", forbidden_runner),
        ):
            with pytest.raises(DeployError, match="serving process"):
                resolve_config(config)

    def test_resolve_config_rejects_a_sole_non_data_input_source(self):
        """A constant must never be promoted to the live deploy input."""
        from haute._types import NodeType
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(pipeline_file=PIPELINE_FILE, model_name="test-model")
        source = MagicMock()
        source.id = "constant"
        source.data.nodeType = NodeType.CONSTANT
        source.data.config = {}
        graph = MagicMock()
        graph.nodes = [source]

        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.deploy._config.find_output_node", return_value="out"),
            patch("haute.deploy._config.prune_for_deploy", return_value=(graph, ["out"], [])),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=[]),
            patch("haute.deploy._config.find_source_nodes", return_value=["constant"]),
        ):
            with pytest.raises(ValueError, match="not a dataInput"):
                resolve_config(config)

    @pytest.mark.parametrize("pipeline_kind", ["relative", "absolute"])
    def test_resolve_config_canonicalises_configured_pipeline(self, tmp_path, pipeline_kind):
        from haute.deploy._config import DeployConfig, resolve_config

        project = tmp_path / "project"
        pipeline = project / "rating" / "main.py"
        pipeline.parent.mkdir(parents=True)
        pipeline.write_text("# pipeline\n")
        configured = Path("rating/main.py") if pipeline_kind == "relative" else pipeline
        config = DeployConfig(
            pipeline_file=configured,
            project_dir=project,
            model_name="test-model",
        )
        graph = self._minimal_graph()

        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph) as parse,
            patch("haute.deploy._config.find_output_node", return_value="quote_response"),
            patch(
                "haute.deploy._config.prune_for_deploy",
                return_value=(graph, ["quote_input", "quote_response"], []),
            ),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=["quote_input"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"col": "Int64"}),
            patch(
                "haute.deploy._schema.infer_deploy_execution_policy",
                return_value=_STUB_EXECUTION_POLICY,
            ),
            patch("haute.deploy._schema.infer_output_schema", return_value={"out": "Float64"}),
        ):
            resolved = resolve_config(config)

        assert resolved.pipeline_file == pipeline.resolve()
        assert config.pipeline_file == configured
        # Parsed as it runs, every submodel occurrence expanded (BUG-18).
        parse.assert_called_once_with(pipeline.resolve(), flatten=True)
        resolved.close()

    def test_resolve_config_rejects_configured_pipeline_escape(self, tmp_path):
        from haute.deploy._config import DeployConfig, resolve_config

        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("# outside\n")
        config = DeployConfig(
            pipeline_file=Path("../outside.py"),
            project_dir=project,
            model_name="test-model",
        )

        with (
            patch("haute.parser.parse_pipeline_file") as parse,
            pytest.raises(DeployError, match="outside.*project root"),
        ):
            resolve_config(config)
        parse.assert_not_called()

    def test_resolve_config_rejects_symlinked_pipeline_escape(self, tmp_path):
        from haute.deploy._config import DeployConfig, resolve_config

        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("# outside\n")
        link = project / "main.py"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation is unavailable on this platform")
        config = DeployConfig(
            pipeline_file=link,
            project_dir=project,
            model_name="test-model",
        )

        with pytest.raises(DeployError, match="outside.*project root"):
            resolve_config(config)

    @pytest.mark.parametrize(
        "output_fields",
        [
            [],
            ["premium", "premium"],
            ["missing"],
            ["premium", 1],
            ["premium", ["unhashable"]],
        ],
        ids=["empty", "duplicate", "missing", "non-string", "unhashable"],
    )
    def test_resolve_config_rejects_invalid_output_fields(self, output_fields):
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
            output_fields=output_fields,
        )
        graph = self._minimal_graph()
        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.deploy._config.find_output_node", return_value="quote_response"),
            patch(
                "haute.deploy._config.prune_for_deploy",
                return_value=(graph, ["quote_input", "quote_response"], []),
            ),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=["quote_input"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"col": "Int64"}),
            patch(
                "haute.deploy._schema.infer_deploy_execution_policy",
                return_value=_STUB_EXECUTION_POLICY,
            ),
            patch(
                "haute.deploy._schema.infer_output_schema",
                return_value={"premium": "Float64", "age": "Int64"},
            ),
            pytest.raises(DeployError, match="output_fields"),
        ):
            resolve_config(config)

    def test_resolve_config_projects_output_schema_in_configured_order(self):
        from haute.deploy._config import DeployConfig, resolve_config

        config = DeployConfig(
            pipeline_file=PIPELINE_FILE,
            model_name="test-model",
            output_fields=["age", "premium"],
        )
        graph = self._minimal_graph()
        with (
            patch("haute.parser.parse_pipeline_file", return_value=graph),
            patch("haute.deploy._config.find_output_node", return_value="quote_response"),
            patch(
                "haute.deploy._config.prune_for_deploy",
                return_value=(graph, ["quote_input", "quote_response"], []),
            ),
            patch("haute.deploy._config.find_deploy_input_nodes", return_value=["quote_input"]),
            patch("haute.deploy._bundler.collect_artifacts", return_value={}),
            patch("haute.deploy._schema.infer_input_schema", return_value={"col": "Int64"}),
            patch(
                "haute.deploy._schema.infer_deploy_execution_policy",
                return_value=_STUB_EXECUTION_POLICY,
            ),
            patch(
                "haute.deploy._schema.infer_output_schema",
                return_value={"premium": "Float64", "age": "Int64"},
            ),
        ):
            resolved = resolve_config(config)

        assert list(resolved.output_schema) == ["age", "premium"]
        resolved.close()
