"""Guardrails for skip/xfail/importorskip test debt."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

TESTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class _DebtViolation:
    path: Path
    line: int
    message: str


def _attr_path(node: ast.AST) -> str | None:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _has_reason_expr(expr: ast.AST) -> bool:
    if isinstance(expr, ast.Constant):
        if expr.value is None:
            return False
        if isinstance(expr.value, str):
            return bool(expr.value.strip())
        return True
    return True


def _reason_keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        return keyword.value
    return None


def _first_reason_arg(call: ast.Call) -> ast.AST | None:
    if not call.args:
        return None
    return call.args[0]


def _has_skip_or_xfail_reason(call: ast.Call) -> bool:
    keyword_reason = _reason_keyword(call, "reason")
    if keyword_reason is not None:
        return _has_reason_expr(keyword_reason)
    positional_reason = _first_reason_arg(call)
    return positional_reason is not None and _has_reason_expr(positional_reason)


def _has_importorskip_reason(call: ast.Call) -> bool:
    keyword_reason = _reason_keyword(call, "reason")
    return keyword_reason is not None and _has_reason_expr(keyword_reason)


def _scan_file(path: Path) -> list[_DebtViolation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[_DebtViolation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = _attr_path(node.func)
            if func_name in {"pytest.skip", "pytest.xfail"} and not _has_skip_or_xfail_reason(node):
                violations.append(
                    _DebtViolation(
                        path=path,
                        line=node.lineno,
                        message=f"{func_name} must carry a non-empty reason",
                    )
                )
            elif func_name == "pytest.importorskip" and not _has_importorskip_reason(node):
                violations.append(
                    _DebtViolation(
                        path=path,
                        line=node.lineno,
                        message="pytest.importorskip must carry a non-empty reason=",
                    )
                )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                decorator_path = _attr_path(decorator)
                if decorator_path in {
                    "pytest.mark.skip",
                    "pytest.mark.xfail",
                    "pytest.mark.skipif",
                }:
                    violations.append(
                        _DebtViolation(
                            path=path,
                            line=decorator.lineno,
                            message=f"{decorator_path} must be called with reason=...",
                        )
                    )
                    continue
                if not isinstance(decorator, ast.Call):
                    continue
                decorator_name = _attr_path(decorator.func)
                if decorator_name in {
                    "pytest.mark.skip",
                    "pytest.mark.xfail",
                    "pytest.mark.skipif",
                } and not _has_skip_or_xfail_reason(decorator):
                    violations.append(
                        _DebtViolation(
                            path=path,
                            line=decorator.lineno,
                            message=f"{decorator_name} must carry a non-empty reason",
                        )
                    )

    return violations


def test_skip_and_xfail_sites_have_reasons() -> None:
    violations: list[_DebtViolation] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        violations.extend(_scan_file(path))

    assert not violations, "\n".join(
        f"{violation.path.relative_to(TESTS_DIR.parent)}:{violation.line}: {violation.message}"
        for violation in violations
    )
