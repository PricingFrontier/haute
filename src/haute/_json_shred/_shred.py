"""The single-pass v2 shred walk: table specs, per-record row emission,
root-conservation accounting, and parallel chunk execution primitives."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import orjson
import polars as pl

from haute._api_input_schema import (
    _RESERVED_LEAF as _SCALAR_VALUE_LEAF,
)
from haute._api_input_schema import (
    ApiInputSchemaError,
    ColumnType,
    PathSeg,
    array_depth,
    parse_column_path_full,
    parse_table_path,
    validate_v2_schema,
)
from haute._json_shred._records import ShredSkipStats, _ShredExecutionProgress
from haute._logging import get_logger

logger = get_logger(component="json_shred")


# A JSON *scalar* array (e.g. ``coverages: ["TPFT", "comprehensive"]``)
# becomes its own child table with a single ``value`` column — exactly how
# an array of objects becomes a child table. The column's ``path`` carries a
# reserved ``$value`` leaf meaning "the element itself".
#
# A literal JSON key *can* be spelled ``$value`` (JSON keys are arbitrary
# strings), and such a key WOULD collide with this sentinel: the shred would
# read ``$value`` as "the element itself" instead of that field, silently
# dropping the real value. The collision is made impossible to express
# silently by failing LOUD at inference time — :func:`infer_v2_schema_from_data`
# rejects a source key equal to ``$value`` (and, for the same reason, any key
# containing ``.``, which the path grammar reserves as the object-nesting
# separator). A hand-edited config that mixes a ``$value`` leaf with real-key
# columns on one table is likewise rejected at shred time
# (:func:`shred_to_buffers`). The sentinel string is single-sourced in
# ``_api_input_schema`` (imported above as ``_SCALAR_VALUE_LEAF``) because the
# INPUT path parser must know to accept this one non-identifier leaf; this is
# the downstream consumer.
_SCALAR_VALUE_COLUMN = "value"


def _scalar_to_str(value: Any) -> str:
    """Render a JSON scalar as a string (JSON-style booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_scalar(value: Any, type_token: str) -> Any:
    """Coerce a genuine JSON scalar to its inferred column type.

    Inference can widen mixed scalar observations (for example ``int`` +
    ``str`` → ``str`` or ``int`` + ``float`` → ``float``). Emission applies
    that same rule to object-table columns and scalar-array ``$value`` columns
    so a schema accepted by inference builds consistently. Shape values
    (dicts/lists) are not converted here; their callers reject or skip them
    under the table-shape contract.
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
    if not isinstance(tables, list):
        raise ApiInputSchemaError("v2 tables must be a list")
    canonical: list[dict[str, Any]] = []
    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            # Fail LOUD rather than silently dropping a malformed table: a
            # skipped entry would let two structurally-different on-disk
            # configs collapse to the SAME fingerprint, so a schema change
            # from one broken shape to another would not invalidate a stale
            # cache. Distinct configs must hash distinctly (W1).
            raise ApiInputSchemaError(f"v2 tables[{ti}] is not a dict")
        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise ApiInputSchemaError(f"v2 tables[{ti}].columns must be a list")
        cols_canon: list[dict[str, Any]] = []
        for ci, col in enumerate(columns):
            if not isinstance(col, dict):
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].columns[{ci}] is not a dict",
                )
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
# Shared emitting predicate (W2 item 2.5)
# ---------------------------------------------------------------------------


def table_is_emitting(table: Any) -> bool:
    """THE single definition of "this table contributes a data frame".

    ``emitting = emit AND at least one selected column``. Build, validity
    and load all route through this predicate; before W2 they each
    re-derived their own variant, and the disagreement (build skipped the
    parquet for an emit-true zero-selected-column table while validity
    demanded it) wedged the cache permanently — re-clicking "Cache as
    Parquet" could never repair it.

    Tolerates non-dict tables/columns (returns ``False``) because validity
    runs against arbitrary on-disk configs, mirroring the defensive
    iteration in :func:`_v2_fingerprint`.
    """
    if not isinstance(table, dict):
        return False
    if not table.get("emit"):
        return False
    columns = table.get("columns") or []
    if not isinstance(columns, list):
        return False
    return any(isinstance(col, dict) and col.get("selected") for col in columns)


# ---------------------------------------------------------------------------
# Shred core
# ---------------------------------------------------------------------------


_LeafSpec = tuple[str, str, str]  # (column_name, leaf_path_dotted, type_token)


# As _LeafSpec, plus the array-iteration depth at which the column's value
# lives (W1): equal to the table's depth for a normal column, shallower for
# an ancestor column whose value distributes over descendant rows.
_WalkSpec = tuple[str, str, str, int]


# As _WalkSpec, plus the two per-column constants the walk would otherwise
# re-derive on every emitted row: the leaf pre-split into hops, and whether the
# leaf is the reserved scalar sentinel. Built once per shred in
# :func:`shred_to_buffers`; never part of a stored config.
# (column_name, leaf_path_dotted, leaf_parts, type_token, source_depth, is_scalar_leaf)
_PreparedCol = tuple[str, str, tuple[str, ...], str, int, bool]


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _EmittingTableSpec:
    """One validated emitting table, parsed once for every shred consumer.

    ``columns`` carries the full walk-time form (including source array depth)
    needed for ancestor broadcasts. Cache writes and in-memory runtime loads
    derive their leaf-only frame schema from the same objects, so the two paths
    cannot disagree about selected columns, names, types, or paths.
    """

    label: str
    segments: tuple[PathSeg, ...]
    columns: tuple[_WalkSpec, ...]

    @property
    def leaf_specs(self) -> list[_LeafSpec]:
        return [(name, leaf, type_token) for name, leaf, type_token, _depth in self.columns]


def _leaf_parts(leaf: str) -> tuple[str, ...]:
    """Split a dotted leaf into its hops once, for reuse across every row.

    The leaf path is a constant of the column spec, but the shred resolves it
    per row per column (millions of calls on a large file). Splitting there
    re-derived the same tuple every time; callers on the hot path precompute
    it with this and hand it to :func:`_resolve_leaf`.
    """
    return tuple(leaf.split("."))


def _resolve_leaf(
    value: Any,
    leaf: str,
    parts: tuple[str, ...] | None = None,  # pragma: no mutate - type declaration
) -> Any:
    """Resolve a dotted leaf path within a single dict.

    *parts* is ``leaf`` pre-split by :func:`_leaf_parts`. It is purely a hot-path
    optimisation — omitted, the split happens here and behaviour is identical.

    For ``leaf = "policy_id"`` returns ``value["policy_id"]`` (or None).
    For ``leaf = "profile.age"`` walks one level deeper.

    A dotted leaf addresses 1-1 OBJECT nesting only (that is the only shape
    inference ever produces a dotted leaf for — an array becomes a child
    table, never a dotted hop). A NON-EMPTY list encountered mid-walk does
    not match that shape: collapsing it to one element would violate
    conservation, so the array field must be modelled as its own child table.

    An EMPTY list mid-walk discards nothing — there is no element to drop —
    so it is not a conservation violation. It resolves to None (the value is
    simply absent at this key), so data that legitimately mixes an object
    with an occasional empty-array/missing value at that key does not
    hard-fail the whole build.

    The reserved ``$value`` leaf means "the scalar element itself" (scalar
    array child tables): ``value`` is then the element, returned as-is. A
    dict/list under that leaf is a shape mismatch and resolves to None — the
    caller guards against emitting those rows.
    """
    if leaf == _SCALAR_VALUE_LEAF:
        return value if not isinstance(value, (dict, list)) else None
    if not isinstance(value, dict):
        return None
    if parts is None:
        parts = _leaf_parts(leaf)
    if len(parts) == 1:
        return value.get(parts[0])
    cur: Any = value
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            if not cur:
                # An empty list discards nothing — not a conservation
                # violation. Resolve to None (value absent at this key)
                # rather than hard-failing the build (W1).
                return None
            raise ApiInputSchemaError(
                f"dotted column leaf {leaf!r} crosses an array at segment "
                f"{part!r}, but a dotted leaf addresses 1-1 object nesting only "
                "(silently taking the first element would drop the rest); model "
                "this array as its own child table instead",
                column=leaf,
            )
        else:
            return None
    return cur


def _reject_reserved_leaf_collision(label: str, own_depth: int, col_specs: list[_WalkSpec]) -> None:
    """Fail loud if a table mixes the reserved ``$value`` leaf with a real sibling.

    The ``$value`` sentinel means "the scalar element itself"; a scalar-array
    child table carries it as the ONE column sourced at the table's own array
    depth (``own_depth``). It MAY additionally carry ancestor columns sourced at
    a SHALLOWER depth — those distribute a parent value over the scalar rows (a
    legitimate W1 pattern), so they don't collide.

    What IS malformed is a ``$value`` leaf coexisting with ANOTHER own-depth
    column — a real sibling field on what is actually an object table. The whole
    table is then treated as scalar (``is_scalar_table`` keys off the presence
    of any ``$value`` leaf), so every object row is silently dropped as a shape
    mismatch and the sibling fields never read. Inference can no longer produce
    this shape (it rejects a literal ``$value`` source key), but a hand-edited
    config could; reject it at shred time rather than emit a table whose rows all
    vanish.
    """
    own_depth_cols = [(n, leaf) for n, leaf, _t, d in col_specs if d == own_depth]
    if any(leaf == _SCALAR_VALUE_LEAF for _n, leaf in own_depth_cols) and len(own_depth_cols) > 1:
        names = [n for n, _leaf in own_depth_cols]
        raise ApiInputSchemaError(
            f"table {label!r} mixes the reserved '{_SCALAR_VALUE_LEAF}' "
            "scalar-array leaf with a real sibling column at the same depth; a "
            f"'$value' column must be the table's only own-depth column "
            f"(own-depth columns: {names})",
            table=label,
        )


def _emitting_table_specs(v2_config: dict[str, Any]) -> tuple[_EmittingTableSpec, ...]:
    """Validate *v2_config* and parse every emitting table exactly one way."""
    validate_v2_schema(v2_config)

    table_specs: list[_EmittingTableSpec] = []
    for table in v2_config["tables"]:
        if not table_is_emitting(table):
            continue
        segments = parse_table_path(table["path"])
        columns: list[_WalkSpec] = []
        for col in table.get("columns", []) or []:
            if not col.get("selected"):
                continue
            locating, leaf = parse_column_path_full(col["path"])
            columns.append(
                (
                    col["name"],
                    leaf,
                    col["type"],
                    array_depth(locating),
                ),
            )
        _reject_reserved_leaf_collision(table["label"], array_depth(segments), columns)
        table_specs.append(
            _EmittingTableSpec(
                label=table["label"],
                segments=segments,
                columns=tuple(columns),
            ),
        )
    return tuple(table_specs)


def shred_to_buffers(
    records: Iterable[dict[str, Any]],
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
    _table_specs: tuple[_EmittingTableSpec, ...] | None = None,  # pragma: no mutate
    _row_sink: Callable[[str, dict[str, Any]], None] | None = None,  # pragma: no mutate
    _emitted_counts: dict[str, int] | None = None,  # pragma: no mutate
) -> dict[str, list[dict[str, Any]]]:
    """Shred *records* according to *v2_config*, returning per-frame row buffers.

    Output is a dict keyed by ``table.label`` (the frame name); each value
    is a list of rows. Each row is a dict mapping ``column.name`` to the
    extracted value (or ``None`` when the path doesn't resolve).

    Validates the schema before walking, so a malformed config raises
    upfront rather than silently producing empty buffers.

    Only *emitting* tables (per :func:`table_is_emitting` — emit AND ≥1
    selected column) get a buffer, matching exactly the set of parquets
    the build writes and validity demands. *stats*, when provided, counts
    every array element dropped at an emitting table's depth because its
    shape mismatched that table (W2 item 2.7); cache builds and direct runtime
    materialisation both pass one through :func:`_shred_data_file`.
    """
    progress = _ShredExecutionProgress.current()
    progress.checkpoint("json_shred_before_rows")
    table_specs = _emitting_table_specs(v2_config) if _table_specs is None else _table_specs

    # Each table's POSITION is its full ``(key, is_array)`` segment tuple: the
    # array hops set its relational depth, the object hops only LOCATE it. A
    # column spec is (name, leaf, type_token, source_depth); source_depth is the
    # ARRAY depth at which the column's value lives — the table's own array
    # depth for a normal column, or a SHALLOWER array depth for an ancestor
    # column (W1), filled into every descendant row at emission (walk-time
    # distribution, never a post-shred join). validate_v2_schema (above) has
    # guaranteed source_depth is the table's depth or a proper-ancestor prefix.
    emit_tables = [(spec.label, spec.segments, list(spec.columns)) for spec in table_specs]

    # Group tables by their full-segment position — the place the walk emits.
    #
    # Everything constant for a (position, table) pair is derived ONCE here
    # rather than per emitted row: the leaf path pre-split into hops, whether
    # the leaf is the scalar sentinel, and whether the table is a scalar child
    # table. On a large file the walk runs these millions of times, and they
    # depend only on the spec and the position's array depth (fixed, since the
    # position is the key) — never on the record being walked.
    tables_by_pos: dict[tuple[PathSeg, ...], list[tuple[str, bool, list[_PreparedCol]]]] = {}
    for label, segments, col_specs in emit_tables:
        pos_depth = array_depth(segments)
        prepared: list[_PreparedCol] = [
            (name, leaf, _leaf_parts(leaf), type_token, source_depth, leaf == _SCALAR_VALUE_LEAF)
            for name, leaf, type_token, source_depth in col_specs
        ]
        is_scalar_table = any(
            is_scalar_leaf and source_depth == pos_depth
            for _n, _l, _p, _t, source_depth, is_scalar_leaf in prepared
        )
        tables_by_pos.setdefault(segments, []).append((label, is_scalar_table, prepared))

    # Descents: at each position, the (object-prefix, array-key) hops to reach a
    # child array, with the resulting child position. Object hops between arrays
    # locate the array without advancing depth; the parent array element is the
    # ancestor for the child level. Intermediate non-table positions still get
    # their descents registered so a deeper table is reachable.
    _Descent = tuple[tuple[str, ...], str, tuple[PathSeg, ...]]  # noqa: N806 (a type alias, conventionally PascalCase)
    descents_by_pos: dict[tuple[PathSeg, ...], set[_Descent]] = {}
    for _label, segments, _cols in emit_tables:
        parent_pos: tuple[PathSeg, ...] = ()
        obj_prefix: list[str] = []
        for j, (key, is_array) in enumerate(segments):
            if is_array:
                child_pos = tuple(segments[: j + 1])
                descents_by_pos.setdefault(parent_pos, set()).add(
                    (tuple(obj_prefix), key, child_pos)
                )
                parent_pos = child_pos
                obj_prefix = []
            else:
                obj_prefix.append(key)

    # The public path retains its historic per-table buffers.  Direct runtime
    # loading supplies a sink, so the walk instead hands each row to its bounded
    # spill bundle immediately and does not accumulate file-sized Python lists.
    buffers: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in emit_tables}

    def _deliver_row(label: str, row: dict[str, Any]) -> None:
        if _emitted_counts is not None:
            _emitted_counts[label] = _emitted_counts.get(label, 0) + 1
        if _row_sink is None:
            buffers[label].append(row)
        else:
            _row_sink(label, row)

    def _count_row_skip(label: str) -> None:
        if stats is not None:
            stats.count_row_skip(label)

    def _emit_row(
        col_specs: list[_PreparedCol],
        value: Any,
        ancestors: tuple[Any, ...],
        depth: int,
    ) -> dict[str, Any]:
        """Build one output row. Each column's value is sourced at its own
        depth: the current node when ``source_depth == depth``, else the
        ancestor dict carried at that shallower depth — the same value
        distributed across every descendant row (W1)."""
        row: dict[str, Any] = {}
        for col_name, leaf, parts, type_token, src_depth, is_scalar_leaf in col_specs:
            src = value if src_depth == depth else ancestors[src_depth]  # pragma: no mutate
            resolved = _resolve_leaf(src, leaf, parts)
            if is_scalar_leaf or (type_token == "str" and not isinstance(resolved, (dict, list))):
                resolved = _coerce_scalar(resolved, type_token)
            row[col_name] = resolved
        progress.advance("json_shred_rows")
        return row

    def _emit_at(pos: tuple[PathSeg, ...], record: Any, ancestors: tuple[Any, ...]) -> None:
        # Process one element located at ``pos`` (a root or array element):
        # emit rows for the tables at ``pos`` and descend into child arrays.
        # ``ancestors[d]`` is the array element at array-depth ``d`` enclosing
        # this one, so ``len(ancestors) == array_depth(pos)``; a row pulls an
        # ancestor (W1) column's value from the right enclosing element.
        depth = array_depth(pos)
        is_dict = isinstance(record, dict)
        is_scalar = not isinstance(record, (dict, list))

        # A scalar child table (single ``$value`` column) takes only scalar
        # elements; an object table takes only dict records. Skip the mismatched
        # shape — but COUNT it (W2 item 2.7): a mixed array loses that element's
        # row for this table, and the loss must be surfaced, never silent.
        for label, is_scalar_table, col_specs in tables_by_pos.get(pos, []):
            shape_matches = is_scalar if is_scalar_table else is_dict
            if not shape_matches:
                _count_row_skip(label)
                continue
            _deliver_row(label, _emit_row(col_specs, record, ancestors, depth))

        if not is_dict:
            return

        # Descend to each child array, navigating any 1-1 object hops that
        # locate it. The object hops don't advance depth; this dict is the
        # ancestor element for the child array's level.
        child_ancestors = ancestors + (record,)
        for obj_prefix, array_key, child_pos in descents_by_pos.get(pos, ()):
            container: Any = record
            for okey in obj_prefix:
                container = container.get(okey) if isinstance(container, dict) else None
            arr = container.get(array_key) if isinstance(container, dict) else None
            _walk_array(arr, child_pos, child_ancestors)

    def _walk_array(arr: Any, pos: tuple[PathSeg, ...], ancestors: tuple[Any, ...]) -> None:
        # Iterate the array at ``pos``, emitting a row per element. A missing
        # key or non-array value yields nothing.
        if not isinstance(arr, list):
            return
        depth = array_depth(pos)
        for item in arr:
            if item is None:
                # A null *element* is a real value for a scalar child table (its
                # $value resolves to None; ancestor columns still distribute), a
                # non-record for an object table (counted as a dropped row).
                for label, is_scalar_table, col_specs in tables_by_pos.get(pos, []):
                    if is_scalar_table:
                        _deliver_row(label, _emit_row(col_specs, None, ancestors, depth))
                    else:
                        _count_row_skip(label)
                continue
            _emit_at(pos, item, ancestors)

    for record in records:
        _emit_at((), record, ())
        progress.advance("json_shred_rows")

    progress.checkpoint("json_shred_after_rows")
    return buffers


def _assert_root_conservation(
    table_specs: tuple[_EmittingTableSpec, ...],
    buffers: Mapping[str, Sequence[object]],
    skip_stats: ShredSkipStats,
    record_count: int,
    *,  # pragma: no mutate
    location: str = "",
    emitted_counts: Mapping[str, int] | None = None,  # pragma: no mutate
) -> None:
    """Assert that every root input record was emitted or explicitly skipped."""
    for table_spec in table_specs:
        if table_spec.segments:
            continue
        emitted = (
            emitted_counts.get(table_spec.label, 0)
            if emitted_counts is not None
            else len(buffers.get(table_spec.label, ()))
        )
        skipped = skip_stats.skipped_rows_by_table.get(table_spec.label, 0)
        if emitted + skipped != record_count:
            raise RuntimeError(
                "json shred conservation violation for root table "
                f"{table_spec.label!r}{location}: {emitted} emitted + {skipped} skipped "
                f"!= {record_count} records read — a row was lost or "
                "duplicated without accounting",
            )


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


def _declared_frame_schema(table_spec: _EmittingTableSpec) -> pl.Schema:
    """Return the exact selected-column schema a cache frame must expose."""
    return pl.Schema(
        {
            name: _POLARS_TYPE_MAP[cast(ColumnType, type_token)]
            for name, _leaf, type_token, _depth in table_spec.columns
        },
    )


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
    progress = _ShredExecutionProgress.current()
    progress.checkpoint("json_shred_frame_before")
    series_list: list[pl.Series] = []
    for col_name, _leaf, col_type in col_specs:
        dtype = _POLARS_TYPE_MAP[cast(ColumnType, col_type)]
        if progress.execution_context is None:
            values = [row.get(col_name) for row in rows]
        else:
            values = []
            for row in rows:
                values.append(row.get(col_name))
                progress.advance("json_shred_frame_values")
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
        # Same guard family for dates (W2 item 2.8): Polars strict-builds a
        # raw JSON int/bool into a Date column SILENTLY as a days-since-epoch
        # offset (2024 → 1975-07-18, True → 1970-01-02) — a garbage date from
        # a successful "strict" build. Reject loudly instead. ISO-8601 strings
        # parse correctly and floats already fail loud in the strict build.
        if col_type == "date" and any(isinstance(v, int) for v in values):
            raise ApiInputSchemaError(
                f"column {col_name!r} is declared 'date' but contains raw JSON "
                "numbers/booleans (Polars would silently reinterpret them as "
                "days since 1970-01-01, e.g. 2024 becomes 1975-07-18); use "
                'ISO-8601 date strings (e.g. "2024-01-15") or change the '
                "column's type",
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
    frame = pl.DataFrame(series_list)
    progress.checkpoint("json_shred_frame_after")
    return frame


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
