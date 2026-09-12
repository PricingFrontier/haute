"""MLflow discovery and connection endpoints.

Lists experiments, runs (with model artifacts), registered models,
and model versions so the frontend can populate dropdowns; also owns the
connection surface — the destinations inventory with optional concurrent
bounded probes, ``[mlflow]`` settings read/write in ``haute.toml``, and a
bounded test-connection probe.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    import types as _types

    from mlflow.tracking import MlflowClient
    from mlflow.utils.rest_utils import MlflowHostCreds

from haute._logging import get_logger
from haute._mlflow_utils import (
    allow_file_store_if_local,
    registry_uri_for_tracking,
    search_versions,
)
from haute._sandbox import _get_project_root
from haute.errors import MlflowConfigError
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
from haute.schemas import (
    MlflowDestinationEntry,
    MlflowDestinationsResponse,
    MlflowExperimentSummary,
    MlflowModelSummary,
    MlflowModelVersionSummary,
    MlflowProbeCategory,
    MlflowRunSummary,
    MlflowSettingsResponse,
    MlflowSettingsUpdateRequest,
    MlflowTestConnectionRequest,
    MlflowTestConnectionResponse,
    MlflowVersionBrief,
)

logger = get_logger(component="server.mlflow")

router = APIRouter(prefix="/api/mlflow", tags=["mlflow"])

_DestinationQuery = Literal["", "databricks", "server", "local"]


def _elapsed_ms(started_at: float, ended_at: float) -> float:
    """Return elapsed monotonic-clock time in milliseconds."""
    return (ended_at - started_at) * 1000


@dataclass(slots=True)
class _RunDiscoveryMeasurement:
    """Constant-space aggregate for one MLflow run discovery attempt."""

    max_results: int
    started_at: float
    search_calls: int = 0
    artifact_calls: int = 0
    runs_scanned: int = 0
    runs_returned: int = 0
    artifact_failures: int = 0
    search_ms: float = 0.0
    artifact_ms: float = 0.0

    def record_search(self, started_at: float, ended_at: float) -> None:
        self.search_calls += 1
        self.search_ms += _elapsed_ms(started_at, ended_at)

    def record_artifact_call(self, started_at: float, ended_at: float) -> None:
        self.artifact_calls += 1
        self.artifact_ms += _elapsed_ms(started_at, ended_at)

    def emit(self, *, outcome: str) -> None:
        total_ms = _elapsed_ms(self.started_at, perf_counter())
        assembly_ms = max(0.0, total_ms - self.search_ms - self.artifact_ms)
        logger.info(
            "mlflow_run_discovery_completed",
            outcome=outcome,
            max_results=self.max_results,
            search_calls=self.search_calls,
            artifact_calls=self.artifact_calls,
            runs_scanned=self.runs_scanned,
            runs_returned=self.runs_returned,
            artifact_failures=self.artifact_failures,
            search_ms=self.search_ms,
            artifact_ms=self.artifact_ms,
            assembly_ms=assembly_ms,
            total_ms=total_ms,
        )


def _run_summaries(
    runs: list[Any],
    client: MlflowClient,
    artifact_filter: str,
    measurement: _RunDiscoveryMeasurement,
) -> list[MlflowRunSummary]:
    """Build filtered summaries while updating only aggregate work counters."""
    model_extensions = (".cbm", ".rsglm")

    def _match(path: str) -> bool:
        if artifact_filter == "optimiser":
            return path == "optimiser_result.json"
        return any(path.endswith(ext) for ext in model_extensions)

    results: list[MlflowRunSummary] = []
    for run in runs:
        measurement.runs_scanned += 1
        run_id = run.info.run_id
        # Check for matching artifacts (N+1 — unavoidable without batch API)
        artifact_started_at = perf_counter()
        try:
            artifacts = client.list_artifacts(run_id)
            matched = [a.path for a in artifacts if _match(a.path)]
            if not matched:
                continue
        except Exception as exc:
            measurement.artifact_failures += 1
            # Never str(exc): tracking errors can echo credential-bearing URIs.
            logger.warning(
                "artifact_list_failed",
                run_id=run_id,
                category=_classify_probe_error(exc),
                error_type=type(exc).__name__,
            )
            continue
        finally:
            measurement.record_artifact_call(artifact_started_at, perf_counter())

        results.append(
            MlflowRunSummary(
                run_id=run_id,
                run_name=run.info.run_name or "",
                status=run.info.status,
                start_time=run.info.start_time,
                metrics=run.data.metrics or {},
                params=run.data.params or {},
                artifacts=matched,
            )
        )
        measurement.runs_returned += 1
    return results


def _ensure_tracking(destination: str = "") -> tuple[_types.ModuleType, MlflowClient]:
    """Return ``(mlflow, client)`` with a client pinned to the requested destination.

    Raises ``HTTPException(503)`` if mlflow is not installed, or
    ``HTTPException(502)`` if the tracking backend cannot be resolved — a
    tracking misconfiguration surfaces its own (non-secret) reason.
    """
    try:
        import mlflow
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="mlflow is not installed. Install it with: pip install mlflow",
        )

    try:
        from mlflow.tracking import MlflowClient

        from haute.modelling._mlflow_settings import (
            resolve_destination,
            resolve_tracking_config,
        )

        root = _get_project_root()
        config = (
            resolve_destination(destination, root) if destination else resolve_tracking_config(root)
        )
        tracking_uri, backend = config.tracking_uri, config.mode
        allow_file_store_if_local(tracking_uri, backend)
        # Pin the registry to the resolved destination explicitly: without
        # this, the client falls back to the process-global registry URI,
        # so ambient state from another destination could answer registry
        # queries.
        registry_uri = registry_uri_for_tracking(tracking_uri)
        client = MlflowClient(tracking_uri=tracking_uri, registry_uri=registry_uri)
        return mlflow, client
    except HTTPException:
        raise
    except MlflowConfigError as exc:
        # Our own configuration messages are actionable and never secret.
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        # Never str(exc): a client construction failure can echo the tracking URI.
        logger.error(
            "mlflow_tracking_setup_failed",
            category=_classify_probe_error(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail=_INTERNAL_ERROR_DETAIL)


# Actionable, non-leaking details for expected discovery failures. The
# underlying error text never reaches the client (it may carry tokens or
# infrastructure detail); the category-mapped message does.
_DISCOVERY_CATEGORY_DETAILS = {
    "authentication": ("MLflow authentication failed. Check the credentials in your .env file."),
    "permission": "MLflow denied access. Check your workspace permissions.",
    "missing_resource": "The requested MLflow resource was not found.",
    "connectivity": (
        "Could not reach the MLflow tracking server. Check that it is running "
        "and that the tracking destination in the MLflow settings is right."
    ),
}


def _discovery_http_error(exc: BaseException, event: str) -> HTTPException:
    """Map a discovery failure to a 502 with a categorised, non-secret detail."""
    category = _classify_probe_error(exc)
    # Category and exception type only: the raw text may carry tokens or
    # credential-bearing URIs, and the client already gets the mapped detail.
    logger.error(event, category=category, error_type=type(exc).__name__)
    return HTTPException(
        status_code=502,
        detail=_DISCOVERY_CATEGORY_DETAILS.get(category, _INTERNAL_ERROR_DETAIL),
    )


# ---------------------------------------------------------------------------
# Connection surface: status, settings, test-connection
# ---------------------------------------------------------------------------

_PROBE_TIMEOUT_SECONDS = 5.0


def _mlflow_availability() -> tuple[bool, bool, str]:
    """Return ``(installed, importable, detail)`` for the mlflow package."""
    import importlib
    import importlib.util

    if importlib.util.find_spec("mlflow") is None:
        return False, False, "MLflow package is not installed. Install it with: pip install mlflow"
    try:
        importlib.import_module("mlflow")
    except ImportError as exc:
        logger.warning("mlflow_package_import_failed", error=str(exc))
        return True, False, f"MLflow package import failed: {exc}"
    return True, True, ""


def _settings_response() -> MlflowSettingsResponse:
    from haute.modelling._mlflow_settings import (
        load_mlflow_settings,
        resolve_destination,
    )

    root = _get_project_root()
    stored = load_mlflow_settings(root)
    detail = ""
    resolved_folder = ""
    try:
        resolved_folder = resolve_destination("local", root).destination
    except MlflowConfigError as exc:
        detail = str(exc)
    return MlflowSettingsResponse(
        section_present=stored is not None,
        tracking_uri=stored.tracking_uri if stored else "",
        folder=stored.folder if stored else "",
        resolved_folder=resolved_folder,
        detail=detail,
    )


@router.get("/settings", response_model=MlflowSettingsResponse)
def get_mlflow_settings() -> MlflowSettingsResponse:
    """The stored ``[mlflow]`` table verbatim plus its current resolution."""
    try:
        return _settings_response()
    except MlflowConfigError as exc:
        # A malformed stored section: report it rather than 5xx.
        return MlflowSettingsResponse(section_present=True, detail=str(exc))


@router.put("/settings", response_model=MlflowSettingsResponse)
def put_mlflow_settings(body: MlflowSettingsUpdateRequest) -> MlflowSettingsResponse:
    """Validate and persist the ``[mlflow]`` table, then report the result."""
    from haute.modelling._mlflow_settings import MlflowSettings, save_mlflow_settings

    settings = MlflowSettings(
        tracking_uri=body.tracking_uri,
        folder=body.folder,
    )
    try:
        save_mlflow_settings(settings, _get_project_root())
    except MlflowConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _settings_response()


# At most this many probe workers may exist at once. A worker slot is
# released when its thread finishes; a probe abandoned on timeout keeps its
# slot until the underlying call returns, so stalled backends cannot
# accumulate unbounded threads — later probes report busy instead.
_PROBE_SLOTS = threading.BoundedSemaphore(2)


class _ProbeBusyError(Exception):
    """All probe worker slots are occupied by still-running probes."""


def _run_probe_bounded(
    probe: Callable[[], object], timeout: float = _PROBE_TIMEOUT_SECONDS
) -> None:
    """Run *probe* on a daemon worker thread under a hard deadline.

    Raises whatever *probe* raised, ``TimeoutError`` when the deadline
    passes first (the daemon worker is abandoned; its slot frees when the
    call eventually returns and the thread cannot delay process exit), or
    :class:`_ProbeBusyError` when no worker slot is free.
    """
    if not _PROBE_SLOTS.acquire(blocking=False):
        raise _ProbeBusyError
    outcome: Queue[BaseException | None] = Queue(maxsize=1)

    def _worker() -> None:
        try:
            probe()
            outcome.put(None)
        except BaseException as exc:
            outcome.put(exc)
        finally:
            _PROBE_SLOTS.release()

    threading.Thread(target=_worker, name="mlflow-probe", daemon=True).start()
    try:
        result = outcome.get(timeout=timeout)
    except Empty:
        raise TimeoutError(f"MLflow connection probe exceeded {timeout} seconds.") from None
    if result is not None:
        raise result


_PROBE_SEARCH_ENDPOINT = "/api/2.0/mlflow/experiments/search"


def _probe_host_creds(tracking_uri: str) -> MlflowHostCreds:
    """The credentials MLflow's own REST store would use for *tracking_uri*."""
    if tracking_uri == "databricks" or tracking_uri.startswith("databricks://"):
        from mlflow.utils.databricks_utils import get_databricks_host_creds

        return cast("MlflowHostCreds", get_databricks_host_creds(tracking_uri))
    from mlflow.tracking._tracking_service.utils import get_default_host_creds

    return cast("MlflowHostCreds", get_default_host_creds(tracking_uri))


def _search_experiments_probe(tracking_uri: str) -> None:
    """One ``experiments/search`` call against *tracking_uri* — the real
    network body of the connection test.

    A REST destination (server or Databricks) gets exactly one HTTP attempt
    with retries disabled and the connect/read timeout set to the probe
    budget. MLflow's client defaults (120 s timeout, 7 retries, exponential
    backoff) would keep an abandoned worker — and the probe slot it holds —
    busy for minutes after the route has already reported the failure. The
    local file store has no transport, so it is probed through the client.

    Deliberately never calls ``mlflow.set_tracking_uri``: probing a
    candidate destination must not mutate the process-global tracking URI
    other consumers read.
    """
    from haute.modelling._mlflow_settings import classify_tracking_uri

    allow_file_store_if_local(tracking_uri)
    if classify_tracking_uri(tracking_uri)[0] == "local":
        import mlflow.tracking

        mlflow.tracking.MlflowClient(tracking_uri=tracking_uri).search_experiments(max_results=1)
        return

    from mlflow.utils.rest_utils import http_request, verify_rest_response

    response = http_request(
        _probe_host_creds(tracking_uri),
        _PROBE_SEARCH_ENDPOINT,
        "POST",
        json={"max_results": 1},
        max_retries=0,
        timeout=_PROBE_TIMEOUT_SECONDS,
        retry_timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    verify_rest_response(response, _PROBE_SEARCH_ENDPOINT)


def _iter_exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _classify_probe_error(exc: BaseException) -> MlflowProbeCategory:
    """Map a probe failure to a stable category.

    Uses MLflow's structured ``RestException.error_code`` plus transport
    exception types, inspected across the whole ``__cause__``/``__context__``
    chain — MLflow wraps transport failures in ``MlflowException``, so the
    outer type alone is never trusted.
    """
    import requests
    from mlflow.exceptions import RestException

    connectivity_types = (
        TimeoutError,
        ConnectionError,
        OSError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )
    for link in _iter_exception_chain(exc):
        if isinstance(link, RestException):
            code = getattr(link, "error_code", "")
            if code in ("UNAUTHENTICATED", "INVALID_LOGIN", "CUSTOMER_UNAUTHORIZED"):
                return "authentication"
            if code == "PERMISSION_DENIED":
                return "permission"
            if code in ("RESOURCE_DOES_NOT_EXIST", "ENDPOINT_NOT_FOUND"):
                return "missing_resource"
            return "unknown"
        if isinstance(link, connectivity_types):
            return "connectivity"
    return "unknown"


def _probe_outcome(tracking_uri: str) -> tuple[bool, MlflowProbeCategory, str]:
    """``(ok, category, detail)`` for one bounded probe; never raises."""
    try:
        _run_probe_bounded(lambda: _search_experiments_probe(tracking_uri))
    except _ProbeBusyError:
        return False, "unknown", "Another connection test is still running; try again shortly."
    except BaseException as exc:
        category = _classify_probe_error(exc)
        # Never log str(exc): transport and REST errors can echo tokens or URLs with userinfo.
        logger.warning("mlflow_probe_failed", category=category, error_type=type(exc).__name__)
        return False, category, f"Connection test failed ({category})."
    return True, "", ""


@router.get("/destinations", response_model=MlflowDestinationsResponse)
def mlflow_destinations(probe: bool = Query(False)) -> MlflowDestinationsResponse:
    from concurrent.futures import ThreadPoolExecutor

    from haute.modelling._mlflow_settings import (
        DESTINATION_KEYS,
        list_destinations,
        resolve_destination,
        resolve_tracking_config,
    )

    root = _get_project_root()
    installed, importable, package_detail = _mlflow_availability()
    # Per-entry truth first: list_destinations never raises for a configuration problem — each
    # key reports its own reason (a malformed [mlflow] table lands on the toml-backed server and
    # local entries; a rejected Databricks SDK mode lands on databricks only).
    entries = list_destinations(root)
    # The auto rule is reported separately so one broken entry never hides the usable ones.
    try:
        auto = resolve_tracking_config(root).mode
        auto_detail = ""
    except MlflowConfigError as exc:
        auto, auto_detail = "", str(exc)
    detail = package_detail or auto_detail
    response_entries = {
        e.key: MlflowDestinationEntry(
            key=e.key,  # type: ignore[arg-type]
            configured=e.configured,
            destination=e.destination,
            config_source=e.config_source,  # type: ignore[arg-type]
            detail=e.detail,
        )
        for e in entries
    }
    if probe and installed and importable:
        targets = [k for k in ("databricks", "server") if response_entries[k].configured]
        if targets:
            with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
                outcomes = list(
                    pool.map(
                        lambda k: _probe_outcome(resolve_destination(k, root).tracking_uri),
                        targets,
                    )
                )
            for key, (ok, category, probe_detail) in zip(targets, outcomes, strict=True):
                entry = response_entries[key]
                response_entries[key] = entry.model_copy(
                    update={
                        "probed": True,
                        "ok": ok,
                        "category": category,
                        "detail": probe_detail,
                    }
                )
    return MlflowDestinationsResponse(
        mlflow_installed=installed,
        mlflow_importable=importable,
        auto=auto,  # type: ignore[arg-type]
        destinations=[response_entries[k] for k in DESTINATION_KEYS],
        detail=detail,
    )


@router.post("/test-connection", response_model=MlflowTestConnectionResponse)
def mlflow_test_connection(
    body: MlflowTestConnectionRequest | None = None,
) -> MlflowTestConnectionResponse:
    """Probe a tracking destination; expected failures never 5xx."""
    from haute.modelling._mlflow_settings import (
        MlflowSettings,
        candidate_tracking_config,
        resolve_destination,
        resolve_tracking_config,
        validate_destination_key,
    )

    installed, importable, detail = _mlflow_availability()
    if not (installed and importable):
        return MlflowTestConnectionResponse(ok=False, category="configuration", detail=detail)

    root = _get_project_root()
    try:
        if body is None or not body.destination:
            config = resolve_tracking_config(root)
        elif body.tracking_uri is None and body.folder is None:
            config = resolve_destination(validate_destination_key(body.destination) or "", root)
        else:
            config = candidate_tracking_config(
                body.destination,
                MlflowSettings(tracking_uri=body.tracking_uri or "", folder=body.folder or ""),
                root,
            )
    except MlflowConfigError as exc:
        return MlflowTestConnectionResponse(ok=False, category="configuration", detail=str(exc))
    ok, category, detail = _probe_outcome(config.tracking_uri)
    return MlflowTestConnectionResponse(ok=ok, category=category, detail=detail)


@router.get("/experiments", response_model=list[MlflowExperimentSummary])
def list_experiments(
    destination: Annotated[_DestinationQuery, Query()] = "",
) -> list[MlflowExperimentSummary]:
    """List all MLflow experiments."""
    _mlflow, client = _ensure_tracking(destination)

    try:
        page = client.search_experiments()
        experiments = list(page)
        while page.token:
            page = client.search_experiments(page_token=page.token)
            experiments.extend(page)
    except Exception as exc:
        raise _discovery_http_error(exc, "mlflow_list_experiments_failed")

    return [
        MlflowExperimentSummary(
            experiment_id=exp.experiment_id,
            name=exp.name,
        )
        for exp in experiments
    ]


@router.get("/runs", response_model=list[MlflowRunSummary])
def list_runs(
    experiment_id: str = Query(..., description="MLflow experiment ID"),
    max_results: int = Query(20, ge=1, le=100),
    artifact_filter: str = Query(
        "model",
        description=(
            "Filter runs by artifact type: "
            "'model' for any model artifact (.cbm, .rsglm), "
            "'optimiser' for optimiser results (optimiser_result.json)"
        ),
    ),
    destination: Annotated[_DestinationQuery, Query()] = "",
) -> list[MlflowRunSummary]:
    """List runs for an experiment, filtered to FINISHED runs with matching artifacts.

    Note: Each run requires a separate ``list_artifacts`` call to check for
    matching files.  MLflow has no batch artifacts API, so this is O(N) in
    the number of runs.  The ``max_results`` cap bounds the total calls.
    """
    _mlflow, client = _ensure_tracking(destination)
    measurement = _RunDiscoveryMeasurement(max_results=max_results, started_at=perf_counter())

    search_started_at = perf_counter()
    try:
        runs = client.search_runs(
            experiment_ids=[experiment_id],
            filter_string="status = 'FINISHED'",
            max_results=max_results,
        )
    except Exception as exc:
        measurement.record_search(search_started_at, perf_counter())
        measurement.emit(outcome="search_failed")
        raise _discovery_http_error(exc, "mlflow_list_runs_failed")
    measurement.record_search(search_started_at, perf_counter())

    try:
        results = _run_summaries(runs, client, artifact_filter, measurement)
    except BaseException:
        measurement.emit(outcome="processing_failed")
        raise
    measurement.emit(outcome="success")
    return results


@router.get("/models", response_model=list[MlflowModelSummary])
def list_models(
    max_results: int = Query(100, ge=1, le=1000),
    page_token: str | None = Query(None),
    destination: Annotated[_DestinationQuery, Query()] = "",
) -> list[MlflowModelSummary]:
    """List registered models."""
    _mlflow, client = _ensure_tracking(destination)

    try:
        result = client.search_registered_models(
            max_results=max_results,
            page_token=page_token if page_token else None,
        )
    except Exception as exc:
        raise _discovery_http_error(exc, "mlflow_list_models_failed")

    return [
        MlflowModelSummary(
            name=m.name,
            latest_versions=[
                MlflowVersionBrief(
                    # The file-store registry reports versions as ints where
                    # Databricks reports strings; the wire contract is str.
                    version=str(v.version),
                    status=v.status,
                    run_id=v.run_id,
                )
                for v in (m.latest_versions or [])
            ],
        )
        for m in result
    ]


def _model_version_run_params(client: MlflowClient, run_id: str) -> dict[str, str]:
    """Fetch a registered model version's backing-run params.

    A registered model version may reference a run that has been deleted or
    is otherwise inaccessible — in that case the version itself is still
    valid, so swallow the lookup error and return ``{}`` rather than failing
    the whole ``/model-versions`` response. The exception is logged with a
    stack trace so the underlying cause is diagnosable.
    """
    if not run_id:
        return {}
    try:
        run = client.get_run(run_id)
    except Exception:
        logger.exception(
            "mlflow_model_version_params_unavailable",
            run_id=run_id,
        )
        return {}
    return dict(run.data.params)


@router.get("/model-versions", response_model=list[MlflowModelVersionSummary])
def list_model_versions(
    model_name: str = Query(..., description="Registered model name"),
    destination: Annotated[_DestinationQuery, Query()] = "",
) -> list[MlflowModelVersionSummary]:
    """List versions of a registered model."""
    _mlflow, client = _ensure_tracking(destination)

    try:
        versions = search_versions(client, model_name)
    except Exception as exc:
        raise _discovery_http_error(exc, "mlflow_list_versions_failed")

    return [
        MlflowModelVersionSummary(
            # int on the file store, str on Databricks — the contract is str.
            version=str(v.version),
            run_id=v.run_id or "",
            status=v.status,
            creation_timestamp=v.creation_timestamp,
            # None on the file store, absent or str on Databricks.
            description=getattr(v, "description", "") or "",
            params=_model_version_run_params(client, v.run_id or ""),
        )
        for v in sorted(versions, key=lambda v: int(v.version), reverse=True)
    ]
