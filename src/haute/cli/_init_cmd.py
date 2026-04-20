"""``haute init`` command.

Split into:

* :class:`InitConfig` — the typed bag of CLI inputs.
* :func:`handle_init` — the pure function that does the scaffolding.
* :func:`init` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import click
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from haute._io import read_user_text


@dataclass
class InitConfig:
    """Parsed inputs for the ``haute init`` command."""

    target: str
    ci: str
    force: bool = False

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


def _find_project_table_bounds(text: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of the ``[project]`` table body.

    ``start`` points to the first character *after* the ``[project]`` header
    line. ``end`` points to the start of the next table header (or len(text)
    if ``[project]`` is the final table). Returns ``None`` if no ``[project]``
    table is present.
    """
    headers = _scan_table_headers(text)
    project_start: int | None = None
    project_end: int | None = None
    for idx, (line_start, line_end, name) in enumerate(headers):
        if name == "project":
            project_start = line_end
            if idx + 1 < len(headers):
                project_end = headers[idx + 1][0]
            else:
                project_end = len(text)
            break
    if project_start is None:
        return None
    if project_end is None:
        project_end = len(text)
    return project_start, project_end


# Matches ``dependencies = [`` with arbitrary whitespace around ``=``. Must be
# at the start of a line so we don't pick up ``optional-dependencies`` or any
# other key that ends in ``dependencies``.
_DEPENDENCIES_KEY_RE = re.compile(r"^dependencies\s*=\s*\[", re.MULTILINE)


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


def _rewrite_project_dependencies(text: str) -> str:
    """Return *text* with ``"haute"`` injected into ``[project].dependencies``.

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
    tomllib.loads(text)

    bounds = _find_project_table_bounds(text)
    if bounds is None:
        # No [project] table — append a fresh one.
        sep = "" if text.endswith("\n") or not text else "\n"
        return text + sep + '[project]\ndependencies = [\n    "haute",\n]\n'

    body_start, body_end = bounds
    body = text[body_start:body_end]
    m = _DEPENDENCIES_KEY_RE.search(body)
    if m is None:
        # [project] exists but no dependencies key — insert one right after
        # the header.
        insertion = 'dependencies = [\n    "haute",\n]\n'
        return text[:body_start] + insertion + text[body_start:]

    # Absolute offset of the opening ``[`` of the dependencies array.
    open_bracket_abs = body_start + m.end() - 1
    close_bracket_abs = _find_matching_bracket(text, open_bracket_abs)

    # Replace the array content. Normalise to one item per line with a
    # four-space indent for consistency with the existing scaffold style.
    array_text = text[open_bracket_abs + 1 : close_bracket_abs]
    # Re-parse the single-table slice to discover current entries structurally
    # (tomllib on the full file already succeeded, so this slice is valid).
    current_deps: list[str] = tomllib.loads("dependencies = [" + array_text + "]")["dependencies"]

    new_deps = ["haute", *current_deps]
    # Preserve trailing newline conventions by formatting the replacement
    # body as ``\n    "a",\n    "b",\n`` (one dep per line, trailing comma).
    new_array_body = "\n" + "".join(f'    "{dep}",\n' for dep in new_deps)

    return text[: open_bracket_abs + 1] + new_array_body + text[close_bracket_abs:]


def _ensure_haute_dependency(pyproject_path: Path, name: str) -> None:
    """Add ``haute`` to pyproject.toml dependencies.

    If pyproject.toml exists, insert ``"haute"`` into the
    ``[project].dependencies`` list using a TOML-aware edit (if not already
    present). If it doesn't exist, create a minimal pyproject.toml.

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
            f'dependencies = [\n    "haute",\n]\n' + _DEV_DEPS_BLOCK + _MYPY_BLOCK,
            encoding="utf-8",
        )
        return

    text = read_user_text(pyproject_path)

    # Parse structurally to detect an existing ``haute`` entry.
    parsed = tomllib.loads(text)
    project_deps = parsed.get("project", {}).get("dependencies", [])
    if not isinstance(project_deps, list):
        project_deps = []

    if not _dependencies_contain_haute(project_deps):
        text = _rewrite_project_dependencies(text)

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

    pyproject_path.write_text(text, encoding="utf-8")


def handle_init(config: InitConfig) -> None:
    """Scaffold a Haute project at :func:`pathlib.Path.cwd`.

    Pure function version of the ``haute init`` command — takes a typed
    :class:`InitConfig` and writes all scaffold files.  Exits with
    ``SystemExit(1)`` when ``haute.toml`` already exists (the scaffold
    never overwrites an existing project).
    """
    import tomllib

    from haute._scaffold import (
        azure_devops_yml,
        env_example,
        github_ci_yml,
        github_deploy_prod_yml,
        github_deploy_yml,
        gitlab_ci_yml,
        haute_toml,
        pre_commit_hook,
        starter_pipeline,
        starter_test,
        starter_test_quote,
        starter_utility_features,
        starter_utility_init,
    )

    target = config.target
    ci = config.ci

    # Solo mode is configured in haute.toml, not via a CLI flag.
    # Default is team mode; user sets min_approvers = 0 for solo.

    project_dir = Path.cwd()

    if (project_dir / "haute.toml").exists() and not config.force:
        click.echo(
            "Error: haute.toml already exists - project already initialised. "
            "Re-run with --force to overwrite the existing scaffold.",
            err=True,
        )
        raise SystemExit(1)

    # -- Resolve project name --------------------------------------------------
    pyproject_path = project_dir / "pyproject.toml"
    name = project_dir.name.replace("-", "_").replace(" ", "_").lower()

    if pyproject_path.exists():
        with open(pyproject_path, "rb") as fh:
            pyproject = tomllib.load(fh)
        if "project" in pyproject and "name" in pyproject["project"]:
            name = pyproject["project"]["name"]

    # -- pyproject.toml - ensure haute is a dependency -------------------------
    _ensure_haute_dependency(pyproject_path, name)

    # -- Directories -----------------------------------------------------------
    (project_dir / "data").mkdir(exist_ok=True)
    (project_dir / "prompts").mkdir(exist_ok=True)

    # -- Remove root main.py left over from `uv init` -------------------------
    root_main = project_dir / "main.py"
    if root_main.exists():
        root_main.unlink()

    # -- rating/ - user pipeline files -----------------------------------------
    rating_dir = project_dir / "rating"
    rating_dir.mkdir(exist_ok=True)
    (rating_dir / "__init__.py").write_text("", encoding="utf-8")

    # -- rating/utility/ - project-level utility functions ----------------------
    utility_dir = rating_dir / "utility"
    utility_dir.mkdir(exist_ok=True)
    (utility_dir / "__init__.py").write_text(starter_utility_init(), encoding="utf-8")
    (utility_dir / "features.py").write_text(starter_utility_features(), encoding="utf-8")

    # -- rating/main.py - starter pipeline -------------------------------------
    (rating_dir / "main.py").write_text(starter_pipeline(name), encoding="utf-8")

    # -- rating/ placeholder directories (used once the pipeline grows) --------
    for sub in ("config", "models", "outputs"):
        (rating_dir / sub).mkdir(exist_ok=True)

    # -- haute.toml - project + deploy + safety + CI config --------------------
    (project_dir / "haute.toml").write_text(
        haute_toml(name, target, ci),
        encoding="utf-8",
    )

    # -- .env.example - target-specific credentials ----------------------------
    (project_dir / ".env.example").write_text(env_example(target), encoding="utf-8")

    # -- Starter test file + test quotes ---------------------------------------
    tests_dir = project_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    quotes_dir = tests_dir / "quotes"
    quotes_dir.mkdir(exist_ok=True)
    (quotes_dir / "example.json").write_text(
        starter_test_quote(),
        encoding="utf-8",
    )
    (tests_dir / "test_pipeline.py").write_text(
        starter_test(name),
        encoding="utf-8",
    )

    # -- CI/CD workflow files --------------------------------------------------
    ci_files: list[str] = []
    if ci == "github":
        workflows_dir = project_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "ci.yml").write_text(github_ci_yml(), encoding="utf-8")
        (workflows_dir / "deploy-staging.yml").write_text(
            github_deploy_yml(target),
            encoding="utf-8",
        )
        (workflows_dir / "deploy-production.yml").write_text(
            github_deploy_prod_yml(target),
            encoding="utf-8",
        )
        ci_files = [
            ".github/workflows/ci.yml",
            ".github/workflows/deploy-staging.yml",
            ".github/workflows/deploy-production.yml",
        ]
    elif ci == "gitlab":
        (project_dir / ".gitlab-ci.yml").write_text(
            gitlab_ci_yml(target),
            encoding="utf-8",
        )
        ci_files = [".gitlab-ci.yml"]
    elif ci == "azure-devops":
        (project_dir / "azure-pipelines.yml").write_text(
            azure_devops_yml(target),
            encoding="utf-8",
        )
        ci_files = ["azure-pipelines.yml"]

    # -- Pre-commit hook -------------------------------------------------------
    hooks_dir = project_dir / ".githooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    hook_path.write_text(pre_commit_hook(), encoding="utf-8")
    hook_path.chmod(0o755)

    # Install into .git/hooks if inside a git repo
    git_hooks_dir = project_dir / ".git" / "hooks"
    if git_hooks_dir.is_dir():
        installed = git_hooks_dir / "pre-commit"
        installed.write_text(pre_commit_hook(), encoding="utf-8")
        installed.chmod(0o755)

    # -- .gitignore - append if exists, create if not --------------------------
    gitignore_path = project_dir / ".gitignore"
    haute_entries = ".env\n*.haute.json\nimpact_report.md\n.haute_cache/\nmlruns/\ndata/\n"
    if gitignore_path.exists():
        existing = read_user_text(gitignore_path)
        missing = [line for line in haute_entries.splitlines() if line and line not in existing]
        if missing:
            with open(gitignore_path, "a", encoding="utf-8") as fh:
                fh.write("\n# Haute\n" + "\n".join(missing) + "\n")
    else:
        gitignore_path.write_text(
            "__pycache__/\n*.pyc\n.venv/\n.env\n*.haute.json\n.haute_cache/\nmlruns/\ndata/\n",
            encoding="utf-8",
        )

    # -- Summary ---------------------------------------------------------------
    click.echo(f"Initialised Haute project '{name}' ({target} + {ci})\n")
    click.echo("  pyproject.toml        - haute added as dependency")
    click.echo("  haute.toml            - project, deploy, safety & CI config")
    click.echo(f"  .env.example         - {target} credentials template")
    click.echo("  rating/main.py       - starter pipeline")
    click.echo("  rating/utility/      - project-level utility functions")
    click.echo("  data/                - put your data files here")
    click.echo("  prompts/             - reusable AI prompts for pipeline tasks")
    click.echo("  tests/               - starter test + example quote payloads")
    click.echo("  .githooks/pre-commit - auto-format on commit (ruff)")
    for f in ci_files:  # noqa: F841
        click.echo(f"  {f}")
    if git_hooks_dir.is_dir():
        click.echo("  .git/hooks/pre-commit  (installed)")
    click.echo("\nNext steps:")
    click.echo("  uv sync                # install dependencies")
    click.echo("  cp .env.example .env   # fill in credentials")
    click.echo("  haute serve")


@click.command()
@click.option(
    "--target",
    type=click.Choice(
        [
            "databricks",
            "container",
            "azure-container-apps",
            "aws-ecs",
            "gcp-run",
            "sagemaker",
            "azure-ml",
        ]
    ),
    default="databricks",
    help="Deploy target (default: databricks).",
)
@click.option(
    "--ci",
    type=click.Choice(["github", "gitlab", "azure-devops", "none"]),
    default="github",
    help="CI/CD provider (default: github).",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Overwrite existing scaffold files (haute.toml, starter pipeline, etc.).",
)
def init(target: str, ci: str, force: bool) -> None:
    """Scaffold a Haute pricing project in the current directory.

    Generates haute.toml, CI/CD workflows, credentials template, and a
    starter pipeline - all configured for the chosen deploy target and
    CI provider.

    \b
    Examples:
      haute init                                  # databricks + github
      haute init --target container --ci none      # container, no CI
      haute init --target sagemaker --ci github   # AWS + github
      haute init --force                          # overwrite existing scaffold
    """
    config = InitConfig(target=target, ci=ci, force=force)
    handle_init(config)
