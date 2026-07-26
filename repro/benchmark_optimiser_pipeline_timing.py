from __future__ import annotations

import argparse
import gc
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import polars as pl

from haute._sandbox import set_project_root
from haute.routes._helpers import parse_pipeline_to_graph
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import (
    FrontierAutoRangeContext,
    OptimiserSolveService,
    _auto_range_required_columns_by_node,
    _estimate_scenario_frontier_ranges,
    _find_optimiser_node,
)
from haute.schemas import OptimiserFrontierAutoRangeRequest


def _process_memory_mb() -> dict[str, float]:
    """Return current process memory in MB without requiring psutil."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            raise ctypes.WinError()
        divisor = 1024 * 1024
        return {
            "rss_mb": counters.WorkingSetSize / divisor,
            "process_peak_rss_mb": counters.PeakWorkingSetSize / divisor,
            "private_mb": counters.PrivateUsage / divisor,
            "pagefile_mb": counters.PagefileUsage / divisor,
        }

    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Linux reports KB, macOS reports bytes. This benchmark is run on Windows
    # in the Codex workspace, so the fallback is best-effort.
    peak_mb = usage.ru_maxrss / (1024 if usage.ru_maxrss > 10_000_000 else 1)
    return {"rss_mb": peak_mb, "process_peak_rss_mb": peak_mb}


class StageMemorySampler:
    def __init__(self, interval_seconds: float) -> None:
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.before = _process_memory_mb()
        self.after: dict[str, float] = {}
        self.peak_rss_mb = self.before.get("rss_mb")
        self.peak_private_mb = self.before.get("private_mb")

    def __enter__(self) -> StageMemorySampler:
        if self._interval_seconds > 0:
            self._thread = threading.Thread(target=self._sample_loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self._interval_seconds * 2, 0.1))
        self._sample_once()
        self.after = _process_memory_mb()

    def _sample_loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._sample_once()

    def _sample_once(self) -> None:
        snapshot = _process_memory_mb()
        rss_mb = snapshot.get("rss_mb")
        private_mb = snapshot.get("private_mb")
        if rss_mb is not None:
            self.peak_rss_mb = max(self.peak_rss_mb or rss_mb, rss_mb)
        if private_mb is not None:
            self.peak_private_mb = max(self.peak_private_mb or private_mb, private_mb)

    def event_fields(self) -> dict[str, float]:
        fields: dict[str, float] = {}
        for prefix, snapshot in (("before", self.before), ("after", self.after)):
            for key, value in snapshot.items():
                fields[f"memory_{prefix}_{key}"] = round(value, 3)
        if self.peak_rss_mb is not None:
            fields["memory_stage_peak_rss_mb"] = round(self.peak_rss_mb, 3)
        if self.peak_private_mb is not None:
            fields["memory_stage_peak_private_mb"] = round(self.peak_private_mb, 3)
        return fields


@contextmanager
def timed(
    label: str,
    events: list[dict[str, Any]],
    *,
    memory_sample_interval: float = 0.05,
    **meta: Any,
) -> Iterator[None]:
    start = time.perf_counter()
    memory = StageMemorySampler(memory_sample_interval)
    try:
        with memory:
            yield
    finally:
        events.append(
            {
                "label": label,
                "seconds": round(time.perf_counter() - start, 6),
                **memory.event_fields(),
                **meta,
            },
        )


def parquet_stats(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    stats: dict[str, Any] = {
        "path": str(p),
        "size_mb": round(p.stat().st_size / 1024 / 1024, 3) if p.exists() else None,
    }
    try:
        import pyarrow.parquet as pq

        metadata = pq.ParquetFile(p).metadata
        stats["rows"] = metadata.num_rows
        stats["row_groups"] = metadata.num_row_groups
        stats["columns"] = metadata.num_columns
    except Exception as exc:  # noqa: BLE001 - benchmark metadata only.
        stats["metadata_error"] = str(exc)
    return stats


@contextmanager
def timing_hooks(
    events: list[dict[str, Any]],
    temp_outputs: list[str],
    *,
    detailed_batch: bool = False,
    memory_sample_interval: float = 0.05,
) -> Iterator[None]:
    import haute._execute_lazy as execute_lazy
    import haute._model_scorer as model_scorer

    original_bounded_sink = execute_lazy.bounded_sink

    def timed_bounded_sink(lf: Any, path: str | Path, *args: Any, **kwargs: Any) -> Any:
        label = f"checkpoint:{Path(path).stem}"
        with timed(
            label,
            events,
            kind="checkpoint",
            memory_sample_interval=memory_sample_interval,
        ):
            result = original_bounded_sink(lf, path, *args, **kwargs)
        events[-1].update(parquet_stats(path))
        return result

    execute_lazy.bounded_sink = timed_bounded_sink

    original_sink_to_temp = model_scorer._sink_to_temp

    def timed_sink_to_temp(
        lf: pl.LazyFrame,
        *,
        columns: frozenset[str] | set[str] | None = None,
    ) -> str:
        with timed(
            "model_score:sink_input",
            events,
            kind="model_score",
            memory_sample_interval=memory_sample_interval,
        ):
            path = original_sink_to_temp(lf, columns=columns)
        events[-1].update(parquet_stats(path))
        if columns is not None:
            events[-1]["requested_columns"] = len(columns)
        return path

    model_scorer._sink_to_temp = timed_sink_to_temp

    original_batch_score = model_scorer._batch_score_to_parquet

    def detailed_batch_score(*args: Any, **kwargs: Any) -> str:
        import pyarrow.parquet as pq

        from haute._mlflow_io import _append_classification_proba, _prepare_predict_frame

        scoring_model, input_path, features, output_col, task = args[:5]
        write_projection = kwargs.get("write_projection")
        projected_passthrough = None
        fd, out_path = tempfile.mkstemp(suffix=".parquet", prefix="haute_score_out_")
        os.close(fd)
        totals = {
            "open_input": 0.0,
            "read_batch": 0.0,
            "arrow_to_polars": 0.0,
            "prepare_features": 0.0,
            "predict": 0.0,
            "append_prediction": 0.0,
            "to_arrow": 0.0,
            "write_arrow": 0.0,
        }
        row_count = 0
        batch_count = 0
        writer = None
        want_proba = task == "classification"
        total_start = time.perf_counter()
        memory = StageMemorySampler(memory_sample_interval)
        with memory:
            try:
                t0 = time.perf_counter()
                pf = pq.ParquetFile(input_path)
                input_schema_names = list(pf.schema_arrow.names)
                if (
                    write_projection is not None
                    and write_projection.passthrough_columns is not None
                ):
                    projected_passthrough = [
                        c for c in input_schema_names if c in write_projection.passthrough_columns
                    ]
                totals["open_input"] += time.perf_counter() - t0
                batches = pf.iter_batches(batch_size=model_scorer._SCORE_BATCH_SIZE)
                while True:
                    t0 = time.perf_counter()
                    try:
                        batch = next(batches)
                    except StopIteration:
                        totals["read_batch"] += time.perf_counter() - t0
                        break
                    totals["read_batch"] += time.perf_counter() - t0
                    row_count += batch.num_rows
                    batch_count += 1

                    t0 = time.perf_counter()
                    chunk_raw = pl.from_arrow(batch)
                    chunk = chunk_raw.to_frame() if isinstance(chunk_raw, pl.Series) else chunk_raw
                    totals["arrow_to_polars"] += time.perf_counter() - t0

                    t0 = time.perf_counter()
                    feature_chunk = chunk.select(features)
                    x_data = _prepare_predict_frame(
                        feature_chunk,
                        features,
                        cat_feature_names=scoring_model.cat_feature_names,
                        flavor=scoring_model.flavor,
                    )
                    totals["prepare_features"] += time.perf_counter() - t0

                    t0 = time.perf_counter()
                    preds = scoring_model.predict(x_data)
                    totals["predict"] += time.perf_counter() - t0

                    t0 = time.perf_counter()
                    chunk = chunk.with_columns(pl.Series(output_col, preds))
                    if want_proba:
                        chunk = _append_classification_proba(
                            chunk,
                            scoring_model,
                            x_data,
                            output_col,
                        )
                    if projected_passthrough is not None:
                        generated = [output_col]
                        proba_col = f"{output_col}_proba"
                        if proba_col in chunk.columns:
                            generated.append(proba_col)
                        chunk = chunk.select(projected_passthrough + generated)
                    totals["append_prediction"] += time.perf_counter() - t0

                    t0 = time.perf_counter()
                    table = chunk.to_arrow()
                    totals["to_arrow"] += time.perf_counter() - t0

                    t0 = time.perf_counter()
                    if writer is None:
                        writer = pq.ParquetWriter(out_path, table.schema)
                    writer.write_table(table)
                    totals["write_arrow"] += time.perf_counter() - t0
                    del chunk, x_data, table
            finally:
                if writer is not None:
                    writer.close()
                else:
                    input_schema = pl.read_parquet_schema(input_path)
                    passthrough_cols = (
                        input_schema_names
                        if projected_passthrough is None
                        else projected_passthrough
                    )
                    empty = pl.DataFrame(
                        {
                            c: pl.Series([], dtype=input_schema.get(c, pl.Float64))
                            for c in passthrough_cols
                        },
                    ).with_columns(pl.Series(output_col, [], dtype=pl.Float64))
                    if want_proba:
                        empty = empty.with_columns(
                            pl.Series(f"{output_col}_proba", [], dtype=pl.Float64),
                        )
                    pq.write_table(empty.to_arrow(), out_path)

        events.append(
            {
                "label": "model_score:predict_write_detailed",
                "seconds": round(time.perf_counter() - total_start, 6),
                "kind": "model_score",
                "output_col": output_col,
                "features": list(features),
                "batches": batch_count,
                "input_rows": row_count,
                "breakdown": {k: round(v, 6) for k, v in totals.items()},
                **memory.event_fields(),
                **parquet_stats(out_path),
            },
        )
        temp_outputs.append(out_path)
        return out_path

    def timed_batch_score(*args: Any, **kwargs: Any) -> str:
        if detailed_batch:
            return detailed_batch_score(*args, **kwargs)
        with timed(
            "model_score:predict_write",
            events,
            kind="model_score",
            memory_sample_interval=memory_sample_interval,
        ):
            path = original_batch_score(*args, **kwargs)
        events[-1].update(parquet_stats(path))
        temp_outputs.append(path)
        return path

    model_scorer._batch_score_to_parquet = timed_batch_score

    try:
        yield
    finally:
        execute_lazy.bounded_sink = original_bounded_sink
        model_scorer._sink_to_temp = original_sink_to_temp
        model_scorer._batch_score_to_parquet = original_batch_score


def override_batch_limit(graph: Any, limit: int | None) -> None:
    if limit is None:
        return
    graph.node_map["batch_quotes"].data.config["code"] = f"df = df.limit({limit})"


def run_once(
    root: Path,
    pipeline: Path,
    node_id: str,
    limit: int | None,
    score_batch_size: int | None,
    detailed_batch: bool,
    memory_sample_interval: float,
    projection_mode: str,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    temp_outputs: list[str] = []

    store = JobStore()
    service = OptimiserSolveService(store)

    with timed("parse_graph", events, memory_sample_interval=memory_sample_interval):
        graph = parse_pipeline_to_graph(pipeline)
    override_batch_limit(graph, limit)

    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id=node_id)
    node = _find_optimiser_node(body.graph, body.node_id)
    config = node.data.config

    with timed("validate_config", events, memory_sample_interval=memory_sample_interval):
        mode = service._validate_config(config)
        required_columns_by_node = _auto_range_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
            mode=mode,
        )
        execution_required_columns = (
            required_columns_by_node if projection_mode == "auto_range" else None
        )

    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "frontier_auto_range",
            "progress": 0.0,
            "message": "Benchmarking frontier range",
            "config": dict(config),
            "node_label": node.data.label,
        },
    )

    ranges: dict[str, dict[str, float]] = {}
    minimal_ranges: dict[str, dict[str, float]] = {}
    old_batch_size: int | None = None
    if score_batch_size is not None:
        import haute._model_scorer as model_scorer

        old_batch_size = model_scorer._SCORE_BATCH_SIZE
        model_scorer._SCORE_BATCH_SIZE = score_batch_size

    try:
        with tempfile.TemporaryDirectory(prefix="haute_pipeline_timing_") as raw_dir:
            checkpoint_dir = Path(raw_dir)
            with timing_hooks(
                events,
                temp_outputs,
                detailed_batch=detailed_batch,
                memory_sample_interval=memory_sample_interval,
            ):
                with timed(
                    "execute_pipeline",
                    events,
                    memory_sample_interval=memory_sample_interval,
                ):
                    lazy_outputs = service._execute_pipeline(
                        body,
                        job_id,
                        checkpoint_dir,
                        required_columns_by_node=execution_required_columns,
                    )

                with timed(
                    "resolve_data_source",
                    events,
                    memory_sample_interval=memory_sample_interval,
                ):
                    source_lf = service._resolve_data_source(
                        lazy_outputs,
                        config,
                        body.node_id,
                        job_id,
                    )

                with timed(
                    "validate_and_project_auto_range",
                    events,
                    memory_sample_interval=memory_sample_interval,
                ):
                    constraint_cols, scored_lf = service._validate_and_project_auto_range(
                        source_lf,
                        config,
                        job_id,
                    )

                with timed(
                    "range_aggregate_auto_range_projection",
                    events,
                    memory_sample_interval=memory_sample_interval,
                ):
                    ranges = _estimate_scenario_frontier_ranges(
                        FrontierAutoRangeContext(),
                        scored_lf=scored_lf,
                        quote_id_col=str(config.get("quote_id", "quote_id")),
                        constraint_cols=constraint_cols,
                    )

                qid_col = str(config.get("quote_id", "quote_id"))
                with timed(
                    "range_aggregate_minimal_projection",
                    events,
                    memory_sample_interval=memory_sample_interval,
                ):
                    minimal_lf = source_lf.select(
                        [pl.col(qid_col), *[pl.col(c).cast(pl.Float32) for c in constraint_cols]],
                    )
                    minimal_ranges = _estimate_scenario_frontier_ranges(
                        FrontierAutoRangeContext(),
                        scored_lf=minimal_lf,
                        quote_id_col=qid_col,
                        constraint_cols=constraint_cols,
                    )
    finally:
        if old_batch_size is not None:
            import haute._model_scorer as model_scorer

            model_scorer._SCORE_BATCH_SIZE = old_batch_size
        store.delete_job(job_id)
        for path in temp_outputs:
            try:
                os.unlink(path)
            except OSError:
                pass
        gc.collect()

    return {
        "limit": limit,
        "score_batch_size": score_batch_size,
        "detailed_batch": detailed_batch,
        "memory_sample_interval": memory_sample_interval,
        "projection_mode": projection_mode,
        "node_id": node_id,
        "constraints": list((config.get("constraints") or {}).keys()),
        "events": events,
        "ranges": ranges,
        "minimal_ranges": minimal_ranges,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pipeline", type=Path, default=Path("rating/main.py"))
    parser.add_argument("--node-id", default="online_optimiser")
    parser.add_argument(
        "--limits",
        nargs="+",
        type=int,
        default=[10_000, 100_000],
        help="Quote limits to run. Use 0 for the pipeline's configured limit.",
    )
    parser.add_argument(
        "--score-batch-size",
        type=int,
        default=None,
        help="Override haute._model_scorer._SCORE_BATCH_SIZE for this run.",
    )
    parser.add_argument(
        "--detailed-batch",
        action="store_true",
        help="Break model-score predict/write into read, predict, and parquet-write timings.",
    )
    parser.add_argument(
        "--memory-sample-interval",
        type=float,
        default=0.05,
        help="Seconds between process memory samples while a timed stage is running.",
    )
    parser.add_argument(
        "--projection-mode",
        choices=("auto_range", "unprojected"),
        default="auto_range",
        help=(
            "auto_range passes the optimiser's required column projection into "
            "execution; unprojected omits it as a control run."
        ),
    )
    parser.add_argument(
        "--include-unprojected-control",
        action="store_true",
        help=(
            "For each limit, run both an unprojected control and the auto_range "
            "projection path. For clean high-water memory comparisons, run each "
            "projection mode in a fresh process."
        ),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    set_project_root(root)
    pipeline = args.pipeline if args.pipeline.is_absolute() else root / args.pipeline

    runs = []
    for raw_limit in args.limits:
        limit = None if raw_limit == 0 else raw_limit
        projection_modes = (
            ("unprojected", "auto_range")
            if args.include_unprojected_control
            else (args.projection_mode,)
        )
        for projection_mode in projection_modes:
            runs.append(
                run_once(
                    root,
                    pipeline,
                    args.node_id,
                    limit,
                    args.score_batch_size,
                    args.detailed_batch,
                    args.memory_sample_interval,
                    projection_mode,
                ),
            )

    print(json.dumps({"pipeline": str(pipeline), "runs": runs}, indent=2))


if __name__ == "__main__":
    main()
