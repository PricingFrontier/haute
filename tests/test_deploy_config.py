"""Tests for DeployConfig - new [safety], [ci] sections, env overrides, endpoint suffix."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from haute.deploy._config import (
    AwsEcsConfig,
    AzureContainerAppsConfig,
    CIConfig,
    ContainerConfig,
    DatabricksConfig,
    DeployConfig,
    GcpRunConfig,
    SafetyConfig,
    _VALID_TOML_SCHEMA,
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
    return p


class TestFromToml:
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
        config = DeployConfig.from_toml(p)
        assert config.safety.min_approvers == 2
        assert config.ci.provider == "github"


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
