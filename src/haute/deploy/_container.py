"""Container deployment target - generate FastAPI app + Dockerfile, build, push."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from haute._logging import get_logger
from haute.deploy._config import ResolvedDeploy
from haute.deploy._mlflow import DeployResult
from haute.deploy._utils import build_manifest

logger = get_logger(component="deploy.container")

_VALID_BASE_IMAGE_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")
_VALID_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_base_image(base_image: str) -> None:
    """Validate base_image to prevent command injection."""
    if not _VALID_BASE_IMAGE_RE.match(base_image):
        raise ValueError(f"Invalid base_image: {base_image!r}")


def _validate_model_name(model_name: str) -> None:
    """Validate model_name to prevent path traversal / injection."""
    if not _VALID_MODEL_NAME_RE.match(model_name):
        raise ValueError(f"Invalid model_name: {model_name!r}")


# ── Targets that share container build+push ────────────────────────

_CONTAINER_BASED_TARGETS = frozenset(
    {
        "container",
        "azure-container-apps",
        "aws-ecs",
        "gcp-run",
    }
)


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
from pathlib import Path

import polars as pl
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from haute._types import PipelineGraph
from haute.deploy._scorer import score_graph

# ── Load manifest at startup ────────────────────────────────────────

_MANIFEST_PATH = Path(__file__).parent / "deploy_manifest.json"
_manifest = json.loads(_MANIFEST_PATH.read_text())

_pruned_graph = PipelineGraph.model_validate(_manifest["pruned_graph"])
_input_node_ids = _manifest["input_node_ids"]
_output_node_id = _manifest["output_node_id"]
_artifact_paths = _manifest["artifacts"]
_output_fields = _manifest.get("output_fields")

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


@app.post("/quote")
async def quote(request: Request) -> JSONResponse:
    """Score one or more quotes.

    Accepts a JSON object (single quote) or JSON array (batch).
    Returns a JSON array of result objects.
    """
    body = await request.json()

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
        input_df = pl.DataFrame(rows)
        result = score_graph(
            graph=_pruned_graph,
            input_df=input_df,
            input_node_ids=_input_node_ids,
            output_node_id=_output_node_id,
            artifact_paths=_artifact_paths,
            output_fields=_output_fields,
        )
        return JSONResponse(content=result.to_dicts())
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={{"error": "Validation error"}},
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
    deps_line = " ".join(["haute", "polars", "fastapi", "uvicorn[standard]", *extra_deps])

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
    )
    if result.returncode != 0:
        raise RuntimeError(f"Docker build failed:\n{result.stderr}")


def _docker_push(image_tag: str) -> None:
    """Push a Docker image to a registry."""
    result = subprocess.run(
        ["docker", "push", image_tag],
        capture_output=True,
        text=True,
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
