"""Configuration and readiness checks for the pricing assistant.

The assistant is deliberately opt-in.  Project settings come from the optional
``[assistant]`` table in ``haute.toml``; credentials and the output-token
budget come from the process environment.  This module only inspects that
state.  Provider SDKs are probed without importing them so importing Haute
does not acquire an optional dependency or trigger provider-side behaviour.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import tomllib
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from urllib.parse import SplitResult, urlsplit

from haute import _git
from haute.errors import ConfigError, HauteError
from haute.schemas import GitWorkingBranchResponse

AssistantProvider = Literal["anthropic", "openai", "databricks"]
ProviderTrust = Literal["local", "organization", "external"]
Sensitivity = Literal["public", "internal", "restricted"]

_DEFAULT_MAX_OUTPUT_TOKENS = 8192
_MAX_OUTPUT_TOKENS_ENV = "HAUTE_ASSISTANT_MAX_OUTPUT_TOKENS"
_PROVIDER_SDKS: dict[AssistantProvider, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "databricks": "openai",
}
_PROVIDER_KEYS: dict[AssistantProvider, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "databricks": "DATABRICKS_TOKEN",
}
_ASSISTANT_TABLE_KEYS = frozenset({"provider", "model", "base_url", "egress"})
_EGRESS_TABLE_KEYS = frozenset(
    {
        "trust",
        "max_sensitivity",
        "allow_project_knowledge",
        "allow_executable_source",
        "allow_row_samples",
    }
)
_TRUST_VALUES = frozenset({"local", "organization", "external"})
_SENSITIVITY_VALUES = frozenset({"public", "internal", "restricted"})


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Closed project policy governing provider-visible project material."""

    trust: ProviderTrust
    max_sensitivity: Sensitivity
    allow_project_knowledge: bool
    allow_executable_source: bool
    allow_row_samples: bool

    @property
    def policy_hash(self) -> str:
        payload = {
            "allow_executable_source": self.allow_executable_source,
            "allow_project_knowledge": self.allow_project_knowledge,
            "allow_row_samples": self.allow_row_samples,
            "max_sensitivity": self.max_sensitivity,
            "trust": self.trust,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AssistantConfig:
    """A fully validated assistant provider configuration."""

    provider: AssistantProvider
    model: str
    base_url: str | None
    api_key: str = field(repr=False)
    max_output_tokens: int
    egress: EgressPolicy
    endpoint_host: str


@dataclass(frozen=True, slots=True)
class AssistantReadiness:
    """Status exposed to the assistant route and frontend."""

    configured: bool
    reason: str | None
    provider: str | None
    model: str | None
    endpoint_host: str | None
    trust: ProviderTrust | None
    max_sensitivity: Sensitivity | None
    mutations_enabled: bool
    mutations_reason: str | None


def _mutation_readiness(
    status: GitWorkingBranchResponse,
) -> tuple[bool, str | None]:
    """Map the git working-branch state to the assistant mutation gate."""

    if status.state == "ready":
        return True, None
    if status.state == "no-repository":
        return False, "Initialise Git before using assistant mutations."
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
    if status.state == "detached":
        return (
            False,
            "Attach HEAD by selecting or creating a working branch in the Git panel before "
            "using assistant mutations.",
        )
    if status.state == "invalid":
        return False, "; ".join(status.errors)
    raise ValueError(f"Unknown working-branch state: {status.state!r}")


def mutations_readiness(
    project_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Return the mutation gate for *project_root* using the shared git status.

    ``working_branch_status`` is total for expected repository/readiness
    states. Any unexpected typed ``HauteError`` is still a
    disabled-with-reason outcome, not an HTTP failure, so the status endpoint
    always renders.
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


def _invalid_base_url() -> ConfigError:
    """Build the deliberately value-free OpenAI base URL validation error."""

    return ConfigError(
        "[assistant].base_url must be an absolute http or https URL with a hostname "
        "and no user information"
    )


def _validate_openai_base_url(raw_base_url: str) -> str:
    """Validate an OpenAI-compatible endpoint without exposing its value on failure."""

    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw_base_url
    ):
        raise _invalid_base_url()

    try:
        parsed: SplitResult = urlsplit(raw_base_url)
        port = parsed.port
    except ValueError as exc:
        raise _invalid_base_url() from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or "@" in parsed.netloc
        or "\\" in parsed.netloc
        or parsed.netloc.endswith(":")
        or (port is not None and not 0 <= port <= 65535)
    ):
        raise _invalid_base_url()
    return raw_base_url


def _invalid_databricks_host() -> ConfigError:
    """Build the deliberately value-free Databricks host validation error."""

    return ConfigError(
        "DATABRICKS_HOST must be an absolute HTTPS workspace-root URL with a hostname "
        "and no user information, query, fragment, or non-root path"
    )


def _databricks_host_from_environment() -> str | None:
    """Read the shared Databricks workspace host at configuration-resolution time."""

    return os.getenv("DATABRICKS_HOST")


def _databricks_base_url(raw_host: str) -> str:
    """Validate a workspace host and derive its OpenAI-compatible API root."""

    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw_host
    ):
        raise _invalid_databricks_host()

    try:
        parsed = urlsplit(raw_host)
        port = parsed.port
    except ValueError as exc:
        raise _invalid_databricks_host() from exc

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or "@" in parsed.netloc
        or "\\" in parsed.netloc
        or parsed.netloc.endswith(":")
        or (port is not None and not 0 <= port <= 65535)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise _invalid_databricks_host()

    workspace_root = raw_host[:-1] if parsed.path == "/" else raw_host
    return f"{workspace_root}/serving-endpoints"


def _endpoint(provider: AssistantProvider, base_url: str | None) -> SplitResult:
    url = (
        "https://api.anthropic.com"
        if provider == "anthropic"
        else base_url or "https://api.openai.com/v1"
    )
    return urlsplit(url)


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_egress(
    raw: object,
    *,
    endpoint: SplitResult,
) -> EgressPolicy:
    if not isinstance(raw, dict):
        raise ConfigError("[assistant].egress must be a TOML table")
    unknown = sorted(set(raw).difference(_EGRESS_TABLE_KEYS))
    if unknown:
        paths = ", ".join(f"[assistant].egress.{key}" for key in unknown)
        raise ConfigError(f"Unknown [assistant].egress configuration key(s): {paths}.")
    missing = sorted(_EGRESS_TABLE_KEYS.difference(raw))
    if missing:
        paths = ", ".join(f"[assistant].egress.{key}" for key in missing)
        raise ConfigError(f"Missing required assistant egress key(s): {paths}.")

    trust = raw["trust"]
    sensitivity = raw["max_sensitivity"]
    if not isinstance(trust, str) or trust not in _TRUST_VALUES:
        raise ConfigError("[assistant].egress.trust must be local, organization, or external")
    if not isinstance(sensitivity, str) or sensitivity not in _SENSITIVITY_VALUES:
        raise ConfigError(
            "[assistant].egress.max_sensitivity must be public, internal, or restricted"
        )
    for key in (
        "allow_project_knowledge",
        "allow_executable_source",
        "allow_row_samples",
    ):
        if not isinstance(raw[key], bool):
            raise ConfigError(f"[assistant].egress.{key} must be a boolean")

    host = endpoint.hostname
    assert host is not None
    if trust == "local" and not _is_loopback_host(host):
        raise ConfigError(
            "[assistant].egress.trust local requires a localhost or loopback endpoint"
        )
    if trust in {"organization", "external"} and endpoint.scheme != "https":
        raise ConfigError(f"[assistant].egress.trust {trust} requires an HTTPS endpoint")
    if trust == "external" and (
        sensitivity != "public" or raw["allow_executable_source"] or raw["allow_row_samples"]
    ):
        raise ConfigError(
            "[assistant].egress external is public-only and forbids executable source "
            "and row samples"
        )
    return EgressPolicy(
        trust=cast(ProviderTrust, trust),
        max_sensitivity=cast(Sensitivity, sensitivity),
        allow_project_knowledge=raw["allow_project_knowledge"],
        allow_executable_source=raw["allow_executable_source"],
        allow_row_samples=raw["allow_row_samples"],
    )


def _resolve_config(
    table: dict[str, object],
) -> AssistantConfig | tuple[str, str | None, str | None]:
    """Resolve provider settings, returning one readiness reason on failure."""

    unknown_keys = sorted(set(table).difference(_ASSISTANT_TABLE_KEYS))
    if unknown_keys:
        paths = ", ".join(f"[assistant].{key}" for key in unknown_keys)
        raise ConfigError(f"Unknown [assistant] configuration key(s): {paths}.")

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
    if provider in {"anthropic", "databricks"} and raw_base_url is not None:
        if provider == "databricks":
            reason = (
                "base_url is derived from DATABRICKS_HOST for the databricks assistant provider."
            )
        else:
            reason = "base_url is only supported for the openai assistant provider."
        return reason, provider, model
    if raw_base_url is not None and not isinstance(raw_base_url, str):
        raise ConfigError("[assistant].base_url must be a string", provider=provider)
    base_url: str | None
    if provider == "databricks":
        raw_host = _databricks_host_from_environment()
        if not raw_host:
            return (
                "Missing Databricks host environment variable: DATABRICKS_HOST.",
                provider,
                model,
            )
        base_url = _databricks_base_url(raw_host)
    else:
        base_url = (
            _validate_openai_base_url(raw_base_url) if isinstance(raw_base_url, str) else None
        )
    endpoint = _endpoint(provider, base_url)
    raw_egress = table.get("egress")
    if raw_egress is None:
        return (
            "Missing required [assistant].egress table; add the explicit provider trust "
            "and sensitivity policy.",
            provider,
            model,
        )
    egress = _validate_egress(raw_egress, endpoint=endpoint)

    if not _sdk_importable(provider):
        if provider == "databricks":
            sdk_reason = (
                "The openai SDK required by the databricks provider is missing from "
                "this installation"
            )
        else:
            sdk_reason = f"The {provider} SDK is missing from this installation"
        return (
            f"{sdk_reason}; it ships with haute, so reinstall haute to repair the environment.",
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
        egress=egress,
        endpoint_host=endpoint.hostname or "",
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
    endpoint_host: str | None = None
    trust: ProviderTrust | None = None
    max_sensitivity: Sensitivity | None = None

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
            endpoint_host = resolved.endpoint_host
            trust = resolved.egress.trust
            max_sensitivity = resolved.egress.max_sensitivity
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
        endpoint_host=endpoint_host,
        trust=trust,
        max_sensitivity=max_sensitivity,
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


def resolve_egress_policy(project_root: Path | None = None) -> EgressPolicy:
    """Resolve only the closed egress policy, without probing SDKs or credentials."""

    root = _normalise_project_root(project_root)
    table = _read_assistant_table(root)
    if table is None:
        raise ConfigError("No [assistant] table is configured in haute.toml.")
    unknown = sorted(set(table).difference(_ASSISTANT_TABLE_KEYS))
    if unknown:
        paths = ", ".join(f"[assistant].{key}" for key in unknown)
        raise ConfigError(f"Unknown [assistant] configuration key(s): {paths}.")
    raw_provider = table.get("provider")
    if not isinstance(raw_provider, str) or raw_provider not in _PROVIDER_SDKS:
        raise ConfigError(f"Unknown assistant provider: {raw_provider!r}.")
    provider = cast(AssistantProvider, raw_provider)
    raw_base_url = table.get("base_url")
    if provider in {"anthropic", "databricks"} and raw_base_url is not None:
        if provider == "databricks":
            raise ConfigError(
                "base_url is derived from DATABRICKS_HOST for the databricks assistant provider."
            )
        raise ConfigError("base_url is only supported for the openai assistant provider.")
    if raw_base_url is not None and not isinstance(raw_base_url, str):
        raise ConfigError("[assistant].base_url must be a string")
    base_url: str | None
    if provider == "databricks":
        raw_host = _databricks_host_from_environment()
        if not raw_host:
            raise ConfigError("Missing Databricks host environment variable: DATABRICKS_HOST.")
        base_url = _databricks_base_url(raw_host)
    else:
        base_url = (
            _validate_openai_base_url(raw_base_url) if isinstance(raw_base_url, str) else None
        )
    raw_egress = table.get("egress")
    if raw_egress is None:
        raise ConfigError("Missing required [assistant].egress table.")
    return _validate_egress(raw_egress, endpoint=_endpoint(provider, base_url))


__all__ = [
    "AssistantConfig",
    "EgressPolicy",
    "AssistantProvider",
    "AssistantReadiness",
    "ProviderTrust",
    "Sensitivity",
    "assistant_readiness",
    "mutations_readiness",
    "resolve_assistant_config",
    "resolve_egress_policy",
]
