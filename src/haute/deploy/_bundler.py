"""Artifact discovery and collection for deployment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger
from haute._model_flavors import family_for_artifact
from haute._path_resolution import RuntimePathError, resolve_node_input_path
from haute._sandbox import is_contained
from haute.deploy._node_roles import DEPLOY_ARTIFACTS
from haute.errors import DeployError
from haute.graph_utils import PipelineGraph

if TYPE_CHECKING:
    from haute._mlflow_io import FetchedFiles
    from haute._mlflow_model import ResolvedModel
    from haute._mlflow_utils import ResolvedBackend
    from haute._model_source import RegisteredModelSource, RunModelSource

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
    served_configs: dict[str, dict[str, Any]] | None = None,
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
        served_configs: When given, receives the config each MLflow-sourced
            ``modelScore`` node is served under: its ``artifact_path`` the
            downloaded file's name, the key the deployed scorer finds the bundled
            model by. The graph's own configs are never written to.

    A run or registered ``modelScore`` node is answered by the model resolution,
    once for the whole bundle (``model_resolution_scope``), so a run with no
    artifact path is resolved here, never when served, and one that resolves
    to a pyfunc folder is refused.

    Returns:
        Dict of artifact_name → absolute_path.

    Raises:
        FileNotFoundError: If a referenced artifact file does not exist.
    """
    from haute._mlflow_model import model_resolution_scope

    with model_resolution_scope():
        return _collect_artifacts(
            pruned_graph,
            set(input_node_ids),
            pipeline_dir,
            project_root=(project_root or pipeline_dir).resolve(),
            resources=resources,
            snapshot_provenance=snapshot_provenance,
            model_sources=model_sources,
            served_configs=served_configs,
        )


def _collect_artifacts(
    pruned_graph: PipelineGraph,
    input_set: set[str],
    pipeline_dir: Path,
    *,
    project_root: Path,
    resources: ExitStack | None,
    snapshot_provenance: dict[str, dict[str, Any]] | None,
    model_sources: dict[str, dict[str, Any]] | None,
    served_configs: dict[str, dict[str, Any]] | None,
) -> dict[str, Path]:
    artifacts = ArtifactKeys()

    for node in pruned_graph.nodes:
        nid = node.id
        node_type = node.data.nodeType
        config = node.data.config
        # What the node reads that the bundle must carry (``DEPLOY_ARTIFACTS``).
        artifact = DEPLOY_ARTIFACTS[node_type]

        if artifact == "file":
            raw_path = config.get("path", "")
            if not raw_path:
                continue
            abs_path = _resolve_path(raw_path, pipeline_dir, project_root, nid, "path")
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, node_type.value)
            artifacts.add(nid, artifact_name, abs_path)

        elif artifact == "optimiser_result":
            from haute._node_apply import optimiser_apply_source

            source = optimiser_apply_source(config)
            raw_path = config.get("artifact_path", "")
            if source is not None and source.source_type != "file" and not raw_path:
                # Chosen from MLflow: bundled, so the deployed pipeline never reads MLflow.
                # One the editor has not filled in passes through, as the builder's does.
                if source.identifier:
                    _bundle_mlflow_optimiser_result(
                        nid,
                        config,
                        source.source_type,
                        artifacts,
                        resources=resources,
                        served_configs=served_configs,
                        model_sources=model_sources,
                    )
                continue
            if not raw_path:
                continue
            # Only a file source's artifact is bundled. The runtime tolerates, and
            # ignores, an MLflow source's artifact_path; the bundle refuses it, a
            # recorded disagreement (specs/pipeline-config, "One interpretation").
            if source is None or source.source_type != "file":
                raise ValueError(
                    f"optimiserApply node {nid!r} with artifact_path must set source_type='file'"
                )
            abs_path = _resolve_path(raw_path, pipeline_dir, project_root, nid, "artifact_path")
            artifact_name = _artifact_name(nid, abs_path)
            _check_exists(abs_path, nid, node_type.value)
            artifacts.add(nid, artifact_name, abs_path)

        elif artifact == "model":
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
                contract_path = _resolve_path(
                    explicit_contract, pipeline_dir, project_root, nid, "feature_contract_path"
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
                    nid,
                    node_type.value,
                    model_source,
                    explicit_contract,
                    pipeline_dir,
                    project_root,
                    artifacts,
                )
                continue

            # The node's stored destination ("" = local) is resolved to exactly
            # one backend, after the skip guards so an unconfigured node stays a
            # silent skip, and the model resolution answers the reference at it.
            from haute._mlflow_utils import resolve_backend

            resolved = _resolve_bundled_model(
                nid, model_source, resolve_backend(model_source.mlflow_destination)
            )
            if isinstance(model_source, RegisteredModelSource) and model_sources is not None:
                model_sources[nid] = {
                    "registered_model": model_source.registered_model,
                    "alias": model_source.alias,
                    "version": resolved.version,
                    "run_id": resolved.run_id,
                }

            # Download from MLflow at deploy time so the artifact is
            # bundled into the container / MLflow model package.
            fetched = _fetch_bundled_model(resolved)
            # The served config names the downloaded file, the key the scorer finds
            # the bundled model by; the parsed graph's config is left as it was.
            served_name = Path(resolved.artifact.path).name
            if served_configs is not None:
                served_configs[nid] = {**config, "artifact_path": served_name}
            artifacts.add(nid, f"{nid}__{served_name}", fetched.model)

            # An MLflow model is served under the contract its run logged beside
            # it, the one the preview binds, so each request is checked against
            # training's features, dtypes and offset; a model whose run logged
            # none is served against the features it names, as the preview scores it.
            if explicit_contract is None and fetched.contract is not None:
                artifacts.add(nid, f"{nid}__feature_contract.json", fetched.contract)

        elif artifact == "dataset" and nid not in input_set:
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


def with_served_configs(
    graph: PipelineGraph, served_configs: Mapping[str, Mapping[str, Any]]
) -> PipelineGraph:
    """*graph* with each node in *served_configs* carrying the config it is served under."""
    if not served_configs:
        return graph
    nodes = [
        node.with_config(served_configs[node.id]) if node.id in served_configs else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": nodes})


def _collect_model_file(
    node_id: str,
    node_label: str,
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
        model_source.model_path, pipeline_dir, project_root, node_id, "model_path"
    )
    _check_exists(model_path, node_id, node_label)
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


#: The config keys that choose an MLflow source, which a bundled result no longer reads.
_MLFLOW_SOURCE_KEYS = frozenset(
    {
        "run_id",
        "run_name",
        "experiment_id",
        "experiment_name",
        "registered_model",
        "version",
        "alias",
        "mlflow_destination",
    }
)


def _bundle_mlflow_optimiser_result(
    nid: str,
    config: Mapping[str, Any],
    source_type: str,
    artifacts: ArtifactKeys,
    *,
    resources: ExitStack | None,
    served_configs: dict[str, dict[str, Any]] | None,
    model_sources: dict[str, dict[str, Any]] | None,
) -> None:
    """Download an MLflow-sourced Optimiser Apply's result once and bundle it.

    The copy lives in a folder the resolution's *resources* own, until the bundle
    is built. The node is served as a file source naming the bundled result, and a
    registered source's resolution is recorded in *model_sources*, as a Model
    Scoring node's is.
    """
    import tempfile

    from haute._mlflow_utils import resolve_backend
    from haute._optimiser_io import _MLFLOW_ARTIFACT_NAME, download_mlflow_optimiser_result

    if resources is None:
        raise ValueError(
            "Bundling an optimiser result from MLflow needs the resolution's resources, "
            "which own the downloaded copy until the bundle is built."
        )
    folder = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="haute_opt_")))
    local, run_id, version = download_mlflow_optimiser_result(
        source_type=source_type,
        run_id=str(config.get("run_id") or ""),
        registered_model=str(config.get("registered_model") or ""),
        version=str(config.get("version") or ""),
        alias=str(config.get("alias") or ""),
        backend=resolve_backend(str(config.get("mlflow_destination") or "")),
        dst=folder,
    )
    artifacts.add(nid, f"{nid}__{_MLFLOW_ARTIFACT_NAME}", local)
    if served_configs is not None:
        served_configs[nid] = {
            **{key: value for key, value in config.items() if key not in _MLFLOW_SOURCE_KEYS},
            "source_type": "file",
            "artifact_path": _MLFLOW_ARTIFACT_NAME,
        }
    if source_type == "registered" and model_sources is not None:
        model_sources[nid] = {
            "registered_model": str(config.get("registered_model") or ""),
            "alias": str(config.get("alias") or ""),
            "version": version,
            "run_id": run_id,
        }


def _resolve_bundled_model(
    nid: str, model_source: RunModelSource | RegisteredModelSource, backend: ResolvedBackend
) -> ResolvedModel:
    """The model a run or registered node's reference resolves to, refusing a pyfunc folder."""
    from haute._mlflow_model import resolve_model
    from haute._model_source import RunModelSource

    if isinstance(model_source, RunModelSource) and model_source.artifact_path:
        # A configured path is refused naming the node before the store is asked.
        _validate_mlflow_artifact_identifier(nid, model_source.artifact_path)
    resolved = resolve_model(model_source, backend=backend)
    if not resolved.loads_from_file:
        raise DeployError(
            f"Node {nid!r} scores the MLflow pyfunc model folder {resolved.artifact.path!r} of "
            f"{resolved.reference}, and a deployment cannot bundle a pyfunc folder. Log the "
            "model's native file to the run and score that, or choose a model file.",
            node_id=nid,
            field="artifact_path",
        )
    _validate_mlflow_artifact_identifier(nid, resolved.artifact.path)
    return resolved


def _fetch_bundled_model(resolved: ResolvedModel) -> FetchedFiles:
    """The local copies of a resolved model and its contract, from the shared model cache."""
    from haute._mlflow_io import fetch_resolved_files

    try:
        import mlflow
    except ImportError:
        raise ImportError(
            "mlflow is required to bundle MODEL_SCORE artifacts. "
            "Install it with: pip install mlflow"
        ) from None
    return fetch_resolved_files(mlflow, resolved)


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
    from haute._input_providers import source_cache_identity
    from haute._polars_io_registry import PolarsIoConfigError, validate_data_input_config
    from haute._project_context import current_project
    from haute._source_cache import SourceCacheCorruptError, SourceCacheStore

    try:
        validated = validate_data_input_config(config)
    except PolarsIoConfigError as exc:
        raise DeployError(
            f"Data Input node {node_id!r} is not deployable: invalid Data Input "
            f"configuration: {exc}",
            node_id=node_id,
        ) from exc
    snapshot_config = shipped_snapshot_config(node_id, validated, pipeline_dir, project_root)
    if snapshot_config is None:
        source_path = _resolve_direct_parquet_source(node_id, config, pipeline_dir, project_root)
        artifacts.add(node_id, _artifact_name(node_id, source_path), source_path)
        return

    if resources is None:
        raise DeployError(
            f"Data Input node {node_id!r} requires a deploy resource owner "
            "to hold its snapshot generation lease.",
            node_id=node_id,
        )
    identity = source_cache_identity(snapshot_config, base_dir=pipeline_dir)
    # Only a snapshot that is missing or does not verify is the project's to rebuild;
    # any other failure to lease it keeps its own type and message.
    try:
        generation = resources.enter_context(
            SourceCacheStore(current_project().root).lease(identity)
        )
    except (FileNotFoundError, SourceCacheCorruptError) as exc:
        raise DeployError(
            f"Data Input node {node_id!r} requires a ready, valid matching snapshot "
            "before deployment.",
            node_id=node_id,
        ) from exc
    for path in generation.data_paths:
        artifacts.add(node_id, f"{node_id}__snapshot.{path.name}", path)
    artifacts.add(node_id, f"{node_id}__snapshot.meta.json", generation.metadata_path)
    if snapshot_provenance is not None:
        snapshot_provenance[node_id] = {
            "provider": identity.provider,
            **generation.metadata.to_dict(),
        }


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
        if DEPLOY_ARTIFACTS[node.data.nodeType] == "dataset"
        and node.id not in input_node_ids
        and node.data.config.get("input_type") in _FILE_INPUT_TYPES
    ]


def check_local_input_paths(graph: PipelineGraph, pipeline_dir: Path, project_root: Path) -> None:
    """Refuse a local path *graph* reads that deploy could not ship as the preview read it.

    Every local runtime input field (execution's own enumeration, so deploy cannot drift
    from the engine's definition of a local file read) is resolved by the one anchoring
    rule before any bundle copy, schema read or sample load. A path that leaves the
    project raises, and so does a relative path whose project-root and pipeline-folder
    copies differ: the editor reads the project root's, and the analyst may have checked
    either, so deploy names both instead of shipping one. Identical copies pass.
    """
    from haute._path_resolution import copies_differ, node_input_path_copies
    from haute.execution import _local_runtime_input_path_fields

    for node in graph.nodes:
        for field_name in _local_runtime_input_path_fields(node):
            raw_path = node.data.config.get(field_name)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                copies = node_input_path_copies(
                    raw_path, pipeline_dir=pipeline_dir, project_root=project_root
                )
                differ = copies is not None and copies_differ(*copies, project_root=project_root)
            except RuntimePathError as exc:
                raise DeployError(
                    f"Node {node.id!r} field {field_name!r} has invalid local "
                    f"runtime path {raw_path!r} ({type(exc).__name__}): {exc}. "
                    "Use a path inside the configured project.",
                    node_id=node.id,
                    field=field_name,
                    path=raw_path,
                ) from exc
            if copies is None or not differ:
                continue
            project_copy, pipeline_copy = (_project_path(path, project_root) for path in copies)
            raise DeployError(
                f"Node {node.id!r} field {field_name!r} names {raw_path!r}, which is two "
                f"different files: {project_copy} under the project root and {pipeline_copy} "
                f"in the pipeline folder. The editor reads {project_copy}, and deploy will not "
                "choose between them: delete or rename the copy you do not mean.",
                node_id=node.id,
                field=field_name,
                path=raw_path,
                project_copy=project_copy,
                pipeline_copy=pipeline_copy,
            )


def _project_path(path: Path, project_root: Path) -> str:
    """*path* as the project names it: relative to *project_root*, with forward slashes."""
    return Path(os.path.relpath(path, project_root)).as_posix()


def refuse_data_outside_pipeline_folder(
    graph: PipelineGraph, input_node_ids: list[str], pipeline_dir: Path, project_root: Path
) -> None:
    """Refuse a file the quote output reads that lies outside the pipeline folder.

    Deploy ships only data stored in the pipeline folder (``rating/``); a file outside
    it, such as the project's local ``files/data/``, is never shipped, so a deployment reading
    one could not score. A deployment drops every node the quote output does not
    depend on (the pruned graph no longer holds it: a training or optimisation branch, a
    data export, a source switch's non-live input), so those may read from anywhere.
    Each file is judged where it lies, so a link inside the folder to data outside it
    is refused too. The file judged is the one the anchoring rule names, the one the
    preview read: a project-root file is refused even when an identical copy lies at the
    same relative path in the folder, and the refusal says to point the path at that copy.
    """
    from haute._input_providers import named_source_files
    from haute._path_resolution import node_input_path_copies
    from haute._project_layout import FILES_FOLDER

    folder = pipeline_dir.resolve()
    root = project_root.resolve()
    # not contained_path: names the folder by its project path when it has one
    shown = folder.relative_to(root).as_posix() if folder.is_relative_to(root) else folder.name
    for node_id, validated in _shipped_file_inputs(graph, input_node_ids):
        raw_path = validated.get("path", "")
        source = _resolve_path(raw_path, pipeline_dir, project_root, node_id, "path")
        read = (source.resolve(), *named_source_files(validated, source))
        if all(is_contained(folder, path) for path in read):
            continue
        copies = node_input_path_copies(raw_path, pipeline_dir=pipeline_dir, project_root=root)
        if copies is not None:
            project_copy, pipeline_copy = (_project_path(path, root) for path in copies)
            raise DeployError(
                f"Data Input node {node_id!r} reads {raw_path!r}, which names {project_copy} "
                f"under the project root, outside the pipeline folder {shown!r}, and the quote "
                "output depends on it. Deploy ships only data stored in the pipeline folder, "
                f"and the editor reads the project root's copy, not the one at {pipeline_copy}: "
                f"set the path to {pipeline_copy}, or delete {project_copy}.",
                node_id=node_id,
                field="path",
                path=raw_path,
            )
        raise DeployError(
            f"Data Input node {node_id!r} reads {raw_path!r}, outside the pipeline folder "
            f"{shown!r}, and the quote output depends on it. Deploy ships only data stored "
            f"in the pipeline folder: move the file into {shown}/{FILES_FOLDER}/data, "
            "or take this input off the path to the quote output (a deployment drops every "
            "node the quote output does not depend on, so those may read data from anywhere).",
            node_id=node_id,
            field="path",
            path=raw_path,
        )


def prepare_deploy_snapshots(
    graph: PipelineGraph, input_node_ids: list[str], pipeline_dir: Path, project_root: Path
) -> None:
    """Build or refresh the snapshot of every file-based or inline Data Input deploy reads.

    Those deploy ships, and a request input's, whose first rows are the sample the
    schemas are inferred from. A clean checkout (CI) holds no input snapshot, and an
    edited file leaves its snapshot stale, so deploy prepares them itself, as an
    execution's automatic preparation does: a missing snapshot is built, a stale one
    refreshed, inside a hard memory cap. A shipped snapshot preparation could only
    reuse, its file gone or no cap to refresh it in, is refused rather than shipped. A
    lakehouse table's changes cannot be detected, so its snapshot ships as last
    imported, and database and Databricks inputs are left alone.
    """
    from haute._input_preparation import prepare_snapshots_before_reading
    from haute._polars_io_registry import data_input_is_direct, validate_data_input_config

    prepared: dict[str, dict[str, Any]] = {}
    configured: dict[str, str] = {}
    for node_id, validated in _shipped_file_inputs(graph, input_node_ids):
        snapshot_config = shipped_snapshot_config(node_id, validated, pipeline_dir, project_root)
        if snapshot_config is not None:
            # The path as deploy resolves it, so the snapshot prepared is the one bundled.
            source = _resolve_path(validated["path"], pipeline_dir, project_root, node_id, "path")
            prepared[node_id] = {**snapshot_config, "path": str(source)}
            configured[node_id] = validated["path"]
    for node in graph.nodes:
        # A config naming no provider is not a resolvable Data Input; the node builder
        # owns that refusal, as it does for an execution's preparation.
        if DEPLOY_ARTIFACTS[node.data.nodeType] != "dataset" or not node.data.config.get(
            "input_type"
        ):
            continue
        validated = validate_data_input_config(node.data.config)
        if validated["input_type"] == "inline":
            # Its records are its source: shipped or read as the sample, its snapshot is
            # built from them, with no file to resolve.
            prepared[node.id] = validated
        elif (
            node.id in input_node_ids
            and validated["input_type"] in _FILE_INPUT_TYPES
            and not data_input_is_direct(validated)
        ):
            source = _resolve_path(validated["path"], pipeline_dir, project_root, node.id, "path")
            prepared[node.id] = {**validated, "path": str(source)}
    if not prepared:
        return
    records = prepare_snapshots_before_reading(
        {
            node_id: graph.node_map[node_id].with_config(config)
            for node_id, config in prepared.items()
        },
        operation="deploy_input_snapshots",
        base_dir=pipeline_dir,
    )
    for record in records:
        reason = _UNSHIPPABLE_REUSE.get(record.warning_code or "")
        if reason is not None and record.node_id in configured:
            raw_path = configured[record.node_id]
            raise DeployError(
                f"Data Input node {record.node_id!r} reads {raw_path!r}, which {reason}, so "
                "deploy would ship the data as it was when its snapshot was built.",
                node_id=record.node_id,
                field="path",
                path=raw_path,
            )


# Preparation reuses a snapshot it could not prove current; deploy never ships one.
_UNSHIPPABLE_REUSE = {
    "cap_unavailable_stale_reused": (
        "changed since its snapshot was built, and this machine cannot install the memory "
        "cap rebuilding the snapshot needs"
    ),
    "source_unavailable": "no longer exists",
}


def _resolve_path(
    raw_path: str,
    pipeline_dir: Path,
    project_root: Path | None = None,
    node_id: str | None = None,
    field_name: str | None = None,
) -> Path:
    """Resolve ``raw_path`` to an absolute path at bundle time, as the editor reads it.

    The one anchoring rule (:func:`haute._path_resolution.resolve_node_input_path`):
    the project root's file first, then the one under ``pipeline_dir``, never outside
    the project root and never against the working directory, so the bundle ships the
    file the preview read. Every path returned is already absolute, so the manifest
    stores a deterministic, re-resolution-free pointer into the bundle. Missing files
    surface loudly via :func:`_check_exists`.
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
        return resolve_node_input_path(raw_path, pipeline_dir=pipeline_dir, project_root=root)
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


def _check_exists(path: Path, node_id: str, node_type: str) -> None:
    """Raise FileNotFoundError if the artifact file doesn't exist."""
    if not path.is_file():
        raise FileNotFoundError(f"Artifact not found for {node_type} node '{node_id}': {path}")
