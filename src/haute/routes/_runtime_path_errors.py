"""HTTP mapping for typed runtime-path validation failures."""

from __future__ import annotations

from fastapi import HTTPException

from haute._path_resolution import (
    MalformedRuntimePathError,
    RuntimePathError,
    RuntimePathOutsideProjectError,
)


def runtime_path_http_exception(exc: RuntimePathError) -> HTTPException:
    """Map a typed path failure without inspecting platform-dependent text."""
    if isinstance(exc, MalformedRuntimePathError):
        status_code = 400
    elif isinstance(exc, RuntimePathOutsideProjectError):
        status_code = 403
    else:
        raise TypeError(f"Unsupported runtime path error: {type(exc).__name__}")
    return HTTPException(status_code=status_code, detail=str(exc))
