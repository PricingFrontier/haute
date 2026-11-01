"""The underwriter app (specs/workbench): the sheets in front of an underwriter, apart from
Haute's builder, priced through the release's own pipeline.

A release directory holds the pipeline's bundle as a deploy builds it (``deploy_manifest.json``
and its ``artifacts``), the form without its sample (``form.json``), what the release is
(``release.json``) and an entry point (``app.py``) that serves it through :func:`create_app`.
The app serves the sheets' page from Haute's built frontend, the release, the form and the
pricing, and the quotes it keeps in its quote store (``haute._workbench_app_quotes``), open
while the store's gate finds its tables fit (``haute._workbench_app_store``). Every
route but the health check needs the principal the host signed in: the identity follows the
release's host, and a release with no host refuses everything else. Served on an analyst's
machine, it takes only a request for a loopback host that was not forwarded. A write from a
page of another site is refused.
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
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from haute._env import int_env
from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._hashing import content_hash, content_hash_bytes
from haute._json_safe import to_json_safe
from haute._local_security import DEFAULT_TRUSTED_HOSTS, LocalTrustedHostMiddleware
from haute._logging import get_logger
from haute._output_assembler import render_output_document
from haute._sandbox import set_project_root
from haute._types import PipelineGraph
from haute._workbench_app_store import QuoteStoreClosedError, StoreGate, no_environment
from haute._workbench_config import ENVIRONMENT_ENV, StoreEnvironment, store_environments
from haute._workbench_form import FormSpec, form_revision, render_form
from haute._workbench_tables import finite_document, priced_tables, quote_request
from haute.deploy._request_limits import (
    RequestBodyHeaderError,
    RequestBodyLimitError,
    RequestBodyParseError,
    deploy_quote_request_body_limit_bytes,
    read_limited_json_body,
)
from haute.deploy._scorer import QuoteRequest, admit_deploy_execution, score_graph
from haute.errors import HauteError, is_public_contract_error
from haute.routes._validation_errors import request_validation_error_handler
from haute.schemas import (
    WorkbenchAppFormResponse,
    WorkbenchAppPriceRequest,
    WorkbenchAppPriceResponse,
    WorkbenchAppReleaseResponse,
    WorkbenchPricedTables,
    WorkbenchValues,
)

if TYPE_CHECKING:
    from haute._quote_store import QuoteBook
    from haute._quote_store._environment import StoreLocation

RELEASE_FILE = "release.json"
FORM_FILE = "form.json"
MANIFEST_FILE = "deploy_manifest.json"
#: The page Haute's frontend builds for the app, apart from the builder's, in ``_STATIC``.
APP_PAGE = "app.html"
#: How long one pricing may take before the app answers that it timed out.
PRICE_TIMEOUT_SECONDS = 60.0
#: How many quotes the app prices at once, unless :data:`PRICING_WORKERS_ENV` names another
#: number: a pricing asked for past them is refused as busy rather than queued behind them.
PRICING_WORKERS = 4
PRICING_WORKERS_ENV = "HAUTE_APP_PRICING_WORKERS"
#: The seconds a pricing refused as busy is told to wait before it is asked for again.
BUSY_RETRY_SECONDS = 5
#: The principal Azure's built-in authentication sets on every request it lets through, and
#: strips from what a client sends.
EASY_AUTH_NAME = "x-ms-client-principal-name"
EASY_AUTH_ID = "x-ms-client-principal-id"
#: The principal Azure's authentication passes whole: base64 JSON of its claims, with
#: ``role_typ`` naming the claim type its roles are under.
EASY_AUTH_PRINCIPAL = "x-ms-client-principal"
#: The claim types an app role is under when the principal names none.
_ROLE_CLAIMS = frozenset({"roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"})
#: What the app's quote store needs in the image past the scoring runtime, whatever its
#: environments; each declared environment's driver joins them (:func:`store_dependencies`).
STORE_DEPENDENCIES = ("sqlalchemy", "alembic")
#: The app's pages past its home, each the same page, which routes in the browser.
APP_PAGES = ("/quotes/new", "/quotes/{reference}/{option}")
#: What a browser says of a request's origin that a write is taken from: the page's own.
_SAME_SITE = frozenset({"same-origin", "none"})

APP_ENTRY = '''"""The underwriter app, serving this release (specs/workbench)."""

from functools import partial
from pathlib import Path

from haute._workbench_app import create_app, hosted_store

release = Path(__file__).parent
app = create_app(release, store=partial(hosted_store, release))
'''

#: The app's built page and its assets (``frontend/vite.app.config.ts``).
_STATIC = Path(__file__).parent / "static" / "app"


logger = get_logger(component="workbench.app")


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
    #: The hosted environments of the quote store the project declares, by name, each as
    #: :meth:`StoreEnvironment.declared` records it: its provider and sign-in, never where its
    #: database is or a secret.
    environments: dict[str, dict[str, str]] = field(default_factory=dict)

    def store_environments(self) -> tuple[StoreEnvironment, ...]:
        """The environments the release declares."""
        return store_environments(self.environments, f"{RELEASE_FILE} environments")


@dataclass(frozen=True)
class Pricing:
    """A quote's values priced through the release's pipeline: the output tables as the page
    shows them, the request priced, and the response's document, every value that is not
    finite null, which the quote store keeps."""

    tables: WorkbenchPricedTables
    request: dict[str, Any]
    document: Mapping[str, Any]


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


def _as_priced(graph: dict[str, Any]) -> dict[str, Any]:
    """*graph* as what prices a quote, as the execution fingerprint reads a graph: each
    pipeline's nodes by id, label, type and config, sorted by id, and its edges by their
    ends, without where a node sits on the canvas, its description, an edge's canvas id or
    the file the pipeline was read from."""
    copied: dict[str, Any] = copy.deepcopy(graph)
    for each in _graphs(copied):
        each.pop("source_file", None)
        each["nodes"] = sorted(
            (
                {
                    "id": node["id"],
                    "label": node["data"]["label"],
                    "nodeType": node["data"]["nodeType"],
                    "config": node["data"].get("config"),
                }
                for node in each.get("nodes", [])
            ),
            key=lambda node: str(node["id"]),
        )
        each["edges"] = sorted(
            ({end: edge.get(end) for end in _EDGE_ENDS} for edge in each.get("edges", [])),
            key=lambda edge: json.dumps(edge, sort_keys=True),
        )
    return copied


#: An edge as it prices: which node's handle feeds which.
_EDGE_ENDS = ("source", "sourceHandle", "target", "targetHandle")


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


def _release_id(
    release_dir: Path, shipped: bytes, manifest: dict[str, Any], runtime: list[str]
) -> str:
    """The form as shipped, what of the manifest prices a quote (the graph as it prices,
    `_as_priced`), each artifact's bytes, the utility code's and the *runtime* it prices on,
    so the same project builds the same id wherever it is checked out, however its nodes are
    laid out and whether or not it ships as an image, and a retrained model, an edited
    helper or another runtime under the same name another."""
    from haute._cache import canonical_json

    pricing = {key: manifest.get(key) for key in _PRICING_KEYS}
    pricing["pruned_graph"] = _as_priced(manifest["pruned_graph"])
    pricing["artifacts"] = {
        name: content_hash(release_dir / path)
        for name, path in sorted(manifest["artifacts"].items())
    }
    pricing["utility"] = _utility_hashes(release_dir)
    pricing["runtime"] = runtime
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
    release_dir: Path,
    form: FormSpec,
    host: str | None,
    edit_role: str | None = None,
    *,
    runtime: list[str],
    environments: tuple[StoreEnvironment, ...] = (),
) -> Release:
    """Beside the bundle already in *release_dir*: the form without its sample, what the
    release is, named for the *runtime* it prices on too, with the quote store's
    *environments*, and the entry point."""
    shipped = render_form(form.model_copy(update={"sample": {}})).encode("utf-8")
    # Written as rendered, so the file's revision is the one the release records.
    (release_dir / FORM_FILE).write_bytes(shipped)
    manifest = json.loads((release_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    release = Release(
        id=_release_id(release_dir, shipped, manifest, runtime),
        form_revision=form_revision(shipped),
        pipeline_name=str(manifest.get("pipeline_name") or ""),
        haute_version=str(manifest.get("haute_version") or ""),
        created_at=str(manifest.get("created_at") or ""),
        host=host,
        edit_role=edit_role,
        environments={each.name: each.declared() for each in environments},
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

    *release_dir* is replaced when it holds an earlier release (``_is_release``) and refused
    when it holds anything else, or is or holds the project or the working directory. The
    release is built in a folder beside it and takes its place only once it has built, so a
    build that fails leaves the earlier release as it was. Raises :class:`WorkbenchAppError`
    for a project without a workbench, sheets naming a column the schema does not hold, allowed
    values a params file can no longer give, a directory that is not a release, or a pipeline
    that does not validate.
    """
    import shutil

    from haute._project import resolve_pipeline_file
    from haute._workbench_config import read_workbench_config
    from haute._workbench_folder import read_form_document, unresolved_references
    from haute._workbench_params import ParamFileError, with_param_options
    from haute.deploy._config import (
        DeployConfig,
        _validate_base_image_pinning,
        resolve_config,
    )
    from haute.deploy._container import prepare_build_directory, runtime_versions, write_bundle
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
    unresolved = unresolved_references(document.spec)
    if unresolved:
        raise WorkbenchAppError(f"{config.form}/" + f"; {config.form}/".join(unresolved) + ".")
    # The allowed values a params file gives are what it holds at this commit, not the copy
    # the form kept when they were picked.
    try:
        form = with_param_options(document.spec, project_dir)
    except ParamFileError as exc:
        raise WorkbenchAppError(f"{config.form}: {exc.message}") from exc
    _check_output(project_dir, release_dir)

    deploy_config = DeployConfig.from_toml(project_dir / "haute.toml")
    deploy_config.pipeline_file = resolve_pipeline_file(project_dir)
    base_image = deploy_config.container.base_image
    if base_image:
        try:
            _validate_base_image_pinning(base_image)
        except DeployError as exc:
            raise WorkbenchAppError(f"[deploy.container] base_image: {exc.message}") from exc
    # The workbench nodes take their tables from the form this release ships, read once.
    resolved = resolve_config(deploy_config, form=document)
    # Built beside the release it replaces, which is left as it is until this one has built.
    building = release_dir.with_name(f".{release_dir.name}.building-{uuid.uuid4().hex[:8]}")
    try:
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
                    "The pipeline did not validate:\n"
                    + "\n".join(f"  - {line}" for line in details)
                ) from exc
            failed = [result for result in results if result["status"] != "ok"]
            if failed:
                raise WorkbenchAppError(
                    "Test quotes failed:\n"
                    + "\n".join(f"  - {result['file']}: {result['error']}" for result in failed)
                )
            try:
                runtime = runtime_versions(resolved)
            except DeployError as exc:
                raise WorkbenchAppError(
                    f"The runtime the pipeline prices on could not be read: {exc.message}"
                ) from exc
            if base_image:
                prepare_build_directory(
                    resolved,
                    building,
                    haute_requirement=haute_requirement,
                    extra_files=(FORM_FILE, RELEASE_FILE),
                    extra_dependencies=store_dependencies(config.store),
                    sqlserver_odbc=any(each.provider == "sqlserver" for each in config.store),
                )
            else:
                write_bundle(resolved, building)
        finally:
            resolved.close()
        release = write_release(
            building,
            form,
            config.app_host,
            config.app_edit_role,
            runtime=runtime,
            environments=config.store,
        )
        _swap_in(building, release_dir)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return release


def _is_release(directory: Path) -> bool:
    """Whether *directory* holds a release :func:`write_release` wrote: a ``release.json`` that
    reads as a :class:`Release`, with the bundle's manifest and the entry point beside it."""
    try:
        Release(**json.loads((directory / RELEASE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return False
    return (directory / MANIFEST_FILE).is_file() and (directory / "app.py").is_file()


def _check_output(project_dir: Path, release_dir: Path) -> None:
    """Refuse a *release_dir* a build must not replace: the project or the working directory,
    or a folder holding either, a project's own files or anything but an earlier release."""
    where = release_dir.resolve()
    for protected, what in (
        (project_dir, "the project"),
        (Path.cwd().resolve(), "the working directory"),
    ):
        # not contained_path: the other direction, the release folder must not hold it
        if protected == where or protected.is_relative_to(where):
            raise WorkbenchAppError(
                f"{release_dir} is {what} or holds it: name a folder of the release's own, "
                "such as .haute/app/release."
            )
    if where.exists() and not where.is_dir():
        raise WorkbenchAppError(f"{release_dir} is a file: name a folder for the release.")
    if where.is_dir() and any(where.iterdir()):
        if (where / "haute.toml").exists() or (where / ".git").exists() or not _is_release(where):
            raise WorkbenchAppError(
                f"{release_dir} holds files that are not a release: name an empty directory."
            )


def _swap_in(built: Path, release_dir: Path) -> None:
    """Put the release in *built* at *release_dir*: an earlier release there is moved aside
    first, put back if the new one cannot take its place, and removed once it has. Each move
    waits out a Windows scanner holding a file just written (``rename_directory``)."""
    from haute._file_ops import remove_tree, rename_directory

    earlier: Path | None = None
    if release_dir.is_dir() and any(release_dir.iterdir()):
        earlier = release_dir.with_name(f".{release_dir.name}.earlier-{uuid.uuid4().hex[:8]}")
        rename_directory(release_dir, earlier)
    elif release_dir.is_dir():
        release_dir.rmdir()
    try:
        rename_directory(built, release_dir)
    except OSError:
        if earlier is not None:
            rename_directory(earlier, release_dir)
        raise
    if earlier is not None and not remove_tree(earlier):
        raise WorkbenchAppError(
            f"The release was built into {release_dir}, but the earlier one, moved to "
            f"{earlier}, could not be removed."
        )


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


def image_reference(project_dir: Path) -> str:
    """The image ``haute app build --push`` publishes for the project at *project_dir*:
    ``<registry>/<name>:<short commit>``, the registry ``[deploy.container] registry`` names
    and the project's name (``[deploy] model_name``'s default, which a workbench project never
    sets) in lower case, since Docker takes no capital in a repository's name. Raises
    :class:`WorkbenchAppError` for a project naming no registry, since a published image needs
    one, a name an image cannot take, or a project whose commit git cannot name, since the
    tag says which commit was published."""
    from haute._git_core import _run_git_ok, git_binary_available
    from haute.deploy._config import DeployConfig
    from haute.deploy._container import _validate_model_name

    config = DeployConfig.from_toml(project_dir / "haute.toml")
    registry = config.container.registry.rstrip("/")
    if not registry:
        raise WorkbenchAppError(
            "[deploy.container] registry is empty: name the registry the app's image is "
            "pushed to in haute.toml."
        )
    try:
        _validate_model_name(config.model_name)
    except ValueError as exc:
        raise WorkbenchAppError(f"The project's name cannot name an image: {exc}") from exc
    named, commit = (
        _run_git_ok("rev-parse", "--short", "HEAD", cwd=project_dir)
        if git_binary_available()
        else (False, "")
    )
    if not named or not commit:
        raise WorkbenchAppError(
            "The app's image is tagged with the commit it is built from, and git names none "
            "here: publish from a clone of the project with git installed and a commit made."
        )
    return f"{registry}/{config.model_name.lower()}:{commit}"


def push_image(release_dir: Path, reference: str) -> str:
    """Build the image of the release in *release_dir* as *reference*
    (:func:`image_reference`), push it, and answer the reference by digest the registry holds
    for it, ``<registry>/<name>@sha256:...``: what was published, whatever the tag points at
    later."""
    from haute.deploy._container import _docker_push, _docker_repo_digest
    from haute.errors import DeployError

    build_image(release_dir, reference)
    try:
        _docker_push(reference)
        return _docker_repo_digest(reference)
    except DeployError as exc:
        raise WorkbenchAppError(str(exc)) from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkbenchAppError(f"The release's {path.name} could not be read: {exc}") from exc


def read_release(release_dir: Path) -> tuple[Release, FormSpec, dict[str, Any]]:
    """The release in *release_dir*: what it is, its form and its bundle's manifest, each
    file checked where it is read, so a release that is not one says which file is wrong
    and how, never a raw error."""
    described = _read_json(release_dir / RELEASE_FILE)
    try:
        if not isinstance(described, dict):
            raise TypeError("it is not a JSON object")
        release = Release(**described)
    except TypeError as exc:
        raise WorkbenchAppError(f"The release's {RELEASE_FILE} is not a release: {exc}") from exc
    try:
        form = FormSpec.model_validate(_read_json(release_dir / FORM_FILE))
    except ValidationError as exc:
        [first, *_] = exc.errors()
        place = ".".join(str(part) for part in first["loc"]) or "the form"
        raise WorkbenchAppError(
            f"The release's {FORM_FILE} is not a form: {place}: {first['msg']}"
        ) from exc
    manifest = _read_json(release_dir / MANIFEST_FILE)
    missing = (
        [key for key in _MANIFEST_KEYS if key not in manifest]
        if isinstance(manifest, dict)
        else list(_MANIFEST_KEYS)
    )
    if missing:
        raise WorkbenchAppError(
            f"The release's {MANIFEST_FILE} is not a bundle's manifest: it has no "
            + ", ".join(missing)
        )
    return release, form, manifest


#: What of a manifest the app reads to price a quote.
_MANIFEST_KEYS = (
    "pruned_graph",
    "input_node_ids",
    "output_node_id",
    "artifacts",
)


def store_dependencies(environments: Iterable[StoreEnvironment]) -> tuple[str, ...]:
    """What the app's quote store needs in a release's image past the scoring runtime, pinned
    as installed here: SQLAlchemy and Alembic, the driver of each provider *environments*
    declare, and azure-identity where one signs in through Microsoft Entra ID."""
    from haute._quote_store._providers import ENTRA_PACKAGE, ENTRA_SIGN_INS, PROVIDERS

    needed = list(STORE_DEPENDENCIES)
    for environment in environments:
        needed.extend(PROVIDERS[environment.provider].packages)
        if environment.auth in ENTRA_SIGN_INS:
            needed.append(ENTRA_PACKAGE)
    return tuple(dict.fromkeys(needed))


def hosted_store(release_dir: Path) -> StoreLocation | None:
    """The quote store the host points the release in *release_dir* at: the environment
    :data:`ENVIRONMENT_ENV` names, which the release must declare, at the database the host's
    variables say it is at; None while no environment is named, which closes the app's
    quotes, nothing of the store's loaded. The store's gate calls it as it checks, so an
    environment or a location that cannot be resolved closes the quotes too."""
    name = os.environ.get(ENVIRONMENT_ENV, "").strip()
    if not name:
        return None
    from haute._quote_store._environment import resolve_store

    release, _, _ = read_release(release_dir)
    return resolve_store(release.store_environments(), name, os.environ)


def _cross_site(request: Request) -> bool:
    """A write sent by a page of another site: the browser says so or, from a browser that
    does not say, its origin is not this host. What the browser says is taken as it is: a
    proxy in front of the app may pass the app another host than the page's."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site not in _SAME_SITE
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
    store: StoreLocation | Callable[[], StoreLocation | None] | None = None,
    local_reader: bool = False,
) -> FastAPI:
    """The app serving the release in *release_dir*.

    *local_principal*, given by ``haute app serve`` on an analyst's machine, signs every
    request for a loopback host that was not forwarded in as that name, able to change quotes
    unless *local_reader*, and refuses any other request; otherwise the
    release's host says who is signed in and, for a release with an ``edit_role``, whether
    they hold it. The page and
    its assets come from *static_dir*, Haute's built frontend unless given. *store* is the
    quote store the app keeps quotes in, dev's on the analyst's machine through its
    connection, or what resolves a hosted environment's (:func:`hosted_store`), or None. The
    app starts whatever the store holds: its quotes are open while the store's gate finds its
    database holding the
    release's form's tables, and closed, every quote route and the pricing answering 503
    with the refusal, while it does not, while it cannot be reached and without a store.
    """
    release, form, manifest = read_release(release_dir)
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

    def open_store() -> QuoteBook:
        location = store() if callable(store) else store
        if location is None:
            return no_environment()
        from haute._quote_store import open_book

        return open_book(
            location.url,
            form,
            environment=location.environment,
            haute_version=release.haute_version,
            sql_schema=location.sql_schema,
            sign_in=location.sign_in,
            named_by=location.named_by,
        )

    gate = StoreGate(open_store)
    # Tried once as the app is made, so a store that fits is open for the first request; a
    # closed one is logged by the gate and never stops the app.
    with contextlib.suppress(QuoteStoreClosedError):
        gate.book()

    # A pricing past its timeout cannot be stopped, so it keeps its place until it ends: the
    # places are given back by the pricings, never by the requests that stopped waiting.
    workers = int_env(PRICING_WORKERS_ENV, PRICING_WORKERS)
    pricing_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="workbench-price")
    places = threading.BoundedSemaphore(workers)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            pricing_pool.shutdown(wait=False, cancel_futures=True)
            gate.close()

    app = FastAPI(title=f"{release.pipeline_name or 'Haute'} underwriter app", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)

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

    if local_principal is not None:
        # Every request is signed in as the analyst, so a page of a site whose name resolves
        # to their machine, which the browser treats as that page's own, is refused before
        # it is signed in. Added last, the check runs first.
        app.add_middleware(LocalTrustedHostMiddleware, allowed_hosts=DEFAULT_TRUSTED_HOSTS)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "release": release.id}

    @app.get("/api/app/release", response_model=WorkbenchAppReleaseResponse)
    async def get_release(request: Request) -> WorkbenchAppReleaseResponse:
        return WorkbenchAppReleaseResponse(
            **{key: value for key, value in asdict(release).items() if key != "environments"},
            principal=request.state.principal,
            quotes=await run_in_threadpool(gate.state),
            can_edit=request.state.can_edit,
        )

    @app.get("/api/app/form", response_model=WorkbenchAppFormResponse)
    async def get_form() -> WorkbenchAppFormResponse:
        return WorkbenchAppFormResponse(form=form, revision=release.form_revision)

    # A quote's request is read within the deployed /quote route's body limit, before it is
    # parsed: an oversized body is refused before it is held in memory whole.
    body_limit = deploy_quote_request_body_limit_bytes()

    async def price_values(values: WorkbenchValues) -> Pricing:
        """*values* priced through the release's pipeline, or an HTTP error saying why not."""
        request = quote_request(form, values)
        if not request:
            raise HTTPException(422, "Type the quote first")

        def score() -> Mapping[str, Any]:
            # The admission is held until the answer is the document sent back: rendering
            # it is the pricing's too, as the MLflow model keeps its admission through its
            # conversion.
            context = admit_deploy_execution(operation="workbench_app_price", row_count=1)
            failed = False
            try:
                result = score_graph(
                    graph=graph,
                    input_df=QuoteRequest((request,)),
                    input_node_ids=input_node_ids,
                    output_node_id=output_node_id,
                    artifact_paths=artifact_paths,
                    output_fields=output_fields,
                    execution_context=context,
                    retain_admission_on_success=True,
                )
                [document] = render_output_document(result)
                finite: Mapping[str, Any] = finite_document(document)
                return finite
            except BaseException:
                failed = True
                raise
            finally:
                context.release_admission(preserve_primary_error=failed)

        if not places.acquire(blocking=False):
            raise HTTPException(
                503,
                "The app is busy pricing other quotes: try again in a few seconds.",
                headers={"Retry-After": str(BUSY_RETRY_SECONDS)},
            )
        # The request gives up waiting at the timeout; the pricing left running ends on its
        # own and gives back its admission and its place then.
        running = pricing_pool.submit(contextvars.copy_context().run, score)
        running.add_done_callback(lambda _: places.release())
        try:
            document = await asyncio.wait_for(asyncio.wrap_future(running), PRICE_TIMEOUT_SECONDS)
        except TimeoutError:
            raise HTTPException(504, "Pricing the quote took too long; try again.") from None
        except ExecutionAdmissionError as exc:
            raise HTTPException(503, f"The app is busy pricing other quotes: {exc}") from None
        except ExecutionMemoryLimitExceededError as exc:
            raise HTTPException(
                507, f"The quote needed more memory than the app has: {exc}"
            ) from None
        except HauteError as exc:
            if is_public_contract_error(exc):
                raise HTTPException(422, exc.message or str(exc)) from None
            logger.exception("workbench_app_price_failed")
            raise HTTPException(500, f"The pipeline failed on this quote: {exc}") from None
        except Exception as exc:
            # A node's own code failing on the values typed, said as the deployed pricing
            # API says it, so the underwriter can tell the analyst what broke.
            logger.exception("workbench_app_price_failed")
            raise HTTPException(500, f"The pipeline failed on this quote: {exc}") from None
        return Pricing(
            tables=priced_tables(document), request=to_json_safe(request), document=document
        )

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
        # Nothing is priced that could not be saved: a closed store prices no quote.
        try:
            await run_in_threadpool(gate.book)
        except QuoteStoreClosedError as refusal:
            raise HTTPException(503, detail=refusal.detail) from None
        pricing = await price_values(body.values)
        return WorkbenchAppPriceResponse(tables=pricing.tables, request=pricing.request)

    from haute._workbench_app_quotes import QuoteApp, quote_router

    app.include_router(
        quote_router(
            QuoteApp(
                release_id=release.id,
                form_revision=release.form_revision,
                form=form,
                gate=gate,
                price=price_values,
            )
        )
    )

    @app.get("/", include_in_schema=False)
    @app.get(APP_PAGES[0], include_in_schema=False)
    @app.get(APP_PAGES[1], include_in_schema=False)
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
