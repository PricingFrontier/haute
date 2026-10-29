"""Utility script CRUD endpoints.

Manages Python files in the project's ``utility/`` directory.  These files
contain reusable helper functions, constants, and imports that pipeline
nodes can reference via the preamble (``from utility.<module> import *``).
A write is a project write like any other: staged and renamed into place under
``project_write``, checked against the revision the editor read, and captured
on the save ledger. See specs/server-api.
"""

from __future__ import annotations

import ast
import keyword
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import Path as PathParam

from haute._config_io import is_windows_reserved_filename
from haute._file_ops import atomic_create_text, atomic_write_text, file_revision
from haute._io import read_user_text
from haute._logging import get_logger
from haute._sandbox import contained_path
from haute.routes._helpers import discover_pipelines, pipeline_dir, project_write
from haute.routes._save_pipeline import capture_save_in_ledger
from haute.schemas import (
    UTILITY_MODULE_NAME_MAX,
    UtilityCreateRequest,
    UtilityDeleteResponse,
    UtilityFileItem,
    UtilityListResponse,
    UtilityReadResponse,
    UtilityWriteRequest,
    UtilityWriteResponse,
)

logger = get_logger(component="server.utility")

router = APIRouter(prefix="/api/utility", tags=["utility"])

_VALID_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# A module named in the path is bounded as one named in a body: an over-long name is a
# 422, never a write that fails for the file name's length.
_Module = Annotated[str, PathParam(max_length=UTILITY_MODULE_NAME_MAX)]


def _project_root() -> Path:
    """The project root ``pipeline_dir`` anchors the pipeline at: the working directory."""
    return Path.cwd().resolve()


def _utility_root(project_root: Path) -> Path:
    """The pipeline folder's ``utility/``, resolved, and refused (403) unless it lies inside
    the project: a link or junction at it, or above it, that leads outside would otherwise
    have every route here list, read and write the outside folder's files."""
    return contained_path(project_root, pipeline_dir() / "utility")


def _module_path(project_root: Path, module: str) -> Path:
    """``utility/<module>.py``, resolved inside the authenticated folder (403 otherwise)."""
    return contained_path(_utility_root(project_root), f"{module}.py")


def _display(project_root: Path, path: Path) -> str:
    """*path* relative to the project, POSIX: what messages and the ledger name."""
    return path.relative_to(project_root).as_posix()


def _refusal(status: int, code: str, message: str) -> HTTPException:
    """A refusal in the flat error contract: the message is the detail; the code is logged."""
    logger.info("utility_file_refused", code=code, status=status)
    return HTTPException(status_code=status, detail=message)


def _require_file(target: Path) -> None:
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {target.name}")


def _require_revision(project_root: Path, target: Path, revision: str) -> None:
    """Refuse (412) a write or delete made against a revision the file no longer has."""
    if file_revision(target.stat()) != revision:
        raise _refusal(
            412,
            "file_changed",
            f"{_display(project_root, target)} changed since you opened it. "
            "Open it again, then retry.",
        )


def _replace_or_remove(project_root: Path, target: Path, change: Callable[[], None]) -> None:
    """Run *change* on *target*, answering a file another program holds open with 409."""
    try:
        change()
    except PermissionError:
        raise _refusal(
            409,
            "file_in_use",
            f"{_display(project_root, target)} is open in Haute or another program. "
            "Try again once it's closed.",
        ) from None


def _validate_module_name(name: str) -> None:
    """Raise 400 if *name* is not a valid Python identifier or is reserved."""
    if not _VALID_NAME.match(name) or name.startswith("__"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid module name: '{name}'. Use only letters, digits, and underscores.",
        )


def _validate_new_module_name(name: str, utility_dir: Path) -> None:
    """Refuse a new module name that cannot be imported, or would break another checkout.

    A hard keyword cannot follow ``from utility.``; a Windows device name
    (``CON``, ``NUL``, ``COM1``) cannot be a file there; and a name equal to
    an existing module's ignoring case is one file on Windows and macOS, so a
    Linux checkout holding both would break on them. Every platform refuses
    all three, whatever its own file system allows.
    """
    if keyword.iskeyword(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid module name: '{name}' is a Python keyword, so "
            f"`from utility.{name} import *` could not be written. Choose another name.",
        )
    if is_windows_reserved_filename(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid module name: '{name}' is a reserved device name on Windows, "
            "so the file could not exist there. Choose another name.",
        )
    if utility_dir.is_dir():
        for entry in utility_dir.iterdir():
            if entry.suffix == ".py" and entry.stem.casefold() == name.casefold():
                raise HTTPException(
                    status_code=409,
                    detail=f"Utility file already exists: {entry.name}"
                    + (
                        ""
                        if entry.stem == name
                        else " (names differing only in case are one file on Windows and macOS)."
                    ),
                )


def _validate_syntax(content: str) -> tuple[bool, str | None, int | None]:
    """AST-parse *content* and return (ok, error_msg, error_line)."""
    try:
        ast.parse(content)
        return True, None, None
    except SyntaxError as e:
        return False, str(e), e.lineno


def _format_syntax_error(err_msg: str | None, err_line: int | None) -> str:
    """Build a user-facing flat string describing a syntax error.

    The line number is embedded in the string itself so the frontend can
    extract it with a ``/line (\\d+)/`` regex — structured ``{error,
    error_line}`` dict details are no longer part of the HTTP contract.
    ``str(SyntaxError)`` already contains ``" (<unknown>, line N)"`` so
    the result is de-duplicated on the first ``" (<unknown>, "`` marker.
    """
    msg = err_msg or "Invalid Python syntax"
    # ast.parse wraps the message with " (<unknown>, line N)" — strip
    # that tail so our own "line N" prefix isn't duplicated.
    marker = " (<unknown>,"
    if marker in msg:
        msg = msg.split(marker, 1)[0].rstrip()
    if err_line is None:
        return f"Syntax error: {msg}"
    return f"Syntax error on line {err_line}: {msg}"


def _refuse_new_name_violations(module: str, content: str) -> None:
    """Refuse a utility edit that would make a project pipeline's names collide.

    Every project pipeline is parsed with its support code read twice, as it
    is and with *module* holding *content*; only violations the edit adds are
    refused, naming the pipeline and the colliding node. A pipeline that does
    not parse is skipped: it fails on its own, whatever this file holds.
    """
    from haute._support_code_names import utility_reader
    from haute.errors import HauteError
    from haute.parser import parse_pipeline_source_with_name_violations

    base = pipeline_dir()
    current = utility_reader(base, Path.cwd().resolve())

    def edited(name: str) -> str | None:
        return content if name == module else current(name)

    problems: list[str] = []
    for pipeline_file in discover_pipelines():
        # A registered submodel may star-import the module though the root
        # file never names it, so every pipeline is parsed.
        source = read_user_text(pipeline_file)
        try:
            violations = [
                {
                    violation.message()
                    for violation in parse_pipeline_source_with_name_violations(
                        source,
                        source_file=str(pipeline_file),
                        _base_dir=pipeline_file.parent,
                        _read_utility_source=reader,
                    )[1]
                }
                for reader in (current, edited)
            ]
        except (HauteError, OSError, UnicodeError) as exc:
            logger.info("utility_check_pipeline_skipped", path=str(pipeline_file), error=str(exc))
            continue
        before, after = violations
        added = sorted(after - before)
        if added:
            problems.append(f"Pipeline {pipeline_file.name!r}: " + " ".join(added))
    if problems:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Saving utility/{module}.py would break names in a pipeline that imports it. "
                "Nothing was saved. " + " ".join(problems)
            ),
        )


_INIT_CONTENT = '"""Project-level utilities \u2014 reusable functions for pipeline nodes."""\n'


def _ensure_init(utility_dir: Path) -> list[Path]:
    """Create ``utility/__init__.py`` when it is missing; the files this created."""
    init = utility_dir / "__init__.py"
    if init.exists():
        return []
    try:
        atomic_create_text(init, _INIT_CONTENT)
    except FileExistsError:
        # Another program made it meanwhile: the package has its marker either way.
        return []
    return [init]


def _write_response(
    project_root: Path, module: str, target: Path, written: list[Path]
) -> UtilityWriteResponse:
    warnings: list[str] = []
    captured = capture_save_in_ledger(
        project_root, [_display(project_root, path) for path in written], warnings
    )
    return UtilityWriteResponse(
        status="ok",
        name=f"{module}.py",
        module=module,
        import_line=f"from utility.{module} import *",
        revision=file_revision(target.stat()),
        warnings=warnings,
        **captured._asdict(),
    )


@router.get("", response_model=UtilityListResponse)
async def list_utility_files() -> UtilityListResponse:
    """List all Python files in ``utility/`` (excluding ``__init__.py``)."""
    d = _utility_root(_project_root())
    if not d.is_dir():
        return UtilityListResponse(files=[])

    files: list[UtilityFileItem] = []
    for entry in sorted(d.iterdir()):
        if entry.suffix == ".py" and entry.name != "__init__.py":
            files.append(UtilityFileItem(name=entry.name, module=entry.stem))
    return UtilityListResponse(files=files)


@router.get("/{module}", response_model=UtilityReadResponse)
async def read_utility_file(module: _Module) -> UtilityReadResponse:
    """Read the content of a utility file, with the revision a write must send."""
    _validate_module_name(module)
    target = _module_path(_project_root(), module)
    _require_file(target)
    # The revision is read before the content: a change between the two then refuses
    # the editor's next write (412) instead of letting it overwrite that change.
    revision = file_revision(target.stat())
    content = read_user_text(target)
    return UtilityReadResponse(
        name=f"{module}.py", module=module, content=content, revision=revision
    )


def _refuse_syntax_error(module: str, content: str) -> None:
    ok, err_msg, err_line = _validate_syntax(content)
    if not ok:
        logger.warning(
            "utility_syntax_error",
            module=module,
            error=err_msg,
            error_line=err_line,
        )
        raise HTTPException(
            status_code=400,
            detail=_format_syntax_error(err_msg, err_line),
        )


def _create(project_root: Path, module: str, content: str) -> UtilityWriteResponse:
    utility_dir = _utility_root(project_root)
    # Containment first: a path escaping the utility folder is refused as such.
    target = contained_path(utility_dir, f"{module}.py")
    _validate_new_module_name(module, utility_dir)
    _refuse_syntax_error(module, content)
    _refuse_new_name_violations(module, content)
    utility_dir.mkdir(exist_ok=True)
    written = _ensure_init(utility_dir)
    try:
        atomic_create_text(target, content)
    except FileExistsError:
        raise _refusal(
            409, "file_exists", f"{_display(project_root, target)} already exists."
        ) from None
    logger.info("utility_file_created", module=module)
    return _write_response(project_root, module, target, [*written, target])


@router.post("", response_model=UtilityWriteResponse)
async def create_utility_file(body: UtilityCreateRequest) -> UtilityWriteResponse:
    """Create a new utility file in ``utility/``."""
    _validate_module_name(body.name)
    content = body.content or f'"""Utility module: {body.name}."""\n\nimport polars as pl\n'
    project_root = _project_root()
    return await project_write(project_root, lambda: _create(project_root, body.name, content))


def _update(project_root: Path, module: str, body: UtilityWriteRequest) -> UtilityWriteResponse:
    target = _module_path(project_root, module)
    _require_file(target)
    _refuse_syntax_error(module, body.content)
    _refuse_new_name_violations(module, body.content)
    _require_revision(project_root, target, body.revision)
    _replace_or_remove(project_root, target, lambda: atomic_write_text(target, body.content))
    logger.info("utility_file_updated", module=module)
    return _write_response(project_root, module, target, [target])


@router.put("/{module}", response_model=UtilityWriteResponse)
async def update_utility_file(module: _Module, body: UtilityWriteRequest) -> UtilityWriteResponse:
    """Update an existing utility file, if it is still the revision the editor read."""
    _validate_module_name(module)
    project_root = _project_root()
    return await project_write(project_root, lambda: _update(project_root, module, body))


def _delete(project_root: Path, module: str, revision: str) -> UtilityDeleteResponse:
    target = _module_path(project_root, module)
    _require_file(target)
    _require_revision(project_root, target, revision)
    _replace_or_remove(project_root, target, target.unlink)
    warnings: list[str] = []
    captured = capture_save_in_ledger(project_root, [_display(project_root, target)], warnings)
    logger.info("utility_file_deleted", module=module)
    return UtilityDeleteResponse(module=module, warnings=warnings, **captured._asdict())


@router.delete("/{module}", response_model=UtilityDeleteResponse)
async def delete_utility_file(
    module: _Module, revision: Annotated[str, Query(max_length=512)]
) -> UtilityDeleteResponse:
    """Delete a utility file, if it is still the revision the editor read."""
    _validate_module_name(module)
    project_root = _project_root()
    return await project_write(project_root, lambda: _delete(project_root, module, revision))
