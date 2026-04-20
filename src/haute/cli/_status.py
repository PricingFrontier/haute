"""``haute status`` command.

Split into:

* :class:`StatusConfig` — the typed bag of CLI inputs.
* :func:`handle_status` — the pure function that does the work.
* :func:`status` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from haute.cli._helpers import resolve_model_name


@dataclass
class StatusConfig:
    """Parsed inputs for the ``haute status`` command."""

    model_name: str | None
    version_only: bool


def handle_status(config: StatusConfig) -> None:
    """Check the status of a deployed model and print a summary.

    Resolves the model name via :func:`resolve_model_name` so behaviour is
    consistent with every other CLI command: an explicit positional arg
    wins over the TOML value, and missing both sources produces a clear
    error message pointing at the two user-facing fixes.
    """
    from haute.deploy._config import DatabricksConfig, DeployConfig

    toml_path = Path.cwd() / "haute.toml"
    toml_exists = toml_path.exists()

    # Resolve model name: CLI > TOML > error with hint.
    try:
        model_name = resolve_model_name(
            config.model_name,
            toml_path if toml_exists else None,
        )
    except (ValueError, FileNotFoundError) as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc

    # Load catalog/schema from haute.toml if available; else fall back to
    # DatabricksConfig defaults.
    if toml_exists:
        deploy_config = DeployConfig.from_toml(toml_path)
        databricks = deploy_config.databricks
    else:
        databricks = DatabricksConfig()

    try:
        from haute.deploy._mlflow import get_deploy_status

        info = get_deploy_status(
            model_name,
            catalog=databricks.catalog,
            schema=databricks.schema,
        )
    except ImportError:
        click.echo(
            "Error: mlflow is not installed. Install with: uv add 'haute[databricks]'",
            err=True,
        )
        raise SystemExit(1)

    if config.version_only:
        click.echo(info.get("latest_version", 0))
        return

    if info.get("status") == "not_found":
        click.echo(f"Model '{model_name}' not found in MLflow Model Registry.")
        return

    click.echo(f"Model: {info['model_name']}")
    click.echo(f"  Latest version: {info['latest_version']}")
    click.echo(f"  Stage: {info.get('latest_stage', 'N/A')}")
    click.echo(f"  Status: {info['status']}")
    click.echo(f"  Run ID: {info.get('run_id', 'N/A')}")


@click.command()
@click.argument("model_name", required=False)
@click.option(
    "--version-only",
    is_flag=True,
    help="Print only the latest version number (for scripting).",
)
def status(model_name: str | None, version_only: bool) -> None:
    """Check the status of a deployed model."""
    config = StatusConfig(model_name=model_name, version_only=version_only)
    handle_status(config)
