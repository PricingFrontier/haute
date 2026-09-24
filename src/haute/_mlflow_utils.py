"""Shared MLflow helpers used by _mlflow_io, _optimiser_io, and deploy/_bundler.

Eliminates duplication of:
  - ``resolve_version()``: resolve "latest" to a concrete version number
  - ``search_versions()``: safely quote model name and search
  - ``ResolvedBackend``: once-per-load resolution of a destination key ("" = local)
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
  routes run and logged-model artifacts through MLflow's REST repository, and pins the
  Unity Catalog model-artifact SDK client to the same token, so no Databricks SDK client
  authenticates with the general pair or an ambient service principal.
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
# MLflow's originals for ``_BOUND_SYMBOLS``, in order, as they were before binding,
# or ``None`` while unbound.
_databricks_binding_originals: tuple[Any, ...] | None = None

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


# The MLflow module globals the binder replaces, in the order of
# ``_binding_replacements()``: MLflow's per-request environment credential
# provider, the SDK artifact repository its run and logged-model repositories try
# first, and the SDK client factory of its Unity Catalog model-artifact repository.
_BOUND_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("mlflow.utils.databricks_utils", "EnvironmentVariableConfigProvider"),
    ("mlflow.store.artifact.databricks_tracking_artifact_repo", "DatabricksSdkArtifactRepository"),
    (
        "mlflow.store.artifact.databricks_sdk_models_artifact_repo",
        "_get_databricks_workspace_client",
    ),
)


def _bound_mlflow_modules() -> tuple[ModuleType, ...]:
    import importlib

    return tuple(importlib.import_module(module_name) for module_name, _ in _BOUND_SYMBOLS)


def _binding_replacements() -> tuple[Any, ...]:
    """Build the replacements for ``_BOUND_SYMBOLS``, in order, against the installed MLflow."""
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

    def bound_models_workspace_client(registry_uri: str | None = None) -> Any:
        """The Unity Catalog model-artifact SDK client, pinned to the bound credentials.

        MLflow uses this SDK repository when the workspace (or
        ``MLFLOW_USE_DATABRICKS_SDK_MODEL_ARTIFACTS_REPO_FOR_UC``) selects it. Its own
        factory passes host and token but lets the SDK read every other field from the
        environment, so a general service principal (``DATABRICKS_CLIENT_ID`` /
        ``DATABRICKS_CLIENT_SECRET``, ``DATABRICKS_AUTH_TYPE``) either conflicts with the
        token or replaces it. The host and token come from the bound provider or the
        selected profile, and ``auth_type="pat"`` makes that token the only credential.
        Missing credentials raise instead of falling back to the SDK's default auth.
        """
        from databricks.sdk import WorkspaceClient
        from mlflow.utils.databricks_utils import get_databricks_host_creds

        creds = get_databricks_host_creds(registry_uri)
        if not creds.host or not creds.token:
            raise MlflowException(
                "Databricks Unity Catalog model artifacts need a personal access token: "
                "set DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN, or select a profile "
                "that carries a token with MLFLOW_TRACKING_URI=databricks://<profile>."
            )
        return WorkspaceClient(host=creds.host, token=creds.token, auth_type="pat")

    return MlflowPairConfigProvider, RestOnlyDatabricksArtifacts, bound_models_workspace_client


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
            originals = tuple(
                getattr(module, name)
                for module, (_, name) in zip(modules, _BOUND_SYMBOLS, strict=True)
            )
            replacements = _binding_replacements()
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


_WORKSPACE_MKDIRS_ENDPOINT = "/api/2.0/workspace/mkdirs"


def _is_databricks_tracking(tracking_uri: str) -> bool:
    return tracking_uri == "databricks" or tracking_uri.startswith("databricks://")


_EXPERIMENT_LOCKS: dict[str, threading.Lock] = {}
_EXPERIMENT_LOCKS_GUARD = threading.Lock()


@contextmanager
def _experiment_creation_lock(experiment_name: str) -> Iterator[None]:
    """Serialise one experiment name's lookup-and-create within the process.

    MLflow's file store checks that a name is free and then creates the experiment
    under a newly generated id, so two threads creating the same name both succeed
    and split their runs across two experiments. Keyed by name alone, so a log to
    another experiment never waits, and two destinations that share a name wait
    only for each other's lookup.
    """
    with _EXPERIMENT_LOCKS_GUARD:
        lock = _EXPERIMENT_LOCKS.setdefault(experiment_name, threading.Lock())
    with lock:
        yield


def ensure_experiment(client: MlflowClient, tracking_uri: str, experiment_name: str) -> str:
    """The id of *experiment_name* at *client*'s destination, creating it when missing.

    The client-bound counterpart of :func:`set_experiment_creating_workspace_folder`
    for callers that log through a destination-bound client instead of MLflow's
    process-global fluent state: a new Databricks experiment's workspace folder is
    created first, with the same credentials MLflow's own requests use for
    *tracking_uri*. A deleted experiment is refused exactly as
    ``mlflow.set_experiment`` refuses it.

    Raises:
        MlflowRemoteError: MLflow refused to create the Databricks folder.
        MlflowException: a deleted experiment, and authentication or
            connectivity failures, for the caller to classify.
    """
    from mlflow.entities import LifecycleStage
    from mlflow.exceptions import MlflowException
    from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE

    with _experiment_creation_lock(experiment_name):
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is None:
            folder = experiment_name.rpartition("/")[0]
            if _is_databricks_tracking(tracking_uri) and experiment_name.startswith("/") and folder:
                _create_databricks_workspace_folder(tracking_uri, folder, experiment_name)
            try:
                return str(client.create_experiment(experiment_name))
            except MlflowException as exc:
                # Another process created it between the lookup and the create.
                if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                    raise
                experiment = client.get_experiment_by_name(experiment_name)
                if experiment is None:
                    raise
    if experiment.lifecycle_stage != LifecycleStage.ACTIVE:
        raise MlflowException(
            f"Cannot set a deleted experiment {experiment.name!r} as the active experiment. "
            "You can restore the experiment, or permanently delete the experiment to create "
            "a new one.",
            error_code=INVALID_PARAMETER_VALUE,
        )
    return str(experiment.experiment_id)


def set_experiment_creating_workspace_folder(mlflow: Any, experiment_name: str) -> Any:
    """``mlflow.set_experiment``, first creating a new Databricks experiment's folder.

    A Databricks experiment is a workspace object, and creating one fails with
    ``NOT_FOUND: Parent directory does not exist`` when its folder is missing — as
    ``/Shared/haute`` is in a fresh workspace. When the fluent tracking URI is
    Databricks and the experiment does not exist yet, the parent folder (and its
    ancestors) is created first through the Workspace API, with the same credentials
    MLflow's own requests use. Other backends, and existing experiments, go straight
    to ``set_experiment``.

    Raises:
        MlflowRemoteError: MLflow refused to create the folder (permission, a
            missing parent, or an unclassified refusal); the message names the
            folder and experiment so the user can create it or pick another path.
        MlflowException: authentication and connectivity failures propagate
            unchanged, for the caller to classify with its own copy.
    """
    tracking_uri = mlflow.get_tracking_uri()
    folder = experiment_name.rpartition("/")[0]
    with _experiment_creation_lock(experiment_name):
        if (
            _is_databricks_tracking(tracking_uri)
            and experiment_name.startswith("/")
            and folder
            and mlflow.get_experiment_by_name(experiment_name) is None
        ):
            _create_databricks_workspace_folder(tracking_uri, folder, experiment_name)
        return mlflow.set_experiment(experiment_name)


def _create_databricks_workspace_folder(
    tracking_uri: str, folder: str, experiment_name: str
) -> None:
    from mlflow.exceptions import MlflowException
    from mlflow.utils.databricks_utils import get_databricks_host_creds
    from mlflow.utils.rest_utils import http_request, verify_rest_response

    from haute._mlflow_errors import MlflowRemoteError, classify_mlflow_error

    try:
        response = http_request(
            get_databricks_host_creds(tracking_uri),
            _WORKSPACE_MKDIRS_ENDPOINT,
            "POST",
            json={"path": folder},
        )
        verify_rest_response(response, _WORKSPACE_MKDIRS_ENDPOINT)
    except MlflowException as exc:
        from haute._logging import get_logger

        category = classify_mlflow_error(exc)
        # Category and type only: MLflow's text can echo hosts and tokens.
        get_logger(component="mlflow_utils").warning(
            "databricks_experiment_folder_create_failed",
            folder=folder,
            experiment=experiment_name,
            category=category,
            error_type=type(exc).__name__,
        )
        if category in ("authentication", "connectivity"):
            raise
        refusal = (
            "MLflow denied permission to create"
            if category == "permission"
            else ("Could not create")
        )
        raise MlflowRemoteError(
            category,
            f"{refusal} the Databricks workspace folder {folder} for the MLflow experiment "
            f"{experiment_name}. Create the folder in the workspace, or choose an experiment "
            "path in a folder you can write to.",
        ) from exc


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


@contextmanager
def mlflow_fluent_operation() -> Iterator[None]:
    """Serialize global-state SDK operations and restore state on every exit.

    The only MLflow calls that still need process-global state run here: model
    logging (``mlflow.<flavor>.log_model`` resolves the global tracking URI and
    the thread's active run, and its uv detection is switched off only through
    environment variables), pyfunc downloads (MLflow's nested logged-model
    lookup), and the optimiser route's log. Everything else — discovery, native
    reads, and training and deploy runs, parameters, metrics, tags and
    artifacts — uses destination-bound clients without this lock.

    MLflow's active experiment is scoped here too: it is cleared on entry, so
    attaching to a client-created run with ``mlflow.start_run(run_id=...)`` is
    never refused because an earlier ``set_experiment`` named another
    experiment, and the previous value is restored on exit.
    """
    with _FLUENT_LOCK:
        import mlflow
        from mlflow.tracking import fluent

        tracking_uri, registry_uri = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
        active_experiment_id = fluent._active_experiment_id
        fluent._active_experiment_id = None
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
                fluent._active_experiment_id = active_experiment_id
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
    alias: str = "",
) -> str:
    """Resolve an alias, ``"latest"`` or an empty version to a concrete version number.

    An alias resolves through the registry every time, so moving the alias moves
    what this returns. A concrete version together with an alias is ambiguous and
    rejected; ``"latest"``/``""`` beside an alias is the loaders' default and
    defers to the alias.

    Raises:
        ValueError: if both a concrete version and an alias are given, the model has
            no such alias, or no versions are found for the model.
    """
    if alias:
        if version and version != "latest":
            raise ValueError(
                f"Registered model '{model_name}' was given both version {version} and "
                f"alias '{alias}'; choose one."
            )
        from mlflow.exceptions import MlflowException

        try:
            model_version = client.get_model_version_by_alias(model_name, alias)
        except MlflowException as exc:
            if getattr(exc, "error_code", "") in (
                "INVALID_PARAMETER_VALUE",
                "RESOURCE_DOES_NOT_EXIST",
            ):
                raise ValueError(
                    f"Registered model '{model_name}' has no alias '{alias}'. Choose an "
                    "existing alias or a version."
                ) from exc
            raise
        # int on the file store, str on Databricks — callers expect str.
        return str(model_version.version)
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
    """Resolve a destination key ('' for the local folder) to a ResolvedBackend.

    Raises:
        MlflowConfigError: If the destination cannot be resolved or is invalid.
    """
    from haute.modelling._mlflow_settings import node_destination_key, resolve_destination

    config = resolve_destination(node_destination_key(destination), project_root)

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
    alias: str = "",
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
            or ``""`` for local). Must be ``""`` if *backend* is provided.
        backend: Pre-resolved :class:`ResolvedBackend`. If provided,
            *destination* must be ``""``.
        alias: Registered model alias; resolves to the version it currently
            targets (see :func:`resolve_version`).

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
        resolved_version = resolve_version(client, registered_model, version, alias)
        mv = client.get_model_version(registered_model, resolved_version)
        resolved_run_id = mv.run_id or ""
    elif source_type == "run":
        if not resolved_run_id:
            raise ValueError("run_id is required when sourceType is 'run'")
    else:
        raise ValueError(f"Invalid sourceType: {source_type!r}. Expected 'run' or 'registered'.")

    return resolved_run_id, resolved_version, mlflow, client, backend
