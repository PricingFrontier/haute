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
from collections.abc import Callable, Iterator
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
)
from haute.modelling._native_encoding import (
    encode_frame,
    fit_categorical_levels,
    native_categories,
    restore_native_categories,
)
from haute.modelling._shap import require_feature_order

_META_ATTRIBUTE = "haute"
#: The booster attribute an analyst stamps on a model trained outside Haute to
#: declare its margin: ``"none"``, or ``"<column>:<link>"`` (``log``/``identity``).
OFFSET_STAMP = "haute_offset"
_DEFAULT_ROUNDS = 1000
_DEFAULT_EARLY_STOPPING = 50
#: XGBoost's own default; an external-memory matrix must be told the bins up front.
_DEFAULT_MAX_BIN = 256


@dataclass(kw_only=True)
class XGBoostModel(NativeModel):
    """A booster plus the record every prediction path needs."""

    family: ClassVar[str] = "XGBoost"

    booster: Any
    threads: int | None = field(default=None, compare=False)
    #: A booster trained outside Haute: the category values XGBoost recorded
    #: where they are not strings, and its feature types where a categorical
    #: feature recorded no categories (it scores the input's numeric codes).
    native_levels: dict[str, list[Any]] = field(default_factory=dict, compare=False)
    feature_types: list[str] | None = field(default=None, compare=False)
    #: False for a booster trained outside Haute that no ``haute_offset`` stamp
    #: or contract has declared: XGBoost cannot say whether it used a margin.
    offset_declared: bool = field(default=True, compare=False)

    # -- ScoringModel-facing surface ------------------------------------
    def matrix(
        self,
        frame: pl.DataFrame,
        *,
        label: np.ndarray | None = None,
        weight: np.ndarray | None = None,
    ) -> Any:
        """A ``DMatrix`` with contract-order category codes and the offset margin."""
        import xgboost as xgb

        return xgb.DMatrix(
            **self.matrix_inputs(frame, label=label, weight=weight),
            feature_types=self.feature_types,
            enable_categorical=True,
            nthread=self.threads or -1,
        )

    def matrix_inputs(
        self,
        frame: pl.DataFrame,
        *,
        label: np.ndarray | None = None,
        weight: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """What any matrix takes for *frame*: encoded data, labels, weights and margin."""
        return {
            "data": restore_native_categories(
                encode_frame(frame, self.features, self.categorical_levels, unseen="missing"),
                self.native_levels,
            ),
            "label": label,
            "weight": weight,
            "base_margin": self.baseline(frame),
        }

    def predict_margin(self, frame: pl.DataFrame) -> np.ndarray:
        """Raw scores including the offset (log / log-odds scale for those links)."""
        return np.asarray(self.booster.predict(self.matrix(frame), output_margin=True))

    def predict_response(self, frame: pl.DataFrame) -> np.ndarray:
        """Response-scale predictions; the positive-class probability for classifiers."""
        return np.asarray(self.booster.predict(self.matrix(frame)), dtype=np.float64)

    def contributions(self, frame: pl.DataFrame) -> Contributions:
        """Native ``pred_contribs``; the bias column already carries the offset."""
        raw = np.asarray(self.booster.predict(self.matrix(frame), pred_contribs=True))
        return Contributions(
            bias=raw[:, -1],
            values=raw[:, :-1],
            terms=[(name,) for name in self.features],
        )

    # -- persistence ------------------------------------------------------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.booster.set_attr(**{_META_ATTRIBUTE: json.dumps(self.record())})
        self.booster.save_model(str(path))

    @classmethod
    def load(cls, path: str | Path) -> XGBoostModel:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(path))
        # Serving is CPU scoring, whatever device trained the model.
        booster.set_param({"device": "cpu"})
        raw = booster.attr(_META_ATTRIBUTE)
        if not raw:
            return cls._native_record(booster, Path(path).name)
        return cls(booster=booster, **cls.parse_record(raw, Path(path).name))

    @classmethod
    def _native_record(cls, booster: Any, name: str) -> XGBoostModel:
        """A booster trained outside Haute, read from what XGBoost records itself.

        Its features, their types and the categories stored for each
        categorical feature; task and link from its objective; and the offset
        its ``haute_offset`` stamp declares, or none declared yet.
        """
        from haute.modelling._descriptors import XGBOOST, native_objective

        features = list(booster.feature_names or [])
        if not features:
            raise ConfigError(
                f"{name} records no feature names, so Haute cannot tell which input column "
                "each feature reads; save the booster after training it on a named DataFrame.",
                model_path=name,
            )
        learner = json.loads(booster.save_config())["learner"]
        shape = learner["learner_model_param"]
        if int(shape.get("num_class", "0")) > 1 or int(shape.get("num_target", "1")) > 1:
            raise ConfigError(
                f"{name} predicts more than one value per row; Haute scores regression and "
                "binary classification models.",
                model_path=name,
            )
        task, link = native_objective(XGBOOST, str(learner["objective"]["name"]))
        types = list(booster.feature_types or [])
        stored = booster.get_categories(export_to_arrow=True)
        recorded = (
            {}
            if stored.empty()
            else {
                feature: values.to_pylist()
                for feature, values in stored.to_arrow()
                if values is not None
            }
        )
        offset_column, offset_link, declared = _stamped_offset(booster.attr(OFFSET_STAMP), name)
        categorical = [f for f, kind in zip(features, types, strict=False) if kind == "c"]
        levels, native = native_categories(
            {feature: recorded[feature] for feature in categorical if feature in recorded}
        )
        return cls(
            booster=booster,
            features=features,
            categorical_levels=levels,
            task=task,
            link=link,
            offset_column=offset_column,
            offset_link=offset_link,
            class_labels=(0, 1) if task == "classification" else None,
            native_levels=native,
            feature_types=types if any(f not in recorded for f in categorical) else None,
            offset_declared=declared,
        )

    def objective(self) -> str:
        config = json.loads(self.booster.save_config())
        return str(config["learner"]["objective"]["name"])


def _stamped_offset(stamp: str | None, name: str) -> tuple[str | None, str | None, bool]:
    """The ``(column, link, declared)`` offset a ``haute_offset`` stamp declares."""
    if stamp is None:
        return None, None, False
    if stamp == "none":
        return None, None, True
    column, _, link = stamp.rpartition(":")
    if not column or link not in ("log", "identity"):
        raise ConfigError(
            f"{name}: its {OFFSET_STAMP!r} attribute is {stamp!r}; stamp 'none', "
            "'<column>:log' for an exposure margin or '<column>:identity' for an additive one.",
            model_path=name,
        )
    return column, link, True


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
                    on_iteration(epoch + 1, total, metrics, entry)
                return False  # False = continue training

        self.loss_history = loss_history
        self.callback = Callback()


@dataclass(frozen=True)
class XGBoostExternalMemory:
    """An external-memory fit's inputs: re-openable partition batches and its page cache."""

    train: Callable[[], Iterator[pl.DataFrame]]
    validation: Callable[[], Iterator[pl.DataFrame]] | None
    categorical_levels: dict[str, list[str | None]]
    cache_dir: Path


def _external_memory_matrix(
    batches: Callable[[], Iterator[pl.DataFrame]],
    inputs: Callable[[pl.DataFrame], dict[str, Any]],
    *,
    cache_prefix: Path,
    max_bin: int,
    threads: int | None,
    ref: Any = None,
) -> Any:
    """An ``ExtMemQuantileDMatrix`` over *batches*, its pages cached under *cache_prefix*.

    An exception raised while reading a batch leaves XGBoost and reaches the caller
    unchanged.
    """
    import xgboost as xgb

    class Batches(xgb.DataIter):
        def __init__(self) -> None:
            self._batches: Iterator[pl.DataFrame] | None = None
            super().__init__(cache_prefix=str(cache_prefix), release_data=True, on_host=False)

        def reset(self) -> None:
            self._batches = None

        def next(self, input_data: Callable[..., None]) -> bool:
            if self._batches is None:
                self._batches = batches()
            frame = next(self._batches, None)
            if frame is None:
                return False
            input_data(**{key: value for key, value in inputs(frame).items() if value is not None})
            return True

    return xgb.ExtMemQuantileDMatrix(
        Batches(),
        max_bin=max_bin,
        nthread=threads or -1,
        enable_categorical=True,
        ref=ref,
    )


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

        external: XGBoostExternalMemory | None = kwargs.get("external_memory")
        if train_df is None and external is None:
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
        if external is not None:
            levels = external.categorical_levels
        else:
            assert train_df is not None
            levels = fit_categorical_levels(train_df, cat_features)
        model = XGBoostModel(
            booster=None,
            features=list(features),
            categorical_levels=levels,
            task=task,
            link=native.link,
            offset_column=offset,
            offset_link=native.link if offset is not None else None,
            class_labels=kwargs.get("class_labels") if task == "classification" else None,
            threads=threads,
        )

        def labelled(frame: pl.DataFrame) -> dict[str, Any]:
            label = frame.get_column(target).cast(pl.Float64).to_numpy()
            weights = frame.get_column(weight).cast(pl.Float64).to_numpy() if weight else None
            return {"label": label, "weight": weights}

        if external is not None:
            max_bin = int(params.get("max_bin", _DEFAULT_MAX_BIN))

            def inputs(frame: pl.DataFrame) -> dict[str, Any]:
                return model.matrix_inputs(frame, **labelled(frame))

            dtrain = _external_memory_matrix(
                external.train,
                inputs,
                cache_prefix=external.cache_dir / "train",
                max_bin=max_bin,
                threads=threads,
            )
            evals = [(dtrain, "train")]
            if external.validation is not None:
                evals.append(
                    (
                        _external_memory_matrix(
                            external.validation,
                            inputs,
                            cache_prefix=external.cache_dir / "validation",
                            max_bin=max_bin,
                            threads=threads,
                            ref=dtrain,
                        ),
                        "validation",
                    )
                )
        else:
            assert train_df is not None
            dtrain = model.matrix(train_df, **labelled(train_df))
            evals = [(dtrain, "train")]
            if eval_df is not None:
                evals.append((model.matrix(eval_df, **labelled(eval_df)), "validation"))
        has_validation = len(evals) > 1

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
            if has_validation
            else None
        )
        gpu = kwargs.get("device") == "gpu"
        if gpu:
            from haute.modelling._gpu import require_xgboost_gpu

            require_xgboost_gpu()
            booster_params["device"] = "cuda"
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
        trained_device: str | None = None
        if gpu:
            from haute.modelling._gpu import verify_trained_on_gpu

            # XGBoost moves a CUDA request to the CPU with only a warning when no
            # GPU is visible, so the trained booster's own device is checked.
            trained_device = verify_trained_on_gpu(booster)
        # Scoring, diagnostics and the saved model all run on the CPU, exactly as
        # a CPU-only deployment scores it.
        booster.set_param({"device": "cpu"})
        best_iteration: int | None = None
        if has_validation and early_stopping:
            # best_iteration is zero-based; the booster keeps the rounds after
            # it, so trim before saving (MOD-F00 probe).
            best_iteration = int(booster.best_iteration)
            booster = booster[: best_iteration + 1]
        model.booster = booster
        rounds_fitted = int(booster.num_boosted_rounds())
        if has_validation and best_iteration is None:
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
            device=trained_device,
            evidence={"mode": "out_of_core"} if external is not None else {},
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
