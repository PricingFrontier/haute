"""The underwriter app (specs/workbench): the sheets in front of an underwriter, apart from
Haute's builder, priced through the release's own pipeline.

A release directory holds the pipeline's bundle as a deploy builds it (``deploy_manifest.json``
and its ``artifacts``), the form without its sample (``form.json``), what the release is
(``release.json``) and an entry point (``app.py``) that serves it through :func:`create_app`.
The app serves the sheets' page from Haute's built frontend, the release, the form and the
pricing. Every route but the health check needs the principal the host signed in: the identity
follows the release's host, and a release with no host refuses everything else.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._hashing import content_hash, content_hash_bytes
from haute._json_safe import to_json_safe
from haute._output_assembler import render_output_document
from haute._sandbox import set_project_root
from haute._test_quotes import quote_request
from haute._types import PipelineGraph
from haute._workbench_form import FormSpec, form_revision, render_form
from haute.deploy._scorer import QuoteRequest, admit_deploy_execution, score_graph
from haute.errors import HauteError, is_public_contract_error
from haute.schemas import (
    WorkbenchAppFormResponse,
    WorkbenchAppPriceRequest,
    WorkbenchAppPriceResponse,
    WorkbenchAppReleaseResponse,
)

RELEASE_FILE = "release.json"
FORM_FILE = "form.json"
MANIFEST_FILE = "deploy_manifest.json"
#: The page Haute's frontend builds for the app, apart from the builder's, in ``_STATIC``.
APP_PAGE = "app.html"
#: How long one pricing may take before the app answers that it timed out.
PRICE_TIMEOUT_SECONDS = 60.0
#: The principal Azure's built-in authentication sets on every request it lets through, and
#: strips from what a client sends.
EASY_AUTH_NAME = "x-ms-client-principal-name"
EASY_AUTH_ID = "x-ms-client-principal-id"

APP_ENTRY = '''"""The underwriter app, serving this release (specs/workbench)."""

from pathlib import Path

from haute._workbench_app import create_app

app = create_app(Path(__file__).parent)
'''

#: The app's built page and its assets (``frontend/vite.app.config.ts``).
_STATIC = Path(__file__).parent / "static" / "app"


class WorkbenchAppError(HauteError):
    """A release cannot be read or served: the message says what to fix."""

    error_code = "workbench_app_invalid"


@dataclass(frozen=True)
class Release:
    """What a release is: its id, the form it draws and the pipeline it prices on."""

    id: str
    form_revision: str
    pipeline_name: str
    haute_version: str
    created_at: str
    #: Where the release runs, whose sign-in fronts it; None for a release built with none.
    host: str | None


#: What of a bundle's manifest prices a quote, and so names the release; when, by whom, from
#: which file and for which target it was built do not.
_PRICING_KEYS = (
    "haute_version",
    "input_node_ids",
    "output_node_id",
    "output_fields",
    "input_schema",
    "output_schema",
    "pruned_graph",
)


def _release_id(release_dir: Path, shipped: bytes, manifest: dict[str, Any]) -> str:
    """The form as shipped, what of the manifest prices a quote and each artifact's bytes, so
    the same project builds the same id and a retrained model under the same name another."""
    pricing = {key: manifest.get(key) for key in _PRICING_KEYS}
    pricing["artifacts"] = {
        name: content_hash(release_dir / path)
        for name, path in sorted(manifest["artifacts"].items())
    }
    return content_hash_bytes(shipped + json.dumps(pricing, sort_keys=True).encode("utf-8"))[:12]


def write_release(release_dir: Path, form: FormSpec, host: str | None) -> Release:
    """Beside the bundle already in *release_dir*: the form without its sample, what the
    release is and the entry point."""
    shipped = render_form(form.model_copy(update={"sample": {}})).encode("utf-8")
    # Written as rendered, so the file's revision is the one the release records.
    (release_dir / FORM_FILE).write_bytes(shipped)
    manifest = json.loads((release_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    release = Release(
        id=_release_id(release_dir, shipped, manifest),
        form_revision=form_revision(shipped),
        pipeline_name=str(manifest.get("pipeline_name") or ""),
        haute_version=str(manifest.get("haute_version") or ""),
        created_at=str(manifest.get("created_at") or ""),
        host=host,
    )
    described = json.dumps(asdict(release), indent=2) + "\n"
    (release_dir / RELEASE_FILE).write_bytes(described.encode("utf-8"))
    (release_dir / "app.py").write_bytes(APP_ENTRY.encode("utf-8"))
    return release


def build_release(project_dir: Path, release_dir: Path) -> Release:
    """Build the project's release into *release_dir*: its pipeline resolved, validated and
    bundled as ``haute deploy`` does it, test quotes included, then the release beside it.

    *release_dir* is replaced when it holds an earlier release and refused when it holds
    anything else. Raises :class:`WorkbenchAppError` for a project without a workbench, sheets
    never saved, a directory that is not a release, or a pipeline that does not validate.
    """
    import shutil

    from haute._project import resolve_pipeline_file
    from haute._workbench_config import read_workbench_config
    from haute._workbench_form import read_form_document
    from haute.deploy._config import DeployConfig, resolve_config
    from haute.deploy._container import write_bundle
    from haute.deploy._validators import validate_deploy
    from haute.errors import DeployError

    project_dir = project_dir.resolve()
    set_project_root(project_dir)
    config = read_workbench_config(project_dir)
    if not config.enabled:
        raise WorkbenchAppError(
            "This project has no workbench: add [workbench] enabled = true to haute.toml."
        )
    document = read_form_document(config)
    if document.revision is None:
        raise WorkbenchAppError(f"{config.form} has never been saved: save the sheets first.")
    if release_dir.exists() and any(release_dir.iterdir()):
        if not (release_dir / RELEASE_FILE).is_file():
            raise WorkbenchAppError(
                f"{release_dir} holds files that are not a release: name an empty directory."
            )
        shutil.rmtree(release_dir)

    deploy_config = DeployConfig.from_toml(project_dir / "haute.toml")
    deploy_config.pipeline_file = resolve_pipeline_file(project_dir)
    resolved = resolve_config(deploy_config)
    try:
        try:
            results = validate_deploy(resolved)
        except DeployError as exc:
            context = exc.context if isinstance(exc.context, dict) else {}
            details = [
                *(context.get("structural_errors") or []),
                *(context.get("test_quote_errors") or []),
            ] or [str(exc)]
            raise WorkbenchAppError(
                "The pipeline did not validate:\n" + "\n".join(f"  - {line}" for line in details)
            ) from exc
        failed = [result for result in results if result["status"] != "ok"]
        if failed:
            raise WorkbenchAppError(
                "Test quotes failed:\n"
                + "\n".join(f"  - {result['file']}: {result['error']}" for result in failed)
            )
        write_bundle(resolved, release_dir)
    finally:
        resolved.close()
    return write_release(release_dir, document.spec, config.app_host)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkbenchAppError(f"The release's {path.name} could not be read: {exc}") from exc


def read_release(release_dir: Path) -> tuple[Release, FormSpec, dict[str, Any]]:
    """The release in *release_dir*: what it is, its form and its bundle's manifest."""
    release = Release(**_read_json(release_dir / RELEASE_FILE))
    form = FormSpec.model_validate(_read_json(release_dir / FORM_FILE))
    manifest = _read_json(release_dir / MANIFEST_FILE)
    return release, form, manifest


def create_app(
    release_dir: Path, *, local_principal: str | None = None, static_dir: Path | None = None
) -> FastAPI:
    """The app serving the release in *release_dir*.

    *local_principal*, given by ``haute app serve`` on an analyst's machine, signs every
    request in as that name; otherwise the release's host says who is signed in. The page and
    its assets come from *static_dir*, Haute's built frontend unless given.
    """
    release, form, manifest = read_release(release_dir)
    # The pipeline reads what it ships from the release, wherever the host starts the app.
    set_project_root(release_dir)
    static = _STATIC if static_dir is None else static_dir
    graph = PipelineGraph.model_validate(manifest["pruned_graph"])
    input_node_ids: list[str] = manifest["input_node_ids"]
    output_node_id: str = manifest["output_node_id"]
    output_fields: list[str] | None = manifest.get("output_fields")
    artifact_paths = {
        name: str((release_dir / path).resolve()) for name, path in manifest["artifacts"].items()
    }

    def principal(request: Request) -> str | None:
        if local_principal is not None:
            return local_principal
        if release.host == "azure-container-apps":
            return request.headers.get(EASY_AUTH_NAME) or request.headers.get(EASY_AUTH_ID)
        return None

    def refusal() -> str:
        if release.host is None:
            return (
                "This release names no host to sign underwriters in: set [workbench.app] host "
                "in haute.toml and build it again."
            )
        return (
            "Sign in through Azure's built-in authentication: this request carries no "
            "signed-in principal."
        )

    app = FastAPI(title=f"{release.pipeline_name or 'Haute'} underwriter app")

    @app.middleware("http")
    async def signed_in(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        who = principal(request)
        if who is None:
            return JSONResponse(status_code=401, content={"detail": refusal()})
        request.state.principal = who
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "release": release.id}

    @app.get("/api/app/release", response_model=WorkbenchAppReleaseResponse)
    async def get_release(request: Request) -> WorkbenchAppReleaseResponse:
        return WorkbenchAppReleaseResponse(**asdict(release), principal=request.state.principal)

    @app.get("/api/app/form", response_model=WorkbenchAppFormResponse)
    async def get_form() -> WorkbenchAppFormResponse:
        return WorkbenchAppFormResponse(form=form, revision=release.form_revision)

    @app.post("/api/app/price", response_model=WorkbenchAppPriceResponse)
    async def price(body: WorkbenchAppPriceRequest) -> WorkbenchAppPriceResponse:
        if body.form_revision != release.form_revision:
            raise HTTPException(
                409,
                "This page was drawn for another release of the sheets: reload it to price on "
                "this one.",
            )
        request = quote_request(form, body.values)
        if not request:
            raise HTTPException(422, "Type the quote first")

        def score() -> Any:
            context = admit_deploy_execution(operation="workbench_app_price", row_count=1)
            result = score_graph(
                graph=graph,
                input_df=QuoteRequest((request,)),
                input_node_ids=input_node_ids,
                output_node_id=output_node_id,
                artifact_paths=artifact_paths,
                output_fields=output_fields,
                execution_context=context,
            )
            [document] = render_output_document(result)
            return document

        # An executor's future gives up waiting at the timeout; the pricing left running ends
        # on its own and releases its admission then.
        running = asyncio.get_running_loop().run_in_executor(
            None, contextvars.copy_context().run, score
        )
        try:
            document = await asyncio.wait_for(running, PRICE_TIMEOUT_SECONDS)
        except TimeoutError:
            raise HTTPException(504, "Pricing the quote took too long; try again.") from None
        except ExecutionAdmissionError as exc:
            raise HTTPException(503, f"The app is busy pricing other quotes: {exc}") from None
        except ExecutionMemoryLimitExceededError as exc:
            raise HTTPException(
                507, f"The quote needed more memory than the app has: {exc}"
            ) from None
        except HauteError as exc:
            if not is_public_contract_error(exc):
                raise
            raise HTTPException(422, exc.message or str(exc)) from None
        answered = to_json_safe(document)
        tables = {
            name: rows if isinstance(rows, list) else [rows] for name, rows in answered.items()
        }
        return WorkbenchAppPriceResponse(tables=tables, request=to_json_safe(request))

    @app.get("/", include_in_schema=False)
    async def page() -> Response:
        built = static / APP_PAGE
        if not built.is_file():
            return JSONResponse(
                status_code=503,
                content={"detail": "The app's page is not built: build Haute's frontend first."},
            )
        return FileResponse(built)

    app.mount("/assets", StaticFiles(directory=static / "assets", check_dir=False), name="assets")
    return app
