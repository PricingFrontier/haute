"""The deploy's CI secrets on GitHub, read from ``.env`` and set through the GitHub CLI.

``haute ci secrets`` sets every one of them. The editor's Push sets the ones a repository
lacks when it first publishes a project there, and reports the ones a later Push finds
missing (:func:`push_with_ci_secrets`), which the push panel's Set from .env then sets
(:func:`set_missing_ci_secrets`). Only the command overwrites a secret the repository has.

This module is ``gh``'s subprocess chokepoint (docs/PLATFORM_DIVERGENCE.md §3):
:func:`resolve_gh` finds it on ``PATH``, and :func:`run_gh` runs it with prompts disabled,
a timeout and UTF-8 pinned, raising :class:`CiSecretsError` with ``gh``'s own message.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from haute import _git
from haute.errors import ConfigError, DeployError, HauteError

if TYPE_CHECKING:
    from haute.schemas import GitCiSecrets, GitPushResponse

#: How long one ``gh`` call may run before it counts as failed.
GH_TIMEOUT_SECONDS = 60

# A missing login then fails instead of waiting at a prompt no one can answer.
_GH_ENV = {"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_URL_SCHEMES = frozenset({"https", "http", "ssh", "git"})
_SCP_LIKE = re.compile(r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)")
_OWNER = re.compile(r"[A-Za-z0-9-]+")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]+")

# The editor's secrets steps run one at a time, so two never fill the same gap.
_EDITOR_STEP = threading.Lock()


class CiSecretsError(HauteError):
    """A step toward the deploy's CI secrets that could not be taken.

    The message says why and what to do, and never holds a secret's value.
    """


class DatabricksRefusedError(CiSecretsError):
    """Databricks did not accept a host and token pair read from ``.env``."""


class _SettingsUnreadableError(CiSecretsError):
    """The project's settings could not be read for a remote on GitHub's *repository*."""

    def __init__(self, message: str, repository: str) -> None:
        super().__init__(message)
        self.repository = repository


# ── The GitHub CLI ──────────────────────────────────────────────────


def resolve_gh() -> str:
    """The GitHub CLI on ``PATH``."""
    found = shutil.which("gh")
    if found is None:
        raise CiSecretsError(
            "The GitHub CLI (gh) is not installed: install it from https://cli.github.com "
            "and run `gh auth login`."
        )
    return found


def run_gh(gh: str, project_dir: Path, *args: str, stdin: str | None = None) -> str:
    """Run ``gh`` in *project_dir*; its standard output.

    A non-zero exit, a launch failure, or a call still running after
    ``GH_TIMEOUT_SECONDS`` raises :class:`CiSecretsError` with ``gh``'s own message,
    *stdin* redacted from it.
    """
    from haute._credential_security import redact_sensitive_text

    command = " ".join(("gh", *args))
    try:
        result = subprocess.run(  # noqa: S603 - gh from PATH, fixed arguments
            [gh, *args],
            cwd=project_dir,
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, **_GH_ENV},
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CiSecretsError(
            f"`{command}` did not finish within {GH_TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise CiSecretsError(f"`{command}` could not run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        detail = redact_sensitive_text(detail, known_secrets=(stdin,) if stdin else ())
        raise CiSecretsError(f"`{command}` failed: {detail}")
    return result.stdout


def _repository_args(repository: str | None) -> list[str]:
    """``--repo`` for *repository* on github.com; none lets ``gh`` resolve it from the remotes."""
    return [] if repository is None else ["--repo", f"github.com/{repository}"]


def github_secret_names(gh: str, project_dir: Path, repository: str | None = None) -> set[str]:
    """The names of the repository's Actions secrets (``gh secret list``)."""
    listed = run_gh(
        gh, project_dir, "secret", "list", "--json", "name", *_repository_args(repository)
    )
    try:
        return {str(entry["name"]) for entry in json.loads(listed or "[]")}
    except (ValueError, TypeError, KeyError) as exc:
        raise CiSecretsError("`gh secret list` answered with something other than names.") from exc


def set_github_secret(
    gh: str, project_dir: Path, name: str, value: str, repository: str | None = None
) -> None:
    """Store *value* as the repository's secret *name*, passed on ``gh``'s standard input."""
    run_gh(gh, project_dir, "secret", "set", name, *_repository_args(repository), stdin=value)


# ── The values, from .env ───────────────────────────────────────────


def secret_values(project_dir: Path, names: Sequence[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Each secret's value and where it was read: ``.env``, else the environment.

    ``.env`` comes first because it is what the secrets are set from; a variable only the
    environment sets (a shell profile's, say) is used when ``.env`` lacks it, and the
    source says so. Unset and blank values are left out.
    """
    from dotenv import dotenv_values

    env_file = project_dir / ".env"
    try:
        from_file = dotenv_values(env_file) if env_file.is_file() else {}
    except (OSError, ValueError) as exc:
        raise CiSecretsError(f"Haute could not read .env: {exc}") from exc
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name in names:
        in_file = (from_file.get(name) or "").strip()
        in_environment = os.environ.get(name, "").strip()
        if in_file:
            values[name], sources[name] = in_file, ".env"
        elif in_environment:
            values[name], sources[name] = in_environment, "the environment"
    return values, sources


def usable_secret_values(
    project_dir: Path, target: str, names: Sequence[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Every one of *names*' values and where it was read, or a refusal naming the unusable.

    A value unset in ``.env`` and the environment, or still ``.env.example``'s placeholder,
    raises :class:`CiSecretsError` before anything is set.
    """
    from haute._scaffold import env_example_placeholders

    values, sources = secret_values(project_dir, names)
    unset = [name for name in names if not values.get(name)]
    if unset:
        raise CiSecretsError(
            f"Not set in .env or the environment: {', '.join(unset)}. Copy .env.example to "
            ".env and fill in every value."
        )
    placeholders = env_example_placeholders(target)
    unedited = [name for name in names if values[name] == placeholders.get(name)]
    if unedited:
        raise CiSecretsError(
            f"Still .env.example's placeholder: {', '.join(unedited)}. Put the real values in .env."
        )
    return values, sources


def verify_databricks(values: dict[str, str]) -> list[tuple[str, str, str]]:
    """Check each Databricks pair against its workspace: (host name, token name, user).

    The first pair the workspace refuses raises :class:`DatabricksRefusedError`, naming
    the pair and its use, its token redacted.
    """
    from haute._credential_security import redact_sensitive_text
    from haute._mlflow_utils import MLFLOW_DATABRICKS_HOST_ENV, MLFLOW_DATABRICKS_TOKEN_ENV
    from haute.deploy._databricks_serving import RATING_HOST_ENV, RATING_TOKEN_ENV, workspace_user

    pairs = (
        (MLFLOW_DATABRICKS_HOST_ENV, MLFLOW_DATABRICKS_TOKEN_ENV, "registers the model in MLflow"),
        (RATING_HOST_ENV, RATING_TOKEN_ENV, "creates and calls the serving endpoint"),
    )
    checked: list[tuple[str, str, str]] = []
    for host_name, token_name, purpose in pairs:
        try:
            user = workspace_user(values[host_name], values[token_name])
        except Exception as exc:
            detail = redact_sensitive_text(str(exc), known_secrets=(values[token_name],))
            raise DatabricksRefusedError(
                f"Databricks refused {host_name} and {token_name}, the pair that {purpose}: "
                f"{detail}. Correct them in .env."
            ) from exc
        checked.append((host_name, token_name, user))
    return checked


# ── The editor's Push ───────────────────────────────────────────────


def github_repository(url: str) -> str | None:
    """The ``OWNER/REPO`` a remote URL on github.com names, else ``None``.

    Reads ``https://``, ``http://``, ``ssh://`` and ``git://`` URLs and scp-like
    ``[user@]github.com:OWNER/REPO``, dropping userinfo, a port, a trailing ``/`` and
    ``.git``. Another host, a local path, or a path other than an owner and a repository
    names none.
    """
    if "://" in url:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            return None
        if parts.scheme.lower() not in _URL_SCHEMES:
            return None
        path = parts.path
    else:
        scp = _SCP_LIKE.fullmatch(url)
        if scp is None:
            return None
        host, path = scp["host"].lower(), scp["path"]
    if host not in _GITHUB_HOSTS:
        return None
    segments = path.strip("/").split("/")
    if len(segments) != 2:
        return None
    owner, repository = segments
    repository = repository.removesuffix(".git")
    if not _OWNER.fullmatch(owner) or not _REPOSITORY.fullmatch(repository):
        return None
    if repository in (".", ".."):
        return None
    return f"{owner}/{repository}"


@dataclass(frozen=True)
class RemoteSecrets:
    """The deploy's secret names on one remote's GitHub repository (``OWNER/REPO``)."""

    project_dir: Path
    target: str
    repository: str
    names: tuple[str, ...]


def _remote_secrets_or_reason(remote: str, project_dir: Path) -> RemoteSecrets | str:
    """The deploy's secrets on *remote*'s repository, or why they do not apply there.

    The remote comes first: its fetch URL and every push URL must name one repository on
    github.com, since the secrets belong where the push lands. Then the project's
    settings, whose read failure raises :class:`_SettingsUnreadableError`.
    """
    from haute._scaffold import ci_credentials, ci_secret_names
    from haute.deploy._config import DeployConfig

    url = _git.remote_url(remote, cwd=project_dir)
    if url is None:
        return f"No remote named '{remote}' is configured."
    repository = github_repository(url)
    if repository is None:
        return (
            f"'{remote}' is not a repository on github.com, where Haute sets the deploy's secrets."
        )
    pushes = _git.remote_push_urls(remote, cwd=project_dir)
    # GitHub's owner and repository names are case-insensitive.
    if any((github_repository(push) or "").lower() != repository.lower() for push in pushes):
        return (
            f"'{remote}' pushes somewhere other than {repository}, which it fetches from: Haute "
            "sets the deploy's secrets only where a remote fetches from and pushes to one "
            "GitHub repository."
        )
    toml_path = project_dir / "haute.toml"
    if not toml_path.is_file():
        return "No haute.toml found: this is not a Haute project."
    try:
        config = DeployConfig.from_toml(toml_path)
        if config.ci.provider != "github":
            return (
                f"This project's CI provider is {config.ci.provider}: Haute sets the deploy's "
                "secrets on GitHub only."
            )
        # The deploy target's, or a workbench project's app host's (a WorkbenchConfigError,
        # a ConfigError, when it names none).
        target = ci_credentials(project_dir, config.target)
        names = tuple(ci_secret_names(target))
    except (OSError, ValueError, ConfigError, DeployError) as exc:
        raise _SettingsUnreadableError(
            f"Haute could not read this project's haute.toml and .env: {exc}", repository
        ) from exc
    if not names:
        return f"The {target} target needs no CI secrets."
    return RemoteSecrets(project_dir, target, repository, names)


def remote_secrets(remote: str, project_dir: Path) -> RemoteSecrets | None:
    """The deploy's secrets on *remote*'s repository, or ``None`` where they do not apply.

    They apply when the remote fetches from and pushes to one repository on github.com,
    ``haute.toml`` names the ``github`` CI provider, and the deploy target has secrets.
    Reads only local state; settings that cannot be read raise :class:`CiSecretsError`.
    """
    found = _remote_secrets_or_reason(remote, project_dir)
    return found if isinstance(found, RemoteSecrets) else None


def _report(
    repository: str,
    state: Literal["ready", "missing", "unknown"],
    *,
    missing: Sequence[str] = (),
    newly_set: Sequence[str] = (),
    problem: str | None = None,
) -> GitCiSecrets:
    from haute.schemas import GitCiSecrets

    return GitCiSecrets(
        repository=repository,
        state=state,
        missing=list(missing),
        newly_set=list(newly_set),
        problem=problem,
    )


def _set_missing(secrets: RemoteSecrets, gh: str, missing: list[str], newly_set: list[str]) -> None:
    """Set each name in *missing* from ``.env``, moving it to *newly_set* once stored.

    Every one of the deploy's values must be usable and the Databricks pairs accepted
    first. The pairs' check takes seconds, so the repository is listed again after it,
    and a name it gained meanwhile leaves *missing* without being set. Raises
    :class:`CiSecretsError`; *missing* then holds the names still absent.
    """
    if not missing:
        return
    values, _ = usable_secret_values(secrets.project_dir, secrets.target, secrets.names)
    if secrets.target == "databricks":
        verify_databricks(values)
    present = github_secret_names(gh, secrets.project_dir, secrets.repository)
    missing[:] = [name for name in missing if name not in present]
    for name in list(missing):
        set_github_secret(gh, secrets.project_dir, name, values[name], secrets.repository)
        missing.remove(name)
        newly_set.append(name)


def _fill_before_first_publication(secrets: RemoteSecrets) -> GitCiSecrets:
    """Set the secrets the repository lacks; what was found and done, never a raise."""
    with _EDITOR_STEP:
        try:
            gh = resolve_gh()
            present = github_secret_names(gh, secrets.project_dir, secrets.repository)
        except CiSecretsError as exc:
            return _report(secrets.repository, "unknown", problem=str(exc))
        missing = [name for name in secrets.names if name not in present]
        newly_set: list[str] = []
        try:
            _set_missing(secrets, gh, missing, newly_set)
        except CiSecretsError as exc:
            return _report(
                secrets.repository,
                "missing",
                missing=missing,
                newly_set=newly_set,
                problem=str(exc),
            )
        return _report(secrets.repository, "ready", newly_set=newly_set)


def _listed(secrets: RemoteSecrets) -> GitCiSecrets:
    """Which of the deploy's secrets the repository has; nothing is set."""
    try:
        present = github_secret_names(resolve_gh(), secrets.project_dir, secrets.repository)
    except CiSecretsError as exc:
        return _report(secrets.repository, "unknown", problem=str(exc))
    missing = [name for name in secrets.names if name not in present]
    return _report(secrets.repository, "missing" if missing else "ready", missing=missing)


def push_with_ci_secrets(remote: str, project_dir: Path) -> GitPushResponse:
    """Push the working pair to *remote*, with the deploy's secrets on its GitHub repository.

    A first publication (the remote advertises no ref) sets the secrets the repository
    lacks from ``.env`` before :func:`haute._git.push_working_pair` creates the default
    branch, so the workflow runs that branch starts have them; after any other Push they
    are listed once it has landed. Either outcome, and settings that cannot be read, is
    the response's ``ci_secrets``; none of it stops the push. A remote they do not apply
    to gets no ``ci_secrets``.
    """
    report: GitCiSecrets | None = None
    try:
        secrets = remote_secrets(remote, project_dir)
    except _SettingsUnreadableError as exc:
        secrets, report = None, _report(exc.repository, "unknown", problem=str(exc))
    if secrets is not None and _git.remote_is_empty(remote, cwd=project_dir):
        report = _fill_before_first_publication(secrets)
    response = _git.push_working_pair(remote, project_dir, cwd=project_dir)
    if secrets is not None and report is None:
        report = _listed(secrets)
    return response.model_copy(update={"ci_secrets": report})


def set_missing_ci_secrets(remote: str, project_dir: Path) -> GitCiSecrets:
    """Set the deploy's secrets *remote*'s GitHub repository lacks, from ``.env``.

    The push panel's Set from .env: nothing is overwritten. Raises
    :class:`CiSecretsError` naming why when they do not apply to *remote* or a step cannot
    be taken; a secret set before a later one failed stays set.
    """
    found = _remote_secrets_or_reason(remote, project_dir)
    if not isinstance(found, RemoteSecrets):
        raise CiSecretsError(found)
    with _EDITOR_STEP:
        gh = resolve_gh()
        present = github_secret_names(gh, project_dir, found.repository)
        missing = [name for name in found.names if name not in present]
        newly_set: list[str] = []
        _set_missing(found, gh, missing, newly_set)
        return _report(found.repository, "ready", newly_set=newly_set)
