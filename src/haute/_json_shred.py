"""Per-port JSON shred for v2 schema mappings (MULTI_FRAME_PLAN commit 3).

Where v1's :mod:`_json_flatten` produces a single flat table with
index-based array expansion (``drivers.0.id``, ``drivers.1.id``, ...),
v2 produces ONE frame per emit-true ``tables[]`` entry, each frame
materialised at its own JSON iteration depth.

Algorithm in one paragraph: single-pass walk over the top-level records.
For each record, walk down the tree; when the current iteration depth
matches an emit-true table's path, extract that table's columns and
append a row to its buffer. Lists at any path are treated as repeated
singletons (canonical equivalence) so a JSON array of N objects produces
N row emissions to that path's table. Children are entered only if some
table's path goes deeper; we don't waste work descending into branches
that no table cares about.

On-disk layout in a cache directory (working/<hash>/ or committed/<hash>/):

- one ``<sanitised_label>.parquet`` per emit-true table.
- one ``meta.json`` carrying ``{schema_mode: "v2", schema_fingerprint,
  tables: [{label, parquet, row_count, column_count}, ...]}``.

The per-frame schema for each parquet is also embedded in the parquet's
footer key-value metadata (DUAL_CACHE.md §3) so each file is
self-describing: the schema is co-located with its data, no
schema-wrote-but-data-failed race.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import orjson
import polars as pl

from haute._api_input_schema import (
    ColumnType,
    parse_column_path,
    parse_table_path,
    validate_v2_schema,
)
from haute._logging import get_logger

logger = get_logger(component="json_shred")


_META_FILENAME = "meta.json"


# ---------------------------------------------------------------------------
# Sanitisation and identity
# ---------------------------------------------------------------------------


_FILESYSTEM_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitise_label(label: str) -> str:
    """Filesystem-safe label, used as the parquet filename stem.

    Replaces any non-(letter, digit, underscore, hyphen) with an
    underscore. A label of ``""`` (which should never occur — the codec
    rejects empty labels at validation) falls back to ``"_unnamed"``.
    """
    if not label:
        return "_unnamed"
    return _FILESYSTEM_SAFE.sub("_", label)


def _v2_fingerprint(config: dict[str, Any]) -> str:
    """Stable content hash over the v2 schema's shred-relevant fields.

    Two equivalent v2 configs hash identically; any change to the tables
    or their columns moves the fingerprint. Excludes fields that don't
    affect the shred output (the apiInput's ``path``, ``contract``).
    """
    tables = config.get("tables", [])
    canonical: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        cols_canon: list[dict[str, Any]] = []
        for col in table.get("columns", []) or []:
            if not isinstance(col, dict):
                continue
            cols_canon.append(
                {
                    "name": col.get("name"),
                    "path": col.get("path"),
                    "type": col.get("type"),
                    "selected": bool(col.get("selected")),
                    "levels": col.get("levels"),
                },
            )
        # Sort columns by path for canonical ordering — independent of
        # the user's row-order in the editor.
        cols_canon.sort(key=lambda c: (c.get("path") or "", c.get("name") or ""))
        canonical.append(
            {
                "path": table.get("path"),
                "label": table.get("label"),
                "emit": bool(table.get("emit")),
                "row_id_column": table.get("row_id_column"),
                "columns": cols_canon,
            },
        )
    canonical.sort(key=lambda t: t.get("path") or "")
    payload = orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Record iteration
# ---------------------------------------------------------------------------


def _iter_records(data_path: Path) -> Iterator[dict[str, Any]]:
    """Yield top-level records from a JSON or JSONL file.

    JSONL: one record per non-empty line.
    JSON: if the file's root is an array, yields each element; if the
    root is an object, yields that single object.
    """
    if data_path.suffix.lower() == ".jsonl":
        with data_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                obj = orjson.loads(stripped)
                if isinstance(obj, dict):
                    yield obj
        return
    raw = data_path.read_bytes()
    if not raw.strip():
        return
    obj = orjson.loads(raw)
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item
    elif isinstance(obj, dict):
        yield obj


# ---------------------------------------------------------------------------
# Shred core
# ---------------------------------------------------------------------------


_LeafSpec = tuple[str, str, str]  # (column_name, leaf_path_dotted, type_token)


def _resolve_leaf(value: dict[str, Any], leaf: str) -> Any:
    """Resolve a dotted leaf path within a single dict.

    For ``leaf = "policy_id"`` returns ``value["policy_id"]`` (or None).
    For ``leaf = "profile.age"`` walks one level deeper. Treats a list
    encountered mid-walk as its first element if non-empty — degenerate
    but consistent with v1's behaviour at dotted-leaf positions.
    """
    if "." not in leaf:
        return value.get(leaf)
    cur: Any = value
    for part in leaf.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            cur = cur[0].get(part) if cur and isinstance(cur[0], dict) else None
        else:
            return None
    return cur


def shred_to_buffers(
    records: Iterable[dict[str, Any]],
    v2_config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Shred *records* according to *v2_config*, returning per-port row buffers.

    Output is a dict keyed by ``table.label`` (the port name); each value
    is a list of rows. Each row is a dict mapping ``column.name`` to the
    extracted value (or ``None`` when the path doesn't resolve).

    Validates the schema before walking, so a malformed config raises
    upfront rather than silently producing empty buffers.
    """
    validate_v2_schema(v2_config)

    # Tables we'll actually emit (have emit=true and at least one selected column).
    emit_tables: list[tuple[str, tuple[str, ...], list[_LeafSpec]]] = []
    for table in v2_config["tables"]:
        if not table.get("emit"):
            continue
        table_path = table["path"]
        path_tuple = parse_table_path(table_path)
        col_specs: list[_LeafSpec] = []
        for col in table.get("columns", []) or []:
            if not col.get("selected"):
                continue
            leaf = parse_column_path(col["path"], table_path)
            col_specs.append((col["name"], leaf, col.get("type", "str")))
        emit_tables.append((table["label"], path_tuple, col_specs))

    # Group tables by their iteration-depth path tuple.
    tables_by_path: dict[tuple[str, ...], list[tuple[str, list[_LeafSpec]]]] = {}
    for label, path_tuple, col_specs in emit_tables:
        tables_by_path.setdefault(path_tuple, []).append((label, col_specs))

    # Per-path child keys we need to descend into.
    child_keys_by_path: dict[tuple[str, ...], set[str]] = {}
    for _label, path_tuple, _cols in emit_tables:
        for i in range(len(path_tuple)):
            parent = path_tuple[:i]
            child_keys_by_path.setdefault(parent, set()).add(path_tuple[i])

    # Buffers — one list per emitting table, keyed by label.
    buffers: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in emit_tables}

    def _walk(value: Any, current_path: tuple[str, ...]) -> None:
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                _walk(item, current_path)
            return
        if not isinstance(value, dict):
            return

        # Emit rows for any tables that sit at this exact depth.
        for label, col_specs in tables_by_path.get(current_path, []):
            row: dict[str, Any] = {}
            for col_name, leaf, _type_token in col_specs:
                row[col_name] = _resolve_leaf(value, leaf)
            buffers[label].append(row)

        # Recurse into child keys that some emit-true table cares about.
        for child_key in child_keys_by_path.get(current_path, set()):
            child_value = value.get(child_key)
            _walk(child_value, current_path + (child_key,))

    for record in records:
        _walk(record, ())

    return buffers


# ---------------------------------------------------------------------------
# Frame construction and write
# ---------------------------------------------------------------------------


_POLARS_TYPE_MAP: dict[ColumnType, pl.DataType] = {
    "int": pl.Int64,
    "float": pl.Float64,
    "str": pl.String,
    "bool": pl.Boolean,
    "date": pl.Date,
}


def _buffer_to_frame(
    rows: list[dict[str, Any]],
    col_specs: list[_LeafSpec],
) -> pl.DataFrame:
    """Turn a row buffer into a typed Polars DataFrame.

    Builds a per-column accumulator from the rows (preserves the
    declared column order). Empty buffers produce an empty DataFrame
    with the right schema so downstream readers see a consistent shape.
    Missing column values are ``None``.
    """
    schema: dict[str, pl.DataType] = {
        col_name: _POLARS_TYPE_MAP.get(col_type, pl.String)  # type: ignore[arg-type]
        for col_name, _leaf, col_type in col_specs
    }
    columns: dict[str, list[Any]] = {col_name: [] for col_name, _leaf, _t in col_specs}
    for row in rows:
        for col_name, _leaf, _t in col_specs:
            columns[col_name].append(row.get(col_name))
    return pl.DataFrame(columns, schema=schema)


def _per_frame_metadata(label: str, col_specs: list[_LeafSpec]) -> dict[bytes, bytes]:
    """Build the parquet-footer key-value metadata describing this frame.

    Each parquet carries its own schema (column name + JSON path + type)
    so the file is self-describing — DUAL_CACHE.md §3.
    """
    payload = {
        "port_label": label,
        "columns": [
            {"name": col_name, "leaf": leaf, "type": col_type}
            for col_name, leaf, col_type in col_specs
        ],
    }
    return {b"haute_per_frame_schema": orjson.dumps(payload)}


def build_per_port_cache(
    data_path: str | Path,
    v2_config: dict[str, Any],
    cache_dir: str | Path,
) -> dict[str, Any]:
    """Build the per-port parquet cache for *data_path* under *v2_config*.

    *cache_dir* is the per-data-file directory (e.g.
    ``.haute_cache/working/json_<hash>/``); the caller already knows which
    layer to target.

    Returns a summary dict with ``schema_mode``, ``schema_fingerprint``,
    and ``tables`` (per-port row/column counts + on-disk parquet paths).
    Also writes ``meta.json`` into *cache_dir* with the same payload so
    later cache-validity checks don't need to re-shred to know what's
    there.
    """
    dp = Path(data_path)
    cd = Path(cache_dir)
    cd.mkdir(parents=True, exist_ok=True)

    validate_v2_schema(v2_config)

    # Re-parse table-paths + columns so we can stream-write per-port
    # parquets immediately after the shred.
    emit_tables: list[tuple[str, list[_LeafSpec]]] = []
    for table in v2_config["tables"]:
        if not table.get("emit"):
            continue
        col_specs: list[_LeafSpec] = []
        for col in table.get("columns", []) or []:
            if not col.get("selected"):
                continue
            leaf = parse_column_path(col["path"], table["path"])
            col_specs.append((col["name"], leaf, col.get("type", "str")))
        if col_specs:
            emit_tables.append((table["label"], col_specs))

    # Shred — single pass.
    records = list(_iter_records(dp))
    buffers = shred_to_buffers(records, v2_config)

    # Write per-port parquets + collect per-table summaries.
    import pyarrow.parquet as pq  # local — keeps top-of-module import surface small

    fingerprint = _v2_fingerprint(v2_config)
    table_summaries: list[dict[str, Any]] = []
    for label, col_specs in emit_tables:
        rows = buffers.get(label, [])
        frame = _buffer_to_frame(rows, col_specs)
        parquet_path = cd / f"{_sanitise_label(label)}.parquet"
        # Convert to Arrow and attach the per-frame schema in the footer
        # (DUAL_CACHE.md §3). Polars's DataFrame.write_parquet doesn't
        # accept the bytes-keyed metadata shape PyArrow uses; going via
        # Arrow directly is the same pattern v1 uses in _json_flatten.
        arrow_tbl = frame.to_arrow()
        arrow_tbl = arrow_tbl.replace_schema_metadata(
            _per_frame_metadata(label, col_specs),
        )
        pq.write_table(arrow_tbl, parquet_path, compression="zstd")
        table_summaries.append(
            {
                "label": label,
                "parquet": parquet_path.name,
                "row_count": frame.height,
                "column_count": frame.width,
            },
        )

    # Drop any stale per-port parquets left over from a previous schema.
    keep_filenames = {f"{_sanitise_label(label)}.parquet" for label, _ in emit_tables}
    keep_filenames.add(_META_FILENAME)
    for child in cd.iterdir():
        if child.is_file() and child.name not in keep_filenames:
            try:
                child.unlink()
            except OSError:
                pass

    meta_payload = {
        "schema_mode": "v2",
        "schema_fingerprint": fingerprint,
        "tables": table_summaries,
    }
    meta_path = cd / _META_FILENAME
    meta_path.write_bytes(orjson.dumps(meta_payload))

    logger.info(
        "json_shred_built",
        data_path=str(dp),
        cache_dir=str(cd),
        table_count=len(table_summaries),
        fingerprint=fingerprint[:8],
    )

    return {
        "schema_mode": "v2",
        "schema_fingerprint": fingerprint,
        "tables": table_summaries,
        "cache_dir": str(cd),
    }


def load_per_port_cache(
    cache_dir: str | Path,
    v2_config: dict[str, Any],
) -> dict[str, pl.LazyFrame]:
    """Scan the per-port parquets in *cache_dir* for each emit-true table.

    Returns ``{table_label: LazyFrame}``. A missing parquet (e.g. a table
    that was emit-true at build time but is now disabled) is skipped — the
    caller is expected to validate cache freshness via
    :func:`is_per_port_cache_valid` before this.
    """
    cd = Path(cache_dir)
    out: dict[str, pl.LazyFrame] = {}
    for table in v2_config.get("tables", []) or []:
        if not table.get("emit"):
            continue
        label = table.get("label")
        if not isinstance(label, str):
            continue
        parquet_path = cd / f"{_sanitise_label(label)}.parquet"
        if parquet_path.exists():
            out[label] = pl.scan_parquet(parquet_path)
    return out


def is_per_port_cache_valid(
    cache_dir: str | Path,
    v2_config: dict[str, Any],
) -> bool:
    """Cheap validity check: meta.json's fingerprint matches the v2 schema
    AND all expected per-port parquets exist on disk.
    """
    cd = Path(cache_dir)
    meta_path = cd / _META_FILENAME
    if not meta_path.exists():
        return False
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return False
    if not isinstance(meta, dict):
        return False
    if meta.get("schema_mode") != "v2":
        return False
    if meta.get("schema_fingerprint") != _v2_fingerprint(v2_config):
        return False
    for table in v2_config.get("tables", []) or []:
        if not table.get("emit"):
            continue
        label = table.get("label")
        if not isinstance(label, str):
            return False
        parquet_path = cd / f"{_sanitise_label(label)}.parquet"
        if not parquet_path.exists():
            return False
    return True


def read_per_port_cache_meta(cache_dir: str | Path) -> dict[str, Any] | None:
    """Return the cached ``meta.json`` payload, or ``None`` if absent / corrupt.

    Used by the cache routes' status endpoint to report what's on disk
    without re-shredding.
    """
    cd = Path(cache_dir)
    meta_path = cd / _META_FILENAME
    if not meta_path.exists():
        return None
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return meta
