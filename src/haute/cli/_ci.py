"""``haute ci`` commands: the CI files and secrets a project's deploys need.

* ``haute ci secrets`` sets the CI secrets the deploy target needs from ``.env``, and
  with ``--check`` reports which the CI provider has. It drives GitHub through the
  GitHub CLI (``gh``), by way of :mod:`haute._ci_secrets`, which the editor's Push
  shares; for GitLab and Azure DevOps it names the secrets to add by hand.
* ``haute ci update`` rewrites the CI files ``haute init`` generated, and
  ``.env.example``, for the project's provider, target, workbench and Python, so a
  project takes up a newer haute's workflows without being initialised again.
* ``haute ci registry-host`` prints the host of the registry a container target pushes
  to, which the login step of its generated deploy jobs logs in to.

Split into the typed inputs (:class:`CiSecretsConfig`), the handlers
(:func:`handle_ci_secrets`, :func:`handle_ci_update`, :func:`handle_ci_registry_host`) and
the thin click commands.
"""

from __future__ import annotations

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


def handle_ci_secrets(config: CiSecretsConfig) -> None:
    """Set the CI secrets of the project's CI credentials from ``.env``, or report which are
    set: the deploy target's, or a workbench project's app host's (``ci_credentials``).

    Values are read from the project's ``.env``, or from the environment for a name
    ``.env`` does not set, never printed, and passed to ``gh secret set`` on its standard
    input, replacing any the repository has. A value that is unset, or still
    ``.env.example``'s placeholder, sets nothing. For Databricks both credential pairs
    are first checked against the workspace, unless ``verify`` is off. Exits 1
    (``ClickException``) on any failure, and, with ``check_only``, when a secret is
    missing.
    """
    from haute import _ci_secrets
    from haute._scaffold import ci_credentials, ci_secret_names
    from haute._workbench_config import WorkbenchConfigError

    project_dir, deploy_config = _project()
    try:
        target = ci_credentials(project_dir, deploy_config.target)
    except WorkbenchConfigError as exc:
        raise click.ClickException(str(exc)) from exc
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
    try:
        gh = _ci_secrets.resolve_gh()
        if config.check_only:
            _report_secrets(names, _ci_secrets.github_secret_names(gh, project_dir))
        else:
            _set_every_secret(gh, project_dir, target, names, verify=config.verify)
    except _ci_secrets.DatabricksRefusedError as exc:
        raise click.ClickException(f"{exc} Or pass --no-verify to set them unchecked.") from exc
    except _ci_secrets.CiSecretsError as exc:
        raise click.ClickException(str(exc)) from exc


def _report_secrets(names: list[str], present: set[str]) -> None:
    """Mark each of the deploy's secrets set or missing; a ``ClickException`` if any is missing."""
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


def _set_every_secret(
    gh: str, project_dir: Path, target: str, names: list[str], *, verify: bool
) -> None:
    """Set every one of the deploy's secrets from ``.env``, replacing any the repository has."""
    from haute import _ci_secrets

    values, sources = _ci_secrets.usable_secret_values(project_dir, target, names)
    if verify and target == "databricks":
        for host_name, token_name, user in _ci_secrets.verify_databricks(values):
            click.echo(f"  ✓ {host_name}/{token_name} authenticate as {user}")
    for name in names:
        _ci_secrets.set_github_secret(gh, project_dir, name, values[name])
        click.echo(f"  ✓ {name} (from {sources[name]})")
    click.echo(f"Set the {len(names)} secrets the deploy needs on the GitHub repository.")


def handle_ci_update() -> None:
    """Rewrite the generated CI files and ``.env.example`` for the installed haute.

    The files are generated as ``haute init`` would for the project's ``[ci]
    provider``, ``connections.toml`` and Python: a workbench project's for its
    ``[workbench.app] host``, an automated project's for its ``[deploy] target``, with the
    staging steps on its
    ``[ci.staging] endpoint_suffix`` and the production job under its ``[ci.production]
    environment``; every other generated file (another provider's, or the other kind of
    project's) is removed, and every other file is left alone. Reports each file as updated
    or unchanged.
    """
    from haute._connections import ConnectionsError
    from haute._project_python import ProjectPythonError, project_python_version
    from haute._scaffold import (
        app_env_example,
        ci_credentials,
        ci_files,
        connection_variables,
        env_example,
        production_environment_problem,
        staging_suffix_problem,
        workbench_ci_files,
    )
    from haute._workbench_config import WorkbenchConfigError, read_workbench_config
    from haute.cli._init_cmd import _prune_stale_ci_files, write_generated_files

    project_dir, deploy_config = _project()
    provider = deploy_config.ci.provider
    try:
        workbench = read_workbench_config(project_dir).enabled
        # A workbench project's CI reads its app host's credentials, a host it must name.
        credentials = ci_credentials(project_dir, deploy_config.target)
    except WorkbenchConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    staging_suffix = deploy_config.ci.staging_endpoint_suffix
    production_environment = deploy_config.ci.production_environment
    # A workbench project's workflows deploy no endpoint, so neither key reaches them.
    checked = (
        ()
        if workbench
        else (
            ("[ci.staging] endpoint_suffix", staging_suffix_problem(staging_suffix)),
            ("[ci.production] environment", production_environment_problem(production_environment)),
        )
    )
    for key, problem in checked:
        if problem is not None:
            raise click.ClickException(f"{key} in haute.toml {problem}.")
    try:
        python_version = project_python_version(project_dir)
    except ProjectPythonError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        generated = (
            workbench_ci_files(credentials, provider, python_version=python_version)
            if workbench
            else ci_files(
                credentials,
                provider,
                python_version=python_version,
                staging_suffix=staging_suffix,
                production_environment=production_environment,
            )
        )
    except ValueError as exc:
        raise click.ClickException(f"{exc} in [ci] provider of haute.toml.") from exc
    try:
        variables = connection_variables(project_dir)
    except ConnectionsError as exc:
        raise click.ClickException(str(exc)) from exc
    env_text = (
        app_env_example(credentials, variables)
        if workbench
        else env_example(credentials, variables)
    )
    files = {**generated, ".env.example": env_text}
    _prune_stale_ci_files(project_dir, keep=generated.keys())
    changed = write_generated_files(project_dir, files)
    for rel in files:
        click.echo(f"  {'updated  ' if rel in changed else 'unchanged'} {rel}")
    if changed:
        click.echo("Review the changes (git diff) and commit them.")
    else:
        click.echo("The CI files are up to date.")


def registry_host(registry: str) -> str:
    """The host ``docker login`` takes for images pushed under *registry*, ``""`` for none.

    Docker reads a reference's first ``/`` segment as its registry's host when the segment
    holds a ``.`` or a ``:`` or is ``localhost``; anything else is a Docker Hub namespace,
    whose host is ``docker.io``.
    """
    if not registry:
        return ""
    first = registry.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        return first
    return "docker.io"


def handle_ci_registry_host() -> None:
    """Print the host of the project's ``[deploy.container] registry`` as the last line.

    A container-based target's generated deploy jobs log in to it before ``haute deploy``
    pushes, reading the last line, since a log line may come first. An empty registry
    prints an empty line: the image then stays local.
    """
    _, deploy_config = _project()
    click.echo(registry_host(deploy_config.container.registry))


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


@ci.command(name="registry-host")
def registry_host_command() -> None:
    """Print the host of the registry a container deploy pushes to."""
    handle_ci_registry_host()
