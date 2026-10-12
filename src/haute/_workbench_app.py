"""The underwriter app (specs/workbench): the sheets in front of an underwriter, apart from
Haute's builder, priced through the release's own pipeline.

A release directory holds the pipeline's bundle as a deploy builds it (``deploy_manifest.json``
and its ``artifacts``), the form without its sample (``form.json``), what the release is
(``release.json``) and an entry point (``app.py``) that serves it through :func:`create_app`.
The app serves the sheets' page from Haute's built frontend, the release, the form and the
pricing, and, given a quote store, the quotes it keeps (``haute._workbench_app_quotes``). Every
route but the health check needs the principal the host signed in: the identity follows the
release's host, and a release with no host refuses everything else. A write from a page of
another site is refused.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import contextvars
import copy
import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
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
from haute._workbench_quote_list import quote_list_problems
from haute.deploy._request_limits import (
    RequestBodyHeaderError,
    RequestBodyLimitError,
    RequestBodyParseError,
    deploy_quote_request_body_limit_bytes,
    read_limited_json_body,
)
from haute.deploy._scorer import QuoteRequest, admit_deploy_execution, score_graph
from haute.errors import HauteError, is_public_contract_error
from haute.schemas import (
    WorkbenchAppFormResponse,
    WorkbenchAppPriceRequest,
    WorkbenchAppPriceResponse,
    WorkbenchAppReleaseResponse,
    WorkbenchPricedTables,
    WorkbenchValues,
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
#: The principal Azure's authentication passes whole: base64 JSON of its claims, with
#: ``role_typ`` naming the claim type its roles are under.
EASY_AUTH_PRINCIPAL = "x-ms-client-principal"
#: The claim types an app role is under when the principal names none.
_ROLE_CLAIMS = frozenset({"roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"})
#: The environment variable a hosted app reads its quote store's SQLAlchemy URL from, which
#: the host supplies at runtime; without it the app keeps no quotes.
STORE_URL_ENV = "HAUTE_QUOTE_STORE_URL"
#: What the app's quote store needs in the image past the scoring runtime; a server
#: engine's driver joins them with that engine's backend.
STORE_DEPENDENCIES = ("sqlalchemy", "alembic")
#: The app's pages past its home, each the same page, which routes in the browser.
APP_PAGES = ("/quotes/new", "/quotes/{quote_id}", "/quotes/{quote_id}/revisions/{number}")
#: What a browser says of a request's origin that a write is taken from: the page's own.
_SAME_SITE = frozenset({"same-origin", "none"})

APP_ENTRY = '''"""The underwriter app, serving this release (specs/workbench)."""

from pathlib import Path

from haute._workbench_app import create_app, quote_store_url

app = create_app(Path(__file__).parent, store_url=quote_store_url())
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
    #: The role that may change quotes; None lets everyone signed in change them.
    edit_role: str | None = None


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


def _graphs(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """A manifest's pipeline graph and each submodel's, as dicts."""
    found = [graph]
    for submodel in (graph.get("submodels") or {}).values():
        found.extend(_graphs(submodel["graph"]))
    return found


def _without_sources(graph: dict[str, Any]) -> dict[str, Any]:
    """*graph* without the files its pipelines were read from: where a checkout sits on
    disk prices nothing."""
    copied: dict[str, Any] = copy.deepcopy(graph)
    for each in _graphs(copied):
        each.pop("source_file", None)
    return copied


def _utility_hashes(release_dir: Path) -> dict[str, str]:
    """Each file of the project's utility code the bundle carries, by its path in it."""
    from haute.deploy._project_modules import UTILITY_PACKAGE

    package = release_dir / UTILITY_PACKAGE
    files = [release_dir / f"{UTILITY_PACKAGE}.py"]
    if package.is_dir():
        files += [file for file in package.rglob("*") if "__pycache__" not in file.parts]
    return {
        file.relative_to(release_dir).as_posix(): content_hash(file)
        for file in sorted(files)
        if file.is_file()
    }


def _release_id(release_dir: Path, shipped: bytes, manifest: dict[str, Any]) -> str:
    """The form as shipped, what of the manifest prices a quote, each artifact's bytes and
    the utility code's, so the same project builds the same id wherever it is checked out,
    and a retrained model or an edited helper under the same name another."""
    from haute._cache import canonical_json

    pricing = {key: manifest.get(key) for key in _PRICING_KEYS}
    pricing["pruned_graph"] = _without_sources(manifest["pruned_graph"])
    pricing["artifacts"] = {
        name: content_hash(release_dir / path)
        for name, path in sorted(manifest["artifacts"].items())
    }
    pricing["utility"] = _utility_hashes(release_dir)
    return content_hash_bytes(shipped + canonical_json(pricing).encode("utf-8"))[:12]


def easy_auth_roles(header: str) -> frozenset[str]:
    """The roles in the principal Azure's authentication passes (``EASY_AUTH_PRINCIPAL``):
    the values of its claims of the type its ``role_typ`` names, or of an app role's claim
    types when it names none. Raises :class:`ValueError` for a header that is not one."""
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("the principal is not base64 JSON") from exc
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        raise ValueError("the principal holds no claims")
    role_typ = payload.get("role_typ")
    types = {role_typ} if isinstance(role_typ, str) and role_typ else set(_ROLE_CLAIMS)
    return frozenset(
        claim["val"]
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("typ") in types
        and isinstance(claim.get("val"), str)
    )


def write_release(
    release_dir: Path, form: FormSpec, host: str | None, edit_role: str | None = None
) -> Release:
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
        edit_role=edit_role,
    )
    described = json.dumps(asdict(release), indent=2) + "\n"
    (release_dir / RELEASE_FILE).write_bytes(described.encode("utf-8"))
    (release_dir / "app.py").write_bytes(APP_ENTRY.encode("utf-8"))
    return release


def build_release(
    project_dir: Path, release_dir: Path, *, haute_requirement: str | None = None
) -> Release:
    """Build the project's release into *release_dir*: its pipeline resolved, validated and
    bundled as ``haute deploy`` does it, test quotes included, then the release beside it.
    With ``[deploy.container] base_image`` set, pinned as a container deploy pins it, the
    directory is the image's build context: the container's Dockerfile, copying the release
    too, with the release's entry point in the scoring app's place; *haute_requirement* is the
    ``haute`` the image installs, as the container target takes it.

    *release_dir* is replaced when it holds an earlier release and refused when it holds
    anything else. Raises :class:`WorkbenchAppError` for a project without a workbench, sheets
    never saved, a directory that is not a release, or a pipeline that does not validate.
    """
    import shutil

    from haute._project import resolve_pipeline_file
    from haute._workbench_config import read_workbench_config
    from haute._workbench_form import read_form_document
    from haute.deploy._config import (
        DeployConfig,
        _validate_base_image_pinning,
        resolve_config,
    )
    from haute.deploy._container import prepare_build_directory, write_bundle
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
    base_image = deploy_config.container.base_image
    if base_image:
        try:
            _validate_base_image_pinning(base_image)
        except DeployError as exc:
            raise WorkbenchAppError(f"[deploy.container] base_image: {exc.message}") from exc
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
        if base_image:
            prepare_build_directory(
                resolved,
                release_dir,
                haute_requirement=haute_requirement,
                extra_files=(FORM_FILE, RELEASE_FILE),
                extra_dependencies=STORE_DEPENDENCIES,
            )
        else:
            write_bundle(resolved, release_dir)
    finally:
        resolved.close()
    return write_release(release_dir, document.spec, config.app_host, config.app_edit_role)


def build_image(release_dir: Path, tag: str) -> None:
    """Build the image of the release in *release_dir*, tagged *tag*, with Docker."""
    from haute.deploy._container import _check_docker_available, _docker_build
    from haute.errors import DeployError

    if not (release_dir / "Dockerfile").is_file():
        raise WorkbenchAppError(
            "The release has no Dockerfile: set [deploy.container] base_image in haute.toml, "
            "pinned to a patch release or a digest, and build it again."
        )
    try:
        _check_docker_available()
    except DeployError as exc:
        raise WorkbenchAppError(
            f"Docker is not available here: build the image from {release_dir} where it is, "
            "such as in CI."
        ) from exc
    try:
        _docker_build(release_dir, tag)
    except DeployError as exc:
        raise WorkbenchAppError(str(exc)) from exc


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


def quote_store_url() -> str | None:
    """The quote store's URL a hosted app is given in :data:`STORE_URL_ENV`; None unset."""
    url = os.environ.get(STORE_URL_ENV, "").strip()
    return url or None


def _cross_site(request: Request) -> bool:
    """A write sent by a page of another site: the browser says so, or its origin is not
    this host."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in _SAME_SITE:
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].rstrip("/") != host


def create_app(
    release_dir: Path,
    *,
    local_principal: str | None = None,
    static_dir: Path | None = None,
    store_url: str | None = None,
    local_reader: bool = False,
) -> FastAPI:
    """The app serving the release in *release_dir*.

    *local_principal*, given by ``haute app serve`` on an analyst's machine, signs every
    request in as that name, able to change quotes unless *local_reader*; otherwise the
    release's host says who is signed in and, for a release with an ``edit_role``, whether
    they hold it. The page and
    its assets come from *static_dir*, Haute's built frontend unless given. *store_url* names
    the quote store the app keeps quotes in, which must be migrated: the app does not start on
    one that is not, and keeps no quotes without one.
    """
    release, form, manifest = read_release(release_dir)
    problems = quote_list_problems(form)
    if problems:
        raise WorkbenchAppError(f"The release's quote list cannot be shown: {'; '.join(problems)}")
    # The pipeline reads what it ships from the release, wherever the host starts the app.
    set_project_root(release_dir)
    static = _STATIC if static_dir is None else static_dir
    # The pipeline imports its utility code from beside its file: placed in the release, it
    # imports the bundle's copy, never the project's as it is edited afterwards.
    shipped_graph = copy.deepcopy(manifest["pruned_graph"])
    for each in _graphs(shipped_graph):
        if each.get("source_file"):
            each["source_file"] = str(release_dir.resolve() / Path(each["source_file"]).name)
    graph = PipelineGraph.model_validate(shipped_graph)
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

    def may_edit(request: Request) -> bool | None:
        """Whether the request's principal may change quotes; None when the host passed
        no roles that can be read and the release needs one."""
        if local_principal is not None:
            return not local_reader
        if release.edit_role is None:
            return True
        header = request.headers.get(EASY_AUTH_PRINCIPAL)
        if header is None:
            return None
        try:
            return release.edit_role in easy_auth_roles(header)
        except ValueError:
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

    book = None
    if store_url is not None:
        from haute._workbench_app_quotes import open_book

        book = open_book(store_url, form)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if book is not None:
                book.close()

    app = FastAPI(title=f"{release.pipeline_name or 'Haute'} underwriter app", lifespan=lifespan)

    @app.middleware("http")
    async def signed_in(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        who = principal(request)
        if who is None:
            return JSONResponse(status_code=401, content={"detail": refusal()})
        if _cross_site(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "A change to the app's quotes comes from the app's own page."},
            )
        can_edit = may_edit(request)
        if can_edit is None:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": f"This app lets the {release.edit_role} role change quotes, and "
                    "Azure's built-in authentication passed no roles it can read "
                    f"({EASY_AUTH_PRINCIPAL}): check the app's authentication settings."
                },
            )
        request.state.principal = who
        request.state.can_edit = can_edit
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "release": release.id}

    @app.get("/api/app/release", response_model=WorkbenchAppReleaseResponse)
    async def get_release(request: Request) -> WorkbenchAppReleaseResponse:
        return WorkbenchAppReleaseResponse(
            **asdict(release),
            principal=request.state.principal,
            quotes=book is not None,
            can_edit=request.state.can_edit,
        )

    @app.get("/api/app/form", response_model=WorkbenchAppFormResponse)
    async def get_form() -> WorkbenchAppFormResponse:
        return WorkbenchAppFormResponse(form=form, revision=release.form_revision)

    # A quote's request is read within the deployed /quote route's body limit, before it is
    # parsed: an oversized body is refused before it is held in memory whole.
    body_limit = deploy_quote_request_body_limit_bytes()

    async def price_values(
        values: WorkbenchValues,
    ) -> tuple[WorkbenchPricedTables, dict[str, Any]]:
        """*values* priced through the release's pipeline: its output tables and the request
        priced, or an HTTP error saying why not."""
        request = quote_request(form, values)
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
        return tables, to_json_safe(request)

    @app.post("/api/app/price", response_model=WorkbenchAppPriceResponse)
    async def price(http: Request) -> Any:
        try:
            raw = await read_limited_json_body(
                http, operation="workbench_app_price", limit_bytes=body_limit
            )
        except RequestBodyLimitError as exc:
            return JSONResponse(status_code=413, content=exc.to_payload())
        except RequestBodyHeaderError as exc:
            return JSONResponse(status_code=400, content=exc.to_payload())
        except RequestBodyParseError as exc:
            return JSONResponse(status_code=422, content=exc.to_payload())
        try:
            body = WorkbenchAppPriceRequest.model_validate(raw)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from None
        if body.form_revision != release.form_revision or body.release != release.id:
            raise HTTPException(
                409,
                "This page was drawn for another release of the app: reload it to price on "
                "this one.",
            )
        tables, request = await price_values(body.values)
        return WorkbenchAppPriceResponse(tables=tables, request=request)

    if book is not None:
        from haute._workbench_app_quotes import QuoteApp, quote_router

        app.include_router(
            quote_router(
                QuoteApp(
                    release_id=release.id,
                    form_revision=release.form_revision,
                    form=form,
                    book=book,
                    price=price_values,
                )
            )
        )
    else:

        @app.api_route(
            "/api/app/quotes{rest:path}", methods=["GET", "POST"], include_in_schema=False
        )
        @app.get("/api/app/people", include_in_schema=False)
        async def no_quotes(rest: str = "") -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "This app keeps no quotes: it was started without a quote store "
                    f"({STORE_URL_ENV})."
                },
            )

    @app.get("/", include_in_schema=False)
    @app.get(APP_PAGES[0], include_in_schema=False)
    @app.get(APP_PAGES[1], include_in_schema=False)
    @app.get(APP_PAGES[2], include_in_schema=False)
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
