"""Utility script CRUD endpoints.

Manages Python files in the project's ``utility/`` directory.  These files
contain reusable helper functions, constants, and imports that pipeline
nodes can reference via the preamble (``from utility.<module> import *``).
"""

from __future__ import annotations

import ast
import keyword
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from haute._config_io import is_windows_reserved_filename
from haute._io import read_user_text
from haute._logging import get_logger
from haute._sandbox import contained_path
from haute.routes._helpers import discover_pipelines, pipeline_dir
from haute.schemas import (
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


def _utility_dir() -> Path:
    """Return the ``utility/`` directory under the project root."""
    return pipeline_dir() / "utility"


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

    Each project pipeline is parsed with its support code read twice, as it
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
        source = read_user_text(pipeline_file)
        if "utility" not in source:
            continue
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


def _ensure_init(utility_dir: Path) -> None:
    """Create ``utility/__init__.py`` if it doesn't exist."""
    init = utility_dir / "__init__.py"
    if not init.exists():
        init.write_text(
            '"""Project-level utilities \u2014 reusable functions for pipeline nodes."""\n',
            encoding="utf-8",
        )


@router.get("", response_model=UtilityListResponse)
async def list_utility_files() -> UtilityListResponse:
    """List all Python files in ``utility/`` (excluding ``__init__.py``)."""
    d = _utility_dir()
    if not d.is_dir():
        return UtilityListResponse(files=[])

    files: list[UtilityFileItem] = []
    for entry in sorted(d.iterdir()):
        if entry.suffix == ".py" and entry.name != "__init__.py":
            files.append(UtilityFileItem(name=entry.name, module=entry.stem))
    return UtilityListResponse(files=files)


@router.get("/{module}", response_model=UtilityReadResponse)
async def read_utility_file(module: str) -> UtilityReadResponse:
    """Read the content of a utility file."""
    _validate_module_name(module)
    base = _utility_dir()
    target = contained_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    content = read_user_text(target)
    return UtilityReadResponse(name=f"{module}.py", module=module, content=content)


@router.post("", response_model=UtilityWriteResponse)
async def create_utility_file(body: UtilityCreateRequest) -> UtilityWriteResponse:
    """Create a new utility file in ``utility/``."""
    _validate_module_name(body.name)
    d = _utility_dir()
    _validate_new_module_name(body.name, d)

    d.mkdir(exist_ok=True)
    _ensure_init(d)

    target = contained_path(d, f"{body.name}.py")

    content = body.content or f'"""Utility module: {body.name}."""\n\nimport polars as pl\n'

    ok, err_msg, err_line = _validate_syntax(content)
    if not ok:
        logger.warning(
            "utility_syntax_error",
            module=body.name,
            error=err_msg,
            error_line=err_line,
        )
        raise HTTPException(
            status_code=400,
            detail=_format_syntax_error(err_msg, err_line),
        )

    _refuse_new_name_violations(body.name, content)
    target.write_text(content, encoding="utf-8")
    import_line = f"from utility.{body.name} import *"
    logger.info("utility_file_created", module=body.name)

    return UtilityWriteResponse(
        status="ok",
        name=f"{body.name}.py",
        module=body.name,
        import_line=import_line,
    )


@router.put("/{module}", response_model=UtilityWriteResponse)
async def update_utility_file(module: str, body: UtilityWriteRequest) -> UtilityWriteResponse:
    """Update an existing utility file."""
    _validate_module_name(module)
    base = _utility_dir()
    target = contained_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    ok, err_msg, err_line = _validate_syntax(body.content)
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

    _refuse_new_name_violations(module, body.content)
    target.write_text(body.content, encoding="utf-8")
    logger.info("utility_file_updated", module=module)

    return UtilityWriteResponse(
        status="ok",
        name=f"{module}.py",
        module=module,
        import_line=f"from utility.{module} import *",
    )


@router.delete("/{module}", response_model=UtilityDeleteResponse)
async def delete_utility_file(module: str) -> UtilityDeleteResponse:
    """Delete a utility file."""
    _validate_module_name(module)
    base = _utility_dir()
    target = contained_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    target.unlink()
    logger.info("utility_file_deleted", module=module)
    return UtilityDeleteResponse(module=module)
