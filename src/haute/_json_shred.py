"""Per-frame JSON shred for v2 schema mappings (MULTI_FRAME_PLAN commit 3).

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
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
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
    make_table_path,
    parse_column_path,
    parse_column_path_full,
    parse_table_path,
    validate_v2_schema,
)
from haute._api_input_schema import (
    sanitise_label_for_filesystem as _sanitise_label,
)
from haute._logging import get_logger

logger = get_logger(component="json_shred")


_META_FILENAME = "meta.json"
_STALE_CACHE_MESSAGE = (
    "API Input data hasn't been cached for the current schema, or the cache is stale. "
    "Click 'Cache as Parquet' on the API Input node to (re)build."
)

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
    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            # Fail LOUD rather than silently dropping a malformed table: a
            # skipped entry would let two structurally-different on-disk
            # configs collapse to the SAME fingerprint, so a schema change
            # from one broken shape to another would not invalidate a stale
            # cache. Distinct configs must hash distinctly (W1).
            raise ApiInputSchemaError(f"v2 tables[{ti}] is not a dict")
        cols_canon: list[dict[str, Any]] = []
        for ci, col in enumerate(table.get("columns", []) or []):
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
# Skip accounting (W2 item 2.7) — zero silent record loss
# ---------------------------------------------------------------------------


@dataclass
class ShredSkipStats:
    """Counts of inputs the shred dropped because their shape didn't fit.

    Two units, never conflated:

    - ``skipped_records`` — top-level inputs that aren't JSON objects (a
      JSONL line holding a number/string/array, a non-object element of a
      root array). They produce no rows in ANY table.
    - ``skipped_rows_by_table`` — array elements at an emitting table's
      depth whose shape mismatched that table (a scalar/null in an
      object-table array, an object in a scalar-table array). Each one is
      a row that table silently lost before W2.

    The build records these in its summary, in ``meta.json``, and the
    route surfaces them in the build/status responses.
    """

    skipped_records: int = 0
    skipped_rows_by_table: dict[str, int] = field(default_factory=dict)

    def count_record_skip(self) -> None:
        self.skipped_records += 1

    def count_row_skip(self, label: str) -> None:
        self.skipped_rows_by_table[label] = self.skipped_rows_by_table.get(label, 0) + 1

    @property
    def total(self) -> int:
        return self.skipped_records + sum(self.skipped_rows_by_table.values())

    def as_meta(self) -> dict[str, Any]:
        """The ``skipped`` payload shape written to meta.json / build summary."""
        return {
            "records": self.skipped_records,
            "rows_by_table": dict(self.skipped_rows_by_table),
        }


# ---------------------------------------------------------------------------
# Data-file signature (W2 item 2.4) — validity must see data edits
# ---------------------------------------------------------------------------


def _data_file_signature(data_path: Path) -> dict[str, Any]:
    """Signature of the data file recorded into ``meta.json`` at build time.

    ``size`` + ``mtime_ns`` give a cheap stat-only freshness fast path;
    ``sha256`` arbitrates when the mtime moved without a content change
    (deploy rsync / docker COPY / ``touch``), so the committed-layer deploy
    fallback isn't invalidated by a copy. Raises ``OSError`` if the file is
    unreadable — the build cannot meaningfully record a signature then.
    """
    st = data_path.stat()
    return {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": _hash_file(data_path),
    }


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # pragma: no mutate
            h.update(chunk)
    return h.hexdigest()


def _data_file_matches(recorded: Any, data_path: Path) -> bool:
    """True iff the data file on disk still matches the recorded signature.

    Order of checks: missing/garbled signature → stale (pre-W2 caches
    invalidate once and rebuild); stat failure → stale (serving cached rows
    for a deleted source would be silent wrongness); size mismatch → stale
    (a cheap pre-reject); otherwise the recorded content hash is the sole
    authority.

    The content hash is verified ALWAYS, not skipped on an ``mtime_ns``
    match: a byte-changing rewrite that happens to preserve both ``size``
    and ``mtime_ns`` (a deliberate ``os.utime`` restore, or a same-length
    edit on a filesystem whose mtime resolution the write didn't advance)
    must NOT be served as fresh — that would silently return stale rating
    rows. Correctness of the served data outweighs skipping one hash on the
    validity path; the deploy-copy case (mtime moved, content identical)
    still validates because the hash matches.
    """
    if not isinstance(recorded, dict):
        return False
    try:
        st = data_path.stat()
    except OSError:
        return False
    if st.st_size != recorded.get("size"):
        return False
    return _hash_file(data_path) == recorded.get("sha256")


# ---------------------------------------------------------------------------
# Build serialization (W2 item 2.6)
# ---------------------------------------------------------------------------

# One lock per canonical cache directory. Concurrent builds of the SAME
# cache interleaving their write phases could stamp one schema's meta onto
# another schema's parquets; builds of different caches stay independent.
# Process-local by design: the FastAPI routes are the only production
# producer and run builds in threads of this process.
_BUILD_LOCKS: dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()
_RENAME_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1)  # pragma: no mutate


def _build_lock_for(cache_dir: Path) -> threading.Lock:
    key = os.path.normcase(str(cache_dir.resolve()))
    with _BUILD_LOCKS_GUARD:
        return _BUILD_LOCKS.setdefault(key, threading.Lock())


def _unique_build_tmp_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-tmp-{uuid.uuid4().hex}")


def _unique_build_old_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-old-{uuid.uuid4().hex}")


def _rename_dir_with_retry(source: Path, target: Path) -> None:
    """Rename a fully-built cache dir, retrying transient Windows handle locks."""
    for delay in (*_RENAME_RETRY_DELAYS_SECONDS, None):
        try:
            source.rename(target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Record iteration
# ---------------------------------------------------------------------------


def _iter_records(
    data_path: Path,
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    """Yield top-level records from a JSON or JSONL file.

    JSONL: one record per non-empty line.
    JSON: if the file's root is an array, yields each element; if the
    root is an object, yields that single object.

    A top-level input that parses as valid JSON but isn't an object (a
    JSONL line holding ``5`` / ``"x"`` / ``[...]``, a non-object element of
    a root array, a scalar root) is not a record and is skipped — *stats*,
    when provided, counts each one so the build can surface the loss
    (W2 item 2.7). Blank JSONL lines are formatting, not records, and are
    never counted. Malformed JSON still raises.
    """

    def _count_record_skip() -> None:
        if stats is not None:
            stats.count_record_skip()

    if data_path.suffix.lower() == ".jsonl":
        with data_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                obj = orjson.loads(stripped)
                if isinstance(obj, dict):
                    yield obj
                else:
                    _count_record_skip()
        return
    raw = data_path.read_bytes()
    if not raw.strip():
        return
    obj = orjson.loads(raw)
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item
            else:
                _count_record_skip()
    elif isinstance(obj, dict):
        yield obj
    else:
        _count_record_skip()


def _json_decode_error(message: str, pos: int) -> orjson.JSONDecodeError:
    return orjson.JSONDecodeError(message, "", pos)


def _iter_sampled_json_array_records(
    data_path: Path,
    sample_size: int,
) -> Iterator[dict[str, Any]]:
    """Yield up to ``sample_size`` object records from a root JSON array.

    This is intentionally used only for inference sampling. Full builds and
    unsampled inference still parse the whole file so malformed data is caught
    before any cache is materialised.
    """
    yielded = 0
    pos = 0
    expect_value = False

    with data_path.open("rb") as f:

        def _read_byte() -> bytes:
            nonlocal pos
            b = f.read(1)
            if b:
                pos += 1
            return b

        def _read_non_ws() -> bytes:
            while True:
                b = _read_byte()
                if not b or b not in b" \t\r\n":
                    return b

        first = _read_non_ws()
        if not first:
            return
        if first != b"[":
            yield from islice(_iter_records(data_path), sample_size)
            return

        def _validate_eof() -> None:
            trailing = f.read()
            if any(b not in b" \t\r\n" for b in trailing):
                raise _json_decode_error("unexpected trailing data", pos)

        while yielded < sample_size:
            first = _read_non_ws()
            if not first:
                raise _json_decode_error("unexpected end of data", pos)
            if first == b"]":
                if expect_value:
                    raise _json_decode_error("trailing comma in array", pos)
                _validate_eof()
                return

            value, delimiter = _read_root_array_value(first, _read_byte, lambda: pos)
            obj = orjson.loads(value)
            if isinstance(obj, dict):
                yield obj
                yielded += 1
            expect_value = delimiter == b","
            if delimiter == b"]":
                _validate_eof()
                return


def _read_root_array_value(
    first: bytes,
    read_byte: Callable[[], bytes],
    current_pos: Callable[[], int],
) -> tuple[bytes, bytes]:
    """Read one value from a root JSON array and return its delimiter."""
    buf = bytearray(first)
    depth = 1 if first in {b"{", b"["} else 0
    in_string = first == b'"'
    escaped = False

    while True:
        b = read_byte()
        if not b:
            raise _json_decode_error("unexpected end of data", current_pos())

        if in_string:
            buf.extend(b)
            if escaped:
                escaped = False
            elif b == b"\\":
                escaped = True
            elif b == b'"':
                in_string = False
            continue

        if b == b'"':
            buf.extend(b)
            in_string = True
            continue

        if b in {b"{", b"["}:
            depth += 1
            buf.extend(b)
            continue

        if b in {b"}", b"]"}:
            if depth > 0:
                depth -= 1
                buf.extend(b)
                continue
            if b == b"]":
                return bytes(buf).rstrip(), b
            raise _json_decode_error("unexpected '}'", current_pos())

        # A depth-0 ``]`` is already handled by the close-delimiter block above,
        # so only the comma remains as a value terminator here.
        if depth == 0 and b == b",":
            return bytes(buf).rstrip(), b

        buf.extend(b)


def _iter_records_for_inference(
    data_path: Path,
    *,  # pragma: no mutate
    sample_size: int | None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    if sample_size is None or sample_size <= 0:
        yield from _iter_records(data_path)
        return
    if data_path.suffix.lower() == ".jsonl":
        yield from islice(_iter_records(data_path), sample_size)
        return
    yield from _iter_sampled_json_array_records(data_path, sample_size)


# ---------------------------------------------------------------------------
# Shred core
# ---------------------------------------------------------------------------


_LeafSpec = tuple[str, str, str]  # (column_name, leaf_path_dotted, type_token)
# As _LeafSpec, plus the array-iteration depth at which the column's value
# lives (W1): equal to the table's depth for a normal column, shallower for
# an ancestor column whose value distributes over descendant rows.
_WalkSpec = tuple[str, str, str, int]


def _resolve_leaf(value: Any, leaf: str) -> Any:
    """Resolve a dotted leaf path within a single dict.

    For ``leaf = "policy_id"`` returns ``value["policy_id"]`` (or None).
    For ``leaf = "profile.age"`` walks one level deeper.

    A dotted leaf addresses 1-1 OBJECT nesting only (that is the only shape
    inference ever produces a dotted leaf for — an array becomes a child
    table, never a dotted hop). If a list is encountered mid-walk the data
    doesn't match that shape, and the historical behaviour of silently
    taking ``cur[0]`` discarded every other element with no accounting.
    That silent collapse is a conservation violation, so it now fails LOUD
    (W1): the array field must be modelled as its own child table.

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


def shred_to_buffers(
    records: Iterable[dict[str, Any]],
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
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
    shape mismatched that table (W2 item 2.7); the production build always
    passes one.
    """
    validate_v2_schema(v2_config)

    # Tables we'll actually emit — the shared predicate (W2 item 2.5). Each
    # column spec is (name, leaf, type_token, source_depth); source_depth is
    # the array-iteration depth at which the column's value lives. It equals
    # the table's own depth for a normal column, or a SHALLOWER depth for an
    # ancestor column (W1) — whose value is filled into every descendant row
    # at emission (walk-time distribution, never a post-shred join).
    # validate_v2_schema (above) has already guaranteed source_depth is the
    # table's depth or a proper-ancestor prefix of it.
    # Each table's POSITION is its full ``(key, is_array)`` segment tuple: the
    # array hops set its relational depth, the object hops only LOCATE it. A
    # column spec is (name, leaf, type_token, source_depth); source_depth is the
    # ARRAY depth at which the column's value lives — the table's own array
    # depth for a normal column, or a SHALLOWER array depth for an ancestor
    # column (W1), filled into every descendant row at emission (walk-time
    # distribution, never a post-shred join). validate_v2_schema (above) has
    # guaranteed source_depth is the table's depth or a proper-ancestor prefix.
    emit_tables: list[tuple[str, tuple[PathSeg, ...], list[_WalkSpec]]] = []
    for table in v2_config["tables"]:
        if not table_is_emitting(table):
            continue
        segments = parse_table_path(table["path"])
        col_specs: list[_WalkSpec] = []
        for col in table.get("columns", []) or []:
            if not col.get("selected"):
                continue
            locating, leaf = parse_column_path_full(col["path"])
            col_specs.append((col["name"], leaf, col.get("type", "str"), array_depth(locating)))
        _reject_reserved_leaf_collision(table["label"], array_depth(segments), col_specs)
        emit_tables.append((table["label"], segments, col_specs))

    # Group tables by their full-segment position — the place the walk emits.
    tables_by_pos: dict[tuple[PathSeg, ...], list[tuple[str, list[_WalkSpec]]]] = {}
    for label, segments, col_specs in emit_tables:
        tables_by_pos.setdefault(segments, []).append((label, col_specs))

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

    # Buffers — one list per emitting table, keyed by label.
    buffers: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in emit_tables}

    def _count_row_skip(label: str) -> None:
        if stats is not None:
            stats.count_row_skip(label)

    def _emit_row(
        col_specs: list[_WalkSpec],
        value: Any,
        ancestors: tuple[Any, ...],
        depth: int,
    ) -> dict[str, Any]:
        """Build one output row. Each column's value is sourced at its own
        depth: the current node when ``source_depth == depth``, else the
        ancestor dict carried at that shallower depth — the same value
        distributed across every descendant row (W1)."""
        row: dict[str, Any] = {}
        for col_name, leaf, type_token, src_depth in col_specs:
            src = value if src_depth == depth else ancestors[src_depth]
            resolved = _resolve_leaf(src, leaf)
            if leaf == _SCALAR_VALUE_LEAF:
                resolved = _coerce_scalar(resolved, type_token)
            row[col_name] = resolved
        return row

    def _emit_at(pos: tuple[PathSeg, ...], record: Any, ancestors: tuple[Any, ...]) -> None:
        # Process one element located at ``pos`` (a root or array element):
        # emit rows for the tables at ``pos`` and descend into child arrays.
        # ``ancestors[d]`` is the array element at array-depth ``d`` enclosing
        # this one, so ``len(ancestors) == array_depth(pos)``; a row pulls an
        # ancestor (W1) column's value from the right enclosing element.
        depth = array_depth(pos)
        is_dict = isinstance(record, dict)

        # A scalar child table (single ``$value`` column) takes only scalar
        # elements; an object table takes only dict records. Skip the mismatched
        # shape — but COUNT it (W2 item 2.7): a mixed array loses that element's
        # row for this table, and the loss must be surfaced, never silent.
        for label, col_specs in tables_by_pos.get(pos, []):
            is_scalar_table = any(leaf == _SCALAR_VALUE_LEAF for _n, leaf, _t, _d in col_specs)
            if is_scalar_table != (not is_dict):
                _count_row_skip(label)
                continue
            buffers[label].append(_emit_row(col_specs, record, ancestors, depth))

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
                for label, col_specs in tables_by_pos.get(pos, []):
                    if any(leaf == _SCALAR_VALUE_LEAF for _n, leaf, _t, _d in col_specs):
                        buffers[label].append(_emit_row(col_specs, None, ancestors, depth))
                    else:
                        _count_row_skip(label)
                continue
            _emit_at(pos, item, ancestors)

    for record in records:
        _emit_at((), record, ())

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
    data_path: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    cache_dir: str | Path,  # pragma: no mutate
) -> dict[str, Any]:
    """Build the per-port parquet cache for *data_path* under *v2_config*.

    *cache_dir* is the per-data-file directory (e.g.
    ``.haute_cache/working/json_<hash>/``); the caller already knows which
    layer to target.

    Returns a summary dict with ``schema_mode``, ``schema_fingerprint``,
    ``tables`` (per-port row/column counts + on-disk parquet paths),
    ``data_file`` (the data-file signature validity checks against — W2
    item 2.4), and ``skipped`` (counts of shape-mismatched inputs dropped
    during the shred — W2 item 2.7). Also writes ``meta.json`` into
    *cache_dir* with the same payload so later cache-validity checks don't
    need to re-shred to know what's there.

    The build is **serialized** per cache directory (a concurrent build of
    the same cache waits) and **atomic**: everything is written into a
    sibling temp directory which is swapped into place only once complete,
    so a failed or interrupted build can never corrupt a previously valid
    cache or leave a half-written one (W2 item 2.6).
    """
    dp = Path(data_path)
    cd = Path(cache_dir)

    validate_v2_schema(v2_config)

    with _build_lock_for(cd):
        # No-op trapdoor: if the existing meta.json's fingerprint matches the
        # current v2 schema, the recorded data-file signature still matches
        # the file on disk, AND all expected per-port parquets exist, skip
        # the rebuild entirely. Repeated cache-button clicks then don't churn
        # the preview cache via commit 1's mtime-in-fingerprint invalidation.
        if is_per_port_cache_valid(cd, v2_config, data_path=dp):
            existing_meta = read_per_port_cache_meta(cd)
            if existing_meta is not None:
                fp8 = str(existing_meta.get("schema_fingerprint", ""))[:8]  # pragma: no mutate
                logger.info(
                    "json_shred_build_noop",
                    data_path=str(dp),
                    cache_dir=str(cd),
                    fingerprint=fp8,
                )
                return {
                    "schema_mode": existing_meta.get("schema_mode", "v2"),
                    "schema_fingerprint": existing_meta.get("schema_fingerprint", ""),
                    "tables": existing_meta.get("tables", []),
                    "data_file": existing_meta.get("data_file"),
                    "skipped": existing_meta.get("skipped", {"records": 0, "rows_by_table": {}}),
                    "cache_dir": str(cd),
                }

        # Record the data-file signature BEFORE reading records so the
        # signature can only ever be same-or-older than the data we shred —
        # a mid-build edit then invalidates on the next validity check
        # rather than being masked.
        data_file_sig = _data_file_signature(dp)

        # Re-parse table-paths + columns so we can stream-write per-port
        # parquets immediately after the shred. Same emitting predicate as
        # the shred, validity and load (W2 item 2.5).
        emit_tables: list[tuple[str, list[_LeafSpec]]] = []
        for table in v2_config["tables"]:
            if not table_is_emitting(table):
                continue
            col_specs: list[_LeafSpec] = []
            for col in table.get("columns", []) or []:
                if not col.get("selected"):
                    continue
                leaf = parse_column_path(col["path"], table["path"])
                col_specs.append((col["name"], leaf, col.get("type", "str")))
            emit_tables.append((table["label"], col_specs))

        # Shred — single pass, with skip accounting (W2 item 2.7). The record
        # iterator is consumed directly (not materialised into a list) so the
        # build doesn't hold a full extra Python-object copy of the file
        # alongside the row buffers (W1). A tiny wrapper counts the object
        # records as they flow through so the conservation assertion below can
        # cross-check that no row silently vanished.
        skip_stats = ShredSkipStats()
        record_count = 0

        def _counted_records() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for rec in _iter_records(dp, stats=skip_stats):
                record_count += 1
                yield rec

        buffers = shred_to_buffers(_counted_records(), v2_config, stats=skip_stats)

        # Conservation assertion (W1): at the ROOT array level, every object
        # record contributes EXACTLY one row to each emitting root table, or is
        # counted as a shape-mismatch skip for it — never both, never neither.
        # A violation means a row vanished (or duplicated) without accounting,
        # i.e. a shred bug; fail loud rather than write a cache that silently
        # lost data. (Non-object top-level inputs are counted separately in
        # ``skipped_records`` and are not yielded, so they don't enter this sum.)
        for table in v2_config["tables"]:
            if not table_is_emitting(table) or parse_table_path(table["path"]) != ():
                continue
            root_label = table["label"]
            emitted = len(buffers.get(root_label, []))
            skipped_here = skip_stats.skipped_rows_by_table.get(root_label, 0)
            if emitted + skipped_here != record_count:
                raise RuntimeError(
                    "json shred conservation violation for root table "
                    f"{root_label!r}: {emitted} emitted + {skipped_here} skipped "
                    f"!= {record_count} records read — a row was lost or "
                    "duplicated without accounting",
                )

        # Write per-port parquets + meta into a sibling temp dir, then swap
        # it into place. Unique temp names prevent staging-dir collisions
        # across xdist/CLI processes; the live-dir swap itself remains the
        # atomic publish boundary.
        import pyarrow.parquet as pq  # local — keeps top-of-module import surface small

        fingerprint = _v2_fingerprint(v2_config)
        legacy_tmp_dir = cd.with_name(cd.name + ".build-tmp")
        if legacy_tmp_dir.exists():
            shutil.rmtree(legacy_tmp_dir)
        tmp_dir = _unique_build_tmp_dir(cd)
        tmp_dir.mkdir(parents=True)
        try:
            table_summaries: list[dict[str, Any]] = []
            for label, col_specs in emit_tables:
                rows = buffers.get(label, [])
                frame = _buffer_to_frame(rows, col_specs)
                parquet_path = tmp_dir / f"{_sanitise_label(label)}.parquet"
                # Convert to Arrow and attach the per-frame schema in the
                # footer (DUAL_CACHE.md §3). Polars's DataFrame.write_parquet
                # doesn't accept the bytes-keyed metadata shape PyArrow uses;
                # going via Arrow directly is the same pattern v1 used in
                # _json_flatten.
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

            meta_payload = {
                "schema_mode": "v2",
                "schema_fingerprint": fingerprint,
                "tables": table_summaries,
                "data_file": data_file_sig,
                "skipped": skip_stats.as_meta(),
            }
            (tmp_dir / _META_FILENAME).write_bytes(orjson.dumps(meta_payload))
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        _swap_dir_into_place(tmp_dir, cd)

    if skip_stats.total:
        logger.warning(
            "json_shred_records_skipped",
            data_path=str(dp),
            cache_dir=str(cd),
            skipped_records=skip_stats.skipped_records,
            skipped_rows_by_table=skip_stats.skipped_rows_by_table,
        )
    logger.info(
        "json_shred_built",
        data_path=str(dp),
        cache_dir=str(cd),
        table_count=len(table_summaries),
        fingerprint=fingerprint[:8],  # pragma: no mutate
    )

    return {
        "schema_mode": "v2",
        "schema_fingerprint": fingerprint,
        "tables": table_summaries,
        "data_file": data_file_sig,
        "skipped": skip_stats.as_meta(),
        "cache_dir": str(cd),
    }


def _swap_dir_into_place(tmp_dir: Path, live_dir: Path) -> None:
    """Atomically replace *live_dir* with the fully-built *tmp_dir*.

    Same rename dance as :func:`haute._json_flatten.mirror_cache_to_committed`:
    rename the live dir aside, rename the temp dir in, then best-effort
    remove the old copy. If the second rename fails the old dir is restored
    before re-raising, so the cache is never left missing.
    """
    legacy_backup = live_dir.with_name(live_dir.name + ".build-old")
    if legacy_backup.exists():
        shutil.rmtree(legacy_backup)

    if live_dir.exists():
        backup = _unique_build_old_dir(live_dir)
        try:
            _rename_dir_with_retry(live_dir, backup)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:
            try:
                _rename_dir_with_retry(backup, live_dir)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    else:
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise


def load_per_port_cache(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
) -> dict[str, pl.LazyFrame]:
    """Scan the per-port parquets in *cache_dir* for each emitting table.

    "Emitting" is the shared :func:`table_is_emitting` predicate (emit AND
    ≥1 selected column) — exactly the set of parquets the build writes.
    Returns ``{table_label: LazyFrame}``. A missing parquet (e.g. a table
    that was emitting at build time but is now disabled) is skipped — the
    caller is expected to validate cache freshness via
    :func:`is_per_port_cache_valid` before this.
    """
    cd = Path(cache_dir)
    out: dict[str, pl.LazyFrame] = {}
    for table in v2_config.get("tables", []) or []:
        if not table_is_emitting(table):
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
) -> pl.LazyFrame | dict[str, pl.LazyFrame]:  # pragma: no mutate
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
      (schema fingerprint OR data-file signature mismatch) raises the
      "click Cache as Parquet" message.
    - 1 emitting label → a bare ``LazyFrame`` (single-frame shorthand); 2+ →
      a ``dict[port_label, LazyFrame]`` in schema order.

    Frame resolution uses the shared :func:`table_is_emitting` predicate, so
    an emit-true table with zero selected columns contributes no frame and —
    crucially — no longer wedges validity (W2 item 2.5).
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
    emit_labels = [t["label"] for t in emit_true_tables if table_is_emitting(t)]
    if not emit_labels:
        labels = [t["label"] for t in emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected columns. "
            f"Open the node and tick at least one column on the emitting "
            f"table(s): {labels}. Then click 'Cache as Parquet' before previewing.",
        )
    cache_dir = _json_cache_dir(data_path, "working")
    if not is_per_port_cache_valid(cache_dir, config, data_path=data_path):
        # Fall back to the committed layer (deploy / fresh-server case).
        cache_dir = _json_cache_dir(data_path, "committed")
        if not is_per_port_cache_valid(cache_dir, config, data_path=data_path):
            raise RuntimeError(_STALE_CACHE_MESSAGE)
    bundle = load_per_port_cache(cache_dir, config)
    missing_labels = [label for label in emit_labels if label not in bundle]
    if missing_labels:
        raise RuntimeError(
            "API Input cache changed while it was being loaded; missing parquet "
            f"frame(s): {missing_labels}. {_STALE_CACHE_MESSAGE}",
        )
    # Single-frame shorthand: bare LazyFrame instead of a one-entry dict.
    if len(emit_labels) == 1:
        return bundle[emit_labels[0]]
    # Multi-frame: preserve schema order so executor logs/errors are deterministic.
    return {label: bundle[label] for label in emit_labels}


def is_per_port_cache_valid(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
) -> bool:
    """Cheap validity check: meta.json's fingerprint matches the v2 schema,
    the recorded data-file signature still matches *data_path* on disk
    (W2 item 2.4 — an edited data file means the cached rows are stale),
    AND a parquet exists for every emitting table (shared
    :func:`table_is_emitting` predicate — W2 item 2.5).

    The data-file check is stat-fast: size + mtime_ns match → fresh; an
    mtime-only drift (copy/touch) falls back to the recorded content hash
    so the committed-layer deploy fallback survives file copies. A meta
    without a recorded signature (pre-W2 cache) is stale by construction —
    one-time invalidation on upgrade.
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
    try:
        expected_fingerprint = _v2_fingerprint(v2_config)
    except ApiInputSchemaError:
        # A config so malformed it can't even be fingerprinted (a non-dict
        # table/column) cannot match a validly-built cache — treat it as
        # stale/invalid, never fresh. This keeps the predicate's bool contract
        # for direct validity probes on unvalidated on-disk configs (e.g.
        # GET /status), while _v2_fingerprint itself still fails loud so no two
        # distinct malformed configs silently collapse to one fingerprint. The
        # build path validates the schema before it ever reaches here.
        return False
    if meta.get("schema_fingerprint") != expected_fingerprint:
        return False
    if not _data_file_matches(meta.get("data_file"), Path(data_path)):
        return False
    for table in v2_config.get("tables", []) or []:
        if not table_is_emitting(table):
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


def _widen_type(existing: str | None, new: str) -> str:  # pragma: no mutate
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


def _assign_column_names(object_paths: list[tuple[str, ...]]) -> dict[tuple[str, ...], str]:
    """Name flattened object-leaf columns: bare leaf where unique, else qualify.

    The 2026-06-17 ruling: a 1-1 object's scalars flatten into one table. Each
    column's NAME is its bare leaf key when that's unique within the table; on
    collision (e.g. two add-ons each carrying ``selected``) the colliding
    columns take their full underscore-joined object path
    (``breakdown_cover_selected``). A residual pathological clash gets a numeric
    suffix so names stay unique (validate_v2_schema rejects duplicates
    downstream). The column PATH always carries the full address regardless of
    the chosen name.
    """
    bare = {op: op[-1] for op in object_paths}
    bare_counts: dict[str, int] = {}
    for leaf in bare.values():
        bare_counts[leaf] = bare_counts.get(leaf, 0) + 1
    names: dict[tuple[str, ...], str] = {}
    for op in object_paths:
        names[op] = "_".join(op) if bare_counts[bare[op]] > 1 else bare[op]
    # Deterministic final dedup for any residual collision.
    seen: set[str] = set()
    for op in object_paths:
        nm = names[op]
        if nm in seen:
            i = 2
            while f"{nm}_{i}" in seen:
                i += 1
            nm = f"{nm}_{i}"
            names[op] = nm
        seen.add(nm)
    return names


def _reject_unexpressible_key(key: str) -> None:
    """Fail loud on a source JSON key the v2 path grammar can't address cleanly.

    Two keys parse as valid paths but resolve to the WRONG thing, silently
    dropping the real value at shred time, so inference must not manufacture a
    column path for them (W1):

    - ``$value`` collides with the reserved scalar-array sentinel — the shred
      would read it as "the element itself" and never touch the field.
    - a key containing ``.`` is split by the dotted-leaf walker into two
      object hops (``{"a.b": v}`` becomes ``value["a"]["b"]`` → ``None``).

    Both must be renamed in the source data; there is no unambiguous mapping.
    (Other non-identifier keys — hyphens, spaces, leading digits — already fail
    loud downstream in :func:`validate_v2_schema` when the path is parsed.)
    """
    if key == _SCALAR_VALUE_LEAF:
        raise ApiInputSchemaError(
            f"source JSON key '{_SCALAR_VALUE_LEAF}' collides with the reserved "
            "scalar-array sentinel and cannot be addressed as a column; rename "
            "this field in the source data",
            column=key,
        )
    if "." in key:
        raise ApiInputSchemaError(
            f"source JSON key {key!r} contains '.', which the path grammar "
            "reserves as the object-nesting separator, so it cannot be expressed "
            "as a column path; rename this field in the source data",
            column=key,
        )


def infer_v2_schema_from_data(
    data_path: str | Path,  # pragma: no mutate
    *,  # pragma: no mutate
    sample_size: int | None = None,  # pragma: no mutate
) -> dict[str, Any]:
    """Sniff the v2 schema mapping from the records of *data_path*.

    Produces a v2 config (without the apiInput's ``path`` / ``contract``
    metadata) — the caller stitches them in. Relational depth is ARRAY
    (``[:]``) depth only (the 2026-06-17 object-nesting ruling). Walks the
    records and records:

    - every ARRAY-of-objects depth as a candidate table — a 1-1 OBJECT mints
      NO table; its scalars fold into the enclosing array level as dotted-leaf
      columns (``$[:].quote_metadata.quote_id``), and an array nested inside a
      1-1 object is a child table located through the object hop
      (``$[:].proposer.claims[:]``);
    - the leaf keys at each level, with types inferred and *widened* across
      all scanned records (so a late-appearing float widens an int column
      rather than crashing the build), and named bare-where-unique /
      qualified-on-collision (see :func:`_assign_column_names`);
    - a JSON **scalar array** (e.g. ``["TPFT", "comprehensive"]``) as its own
      child table with a single ``value`` column — mirroring how an array of
      objects becomes a child table (Option 2). Element types are widened
      the same way.

    Types are inferred across the whole file by default; pass ``sample_size``
    to cap the number of records scanned. For JSONL and root JSON arrays, the
    iterator stops after the requested object records instead of reading the
    rest of the file.

    Raises :class:`ApiInputSchemaError` for a nested array (array of arrays),
    which can't be expressed as a flat table.

    Each table is ``emit=True`` only for the root; nested tables are off so
    the user opts in explicitly.
    """
    records = _iter_records_for_inference(Path(data_path), sample_size=sample_size)

    # Relational level (full ``(key, is_array)`` segments, ending at an array or
    # root ``()``) → {object-path-within-level: widened type}. Object nesting is
    # FOLDED into the enclosing array level (the 2026-06-17 ruling): a 1-1
    # object mints no table — its scalars become dotted-leaf columns here.
    levels: dict[tuple[PathSeg, ...], dict[tuple[str, ...], str]] = {(): {}}
    # Level → widened element type, for scalar-array child tables. ``None``
    # marks a level seen ONLY as an empty array (``[]``): its type is still
    # unknown, so it must not seed a concrete token that would then poison
    # widening — a later ``[1, 2]`` must type the column ``int``, not ``str``
    # (W1). A level that stays ``None`` (only ever empty) defaults to ``str``
    # at table assembly.
    scalar_levels: dict[tuple[PathSeg, ...], str | None] = {}

    def _walk(value: Any, level: tuple[PathSeg, ...], obj_prefix: tuple[str, ...]) -> None:
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                _walk(item, level, obj_prefix)
            return
        if not isinstance(value, dict):
            return
        cols = levels.setdefault(level, {})
        for k, v in value.items():
            _reject_unexpressible_key(k)
            opath = obj_prefix + (k,)
            if isinstance(v, dict):
                # 1-1 object — relationally transparent: stay in this level,
                # deepen the object prefix (no new table).
                _walk(v, level, opath)
            elif isinstance(v, list):
                # An array of objects descends a level; it is LOCATED through the
                # object prefix that wraps it (object hops carry is_array=False).
                child = level + tuple((p, False) for p in obj_prefix) + ((k, True),)
                if not v:
                    # Empty array — ambiguous. Tentatively a (currently empty)
                    # scalar child table; a later record with objects records
                    # columns here and wins at table-assembly time. Seed ``None``
                    # (type-unknown), NOT ``"str"``: a concrete seed would widen
                    # a later pure-int array to ``str`` (W1). ``setdefault`` keeps
                    # any type already learned from an earlier non-empty array.
                    scalar_levels.setdefault(child, None)
                elif any(isinstance(item, dict) for item in v):
                    _walk(v, child, ())  # array of objects → object child table
                else:
                    elem_type: str | None = None
                    for item in v:
                        if isinstance(item, list):
                            raise ApiInputSchemaError(
                                f"column {'.'.join(opath)!r}: nested arrays "
                                "(array of arrays) cannot be expressed as a flat "
                                "table column; flatten this field in the source data",
                                column=".".join(opath),
                            )
                        if item is None:
                            continue
                        elem_type = _widen_type(elem_type, _infer_type(item))
                    scalar_levels[child] = _widen_type(scalar_levels.get(child), elem_type or "str")
            else:
                cols[opath] = _widen_type(cols.get(opath), _infer_type(v))

    for record in records:
        _walk(record, (), ())

    tables: list[dict[str, Any]] = []
    all_levels = set(levels) | set(scalar_levels)
    for level in sorted(all_levels, key=lambda s: (array_depth(s), len(s), tuple(s))):
        table_path = make_table_path(level)
        # A level ever dict-walked is an object table; a level only ever reached
        # as a scalar array is a scalar child table.
        if level in scalar_levels and level not in levels:
            columns: list[dict[str, Any]] = [
                {
                    "name": _SCALAR_VALUE_COLUMN,
                    "path": f"{table_path}.{_SCALAR_VALUE_LEAF}",
                    # ``None`` = only-ever-empty array (type never observed):
                    # default to ``str`` so the column has a concrete type.
                    "type": scalar_levels[level] or "str",
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
            ]
        else:
            col_paths = list(levels.get(level, {}).keys())
            names = _assign_column_names(col_paths)
            columns = [
                {
                    "name": names[opath],
                    "path": f"{table_path}." + ".".join(opath),
                    "type": levels[level][opath],
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
                for opath in col_paths
            ]
        tables.append(
            {
                "path": table_path,
                "label": table_path,
                "displayPath": None,
                "emit": array_depth(level) == 0,  # only the root level emits by default
                "row_id_column": None,
                "columns": columns,
            },
        )
    return {"tables": tables}


def read_per_port_cache_meta(cache_dir: str | Path) -> dict[str, Any] | None:  # pragma: no mutate
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
