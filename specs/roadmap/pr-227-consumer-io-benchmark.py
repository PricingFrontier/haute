"""Bounded consumer I/O measurements for PR-227."""

from __future__ import annotations

# ruff: noqa: E402 — thread limits must be set before importing Polars.
import argparse
import importlib.util
import io
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

os.environ["POLARS_MAX_THREADS"] = "2"
os.environ["OMP_NUM_THREADS"] = "2"
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.memory_smoke import run_smoke

alloc_spec = importlib.util.spec_from_file_location(
    "alloc", ROOT / "specs/roadmap/pr-227-allocation-benchmark.py"
)
alloc = importlib.util.module_from_spec(alloc_spec)
alloc_spec.loader.exec_module(alloc)


class SumModel:
    def predict(self, frame):
        return (frame["feature0"] + frame["feature1"]).to_numpy()


def make_score(path, rows, parts=1):
    df = pl.DataFrame(
        {
            "quote_id": pl.int_range(0, rows, eager=True).cast(pl.Int64),
            "feature0": pl.arange(0, rows, eager=True).cast(pl.Float32),
            "feature1": pl.Series("feature1", [2.0] * rows, dtype=pl.Float32),
            **{
                f"feature{i}": (pl.arange(0, rows, eager=True) % (101 + i)).cast(pl.Float32)
                for i in range(2, 8)
            },
        }
    )
    if parts == 1:
        df.write_parquet(path / "score.parquet")
        return [path / "score.parquet"]
    out = []
    for i in range(parts):
        q = path / f"score-{i}.parquet"
        df.slice(i * rows // parts, rows // parts).write_parquet(q)
        out.append(q)
    return out


def worker(ws, variant):
    import haute._model_scorer as scorer
    from haute._execution_context import current_rss_bytes

    if variant.startswith("grid"):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        paths = sorted(ws.glob("grid*.parquet"))
        lf = pl.scan_parquet([str(p) for p in paths])
        if variant == "grid-filter":
            lf = lf.filter(pl.col("scenario_value") > 0)
        sink_calls = []
        import haute.routes._optimiser_service as om

        real = om.bounded_sink

        def sink(*a, **k):
            result = real(*a, **k)
            path = Path(a[1]) if len(a) > 1 else Path(k["path"])
            sink_calls.append((path, path.stat().st_size))
            return result

        om.bounded_sink = sink
        base = current_rss_bytes()
        start = time.perf_counter()
        store = JobStore()
        job = store.create_job({"status": "running"})
        svc = OptimiserSolveService(store)
        cfg = {
            "objective": "expected_income",
            "quote_id": "quote_id",
            "scenario_value": "scenario_value",
            "scenario_index": "scenario_index",
            "chunk_size": 100000,
        }
        grid = svc._build_grid(
            lf, [f"c{i}" for i in range(4)], cfg, "opt", job, streaming_chunk_size=100000
        )
        elapsed = time.perf_counter() - start
        assert (
            grid.n_quotes == 25000
            and grid.n_steps == 20
            and list(grid.constraint_names) == [f"c{i}" for i in range(4)]
        )
        (ws / "worker.json").write_text(
            json.dumps(
                {
                    "variant": variant,
                    "elapsed_seconds": elapsed,
                    "baseline_rss_bytes": base,
                    "end_rss_bytes": current_rss_bytes(),
                    "sink_call_count": len(sink_calls),
                    "sink_bytes": sum(size for _, size in sink_calls),
                    "verified_scalar_contract": True,
                }
            ),
            encoding="utf-8",
        )
        return
    paths = sorted(ws.glob("score*.parquet"))
    lf = pl.scan_parquet([str(p) for p in paths])
    if variant == "staged":
        scorer.sliceable = lambda _: False
    staged = []
    real = scorer._sink_to_temp

    def sink(*a, **k):
        p = Path(real(*a, **k))
        staged.append((p, p.stat().st_size))
        return str(p)

    scorer._sink_to_temp = sink
    base = current_rss_bytes()
    start = time.perf_counter()
    result = scorer._score_batched_unified(
        SumModel(), lf, ["feature0", "feature1"], frozenset(), "pyfunc", "regression", "prediction"
    )
    elapsed = time.perf_counter() - start
    # Verification is outside the timed write interval and collects only scalars.
    checked = result.select(pl.len().alias("rows"), pl.col("prediction").sum()).collect()
    assert checked["rows"][0] == 500000 and checked["prediction"][0] == 125000750000.0
    (ws / "worker.json").write_text(
        json.dumps(
            {
                "variant": variant,
                "elapsed_seconds": elapsed,
                "baseline_rss_bytes": base,
                "end_rss_bytes": current_rss_bytes(),
                "staging_call_count": len(staged),
                "staging_bytes": sum(size for _, size in staged),
                "prediction_sum": float(checked["prediction"][0]),
                "verified_scalar_contract": True,
            }
        ),
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--workspace", type=Path)
    ap.add_argument("--variant")
    a = ap.parse_args()
    if a.worker:
        worker(a.workspace, a.variant)
        return
    script = str(Path(__file__).resolve())
    boot = (
        f"import runpy,sys;sys.path[:0]={[p for p in sys.path if p]!r};"
        f"runpy.run_path({script!r},run_name='__main__')"
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="pr227-consumer-") as td:
        ws = Path(td)
        make_score(ws, 500000, 1)
        for variant in ("single", "staged"):
            for rep in (1, 2):
                log = io.BytesIO()
                sm = run_smoke(
                    command=[
                        str(getattr(sys, "_base_executable", sys.executable)),
                        "-c",
                        boot,
                        "--worker",
                        "--workspace",
                        str(ws),
                        "--variant",
                        variant,
                    ],
                    enable_tracemalloc=False,
                    poll_interval_seconds=0.005,
                    child_output=log,
                )
                if sm["exit_code"]:
                    raise RuntimeError(log.getvalue().decode(errors="replace"))
                r = json.loads((ws / "worker.json").read_text(encoding="utf-8"))
                assert r["variant"] == variant
                r.update(
                    repetition=rep,
                    peak_rss_bytes=sm["child_peak_rss_bytes"],
                    incremental_peak_rss_bytes=sm["child_peak_rss_bytes"] - r["baseline_rss_bytes"],
                    worker_exit_code=sm["exit_code"],
                    rss_samples=sm["child_rss_sample_count"],
                )
                results.append(r)
                print(json.dumps(r), flush=True)
        (ws / "score.parquet").unlink(missing_ok=True)
        make_score(ws, 500000, 2)
        for rep in (1, 2):
            log = io.BytesIO()
            sm = run_smoke(
                command=[
                    str(getattr(sys, "_base_executable", sys.executable)),
                    "-c",
                    boot,
                    "--worker",
                    "--workspace",
                    str(ws),
                    "--variant",
                    "single",
                ],
                enable_tracemalloc=False,
                poll_interval_seconds=0.005,
                child_output=log,
            )
            if sm["exit_code"]:
                raise RuntimeError(log.getvalue().decode(errors="replace"))
            r = json.loads((ws / "worker.json").read_text(encoding="utf-8"))
            r.update(
                repetition=rep,
                variant="multipart",
                peak_rss_bytes=sm["child_peak_rss_bytes"],
                incremental_peak_rss_bytes=sm["child_peak_rss_bytes"] - r["baseline_rss_bytes"],
                worker_exit_code=sm["exit_code"],
                rss_samples=sm["child_rss_sample_count"],
            )
            results.append(r)
            print(json.dumps(r), flush=True)
        for name, parts in (("grid", 1), ("grid-filter", 1), ("grid-multipart", 2)):
            for rep in (1, 2):
                if parts == 1:
                    (ws / "grid-a.parquet").unlink(missing_ok=True)
                    (ws / "grid-b.parquet").unlink(missing_ok=True)
                    alloc.make_batch(0, 500000, "grid").write_parquet(ws / "grid.parquet")
                else:
                    (ws / "grid.parquet").unlink(missing_ok=True)
                    frame = alloc.make_batch(0, 500000, "grid")
                    frame.slice(0, 250000).write_parquet(ws / "grid-a.parquet")
                    frame.slice(250000).write_parquet(ws / "grid-b.parquet")
                log = io.BytesIO()
                sm = run_smoke(
                    command=[
                        str(getattr(sys, "_base_executable", sys.executable)),
                        "-c",
                        boot,
                        "--worker",
                        "--workspace",
                        str(ws),
                        "--variant",
                        name,
                    ],
                    enable_tracemalloc=False,
                    poll_interval_seconds=0.005,
                    child_output=log,
                )
                if sm["exit_code"]:
                    raise RuntimeError(log.getvalue().decode(errors="replace"))
                r = json.loads((ws / "worker.json").read_text(encoding="utf-8"))
                assert r["variant"] == name
                r.update(
                    repetition=rep,
                    peak_rss_bytes=sm["child_peak_rss_bytes"],
                    incremental_peak_rss_bytes=sm["child_peak_rss_bytes"] - r["baseline_rss_bytes"],
                    worker_exit_code=sm["exit_code"],
                    rss_samples=sm["child_rss_sample_count"],
                )
                results.append(r)
                print(json.dumps(r), flush=True)
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "polars": pl.__version__,
                "threads": 2,
                "rss_poll_interval_ms": 5,
                "results": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
