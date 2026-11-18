"""No server code reads text as a number but the one reader (specs/frontend-shared high-level,
"Typed numbers"): the mypy plugin ``scripts/mypy_typed_numbers.py``, which the project's mypy
gate loads, refuses ``float``, ``int`` and ``Decimal`` given text outside
``haute._typed_numbers`` but the functions on its ``NOT_TYPED`` list."""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest
from mypy import api as mypy_api

from scripts.mypy_typed_numbers import NOT_TYPED, READER

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "scripts" / "mypy_typed_numbers.py"


def _mypy(tmp_path: Path, source: str) -> str:
    module = tmp_path / "typed_probe.py"
    module.write_text(textwrap.dedent(source), encoding="utf-8")
    config = tmp_path / "mypy.ini"
    config.write_text(
        f"[mypy]\nplugins = {PLUGIN.as_posix()}\ncache_dir = {(tmp_path / 'cache').as_posix()}\n",
        encoding="utf-8",
    )
    stdout, _stderr, _status = mypy_api.run(
        ["--config-file", str(config), "--no-error-summary", str(module)]
    )
    return stdout


def test_a_language_reader_given_text_is_refused_and_one_given_a_number_is_not(
    tmp_path: Path,
) -> None:
    found = _mypy(
        tmp_path,
        """
        from decimal import Decimal
        from typing import Any

        def typed(text: str, either: str | int, raw: bytes) -> None:
            float(text)
            int(either)
            Decimal(text)
            int(raw)

        def not_typed(number: float, whole: int, anything: Any) -> None:
            float(number)
            int(whole)
            Decimal(str(number))
            Decimal(repr(number))
            float(anything)
        """,
    )

    lines = [line for line in found.splitlines() if "Text read as a number by hand" in line]
    assert [re.search(r":(\d+): error", line).group(1) for line in lines] == ["6", "7", "8", "9"]
    assert all("typed_probe.typed:" in line for line in lines)


def test_every_exception_names_a_function_that_still_reads_a_number() -> None:
    """A function listed on ``NOT_TYPED`` exists and calls ``float``, ``int`` or ``Decimal``, so
    the list holds no exception for code that has gone, and none for the reader itself."""
    assert [key for key in NOT_TYPED if not _reads_a_number(key)] == []
    assert not any(key.startswith(f"{READER}.") for key in NOT_TYPED)


def _reads_a_number(key: str) -> bool:
    """Whether the function *key* names (``module.function``, ``module.Class.method`` or
    ``module.<module>`` for a module's top level) calls a language reader of a number."""
    parts = key.split(".")
    for split in range(len(parts) - 1, 0, -1):
        base = ROOT / "src" if parts[0] == "haute" else ROOT
        source = base.joinpath(*parts[:split]).with_suffix(".py")
        if source.is_file():
            tree = ast.parse(source.read_text(encoding="utf-8"))
            scope = parts[split:]
            return _calls_a_reader(tree if scope == ["<module>"] else _find(tree, scope))
    return False


def _find(tree: ast.AST, scope: list[str]) -> ast.AST | None:
    node: ast.AST | None = tree
    for name in scope:
        if node is None:
            return None
        node = next(
            (
                child
                for child in ast.walk(node)
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and child.name == name
            ),
            None,
        )
    return node


def _calls_a_reader(node: ast.AST | None) -> bool:
    return node is not None and any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in {"float", "int", "Decimal"}
        for call in ast.walk(node)
    )


@pytest.mark.parametrize("key", sorted(NOT_TYPED))
def test_every_exception_says_why_its_text_is_not_typed(key: str) -> None:
    assert NOT_TYPED[key].strip()
