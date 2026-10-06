"""Mutation witnesses for ``src/haute/routes/json_cache.py`` helper functions.

Each test pins the REAL behaviour of a directly-callable route helper so that a
Cosmic Ray mutation of the targeted line flips an assertion (killing the mutant).
The route handler (``/infer``) needs the FastAPI app and is covered by the
integration suites (test_json_cache_*.py); these unit witnesses cover the
helper layer the handler delegates to. Kill targets are named per test.

Verified killable out-of-band by applying each operator mutation in-memory and
confirming the helper's output changes (see .scratch/json-cache/verify.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from haute.routes.json_cache import _resolve_data_path

# ── path resolution: traversal + null-byte (security + status contract) ──


def test_resolve_data_path_outside_root_raises_403(tmp_path: Path) -> None:
    """L154 ``enforce_project_root=True`` (True->False would resolve instead of
    raise), L158 ``else 403`` (number), and L157 ``except ValueError`` (changing
    the caught type lets ValueError escape instead of becoming HTTPException).

    ``tmp_path`` is an absolute path outside the worktree root, so
    ``resolve_runtime_file_path`` raises ValueError -> HTTPException(403).
    """
    with pytest.raises(HTTPException) as ei:
        _resolve_data_path(str(tmp_path / "outside.json"))
    assert ei.value.status_code == 403


def test_resolve_data_path_null_byte_raises_400() -> None:
    """Typed malformed-path failures map to 400 without scraping their message.

    An embedded NUL makes ``resolve_runtime_file_path`` raise
    ``MalformedRuntimePathError`` and the shared route adapter selects 400.
    """
    with pytest.raises(HTTPException) as ei:
        _resolve_data_path("data\x00.json")
    assert ei.value.status_code == 400
