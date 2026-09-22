"""Bounded chunked writes.

Polars' streaming engine does not bound the memory of one long query over a
large input: its peak grows with the rows read, and a join's lookup side costs
its whole hash table whatever the other side holds. The driver loop bounds
input slices and output parts only for strategies it can prove; complex
operators still execute under their execution-context runtime limits. This
module writes a node's output as ordered part files:

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
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any, Literal, cast

import polars as pl
import pyarrow.parquet as pq

from haute._edge_join import build_edge_join_kwargs
from haute._execution_context import ExecutionContext, current_execution_context
from haute._file_ops import ensure_disk_headroom
from haute._hashing import HashingWriter
from haute._logging import get_logger
from haute._polars_utils import (
    atomic_write,
    bounded_hashed_sink,
    bounded_sink,
    current_streaming_chunk_size,
    execution_collect,
)
from haute._ram_estimate import decoded_frame_row_width_bytes

logger = get_logger(component="chunked_writes")

PART_PREFIX = "part-"
PART_SUFFIX = ".parquet"
SINGLE_FILE_ROW_GROUP_SIZE = 100_000
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
    """Raised when a write recipe's native plan does not match the frame handed to the writer."""


def part_name(index: int) -> str:
    """Name of the ``index``-th part file of a chunked output."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("part index must be a non-negative integer")
    return f"{PART_PREFIX}{index:05d}{PART_SUFFIX}"


def is_part_name(name: str) -> bool:
    if (
        not isinstance(name, str)
        or not name.startswith(PART_PREFIX)
        or not name.endswith(PART_SUFFIX)
    ):
        return False
    stem = name[len(PART_PREFIX) : -len(PART_SUFFIX)]
    return len(stem) >= 5 and stem.isascii() and stem.isdecimal() and stem == f"{int(stem):05d}"


def part_paths(directory: Path) -> list[Path]:
    """The part files a chunked write left in ``directory``, in write order."""
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and is_part_name(path.name)),
        key=lambda path: int(path.name[len(PART_PREFIX) : -len(PART_SUFFIX)]),
    )


def _resolve_generated_name(preferred: str, names: set[str]) -> str:
    """A deterministic generated name absent from ``names``."""
    resolved = preferred
    suffix = 0
    while resolved in names:
        resolved = f"{preferred}_{suffix}"
        suffix += 1
    return resolved


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


def check_recipe_equivalence(recipe: WriteRecipe, frame: pl.LazyFrame) -> None:
    # Limit: two in-memory frames of equal schema are indistinguishable this way.
    # It catches a recipe bound to a different plan, not one bound to an identical
    # plan over different data.
    recipe_native = recipe.native()
    if recipe_native.collect_schema() != frame.collect_schema():
        raise RecipeEquivalenceError(
            "Write recipe schema does not match the frame handed to the writer"
        )
    if recipe_native.explain(optimized=False) != frame.explain(optimized=False):
        raise RecipeEquivalenceError(
            "Write recipe plan does not match the frame handed to the writer"
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
    base_drives = how != "right" and not (how == "cross" and order in {"right", "right_left"})
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
    # On the parts path, the part count written; on the single-file path,
    # the slices applied to the recipe.
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
        ensure_disk_headroom(
            self.directory, (self.directory / self.names[-1]).stat().st_size if self.names else 0
        )
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
        ensure_disk_headroom(self.directory, int(frame.estimated_size()))
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


def _budgeted_rows(
    requested_rows: int,
    inputs: Sequence[pl.LazyFrame],
    *,
    execution_context: ExecutionContext | None,
) -> tuple[int, int | None, tuple[float, ...] | None]:
    """Return the bounded row count and working allowance for proven-sliceable inputs."""
    if not isinstance(execution_context, ExecutionContext):
        return requested_rows, None, None
    remaining = execution_context.remaining_memory_bytes()
    if remaining is None:
        return requested_rows, None, None
    sample_rows = min(512, requested_rows)
    widths = tuple(
        decoded_frame_row_width_bytes(
            execution_collect(frame.slice(0, sample_rows), execution_context=execution_context)
        )
        for frame in inputs
    )
    working_allowance = remaining // 2
    width = sum(widths)
    if width <= 0:
        return requested_rows, working_allowance, widths
    rows = min(requested_rows, max(1, math.floor(working_allowance / (4 * width))))
    return rows, working_allowance, widths


def _budgeted_recipe_rows(
    requested_rows: int,
    recipe: WriteRecipe,
    *,
    execution_context: ExecutionContext | None,
) -> int:
    """Size a row-local recipe from one bounded input and output sample."""
    if not isinstance(execution_context, ExecutionContext):
        return requested_rows
    remaining = execution_context.remaining_memory_bytes()
    if remaining is None:
        return requested_rows
    sample = execution_collect(
        recipe.input.slice(0, min(512, requested_rows)), execution_context=execution_context
    )
    output = execution_collect(recipe.apply(sample.lazy()), execution_context=execution_context)
    width = decoded_frame_row_width_bytes(sample) + decoded_frame_row_width_bytes(output)
    if width <= 0:
        return requested_rows
    return min(requested_rows, max(1, math.floor((remaining // 2) / (4 * width))))


def _native_join_output_upper_bound(
    shape: _JoinShape, base_rows: int, join_rows: int
) -> int | None:
    """The largest output admitted by the join's validation contract."""
    if shape.how in {"semi", "anti"}:
        return base_rows
    validate = shape.validate
    if validate not in {"1:1", "m:1", "1:m"}:
        return None
    if validate == "1:1":
        if shape.how == "inner":
            return min(base_rows, join_rows)
        if shape.how == "left":
            return base_rows
        if shape.how == "right":
            return join_rows
        if shape.how == "full":
            return base_rows + join_rows
    if validate == "m:1":
        if shape.how in {"inner", "left"}:
            return base_rows
        if shape.how in {"right", "full"}:
            return base_rows + join_rows
    if validate == "1:m":
        if shape.how in {"inner", "right"}:
            return join_rows
        if shape.how in {"left", "full"}:
            return base_rows + join_rows
    return None


def _join_within_memory_budget(
    recipe: JoinRecipe,
    shape: _JoinShape,
    *,
    requested_rows: int,
    execution_context: ExecutionContext | None,
) -> bool:
    if (
        not isinstance(execution_context, ExecutionContext)
        or recipe.finish is not _identity
        or shape.how == "cross"
        or shape.kwargs.get("maintain_order") not in (None, "none")
        or not sliceable(recipe.base)
        or not sliceable(recipe.join)
    ):
        return False
    output_rows = _native_join_output_upper_bound(shape, 0, 0)
    if output_rows is None:
        return False
    remaining = execution_context.remaining_memory_bytes()
    if remaining is None:
        return False
    base_rows = row_count(recipe.base, execution_context=execution_context)
    join_rows = row_count(recipe.join, execution_context=execution_context)
    output_rows = _native_join_output_upper_bound(shape, base_rows, join_rows)
    assert output_rows is not None
    sample_rows = min(512, requested_rows)
    widths = tuple(
        decoded_frame_row_width_bytes(
            execution_collect(
                input_frame.slice(0, sample_rows), execution_context=execution_context
            )
        )
        for input_frame in (recipe.base, recipe.join)
    )
    working_allowance = remaining // 2
    base_width, join_width = widths
    required = (
        int(
            3
            * (
                base_rows * base_width
                + join_rows * join_width
                + output_rows * (base_width + join_width)
            )
        )
        + 64 * 1024 * 1024
    )
    return required <= working_allowance


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
    execution_context = execution_context or current_execution_context()
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
            if _join_within_memory_budget(
                join,
                shape,
                requested_rows=rows,
                execution_context=execution_context,
            ):
                parts.sink(frame, conform=False)
                return _report(
                    "native",
                    parts=parts.names,
                    digests=parts.digests,
                    chunk_rows=None,
                    native_reason="join_within_memory_budget",
                    node_id=node_id,
                )
            if sliceable(join.base) and sliceable(join.join):
                rows, _, _ = _budgeted_rows(
                    rows,
                    (join.base, join.join),
                    execution_context=execution_context,
                )
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
                "chunked_join",
                parts=parts.names,
                digests=parts.digests,
                chunk_rows=rows,
                staged_inputs=staged,
                node_id=node_id,
            )
        parts.sink(frame, conform=False)
        return _report(
            "native",
            parts=parts.names,
            digests=parts.digests,
            chunk_rows=None,
            native_reason=native_reason or "join_not_chunkable",
            node_id=node_id,
        )

    if sliceable(frame):
        rows, _, _ = _budgeted_rows(rows, (frame,), execution_context=execution_context)
        total = row_count(frame, execution_context=execution_context)
        for offset in range(0, total, rows):
            parts.sink(frame.slice(offset, rows))
        parts.ensure_one()
        return _report(
            "sliced",
            parts=parts.names,
            digests=parts.digests,
            chunk_rows=rows,
            node_id=node_id,
        )

    if recipe is not None and recipe.fn is not None:
        check_recipe_equivalence(recipe, frame)
        if not sliceable(recipe.input):
            parts.sink(frame, conform=False)
            return _report(
                "native",
                parts=parts.names,
                digests=parts.digests,
                chunk_rows=None,
                native_reason="input_not_sliceable",
                node_id=node_id,
            )
        rows = _budgeted_recipe_rows(rows, recipe, execution_context=execution_context)
        total = row_count(recipe.input, execution_context=execution_context)
        for offset in range(0, total, rows):
            parts.sink(recipe.apply(recipe.input.slice(offset, rows)))
        parts.ensure_one()
        # input_slices always equals the part count on this path.
        return _report(
            "input_sliced",
            parts=parts.names,
            digests=parts.digests,
            chunk_rows=rows,
            input_slices=len(parts.names),
            node_id=node_id,
        )

    if recipe is not None:
        parts.sink(frame, conform=False)
        return _report(
            "native",
            parts=parts.names,
            digests=parts.digests,
            chunk_rows=None,
            native_reason=recipe.reason,
            blocking_operator=recipe.blocking_operator,
            node_id=node_id,
        )

    parts.sink(frame, conform=False)
    return _report(
        "native",
        parts=parts.names,
        digests=parts.digests,
        chunk_rows=None,
        native_reason="not_sliceable",
        node_id=node_id,
    )


def _report(
    strategy: WriteStrategy,
    *,
    parts: Sequence[str] = (),
    digests: Mapping[str, str] = _EMPTY_DIGESTS,
    chunk_rows: int | None = None,
    staged_inputs: int = 0,
    native_reason: str | None = None,
    blocking_operator: str | None = None,
    input_slices: int | None = None,
    node_id: str | None = None,
) -> ChunkedWrite:
    parts_tuple = tuple(parts)
    written = ChunkedWrite(
        strategy=strategy,
        parts=parts_tuple,
        chunks=len(parts_tuple),
        staged_inputs=staged_inputs,
        chunk_rows=chunk_rows,
        native_reason=native_reason,
        blocking_operator=blocking_operator,
        input_slices=input_slices,
        digests=MappingProxyType(dict(digests)) if digests else _EMPTY_DIGESTS,
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


@contextmanager
def _destination(destination: Path, *, atomic: bool) -> Iterator[Path]:
    """Where the slice loop writes, and what a failure leaves behind.

    ``pq.ParquetWriter`` closes its footer on the way out of an exception, so a
    write that died half way leaves a shorter file that reads back perfectly.
    Writing through a temporary and renaming is what makes a failure leave
    nothing — unless the caller is already writing to a staging path it will
    rename or discard itself, where a second temporary would leave a hidden
    sibling in a directory the caller governs and does not sweep.
    """
    if not atomic:
        destination.parent.mkdir(parents=True, exist_ok=True)
        yield destination
        return
    with atomic_write(destination) as tmp:
        yield tmp


def _sink_slices(
    destination: Path,
    *,
    source: pl.LazyFrame,
    schema: pl.Schema,
    rows: int,
    apply: Callable[[pl.LazyFrame], pl.LazyFrame],
    execution_context: ExecutionContext | None,
    node_id: str | None,
    atomic: bool = True,
) -> int:
    """Drive ``source`` a slice at a time through one Parquet writer, and count the slices.

    One writer means one file, so every slice is cast to ``schema`` before it is
    handed over — the same conforming ``_Parts.sink`` does, for the same reason:
    a slice may narrow a dtype the whole frame widens. An empty slice is skipped
    rather than written, so a selective filter leaves no run of zero-row row
    groups, and the returned count is therefore the slices APPLIED, not the
    tables written. Row groups are bounded independently of the slice size, or a
    bounded write would only move the peak from here to every later reader.
    """
    total = row_count(source, execution_context=execution_context)
    arrow_schema = pl.DataFrame(schema=schema).to_arrow().schema
    with _destination(destination, atomic=atomic) as target:
        with pq.ParquetWriter(target, arrow_schema, compression="zstd") as writer:
            for offset in range(0, total, rows):
                if execution_context is not None:
                    execution_context.checkpoint(label="chunked_write", node_id=node_id)
                slice_df = execution_collect(
                    apply(source.slice(offset, rows)), execution_context=execution_context
                )
                if slice_df.height == 0:
                    continue
                conformed = slice_df.select(
                    [pl.col(name).cast(dtype, strict=True) for name, dtype in schema.items()]
                )
                table = conformed.to_arrow()
                ensure_disk_headroom(target.parent, table.nbytes)
                writer.write_table(table, row_group_size=SINGLE_FILE_ROW_GROUP_SIZE)
                if execution_context is not None:
                    execution_context.record_chunk()
    # An empty input drives no slice, but it still wrote one file's worth of output.
    return len(range(0, total, rows)) if total > 0 else 1


def write_file(
    destination: str | Path,
    frame: pl.LazyFrame,
    *,
    recipe: WriteRecipe | None = None,
    chunk_rows: int | None = None,
    execution_context: ExecutionContext | None = None,
    node_id: str | None = None,
    atomic: bool = True,
) -> ChunkedWrite:
    """Write ``frame`` into ``destination`` as one parquet file.

    ``recipe``, when given, is the write recipe ``frame`` was built from;
    the write is then performed a slice of its input at a time when chunk-local.
    When the recipe is absent, rejected or unsliceable, ``frame`` is sunk
    natively. The file is written directly to ``destination`` without part
    files or hashing. Atomicity is owned by this function for every strategy:
    on failure nothing is left at ``destination``, and a successful write appears
    atomically via temporary file rename. Pass ``atomic=False`` only when the
    caller is already writing to a staging path it will rename or discard
    itself — a second temporary would leave a hidden sibling in a directory the
    caller governs and does not sweep.
    """
    dest = Path(destination)
    rows = _chunk_rows(chunk_rows)
    execution_context = execution_context or current_execution_context()
    if sliceable(frame):
        rows, _, _ = _budgeted_rows(rows, (frame,), execution_context=execution_context)
        _sink_slices(
            dest,
            source=frame,
            schema=frame.collect_schema(),
            rows=rows,
            apply=_identity,
            execution_context=execution_context,
            node_id=node_id,
            atomic=atomic,
        )
        return _report("sliced", chunk_rows=rows, node_id=node_id)

    if recipe is not None and recipe.fn is not None:
        check_recipe_equivalence(recipe, frame)
        if not sliceable(recipe.input):
            bounded_sink(frame, dest, streaming_chunk_size=rows, atomic=atomic)
            return _report(
                "native",
                chunk_rows=None,
                native_reason="input_not_sliceable",
                node_id=node_id,
            )
        rows = _budgeted_recipe_rows(rows, recipe, execution_context=execution_context)
        applied_slices = _sink_slices(
            dest,
            source=recipe.input,
            schema=frame.collect_schema(),
            rows=rows,
            apply=recipe.apply,
            execution_context=execution_context,
            node_id=node_id,
            atomic=atomic,
        )
        return _report(
            "input_sliced",
            chunk_rows=rows,
            input_slices=applied_slices,
            node_id=node_id,
        )

    if recipe is not None:
        bounded_sink(frame, dest, streaming_chunk_size=rows, atomic=atomic)
        return _report(
            "native",
            chunk_rows=None,
            native_reason=recipe.reason,
            blocking_operator=recipe.blocking_operator,
            node_id=node_id,
        )

    bounded_sink(frame, dest, streaming_chunk_size=rows, atomic=atomic)
    return _report(
        "native",
        chunk_rows=None,
        native_reason="not_sliceable",
        node_id=node_id,
    )


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
        driving, lookup = (base, join) if shape.base_drives else (join, base)
        lookup_total = row_count(lookup, execution_context=execution_context)
        total = row_count(driving, execution_context=execution_context)
        if not total or not lookup_total:
            return counter[0]
        # A small lookup can be reused within the same row ceiling. Otherwise
        # one driving row visits bounded lookup slices in its requested order.
        lookup_chunk_rows = min(rows, lookup_total)
        per_chunk = max(1, rows // lookup_chunk_rows)
        small_lookup = (
            execution_collect(lookup.slice(0, lookup_total), execution_context=execution_context)
            if lookup_total <= rows
            else None
        )
        for offset in range(0, total, per_chunk):
            chunk = execution_collect(
                driving.slice(offset, per_chunk), execution_context=execution_context
            )
            for lookup_offset in range(0, lookup_total, lookup_chunk_rows):
                lookup_chunk = (
                    small_lookup
                    if small_lookup is not None
                    else execution_collect(
                        lookup.slice(lookup_offset, lookup_chunk_rows),
                        execution_context=execution_context,
                    )
                )
                left, right = (chunk, lookup_chunk) if shape.base_drives else (lookup_chunk, chunk)
                parts.write_join_part(
                    finish(left.lazy().join(right.lazy(), **kwargs)), ordered=keep_lookup_order
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
        self._matches_column: str | None = None

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

    def _resolve_matches_column(self, frame: pl.DataFrame) -> str:
        if self._matches_column is None:
            names = set(frame.columns)
            names.update(self.lookup.collect_schema().names())
            self._matches_column = _resolve_generated_name(_MATCHES_COLUMN, names)
        return self._matches_column

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
            matches_column = self._resolve_matches_column(chunk)
            counts = probe.group_by(self.lookup_keys).len(name=matches_column)
            if self._expected_rows(chunk, counts).sum() <= self.rows:
                self._sink(chunk, probe.lazy())
                return
            self._write_split(chunk, counts, matched=probe)
            return
        counts = self._collect(
            self._matches(chunk, self.lookup.select(self.lookup_keys))
            .group_by(self.lookup_keys)
            .len(name=self._resolve_matches_column(chunk))
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
            .get_column(self._resolve_matches_column(frame))
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
            index = _resolve_generated_name(_INDEX_COLUMN, names)
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
    matches_column = _resolve_generated_name(_MATCHES_COLUMN, set(keys))
    total = row_count(key_rows, execution_context=execution_context)
    partitions = max(1, math.ceil(total / rows))
    bucket = pl.struct(keys).hash(seed=0) % partitions
    for index in range(partitions):
        subset = key_rows if partitions == 1 else key_rows.filter(bucket == index)
        duplicates = execution_collect(
            subset.group_by(keys)
            .len(name=matches_column)
            .filter(pl.col(matches_column) > 1)
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
