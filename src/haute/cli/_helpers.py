"""Shared CLI utilities."""

from __future__ import annotations

import os
import shutil
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from haute.deploy._config import DeployConfig

from haute._logging import get_logger
from haute.errors import HauteError

logger = get_logger(component="cli")

#: The failures a command reports as one line and exit 1: Haute's own errors (a refused
#: configuration, a pipeline that does not parse, a deploy that cannot go ahead), a value
#: that cannot be used and a file that cannot be read. Anything else is a defect and
#: reaches the terminal with its traceback.
EXPECTED_FAILURES: tuple[type[Exception], ...] = (HauteError, ValueError, OSError)


# ---------------------------------------------------------------------------
# Shared Click option help strings
# ---------------------------------------------------------------------------
#
# A single source of truth for option help that appears on more than one
# command — without this the wording drifts over time and tooling that
# parses ``--help`` output has to special-case each command.
#
# Any command that accepts ``--endpoint-suffix`` MUST use this constant.

ENDPOINT_SUFFIX_HELP = 'Suffix appended to endpoint name (e.g. "-staging").'


# ---------------------------------------------------------------------------
# Model name resolution — CLI > HAUTE_MODEL_NAME > haute.toml > error
# ---------------------------------------------------------------------------


def resolve_model_name(cli_arg: str | None, toml_path: Path | None) -> str:
    """Resolve the model name for a CLI command.

    Encodes the precedence rule used across every CLI command that accepts
    a model name:

        CLI value  >  HAUTE_MODEL_NAME  >  haute.toml [deploy].model_name  >  error

    Parameters
    ----------
    cli_arg:
        The value supplied on the command line (``--model-name`` flag or
        positional argument).  ``None`` or an empty string means no CLI value
        was given, and a non-empty ``HAUTE_MODEL_NAME`` environment variable
        is used next.
    toml_path:
        Path to a ``haute.toml`` file to read ``[deploy].model_name`` from.
        ``None`` means no project config is available.

    Returns
    -------
    str
        The resolved model name.

    Raises
    ------
    ValueError
        When neither *cli_arg* nor ``HAUTE_MODEL_NAME`` gives a name and no usable
        TOML source is available
        — either *toml_path* is ``None``, or the TOML file lacks a
        ``[deploy].model_name`` entry.  The message always names both
        user-facing fixes (``--model-name`` flag or
        ``[deploy].model_name`` in ``haute.toml``).
    FileNotFoundError
        When *toml_path* is given but does not exist — a missing config
        file is a programmer bug (the caller passed a wrong path), not a
        fallback to auto-discovery.
    """
    if cli_arg is not None and cli_arg != "":
        return cli_arg

    environment_name = os.environ.get("HAUTE_MODEL_NAME")
    if environment_name:
        return environment_name

    if toml_path is None:
        raise ValueError(
            "model_name is required — pass --model-name on the command line "
            "or run inside a Haute project with [deploy].model_name set in "
            "haute.toml."
        )

    if not toml_path.exists():
        raise FileNotFoundError(
            f"haute.toml not found at {toml_path}. "
            "Pass --model-name explicitly or cd to a Haute project."
        )

    import tomllib

    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    deploy_section = data.get("deploy")
    if not isinstance(deploy_section, dict):
        raise ValueError(
            f"haute.toml at {toml_path} has no [deploy] section. "
            "Add a [deploy] block with model_name, or pass --model-name."
        )

    model_name = deploy_section.get("model_name")
    if not model_name:
        raise ValueError(
            f"haute.toml at {toml_path} has no [deploy].model_name. "
            "Add model_name under [deploy], or pass --model-name."
        )

    return str(model_name)


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
    available) are populated from ``config.ci``.  Raises
    :class:`click.ClickException` if the staging URL is required but missing.
    """
    from haute.deploy._config import _CONTAINER_BASED_TARGETS

    if config.target == "databricks":
        return TransportInfo(kind="databricks")

    if config.target in _CONTAINER_BASED_TARGETS:
        staging_url = config.ci.staging_endpoint_url
        if not staging_url:
            raise click.ClickException(
                "No staging endpoint URL configured. Set [ci.staging] endpoint_url in haute.toml."
            )
        prod_url = config.ci.production_endpoint_url
        return TransportInfo(kind="http", staging_url=staging_url, prod_url=prod_url)

    return TransportInfo(kind="unsupported")
