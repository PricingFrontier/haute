"""Bounded native-vs-JoinRecipe parquet join benchmark for PR #227."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.memory_smoke import run_smoke  # noqa: E402

SIZES = (100_000, 400_000)
STRATEGIES = ("native", "recipe")
SEED = 227
CHUNK_ROWS = 25_000


def _fixture(size: int, side: str, path: Path) -> None:
    rng = np.random.default_rng(SEED + size + (0 if side == "left" else 1))
    keys = np.arange(size, dtype=np.int64)
    rng.shuffle(keys)
    values = {"key": keys}
    for index in range(4):
        values[f"{side}_payload_{index}"] = rng.random(size, dtype=np.float64)
    pl.DataFrame(values).write_parquet(path, row_group_size=CHUNK_ROWS)


def prepare(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        for side in ("left", "right"):
            _fixture(size, side, out / f"{side}-{size}.parquet")


def _reference(left: Path, right: Path) -> dict[str, float | int]:
    lf = pl.scan_parquet(left).join(pl.scan_parquet(right), on="key", how="left")
    return {
        "rows": int(lf.select(pl.len()).collect().item()),
        **{
            name: float(lf.select(pl.col(name).sum()).collect().item() or 0.0)
            for name in [
                "key",
                "left_payload_0",
                "left_payload_1",
                "left_payload_2",
                "left_payload_3",
                "right_payload_0",
                "right_payload_1",
                "right_payload_2",
                "right_payload_3",
            ]
        },
    }


def _verify(path: Path, expected: dict[str, float | int]) -> dict[str, object]:
    lf = pl.scan_parquet(path) if path.is_file() else pl.scan_parquet(path / "*.parquet")
    actual = {
        "rows": int(lf.select(pl.len()).collect().item()),
        **{
            name: float(lf.select(pl.col(name).sum()).collect().item() or 0.0)
            for name in expected
            if name != "rows"
        },
    }
    checks = {
        key: bool(
            actual[key] == value
            if key == "rows"
            else np.isclose(actual[key], value, rtol=1e-10, atol=1e-10)
        )
        for key, value in expected.items()
    }
    return {"actual": actual, "expected": expected, "checks": checks, "ok": all(checks.values())}


def worker(size: int, strategy: str, workspace: Path, output: Path) -> None:
    workspace = workspace.resolve()
    output = output.resolve()
    if workspace not in output.parents:
        raise ValueError("worker output must be beneath its private workspace")
    left = workspace / f"left-{size}.parquet"
    right = workspace / f"right-{size}.parquet"
    expected = json.loads((workspace / f"expected-{size}.json").read_text(encoding="utf-8"))
    if output.exists():
        shutil.rmtree(output) if output.is_dir() else output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    left_lf = pl.scan_parquet(left)
    right_lf = pl.scan_parquet(right)
    from haute._chunked_writes import JoinRecipe, write_parts
    from haute._execution_context import current_rss_bytes
    from haute._polars_utils import bounded_sink

    start_rss = current_rss_bytes()
    started = time.perf_counter()
    if strategy == "native":
        bounded_sink(left_lf.join(right_lf, on="key", how="left"), output, fast_checkpoint=False)
    else:
        recipe = JoinRecipe(left_lf, right_lf, {"on": "key", "how": "left"})
        output.mkdir()
        write_parts(
            output, recipe.native(), join=recipe, chunk_rows=CHUNK_ROWS, fast_checkpoint=False
        )
    elapsed = time.perf_counter() - started
    verification = _verify(output, expected)
    if not verification["ok"]:
        raise AssertionError(json.dumps(verification, sort_keys=True))
    worker_result = {
        "size": size,
        "strategy": strategy,
        "worker_rss_before_write_bytes": start_rss,
        "write_elapsed_seconds": elapsed,
        "verification": verification,
    }
    (workspace / f"{strategy}-{size}.verification.json").write_text(
        json.dumps(worker_result, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(worker_result, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--size", type=int, choices=SIZES)
    parser.add_argument("--strategy", choices=STRATEGIES)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.size is None or args.strategy is None:
            parser.error("--worker requires --size and --strategy")
        if args.workspace is None:
            parser.error("--worker requires --workspace")
        worker(
            args.size,
            args.strategy,
            args.workspace,
            args.workspace / f"{args.strategy}-{args.size}",
        )
        return 0
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pr227-join-") as temp:
        workspace = Path(temp)
        prepare(workspace)
        for size in SIZES:
            expected = _reference(
                workspace / f"left-{size}.parquet", workspace / f"right-{size}.parquet"
            )
            (workspace / f"expected-{size}.json").write_text(
                json.dumps(expected) + "\n", encoding="utf-8"
            )
        interpreter = str(getattr(sys, "_base_executable", sys.executable))
        inherited_paths = [path for path in sys.path if path]
        bootstrap = (
            "import runpy,sys;"
            + f"sys.path[:0]={inherited_paths!r};"
            + f"runpy.run_path({str(Path(__file__).resolve())!r},run_name='__main__')"
        )
        for size in SIZES:
            for strategy in STRATEGIES:
                for repetition in (1, 2):
                    command = [
                        interpreter,
                        "-c",
                        bootstrap,
                        "--worker",
                        "--size",
                        str(size),
                        "--strategy",
                        strategy,
                        "--workspace",
                        str(workspace),
                    ]
                    smoke = run_smoke(
                        command=command, enable_tracemalloc=False, poll_interval_seconds=0.005
                    )
                    worker_result = json.loads(
                        (workspace / f"{strategy}-{size}.verification.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    results.append(
                        {
                            "size": size,
                            "strategy": strategy,
                            "repetition": repetition,
                            "memory_smoke": smoke,
                            "worker": worker_result,
                            "verification_included_in_external_peak": True,
                        }
                    )
    report = {
        "runtime": {
            "python": platform.python_version(),
            "polars": pl.__version__,
            "numpy": np.__version__,
        },
        "command": "uv run python scripts/benchmarks/pr-227-join-benchmark.py",
        "seed": SEED,
        "chunk_rows": CHUNK_ROWS,
        "results": results,
        "output_directory": "TemporaryDirectory (removed after run)",
    }
    report_path = Path(__file__).with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
