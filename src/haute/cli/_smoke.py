"""``haute smoke`` command.

Split into:

* :class:`SmokeConfig` — the typed bag of CLI inputs.
* :func:`handle_smoke` — the pure function that does the work.
* :func:`smoke` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from haute.cli._helpers import ENDPOINT_SUFFIX_HELP, resolve_transport


@dataclass
class SmokeConfig:
    """Parsed inputs for the ``haute smoke`` command."""

    endpoint_suffix: str | None


def handle_smoke(config: SmokeConfig) -> None:
    """Run the smoke-test suite against a live serving endpoint.

    Requires an existing ``haute.toml`` — smoke tests are always run
    inside a configured project.  Exits with ``SystemExit(1)`` on any
    failure (missing config, missing test quotes, endpoint error).
    """
    from haute.deploy._config import DeployConfig

    toml_path = Path.cwd() / "haute.toml"
    if not toml_path.exists():
        click.echo("Error: No haute.toml found.", err=True)
        raise SystemExit(1)

    deploy_config = DeployConfig.from_toml(toml_path)
    if config.endpoint_suffix and deploy_config.target != "databricks":
        click.echo(
            "Error: --endpoint-suffix is only supported for Databricks. "
            "Set the full [ci.staging].endpoint_url in haute.toml for HTTP targets.",
            err=True,
        )
        raise SystemExit(1)
    click.echo("  \u2713 Loaded config from haute.toml")
    if config.endpoint_suffix:
        deploy_config = deploy_config.override(endpoint_suffix=config.endpoint_suffix)

    endpoint_name = deploy_config.effective_endpoint_name
    tq_dir = deploy_config.test_quotes_dir

    if tq_dir is None or not tq_dir.is_dir():
        click.echo(f"Error: No test quotes directory found (resolved: {tq_dir}).", err=True)
        raise SystemExit(1)

    from haute._test_quotes import test_quote_files

    json_files = test_quote_files(tq_dir)
    if not json_files:
        click.echo(f"Error: No test quotes in {tq_dir}.", err=True)
        raise SystemExit(1)

    click.echo(f"Smoke testing endpoint: {endpoint_name}")
    click.echo(f"  Target: {deploy_config.target}")

    transport = resolve_transport(deploy_config)

    all_ok = True

    if transport.kind == "databricks":
        if not endpoint_name:
            click.echo(
                "Error: No endpoint name configured. "
                "Set endpoint_name or endpoint_suffix in haute.toml.",
                err=True,
            )
            raise SystemExit(1)
        all_ok = _smoke_databricks(endpoint_name, json_files)
    elif transport.kind == "http":
        all_ok = _smoke_http(transport.staging_url, json_files)
    else:
        click.echo(
            f"Error: Smoke is not supported for target '{deploy_config.target}'.",
            err=True,
        )
        raise SystemExit(1)

    if not all_ok:
        click.echo("\n  \u2717 Smoke test failed.", err=True)
        raise SystemExit(1)

    click.echo("\n  \u2713 All smoke tests passed.")


@click.command()
@click.option(
    "--endpoint-suffix",
    default=None,
    help=ENDPOINT_SUFFIX_HELP,
)
def smoke(endpoint_suffix: str | None) -> None:
    """Score test quotes against a live serving endpoint.

    Sends each test quote JSON file as an HTTP request to the deployed
    endpoint and validates the response. Used after staging deploys to
    verify the endpoint is functional.
    """
    config = SmokeConfig(endpoint_suffix=endpoint_suffix)
    handle_smoke(config)


def _smoke_databricks(endpoint_name: str, json_files: list[Path]) -> bool:
    """Run smoke tests against a Databricks Model Serving endpoint."""

    try:
        from databricks.sdk import WorkspaceClient  # noqa: F401 - the SDK must be installed
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("databricks"):
            click.echo(
                "Error: installed databricks-sdk is incomplete or incompatible. "
                "Upgrade with: uv add --upgrade databricks-sdk",
                err=True,
            )
            raise SystemExit(1)
        click.echo(
            "Error: databricks-sdk not installed. Install with: uv add haute[databricks]",
            err=True,
        )
        raise SystemExit(1)
    except ImportError:
        click.echo(
            "Error: installed databricks-sdk is incomplete or incompatible. "
            "Upgrade with: uv add --upgrade databricks-sdk",
            err=True,
        )
        raise SystemExit(1)
    try:
        from databricks.sdk.errors import NotFound
    except ImportError:
        click.echo(
            "Error: installed databricks-sdk is too old for endpoint readiness checks. "
            "Upgrade with: uv add --upgrade databricks-sdk",
            err=True,
        )
        raise SystemExit(1)

    from haute.deploy._config import _load_env
    from haute.deploy._databricks_serving import (
        ServingEndpointError,
        refusal_reason,
        serving_workspace_client,
        wait_for_endpoint,
    )

    _load_env(Path.cwd())

    # The endpoint is reached with the serving pair, as deploy reaches it. A deploy
    # returns once its endpoint serves, so this wait is short unless another update
    # is under way; a failed update fails here with Databricks' reason.
    click.echo(f"  \u2026 Waiting for endpoint '{endpoint_name}' to be ready...")
    try:
        ws = serving_workspace_client()
        wait_for_endpoint(
            ws, endpoint_name, progress=lambda message: click.echo(f"  \u2026 {message}")
        )
    except ServingEndpointError as exc:
        click.echo(f"  \u2717 {exc}", err=True)
        raise SystemExit(1) from exc
    except NotFound as exc:
        click.echo(
            f"  \u2717 Endpoint '{endpoint_name}' does not exist ({exc}): `haute deploy` "
            "creates it.",
            err=True,
        )
        raise SystemExit(1) from exc
    except Exception as exc:
        raise click.ClickException(
            f"Could not check Databricks endpoint '{endpoint_name}': {refusal_reason(exc)}"
        ) from exc
    click.echo("  \u2713 Endpoint ready")

    def price(record: dict[str, Any]) -> list[Any]:
        response = ws.serving_endpoints.query(name=endpoint_name, dataframe_records=[record])
        predictions = response.predictions
        if predictions is None:
            raise ValueError("Endpoint returned no predictions")
        return predictions if isinstance(predictions, list) else [predictions]

    return _smoke_files(json_files, price)


def _smoke_http(endpoint_url: str, json_files: list[Path]) -> bool:
    """Run smoke tests against an HTTP endpoint (container target)."""
    import json
    import urllib.request

    from haute.deploy._impact import score_http_endpoint_batched

    health_url = endpoint_url.rstrip("/") + "/health"

    # Check health first
    click.echo(f"  \u2026 Checking health: {health_url}")
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        click.echo(f"  \u2713 Health: {health.get('status', 'ok')}")
    except Exception as exc:
        click.echo(f"  \u2717 Health check failed: {exc}", err=True)
        return False

    def price(record: dict[str, Any]) -> list[Any]:
        return score_http_endpoint_batched(endpoint_url, [record], batch_size=1)

    return _smoke_files(json_files, price)


def _smoke_files(json_files: list[Path], price: Callable[[dict[str, Any]], list[Any]]) -> bool:
    """Price every file's test quotes, each a request of its own, and check their expected
    outputs as deploy does; report each file, and whether every one passed."""
    from haute.deploy._validators import price_test_quotes_live

    all_ok = True
    for jf in json_files:
        try:
            # Smoke runs in the project, as handle_smoke reads its haute.toml: the workbench's
            # test quotes are rebuilt against its form as deploy rebuilds them.
            result = price_test_quotes_live(jf, price, project_root=Path.cwd())
        except (OSError, ValueError) as exc:
            click.echo(f"  \u2717 {jf.name}: {exc}", err=True)
            all_ok = False
            continue
        for error in result.errors:
            click.echo(f"  \u2717 {jf.name}: {error}", err=True)
        if result.errors:
            all_ok = False
            continue
        click.echo(
            f"  \u2713 {jf.name}: {result.priced} quote(s) priced, {result.checked} checked "
            "against expected outputs"
        )
    return all_ok
