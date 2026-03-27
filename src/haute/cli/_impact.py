"""``haute impact`` command."""

from pathlib import Path

import click

from haute.cli._helpers import _load_deploy_config, resolve_transport


@click.command()
@click.option(
    "--endpoint-suffix",
    default=None,
    help='Suffix identifying the staging endpoint (e.g. "-staging").',
)
@click.option(
    "--sample",
    default=10000,
    type=int,
    help="Max rows to score (0 = all). Default: 10000.",
)
@click.option(
    "--batch-size",
    default=500,
    type=click.IntRange(min=1),
    help="Rows per endpoint request. Default: 500.",
)
def impact(endpoint_suffix: str | None, sample: int, batch_size: int) -> None:
    """Compare staging vs production endpoint predictions.

    Scores the safety impact dataset through both the staging and production
    endpoints, computes pricing change metrics, and writes a report.
    Output goes to stdout (terminal) and to $GITHUB_STEP_SUMMARY when
    running in GitHub Actions so reviewers can inspect before approving
    the production deployment.
    """
    import os

    from haute.deploy._impact import (
        ImpactReport,
        build_report,
        format_markdown,
        format_terminal,
    )

    config = _load_deploy_config(require_toml=True)

    # Determine endpoint names
    staging_suffix = endpoint_suffix or config.ci.staging_endpoint_suffix or "_staging"
    base_name = config.endpoint_name or config.model_name
    staging_name = base_name + staging_suffix
    prod_name = base_name

    # Load impact dataset
    impact_path = config.safety.impact_dataset
    if not impact_path:
        click.echo(
            "Error: No impact_dataset configured in [safety] section of haute.toml.",
            err=True,
        )
        raise SystemExit(1)

    import polars as pl

    dataset_file = (Path.cwd() / impact_path).resolve()
    if not dataset_file.exists():
        click.echo(f"Error: Impact dataset not found: {dataset_file}", err=True)
        raise SystemExit(1)

    df = pl.read_parquet(dataset_file)
    total_rows = len(df)

    if sample > 0 and total_rows > sample:
        df = df.sample(n=sample, seed=42)

    records = df.to_dicts()

    click.echo(f"Impact analysis: {staging_name} vs {prod_name}")
    click.echo(f"  Dataset: {impact_path} ({len(records):,} rows)")

    # Score via the appropriate transport
    transport = resolve_transport(config)

    if transport.kind == "databricks":
        staging_preds, prod_preds, prod_exists = _impact_databricks(
            staging_name,
            prod_name,
            records,
            batch_size,
        )
    elif transport.kind == "http":
        staging_preds, prod_preds, prod_exists = _impact_http(
            transport.staging_url,
            transport.prod_url,
            records,
            batch_size,
        )
    else:
        click.echo(
            f"  \u26a0 Impact analysis not yet implemented for target '{config.target}'.",
            err=True,
        )
        return

    # Build report
    if not prod_exists:
        report = ImpactReport(
            pipeline_name=config.model_name,
            staging_endpoint=staging_name,
            prod_endpoint=prod_name,
            dataset_path=impact_path,
            total_rows=total_rows,
            sampled_rows=len(records),
            scored_rows=len(staging_preds),
            failed_rows=len(records) - len(staging_preds),
            column_stats=[],
            segments={},
            is_first_deploy=True,
        )
    else:
        report = build_report(
            staging_preds=staging_preds,
            prod_preds=prod_preds,
            input_df=df,
            pipeline_name=config.model_name,
            staging_endpoint=staging_name,
            prod_endpoint=prod_name,
            dataset_path=impact_path,
            total_rows=total_rows,
        )

    # Print terminal report
    click.echo(format_terminal(report))

    # Always write portable markdown artifact (works on any CI platform)
    md = format_markdown(report)
    report_path = Path.cwd() / "impact_report.md"
    report_path.write_text(md, encoding="utf-8")
    click.echo(f"  \u2192 Report written to {report_path}")

    # Platform-specific CI summary integration
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write(md)
        click.echo("  \u2192 Report written to GitHub Step Summary")


def _impact_databricks(
    staging_name: str,
    prod_name: str,
    records: list[dict],
    batch_size: int,
) -> tuple[list, list, bool]:
    """Score through Databricks endpoints for impact analysis."""
    from haute.deploy._config import _load_env
    from haute.deploy._impact import score_endpoint_batched

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        click.echo(
            "Error: databricks-sdk not installed. Install with: uv add haute[databricks]",
            err=True,
        )
        raise SystemExit(1)

    _load_env(Path.cwd())
    ws = WorkspaceClient()

    # Check if prod endpoint exists
    prod_exists = True
    try:
        ws.serving_endpoints.get(prod_name)
    except Exception as exc:
        not_found_names = ("NotFound", "ResourceDoesNotExist")
        if type(exc).__name__ in not_found_names:
            click.echo(f"  First deployment - production endpoint '{prod_name}' not found")
        else:
            click.echo(f"  \u26a0 Could not reach production endpoint '{prod_name}': {exc}")
        prod_exists = False

    # Score staging
    click.echo(f"  Scoring through staging ({staging_name})...")
    staging_preds = score_endpoint_batched(ws, staging_name, records, batch_size, click.echo)

    if prod_exists:
        click.echo(f"  Scoring through production ({prod_name})...")
        prod_preds = score_endpoint_batched(ws, prod_name, records, batch_size, click.echo)
    else:
        prod_preds = []

    return staging_preds, prod_preds, prod_exists


def _impact_http(
    staging_url: str,
    prod_url: str,
    records: list[dict],
    batch_size: int,
) -> tuple[list, list, bool]:
    """Score through HTTP endpoints (container target) for impact analysis."""
    from haute.deploy._impact import score_http_endpoint_batched

    # Score staging
    click.echo(f"  Scoring through staging ({staging_url})...")
    staging_preds = score_http_endpoint_batched(
        staging_url,
        records,
        batch_size,
        click.echo,
    )

    # Score production (if URL is configured)
    prod_exists = bool(prod_url)
    prod_preds: list = []
    if prod_exists:
        click.echo(f"  Scoring through production ({prod_url})...")
        try:
            prod_preds = score_http_endpoint_batched(
                prod_url,
                records,
                batch_size,
                click.echo,
            )
        except Exception as exc:
            click.echo(f"  First deployment - production endpoint not reachable: {exc}")
            prod_exists = False

    return staging_preds, prod_preds, prod_exists
