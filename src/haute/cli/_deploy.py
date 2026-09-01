"""``haute deploy`` command.

Split into three pieces:

* :class:`DeployCliConfig` — the typed bag of CLI-supplied values.
* :func:`handle_deploy` — the pure function that does the work (loads the
  :class:`~haute.deploy._config.DeployConfig`, validates, and deploys).
* :func:`deploy` — the thin ``@click.command`` entry point.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from haute._project import resolve_pipeline_file
from haute.cli._helpers import ENDPOINT_SUFFIX_HELP
from haute.errors import DeployError

# Map of provider-name → env var set by that provider when a job is running.
# Each provider is recognised explicitly so detection is obvious to anyone
# reading the code, and so that individual providers can be enabled/disabled
# without affecting others. The generic ``CI`` variable is checked last as a
# fallback for unknown providers.
_CI_PROVIDER_ENV_VARS: tuple[str, ...] = (
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "CIRCLECI",
    "TF_BUILD",  # Azure DevOps
    "BUILDKITE",
    "CI",
)


def _detect_ci_env(env: Mapping[str, str]) -> bool:
    """Return ``True`` iff *env* contains a recognised CI marker variable.

    A variable counts as set when its value is non-empty and not obviously
    falsy (``"0"``, ``"false"``). This matches the convention used by the
    major providers (GitHub Actions, GitLab, CircleCI, Azure DevOps,
    Buildkite), each of which sets its marker to a truthy value while a job
    is running.
    """
    falsy = {"", "0", "false", "False", "FALSE", "no", "No", "NO"}
    for var in _CI_PROVIDER_ENV_VARS:
        value = env.get(var)
        if value is not None and value not in falsy:
            return True
    return False


@dataclass
class DeployCliConfig:
    """Parsed CLI inputs for the ``haute deploy`` command.

    Distinct from :class:`haute.deploy._config.DeployConfig` — this is the
    raw shape of command-line arguments; the handle function turns it into
    a fully populated :class:`DeployConfig` via ``from_toml`` or
    ``from_cli_args``.
    """

    pipeline_file: str | None
    model_name: str | None
    endpoint_suffix: str | None
    dry_run: bool


def handle_deploy(config: DeployCliConfig) -> None:
    """Execute ``haute deploy`` with parsed CLI inputs.

    Loads a :class:`haute.deploy._config.DeployConfig` from ``haute.toml``
    when present (preferred — gives access to every deploy option) or
    falls back to :meth:`DeployConfig.from_cli_args` when running
    standalone.  Blocks non-dry-run deploys outside of CI, resolves the
    pipeline, validates, scores test quotes, and finally dispatches to
    the target-specific deploy backend.
    """
    import os

    from haute.deploy._config import DeployConfig, resolve_config
    from haute.deploy._validators import validate_deploy

    # 1. Load config — prefer haute.toml when present, else build from CLI.
    toml_path = Path.cwd() / "haute.toml"
    if toml_path.exists():
        deploy_config = DeployConfig.from_toml(toml_path)
        click.echo("  \u2713 Loaded config from haute.toml")
    else:
        try:
            resolved = resolve_pipeline_file(
                Path(config.pipeline_file) if config.pipeline_file else None,
            )
            deploy_config = DeployConfig.from_cli_args(
                pipeline_file=resolved,
                model_name=config.model_name or resolved.stem,
            )
        except (FileNotFoundError, ValueError) as exc:
            click.echo(f"Error: {exc}", err=True)
            click.echo(
                "  Run inside a Haute project (cd to one, or run 'haute init'), "
                "or pass --model-name and a pipeline file explicitly.",
                err=True,
            )
            raise SystemExit(1) from exc

    # Block local deploys - production changes must go through CI/CD.
    is_ci = _detect_ci_env(os.environ)
    if not config.dry_run and not is_ci:
        click.echo("Error: Deploys must go through CI/CD.", err=True)
        click.echo("  Use --dry-run to validate locally without deploying.", err=True)
        raise SystemExit(1)

    # Apply CLI overrides on top of the loaded config.
    overrides: dict[str, str | Path | None] = {}
    if config.pipeline_file:
        overrides["pipeline_file"] = Path(config.pipeline_file)
    if config.model_name:
        overrides["model_name"] = config.model_name
    if config.endpoint_suffix:
        overrides["endpoint_suffix"] = config.endpoint_suffix
    deploy_config = deploy_config.override(**overrides)
    pipeline_candidate = (
        Path(config.pipeline_file)
        if config.pipeline_file
        else deploy_config.project_dir or Path.cwd()
    )
    deploy_config.pipeline_file = resolve_pipeline_file(pipeline_candidate)

    click.echo(f"\nDeploying pipeline: {deploy_config.model_name}")
    click.echo(f"  Pipeline: {deploy_config.pipeline_file}")
    click.echo(f"  Endpoint: {deploy_config.effective_endpoint_name}")

    # 2. Resolve (parse, prune, detect I/O, collect artifacts, infer schemas)
    try:
        resolved_deploy = resolve_config(deploy_config)
    except Exception as e:
        click.echo(f"  \u2717 Resolution failed: {e}", err=True)
        raise SystemExit(1)

    n_kept = len(resolved_deploy.pruned_graph.nodes)
    n_removed = len(resolved_deploy.removed_node_ids)
    click.echo(
        f"  \u2713 Parsed pipeline ({n_kept + n_removed} nodes, "
        f"{len(resolved_deploy.pruned_graph.edges)} edges)"
    )
    click.echo(f"  \u2713 Pruned to output ancestors ({n_kept} nodes)")
    if n_removed:
        click.echo(
            f"  \u2713 Skipped {n_removed} nodes not in scoring path "
            f"({', '.join(resolved_deploy.removed_node_ids)})"
        )
    click.echo(f"  \u2713 Collected {len(resolved_deploy.artifacts)} artifacts")
    click.echo(f"  \u2713 Input node(s): {', '.join(resolved_deploy.input_node_ids)}")
    click.echo(f"  \u2713 Output node: {resolved_deploy.output_node_id}")
    click.echo(f"  \u2713 Inferred input schema ({len(resolved_deploy.input_schema)} columns)")
    click.echo(f"  \u2713 Inferred output schema ({len(resolved_deploy.output_schema)} columns)")

    # 3. Validate
    try:
        tq_results = validate_deploy(resolved_deploy)
    except DeployError as exc:
        click.echo("\n  \u2717 Validation failed:", err=True)
        context = getattr(exc, "context", {})
        structural_errors = context.get("structural_errors") if isinstance(context, dict) else None
        test_quote_errors = context.get("test_quote_errors") if isinstance(context, dict) else None
        details = [*(structural_errors or []), *(test_quote_errors or [])]
        if details:
            for err in details:
                click.echo(f"    - {err}", err=True)
        else:
            click.echo(f"    - {exc}", err=True)
        resolved_deploy.close()
        raise SystemExit(1)
    except BaseException:
        resolved_deploy.close()
        raise
    click.echo("  \u2713 Validation passed")

    # 4. Render the quote results produced by the validation gate.
    if tq_results:
        all_ok = True
        for r in tq_results:
            status_icon = "\u2713" if r["status"] == "ok" else "\u2717"
            click.echo(
                f"  {status_icon} Test quotes: {r['file']:<30s} "
                f"{r['rows']:>3} rows  {r['status']}  ({r['time_ms']}ms)"
            )
            if r["status"] != "ok":
                click.echo(f"      Error: {r['error']}", err=True)
                all_ok = False
        if not all_ok:
            click.echo(
                "\n  \u2717 Test quote scoring failed. Fix errors before deploying.",
                err=True,
            )
            resolved_deploy.close()
            raise SystemExit(1)

    if config.dry_run:
        click.echo("\n  Dry run complete - no model was deployed.")
        resolved_deploy.close()
        return

    # 5. Deploy to target
    try:
        from haute.deploy import deploy_resolved

        result = deploy_resolved(resolved_deploy)
        click.echo(f"  \u2713 Deployed: {result.model_name} v{result.model_version}")
        if result.endpoint_url:
            click.echo(f"\nEndpoint ready:\n  POST {result.endpoint_url}")
        elif result.model_uri:
            click.echo("\nDeploy complete. Serve locally with:")
            click.echo(f'  mlflow models serve -m "{result.model_uri}" -p 5001')
    except ImportError as e:
        click.echo(f"\n  \u2717 Missing dependency: {e}", err=True)
        click.echo(
            "  Install the right extras for your target, e.g.: uv add 'haute[databricks]'",
            err=True,
        )
        raise SystemExit(1)
    except NotImplementedError as e:
        click.echo(f"\n  \u2717 {e}", err=True)
        raise SystemExit(1)
    except DeployError as e:
        click.echo(f"\n  \u2717 Deployment failed: {e}", err=True)
        raise SystemExit(1)
    finally:
        resolved_deploy.close()


@click.command()
@click.argument("pipeline_file", required=False)
@click.option("--model-name", default=None, help="Override model name from haute.toml.")
@click.option("--dry-run", is_flag=True, help="Validate and score test quotes without deploying.")
@click.option(
    "--endpoint-suffix",
    default=None,
    help=ENDPOINT_SUFFIX_HELP,
)
def deploy(
    pipeline_file: str | None,
    model_name: str | None,
    endpoint_suffix: str | None,
    dry_run: bool,
) -> None:
    """Deploy a pipeline as a live scoring API.

    Reads config from haute.toml + credentials from .env.
    Pipeline file, model name, and target are all optional -
    defaults come from [project] and [deploy] in haute.toml.
    """
    config = DeployCliConfig(
        pipeline_file=pipeline_file,
        model_name=model_name,
        endpoint_suffix=endpoint_suffix,
        dry_run=dry_run,
    )
    handle_deploy(config)
