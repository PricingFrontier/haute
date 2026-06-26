"""Mutation-killing witness tests for the cache build/validity/load lifecycle
in ``haute._json_shred``.

Each test targets a specific Cosmic Ray survivor (line numbers from the
working-tree source). The goal is a test that FAILS under the named mutation
but PASSES on the real code, so the assertions are tight enough to discriminate
the exact operator.

Survivors covered:
- 847  ``if existing_meta is not None:``  (Eq_GtE) — no-op trapdoor idempotency
- 940  ``if skip_stats.total:``           (AddNot) — skip-warning gate
- 1089 ``if len(emit_labels) == 1:``      — single vs multi frame return shape
- 1090 ``return bundle[emit_labels[0]]``  — single-frame index
- 1123 ``if meta.get("schema_mode") != "v2":`` (NotEq) — schema_mode gate
- 1128 ``return False`` (FalseWithTrue)   — non-str label invalidates
- 1131 ``continue`` (ContinueWithBreak)   — missing-parquet skips non-emit
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import structlog

from haute._json_shred import (
    _v2_fingerprint,
    build_per_port_cache,
    is_per_port_cache_valid,
    load_v2_api_source,
    read_per_port_cache_meta,
)

# ---------------------------------------------------------------------------
# Config / data helpers (mirror tests/test_load_v2_api_source.py)
# ---------------------------------------------------------------------------


def _col(name: str, path: str, *, selected: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": "int",
        "status": "Confirmed",
        "selected": selected,
        "levels": None,
    }


def _table(
    path: str, label: str, cols: list[dict[str, Any]], *, emit: bool = True
) -> dict[str, Any]:
    return {
        "path": path,
        "label": label,
        "emit": emit,
        "row_id_column": None,
        "columns": cols,
    }


def _write(tmp_path: Path, records: list[Any]) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 847 — no-op trapdoor: build twice, second call returns the existing summary
# without churning the cache (Eq_GtE on `existing_meta is not None`).
# ---------------------------------------------------------------------------


def test_rebuild_is_noop_trapdoor_idempotent(tmp_path: Path) -> None:
    """Building the same schema+data a second time hits the no-op trapdoor:
    the existing meta is returned verbatim and the on-disk parquet is NOT
    rewritten.

    Discriminates line 847: the trapdoor only returns when ``existing_meta``
    (from ``read_per_port_cache_meta``) is present. If the build skipped the
    trapdoor it would re-shred and rewrite the parquet, bumping its mtime."""
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"

    first = build_per_port_cache(str(data), cfg, cache_dir)
    parquet = cache_dir / "root.parquet"
    assert parquet.exists()
    first_mtime = parquet.stat().st_mtime_ns
    first_bytes = parquet.read_bytes()

    second = build_per_port_cache(str(data), cfg, cache_dir)

    # Same fingerprint + identical summary payload — the trapdoor returned the
    # recorded meta, not a fresh shred result.
    assert second["schema_fingerprint"] == first["schema_fingerprint"]
    assert second["tables"] == first["tables"]
    assert second["skipped"] == first["skipped"]
    assert second["data_file"] == first["data_file"]

    # No churn: the parquet was not rewritten (same bytes, same mtime). A
    # mutated trapdoor that fell through would have re-published a new parquet.
    assert parquet.stat().st_mtime_ns == first_mtime
    assert parquet.read_bytes() == first_bytes

    # And the returned summary matches what is on disk in meta.json.
    meta = read_per_port_cache_meta(cache_dir)
    assert meta is not None
    assert second["schema_fingerprint"] == meta["schema_fingerprint"]


# ---------------------------------------------------------------------------
# 940 — `if skip_stats.total:` (AddNot). The warning log fires iff there were
# skips. The summary's `skipped` field is unconditional, so the ONLY
# observable difference of this branch is the warning event.
# ---------------------------------------------------------------------------


def test_skip_warning_fires_only_when_records_skipped(tmp_path: Path) -> None:
    """A root array with a non-object element produces a record skip; the build
    summary reports it AND a ``json_shred_records_skipped`` warning is emitted.
    A clean file produces no skips and no such warning.

    The summary's ``skipped`` is identical regardless of the branch, so the
    warning presence/absence is what discriminates line 940's AddNot: invert
    the gate and the warning fires for the clean build but not the dirty one."""
    # Dirty file: one object record + one scalar (non-object) element → 1 skip.
    dirty = _write(tmp_path, [{"id": 1}, 5])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    with structlog.testing.capture_logs() as logs:
        summary = build_per_port_cache(str(dirty), cfg, tmp_path / "dirty_cache")
    # The skip is recorded in the summary either way.
    assert summary["skipped"]["records"] == 1
    # The warning fires precisely because skip_stats.total is truthy.
    skip_warnings = [e for e in logs if e.get("event") == "json_shred_records_skipped"]
    assert len(skip_warnings) == 1
    assert skip_warnings[0]["skipped_records"] == 1

    # Clean file: zero skips → NO warning. Inverting the gate would flip this.
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
    with structlog.testing.capture_logs() as logs2:
        clean_summary = build_per_port_cache(str(clean), cfg, tmp_path / "clean_cache")
    assert clean_summary["skipped"]["records"] == 0
    assert [e for e in logs2 if e.get("event") == "json_shred_records_skipped"] == []


# ---------------------------------------------------------------------------
# 1089 / 1090 — single emitting label returns a bare LazyFrame; 2+ returns a
# dict keyed by label.
# ---------------------------------------------------------------------------


def test_single_emitting_table_returns_bare_lazyframe(tmp_path: Path) -> None:
    """One emitting label → bare LazyFrame (count boundary `== 1` + index 0).

    Kills 1089 (`== 1` boundary): if it were `>= 1`, two-table configs would
    wrongly return a frame too. Kills 1090's index: the single frame returned
    must be the one keyed by the only emitting label."""
    data = _write(tmp_path, [{"id": 10}, {"id": 20}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    build_per_port_cache(str(data), cfg, _working_cache(data))
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, pl.LazyFrame)
    assert out.collect()["id"].to_list() == [10, 20]


def test_two_emitting_tables_return_dict_keyed_by_label(tmp_path: Path) -> None:
    """Two emitting labels → dict keyed by label, NOT a bare frame.

    Kills the 1089 count boundary: with `== 1` two tables fall through to the
    dict branch; a mutation to `>= 1` / `> 1` / `<= 1` would change the shape
    for one of the two arities. Pairing this with the single-table test pins
    the boundary at exactly 1."""
    data = _write(
        tmp_path,
        [{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}],
    )
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [_col("age", "$[:].drivers[:].age")],
            ),
        ]
    }
    build_per_port_cache(str(data), cfg, _working_cache(data))
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root", "drivers"]
    assert out["root"].collect()["id"].to_list() == [1]
    assert out["drivers"].collect()["age"].to_list() == [30, 40]


def _working_cache(data: Path) -> Path:
    from haute._json_flatten import _json_cache_dir

    return _json_cache_dir(str(data), "working")


# ---------------------------------------------------------------------------
# 1123 — `if meta.get("schema_mode") != "v2":` (NotEq cluster).
# ---------------------------------------------------------------------------


def test_validity_requires_schema_mode_v2(tmp_path: Path) -> None:
    """A meta with schema_mode != "v2" is invalid; the same meta with "v2"
    (matching fingerprint + signature + parquet) is valid.

    Kills 1123's NotEq: flipping `!=` to `==` would mark the genuine v2 cache
    invalid and the v1 cache valid — both assertions below would flip."""
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), cfg, cache_dir)

    # As built (schema_mode == "v2") → valid.
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    # Rewrite meta.json with schema_mode == "v1", keeping every other field
    # (fingerprint, data_file, tables) intact so schema_mode is the deciding
    # check, not a fingerprint/signature mismatch.
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    assert meta["schema_mode"] == "v2"
    meta["schema_mode"] = "v1"
    meta_path.write_bytes(orjson.dumps(meta))
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False


# ---------------------------------------------------------------------------
# 1128 — `return False` for a non-str label on an emitting table
# (FalseWithTrue). 1131 — `continue` skips non-emitting tables
# (ContinueWithBreak).
# ---------------------------------------------------------------------------


def test_non_string_label_on_emitting_table_invalidates(tmp_path: Path) -> None:
    """An emitting table whose label is not a string → validity False.

    Kills 1128 (FalseWithTrue): the non-str-label arm must return False. We
    align the fingerprint to the built cache so the label arm — not the
    fingerprint check — is the deciding branch."""
    data = _write(tmp_path, [{"id": 1}])
    good = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), good, cache_dir)

    bad = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    bad["tables"][0]["label"] = 123  # non-str label on an emitting table

    # Force the fingerprint to match so the non-str-label branch is reached
    # (the fingerprint check sits earlier and would otherwise short-circuit).
    if _v2_fingerprint(bad) != _v2_fingerprint(good):
        meta_path = cache_dir / "meta.json"
        meta = orjson.loads(meta_path.read_bytes())
        meta["schema_fingerprint"] = _v2_fingerprint(bad)
        meta_path.write_bytes(orjson.dumps(meta))

    assert is_per_port_cache_valid(cache_dir, bad, data_path=data) is False


def test_non_emitting_table_is_skipped_then_later_table_checked(
    tmp_path: Path,
) -> None:
    """A non-emitting table must be `continue`d over (not `break`), so a LATER
    emitting table with a missing parquet still drives validity to False.

    Kills 1131 (ContinueWithBreak): order the tables as [non-emitting,
    emitting-with-missing-parquet]. With `break` the loop stops at the
    non-emitting table and never checks the emitting one → wrongly valid.
    With `continue` it proceeds and the missing parquet → invalid."""
    data = _write(tmp_path, [{"id": 1, "x": 2}])
    # Build a cache for ONLY the emitting "root" table so its parquet exists,
    # then validate against a config whose first table is non-emitting and
    # whose second emitting table ("extra") has no parquet on disk.
    built_cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), built_cfg, cache_dir)

    check_cfg = {
        "tables": [
            _table("$[:]", "skipme", [_col("x", "$[:].x")], emit=False),
            _table("$[:]", "extra", [_col("x", "$[:].x")]),  # emitting, no parquet
        ]
    }
    # Align fingerprint so the loop (not the fingerprint check) decides.
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    meta["schema_fingerprint"] = _v2_fingerprint(check_cfg)
    meta_path.write_bytes(orjson.dumps(meta))

    # "extra" parquet does not exist → the emitting-table loop must reach it
    # (continue past "skipme") and return False.
    assert not (cache_dir / "extra.parquet").exists()
    assert is_per_port_cache_valid(cache_dir, check_cfg, data_path=data) is False

    # Sanity: if instead the second emitting table's parquet DID exist, the
    # same config would be valid — confirming "extra"'s absence is the cause
    # (and that the loop genuinely reaches the second table).
    import shutil

    shutil.copyfile(cache_dir / "root.parquet", cache_dir / "extra.parquet")
    assert is_per_port_cache_valid(cache_dir, check_cfg, data_path=data) is True
