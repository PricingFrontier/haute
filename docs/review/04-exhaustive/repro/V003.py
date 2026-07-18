"""Isolated reproduction for V003.

Claim: `_DEPENDENCIES_KEY_RE` (anchored with `^dependencies` under re.MULTILINE)
does NOT match a `dependencies` key that is *indented* inside `[project]`.
TOML permits leading whitespace before a key, so

    [project]
    name = "x"
    version = "0.1.0"
      dependencies = [
        "polars",
    ]

is valid TOML. `_dependencies_contain_haute` sees only ["polars"] (no haute),
so `_rewrite_project_dependencies` runs, fails to locate the array (regex None),
and INSERTS a *second* `dependencies = [...]` right after the [project] header.
The table then has two `dependencies` keys -> tomllib rejects it with
"Cannot overwrite a value", and `haute init`'s `_ensure_haute_dependency`
crashes.

This repro asserts on the SPECIFIC wrong behaviour:
  (A) the regex does not match the indented key,
  (B) `_rewrite_project_dependencies` produces output with TWO `dependencies`
      keys in the [project] table (a duplicate-key corruption), and that this
      output is no longer parseable as TOML (the concrete corruption), and
  (C) the higher-level `_ensure_haute_dependency` raises TOMLDecodeError
      on a real pyproject.toml file written to a tempfile.

Isolation: only a Python tempfile is touched on disk. No rating/, src/, tests/
or real project files are read or written.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from pathlib import Path

from haute.cli._init_cmd import (
    _DEPENDENCIES_KEY_RE,
    _dependencies_contain_haute,
    _ensure_haute_dependency,
    _rewrite_project_dependencies,
)

# A valid pyproject.toml where the `dependencies` key is indented under
# [project]. Leading whitespace before a TOML key is legal.
PYPROJECT = (
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    "  dependencies = [\n"
    '    "polars",\n'
    "]\n"
)


def _count_dependencies_keys(text: str) -> int:
    """Count lines that are a top-level (ignoring leading ws) `dependencies =`."""
    return len(re.findall(r"(?m)^\s*dependencies\s*=", text))


def main() -> None:
    # Sanity: the crafted input really is valid TOML to begin with.
    parsed = tomllib.loads(PYPROJECT)
    assert parsed["project"]["dependencies"] == ["polars"], parsed
    print("[setup] input is valid TOML; project.dependencies =", parsed["project"]["dependencies"])

    # (A) The regex fails to match the indented key (root cause).
    regex_match = _DEPENDENCIES_KEY_RE.search(PYPROJECT)
    print("[A] _DEPENDENCIES_KEY_RE.search(indented) =", regex_match)
    assert regex_match is None, (
        "EXPECTED the anchored regex to MISS the indented key (demonstrating the "
        f"bug), but it matched: {regex_match!r}"
    )

    # The caller would proceed to rewrite because haute is absent.
    assert _dependencies_contain_haute(["polars"]) is False

    # (B) _rewrite_project_dependencies corrupts the file: it inserts a SECOND
    # `dependencies` key instead of editing the existing (indented) array.
    rewritten = _rewrite_project_dependencies(PYPROJECT)
    n_keys = _count_dependencies_keys(rewritten)
    print("[B] dependencies-key count after rewrite =", n_keys)
    print("[B] rewritten text:\n" + rewritten)

    assert n_keys == 2, (
        "EXPECTED two duplicate `dependencies` keys (corruption) but found "
        f"{n_keys}. Rewritten:\n{rewritten}"
    )

    # The concrete corruption: the rewritten output no longer parses as TOML.
    corruption_confirmed = False
    try:
        tomllib.loads(rewritten)
    except tomllib.TOMLDecodeError as exc:
        corruption_confirmed = True
        print("[B] rewritten TOML now FAILS to parse:", type(exc).__name__, "-", exc)
    assert corruption_confirmed, (
        "EXPECTED the rewritten output to be invalid TOML (duplicate key), but "
        "it parsed cleanly. Rewritten:\n" + rewritten
    )

    # (C) End-to-end: `_ensure_haute_dependency` on a real file crashes with
    # TOMLDecodeError ("Cannot overwrite a value"). Use a tempfile only.
    with tempfile.TemporaryDirectory() as tmp:
        pyproject_path = Path(tmp) / "pyproject.toml"
        pyproject_path.write_text(PYPROJECT, encoding="utf-8")

        raised: Exception | None = None
        try:
            _ensure_haute_dependency(pyproject_path, "demo")
        except Exception as exc:  # noqa: BLE001 - we assert on the type/msg below
            raised = exc

        print("[C] _ensure_haute_dependency raised:", type(raised).__name__ if raised else None,
              "-", raised)
        assert isinstance(raised, tomllib.TOMLDecodeError), (
            "EXPECTED _ensure_haute_dependency to raise tomllib.TOMLDecodeError "
            f"(duplicate dependencies key), but got: {raised!r}"
        )
        assert "overwrite" in str(raised).lower(), (
            "EXPECTED a 'Cannot overwrite a value' style message, got: " + str(raised)
        )

    # Control: a properly column-0 `dependencies` key is handled correctly
    # (proves the failure is specifically the indentation, not something else).
    ok_input = (
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "dependencies = [\n"
        '    "polars",\n'
        "]\n"
    )
    ok_rewritten = _rewrite_project_dependencies(ok_input)
    ok_parsed = tomllib.loads(ok_rewritten)
    print("[control] column-0 key rewrite ->", ok_parsed["project"]["dependencies"])
    assert ok_parsed["project"]["dependencies"] == ["haute", "polars"], ok_parsed
    assert _count_dependencies_keys(ok_rewritten) == 1, ok_rewritten

    print("\nV003 REPRODUCED: indented `dependencies` key -> duplicate-key corruption -> crash.")


if __name__ == "__main__":
    main()
