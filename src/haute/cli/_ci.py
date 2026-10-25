"""``haute ci`` commands: the CI files and secrets a project's deploys need.

* ``haute ci secrets`` sets the CI secrets the deploy target needs from ``.env``, and
  with ``--check`` reports which the CI provider has. It drives GitHub through the
  GitHub CLI (``gh``); for GitLab and Azure DevOps it names the secrets to add by hand.
* ``haute ci update`` rewrites the CI files ``haute init`` generated, and
  ``.env.example``, for the project's provider, target, workbench and Python, so a
  project takes up a newer haute's workflows without being initialised again.

Split into the typed inputs (:class:`CiSecretsConfig`), the handlers
(:func:`handle_ci_secrets`, :func:`handle_ci_update`) and the thin click commands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from haute.deploy._config import DeployConfig

#: Where each provider's guide shows adding the secrets by hand.
_PROVIDER_GUIDES = {
    "github": "https://pricingfrontier.github.io/haute/deployment/ci/github-actions/",
    "gitlab": "https://pricingfrontier.github.io/haute/deployment/ci/gitlab/",
    "azure-devops": "https://pricingfrontier.github.io/haute/deployment/ci/azure-devops/",
}


@dataclass
class CiSecretsConfig:
    """Parsed inputs for ``haute ci secrets``."""

    check_only: bool
    verify: bool


def _project() -> tuple[Path, DeployConfig]:
    """The project in the working directory and its ``haute.toml``."""
    from haute.deploy._config import DeployConfig

    project_dir = Path.cwd()
    toml_path = project_dir / "haute.toml"
    if not toml_path.is_file():
        raise click.ClickException("No haute.toml found: run this in a Haute project.")
    return project_dir, DeployConfig.from_toml(toml_path)


def _gh(project_dir: Path) -> str:
    """The GitHub CLI on PATH, or a ``ClickException`` saying how to install it."""
    found = shutil.which("gh")
    if found is None:
        raise click.ClickException(
            "The GitHub CLI (gh) is not installed: install it from https://cli.github.com, "
            "run `gh auth login`, then run this again."
        )
    return found


def _run_gh(gh: str, project_dir: Path, *args: str, stdin: str | None = None) -> str:
    """Run ``gh`` in the project's repository; its stdout, or its stderr as the error."""
    result = subprocess.run(  # noqa: S603 - gh from PATH, fixed arguments
        [gh, *args],
        cwd=project_dir,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise click.ClickException(f"`gh {' '.join(args)}` failed: {detail}")
    return result.stdout


def _github_secret_names(gh: str, project_dir: Path) -> set[str]:
    """The names of the repository's Actions secrets (``gh secret list``)."""
    listed = json.loads(_run_gh(gh, project_dir, "secret", "list", "--json", "name") or "[]")
    return {str(entry["name"]) for entry in listed}


def _secret_values(project_dir: Path, names: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Each secret's value and where it was read: ``.env``, else the environment.

    ``.env`` comes first because it is what the command sets secrets from; a variable
    only the environment sets (a shell profile's, say) is used when ``.env`` lacks it,
    and the output says so. Unset and blank values are left out.
    """
    from dotenv import dotenv_values

    env_file = project_dir / ".env"
    from_file = dotenv_values(env_file) if env_file.is_file() else {}
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


def _verify_databricks(values: dict[str, str]) -> None:
    """Refuse credentials Databricks does not accept, naming the pair and its use."""
    from haute._credential_security import redact_sensitive_text
    from haute._mlflow_utils import MLFLOW_DATABRICKS_HOST_ENV, MLFLOW_DATABRICKS_TOKEN_ENV
    from haute.deploy._databricks_serving import RATING_HOST_ENV, RATING_TOKEN_ENV, workspace_user

    pairs = (
        (MLFLOW_DATABRICKS_HOST_ENV, MLFLOW_DATABRICKS_TOKEN_ENV, "registers the model in MLflow"),
        (RATING_HOST_ENV, RATING_TOKEN_ENV, "creates and calls the serving endpoint"),
    )
    for host_name, token_name, purpose in pairs:
        try:
            user = workspace_user(values[host_name], values[token_name])
        except Exception as exc:
            detail = redact_sensitive_text(str(exc), known_secrets=(values[token_name],))
            raise click.ClickException(
                f"Databricks refused {host_name} and {token_name}, the pair that {purpose}: "
                f"{detail}. Correct them in .env, or pass --no-verify to set them unchecked."
            ) from exc
        click.echo(f"  ✓ {host_name}/{token_name} authenticate as {user}")


def handle_ci_secrets(config: CiSecretsConfig) -> None:
    """Set the deploy target's CI secrets from ``.env``, or report which are set.

    Values are read from the project's ``.env``, or from the environment for a name
    ``.env`` does not set (:func:`_secret_values`), never printed, and passed to ``gh
    secret set`` on its standard input. A value that is unset, or still
    ``.env.example``'s placeholder, sets nothing. For Databricks both credential pairs
    are first checked against the workspace, unless ``verify`` is off. Exits 1
    (``ClickException``) on any failure, and, with ``check_only``, when a secret is
    missing.
    """
    from haute._scaffold import ci_secret_names, env_example_placeholders

    project_dir, deploy_config = _project()
    target = deploy_config.target
    provider = deploy_config.ci.provider
    names = ci_secret_names(target)
    if not names:
        click.echo(f"The {target} target needs no CI secrets.")
        return
    if provider != "github":
        guide = _PROVIDER_GUIDES.get(provider, _PROVIDER_GUIDES["github"])
        raise click.ClickException(
            f"haute sets CI secrets on GitHub; this project's CI provider is {provider}. Add "
            "these by hand, as its guide shows (" + guide + "):\n  " + "\n  ".join(names)
        )
    gh = _gh(project_dir)

    if config.check_only:
        present = _github_secret_names(gh, project_dir)
        for name in names:
            mark = "✓" if name in present else "✗"
            click.echo(f"  {mark} {name}")
        missing = [name for name in names if name not in present]
        if missing:
            raise click.ClickException(
                f"The repository is missing {len(missing)} of the {len(names)} secrets the "
                f"deploy needs: {', '.join(missing)}. Set them from .env with "
                "`haute ci secrets`."
            )
        click.echo(f"All {len(names)} secrets the deploy needs are set.")
        return

    values, sources = _secret_values(project_dir, names)
    unset = [name for name in names if not values.get(name)]
    if unset:
        raise click.ClickException(
            f"Not set in .env or the environment: {', '.join(unset)}. Copy .env.example to "
            ".env, fill in every value, and run this again."
        )
    placeholders = env_example_placeholders(target)
    unedited = [name for name in names if values[name] == placeholders.get(name)]
    if unedited:
        raise click.ClickException(
            f"Still .env.example's placeholder: {', '.join(unedited)}. Put the real values in "
            ".env and run this again."
        )
    if config.verify and target == "databricks":
        _verify_databricks(values)
    for name in names:
        _run_gh(gh, project_dir, "secret", "set", name, stdin=values[name])
        click.echo(f"  ✓ {name} (from {sources[name]})")
    click.echo(f"Set the {len(names)} secrets the deploy needs on the GitHub repository.")


def handle_ci_update() -> None:
    """Rewrite the generated CI files and ``.env.example`` for the installed haute.

    The files are generated as ``haute init`` would for the project's ``[ci]
    provider``, ``[deploy] target``, workbench and Python, with the staging steps on its
    ``[ci.staging] endpoint_suffix`` and the production job under its ``[ci.production]
    environment``; another provider's generated files are removed, and every other file is
    left alone. Reports each file as updated or unchanged.
    """
    from haute._project_python import ProjectPythonError, project_python_version
    from haute._scaffold import (
        ci_files,
        env_example,
        production_environment_problem,
        staging_suffix_problem,
    )
    from haute._workbench_config import WorkbenchConfigError, read_workbench_config
    from haute.cli._init_cmd import _prune_stale_ci_files, write_generated_files

    project_dir, deploy_config = _project()
    provider = deploy_config.ci.provider
    target = deploy_config.target
    staging_suffix = deploy_config.ci.staging_endpoint_suffix
    production_environment = deploy_config.ci.production_environment
    for key, problem in (
        ("[ci.staging] endpoint_suffix", staging_suffix_problem(staging_suffix)),
        ("[ci.production] environment", production_environment_problem(production_environment)),
    ):
        if problem is not None:
            raise click.ClickException(f"{key} in haute.toml {problem}.")
    try:
        python_version = project_python_version(project_dir)
        workbench = read_workbench_config(project_dir).enabled
    except (ProjectPythonError, WorkbenchConfigError) as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        generated = ci_files(
            target,
            provider,
            workbench=workbench,
            python_version=python_version,
            staging_suffix=staging_suffix,
            production_environment=production_environment,
        )
    except ValueError as exc:
        raise click.ClickException(f"{exc} in [ci] provider of haute.toml.") from exc
    files = {**generated, ".env.example": env_example(target)}
    _prune_stale_ci_files(project_dir, keep=provider)
    changed = write_generated_files(project_dir, files)
    for rel in files:
        click.echo(f"  {'updated  ' if rel in changed else 'unchanged'} {rel}")
    if changed:
        click.echo("Review the changes (git diff) and commit them.")
    else:
        click.echo("The CI files are up to date.")


@click.group(name="ci")
def ci() -> None:
    """The CI files and secrets a project's deploys need."""


@ci.command(name="secrets")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report which of the deploy's secrets the CI provider has, without setting any.",
)
@click.option(
    "--no-verify",
    is_flag=True,
    help="Set the secrets without first checking that Databricks accepts them.",
)
def secrets(check_only: bool, no_verify: bool) -> None:
    """Set the CI secrets the deploy needs, from .env (GitHub, through gh)."""
    handle_ci_secrets(CiSecretsConfig(check_only=check_only, verify=not no_verify))


@ci.command(name="update")
def update() -> None:
    """Rewrite the generated CI files and .env.example for this haute."""
    handle_ci_update()
