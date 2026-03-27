"""``haute status`` command."""

import click

from haute.cli._helpers import _load_deploy_config


@click.command()
@click.argument("model_name", required=False)
@click.option(
    "--version-only",
    is_flag=True,
    help="Print only the latest version number (for scripting).",
)
def status(model_name: str | None, version_only: bool) -> None:
    """Check the status of a deployed model."""
    # Load config — always needed for catalog/schema, and for model_name
    # when not explicitly passed on the CLI.
    config = _load_deploy_config(require_toml=(model_name is None))
    if model_name is None:
        model_name = config.model_name

    try:
        from haute.deploy._mlflow import get_deploy_status

        info = get_deploy_status(
            model_name,
            catalog=config.databricks.catalog,
            schema=config.databricks.schema,
        )
    except ImportError:
        click.echo(
            "Error: mlflow is not installed. Install with: uv pip install haute[databricks]",
            err=True,
        )
        raise SystemExit(1)

    if version_only:
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
