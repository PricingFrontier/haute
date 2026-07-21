"""Second-pass mutation witnesses for _json_shred survivors the first round
under-witnessed.

Each targets a specific Cosmic Ray survivor that a prior witness *claimed* but
did not actually kill (wrong line identity, or a test input that couldn't
distinguish the mutated operator from the original over its reachable domain).
The kill strategy for each is spelled out in its docstring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _read_root_array_value,
    _resolve_leaf,
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
)


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
    path: str, label: Any, cols: list[dict[str, Any]], *, emit: bool = True
) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": emit, "row_id_column": None, "columns": cols}


def _write(tmp_path: Path, records: list[Any]) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


# ─── 546 — _resolve_leaf list descent takes cur[0], not cur[-1] ──────


def test_resolve_leaf_dotted_through_list_fails_loud() -> None:
    # W1: a dotted leaf that crosses a list no longer silently descends into
    # the first element (dropping the rest) — it raises ApiInputSchemaError
    # naming the offending leaf, so the mis-modelled array (which should be a
    # child table) can never silently lose rows.
    with pytest.raises(ApiInputSchemaError, match="claims.amount"):
        _resolve_leaf({"claims": [{"amount": 3}, {"amount": 9}]}, "claims.amount")


# ─── 486 — _read_root_array_value raises on a depth-0 '}' ────────────


def _byte_reader(rest: bytes):  # noqa: ANN202
    it = iter(rest)

    def read_byte() -> bytes:
        try:
            return bytes([next(it)])
        except StopIteration:
            return b""

    return read_byte


def test_read_root_array_value_rejects_unbalanced_close_brace() -> None:
    # ``if b == b"]"`` (L486) sits inside ``if b in {b"}", b"]"}`` at depth 0:
    # a ']' returns the value, a '}' is unbalanced and must raise "unexpected
    # '}'". Eq->GtE makes ``b >= b"]"`` true for '}' (0x7d >= ']' 0x5d), so the
    # stray brace would be accepted as a value-end and RETURNED instead of
    # raising. Input '{}}' drives depth 1 -> 0 then hits the bare '}'.
    pos = [3]
    with pytest.raises(orjson.JSONDecodeError, match="unexpected '}'"):
        _read_root_array_value(b"{", _byte_reader(b"}}"), lambda: pos[0])


def test_read_root_array_value_returns_on_top_level_close_bracket() -> None:
    # The companion happy path: a balanced object value terminates at the
    # top-level ']' delimiter, returning the buffer. (Pins that the GtE-killing
    # test above isn't just asserting "always raises".)
    value, delim = _read_root_array_value(b"{", _byte_reader(b'"a":1}]'), lambda: 0)
    assert orjson.loads(value) == {"a": 1}
    assert delim == b"]"


# ─── 1123 — is_per_port_cache_valid: schema_mode must equal "v2" exactly ──


def test_validity_rejects_schema_mode_lexically_after_v2(tmp_path: Path) -> None:
    # ``if meta.get("schema_mode") != "v2"`` (L1123). NotEq->Lt makes it
    # ``schema_mode < "v2"``: a mode that sorts AFTER "v2" (e.g. "v3") then
    # passes the check (``"v3" < "v2"`` is False) and the stale cache is wrongly
    # accepted. "v1" can't catch this ('v1' < 'v2' is True, same as !=), so the
    # mode must be lexically greater than "v2".
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), cfg, cache_dir)
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    meta["schema_mode"] = "v3"  # lexically AFTER "v2"
    meta_path.write_bytes(orjson.dumps(meta))
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False


# ─── 1128 — is_per_port_cache_valid: edited data file invalidates ────


def test_validity_rejects_when_data_file_changed(tmp_path: Path) -> None:
    # L1128 is the ``if not _data_file_matches(...): return False`` branch (the
    # data-file freshness gate — NOT the label check the earlier witness aimed
    # at). FalseWithTrue would call an edited data file "fresh". Build, then
    # rewrite the data file with different content+size so the recorded
    # signature no longer matches.
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), cfg, cache_dir)
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    data.write_text(json.dumps([{"id": 1}, {"id": 2}, {"id": 3}]), encoding="utf-8")
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False


# ─── 1019 — load_per_port_cache non-emitting skip uses continue ──────


def test_load_skips_non_emitting_table_then_loads_later_one(tmp_path: Path) -> None:
    # L1019 ``if not table_is_emitting(table): continue``. A non-emitting table
    # ordered BEFORE the emitting one must be skipped, not break the loop —
    # otherwise the real frame after it is never loaded.
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    load_cfg = {
        "tables": [
            _table("$[:]", "skipme", [_col("id", "$[:].id")], emit=False),
            _table("$[:]", "root", [_col("id", "$[:].id")]),
        ]
    }
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), load_cfg, cache_dir)
    out = load_per_port_cache(cache_dir, load_cfg)
    assert "root" in out  # continue past skipme; break would drop root
    assert out["root"].collect()["id"].to_list() == [1, 2]


# ─── 875 / 879 — build skip branches use continue ───────────────────


def test_build_skips_non_emitting_table_then_builds_later_one(tmp_path: Path) -> None:
    # L875 ``if not table_is_emitting(table): continue`` in build. A
    # non-emitting table before the emitting one must not break the build of the
    # later table.
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {
        "tables": [
            _table("$[:]", "skipme", [_col("id", "$[:].id")], emit=False),
            _table("$[:]", "root", [_col("id", "$[:].id")]),
        ]
    }
    summary = build_per_port_cache(str(data), cfg, tmp_path / "cache")
    labels = [t["label"] for t in summary["tables"]]
    assert "root" in labels  # break would never reach/build root


def test_build_skips_unselected_column_then_keeps_later_one(tmp_path: Path) -> None:
    # L879 ``if not col.get("selected"): continue``. An unselected column before
    # a selected one must be skipped without breaking out — the selected column
    # after it must still make it into the frame.
    data = _write(tmp_path, [{"skip": 1, "keep": 2}, {"skip": 3, "keep": 4}])
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [
                    _col("skip", "$[:].skip", selected=False),
                    _col("keep", "$[:].keep", selected=True),
                ],
            )
        ]
    }
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), cfg, cache_dir)
    frame = pl.read_parquet(cache_dir / "root.parquet")
    assert "keep" in frame.columns  # break would drop the column after skip
    assert "skip" not in frame.columns
    assert frame["keep"].to_list() == [2, 4]
