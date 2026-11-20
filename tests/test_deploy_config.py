"""Tests for DeployConfig - new [safety], [ci] sections, env overrides, endpoint suffix."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from haute._project_settings import _schema, read_project_settings
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.deploy._config import (
    AwsEcsConfig,
    AzureContainerAppsConfig,
    CIConfig,
    ContainerConfig,
    DatabricksConfig,
    DeployConfig,
    GcpRunConfig,
    ResolvedDeploy,
    SafetyConfig,
    _apply_env_overrides,
)
from haute.deploy._project_modules import ProjectModules
from haute.deploy._request_rows import RequestRows
from haute.deploy._validators import _TestQuoteCase, validate_deploy
from haute.errors import ConfigError
from tests._deploy_helpers import artifact_digests, resolved_paths
from tests._project import use_project

_MINIMAL_LIVE_QUOTE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "haute"
    / "assistant"
    / "assets"
    / "examples"
    / "minimal_live_quote"
)


@pytest.fixture()
def toml_file(tmp_path: Path) -> Path:
    """Write a haute.toml with all sections and return the path."""
    content = """\
[project]
name = "motor-pricing"
pipeline = "main.py"

[deploy]
target = "databricks"
model_name = "motor-pricing"
endpoint_name = "motor-pricing"

[deploy.databricks]
experiment_name = "/Shared/haute/motor-pricing"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"
serving_scale_to_zero = true

[test_quotes]
dir = "tests/quotes"

[safety]
impact_dataset = "data/portfolio.parquet"

[safety.approval]
min_approvers = 3

[ci]
provider = "github"

[ci.staging]
endpoint_suffix = "-stg"

"""
    p = tmp_path / "haute.toml"
    p.write_text(content)
    (tmp_path / "main.py").write_text(
        "import haute\npipeline = haute.Pipeline('main')\n",
        encoding="utf-8",
    )
    return p


class TestFromToml:
    def test_accepts_server_host_used_by_cli(self, tmp_path: Path) -> None:
        from haute.cli._serve import _settings_server_host

        path = tmp_path / "haute.toml"
        path.write_text(
            """\
[project]
name = "simple"
pipeline = "main.py"

[server]
host = "localhost"
""",
            encoding="utf-8",
        )

        config = DeployConfig.from_project(path.parent)

        assert config.model_name == "simple"
        settings = read_project_settings(tmp_path)
        assert settings is not None
        assert _settings_server_host(settings) == "localhost"

    def test_loads_safety_section(self, toml_file: Path) -> None:
        config = DeployConfig.from_project(toml_file.parent)
        assert config.safety.impact_dataset == "data/portfolio.parquet"
        assert config.safety.min_approvers == 3

    def test_loads_ci_section(self, toml_file: Path) -> None:
        config = DeployConfig.from_project(toml_file.parent)
        assert config.ci.provider == "github"
        assert config.ci.staging_endpoint_suffix == "-stg"

    def test_loads_target(self, toml_file: Path) -> None:
        config = DeployConfig.from_project(toml_file.parent)
        assert config.target == "databricks"

    def test_defaults_when_sections_missing(self, tmp_path: Path) -> None:
        content = """\
[project]
name = "simple"
pipeline = "main.py"

[deploy]
model_name = "simple"
"""
        p = tmp_path / "haute.toml"
        p.write_text(content)
        (tmp_path / "main.py").write_text(
            "import haute\npipeline = haute.Pipeline('main')\n",
            encoding="utf-8",
        )
        config = DeployConfig.from_project(p.parent)
        assert config.safety.min_approvers == 2
        assert config.ci.provider == "github"

    def test_pipeline_path_is_retained_until_deploy_binding(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text(
            '[project]\nname = "x"\npipeline = "configured.py"\n',
            encoding="utf-8",
        )
        configured = tmp_path / "configured.py"
        configured.write_text(
            "import haute\npipeline = haute.Pipeline('configured')\n",
            encoding="utf-8",
        )
        (tmp_path / "main.py").write_text(
            "import haute\npipeline = haute.Pipeline('decoy')\n",
            encoding="utf-8",
        )

        config = DeployConfig.from_project(tmp_path)
        assert config.pipeline_file == Path("configured.py")

    def test_resolution_leaves_the_callers_config_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DEP-R10: the canonical pipeline path lives on the resolution, not the caller's
        # config, which a second resolution (a staging then production deploy) reuses.
        import shutil

        from haute.deploy import resolve_config

        project = tmp_path / "project"
        shutil.copytree(_MINIMAL_LIVE_QUOTE, project)
        use_project(tmp_path)
        config = DeployConfig.from_project(project)
        before = dataclasses.replace(config)

        with resolve_config(config) as resolved:
            assert resolved.pipeline_file == (project / "pipeline.py").resolve()
            assert resolved.project_root == project.resolve()
        assert config == before
        assert config.pipeline_file == Path("pipeline.py")

    @pytest.mark.parametrize("value", ["[]", '["premium", "age"]'])
    def test_output_fields_toml_preserves_explicit_arrays(self, tmp_path: Path, value: str) -> None:
        path = tmp_path / "haute.toml"
        path.write_text(f'[project]\nname = "x"\n[deploy]\noutput_fields = {value}\n')
        config = DeployConfig.from_project(path.parent)
        assert config.output_fields == ([] if value == "[]" else ["premium", "age"])

    def test_reads_the_test_quotes_from_tests_quotes_when_none_is_named(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "haute.toml"
        path.write_text('[project]\nname = "x"\n', encoding="utf-8")
        quotes = tmp_path / "tests" / "quotes"

        # haute.toml names no folder: tests/quotes, once it holds test quotes.
        assert DeployConfig.from_project(path.parent).test_quotes_dir is None
        quotes.mkdir(parents=True)
        assert DeployConfig.from_project(path.parent).test_quotes_dir is None
        (quotes / "golden.json").write_text("[]", encoding="utf-8")
        assert DeployConfig.from_project(path.parent).test_quotes_dir == quotes.resolve()

        path.write_text('[project]\nname = "x"\n[test_quotes]\ndir = "../outside"\n')
        with pytest.raises(ValueError, match="inside the project"):
            DeployConfig.from_project(path.parent)

    def test_output_fields_toml_rejects_non_array(self, tmp_path: Path) -> None:
        path = tmp_path / "haute.toml"
        path.write_text('[project]\nname = "x"\n[deploy]\noutput_fields = "premium"\n')
        with pytest.raises(ValueError, match="output_fields.*array"):
            DeployConfig.from_project(path.parent)

    @pytest.mark.parametrize(
        ("toml", "key"),
        [
            ('[deploy.container]\nport = "x"\n', r"\[deploy.container\] port"),
            ("[deploy.container]\nport = true\n", r"\[deploy.container\] port"),
            (
                '[deploy.databricks]\nserving_scale_to_zero = "yes"\n',
                r"\[deploy.databricks\] serving_scale_to_zero",
            ),
            ("[deploy]\ntarget = 5\n", r"\[deploy\] target"),
            ("[ci.staging]\nendpoint_url = 8080\n", r"\[ci.staging\] endpoint_url"),
            ('[safety.approval]\nmin_approvers = "2"\n', r"\[safety.approval\] min_approvers"),
            ("[test_quotes]\ndir = 5\n", r"\[test_quotes\] dir"),
            ('[test_quotes]\ndir = ""\n', r"\[test_quotes\] dir"),
            ("deploy = 5\n", r"\[deploy\] must be a table"),
            ("[deploy]\ncontainer = 5\n", r"\[deploy.container\] must be a table"),
        ],
    )
    def test_a_value_of_the_wrong_type_is_refused_naming_its_key(
        self, tmp_path: Path, toml: str, key: str
    ) -> None:
        # DEP-R10: a wrong type never loads, and a malformed test-quotes folder never falls
        # back to tests/quotes (and so to no gate at all).
        path = tmp_path / "haute.toml"
        path.write_text(f'{toml}[project]\nname = "x"\n', encoding="utf-8")
        with pytest.raises((ValueError, ConfigError), match=key):
            DeployConfig.from_project(path.parent)


class TestEffectiveEndpointName:
    def test_no_suffix(self) -> None:
        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="motor",
            endpoint_name="motor",
        )
        assert config.effective_endpoint_name == "motor"

    def test_with_suffix(self) -> None:
        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="motor",
            endpoint_name="motor",
            endpoint_suffix="-staging",
        )
        assert config.effective_endpoint_name == "motor-staging"

    def test_none_when_no_name_and_no_suffix(self) -> None:
        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="motor",
            endpoint_name=None,
        )
        assert config.effective_endpoint_name is None

    def test_suffix_falls_back_to_model_name(self) -> None:
        config = DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="motor",
            endpoint_name=None,
            endpoint_suffix="-staging",
        )
        assert config.effective_endpoint_name == "motor-staging"


class TestEnvOverrides:
    def test_haute_model_name_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_MODEL_NAME", "overridden-name")
        config = DeployConfig.from_project(toml_file.parent)
        assert config.model_name == "overridden-name"

    def test_haute_target_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_TARGET", "container")
        config = DeployConfig.from_project(toml_file.parent)
        assert config.target == "container"

    def test_haute_nested_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_SERVING_WORKLOAD_SIZE", "Large")
        config = DeployConfig.from_project(toml_file.parent)
        assert config.databricks.serving_workload_size == "Large"

    def test_haute_bool_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", "false")
        config = DeployConfig.from_project(toml_file.parent)
        assert config.databricks.serving_scale_to_zero is False

    def test_no_env_no_override(self, toml_file: Path) -> None:
        # Ensure HAUTE_ vars are not set
        for key in ("HAUTE_MODEL_NAME", "HAUTE_TARGET"):
            os.environ.pop(key, None)
        config = DeployConfig.from_project(toml_file.parent)
        assert config.model_name == "motor-pricing"
        assert config.target == "databricks"


# ---------------------------------------------------------------------------
# T14: TOML schema ↔ dataclass field sync test
# ---------------------------------------------------------------------------


class TestTomlSchemaSyncWithDataclasses:
    """Guard against the settings parse's schema drifting from config dataclass fields.

    If someone adds a field to a config dataclass but forgets the key in
    ``haute._project_settings``, the parse will refuse the new key. These
    tests detect such drift at CI time.
    """

    @staticmethod
    def _deploy_keys(table: str) -> set[str]:
        tables = _schema()["deploy"].tables
        assert tables is not None
        return set(tables[table].keys)

    def test_databricks_fields_in_schema(self) -> None:
        """Every DatabricksConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(DatabricksConfig)}
        assert dc_fields == self._deploy_keys("databricks")

    def test_container_fields_in_schema(self) -> None:
        dc_fields = {f.name for f in dataclasses.fields(ContainerConfig)}
        assert dc_fields == self._deploy_keys("container")

    def test_azure_container_apps_fields_in_schema(self) -> None:
        dc_fields = {f.name for f in dataclasses.fields(AzureContainerAppsConfig)}
        assert dc_fields == self._deploy_keys("azure-container-apps")

    def test_aws_ecs_fields_in_schema(self) -> None:
        dc_fields = {f.name for f in dataclasses.fields(AwsEcsConfig)}
        assert dc_fields == self._deploy_keys("aws-ecs")

    def test_gcp_run_fields_in_schema(self) -> None:
        dc_fields = {f.name for f in dataclasses.fields(GcpRunConfig)}
        assert dc_fields == self._deploy_keys("gcp-run")

    def test_safety_fields_in_schema(self) -> None:
        """``impact_dataset`` in [safety], ``min_approvers`` in [safety.approval]."""
        safety = _schema()["safety"]
        assert safety.tables is not None
        flat_keys = set(safety.keys) | set(safety.tables["approval"].keys)
        assert {f.name for f in dataclasses.fields(SafetyConfig)} == flat_keys

    def test_ci_fields_in_schema(self) -> None:
        """``provider`` in [ci]; [ci.staging] and [ci.production] keys by their prefix."""
        ci = _schema()["ci"]
        assert ci.tables is not None
        flat_keys = set(ci.keys)
        flat_keys |= {f"staging_{k}" for k in ci.tables["staging"].keys}
        flat_keys |= {f"production_{k}" for k in ci.tables["production"].keys}
        assert {f.name for f in dataclasses.fields(CIConfig)} == flat_keys


# ---------------------------------------------------------------------------
# validate_deploy tests
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: NodeType = NodeType.POLARS,
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(nodeType=node_type, config=config or {}),
    )


def _make_resolved(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    input_node_ids: list[str],
    output_node_id: str,
    artifacts: dict[str, Path] | None = None,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
) -> ResolvedDeploy:
    graph = PipelineGraph(nodes=nodes, edges=edges)
    config = DeployConfig(pipeline_file=Path("main.py"), model_name="test")
    return ResolvedDeploy(
        config=config,
        **resolved_paths(config),
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifacts=artifacts or {},
        input_schema=input_schema or {"col": "Float64"},
        output_schema=output_schema or {"result": "float"},
        project_modules=ProjectModules(utility=None, unbundled_imports=()),
        request_rows=RequestRows(multi_quote="in_request_order"),
        artifact_digests=artifact_digests(artifacts or {}),
    )


class TestValidateDeploy:
    def test_all_checks_pass_returns_empty_quote_results(self) -> None:
        inp = _make_node("input1")
        out = _make_node("output1")
        edge = GraphEdge(id="e1", source="input1", target="output1")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        assert validate_deploy(resolved) == []

    def test_returns_the_single_scoring_pass_results(self, tmp_path: Path) -> None:
        quote_file = tmp_path / "quote.json"
        quote_file.write_text('[{"input": {"col": 1}}]')
        inp = _make_node("input1")
        out = _make_node("output1")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[GraphEdge(id="e1", source="input1", target="output1")],
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        resolved.config.test_quotes_dir = tmp_path
        quote_results: list[dict[str, str | int | float]] = [
            {"file": "quote.json", "rows": 1, "status": "ok", "time_ms": 1.5, "error": ""}
        ]

        with patch(
            "haute.deploy._validators._score_test_quote_cases",
            return_value=quote_results[0],
        ) as scorer:
            result = validate_deploy(resolved)

        assert result == quote_results
        # The cases the pre-check read are the ones scored: the file is read once.
        scorer.assert_called_once_with(
            resolved,
            "quote.json",
            [_TestQuoteCase(input={"col": 1}, expected=None, tolerance_pct=0.0)],
            started=ANY,
        )

    def test_failed_quote_warns_once_and_raises_from_the_same_scoring_pass(
        self,
        tmp_path: Path,
    ) -> None:
        from haute.errors import DeployError

        (tmp_path / "quote.json").write_text('[{"input": {"col": 1}}]')
        inp = _make_node("input1")
        out = _make_node("output1")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[GraphEdge(id="e1", source="input1", target="output1")],
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        resolved.config.test_quotes_dir = tmp_path
        quote_results: list[dict[str, str | int | float]] = [
            {
                "file": "quote.json",
                "rows": 0,
                "status": "error",
                "time_ms": 1.5,
                "error": "schema mismatch",
            }
        ]

        with (
            patch(
                "haute.deploy._validators._score_test_quote_cases",
                return_value=quote_results[0],
            ) as scorer,
            patch("haute.deploy._validators.logger.warning") as warning,
            pytest.raises(DeployError, match="schema mismatch"),
        ):
            validate_deploy(resolved)

        assert scorer.call_count == 1
        warning.assert_called_once_with(
            "validation_failed",
            structural_errors=0,
            test_quote_errors=1,
        )

    def test_input_node_not_in_pruned_graph(self) -> None:
        from haute.errors import DeployError

        out = _make_node("output1")
        resolved = _make_resolved(
            nodes=[out],
            edges=[],
            input_node_ids=["missing_input"],
            output_node_id="output1",
        )
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "missing_input" in e and "not in pruned graph" in e
            for e in exc_info.value.context["structural_errors"]
        )

    def test_input_node_has_incoming_edges(self) -> None:
        from haute.errors import DeployError

        inp = _make_node("input1")
        mid = _make_node("mid1")
        out = _make_node("output1")
        edges = [
            GraphEdge(id="e1", source="mid1", target="input1"),
            GraphEdge(id="e2", source="input1", target="output1"),
        ]
        resolved = _make_resolved(
            nodes=[inp, mid, out],
            edges=edges,
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "input1" in e and "incoming edges" in e
            for e in exc_info.value.context["structural_errors"]
        )

    def test_artifact_file_not_found(self, tmp_path: Path) -> None:
        from haute.errors import DeployError

        inp = _make_node("input1")
        out = _make_node("output1")
        edge = GraphEdge(id="e1", source="input1", target="output1")
        missing_path = tmp_path / "nonexistent.pkl"
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["input1"],
            output_node_id="output1",
            artifacts={"model": missing_path},
        )
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "model" in e and "not found" in e for e in exc_info.value.context["structural_errors"]
        )

    def test_databricks_source_node_detected(self) -> None:
        from haute.errors import DeployError

        inp = _make_node("input1")
        db_src = _make_node(
            "db_src",
            node_type=NodeType.DATA_INPUT,
            config={
                "input_type": "databricks",
                "http_path": "/sql/1.0/warehouses/test",
                "table": "catalog.schema.table",
                "arguments": {},
            },
        )
        out = _make_node("output1")
        edges = [
            GraphEdge(id="e1", source="input1", target="output1"),
            GraphEdge(id="e2", source="db_src", target="output1"),
        ]
        resolved = _make_resolved(
            nodes=[inp, db_src, out],
            edges=edges,
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert any(
            "db_src" in e and "ready, valid matching snapshot" in e
            for e in exc_info.value.context["structural_errors"]
        )

    def test_multiple_validation_errors_returned_together(self, tmp_path: Path) -> None:
        from haute.errors import DeployError

        db_src = _make_node(
            "db_src",
            node_type=NodeType.DATA_INPUT,
            config={
                "input_type": "databricks",
                "http_path": "/sql/1.0/warehouses/test",
                "table": "catalog.schema.table",
                "arguments": {},
            },
        )
        out = _make_node("output1")
        missing_artifact = tmp_path / "missing.pkl"
        resolved = _make_resolved(
            nodes=[db_src, out],
            edges=[],
            input_node_ids=["ghost_input"],
            output_node_id="output1",
            artifacts={"m": missing_artifact},
        )
        with pytest.raises(DeployError) as exc_info:
            validate_deploy(resolved)
        assert len(exc_info.value.context["structural_errors"]) >= 3


# ---------------------------------------------------------------------------
# _apply_env_overrides edge cases
# ---------------------------------------------------------------------------


class TestApplyEnvOverridesEdgeCases:
    def _make_config(self) -> DeployConfig:
        return DeployConfig(pipeline_file=Path("main.py"), model_name="base")

    def test_scale_to_zero_false_capital_f(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", "False")
        config = _apply_env_overrides(self._make_config())
        assert config.databricks.serving_scale_to_zero is False

    def test_scale_to_zero_true_all_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", "TRUE")
        config = _apply_env_overrides(self._make_config())
        assert config.databricks.serving_scale_to_zero is True

    def test_scale_to_zero_numeric_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", "0")
        config = _apply_env_overrides(self._make_config())
        assert config.databricks.serving_scale_to_zero is False

    @pytest.mark.parametrize(("raw", "expected"), [("yes", True), ("No", False), ("1", True)])
    def test_scale_to_zero_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", raw)
        config = _apply_env_overrides(self._make_config())
        assert config.databricks.serving_scale_to_zero is expected

    @pytest.mark.parametrize("raw", ["ture", "", "on"])
    def test_scale_to_zero_typo_is_refused(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        # DEP-R10: a typo never reads as False and silently turns scale-to-zero off.
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", raw)
        with pytest.raises(ValueError, match="HAUTE_SERVING_SCALE_TO_ZERO"):
            _apply_env_overrides(self._make_config())

    def test_empty_string_env_var_still_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_MODEL_NAME", "")
        config = _apply_env_overrides(self._make_config())
        assert config.model_name == ""

    def test_model_name_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_MODEL_NAME", "env-model")
        config = _apply_env_overrides(self._make_config())
        assert config.model_name == "env-model"

    def test_target_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_TARGET", "container")
        config = _apply_env_overrides(self._make_config())
        assert config.target == "container"

    def test_endpoint_name_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_ENDPOINT_NAME", "my-ep")
        config = _apply_env_overrides(self._make_config())
        assert config.endpoint_name == "my-ep"


# ---------------------------------------------------------------------------
# DeployConfig.override() edge cases
# ---------------------------------------------------------------------------


class TestDeployConfigOverride:
    def _make_config(self) -> DeployConfig:
        return DeployConfig(
            pipeline_file=Path("main.py"),
            model_name="original",
            target="databricks",
        )

    def test_override_with_none_is_skipped(self) -> None:
        config = self._make_config()
        overridden = config.override(model_name=None)
        assert overridden.model_name == "original"

    def test_override_of_an_unknown_key_raises(self) -> None:
        # DEP-R10: a misspelt override is never dropped without a word.
        config = self._make_config()
        with pytest.raises(TypeError, match="endpoint_sufix"):
            config.override(endpoint_sufix="-staging")

    def test_multiple_overrides_in_single_call(self) -> None:
        config = self._make_config()
        overridden = config.override(model_name="new-name", target="container")
        assert overridden.model_name == "new-name"
        assert overridden.target == "container"
        assert config.model_name == "original"
        assert config.target == "databricks"
