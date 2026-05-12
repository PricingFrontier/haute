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
            source_type = config.get("sourceType", "")
            raw_path = config.get("artifact_path", "")
            if not raw_path:
                continue
            if source_type != "file":
                raise ValueError(
                    f"optimiserApply node {nid!r} with artifact_path must set sourceType='file'"
                )
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

            # Bundle the feature contract alongside the model so the deploy
            # scorer can verify train-vs-score drift at load time.
            # The training pipeline writes ``feature_contract.json`` into
            # the same cache directory; when present, include it as an
            # explicit artifact keyed to this node.
            _bundle_feature_contract(nid, local_path, artifacts)

        elif node_type == NodeType.DATA_SOURCE and nid not in input_set:
            raw_path = config.get("path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir)
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "dataSource (static)")
            # When the pipeline declares an expected column order for the
            # static source, verify the file agrees.  A
            # silent reorder leads to wrong joins at runtime.
            _verify_static_source_schema(nid, abs_path, config)
            artifacts[artifact_name] = abs_path

    return artifacts


def _bundle_feature_contract(
    node_id: str,
    model_path: Path,
    artifacts: dict[str, Path],
) -> None:
    """Add the model's feature contract (if present) to the bundle.

    Looks for ``feature_contract.json`` sitting next to the model file
    (that's where ``TrainingJob._save_artifacts`` writes it at train
    time and where the MLflow cache keeps it after download).  The
    artifact is keyed with the same ``<node>__<filename>`` scheme the
    deploy scorer uses to discover bundled files.
    """
    from haute.modelling._feature_contract import CONTRACT_FILENAME

    contract_path = model_path.parent / CONTRACT_FILENAME
    if not contract_path.is_file():
        logger.info(
            "model_score_no_feature_contract",
            node_id=node_id,
            looked_at=str(contract_path),
        )
        return
    artifact_name = _artifact_name(node_id, contract_path)
    artifacts[artifact_name] = contract_path


def _verify_static_source_schema(
    node_id: str,
    abs_path: Path,
    config: dict,
) -> None:
    """Check that a static dataSource file matches its declared schema.

    Reads the file schema through the same data-source adapter used at
    execution time, so schema declarations and bounded-profile source
    restrictions are enforced at the deploy boundary too. When
    ``expected_columns`` is declared, disagreement raises
    :class:`DeployError` naming the node. The deploy layer refuses to
    bundle a file whose shape drifted from the contract the rest of the
    pipeline was designed against.
    """
    expected = config.get("expected_columns")
    has_schema_declaration = any(
        key in config for key in ("schema_overrides", "dtypes", "column_dtypes", "schema")
    )
    if not expected and not has_schema_declaration:
        return

    from haute._execution_context import ExecutionProfile
    from haute._io import read_data_source
    from haute.errors import DeployError

    try:
        source_config = {**config, "path": str(abs_path)}
        schema = read_data_source(
            source_config,
            profile=ExecutionProfile.DEPLOY_BATCH,
        ).collect_schema()
        actual = schema.names()
    except Exception as exc:  # pragma: no cover — malformed-file path
        raise DeployError(
            f"Could not read schema for static dataSource node {node_id!r} "
            f"to verify its expected_columns contract.",
            node_id=node_id,
            path=str(abs_path),
            error=str(exc),
        ) from exc

    if expected and list(expected) != list(actual):
        raise DeployError(
            f"Static dataSource {node_id!r} column order does not match the "
            f"expected_columns declared in the pipeline: "
            f"expected={list(expected)}, actual={list(actual)}.",
            node_id=node_id,
            path=str(abs_path),
            expected_columns=list(expected),
            actual_columns=list(actual),
        )


def _resolve_path(raw_path: str, pipeline_dir: Path) -> Path:
    """Resolve ``raw_path`` to an absolute path at bundle time.

    Resolution order:

    1. Absolute paths are returned :meth:`~Path.resolve`-ed.
    2. Relative paths are first resolved against ``pipeline_dir`` — the
       directory containing the pipeline source file.  When the
       pipeline-relative file exists, that absolute path is returned.
    The key invariant — enforced by the test in
    ``test_deploy_config_and_bundle`` — is that a file existing under
    ``pipeline_dir`` **always wins** over a same-named file elsewhere.  The
    old code preferred CWD when both existed, which baked the caller's
    working directory into the manifest; the deployed container then failed
    because CWD in the container is ``/``.

    Every path returned is already absolute, so the manifest stores a
    deterministic, re-resolution-free pointer into the bundle.  Missing
    files surface loudly via :func:`_check_exists`.
    """
    p = Path(raw_path)
    if p.is_absolute():
        return p.resolve()
    pipeline_abs = (pipeline_dir / p).resolve()
    if pipeline_abs.exists():
        return pipeline_abs
    return pipeline_abs


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
