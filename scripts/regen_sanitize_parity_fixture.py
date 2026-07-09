#!/usr/bin/env python3
"""Regenerate the sanitizer parity fixture from the backend implementation.

The fixture ``frontend/src/utils/__tests__/sanitizeParity.fixture.json`` pins
``frontend/src/utils/sanitizeName.ts`` to the backend
``haute._graph_utils._sanitize_func_name`` (vitest asserts the TS side,
``tests/test_sanitize_parity_fixture.py`` asserts the Python side — a change
to either implementation fails its leg until the pair is realigned).

This script re-derives every expected output from the CURRENT backend
sanitizer, keeping the input set as-is.  Run it after an intentional backend
sanitizer change, then update ``sanitizeName.ts`` until vitest is green again:

    python3 scripts/regen_sanitize_parity_fixture.py

To extend coverage, append ``["new input", ""]`` pairs to the fixture and
rerun — the expected side is always recomputed.  The serialization is
canonical (compact ``json.dumps`` with ``ensure_ascii=False``, no trailing
newline); ``tests/test_sanitize_parity_fixture.py`` asserts that shape so the
fixture can only be produced this way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haute._graph_utils import _sanitize_func_name  # noqa: E402

FIXTURE = ROOT / "frontend" / "src" / "utils" / "__tests__" / "sanitizeParity.fixture.json"


def regenerate() -> int:
    pairs = json.loads(FIXTURE.read_text(encoding="utf-8"))
    regenerated = [[inp, _sanitize_func_name(inp)] for inp, _expected in pairs]
    FIXTURE.write_text(json.dumps(regenerated, ensure_ascii=False), encoding="utf-8")
    return len(regenerated)


if __name__ == "__main__":
    count = regenerate()
    print(f"regenerated {count} pairs -> {FIXTURE.relative_to(ROOT)}")
