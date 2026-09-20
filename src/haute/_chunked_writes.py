"""Bounded chunked writes.

Polars' streaming engine does not bound the memory of one long query over a
large input: its peak grows with the rows read, and a join's lookup side costs
its whole hash table whatever the other side holds. What stays bounded is a
driver loop that issues one small query per chunk. This module writes a node's
output that way, as ordered part files:

- a frame whose rows can be sliced at its source is written one slice at a
  time;
- an edge join is written one chunk of its driving side at a time, against
  only the lookup rows whose keys that chunk holds;
- anything else is written by one native sink, as before.

Whether a frame can be sliced is Polars' own answer: after ``slice`` the
optimised plan must have pushed the slice into its single Parquet/IPC scan or
in-memory frame, which Polars does only through operations where that is
equivalent.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any, Literal, cast

import polars as pl

from haute._edge_join import build_edge_join_kwargs
from haute._hashing import HashingWriter
from haute._logging import get_logger
from haute._polars_utils import (
    atomic_write,
    bounded_hashed_sink,
    current_streaming_chunk_size,
    execution_collect,
)

if TYPE_CHECKING:
    from haute._execution_context import ExecutionContext

logger = get_logger(component="chunked_writes")

PART_PREFIX = "part-"
PART_SUFFIX = ".parquet"
_SUPPORTED_IR_MAJOR = 14
_SLICEABLE_SCAN_TYPES = frozenset({"parquet", "ipc"})
# Row-count-preserving plan nodes a slice passes through unchanged. Anything
# else — notably Distinct, GroupBy and Sort, which absorb a slice into their
# own options and recompute a global result per slice — disqualifies a plan.
_SLICE_TRANSPARENT_NODES = frozenset({"HStack", "Select", "SimpleProjection"})
_SLICE_TRANSPARENT_MAP_FUNCTIONS = frozenset({"rename", "unnest"})
_MATCHES_COLUMN = "__haute_chunk_matches"
_INDEX_COLUMN = "__haute_chunk_index"

WriteStrategy = Literal["chunked_join", "sliced", "input_sliced", "native"]


class RecipeEquivalenceError(ValueError):
    """Raised when a write recipe's native plan does not match the frame handed to write_parts."""


def part_name(index: int) -> str:
    """Name of the ``index``-th part file of a chunked output."""
    if index < 0:
        raise ValueError("part index must be non-negative")
    return f"{PART_PREFIX}{index:05d}{PART_SUFFIX}"


def is_part_name(name: str) -> bool:
    stem = name[len(PART_PREFIX) : -len(PART_SUFFIX)]
    return (
        name.startswith(PART_PREFIX)
        and name.endswith(PART_SUFFIX)
        and len(stem) == 5
        and stem.isdigit()
    )


def part_paths(directory: Path) -> list[Path]:
    """The part files a chunked write left in ``directory``, in write order."""
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and is_part_name(path.name)),
        key=lambda path: path.name,
    )


# ---------------------------------------------------------------------------
# Sliceability
# ---------------------------------------------------------------------------


def sliceable(lf: pl.LazyFrame) -> bool:
    """Whether slicing ``lf`` equals slicing its single file or in-memory input.

    Decided on Polars' optimised plan of ``lf.slice(1, 1)``, by positive proof:
    the plan is one chain of slice-transparent, row-count-preserving nodes
    (``_SLICE_TRANSPARENT_NODES`` and the rename/unnest map functions) down to
    one leaf that received the slice — a Parquet/IPC scan whose ``n_rows``
    carries it, or an in-memory frame. Polars pushes a slice through a
    projection only when its expressions are row-local, and otherwise keeps a
    ``Slice`` node, which is not transparent. An unknown optimiser IR version
    answers False, which costs a staging write but never a wrong row.
    """
    try:
        traverser = lf.slice(1, 1)._ldf.visit()
        if traverser.version()[0] != _SUPPORTED_IR_MAJOR:
            return False
        while True:
            node = traverser.view_current_node()
            kind = type(node).__name__
            inputs = traverser.get_inputs()
            if not inputs:
                if kind == "DataFrameScan":
                    return True
                if kind != "Scan":
                    return False
                scan_type = node.scan_type
                name = scan_type[0] if isinstance(scan_type, tuple) else scan_type
                return str(name) in _SLICEABLE_SCAN_TYPES and node.file_options.n_rows is not None
            if len(inputs) != 1:
                return False
            if kind == "MapFunction":
                function = node.function
                name = function[0] if isinstance(function, tuple) else function
                if name not in _SLICE_TRANSPARENT_MAP_FUNCTIONS:
                    return False
            elif kind not in _SLICE_TRANSPARENT_NODES:
                return False
            traverser.set_node(inputs[0])
    except Exception:  # noqa: BLE001 - an unreadable plan is simply not sliceable
        return False


def reads_only_memory(lf: pl.LazyFrame) -> bool:
    """Whether every input ``lf`` reads is an in-memory frame the caller already holds."""
    try:
        traverser = lf._ldf.visit()
        if traverser.version()[0] != _SUPPORTED_IR_MAJOR:
            return False
        pending = [traverser.get_node()]
        while pending:
            traverser.set_node(pending.pop())
            inputs = traverser.get_inputs()
            if not inputs and type(traverser.view_current_node()).__name__ != "DataFrameScan":
                return False
            pending.extend(inputs)
        return True
    except Exception:  # noqa: BLE001 - an unreadable plan is not proven in memory
        return False


def row_count(lf: pl.LazyFrame, *, execution_context: ExecutionContext | None = None) -> int:
    return int(execution_collect(lf.select(pl.len()), execution_context=execution_context).item())


# ---------------------------------------------------------------------------
# Join recipes
# ---------------------------------------------------------------------------


def _identity(lf: pl.LazyFrame) -> pl.LazyFrame:
    return lf


@dataclass(frozen=True, slots=True)
class JoinRecipe:
    """An edge join as its builder received it, and the row-local step after it."""

    base: pl.LazyFrame
    join: pl.LazyFrame
    config: Mapping[str, Any]
    finish: Callable[[pl.LazyFrame], pl.LazyFrame] = _identity

    def then(self, step: Callable[[pl.LazyFrame], pl.LazyFrame]) -> JoinRecipe:
        """This recipe followed by another row-local step."""
        first = self.finish
        return JoinRecipe(self.base, self.join, self.config, finish=lambda lf: step(first(lf)))

    def native(self) -> pl.LazyFrame:
        """The whole join as one Polars plan, as the builder produces it."""
        return self.finish(self.base.join(self.join, **build_edge_join_kwargs(dict(self.config))))


@dataclass(frozen=True, slots=True)
class WriteRecipe:
    """A chunk-local single-input node's recipe, and the row-local step after it."""

    input: pl.LazyFrame
    fn: Callable[[pl.LazyFrame], pl.LazyFrame] | None = None
    finish: Callable[[pl.LazyFrame], pl.LazyFrame] = _identity
    reason: str | None = None
    blocking_operator: str | None = None

    def __post_init__(self) -> None:
        if self.fn is None and self.reason is None:
            raise ValueError("A rejected write recipe must carry a reason")

    def then(self, step: Callable[[pl.LazyFrame], pl.LazyFrame]) -> WriteRecipe:
        """This recipe followed by another row-local step."""
        first = self.finish
        return WriteRecipe(
            self.input,
            fn=self.fn,
            finish=lambda lf: step(first(lf)),
            reason=self.reason,
            blocking_operator=self.blocking_operator,
        )

    def apply(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """Apply this recipe's computation to an input slice or full frame."""
        if self.fn is None:
            raise ValueError("Cannot apply a rejected write recipe")
        return self.finish(self.fn(lf))

    def native(self) -> pl.LazyFrame:
        """The whole computation as one Polars plan, as the builder produces it."""
        if self.fn is None:
            raise ValueError("Cannot construct native plan for a rejected write recipe")
        return self.apply(self.input)


def _check_recipe_equivalence(recipe: WriteRecipe, frame: pl.LazyFrame) -> None:
    # Limit: two in-memory frames of equal schema are indistinguishable this way.
    # It catches a recipe bound to a different plan, not one bound to an identical
    # plan over different data.
    recipe_native = recipe.native()
    if recipe_native.collect_schema() != frame.collect_schema():
        raise RecipeEquivalenceError(
            "Write recipe schema does not match the frame handed to write_parts"
        )
    if recipe_native.explain(optimized=False) != frame.explain(optimized=False):
        raise RecipeEquivalenceError(
            "Write recipe plan does not match the frame handed to write_parts"
        )


@dataclass(frozen=True, slots=True)
class _JoinShape:
    how: str
    kwargs: dict[str, Any]
    base_keys: list[str]
    join_keys: list[str]
    validate: str | None
    base_drives: bool


def _keys(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _join_shape(recipe: JoinRecipe) -> tuple[_JoinShape | None, str | None]:
    """The chunk plan for ``recipe``, or why it must be written natively."""
    kwargs = build_edge_join_kwargs(dict(recipe.config))
    how = kwargs["how"]
    validate = kwargs.pop("validate", None)
    if how == "cross":
        # Polars ignores a cross join's validate; so does its chunked write.
        validate = None
    order = kwargs.get("maintain_order")
    order = None if order in (None, "none") else order
    if "on" in kwargs:
        base_keys = join_keys = _keys(kwargs["on"])
    else:
        base_keys = _keys(kwargs.get("left_on"))
        join_keys = _keys(kwargs.get("right_on"))
    base_drives = how != "right"
    if how == "full" and order is not None:
        return None, "full_join_ordered"
    if order is not None:
        driving_order = {"left", "left_right"} if base_drives else {"right", "right_left"}
        if order not in driving_order:
            return None, "ordered_by_lookup_side"
    return (
        _JoinShape(
            how=how,
            kwargs=kwargs,
            base_keys=base_keys,
            join_keys=join_keys,
            validate=validate,
            base_drives=base_drives,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


_EMPTY_DIGESTS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ChunkedWrite:
    """What one chunked write did."""

    strategy: WriteStrategy
    parts: tuple[str, ...]
    chunks: int
    staged_inputs: int
    chunk_rows: int | None = None
    native_reason: str | None = None
    blocking_operator: str | None = None
    input_slices: int | None = None
    digests: Mapping[str, str] = field(default_factory=lambda: _EMPTY_DIGESTS)


class _Parts:
    """Sequentially numbered part sinks into one directory, with a fixed schema."""

    def __init__(
        self,
        directory: Path,
        *,
        schema: pl.Schema,
        fast_checkpoint: bool,
        execution_context: ExecutionContext | None,
        node_id: str | None,
    ) -> None:
        self.directory = directory
        self.schema = schema
        self.fast_checkpoint = fast_checkpoint
        self.execution_context = execution_context
        self.node_id = node_id
        self.names: list[str] = []
        self.digests: dict[str, str] = {}

    def sink(self, lf: pl.LazyFrame, *, conform: bool = True) -> None:
        if self.execution_context is not None:
            self.execution_context.checkpoint(label="chunked_write", node_id=self.node_id)
        if conform:
            # Every part carries exactly the whole output's schema, so the
            # parts scan as one frame even where a chunk infers a narrower type.
            lf = lf.select(
                [pl.col(name).cast(dtype, strict=True) for name, dtype in self.schema.items()]
            )
        name = part_name(len(self.names))
        digest = bounded_hashed_sink(
            lf, self.directory / name, fast_checkpoint=self.fast_checkpoint
        )
        self.names.append(name)
        self.digests[name] = digest
        if self.execution_context is not None:
            self.execution_context.record_chunk()

    def collect_and_write(self, lf: pl.LazyFrame) -> None:
        """Compute a bounded part in memory and write it.

        Used for ordered joins: the in-memory engine is the reference for a
        join's documented ``maintain_order``, and a part is bounded by
        construction. Unordered parts are sunk natively, which is faster.
        """
        frame = execution_collect(
            lf.select(
                [pl.col(name).cast(dtype, strict=True) for name, dtype in self.schema.items()]
            ),
            execution_context=self.execution_context,
            engine="in-memory",
        )
        if self.execution_context is not None:
            self.execution_context.checkpoint(label="chunked_write", node_id=self.node_id)
        name = part_name(len(self.names))
        path = self.directory / name
        with atomic_write(path) as tmp:
            with HashingWriter(open(tmp, "wb")) as writer:
                frame.write_parquet(
                    cast("IO[bytes]", writer),
                    compression="lz4" if self.fast_checkpoint else "zstd",
                )
            digest = writer.hexdigest()
        self.names.append(name)
        self.digests[name] = digest
        if self.execution_context is not None:
            self.execution_context.record_bytes_written(path.stat().st_size)
            self.execution_context.record_chunk()

    def write_join_part(self, lf: pl.LazyFrame, *, ordered: bool) -> None:
        if ordered:
            self.collect_and_write(lf)
        else:
            self.sink(lf)

    def ensure_one(self) -> None:
        if not self.names:
            self.collect_and_write(pl.DataFrame(schema=self.schema).lazy())


def _chunk_rows(chunk_rows: int | None) -> int:
    # Unset, a write follows the chunk size its request runs under.
    rows = current_streaming_chunk_size() if chunk_rows is None else chunk_rows
    if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
        raise ValueError("chunk_rows must be a positive integer")
    return rows


def write_parts(
    directory: Path,
    frame: pl.LazyFrame,
    *,
    join: JoinRecipe | None = None,
    recipe: WriteRecipe | None = None,
    chunk_rows: int | None = None,
    fast_checkpoint: bool = True,
    execution_context: ExecutionContext | None = None,
    node_id: str | None = None,
) -> ChunkedWrite:
    """Write ``frame`` into ``directory`` as ordered part files.

    ``join``, when given, is the recipe ``frame`` was built from; the join is
    then written a driving chunk at a time where its semantics allow.
    ``recipe``, when given, is the write recipe ``frame`` was built from;
    the write is then performed a slice of its input at a time when chunk-local.
    The directory must exist and hold no parts yet. Inputs the write stages are
    kept in a private subdirectory and removed before returning.
    """
    rows = _chunk_rows(chunk_rows)
    if part_paths(directory):
        raise ValueError("chunked write target already holds parts")
    # A join's own schema resolution runs first: Polars rejects some
    # validate/how combinations there, and the write must raise exactly that.
    schema = (join.native() if join is not None else frame).collect_schema()
    parts = _Parts(
        directory,
        schema=schema,
        fast_checkpoint=fast_checkpoint,
        execution_context=execution_context,
        node_id=node_id,
    )
    native_reason: str | None = None
    if join is not None:
        shape, native_reason = _join_shape(join)
        if shape is not None:
            staging = directory / ".chunk-inputs"
            try:
                staged = _write_chunked_join(
                    parts,
                    join,
                    shape,
                    rows=rows,
                    staging=staging,
                    execution_context=execution_context,
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            parts.ensure_one()
            return _report(
                parts,
                "chunked_join",
                chunk_rows=None if shape.how == "cross" else rows,
                staged_inputs=staged,
                node_id=node_id,
            )
        parts.sink(frame, conform=False)
        return _report(
            parts,
            "native",
            chunk_rows=None,
            native_reason=native_reason or "join_not_chunkable",
            node_id=node_id,
        )

    if sliceable(frame):
        total = row_count(frame, execution_context=execution_context)
        for offset in range(0, total, rows):
            parts.sink(frame.slice(offset, rows))
        parts.ensure_one()
        return _report(parts, "sliced", chunk_rows=rows, node_id=node_id)

    if recipe is not None and recipe.fn is not None:
        _check_recipe_equivalence(recipe, frame)
        if not sliceable(recipe.input):
            parts.sink(frame, conform=False)
            return _report(
                parts,
                "native",
                chunk_rows=None,
                native_reason="input_not_sliceable",
                node_id=node_id,
            )
        total = row_count(recipe.input, execution_context=execution_context)
        slice_count = 0
        for offset in range(0, total, rows):
            slice_count += 1
            parts.sink(recipe.apply(recipe.input.slice(offset, rows)))
        parts.ensure_one()
        # input_slices always equals the part count on this path.
        return _report(
            parts,
            "input_sliced",
            chunk_rows=rows,
            input_slices=slice_count,
            node_id=node_id,
        )

    if recipe is not None:
        parts.sink(frame, conform=False)
        return _report(
            parts,
            "native",
            chunk_rows=None,
            native_reason=recipe.reason,
            blocking_operator=recipe.blocking_operator,
            node_id=node_id,
        )

    parts.sink(frame, conform=False)
    return _report(parts, "native", chunk_rows=None, native_reason="not_sliceable", node_id=node_id)


def _report(
    parts: _Parts,
    strategy: WriteStrategy,
    *,
    chunk_rows: int | None = None,
    staged_inputs: int = 0,
    native_reason: str | None = None,
    blocking_operator: str | None = None,
    input_slices: int | None = None,
    node_id: str | None,
) -> ChunkedWrite:
    written = ChunkedWrite(
        strategy=strategy,
        parts=tuple(parts.names),
        chunks=len(parts.names),
        staged_inputs=staged_inputs,
        chunk_rows=chunk_rows,
        native_reason=native_reason,
        blocking_operator=blocking_operator,
        input_slices=input_slices,
        digests=MappingProxyType(dict(parts.digests)),
    )
    logger.info(
        "chunked_write",
        node_id=node_id,
        strategy=strategy,
        parts=written.chunks,
        chunk_rows=chunk_rows,
        staged_inputs=staged_inputs,
        native_reason=native_reason,
        blocking_operator=blocking_operator,
        input_slices=input_slices,
    )
    return written


def scan_parts(paths: Sequence[Path]) -> pl.LazyFrame:
    """One frame over part files, in their order."""
    if not paths:
        raise ValueError("a chunked output holds at least one part")
    return pl.scan_parquet([str(path) for path in paths])


def _staged(
    lf: pl.LazyFrame,
    staging: Path,
    *,
    execution_context: ExecutionContext | None,
    counter: list[int],
) -> pl.LazyFrame:
    """``lf`` if it can be sliced, else ``lf`` written once and scanned back."""
    if sliceable(lf):
        return lf
    target = staging / f"input-{counter[0]}"
    counter[0] += 1
    target.mkdir(parents=True)
    write_parts(target, lf, execution_context=execution_context, fast_checkpoint=True)
    return scan_parts(part_paths(target))


# ---------------------------------------------------------------------------
# Chunked joins
# ---------------------------------------------------------------------------


def _semi(
    lookup: pl.LazyFrame,
    keys_frame: pl.DataFrame,
    *,
    lookup_keys: list[str],
    frame_keys: list[str],
    keep_order: bool,
) -> pl.LazyFrame:
    return lookup.join(
        keys_frame.lazy().select(frame_keys),
        left_on=lookup_keys,
        right_on=frame_keys,
        how="semi",
        maintain_order="left" if keep_order else "none",
    )


def _write_chunked_join(
    parts: _Parts,
    recipe: JoinRecipe,
    shape: _JoinShape,
    *,
    rows: int,
    staging: Path,
    execution_context: ExecutionContext | None,
) -> int:
    counter = [0]
    base = _staged(recipe.base, staging, execution_context=execution_context, counter=counter)
    join = _staged(recipe.join, staging, execution_context=execution_context, counter=counter)
    if shape.validate is not None:
        _validate_uniqueness(
            base,
            join,
            shape,
            rows=rows,
            execution_context=execution_context,
        )
    kwargs = shape.kwargs
    finish = recipe.finish
    # An ordered join also orders one driving row's matches by the lookup's
    # own order, so the lookup's rows must reach each chunk in that order.
    keep_lookup_order = kwargs.get("maintain_order") not in (None, "none")

    if shape.how == "cross":
        lookup_df = execution_collect(join, execution_context=execution_context)
        per_chunk = max(1, rows // max(1, lookup_df.height))
        total = row_count(base, execution_context=execution_context)
        for offset in range(0, total, per_chunk):
            chunk = execution_collect(
                base.slice(offset, per_chunk), execution_context=execution_context
            )
            parts.write_join_part(
                finish(chunk.lazy().join(lookup_df.lazy(), **kwargs)), ordered=keep_lookup_order
            )
        return counter[0]

    driving, lookup = (base, join) if shape.base_drives else (join, base)
    driving_keys, lookup_keys = (
        (shape.base_keys, shape.join_keys)
        if shape.base_drives
        else (shape.join_keys, shape.base_keys)
    )
    chunk_join = _ChunkJoin(
        parts,
        shape,
        lookup=lookup,
        driving_keys=driving_keys,
        lookup_keys=lookup_keys,
        rows=rows,
        keep_lookup_order=keep_lookup_order,
        finish=finish,
        execution_context=execution_context,
    )
    total = row_count(driving, execution_context=execution_context)
    for offset in range(0, total, rows):
        chunk_join.write(
            execution_collect(driving.slice(offset, rows), execution_context=execution_context)
        )

    if shape.how == "full":
        # Lookup rows no driving row matched, once each: the full join of an
        # empty base with them has exactly the full join's shape for them.
        empty_base = pl.DataFrame(schema=base.collect_schema()).lazy()
        lookup_total = row_count(join, execution_context=execution_context)
        for offset in range(0, lookup_total, rows):
            chunk = execution_collect(join.slice(offset, rows), execution_context=execution_context)
            present = execution_collect(
                _semi(
                    base.select(shape.base_keys),
                    chunk,
                    lookup_keys=shape.base_keys,
                    frame_keys=shape.join_keys,
                    keep_order=False,
                ).unique(),
                execution_context=execution_context,
            )
            unmatched = chunk.lazy().join(
                present.lazy(),
                left_on=shape.join_keys,
                right_on=shape.base_keys,
                how="anti",
            )
            parts.sink(finish(empty_base.join(unmatched, **kwargs)))
    return counter[0]


class _ChunkJoin:
    """Joins one driving chunk against only the lookup rows its keys match.

    No query holds more than ``rows`` driving rows or ``rows`` matched lookup
    rows, and a part holds at most ``rows`` output rows unless one driving
    row alone matches more (then that row's output is written ``rows`` lookup
    matches at a time).
    """

    def __init__(
        self,
        parts: _Parts,
        shape: _JoinShape,
        *,
        lookup: pl.LazyFrame,
        driving_keys: list[str],
        lookup_keys: list[str],
        rows: int,
        keep_lookup_order: bool,
        finish: Callable[[pl.LazyFrame], pl.LazyFrame],
        execution_context: ExecutionContext | None,
    ) -> None:
        self.parts = parts
        self.shape = shape
        self.lookup = lookup
        self.driving_keys = driving_keys
        self.lookup_keys = lookup_keys
        self.rows = rows
        self.keep_lookup_order = keep_lookup_order
        self.finish = finish
        self.execution_context = execution_context
        self._index: tuple[str, pl.LazyFrame] | None = None

    def _collect(self, lf: pl.LazyFrame) -> pl.DataFrame:
        # These queries scan the whole lookup side for a chunk's keys: the
        # streaming engine reads it through without holding it.
        return execution_collect(lf, execution_context=self.execution_context, engine="streaming")

    def _matches(self, frame: pl.DataFrame, lookup: pl.LazyFrame | None = None) -> pl.LazyFrame:
        return _semi(
            self.lookup if lookup is None else lookup,
            frame,
            lookup_keys=self.lookup_keys,
            frame_keys=self.driving_keys,
            keep_order=self.keep_lookup_order,
        )

    def _sink(self, frame: pl.DataFrame, matched: pl.LazyFrame) -> None:
        if self.shape.base_drives:
            joined = frame.lazy().join(matched, **self.shape.kwargs)
        else:
            joined = matched.join(frame.lazy(), **self.shape.kwargs)
        self.parts.write_join_part(self.finish(joined), ordered=self.keep_lookup_order)

    def write(self, chunk: pl.DataFrame) -> None:
        if self.shape.how in {"semi", "anti"}:
            # Only the lookup's keys matter, so it never exceeds the chunk's keys.
            keys = self._collect(
                self._matches(chunk, self.lookup.select(self.lookup_keys)).unique()
            )
            self._sink(chunk, keys.lazy())
            return
        probe = self._collect(self._matches(chunk).head(self.rows + 1))
        if probe.height <= self.rows:
            if probe.select(pl.struct(self.lookup_keys).is_unique().all()).item():
                # Each driving row matches at most one lookup row: the part
                # holds at most the chunk's rows.
                self._sink(chunk, probe.lazy())
                return
            counts = probe.group_by(self.lookup_keys).len(name=_MATCHES_COLUMN)
            if self._expected_rows(chunk, counts).sum() <= self.rows:
                self._sink(chunk, probe.lazy())
                return
            self._write_split(chunk, counts, matched=probe)
            return
        counts = self._collect(
            self._matches(chunk, self.lookup.select(self.lookup_keys))
            .group_by(self.lookup_keys)
            .len(name=_MATCHES_COLUMN)
        )
        self._write_split(chunk, counts, matched=None)

    def _expected_rows(self, frame: pl.DataFrame, counts: pl.DataFrame) -> pl.Series:
        """Output rows each driving row produces, in the frame's order."""
        matches = (
            frame.select(self.driving_keys)
            .join(
                counts,
                left_on=self.driving_keys,
                right_on=self.lookup_keys,
                how="left",
                maintain_order="left",
            )
            .get_column(_MATCHES_COLUMN)
            .fill_null(0)
        )
        if self.shape.how == "inner":
            return matches
        # left, right and the full join's first pass keep an unmatched row once.
        return matches.clip(lower_bound=1)

    def _write_split(
        self,
        chunk: pl.DataFrame,
        counts: pl.DataFrame,
        *,
        matched: pl.DataFrame | None,
    ) -> None:
        expected = self._expected_rows(chunk, counts).to_list()
        start = 0
        while start < chunk.height:
            if expected[start] > self.rows:
                self._write_heavy_row(chunk.slice(start, 1), expected[start])
                start += 1
                continue
            end = start
            total = 0
            while end < chunk.height and expected[end] <= self.rows:
                if total + expected[end] > self.rows:
                    break
                total += expected[end]
                end += 1
            group = chunk.slice(start, end - start)
            group_matches = (
                self._matches(group, matched.lazy())
                if matched is not None
                else self._matches(group)
            )
            self._sink(group, self._collect(group_matches).lazy())
            start = end

    @property
    def _index_column(self) -> str | None:
        return self._index[0] if self._index is not None else None

    def _resolve_index(self) -> tuple[str, pl.LazyFrame]:
        if self._index is None:
            names = set(self.lookup.collect_schema().names())
            index = _INDEX_COLUMN
            suffix = 0
            while index in names:
                index = f"{_INDEX_COLUMN}_{suffix}"
                suffix += 1
            self._index = (index, self.lookup.with_row_index(index))
        return self._index

    def _heavy_window(self, matches: pl.LazyFrame, cursor: int | None) -> pl.LazyFrame:
        index, _ = self._resolve_index()
        pending = matches if cursor is None else matches.filter(pl.col(index) > cursor)
        return pending.bottom_k(self.rows, by=index)

    def _write_heavy_row(self, row: pl.DataFrame, expected: int) -> None:
        """One driving row whose matches exceed a part: its matches a part at a time."""
        index, indexed_lookup = self._resolve_index()
        matches = self._matches(row, indexed_lookup)
        cursor: int | None = None
        written = 0
        while True:
            window = self._collect(self._heavy_window(matches, cursor))
            if window.height == 0:
                break
            window = window.sort(index)
            cursor = int(window.get_column(index)[-1])
            self._sink(row, window.drop(index).lazy())
            written += window.height
            if window.height < self.rows:
                break
        if written != expected:
            raise RuntimeError(f"heavy row wrote {written} rows, expected {expected}")


def _unique_key_violation(
    side: pl.LazyFrame,
    keys: list[str],
    *,
    rows: int,
    execution_context: ExecutionContext | None,
) -> bool:
    """Whether any non-null key occurs twice on ``side``, a bounded pass at a time."""
    key_rows = side.select(keys).drop_nulls()
    total = row_count(key_rows, execution_context=execution_context)
    partitions = max(1, math.ceil(total / rows))
    bucket = pl.struct(keys).hash(seed=0) % partitions
    for index in range(partitions):
        subset = key_rows if partitions == 1 else key_rows.filter(bucket == index)
        duplicates = execution_collect(
            subset.group_by(keys)
            .len(name=_MATCHES_COLUMN)
            .filter(pl.col(_MATCHES_COLUMN) > 1)
            .head(1),
            execution_context=execution_context,
        )
        if duplicates.height:
            return True
        if execution_context is not None:
            execution_context.checkpoint(label="chunked_join_validate")
    return False


def _validate_uniqueness(
    base: pl.LazyFrame,
    join: pl.LazyFrame,
    shape: _JoinShape,
    *,
    rows: int,
    execution_context: ExecutionContext | None,
) -> None:
    validate = shape.validate
    if validate in (None, "m:m"):
        return
    base_unique = validate in {"1:m", "1:1"}
    join_unique = validate in {"m:1", "1:1"}
    if (
        base_unique
        and _unique_key_violation(
            base, shape.base_keys, rows=rows, execution_context=execution_context
        )
    ) or (
        join_unique
        and _unique_key_violation(
            join, shape.join_keys, rows=rows, execution_context=execution_context
        )
    ):
        raise pl.exceptions.ComputeError(f"join keys did not fulfill {validate} validation")


def iter_slices(
    lf: pl.LazyFrame,
    *,
    chunk_rows: int,
    execution_context: ExecutionContext | None = None,
) -> Iterator[pl.DataFrame]:
    """Collect a sliceable ``lf`` one slice at a time, in order."""
    total = row_count(lf, execution_context=execution_context)
    for offset in range(0, total, chunk_rows):
        yield execution_collect(lf.slice(offset, chunk_rows), execution_context=execution_context)
