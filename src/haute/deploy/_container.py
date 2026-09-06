"""Container deployment target - generate FastAPI app + Dockerfile, build, push."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path

from haute._logging import get_logger
from haute.deploy._config import ResolvedDeploy
from haute.deploy._mlflow import DeployResult
from haute.deploy._request_limits import (
    DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES,
)
from haute.deploy._utils import build_manifest
from haute.errors import DeployError

logger = get_logger(component="deploy.container")

_VALID_BASE_IMAGE_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")
_VALID_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
DEFAULT_QUOTE_REQUEST_BODY_LIMIT_BYTES = DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES
DEFAULT_QUOTE_RESPONSE_ROW_LIMIT = 1_000
_CORE_DOCKERFILE_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("haute", "haute"),
    ("polars", "polars"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
)


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
    manifest_path: Path
    build_dir: Path
    model_name: str
    model_version: int


def prepare_build_directory(
    resolved: ResolvedDeploy,
    build_dir: Path,
    *,
    haute_requirement: str | None = None,
) -> Path:
    """Prepare the container build directory with manifest, artifacts, app.py, and Dockerfile.

    Steps:
        1. Create build directory and artifacts subdirectory
        2. Build deployment manifest JSON
        3. Copy artifacts into build directory
        4. Generate FastAPI app source
        5. Generate Dockerfile (and copy wheel if haute_requirement is a wheel path)

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        build_dir: Destination directory for the build artefacts.
        haute_requirement: Optional override for the ``haute`` dependency in the Dockerfile.
            When it names a local wheel file (*.whl), the wheel is copied into ``build_dir``
            and the Dockerfile gains ``COPY <wheel name> .`` before ``pip install ./<wheel name>``.

    Returns:
        Path to the written ``deploy_manifest.json``.
    """
    config = resolved.config
    model_name = config.model_name
    ct = config.container

    _validate_base_image(ct.base_image)
    _validate_model_name(model_name)

    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = build_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    manifest = build_manifest(resolved)
    container_artifacts: dict[str, str] = {}
    for artifact_name in resolved.artifacts:
        container_artifacts[artifact_name] = f"artifacts/{artifact_name}"

    wheel_name: str | None = None
    haute_pip_dep: str | None = None
    if haute_requirement is not None:
        if haute_requirement.lower().endswith(".whl"):
            wheel_path = Path(haute_requirement)
            wheel_name = wheel_path.name
            dest_wheel = build_dir / wheel_name
            if wheel_path.resolve() != dest_wheel.resolve():
                shutil.copy2(wheel_path, dest_wheel)
            haute_pip_dep = f"./{wheel_name}"
        else:
            haute_pip_dep = haute_requirement

    manifest["artifacts"] = container_artifacts
    manifest["container_dependencies"] = _pinned_dockerfile_deps(
        resolved,
        haute_requirement=haute_pip_dep,
    )

    manifest_path = build_dir / "deploy_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for artifact_name, artifact_path in resolved.artifacts.items():
        dest = artifacts_dir / artifact_name
        shutil.copy2(artifact_path, dest)

    app_source = _generate_app_source(config.model_name, ct.port)
    (build_dir / "app.py").write_text(app_source, encoding="utf-8")

    dockerfile = _generate_dockerfile(
        ct.base_image,
        ct.port,
        resolved,
        haute_pip_dep=haute_pip_dep,
        wheel_name=wheel_name,
    )
    (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    return manifest_path


def build_and_push_image(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
) -> ContainerBuildResult:
    """Build a Docker image from a resolved pipeline - shared by all container targets.

    Steps:
        1. Build deployment manifest JSON
        2. Generate FastAPI app source
        3. Copy artifacts into build directory
        4. Generate Dockerfile
        5. Build Docker image
        6. Push to registry (if configured)

    Args:
        resolved: Fully resolved deployment config (from ``resolve_config()``).
        progress: Optional callback for step-by-step progress messages.

    Returns:
        ContainerBuildResult with image tag and paths for the caller.
    """

    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    config = resolved.config
    model_name = config.model_name
    ct = config.container

    _validate_base_image(ct.base_image)
    _validate_model_name(model_name)

    # 1. Create build directory
    build_dir = Path.cwd() / ".haute_build"
    build_dir.mkdir(exist_ok=True)

    try:
        _log("Building deployment manifest...")
        manifest_path = prepare_build_directory(resolved, build_dir)
        _log(f"  Manifest: {manifest_path}")
        _log(f"Copying {len(resolved.artifacts)} artifacts...")
        _log("Generating FastAPI app...")
        _log("Generating Dockerfile...")

        # 6. Determine image tag
        git_sha = _git_sha_short()
        version = _next_version()
        if ct.registry:
            image_tag = f"{ct.registry.rstrip('/')}/{model_name}:{git_sha}"
        else:
            image_tag = f"{model_name}:{git_sha}"

        # 7. Build Docker image
        _log(f"Building Docker image: {image_tag}")
        _check_docker_available()
        _docker_build(build_dir, image_tag)
        _log(f"  ✓ Image built: {image_tag}")

        # 8. Push if registry is configured
        if ct.registry:
            _log(f"Pushing to registry: {ct.registry}")
            _docker_push(image_tag)
            _log(f"  ✓ Image pushed: {image_tag}")
        else:
            _log("  No registry configured - image is local only.")

        return ContainerBuildResult(
            image_tag=image_tag,
            manifest_path=manifest_path,
            build_dir=build_dir,
            model_name=model_name,
            model_version=version,
        )
    except BaseException:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise


def deploy_to_container(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
) -> DeployResult:
    """Generic container target - build and push only, no service update.

    Use this for local testing or when IT manages the service separately.
    For managed platform targets (Azure Container Apps, AWS ECS, GCP Cloud
    Run), use ``deploy_to_platform_container()`` instead.
    """
    result = build_and_push_image(resolved, progress)
    return DeployResult(
        model_name=result.model_name,
        model_version=result.model_version,
        model_uri=result.image_tag,
        endpoint_url=None,
        manifest_path=result.manifest_path,
    )


def deploy_to_platform_container(
    resolved: ResolvedDeploy,
    progress: Callable[[str], None] | None = None,
) -> DeployResult:
    """Platform container target - build, push, then update the running service.

    Shared entry point for azure-container-apps, aws-ecs, gcp-run.
    After building and pushing the image, calls the platform-specific
    SDK to create a new revision / update the service.
    """
    result = build_and_push_image(resolved, progress)

    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    target = resolved.config.target
    _log(f"Updating service on {target}...")
    endpoint_url = _update_service(target, result.image_tag, resolved)
    _log(f"  ✓ Service updated: {endpoint_url or '(no URL returned)'}")

    return DeployResult(
        model_name=result.model_name,
        model_version=result.model_version,
        model_uri=result.image_tag,
        endpoint_url=endpoint_url,
        manifest_path=result.manifest_path,
    )


def _update_service(
    target: str,
    image_tag: str,
    resolved: ResolvedDeploy,
) -> str | None:
    """Call the platform SDK to update the running service with the new image.

    Each platform target will have its own implementation module
    (e.g. ``_azure_container_apps.py``) once the SDK integration is built.
    """
    raise NotImplementedError(
        f"Service update for '{target}' is not yet implemented. "
        f"The image has been built and pushed as {image_tag}. "
        f"You can update the service manually, or wait for the "
        f"'{target}' SDK integration to be completed."
    )


# ── App generation ──────────────────────────────────────────────────


def _generate_app_source(model_name: str, port: int) -> str:
    """Generate the FastAPI application source code."""
    return f'''\
"""Haute scoring API - auto-generated by ``haute deploy``."""

import json
import logging
import tempfile
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
from haute._polars_utils import bounded_collect_batches
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
from haute.deploy._scorer import admit_deploy_execution, score_graph, score_graph_lazy
from haute.routes._isolated_worker_async import run_isolated_worker_async

# ── Load manifest at startup ────────────────────────────────────────

_MANIFEST_PATH = Path(__file__).parent / "deploy_manifest.json"
_manifest = json.loads(_MANIFEST_PATH.read_text())

_pruned_graph = PipelineGraph.model_validate(_manifest["pruned_graph"])
_input_node_ids = _manifest["input_node_ids"]
_output_node_id = _manifest["output_node_id"]


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
    }}


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
            if batch.height == 0:
                continue
            text = batch.write_ndjson()
            if text and not text.endswith("\\n"):
                text += "\\n"
            spool.write(text.encode("utf-8"))
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
    frame = pl.scan_parquet(result.result_path).head(returned_rows).collect()
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
            if batch.height == 0:
                continue
            text = batch.write_ndjson()
            if text and not text.endswith("\\n"):
                text += "\\n"
            spool.write(text.encode("utf-8"))
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _batch_error_response(exc):
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
    logger.exception("deploy_quote_batch_failed")
    return JSONResponse(
        status_code=500,
        content={{"error_code": "deploy_internal_error", "error": exc.detail}},
    )


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
            response = JSONResponse(
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
            logger.exception("deploy_quote_batch_failed")
            response = JSONResponse(
                status_code=500,
                content={{"error_code": "deploy_internal_error", "error": str(exc)}},
            )
    except BatchScoreError as exc:
        response = _batch_error_response(exc)
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
            logger.exception("deploy_quote_batch_failed")
            response = JSONResponse(
                status_code=500,
                content={{"error_code": "deploy_internal_error", "error": str(exc)}},
            )
    except IsolatedWorkerError as exc:
        logger.exception("deploy_quote_batch_failed")
        response = JSONResponse(
            status_code=500,
            content={{"error_code": "deploy_internal_error", "error": str(exc)}},
        )
    except Exception as exc:
        # Same contract as the live path: an unexpected parent-side failure is
        # logged and reported as the internal-error envelope, never a bare 500.
        logger.exception("deploy_quote_batch_failed")
        response = JSONResponse(
            status_code=500,
            content={{"error_code": "deploy_internal_error", "error": str(exc)}},
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if plan is not None:
            try:
                plan.cleanup(primary_error=primary_error)
            except BatchScoreCleanupError as cleanup_error:
                # The request rows and the scored parquet are still on disk;
                # that is a data-retention defect even after a good score.
                logger.exception("deploy_quote_batch_cleanup_failed")
                response = JSONResponse(
                    status_code=500,
                    content={{
                        "error_code": "deploy_internal_error",
                        "error": str(cleanup_error),
                    }},
                )
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

    try:
        row_count = len(rows)
        execution_context = admit_deploy_execution(
            operation="deploy_quote",
            row_count=row_count,
        )
        execution_context.checkpoint(label="before_deploy_request_dataframe")
        input_df = pl.DataFrame(rows)
        execution_context.checkpoint(label="after_deploy_request_dataframe")
        if _wants_ndjson(request):
            plan = score_graph_lazy(
                graph=_pruned_graph,
                input_df=input_df,
                input_node_ids=_input_node_ids,
                output_node_id=_output_node_id,
                artifact_paths=_artifact_paths,
                output_fields=_output_fields,
                execution_context=execution_context,
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
        )
        return JSONResponse(content=_quote_response_content(result, execution_context))
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
        logger.exception("deploy_quote_failed")
        return JSONResponse(
            status_code=500,
            content={{
                "error_code": "deploy_internal_error",
                "error": str(exc),
            }},
        )
    except Exception as exc:
        logger.exception("deploy_quote_failed")
        return JSONResponse(
            status_code=500,
            content={{
                "error_code": "deploy_internal_error",
                "error": str(exc),
            }},
        )
'''


# ── Dockerfile generation ───────────────────────────────────────────


def _generate_dockerfile(
    base_image: str,
    port: int,
    resolved: ResolvedDeploy,
    *,
    haute_pip_dep: str | None = None,
    wheel_name: str | None = None,
) -> str:
    """Generate a Dockerfile for the scoring container."""
    deps_line = " ".join(_pinned_dockerfile_deps(resolved, haute_requirement=haute_pip_dep))
    copy_wheel = f"COPY {wheel_name} .\n" if wheel_name else ""

    return f"""\
FROM {base_image}

WORKDIR /app

# Select fixed server budgets explicitly. This remains admission/RSS
# enforcement; the hosting platform owns any outer hard container cap.
ENV HAUTE_EXECUTION_MEMORY_POLICY=strict_server

# Install Python dependencies
{copy_wheel}RUN pip install --no-cache-dir {deps_line}

# Copy application code and artifacts
COPY deploy_manifest.json .
COPY app.py .
COPY artifacts/ artifacts/

EXPOSE {port}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "{port}"]
"""


def _pinned_dockerfile_deps(
    resolved: ResolvedDeploy,
    *,
    haute_requirement: str | None = None,
) -> list[str]:
    """Every ``pip install`` requirement of the scoring container, pinned.

    Core runtime and model runtime alike are pinned to the version installed
    in the deploying environment: the container unpickles the model, and a
    model loaded under a different scikit-learn (or LightGBM, ...) than the
    one that wrote it is silently wrong premiums, not an error.
    """
    return [
        *_pinned_core_dockerfile_deps(haute_requirement=haute_requirement),
        *_pinned_extra_dockerfile_deps(resolved),
    ]


def _pinned_core_dockerfile_deps(*, haute_requirement: str | None = None) -> list[str]:
    deps: list[str] = []
    for distribution_name, install_name in _CORE_DOCKERFILE_DEPENDENCIES:
        if distribution_name == "haute" and haute_requirement is not None:
            deps.append(haute_requirement)
        else:
            deps.append(_pinned_dockerfile_dependency(distribution_name, install_name))
    return deps


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
        needed_by = f" (needed by artifact {', '.join(required_by)})" if required_by else ""
        raise DeployError(
            f"Cannot pin Dockerfile dependency {install_name!r}{needed_by}: "
            f"installed distribution {distribution_name!r} was not found. The "
            f"container is pinned to the versions installed in the deploying "
            f"environment, so install {distribution_name!r} here and re-run."
        ) from exc
    return f"{install_name}=={package_version}"


# Artifact extension -> distribution name of the runtime that loads it.  Every
# entry is also the ``pip install`` name, and every entry is pinned through
# ``_pinned_dockerfile_dependency`` -- catboost included, even though haute's
# own ``catboost<2`` cap would happen to constrain a bare name.
_ARTIFACT_EXT_TO_DEP: dict[str, str] = {
    ".cbm": "catboost",
    ".pkl": "scikit-learn",
    ".pickle": "scikit-learn",
    ".lgb": "lightgbm",
    ".xgb": "xgboost",
    ".onnx": "onnxruntime",
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
        if suffix in _ARTIFACT_EXT_TO_DEP:
            needed.setdefault(_ARTIFACT_EXT_TO_DEP[suffix], []).append(artifact_name)
    return dict(sorted(needed.items()))


def _detect_extra_deps(resolved: ResolvedDeploy) -> list[str]:
    """Detect extra Python packages needed based on artifact file extensions."""
    return list(_extra_deps_by_artifact(resolved))


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
        ["docker", "build", "-t", image_tag, "."],
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


# ── Helpers ─────────────────────────────────────────────────────────


def _git_sha_short() -> str:
    """Get the short git SHA of HEAD, or 'local' if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "local"


def _next_version() -> int:
    """Simple version counter based on existing local images.

    Returns 1 if no previous images exist. In production, the
    registry or git tags are the real version source.
    """
    return 1
