"""Python-side drift gate for the backend↔frontend sanitizer parity fixture.

The fixture ``frontend/src/utils/__tests__/sanitizeParity.fixture.json`` was
generated FROM the backend ``_sanitize_func_name`` and is asserted against the
frontend ``sanitizeName.ts`` by vitest (``sanitizeParity.diff.test.ts``).
Before this gate existed, a backend sanitizer change kept BOTH suites green
while the implementations diverged — vitest checks TS against the (stale)
fixture, and nothing checked the fixture against Python.  This test closes
that: any backend change that alters an expected output fails here until
``scripts/regen_sanitize_parity_fixture.py`` is rerun (which in turn breaks
vitest until the TS side is realigned).  Drift can no longer stay green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haute._graph_utils import _sanitize_func_name

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "frontend" / "src" / "utils" / "__tests__" / "sanitizeParity.fixture.json"


def _load_pairs() -> list[list[str]]:
    if not (_REPO_ROOT / "frontend").is_dir():
        # Running outside the repo checkout (e.g. against an installed
        # package) — the frontend tree, and therefore the parity contract,
        # does not exist there. In-repo CI always has it.
        pytest.skip("frontend tree not present; parity fixture out of scope")
    # If frontend/ exists the fixture MUST: a missing fixture is drift, not
    # an excuse to skip.
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_fixture_expected_outputs_match_backend_sanitizer() -> None:
    pairs = _load_pairs()
    assert len(pairs) >= 400, "parity fixture unexpectedly shrank"
    mismatches = [
        (inp, expected, _sanitize_func_name(inp))
        for inp, expected in pairs
        if _sanitize_func_name(inp) != expected
    ]
    assert mismatches == [], (
        "Backend _sanitize_func_name diverged from the parity fixture "
        "(input, fixture, backend): "
        f"{mismatches[:10]!r} — if the backend change is intentional, run "
        "scripts/regen_sanitize_parity_fixture.py and realign sanitizeName.ts"
    )


def test_fixture_serialization_is_regenerator_canonical() -> None:
    """The on-disk bytes must be exactly what the regenerator would emit.

    Guards the regeneration loop itself: a hand-edited or re-serialized
    fixture (indented, ascii-escaped, trailing newline) would make
    ``regen_sanitize_parity_fixture.py`` produce spurious diffs.
    """
    pairs = _load_pairs()
    canonical = json.dumps(
        [[inp, _sanitize_func_name(inp)] for inp, _expected in pairs],
        ensure_ascii=False,
    )
    assert _FIXTURE.read_text(encoding="utf-8") == canonical
