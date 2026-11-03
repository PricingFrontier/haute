"""Fresh-process probe: the peak resident memory of one training fit.

``--mode family`` runs one canonical Haute training job for one model family the
way the training worker does: the node config goes through
``build_training_job_kwargs`` with the sunk training parquet as its data, and
``TrainingJob.run`` gets the worker's execution context, progress, iteration and
cancellation callbacks. The job therefore includes the evaluation plan, the
validation fit and the refit, metrics and diagnostics.

``--mode xgboost`` trains one native XGBoost booster on the same parquet through
one data structure: an in-memory ``QuantileDMatrix`` built from the whole frame,
a ``QuantileDMatrix`` fed by a ``DataIter`` over the parquet's row groups, or an
``ExtMemQuantileDMatrix`` fed by that iterator with its pages cached on disk
(``on_host=False``) or in host memory (``on_host=True``). It writes the
per-iteration training objective and predictions for the first rows so the
harness can compare the variants.

Each run reads its resident set after imports (the baseline) and its high-water
mark at every progress message and at the end. On Linux the mark is ``VmHWM``, not
``ru_maxrss``: Linux carries ``ru_maxrss`` over ``exec``, so a fresh child reports
its launcher's resident set until it exceeds it. On Linux a thread also samples
anonymous and file-backed resident memory, because external memory maps its cache
files and mapped pages count towards the resident set.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from haute._project_context import context_thread

#: Training data shape: 30 numeric and 5 string categorical features, a Poisson
#: frequency target and an exposure weight.
NUMERIC_FEATURES = tuple(f"x{index:02d}" for index in range(30))
CATEGORICAL_LEVELS = {"c0": 4, "c1": 8, "c2": 16, "c3": 32, "c4": 64}
CATEGORICAL_FEATURES = tuple(CATEGORICAL_LEVELS)
FEATURES = (*NUMERIC_FEATURES, *CATEGORICAL_FEATURES)
TARGET = "frequency"
WEIGHT = "exposure"
COLUMNS = (*FEATURES, WEIGHT, TARGET)
#: Rows per generated chunk, per parquet row group and per external-memory batch.
ROW_GROUP_ROWS = 200_000
DATA_SEED = 20_261_102

FAMILIES = ("catboost", "xgboost", "lightgbm", "ebm", "tboost", "glm")
#: The Modelling node's default evaluation: random split, 20% holdout validation.
EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}
#: Modest settings per family; every boosted family gets 200 rounds.
FAMILY_PARAMS: dict[str, dict[str, Any]] = {
    "catboost": {"iterations": 200, "depth": 6, "learning_rate": 0.1},
    "xgboost": {"num_boost_round": 200, "max_depth": 6, "eta": 0.1},
    "lightgbm": {
        "num_iterations": 200,
        "max_depth": 6,
        "num_leaves": 63,
        "learning_rate": 0.1,
    },
    # Haute fits an EBM with one outer bag and no early stopping.
    "ebm": {"max_rounds": 200, "interactions": 0},
    # One bag and no pruning cross-validation, as the family tests' fast settings.
    "tboost": {"n_trees": 200, "learning_rate": 0.1, "n_bags": 1, "prune": False},
}
TRAINING_THREADS = 8

XGBOOST_VARIANTS = ("in_memory", "iterator_in_memory", "external_disk", "external_host")
XGBOOST_ROUNDS = 200
XGBOOST_PARAMS: dict[str, Any] = {
    "objective": "count:poisson",
    "tree_method": "hist",
    "device": "cpu",
    "max_depth": 6,
    "eta": 0.1,
    "max_bin": 256,
    "seed": 0,
    "nthread": TRAINING_THREADS,
    "verbosity": 0,
}
XGBOOST_EVAL_METRIC = "poisson-nloglik"
PREDICTION_ROWS = 100_000


def category_levels(name: str) -> list[str]:
    return [f"{name}_{code:03d}" for code in range(CATEGORICAL_LEVELS[name])]


def node_config(family: str) -> dict[str, Any]:
    """The Modelling node config a user would save for *family* on this data."""
    config: dict[str, Any] = {
        "algorithm": family,
        "task": "regression",
        "target": TARGET,
        "weight": WEIGHT,
        "evaluation": EVALUATION,
    }
    if family == "glm":
        terms: dict[str, dict[str, str]] = {name: {"type": "linear"} for name in NUMERIC_FEATURES}
        terms.update({name: {"type": "categorical"} for name in CATEGORICAL_FEATURES})
        return {**config, "family": "poisson", "terms": terms}
    return {
        **config,
        "loss_function": "Poisson",
        "params": dict(FAMILY_PARAMS[family]),
        "feature_columns": list(FEATURES),
    }


def _chunk(chunk_index: int, rows: int) -> Any:
    import numpy as np
    import polars as pl

    rng = np.random.default_rng([DATA_SEED, chunk_index])
    normal = rng.standard_normal((rows, len(NUMERIC_FEATURES)))
    columns: dict[str, Any] = {}
    for index, name in enumerate(NUMERIC_FEATURES):
        values = normal[:, index]
        if index % 3 == 1:
            values = np.exp(0.5 * values)
        elif index % 3 == 2:
            values = np.round(values, 1)
        columns[name] = values
    # A signal in a third of the numerics (with one interaction) and in every
    # categorical, so boosting keeps improving the validation loss for many rounds.
    eta = -1.5 + 0.2 * normal[:, 6] * normal[:, 9]
    for index in range(0, len(NUMERIC_FEATURES), 3):
        eta = eta + (0.3 / (1 + index // 6)) * np.tanh(normal[:, index])
    for name, levels in CATEGORICAL_LEVELS.items():
        codes = rng.integers(0, levels, rows)
        columns[name] = pl.Series(name, category_levels(name)).gather(codes)
        eta = eta + 0.6 * np.sin(codes * 2.0 * np.pi / levels)
    exposure = rng.uniform(0.1, 1.0, rows)
    claims = rng.poisson(np.exp(eta) * exposure)
    columns[WEIGHT] = exposure
    columns[TARGET] = claims / exposure
    return pl.DataFrame(columns)


def write_training_parquet(path: Path, rows: int) -> None:
    """Write *rows* deterministic rows in row groups of ``ROW_GROUP_ROWS``."""
    import polars as pl
    import pyarrow.parquet as pq

    writer: pq.ParquetWriter | None = None
    try:
        for chunk_index, start in enumerate(range(0, rows, ROW_GROUP_ROWS)):
            table = _chunk(chunk_index, min(ROW_GROUP_ROWS, rows - start)).to_arrow(
                compat_level=pl.CompatLevel.oldest()
            )
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema)
            writer.write_table(table, row_group_size=ROW_GROUP_ROWS)
    finally:
        if writer is not None:
            writer.close()


def current_estimate(path: Path) -> dict[str, Any]:
    """What ``_estimate_peak_bytes`` predicts for this parquet as the training source.

    The row width is derived as ``estimate_safe_training_rows`` derives it: every
    column of the source trains (features, target and weight), and each column's
    width comes from the source's footer and bounded head probe.
    """
    from haute._ram_estimate import (
        _OVERHEAD_MULTIPLIER,
        _detailed_parquet_metadata,
        _estimate_base_bytes_per_row,
        _estimate_peak_bytes,
    )

    meta = _detailed_parquet_metadata(str(path))
    columns = tuple(meta.columns)
    base_bytes_per_row = _estimate_base_bytes_per_row(
        len(columns),
        target_columns=columns,
        target_width_columns=columns,
        sources=(meta,),
    )
    return {
        "rows": meta.row_count,
        "columns": len(columns),
        "base_bytes_per_row": base_bytes_per_row,
        "overhead_multiplier": _OVERHEAD_MULTIPLIER,
        "predicted_peak_bytes": _estimate_peak_bytes(
            meta.row_count,
            len(columns),
            base_bytes_per_row=base_bytes_per_row,
        ),
    }


def _status_bytes(fields: tuple[str, ...]) -> dict[str, int] | None:
    if not sys.platform.startswith("linux"):
        return None
    values: dict[str, int] = {}
    with open("/proc/self/status", encoding="utf-8") as status:
        for line in status:
            name, _, rest = line.partition(":")
            if name in fields:
                values[name] = int(rest.split()[0]) * 1024
    return values


_STATUS_FIELDS = ("VmRSS", "RssAnon", "RssFile")


class _ResidentSampler:
    """Maximum anonymous and file-backed resident memory, sampled on a thread (Linux)."""

    def __init__(self, interval_seconds: float = 0.025) -> None:
        self._interval = interval_seconds
        self._stop = threading.Event()
        self.maxima: dict[str, int] = {}
        self._thread = context_thread(self._run, daemon=True)

    def _sample(self) -> None:
        values = _status_bytes(_STATUS_FIELDS)
        for name, value in (values or {}).items():
            self.maxima[name] = max(self.maxima.get(name, 0), value)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)

    def __enter__(self) -> _ResidentSampler:
        if sys.platform.startswith("linux"):
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._thread.is_alive():
            self._stop.set()
            self._thread.join()
            self._sample()


def _baseline_rss() -> int:
    from scripts.memory_smoke import StdlibMemorySampler

    gc.collect()
    # A short pause lets the allocator settle after the imports.
    time.sleep(0.2)
    rss = StdlibMemorySampler().process_rss_bytes(os.getpid())
    assert rss is not None
    return rss


def _peak_rss() -> int:
    status = _status_bytes(("VmHWM",))
    if status is not None:
        return status["VmHWM"]
    from scripts.memory_smoke import StdlibMemorySampler

    peak = StdlibMemorySampler().process_peak_rss_bytes()
    assert peak is not None
    return peak


class _StageLog:
    """The high-water mark at each progress message, to name the stage that set it."""

    def __init__(self, started: float) -> None:
        self._started = started
        self.entries: list[dict[str, Any]] = []
        self._stage = "start"

    def mark(self, message: str) -> None:
        self.entries.append(
            {
                "stage": self._stage,
                "ended_at_seconds": round(time.perf_counter() - self._started, 3),
                "peak_rss_bytes_at_end": _peak_rss(),
            }
        )
        self._stage = message

    def peak_stage(self, final_peak: int) -> str:
        """The stage during which the high-water mark first reached its final value."""
        for entry in self.entries:
            if entry["peak_rss_bytes_at_end"] >= final_peak:
                return str(entry["stage"])
        return self._stage


def _run_family(family: str, data: Path, work: Path) -> dict[str, Any]:
    import importlib

    from haute._execution_context import ExecutionContext, ExecutionProfile
    from haute.modelling import TrainingJob
    from haute.modelling._descriptors import algorithm_descriptor
    from haute.modelling._train_config import build_training_job_kwargs

    importlib.import_module(algorithm_descriptor(family).engine_module)
    baseline = _baseline_rss()
    baseline_peak = _peak_rss()

    job_kwargs = build_training_job_kwargs(node_config(family), data=str(data))
    job_kwargs["output_dir"] = str(work / "output")
    # The worker's context: the training-prep profile, no memory limit given.
    context = ExecutionContext(
        operation="training_job",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="training-family-memory-probe",
        memory_baseline_bytes=baseline,
    )
    started = time.perf_counter()
    stages = _StageLog(started)
    rounds = 0

    def progress(message: str, _fraction: float) -> None:
        context.checkpoint(label="training_progress")
        stages.mark(message)

    def iteration(*_args: object) -> None:
        nonlocal rounds
        context.checkpoint(label="training_iteration")
        rounds += 1

    with _ResidentSampler() as sampler:
        job = TrainingJob(**job_kwargs)
        result = job.run(
            progress,
            iteration,
            check_cancelled=lambda: context.checkpoint(label="training_cancel_checkpoint"),
            execution_context=context,
        )
        wall_seconds = time.perf_counter() - started
        stages.mark("done")
    peak = _peak_rss()
    return {
        "family": family,
        "baseline_rss_bytes": baseline,
        "baseline_peak_rss_bytes": baseline_peak,
        "peak_rss_bytes": peak,
        "peak_stage": stages.peak_stage(peak),
        "stages": stages.entries,
        "sampled_maxima_bytes": sampler.maxima,
        "wall_seconds": wall_seconds,
        "train_rows": result.train_rows,
        "validation_rows": result.validation_rows,
        "iteration_callbacks": rounds,
        "fit_evidence": result.fit_evidence,
        "diagnostics_errors": list(result.diagnostics_errors or []),
    }


def _xgboost_inputs(frame: Any) -> tuple[Any, Any, Any]:
    """A pandas frame of float32 numerics and fixed-category codes, label and weight."""
    import numpy as np
    import pandas as pd
    import polars as pl

    columns: dict[str, Any] = {
        name: frame.get_column(name).to_numpy().astype(np.float32, copy=False)
        for name in NUMERIC_FEATURES
    }
    for name in CATEGORICAL_FEATURES:
        levels = category_levels(name)
        codes = frame.get_column(name).cast(pl.Enum(levels)).to_physical().to_numpy()
        columns[name] = pd.Categorical.from_codes(codes, categories=levels)
    return (
        pd.DataFrame(columns, columns=list(FEATURES)),
        frame.get_column(TARGET).to_numpy(),
        frame.get_column(WEIGHT).to_numpy(),
    )


def _parquet_batches(data: Path) -> Callable[[], Iterator[Any]]:
    import polars as pl
    import pyarrow.parquet as pq

    def batches() -> Iterator[Any]:
        with pq.ParquetFile(data) as parquet:
            for batch in parquet.iter_batches(batch_size=ROW_GROUP_ROWS, columns=list(COLUMNS)):
                frame = pl.from_arrow(batch)
                assert isinstance(frame, pl.DataFrame)
                yield frame

    return batches


def _xgboost_matrix(variant: str, data: Path, work: Path) -> Any:
    import polars as pl
    import xgboost as xgb

    class ParquetBatches(xgb.DataIter):
        """One parquet row group per batch, re-read from disk on every pass."""

        def __init__(self, *, cache_prefix: str | None, on_host: bool) -> None:
            self._open = _parquet_batches(data)
            self._batches: Iterator[Any] | None = None
            super().__init__(cache_prefix=cache_prefix, release_data=True, on_host=on_host)

        def reset(self) -> None:
            self._batches = None

        def next(self, input_data: Callable[..., None]) -> bool:
            if self._batches is None:
                self._batches = self._open()
            frame = next(self._batches, None)
            if frame is None:
                return False
            x, label, weight = _xgboost_inputs(frame)
            input_data(data=x, label=label, weight=weight)
            return True

    common = {
        "max_bin": XGBOOST_PARAMS["max_bin"],
        "nthread": TRAINING_THREADS,
        "enable_categorical": True,
    }
    if variant == "in_memory":
        frame = pl.read_parquet(data, columns=list(COLUMNS))
        x, label, weight = _xgboost_inputs(frame)
        del frame
        return xgb.QuantileDMatrix(x, label=label, weight=weight, **common)
    if variant == "iterator_in_memory":
        return xgb.QuantileDMatrix(ParquetBatches(cache_prefix=None, on_host=False), **common)
    cache = work / "xgboost-cache"
    cache.mkdir()
    iterator = ParquetBatches(
        cache_prefix=str(cache / "cache"),
        on_host=variant == "external_host",
    )
    return xgb.ExtMemQuantileDMatrix(iterator, **common)


def _run_xgboost(variant: str, data: Path, work: Path) -> dict[str, Any]:
    import numpy as np
    import pandas  # noqa: F401  - imported before the baseline with the engine
    import polars as pl
    import pyarrow.parquet  # noqa: F401
    import xgboost as xgb

    baseline = _baseline_rss()
    baseline_peak = _peak_rss()
    started = time.perf_counter()
    stages = _StageLog(started)
    history: dict[str, dict[str, list[float]]] = {}
    with _ResidentSampler() as sampler:
        matrix = _xgboost_matrix(variant, data, work)
        matrix_seconds = time.perf_counter() - started
        stages.mark("train")
        booster = xgb.train(
            XGBOOST_PARAMS,
            matrix,
            num_boost_round=XGBOOST_ROUNDS,
            evals=[(matrix, "train")],
            evals_result=history,
            verbose_eval=False,
        )
        wall_seconds = time.perf_counter() - started
        stages.mark("predict")
    peak = _peak_rss()
    cache_files = sorted((work / "xgboost-cache").glob("*"))
    cache_bytes = sum(path.stat().st_size for path in cache_files)
    del matrix
    gc.collect()
    for path in cache_files:
        path.unlink(missing_ok=True)
    head = pl.scan_parquet(data).select(COLUMNS).head(PREDICTION_ROWS).collect()
    x, _label, _weight = _xgboost_inputs(head)
    predictions = booster.predict(xgb.DMatrix(x, enable_categorical=True))
    np.save(work / "predictions.npy", np.asarray(predictions, dtype=np.float64))
    return {
        "variant": variant,
        "baseline_rss_bytes": baseline,
        "baseline_peak_rss_bytes": baseline_peak,
        "peak_rss_bytes": peak,
        "peak_stage": stages.peak_stage(peak),
        "stages": stages.entries,
        "sampled_maxima_bytes": sampler.maxima,
        "matrix_seconds": matrix_seconds,
        "wall_seconds": wall_seconds,
        "rounds": int(booster.num_boosted_rounds()),
        "objective_history": history["train"][XGBOOST_EVAL_METRIC],
        "cache_file_bytes": cache_bytes,
        "predictions_path": str(work / "predictions.npy"),
        "xgboost_version": xgb.__version__,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("family", "xgboost"), required=True)
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--variant", choices=XGBOOST_VARIANTS)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import polars  # noqa: F401  - imported before the baseline, as the worker has it

    if args.mode == "family":
        payload = _run_family(args.family, args.data, args.work)
    else:
        payload = _run_xgboost(args.variant, args.data, args.work)
    # ``--output`` is supplied from pytest's tmp_path by the parent harness.
    args.output.write_text(  # write-sandbox: deliberate
        json.dumps({"schema_version": 1, **payload}, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
