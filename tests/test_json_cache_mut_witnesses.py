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

from dataclasses import FrozenInstanceError
from inspect import Parameter, signature
from pathlib import Path

import pytest
from fastapi import HTTPException

import haute.routes.json_cache as json_cache_module
from haute._json_shred import PreparedPerPortCacheBuild
from haute._worker_isolation import IsolatedWorkerRemoteError
from haute.routes.json_cache import (
    _aggregate_v2_build_response,
    _aggregate_v2_status_response,
    _build_timeout,
    _finish_build_progress,
    _get_build_progress,
    _isolated_memory_detail,
    _JsonCacheWorkerOutcome,
    _resolve_config_path,
    _resolve_data_path,
    _start_build_progress,
    _validate_worker_prepared_manifest,
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


def test_build_timeout_default_is_part_of_the_route_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAUTE_BUILD_TIMEOUT", raising=False)
    assert _build_timeout() == 1800.0


def test_worker_outcome_is_frozen_and_slotted() -> None:
    outcome = _JsonCacheWorkerOutcome()
    assert not hasattr(outcome, "__dict__")
    with pytest.raises(FrozenInstanceError):
        outcome.detail = "changed"  # type: ignore[misc]


def test_worker_manifest_parent_evidence_remains_keyword_only() -> None:
    parameters = signature(_validate_worker_prepared_manifest).parameters
    assert parameters["candidate"].kind is Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["data_path"].kind is Parameter.KEYWORD_ONLY
    assert parameters["cache_dir"].kind is Parameter.KEYWORD_ONLY
    assert parameters["staging_dir"].kind is Parameter.KEYWORD_ONLY


def test_worker_noop_manifest_rejects_object_that_compares_equal_to_none(
    tmp_path: Path,
) -> None:
    class _PretendsToBeNone:
        def __eq__(self, other: object) -> bool:
            return other is None

        def __ne__(self, other: object) -> bool:
            return other is not None

    data_path = tmp_path / "source.json"
    cache_dir = tmp_path / "cache"
    staging_dir = tmp_path / "stage"
    candidate = PreparedPerPortCacheBuild(
        data_path=str(data_path.resolve()),
        cache_dir=str(cache_dir.resolve()),
        staging_dir=_PretendsToBeNone(),  # type: ignore[arg-type]
        schema_fingerprint="schema",
        data_file_signature={},
        summary={},
        no_op=True,
    )

    with pytest.raises(ValueError, match="no-op named a staging directory"):
        _validate_worker_prepared_manifest(
            candidate,
            data_path=str(data_path),
            cache_dir=cache_dir,
            staging_dir=staging_dir,
        )


def test_worker_manifest_requires_a_builtin_bool_without_overloaded_type_equality(
    tmp_path: Path,
) -> None:
    class _BoolLikeMeta(type):
        def __eq__(cls, other: object) -> bool:
            return other is bool

        def __ne__(cls, other: object) -> bool:
            return other is not bool

    class _BoolLike(metaclass=_BoolLikeMeta):
        pass

    data_path = tmp_path / "source.json"
    cache_dir = tmp_path / "cache"
    staging_dir = tmp_path / "stage"
    candidate = PreparedPerPortCacheBuild(
        data_path=str(data_path.resolve()),
        cache_dir=str(cache_dir.resolve()),
        staging_dir=None,
        schema_fingerprint="schema",
        data_file_signature={},
        summary={},
        no_op=_BoolLike(),  # type: ignore[arg-type]
    )

    with pytest.raises(TypeError, match="non-boolean no_op"):
        _validate_worker_prepared_manifest(
            candidate,
            data_path=str(data_path),
            cache_dir=cache_dir,
            staging_dir=staging_dir,
        )


@pytest.mark.parametrize(
    ("remote_type", "expected_reason"),
    [
        (
            "".join(("NativeMemoryLimit", "UnsupportedError")),
            "native_memory_cap_unavailable",
        ),
        ("A", "worker_memory_limit"),
        ("Z", "worker_memory_limit"),
    ],
)
def test_isolated_memory_detail_uses_exact_remote_failure_type(
    remote_type: str,
    expected_reason: str,
) -> None:
    exc = IsolatedWorkerRemoteError(
        remote_type=remote_type,
        remote_message="detail",
        remote_traceback="trace",
    )
    assert _isolated_memory_detail(exc, memory_limit_bytes=123)["reason"] == expected_reason


def test_progress_rounds_elapsed_to_one_decimal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((100.0, 101.26))
    monkeypatch.setattr(json_cache_module.time, "monotonic", lambda: next(ticks))
    key = str(tmp_path / "timed.json")
    _start_build_progress(key)
    try:
        assert _get_build_progress(key).elapsed == 1.3
    finally:
        _finish_build_progress(key)


def test_three_progress_starts_and_finishes_balance_exactly(tmp_path: Path) -> None:
    key = str(tmp_path / "three.json")
    for _ in range(3):
        _start_build_progress(key)
    for _ in range(3):
        _finish_build_progress(key)
    assert _get_build_progress(key).active is False


def test_finish_decrements_one_builder_at_a_time(tmp_path: Path) -> None:
    key = str(tmp_path / "decrement.json")
    for _ in range(3):
        _start_build_progress(key)
    _finish_build_progress(key)
    _finish_build_progress(key)
    assert _get_build_progress(key).active is True
    _finish_build_progress(key)


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


def test_aggregate_build_rejects_whole_malformed_column_mapping(tmp_path: Path) -> None:
    summary = {
        "tables": [
            {
                "label": "t",
                "parquet": None,
                "columns": {"valid": "Int64", 1: "String"},
            }
        ],
        "skipped": {},
    }
    response = _aggregate_v2_build_response(summary, tmp_path, "/d.json", 1.0)
    assert response.columns == {}


def test_aggregate_build_rounds_elapsed_to_three_decimals(tmp_path: Path) -> None:
    summary = {"tables": [], "skipped": {}}
    response = _aggregate_v2_build_response(summary, tmp_path, "/d.json", 1.23456)
    assert response.cache_seconds == 1.235


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
