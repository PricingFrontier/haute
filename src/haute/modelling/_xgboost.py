"""XGBoost adapter: native ``hist`` boosters with Haute's data and prediction contract.

The saved ``.ubj`` model is self-describing: a ``haute`` attribute records the
feature order, the categorical levels the codes were built from, the task and
link, the offset column and how it enters the margin, and the binary class
labels. :class:`XGBoostModel` wraps the booster with that record so training
metrics, local scoring, MLflow serving and explanations all build the same
matrix and apply the same offset. Behaviour the engine gets wrong on its own —
positional category codes, silently scored unseen categories, a fitted
``base_score`` ignored once a ``base_margin`` is supplied, extra rounds kept
after early stopping — is handled here, per the MOD-F00 engine probes.
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

_META_ATTRIBUTE = "haute"
_DEFAULT_ROUNDS = 1000
_DEFAULT_EARLY_STOPPING = 50


@dataclass
class XGBoostModel:
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

    def matrix(
        self,
        frame: pl.DataFrame,
        *,
        label: np.ndarray | None = None,
        weight: np.ndarray | None = None,
    ) -> Any:
        """A ``DMatrix`` with contract-order category codes and the offset margin."""
        import xgboost as xgb

        from haute.modelling._algorithms import _extract_offset_baseline

        encoded = encode_frame(frame, self.features, self.categorical_levels, context="XGBoost")
        margin = None
        if self.offset_column is not None:
            margin = _extract_offset_baseline(
                frame,
                self.offset_column,
                link=str(self.offset_link),
                context="XGBoost",
            )
        return xgb.DMatrix(
            encoded,
            label=label,
            weight=weight,
            base_margin=margin,
            enable_categorical=True,
            nthread=self.threads or -1,
        )

    def predict_margin(self, frame: pl.DataFrame) -> np.ndarray:
        """Raw scores including the offset (log / log-odds scale for those links)."""
        return np.asarray(self.booster.predict(self.matrix(frame), output_margin=True))

    def predict_response(self, frame: pl.DataFrame) -> np.ndarray:
        """Response-scale predictions; the positive-class probability for classifiers."""
        return np.asarray(self.booster.predict(self.matrix(frame)), dtype=np.float64)

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        """Served predictions: responses, or original labels for classifiers."""
        response = self.predict_response(frame)
        if self.task != "classification":
            return response
        from haute._mlflow_io import binary_labels

        if self.class_labels is None:
            raise HauteValidationError("XGBoost classifier has no recorded class labels")
        return binary_labels(response, self.class_labels)

    def predict_proba(self, frame: pl.DataFrame) -> np.ndarray:
        if self.task != "classification":
            raise HauteValidationError("XGBoost regression models have no class probabilities")
        positive = self.predict_response(frame)
        return np.column_stack([1.0 - positive, positive])

    def contributions(self, frame: pl.DataFrame) -> Contributions:
        """Native ``pred_contribs``; the bias column already carries the offset."""
        raw = np.asarray(self.booster.predict(self.matrix(frame), pred_contribs=True))
        return Contributions(
            bias=raw[:, -1],
            values=raw[:, :-1],
            terms=[(name,) for name in self.features],
        )

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
        path.parent.mkdir(parents=True, exist_ok=True)
        self.booster.set_attr(**{_META_ATTRIBUTE: json.dumps(self.metadata())})
        self.booster.save_model(str(path))

    @classmethod
    def load(cls, path: str | Path) -> XGBoostModel:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(path))
        raw = booster.attr(_META_ATTRIBUTE)
        if not raw:
            raise HauteValidationError(
                f"{path} is not an XGBoost model Haute trained (no '{_META_ATTRIBUTE}' record); "
                "retrain it in Haute."
            )
        meta = json.loads(raw)
        labels = meta.get("class_labels")
        return cls(
            booster=booster,
            features=list(meta["features"]),
            categorical_levels={k: list(v) for k, v in meta["categorical_levels"].items()},
            task=str(meta["task"]),
            link=str(meta["link"]),
            offset_column=meta.get("offset_column"),
            offset_link=meta.get("offset_link"),
            class_labels=(labels[0], labels[1]) if labels is not None else None,
        )

    def objective(self) -> str:
        config = json.loads(self.booster.save_config())
        return str(config["learner"]["objective"]["name"])


class _XGBoostProgress:
    """Training callback: reports iterations (cancellation raises through it)."""

    def __init__(self, on_iteration: IterationCallback | None, total: int) -> None:
        import xgboost as xgb

        loss_history: list[dict[str, float]] = []

        class Callback(xgb.callback.TrainingCallback):
            def after_iteration(self, model: Any, epoch: int, evals_log: Any) -> bool:
                metrics: dict[str, float] = {}
                entry: dict[str, float] = {"iteration": float(epoch + 1)}
                for dataset, by_metric in evals_log.items():
                    for metric, values in by_metric.items():
                        if values:
                            value = float(values[-1])
                            prefix = "train" if dataset == "train" else "eval"
                            label = metric if dataset == "train" else f"{dataset}_{metric}"
                            metrics[label] = value
                            entry[f"{prefix}_{metric}"] = value
                loss_history.append(entry)
                if on_iteration is not None:
                    on_iteration(epoch + 1, total, metrics)
                return False  # False = continue training

        self.loss_history = loss_history
        self.callback = Callback()


class XGBoostAlgorithm(BaseAlgorithm):
    """XGBoost ``hist`` boosting behind Haute's descriptor, loss and offset contract."""

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
        import xgboost as xgb

        from haute.modelling._descriptors import XGBOOST, round_ceiling

        if train_df is None:
            raise HauteValidationError("XGBoost needs the training frame")
        if feature_weights:
            raise HauteValidationError("XGBoost does not support feature weights")
        loss = kwargs.get("loss")
        if not loss:
            raise HauteValidationError("XGBoost needs an explicit loss")
        native = XGBOOST.native_loss(task, str(loss))
        if offset is not None and native.link == "logit":
            raise HauteValidationError(
                "XGBoost classification does not support an offset; remove the offset column."
            )
        threads = kwargs.get("threads")
        model = XGBoostModel(
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

        def matrix(frame: pl.DataFrame) -> Any:
            label = frame.get_column(target).cast(pl.Float64).to_numpy()
            weights = frame.get_column(weight).cast(pl.Float64).to_numpy() if weight else None
            return model.matrix(frame, label=label, weight=weights)

        dtrain = matrix(train_df)
        evals = [(dtrain, "train")]
        if eval_df is not None:
            evals.append((matrix(eval_df), "validation"))

        configured = int(round_ceiling(XGBOOST, params, _DEFAULT_ROUNDS))
        booster_params = {
            key: value
            for key, value in params.items()
            if key not in {"num_boost_round", "early_stopping_rounds"}
        }
        booster_params.update(
            objective=native.objective,
            tree_method="hist",
            seed=int(kwargs.get("seed") or 0),
            nthread=threads or -1,
            verbosity=0,
        )
        if loss == "Tweedie":
            booster_params["tweedie_variance_power"] = kwargs.get("variance_power")
        if monotone_constraints:
            booster_params["monotone_constraints"] = tuple(
                int(monotone_constraints.get(name, 0)) for name in features
            )
        early_stopping = (
            max(0, int(params.get("early_stopping_rounds", _DEFAULT_EARLY_STOPPING)))
            if eval_df is not None
            else None
        )
        progress = _XGBoostProgress(on_iteration, configured)
        booster = xgb.train(
            booster_params,
            dtrain,
            num_boost_round=configured,
            evals=evals,
            early_stopping_rounds=early_stopping or None,
            callbacks=[progress.callback],
            verbose_eval=False,
        )
        best_iteration: int | None = None
        if eval_df is not None and early_stopping:
            # best_iteration is zero-based; the booster keeps the rounds after
            # it, so trim before saving (MOD-F00 probe).
            best_iteration = int(booster.best_iteration)
            booster = booster[: best_iteration + 1]
        model.booster = booster
        rounds_fitted = int(booster.num_boosted_rounds())
        if eval_df is not None and best_iteration is None:
            # A validation fit without early stopping selects every fitted
            # round, so the refit still has a round count to reuse.
            best_iteration = rounds_fitted - 1
        return FitResult(
            model=model,
            best_iteration=best_iteration,
            loss_history=progress.loss_history,
            rounds_configured=configured,
            rounds_fitted=rounds_fitted,
            stopping_reason="validation" if rounds_fitted < configured else "none",
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
        gains = model.booster.get_score(importance_type="total_gain")
        rows = [
            {"feature": name, "importance": float(gains.get(name, 0.0))} for name in model.features
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
