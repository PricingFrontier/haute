"""Tests for DeployConfig - new [safety], [ci] sections, env overrides, endpoint suffix."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.deploy._config import (
    _VALID_TOML_SCHEMA,
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
    _validate_toml_keys,
)
from haute.deploy._validators import validate_deploy


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
        from haute.cli._serve import _load_toml_server_host

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

        config = DeployConfig.from_toml(path)

        assert config.model_name == "simple"
        assert _load_toml_server_host(tmp_path) == "localhost"

    def test_loads_safety_section(self, toml_file: Path) -> None:
        config = DeployConfig.from_toml(toml_file)
        assert config.safety.impact_dataset == "data/portfolio.parquet"
        assert config.safety.min_approvers == 3

    def test_loads_ci_section(self, toml_file: Path) -> None:
        config = DeployConfig.from_toml(toml_file)
        assert config.ci.provider == "github"
        assert config.ci.staging_endpoint_suffix == "-stg"

    def test_loads_target(self, toml_file: Path) -> None:
        config = DeployConfig.from_toml(toml_file)
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
        config = DeployConfig.from_toml(p)
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

        config = DeployConfig.from_toml(tmp_path / "haute.toml")
        assert config.pipeline_file == Path("configured.py")

    @pytest.mark.parametrize("value", ["[]", '["premium", "age"]'])
    def test_output_fields_toml_preserves_explicit_arrays(self, tmp_path: Path, value: str) -> None:
        path = tmp_path / "haute.toml"
        path.write_text(f'[project]\nname = "x"\n[deploy]\noutput_fields = {value}\n')
        config = DeployConfig.from_toml(path)
        assert config.output_fields == ([] if value == "[]" else ["premium", "age"])

    def test_output_fields_toml_rejects_non_array(self, tmp_path: Path) -> None:
        path = tmp_path / "haute.toml"
        path.write_text('[project]\nname = "x"\n[deploy]\noutput_fields = "premium"\n')
        with pytest.raises(ValueError, match="output_fields.*array"):
            DeployConfig.from_toml(path)


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
        config = DeployConfig.from_toml(toml_file)
        assert config.model_name == "overridden-name"

    def test_haute_target_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_TARGET", "container")
        config = DeployConfig.from_toml(toml_file)
        assert config.target == "container"

    def test_haute_nested_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_SERVING_WORKLOAD_SIZE", "Large")
        config = DeployConfig.from_toml(toml_file)
        assert config.databricks.serving_workload_size == "Large"

    def test_haute_bool_override(
        self,
        toml_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HAUTE_SERVING_SCALE_TO_ZERO", "false")
        config = DeployConfig.from_toml(toml_file)
        assert config.databricks.serving_scale_to_zero is False

    def test_no_env_no_override(self, toml_file: Path) -> None:
        # Ensure HAUTE_ vars are not set
        for key in ("HAUTE_MODEL_NAME", "HAUTE_TARGET"):
            os.environ.pop(key, None)
        config = DeployConfig.from_toml(toml_file)
        assert config.model_name == "motor-pricing"
        assert config.target == "databricks"


# ---------------------------------------------------------------------------
# T14: TOML schema ↔ dataclass field sync test
# ---------------------------------------------------------------------------


class TestTomlSchemaSyncWithDataclasses:
    """Guard against _VALID_TOML_SCHEMA drifting from config dataclass fields.

    If someone adds a field to a config dataclass but forgets to add it to
    _VALID_TOML_SCHEMA, the TOML validator will reject the new key.  These
    tests detect such drift at CI time.
    """

    def test_databricks_fields_in_schema(self) -> None:
        """Every DatabricksConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(DatabricksConfig)}
        schema_keys = _VALID_TOML_SCHEMA["deploy"]["databricks"]
        assert dc_fields == schema_keys, (
            f"DatabricksConfig fields {dc_fields - schema_keys} missing from "
            f"TOML schema, or schema has extra keys {schema_keys - dc_fields}"
        )

    def test_container_fields_in_schema(self) -> None:
        """Every ContainerConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(ContainerConfig)}
        schema_keys = _VALID_TOML_SCHEMA["deploy"]["container"]
        assert dc_fields == schema_keys

    def test_azure_container_apps_fields_in_schema(self) -> None:
        """Every AzureContainerAppsConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(AzureContainerAppsConfig)}
        schema_keys = _VALID_TOML_SCHEMA["deploy"]["azure-container-apps"]
        assert dc_fields == schema_keys

    def test_aws_ecs_fields_in_schema(self) -> None:
        """Every AwsEcsConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(AwsEcsConfig)}
        schema_keys = _VALID_TOML_SCHEMA["deploy"]["aws-ecs"]
        assert dc_fields == schema_keys

    def test_gcp_run_fields_in_schema(self) -> None:
        """Every GcpRunConfig field must appear in the TOML schema."""
        dc_fields = {f.name for f in dataclasses.fields(GcpRunConfig)}
        schema_keys = _VALID_TOML_SCHEMA["deploy"]["gcp-run"]
        assert dc_fields == schema_keys

    def test_safety_fields_in_schema(self) -> None:
        """SafetyConfig fields must appear across the safety TOML schema.

        SafetyConfig has ``impact_dataset`` (in [safety]._self) and
        ``min_approvers`` (in [safety.approval]).
        """
        dc_fields = {f.name for f in dataclasses.fields(SafetyConfig)}
        # Flatten the safety schema: _self keys + approval sub-keys
        safety_schema = _VALID_TOML_SCHEMA["safety"]
        flat_keys = set(safety_schema.get("_self", set()))
        flat_keys |= set(safety_schema.get("approval", set()))
        assert dc_fields == flat_keys, (
            f"SafetyConfig fields {dc_fields - flat_keys} missing from TOML schema, "
            f"or schema has extra keys {flat_keys - dc_fields}"
        )

    def test_ci_fields_in_schema(self) -> None:
        """CIConfig fields must appear across the ci TOML schema.

        CIConfig has ``provider`` ([ci]._self), ``staging_endpoint_suffix``
        and ``staging_endpoint_url`` ([ci.staging]), and
        ``production_endpoint_url`` ([ci.production]).
        """
        dc_fields = {f.name for f in dataclasses.fields(CIConfig)}
        ci_schema = _VALID_TOML_SCHEMA["ci"]
        # CIConfig field names use underscored prefixes (staging_endpoint_suffix)
        # but TOML uses nested sections ([ci.staging] endpoint_suffix).
        # Build the expected mapping:
        flat_keys: set[str] = set()
        for k in ci_schema.get("_self", set()):
            flat_keys.add(k)
        for k in ci_schema.get("staging", set()):
            flat_keys.add(f"staging_{k}")
        for k in ci_schema.get("production", set()):
            flat_keys.add(f"production_{k}")
        assert dc_fields == flat_keys, (
            f"CIConfig fields {dc_fields - flat_keys} missing from TOML schema, "
            f"or schema has extra keys {flat_keys - dc_fields}"
        )


# ---------------------------------------------------------------------------
# _validate_toml_keys tests
# ---------------------------------------------------------------------------


class TestValidateTomlKeys:
    def test_valid_data_no_error(self, tmp_path: Path) -> None:
        data = {
            "project": {"name": "foo", "pipeline": "main.py"},
            "deploy": {
                "target": "databricks",
                "model_name": "foo",
                "databricks": {"catalog": "main"},
            },
        }
        _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_unknown_top_level_section(self, tmp_path: Path) -> None:
        data = {"project": {"name": "foo"}, "bogus": {"x": 1}}
        with pytest.raises(ValueError, match=r"unknown top-level section \[bogus\]"):
            _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_unknown_key_in_project(self, tmp_path: Path) -> None:
        data = {"project": {"name": "foo", "unknown_key": "bar"}}
        with pytest.raises(ValueError, match=r"\[project\] unknown key 'unknown_key'"):
            _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_unknown_key_in_deploy_databricks(self, tmp_path: Path) -> None:
        data = {"deploy": {"databricks": {"catalog": "main", "typo_key": "x"}}}
        with pytest.raises(ValueError, match=r"\[deploy\.databricks\] unknown key 'typo_key'"):
            _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_unknown_subsection_in_deploy(self, tmp_path: Path) -> None:
        data = {"deploy": {"unknown": {"x": 1}}}
        with pytest.raises(ValueError, match=r"\[deploy\] unknown key 'unknown'"):
            _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_typo_in_section_name(self, tmp_path: Path) -> None:
        data = {"deploy": {"databrick": {"catalog": "main"}}}
        with pytest.raises(ValueError, match=r"\[deploy\] unknown key 'databrick'"):
            _validate_toml_keys(data, tmp_path / "haute.toml")

    def test_empty_data_no_error(self, tmp_path: Path) -> None:
        _validate_toml_keys({}, tmp_path / "haute.toml")

    def test_non_dict_value_for_expected_section(self, tmp_path: Path) -> None:
        data = {"project": "not_a_dict"}
        # Non-dict value for a section that expects a dict should be silently skipped
        # (the isinstance check in the loop guards this)
        _validate_toml_keys(data, tmp_path / "haute.toml")


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
    return ResolvedDeploy(
        config=DeployConfig(pipeline_file=Path("main.py"), model_name="test"),
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifacts=artifacts or {},
        input_schema=input_schema or {"col": "float"},
        output_schema=output_schema or {"result": "float"},
    )


class TestValidateDeploy:
    def test_all_checks_pass_returns_empty(self) -> None:
        inp = _make_node("input1")
        out = _make_node("output1")
        edge = GraphEdge(id="e1", source="input1", target="output1")
        resolved = _make_resolved(
            nodes=[inp, out],
            edges=[edge],
            input_node_ids=["input1"],
            output_node_id="output1",
        )
        assert validate_deploy(resolved) is None

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
                "inputType": "databricks",
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
                "inputType": "databricks",
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

    def test_override_nonexistent_attribute_is_skipped(self) -> None:
        config = self._make_config()
        overridden = config.override(nonexistent_field="value")
        assert not hasattr(overridden, "nonexistent_field")

    def test_multiple_overrides_in_single_call(self) -> None:
        config = self._make_config()
        overridden = config.override(model_name="new-name", target="container")
        assert overridden.model_name == "new-name"
        assert overridden.target == "container"
        assert config.model_name == "original"
        assert config.target == "databricks"
