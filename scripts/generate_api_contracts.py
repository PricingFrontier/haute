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
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, RootModel

from haute._execution_schemas import ExecutionStrategyDiagnosticPayload
from haute._explore_chart_contracts import ExploreChartsConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_SCHEMA_PATH = REPO_ROOT / "frontend" / "src" / "generated" / "api-contracts.schema.json"
SCHEMA_ID = "https://haute.dev/schemas/api-contracts.v1.json"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_ContractModel = type[BaseModel] | type[RootModel[Any]]


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


def _definitions_for(model: _ContractModel) -> dict[str, Any]:
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
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


def _json_value_definition() -> dict[str, Any]:
    """Return the recursive finite JSON-value grammar Pydantic cannot emit."""
    reference = {"$ref": "#/$defs/JsonValue"}
    return {
        "anyOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"items": reference, "type": "array"},
            {"additionalProperties": reference, "type": "object"},
        ]
    }


def build_contract_bundle() -> dict[str, Any]:
    """Build one deterministic JSON Schema bundle for the approved pilots."""
    definitions: dict[str, Any] = {}
    for model in (ExecutionStrategyDiagnosticPayload, ExploreChartsConfig):
        for name, definition in _definitions_for(model).items():
            _merge_definition(definitions, name=name, value=definition)

    if "JsonValue" not in definitions:
        raise RuntimeError("Explore chart schema did not declare its JsonValue extension grammar")
    definitions["JsonValue"] = _json_value_definition()

    return {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "HauteApiContractBundle",
        "type": "object",
        "properties": {
            "execution_strategy_diagnostic": {"$ref": "#/$defs/ExecutionStrategyDiagnosticPayload"},
            "explore_charts": {"$ref": "#/$defs/ExploreChartsConfig"},
        },
        "required": [
            "execution_strategy_diagnostic",
            "explore_charts",
        ],
        "additionalProperties": False,
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
