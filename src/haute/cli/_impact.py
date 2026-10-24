"""``haute impact`` command.

Split into:

* :class:`ImpactConfig` — the typed bag of CLI inputs.
* :func:`handle_impact` — the pure function that does the work.
* :func:`impact` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from haute._project import get_project_root
from haute.cli._helpers import ENDPOINT_SUFFIX_HELP, resolve_transport

if TYPE_CHECKING:
    import polars as pl

    from haute.deploy._config import DeployConfig


@dataclass
class ImpactConfig:
    """Parsed inputs for the ``haute impact`` command."""

    endpoint_suffix: str | None
    sample: int
    batch_size: int


def handle_impact(config: ImpactConfig) -> None:
    """Score the impact dataset through staging + production endpoints.

    Compares predictions from the staging and production endpoints across
    the configured safety dataset, writes a markdown/terminal report, and
    (when running on GitHub Actions) appends the report to the step
    summary.  Requires a ``haute.toml`` with a ``[safety].impact_dataset``
    entry; fails loudly if either is missing.
    """
    import os

    from haute.deploy._config import DeployConfig
    from haute.deploy._impact import (
        ImpactReport,
        build_report,
        format_markdown,
        format_terminal,
    )

    toml_path = Path.cwd() / "haute.toml"
    if not toml_path.exists():
        click.echo("Error: No haute.toml found.", err=True)
        raise SystemExit(1)

    deploy_config = DeployConfig.from_toml(toml_path)
    click.echo("  \u2713 Loaded config from haute.toml")
    project_root = get_project_root()

    if config.endpoint_suffix and deploy_config.target != "databricks":
        click.echo(
            "Error: --endpoint-suffix is only supported for Databricks. "
            "Set the full [ci.staging].endpoint_url in haute.toml for HTTP targets.",
            err=True,
        )
        raise SystemExit(1)
    transport = resolve_transport(deploy_config)

    # Resolve staging suffix: CLI flag wins when given, otherwise the
    # TOML-loaded ``deploy_config.ci.staging_endpoint_suffix`` is
    # authoritative.  No literal fallback — a blank suffix would produce
    # ``staging_name == prod_name`` which silently invalidates the impact
    # comparison, so we fail loudly and point the user at the config key
    # they need to set.
    staging_suffix = (
        config.endpoint_suffix
        if config.endpoint_suffix
        else deploy_config.ci.staging_endpoint_suffix
    )
    if transport.kind == "databricks" and not staging_suffix:
        click.echo(
            "Error: No staging endpoint suffix configured. "
            "Set [ci.staging] endpoint_suffix in haute.toml "
            "or pass --endpoint-suffix.",
            err=True,
        )
        raise SystemExit(1)

    base_name = deploy_config.endpoint_name or deploy_config.model_name
    staging_name = base_name + (staging_suffix or "")
    prod_name = base_name

    # The quotes to compare: the [safety] portfolio sample, or the test quotes without one.
    records, input_df, total_rows, dataset_label = _impact_records(
        deploy_config, project_root, sample=config.sample
    )

    click.echo(f"Impact analysis: {staging_name} vs {prod_name}")
    click.echo(f"  Dataset: {dataset_label} ({len(records):,} rows)")
    # Test quotes go one per request, as smoke sends them: an endpoint a Workbench
    # Input answers takes exactly one quote per request.
    batch_size = config.batch_size if deploy_config.safety.impact_dataset else 1

    # Score via the appropriate transport
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
            f"  \u26a0 Impact analysis not yet implemented for target '{deploy_config.target}'.",
            err=True,
        )
        return

    # Build report
    if not prod_exists:
        report = ImpactReport(
            pipeline_name=deploy_config.model_name,
            staging_endpoint=staging_name,
            prod_endpoint=prod_name,
            dataset_path=dataset_label,
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
            input_df=input_df,
            pipeline_name=deploy_config.model_name,
            staging_endpoint=staging_name,
            prod_endpoint=prod_name,
            dataset_path=dataset_label,
            total_rows=total_rows,
        )

    # Print terminal report
    click.echo(format_terminal(report))

    # Always write portable markdown artifact (works on any CI platform)
    md = format_markdown(report)
    report_path = project_root / "impact_report.md"
    report_path.write_text(md, encoding="utf-8")
    click.echo(f"  \u2192 Report written to {report_path}")

    # Platform-specific CI summary integration
    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a", encoding="utf-8") as f:
            f.write(md)
        click.echo("  \u2192 Report written to GitHub Step Summary")


def _impact_records(
    deploy_config: DeployConfig, project_root: Path, *, sample: int
) -> tuple[list[dict[str, Any]], pl.DataFrame, int, str]:
    """The quotes impact compares: ``(records, input frame, total rows, dataset label)``.

    ``[safety] impact_dataset`` names a Parquet portfolio sample, sampled to *sample*
    rows. Without one the test quotes are compared, the requests smoke sends, so a new
    project's impact step runs before anyone has committed a portfolio sample; the
    report names which it compared. Exits 1 when the named sample is missing, or, with
    none named, when there are no test quotes to compare.
    """
    import polars as pl

    impact_path = deploy_config.safety.impact_dataset
    if impact_path:
        dataset_file = (project_root / impact_path).resolve()
        if not dataset_file.exists():
            click.echo(f"Error: Impact dataset not found: {dataset_file}", err=True)
            raise SystemExit(1)
        df = pl.read_parquet(dataset_file)
        total_rows = len(df)
        if sample > 0 and total_rows > sample:
            df = df.sample(n=sample, seed=42)
        return df.to_dicts(), df, total_rows, impact_path

    from haute.deploy._validators import test_quote_requests

    tq_dir = deploy_config.test_quotes_dir
    if tq_dir is None or not tq_dir.is_dir():
        click.echo(
            "Error: No impact_dataset is set in [safety] of haute.toml, and there is no test "
            f"quotes directory to compare instead (resolved: {tq_dir}).",
            err=True,
        )
        raise SystemExit(1)
    try:
        records = test_quote_requests(tq_dir)
    except (OSError, ValueError) as exc:
        click.echo(f"Error: Could not read the test quotes in {tq_dir}: {exc}", err=True)
        raise SystemExit(1) from exc
    if not records:
        click.echo(
            f"Error: No impact_dataset is set in [safety] of haute.toml, and {tq_dir} has no "
            "test quotes to compare instead.",
            err=True,
        )
        raise SystemExit(1)
    click.echo(
        "  No [safety] impact_dataset is set: comparing the test quotes. Commit a portfolio "
        "sample and set impact_dataset to compare a representative book."
    )
    try:
        label = tq_dir.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        label = str(tq_dir)
    # Segments are read from a quote's text fields; its tables are not segmented.
    input_df = pl.DataFrame(
        [
            {key: value for key, value in record.items() if isinstance(value, str)}
            for record in records
        ],
        infer_schema_length=None,
    )
    return records, input_df, len(records), f"test quotes ({label})"


@click.command()
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
@click.option(
    "--endpoint-suffix",
    default=None,
    help=ENDPOINT_SUFFIX_HELP,
)
def impact(endpoint_suffix: str | None, sample: int, batch_size: int) -> None:
    """Compare staging vs production endpoint predictions.

    Scores the safety impact dataset through both the staging and production
    endpoints, computes pricing change metrics, and writes a report.
    Output goes to stdout (terminal) and to $GITHUB_STEP_SUMMARY when
    running in GitHub Actions so reviewers can inspect before approving
    the production deployment.
    """
    config = ImpactConfig(
        endpoint_suffix=endpoint_suffix,
        sample=sample,
        batch_size=batch_size,
    )
    handle_impact(config)


# Exception class names the Databricks SDK raises when an endpoint is not
# found. These indicate "production has never been deployed" and are the
# only shapes that should flip ``prod_exists`` to False — every other
# exception (timeout, 5xx, connection error) is a real failure and must
# propagate.
_DATABRICKS_NOT_FOUND_CLASSES: frozenset[str] = frozenset({"NotFound", "ResourceDoesNotExist"})


def _is_databricks_not_found(exc: BaseException) -> bool:
    """Return ``True`` iff *exc* represents a 'endpoint does not exist' signal.

    The Databricks SDK uses the exception class name (``NotFound`` or
    ``ResourceDoesNotExist``) to communicate 404-style errors. We look at
    the class name across the MRO so that subclassed exceptions — and the
    dynamically-constructed test doubles in the test suite — both count.
    """
    for klass in type(exc).__mro__:
        if klass.__name__ in _DATABRICKS_NOT_FOUND_CLASSES:
            return True
    return False


def _is_http_not_found(exc: BaseException) -> bool:
    """Return ``True`` iff *exc* is an HTTP 404 wrapped by ``score_http_*``.

    ``haute.deploy._impact.score_http_endpoint_batched`` turns urllib
    ``HTTPError`` into a ``RuntimeError`` whose message embeds ``HTTP 404``.
    We inspect the message for that exact token so that other status codes
    (5xx) and transport-level exceptions (``TimeoutError``,
    ``ConnectionRefusedError``, …) remain genuine failures and propagate
    to the caller.
    """
    if not isinstance(exc, RuntimeError):
        return False
    return "HTTP 404" in str(exc)


def _impact_databricks(
    staging_name: str,
    prod_name: str,
    records: list[dict],
    batch_size: int,
) -> tuple[list, list, bool]:
    """Score through Databricks endpoints for impact analysis.

    Only ``NotFound`` / ``ResourceDoesNotExist`` from the prod endpoint
    lookup are classified as "first deploy" — every other exception
    (timeout, 5xx, connection refused, etc.) is re-raised so the caller
    sees the real failure rather than silently treating transient issues
    as "no prod yet".
    """
    from haute.deploy._config import _load_env
    from haute.deploy._databricks_serving import ServingEndpointError, serving_workspace_client
    from haute.deploy._impact import score_endpoint_batched

    try:
        import databricks.sdk  # noqa: F401 - the SDK must be installed
    except ImportError:
        click.echo(
            "Error: databricks-sdk not installed. Install with: uv add 'haute[databricks]'",
            err=True,
        )
        raise SystemExit(1)

    _load_env(Path.cwd())
    # Both endpoints are reached with the serving pair, as deploy reaches them.
    try:
        ws = serving_workspace_client()
    except ServingEndpointError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc

    # Check if prod endpoint exists. Only 'does not exist' signals flip
    # prod_exists to False; anything else propagates.
    prod_exists = True
    try:
        ws.serving_endpoints.get(prod_name)
    except Exception as exc:
        if _is_databricks_not_found(exc):
            click.echo(f"  First deployment - production endpoint '{prod_name}' not found")
            prod_exists = False
        else:
            raise

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
    """Score through HTTP endpoints (container target) for impact analysis.

    Only an HTTP 404 on the prod URL (surfaced as ``RuntimeError('HTTP 404 ...')``
    by :mod:`haute.deploy._impact`) is treated as 'no prod yet'. Every other
    exception — timeouts, 5xx, ``ConnectionRefusedError``, and so on — is
    re-raised so transport failures are never silently misclassified as
    first-deploy scenarios.
    """
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
            if _is_http_not_found(exc):
                click.echo(
                    "  First deployment - production endpoint not yet available "
                    f"(404 at {prod_url})"
                )
                prod_exists = False
                prod_preds = []
            else:
                raise

    return staging_preds, prod_preds, prod_exists
