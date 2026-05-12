from __future__ import annotations

import ast
from pathlib import Path

BOUNDED_EXECUTION_MODULES = (
    "src/haute/_execute_lazy.py",
    "src/haute/chunking.py",
    "src/haute/routes/_optimiser_service.py",
    "src/haute/routes/_train_service.py",
    "src/haute/deploy/_scorer.py",
    "src/haute/_model_scorer.py",
)


def test_bounded_execution_modules_do_not_call_polars_collect_directly() -> None:
    """Bounded paths must use the shared collection/sink helpers."""
    violations: list[str] = []
    root = Path(__file__).resolve().parents[1]
    for relative_path in BOUNDED_EXECUTION_MODULES:
        path = root / relative_path
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "collect":
                continue
            if isinstance(func.value, ast.Name) and func.value.id == "gc":
                continue
            violations.append(f"{relative_path}:{node.lineno}")

    assert violations == []
