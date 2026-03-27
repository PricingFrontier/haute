"""Shared CLI utilities."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    """Open *url* in the default browser, suppressing noisy stderr from gio."""
    try:
        if sys.platform == "linux":
            rc = subprocess.call(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if rc != 0:
                webbrowser.open(url)
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(url)
    except Exception:
        webbrowser.open(url)


def _node_env() -> dict[str, str] | None:
    """Return an env dict with Node.js on PATH, or *None* if already available."""
    if shutil.which("node"):
        return None  # already on PATH, no override needed
    if sys.platform == "win32":
        nodejs_dir = Path(r"C:\Program Files\nodejs")
        if (nodejs_dir / "node.exe").exists():
            env = os.environ.copy()
            env["PATH"] = f"{nodejs_dir};{env.get('PATH', '')}"
            return env
    return None


def _npm() -> str:
    """Return the npm executable, resolving common Windows install paths."""
    found = shutil.which("npm")
    if found:
        return found
    if sys.platform == "win32":
        candidate = Path(r"C:\Program Files\nodejs\npm.cmd")
        if candidate.exists():
            return str(candidate)
    msg = (
        "npm not found on PATH. Install Node.js from https://nodejs.org and restart your terminal."
    )
    raise click.ClickException(msg)


def _find_frontend_dir() -> Path | None:
    """Walk up from cwd looking for a frontend/ directory with package.json."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / "frontend"
        if (candidate / "package.json").exists():
            return candidate
    return None


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
