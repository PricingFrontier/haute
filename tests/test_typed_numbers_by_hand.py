"""No server code reads text as a number but the one reader (specs/frontend-shared high-level,
"Typed numbers"): the mypy plugin ``scripts/mypy_typed_numbers.py``, which the project's mypy
gate loads, refuses ``float``, ``int`` and ``Decimal`` given text outside
``haute._typed_numbers`` but the calls on its ``NOT_TYPED`` list, each one call."""

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
            float(text)

        def not_typed(number: float, whole: int, anything: Any) -> None:
            float(number)
            int(whole)
            Decimal(str(number))
            Decimal(repr(number))
            float(anything)
        """,
    )

    lines = [line for line in found.splitlines() if "Text read as a number by hand" in line]
    assert [re.search(r":(\d+): error", line).group(1) for line in lines] == [
        "6",
        "7",
        "8",
        "9",
        "10",
    ]
    # Each finding is keyed by its own call, a second of the same call by its place, so an
    # exception for one never covers another beside it.
    assert [re.search(r"by hand at `([^`]*)`", line).group(1) for line in lines] == [
        "typed_probe.typed::float(text)",
        "typed_probe.typed::int(either)",
        "typed_probe.typed::Decimal(text)",
        "typed_probe.typed::int(raw)",
        "typed_probe.typed::float(text)#2",
    ]


def test_every_exception_names_a_call_that_still_reads_a_number() -> None:
    """Each call listed on ``NOT_TYPED`` is still in its function's source, as many times as
    its ``#n`` says, so the list holds no exception for code that has gone, and none for the
    reader itself."""
    assert [key for key in NOT_TYPED if not _still_called(key)] == []
    assert not any(key.startswith(f"{READER}.") for key in NOT_TYPED)


def _still_called(key: str) -> bool:
    where, _, call = key.partition("::")
    call, _, nth = call.partition("#")
    node = _function(where)
    if node is None:
        return False
    source, tree = node
    segment = source if isinstance(tree, ast.Module) else ast.get_source_segment(source, tree)
    text = " ".join((segment or "").split())
    return _calls_a_reader(tree) and text.count(call) >= int(nth or 1)


def _function(where: str) -> tuple[str, ast.AST] | None:
    """The source of the module *where* names and the function in it (``module.function``,
    ``module.Class.method``, or ``module.<module>`` for its top level), or None."""
    parts = where.split(".")
    for split in range(len(parts) - 1, 0, -1):
        base = ROOT / "src" if parts[0] == "haute" else ROOT
        path = base.joinpath(*parts[:split]).with_suffix(".py")
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            scope = parts[split:]
            found = tree if scope == ["<module>"] else _find(tree, scope)
            return None if found is None else (source, found)
    return None


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
