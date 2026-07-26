"""Shared helpers for deploy modules - manifest building, user detection."""

from __future__ import annotations

import getpass
from datetime import UTC, datetime
from typing import Any

from haute._logging import get_logger
from haute.deploy._config import ResolvedDeploy

logger = get_logger(component="deploy.utils")


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


def build_manifest(resolved: ResolvedDeploy) -> dict[str, Any]:
    """Build the deployment manifest dict.

    Shared by both MLflow and container deploy targets.  The key names
    are the canonical manifest schema; runtime consumers (_model_code.py
    and the generated container app.py) must use these same keys.
    """
    config = resolved.config
    return {
        "haute_version": get_haute_version(),
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
        "artifacts": {name: path.as_posix() for name, path in resolved.artifacts.items()},
        "snapshot_provenance": resolved.snapshot_provenance,
        "pruned_graph": resolved.pruned_graph.model_dump(),
        "nodes_deployed": len(resolved.pruned_graph.nodes),
        "nodes_skipped": len(resolved.removed_node_ids),
        "nodes_skipped_names": resolved.removed_node_ids,
    }
