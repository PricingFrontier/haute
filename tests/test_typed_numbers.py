"""The one grammar for a typed number, read in both ways, held to the case file the browser's
reader is held to too (specs/frontend-shared high-level, "Typed numbers")."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._typed_numbers import (
    TYPED_SPACES,
    ConstantValueError,
    constant_value,
    number_text,
    read_typed_number,
    read_typed_whole,
    trim_typed,
)

CASES: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1] / "frontend/src/utils/__tests__/fixtures/typedText.json"
    ).read_text(encoding="utf-8")
)

_TYPES = {"Int64": int, "Float64": float, "Boolean": bool, "String": str, "Null": type(None)}


def _held_as(case: dict[str, Any], value: object) -> None:
    expected = case["constant"]
    if expected == "refused":
        with pytest.raises(ConstantValueError):
            constant_value(value)
        return
    held = constant_value(value)
    assert type(held) is _TYPES[expected["type"]]
    if expected["type"] == "String":
        assert held == value
    elif expected["type"] == "Int64":
        assert held == int(expected["value"])
    else:
        assert held == expected["value"]


def test_the_typed_spaces_are_the_case_files() -> None:
    assert [ord(character) for character in TYPED_SPACES] == CASES["spaces"]


@pytest.mark.parametrize("case", CASES["numbers"], ids=[case["name"] for case in CASES["numbers"]])
def test_a_numeric_field_reads_typed_text_by_the_grammar(case: dict[str, Any]) -> None:
    field = case["field"]
    if field is None:
        assert (read_typed_number(case["text"]), read_typed_whole(case["text"])) == (None, None)
        return
    assert read_typed_number(case["text"]) == field["float"]
    whole = read_typed_whole(case["text"])
    assert whole == (None if field["int"] is None else int(field["int"]))


@pytest.mark.parametrize("case", CASES["numbers"], ids=[case["name"] for case in CASES["numbers"]])
def test_a_constant_holds_typed_text_as_its_canonical_value(case: dict[str, Any]) -> None:
    _held_as(case, case["text"])


@pytest.mark.parametrize(
    "case", CASES["constants"], ids=[case["name"] for case in CASES["constants"]]
)
def test_a_constant_holds_a_settings_files_value_as_its_type(case: dict[str, Any]) -> None:
    _held_as(case, case["value"])


def test_a_constant_refuses_a_float_that_is_not_finite() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ConstantValueError):
            constant_value(value)


@pytest.mark.parametrize("number", [2.5, 5.0, 1e-05, 1e20, -0.0, 0.1 + 0.2, 123456.789])
def test_a_floats_canonical_text_reads_back_as_the_same_float(number: float) -> None:
    text = number_text(number)
    assert "e" not in text and "." in text
    assert constant_value(text) == number
    assert type(constant_value(text)) is float


def test_typed_text_is_trimmed_of_the_typed_spaces_alone() -> None:
    assert trim_typed(f"{TYPED_SPACES}5{TYPED_SPACES}") == "5"
    assert trim_typed("\x1c5\x85") == "\x1c5\x85"
