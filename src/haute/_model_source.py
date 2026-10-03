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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias

from haute.errors import ConfigError

if TYPE_CHECKING:
    from haute._mlflow_io import ScoringModel

MODEL_SOURCE_TYPES: tuple[str, ...] = ("run", "registered")


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


ModelSource: TypeAlias = RunModelSource | RegisteredModelSource


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
            (``run_id`` or ``registered_model``) is empty.
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
            "modelScore node has no model source; set sourceType to 'run' or 'registered'.",
            missing_field="sourceType",
            supported_source_types=list(MODEL_SOURCE_TYPES),
        )
    return source


def load_scoring_model(source: ModelSource, task: str) -> ScoringModel:
    """Load the model *source* names, as the *task* the node scores it for."""
    from haute import _mlflow_io

    if isinstance(source, RunModelSource):
        return _mlflow_io.load_mlflow_model(
            source_type=source.source_type,
            run_id=source.run_id,
            artifact_path=source.artifact_path,
            task=task,
            destination=source.mlflow_destination,
        )
    return _mlflow_io.load_mlflow_model(
        source_type=source.source_type,
        registered_model=source.registered_model,
        version=source.version,
        alias=source.alias,
        artifact_path=source.artifact_path,
        task=task,
        destination=source.mlflow_destination,
    )
