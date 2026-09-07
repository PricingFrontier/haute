"""Test-debt scanning primitives shared by test_test_debt.py and test_workflow_coverage.py."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

_CALL_DEBT_TARGETS = {
    "pytest.importorskip",
    "pytest.skip",
    "pytest.xfail",
}
_MARK_DEBT_TARGETS = {
    "pytest.mark.flaky",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
}
_DEBT_TARGETS = _CALL_DEBT_TARGETS | _MARK_DEBT_TARGETS

_FRONTEND_TEST_ROOTS = (
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "frontend" / "e2e",
)
_FRONTEND_TEST_SUFFIXES = {
    ".cjs",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".mts",
    ".ts",
    ".tsx",
}
_FRONTEND_DEBT_BASE_PATTERN = re.compile(r"(?<![\w$.])(?P<base>test|describe|it)(?![\w$])")
_FRONTEND_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][\w$]*")
_FRONTEND_DEBT_METHODS = frozenset(
    {"fail", "fails", "fixme", "only", "runIf", "skip", "skipIf", "todo"}
)
_FRONTEND_UNRESOLVED_COMPUTED_MEMBER = "<computed>"
_FRONTEND_STATIC_COMPUTED_MEMBER = "<static-computed>"


@dataclass(frozen=True)
class _PytestAliases:
    module_names: frozenset[str]
    mark_names: frozenset[str]
    direct_names: dict[str, str]


@dataclass(frozen=True)
class _DebtSite:
    id: str
    path: Path
    line: int
    scope: str
    kind: str
    reason: str
    strict: bool | None
    reason_is_static: bool


@dataclass(frozen=True)
class _FrontendDebtSite:
    id: str
    path: Path
    line: int
    callee: str
    source: str


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


def _canonical_pytest_path(node: ast.AST, aliases: _PytestAliases) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.direct_names.get(node.id)

    raw_path = _attr_path(node)
    if raw_path is None:
        return None

    parts = raw_path.split(".")
    if parts[0] in aliases.module_names:
        return ".".join(("pytest", *parts[1:]))
    if parts[0] in aliases.mark_names:
        return ".".join(("pytest", "mark", *parts[1:]))
    if parts[0] in aliases.direct_names:
        direct = aliases.direct_names[parts[0]]
        return ".".join((direct, *parts[1:]))
    return raw_path


def _collect_pytest_aliases(tree: ast.AST) -> _PytestAliases:
    module_names = {"pytest"}
    mark_names: set[str] = set()
    direct_names: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name == "mark":
                    mark_names.add(local_name)
                elif alias.name in {"skip", "xfail", "importorskip"}:
                    direct_names[local_name] = f"pytest.{alias.name}"

    return _PytestAliases(
        module_names=frozenset(module_names),
        mark_names=frozenset(mark_names),
        direct_names=direct_names,
    )


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _static_string_value(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _static_string_value(expr.left)
        right = _static_string_value(expr.right)
        if left is not None and right is not None:
            return left + right
    return None


def _reason_expr(kind: str, call: ast.Call) -> ast.AST | None:
    keyword_reason = _keyword(call, "reason")
    if keyword_reason is not None:
        return keyword_reason
    if kind in {"pytest.skip", "pytest.xfail"} and call.args:
        return call.args[0]
    return None


def _strict_value(call: ast.Call) -> bool | None:
    strict = _keyword(call, "strict")
    if isinstance(strict, ast.Constant) and isinstance(strict.value, bool):
        return strict.value
    return None


def _has_non_empty_reason(kind: str, call: ast.Call) -> bool:
    expr = _reason_expr(kind, call)
    if expr is None:
        return False
    if isinstance(expr, ast.Constant):
        if expr.value is None:
            return False
        if isinstance(expr.value, str):
            return bool(expr.value.strip())
    return True


def _reason_text(kind: str, call: ast.Call) -> str:
    expr = _reason_expr(kind, call)
    if expr is None:
        return ""
    return _static_string_value(expr) or ast.unparse(expr)


def _reason_is_static_non_empty(kind: str, call: ast.Call) -> bool:
    expr = _reason_expr(kind, call)
    if expr is None:
        return False
    value = _static_string_value(expr)
    return value is not None and bool(value.strip())


def _normalized_source(node: ast.AST) -> str:
    return re.sub(r"\s+", " ", ast.unparse(node)).strip()


def _fingerprint(path: Path, scope: str, kind: str, reason: str, node: ast.AST) -> str:
    raw = "|".join(
        (
            path.as_posix(),
            scope,
            kind,
            reason,
            _normalized_source(node),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class _DebtVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.scope: list[str] = []
        self.sites: list[_DebtSite] = []
        self.aliases = _PytestAliases(
            module_names=frozenset({"pytest"}),
            mark_names=frozenset(),
            direct_names={},
        )
        self._call_func_node_ids: set[int] = set()

    def scan(self, tree: ast.AST) -> list[_DebtSite]:
        self.aliases = _collect_pytest_aliases(tree)
        self.visit(tree)
        return sorted(self.sites, key=lambda site: (site.path.as_posix(), site.line, site.kind))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self._call_func_node_ids.add(id(node.func))
        kind = _canonical_pytest_path(node.func, self.aliases)
        if kind in _DEBT_TARGETS:
            scope = ".".join(self.scope) or "<module>"
            reason = _reason_text(kind, node)
            strict = _strict_value(node) if kind == "pytest.mark.xfail" else None
            self.sites.append(
                _DebtSite(
                    id=_fingerprint(self.path, scope, kind, reason, node),
                    path=self.path,
                    line=node.lineno,
                    scope=scope,
                    kind=kind,
                    reason=reason,
                    strict=strict,
                    reason_is_static=_reason_is_static_non_empty(kind, node),
                )
            )
        self._record_dynamic_add_marker_debt(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if id(node) in self._call_func_node_ids:
            self.generic_visit(node)
            return

        kind = _canonical_pytest_path(node, self.aliases)
        if kind in _MARK_DEBT_TARGETS:
            scope = ".".join(self.scope) or "<module>"
            self.sites.append(
                _DebtSite(
                    id=_fingerprint(self.path, scope, kind, "", node),
                    path=self.path,
                    line=node.lineno,
                    scope=scope,
                    kind=kind,
                    reason="",
                    strict=None,
                    reason_is_static=False,
                )
            )
        self.generic_visit(node)

    def _record_dynamic_add_marker_debt(self, node: ast.Call) -> None:
        call_path = _attr_path(node.func)
        if call_path is None or not call_path.endswith(".add_marker") or not node.args:
            return

        marker_arg = node.args[0]
        if not (
            isinstance(marker_arg, ast.Constant)
            and isinstance(marker_arg.value, str)
            and marker_arg.value in {"flaky", "skip", "skipif", "xfail"}
        ):
            return

        kind = f"pytest.mark.{marker_arg.value}"
        scope = ".".join(self.scope) or "<module>"
        self.sites.append(
            _DebtSite(
                id=_fingerprint(self.path, scope, kind, "", node),
                path=self.path,
                line=node.lineno,
                scope=scope,
                kind=kind,
                reason="",
                strict=None,
                reason_is_static=False,
            )
        )


def _mask_frontend_comments_and_strings(source: str) -> str:
    result: list[str] = []
    index = 0
    state = "code"
    quote = ""

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if char == "/" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "line-comment"
                continue
            if char == "/" and next_char == "*":
                result.extend((" ", " "))
                index += 2
                state = "block-comment"
                continue
            if char in {"'", '"', "`"}:
                result.append(" ")
                index += 1
                state = "string"
                quote = char
                continue
            result.append(char)
            index += 1
            continue

        if state == "line-comment":
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
            continue

        if state == "block-comment":
            if char == "*" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "code"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "string":
            if char == "\\":
                result.append(" ")
                if next_char:
                    result.append("\n" if next_char == "\n" else " ")
                    index += 2
                else:
                    index += 1
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == quote:
                state = "code"
            continue

    return "".join(result)


def _skip_frontend_whitespace(source: str, index: int) -> int:
    while index < len(source) and source[index].isspace():
        index += 1
    return index


def _read_frontend_identifier(source: str, index: int) -> tuple[str, int] | None:
    match = _FRONTEND_IDENTIFIER_PATTERN.match(source, index)
    if match is None:
        return None
    return match.group(0), match.end()


def _skip_frontend_balanced_call(source: str, index: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack = [")"]
    index += 1

    while index < len(source) and stack:
        char = source[index]
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers and char == stack[-1]:
            stack.pop()
        index += 1

    return index


def _frontend_computed_property_end(masked: str, index: int) -> int | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack = ["]"]
    cursor = index + 1

    while cursor < len(masked) and stack:
        char = masked[cursor]
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if char != stack[-1]:
                return None
            stack.pop()
        cursor += 1

    if stack:
        return None
    return cursor


def _parse_frontend_string_member(expression: str) -> tuple[str, bool] | None:
    stripped = expression.strip()
    if not stripped or stripped[0] not in {"'", '"', "`"}:
        return None

    quote = stripped[0]
    cursor = 1
    value: list[str] = []
    has_template_interpolation = False
    while cursor < len(stripped):
        char = stripped[cursor]
        next_char = stripped[cursor + 1] if cursor + 1 < len(stripped) else ""
        if char == "\\":
            if not next_char:
                return None
            value.append(next_char)
            cursor += 2
            continue
        if quote == "`" and char == "$" and next_char == "{":
            has_template_interpolation = True
        if char == quote:
            if stripped[cursor + 1 :].strip():
                return None
            if has_template_interpolation:
                return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False
            return "".join(value), True
        value.append(char)
        cursor += 1

    return None


def _split_frontend_top_level_conditional(masked_expression: str) -> tuple[int, int] | None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack: list[str] = []
    question_index: int | None = None

    for index, char in enumerate(masked_expression):
        next_char = masked_expression[index + 1] if index + 1 < len(masked_expression) else ""
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if stack and char == stack[-1]:
                stack.pop()
        elif not stack and char == "?" and next_char != ".":
            question_index = index
            break

    if question_index is None:
        return None

    stack = []
    nested_conditionals = 0
    for index in range(question_index + 1, len(masked_expression)):
        char = masked_expression[index]
        next_char = masked_expression[index + 1] if index + 1 < len(masked_expression) else ""
        if char in pairs:
            stack.append(pairs[char])
        elif char in closers:
            if stack and char == stack[-1]:
                stack.pop()
        elif not stack and char == "?" and next_char != ".":
            nested_conditionals += 1
        elif not stack and char == ":":
            if nested_conditionals == 0:
                return question_index, index
            nested_conditionals -= 1

    return None


def _classify_frontend_computed_member(
    masked_expression: str,
    original_expression: str,
) -> tuple[str, bool]:
    static_string = _parse_frontend_string_member(original_expression)
    if static_string is not None:
        return static_string

    conditional = _split_frontend_top_level_conditional(masked_expression)
    if conditional is not None:
        question_index, colon_index = conditional
        branches = (
            (
                masked_expression[question_index + 1 : colon_index],
                original_expression[question_index + 1 : colon_index],
            ),
            (
                masked_expression[colon_index + 1 :],
                original_expression[colon_index + 1 :],
            ),
        )
        branch_results = [
            _classify_frontend_computed_member(masked_branch, original_branch)
            for masked_branch, original_branch in branches
        ]
        if all(
            is_static and name not in _FRONTEND_DEBT_METHODS for name, is_static in branch_results
        ):
            return _FRONTEND_STATIC_COMPUTED_MEMBER, True
        return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False

    if re.fullmatch(
        r"(?:[+-]?\d+(?:\.\d+)?|true|false|null|undefined)",
        original_expression.strip(),
    ):
        return _FRONTEND_STATIC_COMPUTED_MEMBER, True

    return _FRONTEND_UNRESOLVED_COMPUTED_MEMBER, False


def _read_frontend_computed_property(
    masked: str,
    original: str,
    index: int,
) -> tuple[str, int, int, bool] | None:
    if index >= len(masked) or masked[index] != "[":
        return None

    property_start = _skip_frontend_whitespace(original, index + 1)
    if property_start >= len(original):
        return None
    end = _frontend_computed_property_end(masked, index)
    if end is None:
        return None

    name, is_static = _classify_frontend_computed_member(
        masked[index + 1 : end - 1],
        original[index + 1 : end - 1],
    )
    return name, end, property_start, is_static


def _frontend_debt_chain(
    masked: str,
    original: str,
    match: re.Match[str],
) -> tuple[int, int, str] | None:
    parts = [match.group("base")]
    index = match.end()
    first_debt_index: int | None = None
    called_after_debt = False
    end = index

    while True:
        index = _skip_frontend_whitespace(masked, index)
        if index >= len(masked):
            break

        if masked[index] == "(":
            index = _skip_frontend_balanced_call(masked, index)
            end = index
            if first_debt_index is not None:
                called_after_debt = True
            continue

        if masked.startswith("?.", index):
            index = _skip_frontend_whitespace(masked, index + 2)
            if index < len(masked) and masked[index] == "(":
                index = _skip_frontend_balanced_call(masked, index)
                end = index
                if first_debt_index is not None:
                    called_after_debt = True
                continue
        elif masked[index] == ".":
            index = _skip_frontend_whitespace(masked, index + 1)

        if index < len(masked) and masked[index] == "[":
            computed = _read_frontend_computed_property(masked, original, index)
            if computed is None:
                break
            name, index, property_start, is_static_literal = computed
            parts.append(name)
            end = index
            if (
                name in _FRONTEND_DEBT_METHODS
                or not is_static_literal
                and name == _FRONTEND_UNRESOLVED_COMPUTED_MEMBER
            ) and first_debt_index is None:
                first_debt_index = property_start
            continue

        identifier_start = index
        identifier = _read_frontend_identifier(masked, identifier_start)
        if identifier is None:
            break

        name, index = identifier
        parts.append(name)
        end = index
        if name in _FRONTEND_DEBT_METHODS and first_debt_index is None:
            first_debt_index = identifier_start

    if first_debt_index is None or not called_after_debt:
        return None

    return first_debt_index, end, ".".join(parts)


def _is_frontend_test_file(path: Path) -> bool:
    if path.suffix not in _FRONTEND_TEST_SUFFIXES:
        return False
    relative = path.relative_to(REPO_ROOT)
    if relative.parts[:2] == ("frontend", "e2e"):
        return True
    if relative.parts[:2] != ("frontend", "src"):
        return False
    return "__tests__" in relative.parts or ".test." in path.name or ".spec." in path.name


def _frontend_fingerprint(path: Path, callee: str, source: str) -> str:
    normalized_source = re.sub(r"\s+", " ", source).strip()
    raw = "|".join((path.as_posix(), callee, normalized_source))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _scan_frontend_source(source: str, path: Path) -> list[_FrontendDebtSite]:
    masked = _mask_frontend_comments_and_strings(source)
    lines = source.splitlines()
    sites: list[_FrontendDebtSite] = []
    for match in _FRONTEND_DEBT_BASE_PATTERN.finditer(masked):
        debt_chain = _frontend_debt_chain(masked, source, match)
        if debt_chain is None:
            continue

        debt_start, _end, callee = debt_chain
        line = masked.count("\n", 0, debt_start) + 1
        source_line = lines[line - 1].strip()
        sites.append(
            _FrontendDebtSite(
                id=_frontend_fingerprint(path, callee, source_line),
                path=path,
                line=line,
                callee=callee,
                source=source_line,
            )
        )
    return sites
