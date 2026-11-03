"""Why the project's pre-commit hook refused a milestone, said in the editor's terms.

The hook ``haute init`` scaffolds prints each problem ruff cannot fix safely as
``<file>:<line>:<column>: <code> <message>``, at the lines of the file as the hook left
it. A milestone's refusal names each problem by the node whose code holds it (or by its
file and line outside a node), quotes the line, and says what to do.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

#: Where the editor's documentation says how to handle a refused milestone.
REFUSAL_GUIDE_URL = (
    "https://pricingfrontier.github.io/haute/building-models/versions/#when-a-milestone-is-refused"
)
#: The problems a refusal lists; the rest are counted.
_LISTED = 5
#: The longest quoted line before it is cut short.
_QUOTED = 60
#: The objects a pipeline or submodel file registers its nodes on.
_RECEIVERS = frozenset({"pipeline", "submodel"})

_DIAGNOSTIC = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+):(?P<column>\d+): (?P<code>[A-Z]+[0-9]+) (?P<message>.+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class LintProblem:
    """One problem the hook reported: a file relative to the repository, a 1-based line."""

    path: str
    line: int
    code: str
    message: str


def lint_problems(output: str) -> list[LintProblem]:
    """The problems in what the hook printed, in the order it printed them."""
    return [
        LintProblem(
            path=PurePath(match["path"].strip().replace("\\", "/")).as_posix(),
            line=int(match["line"]),
            code=match["code"],
            message=match["message"].strip(),
        )
        for match in _DIAGNOSTIC.finditer(output)
    ]


def explain_lint_refusal(output: str, repository: Path) -> str | None:
    """The refusal to show for what the pre-commit hook printed, or ``None`` when it printed
    no problem in ruff's form (another hook, or ruff failing to run), whose own words stand.

    *repository* is the folder the hook ran in, which the reported paths are relative to.
    """
    problems = lint_problems(output)
    if not problems:
        return None
    count = len(problems)
    lines = [
        f"the project's pre-commit hook found {count} problem{'' if count == 1 else 's'} "
        "to fix first:"
    ]
    lines.extend(f"- {_describe(problem, repository)}" for problem in problems[:_LISTED])
    if count > _LISTED:
        lines.append(f"- and {count - _LISTED} more")
    lines.append(
        "Change each line, save, and commit again. To keep a line as it is, end it with "
        f"`# noqa: {problems[0].code}` (its code). More: {REFUSAL_GUIDE_URL}"
    )
    return "\n".join(lines)


def _describe(problem: LintProblem, repository: Path) -> str:
    """``Node `name`: `<line>` - <message> (<code>)``, or the file and line outside a node."""
    what = f"{problem.message} ({problem.code})"
    try:
        source = (repository / problem.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"{problem.path}, line {problem.line}: {what}"
    lines = source.splitlines()
    quoted = lines[problem.line - 1].strip() if 0 < problem.line <= len(lines) else ""
    if len(quoted) > _QUOTED:
        quoted = f"{quoted[: _QUOTED - 3]}..."
    code = f"`{quoted}` - " if quoted else ""
    node = _enclosing_node(source, problem.line)
    if node is not None:
        return f"Node `{node}`: {code}{what}"
    return f"{problem.path}, line {problem.line}: {code}{what}"


def _enclosing_node(source: str, line: int) -> str | None:
    """The node whose function holds *line*, decorators included: a top-level function
    registered on ``pipeline`` or ``submodel``; ``None`` outside one or in a file that does
    not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for statement in tree.body:
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not any(_registers_a_node(decorator) for decorator in statement.decorator_list):
            continue
        start = min(decorator.lineno for decorator in statement.decorator_list)
        if start <= line <= (statement.end_lineno or statement.lineno):
            return statement.name
    return None


def _registers_a_node(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id in _RECEIVERS
    )
