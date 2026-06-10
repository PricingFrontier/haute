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
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

import orjson
import polars as pl

from haute._api_input_schema import (
    ApiInputSchemaError,
    ColumnType,
    parse_column_path,
    parse_table_path,
    validate_v2_schema,
)
from haute._api_input_schema import (
    sanitise_label_for_filesystem as _sanitise_label,
)
from haute._logging import get_logger

logger = get_logger(component="json_shred")


_META_FILENAME = "meta.json"

# A JSON *scalar* array (e.g. ``coverages: ["TPFT", "comprehensive"]``)
# becomes its own child table with a single ``value`` column — exactly how
# an array of objects becomes a child table. The column's ``path`` carries a
# reserved ``$value`` leaf meaning "the element itself" (a JSON key can't be
# ``$value`` in this path grammar, so there's no collision with a real field).
_SCALAR_VALUE_LEAF = "$value"
_SCALAR_VALUE_COLUMN = "value"


def _scalar_to_str(value: Any) -> str:
    """Render a JSON scalar as a string (JSON-style booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_scalar(value: Any, type_token: str) -> Any:
    """Coerce a scalar-array element to its inferred ``value``-column type.

    Used ONLY for scalar-array child tables, whose single ``value`` column
    type was inferred from these very elements (see
    :func:`infer_v2_schema_from_data`). When the elements were mixed and the
    type widened (e.g. ``int`` + ``str`` → ``str``, ``int`` + ``float`` →
    ``float``), this keeps the built column consistent with the declared
    type. It is NOT a silent coercion of user-declared object columns — those
    fail loud in :func:`_buffer_to_frame`.
    """
    if value is None:
        return None
    if type_token == "str":
        return value if isinstance(value, str) else _scalar_to_str(value)
    if type_token == "float":
        if isinstance(value, bool):
            # Leave bools alone; `_buffer_to_frame` rejects a bool in a numeric
            # column rather than letting Polars silently coerce it to 0.0/1.0.
            return value
        if isinstance(value, int):
            return float(value)
    return value


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


def _resolve_leaf(value: Any, leaf: str) -> Any:
    """Resolve a dotted leaf path within a single dict.

    For ``leaf = "policy_id"`` returns ``value["policy_id"]`` (or None).
    For ``leaf = "profile.age"`` walks one level deeper. Treats a list
    encountered mid-walk as its first element if non-empty — degenerate
    but consistent with v1's behaviour at dotted-leaf positions.

    The reserved ``$value`` leaf means "the scalar element itself" (scalar
    array child tables): ``value`` is then the element, returned as-is. A
    dict/list under that leaf is a shape mismatch and resolves to None — the
    caller guards against emitting those rows.
    """
    if leaf == _SCALAR_VALUE_LEAF:
        return value if not isinstance(value, (dict, list)) else None
    if not isinstance(value, dict):
        return None
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
                if item is None:
                    # A null *element* of a scalar array is a real value: emit a
                    # None-valued row so the row count matches the element count.
                    # (For an object array a null isn't a record — nothing to
                    # emit; the array key itself being null is handled above.)
                    for label, col_specs in tables_by_path.get(current_path, []):
                        if any(leaf == _SCALAR_VALUE_LEAF for _n, leaf, _t in col_specs):
                            buffers[label].append(
                                {col_name: None for col_name, _leaf, _t in col_specs},
                            )
                    continue
                _walk(item, current_path)
            return

        is_dict = isinstance(value, dict)

        # Emit rows for any tables that sit at this exact depth. A scalar
        # child table (single ``$value`` column) takes only scalar elements;
        # an object table takes only dict records. Skip the mismatched shape
        # rather than emit a list-into-typed-column crash or a None row.
        for label, col_specs in tables_by_path.get(current_path, []):
            is_scalar_table = any(leaf == _SCALAR_VALUE_LEAF for _n, leaf, _t in col_specs)
            if is_scalar_table != (not is_dict):
                continue
            row: dict[str, Any] = {}
            for col_name, leaf, type_token in col_specs:
                resolved = _resolve_leaf(value, leaf)
                if leaf == _SCALAR_VALUE_LEAF:
                    resolved = _coerce_scalar(resolved, type_token)
                row[col_name] = resolved
            buffers[label].append(row)

        if not is_dict:
            return

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


# Values are Polars DataType *classes*, not instances. Polars's Schema /
# DataFrame constructors accept either; we keep the classes here so the
# table is constant-folded and cheap to look up.
_POLARS_TYPE_MAP: dict[ColumnType, type[pl.DataType]] = {
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
    # Build each column as a strictly-typed Series so a value that doesn't
    # match the declared type fails LOUD and SPECIFIC — naming the offending
    # column — rather than as an opaque 500. ``col_type`` is `str` at the
    # call site (from `_LeafSpec`); validate_v2_schema (B1) has already
    # guaranteed it's one of the five tokens, so the map lookup can't miss.
    series_list: list[pl.Series] = []
    for col_name, _leaf, col_type in col_specs:
        dtype = _POLARS_TYPE_MAP[cast(ColumnType, col_type)]
        values = [row.get(col_name) for row in rows]
        # Polars strict-builds a bool into an int/float column SILENTLY
        # (True → 1/1.0), which would hide a genuine type mismatch — reject it
        # loudly instead. (bool is a subclass of int, so the strict build won't
        # raise on its own here.)
        if col_type in ("int", "float") and any(isinstance(v, bool) for v in values):
            raise ApiInputSchemaError(
                f"column {col_name!r} is declared {col_type!r} but contains "
                "boolean values (Polars would silently coerce them to 0/1); "
                "change the column's type or fix the source data",
                column=col_name,
                declared_type=col_type,
            )
        try:
            series_list.append(pl.Series(col_name, values, dtype=dtype, strict=True))
        except (pl.exceptions.PolarsError, TypeError, OverflowError, ValueError) as exc:
            raise ApiInputSchemaError(
                f"column {col_name!r} has values that don't match its declared "
                f"type {col_type!r}; re-infer the schema or change the column's "
                "type to match the data",
                column=col_name,
                declared_type=col_type,
            ) from exc
    return pl.DataFrame(series_list)


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

    # No-op trapdoor: if the existing meta.json's fingerprint matches the
    # current v2 schema AND all expected per-port parquets are on disk,
    # skip the rebuild entirely. Mirrors v1's `build_json_cache` no-op so
    # repeated cache-button clicks don't churn the preview cache via
    # commit 1's mtime-in-fingerprint invalidation.
    if is_per_port_cache_valid(cd, v2_config):
        existing_meta = read_per_port_cache_meta(cd)
        if existing_meta is not None:
            logger.info(
                "json_shred_build_noop",
                data_path=str(dp),
                cache_dir=str(cd),
                fingerprint=str(existing_meta.get("schema_fingerprint", ""))[:8],
            )
            return {
                "schema_mode": existing_meta.get("schema_mode", "v2"),
                "schema_fingerprint": existing_meta.get("schema_fingerprint", ""),
                "tables": existing_meta.get("tables", []),
                "cache_dir": str(cd),
            }

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
            except OSError as exc:
                # Best-effort cleanup of a rebuildable cache file (a locked /
                # in-use parquet is overwritten on the next build). Log so a
                # leftover stale file is diagnosable, but don't fail the build.
                logger.debug(
                    "json_shred_stale_parquet_unlink_failed",
                    cache_dir=str(cd),
                    file=child.name,
                    error=str(exc),
                )

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


def load_v2_api_source(
    data_path: str,
    config: dict[str, Any],
) -> pl.LazyFrame | dict[str, pl.LazyFrame]:
    """Resolve a v2 apiInput's per-port cache and return its frame(s).

    The single runtime entry point shared by the executor's source builder
    (:func:`haute._builders._make_api_source_v2`) and the generated/deploy
    code (:func:`haute._codegen_builders._api_input_template`), so the two
    can't drift. Assumes *config* has already passed :func:`validate_v2_schema`
    (both callers validate first — at build time and at module import
    respectively).

    Behaviour:

    - 0 emit-true tables → ``RuntimeError`` (tick an ``emit`` toggle).
    - emit-true tables but none with a selected column → ``RuntimeError``.
    - resolves the dual-cache ``working/`` layer, falling back to
      ``committed/`` (the deploy / fresh-server case); a missing/stale cache
      raises the "click Cache as Parquet" message.
    - 1 emit label → a bare ``LazyFrame`` (single-port shorthand); 2+ → a
      ``dict[port_label, LazyFrame]`` in schema order.
    """
    from haute._json_flatten import _json_cache_dir

    tables = config.get("tables", []) or []
    emit_true_tables = [t for t in tables if t.get("emit")]
    if not emit_true_tables:
        raise RuntimeError(
            "API Input has no emitting tables. Open the node, tick the 'emit' "
            "toggle on at least one table, then click 'Cache as Parquet' before "
            "previewing.",
        )
    emit_labels = [
        t["label"]
        for t in emit_true_tables
        if any(c.get("selected") for c in (t.get("columns") or []))
    ]
    if not emit_labels:
        labels = [t["label"] for t in emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected columns. "
            f"Open the node and tick at least one column on the emitting "
            f"table(s): {labels}. Then click 'Cache as Parquet' before previewing.",
        )
    cache_dir = _json_cache_dir(data_path, "working")
    if not is_per_port_cache_valid(cache_dir, config):
        # Fall back to the committed layer (deploy / fresh-server case).
        cache_dir = _json_cache_dir(data_path, "committed")
        if not is_per_port_cache_valid(cache_dir, config):
            raise RuntimeError(
                "API Input data hasn't been cached for the current schema, or "
                "the cache is stale. Click 'Cache as Parquet' on the API Input "
                "node to (re)build.",
            )
    bundle = load_per_port_cache(cache_dir, config)
    # Single-port shorthand: bare LazyFrame instead of a one-entry dict.
    if len(emit_labels) == 1:
        return bundle[emit_labels[0]]
    # Multi-port: preserve schema order so executor logs/errors are deterministic.
    return {label: bundle[label] for label in emit_labels if label in bundle}


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


def _infer_type(value: Any) -> str:
    """Infer a v2 column type token from a single JSON scalar."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _widen_type(existing: str | None, new: str) -> str:
    """Combine two observed type tokens into the narrowest that fits both.

    ``int`` + ``float`` → ``float``; any other disagreement → ``str``. This
    makes inference reflect the WHOLE file (e.g. an ``int`` column whose
    151st row is a float is typed ``float``, not ``int``), so the strict
    parquet build doesn't fail on a value that appears past an early sample.
    """
    if existing is None:
        return new
    if existing == new:
        return existing
    if {existing, new} == {"int", "float"}:
        return "float"
    return "str"


def infer_v2_schema_from_data(
    data_path: str | Path,
    *,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Sniff the v2 schema mapping from the records of *data_path*.

    Produces a v2 config (without the apiInput's ``path`` / ``contract``
    metadata) — the caller stitches them in. Walks the records and records:

    - every nested-array depth as a candidate table;
    - the leaf keys at each depth, with types inferred and *widened* across
      all scanned records (so a late-appearing float widens an int column
      rather than crashing the build);
    - a JSON **scalar array** (e.g. ``["TPFT", "comprehensive"]``) as its own
      child table with a single ``value`` column — mirroring how an array of
      objects becomes a child table (Option 2). Element types are widened
      the same way.

    Types are inferred across the whole file by default; pass ``sample_size``
    to cap the number of records scanned (the build still reads every record,
    so a mismatch past the sample fails loud in :func:`_buffer_to_frame`
    rather than silently).

    Raises :class:`ApiInputSchemaError` for a nested array (array of arrays),
    which can't be expressed as a flat table.

    Each table is ``emit=True`` only for the root; nested tables are off so
    the user opts in explicitly.
    """
    dp = Path(data_path)
    records = list(_iter_records(dp))
    if sample_size is not None and sample_size > 0:
        records = records[:sample_size]

    # depth → ordered {column_name: widened type}  (object/leaf columns here)
    object_cols: dict[tuple[str, ...], dict[str, str]] = {(): {}}
    # depth → widened element type  (scalar-array child tables)
    scalar_tables: dict[tuple[str, ...], str] = {}

    def _walk(value: Any, path: tuple[str, ...]) -> None:
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                _walk(item, path)
            return
        if not isinstance(value, dict):
            return
        cols = object_cols.setdefault(path, {})
        for k, v in value.items():
            child = path + (k,)
            if isinstance(v, dict):
                _walk(v, child)
            elif isinstance(v, list):
                if not v:
                    # Empty array — ambiguous. Tentatively a (currently empty)
                    # scalar child table; if a later record shows objects at
                    # this key, the object branch records columns here and
                    # wins at table-assembly time.
                    scalar_tables.setdefault(child, "str")
                elif any(isinstance(item, dict) for item in v):
                    _walk(v, child)  # array of objects → object child table
                else:
                    elem_type: str | None = None
                    for item in v:
                        if isinstance(item, list):
                            raise ApiInputSchemaError(
                                f"column {'.'.join(child)!r}: nested arrays "
                                "(array of arrays) cannot be expressed as a flat "
                                "table column; flatten this field in the source data",
                                column=".".join(child),
                            )
                        if item is None:
                            continue
                        elem_type = _widen_type(elem_type, _infer_type(item))
                    scalar_tables[child] = _widen_type(scalar_tables.get(child), elem_type or "str")
            else:
                cols[k] = _widen_type(cols.get(k), _infer_type(v))

    for record in records:
        _walk(record, ())

    def _make_path(segments: tuple[str, ...]) -> str:
        if not segments:
            return "$[*]"
        return "$[*]." + ".".join(f"{s}[*]" for s in segments)

    tables: list[dict[str, Any]] = []
    all_paths = set(object_cols) | set(scalar_tables)
    for path_tuple in sorted(all_paths, key=lambda p: (len(p), p)):
        table_path = _make_path(path_tuple)
        # A depth that was ever dict-walked is an object table; a depth only
        # ever reached as a scalar array is a scalar child table.
        if path_tuple in scalar_tables and path_tuple not in object_cols:
            columns: list[dict[str, Any]] = [
                {
                    "name": _SCALAR_VALUE_COLUMN,
                    "path": f"{table_path}.{_SCALAR_VALUE_LEAF}",
                    "type": scalar_tables[path_tuple],
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
            ]
        else:
            columns = [
                {
                    "name": col_name,
                    "path": f"{table_path}.{col_name}",
                    "type": col_type,
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
                for col_name, col_type in object_cols.get(path_tuple, {}).items()
            ]
        tables.append(
            {
                "path": table_path,
                "label": table_path,
                "displayPath": None,
                "emit": not path_tuple,  # only the root emits by default
                "row_id_column": None,
                "columns": columns,
            },
        )
    return {"tables": tables}


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
