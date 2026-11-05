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
from collections.abc import Collection, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from haute._gitignore_guard import ensure_gitignore_guards
from haute._io import read_user_text
from haute._project_context import current_project
from haute._scaffold_files import ensure_gitattributes, lf_bytes, write_lf

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


def _with_extras(dependency: str, extras: Collection[str]) -> str:
    """*dependency* given each of *extras* it lacks when it is a ``haute`` requirement, its
    version specifier, marker and other extras kept; otherwise *dependency* as it is."""
    requirement = Requirement(dependency)
    if canonicalize_name(requirement.name) != "haute" or set(extras) <= requirement.extras:
        return dependency
    requirement.extras |= set(extras)
    return str(requirement)


def _ensure_haute_dependency(pyproject_path: Path, name: str, requirement: str = "haute") -> bool:
    """Make pyproject.toml's dependencies list *requirement*, ``haute`` or ``haute`` with
    extras; return whether the file changed.

    An existing file is edited through :mod:`tomlkit`, which keeps its formatting and
    comments: *requirement* goes first in ``[project].dependencies`` (created where there
    is none) when no ``haute`` requirement is listed, and one that is gains each of
    *requirement*'s extras it lacks, keeping its version specifier, marker and other
    extras and losing none. A one-line array is laid out one entry a line once
    *requirement* joins it. Without a file a minimal pyproject.toml is written.

    A ``[dependency-groups]`` dev group of ruff, mypy and pytest and a ``[tool.mypy]``
    table are added when the file has none, so the generated CI workflows work.
    """
    import tomlkit
    from tomlkit.items import AoT, Array

    if not pyproject_path.exists():
        document = tomlkit.document()
        new_project = tomlkit.table()
        new_project["name"] = name
        new_project["version"] = "0.1.0"
        new_project["requires-python"] = ">=3.11"
        new_project["dependencies"] = tomlkit.array().multiline(True)
        new_project["dependencies"].append(requirement)
        document["project"] = new_project
        write_lf(pyproject_path, tomlkit.dumps(document) + _DEV_DEPS_BLOCK + _MYPY_BLOCK)
        return True

    original = read_user_text(pyproject_path)
    document = tomlkit.parse(original)
    project = document.get("project")
    if isinstance(project, AoT):
        # Invalid for a PEP 621 pyproject, and there is no single table to edit.
        raise ValueError(
            "pyproject.toml [project] must be a table, not an array-of-tables ([[project]])."
        )
    if project is None:
        document["project"] = tomlkit.table()
        project = document["project"]
    dependencies = project.get("dependencies")
    if dependencies is None:
        project["dependencies"] = tomlkit.array().multiline(True)
        dependencies = project["dependencies"]
    if not isinstance(dependencies, Array):
        raise ValueError("pyproject.toml [project].dependencies must be an array.")

    if _dependencies_contain_haute(list(dependencies)):
        extras = Requirement(requirement).extras
        for index, dependency in enumerate(list(dependencies)):
            if isinstance(dependency, str) and _with_extras(dependency, extras) != dependency:
                dependencies[index] = _with_extras(dependency, extras)
    else:
        one_line = "\n" not in dependencies.as_string()
        dependencies.insert(0, requirement)
        if one_line:
            dependencies.multiline(True)

    text = tomlkit.dumps(document)
    if "dependency-groups" not in document:
        text += _DEV_DEPS_BLOCK
    tool = document.get("tool")
    if not (isinstance(tool, Mapping) and "mypy" in tool):
        text += _MYPY_BLOCK
    if text == original:
        return False
    tomllib.loads(text)  # what is written must read back as TOML
    write_lf(pyproject_path, text)
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
        if path.is_file() and path.read_bytes() == lf_bytes(content):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        write_lf(path, content)
        changed.append(rel)
    return changed


def capture_new_files(project_dir: Path, written: Sequence[str]) -> None:
    """Record the files Haute just wrote into *project_dir* that Git does not track yet, as
    one save on the working branch's ledger, and say what became of them.

    Commit takes a new file only in the pipeline folder (specs/git-integration), so a
    workflow a newer Haute adds, or ``connections.toml``, would otherwise never reach version
    history, and a branch could merge without its deploy workflow. "Not tracked yet" is
    untracked, or staged as an addition but never committed, as a capture whose commit
    failed leaves its files. What Haute changed in tracked files is left on disk, for review
    and the next Commit. Recording checks the ledger out, so it is made only while the
    checkout is on the working branch's ledger, never from under another branch, whose files
    it would replace. A capture skipped (no working branch chosen, the checkout elsewhere, no
    git identity) or failed prints why and names the files for ``git status``, which lists new
    files where ``git diff`` does not. Outside a repository there is nothing to record.
    """
    from haute._git_core import (
        _get_current_branch,
        _is_git_repo,
        _run_git,
        _run_git_ok,
        ledger_name,
    )
    from haute._git_state import read_working_branch

    paths = list(dict.fromkeys(written))
    if not paths or not _is_git_repo(project_dir):
        return
    pathspecs = [f":(literal){path}" for path in paths]
    untracked = _run_git(
        "ls-files", "--others", "--exclude-standard", "-z", "--", *pathspecs, cwd=project_dir
    )
    # A repository with no commit yet has no HEAD to compare with, and nothing staged that
    # an earlier capture could have left.
    has_head, staged = _run_git_ok(
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=A",
        "-z",
        "HEAD",
        "--",
        *pathspecs,
        cwd=project_dir,
    )
    listed = untracked.split("\0") + (staged.split("\0") if has_head else [])
    new = list(dict.fromkeys(path for path in listed if path))
    if not new:
        return
    working = read_working_branch(project_dir)
    if working is not None:
        ledger = ledger_name(working)
        current = _get_current_branch(project_dir)
        if current != ledger:
            on = "no branch (a detached HEAD)" if current == "HEAD" else current
            click.echo(
                f"\nYour checkout is on {on}, not {ledger}, where Haute keeps {working}'s "
                "saves, so these were not recorded from under it."
            )
            _not_in_version_history(new)
            return
    # The editor's own capture: one save on the ledger, or a warning naming why not.
    from haute.routes._save_pipeline import capture_save_in_ledger

    warnings: list[str] = []
    capture = capture_save_in_ledger(project_dir, new, warnings)
    if capture.git_sha is not None:
        click.echo(f"\nRecorded in version history: {', '.join(new)}")
        click.echo("  (a save on your working branch, which its next Commit takes)")
        return
    if not capture.uncaptured:
        return
    for warning in warnings:
        click.echo(f"\n{warning}")
    _not_in_version_history(capture.uncaptured)


def _not_in_version_history(paths: Sequence[str]) -> None:
    click.echo(f"Not yet in version history: {', '.join(paths)}")
    click.echo("  git status lists them (git diff does not): add them before you commit.")


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


#: The root ``main.py`` ``uv init`` writes, whatever the project's name.
_UV_INIT_PLACEHOLDER = re.compile(
    re.escape('def main():\n    print("Hello from ')
    + r'[^"\\\n]+'
    + re.escape('!")\n\n\nif __name__ == "__main__":\n    main()\n')
)


#: ``uv init --package``'s and ``--lib``'s ``src/<module>/__init__.py``, its project name aside.
_UV_INIT_PACKAGE = re.compile(
    r"def (?:main\(\) -> None:\n    print|hello\(\) -> str:\n    return)"
    r'\("Hello from [^"\\\n]+!"\)\n'
)
#: The description ``uv init`` writes, which says nothing about the project.
_UV_INIT_DESCRIPTION = "Add your description here"
#: The build backends ``uv init`` names for a package.
_UV_INIT_BACKENDS = frozenset({"uv_build", "hatchling.build"})


def _referenced(project_dir: Path, module: str, skip: Path) -> bool:
    """Whether a Python file of the project outside *skip* imports *module*."""
    pattern = re.compile(rf"^\s*(?:import|from)\s+{re.escape(module)}\b", re.MULTILINE)
    for path in project_dir.rglob("*.py"):
        if skip in path.parents or ".venv" in path.parts:
            continue
        try:
            if pattern.search(path.read_bytes().decode("utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def remove_uv_init_leftovers(project_dir: Path, pyproject_path: Path) -> list[str]:
    """Remove what ``uv init`` left that a Haute project has no use for (READ-41); return
    what went, for the summary.

    Only on a project's first ``haute init``, and only what is still exactly as uv wrote it
    and nothing references: an empty README and the pyproject's ``readme`` naming it, the
    placeholder ``description``, and a ``--package`` or ``--lib`` project's
    ``src/<module>/`` with the ``[project.scripts]`` entry and the ``[build-system]`` that
    name it, so the build configuration left works. A file written by hand is kept.
    """
    import tomlkit

    if not pyproject_path.is_file():
        return []
    original = read_user_text(pyproject_path)
    document = tomlkit.parse(original)
    project = document.get("project")
    if not isinstance(project, MutableMapping):
        return []
    removed: list[str] = []
    readme = project_dir / "README.md"
    if (
        project.get("readme") == "README.md"
        and readme.is_file()
        and not readme.read_bytes().strip()
    ):
        readme.unlink()
        del project["readme"]
        removed.append("the empty README.md")
    if project.get("description") == _UV_INIT_DESCRIPTION:
        del project["description"]
        removed.append("the placeholder description")
    name = project.get("name")
    module = re.sub(r"[-.]+", "_", str(name)).lower() if isinstance(name, str) else ""
    package = project_dir / "src" / module
    init = package / "__init__.py"
    scripts = project.get("scripts")
    files = sorted(path.name for path in package.iterdir()) if package.is_dir() else []
    untouched = (
        module
        and init.is_file()
        and set(files) <= {"__init__.py", "py.typed", "__pycache__"}
        and _UV_INIT_PACKAGE.fullmatch(
            init.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n")
        )
        is not None
        and not _referenced(project_dir, module, package)
    )
    build = document.get("build-system")
    if untouched and (scripts is None or dict(scripts) == {str(name): f"{module}:main"}):
        for path in sorted(package.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        package.rmdir()
        if not any((project_dir / "src").iterdir()):
            (project_dir / "src").rmdir()
        if scripts is not None:
            del project["scripts"]
        if isinstance(build, Mapping) and build.get("build-backend") in _UV_INIT_BACKENDS:
            del document["build-system"]
        removed.append(f"src/{module}/, uv init's package")
    text = tomlkit.dumps(document)
    if text != original:
        tomllib.loads(text)  # what is written must read back as TOML
        write_lf(pyproject_path, text)
    return removed


def _is_uv_init_placeholder(path: Path) -> bool:
    """Whether *path* holds exactly what ``uv init`` writes as a root ``main.py``, its
    project name aside; a checkout's CRLF line endings count as its LF ones."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return _UV_INIT_PLACEHOLDER.fullmatch(text.replace("\r\n", "\n")) is not None


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

    project_dir = current_project().root
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
    # A package name (PEP 508) from the folder's: what it cannot hold becomes "_", and it
    # starts and ends with a letter or digit, so ".my-project" is "my_project".
    name = re.sub(r"[^a-z0-9.]+", "_", project_dir.name.lower()).strip("._")

    if pyproject_path.exists():
        with open(pyproject_path, "rb") as fh:
            pyproject = tomllib.load(fh)
        if "project" in pyproject and "name" in pyproject["project"]:
            name = pyproject["project"]["name"]
    if previous is not None:
        named = _table(previous, "project").get("name")
        if isinstance(named, str) and named:
            name = named
    if not name:
        raise click.ClickException(
            f"The folder name {project_dir.name!r} gives no project name: it needs a letter "
            "or digit. Rename the folder, or create pyproject.toml with a [project] name."
        )

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
    # A first init removes uv init's leftovers that are still as uv wrote them; a re-run
    # never removes a file (READ-41).
    leftovers = [] if rerun else remove_uv_init_leftovers(project_dir, pyproject_path)
    for leftover in leftovers:
        click.echo(f"  Removed {leftover}, which uv init wrote and nothing uses.")
    pyproject_changed = _ensure_haute_dependency(pyproject_path, name, requirement)

    # -- .python-version - one Python for local, CI and the deployed model -----
    python_version_file = project_dir / ".python-version"
    wrote_python_version = not python_version_file.exists()
    if wrote_python_version:
        write_lf(python_version_file, f"{python_version}\n")

    # -- Directories -----------------------------------------------------------
    (project_dir / "data").mkdir(exist_ok=True)

    # -- Remove the root placeholder ``uv init`` writes, and only that ----------
    root_main = project_dir / "main.py"
    if root_main.is_file() and not rerun:
        if _is_uv_init_placeholder(root_main):
            root_main.unlink()
            click.echo("  Removed root main.py, uv init's placeholder (Haute uses rating/main.py).")
        else:
            click.echo(
                "  Kept root main.py: it is not uv init's placeholder. "
                "The pipeline is rating/main.py."
            )

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
            write_lf(starter, content)
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
    write_lf(haute_toml_path, toml_text)

    # -- rating/workbench/ - the workbench's blank form folder ------------------
    if form_files:
        _write_form_folder(form_folder, form_files)

    # -- connections.toml - each environment's database, for this machine -------
    if write_connections:
        assert answers.store is not None and answers.sign_in is not None
        write_lf(connections_path, connections_toml(answers.store, answers.sign_in))

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
        write_lf(installed, pre_commit_hook())
        # Same Windows caveat as above — see the comment near the .githooks
        # chmod. On Windows, run: git update-index --chmod=+x .git/hooks/pre-commit
        if sys.platform != "win32":
            installed.chmod(0o755)

    # -- .gitignore - append if exists, create if not --------------------------
    # The guard entries (and the stable-layer *.haute.json caveat) live in
    # haute._gitignore_guard, shared with the unborn-repo seed in haute._git so
    # the two sites cannot drift.
    gitignore_path = project_dir / ".gitignore"
    wrote_gitignore = not gitignore_path.exists()
    if wrote_gitignore:
        write_lf(gitignore_path, "__pycache__/\n*.pyc\n.venv/\n")
    ensure_gitignore_guards(project_dir)
    ensure_gitattributes(project_dir)

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
        # What the re-run wrote, every generated file among it (one an earlier run wrote and
        # never committed too): those Git does not track yet are recorded in version history.
        written = [
            "haute.toml",
            *(["pyproject.toml"] if pyproject_changed else []),
            *([".python-version"] if wrote_python_version else []),
            *(f"rating/{FORM_FOLDER}/{rel}" for rel in form_files),
            *([CONNECTIONS_FILE] if write_connections else []),
            *generated,
            *([".gitignore"] if wrote_gitignore else []),
        ]
        capture_new_files(project_dir, written)
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
        click.echo("  git status; git diff   # review the changes, then commit them")
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
