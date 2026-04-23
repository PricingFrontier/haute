"""``haute smoke`` command.

Split into:

* :class:`SmokeConfig` — the typed bag of CLI inputs.
* :func:`handle_smoke` — the pure function that does the work.
* :func:`smoke` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

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
    click.echo("  \u2713 Loaded config from haute.toml")
    if config.endpoint_suffix:
        deploy_config = deploy_config.override(endpoint_suffix=config.endpoint_suffix)

    endpoint_name = deploy_config.effective_endpoint_name
    tq_dir = deploy_config.test_quotes_dir

    if tq_dir is None or not tq_dir.is_dir():
        click.echo(f"Error: No test quotes directory found (resolved: {tq_dir}).", err=True)
        raise SystemExit(1)

    json_files = sorted(tq_dir.glob("*.json"))
    if not json_files:
        click.echo("Error: No .json files in tests/quotes/.", err=True)
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
            f"  \u26a0 Smoke test not yet implemented for target '{deploy_config.target}'.",
            err=True,
        )
        return

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
    from haute.deploy._validators import load_test_quote_file

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        click.echo(
            "Error: databricks-sdk not installed. Install with: uv add haute[databricks]",
            err=True,
        )
        raise SystemExit(1)

    from haute.deploy._config import _load_env

    _load_env(Path.cwd())

    ws = WorkspaceClient()

    # Wait for endpoint to be ready (provisioning can take 10-15 min)
    max_wait = 30 * 60  # 30 minutes
    poll_interval = 30  # seconds
    click.echo(f"  \u2026 Waiting for endpoint '{endpoint_name}' to be ready...")
    waited = 0
    while waited < max_wait:
        try:
            ep = ws.serving_endpoints.get(endpoint_name)
            state = ep.state
            ready = getattr(state, "ready", None) if state else None
            config_update = getattr(state, "config_update", None) if state else None
            ready_str = str(ready).rsplit(".", 1)[-1] if ready else ""
            update_str = str(config_update).rsplit(".", 1)[-1] if config_update else ""
            if ready_str == "READY" and update_str in ("", "NOT_UPDATING"):
                click.echo(f"  \u2713 Endpoint ready (waited {waited}s)")
                break
            status_msg = f"ready={ready}"
            if config_update and config_update != "NOT_UPDATING":
                status_msg += f", config_update={config_update}"
            click.echo(
                f"  \u2026 Endpoint not ready ({status_msg}), polling in {poll_interval}s..."
            )
        except Exception as exc:
            click.echo(
                f"  \u2026 Endpoint not found or error ({exc}), retrying in {poll_interval}s..."
            )
        time.sleep(poll_interval)
        waited += poll_interval
    else:
        click.echo(
            f"  \u2717 Endpoint '{endpoint_name}' not ready after {max_wait // 60} minutes.",
            err=True,
        )
        raise SystemExit(1)

    all_ok = True

    for jf in json_files:
        try:
            cleaned = load_test_quote_file(jf)

            response = ws.serving_endpoints.query(
                name=endpoint_name,
                dataframe_records=cleaned,
            )

            predictions = response.predictions
            if predictions is None:
                raise ValueError("Endpoint returned no predictions")

            n_rows = len(predictions) if isinstance(predictions, list) else 1
            click.echo(f"  \u2713 {jf.name}: {n_rows} predictions returned")
        except Exception as exc:
            click.echo(f"  \u2717 {jf.name}: {exc}", err=True)
            all_ok = False

    return all_ok


def _smoke_http(endpoint_url: str, json_files: list[Path]) -> bool:
    """Run smoke tests against an HTTP endpoint (container target)."""
    import json
    import urllib.request

    from haute.deploy._impact import score_http_endpoint_batched
    from haute.deploy._validators import load_test_quote_file

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

    all_ok = True

    for jf in json_files:
        try:
            cleaned = load_test_quote_file(jf)
            preds = score_http_endpoint_batched(endpoint_url, cleaned, batch_size=len(cleaned))
            n_rows = len(preds) if isinstance(preds, list) else 1
            click.echo(f"  \u2713 {jf.name}: {n_rows} predictions returned")
        except Exception as exc:
            click.echo(f"  \u2717 {jf.name}: {exc}", err=True)
            all_ok = False

    return all_ok
