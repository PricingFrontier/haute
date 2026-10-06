"""MOD-F06 GPU probes for XGBoost (CUDA) and LightGBM (OpenCL / CUDA).

Fits real models on the GPU and records what a GPU capability in Haute would
depend on: which builds and backends exist, which objectives and constraints
run on the device, how far GPU predictions sit from CPU ones, whether a
GPU-trained artifact scores identically on a CPU-only install, whether a
progress callback can cancel a device fit, and peak device memory.

Run from the repository root in an environment with a CUDA XGBoost build (the
full ``xgboost`` wheel, not ``xgboost-cpu``) and LightGBM::

    python scripts/benchmarks/mod-f06-gpu-probes.py OUTPUT.json [CPU_PYTHON]

``CPU_PYTHON`` is an interpreter with only ``xgboost-cpu`` installed; when
given, GPU-trained XGBoost models are re-scored there to prove CPU serving.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESULTS: dict[str, Any] = {}
LEVELS = [f"l{i:02d}" for i in range(30)]


def record(section: str, key: str, value: Any) -> None:
    if isinstance(value, np.generic):
        value = value.item()
    RESULTS.setdefault(section, {})[key] = value


def max_rel(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return float(np.max(np.abs(a - b) / np.maximum(1.0, np.abs(b))))


class DeviceMemory:
    """Peak device memory in MiB over a block, sampled with ``nvidia-smi``."""

    def __enter__(self) -> DeviceMemory:
        self.start = self._used()
        self.peak = self.start
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    @staticmethod
    def _used() -> int:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            return int(out.strip().splitlines()[0])
        except (OSError, subprocess.CalledProcessError, ValueError):
            return -1

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            self.peak = max(self.peak, self._used())

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    @property
    def delta(self) -> int:
        return self.peak - self.start


def dataset(rows: int, seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    numeric = rng.normal(size=(rows, 20))
    region = rng.choice(LEVELS, rows)
    exposure = rng.uniform(0.1, 1.0, rows)
    weight = rng.uniform(0.5, 2.0, rows)
    rate = np.exp(-2 + 0.3 * numeric[:, 0] + np.where(region == "l03", 0.4, 0.0))
    claims = rng.poisson(rate * exposure).astype(float)
    severity = rng.gamma(2.0, 500 * np.exp(0.1 * numeric[:, 1]))
    flag = (rng.uniform(size=rows) < 1 / (1 + np.exp(-numeric[:, 2]))).astype(float)
    frame = pd.DataFrame(numeric, columns=[f"x{i:02d}" for i in range(20)])
    frame["region"] = pd.Categorical(region, categories=LEVELS)
    return {
        "X": frame,
        "exposure": exposure,
        "weight": weight,
        "claims": claims,
        "severity": severity,
        "flag": flag,
    }


XGB_OBJECTIVES = {
    "reg:squarederror": ("severity", False),
    "reg:absoluteerror": ("severity", False),
    "count:poisson": ("claims", True),
    "reg:gamma": ("severity", False),
    "reg:tweedie": ("claims", True),
    "binary:logistic": ("flag", False),
}


def probe_xgboost(train: dict, valid: dict, work: Path, cpu_python: str | None) -> None:
    import xgboost as xgb

    info = xgb.build_info()
    record("xgboost", "version", xgb.__version__)
    record("xgboost", "use_cuda", bool(info.get("USE_CUDA")))
    record("xgboost", "cuda_version", info.get("CUDA_VERSION"))
    if not info.get("USE_CUDA"):
        return

    def matrix(data: dict, target: str, offset: bool) -> Any:
        return xgb.DMatrix(
            data["X"],
            label=data[target],
            weight=data["weight"],
            base_margin=np.log(data["exposure"]) if offset else None,
            enable_categorical=True,
        )

    monotone = tuple([1] + [0] * 20)
    for objective, (target, offset) in XGB_OBJECTIVES.items():
        dtrain, dvalid = matrix(train, target, offset), matrix(valid, target, offset)
        params = {
            "objective": objective,
            "tree_method": "hist",
            "max_depth": 6,
            "eta": 0.1,
            "seed": 0,
            "monotone_constraints": monotone,
            **({"tweedie_variance_power": 1.5} if objective == "reg:tweedie" else {}),
        }
        entry: dict[str, Any] = {}
        boosters = {}
        for device in ("cpu", "cuda"):
            try:
                with DeviceMemory() as memory:
                    started = time.perf_counter()
                    booster = xgb.train(
                        {**params, "device": device},
                        dtrain,
                        num_boost_round=400,
                        evals=[(dvalid, "validation")],
                        early_stopping_rounds=20,
                        verbose_eval=False,
                    )
                    entry[f"{device}_seconds"] = round(time.perf_counter() - started, 2)
                entry[f"{device}_best_iteration"] = int(booster.best_iteration)
                if device == "cuda":
                    entry["device_peak_mib"] = memory.delta
                boosters[device] = booster[: booster.best_iteration + 1]
            except xgb.core.XGBoostError as exc:
                entry[f"{device}_error"] = str(exc).splitlines()[0][:200]
        if "cuda" in boosters:
            gpu = boosters["cuda"]
            gpu.set_param({"device": "cpu"})
            on_cpu = gpu.predict(dvalid)
            gpu.set_param({"device": "cuda"})
            on_gpu = gpu.predict(dvalid)
            entry["gpu_model_cpu_vs_gpu_predict_max_rel"] = max_rel(on_gpu, on_cpu)
            entry["gpu_vs_cpu_trained_max_rel"] = max_rel(on_cpu, boosters["cpu"].predict(dvalid))
            path = work / f"xgb_{objective.replace(':', '_')}.ubj"
            gpu.save_model(str(path))
            np.save(path.with_suffix(".npy"), on_cpu)
            entry["artifact"] = str(path)
        record("xgboost_objectives", objective, entry)

    # Cancellation: a callback raising mid-fit stops a device fit.
    class Cancel(xgb.callback.TrainingCallback):
        def after_iteration(self, model: Any, epoch: int, evals_log: Any) -> bool:
            if epoch == 5:
                raise KeyboardInterrupt("cancelled")
            return False

    dtrain = matrix(train, "severity", False)
    try:
        xgb.train(
            {"objective": "reg:squarederror", "tree_method": "hist", "device": "cuda"},
            dtrain,
            num_boost_round=500,
            callbacks=[Cancel()],
        )
        record("xgboost", "callback_cancels_device_fit", False)
    except KeyboardInterrupt:
        record("xgboost", "callback_cancels_device_fit", True)

    # Whether a device that does not exist fails loudly (recorded False on a
    # one-GPU machine: XGBoost trains anyway).
    try:
        xgb.train({"device": "cuda:7", "tree_method": "hist"}, dtrain, num_boost_round=2)
        record("xgboost", "missing_device_fails", False)
    except xgb.core.XGBoostError as exc:
        record("xgboost", "missing_device_fails", True)
        record("xgboost", "missing_device_error", str(exc).splitlines()[0][:200])

    if cpu_python:
        record("xgboost", "cpu_serving", _cpu_serving(cpu_python, work, valid))


def _cpu_serving(cpu_python: str, work: Path, valid: dict) -> dict[str, Any]:
    """Re-score each GPU-trained artifact under a CPU-only XGBoost install."""
    frame_path = work / "valid.parquet"
    frame = valid["X"].copy()
    frame["exposure"] = valid["exposure"]
    frame.to_parquet(frame_path)
    script = f"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd, xgboost as xgb
frame = pd.read_parquet({str(frame_path)!r})
offset = np.log(frame.pop("exposure").to_numpy())
out = {{"build_use_cuda": bool(xgb.build_info().get("USE_CUDA"))}}
for path in sorted(Path({str(work)!r}).glob("xgb_*.ubj")):
    booster = xgb.Booster()
    booster.load_model(str(path))
    uses_offset = "poisson" in path.name or "tweedie" in path.name
    margin = offset if uses_offset else None
    matrix = xgb.DMatrix(frame, base_margin=margin, enable_categorical=True)
    expected = np.load(path.with_suffix(".npy"))
    got = booster.predict(matrix)
    out[path.stem] = float(np.max(np.abs(got - expected) / np.maximum(1.0, np.abs(expected))))
print(json.dumps(out))
"""
    completed = subprocess.run(
        [cpu_python, "-c", script], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip().splitlines()[-1][:300]}
    return json.loads(completed.stdout.strip().splitlines()[-1])


LGB_OBJECTIVES = {
    "regression": ("severity", False),
    "regression_l1": ("severity", False),
    "poisson": ("claims", True),
    "gamma": ("severity", False),
    "tweedie": ("claims", True),
    "binary": ("flag", False),
}


def probe_lightgbm(train: dict, valid: dict) -> None:
    import lightgbm as lgb

    record("lightgbm", "version", lgb.__version__)

    def dataset_for(data: dict, target: str, offset: bool, reference: Any = None) -> Any:
        return lgb.Dataset(
            data["X"],
            label=data[target],
            weight=data["weight"],
            init_score=np.log(data["exposure"]) if offset else None,
            categorical_feature=["region"],
            reference=reference,
            free_raw_data=False,
        )

    backends = {}
    for device in ("gpu", "cuda"):
        try:
            lgb.train(
                {"objective": "regression", "device_type": device, "verbosity": -1},
                dataset_for(train, "severity", False),
                num_boost_round=2,
            )
            backends[device] = "available"
        except lgb.basic.LightGBMError as exc:
            backends[device] = str(exc).splitlines()[0][:200]
    record("lightgbm", "backends", backends)
    if backends.get("gpu") != "available":
        return

    for objective, (target, offset) in LGB_OBJECTIVES.items():
        dtrain = dataset_for(train, target, offset)
        dvalid = dataset_for(valid, target, offset, reference=dtrain)
        base = {
            "objective": objective,
            "learning_rate": 0.1,
            "num_leaves": 31,
            "seed": 0,
            "verbosity": -1,
            "max_bin": 63,
            **({"tweedie_variance_power": 1.5} if objective == "tweedie" else {}),
        }
        if objective != "regression_l1":
            base["monotone_constraints"] = [1] + [0] * 20
        entry: dict[str, Any] = {}
        preds = {}
        for label, extra in (
            ("cpu", {"device_type": "cpu"}),
            ("gpu", {"device_type": "gpu"}),
            ("gpu_dp", {"device_type": "gpu", "gpu_use_dp": True}),
        ):
            try:
                started = time.perf_counter()
                booster = lgb.train(
                    {**base, **extra},
                    dtrain,
                    num_boost_round=400,
                    valid_sets=[dvalid],
                    callbacks=[lgb.early_stopping(20, verbose=False)],
                )
                entry[f"{label}_seconds"] = round(time.perf_counter() - started, 2)
                entry[f"{label}_best_iteration"] = int(booster.best_iteration)
                preds[label] = booster.predict(
                    valid["X"], raw_score=True, num_iteration=booster.best_iteration
                )
                if label == "gpu":
                    # The saved model text is device-independent: reload and score.
                    reloaded = lgb.Booster(model_str=booster.model_to_string())
                    entry["gpu_model_reload_max_rel"] = max_rel(
                        reloaded.predict(
                            valid["X"], raw_score=True, num_iteration=booster.best_iteration
                        ),
                        preds[label],
                    )
            except lgb.basic.LightGBMError as exc:
                entry[f"{label}_error"] = str(exc).splitlines()[0][:200]
        for label in ("gpu", "gpu_dp"):
            if label in preds and "cpu" in preds:
                entry[f"{label}_vs_cpu_max_rel"] = max_rel(preds[label], preds["cpu"])
        record("lightgbm_objectives", objective, entry)

    dtrain = dataset_for(train, "severity", False)

    def cancel(env: Any) -> None:
        if env.iteration == 5:
            raise KeyboardInterrupt("cancelled")

    try:
        lgb.train(
            {"objective": "regression", "device_type": "gpu", "verbosity": -1},
            dtrain,
            num_boost_round=500,
            callbacks=[cancel],
        )
        record("lightgbm", "callback_cancels_device_fit", False)
    except KeyboardInterrupt:
        record("lightgbm", "callback_cancels_device_fit", True)
    try:
        lgb.train(
            {"objective": "regression", "device_type": "gpu", "gpu_device_id": 7, "verbosity": -1},
            dtrain,
            num_boost_round=2,
        )
        record("lightgbm", "missing_device_fails", False)
    except lgb.basic.LightGBMError as exc:
        record("lightgbm", "missing_device_fails", True)
        record("lightgbm", "missing_device_error", str(exc).splitlines()[0][:200])


def main(output: Path, cpu_python: str | None) -> None:
    RESULTS["environment"] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gpu": subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
    }
    train, valid = dataset(200_000, seed=0), dataset(50_000, seed=1)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        probe_xgboost(train, valid, work, cpu_python)
        probe_lightgbm(train, valid)
    output.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print(json.dumps(RESULTS, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
