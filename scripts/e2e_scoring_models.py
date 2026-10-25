"""Train small saved models for the Model Scoring browser journeys.

Seeds an EXISTING e2e project (the ``.tmp-e2e-project`` the browser harness
scaffolds) with model files and their feature contracts under
``rating/files/models``, the folder Model Training's **Save model to file**
writes. ``frontend/e2e/model-scoring.spec.ts`` then scores them through a
file-sourced Model Scoring node without training in the browser; the modelling
journeys score the models they train themselves. Every model trains through the same
``build_training_job_kwargs`` -> ``TrainingJob`` path the editor uses, on rows
shaped like the project's ``raw_rows`` sample (``proposer_age``, ``channel``,
``vehicle_age``, ``mileage``), with ``channel`` holding only ``direct`` and
``broker``.

CLI (once per project reset; ``git clean`` between browser tests removes the
models):

    uv run python scripts/e2e_scoring_models.py --seed <project-dir> --models glm,classifier

Prints a JSON summary mapping each model name to its ``model_path`` relative
to the pipeline directory (``rating``), as a Model Scoring node's file source
spells it.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute.modelling._train_config import build_training_job_kwargs
from haute.modelling._training_job import TrainingJob

FEATURES = ["proposer_age", "channel", "vehicle_age", "mileage"]
EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}

#: Each regression family's config beyond the shared target, features and evaluation.
REGRESSION_CONFIGS: Mapping[str, Mapping[str, Any]] = {
    "glm": {
        "family": "gaussian",
        "terms": {
            "channel": {"type": "categorical"},
            "proposer_age": {"type": "linear"},
            "vehicle_age": {"type": "linear"},
            "mileage": {"type": "linear"},
        },
    },
    "xgboost": {
        "loss_function": "RMSE",
        "params": {"num_boost_round": 20, "eta": 0.3, "max_depth": 3},
    },
}
#: A binary CatBoost classifier for the probability column.
CLASSIFIER_CONFIG: Mapping[str, Any] = {
    "algorithm": "catboost",
    "target": "flag",
    "task": "classification",
    "positive_class": "yes",
    "loss_function": "Logloss",
    "params": {"iterations": 30, "depth": 3},
    "metrics": ["auc"],
}


def training_rows(n: int = 600, seed: int = 0) -> pl.DataFrame:
    """Rows with the sample's columns and dtypes, enough for each model to fit."""
    rng = np.random.default_rng(seed)
    age = rng.integers(18, 80, n)
    channel = rng.choice(["direct", "broker"], n)
    vehicle_age = rng.integers(0, 15, n)
    mileage = rng.uniform(3_000, 30_000, n)
    value = (
        10.0
        + 0.2 * age
        + np.where(channel == "broker", 5.0, 0.0)
        - 0.3 * vehicle_age
        + mileage / 5_000
        + rng.normal(0.0, 1.0, n)
    )
    return pl.DataFrame(
        {
            "proposer_age": pl.Series(age, dtype=pl.Int64),
            "channel": channel.tolist(),
            "vehicle_age": pl.Series(vehicle_age, dtype=pl.Int64),
            "mileage": mileage,
            "value": value,
            "flag": np.where(value > np.median(value), "yes", "no").tolist(),
        }
    )


def train(project: Path, name: str, config: Mapping[str, Any], data: str) -> str:
    """Train *name* into ``rating/files/models`` and return its pipeline-relative path."""
    pipeline_dir = project / "rating"
    output_dir = pipeline_dir / "files" / "models"
    full_config = {
        "name": name,
        "target": "value",
        "algorithm": name.removeprefix("score_"),
        "task": "regression",
        "feature_columns": FEATURES,
        "evaluation": EVALUATION,
        "metrics": ["rmse"],
        "output_dir": str(output_dir),
        **config,
    }
    result = TrainingJob(**build_training_job_kwargs(full_config, data=data)).run()
    return Path(result.model_path).relative_to(pipeline_dir).as_posix()


def seed(project: Path, models: list[str]) -> dict[str, str]:
    scratch = project / ".haute_cache" / "e2e_scoring"
    scratch.mkdir(parents=True, exist_ok=True)
    data = scratch / "training_rows.parquet"
    training_rows().write_parquet(data)
    paths: dict[str, str] = {}
    for model in models:
        if model == "classifier":
            paths["score_classifier"] = train(
                project, "score_classifier", CLASSIFIER_CONFIG, str(data)
            )
        else:
            paths[f"score_{model}"] = train(
                project, f"score_{model}", REGRESSION_CONFIGS[model], str(data)
            )
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True, help="The e2e project directory.")
    parser.add_argument(
        "--models",
        required=True,
        help="Comma-separated: any of " + ", ".join([*REGRESSION_CONFIGS, "classifier"]),
    )
    args = parser.parse_args()
    models = [model.strip() for model in args.models.split(",") if model.strip()]
    unknown = sorted(set(models) - {*REGRESSION_CONFIGS, "classifier"})
    if unknown:
        parser.error(f"unknown models: {', '.join(unknown)}")
    print(json.dumps(seed(args.seed.resolve(), models), indent=2))


if __name__ == "__main__":
    main()
