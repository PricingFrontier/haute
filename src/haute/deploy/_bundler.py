"""Artifact discovery and collection for deployment."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute._model_flavors import family_for_artifact
from haute._path_resolution import PathPreference, RuntimePathError, resolve_runtime_file_path
from haute.errors import DeployError
from haute.graph_utils import NodeType, PipelineGraph

if TYPE_CHECKING:
    from mlflow.tracking import MlflowClient

    from haute._mlflow_utils import ResolvedBackend

logger = get_logger(component="deploy.bundler")


class ArtifactKeys(dict[str, Path]):
    """The bundle's ``<node>__<filename>`` artifact keys, refusing a collision.

    The key scheme is not injective (node ``a`` with ``b__c.pkl`` and node
    ``a__b`` with ``c.pkl`` both give ``a__b__c.pkl``), and keys equal
    ignoring case clobber each other on a case-insensitive file system, so
    a plain dict would keep the later artifact. :meth:`add` refuses either,
    naming both nodes and files, before anything is uploaded; adding the
    same file under the same key again is accepted.
    """

    def __init__(self) -> None:
        super().__init__()
        self._owners: dict[str, tuple[str, str]] = {}

    def add(self, node_id: str, key: str, path: Path) -> None:
        folded = key.casefold()
        owner = self._owners.get(folded)
        if owner is not None:
            other_node, other_key = owner
            if other_key == key and self[other_key] == path:
                return
            raise DeployError(
                f"Deploy artifacts {other_key!r} (node {other_node!r}, file "
                f"{self[other_key].name!r}) and {key!r} (node {node_id!r}, file {path.name!r}) "
                "would share one bundle key"
                + ("" if other_key == key else " on a case-insensitive file system")
                + ". Rename one of the nodes or files.",
                node_id=node_id,
                other_node_id=other_node,
                artifact_key=key,
            )
        self._owners[folded] = (node_id, key)
        self[key] = path


def collect_artifacts(
    pruned_graph: PipelineGraph,
    input_node_ids: list[str],
    pipeline_dir: Path,
    *,
    project_root: Path | None = None,
    resources: ExitStack | None = None,
    snapshot_provenance: dict[str, dict[str, Any]] | None = None,
    model_sources: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Path]:
    """Discover and collect all artifacts needed for deployment.

    Walks the pruned graph and finds files that must be bundled:

    - ``externalFile`` nodes: model files (``.cbm``, ``.pkl``, etc.)
    - ``optimiserApply`` nodes: optimiser artifact files
    - ``modelScore`` nodes: model files (downloaded from MLflow, or copied from
      the project for a file source) and the feature contracts they score under
    - ``dataInput`` nodes that are NOT deploy inputs: static data inputs

    Args:
        pruned_graph: Pruned React Flow graph JSON.
        input_node_ids: Source node IDs that receive live input (excluded).
        pipeline_dir: Directory containing the pipeline file (for resolving
            relative paths).

        model_sources: When given, receives each registered ``modelScore``
            node's resolution (registered model, alias, concrete version and
            run ID), resolved once here so the bundle records exactly what it
            packaged.

    Returns:
        Dict of artifact_name → absolute_path.

    Raises:
        FileNotFoundError: If a referenced artifact file does not exist.
    """
    input_set = set(input_node_ids)
    project_root = (project_root or pipeline_dir).resolve()
    artifacts = ArtifactKeys()

    for node in pruned_graph.nodes:
        nid = node.id
        node_type = node.data.nodeType
        config = node.data.config

        if node_type == NodeType.EXTERNAL_FILE:
            raw_path = config.get("path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir, project_root, nid, "path")
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "externalFile")
            artifacts.add(nid, artifact_name, abs_path)

        elif node_type == NodeType.OPTIMISER_APPLY:
            source_type = config.get("sourceType", "")
            raw_path = config.get("artifact_path", "")
            if not raw_path:
                continue
            if source_type != "file":
                raise ValueError(
                    f"optimiserApply node {nid!r} with artifact_path must set sourceType='file'"
                )
            abs_path = _resolve_path(raw_path, pipeline_dir, project_root, nid, "artifact_path")
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, "optimiserApply")
            artifacts.add(nid, artifact_name, abs_path)

        elif node_type == NodeType.MODEL_SCORE:
            from haute._model_source import (
                FileModelSource,
                IncompleteModelSourceError,
                RegisteredModelSource,
                parse_model_source,
            )

            explicit_contract = config.get("feature_contract_path")
            if explicit_contract is not None:
                if not isinstance(explicit_contract, str) or not explicit_contract:
                    raise DeployError(
                        f"Node {nid!r} field 'feature_contract_path' must be a non-empty string; "
                        f"got {explicit_contract!r} ({type(explicit_contract).__name__}). "
                        "Use an in-project feature-contract path.",
                        node_id=nid,
                        field="feature_contract_path",
                        path=explicit_contract,
                    )
                # The scoring rule: the project before the pipeline directory.
                contract_path = _resolve_path(
                    explicit_contract,
                    pipeline_dir,
                    project_root,
                    nid,
                    "feature_contract_path",
                    prefer="project",
                )
                _check_exists(contract_path, nid, "modelScore feature contract")
                artifacts.add(nid, f"{nid}__feature_contract.json", contract_path)

            try:
                model_source = parse_model_source(config)
            except IncompleteModelSourceError as exc:
                # Nothing to download; the deploy scorer refuses the node as an
                # identity passthrough unless an artifact is bundled for it.
                logger.warning(
                    "model_score_skip_incomplete_source",
                    node_id=nid,
                    missing_field=exc.context.get("missing_field"),
                )
                continue
            if model_source is None:
                continue
            if isinstance(model_source, FileModelSource):
                _collect_model_file(
                    nid, model_source, explicit_contract, pipeline_dir, project_root, artifacts
                )
                continue

            # The node's stored destination ("" = local) is resolved to exactly
            # one backend, after the skip guards so an unconfigured node stays a
            # silent skip, and that object is threaded through both the registry
            # lookup and the download: a settings save mid-bundle cannot split
            # the two across backends.
            from haute._mlflow_utils import resolve_backend

            if isinstance(model_source, RegisteredModelSource):
                backend = resolve_backend(model_source.mlflow_destination)
                run_id, artifact_path, resolved_version = _resolve_registered_model(
                    model_source.registered_model,
                    model_source.version,
                    backend=backend,
                    alias=model_source.alias,
                )
                if model_sources is not None:
                    model_sources[nid] = {
                        "registered_model": model_source.registered_model,
                        "alias": model_source.alias,
                        "version": resolved_version,
                        "run_id": run_id,
                    }
            else:
                artifact_path = model_source.artifact_path
                if not artifact_path:
                    # A run without an artifact path is discovered when served.
                    continue
                run_id = model_source.run_id
                backend = resolve_backend(model_source.mlflow_destination)

            _validate_mlflow_artifact_identifier(nid, artifact_path)

            # Download from MLflow at deploy time so the artifact is
            # bundled into the container / MLflow model package.
            local_path = _download_model_artifact(
                run_id,
                artifact_path,
                pipeline_dir,
                backend=backend,
            )
            # Patch config so the scorer can build a matching artifact key
            config["artifact_path"] = Path(artifact_path).name
            artifact_name = f"{nid}__{config['artifact_path']}"
            artifacts.add(nid, artifact_name, local_path)

            # An MLflow model is served under the contract its run logged beside
            # it, the one the preview binds, so each request is checked against
            # training's features, dtypes and offset; a model whose run logged
            # none is served against the features it names, as the preview scores it.
            if explicit_contract is None:
                run_contract = _run_contract(backend, run_id, artifact_path)
                if run_contract is not None:
                    artifacts.add(nid, f"{nid}__feature_contract.json", run_contract)

        elif node_type == NodeType.DATA_INPUT and nid not in input_set:
            _collect_static_data_input(
                nid,
                config,
                pipeline_dir,
                artifacts,
                project_root=project_root,
                resources=resources,
                snapshot_provenance=snapshot_provenance,
            )

    return artifacts


def _collect_model_file(
    node_id: str,
    model_source: Any,
    explicit_contract: str | None,
    pipeline_dir: Path,
    project_root: Path,
    artifacts: ArtifactKeys,
) -> None:
    """Bundle a file-sourced Model Scoring node's model and the contract it scores under.

    The model and its sibling contract resolve exactly as scoring resolves them
    (:mod:`haute._model_source`): the project before the pipeline directory, and
    a sibling that leaves the project through a symlink is refused. The model
    is bundled under ``<node>__<configured file name>``, the key the deployed
    scorer looks it up by; without an explicit contract (already bundled by the
    caller), the contract saved beside the model is bundled under
    ``<node>__feature_contract.json``.
    """
    from haute._model_source import model_contract_path
    from haute.deploy._utils import artifact_basename
    from haute.modelling._feature_contract import CONTRACT_FILENAME

    # An unsupported suffix is refused before anything is copied.
    family_for_artifact(model_source.model_path)
    model_path = _resolve_path(
        model_source.model_path,
        pipeline_dir,
        project_root,
        node_id,
        "model_path",
        prefer="project",
    )
    _check_exists(model_path, node_id, "modelScore")
    artifacts.add(node_id, f"{node_id}__{artifact_basename(model_source.model_path)}", model_path)
    if explicit_contract is not None:
        return
    try:
        sibling = model_contract_path(model_source, None, pipeline_dir, project_root=project_root)
    except RuntimePathError as exc:
        raise DeployError(
            f"Node {node_id!r} has a feature contract beside its model file that resolves "
            "outside the project root. Replace it with a contract file inside the project.",
            node_id=node_id,
            field="model_path",
        ) from exc
    if sibling is not None:
        artifacts.add(node_id, f"{node_id}__{CONTRACT_FILENAME}", sibling)


def _run_contract(backend: ResolvedBackend, run_id: str, artifact: str) -> Path | None:
    """The contract run *run_id* logged beside *artifact*, or ``None`` when it logged none.

    The run's client is opened only to list the run when the contract is not
    already cached, as the model's own download is.
    """
    import mlflow

    from haute._mlflow_io import _run_contract_if_logged
    from haute._mlflow_utils import resolve_mlflow_source

    def client() -> MlflowClient:
        opened: MlflowClient = resolve_mlflow_source(
            source_type="run", run_id=run_id, backend=backend
        )[3]
        return opened

    logged = _run_contract_if_logged(mlflow, client, backend, run_id, artifact)
    return None if logged is None else Path(logged)


def _collect_static_data_input(
    node_id: str,
    config: dict,
    pipeline_dir: Path,
    artifacts: ArtifactKeys,
    *,
    project_root: Path,
    resources: ExitStack | None,
    snapshot_provenance: dict[str, dict[str, Any]] | None,
) -> None:
    """Collect a retained canonical Data Input for deployment.

    A direct Parquet source of one file bundles that file. Every other Data Input,
    a direct Parquet folder or glob included, bundles a leased snapshot generation
    (:func:`shipped_snapshot_config`), keeping its exact verified parquet and
    metadata files alive during bundle construction.
    """
    from haute._polars_io_registry import validate_data_input_config
    from haute._source_cache import SourceCacheCorruptError
    from haute.errors import DeployError

    try:
        validated = shipped_snapshot_config(
            node_id, validate_data_input_config(config), pipeline_dir, project_root
        )
        if validated is None:
            source_path = _resolve_direct_parquet_source(
                node_id,
                config,
                pipeline_dir,
                project_root,
            )
            artifacts.add(node_id, _artifact_name(node_id, source_path), source_path)
            return

        from haute._input_providers import source_cache_identity
        from haute._sandbox import _get_project_root
        from haute._source_cache import SourceCacheStore

        if resources is None:
            raise DeployError(
                f"Data Input node {node_id!r} requires a deploy resource owner "
                "to hold its snapshot generation lease.",
                node_id=node_id,
            )
        identity = source_cache_identity(validated, base_dir=pipeline_dir)
        generation = resources.enter_context(SourceCacheStore(_get_project_root()).lease(identity))
        for path in generation.data_paths:
            artifacts.add(node_id, f"{node_id}__snapshot.{path.name}", path)
        artifacts.add(node_id, f"{node_id}__snapshot.meta.json", generation.metadata_path)
        if snapshot_provenance is not None:
            snapshot_provenance[node_id] = {
                "provider": identity.provider,
                **generation.metadata.to_dict(),
            }
    except DeployError:
        raise
    except (FileNotFoundError, SourceCacheCorruptError) as exc:
        raise DeployError(
            f"Data Input node {node_id!r} requires a ready, valid matching snapshot "
            "before deployment.",
            node_id=node_id,
        ) from exc
    except Exception as exc:
        raise DeployError(
            f"Data Input node {node_id!r} is not deployable: invalid Data Input configuration.",
            node_id=node_id,
        ) from exc


def _resolve_direct_parquet_source(
    node_id: str,
    config: dict[str, Any],
    pipeline_dir: Path,
    project_root: Path,
) -> Path:
    """Resolve and prove a canonical direct Parquet source is readable."""
    from haute._polars_io_registry import data_input_is_direct, validate_data_input_config

    validated = validate_data_input_config(config)
    if not data_input_is_direct(validated):
        raise DeployError(
            f"Data Input node {node_id!r} has unsupported direct deployment configuration.",
            node_id=node_id,
        )
    source_path = _resolve_path(
        validated.get("path", ""), pipeline_dir, project_root, node_id, "path"
    )
    if not source_path.is_file():
        raise DeployError(
            f"Data Input node {node_id!r} direct Parquet source does not exist: {source_path}",
            node_id=node_id,
            field="path",
            path=str(source_path),
        )
    try:
        import polars as pl

        pl.scan_parquet(source_path).collect_schema()
    except Exception as exc:
        raise DeployError(
            f"Data Input node {node_id!r} direct Parquet source is not readable: {source_path}",
            node_id=node_id,
            field="path",
            path=str(source_path),
        ) from exc
    return source_path


_FILE_INPUT_TYPES = frozenset({"file", "lakehouse"})


def shipped_snapshot_config(
    node_id: str, validated: dict[str, Any], pipeline_dir: Path, project_root: Path
) -> dict[str, Any] | None:
    """The configuration whose input snapshot deploy ships for a Data Input, or ``None``.

    ``None`` for a direct Parquet source of one file, which ships that file. A direct
    Parquet folder or glob pattern ships a snapshot read from every file it names
    (mode ``"read"``), so a folder's partition columns and every file reach the
    deployment; every other Data Input ships its own snapshot.
    """
    from haute._input_providers import named_source_files
    from haute._polars_io_registry import data_input_is_direct

    if not data_input_is_direct(validated):
        return validated
    source_path = _resolve_path(
        validated.get("path", ""), pipeline_dir, project_root, node_id, "path"
    )
    if source_path.is_file() or not named_source_files(validated, source_path):
        return None
    return {**validated, "mode": "read"}


def _shipped_file_inputs(
    graph: PipelineGraph, input_node_ids: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """The retained file-based Data Inputs deploy ships: each one but a request input."""
    from haute._polars_io_registry import validate_data_input_config

    return [
        (node.id, validate_data_input_config(node.data.config))
        for node in graph.nodes
        if node.data.nodeType == NodeType.DATA_INPUT
        and node.id not in input_node_ids
        and node.data.config.get("inputType") in _FILE_INPUT_TYPES
    ]


def refuse_data_outside_pipeline_folder(
    graph: PipelineGraph, input_node_ids: list[str], pipeline_dir: Path, project_root: Path
) -> None:
    """Refuse a file the quote output reads that lies outside the pipeline folder.

    Deploy ships only data stored in the pipeline folder (``rating/``); a file outside
    it, such as the project's local ``data/``, is never shipped, so a deployment reading
    one could not score. Inputs off the path to the quote output, which the pruned
    graph no longer holds (model training, optimisation), may read from anywhere.
    """
    from haute._project_layout import PIPELINE_FILES_FOLDER

    folder = pipeline_dir.resolve()
    root = project_root.resolve()
    shown = folder.relative_to(root).as_posix() if folder.is_relative_to(root) else folder.name
    for node_id, validated in _shipped_file_inputs(graph, input_node_ids):
        raw_path = validated.get("path", "")
        if _resolve_path(raw_path, pipeline_dir, project_root, node_id, "path").is_relative_to(
            folder
        ):
            continue
        raise DeployError(
            f"Data Input node {node_id!r} reads {raw_path!r}, outside the pipeline folder "
            f"{shown!r}, and the quote output depends on it. Deploy ships only data stored "
            f"in the pipeline folder: move the file into {shown}/{PIPELINE_FILES_FOLDER}/data, "
            "or take this input off the path to the quote output (model training and "
            "optimisation may read data from anywhere).",
            node_id=node_id,
            field="path",
            path=raw_path,
        )


def prepare_shipped_snapshots(
    graph: PipelineGraph, input_node_ids: list[str], pipeline_dir: Path, project_root: Path
) -> None:
    """Build or refresh the snapshot of every file-based Data Input deploy ships.

    A clean checkout (CI) holds no input snapshot, and an edited file leaves its
    snapshot stale, so deploy prepares them itself, as an execution's automatic
    preparation does: a missing snapshot is built, a stale one refreshed, inside a
    hard memory cap. Database and Databricks inputs, and the request input's sample,
    are left alone.
    """
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile
    from haute._input_preparation import prepare_input_snapshots

    shipped: dict[str, dict[str, Any]] = {}
    for node_id, validated in _shipped_file_inputs(graph, input_node_ids):
        snapshot_config = shipped_snapshot_config(node_id, validated, pipeline_dir, project_root)
        if snapshot_config is not None:
            # The path as deploy resolves it, so the snapshot prepared is the one bundled.
            source = _resolve_path(validated["path"], pipeline_dir, project_root, node_id, "path")
            shipped[node_id] = {**snapshot_config, "path": str(source)}
    if not shipped:
        return
    node_map = {
        node_id: graph.node_map[node_id].with_config(config) for node_id, config in shipped.items()
    }
    context = create_admitted_execution_context(
        operation="deploy_input_snapshots", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        prepare_input_snapshots(
            list(shipped),
            node_map,
            profile=ExecutionProfile.LAZY_SINK,
            execution_context=context,
            base_dir=pipeline_dir,
            schema_only=False,
        )
    finally:
        context.release_admission(preserve_primary_error=True)


def _resolve_path(
    raw_path: str,
    pipeline_dir: Path,
    project_root: Path | None = None,
    node_id: str | None = None,
    field_name: str | None = None,
    *,
    prefer: PathPreference = "pipeline",
) -> Path:
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
    root = (project_root or pipeline_dir).resolve()
    if not isinstance(raw_path, str) or not raw_path:
        raise DeployError(
            f"Node {node_id!r} field {field_name!r} must be a non-empty local path string; "
            f"got {raw_path!r} ({type(raw_path).__name__}). Use a path inside the project root.",
            node_id=node_id,
            field=field_name,
            path=raw_path,
        )
    try:
        return resolve_runtime_file_path(
            raw_path,
            pipeline_dir=pipeline_dir,
            project_root=root,
            prefer=prefer,
            enforce_project_root=True,
        )
    except RuntimePathError as exc:
        raise DeployError(
            f"Node {node_id!r} field {field_name!r} has invalid local deploy path "
            f"{raw_path!r} ({type(exc).__name__}): {exc}. Use a path inside "
            f"the project root {root}.",
            node_id=node_id,
            field=field_name,
            path=raw_path,
            project_root=str(root),
        ) from exc


def _validate_mlflow_artifact_identifier(node_id: str, artifact_path: str) -> None:
    """Reject filesystem-shaped MLflow artifact identifiers before download."""
    if not isinstance(artifact_path, str) or not artifact_path:
        raise DeployError(
            f"Node {node_id!r} field 'artifact_path' must be a non-empty MLflow artifact "
            f"identifier string; got {artifact_path!r} ({type(artifact_path).__name__}). "
            "Use a relative artifact name without '..' segments.",
            node_id=node_id,
            field="artifact_path",
            artifact_path=artifact_path,
        )
    posix_parts = artifact_path.replace("\\", "/").split("/")
    windows = PureWindowsPath(artifact_path)
    if (
        Path(artifact_path).is_absolute()
        or PurePosixPath(artifact_path).is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix_parts
    ):
        raise DeployError(
            f"modelScore node {node_id!r} has unsafe MLflow artifact identifier "
            f"{artifact_path!r}. Use a relative artifact name without '..' segments.",
            node_id=node_id,
            field="artifact_path",
            artifact_path=artifact_path,
        )


def _artifact_name(node_id: str, path: Path) -> str:
    """Generate a unique artifact name from node ID and filename."""
    return f"{node_id}__{path.name}"


def _resolve_registered_model(
    registered_model: str,
    version: str,
    *,
    backend: ResolvedBackend,
    alias: str = "",
) -> tuple[str, str, str]:
    """Resolve a registered model name + version or alias to (run_id, artifact_path, version).

    Uses MLflow's model registry to look up the concrete run that produced
    the model version, then auto-discovers the artifact path within that run.

    Args:
        registered_model: Registered model name (e.g. ``"my-model"``).
        version: Version string (``"1"``, ``"2"``, ``"latest"``, or ``""``).
        backend: The backend the caller already resolved for this node. It is
            used as-is, so the registry lookup and the download that follows
            can never land on different destinations.
        alias: Registered model alias, resolved once to the version it targets.

    Returns:
        Tuple of ``(run_id, artifact_path, resolved_version)``.

    Raises:
        ImportError: If ``mlflow`` is not installed.
        ValueError: If the model or version cannot be found, or if the
            resolved model version has no associated run.
    """
    from haute._mlflow_io import _find_model_artifact
    from haute._mlflow_utils import resolve_mlflow_source

    run_id, resolved_version, _mlflow, client, _backend = resolve_mlflow_source(
        source_type="registered",
        registered_model=registered_model,
        version=version,
        backend=backend,
        alias=alias,
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
        alias=alias,
        version=resolved_version,
        run_id=run_id,
        artifact_path=artifact_path,
        backend_mode=backend.mode,
        backend_digest=backend.digest,
    )

    return run_id, artifact_path, resolved_version


def _download_model_artifact(
    run_id: str,
    artifact_path: str,
    pipeline_dir: Path,
    *,
    backend: ResolvedBackend,
) -> Path:
    """Download a MODEL_SCORE .cbm artifact from MLflow, with local caching.

    Uses the same ``.cache/models/`` directory as ``_mlflow_io`` so that
    previously downloaded models aren't re-fetched; *backend* selects the
    digest partition within that cache, so two destinations never share a
    cached file for the same run and artifact path.
    """
    from haute._mlflow_io import _resolve_artifact_local

    try:
        import mlflow
    except ImportError:
        raise ImportError(
            "mlflow is required to bundle MODEL_SCORE artifacts. "
            "Install it with: pip install mlflow"
        ) from None

    local_path = _resolve_artifact_local(
        mlflow,
        backend,
        run_id,
        artifact_path,
    )
    resolved = Path(local_path)
    if not resolved.is_file():
        raise FileNotFoundError(f"MODEL_SCORE artifact not found after download: {local_path}")
    return resolved


def _check_exists(path: Path, node_id: str, node_type: str) -> None:
    """Raise FileNotFoundError if the artifact file doesn't exist."""
    if not path.is_file():
        raise FileNotFoundError(f"Artifact not found for {node_type} node '{node_id}': {path}")
