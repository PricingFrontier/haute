"""The assistant's validated node cards: one teaching card per node type.

A card is library content, never project data: for an authorable node type it
holds a minimal and a realistic configuration with the meaning of each field,
and the input frames those configurations assume; for a type the assistant
cannot author it states why. Each card file also carries a ``fixture`` block
of tiny synthetic rows and files from which the test suite builds a project,
dry-runs and applies every configuration through the application service and
executes it, so a card that drifts from its validator fails CI. The fixture is
test evidence and never reaches the model: :func:`node_card` returns the card
without it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from functools import cache
from importlib import resources
from typing import Any

from haute._types import NodeType

_ASSET_PACKAGE = "haute.assistant"
_CARDS_DIR = ("assets", "node_cards")
#: The two configurations every authorable card teaches, in this order.
CARD_CONFIG_NAMES = ("minimal", "realistic")

_AUTHORABLE_KEYS = frozenset({"node_type", "authorable", "fields", "inputs", "configs", "fixture"})
_NOT_AUTHORABLE_KEYS = frozenset({"node_type", "authorable", "note"})
_INPUT_KEYS = frozenset({"edge", "columns", "source_handle", "target_handle"})
_CONFIG_KEYS = frozenset({"name", "intent", "config", "produces", "inputs"})
_FIXTURE_KEYS = frozenset({"frames", "files", "upstream", "downstream", "model_run", "expect"})
_FRAME_KEYS = frozenset({"rows", "dates"})
_MODEL_RUN_KEYS = frozenset({"frame", "features", "artifact"})


class NodeCardError(RuntimeError):
    """A packaged node card is missing or malformed: a packaging defect."""


def _require_keys(value: object, allowed: frozenset[str], *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NodeCardError(f"{where} must be an object.")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise NodeCardError(f"{where} has unknown keys {unknown}; allowed: {sorted(allowed)}.")
    return value


def _require_text(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NodeCardError(f"{where} must be a non-empty string.")
    return value


def _validate_inputs(raw: object, *, where: str) -> None:
    if not isinstance(raw, list):
        raise NodeCardError(f"{where} must be a list.")
    for index, item in enumerate(raw):
        entry = _require_keys(item, _INPUT_KEYS, where=f"{where}[{index}]")
        _require_text(entry.get("edge"), where=f"{where}[{index}].edge")
        columns = entry.get("columns")
        if (
            not isinstance(columns, dict)
            or not columns
            or not all(isinstance(dtype, str) and dtype for dtype in columns.values())
        ):
            raise NodeCardError(f"{where}[{index}].columns must map column names to dtypes.")


def _validate_configs(raw: object, *, where: str) -> None:
    if not isinstance(raw, list) or [
        item.get("name") if isinstance(item, dict) else None for item in raw
    ] != list(CARD_CONFIG_NAMES):
        raise NodeCardError(f"{where} must list exactly {list(CARD_CONFIG_NAMES)} in order.")
    for item in raw:
        name = item["name"]
        entry = _require_keys(item, _CONFIG_KEYS, where=f"{where}.{name}")
        _require_text(entry.get("intent"), where=f"{where}.{name}.intent")
        if not isinstance(entry.get("config"), dict):
            raise NodeCardError(f"{where}.{name}.config must be an object.")
        produces = entry.get("produces")
        if not isinstance(produces, list) or not all(
            isinstance(column, str) and column for column in produces
        ):
            raise NodeCardError(f"{where}.{name}.produces must list column names.")
        if "inputs" in entry:
            _validate_inputs(entry["inputs"], where=f"{where}.{name}.inputs")


def _validate_fixture(raw: object, *, where: str) -> None:
    fixture = _require_keys(raw, _FIXTURE_KEYS, where=where)
    frames = fixture.get("frames", {})
    if not isinstance(frames, dict):
        raise NodeCardError(f"{where}.frames must be an object.")
    for name, frame in frames.items():
        entry = _require_keys(frame, _FRAME_KEYS, where=f"{where}.frames.{name}")
        rows = entry.get("rows")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise NodeCardError(f"{where}.frames.{name}.rows must be a non-empty row list.")
    if not isinstance(fixture.get("files", {}), dict):
        raise NodeCardError(f"{where}.files must map project paths to JSON values.")
    for key in ("upstream", "downstream"):
        if not isinstance(fixture.get(key, []), list):
            raise NodeCardError(f"{where}.{key} must be an operation list.")
    expect = fixture.get("expect", {})
    if not isinstance(expect, dict) or not set(expect) <= set(CARD_CONFIG_NAMES):
        raise NodeCardError(f"{where}.expect must map configuration names to expected rows.")
    for name, rows in expect.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise NodeCardError(f"{where}.expect.{name} must be a row list.")
    if "model_run" in fixture:
        run = _require_keys(fixture["model_run"], _MODEL_RUN_KEYS, where=f"{where}.model_run")
        for key in ("frame", "artifact"):
            _require_text(run.get(key), where=f"{where}.model_run.{key}")
        features = run.get("features")
        if not isinstance(features, list) or not features:
            raise NodeCardError(f"{where}.model_run.features must list the model's features.")


def _validate_card(node_type: NodeType, card: object) -> dict[str, Any]:
    where = f"Node card {node_type.value!r}"
    if not isinstance(card, dict) or card.get("node_type") != node_type.value:
        raise NodeCardError(f"{where} must declare node_type {node_type.value!r}.")
    authorable = card.get("authorable")
    if authorable is False:
        _require_keys(card, _NOT_AUTHORABLE_KEYS, where=where)
        _require_text(card.get("note"), where=f"{where}.note")
        return card
    if authorable is not True:
        raise NodeCardError(f"{where}.authorable must be true or false.")
    _require_keys(card, _AUTHORABLE_KEYS, where=where)
    fields = card.get("fields")
    if (
        not isinstance(fields, dict)
        or not fields
        or not all(isinstance(text, str) and text.strip() for text in fields.values())
    ):
        raise NodeCardError(f"{where}.fields must map config paths to their meaning.")
    _validate_inputs(card.get("inputs"), where=f"{where}.inputs")
    _validate_configs(card.get("configs"), where=f"{where}.configs")
    _validate_fixture(card.get("fixture"), where=f"{where}.fixture")
    return card


@cache
def _packaged_cards() -> Mapping[NodeType, dict[str, Any]]:
    root = resources.files(_ASSET_PACKAGE).joinpath(*_CARDS_DIR)
    names = sorted(
        child.name for child in root.iterdir() if child.is_file() and child.name.endswith(".json")
    )
    expected = sorted(f"{node_type.value}.json" for node_type in NodeType)
    if names != expected:
        raise NodeCardError(
            "Every node type needs exactly one packaged node card.\n"
            f"  Missing: {sorted(set(expected) - set(names))}\n"
            f"  Unexpected: {sorted(set(names) - set(expected))}"
        )
    cards: dict[NodeType, dict[str, Any]] = {}
    for node_type in NodeType:
        text = root.joinpath(f"{node_type.value}.json").read_text(encoding="utf-8")
        try:
            card = json.loads(text)
        except json.JSONDecodeError as exc:
            raise NodeCardError(f"Node card {node_type.value!r} is not valid JSON: {exc}") from exc
        cards[node_type] = _validate_card(node_type, card)
    return cards


def node_card(node_type: NodeType) -> dict[str, Any]:
    """Return *node_type*'s card as the model reads it, without its test fixture."""

    card = _packaged_cards()[node_type]
    return deepcopy({key: value for key, value in card.items() if key != "fixture"})


def node_card_fixture(node_type: NodeType) -> dict[str, Any] | None:
    """Return the synthetic fixture that executes *node_type*'s card in CI.

    ``None`` for a type the assistant cannot author, whose card has no
    configuration to execute.
    """

    fixture: dict[str, Any] | None = _packaged_cards()[node_type].get("fixture")
    return deepcopy(fixture)


__all__ = [
    "CARD_CONFIG_NAMES",
    "NodeCardError",
    "node_card",
    "node_card_fixture",
]
