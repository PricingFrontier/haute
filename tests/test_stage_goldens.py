"""Public stage responses equal the frozen goldens (see `tests/_stage_goldens.py`).

The goldens were captured from the storage implementation before stage storage v2. A
storage change must keep every client-visible response identical. A deliberate response
change needs regenerated goldens and a stated reason.
"""

from typing import Any

import pytest

from tests._stage_goldens import VALIDATIONS, capture, golden_path, load_golden


def _first_difference(expected: Any, actual: Any, where: str = "") -> str | None:
    if type(expected) is not type(actual):
        return f"{where}: type {type(expected).__name__} != {type(actual).__name__}"
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                return f"{where}/{key}: missing"
            if key not in expected:
                return f"{where}/{key}: unexpected"
            found = _first_difference(expected[key], actual[key], f"{where}/{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{where}: length {len(expected)} != {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            found = _first_difference(left, right, f"{where}[{index}]")
            if found:
                return found
        return None
    return None if expected == actual else f"{where}: {expected!r} != {actual!r}"


@pytest.mark.parametrize("name", sorted(VALIDATIONS))
def test_stage_responses_equal_frozen_goldens(tmp_path, monkeypatch, name):
    if not golden_path(name).exists():
        pytest.fail(f"Missing golden {golden_path(name)}; see tests/_stage_goldens.py")
    actual = capture(tmp_path, monkeypatch, VALIDATIONS[name])
    difference = _first_difference(load_golden(name), actual)
    assert difference is None, difference
