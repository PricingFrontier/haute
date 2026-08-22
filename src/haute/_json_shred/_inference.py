"""V2 schema inference from data: bounded record sampling, type widening, and
deterministic column naming."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from haute._api_input_schema import (
    _RESERVED_LEAF as _SCALAR_VALUE_LEAF,
)
from haute._api_input_schema import (
    ApiInputSchemaError,
    PathSeg,
    array_depth,
    derive_identifier_label,
    make_table_path,
)
from haute._json_shred import _records
from haute._json_shred._records import _ChunkFailure
from haute._json_shred._shred import _SCALAR_VALUE_COLUMN
from haute._jsonpath import is_identifier_name
from haute._logging import get_logger

logger = get_logger(component="json_shred")


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
    if {existing, new} == {"int", "float"}:  # pragma: no mutate
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
        names[op] = "_".join(op) if bare_counts[bare[op]] > 1 else bare[op]  # pragma: no mutate
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

    Inference must not manufacture a column path for any key outside the
    shared ASCII identifier grammar. Two cases receive more specific
    diagnostics because they previously parsed but resolved to the wrong
    value at shred time (W1):

    - ``$value`` collides with the reserved scalar-array sentinel — the shred
      would read it as "the element itself" and never touch the field.
    - a key containing ``.`` is split by the dotted-leaf walker into two
      object hops (``{"a.b": v}`` becomes ``value["a"]["b"]`` → ``None``).

    Every rejected key must be renamed in the source data; there is no
    unambiguous mapping.
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
    if not is_identifier_name(key):
        raise ApiInputSchemaError(
            f"source JSON key {key!r} is not an addressable identifier; keys "
            "must match [A-Za-z_][A-Za-z0-9_]*, so rename this field in the "
            "source data",
            column=key,
        )


_InferenceLevel = tuple[PathSeg, ...]


_InferenceObjectPath = tuple[str, ...]


@dataclass
class _InferenceState:
    """Compact, mergeable evidence collected by exact schema inference."""

    levels: dict[_InferenceLevel, dict[_InferenceObjectPath, str]] = field(
        default_factory=lambda: {(): {}}
    )
    scalar_levels: dict[_InferenceLevel, str | None] = field(  # pragma: no mutate
        default_factory=dict
    )
    container_paths: dict[_InferenceLevel, set[_InferenceObjectPath]] = field(default_factory=dict)
    null_leaves: dict[_InferenceLevel, dict[_InferenceObjectPath, None]] = field(
        default_factory=dict
    )
    validated_keys: set[str] = field(  # pragma: no mutate - repr metadata only
        default_factory=set, repr=False
    )

    def _validate_key_once(self, key: str) -> None:
        if key in self.validated_keys:
            return
        _reject_unexpressible_key(key)
        self.validated_keys.add(key)

    def walk(
        self,
        value: Any,
        level: _InferenceLevel = (),
        obj_prefix: _InferenceObjectPath = (),
    ) -> None:
        """Merge one JSON value's schema evidence into this state."""
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                self.walk(item, level, obj_prefix)
            return
        if not isinstance(value, dict):
            return

        cols = self.levels.setdefault(level, {})
        for key, child_value in value.items():
            self._validate_key_once(key)
            object_path = obj_prefix + (key,)
            if isinstance(child_value, dict):
                self.container_paths.setdefault(level, set()).add(object_path)
                self.walk(child_value, level, object_path)
            elif isinstance(child_value, list):
                self.container_paths.setdefault(level, set()).add(object_path)
                child_level = level + tuple((part, False) for part in obj_prefix) + ((key, True),)
                if not child_value:
                    self.scalar_levels.setdefault(child_level, None)
                elif any(isinstance(item, dict) for item in child_value):
                    self.walk(child_value, child_level, ())
                else:
                    element_type: str | None = None  # pragma: no mutate
                    for item in child_value:
                        if isinstance(item, list):
                            dotted_path = ".".join(object_path)
                            raise ApiInputSchemaError(
                                f"column {dotted_path!r}: nested arrays "
                                "(array of arrays) cannot be expressed as a flat "
                                "table column; flatten this field in the source data",
                                column=dotted_path,
                            )
                        if item is None:
                            continue
                        element_type = _widen_type(element_type, _infer_type(item))
                    self.scalar_levels[child_level] = _widen_type(
                        self.scalar_levels.get(child_level), element_type or "str"
                    )
            elif child_value is None:
                self.null_leaves.setdefault(level, {})[object_path] = None
            else:
                cols[object_path] = _widen_type(cols.get(object_path), _infer_type(child_value))

    def merge(self, other: _InferenceState) -> None:
        """Merge a later file range while preserving serial observation order."""
        for level, observed_columns in other.levels.items():
            columns = self.levels.setdefault(level, {})
            for object_path, observed_type in observed_columns.items():
                columns[object_path] = _widen_type(columns.get(object_path), observed_type)

        for level, scalar_type in other.scalar_levels.items():
            if level not in self.scalar_levels:
                self.scalar_levels[level] = scalar_type
            elif scalar_type is not None:
                self.scalar_levels[level] = _widen_type(self.scalar_levels[level], scalar_type)

        for level, container_paths in other.container_paths.items():
            self.container_paths.setdefault(level, set()).update(container_paths)
        for level, null_paths in other.null_leaves.items():
            self.null_leaves.setdefault(level, {}).update(null_paths)
        self.validated_keys.update(other.validated_keys)


def _infer_records(records: Iterable[dict[str, Any]]) -> _InferenceState:
    state = _InferenceState()
    for record in records:
        state.walk(record)
    return state


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _InferenceChunkResult:
    index: int
    state: _InferenceState | None = None  # pragma: no mutate
    failure: _ChunkFailure | None = None  # pragma: no mutate


def _infer_chunk(args: tuple[str, int, int, int]) -> _InferenceChunkResult:
    """Infer one newline-delimited byte range in a spawned worker."""
    data_path_s, start, end, index = args
    try:
        state = _infer_records(_records._iter_range_records(Path(data_path_s), start, end))
        return _InferenceChunkResult(index=index, state=state)
    except Exception as exc:  # noqa: BLE001 — reconstructed in the parent
        return _InferenceChunkResult(index=index, failure=_records._failure_from_exception(exc))


def _infer_jsonl_in_parallel(data_path: Path, ranges: list[tuple[int, int]]) -> _InferenceState:
    """Infer all *ranges* concurrently and merge them in file order."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    tasks = [(str(data_path), start, end, index) for index, (start, end) in enumerate(ranges)]
    workers = _records._parallel_worker_count(len(tasks))
    logger.info(
        "json_schema_infer_parallel_start",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
    )

    started = time.perf_counter()
    merged = _InferenceState()
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    try:
        for result in pool.map(_infer_chunk, tasks):
            if result.failure is not None:
                _records._raise_worker_failure(result.failure)
            if result.state is None:
                raise RuntimeError(
                    f"parallel schema inference chunk {result.index} returned no state"
                )
            merged.merge(result.state)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    logger.info(
        "json_schema_infer_parallel_complete",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
        duration_seconds=round(  # pragma: no mutate - diagnostic timing only
            time.perf_counter() - started,
            3,  # pragma: no mutate
        ),
    )
    return merged


def _assemble_inference_schema(state: _InferenceState) -> dict[str, Any]:
    """Convert accumulated evidence into the public v2 inference payload."""
    levels = state.levels
    scalar_levels = state.scalar_levels

    # A leaf seen ONLY as null across every scanned record still earns a
    # column — typed ``str``, the same default an all-empty scalar array
    # takes. A path ever holding a container earns none: the dotted leaves or
    # the child table already carry it.
    for level, null_paths in state.null_leaves.items():
        cols = levels.setdefault(level, {})
        containers = state.container_paths.get(level, set())
        for object_path in null_paths:
            if object_path not in cols and object_path not in containers:
                cols[object_path] = "str"

    table_entries: list[tuple[_InferenceLevel, dict[str, Any], str]] = []
    all_levels = set(levels) | set(scalar_levels)
    for level in sorted(all_levels, key=lambda s: (array_depth(s), len(s), tuple(s))):
        table_path = make_table_path(level)
        if level in scalar_levels and level not in levels:
            columns: list[dict[str, Any]] = [
                {
                    "name": _SCALAR_VALUE_COLUMN,
                    "path": f"{table_path}.{_SCALAR_VALUE_LEAF}",
                    "type": scalar_levels[level] or "str",
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
            ]
        else:
            column_paths = list(levels.get(level, {}).keys())
            names = _assign_column_names(column_paths)
            columns = [
                {
                    "name": names[object_path],
                    "path": f"{table_path}." + ".".join(object_path),
                    "type": levels[level][object_path],
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
                for object_path in column_paths
            ]
        base_label = "quote_info" if not level else derive_identifier_label(level[-1][0])
        table_entries.append(
            (
                level,
                {
                    "path": table_path,
                    "label": base_label,
                    "displayPath": table_path,
                    "emit": array_depth(level) == 0,  # pragma: no mutate  # root only
                    "row_id_column": None,
                    "columns": columns,
                },
                base_label,
            ),
        )

    base_label_counts: dict[str, int] = {}
    for _level, _table, base_label in table_entries:
        folded = base_label.casefold()
        base_label_counts[folded] = base_label_counts.get(folded, 0) + 1

    assigned_labels: list[tuple[dict[str, Any], str]] = []
    for level, table, base_label in table_entries:
        if base_label_counts[base_label.casefold()] > 1 and level:
            label = "_".join(derive_identifier_label(key) for key, _is_array in level)
        else:
            label = base_label
        assigned_labels.append((table, label))

    used_labels: set[str] = set()
    for table, label in assigned_labels:
        candidate = label
        suffix = 2
        while candidate.casefold() in used_labels:
            candidate = f"{label}_{suffix}"
            suffix += 1
        table["label"] = candidate
        used_labels.add(candidate.casefold())

    return {"tables": [table for _level, table, _base_label in table_entries]}


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
    source_path = Path(data_path)
    unbounded = sample_size is None or sample_size <= 0
    if unbounded and _records._should_shred_in_parallel(source_path):
        initial_stat = source_path.stat()
        ranges = _records._jsonl_byte_ranges(source_path, _records._PARALLEL_CHUNK_BYTES)
        if len(ranges) > 1:
            state = _infer_jsonl_in_parallel(source_path, ranges)
            final_stat = source_path.stat()
            initial_identity = (
                initial_stat.st_dev,
                initial_stat.st_ino,
                initial_stat.st_size,
                initial_stat.st_mtime_ns,
            )
            final_identity = (
                final_stat.st_dev,
                final_stat.st_ino,
                final_stat.st_size,
                final_stat.st_mtime_ns,
            )
            if final_identity != initial_identity:
                raise ApiInputSchemaError(
                    "data file changed while its schema was inferred; retry inference",
                    path=str(source_path),
                )
            return _assemble_inference_schema(state)

    records = _records._iter_records_for_inference(source_path, sample_size=sample_size)
    return _assemble_inference_schema(_infer_records(records))
