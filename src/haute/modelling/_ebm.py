"""EBM adapter: InterpretML Explainable Boosting Machines under Haute's contract.

An EBM fits on the rows it is given and nothing else: the MOD-F00 probes showed
that passing validation rows through ``bags`` leaks their targets into the
intercept even with early stopping off, so every fit runs on its own training
rows with ``outer_bags=1``, no early stopping, and the explicit ``max_rounds``
the configuration (or a tuning trial) supplies. The saved ``.ebm`` is a joblib
dump of the native estimator only, loaded through the restricted unpickler that
trusts exactly the two EBM classes; everything scoring needs beyond the
estimator (categorical levels, offset, link, class labels) comes from the
model's feature contract, which must also record the installed
``interpret-core`` version before the file is unpickled.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute.errors import ConfigError, HauteValidationError
from haute.modelling._algorithm_base import (
    BaseAlgorithm,
    Contributions,
    FitResult,
    IterationCallback,
)
from haute.modelling._native_encoding import encode_frame, fit_categorical_levels

EBM_CLASSES = frozenset({"ExplainableBoostingRegressor", "ExplainableBoostingClassifier"})
_MISSING_LABEL = "Missing"


def _inverse_link(link: str, margin: np.ndarray) -> np.ndarray:
    if link == "log":
        return np.asarray(np.exp(margin))
    if link == "logit":
        return np.asarray(1.0 / (1.0 + np.exp(-margin)))
    return margin


def _intercept(estimator: Any) -> float:
    return float(np.asarray(estimator.intercept_, dtype=np.float64).reshape(-1)[0])


@dataclass
class EBMModel:
    """A fitted EBM plus the contract facts every prediction path needs."""

    estimator: Any
    features: list[str]
    categorical_levels: dict[str, list[str | None]]
    task: str
    link: str
    offset_column: str | None = None
    offset_link: str | None = None
    class_labels: tuple[Any, Any] | None = None

    # -- ScoringModel-facing surface ------------------------------------
    @property
    def feature_names_(self) -> list[str]:
        return list(self.features)

    @property
    def cat_feature_names(self) -> frozenset[str]:
        return frozenset(self.categorical_levels)

    def encoded(self, frame: pl.DataFrame) -> Any:
        """Features as pandas; categoricals checked against the fitted levels."""
        return encode_frame(frame, self.features, self.categorical_levels, context="EBM")

    def baseline(self, frame: pl.DataFrame) -> np.ndarray | None:
        """The transformed offset each row's margin starts from."""
        if self.offset_column is None:
            return None
        from haute.modelling._algorithms import _extract_offset_baseline

        return _extract_offset_baseline(
            frame, self.offset_column, link=str(self.offset_link), context="EBM"
        )

    def predict_margin(self, frame: pl.DataFrame) -> np.ndarray:
        """Intercept plus every term score plus the offset (link scale)."""
        contributions = self.contributions(frame)
        return np.asarray(contributions.bias + contributions.values.sum(axis=1))

    def predict_response(self, frame: pl.DataFrame) -> np.ndarray:
        """The native response; the positive-class probability for classifiers."""
        encoded = self.encoded(frame)
        baseline = self.baseline(frame)
        if self.task == "classification":
            proba = self.estimator.predict_proba(encoded)
            return np.asarray(proba[:, 1], dtype=np.float64)
        return np.asarray(self.estimator.predict(encoded, init_score=baseline), dtype=np.float64)

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        """Served predictions: responses, or original labels for classifiers."""
        response = self.predict_response(frame)
        if self.task != "classification":
            return response
        from haute._mlflow_io import binary_labels

        if self.class_labels is None:
            raise HauteValidationError("EBM classifier has no recorded class labels")
        return binary_labels(response, self.class_labels)

    def predict_proba(self, frame: pl.DataFrame) -> np.ndarray:
        if self.task != "classification":
            raise HauteValidationError("EBM regression models have no class probabilities")
        positive = self.predict_response(frame)
        return np.column_stack([1.0 - positive, positive])

    def contributions(self, frame: pl.DataFrame) -> Contributions:
        """Native term scores; the bias is the intercept plus the transformed offset."""
        values = np.asarray(self.estimator.eval_terms(self.encoded(frame)), dtype=np.float64)
        bias = np.full(len(frame), _intercept(self.estimator))
        baseline = self.baseline(frame)
        if baseline is not None:
            bias = bias + baseline
        return Contributions(bias=bias, values=values, terms=self.terms())

    def terms(self) -> list[tuple[str, ...]]:
        """Each term's features; a pairwise interaction stays one term."""
        return [
            tuple(self.features[index] for index in term) for term in self.estimator.term_features_
        ]

    def objective(self) -> str:
        """The native objective without its parameters (``tweedie_deviance``)."""
        return str(self.estimator.objective).partition(":")[0]

    # -- persistence ------------------------------------------------------
    def save(self, path: Path) -> None:
        import joblib

        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.estimator, path)

    @classmethod
    def load(cls, path: str | Path, contract: Any) -> EBMModel:
        """Load *path* under its feature *contract*, refusing anything it does not describe.

        The contract must record the installed ``interpret-core`` version before
        anything is unpickled: scikit-learn's own state hook checks versions only
        for ``sklearn`` classes, so it gives InterpretML none.
        """
        import interpret

        from haute._sandbox import ArtifactVersionMismatchError, restricted_joblib_load

        identity = contract.model
        if identity is None or identity.algorithm != "ebm":
            raise ConfigError(
                f"{path} has no EBM feature contract; an EBM model loads only with the "
                "contract it was trained with."
            )
        if identity.engine_version != interpret.__version__:
            raise ArtifactVersionMismatchError(
                f"This EBM was trained with interpret-core {identity.engine_version}, but "
                f"interpret-core {interpret.__version__} is installed; retrain the model or "
                "install the recorded version."
            )
        estimator = restricted_joblib_load(path)
        model = cls(
            estimator=estimator,
            features=list(contract.features),
            categorical_levels={k: list(v) for k, v in contract.categorical_levels.items()},
            task=contract.task,
            link=identity.link,
            offset_column=contract.offset_column,
            offset_link=(
                ("log" if identity.link == "log" else "identity")
                if contract.offset_column
                else None
            ),
            class_labels=identity.class_labels,
        )
        model.validate()
        model.validate_identity(identity)
        return model

    def validate_identity(self, identity: Any) -> None:
        """Refuse a contract whose loss, link or variance power is not this estimator's.

        The contract decides how the offset enters and how labels are read, so a
        contract for another loss would score the right file wrongly.
        """
        from haute.modelling._descriptors import EBM

        if not identity.loss:
            raise ConfigError(
                "This EBM's feature contract records no loss; use the contract saved with it."
            )
        expected = EBM.native_loss(self.task, str(identity.loss))
        objective = str(self.estimator.objective)
        name, _, options = objective.partition(":")
        if name != expected.objective or identity.link != expected.link:
            raise ConfigError(
                f"This EBM was trained with {objective}, but its feature contract describes a "
                f"{identity.loss} model ({identity.link} link); use the contract saved with it."
            )
        if identity.loss == "Tweedie":
            power = options.partition("=")[2]
            if identity.variance_power is None or float(power) != float(identity.variance_power):
                raise ConfigError(
                    f"This EBM was trained with {objective}, but its feature contract records "
                    f"variance power {identity.variance_power}; use the contract saved with it."
                )

    def validate(self) -> None:
        """Check the unpickled estimator is the model the contract describes."""
        estimator = self.estimator
        name = type(estimator).__name__
        if type(estimator).__module__ != "interpret.glassbox._ebm._ebm" or name not in EBM_CLASSES:
            raise HauteValidationError(f"An .ebm file must hold an EBM estimator, not {name}.")
        expected = (
            "ExplainableBoostingClassifier"
            if self.task == "classification"
            else "ExplainableBoostingRegressor"
        )
        if name != expected:
            raise ConfigError(
                f"This EBM is an {name} but its contract describes a {self.task} model."
            )
        if list(estimator.feature_names_in_) != self.features or estimator.n_features_in_ != len(
            self.features
        ):
            raise ConfigError(
                "This EBM's features do not match its feature contract; use the contract saved "
                "with the model."
            )
        expected_types = [
            "nominal" if feature in self.categorical_levels else "continuous"
            for feature in self.features
        ]
        if list(estimator.feature_types_in_) != expected_types:
            raise ConfigError(
                "This EBM's feature types do not match its feature contract; use the contract "
                "saved with the model."
            )
        scores_finite = all(np.isfinite(scores).all() for scores in estimator.term_scores_)
        if not scores_finite or not math.isfinite(_intercept(estimator)):
            raise HauteValidationError("This EBM holds non-finite term scores; retrain it.")
        if self.task == "classification":
            classes = [float(label) for label in estimator.classes_]
            if classes != [0.0, 1.0]:
                raise ConfigError(
                    "A Haute EBM classifier is trained on the 0/1 positive-class encoding; "
                    "this model's classes are different."
                )

    # -- term report --------------------------------------------------------
    def term_report(self) -> list[dict[str, Any]]:
        """Shape functions and pairwise surfaces as additive link-scale term scores.

        Each axis lists the missing bin first, then the categories or the
        continuous bin ranges; the unknown-category bin is dropped because
        Haute rejects values outside the fitted levels before scoring.
        """
        estimator = self.estimator
        importances = np.asarray(estimator.term_importances(), dtype=np.float64)
        report = []
        for index, term in enumerate(estimator.term_features_):
            axes = [self._axis(feature_index, len(term)) for feature_index in term]
            scores = np.asarray(estimator.term_scores_[index], dtype=np.float64)
            # Drop the trailing unknown bin on every axis.
            scores = scores[tuple(slice(0, -1) for _ in term)]
            report.append(
                {
                    "term": " & ".join(self.features[i] for i in term),
                    "features": [self.features[i] for i in term],
                    "kind": "main" if len(term) == 1 else "interaction",
                    "importance": float(importances[index]),
                    "axes": axes,
                    "scores": scores.tolist(),
                }
            )
        report.sort(key=lambda item: -item["importance"])
        return report

    def _axis(self, feature_index: int, term_size: int) -> dict[str, Any]:
        feature = self.features[feature_index]
        levels = self.estimator.bins_[feature_index]
        # Interaction terms bin more coarsely when the fit recorded a coarser level.
        binning = levels[min(term_size, len(levels)) - 1]
        if isinstance(binning, dict):
            ordered = sorted(binning.items(), key=lambda item: item[1])
            labels = [_MISSING_LABEL, *(str(category) for category, _ in ordered)]
            return {"feature": feature, "type": "nominal", "labels": labels}
        cuts = [float(cut) for cut in np.asarray(binning, dtype=np.float64)]
        labels = [_MISSING_LABEL]
        if not cuts:
            labels.append("all values")
        else:
            labels.append(f"< {cuts[0]:.6g}")
            labels.extend(f"{low:.6g} to < {high:.6g}" for low, high in zip(cuts, cuts[1:]))
            labels.append(f">= {cuts[-1]:.6g}")
        return {"feature": feature, "type": "continuous", "labels": labels, "cuts": cuts}


class EBMAlgorithm(BaseAlgorithm):
    """InterpretML EBM behind Haute's descriptor, loss and offset contract."""

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
        from interpret.glassbox import (
            ExplainableBoostingClassifier,
            ExplainableBoostingRegressor,
        )

        from haute.modelling._descriptors import EBM

        # ``eval_df`` is deliberately unused: an EBM never sees validation rows.
        del eval_df
        if train_df is None:
            raise HauteValidationError("EBM needs the training frame")
        if feature_weights:
            raise HauteValidationError("EBM does not support feature weights")
        loss = kwargs.get("loss")
        if not loss:
            raise HauteValidationError("EBM needs an explicit loss")
        native = EBM.native_loss(task, str(loss))
        issue = EBM.config_issue(params, str(loss), monotone_constraints)
        if issue is not None:
            raise HauteValidationError(issue)
        if offset is not None and native.link == "logit":
            raise HauteValidationError(
                "EBM classification does not support an offset; remove the offset column."
            )
        objective = native.objective
        if loss == "Tweedie":
            objective = f"{objective}:variance_power={kwargs.get('variance_power')}"

        model = EBMModel(
            estimator=None,
            features=list(features),
            categorical_levels=fit_categorical_levels(
                train_df, cat_features, kwargs.get("categorical_levels")
            ),
            task=task,
            link=native.link,
            offset_column=offset,
            offset_link=("log" if native.link == "log" else "identity") if offset else None,
            class_labels=kwargs.get("class_labels") if task == "classification" else None,
        )
        interactions = params.get("interactions", 0)
        if isinstance(interactions, list):
            unknown = sorted({name for pair in interactions for name in pair} - set(features))
            if unknown:
                raise HauteValidationError(
                    "EBM interactions name features the model does not use: "
                    f"{', '.join(repr(name) for name in unknown)}."
                )
            interactions = [
                (features.index(first), features.index(second)) for first, second in interactions
            ]
        max_rounds = int(params["max_rounds"])
        estimator_params = {
            key: value for key, value in params.items() if key not in {"interactions"}
        }
        estimator_class = (
            ExplainableBoostingClassifier
            if task == "classification"
            else ExplainableBoostingRegressor
        )
        estimator = estimator_class(
            **estimator_params,
            objective=objective,
            feature_names=list(features),
            feature_types=[
                "nominal" if feature in model.categorical_levels else "continuous"
                for feature in features
            ],
            interactions=interactions,
            monotone_constraints=(
                [int(monotone_constraints.get(name, 0)) for name in features]
                if monotone_constraints
                else None
            ),
            outer_bags=1,
            inner_bags=0,
            validation_size=0,
            early_stopping_rounds=0,
            n_jobs=1,
            random_state=int(kwargs.get("seed") or 0),
        )
        label = train_df.get_column(target).cast(pl.Float64).to_numpy()
        fit_kwargs: dict[str, Any] = {
            "sample_weight": (
                train_df.get_column(weight).cast(pl.Float64).to_numpy() if weight else None
            ),
        }
        if task == "classification":
            label = label.astype(np.int64)
        else:
            fit_kwargs["init_score"] = model.baseline(train_df)
        # EBM's own ``callback`` starts a multiprocessing SharedMemoryManager
        # process, so progress is reported per fit, in rounds, around the call.
        if on_iteration is not None:
            on_iteration(0, max_rounds, {})
        with warnings.catch_warnings():
            # InterpretML's own plots hide the missing bin; Haute's term views show it.
            warnings.filterwarnings("ignore", message="Missing values detected")
            estimator.fit(model.encoded(train_df), label, **fit_kwargs)
        if on_iteration is not None:
            on_iteration(max_rounds, max_rounds, {})
        model.estimator = estimator
        return FitResult(
            model=model,
            best_iteration=None,
            loss_history=[],
            rounds_configured=max_rounds,
            rounds_fitted=None,
            stopping_reason="none",
            threads=1,
            categorical_levels=dict(model.categorical_levels),
            term_update_steps=[
                int(step) for step in np.asarray(estimator.best_iteration_).reshape(-1)
            ],
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
        """EBM term importances (mean absolute term score); interactions stay whole."""
        return [
            {"feature": item["term"], "importance": item["importance"]}
            for item in model.term_report()
        ]

    def ebm_terms(self, model: Any) -> list[dict[str, Any]]:
        return list(model.term_report())

    def save(self, model: Any, path: Path) -> None:
        model.save(path)
