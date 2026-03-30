"""Utility script CRUD endpoints.

Manages Python files in the project's ``utility/`` directory.  These files
contain reusable helper functions, constants, and imports that pipeline
nodes can reference via the preamble (``from utility.<module> import *``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from haute._io import read_user_text
from haute._logging import get_logger
from haute.routes._helpers import pipeline_dir, validate_safe_path
from haute.schemas import (
    UtilityCreateRequest,
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


def _validate_syntax(content: str) -> tuple[bool, str | None, int | None]:
    """AST-parse *content* and return (ok, error_msg, error_line)."""
    try:
        ast.parse(content)
        return True, None, None
    except SyntaxError as e:
        return False, str(e), e.lineno


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
    target = validate_safe_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    content = read_user_text(target)
    return UtilityReadResponse(name=f"{module}.py", module=module, content=content)


@router.post("", response_model=UtilityWriteResponse)
async def create_utility_file(body: UtilityCreateRequest) -> UtilityWriteResponse:
    """Create a new utility file in ``utility/``."""
    _validate_module_name(body.name)

    d = _utility_dir()
    d.mkdir(exist_ok=True)
    _ensure_init(d)

    target = validate_safe_path(d, f"{body.name}.py")
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Utility file already exists: {body.name}.py",
        )

    content = body.content or f'"""Utility module: {body.name}."""\n\nimport polars as pl\n'

    ok, err_msg, err_line = _validate_syntax(content)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": err_msg,
                "error_line": err_line,
            },
        )

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
    target = validate_safe_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    ok, err_msg, err_line = _validate_syntax(body.content)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error": err_msg,
                "error_line": err_line,
            },
        )

    target.write_text(body.content, encoding="utf-8")
    logger.info("utility_file_updated", module=module)

    return UtilityWriteResponse(
        status="ok",
        name=f"{module}.py",
        module=module,
        import_line=f"from utility.{module} import *",
    )


@router.delete("/{module}")
async def delete_utility_file(module: str) -> dict[str, str]:
    """Delete a utility file."""
    _validate_module_name(module)
    base = _utility_dir()
    target = validate_safe_path(base, f"{module}.py")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Utility file not found: {module}.py")

    target.unlink()
    logger.info("utility_file_deleted", module=module)
    return {"status": "ok", "module": module}
