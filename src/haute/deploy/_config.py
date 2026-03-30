"""Deploy configuration - user input, TOML loading, and resolution."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from haute._io import read_user_text
from haute._logging import get_logger
from haute.deploy._pruner import (
    find_deploy_input_nodes,
    find_output_node,
    find_source_nodes,
    prune_for_deploy,
)
from haute.graph_utils import PipelineGraph

logger = get_logger(component="deploy.config")


@dataclass
class DatabricksConfig:
    """Typed Databricks-specific settings from [deploy.databricks] in haute.toml."""

    experiment_name: str = "/Shared/haute/default"
    catalog: str = "main"
    schema: str = "pricing"
    serving_workload_size: str = "Small"
    serving_scale_to_zero: bool = True


@dataclass
class ContainerConfig:
    """Container-specific settings from [deploy.container] in haute.toml."""

    registry: str = ""
    port: int = 8080
    base_image: str = "python:3.11-slim"


@dataclass
class AzureContainerAppsConfig:
    """Azure Container Apps settings from [deploy.azure-container-apps]."""

    resource_group: str = ""
    container_app_name: str = ""
    environment_name: str = ""


@dataclass
class AwsEcsConfig:
    """AWS ECS settings from [deploy.aws-ecs]."""

    region: str = "eu-west-1"
    cluster: str = ""
    service: str = ""


@dataclass
class GcpRunConfig:
    """GCP Cloud Run settings from [deploy.gcp-run]."""

    project: str = ""
    region: str = "europe-west1"
    service: str = ""


@dataclass
class SafetyConfig:
    """Safety settings from [safety] in haute.toml."""

    impact_dataset: str = ""
    min_approvers: int = 2


@dataclass
class CIConfig:
    """CI/CD settings from [ci] in haute.toml."""

    provider: str = "github"
    staging_endpoint_suffix: str = "-staging"
    staging_endpoint_url: str = ""
    production_endpoint_url: str = ""


# ---------------------------------------------------------------------------
# TOML schema validation — reject unknown keys early
# ---------------------------------------------------------------------------

_VALID_TOML_SCHEMA: dict[str, set[str] | dict[str, set[str]]] = {
    "project": {"name", "pipeline"},
    "deploy": {
        "_self": {"target", "model_name", "endpoint_name", "output_fields"},
        "databricks": {
            "experiment_name",
            "catalog",
            "schema",
            "serving_workload_size",
            "serving_scale_to_zero",
        },
        "container": {"registry", "port", "base_image"},
        "azure-container-apps": {
            "resource_group",
            "container_app_name",
            "environment_name",
        },
        "aws-ecs": {"region", "cluster", "service"},
        "gcp-run": {"project", "region", "service"},
    },
    "test_quotes": {"dir"},
    "safety": {
        "_self": {"impact_dataset"},
        "approval": {"min_approvers"},
    },
    "ci": {
        "_self": {"provider"},
        "staging": {"endpoint_suffix", "endpoint_url"},
        "production": {"endpoint_url"},
    },
}


def _validate_toml_keys(data: dict[str, Any], path: Path) -> None:
    """Raise ValueError if haute.toml contains unknown keys."""
    errors: list[str] = []

    def _check(
        section_path: str,
        actual: dict[str, Any],
        schema: set[str] | dict[str, Any],
    ) -> None:
        if isinstance(schema, set):
            unknown = set(actual) - schema
            for k in sorted(unknown):
                errors.append(f"  [{section_path}] unknown key '{k}'")
        else:
            # Dict schema: _self holds top-level keys, rest are sub-sections
            top_keys = schema.get("_self", set())
            sub_sections = {k for k in schema if k != "_self"}
            allowed = top_keys | sub_sections if isinstance(top_keys, set) else sub_sections
            unknown = set(actual) - allowed
            for k in sorted(unknown):
                errors.append(f"  [{section_path}] unknown key '{k}'")
            for sub in sub_sections:
                if sub in actual and isinstance(actual[sub], dict):
                    _check(f"{section_path}.{sub}", actual[sub], schema[sub])

    top_unknown = set(data) - set(_VALID_TOML_SCHEMA)
    for k in sorted(top_unknown):
        errors.append(f"  unknown top-level section [{k}]")

    for section, schema in _VALID_TOML_SCHEMA.items():
        if section in data and isinstance(data[section], dict):
            _check(section, data[section], schema)

    if errors:
        raise ValueError(
            f"Invalid haute.toml ({path}):\n"
            + "\n".join(errors)
            + "\n\nCheck for typos in your configuration keys."
        )


@dataclass
class DeployConfig:
    """User-provided deployment configuration (from haute.toml + CLI)."""

    pipeline_file: Path
    model_name: str
    project_dir: Path | None = None
    target: str = "databricks"
    endpoint_name: str | None = None
    endpoint_suffix: str | None = None
    output_fields: list[str] | None = None
    test_quotes_dir: Path | None = None
    databricks: DatabricksConfig = field(default_factory=DatabricksConfig)
    container: ContainerConfig = field(default_factory=ContainerConfig)
    azure_container_apps: AzureContainerAppsConfig = field(
        default_factory=AzureContainerAppsConfig,
    )
    aws_ecs: AwsEcsConfig = field(default_factory=AwsEcsConfig)
    gcp_run: GcpRunConfig = field(default_factory=GcpRunConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    ci: CIConfig = field(default_factory=CIConfig)

    @property
    def effective_endpoint_name(self) -> str | None:
        """Endpoint name with optional suffix applied (e.g. for staging).

        Returns None when endpoint_name is unset and no suffix is given,
        which signals that no serving endpoint should be created.
        """
        if self.endpoint_name is None and self.endpoint_suffix is None:
            return None
        base = self.endpoint_name or self.model_name
        if self.endpoint_suffix:
            return base + self.endpoint_suffix
        return base

    @classmethod
    def from_toml(cls, path: Path) -> DeployConfig:
        """Load from haute.toml, merging [project], [deploy], [safety], [ci].

        Validates that all TOML keys are recognised — unknown keys raise
        ``ValueError`` with a clear message listing the offending keys.
        """
        import tomllib

        _load_env(path.resolve().parent)

        text = read_user_text(path)
        data = tomllib.loads(text)

        _validate_toml_keys(data, path)

        project = data.get("project", {})
        deploy = data.get("deploy", {})
        db_raw = deploy.get("databricks", {})
        ct_raw = deploy.get("container", {})
        aca_raw = deploy.get("azure-container-apps", {})
        ecs_raw = deploy.get("aws-ecs", {})
        gcr_raw = deploy.get("gcp-run", {})
        tq = data.get("test_quotes", {})
        safety_raw = data.get("safety", {})
        approval_raw = safety_raw.get("approval", {})
        ci_raw = data.get("ci", {})
        ci_staging = ci_raw.get("staging", {})

        pipeline_file = Path(project.get("pipeline", "main.py"))
        model_name = deploy.get("model_name", project.get("name", pipeline_file.stem))
        endpoint_name = deploy.get("endpoint_name")
        target = deploy.get("target", "databricks")
        output_fields_raw = deploy.get("output_fields")
        output_fields = list(output_fields_raw) if output_fields_raw else None

        tq_dir = (path.parent / tq["dir"]).resolve() if tq.get("dir") else None

        db_config = DatabricksConfig(
            experiment_name=db_raw.get("experiment_name", "/Shared/haute/default"),
            catalog=db_raw.get("catalog", "main"),
            schema=db_raw.get("schema", "pricing"),
            serving_workload_size=db_raw.get("serving_workload_size", "Small"),
            serving_scale_to_zero=db_raw.get("serving_scale_to_zero", True),
        )

        ct_config = ContainerConfig(
            registry=ct_raw.get("registry", ""),
            port=ct_raw.get("port", 8080),
            base_image=ct_raw.get("base_image", "python:3.11-slim"),
        )

        aca_config = AzureContainerAppsConfig(
            resource_group=aca_raw.get("resource_group", ""),
            container_app_name=aca_raw.get("container_app_name", ""),
            environment_name=aca_raw.get("environment_name", ""),
        )

        ecs_config = AwsEcsConfig(
            region=ecs_raw.get("region", "eu-west-1"),
            cluster=ecs_raw.get("cluster", ""),
            service=ecs_raw.get("service", ""),
        )

        gcr_config = GcpRunConfig(
            project=gcr_raw.get("project", ""),
            region=gcr_raw.get("region", "europe-west1"),
            service=gcr_raw.get("service", ""),
        )

        safety_config = SafetyConfig(
            impact_dataset=safety_raw.get("impact_dataset", ""),
            min_approvers=approval_raw.get("min_approvers", 2),
        )

        ci_config = CIConfig(
            provider=ci_raw.get("provider", "github"),
            staging_endpoint_suffix=ci_staging.get("endpoint_suffix", "-staging"),
            staging_endpoint_url=ci_staging.get("endpoint_url", ""),
            production_endpoint_url=ci_raw.get("production", {}).get("endpoint_url", ""),
        )

        config = cls(
            pipeline_file=pipeline_file,
            model_name=model_name,
            project_dir=path.parent,
            target=target,
            endpoint_name=endpoint_name,
            output_fields=output_fields,
            test_quotes_dir=tq_dir,
            databricks=db_config,
            container=ct_config,
            azure_container_apps=aca_config,
            aws_ecs=ecs_config,
            gcp_run=gcr_config,
            safety=safety_config,
            ci=ci_config,
        )

        return _apply_env_overrides(config)

    def override(self, **cli_kwargs: Any) -> DeployConfig:
        """Return a copy with non-None CLI flags applied over TOML values."""
        c = copy.deepcopy(self)
        for key, val in cli_kwargs.items():
            if val is not None and hasattr(c, key):
                setattr(c, key, val)
        return c


def _apply_env_overrides(config: DeployConfig) -> DeployConfig:
    """Apply HAUTE_ prefixed env vars over TOML values.

    Resolution order (highest wins):
      1. CLI flags          (applied later via .override())
      2. Environment vars   HAUTE_MODEL_NAME=foo
      3. haute.toml         model_name = "foo"
    """
    env_map: dict[str, tuple[str, type]] = {
        "HAUTE_MODEL_NAME": ("model_name", str),
        "HAUTE_ENDPOINT_NAME": ("endpoint_name", str),
        "HAUTE_TARGET": ("target", str),
        "HAUTE_SERVING_WORKLOAD_SIZE": ("databricks.serving_workload_size", str),
        "HAUTE_SERVING_SCALE_TO_ZERO": ("databricks.serving_scale_to_zero", bool),
    }
    for env_key, (attr_path, attr_type) in env_map.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        if "." in attr_path:
            obj_name, field_name = attr_path.split(".", 1)
            obj = getattr(config, obj_name)
            if attr_type is bool:
                setattr(obj, field_name, val.lower() in ("true", "1", "yes"))
            else:
                setattr(obj, field_name, val)
        else:
            if attr_type is bool:
                setattr(config, attr_path, val.lower() in ("true", "1", "yes"))
            else:
                setattr(config, attr_path, val)
    return config


@dataclass
class ResolvedDeploy:
    """Computed state after resolving a DeployConfig against a parsed pipeline.

    Created by ``resolve_config()`` - never constructed directly.
    """

    config: DeployConfig
    full_graph: PipelineGraph
    pruned_graph: PipelineGraph
    input_node_ids: list[str]
    output_node_id: str
    artifacts: dict[str, Path]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    removed_node_ids: list[str] = field(default_factory=list)


def _load_env(project_root: Path) -> None:
    """Load .env file into os.environ if it exists.

    Uses python-dotenv for robust parsing (quoted values, interpolation,
    multi-line, etc.).  Falls back to no-op if dotenv is not installed.
    """
    env_path = project_root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        # Graceful fallback: minimal key=value parsing
        for line in read_user_text(env_path).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                os.environ.setdefault(key, value)


def resolve_config(config: DeployConfig) -> ResolvedDeploy:
    """Parse pipeline, prune graph, detect I/O nodes, collect artifacts, infer schemas.

    This is the main resolution function that converts user-provided
    ``DeployConfig`` into a fully resolved ``ResolvedDeploy`` ready for
    deployment.
    """
    from haute.deploy._bundler import collect_artifacts
    from haute.deploy._schema import infer_input_schema, infer_output_schema
    from haute.parser import parse_pipeline_file

    # Ensure .env is loaded (idempotent — also called in from_toml, but
    # needed here for programmatic DeployConfig construction).
    project_root = (
        config.project_dir.resolve()
        if config.project_dir is not None
        else config.pipeline_file.resolve().parent
    )
    _load_env(project_root)

    # Parse the pipeline
    full_graph = parse_pipeline_file(config.pipeline_file)
    if not full_graph.nodes:
        raise ValueError(f"No nodes found in {config.pipeline_file}")

    # Find output node
    output_node_id = find_output_node(full_graph)

    # Prune to scoring path
    pruned_graph, _kept_ids, removed_ids = prune_for_deploy(full_graph, output_node_id)

    # Find input nodes
    deploy_inputs = find_deploy_input_nodes(pruned_graph)
    if not deploy_inputs:
        # Fallback: use the single source node in the pruned graph
        all_sources = find_source_nodes(pruned_graph)
        if len(all_sources) == 1:
            deploy_inputs = all_sources
        elif len(all_sources) == 0:
            raise ValueError("No source nodes in the pruned graph.")
        else:
            raise ValueError(
                f"Multiple source nodes in pruned graph ({all_sources}) but none "
                "is an apiInput node. Add an API Input node with "
                "@pipeline.api_input(path=...)."
            )

    # Collect artifacts
    pipeline_dir = config.pipeline_file.parent
    artifacts = collect_artifacts(pruned_graph, deploy_inputs, pipeline_dir)

    # Infer schemas
    input_schema = infer_input_schema(pruned_graph, deploy_inputs[0])
    output_schema = infer_output_schema(pruned_graph, output_node_id, deploy_inputs)

    logger.info(
        "config_resolved",
        pipeline=str(config.pipeline_file),
        total_nodes=len(full_graph.nodes),
        pruned_nodes=len(pruned_graph.nodes),
        removed_nodes=len(removed_ids),
        input_nodes=len(deploy_inputs),
        artifacts=len(artifacts),
    )
    return ResolvedDeploy(
        config=config,
        full_graph=full_graph,
        pruned_graph=pruned_graph,
        input_node_ids=deploy_inputs,
        output_node_id=output_node_id,
        artifacts=artifacts,
        input_schema=input_schema,
        output_schema=output_schema,
        removed_node_ids=removed_ids,
    )
