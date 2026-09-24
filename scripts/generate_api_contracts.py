"""Generate the canonical browser-contract JSON Schema bundle.

The Python stage owns only Pydantic-to-JSON-Schema generation. The frontend
stage consumes the committed bundle to generate TypeScript declarations and
standalone runtime validators without importing Python during an npm-only CI
job.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, RootModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue
from pydantic_core import core_schema

from haute._execution_schemas import ExecutionStrategyDiagnosticPayload
from haute._explore_chart_contracts import ExploreChartsConfig
from haute.schemas import (
    BandingStatsResponse,
    BrowseFilesResponse,
    CatalogListResponse,
    DispersionEstimateResponse,
    DispersionEstimateStatusResponse,
    EditorIdentitiesResponse,
    ExecutionSettings,
    ExplorePivotMembersResponse,
    ExplorePivotRunResponse,
    ExplorePivotStatusResponse,
    GitArchiveResponse,
    GitBindStorageResponse,
    GitBranchAwayResponse,
    GitCommitContext,
    GitCommitResponse,
    GitCreateWorkingBranchResponse,
    GitDeleteBranchResponse,
    GitFastForwardResponse,
    GitForkStorageResponse,
    GitGraphResponse,
    GitLedgerSavesResponse,
    GitMilestoneFork,
    GitMilestonesResponse,
    GitMoveResponse,
    GitPrefs,
    GitPushRejection,
    GitPushResponse,
    GitRemotesResponse,
    GitRestoreResponse,
    GitSetIdentityResponse,
    GitSetWorkingBranchResponse,
    GitUndeleteResponse,
    GitUpstreamStatusResponse,
    GitWorkingBranchesResponse,
    GitWorkingBranchResponse,
    IoCapabilitiesResponse,
    LogExperimentResponse,
    MlflowDestinationsResponse,
    MlflowExperimentList,
    MlflowModelList,
    MlflowModelVersionList,
    MlflowRunList,
    MlflowSettingsResponse,
    MlflowTestConnectionResponse,
    ModellingGpuStatusResponse,
    ModelSaveDestinationResponse,
    NodeDataProfileResponse,
    OptimiserApplyResponse,
    OptimiserEstimateResponse,
    OptimiserFrontierAutoRangeStartResponse,
    OptimiserFrontierAutoRangeStatusResponse,
    OptimiserFrontierSelectResponse,
    OptimiserFrontierStatusResponse,
    OptimiserMlflowLogResponse,
    OptimiserSaveResponse,
    OptimiserSolveResponse,
    OptimiserStatusResponse,
    PolarsStepsRenderResponse,
    RatingLevelsResponse,
    SaveModelResponse,
    SchemaListResponse,
    SessionStatusResponse,
    TableListResponse,
    TrainEstimateResponse,
    TrainResponse,
    TrainStatusResponse,
    UtilityDeleteResponse,
    UtilityListResponse,
    UtilityReadResponse,
    UtilityWriteResponse,
    WarehouseListResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_SCHEMA_PATH = REPO_ROOT / "frontend" / "src" / "generated" / "api-contracts.schema.json"
SCHEMA_ID = "https://haute.dev/schemas/api-contracts.v1.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_ContractModel = type[BaseModel] | type[RootModel[Any]]

RESPONSE_GROUPS_KEYWORD = "x-haute-response-groups"

# Response models the browser parses with generated validators, by the module
# group whose hand-written guards they replace. The frontend generator emits one
# validator module per group with one export per model.
RESPONSE_CONTRACT_GROUPS: dict[str, tuple[type[BaseModel], ...]] = {
    "utility": (
        UtilityListResponse,
        UtilityReadResponse,
        UtilityWriteResponse,
        UtilityDeleteResponse,
    ),
    "databricks": (
        WarehouseListResponse,
        CatalogListResponse,
        SchemaListResponse,
        TableListResponse,
    ),
    "mlflow": (
        MlflowDestinationsResponse,
        MlflowSettingsResponse,
        MlflowTestConnectionResponse,
        MlflowExperimentList,
        MlflowRunList,
        MlflowModelList,
        MlflowModelVersionList,
    ),
    "modelling": (
        ModellingGpuStatusResponse,
        TrainEstimateResponse,
        DispersionEstimateResponse,
        DispersionEstimateStatusResponse,
        LogExperimentResponse,
        ModelSaveDestinationResponse,
        SaveModelResponse,
    ),
    "git": (
        GitWorkingBranchResponse,
        GitSetWorkingBranchResponse,
        GitWorkingBranchesResponse,
        GitCreateWorkingBranchResponse,
        GitSetIdentityResponse,
        GitPrefs,
        GitMoveResponse,
        GitCommitResponse,
        GitCommitContext,
        GitMilestonesResponse,
        GitGraphResponse,
        GitLedgerSavesResponse,
        GitRestoreResponse,
        GitArchiveResponse,
        GitDeleteBranchResponse,
        GitUndeleteResponse,
        GitRemotesResponse,
        GitPushResponse,
        GitFastForwardResponse,
        GitBranchAwayResponse,
        GitBindStorageResponse,
        GitForkStorageResponse,
        GitUpstreamStatusResponse,
        # 409 advisory bodies the push and milestone controls read.
        GitPushRejection,
        GitMilestoneFork,
    ),
    "training": (
        TrainResponse,
        TrainStatusResponse,
    ),
    "explore": (
        ExplorePivotRunResponse,
        ExplorePivotStatusResponse,
        ExplorePivotMembersResponse,
        NodeDataProfileResponse,
    ),
    "factors": (
        BandingStatsResponse,
        RatingLevelsResponse,
    ),
    "io": (IoCapabilitiesResponse,),
    "session": (
        SessionStatusResponse,
        BrowseFilesResponse,
    ),
    "editor": (
        EditorIdentitiesResponse,
        PolarsStepsRenderResponse,
        ExecutionSettings,
    ),
    "optimiser": (
        OptimiserSolveResponse,
        OptimiserEstimateResponse,
        OptimiserStatusResponse,
        OptimiserApplyResponse,
        OptimiserSaveResponse,
        OptimiserMlflowLogResponse,
        OptimiserFrontierStatusResponse,
        OptimiserFrontierAutoRangeStartResponse,
        OptimiserFrontierAutoRangeStatusResponse,
        OptimiserFrontierSelectResponse,
    ),
}


class _ResponseJsonSchema(GenerateJsonSchema):
    """Describe a response as the server serializes it.

    FastAPI always sends every declared field, defaults included, so each one
    is required; only a field the model drops from its output (``exclude_if``)
    is optional.
    """

    def field_is_required(
        self,
        field: core_schema.ModelField | core_schema.DataclassField | core_schema.TypedDictField,
        total: bool,
    ) -> bool:
        if field["type"] == "typed-dict-field":
            return super().field_is_required(field, total)
        return field.get("serialization_exclude_if") is None

    def model_field_schema(self, schema: core_schema.ModelField) -> JsonSchemaValue:
        json_schema = super().model_field_schema(schema)
        exclude_if = schema.get("serialization_exclude_if")
        if self.mode != "serialization" or exclude_if is None or not exclude_if(None):
            return json_schema
        # The field is dropped from the output whenever it is None, so the
        # response never carries null for it.
        return _without_null_branch(json_schema)


def _without_null_branch(json_schema: JsonSchemaValue) -> JsonSchemaValue:
    alternatives = json_schema.get("anyOf")
    if not isinstance(alternatives, list) or {"type": "null"} not in alternatives:
        return json_schema
    kept = [alternative for alternative in alternatives if alternative != {"type": "null"}]
    rest = {key: value for key, value in json_schema.items() if key not in {"anyOf", "default"}}
    if len(kept) == 1:
        return {**kept[0], **rest}
    return {**rest, "anyOf": kept}


def _merge_definition(
    definitions: dict[str, Any],
    *,
    name: str,
    value: Mapping[str, Any],
) -> None:
    candidate = dict(value)
    previous = definitions.setdefault(name, candidate)
    if previous != candidate:
        raise RuntimeError(f"conflicting generated JSON Schema definition: {name}")


_SERIALIZED_SUFFIX = "Output"


def _renamed_ref(reference: str, renames: Mapping[str, str]) -> str:
    name = reference.removeprefix("#/$defs/")
    return f"#/$defs/{renames.get(name, name)}"


def _with_renamed_refs(value: Any, renames: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                _renamed_ref(item, renames)
                if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/")
                else _with_renamed_refs(item, renames)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_with_renamed_refs(item, renames) for item in value]
    return value


def _local_references(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                found.add(item.removeprefix("#/$defs/"))
            else:
                found |= _local_references(item)
        return found
    if isinstance(value, list):
        return set().union(*(_local_references(item) for item in value))
    return set()


def _serialized_definitions(
    response_definitions: Mapping[str, Any],
    bundle_definitions: Mapping[str, Any],
) -> dict[str, Any]:
    """Name a response's serialization-mode copies apart from validation-mode ones.

    A model shared with a validation-mode contract (the execution-strategy
    pilot, whose other routes omit its defaulted fields) serializes with every
    field required, so it is a different contract and gets its own
    ``<Name>Output`` definition. Anything that refers to a renamed definition
    differs too and is renamed with it.
    """
    renames: dict[str, str] = {}

    def conflicts(name: str) -> bool:
        rewritten = _with_renamed_refs(response_definitions[name], renames)
        return name in bundle_definitions and bundle_definitions[name] != rewritten

    # A definition is compared only once the ones it refers to are named, so a
    # copy an earlier response already stored with renamed references matches.
    pending = set(response_definitions)
    while pending:
        ready = sorted(
            name
            for name in pending
            if not (_local_references(response_definitions[name]) - {name}) & pending
        )
        if not ready:
            # Mutually recursive definitions: rename until nothing conflicts.
            while conflicting := {name for name in pending - set(renames) if conflicts(name)}:
                renames.update({name: f"{name}{_SERIALIZED_SUFFIX}" for name in conflicting})
            break
        for name in ready:
            if conflicts(name):
                renames[name] = f"{name}{_SERIALIZED_SUFFIX}"
        pending.difference_update(ready)
    rewritten = {
        name: _with_renamed_refs(definition, renames)
        for name, definition in response_definitions.items()
    }
    serialized: dict[str, Any] = {}
    for name, definition in rewritten.items():
        new_name = renames.get(name, name)
        if new_name != name and definition.get("title") == name:
            definition = {**definition, "title": new_name}
        serialized[new_name] = definition
    return serialized


def _definitions_for(
    model: _ContractModel,
    *,
    mode: JsonSchemaMode = "validation",
    schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
) -> dict[str, Any]:
    schema = model.model_json_schema(
        ref_template="#/$defs/{model}",
        mode=mode,
        schema_generator=schema_generator,
    )
    nested = schema.pop("$defs", {})
    if not isinstance(nested, dict):
        raise RuntimeError(f"{model.__name__} generated a non-object $defs section")
    definitions: dict[str, Any] = {}
    for name, definition in nested.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            raise RuntimeError(f"{model.__name__} generated an invalid definition")
        _merge_definition(definitions, name=name, value=definition)
    _merge_definition(definitions, name=model.__name__, value=schema)
    return definitions


def build_contract_bundle() -> dict[str, Any]:
    """Build one deterministic JSON Schema bundle for the pilots and response groups."""
    definitions: dict[str, Any] = {}
    for model in (
        ExecutionStrategyDiagnosticPayload,
        ExploreChartsConfig,
    ):
        for name, definition in _definitions_for(model).items():
            _merge_definition(definitions, name=name, value=definition)

    properties: dict[str, Any] = {
        "execution_strategy_diagnostic": {"$ref": "#/$defs/ExecutionStrategyDiagnosticPayload"},
        "explore_charts": {"$ref": "#/$defs/ExploreChartsConfig"},
    }
    for group, models in RESPONSE_CONTRACT_GROUPS.items():
        # The group names a generated module file.
        if re.fullmatch(r"[a-z][a-z0-9-]*", group) is None or not models:
            raise RuntimeError(f"invalid response contract group: {group!r}")
        for response_model in models:
            response_definitions = _serialized_definitions(
                _definitions_for(
                    response_model,
                    mode="serialization",
                    schema_generator=_ResponseJsonSchema,
                ),
                definitions,
            )
            if response_model.__name__ not in response_definitions:
                raise RuntimeError(f"response root was renamed: {response_model.__name__}")
            for name, definition in response_definitions.items():
                _merge_definition(definitions, name=name, value=definition)
            root = response_model.__name__
            if root in properties:
                raise RuntimeError(f"response contract listed twice: {root}")
            properties[root] = {"$ref": f"#/$defs/{root}"}

    return {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "HauteApiContractBundle",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
        # The frontend generator emits one validator module per group from this.
        RESPONSE_GROUPS_KEYWORD: {
            group: [model.__name__ for model in models]
            for group, models in RESPONSE_CONTRACT_GROUPS.items()
        },
        "$defs": definitions,
    }


def render_contract_bundle() -> str:
    """Render the bundle with a stable review-friendly byte representation."""
    return (
        json.dumps(
            build_contract_bundle(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the committed schema is stale",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=GENERATED_SCHEMA_PATH,
        help="schema output path (defaults to the committed frontend artifact)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected = render_contract_bundle()
    output = args.output.resolve()
    if args.check:
        if not output.is_file():
            print(f"generated API contract is missing: {output}", file=sys.stderr)
            return 1
        if output.read_text(encoding="utf-8") != expected:
            print(
                "generated API contract is stale; run "
                "`uv run python scripts/generate_api_contracts.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    _write_atomic(output, expected)
    print(f"wrote {output.relative_to(REPO_ROOT) if output.is_relative_to(REPO_ROOT) else output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
