"""``haute init`` command.

Split into:

* :class:`InitConfig` — the typed bag of CLI inputs.
* :func:`handle_init` — the pure function that scaffolds a new project, or sets an existing
  one up again.
* :func:`init` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from haute._gitignore_guard import ensure_gitignore_guards
from haute._io import read_user_text

if TYPE_CHECKING:
    from haute._connections import Connection


@dataclass
class InitConfig:
    """Parsed inputs for the ``haute init`` command: each answer its flag gave, None for one
    to ask at a terminal (*interactive*) or to take the default of without one."""

    #: An automated project's deploy target.
    target: str | None
    ci: str | None
    #: Whether the project prices in the workbench, which hosts no scoring API.
    workbench: bool | None = False
    #: A workbench project's app host, its quote store's database and the analyst's sign-in.
    host: str | None = None
    store: str | None = None
    sign_in: str | None = None
    #: Whether ``haute init`` runs at a terminal, where it asks what its flags leave out.
    interactive: bool = False


_DEV_DEPS_BLOCK = """
[dependency-groups]
dev = [
    "ruff>=0.8",
    "mypy>=1.13",
    "pytest>=8.3",
]
"""

_MYPY_BLOCK = """
[tool.mypy]
ignore_missing_imports = false
# The pipeline imports its utility and submodel modules from its own folder, as
# Python does when it runs rating/main.py.
mypy_path = "rating"
explicit_package_bases = true

[[tool.mypy.overrides]]
module = ["haute.*", "catboost.*", "xgboost.*", "lightgbm.*", "sklearn.*"]
ignore_missing_imports = true
"""


def _dependencies_contain_haute(deps: list[str]) -> bool:
    """Return ``True`` iff *deps* contains a requirement whose canonical
    distribution name is exactly ``haute``.

    Uses :class:`packaging.requirements.Requirement` to parse each entry
    so that version specifiers (``haute>=1``), extras (``haute[databricks]``),
    and unrelated packages with ``haute`` in their name (``haute-utils``)
    are handled correctly.
    """
    for dep in deps:
        if not isinstance(dep, str):
            continue
        req = Requirement(dep)
        if canonicalize_name(req.name) == "haute":
            return True
    return False


def _scan_table_headers(text: str) -> list[tuple[int, int, str]]:
    """Yield ``(line_start, line_end, header_name)`` for every TOML table
    header in *text* that lives OUTSIDE any string, comment, or
    array-of-tables section.

    ``line_start`` is the offset of the ``[`` at column 0. ``line_end`` is
    the offset just past the closing ``\\n`` (or len(text)). The regex-based
    scanner it replaces was fooled by ``[foo]`` lines inside triple-quoted
    string bodies — this walker tracks string state so the same content
    inside a string is ignored.
    """
    headers: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    at_line_start = True
    while i < n:
        c = text[i]
        if c == "\n":
            at_line_start = True
            i += 1
            continue
        if c == "#":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == '"' or c == "'":
            quote = c
            if text[i : i + 3] == quote * 3:
                end = text.find(quote * 3, i + 3)
                if end == -1:
                    return headers
                i = end + 3
                at_line_start = False
                continue
            i += 1
            while i < n and text[i] != quote and text[i] != "\n":
                if quote == '"' and text[i] == "\\":
                    i += 2
                    continue
                i += 1
            if i < n and text[i] == quote:
                i += 1
            at_line_start = False
            continue
        if at_line_start and c == "[":
            if text[i : i + 2] == "[[":
                i += 2
                at_line_start = False
                continue
            close = text.find("]", i)
            nl = text.find("\n", i)
            if close == -1:
                return headers
            if nl != -1 and close > nl:
                i = nl + 1
                at_line_start = True
                continue
            header = text[i + 1 : close].strip()
            line_end = n if nl == -1 else nl + 1
            headers.append((i, line_end, header))
            i = line_end
            at_line_start = True
            continue
        if not c.isspace():
            at_line_start = False
        i += 1
    return headers


def _header_key_path(header: str) -> list[str]:
    """Return the dotted-key path of a TOML table *header*'s bracket content.

    The scanner hands us the raw text between the brackets, which may use any
    TOML key syntax: bare (``project``), quoted (``"project"`` / ``'project'``),
    or dotted (``project.optional-dependencies``). We resolve it to the same
    key path tomllib would, so header matching agrees with the parser instead
    of doing a brittle raw-string compare.

    The caller has already validated the whole file as TOML, so *header* is
    guaranteed to be syntactically valid key syntax.
    """
    parsed = tomllib.loads(header + " = 0")
    path: list[str] = []
    node: object = parsed
    while isinstance(node, dict) and len(node) == 1:
        key = next(iter(node))
        path.append(key)
        node = node[key]
    return path


def _find_project_table_bounds(text: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of the ``[project]`` table body.

    ``start`` points to the first character *after* the ``[project]`` header
    line. ``end`` points to the start of the next table header (or len(text)
    if ``[project]`` is the final table). Returns ``None`` if no ``[project]``
    table is present.

    Header matching is normalised through :func:`_header_key_path` so a quoted
    header (``["project"]``) is recognised as the project table while a dotted
    subtable (``[project.optional-dependencies]``) is not.
    """
    headers = _scan_table_headers(text)
    for idx, (line_start, line_end, name) in enumerate(headers):
        if _header_key_path(name) == ["project"]:
            start = line_end
            if idx + 1 < len(headers):
                end = headers[idx + 1][0]
            else:
                end = len(text)
            return start, end
    return None


# Matches ``dependencies = [`` with arbitrary whitespace around ``=``. Must be
# at the start of a line (after optional indentation) so we don't pick up
# ``optional-dependencies`` or any other key that ends in ``dependencies``.
# The optional leading ``[ \t]*`` lets us match an indented key inside a
# [project] table body (TOML permits leading whitespace on key lines).
_DEPENDENCIES_KEY_RE = re.compile(r"^[ \t]*dependencies\s*=\s*\[", re.MULTILINE)
_DOTTED_PROJECT_DEPENDENCIES_KEY_RE = re.compile(
    r"^[ \t]*project\s*\.\s*dependencies\s*=\s*\[",
    re.MULTILINE,
)


def _toml_basic_string(value: str) -> str:
    """Serialise *value* as a TOML basic string (double-quoted, escaped).

    Dependency values are read back from :mod:`tomllib` as plain Python
    strings, so re-emitting them requires proper escaping — a PEP 508
    environment marker such as ``foo; python_version >= "3.11"`` contains
    double quotes that would otherwise close the string early and produce
    invalid TOML. Escapes backslash, double-quote, the named control-char
    shorthands, and any remaining control characters as ``\\uXXXX``.
    """
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _find_matching_bracket(text: str, open_idx: int) -> int:
    """Return the index of the ``]`` that matches the ``[`` at *open_idx*.

    Handles basic TOML string quoting so brackets inside strings don't
    confuse the scan. Raises ``ValueError`` if the bracket is unbalanced.
    """
    assert text[open_idx] == "["
    depth = 1
    i = open_idx + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            # Skip to the matching closing quote (not escape-aware for simple
            # basic strings, but TOML dependency entries don't contain escaped
            # quotes in practice).
            quote = c
            # Handle triple-quoted strings.
            if text[i : i + 3] == quote * 3:
                end = text.find(quote * 3, i + 3)
                if end == -1:
                    raise ValueError("unterminated triple-quoted string in TOML")
                i = end + 3
                continue
            # Basic string: find the next unescaped quote on the same logical
            # line (TOML basic strings don't span lines).
            i += 1
            while i < n and text[i] != quote:
                if quote == '"' and text[i] == "\\":
                    i += 2
                    continue
                i += 1
            if i >= n:
                raise ValueError("unterminated string in TOML")
            i += 1
            continue
        if c == "#":
            # Comment — skip to end of line.
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced '[' in TOML")


def _rewrite_dependencies_array_at(
    text: str, open_bracket_abs: int, edit: Callable[[list[str]], list[str]]
) -> str:
    """Rewrite the TOML array beginning at *open_bracket_abs* as *edit* makes its entries."""
    close_bracket_abs = _find_matching_bracket(text, open_bracket_abs)

    # Replace the array content. Normalise to one item per line with a
    # four-space indent for consistency with the existing scaffold style.
    array_text = text[open_bracket_abs + 1 : close_bracket_abs]
    # Re-parse the single-table slice to discover current entries structurally
    # (tomllib on the full file already succeeded, so this slice is valid).
    current_deps: list[str] = tomllib.loads("dependencies = [" + array_text + "]")["dependencies"]

    new_deps = edit(current_deps)
    # Preserve trailing newline conventions by formatting the replacement
    # body as ``\n    "a",\n    "b",\n`` (one dep per line, trailing comma).
    # Each dependency is escaped as a TOML basic string so values containing
    # double quotes (env markers) or backslashes round-trip to valid TOML.
    new_array_body = "\n" + "".join(f"    {_toml_basic_string(dep)},\n" for dep in new_deps)

    return text[: open_bracket_abs + 1] + new_array_body + text[close_bracket_abs:]


def _insert_root_dotted_project_dependencies(text: str, requirement: str = "haute") -> str:
    """Insert a top-level ``project.dependencies`` key, listing *requirement*, before any
    table header."""
    headers = _scan_table_headers(text)
    insert_at = headers[0][0] if headers else len(text)
    prefix = text[:insert_at]
    suffix = text[insert_at:]
    before = "" if not prefix or prefix.endswith("\n") else "\n"
    after = "" if not suffix or suffix.startswith("\n") else "\n"
    insertion = f"project.dependencies = [\n    {_toml_basic_string(requirement)},\n]\n"
    return prefix + before + insertion + after + suffix


def _project_dependencies_bracket(text: str) -> int | None:
    """The offset of the ``[`` opening ``[project].dependencies``' array in *text*, written in
    a ``[project]`` table or as a root ``project.dependencies`` key; None when neither holds
    one."""
    bounds = _find_project_table_bounds(text)
    if bounds is None:
        # ``project.dependencies = [...]`` creates the parsed project table without a textual
        # [project] header: its array is rewritten in place, since a second [project] table is
        # refused as "Cannot declare ('project',) twice".
        headers = _scan_table_headers(text)
        root_end = headers[0][0] if headers else len(text)
        dotted = _DOTTED_PROJECT_DEPENDENCIES_KEY_RE.search(text[:root_end])
        return None if dotted is None else dotted.end() - 1
    body_start, body_end = bounds
    found = _DEPENDENCIES_KEY_RE.search(text[body_start:body_end])
    return None if found is None else body_start + found.end() - 1


def _rewrite_project_dependencies(text: str, requirement: str = "haute") -> str:
    """Return *text* with *requirement*, ``"haute"`` or ``haute`` with an extra, injected
    into ``[project].dependencies``.

    This is a structural edit: it uses :mod:`tomllib` to validate the file and
    locate the ``[project]`` table and its ``dependencies`` array, then rewrites
    only the array literal. Comments outside the array and other tables are
    preserved. Raises :class:`ValueError` if the TOML is malformed or the
    ``[project]`` table cannot be located textually.

    The caller is responsible for having verified that ``haute`` is not already
    present in the parsed ``[project].dependencies`` list — this function is a
    no-op wrapper for the textual edit and does not re-check.
    """
    # Validate that the file parses as TOML before we mutate it — better to
    # fail loudly than silently corrupt a broken file further.
    parsed = tomllib.loads(text)

    open_bracket_abs = _project_dependencies_bracket(text)
    if open_bracket_abs is not None:
        return _rewrite_dependencies_array_at(
            text, open_bracket_abs, lambda deps: [requirement, *deps]
        )
    bounds = _find_project_table_bounds(text)
    if bounds is not None:
        # [project] exists but no dependencies key — insert one right after the header.
        body_start = bounds[0]
        insertion = f"dependencies = [\n    {_toml_basic_string(requirement)},\n]\n"
        return text[:body_start] + insertion + text[body_start:]
    if isinstance(parsed.get("project"), dict):
        return _insert_root_dotted_project_dependencies(text, requirement)
    # No [project] table — append a fresh one.
    sep = "" if text.endswith("\n") or not text else "\n"
    listed = _toml_basic_string(requirement)
    return text + sep + f"[project]\ndependencies = [\n    {listed},\n]\n"


def _with_haute_extras(text: str, deps: list[str], extras: Collection[str]) -> str:
    """*text*, whose ``[project].dependencies`` are *deps* and list ``haute``, with that
    requirement given each of *extras* it lacks, its version specifier, marker and other
    extras kept; *text* itself when it has them all. Raises :class:`ValueError` when the
    array cannot be located textually to edit."""

    def with_extras(dep: str) -> str:
        if not isinstance(dep, str):
            return dep
        requirement = Requirement(dep)
        if canonicalize_name(requirement.name) != "haute" or set(extras) <= requirement.extras:
            return dep
        requirement.extras |= set(extras)
        return str(requirement)

    if [with_extras(dep) for dep in deps] == deps:
        return text
    open_bracket_abs = _project_dependencies_bracket(text)
    if open_bracket_abs is None:
        raise ValueError(
            "pyproject.toml lists haute where haute init cannot edit it: add the "
            f"{', '.join(sorted(extras))} extras to it yourself."
        )
    return _rewrite_dependencies_array_at(
        text, open_bracket_abs, lambda current: [with_extras(dep) for dep in current]
    )


def _ensure_haute_dependency(pyproject_path: Path, name: str, requirement: str = "haute") -> bool:
    """Make pyproject.toml's dependencies list *requirement*, ``haute`` or ``haute`` with
    extras; return whether the file changed.

    If pyproject.toml exists, insert *requirement* into the ``[project].dependencies`` list
    using a TOML-aware edit when no ``haute`` requirement is present, and give one that is
    each of *requirement*'s extras it lacks, keeping its version specifier, marker and other
    extras and taking none away. If it doesn't exist, create a minimal pyproject.toml.

    Also ensures a ``[dependency-groups]`` dev section exists with
    ruff, mypy, and pytest so that the generated CI workflows work.

    Detection of ``haute`` as an existing dependency is structural — only
    the parsed ``[project].dependencies`` array is inspected, so comments or
    substring-match packages (e.g. ``haute-utils``) cannot produce false
    positives.
    """
    if not pyproject_path.exists():
        pyproject_path.write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
            f'requires-python = ">=3.11"\n'
            f"dependencies = [\n    {_toml_basic_string(requirement)},\n]\n"
            + _DEV_DEPS_BLOCK
            + _MYPY_BLOCK,
            encoding="utf-8",
        )
        return True

    original = read_user_text(pyproject_path)

    # Parse structurally to detect an existing ``haute`` entry.
    parsed = tomllib.loads(original)
    project = parsed.get("project")
    if project is not None and not isinstance(project, dict):
        # ``[[project]]`` (array-of-tables) makes ``parsed['project']`` a list.
        # That is invalid for a PEP 621 pyproject and there is no single
        # ``[project]`` table to edit — fail loudly rather than crash on a
        # later attribute access or silently corrupt the file.
        raise ValueError(
            "pyproject.toml [project] must be a table, not an array-of-tables ([[project]])."
        )
    project_deps = project.get("dependencies", []) if project is not None else []
    if not isinstance(project_deps, list):
        project_deps = []

    if _dependencies_contain_haute(project_deps):
        text = _with_haute_extras(original, project_deps, Requirement(requirement).extras)
    else:
        text = _rewrite_project_dependencies(original, requirement)

    # Re-parse after mutation to validate and to check whether the remaining
    # scaffold blocks are already present — we use structural checks (not raw
    # string scans) so comments can't fool us.
    parsed = tomllib.loads(text)
    has_dep_groups = "dependency-groups" in parsed
    has_tool_mypy = isinstance(parsed.get("tool", {}), dict) and "mypy" in parsed.get("tool", {})

    if not has_dep_groups:
        text += _DEV_DEPS_BLOCK
    if not has_tool_mypy:
        text += _MYPY_BLOCK

    if text == original:
        return False
    pyproject_path.write_text(text, encoding="utf-8")
    return True


#: Every CI file Haute generates, relative to the project root: both kinds of project's, for
#: every provider. ``haute init --force`` and ``haute ci update`` prune each one the set they
#: write does not hold, so a switch of provider (github -> gitlab, say) or of the kind of
#: project leaves no orphaned workflow behind.
GENERATED_CI_FILES: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    ".github/workflows/deploy-staging.yml",
    ".github/workflows/deploy-production.yml",
    ".github/workflows/release.yml",
    ".github/workflows/release-production.yml",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
)


def _prune_stale_ci_files(project_dir: Path, keep: Collection[str]) -> list[str]:
    """Remove each of :data:`GENERATED_CI_FILES` that *keep*, the set about to be written,
    does not hold; return those removed.

    Only touches the specific files Haute generates. When no GitHub workflow is kept, the
    now-empty ``.github/workflows`` and ``.github`` directories are cleaned up (but only if
    empty, so a user's other GitHub config is left untouched).
    """
    removed: list[str] = []
    for rel in GENERATED_CI_FILES:
        if rel in keep:
            continue
        path = project_dir / rel
        if path.exists():
            path.unlink()
            removed.append(rel)

    if not any(rel.startswith(".github/") for rel in keep):
        workflows = project_dir / ".github" / "workflows"
        if workflows.is_dir() and not any(workflows.iterdir()):
            workflows.rmdir()
        github = project_dir / ".github"
        if github.is_dir() and not any(github.iterdir()):
            github.rmdir()
    return removed


def write_generated_files(project_dir: Path, files: Mapping[str, str]) -> list[str]:
    """Write each generated file (path relative to *project_dir*); return those that changed.

    A file whose content is already the generated content is left untouched.
    """
    changed: list[str] = []
    for rel, content in files.items():
        path = project_dir / rel
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        changed.append(rel)
    return changed


def _form_folder_problem(project_dir: Path, relative: str, files: Mapping[str, str]) -> str | None:
    """Why the form folder at *relative* cannot be written with *files*: the first path that
    is no ordinary one of the project's (a link or junction on the way, one leading out of
    the project, a reserved name), said as the refusal; None when every path is."""
    from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem

    for path in files:
        _, problem = ordinary_path_problem(project_dir, f"{relative}/{path}")
        if problem is not None:
            return f"{relative}/{path} {PATH_PROBLEMS[problem]}."
    return None


def _root_file_problem(project_dir: Path, *names: str) -> str | None:
    """Why a file at the project root, of *names*, cannot be written: the first one that is
    no ordinary file of the project's (a link or junction, dangling or not, or a reserved
    name), said as the refusal; None when each is."""
    from haute._artifact_paths import PATH_PROBLEMS, ordinary_path_problem

    for name in names:
        _, problem = ordinary_path_problem(project_dir, name)
        if problem is not None:
            return f"{name} {PATH_PROBLEMS[problem]}."
    return None


def _write_form_folder(folder: Path, files: dict[str, str]) -> None:
    """The form folder's *files*, each its text by its path from *folder*, written as LF bytes,
    as the folder's own saves write them, so its first save rewrites none of them."""
    for path, text in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))


# ── The questions ──────────────────────────────────────────────────────

#: The deploy targets an automated project is offered, in the order ``--target`` lists them.
TARGET_CHOICES = ("databricks", "container", "azure-container-apps", "aws-ecs", "gcp-run")
#: The CI providers, in the order ``--ci`` lists them.
CI_CHOICES = ("github", "gitlab", "azure-devops", "none")
#: Where a workbench project's underwriter app runs: ``haute._workbench_config.APP_HOSTS``,
#: sorted (a test keeps them equal; the CLI imports neither module to list them).
HOST_CHOICES = ("azure-container-apps",)
#: The databases a workbench project's saved quotes can be on:
#: ``haute._scaffold.WORKBENCH_STORES`` (a test keeps them equal).
STORE_CHOICES = ("sqlserver", "postgresql")
#: How the analyst signs in to a workbench project's database, in the order the question
#: lists them: ``haute._connections.CONNECTION_SIGN_INS`` (a test keeps them equal).
SIGN_IN_CHOICES = ("password", "entra", "service-principal")

# Each question's options: the answer, as its flag takes it, and the label the list shows.
_PRICING_OPTIONS = (
    ("automated", "Automated: an API other systems call"),
    ("workbench", "Workbench: an app underwriters price quotes in"),
)
_CI_LABELS = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "azure-devops": "Azure DevOps",
    "none": "None",
}
_TARGET_LABELS = {
    "databricks": "Databricks",
    "container": "A Docker container",
    "azure-container-apps": "Azure Container Apps (build and push only)",
    "aws-ecs": "AWS ECS (build and push only)",
    "gcp-run": "GCP Cloud Run (build and push only)",
}
_HOST_LABELS = {"azure-container-apps": "Azure Container Apps"}
_STORE_LABELS = {"sqlserver": "SQL Server", "postgresql": "PostgreSQL"}
_SIGN_IN_LABELS = {
    "password": "Password",
    "entra": "Microsoft Entra ID",
    "service-principal": "Service principal",
}


@dataclass(frozen=True, slots=True)
class _Answers:
    """``haute init``'s answers, every question answered: an automated project's *target*,
    or a workbench project's *host*, *store* and *sign_in*, the last None when
    ``connections.toml`` is kept and the question not asked."""

    workbench: bool
    ci: str
    target: str | None = None
    host: str | None = None
    store: str | None = None
    sign_in: str | None = None


def _at_terminal() -> bool:
    """Whether ``haute init`` runs at a terminal, where it may ask what its flags leave out."""
    return (
        sys.stdin is not None
        and sys.stdin.isatty()
        and sys.stdout is not None
        and sys.stdout.isatty()
    )


def _pick(question: str, options: Sequence[tuple[str, str]], default: str | None) -> str:
    """Ask *question* as a numbered list of *options*, each an answer and its label, and take
    the number picked; Enter takes *default*, and a question without one waits for a pick."""
    click.echo(question)
    for position, (_, label) in enumerate(options, start=1):
        click.echo(f"  {position}. {label}")
    answers = [answer for answer, _ in options]
    default_number = None if default is None else answers.index(default) + 1
    picked: int = click.prompt(
        "Choose", type=click.IntRange(1, len(options)), default=default_number
    )
    return answers[picked - 1]


def _labelled(choices: Sequence[str], labels: Mapping[str, str]) -> list[tuple[str, str]]:
    return [(choice, labels[choice]) for choice in choices]


@dataclass(frozen=True, slots=True)
class _Defaults:
    """The answer Enter takes to each of ``haute init``'s questions, and the one a run without
    a terminal takes; None for a question without one."""

    workbench: bool
    ci: str | None
    target: str | None
    store: str | None
    sign_in: str | None


#: A new project's defaults: automated, on GitHub, deployed to Databricks; a workbench's
#: database and sign-in have none.
NEW_PROJECT_DEFAULTS = _Defaults(
    workbench=False, ci="github", target="databricks", store=None, sign_in=None
)


def _read_haute_toml(path: Path) -> dict[str, Any]:
    """An existing project's ``haute.toml``, parsed; a file that does not read as TOML is
    refused, since a re-run takes its answers from it."""
    try:
        return tomllib.loads(read_user_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"haute.toml is not valid TOML ({exc}): haute init reads the project's current "
            "answers from it, so fix it first."
        ) from exc


def _table(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = document.get(key)
    return value if isinstance(value, dict) else {}


def _one_of(values: Collection[object], choices: Sequence[str]) -> str | None:
    """The one value of *values* when there is exactly one and it is among *choices*."""
    if len(values) != 1:
        return None
    (value,) = values
    return value if isinstance(value, str) and value in choices else None


def _current_answers(
    document: Mapping[str, Any], connections: Mapping[str, Connection]
) -> _Defaults:
    """An existing project's current answers, the defaults of a re-run: read from its
    ``haute.toml``, *document*, and its *connections*, each None where the project holds no
    single answer the question offers."""
    workbench = _table(document, "workbench")
    stores = {
        environment.get("provider")
        for environment in _table(workbench, "store").values()
        if isinstance(environment, dict)
    }
    sign_ins = {
        connection.auth
        for connection in connections.values()
        if connection.provider in STORE_CHOICES
    }
    return _Defaults(
        workbench=workbench.get("enabled") is True,
        ci=_one_of({_table(document, "ci").get("provider")}, CI_CHOICES),
        target=_one_of({_table(document, "deploy").get("target")}, TARGET_CHOICES),
        store=_one_of(stores, STORE_CHOICES),
        sign_in=_one_of(sign_ins, SIGN_IN_CHOICES),
    )


def _writes_connections(connections: Mapping[str, Connection] | None, store: str) -> bool:
    """Whether a workbench on *store* has ``connections.toml`` written: when the project has
    none (*connections* None), or when the connection of an environment ``haute.toml`` will
    declare on *store* (``WORKBENCH_ENVIRONMENTS``) is on another database, SQLite included,
    which the quote store would refuse as another provider than its environment's."""
    from haute._scaffold import WORKBENCH_ENVIRONMENTS

    if connections is None:
        return True
    return any(
        connections[name].provider != store
        for name in WORKBENCH_ENVIRONMENTS
        if name in connections
    )


def _ask(
    ask: bool,
    question: str,
    options: Sequence[tuple[str, str]],
    default: str | None,
    missing: str,
) -> str:
    """The answer to *question*: picked at a terminal (*ask*), else its *default*; without a
    default a usage error, *missing*, naming the flag that answers it."""
    if ask:
        return _pick(question, options, default)
    if default is None:
        raise click.UsageError(missing)
    return default


def _answers(
    config: InitConfig, defaults: _Defaults, connections: Mapping[str, Connection] | None
) -> _Answers:
    """Every one of ``haute init``'s answers: each flag's, then each question the flags leave
    out asked in turn at a terminal (``config.interactive``), Enter taking its *defaults*.
    Without one, a question takes its default, and one without, a new workbench's database and
    sign-in or a re-run's question the project holds no answer to, is a usage error naming its
    flag, as is a flag of the other kind of project. The sign-in is asked only when
    ``connections.toml``, *connections* (None without one), is to be written."""
    ask = config.interactive
    workbench = config.workbench
    if workbench is None:
        workbench = (
            _pick(
                "How will this project price quotes?",
                _PRICING_OPTIONS,
                "workbench" if defaults.workbench else "automated",
            )
            == "workbench"
            if ask
            else defaults.workbench
        )
    if workbench and config.target is not None:
        raise click.UsageError(
            "--target is an automated project's deploy target; a workbench project hosts no "
            "scoring API, and its app's host is --host."
        )
    if not workbench:
        given = [
            flag
            for flag, value in (
                ("--host", config.host),
                ("--store", config.store),
                ("--sign-in", config.sign_in),
            )
            if value is not None
        ]
        if given:
            raise click.UsageError(
                f"{', '.join(given)} answer a workbench project's questions; pass --workbench "
                "with them."
            )
    ci = config.ci
    if ci is None:
        ci = _ask(
            ask,
            "Where is the repository?",
            _labelled(CI_CHOICES, _CI_LABELS),
            defaults.ci,
            f"Name where the repository is: --ci ({', '.join(CI_CHOICES)}).",
        )
    if not workbench:
        target = config.target
        if target is None:
            target = _ask(
                ask,
                "Where will the API run?",
                _labelled(TARGET_CHOICES, _TARGET_LABELS),
                defaults.target,
                f"Name where the API runs: --target ({', '.join(TARGET_CHOICES)}).",
            )
        return _Answers(workbench=False, ci=ci, target=target)

    hosts = HOST_CHOICES
    host = config.host
    if host is None:
        # With one host there is one answer: Enter takes it, as does a run without a terminal.
        host = _ask(
            ask,
            "Where will the app run?",
            _labelled(hosts, _HOST_LABELS),
            hosts[0] if len(hosts) == 1 else None,
            f"Name where the app runs: --host ({', '.join(hosts)}).",
        )
    store = config.store
    if store is None:
        store = _ask(
            ask,
            "Which database holds the saved quotes?",
            _labelled(STORE_CHOICES, _STORE_LABELS),
            defaults.store,
            f"Name the database the saved quotes are on: --store ({' or '.join(STORE_CHOICES)}).",
        )
    sign_in = config.sign_in
    if sign_in is None and _writes_connections(connections, store):
        sign_in = _ask(
            ask,
            "How do you sign in to it?",
            _labelled(SIGN_IN_CHOICES, _SIGN_IN_LABELS),
            defaults.sign_in,
            f"Name how you sign in to that database: --sign-in ({', '.join(SIGN_IN_CHOICES)}).",
        )
    return _Answers(workbench=True, ci=ci, host=host, store=store, sign_in=sign_in)


# ── The scaffold ───────────────────────────────────────────────────────


def _is_haute_hook(path: Path) -> bool:
    """Whether the git hook at *path* is Haute's, of any version: it holds the comment that
    marks Haute's hook. Another is the team's own, which ``haute init`` leaves as it is."""
    from haute._scaffold import PRE_COMMIT_HOOK_MARK

    return PRE_COMMIT_HOOK_MARK in read_user_text(path)


#: What a change of the kind of project leaves in the pipeline to replace in the editor, by
#: whether the project is now a workbench.
_NODES_TO_REPLACE = {
    True: (
        "Replace the pipeline's Quote Input and Quote Response, if it has them, by a "
        "Workbench Input and a Workbench Output."
    ),
    False: (
        "Replace the pipeline's Workbench Input and Workbench Output, if it has them, by a "
        "Quote Input and a Quote Response; rating/workbench/ and connections.toml are left "
        "as they are."
    ),
}


def handle_init(config: InitConfig) -> None:
    """Scaffold a Haute project at :func:`pathlib.Path.cwd`, or set an existing one up again.

    Pure function version of the ``haute init`` command — takes a typed
    :class:`InitConfig`, takes the answers its flags leave out (:func:`_answers`) and writes
    the scaffold: an automated project's for its deploy target, or a workbench project's,
    which hosts no scoring API, for its app's host and the quote store's database. A new
    project gets each starter file of ``rating/`` and ``tests/`` where it has none. A project
    that already has a ``haute.toml`` is set up again: its current answers are the
    questions' defaults (:func:`_current_answers`), ``haute.toml`` is written afresh from the
    answers and the workflows, ``.env.example`` and the hook again, and nothing is written in
    ``rating/`` or ``tests/`` but a workbench's form folder where it has none.
    """
    from haute._connections import CONNECTIONS_FILE, ConnectionsError, read_connections
    from haute._project_layout import PIPELINE_FILES_FOLDER, PIPELINE_FILES_SUBFOLDERS
    from haute._project_python import ProjectPythonError, project_python_version
    from haute._scaffold import (
        app_env_example,
        ci_files,
        ci_secret_names,
        connection_variables,
        connections_toml,
        env_example,
        haute_toml,
        pre_commit_hook,
        starter_form,
        starter_pipeline,
        starter_test,
        starter_test_quote,
        starter_utility_features,
        starter_utility_init,
        workbench_ci_files,
        workbench_haute_toml,
    )
    from haute._workbench_config import FORM_FOLDER

    # Solo mode is configured in haute.toml, not via a CLI flag.
    # Default is team mode; user sets min_approvers = 0 for solo.

    project_dir = Path.cwd()
    haute_toml_path = project_dir / "haute.toml"
    connections_path = project_dir / CONNECTIONS_FILE

    # -- The project as it is, read before any question ------------------------
    # connections.toml holds the team's connections, which .env.example names the variables
    # of and the sign-in question defaults to: it must read.
    try:
        connections = read_connections(project_dir) if connections_path.exists() else None
    except ConnectionsError as exc:
        raise click.ClickException(str(exc)) from exc
    previous = _read_haute_toml(haute_toml_path) if haute_toml_path.exists() else None
    rerun = previous is not None
    defaults = (
        NEW_PROJECT_DEFAULTS if previous is None else _current_answers(previous, connections or {})
    )

    answers = _answers(config, defaults, connections)
    ci = answers.ci
    workbench = answers.workbench

    # -- The project's Python, read before anything is written -----------------
    try:
        python_version = project_python_version(project_dir)
    except ProjectPythonError as exc:
        raise click.ClickException(str(exc)) from exc

    # -- Resolve project name --------------------------------------------------
    # A re-run keeps the name haute.toml has, which names the model and the endpoint.
    pyproject_path = project_dir / "pyproject.toml"
    name = project_dir.name.replace("-", "_").replace(" ", "_").lower()

    if pyproject_path.exists():
        with open(pyproject_path, "rb") as fh:
            pyproject = tomllib.load(fh)
        if "project" in pyproject and "name" in pyproject["project"]:
            name = pyproject["project"]["name"]
    if previous is not None:
        named = _table(previous, "project").get("name")
        if isinstance(named, str) and named:
            name = named

    # -- rating/workbench/'s paths, checked before anything is written ---------
    # A form folder already there is the analyst's form: never a blank one in its place.
    form_folder = project_dir / "rating" / FORM_FOLDER
    form_files = starter_form(name, form_folder) if workbench and not form_folder.exists() else {}
    problem = _form_folder_problem(project_dir, f"rating/{FORM_FOLDER}", form_files)
    if problem is not None:
        click.echo(f"Error: {problem}", err=True)
        raise SystemExit(1)
    # connections.toml already there holds the team's connections: kept unless the connection
    # of an environment haute.toml declares is on another database, a changed quote store.
    write_connections = answers.store is not None and _writes_connections(
        connections, answers.store
    )
    problem = _root_file_problem(project_dir, CONNECTIONS_FILE) if write_connections else None
    if problem is not None:
        click.echo(f"Error: {problem}", err=True)
        raise SystemExit(1)

    # -- pyproject.toml - ensure haute is a dependency -------------------------
    # A workbench project's release installs its quote store's driver, and its CI rolls the
    # hosted app out through the Azure management packages, so haute comes with both extras.
    requirement = f"haute[{answers.store},azure]" if workbench else "haute"
    pyproject_changed = _ensure_haute_dependency(pyproject_path, name, requirement)

    # -- .python-version - one Python for local, CI and the deployed model -----
    python_version_file = project_dir / ".python-version"
    if not python_version_file.exists():
        python_version_file.write_text(f"{python_version}\n", encoding="utf-8")

    # -- Directories -----------------------------------------------------------
    (project_dir / "data").mkdir(exist_ok=True)

    # -- Remove the root placeholder created by tools such as ``uv init`` ------
    root_main = project_dir / "main.py"
    if root_main.exists():
        root_main.unlink()
        click.echo("  Removed root main.py (Haute uses rating/main.py).")

    # -- rating/ and tests/ - a new project's starter files ---------------------
    # Each is written only where there is none, and a re-run writes none: the pipeline, its
    # utility modules, the tests and the test quotes are the project's.
    kept: list[str] = []
    if not rerun:
        starters = {
            "rating/__init__.py": "",
            "rating/utility/__init__.py": starter_utility_init(),
            "rating/utility/features.py": starter_utility_features(),
            "rating/main.py": starter_pipeline(name),
            "tests/test_pipeline.py": starter_test(name),
        }
        # A workbench's test quotes are saved from Preview, the folder made with the first; an
        # automated project starts with an example in deploy's shape.
        if not workbench:
            starters["tests/quotes/example.json"] = starter_test_quote()
        for relative, content in starters.items():
            starter = project_dir / relative
            if starter.exists():
                kept.append(relative)
                continue
            starter.parent.mkdir(parents=True, exist_ok=True)
            starter.write_text(content, encoding="utf-8")
        # rating/config/ and rating/files/ - node settings, versioned data and models.
        (project_dir / "rating" / "config").mkdir(exist_ok=True)
        for sub in PIPELINE_FILES_SUBFOLDERS:
            (project_dir / "rating" / PIPELINE_FILES_FOLDER / sub).mkdir(
                parents=True, exist_ok=True
            )

    # -- haute.toml - project, deploy or app image, safety, CI and workbench config --
    # Written afresh from the answers; a re-run's previous file is left to git.
    if workbench:
        assert answers.host is not None and answers.store is not None
        toml_text = workbench_haute_toml(
            name, ci, host=answers.host, store=answers.store, python_version=python_version
        )
    else:
        assert answers.target is not None
        toml_text = haute_toml(name, answers.target, ci, python_version=python_version)
    haute_toml_path.write_text(toml_text, encoding="utf-8")

    # -- rating/workbench/ - the workbench's blank form folder ------------------
    if form_files:
        _write_form_folder(form_folder, form_files)

    # -- connections.toml - each environment's database, for this machine -------
    if write_connections:
        assert answers.store is not None and answers.sign_in is not None
        connections_path.write_text(
            connections_toml(answers.store, answers.sign_in), encoding="utf-8"
        )

    # -- .env.example, the pre-commit hook and the CI/CD workflow files ---------
    variables = connection_variables(project_dir)
    credentials = answers.host if workbench else answers.target
    assert credentials is not None
    env_text = (
        app_env_example(credentials, variables)
        if workbench
        else env_example(credentials, variables)
    )
    generated_ci = (
        workbench_ci_files(credentials, ci, python_version=python_version)
        if workbench
        else ci_files(credentials, ci, python_version=python_version)
    )
    # A re-run's provider, or kind of project, may have changed since the last run: every
    # generated file the new set does not hold is removed first, so no stale workflow lingers.
    # A new project's folder may hold the team's own workflows, which are not Haute's to remove.
    removed = _prune_stale_ci_files(project_dir, keep=generated_ci.keys()) if rerun else []
    generated = {
        ".env.example": env_text,
        ".githooks/pre-commit": pre_commit_hook(),
        **generated_ci,
    }
    existed = {rel for rel in generated if (project_dir / rel).is_file()}
    changed = write_generated_files(project_dir, generated)
    # Path.chmod is a silent no-op on NTFS, and git on Windows ignores the
    # POSIX executable bit anyway. Skip the call on Windows and document the
    # manual workaround so maintainers know the escape hatch.
    # On Windows, run: git update-index --chmod=+x .githooks/pre-commit
    if sys.platform != "win32":
        (project_dir / ".githooks" / "pre-commit").chmod(0o755)

    # Install into .git/hooks if inside a git repo, unless the team has a hook of its own there.
    git_hooks_dir = project_dir / ".git" / "hooks"
    installed = git_hooks_dir / "pre-commit"
    own_hook = git_hooks_dir.is_dir() and installed.exists() and not _is_haute_hook(installed)
    hook_installed = git_hooks_dir.is_dir() and not own_hook
    if hook_installed:
        installed.write_text(pre_commit_hook(), encoding="utf-8")
        # Same Windows caveat as above — see the comment near the .githooks
        # chmod. On Windows, run: git update-index --chmod=+x .git/hooks/pre-commit
        if sys.platform != "win32":
            installed.chmod(0o755)

    # -- .gitignore - append if exists, create if not --------------------------
    # The guard entries (and the stable-layer *.haute.json caveat) live in
    # haute._gitignore_guard, shared with the unborn-repo seed in haute._git so
    # the two sites cannot drift.
    gitignore_path = project_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("__pycache__/\n*.pyc\n.venv/\n", encoding="utf-8")
    ensure_gitignore_guards(project_dir)

    # -- Summary ---------------------------------------------------------------
    kind = f"workbench on {credentials}" if workbench else credentials
    first = "release" if workbench else "deploy"
    if rerun:
        click.echo(f"Set up Haute project '{name}' again ({kind} + {ci})\n")
        click.echo(f"  {'written':<10} haute.toml")
        click.echo(f"  {'updated' if pyproject_changed else 'unchanged':<10} pyproject.toml")
        for rel in generated:
            state = (
                "unchanged" if rel not in changed else "updated" if rel in existed else "written"
            )
            click.echo(f"  {state:<10} {rel}")
        for rel in removed:
            click.echo(f"  {'removed':<10} {rel}")
        if form_files:
            click.echo(f"  {'written':<10} rating/{FORM_FOLDER}/ (the blank form)")
        if write_connections:
            click.echo(f"  {'written':<10} {CONNECTIONS_FILE}")
        if hook_installed:
            click.echo(f"  {'installed':<10} .git/hooks/pre-commit")
        click.echo("\nrating/ and tests/ are left as they are.")
        click.echo(
            "haute.toml is written afresh from the answers: copy back from the previous "
            "version (git diff haute.toml) anything set in it by hand, the [mlflow] settings "
            "saved from the app among them."
        )
        if workbench != defaults.workbench:
            click.echo(_NODES_TO_REPLACE[workbench])
    else:
        click.echo(f"Initialised Haute project '{name}' ({kind} + {ci})\n")
        click.echo(f"  pyproject.toml        - {requirement} added as dependency")
        if workbench:
            click.echo("  haute.toml            - project, app image, CI & workbench config")
            click.echo("  rating/workbench/     - the workbench's blank form")
            click.echo(f"  {CONNECTIONS_FILE:<21} - where each environment's database is")
            click.echo(f"  .env.example         - {credentials} credentials and sign-ins template")
        else:
            click.echo("  haute.toml            - project, deploy, safety & CI config")
            click.echo(f"  .env.example         - {credentials} credentials template")
        click.echo("  rating/main.py       - starter pipeline")
        click.echo("  rating/utility/      - project-level utility functions")
        click.echo("  data/                - put your data files here")
        click.echo("  tests/               - starter test + example quote payloads")
        click.echo("  .githooks/pre-commit - auto-format on commit (ruff)")
        for f in generated_ci:
            click.echo(f"  {f}")
        if hook_installed:
            click.echo("  .git/hooks/pre-commit  (installed)")
        if kept:
            click.echo(f"\nAlready there, so kept: {', '.join(kept)}")
    if own_hook:
        click.echo(
            "\n.git/hooks/pre-commit is not Haute's, so it is left as it is "
            "(Haute's is .githooks/pre-commit)."
        )
    click.echo("\nNext steps:")
    if rerun:
        if pyproject_changed:
            click.echo("  uv sync                # install what pyproject.toml now lists")
        click.echo("  git diff               # review the changes, then commit them")
    else:
        click.echo("  uv sync                # install dependencies")
        click.echo("  cp .env.example .env   # fill in credentials")
        click.echo("  haute serve")
    secret_names = ci_secret_names(credentials)
    if ci != "none" and secret_names:
        click.echo(f"\nBefore the first {first}, {ci} needs these CI secrets:")
        for secret_name in secret_names:
            click.echo(f"  {secret_name}")
        if ci == "github":
            click.echo("Set them from .env with: haute ci secrets")


@click.command()
@click.option(
    "--workbench/--automated",
    "workbench",
    default=None,
    help=(
        "How the project prices quotes: in the workbench, an app underwriters price quotes "
        "in, or automated, an API other systems call (default: automated)."
    ),
)
@click.option(
    "--ci",
    type=click.Choice(CI_CHOICES),
    default=None,
    help="CI/CD provider (default: github).",
)
@click.option(
    "--target",
    type=click.Choice(TARGET_CHOICES),
    default=None,
    help=(
        "An automated project's deploy target (default: databricks). azure-container-apps, "
        "aws-ecs and gcp-run are build and push only: deploy builds the image and pushes it "
        "when a registry is configured, but does not update the service yet."
    ),
)
@click.option(
    "--host",
    type=click.Choice(HOST_CHOICES),
    default=None,
    help="Where a workbench project's underwriter app runs (default: azure-container-apps).",
)
@click.option(
    "--store",
    type=click.Choice(STORE_CHOICES),
    default=None,
    help="The database a workbench project's saved quotes are on.",
)
@click.option(
    "--sign-in",
    "sign_in",
    type=click.Choice(SIGN_IN_CHOICES),
    default=None,
    help="How you sign in to that database.",
)
def init(
    workbench: bool | None,
    ci: str | None,
    target: str | None,
    host: str | None,
    store: str | None,
    sign_in: str | None,
) -> None:
    """Scaffold a Haute pricing project in the current directory, or set it up again.

    At a terminal it asks what its flags leave out: how the project prices
    quotes, where the repository is, where it is hosted and, for a workbench
    project, the database its saved quotes are on and how you sign in to it.
    It then generates haute.toml, CI/CD workflows, a credentials template and a
    starter pipeline. A workbench project hosts no scoring API: its workflows
    build and publish the underwriter app.

    In a project that already has a haute.toml it asks the same questions,
    the project's current answers the defaults, and writes haute.toml, the
    workflows and .env.example afresh, leaving rating/ and tests/ as they are.

    \b
    Examples:
      haute init                                  # asks, at a terminal
      haute init --target container --ci none     # container, no CI
      haute init --target aws-ecs --ci github     # AWS ECS (build and push only)
      haute init --workbench --store sqlserver --sign-in entra
    """
    config = InitConfig(
        target=target,
        ci=ci,
        workbench=workbench,
        host=host,
        store=store,
        sign_in=sign_in,
        interactive=_at_terminal(),
    )
    handle_init(config)
