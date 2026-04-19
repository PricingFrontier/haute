"""Shared CLI utilities."""

from __future__ import annotations

import shutil

# ``subprocess`` is intentionally imported at module scope so tests enforcing
# the cascade-free contract (codebase-review #79) can patch it and assert
# ``subprocess.call`` / ``subprocess.Popen`` are never invoked. The runtime
# code does not use subprocess anywhere in this module —
# :func:`_open_browser` delegates to :mod:`webbrowser` exclusively.
import subprocess  # noqa: F401
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from haute.deploy._config import DeployConfig

from haute._logging import get_logger

logger = get_logger(component="cli")


# ---------------------------------------------------------------------------
# Pipeline file resolution — single source of truth for all CLI commands
# ---------------------------------------------------------------------------


def resolve_pipeline_file(explicit_path: str | None = None) -> Path:
    """Resolve the pipeline file to use.

    Priority:

    1. Explicit path from CLI argument
    2. ``[project].pipeline`` from ``haute.toml``
    3. Auto-discovery via :func:`~haute.discovery.discover_pipelines`
    4. Default to ``main.py``

    Raises :class:`SystemExit` if the resolved file doesn't exist.
    """
    if explicit_path:
        p = Path(explicit_path)
    else:
        # Try haute.toml first
        toml_path = Path.cwd() / "haute.toml"
        if toml_path.exists():
            import tomllib

            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            configured = data.get("project", {}).get("pipeline")
            if configured:
                p = Path(configured)
            else:
                p = _discover_or_default()
        else:
            p = _discover_or_default()

    if not p.exists():
        click.echo(f"Error: Pipeline file not found: {p}", err=True)
        raise SystemExit(1)
    return p


def _discover_or_default() -> Path:
    """Try :func:`~haute.discovery.discover_pipelines`, fall back to ``main.py``."""
    from haute.discovery import discover_pipelines

    found = discover_pipelines()
    return found[0] if found else Path("main.py")


def _open_browser(url: str) -> None:
    """Open *url* in the default browser.

    Delegates to :func:`webbrowser.open` which already handles platform
    detection internally.  When the browser cannot be launched the URL is
    printed to stdout so the user can paste it themselves — that is the
    only sensible fallback.
    """
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        click.echo(
            f"Could not open browser ({exc}). Open this URL manually: {url}",
        )
        return
    if not opened:
        click.echo(f"Could not open browser. Open this URL manually: {url}")


def _node_env() -> dict[str, str] | None:
    """Return ``None`` when Node is on PATH, or raise if it's missing.

    Node is required for the dev-mode frontend.  When :func:`shutil.which`
    finds ``node`` nothing extra is needed and ``None`` is returned (the
    caller then inherits the ambient environment).  When Node is absent
    this function fails loudly with a clear install hint rather than
    silently injecting the default MSI path — that silent fallback only
    works on a specific machine layout and hides the real problem (Node
    isn't installed) from the user.
    """
    if shutil.which("node"):
        return None
    msg = (
        "Node.js is required but was not found on PATH. "
        "Install Node.js from https://nodejs.org and restart your terminal."
    )
    raise click.ClickException(msg)


def _npm() -> str:
    """Return the npm executable from PATH, or fail loud if it's missing.

    Same contract as :func:`_node_env`: no hardcoded Windows install path
    fallback.  If :func:`shutil.which` can't find ``npm`` the user gets a
    clear install hint rather than a silent guess at a specific machine
    layout that hides the real problem (npm isn't on PATH).
    """
    found = shutil.which("npm")
    if found:
        return found
    msg = (
        "npm not found on PATH. Install Node.js from https://nodejs.org and restart your terminal."
    )
    raise click.ClickException(msg)


def _find_frontend_dir() -> Path:
    """Walk up from cwd looking for a ``frontend/`` dir with ``package.json``.

    Raises :class:`FileNotFoundError` when no such directory exists anywhere
    in the ancestor chain.  Callers are expected to catch the exception
    when a missing frontend is acceptable (e.g. production mode serves
    built static files).  Making the absence explicit removes the silent
    ``None`` that previously forced every call-site to implement its own
    "dev-vs-prod" check.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "frontend"
        if (candidate / "package.json").exists():
            return candidate
    raise FileNotFoundError(
        f"No frontend/ directory with package.json found in {cwd} or any parent."
    )


# ---------------------------------------------------------------------------
# Transport dispatch helper — shared by ``smoke`` and ``impact`` commands
# ---------------------------------------------------------------------------


class TransportInfo:
    """Result of :func:`resolve_transport` — describes the transport layer."""

    __slots__ = ("kind", "staging_url", "prod_url")

    def __init__(
        self,
        kind: str,
        staging_url: str = "",
        prod_url: str = "",
    ) -> None:
        self.kind = kind
        self.staging_url = staging_url
        self.prod_url = prod_url


def resolve_transport(config: DeployConfig) -> TransportInfo:
    """Determine the transport layer from *config.target*.

    Returns a :class:`TransportInfo` with ``kind`` set to one of:

    - ``"databricks"`` — Databricks Model Serving
    - ``"http"`` — container-based HTTP endpoint
    - ``"unsupported"`` — target has no transport implementation yet

    For ``"http"`` targets the ``staging_url`` (and ``prod_url`` when
    available) are populated from ``config.ci``.  Raises ``SystemExit``
    if the staging URL is required but missing.
    """
    from haute.deploy._container import _CONTAINER_BASED_TARGETS

    if config.target == "databricks":
        return TransportInfo(kind="databricks")

    if config.target in _CONTAINER_BASED_TARGETS:
        staging_url = config.ci.staging_endpoint_url
        if not staging_url:
            click.echo(
                "Error: No staging endpoint URL configured.\n"
                "  Set [ci.staging] endpoint_url in haute.toml.",
                err=True,
            )
            raise SystemExit(1)
        prod_url = config.ci.production_endpoint_url
        return TransportInfo(kind="http", staging_url=staging_url, prod_url=prod_url)

    return TransportInfo(kind="unsupported")


def _load_deploy_config(
    *,
    pipeline_file: str | None = None,
    model_name: str | None = None,
    require_toml: bool = False,
) -> DeployConfig:
    """Load a :class:`DeployConfig` from ``haute.toml`` or CLI arguments.

    Centralises the repeated pattern of:

    1. Check if ``haute.toml`` exists in the current working directory.
    2. If it does, load a :class:`DeployConfig` from it.
    3. Otherwise, fall back to constructing one from CLI arguments
       (only when *require_toml* is ``False`` and *pipeline_file* is given).

    Parameters
    ----------
    pipeline_file:
        Path to the pipeline file (CLI argument fallback).
    model_name:
        Model name (CLI argument fallback).
    require_toml:
        If ``True``, exit with an error when ``haute.toml`` is missing
        instead of falling back to CLI arguments.

    Returns
    -------
    DeployConfig
        Loaded (or constructed) deploy configuration.

    Raises
    ------
    SystemExit
        When no config source is available.
    """
    from haute.deploy._config import DeployConfig

    toml_path = Path.cwd() / "haute.toml"

    if toml_path.exists():
        config = DeployConfig.from_toml(toml_path)
        click.echo("  \u2713 Loaded config from haute.toml")
        return config

    if require_toml:
        click.echo("Error: No haute.toml found.", err=True)
        raise SystemExit(1)

    # No haute.toml — resolve pipeline file using the shared strategy
    resolved = resolve_pipeline_file(pipeline_file)
    return DeployConfig(
        pipeline_file=resolved,
        model_name=model_name or resolved.stem,
    )
