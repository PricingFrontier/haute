"""Mutation witnesses for ``src/haute/routes/json_cache.py`` helper functions.

Each test pins the REAL behaviour of a directly-callable route helper so that a
Cosmic Ray mutation of the targeted line flips an assertion (killing the mutant).
The route HANDLER bodies (build/status/infer/delete) need the FastAPI app and are
covered by the integration suites (test_json_cache_*.py); these unit witnesses
cover the helper layer the handlers delegate to. Kill targets are named per test.

Verified killable out-of-band by applying each operator mutation in-memory and
confirming the helper's output changes (see .scratch/json-cache/verify.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from haute.routes.json_cache import (
    _aggregate_v2_build_response,
    _aggregate_v2_status_response,
    _finish_build_progress,
    _get_build_progress,
    _resolve_config_path,
    _resolve_data_path,
    _start_build_progress,
)

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


def test_resolve_config_path_outside_root_raises_403(tmp_path: Path) -> None:
    """L172 ``enforce_project_root=True``, L176 ``status_code=403``, and L175
    ``except ValueError`` — same shape as the data-path helper but config-side.
    """
    with pytest.raises(HTTPException) as ei:
        _resolve_config_path(str(tmp_path / "outside.json"))
    assert ei.value.status_code == 403


# ── build-progress accounting ────────────────────────────────────────


def test_progress_balanced_start_finish_clears(tmp_path: Path) -> None:
    """One start + one finish must clear the entry -> ``active is False``.

    Kills: L74 initial ``"active_count": 1`` (->2 leaves remaining 1 after
    finish), L88 ``remaining = active_count - 1`` (Sub-> any op that doesn't
    reach 0), and L89 ``if remaining <= 0`` (<= -> < / > / != all leave the
    entry un-popped at remaining 0).
    """
    key = str(tmp_path / "d.json")
    _start_build_progress(key)
    _finish_build_progress(key)
    assert _get_build_progress(key).active is False


def test_progress_two_start_one_finish_stays_active(tmp_path: Path) -> None:
    """Two starts + one finish must remain active (one builder still running).

    Kills L79 ``current["active_count"] = int(...) + 1`` (Add-> any op that
    doesn't yield 2 makes the single finish drop the count to <=0 and pop).
    """
    key = str(tmp_path / "d.json")
    _start_build_progress(key)
    _start_build_progress(key)
    _finish_build_progress(key)
    assert _get_build_progress(key).active is True
    _finish_build_progress(key)  # cleanup


def test_progress_rows_zero_and_elapsed_small_after_start(tmp_path: Path) -> None:
    """Immediately after start: rows == 0 and elapsed is sub-second.

    Kills L75 initial ``"rows": 0`` (->1) and L101
    ``max(0.0, monotonic() - started_at)`` — the ``-``->``+`` flip makes elapsed
    ~2x monotonic (huge) and the ``0.0``->``1.0`` floor change forces elapsed to
    1.0, both breaking ``elapsed < 1.0``.
    """
    key = str(tmp_path / "d.json")
    _start_build_progress(key)
    prog = _get_build_progress(key)
    assert prog.rows == 0
    assert prog.elapsed < 1.0
    _finish_build_progress(key)  # cleanup


# ── build/status aggregation (default counts + skipped) ──────────────


def test_aggregate_build_missing_counts_default_zero(tmp_path: Path) -> None:
    """A table missing ``row_count``/``column_count`` and an empty ``skipped``
    must aggregate to all-zero.

    Kills L269 ``int(t.get("row_count", 0))``, L270 ``int(t.get("column_count",
    0))``, L283 ``range(int(table.get("column_count", 0)))``, L273
    ``cached_at = 0.0`` (no parquet on disk), L295
    ``int(skipped.get("records", 0))`` — each ``0``->non-zero flips an assertion.
    """
    summary = {"tables": [{"label": "t", "parquet": None}], "skipped": {}}
    resp = _aggregate_v2_build_response(summary, tmp_path, "/d.json", 1.0)
    assert resp.row_count == 0
    assert resp.column_count == 0
    assert len(resp.columns) == 0
    assert resp.cached_at == 0.0
    assert resp.skipped_records == 0


def test_aggregate_build_present_counts_summed(tmp_path: Path) -> None:
    """Present counts must flow through: row_count/column_count summed, one
    column entry per declared column, skipped_records read from the payload.
    Pins the non-default path so a mutant can't pass by zeroing everything.
    """
    summary = {
        "tables": [
            {
                "label": "t",
                "parquet": None,
                "row_count": 3,
                "column_count": 2,
                "columns": {"id": "Int64", "name": "String"},
            }
        ],
        "skipped": {"records": 5, "rows_by_table": {}},
    }
    resp = _aggregate_v2_build_response(summary, tmp_path, "/d.json", 1.0)
    assert resp.row_count == 3
    assert resp.column_count == 2
    assert resp.columns == {"t.id": "Int64", "t.name": "String"}
    assert resp.skipped_records == 5


def test_aggregate_status_missing_counts_default_zero(tmp_path: Path) -> None:
    """Status-side mirror of the build aggregation zero-defaults.

    Kills L313 ``int(t.get("row_count", 0))``, L314 ``int(t.get("column_count",
    0))``, L327 ``range(int(table.get("column_count", 0)))``, L317
    ``cached_at = 0.0``, L339 ``int(skipped.get("records", 0))``.
    """
    meta = {"tables": [{"label": "t", "parquet": None}]}
    resp = _aggregate_v2_status_response(tmp_path, "/d.json", meta)
    assert resp.row_count == 0
    assert resp.column_count == 0
    assert len(resp.columns) == 0
    assert resp.cached_at == 0.0
    assert resp.skipped_records == 0
