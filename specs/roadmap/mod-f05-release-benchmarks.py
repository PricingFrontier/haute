"""MOD-F05 release benchmarks for the XGBoost, LightGBM and EBM families.

Fits each new family through its Haute adapter on representative shapes: a wide
numeric frame, a categorical-heavy frame with an exposure offset, and EBM with
growing interaction counts. Records fit and scoring time, peak resident memory
above the pre-fit baseline, and the saved model's size (which bounds what an
``.ebm`` joblib file holds).

Run from the repository root with Haute's dependencies installed::

    PYTHONPATH=src python specs/roadmap/mod-f05-release-benchmarks.py OUTPUT.json
"""

from __future__ import annotations

import gc
import json
import os
import platform
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute._execution_context import current_rss_bytes
from haute.modelling._ebm import EBMAlgorithm
from haute.modelling._lightgbm import LightGBMAlgorithm
from haute.modelling._xgboost import XGBoostAlgorithm

THREADS = int(os.environ.get("HAUTE_TRAINING_THREADS", os.cpu_count() or 1))
ROUNDS = {"xgboost": 200, "lightgbm": 200, "ebm": 300}
ROUND_KEY = {"xgboost": "num_boost_round", "lightgbm": "num_iterations", "ebm": "max_rounds"}
SUFFIX = {"xgboost": ".ubj", "lightgbm": ".lgbm", "ebm": ".ebm"}
ADAPTERS = {"xgboost": XGBoostAlgorithm, "lightgbm": LightGBMAlgorithm, "ebm": EBMAlgorithm}


class PeakRss:
    """Samples resident memory on a thread; reports the peak above the start."""

    def __enter__(self) -> PeakRss:
        gc.collect()
        self.start = current_rss_bytes() or 0
        self.peak = self.start
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.wait(0.05):
            self.peak = max(self.peak, current_rss_bytes() or 0)

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, current_rss_bytes() or 0)

    @property
    def delta_mb(self) -> float:
        return round((self.peak - self.start) / 2**20, 1)


def wide_frame(rows: int, features: int, seed: int = 0) -> tuple[pl.DataFrame, list[str]]:
    rng = np.random.default_rng(seed)
    data = rng.normal(size=(rows, features))
    names = [f"x{i:03d}" for i in range(features)]
    signal = data[:, :10] @ rng.normal(size=10)
    frame = pl.DataFrame(data, schema=names).with_columns(
        pl.Series("y", signal + rng.normal(size=rows))
    )
    return frame, names


def categorical_frame(rows: int, seed: int = 0) -> tuple[pl.DataFrame, list[str], list[str]]:
    rng = np.random.default_rng(seed)
    numeric = {f"n{i}": rng.normal(size=rows) for i in range(10)}
    cardinalities = [50] * 19 + [1000]
    categorical = {
        f"c{i:02d}": rng.integers(0, size, rows).astype(str).astype(object)
        for i, size in enumerate(cardinalities)
    }
    exposure = rng.uniform(0.1, 1.0, rows)
    effect = sum(np.where(categorical[f"c{i:02d}"] == "3", 0.3, 0.0) for i in range(5))
    rate = np.exp(-2 + 0.2 * numeric["n0"] + effect)
    frame = pl.DataFrame(
        {
            **numeric,
            **{name: values.tolist() for name, values in categorical.items()},
            "exposure": exposure,
            "claims": rng.poisson(rate * exposure).astype(float),
        }
    )
    return frame, [*numeric, *categorical], list(categorical)


def run_case(
    family: str,
    frame: pl.DataFrame,
    features: list[str],
    cat_features: list[str],
    *,
    target: str,
    loss: str,
    offset: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {ROUND_KEY[family]: ROUNDS[family], **(extra_params or {})}
    algorithm = ADAPTERS[family]()
    with PeakRss() as memory:
        started = time.perf_counter()
        fit = algorithm.fit(
            frame,
            features,
            cat_features,
            target,
            None,
            params,
            "regression",
            offset=offset,
            loss=loss,
            threads=THREADS,
            seed=0,
        )
        fit_seconds = time.perf_counter() - started
    started = time.perf_counter()
    fit.model.predict(frame)
    score_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"model{SUFFIX[family]}"
        algorithm.save(fit.model, path)
        size_mb = round(path.stat().st_size / 2**20, 2)
    del fit
    gc.collect()
    return {
        "fit_seconds": round(fit_seconds, 2),
        "score_seconds": round(score_seconds, 2),
        "peak_rss_delta_mb": memory.delta_mb,
        "model_size_mb": size_mb,
        "rows": frame.height,
        "features": len(features),
        "params": params,
    }


def repeated_loads(
    frame: pl.DataFrame, features: list[str], cat_features: list[str], loads: int = 30
) -> dict[str, Any]:
    """Resident memory after one load versus after many: a leak grows with each load."""
    from haute._mlflow_io import load_local_model
    from haute.modelling._feature_contract import build_contract, save_contract
    from haute.modelling._training_job import model_contract_filename

    measured: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as directory:
        for family in ("xgboost", "lightgbm", "ebm"):
            algorithm = ADAPTERS[family]()
            params: dict[str, Any] = {ROUND_KEY[family]: 50}
            if family == "ebm":
                params["interactions"] = 5
            fit = algorithm.fit(
                frame,
                features,
                cat_features,
                "claims",
                None,
                params,
                "regression",
                offset="exposure",
                loss="Poisson",
                threads=THREADS,
                seed=0,
            )
            path = Path(directory) / f"repeat{SUFFIX[family]}"
            algorithm.save(fit.model, path)
            if family == "ebm":
                import interpret

                from haute.modelling._feature_contract import ModelIdentity

                save_contract(
                    build_contract(
                        features=list(features),
                        feature_types={
                            name: "String" if name in cat_features else "Float64"
                            for name in features
                        },
                        categorical_features=list(cat_features),
                        target_name="claims",
                        target_type="Float64",
                        task="regression",
                        categorical_levels=fit.categorical_levels,
                        offset_column="exposure",
                        model=ModelIdentity(
                            algorithm="ebm",
                            link="log",
                            engine_name="interpret",
                            engine_version=interpret.__version__,
                            haute_version="benchmark",
                            loss="Poisson",
                        ),
                    ),
                    Path(directory) / model_contract_filename("repeat"),
                )
            del fit
            load_local_model(str(path))
            gc.collect()
            after_one = current_rss_bytes() or 0
            for _ in range(loads):
                load_local_model(str(path))
            gc.collect()
            after_many = current_rss_bytes() or 0
            measured[family] = {
                "loads": loads,
                "rss_growth_mb": round((after_many - after_one) / 2**20, 1),
            }
    return measured


def main(output: Path) -> None:
    results: dict[str, Any] = {
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "threads": THREADS,
        },
        "wide": {},
        "categorical": {},
        "ebm_interactions": {},
    }
    wide, wide_names = wide_frame(50_000, 200)
    for family in ("xgboost", "lightgbm", "ebm"):
        print("wide", family, flush=True)
        results["wide"][family] = run_case(
            family,
            wide,
            wide_names,
            [],
            target="y",
            loss="RMSE",
            extra_params={"interactions": 0} if family == "ebm" else None,
        )
    del wide
    categorical, names, cats = categorical_frame(100_000)
    for family in ("xgboost", "lightgbm", "ebm"):
        print("categorical", family, flush=True)
        results["categorical"][family] = run_case(
            family,
            categorical,
            names,
            cats,
            target="claims",
            loss="Poisson",
            offset="exposure",
            extra_params={"interactions": 0} if family == "ebm" else None,
        )
    interaction_frame, interaction_names, interaction_cats = categorical_frame(50_000, seed=1)
    subset = interaction_names[:20] + interaction_names[-5:]
    subset_cats = [name for name in subset if name in interaction_cats]
    for count in (0, 10, 40):
        print("ebm interactions", count, flush=True)
        results["ebm_interactions"][str(count)] = run_case(
            "ebm",
            interaction_frame,
            subset,
            subset_cats,
            target="claims",
            loss="Poisson",
            offset="exposure",
            extra_params={"interactions": count},
        )
    results["repeated_loads"] = repeated_loads(interaction_frame, subset, subset_cats)
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
