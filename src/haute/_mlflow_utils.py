"""Shared MLflow helpers used by _mlflow_io, _optimiser_io, and deploy/_bundler.

Eliminates duplication of:
  - ``resolve_version()``: resolve "latest" to a concrete version number
  - ``search_versions()``: safely quote model name and search
  - ``ResolvedBackend``: once-per-load resolution of a destination key (or auto)
    to a concrete tracking URI, registry URI, secret-free identity, and filesystem digest
  - ``backend_identity()``: secret-free identity derivation per backend
  - ``resolve_backend()``: destination resolution to a ``ResolvedBackend``
  - ``resolve_mlflow_source()``: import mlflow, create a destination-pinned client,
    resolve a source_type/run_id/registered_model to a concrete run_id, and return
    the ``ResolvedBackend`` alongside the client

Databricks credential binding:
  MLflow authenticates a bare ``databricks`` tracking URI from the general
  ``DATABRICKS_HOST``/``DATABRICKS_TOKEN`` pair by default, but haute keeps MLflow on
  its own ``DATABRICKS_MLFLOW_HOST``/``DATABRICKS_MLFLOW_TOKEN`` pair because Databricks
  token scopes may not let one token cover data access and MLflow.
  ``bind_mlflow_databricks_credentials()`` pins ``MLFLOW_ENABLE_DB_SDK=false`` (MLflow's
  SDK path resolves environment-first and caches its client), replaces MLflow's
  environment credential provider with one that reads the MLflow pair on every request,
  and routes run and logged-model artifacts through MLflow's REST repository so no
  Databricks SDK client is built from the general pair.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mlflow.entities.model_registry import ModelVersion
    from mlflow.tracking import MlflowClient

    from haute.modelling._mlflow_settings import TrackingConfig


_FLUENT_LOCK = threading.RLock()
_tracking_environment_snapshot: tuple[str | None] | None = None


MLFLOW_DATABRICKS_HOST_ENV = "DATABRICKS_MLFLOW_HOST"
MLFLOW_DATABRICKS_TOKEN_ENV = "DATABRICKS_MLFLOW_TOKEN"

_DATABRICKS_BINDING_LOCK = threading.Lock()
# ``(MLflow's environment provider class, MLflow's SDK artifact repository class)``
# as they were before binding, or ``None`` while unbound.
_databricks_binding_originals: tuple[Any, Any] | None = None

_UNBOUND_PAIR_MESSAGE = (
    "Databricks MLflow credentials are not configured: set DATABRICKS_MLFLOW_HOST and "
    "DATABRICKS_MLFLOW_TOKEN in the environment (.env), or select a profile with "
    "MLFLOW_TRACKING_URI=databricks://<profile>. The general DATABRICKS_HOST/"
    "DATABRICKS_TOKEN pair is never used for MLflow."
)
_REST_ONLY_ARTIFACTS_MESSAGE = (
    "haute routes Databricks run and logged-model artifacts through MLflow's REST "
    "artifact repository, which is bound to the MLflow credentials"
)


# The two MLflow module globals the binder replaces, in the order of
# ``_binding_classes()``: MLflow's per-request environment credential provider,
# and the SDK artifact repository its run and logged-model repositories try first.
_BOUND_SYMBOLS: tuple[tuple[str, str], tuple[str, str]] = (
    ("mlflow.utils.databricks_utils", "EnvironmentVariableConfigProvider"),
    ("mlflow.store.artifact.databricks_tracking_artifact_repo", "DatabricksSdkArtifactRepository"),
)


def _bound_mlflow_modules() -> tuple[ModuleType, ModuleType]:
    import importlib

    return (
        importlib.import_module(_BOUND_SYMBOLS[0][0]),
        importlib.import_module(_BOUND_SYMBOLS[1][0]),
    )


def _binding_classes() -> tuple[type, type]:
    """Build the provider and artifact-repository stand-in against the installed MLflow."""
    from mlflow.exceptions import MlflowException
    from mlflow.legacy_databricks_cli.configure.provider import (
        DatabricksConfig,
        DatabricksConfigProvider,
    )

    class MlflowPairConfigProvider(DatabricksConfigProvider):
        """MLflow's environment credential provider, reading the dedicated MLflow pair.

        Read on every call, so a repointed pair is followed by the very next request.
        An unset pair raises instead of returning ``None``: MLflow would otherwise move
        on to the ``DEFAULT`` profile and later providers.
        """

        def get_config(self) -> Any:
            host = os.environ.get(MLFLOW_DATABRICKS_HOST_ENV, "").strip().rstrip("/")
            token = os.environ.get(MLFLOW_DATABRICKS_TOKEN_ENV, "").strip()
            if not host or not token:
                raise MlflowException(_UNBOUND_PAIR_MESSAGE)
            return DatabricksConfig.from_token(host, token)

    class RestOnlyDatabricksArtifacts:
        """Stand-in for MLflow's SDK artifact repository.

        MLflow's run and logged-model artifact repositories try this object first and
        fall back to the REST repository on any exception. The real SDK repository
        builds a bare ``WorkspaceClient()`` from the general environment pair, so
        every operation refuses and the credential-bound REST path does the work.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def log_artifact(self, *args: Any, **kwargs: Any) -> None:
            raise MlflowException(_REST_ONLY_ARTIFACTS_MESSAGE)

        def log_artifacts(self, *args: Any, **kwargs: Any) -> None:
            raise MlflowException(_REST_ONLY_ARTIFACTS_MESSAGE)

        def list_artifacts(self, *args: Any, **kwargs: Any) -> Any:
            raise MlflowException(_REST_ONLY_ARTIFACTS_MESSAGE)

        def _download_file(self, *args: Any, **kwargs: Any) -> None:
            raise MlflowException(_REST_ONLY_ARTIFACTS_MESSAGE)

    return MlflowPairConfigProvider, RestOnlyDatabricksArtifacts


def bind_mlflow_databricks_credentials() -> None:
    """Bind every MLflow Databricks request to the dedicated MLflow credentials.

    Idempotent and lock-guarded; called from the Databricks destination resolver, which
    every consumer passes through before it can build a Databricks tracking URI. A
    ``databricks://<profile>`` URI keeps using that profile (MLflow reads only the profile
    for it); a bare ``databricks`` URI reads ``DATABRICKS_MLFLOW_HOST`` /
    ``DATABRICKS_MLFLOW_TOKEN``. Returns without binding when mlflow is not installed or
    cannot be imported, since such a process cannot make MLflow requests. An installed
    MLflow that no longer exposes the two symbols this replaces raises ``MlflowConfigError``
    rather than leaving MLflow on the general credentials.
    """
    global _databricks_binding_originals

    os.environ.setdefault("MLFLOW_ENABLE_DB_SDK", "false")
    if _databricks_binding_originals is not None:
        return
    if importlib.util.find_spec("mlflow") is None:
        return
    try:
        import mlflow  # noqa: F401
    except Exception:
        return
    with _DATABRICKS_BINDING_LOCK:
        if _databricks_binding_originals is not None:
            return
        try:
            modules = _bound_mlflow_modules()
            originals = (
                getattr(modules[0], _BOUND_SYMBOLS[0][1]),
                getattr(modules[1], _BOUND_SYMBOLS[1][1]),
            )
            replacements = _binding_classes()
        except (ImportError, AttributeError) as exc:
            from haute.errors import MlflowConfigError

            raise MlflowConfigError(
                "haute cannot bind Databricks MLflow credentials for this MLflow version "
                f"({type(exc).__name__}); pin MLflow to a supported release."
            ) from None
        for module, (_, name), replacement in zip(
            modules, _BOUND_SYMBOLS, replacements, strict=True
        ):
            setattr(module, name, replacement)
        _databricks_binding_originals = originals


def _restore_mlflow_databricks_credentials() -> None:
    """Undo :func:`bind_mlflow_databricks_credentials` (test isolation only)."""
    global _databricks_binding_originals

    with _DATABRICKS_BINDING_LOCK:
        if _databricks_binding_originals is None:
            return
        for module, (_, name), original in zip(
            _bound_mlflow_modules(), _BOUND_SYMBOLS, _databricks_binding_originals, strict=True
        ):
            setattr(module, name, original)
        _databricks_binding_originals = None


def tracking_uri_from_environment() -> str:
    """Read configured credentials even while MLflow updates its own environment."""
    snapshot = _tracking_environment_snapshot
    value = snapshot[0] if snapshot is not None else os.environ.get("MLFLOW_TRACKING_URI")
    return value or ""


def registry_uri_for_tracking(tracking_uri: str) -> str:
    """Keep registry selection and Databricks profile aligned with tracking."""
    if tracking_uri == "databricks" or tracking_uri.startswith("databricks://"):
        return "databricks-uc" + tracking_uri[len("databricks") :]
    return tracking_uri


def set_tracking_uri_preserving_env(mlflow: Any, tracking_uri: str) -> None:
    """Configure fluent MLflow without overwriting the user's credential source."""
    global _tracking_environment_snapshot
    with _FLUENT_LOCK:
        configured_uri = os.environ.get("MLFLOW_TRACKING_URI")
        previous_snapshot = _tracking_environment_snapshot
        _tracking_environment_snapshot = (configured_uri,)
        try:
            mlflow.set_tracking_uri(tracking_uri)
        finally:
            _restore_env("MLFLOW_TRACKING_URI", configured_uri)
            _tracking_environment_snapshot = previous_snapshot


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


@contextmanager
def mlflow_fluent_operation() -> Iterator[None]:
    """Serialize global-state SDK operations and restore state on every exit.

    Discovery and native reads use pinned clients without this lock. Pyfunc
    downloads share it for MLflow's nested global-state model lookup. Settings
    can change while a log is in progress; its fluent URI remains fixed until
    the run has terminated. The next writer then resolves the new settings.
    """
    with _FLUENT_LOCK:
        import mlflow

        tracking_uri, registry_uri = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
        environment = {
            name: os.environ.get(name)
            for name in (
                "MLFLOW_TRACKING_URI",
                "MLFLOW_REGISTRY_URI",
                "MLFLOW_EXPERIMENT_ID",
            )
        }
        try:
            yield
        finally:
            try:
                if mlflow.get_tracking_uri() != tracking_uri:
                    set_tracking_uri_preserving_env(mlflow, tracking_uri)
                if mlflow.get_registry_uri() != registry_uri:
                    mlflow.set_registry_uri(registry_uri)
            finally:
                for name, value in environment.items():
                    _restore_env(name, value)


def search_versions(
    client: MlflowClient,
    model_name: str,
) -> list[ModelVersion]:
    """Search model versions, safely quoting the model name."""
    safe_name = model_name.replace("'", "\\'")
    return client.search_model_versions(f"name='{safe_name}'")


def resolve_version(
    client: Any,
    model_name: str,
    version: str,
) -> str:
    """Resolve ``"latest"`` or empty version to a concrete version number.

    Raises:
        ValueError: if no versions are found for the model.
    """
    if version and version != "latest":
        return version

    versions = search_versions(client, model_name)
    if not versions:
        raise ValueError(f"No versions found for registered model '{model_name}'.")
    sorted_versions = sorted(versions, key=lambda v: int(v.version), reverse=True)
    # int on the file store, str on Databricks — callers expect str.
    return str(sorted_versions[0].version)


@contextmanager
def runtime_environment_inference() -> Iterator[None]:
    """Make MLflow record the executing interpreter as a logged model's environment.

    MLflow 3.15 looks for ``uv.lock`` + ``pyproject.toml`` in the *working
    directory* and, when found, ``uv export``s that lock as the model's
    requirements (and logs the lock itself as an artifact) instead of
    capturing the packages the model actually imports. A lock in the cwd is
    not the environment that trained the model whenever the interpreter was
    not synced to it — a stale lock, a dev install, or a different venv — so
    the recorded ``requirements.txt`` would describe an environment that
    never ran and MLflow reports spurious dependency mismatches.

    Inside this context both switches are off, so inference captures the
    installed versions of the imported packages; the previous values
    (including absence) are restored on exit. Call it around every
    ``mlflow.*.log_model`` haute makes without an explicit environment.
    """
    switches = ("MLFLOW_UV_AUTO_DETECT", "MLFLOW_LOG_UV_FILES")
    previous = {name: os.environ.get(name) for name in switches}
    for name in switches:
        os.environ[name] = "false"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def allow_file_store_if_local(tracking_uri: str, backend: str = "") -> None:
    """Opt into MLflow's local file backend before constructing a client."""
    uri = tracking_uri.lower()
    is_local_uri = uri.startswith("file:") or ("://" not in uri and uri != "databricks")
    if backend == "local" or is_local_uri:
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")


@dataclass(frozen=True)
class ResolvedBackend:
    mode: str
    tracking_uri: str  # connection value; may carry env credentials; never logged or persisted
    registry_uri: str
    identity: str  # secret-free
    digest: str  # sha256(identity)[:16], filesystem-safe


def backend_identity(config: TrackingConfig) -> str:
    """Derive the secret-free identity string for a resolved tracking configuration.

    - local: ``local:<canonical absolute folder>``
    - server: ``server:<redacted endpoint>``
    - databricks: ``databricks:<effective host>|profile=<profile or empty>``
    All suffixed with ``|registry=<redacted registry URI>``.
    """
    from haute.errors import MlflowConfigError
    from haute.modelling._mlflow_settings import redact_uri

    if config.mode == "local":
        canonical = os.path.normcase(os.path.normpath(str(Path(config.destination).resolve())))
        prefix = f"local:{canonical}"
    elif config.mode == "server":
        prefix = f"server:{redact_uri(config.tracking_uri).rstrip('/')}"
    elif config.mode == "databricks":
        tracking_uri = config.tracking_uri
        profile = (
            tracking_uri[len("databricks://") :] if tracking_uri.startswith("databricks://") else ""
        )
        from mlflow.utils.databricks_utils import get_databricks_host_creds

        try:
            host_creds = get_databricks_host_creds(tracking_uri)
            host = host_creds.host if host_creds else ""
        except Exception:
            if profile:
                raise MlflowConfigError(
                    f"Databricks profile {profile!r} could not be loaded "
                    "from the Databricks CLI configuration."
                ) from None
            raise MlflowConfigError(
                "Databricks host could not be resolved from the environment."
            ) from None

        if not host:
            if profile:
                raise MlflowConfigError(
                    f"Databricks profile {profile!r} could not be loaded "
                    "from the Databricks CLI configuration."
                )
            raise MlflowConfigError("Databricks host could not be resolved from the environment.")

        prefix = f"databricks:{host.rstrip('/')}|profile={profile}"
    else:
        raise MlflowConfigError(
            f"Unknown MLflow destination mode {config.mode!r}; "
            "expected databricks, server, or local."
        )

    registry_part = redact_uri(registry_uri_for_tracking(config.tracking_uri))
    return f"{prefix}|registry={registry_part}"


def resolve_backend(destination: str = "", project_root: Path | None = None) -> ResolvedBackend:
    """Resolve a destination key (or '' for auto) to a ResolvedBackend.

    Raises:
        MlflowConfigError: If the destination cannot be resolved or is invalid.
    """
    from haute.modelling._mlflow_settings import (
        resolve_destination,
        resolve_tracking_config,
        validate_destination_key,
    )

    if destination:
        validate_destination_key(destination)
        config = resolve_destination(destination, project_root)
    else:
        config = resolve_tracking_config(project_root)

    identity = backend_identity(config)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    registry_uri = registry_uri_for_tracking(config.tracking_uri)
    return ResolvedBackend(
        mode=config.mode,
        tracking_uri=config.tracking_uri,
        registry_uri=registry_uri,
        identity=identity,
        digest=digest,
    )


def resolve_mlflow_source(
    *,
    source_type: str,
    run_id: str = "",
    registered_model: str = "",
    version: str = "",
    destination: str = "",
    backend: ResolvedBackend | None = None,
) -> tuple[str, str, ModuleType, Any, ResolvedBackend]:
    """Import mlflow, pin a client to the destination, and resolve a model source.

    Handles the boilerplate shared across ``_mlflow_io``, ``_optimiser_io``,
    and ``deploy/_bundler``:

    1. Import ``mlflow`` (with a friendly :class:`ImportError`).
    2. Resolve the backend (or use the supplied ``backend`` without re-resolving).
    3. Ensure file store is allowed if the backend is local.
    4. Create an :class:`~mlflow.tracking.MlflowClient` with explicit tracking
       and registry URIs pinned to the backend.
    5. Map *source_type* (``"registered"`` or ``"run"``) to a concrete
       ``run_id`` and ``version``.

    Args:
        source_type: ``"run"`` or ``"registered"``.
        run_id: MLflow run ID (required when *source_type* is ``"run"``).
        registered_model: Registered model name (required when
            *source_type* is ``"registered"``).
        version: Model version (``"1"``, ``"latest"``, etc.).
        destination: Destination key (``"databricks"``, ``"server"``, ``"local"``,
            or ``""`` for auto). Must be ``""`` if *backend* is provided.
        backend: Pre-resolved :class:`ResolvedBackend`. If provided,
            *destination* must be ``""``.

    Returns:
        ``(resolved_run_id, resolved_version, mlflow_module, client, backend)``
        where *mlflow_module* is the imported ``mlflow`` package,
        *client* is an :class:`~mlflow.tracking.MlflowClient`, and
        *backend* is the :class:`ResolvedBackend`.

    Raises:
        ImportError: If ``mlflow`` is not installed.
        ValueError: If required arguments are missing or *source_type* is
            invalid, or both *backend* and *destination* are given.
    """
    if backend is not None and destination != "":
        raise ValueError("Cannot specify both 'backend' and a non-empty 'destination'")

    try:
        import mlflow
    except ImportError:
        raise ImportError("mlflow is not installed. Install it with: pip install mlflow") from None

    from mlflow.tracking import MlflowClient

    if backend is None:
        backend = resolve_backend(destination)

    allow_file_store_if_local(backend.tracking_uri, backend.mode)
    client = MlflowClient(
        tracking_uri=backend.tracking_uri,
        registry_uri=backend.registry_uri,
    )

    resolved_run_id = run_id
    resolved_version = version

    if source_type == "registered":
        if not registered_model:
            raise ValueError("registered_model is required when sourceType is 'registered'")
        resolved_version = resolve_version(client, registered_model, version)
        mv = client.get_model_version(registered_model, resolved_version)
        resolved_run_id = mv.run_id or ""
    elif source_type == "run":
        if not resolved_run_id:
            raise ValueError("run_id is required when sourceType is 'run'")
    else:
        raise ValueError(f"Invalid sourceType: {source_type!r}. Expected 'run' or 'registered'.")

    return resolved_run_id, resolved_version, mlflow, client, backend
