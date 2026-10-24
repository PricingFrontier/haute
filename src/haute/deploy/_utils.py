"""Shared helpers for deploy modules - manifest building, user detection."""

from __future__ import annotations

import getpass
import re
from datetime import UTC, datetime
from typing import Any

from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._types import NodeType, PipelineGraph
from haute.deploy._config import ResolvedDeploy

logger = get_logger(component="deploy.utils")


def artifact_basename(raw_path: str) -> str:
    """The file name a configured path names, on every platform.

    A path saved on Windows may use backslashes; splitting on both separators
    gives the bundler and the deployed scorer one artifact key for it.
    """
    return re.split(r"[\\/]", raw_path)[-1]


def artifact_file_name(key: str) -> str:
    """The flat file name a bundle stores the artefact *key* under.

    A key is ``<node id>__<file>``. A node inside a submodel has a runtime id holding
    ``/`` (``submodel_runtime/<occurrence>/<node>``), and two occurrences of one
    definition share their nodes' names, so its key is stored as
    ``<digest of the key>__<file>``: one flat file, the same in every build, and
    distinct for each occurrence. Any other key is its own file name.
    """
    name = artifact_basename(key)
    if name == key:
        return key
    return f"{content_hash_bytes(key.encode('utf-8'))}__{name}"


def get_user() -> str:
    """Get the current user's name, or 'unknown' if unavailable."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def get_haute_version() -> str:
    """Get the installed haute version."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("haute")
    except PackageNotFoundError:
        return "0.0.0-dev"


def model_source_line(node_id: str, source: dict[str, Any]) -> str:
    """One deploy-output line naming the registered model version a node bundles."""
    alias = f" @{source['alias']}" if source.get("alias") else ""
    return (
        f"Model {node_id}: {source['registered_model']}{alias} -> version "
        f"{source['version']} (run {source['run_id']})"
    )


def shipped_graph(graph: PipelineGraph) -> dict[str, Any]:
    """*graph* as a bundle carries it: every Workbench Input without its ``sample``.

    The sample is the quote an analyst typed while building. A deployed pipeline reads
    each request instead, and the schema dry run its tables' null quote, so nothing in a
    bundle reads the sample and it stays in the project. Submodels' graphs are walked too.
    """
    shipped = graph.model_dump()
    _drop_samples(shipped)
    return shipped


def _drop_samples(graph: dict[str, Any]) -> None:
    for node in graph["nodes"]:
        if node["data"]["nodeType"] == NodeType.WORKBENCH_INPUT:
            node["data"]["config"].pop("sample", None)
    for submodel in (graph.get("submodels") or {}).values():
        _drop_samples(submodel["graph"])


def build_manifest(resolved: ResolvedDeploy) -> dict[str, Any]:
    """Build the deployment manifest dict.

    Shared by both MLflow and container deploy targets.  The key names
    are the canonical manifest schema; runtime consumers (_model_code.py
    and the generated container app.py) must use these same keys.
    """
    import polars

    config = resolved.config
    return {
        "haute_version": get_haute_version(),
        # A served model runs only under the Polars it was deployed with.
        "polars_version": polars.__version__,
        "pipeline_name": resolved.pruned_graph.pipeline_name or config.model_name,
        "pipeline_file": config.pipeline_file.as_posix(),
        "target": config.target,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": get_user(),
        "input_node_ids": resolved.input_node_ids,
        "output_node_id": resolved.output_node_id,
        "output_fields": config.output_fields,
        "input_schema": resolved.input_schema,
        "output_schema": resolved.output_schema,
        "execution_policy": resolved.execution_policy,
        "artifacts": {name: path.as_posix() for name, path in resolved.artifacts.items()},
        "snapshot_provenance": resolved.snapshot_provenance,
        "model_sources": resolved.model_sources,
        "pruned_graph": shipped_graph(resolved.pruned_graph),
        "nodes_deployed": len(resolved.pruned_graph.nodes),
        "nodes_skipped": len(resolved.removed_node_ids),
        "nodes_skipped_names": resolved.removed_node_ids,
        "request_rows": resolved.request_rows.to_manifest(),
        "artifact_digests": dict(resolved.artifact_digests),
    }
