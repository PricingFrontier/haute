"""Shared factory for building ResolvedDeploy instances in deploy tests."""

from __future__ import annotations

from pathlib import Path

from haute._hashing import content_hash
from haute._project_context import current_project
from haute.deploy._config import ContainerConfig, DeployConfig, ResolvedDeploy
from haute.deploy._project_modules import ProjectModules
from haute.deploy._request_rows import RequestRows
from haute.graph_utils import PipelineGraph

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
DEFAULT_PIPELINE_FILE = FIXTURE_DIR / "pipeline.py"

# Patch-pinned image used by tests that target container builds but do not
# care about the specific image.  Keeping this in one place lets us bump the
# patch version across the suite without touching every call site.
TEST_PATCH_PINNED_BASE_IMAGE = "python:3.11.9-slim"

_SENTINEL = object()


def make_resolved_deploy(
    config: DeployConfig | None = None,
    **overrides: object,
) -> ResolvedDeploy:
    """Build a lightweight ResolvedDeploy with sensible defaults.

    Accepts either a pre-built DeployConfig or keyword overrides applied to a
    minimal config.  All graph/schema fields default to empty so tests that
    only exercise the MLflow API layer don't need to build real graphs.

    Supports all deploy test patterns:
    - ``make_resolved_deploy()`` — bare defaults
    - ``make_resolved_deploy(input_schema={...})`` — override specific fields
    - ``make_resolved_deploy(config=custom_cfg)`` — supply a pre-built config

    When ``config`` is None, DeployConfig fields can be passed as keyword
    arguments (``pipeline_file``, ``model_name``, ``target``, ``output_fields``,
    ``container``) and they'll be extracted from ``overrides`` before building
    the config.  All other kwargs become ResolvedDeploy field overrides.

    If ``target == "container"`` and no explicit ``container`` override is
    provided, a :class:`ContainerConfig` with a valid patch-pinned base image
    is injected.  ``DeployConfig.__post_init__`` rejects unpinned images for
    container targets, so tests that do not exercise image validation itself
    need a valid default.
    """
    if config is None:
        config_kwargs: dict[str, object] = {
            "pipeline_file": overrides.pop("pipeline_file", DEFAULT_PIPELINE_FILE),
            "model_name": overrides.pop("model_name", "test-model"),
            "target": overrides.pop("target", "databricks"),
        }
        # Only pass optional config fields when explicitly provided.
        output_fields = overrides.pop("output_fields", _SENTINEL)
        if output_fields is not _SENTINEL:
            config_kwargs["output_fields"] = output_fields
        container = overrides.pop("container", _SENTINEL)
        if container is not _SENTINEL:
            config_kwargs["container"] = container
        elif config_kwargs["target"] == "container":
            # Supply a pinned image so base-image validation passes for tests
            # that only care about the target dispatch, not the image itself.
            config_kwargs["container"] = ContainerConfig(
                base_image=TEST_PATCH_PINNED_BASE_IMAGE,
            )
        config = DeployConfig(**config_kwargs)

    paths: dict[str, Path] = resolved_paths(config)
    if config.project_dir is None and config.pipeline_file == DEFAULT_PIPELINE_FILE:
        # The default pipeline is the repository's fixture: the deployment is resolved in
        # the test's project instead, which holds the files the test writes; its steps run
        # there (``in_resolved_project``). A pipeline the test names keeps its own folder.
        paths["project_root"] = current_project().root
    defaults: dict[str, object] = {
        "config": config,
        **paths,
        "full_graph": PipelineGraph(),
        "pruned_graph": PipelineGraph(),
        "input_node_ids": ["policies"],
        "output_node_id": "output",
        "artifacts": {},
        "input_schema": {"col": "Int64"},
        "output_schema": {"col": "Int64"},
        "project_modules": ProjectModules(utility=None, unbundled_imports=()),
        "request_rows": RequestRows(multi_quote="in_request_order"),
    }
    defaults.update(overrides)
    if "artifact_digests" not in defaults:
        artifacts = defaults["artifacts"]
        assert isinstance(artifacts, dict)
        defaults["artifact_digests"] = artifact_digests(artifacts)
    return ResolvedDeploy(**defaults)


def resolved_paths(config: DeployConfig) -> dict[str, Path]:
    """The pipeline and project root a resolution of *config* records, as ``resolve_config``
    takes them: the configured project directory, else the pipeline's own."""
    return {
        "pipeline_file": config.pipeline_file,
        "project_root": (config.project_dir or config.pipeline_file.parent).resolve(),
    }


def artifact_digests(artifacts: dict[str, Path]) -> dict[str, str]:
    """Each existing artefact's digest, as resolution records it before anything reads it."""
    return {
        name: content_hash(Path(path)) for name, path in artifacts.items() if Path(path).is_file()
    }
