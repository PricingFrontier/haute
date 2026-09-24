"""The runtime apiInput source loader.

Canvas execution reads each emitting table from its generation in the shared
input-snapshot store (see :mod:`haute._json_shred._snapshots`); automatic
preparation publishes those generations before execution. Generated standalone
code runs without a project store and shreds its source in-process instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl

from haute._execution_context import current_execution_context
from haute._json_shred import _shred, _writer
from haute._json_shred._shred import _EmittingTableSpec
from haute._logging import get_logger
from haute._source_cache import SourceCacheStore

logger = get_logger(component="json_shred")

# Generated standalone code spills its in-process shred below the process
# working directory's cache root, outside the shared input-snapshot store.
_STANDALONE_SPILL_ROOT = ".haute_cache"


def _projected_table_specs(
    complete_table_specs: tuple[_EmittingTableSpec, ...],
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None,  # pragma: no mutate
) -> tuple[_EmittingTableSpec, ...]:
    """The demanded ports and columns, in schema order."""
    if port_columns is None:
        return complete_table_specs
    if not isinstance(port_columns, Mapping) or not port_columns:
        raise ValueError("port_columns must be a non-empty mapping")
    complete_by_label = {spec.label: spec for spec in complete_table_specs}
    projected_specs: list[_EmittingTableSpec] = []
    for label, requested_columns in port_columns.items():
        if label not in complete_by_label:
            raise ValueError(f"port_columns requests unknown emitting port {label!r}")
        if requested_columns is not None and not isinstance(
            requested_columns,
            (frozenset, set),
        ):
            raise ValueError(
                f"port_columns[{label!r}] must be None or a frozenset/set",
            )
        complete_spec = complete_by_label[label]
        if requested_columns is None:
            projected_specs.append(complete_spec)
            continue
        if any(not isinstance(column, str) or not column for column in requested_columns):
            raise ValueError(
                f"port_columns[{label!r}] must contain non-empty string column names",
            )
        available = {name for name, _leaf, _type, _depth in complete_spec.columns}
        missing = set(requested_columns) - available
        if missing:
            raise ValueError(
                f"port_columns[{label!r}] requests missing declared column(s): {sorted(missing)!r}",
            )
        # Preserve config order, not demand-set iteration order. An empty
        # logical demand is cardinality-only; one physical carrier keeps
        # Polars from collapsing the frame to zero rows.
        physical_columns = (
            set(requested_columns) if requested_columns else {complete_spec.columns[0][0]}
        )
        projected_specs.append(
            _EmittingTableSpec(
                label=complete_spec.label,
                segments=complete_spec.segments,
                columns=tuple(
                    column for column in complete_spec.columns if column[0] in physical_columns
                ),
            ),
        )
    # Preserve v2 schema order even when the caller supplied its mapping in
    # a different order.
    requested_by_label = {spec.label: spec for spec in projected_specs}
    return tuple(
        requested_by_label[spec.label]
        for spec in complete_table_specs
        if spec.label in requested_by_label
    )


def _snapshot_missing_message(label: str) -> str:
    return (
        f"input_snapshot_missing: This API Input's table {label!r} runs from a "
        "snapshot that has not been built yet. Build the snapshot (or run a "
        "preview, which builds it automatically) and try again."
    )


def _leased_table_frames(
    data_path: str,
    config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    store: SourceCacheStore | None,  # pragma: no mutate
) -> dict[str, pl.LazyFrame]:
    from haute._input_providers import _cache_root, lease_input_generation
    from haute._json_shred._snapshots import api_input_snapshot_source

    source = api_input_snapshot_source(config, data_path)
    cache_store = store or SourceCacheStore(_cache_root())
    frames: dict[str, pl.LazyFrame] = {}
    for spec in table_specs:
        frame = lease_input_generation(
            cache_store,
            source.table(spec.label).identity,
            missing_message=_snapshot_missing_message(spec.label),
        )
        frames[spec.label] = frame.select(_shred._declared_frame_schema(spec).names())
    return frames


def _standalone_shred(
    data_path: str,
    config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
) -> dict[str, pl.LazyFrame]:
    direct_bundle, skip_stats = _writer._shred_data_file_to_direct_spill(
        Path(data_path),
        config,
        table_specs,
        Path.cwd() / _STANDALONE_SPILL_ROOT,
    )
    execution_context = current_execution_context()
    if execution_context is not None:
        execution_context.record_cache_direct_fallback()
    if skip_stats.total:
        logger.warning(
            "json_shred_direct_records_skipped",
            data_path=data_path,
            skipped_records=skip_stats.skipped_records,
            skipped_rows_by_table=skip_stats.skipped_rows_by_table,
        )
    logger.info(
        "json_shred_loaded_direct",
        data_path=data_path,
        table_count=len(table_specs),
    )
    return direct_bundle


def load_v2_api_source(
    data_path: str,
    config: dict[str, Any],
    *,  # pragma: no mutate
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None = None,  # pragma: no mutate
    read_snapshots: bool = False,
    store: SourceCacheStore | None = None,  # pragma: no mutate
) -> dict[str, pl.LazyFrame]:
    """Load a v2 apiInput as an emit-gated per-port frame bundle.

    The shared frame-bundle loader reached through
    :func:`haute._node_apply.resolve_api_input_from_config` by both executor
    and generated code. Validates *config* itself so direct callers receive
    the same typed schema errors as the executor and generated module
    boundaries.

    Behaviour:

    - 0 emit-true tables → ``RuntimeError`` (tick an ``emit`` toggle).
    - emit-true tables but none with a selected column → ``RuntimeError``.
    - ``read_snapshots`` (canvas execution) leases each demanded table's
      current generation from the shared store and never reads the source; a
      table with no generation raises ``PolarsIoConfigError``
      (``input_snapshot_missing``).
    - otherwise (generated standalone code) shreds JSON, JSONL, or XML
      in-process into a bounded, process-owned spill.
    - 1+ emitting labels → a ``dict[port_label, LazyFrame]`` in schema order.

    Frame resolution uses the shared :func:`table_is_emitting` predicate, so
    an emit-true table with zero selected columns contributes no frame.
    """
    # Parse the complete config before considering a demand: the demand only
    # controls which ports and columns are materialised for this caller.
    complete_table_specs = _shred._emitting_table_specs(config)
    tables = config["tables"]
    emit_true_tables = [t for t in tables if t.get("emit")]
    if not emit_true_tables:
        raise RuntimeError(
            "API Input has no emitting tables. Open the node, tick the 'emit' "
            "toggle on at least one table, then preview again.",
        )
    if not complete_table_specs:
        labels = [t["label"] for t in emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected columns. "
            f"Open the node and tick at least one column on the emitting "
            f"table(s): {labels}, then preview again.",
        )
    table_specs = _projected_table_specs(complete_table_specs, port_columns)
    if read_snapshots:
        return _leased_table_frames(data_path, config, table_specs, store)
    return _standalone_shred(data_path, config, table_specs)
