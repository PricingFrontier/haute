"""Bounded Parquet emission: aggregate-bounded row-group writing for cache
artifacts and leased runtime spill bundles."""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import orjson
import polars as pl

from haute._api_input_schema import (
    sanitise_label_for_filesystem as _sanitise_label,
)
from haute._cpu_performance import configure_process_high_qos
from haute._env import int_env
from haute._execution_context import (
    current_execution_context,
)
from haute._json_shred import _records, _runtime_storage, _shred
from haute._json_shred._records import ShredSkipStats, _ChunkFailure, _ShredExecutionProgress
from haute._json_shred._shred import _EmittingTableSpec, _Row
from haute._logging import get_logger

logger = get_logger(component="json_shred")


_DIRECT_SPILL_MAX_ROWS_DEFAULT = 10_000


_DIRECT_SPILL_MAX_BYTES_DEFAULT = 16 * 1024 * 1024


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _ChunkResult:
    """One chunk's contribution, as returned across the process boundary."""

    index: int
    record_count: int
    skipped_records: int
    skipped_rows_by_table: dict[str, int]
    row_counts: dict[str, int]
    # label -> bounded Parquet part path. Rows stay on disk: piping millions of rows
    # back through the pool's result channel would cost more than the shred.
    part_paths: dict[str, str]
    failure: _ChunkFailure | None = None  # pragma: no mutate


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _ShredTables:
    """One scratch shred: its skip evidence and each table's Parquet files in row order."""

    skip_stats: ShredSkipStats
    files: dict[str, tuple[Path, ...]]


def _shred_chunk(
    args: tuple[str, int, int, int, dict[str, Any], str],
) -> _ChunkResult:
    """Shred one byte range into bounded Parquet row-group parts.

    Module-level and argument-driven so it survives ``spawn`` pickling on
    Windows. Ordinary failures return structured evidence for the parent to
    re-raise; process-control ``BaseException`` subclasses deliberately escape.
    """
    data_path_s, start, end, index, v2_config, tmp_dir_s = args
    data_path = Path(data_path_s)
    tmp_dir = Path(tmp_dir_s)
    writer: _BoundedParquetRowGroupWriter | None = None  # pragma: no mutate
    try:
        table_specs = _shred._emitting_table_specs(v2_config)
        stats = ShredSkipStats()
        record_count = 0
        emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
        writer = _BoundedParquetRowGroupWriter(
            tmp_dir,
            table_specs,
            filename_suffix=f".{index:06d}.part",
        )

        def _counted() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _records._iter_range_records(data_path, start, end, stats):
                record_count += 1
                yield record

        _shred.shred_to_buffers(
            _counted(),
            v2_config,
            stats=stats,
            _table_specs=table_specs,
            _row_sink=writer.emit,
            _emitted_counts=emitted_counts,
        )

        # Ranges tile the file exactly, so holding conservation on every chunk
        # holds it on the whole file and localises a violation to its range.
        _shred._assert_root_conservation(
            table_specs,
            {},
            stats,
            record_count,
            location=f" in byte range [{start}, {end})",
            emitted_counts=emitted_counts,
        )
        writer.flush()
        writer.close()

        return _ChunkResult(
            index=index,
            record_count=record_count,
            skipped_records=stats.skipped_records,
            skipped_rows_by_table=dict(stats.skipped_rows_by_table),
            row_counts=dict(writer.row_counts),
            part_paths={label: str(path) for label, path in writer.paths.items()},
        )
    except Exception as exc:  # noqa: BLE001 — reported, then re-raised in the parent
        if writer is not None:
            try:
                writer.close()
            except BaseException as cleanup_exc:
                exc.add_note(f"bounded chunk writer cleanup failed: {cleanup_exc}")
        return _ChunkResult(
            index=index,
            record_count=0,
            skipped_records=0,
            skipped_rows_by_table={},
            row_counts={},
            part_paths={},
            failure=_records._failure_from_exception(exc),
        )


def _raise_chunk_error(result: _ChunkResult) -> NoReturn:
    """Re-raise a shred worker failure without pickling arbitrary exceptions."""
    if result.failure is None:
        raise RuntimeError("parallel json shred chunk has no recorded failure")
    _records._raise_worker_failure(result.failure)


class _BoundedParquetRowGroupWriter:
    """Shared aggregate-bounded writer for snapshot builds and runtime spills."""

    def __init__(
        self,
        output_dir: Path,
        table_specs: tuple[_EmittingTableSpec, ...],
        *,  # pragma: no mutate
        disk_budget_root: Path | None = None,  # pragma: no mutate
        filename_suffix: str = "",
    ) -> None:
        import pyarrow.parquet as pq

        self.output_dir = output_dir
        self.cache_root = disk_budget_root
        self.table_specs = table_specs
        self.max_rows = int_env("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", _DIRECT_SPILL_MAX_ROWS_DEFAULT)
        self.max_bytes = int_env(
            "HAUTE_JSON_DIRECT_SPILL_MAX_BYTES", _DIRECT_SPILL_MAX_BYTES_DEFAULT
        )
        self.buffers: dict[str, list[_Row]] = {spec.label: [] for spec in table_specs}
        # A row's size estimate is its JSON-object encoding. orjson encodes the
        # row's values as ``[v1,v2]`` and the object as ``{"n1":v1,"n2":v2}``;
        # the difference is each encoded name plus its colon, fixed per table.
        self.name_bytes: dict[str, int] = {
            spec.label: sum(len(orjson.dumps(name)) + 1 for name, *_spec in spec.columns)
            for spec in table_specs
        }
        self.buffered_rows = 0
        self.buffered_bytes = 0
        self.paths: dict[str, Path] = {}
        self.writers: dict[str, Any] = {}
        self.row_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
        self.schema_frames: dict[str, pl.DataFrame] = {}
        try:
            with self._disk_transaction():
                for spec in table_specs:
                    frame = _shred._rows_to_frame([], spec.leaf_specs)
                    path = output_dir / (f"{_sanitise_label(spec.label)}{filename_suffix}.parquet")
                    arrow_schema = frame.to_arrow().schema.with_metadata(
                        _shred._per_frame_metadata(spec.label, spec.leaf_specs)
                    )
                    self.schema_frames[spec.label] = frame
                    self.paths[spec.label] = path
                    self.writers[spec.label] = pq.ParquetWriter(
                        path,
                        arrow_schema,
                        compression="zstd",
                    )
        except BaseException as exc:
            try:
                self.close()
            except BaseException as cleanup_exc:
                exc.add_note(f"bounded parquet writer cleanup failed: {cleanup_exc}")
            raise

    def _disk_transaction(self, *, allow_existing_excess: bool = False) -> Any:
        if self.cache_root is None:
            return nullcontext()
        return _runtime_storage._runtime_disk_budget_transaction(
            self.cache_root,
            allow_existing_excess=allow_existing_excess,
        )

    def emit(self, label: str, row: _Row) -> None:
        if label not in self.buffers:
            raise RuntimeError(f"bounded parquet writer received unknown table {label!r}")
        self.buffers[label].append(row)
        self.row_counts[label] += 1
        self.buffered_rows += 1
        # This is an accounting estimate, not serialisation retained in memory.
        self.buffered_bytes += len(orjson.dumps(row)) + self.name_bytes[label]
        if self.buffered_rows >= self.max_rows or self.buffered_bytes >= self.max_bytes:
            self.flush()

    def flush(self) -> None:
        progress = _ShredExecutionProgress.current()
        progress.checkpoint("json_shred_row_group_before_flush")
        with self._disk_transaction():
            for spec in self.table_specs:
                rows = self.buffers[spec.label]
                if not rows:
                    continue
                frame = _shred._rows_to_frame(rows, spec.leaf_specs)
                self.writers[spec.label].write_table(frame.to_arrow())
                rows.clear()
        self.buffered_rows = 0
        self.buffered_bytes = 0
        progress.checkpoint("json_shred_row_group_after_flush")

    def close(self) -> None:
        if not self.writers:
            return
        errors: list[BaseException] = []
        try:
            with self._disk_transaction(allow_existing_excess=True):
                for writer in self.writers.values():
                    try:
                        writer.close()
                    except BaseException as exc:
                        errors.append(exc)
        finally:
            self.writers.clear()
        if errors:
            first, *rest = errors
            for error in rest:
                first.add_note(f"additional bounded parquet writer cleanup failure: {error}")
            raise first

    def lazy_bundle(self) -> dict[str, pl.LazyFrame]:
        return {spec.label: pl.scan_parquet(self.paths[spec.label]) for spec in self.table_specs}


class _DirectSpillBundle(_BoundedParquetRowGroupWriter):
    """Runtime lifecycle wrapper around the shared bounded row-group writer."""

    def __init__(
        self,
        cache_dir: Path,
        table_specs: tuple[_EmittingTableSpec, ...],
    ) -> None:
        cache_root = _runtime_storage._runtime_storage_root_for_cache(cache_dir)
        self.spill_dir = _runtime_storage._new_direct_spill_dir(cache_dir)
        try:
            super().__init__(
                self.spill_dir,
                table_specs,
                disk_budget_root=cache_root,
            )
        except BaseException as exc:
            try:
                _runtime_storage._release_direct_spill_dir(self.spill_dir)
            except BaseException as cleanup_exc:
                exc.add_note(f"direct spill directory cleanup failed: {cleanup_exc}")
            raise


def _shred_data_file_to_direct_spill(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    cache_dir: Path,
) -> tuple[dict[str, pl.LazyFrame], ShredSkipStats]:
    """Shred one uncached source into a leased, bounded Parquet spill bundle."""
    skip_stats = ShredSkipStats()
    emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
    record_count = 0
    bundle = _DirectSpillBundle(cache_dir, table_specs)
    try:

        def _counted_records() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _records._iter_records(data_path, stats=skip_stats):
                record_count += 1
                yield record

        _shred.shred_to_buffers(
            _counted_records(),
            v2_config,
            stats=skip_stats,
            _table_specs=table_specs,
            _row_sink=bundle.emit,
            _emitted_counts=emitted_counts,
        )
        _shred._assert_root_conservation(
            table_specs,
            {},
            skip_stats,
            record_count,
            emitted_counts=emitted_counts,
        )
        bundle.flush()
        bundle.close()
        direct_bundle = bundle.lazy_bundle()
        execution_context = current_execution_context()
        if execution_context is not None:
            spill_dir = bundle.spill_dir
            try:
                execution_context.add_cleanup(
                    lambda: _runtime_storage._release_direct_spill_dir(spill_dir)
                )
            except BaseException:
                _runtime_storage._release_direct_spill_dir(bundle.spill_dir)
                raise
        # Unmanaged bundles intentionally stay in _DIRECT_SPILL_DIRS for the
        # process atexit hook: derived LazyFrames have no observable lifetime.
        return direct_bundle, skip_stats
    except BaseException as exc:
        try:
            bundle.close()
        except BaseException as cleanup_exc:
            exc.add_note(f"direct spill writer cleanup failed: {cleanup_exc}")
        try:
            _runtime_storage._release_direct_spill_dir(bundle.spill_dir)
        except BaseException as cleanup_exc:
            exc.add_note(f"direct spill directory cleanup failed: {cleanup_exc}")
        raise


def _write_tables_streaming(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    tmp_dir: Path,
) -> _ShredTables:
    """Stream one source into one bounded Parquet file per table in *tmp_dir*."""
    skip_stats = ShredSkipStats()
    emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
    record_count = 0
    writer = _BoundedParquetRowGroupWriter(tmp_dir, table_specs)
    try:

        def _counted_records() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _records._iter_records(data_path, stats=skip_stats):
                record_count += 1
                yield record

        _shred.shred_to_buffers(
            _counted_records(),
            v2_config,
            stats=skip_stats,
            _table_specs=table_specs,
            _row_sink=writer.emit,
            _emitted_counts=emitted_counts,
        )
        _shred._assert_root_conservation(
            table_specs,
            {},
            skip_stats,
            record_count,
            emitted_counts=emitted_counts,
        )
        writer.flush()
        writer.close()
        return _ShredTables(
            skip_stats,
            {spec.label: (writer.paths[spec.label],) for spec in table_specs},
        )
    except BaseException as exc:
        try:
            writer.close()
        except BaseException as cleanup_exc:
            exc.add_note(f"bounded cache writer cleanup failed: {cleanup_exc}")
        raise


def _merge_chunk_skip_stats(results: Iterable[_ChunkResult]) -> ShredSkipStats:
    """Combine worker skip evidence without losing counts shared by chunks."""
    combined = ShredSkipStats()
    for result in results:
        combined.skipped_records += result.skipped_records
        for label, count in result.skipped_rows_by_table.items():
            combined.skipped_rows_by_table[label] = (
                combined.skipped_rows_by_table.get(label, 0) + count
            )
    return combined


def _config_emitting_only(v2_config: dict[str, Any], labels: set[str]) -> dict[str, Any]:
    """*v2_config* with every emitting table outside *labels* turned off."""
    tables = v2_config["tables"]
    if all(not _shred.table_is_emitting(table) or table["label"] in labels for table in tables):
        return v2_config
    return {
        **v2_config,
        "tables": [
            table if table.get("label") in labels else {**table, "emit": False} for table in tables
        ],
    }


def _write_tables_in_parallel(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    tmp_dir: Path,
    ranges: list[tuple[int, int]],
) -> _ShredTables:
    """Shred *ranges* across worker processes into one bounded part per table each.

    Each table's parts are returned in chunk order, so reading them in that
    order gives the serial shred's rows in the serial order. The parts are not
    merged here: publication rewrites a table as the store's own part files, so
    a merge would only write every row one more time.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    import pyarrow.parquet as pq

    # Workers rebuild their table specs from the config, so the config they
    # receive emits exactly the tables being written: a build of one edited
    # table never shreds its fresh siblings.
    worker_config = _config_emitting_only(v2_config, {spec.label for spec in table_specs})
    tasks = [
        (str(data_path), start, end, index, worker_config, str(tmp_dir))
        for index, (start, end) in enumerate(ranges)
    ]
    workers = _records._parallel_worker_count(len(tasks))
    logger.info(
        "json_shred_parallel_start",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
    )
    started = time.perf_counter()

    # "spawn" explicitly: it is the only start method available on Windows and
    # the only one safe alongside the server's threads elsewhere, so every
    # platform exercises the same picklable-arguments path.
    results: list[_ChunkResult] = []
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=configure_process_high_qos,
    )
    try:
        # ``map`` yields in submission order, which is file order.
        for result in pool.map(_shred_chunk, tasks):
            if result.failure is not None:
                _raise_chunk_error(result)
            results.append(result)
    finally:
        # ``map`` submits every chunk up front, so a plain shutdown would wait
        # for the whole file even when chunk 2 of 200 already failed. Cancelling
        # the queued futures bounds a failed build by the chunks already in
        # flight (at most one per worker) rather than by the file's size.
        pool.shutdown(wait=True, cancel_futures=True)

    files: dict[str, tuple[Path, ...]] = {}
    for spec in table_specs:
        parts: list[Path] = []
        for result in results:
            part = result.part_paths.get(spec.label)
            if part is None:
                # Every successful chunk writes one part per emitting table
                # (worker and parent parse the same config). A missing part
                # means the two disagree about the table set — publishing a
                # parquet with silently absent rows would be worse than any
                # failure, so stop here.
                raise RuntimeError(
                    f"parallel json shred chunk {result.index} wrote no part "
                    f"for table {spec.label!r} — worker and parent table "
                    "specs diverged",
                )
            part_rows = pq.read_metadata(part).num_rows
            reported_rows = result.row_counts.get(spec.label, 0)
            if part_rows != reported_rows:
                raise RuntimeError(
                    f"parallel json shred row-count mismatch for table {spec.label!r} "
                    f"in chunk {result.index}: part holds {part_rows} != "
                    f"worker-reported {reported_rows}"
                )
            parts.append(Path(part))
        files[spec.label] = tuple(parts)

    logger.info(
        "json_shred_parallel_complete",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
        duration_seconds=round(  # pragma: no mutate - diagnostic timing only
            time.perf_counter() - started,
            3,  # pragma: no mutate
        ),
    )
    return _ShredTables(_merge_chunk_skip_stats(results), files)
