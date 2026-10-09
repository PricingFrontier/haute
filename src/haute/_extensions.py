"""Extensions: installed packages that add a view to ``haute serve`` beside the pipeline editor.

A package declares one in the ``haute.extensions`` entry-point group; Obverse, the
underwriting front end, is the first. Haute never imports an extension by name. The object
an entry point loads (Obverse's is a module) declares:

- ``label``: the name the view switcher shows;
- ``create_router(project_dir)``: a FastAPI router, mounted at ``/api/extensions/<name>``;
- ``assets_dir``: a directory served at ``/extensions/<name>/``;
- ``entry``: the browser module in ``assets_dir``, whose ``mount`` function renders the
  view into the editor's page (``frontend/src/extensions/loadExtensionModule.ts``);
- optionally ``quote_tables(project_dir)``: the Quote Input's tables, in its v2 shape, as
  the extension's files define them now. At most one installed extension may supply them;
  ``GET /api/quote-tables`` serves them, checked against the Quote Input's schema rules;
- optionally, beside ``quote_tables``, ``quote_sample(project_dir)``: one quote as a request
  holds it, as the extension's files define it now, which ``GET /api/quote-tables`` serves
  as ``sample`` and a Workbench Input previews on;
- optionally, beside ``quote_tables``, ``response_tables(project_dir)``: the tables a priced
  quote fills in, in the same v2 shape, which ``GET /api/quote-tables`` serves as
  ``response_tables``, checked as the tables are, and a Workbench Output fills.

``haute.server`` mounts every installed extension when it is imported, before its catch-all
routes, which would otherwise answer the extension's ``GET`` requests.
"""

from __future__ import annotations

import functools
import importlib.util
import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._logging import get_logger
from haute.routes.json_cache import api_input_schema_error_response
from haute.schemas import ExtensionInfo, ExtensionsResponse, QuoteTablesResponse

EXTENSIONS_GROUP = "haute.extensions"
# The editor's own view in the switcher.
RESERVED_NAMES = frozenset({"pipeline"})

_NAME = re.compile(r"[a-z][a-z0-9-]*\Z")
_MISSING = object()

_T = TypeVar("_T")

logger = get_logger(component="server.extensions")


class ExtensionError(RuntimeError):
    """An installed extension that cannot be loaded."""


@dataclass(frozen=True)
class Extension:
    """A loaded extension: its entry-point name and what it declared."""

    name: str
    label: str
    router: APIRouter
    assets_dir: Path
    entry: str
    # ``quote_tables`` bound to the project directory, when the extension supplies them.
    quote_tables: Callable[[], object] | None = None
    # ``quote_sample`` bound to the project directory, when it also supplies a sample quote.
    quote_sample: Callable[[], object] | None = None
    # ``response_tables`` bound to the project directory, when it also supplies the
    # response's tables.
    response_tables: Callable[[], object] | None = None

    @property
    def api_base(self) -> str:
        return f"/api/extensions/{self.name}"

    @property
    def assets_url(self) -> str:
        return f"/extensions/{self.name}"

    @property
    def entry_url(self) -> str:
        return f"{self.assets_url}/{self.entry}"


def discover_extensions(project_dir: Path) -> list[Extension]:
    """Load every installed extension, in name order, for the project in ``project_dir``."""
    found = sorted(entry_points(group=EXTENSIONS_GROUP), key=lambda ep: (ep.name, ep.value))
    names = [ep.name for ep in found]
    for name in sorted({name for name in names if names.count(name) > 1}):
        declared_by = ", ".join(ep.value for ep in found if ep.name == name)
        raise ExtensionError(
            f"Haute extension {name!r} is declared by more than one installed package: "
            f"{declared_by}. Uninstall one of them."
        )
    extensions = [_load(entry_point, project_dir) for entry_point in found]
    suppliers = [extension for extension in extensions if extension.quote_tables is not None]
    if len(suppliers) > 1:
        named = ", ".join(repr(extension.name) for extension in suppliers)
        raise ExtensionError(
            f"Haute extensions {named} each supply the Quote Input's tables; a project can "
            "take them from one. Uninstall all but one of them."
        )
    return extensions


def _load(entry_point: EntryPoint, project_dir: Path) -> Extension:
    where = f"Haute extension {entry_point.name!r} ({entry_point.value})"
    if not _NAME.match(entry_point.name) or entry_point.name in RESERVED_NAMES:
        raise ExtensionError(
            f"{where}: an extension name is lower-case letters, digits and hyphens, "
            f"starts with a letter, and is not {', '.join(sorted(RESERVED_NAMES))}."
        )
    try:
        declared = entry_point.load()
    except Exception as exc:
        raise ExtensionError(f"{where} could not be imported: {exc}") from exc

    label = _attribute(declared, "label", str, where).strip()
    if not label:
        raise ExtensionError(f"{where}: label is empty.")
    assets_dir = _attribute(declared, "assets_dir", Path, where)
    entry = _attribute(declared, "entry", str, where)
    if entry in {"", ".", ".."} or "/" in entry or "\\" in entry:
        raise ExtensionError(f"{where}: entry must be a file name in assets_dir, not {entry!r}.")
    create_router = getattr(declared, "create_router", None)
    if not callable(create_router):
        raise ExtensionError(f"{where} does not define create_router(project_dir).")
    try:
        router = create_router(project_dir)
    except Exception as exc:
        raise ExtensionError(f"{where}: create_router failed: {exc}") from exc
    if not isinstance(router, APIRouter):
        raise ExtensionError(
            f"{where}: create_router returned {type(router).__name__}, not a FastAPI APIRouter."
        )
    supplied = _bound(declared, "quote_tables", project_dir, where)
    sample = _bound(declared, "quote_sample", project_dir, where)
    if sample is not None and supplied is None:
        raise ExtensionError(
            f"{where} defines quote_sample without quote_tables: a sample quote is read "
            "through the tables, so define both."
        )
    response = _bound(declared, "response_tables", project_dir, where)
    if response is not None and supplied is None:
        raise ExtensionError(
            f"{where} defines response_tables without quote_tables: the response answers "
            "the quote the tables define, so define both."
        )
    return Extension(entry_point.name, label, router, assets_dir, entry, supplied, sample, response)


def _bound(
    declared: object, name: str, project_dir: Path, where: str
) -> Callable[[], object] | None:
    """The optional function *name* bound to the project directory, or ``None``."""
    function = getattr(declared, name, None)
    if function is None:
        return None
    if not callable(function):
        raise ExtensionError(
            f"{where}: {name} must be a function of the project directory, "
            f"not a {type(function).__name__}."
        )
    return functools.partial(function, project_dir)


def _attribute(declared: object, name: str, kind: type[_T], where: str) -> _T:
    value = getattr(declared, name, _MISSING)
    if value is _MISSING:
        raise ExtensionError(f"{where} does not define {name}.")
    if not isinstance(value, kind):
        actual = type(value).__name__
        raise ExtensionError(f"{where}: {name} must be a {kind.__name__}, not {actual}.")
    return value


def mount_extensions(app: FastAPI, extensions: Sequence[Extension]) -> None:
    """Mount each extension's API and assets, and ``GET /api/extensions``.

    Call it before the app's catch-all routes: Starlette tries routes in order.
    """
    for extension in extensions:
        app.include_router(extension.router, prefix=extension.api_base)
        app.include_router(_assets_router(extension))

    listing = APIRouter(prefix="/api", tags=["extensions"])
    supplier = next((extension for extension in extensions if extension.quote_tables), None)

    @listing.get("/extensions", response_model=ExtensionsResponse)
    async def list_extensions() -> ExtensionsResponse:
        """The installed extensions, and whether each one's browser module is built."""
        return ExtensionsResponse(extensions=[_info(extension) for extension in extensions])

    @listing.get("/quote-tables", response_model=QuoteTablesResponse)
    async def quote_tables() -> QuoteTablesResponse | JSONResponse:
        """The Quote Input's tables from the extension that supplies them, as they are now."""
        if supplier is None or supplier.quote_tables is None:
            raise HTTPException(404, "No installed extension supplies the Quote Input's tables.")
        return await _supplied_tables(supplier)

    app.include_router(listing)


async def _supplied_tables(supplier: Extension) -> QuoteTablesResponse | JSONResponse:
    name = supplier.name
    assert supplier.quote_tables is not None
    tables = await _supplied_table_list(name, supplier.quote_tables, "the Quote Input's tables")
    response_tables: list[dict[str, Any]] = []
    if supplier.response_tables is not None:
        response_tables = await _supplied_table_list(
            name, supplier.response_tables, "the response's tables"
        )
    try:
        validate_v2_schema({"tables": tables})
        validate_v2_schema({"tables": response_tables})
    except ApiInputSchemaError as exc:
        return api_input_schema_error_response(exc)
    # Not checked against the tables here: a sample that doesn't fit fails the previews
    # that read it, and never stops the tables updating.
    sample = (
        None
        if supplier.quote_sample is None
        else await _supplied_sample(name, supplier.quote_sample)
    )
    return QuoteTablesResponse(
        extension=name, tables=tables, sample=sample or {}, response_tables=response_tables
    )


async def _supplied_table_list(
    name: str, supply: Callable[[], object], what: str
) -> list[dict[str, Any]]:
    """The tables *supply* returns, run off the event loop, or a 500 saying why not."""
    try:
        # The extension reads its own files, so keep it off the event loop.
        tables = await run_in_threadpool(supply)
    except Exception as exc:
        logger.exception("extension_quote_tables_failed", extension=name, supplying=what)
        raise HTTPException(500, f"Extension {name!r} could not supply {what}: {exc}") from exc
    if not isinstance(tables, list):
        raise HTTPException(
            500,
            f"Extension {name!r} supplied {what} as a {type(tables).__name__}, not a list.",
        )
    return tables


async def _supplied_sample(name: str, quote_sample: Callable[[], object]) -> dict[str, Any] | None:
    try:
        sample = await run_in_threadpool(quote_sample)
    except Exception as exc:
        logger.exception("extension_quote_sample_failed", extension=name)
        raise HTTPException(
            500, f"Extension {name!r} could not supply its sample quote: {exc}"
        ) from exc
    if sample is not None and not isinstance(sample, dict):
        raise HTTPException(
            500,
            f"Extension {name!r} supplied its sample quote as a {type(sample).__name__}, "
            "not an object.",
        )
    return sample


def _assets_router(extension: Extension) -> APIRouter:
    # Served outside the session gate, like the editor's own built assets. Not a
    # StaticFiles mount: that fails with a 500 until the directory exists, and an
    # extension's front end may be built after the server starts.
    router = APIRouter(tags=["extensions"])

    @router.get(
        f"{extension.assets_url}/{{path:path}}", response_model=None, include_in_schema=False
    )
    async def extension_asset(path: str) -> FileResponse:
        root = extension.assets_dir.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HTTPException(404, f"No such file in extension {extension.name!r}: {path}")
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        # Revalidate every load, so a rebuilt front end shows on the next page load.
        return FileResponse(candidate, media_type=media_type, headers={"Cache-Control": "no-cache"})

    return router


def _info(extension: Extension) -> ExtensionInfo:
    entry_file = extension.assets_dir / extension.entry
    ready = entry_file.is_file()
    return ExtensionInfo(
        name=extension.name,
        label=extension.label,
        api_base=extension.api_base,
        entry_url=extension.entry_url,
        ready=ready,
        detail=None
        if ready
        else f"{entry_file} does not exist. Build {extension.label}'s front end, then reload.",
        quote_tables=extension.quote_tables is not None,
        response_tables=extension.response_tables is not None,
    )


def extension_package_dirs() -> list[Path]:
    """The package directories of installed extensions, found without importing them.

    Dev mode's reloader watches them, so editing an extension restarts the server. An
    extension whose top-level module is not a package adds nothing: watching the directory
    holding a lone module would mean watching site-packages.
    """
    dirs: list[Path] = []
    for entry_point in sorted(entry_points(group=EXTENSIONS_GROUP), key=lambda ep: ep.name):
        spec = importlib.util.find_spec(entry_point.module.partition(".")[0])
        if spec is not None and spec.submodule_search_locations:
            dirs.extend(Path(location) for location in spec.submodule_search_locations)
    return dirs
