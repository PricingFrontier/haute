"""Stage diagnostics stored once: split results into references; hydrate them back.

A stage result keeps its small fields inline and moves its diagnostics into
content-addressed objects ("Stage storage (version 2)" in `specs/modelling/low-level.md`):
- every snapshot in `staged.stages` becomes a `snapshot/<sha256>` object, the selected
  stage included;
- dense fields (`tboost_tables` and each loss history) inside snapshots and at the top
  level become `table/<sha256>` and `history/<sha256>` objects.

References appear **only at those positions**, so result data elsewhere is never read as
one. A reference is `{"$object": "<kind>/<sha256>"}`. A table reference also carries the
table's `summary` (`table_summary()` of it), so compact reads never load the bank.
Identical content gets an identical name, so each snapshot, bank and history is stored
once however many results share it.

`hydrate(split(x)) == x` for every result form: point results (`TrainResult` fields),
public responses and store results (`{"response": ...}`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal

from haute.errors import HauteValidationError
from haute.modelling._stage_objects import canonical_json
from haute.modelling._stage_views import compact_response, table_summary

REFERENCE = "$object"
SUMMARY = "summary"
_DENSE: dict[str, Literal["table", "history"]] = {
    "tboost_tables": "table",
    "loss_history": "history",
    "validation_loss_history": "history",
}
_NAME = re.compile(r"(snapshot|table|history)/[a-f0-9]{64}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_ENTRY = {"term", "features", "order", "cells", "shape_digest"}

Put = Callable[[str, bytes], object]
Get = Callable[[str], Any]


def without_nulls(value: Any) -> Any:
    """*value* with every null member dropped, recursively.

    Worker responses are serialised with ``exclude_none``, while artifact records keep
    explicit nulls. Both sides of a response-versus-artifact comparison pass through
    this, so the comparison sees the same shape.
    """
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    return value


def _reference(kind: str, value: Any, put: Put) -> dict[str, Any]:
    payload = canonical_json(value)
    name = f"{kind}/{hashlib.sha256(payload).hexdigest()}"
    put(name, payload)
    reference: dict[str, Any] = {REFERENCE: name}
    if kind == "table":
        reference[SUMMARY] = table_summary(value)
    return reference


def _name(value: Any, kind: str) -> str:
    """Validate a reference at a position that must hold one of *kind*; return its name."""
    expected = {REFERENCE, SUMMARY} if kind == "table" else {REFERENCE}
    if not isinstance(value, dict) or set(value) != expected:
        raise HauteValidationError(f"Invalid stage {kind} reference")
    name = value[REFERENCE]
    match = _NAME.fullmatch(name) if isinstance(name, str) else None
    if match is None or match.group(1) != kind:
        raise HauteValidationError(f"Invalid stage {kind} reference")
    if kind == "table" and not _valid_summary(value[SUMMARY]):
        raise HauteValidationError("Invalid stage table summary")
    return str(name)


def _count(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _valid_summary(summary: Any) -> bool:
    """The `StageTableSummary` response contract, checked without loading the bank."""
    return (
        isinstance(summary, dict)
        and set(summary) == {"tables", "factored"}
        and _count(summary["factored"], 0)
        and isinstance(summary["tables"], list)
        and all(
            isinstance(entry, dict)
            and set(entry) == _ENTRY
            and isinstance(entry["term"], str)
            and isinstance(entry["features"], list)
            and all(isinstance(feature, str) for feature in entry["features"])
            and _count(entry["order"], 1)
            and _count(entry["cells"], 0)
            and isinstance(entry["shape_digest"], str)
            and _DIGEST.fullmatch(entry["shape_digest"]) is not None
            for entry in summary["tables"]
        )
    )


def _split_dense(value: Mapping[str, Any], put: Put) -> dict[str, Any]:
    result = dict(value)
    for field, kind in _DENSE.items():
        if result.get(field):
            result[field] = _reference(kind, result[field], put)
    return result


def _split_body(value: Mapping[str, Any], put: Put) -> dict[str, Any]:
    result = _split_dense(value, put)
    staged = result.get("staged")
    if isinstance(staged, Mapping) and isinstance(staged.get("stages"), Mapping):
        result["staged"] = {
            **staged,
            "stages": {
                name: _reference("snapshot", _split_dense(snapshot, put), put)
                for name, snapshot in staged["stages"].items()
            },
        }
    return result


def _is_store_result(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("response"), Mapping)


def split(value: Mapping[str, Any], put: Put) -> dict[str, Any]:
    """Move a result's diagnostics into objects via *put*(name, bytes); return the envelope."""
    if _is_store_result(value):
        return {**value, "response": _split_body(value["response"], put)}
    return _split_body(value, put)


def _hydrate_dense(value: Mapping[str, Any], get: Get, *, compact: bool) -> dict[str, Any]:
    result = dict(value)
    for field, kind in _DENSE.items():
        item = result.get(field)
        if not item:
            continue  # absent and empty values stay inline
        name = _name(item, kind)
        if kind == "table" and compact:
            # As `compact_response` does from the bank: the bank's summary replaces any stored one.
            result["table_summary"] = item[SUMMARY]
            result[field] = None
        else:
            result[field] = get(name)
    return result


def _hydrate_body(value: Mapping[str, Any], get: Get, *, compact: bool) -> dict[str, Any]:
    result = _hydrate_dense(value, get, compact=compact)
    staged = result.get("staged")
    if isinstance(staged, Mapping) and isinstance(staged.get("stages"), Mapping):
        stages = {}
        for name, reference in staged["stages"].items():
            snapshot = get(_name(reference, "snapshot"))
            if not isinstance(snapshot, dict):
                raise HauteValidationError("Invalid stage snapshot object")
            stages[name] = _hydrate_dense(snapshot, get, compact=compact)
        result["staged"] = {**staged, "stages": stages}
    return compact_response(result) if compact else result


def _envelope(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HauteValidationError("Invalid stage result envelope")
    return value


def hydrate(envelope: Mapping[str, Any], get: Get, *, compact: bool = False) -> dict[str, Any]:
    """Resolve an envelope's references through *get*(name) -> decoded JSON.

    Compact hydration leaves tables unloaded and applies the compact projection.
    """
    envelope = _envelope(envelope)
    if _is_store_result(envelope):
        return {**envelope, "response": _hydrate_body(envelope["response"], get, compact=compact)}
    return _hydrate_body(envelope, get, compact=compact)


def closure(envelope: Mapping[str, Any], get: Get) -> set[str]:
    """Every object name an envelope needs, including those its snapshots reference."""
    envelope = _envelope(envelope)
    body = envelope["response"] if _is_store_result(envelope) else envelope

    def dense(value: Mapping[str, Any]) -> set[str]:
        return {_name(value[field], kind) for field, kind in _DENSE.items() if value.get(field)}

    names = dense(body)
    staged = body.get("staged")
    if isinstance(staged, Mapping) and isinstance(staged.get("stages"), Mapping):
        for reference in staged["stages"].values():
            name = _name(reference, "snapshot")
            names |= {name, *dense(get(name))}
    return names


def same_staged(
    envelope_staged: Mapping[str, Any], public_staged: Mapping[str, Any], get: Get
) -> bool:
    """Whether reference-bearing staged metadata describes a public `staged` response.

    Both sides are compared hydrated and without nulls: the public schema adds explicit
    nulls for optional fields the artifact omits.
    """
    hydrated = _hydrate_body({"staged": dict(envelope_staged)}, get, compact=False)["staged"]
    return bool(without_nulls(hydrated) == without_nulls(dict(public_staged)))
