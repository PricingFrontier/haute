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
from typing import Any, ClassVar

import numpy as np
import polars as pl

from haute.errors import ConfigError, HauteValidationError
from haute.modelling._algorithm_base import (
    BaseAlgorithm,
    Contributions,
    FitResult,
    IterationCallback,
    NativeModel,
    inverse_link,
)
from haute.modelling._native_encoding import (
    encode_frame,
    fit_categorical_levels,
    native_categories,
    restore_native_categories,
)
from haute.modelling._shap import require_feature_order

_META_PREFIX = "haute:"
_DEFAULT_ROUNDS = 1000
_DEFAULT_EARLY_STOPPING = 50


@dataclass(kw_only=True)
class LightGBMModel(NativeModel):
    """A booster plus the record every prediction path needs."""

    family: ClassVar[str] = "LightGBM"

    booster: Any
    threads: int | None = field(default=None, compare=False)
    #: A model trained outside Haute: the category values LightGBM recorded
    #: where they are not strings.
    native_levels: dict[str, list[Any]] = field(default_factory=dict, compare=False)
    #: A model trained outside Haute is priced by LightGBM's own ``predict``, so
    #: the response transform it recorded (a non-unit sigmoid, ``reg_sqrt``)
    #: applies; Haute's own models add their offset to the raw score instead.
    native_response: bool = field(default=False, compare=False)

    # -- ScoringModel-facing surface ------------------------------------
    def encoded(self, frame: pl.DataFrame) -> Any:
        """Features as pandas with contract-order categoricals."""
        return restore_native_categories(
            encode_frame(frame, self.features, self.categorical_levels, unseen="missing"),
            self.native_levels,
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
        if self.native_response:
            return np.asarray(
                self.booster.predict(self.encoded(frame), num_threads=self._threads()),
                dtype=np.float64,
            )
        return np.asarray(inverse_link(self.link, self.predict_margin(frame)), dtype=np.float64)

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
    def save(self, path: Path) -> None:
        """LightGBM's model text with the ``haute:`` record inserted."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.booster.model_to_string().splitlines(keepends=True)
        record = _META_PREFIX + json.dumps(self.record()) + "\n"
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
        if not records:
            return cls._native_record(text, Path(path).name)
        if len(records) > 1:
            raise HauteValidationError(
                f"{path} holds {len(records)} '{_META_PREFIX}' records, not one; retrain it in "
                "Haute."
            )
        return cls(
            booster=lgb.Booster(model_str=text),
            **cls.parse_record(records[0][len(_META_PREFIX) :], Path(path).name),
        )

    @classmethod
    def _native_record(cls, text: str, name: str) -> LightGBMModel:
        """A model trained outside Haute, read from what LightGBM records itself.

        Its features, its categorical features with the levels ``pandas_categorical``
        recorded (one trained on integer codes records none and scores the input's
        codes), task and link from its objective; no offset.
        """
        import lightgbm as lgb

        from haute.modelling._descriptors import LIGHTGBM, native_objective

        booster = lgb.Booster(model_str=text)
        if booster.num_model_per_iteration() > 1:
            raise ConfigError(
                f"{name} predicts more than one value per row; Haute scores regression and "
                "binary classification models.",
                model_path=name,
            )
        features = list(booster.feature_name())
        declared = booster.params.get("categorical_feature") or []
        if isinstance(declared, str):
            declared = [part for part in declared.split(",") if part]
        categorical = [
            features[int(item)] if str(item).isdigit() else str(item) for item in declared
        ]
        recorded = booster.pandas_categorical or []
        if recorded and len(recorded) != len(categorical):
            raise ConfigError(
                f"{name} records levels for {len(recorded)} of its {len(categorical)} categorical "
                "features, so Haute cannot tell which levels belong to which feature.",
                model_path=name,
            )
        levels, native = native_categories(dict(zip(categorical, recorded, strict=False)))
        task, link = native_objective(LIGHTGBM, _text_objective(text))
        return cls(
            booster=booster,
            features=features,
            categorical_levels=levels,
            task=task,
            link=link,
            class_labels=(0, 1) if task == "classification" else None,
            native_levels=native,
            native_response=True,
        )

    def objective(self) -> str:
        """The objective recorded in the model text (``objective=poisson`` → ``poisson``)."""
        return _text_objective(self.booster.model_to_string())


def _text_objective(text: str) -> str:
    """The objective a LightGBM model text records (``objective=binary sigmoid:1`` → ``binary``)."""
    line = next((line for line in text.splitlines() if line.startswith("objective=")), "objective=")
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
            categorical_levels=fit_categorical_levels(train_df, cat_features),
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

    def shap_values(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        cat_features: list[str],
    ) -> np.ndarray:
        """Native contributions for every row of *df*, one column per feature, no bias."""
        require_feature_order(model.features, features)
        return np.asarray(model.contributions(df).values, dtype=np.float64)

    def save(self, model: Any, path: Path) -> None:
        model.save(path)
