"""Configuration and readiness checks for the pricing assistant.

The assistant is deliberately opt-in.  Project settings come from the optional
``[assistant]`` table in ``haute.toml``; credentials and the output-token
budget come from the process environment.  This module only inspects that
state.  Provider SDKs are probed without importing them so importing Haute
does not acquire an optional dependency or trigger provider-side behaviour.
"""

from __future__ import annotations

import importlib.util
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from haute import _git
from haute.errors import ConfigError, HauteError
from haute.schemas import GitWorkingBranchResponse

AssistantProvider = Literal["anthropic", "openai"]

_DEFAULT_MAX_OUTPUT_TOKENS = 8192
_MAX_OUTPUT_TOKENS_ENV = "HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS"
_PROVIDER_SDKS: dict[AssistantProvider, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
}
_PROVIDER_KEYS: dict[AssistantProvider, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@dataclass(frozen=True, slots=True)
class AssistantConfig:
    """A fully validated assistant provider configuration."""

    provider: AssistantProvider
    model: str
    base_url: str | None
    api_key: str = field(repr=False)
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class AssistantReadiness:
    """Status exposed to the assistant route and frontend."""

    configured: bool
    reason: str | None
    provider: str | None
    model: str | None
    mutations_enabled: bool
    mutations_reason: str | None


def _mutation_readiness(
    status: GitWorkingBranchResponse,
) -> tuple[bool, str | None]:
    """Map the git working-branch state to the assistant mutation gate."""

    if status.state == "ready":
        return True, None
    if status.state == "unset":
        return (
            False,
            "Create or select a working branch in the Git panel before using assistant mutations.",
        )
    if status.state == "divergent":
        return (
            False,
            "Resolve the working-branch divergence in the Git panel before using assistant "
            "mutations.",
        )
    if status.state == "invalid":
        return False, "; ".join(status.errors)
    raise ValueError(f"Unknown working-branch state: {status.state!r}")


def mutations_readiness(
    project_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Return the mutation gate for *project_root* using the shared git status.

    ``working_branch_status`` is not total: a project that is not a git
    repository (or any other git-domain failure) raises a typed
    ``HauteError`` rather than returning a state.  For readiness that is a
    disabled-with-reason outcome, not an HTTP failure — the status endpoint
    must always render, and the git error text ("Not a git repository. Run
    'git init' first.") is exactly the analyst-facing reason.
    """

    root = _normalise_project_root(project_root)
    try:
        status = _git.working_branch_status(root)
    except HauteError as exc:
        return False, str(exc)
    return _mutation_readiness(status)


def _normalise_project_root(project_root: Path | None) -> Path:
    """Return the resolved directory containing the project's ``haute.toml``."""

    root = Path.cwd() if project_root is None else Path(project_root)
    if root.is_file():
        root = root.parent
    return root.resolve()


def _read_assistant_table(project_root: Path) -> dict[str, object] | None:
    """Read the optional ``[assistant]`` table from the project TOML."""

    toml_path = project_root / "haute.toml"
    if not toml_path.exists():
        return None

    try:
        with toml_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            "haute.toml is malformed and could not be parsed",
            path=str(toml_path),
            error=str(exc),
        ) from exc
    except OSError as exc:
        raise ConfigError(
            "haute.toml could not be read",
            path=str(toml_path),
            error=str(exc),
        ) from exc

    raw_table = data.get("assistant")
    if raw_table is None:
        return None
    if not isinstance(raw_table, dict):
        raise ConfigError("[assistant] must be a TOML table", path=str(toml_path))
    return cast(dict[str, object], raw_table)


def _max_output_tokens() -> tuple[int | None, str | None]:
    """Parse the strict per-provider output budget."""

    raw = os.getenv(_MAX_OUTPUT_TOKENS_ENV)
    if raw is None:
        return _DEFAULT_MAX_OUTPUT_TOKENS, None
    if not raw.isascii() or not raw.isdigit():
        return (
            None,
            f"{_MAX_OUTPUT_TOKENS_ENV} must be a positive integer; got {raw!r}.",
        )
    value = int(raw, 10)
    if value <= 0:
        return (
            None,
            f"{_MAX_OUTPUT_TOKENS_ENV} must be a positive integer; got {raw!r}.",
        )
    return value, None


def _sdk_importable(provider: AssistantProvider) -> bool:
    """Probe the configured provider SDK without importing it at server start."""

    module_name = _PROVIDER_SDKS[provider]
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _resolve_config(
    table: dict[str, object],
) -> AssistantConfig | tuple[str, str | None, str | None]:
    """Resolve provider settings, returning one readiness reason on failure."""

    raw_provider = table.get("provider")
    provider_echo = raw_provider if isinstance(raw_provider, str) else None
    raw_model = table.get("model")
    model_echo = raw_model if isinstance(raw_model, str) else None
    if not isinstance(raw_provider, str) or raw_provider not in _PROVIDER_SDKS:
        return f"Unknown assistant provider: {raw_provider!r}.", provider_echo, model_echo
    provider = cast(AssistantProvider, raw_provider)

    if not isinstance(raw_model, str) or not raw_model.strip():
        return "Missing assistant model.", provider, None
    model = raw_model

    raw_base_url = table.get("base_url")
    if provider == "anthropic" and raw_base_url is not None:
        return "base_url is only supported for the openai assistant provider.", provider, model
    if raw_base_url is not None and not isinstance(raw_base_url, str):
        raise ConfigError("[assistant].base_url must be a string", provider=provider)
    base_url: str | None = raw_base_url if isinstance(raw_base_url, str) else None

    if not _sdk_importable(provider):
        return (
            f"The {provider} SDK is missing from this installation; it ships with haute, "
            "so reinstall haute to repair the environment.",
            provider,
            model,
        )

    max_output_tokens, token_reason = _max_output_tokens()
    if token_reason is not None:
        return token_reason, provider, model
    assert max_output_tokens is not None

    key_name = _PROVIDER_KEYS[provider]
    api_key = os.getenv(key_name)
    if not api_key:
        return f"Missing API key environment variable: {key_name}.", provider, model

    return AssistantConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_output_tokens=max_output_tokens,
    )


def assistant_readiness(
    project_root: Path | None = None,
) -> AssistantReadiness:
    """Return the complete status for the assistant in *project_root*.

    The result is always safe to expose through the status endpoint: it never
    contains the API key.  A valid provider configuration sets ``configured``
    to ``True``; the independent mutation gate reports whether the shared Git
    working-branch state permits edits.
    """

    root = _normalise_project_root(project_root)
    table = _read_assistant_table(root)
    provider: str | None
    model: str | None

    # Read and validate TOML before asking Git for its status.  A malformed
    # project configuration must consistently surface as ConfigError, even if
    # the caller is outside a Git working tree.
    if table is None:
        reason = "No [assistant] table is configured in haute.toml."
        provider = model = None
        configured = False
    else:
        resolved = _resolve_config(table)
        if isinstance(resolved, AssistantConfig):
            reason = None
            provider = resolved.provider
            model = resolved.model
            configured = True
        else:
            reason, provider, model = resolved
            configured = False

    mutations_enabled, mutations_reason = mutations_readiness(root)
    return AssistantReadiness(
        configured=configured,
        reason=reason,
        provider=provider,
        model=model,
        mutations_enabled=mutations_enabled,
        mutations_reason=mutations_reason,
    )


def resolve_assistant_config(project_root: Path | None = None) -> AssistantConfig:
    """Return a fully validated provider config, or a typed config error.

    The public readiness function intentionally does not expose credentials.
    Provider adapters use this resolver only after the status gate has passed.
    """

    root = _normalise_project_root(project_root)
    table = _read_assistant_table(root)
    if table is None:
        raise ConfigError("No [assistant] table is configured in haute.toml.")

    resolved = _resolve_config(table)
    if isinstance(resolved, AssistantConfig):
        return resolved
    reason, provider, model = resolved
    raise ConfigError(reason, provider=provider, model=model)


__all__ = [
    "AssistantConfig",
    "AssistantProvider",
    "AssistantReadiness",
    "assistant_readiness",
    "mutations_readiness",
    "resolve_assistant_config",
]
