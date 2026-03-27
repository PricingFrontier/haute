"""Artifact discovery and collection for deployment."""

from __future__ import annotations

from pathlib import Path

from haute._logging import get_logger
from haute.graph_utils import NodeType, PipelineGraph

logger = get_logger(component="deploy.bundler")


def collect_artifacts(
    pruned_graph: PipelineGraph,
    input_node_ids: list[str],
    pipeline_dir: Path,
) -> dict[str, Path]:
    """Discover and collect all artifacts needed for deployment.

    Walks the pruned graph and finds files that must be bundled:

    - ``externalFile`` nodes: model files (``.cbm``, ``.pkl``, etc.)
    - ``optimiserApply`` nodes: optimiser artifact files
    - ``modelScore`` nodes: CatBoost ``.cbm`` models (downloaded from MLflow)
    - ``dataSource`` nodes that are NOT deploy inputs: static data files

    Args:
        pruned_graph: Pruned React Flow graph JSON.
        input_node_ids: Source node IDs that receive live input (excluded).
        pipeline_dir: Directory containing the pipeline file (for resolving
            relative paths).

    Returns:
        Dict of artifact_name → absolute_path.

    Raises:
        FileNotFoundError: If a referenced artifact file does not exist.
    """
    input_set = set(input_node_ids)
    artifacts: dict[str, Path] = {}

    for node in pruned_graph.nodes:
        nid = node.id
        node_type = node.data.nodeType
        config = node.data.config

        if node_type == NodeType.EXTERNAL_FILE:
            raw_path = config.get("path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir)
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "externalFile")
            artifacts[artifact_name] = abs_path

        elif node_type == NodeType.OPTIMISER_APPLY:
            raw_path = config.get("artifact_path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir)
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "optimiserApply")
            artifacts[artifact_name] = abs_path

        elif node_type == NodeType.MODEL_SCORE:
            source_type = config.get("sourceType", "run")
            run_id = config.get("run_id", "")
            artifact_path = config.get("artifact_path", "")

            if source_type == "registered":
                registered_model = config.get("registered_model", "")
                version = config.get("version", "")
                if not registered_model:
                    logger.warning(
                        "model_score_skip_no_registered_model",
                        node_id=nid,
                    )
                    continue
                run_id, artifact_path = _resolve_registered_model(
                    registered_model,
                    version,
                )
            else:
                # source_type == "run" (default)
                if not run_id or not artifact_path:
                    continue

            # Download from MLflow at deploy time so the artifact is
            # bundled into the container / MLflow model package.
            local_path = _download_model_artifact(
                run_id,
                artifact_path,
                pipeline_dir,
            )
            # Patch config so the scorer can build a matching artifact key
            config["artifact_path"] = local_path.name
            artifact_name = _artifact_name(nid, local_path)
            artifacts[artifact_name] = local_path

        elif node_type == NodeType.DATA_SOURCE and nid not in input_set:
            raw_path = config.get("path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir)
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "dataSource (static)")
            artifacts[artifact_name] = abs_path

    return artifacts


def _resolve_path(raw_path: str, pipeline_dir: Path) -> Path:
    """Resolve a possibly-relative path against the pipeline directory."""
    p = Path(raw_path)
    if p.is_absolute():
        return p
    # Try relative to CWD first (matches runtime behavior), then pipeline dir
    cwd_path = Path.cwd() / p
    if cwd_path.exists():
        return cwd_path.resolve()
    pipe_path = pipeline_dir / p
    if pipe_path.exists():
        return pipe_path.resolve()
    # Return CWD-relative (will be caught by _check_exists)
    return cwd_path.resolve()


def _artifact_name(node_id: str, path: Path) -> str:
    """Generate a unique artifact name from node ID and filename."""
    return f"{node_id}__{path.name}"


def _resolve_registered_model(
    registered_model: str,
    version: str,
) -> tuple[str, str]:
    """Resolve a registered model name + version to (run_id, artifact_path).

    Uses MLflow's model registry to look up the concrete run that produced
    the model version, then auto-discovers the artifact path within that run.

    Args:
        registered_model: Registered model name (e.g. ``"my-model"``).
        version: Version string (``"1"``, ``"2"``, ``"latest"``, or ``""``).

    Returns:
        Tuple of ``(run_id, artifact_path)``.

    Raises:
        ImportError: If ``mlflow`` is not installed.
        ValueError: If the model or version cannot be found, or if the
            resolved model version has no associated run.
    """
    from haute._mlflow_io import _find_model_artifact
    from haute._mlflow_utils import resolve_mlflow_source

    run_id, resolved_version, _mlflow, client = resolve_mlflow_source(
        source_type="registered",
        registered_model=registered_model,
        version=version,
    )

    if not run_id:
        raise ValueError(
            f"Registered model '{registered_model}' version {resolved_version} "
            "has no associated run_id. Cannot download artifact."
        )

    # Auto-discover the artifact path (e.g. "model.cbm" or "model/")
    artifact_path, _flavor = _find_model_artifact(client, run_id)

    logger.info(
        "registered_model_resolved",
        model=registered_model,
        version=resolved_version,
        run_id=run_id,
        artifact_path=artifact_path,
    )

    return run_id, artifact_path


def _download_model_artifact(
    run_id: str,
    artifact_path: str,
    pipeline_dir: Path,
) -> Path:
    """Download a MODEL_SCORE .cbm artifact from MLflow, with local caching.

    Uses the same ``.cache/models/`` directory as ``_mlflow_io`` so that
    previously downloaded models aren't re-fetched.
    """
    from haute._mlflow_io import _resolve_artifact_local

    try:
        import mlflow
    except ImportError:
        raise ImportError(
            "mlflow is required to bundle MODEL_SCORE artifacts. "
            "Install it with: pip install mlflow"
        ) from None

    from haute.modelling._mlflow_log import resolve_tracking_backend

    tracking_uri, _ = resolve_tracking_backend()
    mlflow.set_tracking_uri(tracking_uri)

    local_path = _resolve_artifact_local(mlflow, run_id, artifact_path)
    resolved = Path(local_path)
    if not resolved.is_file():
        raise FileNotFoundError(f"MODEL_SCORE artifact not found after download: {local_path}")
    return resolved


def _check_exists(path: Path, node_id: str, node_type: str) -> None:
    """Raise FileNotFoundError if the artifact file doesn't exist."""
    if not path.is_file():
        raise FileNotFoundError(f"Artifact not found for {node_type} node '{node_id}': {path}")
