"""The Model Scoring node's model source, parsed once from its config.

Every consumer that loads a Model Scoring node's model — the executor's builder,
the column-contract planner, the standalone ``score_from_config``, the trace
explanation, and the deploy bundler and scorer — parses the node config through
:func:`parse_model_source` and loads through :func:`load_scoring_model`. The
source defaults and validation rules live only here, so one config cannot load a
different model in one context than in another.

The fields are named after the config keys they come from, so the typed
node-config models (``PCFG-R07``) absorb these classes as the Model Scoring
source slice rather than adding a second config model.

Every loaded model is bound to the feature contract it scores under
(:func:`haute._mlflow_io.bind_feature_contract`) before any consumer sees it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias

from haute.errors import ConfigError

if TYPE_CHECKING:
    from haute._mlflow_io import ScoringModel
    from haute._mlflow_utils import ResolvedBackend

MODEL_SOURCE_TYPES: tuple[str, ...] = ("run", "registered", "file")


class IncompleteModelSourceError(ConfigError):
    """A source type is chosen but its identifying field is empty.

    Interactive and standalone consumers treat it like any ``ConfigError``. The
    deploy bundler alone treats it as an unconfigured node and skips bundling;
    the deploy scorer then refuses the node as an identity passthrough.
    """


@dataclass(frozen=True, slots=True)
class RunModelSource:
    """A model logged to an MLflow run."""

    source_type: ClassVar[Literal["run"]] = "run"

    run_id: str
    artifact_path: str
    mlflow_destination: str


@dataclass(frozen=True, slots=True)
class RegisteredModelSource:
    """A version of an MLflow registered model, by number, ``latest`` or alias."""

    source_type: ClassVar[Literal["registered"]] = "registered"

    registered_model: str
    version: str
    alias: str
    artifact_path: str
    mlflow_destination: str


@dataclass(frozen=True, slots=True)
class FileModelSource:
    """A model file in the project, as the node configures it."""

    source_type: ClassVar[Literal["file"]] = "file"

    model_path: str


ModelSource: TypeAlias = RunModelSource | RegisteredModelSource | FileModelSource


def _optional_string(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(
            f"modelScore node has a non-string {key}",
            field=key,
            value_type=type(value).__name__,
        )
    return value


def _identifier(config: Mapping[str, Any], key: str, source_type: str) -> str:
    value = _optional_string(config, key)
    if not value:
        raise IncompleteModelSourceError(
            f"modelScore node is misconfigured: sourceType={source_type!r} but {key} is empty",
            sourceType=source_type,
            missing_field=key,
        )
    return value


def parse_model_source(config: Mapping[str, Any]) -> ModelSource | None:
    """The node's model source, or ``None`` for a node with no source chosen.

    Raises:
        IncompleteModelSourceError: the chosen source's identifying field
            (``run_id``, ``registered_model`` or ``model_path``) is empty.
        ConfigError: ``sourceType`` is not a supported source, a source field
            has the wrong type, or a registered source names a version beside
            an alias (or a malformed alias).
    """
    source_type = config.get("sourceType", "")
    if source_type == "":
        return None
    if not isinstance(source_type, str):
        raise ConfigError(
            "modelScore node has a non-string sourceType",
            sourceType=source_type,
        )
    if source_type not in MODEL_SOURCE_TYPES:
        raise ConfigError(
            "modelScore node has an unsupported sourceType",
            sourceType=source_type,
            supported_source_types=list(MODEL_SOURCE_TYPES),
        )
    if source_type == "file":
        # A file source never consults MLflow.
        return FileModelSource(model_path=_identifier(config, "model_path", source_type))
    artifact_path = _optional_string(config, "artifact_path")
    destination = str(config.get("mlflow_destination") or "")
    if source_type == "run":
        return RunModelSource(
            run_id=_identifier(config, "run_id", source_type),
            artifact_path=artifact_path,
            mlflow_destination=destination,
        )

    from haute._config_validation import validate_registered_model_alias
    from haute._types import NodeType

    registered_model = _identifier(config, "registered_model", source_type)
    validate_registered_model_alias(NodeType.MODEL_SCORE, config)
    alias = _optional_string(config, "alias")
    version = _optional_string(config, "version") or ("" if alias else "latest")
    return RegisteredModelSource(
        registered_model=registered_model,
        version=version,
        alias=alias,
        artifact_path=artifact_path,
        mlflow_destination=destination,
    )


def require_model_source(config: Mapping[str, Any]) -> ModelSource:
    """The node's model source, refusing a node with no source chosen."""
    source = parse_model_source(config)
    if source is None:
        raise ConfigError(
            "modelScore node has no model source; set sourceType to 'run', 'registered' or 'file'.",
            missing_field="sourceType",
            supported_source_types=list(MODEL_SOURCE_TYPES),
        )
    return source


def _resolve_project_file(
    raw_path: str,
    base_dir: str | Path | None,
    kind: str,
    *,
    project_root: str | Path | None = None,
) -> Path:
    """Resolve a configured project file through the runtime sandbox; it must exist.

    The project is preferred over the pipeline directory, as for every runtime
    input, so the preview, a standalone run and the deploy bundler pick the same
    file. *project_root* defaults to the execution-scoped project root.
    """
    from haute._path_resolution import current_runtime_project_root, resolve_runtime_file_path

    resolved = resolve_runtime_file_path(
        raw_path,
        pipeline_dir=base_dir,
        project_root=project_root if project_root is not None else current_runtime_project_root(),
        prefer="project",
        enforce_project_root=True,
    )
    if not resolved.is_file():
        raise ConfigError(
            f"The {kind} {raw_path!r} does not exist in the project.",
            path=raw_path,
        )
    return resolved


def resolve_model_file(
    model_path: str,
    base_dir: str | Path | None,
    *,
    project_root: str | Path | None = None,
) -> Path:
    """The file a file source names, resolved through the runtime sandbox.

    Raises:
        RuntimePathOutsideProjectError: the path resolves outside the project.
        ConfigError: the file does not exist, or no model family loads its suffix.
    """
    from haute._model_flavors import family_for_artifact

    # An unsupported suffix is refused before the file is looked for.
    family_for_artifact(model_path)
    return _resolve_project_file(model_path, base_dir, "model file", project_root=project_root)


def model_contract_path(
    source: FileModelSource,
    feature_contract_path: str | None,
    base_dir: str | Path | None,
    *,
    project_root: str | Path | None = None,
) -> Path | None:
    """The contract a file source scores under: explicit, else saved beside the model.

    ``None`` when there is no explicit contract and none beside the model. A
    sibling is a runtime input like the explicit contract: one that resolves
    outside the project (through a symlink) is refused, never read.
    """
    from haute._mlflow_io import model_contract_candidates

    if feature_contract_path:
        return _resolve_project_file(
            feature_contract_path, base_dir, "feature contract", project_root=project_root
        )
    model_file = resolve_model_file(source.model_path, base_dir, project_root=project_root)
    for candidate in model_contract_candidates(model_file):
        if candidate.is_file():
            return _resolve_project_file(
                str(candidate), base_dir, "feature contract", project_root=project_root
            )
    return None


def scoring_contract_path(
    source: ModelSource, config: Mapping[str, Any], base_dir: str | Path | None
) -> str | None:
    """The contract a node's model scores under, resolved once for every consumer.

    The explicit ``feature_contract_path``, or for a file source the contract
    saved beside the model; ``None`` when there is neither.
    """
    explicit = config.get("feature_contract_path") or None
    if explicit is not None and not isinstance(explicit, str):
        raise ConfigError(
            "modelScore node has a non-string feature_contract_path",
            field="feature_contract_path",
            value_type=type(explicit).__name__,
        )
    if isinstance(source, FileModelSource):
        path = model_contract_path(source, explicit, base_dir)
    else:
        path = _resolve_project_file(explicit, base_dir, "feature contract") if explicit else None
    return str(path) if path is not None else None


def model_source_name(source: ModelSource) -> str:
    """How errors name the model *source* loads."""
    if isinstance(source, FileModelSource):
        return repr(source.model_path)
    if isinstance(source, RunModelSource):
        return f"from MLflow run {source.run_id!r}"
    return f"registered model {source.registered_model!r}"


@dataclass(frozen=True, slots=True)
class BoundModel:
    """A loaded model and the contract it is bound to (``None`` without one)."""

    scoring_model: ScoringModel
    contract_path: str | None


def load_scoring_model(
    source: ModelSource,
    task: str,
    *,
    feature_contract_path: str | None = None,
    base_dir: str | Path | None = None,
) -> ScoringModel:
    """Load the model *source* names as *task*, bound to its feature contract."""
    return load_bound_model(
        source, task, feature_contract_path=feature_contract_path, base_dir=base_dir
    ).scoring_model


def load_bound_model(
    source: ModelSource,
    task: str,
    *,
    feature_contract_path: str | None = None,
    base_dir: str | Path | None = None,
) -> BoundModel:
    """Load the model *source* names as *task*, bound to its feature contract.

    *feature_contract_path* is the contract the node scores under (see
    :func:`scoring_contract_path`); a file source without one discovers the
    contract saved beside the model, and a run or registered CatBoost model
    whose offset its file does not declare is bound to the contract its run
    logged beside it. That model and contract are read from one concrete run,
    so an alias or ``latest`` that moves between the two reads cannot pair one
    version's model with another version's contract. *base_dir* is the pipeline
    directory relative file paths may also resolve against. The returned
    contract path is the one scoring enforces categorical domains from.
    """
    from haute import _mlflow_io
    from haute.modelling._feature_contract import load_contract_cached

    if isinstance(source, FileModelSource):
        model_file = resolve_model_file(source.model_path, base_dir)
        contract_file = feature_contract_path or model_contract_path(source, None, base_dir)
        contract_arg = str(contract_file) if contract_file is not None else None
        scoring_model = _mlflow_io.load_local_model_cached(
            str(model_file), task, contract_arg, model_name=model_source_name(source)
        )
        return BoundModel(scoring_model, contract_arg)

    from haute._mlflow_utils import resolve_backend

    # One backend for every read below: a settings change mid-load cannot pair
    # one backend's model with another's contract.
    backend = resolve_backend(source.mlflow_destination)
    scoring_model = _load_mlflow_source(source, task, backend)
    contract_path = feature_contract_path
    if contract_path is None and not scoring_model.offset_declared:
        run_source = _concrete_run_source(source, backend)
        if run_source != source:
            scoring_model = _load_mlflow_source(run_source, task, backend)
        if not scoring_model.offset_declared:
            contract_path = _mlflow_io.run_logged_contract_path(
                run_id=run_source.run_id,
                artifact_path=run_source.artifact_path,
                backend=backend,
            )
    contract = load_contract_cached(contract_path) if contract_path is not None else None
    bound = _mlflow_io.bind_feature_contract(
        scoring_model, contract, model_name=model_source_name(source)
    )
    return BoundModel(bound, contract_path)


def _load_mlflow_source(
    source: RunModelSource | RegisteredModelSource, task: str, backend: ResolvedBackend
) -> ScoringModel:
    from haute import _mlflow_io

    if isinstance(source, RunModelSource):
        return _mlflow_io.load_mlflow_model(
            source_type=source.source_type,
            run_id=source.run_id,
            artifact_path=source.artifact_path,
            task=task,
            destination=source.mlflow_destination,
            backend=backend,
        )
    return _mlflow_io.load_mlflow_model(
        source_type=source.source_type,
        registered_model=source.registered_model,
        version=source.version,
        alias=source.alias,
        artifact_path=source.artifact_path,
        task=task,
        destination=source.mlflow_destination,
        backend=backend,
    )


def _concrete_run_source(
    source: RunModelSource | RegisteredModelSource, backend: ResolvedBackend
) -> RunModelSource:
    """The source as one run and artifact, resolving a registered version or alias once."""
    from haute import _mlflow_io

    if isinstance(source, RunModelSource):
        if source.artifact_path:
            return source
        run_id, artifact = _mlflow_io.resolve_run_artifact(
            source_type=source.source_type,
            run_id=source.run_id,
            backend=backend,
        )
    else:
        run_id, artifact = _mlflow_io.resolve_run_artifact(
            source_type=source.source_type,
            registered_model=source.registered_model,
            version=source.version,
            alias=source.alias,
            artifact_path=source.artifact_path,
            backend=backend,
        )
    return RunModelSource(
        run_id=run_id, artifact_path=artifact, mlflow_destination=source.mlflow_destination
    )


@dataclass(frozen=True, slots=True)
class ModelFileInspection:
    """What a model file in the project scores as, for the node editor."""

    model_path: str
    flavor: str
    label: str
    task: str | None
    features: list[str]
    categorical_features: list[str]
    offset_column: str | None
    offset_link: str | None
    contract_path: str | None


def inspect_model_file(
    model_path: str,
    *,
    feature_contract_path: str | None = None,
    base_dir: str | Path | None = None,
) -> ModelFileInspection:
    """Load a project model file and its contract exactly as a file source scores it.

    The task is the contract's when there is one; otherwise the model loads as
    a regression and, when its loader refuses that naming the task it was
    trained for, as that task. A file the node would refuse raises the same
    error the node would.
    """
    from haute._mlflow_io import load_local_model_cached
    from haute._model_flavors import model_family
    from haute._path_resolution import current_runtime_project_root
    from haute.modelling._feature_contract import load_contract_cached

    source = FileModelSource(model_path=model_path)
    model_file = resolve_model_file(model_path, base_dir)
    contract_file = model_contract_path(source, feature_contract_path, base_dir)
    task = load_contract_cached(contract_file).task if contract_file is not None else None
    contract_arg = str(contract_file) if contract_file is not None else None
    name = model_source_name(source)
    try:
        scoring_model = load_local_model_cached(
            str(model_file), task or "regression", contract_arg, model_name=name
        )
    except ConfigError as exc:
        trained = exc.context.get("trained_task")
        if task is not None or trained not in ("regression", "classification"):
            raise
        task = trained
        scoring_model = load_local_model_cached(
            str(model_file), task, contract_arg, model_name=name
        )
    family = model_family(scoring_model.flavor)
    if task is None and family.self_describing:
        task = scoring_model.raw_model.task
    root = current_runtime_project_root()
    return ModelFileInspection(
        model_path=model_path,
        flavor=family.flavor,
        label=family.label,
        task=task,
        features=list(scoring_model.feature_names),
        categorical_features=sorted(scoring_model.cat_feature_names),
        offset_column=scoring_model.offset_column,
        offset_link=scoring_model.offset_link if scoring_model.offset_column else None,
        contract_path=(
            contract_file.resolve().relative_to(root).as_posix()
            if contract_file is not None
            else None
        ),
    )
