"""LightGBM adapter: native GBDT boosters with Haute's data and prediction contract.

The saved ``.lgbm`` file is LightGBM's own model text with one extra ``haute:``
line (before LightGBM's ``pandas_categorical`` line, which its loader ignores)
recording the feature order, the categorical levels, the task and link, the
offset column and how it enters the margin, and the binary class labels, so the
file stays loadable by plain LightGBM while scoring stays self-describing.
LightGBM's ``predict`` ignores ``init_score`` (MOD-F00 probe), so
:class:`LightGBMModel` adds the transformed offset to the raw score itself,
exactly once, before the inverse link. Early-stopped models are rebuilt with
only their best iteration, so the fitted and the saved model are the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute.errors import HauteValidationError
from haute.modelling._algorithm_base import (
    BaseAlgorithm,
    Contributions,
    FitResult,
    IterationCallback,
)
from haute.modelling._native_encoding import encode_frame, fit_categorical_levels

_META_PREFIX = "haute:"
_DEFAULT_ROUNDS = 1000
_DEFAULT_EARLY_STOPPING = 50


def _inverse_link(link: str, margin: np.ndarray) -> np.ndarray:
    if link == "log":
        return np.asarray(np.exp(margin))
    if link == "logit":
        return np.asarray(1.0 / (1.0 + np.exp(-margin)))
    return margin


@dataclass
class LightGBMModel:
    """A booster plus the record every prediction path needs."""

    booster: Any
    features: list[str]
    categorical_levels: dict[str, list[str | None]]
    task: str
    link: str
    offset_column: str | None = None
    offset_link: str | None = None
    class_labels: tuple[Any, Any] | None = None
    threads: int | None = field(default=None, compare=False)

    # -- ScoringModel-facing surface ------------------------------------
    @property
    def feature_names_(self) -> list[str]:
        return list(self.features)

    @property
    def cat_feature_names(self) -> frozenset[str]:
        return frozenset(self.categorical_levels)

    def encoded(self, frame: pl.DataFrame) -> Any:
        """Features as pandas with contract-order categoricals."""
        return encode_frame(frame, self.features, self.categorical_levels, context="LightGBM")

    def baseline(self, frame: pl.DataFrame) -> np.ndarray | None:
        """The transformed offset each row's raw score starts from."""
        if self.offset_column is None:
            return None
        from haute.modelling._algorithms import _extract_offset_baseline

        return _extract_offset_baseline(
            frame, self.offset_column, link=str(self.offset_link), context="LightGBM"
        )

    def predict_margin(self, frame: pl.DataFrame) -> np.ndarray:
        """Raw scores including the offset (log / log-odds scale for those links)."""
        raw = np.asarray(
            self.booster.predict(self.encoded(frame), raw_score=True, num_threads=self._threads()),
            dtype=np.float64,
        )
        baseline = self.baseline(frame)
        return raw if baseline is None else raw + baseline

    def predict_response(self, frame: pl.DataFrame) -> np.ndarray:
        """Response-scale predictions; the positive-class probability for classifiers."""
        return np.asarray(_inverse_link(self.link, self.predict_margin(frame)), dtype=np.float64)

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        """Served predictions: responses, or original labels for classifiers."""
        response = self.predict_response(frame)
        if self.task != "classification":
            return response
        from haute._mlflow_io import binary_labels

        if self.class_labels is None:
            raise HauteValidationError("LightGBM classifier has no recorded class labels")
        return binary_labels(response, self.class_labels)

    def predict_proba(self, frame: pl.DataFrame) -> np.ndarray:
        if self.task != "classification":
            raise HauteValidationError("LightGBM regression models have no class probabilities")
        positive = self.predict_response(frame)
        return np.column_stack([1.0 - positive, positive])

    def contributions(self, frame: pl.DataFrame) -> Contributions:
        """Native ``pred_contrib``; the adapter adds the offset to the bias column."""
        raw = np.asarray(
            self.booster.predict(
                self.encoded(frame), pred_contrib=True, num_threads=self._threads()
            ),
            dtype=np.float64,
        )
        bias = raw[:, -1].copy()
        baseline = self.baseline(frame)
        if baseline is not None:
            bias = bias + baseline
        return Contributions(
            bias=bias,
            values=raw[:, :-1],
            terms=[(name,) for name in self.features],
        )

    def _threads(self) -> int:
        return self.threads or 0  # 0 = LightGBM's default (OpenMP) thread count

    # -- persistence ------------------------------------------------------
    def metadata(self) -> dict[str, Any]:
        return {
            "features": list(self.features),
            "categorical_levels": {k: list(v) for k, v in self.categorical_levels.items()},
            "task": self.task,
            "link": self.link,
            "offset_column": self.offset_column,
            "offset_link": self.offset_link,
            "class_labels": list(self.class_labels) if self.class_labels is not None else None,
        }

    def save(self, path: Path) -> None:
        """LightGBM's model text with the ``haute:`` record inserted."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.booster.model_to_string().splitlines(keepends=True)
        record = _META_PREFIX + json.dumps(self.metadata()) + "\n"
        index = next(
            (i for i, line in enumerate(lines) if line.startswith("pandas_categorical:")),
            len(lines),
        )
        lines.insert(index, record)
        path.write_bytes("".join(lines).encode("utf-8"))

    @classmethod
    def load(cls, path: str | Path) -> LightGBMModel:
        import lightgbm as lgb

        text = Path(path).read_bytes().decode("utf-8")
        records = [line for line in text.splitlines() if line.startswith(_META_PREFIX)]
        if len(records) != 1:
            raise HauteValidationError(
                f"{path} is not a LightGBM model Haute trained (no '{_META_PREFIX}' record); "
                "retrain it in Haute."
            )
        meta = json.loads(records[0][len(_META_PREFIX) :])
        labels = meta.get("class_labels")
        return cls(
            booster=lgb.Booster(model_str=text),
            features=list(meta["features"]),
            categorical_levels={k: list(v) for k, v in meta["categorical_levels"].items()},
            task=str(meta["task"]),
            link=str(meta["link"]),
            offset_column=meta.get("offset_column"),
            offset_link=meta.get("offset_link"),
            class_labels=(labels[0], labels[1]) if labels is not None else None,
        )

    def objective(self) -> str:
        """The objective recorded in the model text (``objective=poisson`` → ``poisson``)."""
        line = next(
            (
                line
                for line in self.booster.model_to_string().splitlines()
                if line.startswith("objective=")
            ),
            "objective=",
        )
        return line.partition("=")[2].split(" ")[0]


class LightGBMAlgorithm(BaseAlgorithm):
    """LightGBM GBDT behind Haute's descriptor, loss and offset contract."""

    def fit(
        self,
        train_df: pl.DataFrame | None,
        features: list[str],
        cat_features: list[str],
        target: str,
        weight: str | None,
        params: dict[str, Any],
        task: str,
        on_iteration: IterationCallback | None = None,
        eval_df: pl.DataFrame | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        **kwargs: Any,
    ) -> FitResult:
        import lightgbm as lgb

        from haute.modelling._descriptors import LIGHTGBM, round_ceiling

        if train_df is None:
            raise HauteValidationError("LightGBM needs the training frame")
        if feature_weights:
            raise HauteValidationError("LightGBM does not support feature weights")
        loss = kwargs.get("loss")
        if not loss:
            raise HauteValidationError("LightGBM needs an explicit loss")
        native = LIGHTGBM.native_loss(task, str(loss))
        monotone_issue = LIGHTGBM.monotone_constraint_issue(str(loss), monotone_constraints)
        if monotone_issue is not None:
            raise HauteValidationError(monotone_issue)
        if offset is not None and native.link == "logit":
            raise HauteValidationError(
                "LightGBM classification does not support an offset; remove the offset column."
            )
        threads = kwargs.get("threads")
        model = LightGBMModel(
            booster=None,
            features=list(features),
            categorical_levels=fit_categorical_levels(
                train_df, cat_features, kwargs.get("categorical_levels")
            ),
            task=task,
            link=native.link,
            offset_column=offset,
            offset_link=native.link if offset is not None else None,
            class_labels=kwargs.get("class_labels") if task == "classification" else None,
            threads=threads,
        )

        def dataset(frame: pl.DataFrame, reference: Any = None) -> Any:
            return lgb.Dataset(
                model.encoded(frame),
                label=frame.get_column(target).cast(pl.Float64).to_numpy(),
                weight=frame.get_column(weight).cast(pl.Float64).to_numpy() if weight else None,
                init_score=model.baseline(frame),
                categorical_feature=list(model.categorical_levels) or "auto",
                reference=reference,
                free_raw_data=False,
            )

        train_set = dataset(train_df)
        valid_sets = [train_set]
        valid_names = ["train"]
        if eval_df is not None:
            valid_sets.append(dataset(eval_df, reference=train_set))
            valid_names.append("validation")

        configured = int(round_ceiling(LIGHTGBM, params, _DEFAULT_ROUNDS))
        booster_params = {
            key: value
            for key, value in params.items()
            if key not in {"num_iterations", "early_stopping_round"}
        }
        booster_params.update(
            objective=native.objective,
            boosting="gbdt",
            seed=int(kwargs.get("seed") or 0),
            num_threads=threads or 0,
            verbosity=-1,
        )
        if loss == "Tweedie":
            booster_params["tweedie_variance_power"] = kwargs.get("variance_power")
        if monotone_constraints:
            booster_params["monotone_constraints"] = [
                int(monotone_constraints.get(name, 0)) for name in features
            ]
        loss_history: list[dict[str, float]] = []

        def progress(env: Any) -> None:
            metrics: dict[str, float] = {}
            entry: dict[str, float] = {"iteration": float(env.iteration + 1)}
            for dataset_name, metric_name, value, _higher in env.evaluation_result_list:
                prefix = "train" if dataset_name == "train" else "eval"
                label = metric_name if dataset_name == "train" else f"{dataset_name}_{metric_name}"
                metrics[label] = float(value)
                entry[f"{prefix}_{metric_name}"] = float(value)
            loss_history.append(entry)
            if on_iteration is not None:
                on_iteration(env.iteration + 1, configured, metrics, entry)

        callbacks: list[Any] = [progress]
        # LightGBM treats a non-positive round count as "no early stopping".
        early_stopping = (
            max(0, int(params.get("early_stopping_round", _DEFAULT_EARLY_STOPPING)))
            if eval_df is not None
            else 0
        )
        if early_stopping:
            callbacks.append(lgb.early_stopping(early_stopping, verbose=False))
        booster = lgb.train(
            booster_params,
            train_set,
            num_boost_round=configured,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )
        best_iteration: int | None = None
        stopped_early = bool(early_stopping and booster.best_iteration > 0)
        if stopped_early:
            # LightGBM's best_iteration is already a one-based count; keep only
            # those trees so the fitted and the saved model are the same.
            best_count = int(booster.best_iteration)
            best_iteration = best_count - 1
            booster = lgb.Booster(model_str=booster.model_to_string(num_iteration=best_count))
        model.booster = booster
        rounds_fitted = int(booster.current_iteration())
        if eval_df is not None and best_iteration is None:
            # A validation fit without early stopping selects every fitted
            # round, so the refit still has a round count to reuse.
            best_iteration = rounds_fitted - 1
        if rounds_fitted >= configured:
            stopping_reason = "none"
        elif stopped_early:
            stopping_reason = "validation"
        else:
            stopping_reason = "native_exhaustion"
        return FitResult(
            model=model,
            best_iteration=best_iteration,
            loss_history=loss_history,
            rounds_configured=configured,
            rounds_fitted=rounds_fitted,
            stopping_reason=stopping_reason,
            categorical_levels=dict(model.categorical_levels),
        )

    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        offset: str | None = None,
    ) -> np.ndarray:
        """Metric-scale predictions: responses, or positive-class probabilities."""
        return np.asarray(model.predict_response(df), dtype=np.float64)

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Total gain per feature; features no tree used report zero."""
        gains = model.booster.feature_importance(importance_type="gain")
        rows = [
            {"feature": name, "importance": float(value)}
            for name, value in zip(model.features, gains, strict=True)
        ]
        return sorted(rows, key=lambda row: row["importance"], reverse=True)

    def shap_summary(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        cat_features: list[str],
    ) -> list[dict[str, Any]]:
        """Mean absolute native contribution per feature, largest first."""
        values = model.contributions(df).values
        mean_abs = np.abs(values).mean(axis=0) if len(values) else np.zeros(len(features))
        pairs = sorted(zip(model.features, mean_abs, strict=True), key=lambda x: -x[1])
        return [{"feature": name, "mean_abs_shap": float(value)} for name, value in pairs]

    def save(self, model: Any, path: Path) -> None:
        model.save(path)
