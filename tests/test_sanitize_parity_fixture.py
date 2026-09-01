"""Backend compatibility golden test for the retained sanitizer fixture.

The fixture ``frontend/src/utils/__tests__/sanitizeParity.fixture.json`` is
generated FROM the backend ``_sanitize_func_name`` by
``scripts/regen_sanitize_parity_fixture.py`` and is asserted against the
backend implementation only. It is retained as a compatibility golden, not a
frontend/backend parity requirement. Any backend change that alters an expected
output fails here until the regenerator is rerun.

The regenerator module is the single source of BOTH the input corpus and the
expected outputs, and the gate asserts the on-disk bytes equal
``canonical(build_fixture())`` — so the corpus CONTENT is pinned too: an
edge-case input (keyword case, hex-encoding discriminator) cannot be dropped
from the fixture while both suites stay green, which the previous
outputs-only recompute allowed (only a row-count floor guarded the corpus).
Same idiom as ``test_api_input_label_parity_fixture.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "frontend" / "src" / "utils" / "__tests__" / "sanitizeParity.fixture.json"


def _load_regen() -> Any:
    """Import the regenerator as a module (its corpus + canonical serializer
    are the single source of the fixture)."""
    script_path = _REPO_ROOT / "scripts" / "regen_sanitize_parity_fixture.py"
    spec = importlib.util.spec_from_file_location("regen_sanitize_parity_fixture", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


regen = _load_regen()


def _require_fixture() -> str:
    if not (_REPO_ROOT / "frontend").is_dir():
        # Running outside the repo checkout (e.g. against an installed
        # package) — the frontend tree, and therefore the parity contract,
        # does not exist there. In-repo CI always has it.
        pytest.skip("frontend tree not present; parity fixture out of scope")
    # If frontend/ exists the fixture MUST: a missing fixture is drift, not
    # an excuse to skip.
    return _FIXTURE.read_text(encoding="utf-8")


def test_fixture_matches_current_backend() -> None:
    """On-disk fixture bytes == what the regenerator emits from today's backend.

    A single equality covers every drift face: expected outputs still match
    ``_sanitize_func_name``, the input corpus is exactly the regenerator's
    (no silently dropped edge cases), and the serialization is the canonical
    shape (compact ``json.dumps`` with ``ensure_ascii=False``, no trailing
    newline) — a hand-edited or re-indented fixture fails here too, so the
    fixture can only be produced by the regenerator.
    """
    on_disk = _require_fixture()
    assert on_disk == regen.canonical(regen.build_fixture()), (
        "Backend compatibility golden is stale — if a change to "
        "_sanitize_func_name (or the corpus) is intentional, run "
        "`uv run python scripts/regen_sanitize_parity_fixture.py` and realign "
        "the backend sanitizer changes, regenerate the compatibility golden."
    )


def test_fixture_width_floor() -> None:
    """The corpus must not silently shrink below its researched breadth."""
    pairs = json.loads(_require_fixture())
    assert len(pairs) >= 400, "parity fixture unexpectedly shrank"
