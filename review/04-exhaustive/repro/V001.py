"""Isolated reproduction for V001.

Claim: ``_scan_table_headers`` only recognises a *bare* literal ``[project]``
header. A quoted ``["project"]`` header or dotted ``project.x = ...`` keys are
valid project tables per tomllib but are MISSED by the textual scanner, so
``_find_project_table_bounds`` returns None. ``_rewrite_project_dependencies``
then takes its ``bounds is None`` branch and APPENDS a fresh ``[project]``
table, producing a file with TWO project declarations. The re-parse inside
``_ensure_haute_dependency`` (tomllib.loads) then raises
``Cannot declare ('project',) twice``.

This repro exercises the real ``_ensure_haute_dependency`` on a tempfile pyproject.
ISOLATION: all disk I/O is via tempfile; no rating/, src/, tests/, or real
project files are read or written.
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from haute.cli._init_cmd import (  # noqa: E402
    _ensure_haute_dependency,
    _find_project_table_bounds,
    _scan_table_headers,
)


def _run_case(label: str, pyproject_text: str) -> dict:
    """Run _ensure_haute_dependency against a synthetic pyproject and report."""
    # Sanity: tomllib genuinely treats this as project.dependencies.
    parsed = tomllib.loads(pyproject_text)
    assert parsed["project"]["dependencies"] == ["polars"], (
        f"{label}: precondition — tomllib must see a project.dependencies of ['polars'], "
        f"got {parsed.get('project')}"
    )

    headers = _scan_table_headers(pyproject_text)
    bounds = _find_project_table_bounds(pyproject_text)

    with tempfile.TemporaryDirectory() as tmp:
        pyproject = Path(tmp) / "pyproject.toml"
        pyproject.write_text(pyproject_text, encoding="utf-8")
        error: BaseException | None = None
        try:
            _ensure_haute_dependency(pyproject, "demo_project")
        except BaseException as exc:  # noqa: BLE001 - we are characterising the failure
            error = exc

    return {
        "label": label,
        "headers": headers,
        "bounds": bounds,
        "error": error,
    }


CASE_QUOTED = (
    '["project"]\n'
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    'dependencies = ["polars"]\n'
)

CASE_DOTTED = (
    'project.name = "demo"\n'
    'project.version = "0.1.0"\n'
    'project.requires-python = ">=3.11"\n'
    'project.dependencies = ["polars"]\n'
)


def main() -> int:
    failures: list[str] = []

    for label, text in (("quoted-header", CASE_QUOTED), ("dotted-keys", CASE_DOTTED)):
        result = _run_case(label, text)
        print(f"--- {label} ---")
        print(f"  _scan_table_headers      -> {result['headers']}")
        print(f"  _find_project_table_bounds-> {result['bounds']}")
        err = result["error"]
        if err is None:
            print("  _ensure_haute_dependency  -> no error")
        else:
            print(f"  _ensure_haute_dependency  -> {type(err).__name__}: {err}")

        # The bug prediction: the scanner misses the project table (bounds None),
        # so the rewrite appends a duplicate [project] and the re-parse explodes
        # with a TOMLDecodeError about declaring 'project' twice.
        if result["bounds"] is not None:
            failures.append(
                f"{label}: expected _find_project_table_bounds to MISS the table (None), "
                f"but got {result['bounds']} — bug mechanism not present."
            )
            continue
        if not isinstance(err, tomllib.TOMLDecodeError):
            failures.append(
                f"{label}: expected tomllib.TOMLDecodeError from duplicate [project], "
                f"got {type(err).__name__ if err else 'no error'}: {err}"
            )
            continue
        if "twice" not in str(err):
            failures.append(
                f"{label}: error did not mention declaring a table twice: {err!r}"
            )
            continue
        print(f"  REPRODUCED: duplicate [project] -> {type(err).__name__}: {err}")

    print()
    if failures:
        print("REPRO RESULT: claim NOT reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED — both quoted-header and dotted-key project")
    print("tables are missed by the textual scanner; _ensure_haute_dependency appends")
    print("a duplicate [project] and crashes with TOMLDecodeError 'Cannot declare ... twice'.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
