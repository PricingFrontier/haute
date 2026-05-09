from __future__ import annotations

import argparse
import gc
import json
import statistics
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from haute._sandbox import set_project_root
from haute.parser import parse_pipeline_file
from haute.routes._job_store import JobStore
from haute.routes._optimiser_service import (
    OptimiserSolveService,
    _estimate_scenario_frontier_ranges,
)
from haute.schemas import OptimiserFrontierAutoRangeRequest


@contextmanager
def timed(name: str, sink: list[dict[str, float | str]]) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        sink.append({"phase": name, "seconds": time.perf_counter() - start})


def _summarise_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    phases = sorted({phase["phase"] for run in runs for phase in run["phases"]})
    summary: dict[str, Any] = {}
    for phase in phases:
        values = [
            float(item["seconds"])
            for run in runs
            for item in run["phases"]
            if item["phase"] == phase
        ]
        summary[phase] = {
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
        }
    return summary


def run_once(root: Path, pipeline: Path, node_id: str) -> dict[str, Any]:
    phases: list[dict[str, float | str]] = []
    store = JobStore()
    service = OptimiserSolveService(store)

    with timed("parse_pipeline_file", phases):
        graph = parse_pipeline_file(pipeline, flatten=True)
    body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id=node_id)

    with timed("validate_config", phases):
        prepared = service._prepare_frontier_auto_range(body)
        node = prepared["node"]
        config = prepared["config"]

    job_id = store.create_job(
        {
            "status": "running",
            "job_type": "frontier_auto_range",
            "progress": 0.0,
            "message": "Benchmarking frontier range",
            "config": dict(config),
            "node_label": node.data.label,
        }
    )

    try:
        with tempfile.TemporaryDirectory(prefix="haute_frontier_range_bench_") as raw_dir:
            checkpoint_dir = Path(raw_dir)
            with timed("execute_pipeline", phases):
                lazy_outputs = service._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=prepared["required_columns_by_node"],
                )
            with timed("resolve_data_source", phases):
                source_lf = service._resolve_data_source(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                )
            with timed("validate_and_project_auto_range", phases):
                constraint_cols, scored_lf = service._validate_and_project_auto_range(
                    source_lf,
                    config,
                    job_id,
                )
            with timed("estimate_scenario_frontier_ranges", phases):
                ranges = _estimate_scenario_frontier_ranges(
                    scored_lf,
                    quote_id_col=str(config.get("quote_id", "quote_id")),
                    constraint_cols=constraint_cols,
                    chunk_size=prepared["chunk_size"],
                    partition_count=prepared["partition_count"],
                )
    finally:
        store.delete_job(job_id)
        gc.collect()

    total = sum(float(phase["seconds"]) for phase in phases)
    return {
        "node_id": node_id,
        "objective": config.get("objective"),
        "constraints": list((config.get("constraints") or {}).keys()),
        "data_input": config.get("data_input"),
        "phases": phases,
        "total_measured_seconds": total,
        "ranges": ranges,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pipeline", type=Path, default=Path("rating/main.py"))
    parser.add_argument("--node-id", default="online_optimiser")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    root = args.root.resolve()
    set_project_root(root)
    pipeline = args.pipeline
    if not pipeline.is_absolute():
        pipeline = root / pipeline

    runs = [run_once(root, pipeline, args.node_id) for _ in range(args.runs)]
    payload = {
        "root": str(root),
        "pipeline": str(pipeline),
        "runs": runs,
        "summary": _summarise_runs(runs),
    }
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
