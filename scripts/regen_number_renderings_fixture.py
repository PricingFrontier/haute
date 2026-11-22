#!/usr/bin/env python3
"""Regenerate the numbers the step editor's spelling rule is checked against (CGEN-09).

``frontend/src/panels/editors/polarsSteps/__tests__/fixtures/numberRenderings.json`` holds,
for each of a fixed set of doubles, how the server writes the number a browser sends for it
given no spelling (``haute._polars_steps.number_rendering`` of ``js_held``): as a bare
literal and as a member of a list of floats, of decimals and of whole numbers. The editor's
``numberSpelling.test.ts`` requires ``arrivedRendering`` to give the same, so the editor and
the server decide alike when a number carries its ``text``;
``tests/test_polars_steps_lift.py`` requires this file to equal ``build_fixture()``. After a
change to either rule:

    uv run python scripts/regen_number_renderings_fixture.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haute._polars_steps import js_held, number_rendering  # noqa: E402

FIXTURE = (
    ROOT
    / "frontend"
    / "src"
    / "panels"
    / "editors"
    / "polarsSteps"
    / "__tests__"
    / "fixtures"
    / "numberRenderings.json"
)

#: The doubles at each boundary of the two languages' ways of writing a number.
_EDGES = (
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.1,
    2.5,
    1e-4,
    1e-5,
    1e-7,
    1.5e-7,
    123.456,
    1e15,
    1e16,
    1e20,
    1e21,
    1e22,
    1.5e300,
    9007199254740993.0,
    2.0**60,
    1234567890123456789.0,
)


def _doubles() -> list[float]:
    draw = random.Random(20261120)
    found = list(_EDGES)
    for _ in range(400):
        power = draw.randint(-30, 30)
        found.append((draw.random() - 0.5) * 10.0**power)
        found.append(float(round((draw.random() - 0.5) * 10.0 ** (power / 2))))
    return found


def build_fixture() -> dict[str, Any]:
    cases = []
    for value in _doubles():
        arrived = js_held(value)
        case: dict[str, Any] = {"value": repr(value)}
        case["bare"] = number_rendering(arrived)
        case["float"] = number_rendering(arrived, "float")
        case["decimal"] = number_rendering(arrived, "decimal")
        if value.is_integer():
            case["integer"] = number_rendering(arrived, "integer")
        cases.append(case)
    return {"cases": cases}


def render(fixture: dict[str, Any]) -> str:
    return '{"cases":[\n' + ",\n".join(json.dumps(case) for case in fixture["cases"]) + "\n]}\n"


if __name__ == "__main__":
    FIXTURE.write_bytes(render(build_fixture()).encode("utf-8"))
    print(f"wrote {FIXTURE.relative_to(ROOT)}")
