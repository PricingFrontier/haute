"""Container deployment target - generate FastAPI app + Dockerfile, build, push."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path, PurePosixPath
from typing import Any

from haute._git_core import _run_git_ok, git_binary_available
from haute._logging import get_logger
from haute._model_flavors import XGBOOST_DISTRIBUTION, family_for_suffix
from haute._polars_environment import polars_process_environment, polars_spill_root
from haute._price_contour import DISTRIBUTION as PRICE_CONTOUR_DISTRIBUTION
from haute._price_contour import PriceContourCompatibilityError, price_contour_install
from haute.deploy._config import ResolvedDeploy
from haute.deploy._lock import DOCKER_PLATFORM, TARGET_PLATFORM, lock_requirements
from haute.deploy._mlflow import DeployResult
from haute.deploy._node_roles import DEPLOY_ARTIFACTS
from haute.deploy._project_modules import UTILITY_PACKAGE
from haute.deploy._request_limits import (
    DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES,
)
from haute.deploy._request_rows import copy_verified
from haute.deploy._utils import (
    artifact_file_name,
    build_manifest,
    model_source_line,
    owned_build_dir,
)
from haute.errors import DeployError

logger = get_logger(component="deploy.container")

_VALID_BASE_IMAGE_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")
_VALID_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
DEFAULT_QUOTE_REQUEST_BODY_LIMIT_BYTES = DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES
DEFAULT_QUOTE_RESPONSE_ROW_LIMIT = 1_000
# The scoring runtime a deployed pricing API imports, as (distribution, pip
# install name). The Dockerfile installs these pinned, then ``haute`` itself
# with ``--no-deps``: haute's own dependencies bring the editor, assistant,
# training, tuning and MLflow stacks, which a scoring container never runs.
# ``pip install haute`` is unchanged.
_SCORING_RUNTIME_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("polars", "polars"),
    ("pyarrow", "pyarrow"),
    ("numpy", "numpy"),
    ("pydantic", "pydantic"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("structlog", "structlog"),
    ("xxhash", "xxhash"),
    ("psutil", "psutil"),
    ("orjson", "orjson"),
    ("msgspec", "msgspec"),
    ("joblib", "joblib"),
    # The price-contour compatibility guard checks the installed version with it.
    ("packaging", "packaging"),
    (PRICE_CONTOUR_DISTRIBUTION, PRICE_CONTOUR_DISTRIBUTION),
)

#: haute's own requirements a scoring image leaves out, reviewed against the scoring
#: runtime policy (specs/deploy, Scoring runtime): the editor, assistant, training,
#: tuning, workbench-store and registry stacks, and each model engine, which the image
#: installs only when a bundled artefact needs it (``_extra_deps_by_artifact``) or the
#: graph loads from MLflow. The image's runtime check accepts haute's requirement on one of
#: these being absent, and no other: a requirement haute adds is reviewed here first.
HAUTE_SCORING_OMISSIONS: frozenset[str] = frozenset(
    {
        "alembic",
        "anthropic",
        "catboost",
        "interpret-core",
        "libcst",
        "lightgbm",
        "mlflow",
        "openai",
        "optuna",
        "pandas",
        "python-multipart",
        "rustystats",
        "scipy",
        "sqlalchemy",
        "t-boost",
        "tomlkit",
        "xgboost",
        "xgboost-cpu",
    }
)

#: The lock files a bundle carries beside its Dockerfile: the runtime, haute (installed
#: without its dependencies) and the record the image's runtime check reads.
RUNTIME_LOCK_FILE = "requirements.lock"
HAUTE_LOCK_FILE = "haute.lock"
LOCK_RECORD_FILE = "dependency_lock.json"


def _validate_base_image(base_image: str) -> None:
    """Validate base_image to prevent command injection."""
    if not _VALID_BASE_IMAGE_RE.match(base_image):
        raise ValueError(f"Invalid base_image: {base_image!r}")


def _validate_model_name(model_name: str) -> None:
    """Validate model_name to prevent path traversal / injection."""
    if not _VALID_MODEL_NAME_RE.match(model_name):
        raise ValueError(f"Invalid model_name: {model_name!r}")


# ── Targets that share container build+push ────────────────────────


@dataclass
class ContainerBuildResult:
    """Intermediate result from build_and_push_image()."""

    image_tag: str
    model_name: str
    model_version: str
    """The image's tag (the commit it was built from), the version a container carries."""
    build_dir: Path | None
    """The build's directory when it was kept, else ``None``: removed once it shipped."""


def write_bundle(
    resolved: ResolvedDeploy,
    build_dir: Path,
    manifest_extras: Mapping[str, Any] | None = None,
) -> Path:
    """Write the pipeline's bundle into *build_dir*, as a deploy and a workbench release hold it.

    The manifest (``build_manifest`` with its artifacts at ``artifacts/<file>``, each
    key's flat ``artifact_file_name``, and *manifest_extras* added), the artifacts copied
    under ``artifacts/``, and the project's utility package beside them. *build_dir* must
    be empty or not exist yet: a bundle holds its own build's files and nothing else, so
    one holding anything is refused rather than written over.

    Returns:
        Path to the written ``deploy_manifest.json``.
    """
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    if any(build_dir.iterdir()):
        raise DeployError(
            f"{build_dir} already holds files: a bundle is written into an empty directory, "
            "so it ships only its own build's files.",
            build_dir=str(build_dir),
        )
    artifacts_dir = build_dir / "artifacts"
    artifacts_dir.mkdir()

    manifest = build_manifest(resolved)
    manifest["artifacts"] = {
        name: f"artifacts/{artifact_file_name(name)}" for name in resolved.artifacts
    }
    manifest.update(manifest_extras or {})
    manifest_path = build_dir / "deploy_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Each artefact is copied under the digest resolution recorded before checking it.
    for artifact_name, artifact_path in resolved.artifacts.items():
        copy_verified(
            artifact_path,
            artifacts_dir / artifact_file_name(artifact_name),
            digest=resolved.artifact_digests[artifact_name],
            name=artifact_name,
        )

    utility = resolved.project_modules.utility
    if utility is not None and utility.is_dir():
        shutil.copytree(
            utility,
            build_dir / UTILITY_PACKAGE,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    elif utility is not None:
        shutil.copy2(utility, build_dir / f"{UTILITY_PACKAGE}.py")
    return manifest_path


def prepare_build_directory(
    resolved: ResolvedDeploy,
    build_dir: Path,
    *,
    haute_requirement: str | None = None,
    extra_files: Sequence[str] = (),
    extra_dependencies: Sequence[str] = (),
    sqlserver_odbc: bool = False,
) -> Path:
    """Prepare the container build directory with manifest, artifacts, app.py, and Dockerfile.

    Steps:
        1. Write the bundle (``write_bundle``) into the empty *build_dir*: the manifest,
           with the container's pinned dependencies, the artifacts and the utility package
        2. Copy the haute wheel, when haute_requirement names one
        3. Generate FastAPI app source
        4. Generate Dockerfile

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        build_dir: Destination directory for the build artefacts, empty or not yet made.
        haute_requirement: Optional override for the ``haute`` dependency in the Dockerfile.
            When it names a local wheel file (*.whl), the wheel is copied into ``build_dir``
            and the Dockerfile gains ``COPY <wheel name> .`` before ``pip install ./<wheel name>``.
        extra_files: Files the caller writes beside ``app.py``, which the image copies too: a
            workbench release's form and description.
        extra_dependencies: Distributions the caller's app needs past the scoring runtime,
            installed in the image pinned to the versions installed here: the underwriter
            app's quote store.
        sqlserver_odbc: Install the system libraries the ODBC Driver 18 inside mssql-python's
            wheels loads, which the quote store's ``sqlserver`` provider connects through.

    Returns:
        Path to the written ``deploy_manifest.json``.
    """
    config = resolved.config
    model_name = config.model_name
    ct = config.container

    _validate_base_image(ct.base_image)
    _validate_model_name(model_name)

    build_dir = Path(build_dir)

    wheel_path: Path | None = None
    haute_pip_dep: str | None = None
    if haute_requirement is not None:
        if haute_requirement.lower().endswith(".whl"):
            wheel_path = Path(haute_requirement)
            haute_pip_dep = f"./{wheel_path.name}"
        else:
            haute_pip_dep = haute_requirement
    wheel_name = wheel_path.name if wheel_path is not None else None

    extra_pins = [
        _pinned_dockerfile_dependency(name, name, required_by=["the app"])
        for name in extra_dependencies
    ]
    haute_root, *runtime_roots = _pinned_dockerfile_deps(resolved, haute_requirement=haute_pip_dep)
    python_version = container_python_version(resolved)
    # The closure is resolved once, here, for the image's Python and platform, and the
    # image installs exactly it: a rebuild of this bundle installs the same runtime.
    lock = lock_requirements(
        [*runtime_roots, *extra_pins],
        python_version=python_version,
        platform=TARGET_PLATFORM,
        constraints=haute_constraints(),
    )
    manifest_path = write_bundle(
        resolved,
        build_dir,
        {
            "container_dependencies": [
                haute_root,
                *(f"{name}=={version}" for name, version in sorted(lock.pins.items())),
            ],
            "dependency_lock": lock.provenance(),
        },
    )
    if wheel_path is not None:
        shutil.copy2(wheel_path, build_dir / wheel_path.name)
    (build_dir / RUNTIME_LOCK_FILE).write_text(lock.requirements, encoding="utf-8")
    (build_dir / HAUTE_LOCK_FILE).write_text(
        _haute_lock(haute_root, wheel_path, python_version), encoding="utf-8"
    )
    (build_dir / LOCK_RECORD_FILE).write_text(
        json.dumps(
            {
                **lock.provenance(),
                "haute_omitted": sorted(HAUTE_SCORING_OMISSIONS - set(lock.pins)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    app_source = _generate_app_source(config.model_name, ct.port)
    (build_dir / "app.py").write_text(app_source, encoding="utf-8")

    dockerfile = _generate_dockerfile(
        ct.base_image,
        ct.port,
        resolved,
        wheel_name=wheel_name,
        extra_files=extra_files,
        sqlserver_odbc=sqlserver_odbc,
    )
    (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    return manifest_path


def build_and_push_image(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
    *,
    keep_build: bool = False,
) -> ContainerBuildResult:
    """Build a Docker image from a resolved pipeline - shared by all container targets.

    Steps:
        1. Stage the bundle, app and Dockerfile in a directory this build owns under the
           project's ``.haute_build`` (``owned_build_dir``)
        2. Build the Docker image
        3. Push it to the registry (if configured)
        4. Remove the build's directory, shipped or failed, unless *keep_build*

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        progress: Optional callback for step-by-step progress messages.
        keep_build: Keep the build's directory, named in the result, for inspection.

    Returns:
        ContainerBuildResult with the image tag.
    """

    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    config = resolved.config
    model_name = config.model_name
    ct = config.container

    _validate_base_image(ct.base_image)
    _validate_model_name(model_name)

    with owned_build_dir(resolved, keep=keep_build) as build_dir:
        _log("Building deployment manifest...")
        manifest_path = prepare_build_directory(resolved, build_dir)
        _log(f"  Manifest: {manifest_path}")
        for node_id, source in resolved.model_sources.items():
            _log(f"  {model_source_line(node_id, source)}")
        _log(f"Copying {len(resolved.artifacts)} artifacts...")
        _log("Generating FastAPI app...")
        _log("Generating Dockerfile...")

        git_sha = _git_sha_short()
        if ct.registry:
            image_tag = f"{ct.registry.rstrip('/')}/{model_name}:{git_sha}"
        else:
            image_tag = f"{model_name}:{git_sha}"

        _log(f"Building Docker image: {image_tag}")
        _check_docker_available()
        _docker_build(build_dir, image_tag)
        _log(f"  ✓ Image built: {image_tag}")

        if ct.registry:
            _log(f"Pushing to registry: {ct.registry}")
            _docker_push(image_tag)
            _log(f"  ✓ Image pushed: {image_tag}")
        else:
            _log("  No registry configured - image is local only.")

    if keep_build:
        _log(f"  Build kept in {build_dir}")
    return ContainerBuildResult(
        image_tag=image_tag,
        model_name=model_name,
        model_version=git_sha,
        build_dir=build_dir if keep_build else None,
    )


def deploy_to_container(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
    *,
    keep_build: bool = False,
) -> DeployResult:
    """Generic container target - build and push only, no service update.

    Use this for local testing or when IT manages the service separately.
    For managed platform targets (Azure Container Apps, AWS ECS, GCP Cloud
    Run), use ``deploy_to_platform_container()`` instead.
    """
    result = build_and_push_image(resolved, progress, keep_build=keep_build)
    return DeployResult(
        model_name=result.model_name,
        model_version=result.model_version,
        model_uri=result.image_tag,
        endpoint_url=None,
        build_dir=result.build_dir,
    )


def deploy_to_platform_container(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
    *,
    keep_build: bool = False,
) -> DeployResult:
    """Platform container target (azure-container-apps, aws-ecs, gcp-run): build and push.

    These targets are build and push only: no platform SDK adapter updates the
    running service yet. The image must reach a registry for anyone to deploy it,
    so a missing ``container.registry`` fails before the build; after the push the
    deploy succeeds and reports which image to point the service at.
    """
    target = resolved.config.target
    if not resolved.config.container.registry:
        raise DeployError(
            f"Target '{target}' pushes the scoring image for a manual service update, "
            "so it needs a registry: set [deploy.container] registry in haute.toml."
        )
    result = build_and_push_image(resolved, progress, keep_build=keep_build)
    if progress:
        progress(
            f"Service not updated: updating {target} is not implemented yet. "
            f"Point the service at {result.image_tag}."
        )
    return DeployResult(
        model_name=result.model_name,
        model_version=result.model_version,
        model_uri=result.image_tag,
        endpoint_url=None,
        build_dir=result.build_dir,
    )


# ── App generation ──────────────────────────────────────────────────


def _generate_app_source(model_name: str, port: int) -> str:
    """Generate the FastAPI application source code."""
    return f'''\
"""Haute scoring API - auto-generated by ``haute deploy``."""

import json
import logging
import tempfile
import uuid
from pathlib import Path

import polars as pl
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from haute._execution_admission import (
    ExecutionAdmissionError,
)
from haute._execution_context import ExecutionCancelledError, ExecutionMemoryLimitExceededError
from haute._output_assembler import render_output_document
from haute._polars_utils import bounded_collect_batches, streaming_collect
from haute._types import PipelineGraph
from haute.errors import HauteError, BoundedMemoryUnsupportedError, is_public_contract_error
from haute.deploy._request_limits import (
    RequestBodyHeaderError,
    RequestBodyLimitError,
    RequestBodyParseError,
    deploy_quote_request_body_limit_bytes,
    read_limited_json_body,
)
from haute._worker_isolation import (
    IsolatedWorkerCrashedError,
    IsolatedWorkerError,
    IsolatedWorkerMemoryLimitExceededError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerTimeoutError,
    isolated_worker_failure_is_memory,
    isolated_worker_memory_detail,
    process_memory_caps_supported,
    resolve_worker_memory_enforcement,
)
from haute.deploy._batch_scoring import (
    BatchScoreCleanupError,
    BatchScoreError,
    accept_batch_outcome,
    deploy_batch_timeout_seconds,
    prepare_batch_scoring,
    score_batch_worker,
)
from haute.deploy._request_frame import frame_rows, request_frame_schema
from haute.deploy._request_rows import RequestRows
from haute.deploy._response_json import ndjson_rows, response_json
from haute.deploy._scorer import (
    QuoteRequest,
    admit_deploy_execution,
    reads_records,
    score_graph,
    score_graph_lazy,
)
from haute._project_context import ProjectContext, ProjectContextMiddleware
from haute.routes._isolated_worker_async import run_isolated_worker_async

# ── Load manifest at startup ────────────────────────────────────────

_MANIFEST_PATH = Path(__file__).parent / "deploy_manifest.json"
_PROJECT = ProjectContext.at(Path(__file__).parent)
_manifest = json.loads(_MANIFEST_PATH.read_text())

_pruned_graph = PipelineGraph.model_validate(_manifest["pruned_graph"])
_input_node_ids = _manifest["input_node_ids"]
# A Workbench Input, and a Quote Input with tables, read the quotes from the records as
# sent, typing each value themselves; any other request input takes the rows as a frame.
_reads_quotes = reads_records(_pruned_graph, _input_node_ids)
# A flat Quote Input's rows are typed by the schema its sample recorded, never inferred.
_request_schema = {{}} if _reads_quotes else request_frame_schema(_manifest["input_schema"])
# The columns a quote may leave out: its exposures, priced as a full policy term.
_omittable = frozenset() if _reads_quotes else frozenset(_manifest["omittable_inputs"])
_output_node_id = _manifest["output_node_id"]
# Whether a request of several quotes is answered in request order, or refused.
_request_rows = RequestRows.from_manifest(_manifest["request_rows"])


def _resolve_manifest_artifact_path(raw_path):
    path = Path(raw_path)
    if not path.is_absolute():
        path = _MANIFEST_PATH.parent / path
    return str(path.resolve())


_artifact_paths = {{
    name: _resolve_manifest_artifact_path(path)
    for name, path in _manifest["artifacts"].items()
}}
_output_fields = _manifest.get("output_fields")
_execution_policy = _manifest.get("execution_policy") or {{}}


def _require_fail_closed_batch_enforcement(policy):
    """Refuse to start when a cap-dependent policy has no enforced cap.

    A ``warned`` / ``full-width-conservative`` policy is a promise the bundle
    could only make because the batch worker runs under a hard memory cap. In
    ``best_effort`` mode a host that cannot install the cap silently starts a
    child with no native backend, where the planner rejects the unavailable
    estimate on every batch request. Failing at startup names the real problem
    once instead of turning every batch into an unexplained 422.
    """
    if policy.get("status") != "warned":
        return
    enforcement = resolve_worker_memory_enforcement()
    if enforcement == "required":
        if process_memory_caps_supported():
            return
        raise RuntimeError(
            "This deployment's batch execution policy is "
            f"{{policy.get('status')!r}} / {{policy.get('strategy')!r}} "
            f"({{policy.get('reason_code')!r}}) at node "
            f"{{policy.get('blocking_node_id')!r}} (operator "
            f"{{policy.get('blocking_operator')!r}}), which is only valid while the "
            "batch worker runs under an enforced hard memory cap. This host "
            "cannot install a native memory cap, so HAUTE_WORKER_MEMORY_ENFORCEMENT"
            "=required would fail every batch request. Serve this image on a host "
            "that supports native memory caps."
        )
    raise RuntimeError(
        "This deployment's batch execution policy is "
        f"{{policy.get('status')!r}} / {{policy.get('strategy')!r}} "
        f"({{policy.get('reason_code')!r}}) at node "
        f"{{policy.get('blocking_node_id')!r}} (operator "
        f"{{policy.get('blocking_operator')!r}}), which is only valid while the "
        "batch worker runs under an enforced hard memory cap. This host is "
        f"configured as HAUTE_WORKER_MEMORY_ENFORCEMENT={{enforcement}}. Set "
        "HAUTE_WORKER_MEMORY_ENFORCEMENT=required (on a host that supports "
        "native memory caps) before serving this image."
    )


_require_fail_closed_batch_enforcement(_execution_policy)
_QUOTE_REQUEST_BODY_LIMIT_BYTES = deploy_quote_request_body_limit_bytes()
_QUOTE_RESPONSE_ROW_LIMIT = {DEFAULT_QUOTE_RESPONSE_ROW_LIMIT}
_DEPLOY_STREAM_CHUNK_SIZE = 50_000
_DEPLOY_STREAM_SPOOL_MAX_SIZE = 8 * 1024 * 1024
_DEPLOY_STREAM_READ_SIZE = 64 * 1024
logger = logging.getLogger("haute.deploy.container")

app = FastAPI(
    title="{model_name}",
    description="Pricing API - auto-generated by Haute",
    version=_manifest.get("haute_version", "0.0.0"),
)

#: A 500's message. The failure's detail (a path, an internal name) stays in the
#: service's log, under the request id the envelope names.
_INTERNAL_ERROR_MESSAGE = (
    "The quote could not be scored. The service's log holds the detail under this request_id."
)


@app.middleware("http")
async def _request_id(request: Request, call_next):
    """Give each request an id, sent back as X-Request-ID, that its log lines carry.

    A failure no route handled is answered here as every other 500 is, naming the id.
    """
    request.state.request_id = uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        response = _internal_error(request, "deploy_request_failed")
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _internal_error(request: Request, event: str) -> JSONResponse:
    """Log the failure being handled under the request's id and answer a 500 naming it.

    Never the exception's text: it can carry file paths and internal identifiers.
    """
    request_id = request.state.request_id
    logger.exception("%s request_id=%s", event, request_id)
    return JSONResponse(
        status_code=500,
        content={{
            "error_code": "deploy_internal_error",
            "error": _INTERNAL_ERROR_MESSAGE,
            "request_id": request_id,
        }},
    )


@app.get("/health")
def health() -> dict:
    """Liveness / readiness check."""
    return {{
        "status": "ok",
        "model": _manifest.get("pipeline_name", "{model_name}"),
        "version": _manifest.get("haute_version", "unknown"),
        "nodes_deployed": _manifest.get("nodes_deployed", 0),
        "input_schema": _manifest.get("input_schema", {{}}),
        "output_schema": _manifest.get("output_schema", {{}}),
        # Describes single-row live scoring, which runs in this process.
        "memory_enforcement": "admission_rss_best_effort",
        # Multi-row batches run in a spawn worker with a hard RSS cap.
        "batch_memory_enforcement": resolve_worker_memory_enforcement(),
        "execution_policy": _manifest.get("execution_policy"),
        "request_rows": _manifest["request_rows"],
    }}


class _DocumentResponse(JSONResponse):
    """The JSON envelope, its rows encoded as NDJSON encodes them (``response_json``)."""

    def render(self, content) -> bytes:
        return response_json(content)


def _quote_response_content(result: pl.DataFrame, execution_context):
    row_count = result.height
    returned_rows = min(row_count, _QUOTE_RESPONSE_ROW_LIMIT)
    # ``result`` is the OUTPUT node's assembled document (struct columns,
    # ragged → null-filled). Render it as the pruned JSON so the deployed API
    # returns the real response shape; a no-op for a flat OUTPUT.
    return {{
        "rows": render_output_document(result.head(returned_rows)),
        "row_count": row_count,
        "returned_rows": returned_rows,
        "truncated": row_count > _QUOTE_RESPONSE_ROW_LIMIT,
        "limit": _QUOTE_RESPONSE_ROW_LIMIT,
        "execution_metrics": execution_context.metrics_payload(status="completed"),
    }}


def _wants_ndjson(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    return "application/x-ndjson" in accept or "application/ndjson" in accept


def _materialize_quote_ndjson(plan):
    spool = tempfile.SpooledTemporaryFile(
        max_size=_DEPLOY_STREAM_SPOOL_MAX_SIZE,
        mode="w+b",
    )
    preserve_primary_error = False
    try:
        for batch in bounded_collect_batches(
            plan.lazy_frame,
            chunk_size=_DEPLOY_STREAM_CHUNK_SIZE,
            maintain_order=True,
            execution_context=plan.execution_context,
            stage_name="deploy_stream_batch",
            node_id=_output_node_id,
        ):
            # The rows the JSON envelope renders, one line each: the same document
            # whichever the client asks for.
            spool.write(ndjson_rows(batch))
        spool.seek(0)
        return spool
    except BaseException:
        preserve_primary_error = True
        spool.close()
        raise
    finally:
        plan.cleanup(preserve_primary_error=preserve_primary_error)


def _quote_ndjson_chunks(spool):
    try:
        while chunk := spool.read(_DEPLOY_STREAM_READ_SIZE):
            yield chunk
    finally:
        spool.close()


def _batch_response_content(result):
    row_count = result.row_count
    returned_rows = min(row_count, _QUOTE_RESPONSE_ROW_LIMIT)
    frame = streaming_collect(pl.scan_parquet(result.result_path).head(returned_rows))
    return {{
        "rows": render_output_document(frame),
        "row_count": row_count,
        "returned_rows": returned_rows,
        "truncated": row_count > _QUOTE_RESPONSE_ROW_LIMIT,
        "limit": _QUOTE_RESPONSE_ROW_LIMIT,
        "execution_metrics": result.execution_metrics,
    }}


def _materialize_batch_ndjson(plan, result):
    spool = tempfile.SpooledTemporaryFile(
        max_size=_DEPLOY_STREAM_SPOOL_MAX_SIZE,
        mode="w+b",
    )
    try:
        for batch in bounded_collect_batches(
            pl.scan_parquet(result.result_path),
            chunk_size=_DEPLOY_STREAM_CHUNK_SIZE,
            maintain_order=True,
            execution_context=plan.execution_context,
            stage_name="deploy_batch_stream",
            node_id=_output_node_id,
        ):
            # The rows the JSON envelope renders, one line each: the same document
            # whichever the client asks for.
            spool.write(ndjson_rows(batch))
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _batch_error_response(request, exc):
    if exc.kind in {{"contract", "bounded"}}:
        if exc.payload is not None:
            return JSONResponse(status_code=422, content=exc.payload)
        return JSONResponse(
            status_code=422,
            content={{
                "error_code": "bounded_streaming_unsupported",
                "error": "Bounded streaming unsupported",
                "detail": exc.detail,
            }},
        )
    if exc.kind == "memory":
        return JSONResponse(status_code=507, content=exc.payload)
    if exc.kind == "cancelled":
        return JSONResponse(
            status_code=499,
            content={{
                "error_code": "execution_cancelled",
                "operation": "deploy_quote",
                "job_id": None,
                "reason": exc.detail,
            }},
        )
    return _internal_error(request, "deploy_quote_batch_failed")


async def _quote_batch(request: Request, rows: list):
    """Score a multi-row request inside one hard-capped spawn worker."""
    plan = None
    response = None
    # Only an UNHANDLED failure counts as a primary error. A handled branch has
    # already produced its response, so a cleanup failure there is the only
    # unreported problem left and must win.
    primary_error = None
    try:
        plan = await run_in_threadpool(
            prepare_batch_scoring,
            rows,
            graph=_pruned_graph,
            input_node_ids=_input_node_ids,
            output_node_id=_output_node_id,
            artifact_paths=_artifact_paths,
            output_fields=_output_fields,
            input_schema=None if _reads_quotes else _request_schema,
            request_rows=_request_rows,
            omittable_inputs=_omittable,
        )
        outcome = await run_isolated_worker_async(
            score_batch_worker,
            plan.request,
            plan.budget,
            config=plan.worker_config,
        )
        result = accept_batch_outcome(plan, outcome)
        if _wants_ndjson(request):
            # The spool is fully materialised here, so removing the parquet in
            # the finally below cannot truncate the streamed body.
            spool = await run_in_threadpool(_materialize_batch_ndjson, plan, result)
            response = StreamingResponse(
                _quote_ndjson_chunks(spool),
                media_type="application/x-ndjson",
            )
        else:
            response = _DocumentResponse(
                content=await run_in_threadpool(_batch_response_content, result)
            )
    except ExecutionAdmissionError as exc:
        response = JSONResponse(status_code=507, content=exc.to_payload())
    except ExecutionCancelledError as exc:
        response = JSONResponse(
            status_code=499,
            content={{
                "error_code": "execution_cancelled",
                "operation": exc.operation,
                "job_id": exc.job_id,
                "reason": str(exc),
            }},
        )
    except ExecutionMemoryLimitExceededError as exc:
        response = JSONResponse(status_code=507, content=exc.to_payload())
    except BoundedMemoryUnsupportedError as exc:
        if is_public_contract_error(exc):
            response = JSONResponse(status_code=422, content=exc.to_payload())
        else:
            response = JSONResponse(
                status_code=422,
                content={{
                    "error_code": "bounded_streaming_unsupported",
                    "error": "Bounded streaming unsupported",
                    "detail": str(exc),
                }},
            )
    except HauteError as exc:
        if is_public_contract_error(exc):
            response = JSONResponse(status_code=422, content=exc.to_payload())
        else:
            response = _internal_error(request, "deploy_quote_batch_failed")
    except BatchScoreError as exc:
        response = _batch_error_response(request, exc)
    except (
        IsolatedWorkerMemoryLimitExceededError,
        IsolatedWorkerMemoryLimitUnsupportedError,
    ) as exc:
        response = JSONResponse(
            status_code=507,
            content=isolated_worker_memory_detail(
                exc,
                operation="deploy_quote",
                memory_limit_bytes=plan.budget.memory_limit_bytes,
            ),
        )
    except IsolatedWorkerTimeoutError:
        logger.exception("deploy_quote_batch_timed_out")
        response = JSONResponse(
            status_code=504,
            content={{
                "error_code": "deploy_batch_timeout",
                "operation": "deploy_quote",
                "timeout_seconds": deploy_batch_timeout_seconds(),
            }},
        )
    except (IsolatedWorkerCrashedError, IsolatedWorkerRemoteError) as exc:
        if isolated_worker_failure_is_memory(exc):
            response = JSONResponse(
                status_code=507,
                content=isolated_worker_memory_detail(
                    exc,
                    operation="deploy_quote",
                    memory_limit_bytes=plan.budget.memory_limit_bytes,
                ),
            )
        else:
            response = _internal_error(request, "deploy_quote_batch_failed")
    except IsolatedWorkerError:
        response = _internal_error(request, "deploy_quote_batch_failed")
    except Exception:
        # Same contract as the live path: an unexpected parent-side failure is
        # logged and reported as the internal-error envelope, never a bare 500.
        response = _internal_error(request, "deploy_quote_batch_failed")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if plan is not None:
            try:
                plan.cleanup(primary_error=primary_error)
            except BatchScoreCleanupError:
                # The request rows and the scored parquet are still on disk;
                # that is a data-retention defect even after a good score.
                response = _internal_error(request, "deploy_quote_batch_cleanup_failed")
    return response


@app.post("/quote")
async def quote(request: Request) -> JSONResponse:
    """Score one or more quotes.

    Accepts a JSON object (single quote) or JSON array (batch).
    Returns a stable JSON object with rows, row_count, returned_rows,
    truncated, and limit fields.
    """
    try:
        body = await read_limited_json_body(
            request,
            operation="deploy_quote",
            limit_bytes=_QUOTE_REQUEST_BODY_LIMIT_BYTES,
        )
    except RequestBodyLimitError as exc:
        return JSONResponse(status_code=413, content=exc.to_payload())
    except RequestBodyHeaderError as exc:
        return JSONResponse(status_code=400, content=exc.to_payload())
    except RequestBodyParseError as exc:
        return JSONResponse(status_code=422, content=exc.to_payload())

    if isinstance(body, dict):
        rows = [body]
    elif isinstance(body, list):
        rows = body
    else:
        return JSONResponse(
            status_code=400,
            content={{"error": "Expected a JSON object or array of objects."}},
        )

    # Multi-row batches are the only request shape that can materialise, so
    # they run in a hard-capped worker instead of this service process.
    if len(rows) > 1:
        return await _quote_batch(request, rows)

    # The body is size-capped, so a single quote is framed before anything is admitted:
    # a value its column does not hold is refused without holding a context.
    try:
        input_df = (
            QuoteRequest(tuple(rows))
            if _reads_quotes
            else frame_rows(rows, _request_schema, omittable=_omittable)
        )
    except HauteError as exc:
        if is_public_contract_error(exc):
            return JSONResponse(status_code=422, content=exc.to_payload())
        raise

    try:
        row_count = len(rows)
        execution_context = admit_deploy_execution(
            operation="deploy_quote",
            row_count=row_count,
        )
        if _wants_ndjson(request):
            plan = score_graph_lazy(
                graph=_pruned_graph,
                input_df=input_df,
                input_node_ids=_input_node_ids,
                output_node_id=_output_node_id,
                artifact_paths=_artifact_paths,
                output_fields=_output_fields,
                execution_context=execution_context,
                request_rows=_request_rows,
            )
            spool = await run_in_threadpool(_materialize_quote_ndjson, plan)
            return StreamingResponse(
                _quote_ndjson_chunks(spool),
                media_type="application/x-ndjson",
            )
        result = score_graph(
            graph=_pruned_graph,
            input_df=input_df,
            input_node_ids=_input_node_ids,
            output_node_id=_output_node_id,
            artifact_paths=_artifact_paths,
            output_fields=_output_fields,
            execution_context=execution_context,
            request_rows=_request_rows,
        )
        return _DocumentResponse(content=_quote_response_content(result, execution_context))
    except ExecutionAdmissionError as exc:
        return JSONResponse(status_code=507, content=exc.to_payload())
    except ExecutionCancelledError as exc:
        return JSONResponse(
            status_code=499,
            content={{
                "error_code": "execution_cancelled",
                "operation": exc.operation,
                "job_id": exc.job_id,
                "reason": str(exc),
            }},
        )
    except ExecutionMemoryLimitExceededError as exc:
        return JSONResponse(status_code=507, content=exc.to_payload())
    except BoundedMemoryUnsupportedError as exc:
        if is_public_contract_error(exc):
            return JSONResponse(status_code=422, content=exc.to_payload())
        return JSONResponse(
            status_code=422,
            content={{
                "error_code": "bounded_streaming_unsupported",
                "error": "Bounded streaming unsupported",
                "detail": str(exc),
            }},
        )
    except HauteError as exc:
        if is_public_contract_error(exc):
            return JSONResponse(status_code=422, content=exc.to_payload())
        return _internal_error(request, "deploy_quote_failed")
    except Exception:
        return _internal_error(request, "deploy_quote_failed")


# Added last, so it is outermost: every request runs in the bundle's project, the
# folder this app ships in, whatever the working directory the image starts in.
app.add_middleware(ProjectContextMiddleware, context=_PROJECT)
'''


# ── Dockerfile generation ───────────────────────────────────────────


#: The image's steps installing, from the base image's own distribution, the system libraries
#: the ODBC Driver 18 inside mssql-python's wheels loads (libltdl, and Kerberos's for its
#: sign-ins), which the quote store's ``sqlserver`` provider connects through: the driver
#: itself comes with the package, so no repository of Microsoft's is added.
SQLSERVER_ODBC_STEPS = r"""# The system libraries the ODBC driver inside mssql-python
# loads, which the quote store's sqlserver provider connects through.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libltdl7 libkrb5-3 libgssapi-krb5-2 \
 && rm -rf /var/lib/apt/lists/*

"""


def _generate_dockerfile(
    base_image: str,
    port: int,
    resolved: ResolvedDeploy,
    *,
    wheel_name: str | None = None,
    extra_files: Sequence[str] = (),
    sqlserver_odbc: bool = False,
) -> str:
    """Generate a Dockerfile for the scoring container, copying *extra_files* beside app.py, and
    the libraries SQL Server's driver loads first when *sqlserver_odbc*
    (:data:`SQLSERVER_ODBC_STEPS`). It installs the bundle's locks (``prepare_build_directory``)
    with ``--require-hashes``, never resolving anything, and then checks the installed
    runtime against them (``haute.deploy._runtime_check``)."""
    copy_wheel = f"COPY {wheel_name} .\n" if wheel_name else ""
    utility = resolved.project_modules.utility
    if utility is None:
        copy_utility = ""
    elif utility.is_dir():
        copy_utility = f"COPY {UTILITY_PACKAGE}/ {UTILITY_PACKAGE}/\n"
    else:
        copy_utility = f"COPY {UTILITY_PACKAGE}.py .\n"
    copy_extra = "".join(f"COPY {name} .\n" for name in extra_files)
    polars_settings = polars_process_environment(polars_spill_root(PurePosixPath("/app")))
    polars_env = "".join(f"ENV {name}={value}\n" for name, value in sorted(polars_settings.items()))
    odbc_steps = SQLSERVER_ODBC_STEPS if sqlserver_odbc else ""

    return f"""\
FROM {base_image}

WORKDIR /app

# Select fixed server budgets explicitly. This remains admission/RSS
# enforcement; the hosting platform owns any outer hard container cap.
ENV HAUTE_EXECUTION_MEMORY_POLICY=strict_server

# Keep Polars' out-of-core spilling off and inside the container's storage,
# and load an unknown Arrow extension column as its storage type.
{polars_env}
{odbc_steps}# Install the locked scoring runtime, every distribution pinned by version and hash
# and resolved for this image's Python and platform when the bundle was built, then
# haute itself without its dependencies: they include the editor, assistant, training
# and tuning stacks, which a scoring container never runs. Then check the installed
# runtime against the lock.
COPY {RUNTIME_LOCK_FILE} {HAUTE_LOCK_FILE} {LOCK_RECORD_FILE} ./
RUN pip install --no-cache-dir --no-deps --require-hashes --only-binary :all: \\
    -r {RUNTIME_LOCK_FILE}
{copy_wheel}RUN pip install --no-cache-dir --no-deps --require-hashes -r {HAUTE_LOCK_FILE}
RUN python -m haute.deploy._runtime_check {LOCK_RECORD_FILE}

# Copy application code and artifacts
COPY deploy_manifest.json .
COPY app.py .
{copy_extra}COPY artifacts/ artifacts/
{copy_utility}
EXPOSE {port}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "{port}"]
"""


def container_python_version(resolved: ResolvedDeploy) -> str:
    """The Python a scoring image runs: its official ``python`` base image's, else the
    project's (``deployed_python_version``), which resolution keeps the image in step with."""
    from haute._project_python import base_image_python_version, deployed_python_version

    return base_image_python_version(
        resolved.config.container.base_image
    ) or deployed_python_version(resolved.project_root)


def haute_constraints() -> list[str]:
    """haute's own requirements as constraints for a scoring image's lock: haute is
    installed without its dependencies beside it, so whatever the lock holds must be a
    version haute admits. Extras and the requirements of haute's own extras are left out."""
    from importlib import metadata

    from packaging.requirements import Requirement

    constraints: list[str] = []
    for line in metadata.distribution("haute").requires or ():
        requirement = Requirement(line)
        if requirement.marker is not None and "extra" in str(requirement.marker):
            continue
        requirement.extras = set()
        constraints.append(str(requirement))
    return constraints


def _haute_lock(haute_root: str, wheel_path: Path | None, python_version: str) -> str:
    """The lock haute itself installs from, without its dependencies: the bundled wheel by
    its digest, or the index release resolved alone, hashed."""
    if wheel_path is not None:
        digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        return f"./{wheel_path.name} --hash=sha256:{digest}\n"
    return lock_requirements(
        [haute_root], python_version=python_version, platform=TARGET_PLATFORM, no_deps=True
    ).requirements


def _pinned_dockerfile_deps(
    resolved: ResolvedDeploy,
    *,
    haute_requirement: str | None = None,
) -> list[str]:
    """Every ``pip install`` requirement of the scoring container, pinned: ``haute`` first.

    Scoring runtime and model runtime alike are pinned to the version installed
    in the deploying environment: the container unpickles the model, and a
    model loaded under a different scikit-learn (or LightGBM, ...) than the
    one that wrote it is silently wrong premiums, not an error.
    """
    haute = (
        haute_requirement
        if haute_requirement is not None
        else _pinned_dockerfile_dependency("haute", "haute")
    )
    return [
        haute,
        *(
            _pinned_price_contour_dependency()
            if distribution_name == PRICE_CONTOUR_DISTRIBUTION
            else _pinned_dockerfile_dependency(distribution_name, install_name)
            for distribution_name, install_name in _SCORING_RUNTIME_DEPENDENCIES
        ),
        *_pinned_graph_dockerfile_deps(resolved),
        *_pinned_extra_dockerfile_deps(resolved),
    ]


def runtime_versions(resolved: ResolvedDeploy) -> list[str]:
    """The runtime the served pipeline prices on, as installed here: each distribution of
    the scoring runtime, the graph's and the artifacts' own, ``name==version``, sorted; what a
    container image pins, but ``haute`` itself, read for any build, so two builds of a
    project on one runtime agree and another runtime is told apart."""
    return sorted(
        {
            *(
                _pinned_dockerfile_dependency(distribution_name, distribution_name)
                for distribution_name, _ in _SCORING_RUNTIME_DEPENDENCIES
            ),
            *_pinned_graph_dockerfile_deps(resolved),
            *_pinned_extra_dockerfile_deps(resolved),
        }
    )


def _pinned_graph_dockerfile_deps(resolved: ResolvedDeploy) -> list[str]:
    """Pin the runtime the served graph needs beyond the scoring runtime.

    An optimiser apply sourced from an MLflow run or registered model loads its
    artefact from MLflow when the container runs (such artefacts are not
    bundled), so the image needs ``mlflow``.
    """
    nodes = [
        node.id
        for node in resolved.pruned_graph.nodes
        if DEPLOY_ARTIFACTS[node.data.nodeType] == "optimiser_result"
        and node.data.config.get("sourceType") in {"run", "registered"}
    ]
    if not nodes:
        return []
    return [_pinned_dockerfile_dependency("mlflow", "mlflow", required_by=sorted(nodes))]


def _pinned_extra_dockerfile_deps(resolved: ResolvedDeploy) -> list[str]:
    """Pin the model-runtime packages the artifacts need (sorted by package)."""
    return [
        _pinned_dockerfile_dependency(dependency, dependency, required_by=artifact_names)
        for dependency, artifact_names in _extra_deps_by_artifact(resolved).items()
    ]


def _pinned_dockerfile_dependency(
    distribution_name: str,
    install_name: str,
    *,
    required_by: list[str] | None = None,
) -> str:
    try:
        package_version = metadata_version(distribution_name)
    except PackageNotFoundError as exc:
        needed_by = f" (needed by {', '.join(required_by)})" if required_by else ""
        raise DeployError(
            f"Cannot pin dependency {install_name!r}{needed_by}: "
            f"installed distribution {distribution_name!r} was not found. A "
            f"deployment is pinned to the versions installed in the deploying "
            f"environment, so install {distribution_name!r} here and re-run."
        ) from exc
    return f"{install_name}=={package_version}"


def _pinned_price_contour_dependency() -> str:
    """Pin the verified price-contour; refuse a build the container cannot reproduce.

    The container installs ``price-contour==<version>`` from the package index,
    so the deploying environment must run a released build: an editable
    checkout or a direct-URL install of the same version number may hold
    different solver code, and the deployed prices would silently differ from
    the ones reviewed here.
    """
    try:
        install = price_contour_install()
    except PriceContourCompatibilityError as exc:
        raise DeployError(f"Cannot pin price-contour for the scoring container.\n{exc}") from exc
    if install.kind != "wheel":
        raise DeployError(
            f"Cannot pin price-contour for the scoring container: version {install.version} "
            f"is installed from {install.description}, which the container cannot "
            "reproduce from the package index. Install the released build with "
            "`uv sync --locked` and deploy again."
        )
    return f"{PRICE_CONTOUR_DISTRIBUTION}=={install.version}"


# A pickle or joblib artifact may hold an object of any third-party package
# haute's restricted unpickler allows (``_sandbox._ALLOWED_PICKLE_CLASSES`` and
# ``_ALLOWED_PICKLE_GLOBALS``) beyond the scoring runtime, so the image installs
# all of them, as a full haute install would.
_PICKLE_RUNTIME_DEPENDENCIES: tuple[str, ...] = (
    "catboost",
    "interpret-core",
    "pandas",
    "scikit-learn",
)

# Load File artifact extension -> distribution names of the runtime that
# loads it. A Model Scoring artifact's runtime is its registered model
# family's ``distributions`` instead. Every entry is also the ``pip install``
# name, and every entry is pinned through ``_pinned_dockerfile_dependency``:
# haute is installed without its own dependencies, so each engine comes from
# here or from the family registry.
_LOAD_FILE_EXT_TO_DEPS: dict[str, tuple[str, ...]] = {
    ".pkl": _PICKLE_RUNTIME_DEPENDENCIES,
    ".pickle": _PICKLE_RUNTIME_DEPENDENCIES,
    ".joblib": _PICKLE_RUNTIME_DEPENDENCIES,
    ".lgb": ("lightgbm",),
    ".xgb": (XGBOOST_DISTRIBUTION,),
    ".onnx": ("onnxruntime",),
}


def _extra_deps_by_artifact(resolved: ResolvedDeploy) -> dict[str, list[str]]:
    """Map each model-runtime package to the artifact names that need it.

    Only unambiguous model extensions are matched.  Generic extensions
    like ``.txt`` and ``.json`` are deliberately excluded because they
    would cause false positives (e.g. ``deploy_manifest.json``).  Both
    levels are sorted so the generated Dockerfile is byte-stable.
    """
    needed: dict[str, list[str]] = {}
    for artifact_name in sorted(resolved.artifacts):
        suffix = Path(artifact_name).suffix.lower()
        family = family_for_suffix(suffix)
        dependencies = (
            family.distributions if family is not None else _LOAD_FILE_EXT_TO_DEPS.get(suffix, ())
        )
        for dependency in dependencies:
            needed.setdefault(dependency, []).append(artifact_name)
    return dict(sorted(needed.items()))


# ── Docker build / push ─────────────────────────────────────────────


def _check_docker_available() -> None:
    """Raise DeployError if Docker is not available."""
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise DeployError(
            "Docker is not available. `haute deploy` for container targets "
            "is designed to run in CI (where Docker is pre-installed), not "
            "locally. Push your changes and let the CI pipeline build the image."
        ) from exc


def _docker_build(build_dir: Path, image_tag: str) -> None:
    """Build a Docker image from the build directory."""
    result = subprocess.run(
        ["docker", "build", "--platform", DOCKER_PLATFORM, "-t", image_tag, "."],
        cwd=build_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise DeployError(f"Docker build failed:\n{result.stderr}")


def _docker_push(image_tag: str) -> None:
    """Push a Docker image to a registry."""
    result = subprocess.run(
        ["docker", "push", image_tag],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise DeployError(f"Docker push failed:\n{result.stderr}")


def _docker_repo_digest(image_tag: str) -> str:
    """The reference by digest, ``<repository>@sha256:...``, that the registry holds for
    *image_tag* once pushed: the one of ``docker image inspect``'s ``RepoDigests`` naming the
    tag's repository. *image_tag* is ``<repository>:<tag>``, its tag holding no ``/``."""
    repository = image_tag[: image_tag.rindex(":")]
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_tag],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise DeployError(f"Docker could not inspect {image_tag}:\n{result.stderr}")
    digests = json.loads(result.stdout) or []
    for digest in digests:
        if digest.startswith(f"{repository}@"):
            return str(digest)
    raise DeployError(
        f"Docker holds no digest of {repository} for {image_tag}: the push recorded none."
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _git_sha_short() -> str:
    """Get the short git SHA of HEAD, or 'local' without git or outside a repo."""
    if not git_binary_available():
        return "local"
    ok, sha = _run_git_ok("rev-parse", "--short", "HEAD")
    return sha if ok else "local"
