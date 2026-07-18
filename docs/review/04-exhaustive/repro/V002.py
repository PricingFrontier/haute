"""Isolated reproduction for V002.

Claim: `_rewrite_project_dependencies` (src/haute/cli/_init_cmd.py:269) re-emits
each parsed dependency string with a naive f-string `f'    "{dep}",\n'` that does
NOT escape embedded double quotes. A dependency whose value legitimately contains
a `"` (PEP 508 environment markers, e.g. `tomli ; python_version < "3.11"`) is
therefore round-tripped to INVALID TOML, and the subsequent re-parse in
`_ensure_haute_dependency` crashes with TOMLDecodeError ("Unclosed array").

This repro:
  1. Builds a VALID pyproject.toml entirely in a tempfile (no project files
     touched) whose [project].dependencies contains an env-marker dependency
     with an embedded escaped quote.
  2. Confirms the input is valid TOML and that tomllib decodes the marker to a
     Python string carrying a *literal* unescaped double quote.
  3. Calls the real `_rewrite_project_dependencies` and asserts on the SPECIFIC
     wrong value: the emitted line contains an unescaped `"` mid-string, and the
     rewritten text no longer parses as TOML (the exact failure the finding
     predicts).
  4. Also drives the public `_ensure_haute_dependency` to show `haute init`'s
     code path raises TOMLDecodeError end-to-end.
"""

from __future__ import annotations

import tempfile
import tomllib
from pathlib import Path

from haute.cli._init_cmd import (
    _ensure_haute_dependency,
    _rewrite_project_dependencies,
)

# A valid pyproject.toml. In the TOML source the marker quote is escaped (\");
# this is exactly how a real pyproject expresses `python_version < "3.11"`.
PYPROJECT_SRC = (
    "[project]\n"
    'name = "demo"\n'
    'version = "0.1.0"\n'
    'requires-python = ">=3.11"\n'
    "dependencies = [\n"
    '    "polars",\n'
    '    "tomli ; python_version < \\"3.11\\"",\n'
    "]\n"
)


def _check_input_is_valid_and_has_literal_quote() -> None:
    parsed = tomllib.loads(PYPROJECT_SRC)
    deps = parsed["project"]["dependencies"]
    # The decoded Python string carries a LITERAL double quote (no backslash).
    marker_dep = deps[1]
    assert marker_dep == 'tomli ; python_version < "3.11"', repr(marker_dep)
    assert '"' in marker_dep, "precondition: decoded dep must contain a literal quote"
    print("[setup] input is valid TOML; decoded marker dep =", repr(marker_dep))


def _check_rewrite_emits_invalid_toml() -> str:
    rewritten = _rewrite_project_dependencies(PYPROJECT_SRC)
    print("[rewrite] produced array region:")
    for line in rewritten.splitlines():
        if "tomli" in line or "haute" in line or "polars" in line:
            print("   |", line)

    # The bug signature: the marker dep is re-emitted wrapped in "..." with its
    # embedded quotes left UNescaped, i.e. literally:
    #     "tomli ; python_version < "3.11"",
    bad_line = '    "tomli ; python_version < "3.11"",'
    assert bad_line in rewritten, (
        "expected the naive (buggy) unescaped re-emission to be present; "
        "if this assertion fails the dep was escaped correctly and the bug is fixed"
    )
    print("[rewrite] confirmed unescaped re-emission line is present")

    # And the whole document no longer parses as TOML -> the predicted corruption.
    raised: tomllib.TOMLDecodeError | None = None
    try:
        tomllib.loads(rewritten)
    except tomllib.TOMLDecodeError as exc:  # noqa: PERF203
        raised = exc
    assert raised is not None, (
        "expected rewritten text to be INVALID TOML, but it parsed cleanly — "
        "the round-trip would then be safe and the finding refuted"
    )
    msg = str(raised)
    print("[rewrite] rewritten text is INVALID TOML:", msg)
    # The finding specifically predicts an 'Unclosed array' decode error.
    assert "Unclosed array" in msg, f"unexpected decode error message: {msg!r}"
    return rewritten


def _check_ensure_haute_dependency_crashes() -> None:
    with tempfile.TemporaryDirectory() as td:
        pyproject = Path(td) / "pyproject.toml"
        pyproject.write_text(PYPROJECT_SRC, encoding="utf-8")
        raised: tomllib.TOMLDecodeError | None = None
        try:
            _ensure_haute_dependency(pyproject, "demo")
        except tomllib.TOMLDecodeError as exc:
            raised = exc
        assert raised is not None, (
            "expected `_ensure_haute_dependency` (haute init path) to raise "
            "TOMLDecodeError on the env-marker dependency, but it succeeded"
        )
        print("[init-path] _ensure_haute_dependency raised:", str(raised))
        assert "Unclosed array" in str(raised), str(raised)


if __name__ == "__main__":
    _check_input_is_valid_and_has_literal_quote()
    _check_rewrite_emits_invalid_toml()
    _check_ensure_haute_dependency_crashes()
    print("\nREPRO CONFIRMED: env-marker dependency round-trips to invalid TOML "
          "and crashes the `haute init` code path with TOMLDecodeError.")
