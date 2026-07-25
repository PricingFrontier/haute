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
        artifacts_dir = build_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        # 2. Build deployment manifest
        _log("Building deployment manifest...")
        manifest = build_manifest(resolved)

        # Remap artifact paths to container-relative paths
        container_artifacts: dict[str, str] = {}
        for artifact_name, artifact_path in resolved.artifacts.items():
            container_artifacts[artifact_name] = f"artifacts/{artifact_name}"

        manifest["artifacts"] = container_artifacts

        manifest_path = build_dir / "deploy_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        _log(f"  Manifest: {manifest_path}")

        # 3. Copy artifacts
        _log(f"Copying {len(resolved.artifacts)} artifacts...")
        for artifact_name, artifact_path in resolved.artifacts.items():
            dest = artifacts_dir / artifact_name
            shutil.copy2(artifact_path, dest)
            _log(f"  {artifact_name} → {dest}")

        # 4. Generate FastAPI app
        _log("Generating FastAPI app...")
        app_source = _generate_app_source(config.model_name, ct.port)
        (build_dir / "app.py").write_text(app_source)

        # 5. Generate Dockerfile
        _log("Generating Dockerfile...")
        dockerfile = _generate_dockerfile(ct.base_image, ct.port, resolved)
        (build_dir / "Dockerfile").write_text(dockerfile)

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
from pathlib import Path

import polars as pl
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

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
from haute.deploy._scorer import admit_deploy_execution, score_graph, score_graph_lazy

# ── Load manifest at startup ────────────────────────────────────────

_MANIFEST_PATH = Path(__file__).parent / "deploy_manifest.json"
_manifest = json.loads(_MANIFEST_PATH.read_text())

_pruned_graph = PipelineGraph.model_validate(_manifest["pruned_graph"])
_input_node_ids = _manifest["input_node_ids"]
_output_node_id = _manifest["output_node_id"]
_artifact_paths = _manifest["artifacts"]
_output_fields = _manifest.get("output_fields")
_QUOTE_REQUEST_BODY_LIMIT_BYTES = deploy_quote_request_body_limit_bytes()
_QUOTE_RESPONSE_ROW_LIMIT = {DEFAULT_QUOTE_RESPONSE_ROW_LIMIT}
_DEPLOY_STREAM_CHUNK_SIZE = 50_000
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


def _quote_ndjson_chunks(plan):
    preserve_primary_error = False
    try:
        for batch in bounded_collect_batches(
            plan.lazy_frame,
            profile=plan.execution_context.profile,
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
            yield text
    except BaseException:
        preserve_primary_error = True
        raise
    finally:
        plan.cleanup(preserve_primary_error=preserve_primary_error)


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
            return StreamingResponse(
                _quote_ndjson_chunks(plan),
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
) -> str:
    """Generate a Dockerfile for the scoring container."""
    # Detect model-specific dependencies from artifacts
    extra_deps = _detect_extra_deps(resolved)
    deps_line = " ".join([*_pinned_core_dockerfile_deps(), *extra_deps])

    return f"""\
FROM {base_image}

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir {deps_line}

# Copy application code and artifacts
COPY deploy_manifest.json .
COPY app.py .
COPY artifacts/ artifacts/

EXPOSE {port}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "{port}"]
"""


def _pinned_core_dockerfile_deps() -> list[str]:
    return [
        _pinned_dockerfile_dependency(distribution_name, install_name)
        for distribution_name, install_name in _CORE_DOCKERFILE_DEPENDENCIES
    ]


def _pinned_dockerfile_dependency(distribution_name: str, install_name: str) -> str:
    try:
        package_version = metadata_version(distribution_name)
    except PackageNotFoundError as exc:
        raise RuntimeError(
            f"Cannot pin Dockerfile dependency {install_name!r}: "
            f"installed distribution {distribution_name!r} was not found."
        ) from exc
    return f"{install_name}=={package_version}"


_ARTIFACT_EXT_TO_DEP: dict[str, str] = {
    ".cbm": "catboost",
    ".pkl": "scikit-learn",
    ".pickle": "scikit-learn",
    ".lgb": "lightgbm",
    ".xgb": "xgboost",
    ".onnx": "onnxruntime",
}


def _detect_extra_deps(resolved: ResolvedDeploy) -> list[str]:
    """Detect extra Python packages needed based on artifact file extensions.

    Only unambiguous model extensions are matched.  Generic extensions
    like ``.txt`` and ``.json`` are deliberately excluded because they
    would cause false positives (e.g. ``deploy_manifest.json``).
    """
    deps: set[str] = set()
    for artifact_name in resolved.artifacts:
        suffix = Path(artifact_name).suffix.lower()
        if suffix in _ARTIFACT_EXT_TO_DEP:
            deps.add(_ARTIFACT_EXT_TO_DEP[suffix])
    return sorted(deps)


# ── Docker build / push ─────────────────────────────────────────────


def _check_docker_available() -> None:
    """Raise RuntimeError if Docker is not available."""
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
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
        raise RuntimeError(f"Docker build failed:\n{result.stderr}")


def _docker_push(image_tag: str) -> None:
    """Push a Docker image to a registry."""
    result = subprocess.run(
        ["docker", "push", image_tag],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Docker push failed:\n{result.stderr}")


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
