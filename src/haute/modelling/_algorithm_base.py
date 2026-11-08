"""Algorithm base types shared by every model-family adapter.

Kept import-light and free of engine code so an adapter module can subclass
``BaseAlgorithm`` without importing ``_algorithms`` (which registers every
adapter and would otherwise form an import cycle).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

import numpy as np
import polars as pl

from haute.errors import ConfigError, HauteValidationError

# (iteration, total, readout metrics, the loss-history row this iteration adds or None).
IterationCallback = Callable[[int, int, dict[str, float], dict[str, float] | None], None]


#: Whether a fit holds its training rows in memory or reads them in passes from disk.
FitMode = Literal["in_memory", "out_of_core"]


@dataclass
class FitResult:
    """Result of algorithm.fit() — model plus training artifacts."""

    model: Any
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = field(default_factory=list)
    #: The round ceiling the fit was configured with (``None`` for the GLM).
    rounds_configured: int | None = None
    #: Rounds the saved model holds, read from the native model.
    rounds_fitted: int | None = None
    #: ``none``, ``validation`` (early stopping), or ``native_exhaustion``.
    stopping_reason: str | None = None
    #: Threads the engine actually used, when it does not take the job's
    #: allotment (EBM always fits with ``n_jobs=1``).
    threads: int | None = None
    #: The device a GPU-capable fit actually trained on (``cuda:0``);
    #: ``None`` for a CPU fit.
    device: str | None = None
    #: EBM's native ``best_iteration_``: executed term updates per boosting
    #: stage. Neither a tree count nor a round budget, and never converted.
    term_update_steps: list[int] | None = None
    #: Further ``FitEvidencePayload`` fields the family reports (the GLM's fit
    #: mode, solver caps and, out of core, its passes and memory plan).
    evidence: dict[str, Any] = field(default_factory=dict)


class BaseAlgorithm(ABC):
    """Abstract base class for training algorithms."""

    @abstractmethod
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
        """Train a model and return a FitResult.

        *train_df* may be ``None`` when a pre-built pool is passed
        via the ``pool`` keyword argument (CatBoost path).
        """

    @abstractmethod
    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        offset: str | None = None,
    ) -> np.ndarray:
        """Generate predictions from a fitted model.

        When *offset* names the column the model was trained with, the
        prediction re-applies it exactly as the fit did (GLM: the model
        extracts and transforms its offset column; CatBoost: the baseline
        is re-supplied through a ``Pool``).  A missing offset column in
        *df* raises — predictions are never silently produced on an
        offset-absent basis.
        """

    @abstractmethod
    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Return feature importances as [{feature, importance}, ...]."""

    @abstractmethod
    def save(self, model: Any, path: Path) -> None:
        """Save the model to disk."""


@dataclass
class Contributions:
    """Additive explanation of raw margins: ``bias + values.sum(axis=1)``."""

    bias: np.ndarray
    values: np.ndarray
    terms: list[tuple[str, ...]]


def inverse_link(link: str, margin: np.ndarray) -> np.ndarray:
    """The response a link-scale margin stands for: ``log`` and ``logit`` invert, else as is."""
    if link == "log":
        return np.asarray(np.exp(margin))
    if link == "logit":
        return np.asarray(1.0 / (1.0 + np.exp(-margin)))
    return margin


#: The fields of the ``haute`` record a native model file stores.
RECORD_KEYS = frozenset(
    {
        "features",
        "categorical_levels",
        "task",
        "link",
        "offset_column",
        "offset_link",
        "class_labels",
    }
)
_RECORD_LINKS = frozenset({"identity", "log", "logit"})


@dataclass(kw_only=True)
class NativeModel(ABC):
    """What every native model Haute trains records and serves the same way.

    The record its file stores (feature order, categorical levels, task, link, the
    offset and how it enters the margin, the binary class labels), the
    scoring-facing feature names, the offset's baseline, served labels and class
    probabilities. A family supplies its response-scale prediction.
    """

    #: How messages name the family (``"LightGBM"``).
    family: ClassVar[str]

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

    def baseline(self, frame: pl.DataFrame) -> np.ndarray | None:
        """The transformed offset each row's margin starts from, or ``None`` without one."""
        if self.offset_column is None:
            return None
        from haute.modelling._algorithms import _extract_offset_baseline

        return _extract_offset_baseline(
            frame, self.offset_column, link=str(self.offset_link), context=self.family
        )

    @abstractmethod
    def predict_response(self, frame: pl.DataFrame) -> np.ndarray:
        """Response-scale predictions; the positive-class probability for classifiers."""

    def predict(self, frame: pl.DataFrame) -> np.ndarray:
        """Served predictions: responses, or original labels for classifiers."""
        response = self.predict_response(frame)
        if self.task != "classification":
            return response
        from haute._mlflow_io import binary_labels

        if self.class_labels is None:
            raise HauteValidationError(f"{self.family} classifier has no recorded class labels")
        return binary_labels(response, self.class_labels)

    def predict_proba(self, frame: pl.DataFrame) -> np.ndarray:
        if self.task != "classification":
            raise HauteValidationError(
                f"{self.family} regression models have no class probabilities"
            )
        positive = self.predict_response(frame)
        return np.column_stack([1.0 - positive, positive])

    # -- the record -------------------------------------------------------
    def record(self) -> dict[str, Any]:
        """The ``haute`` record the model file stores."""
        return {
            "features": list(self.features),
            "categorical_levels": {k: list(v) for k, v in self.categorical_levels.items()},
            "task": self.task,
            "link": self.link,
            "offset_column": self.offset_column,
            "offset_link": self.offset_link,
            "class_labels": list(self.class_labels) if self.class_labels is not None else None,
        }

    @classmethod
    def parse_record(cls, text: str | bytes, name: str) -> dict[str, Any]:
        """:meth:`record_fields` of a record stored as JSON text."""
        import json

        try:
            record = json.loads(text)
        except ValueError as exc:
            raise ConfigError(
                f"{name}: the {cls.family} model's Haute record is not valid JSON; retrain it "
                "in Haute.",
                model_path=name,
            ) from exc
        return cls.record_fields(record, name)

    @classmethod
    def record_fields(cls, record: object, name: str) -> dict[str, Any]:
        """The constructor fields a stored ``haute`` record holds, every one checked.

        A record that is not an object, lacks or adds a field, or holds a field of
        the wrong shape raises ``ConfigError`` naming it, never a ``KeyError``.
        """

        def fail(problem: str) -> ConfigError:
            return ConfigError(
                f"{name}: the {cls.family} model's Haute record {problem}; retrain it in Haute.",
                model_path=name,
            )

        if not isinstance(record, dict):
            raise fail("is not an object")
        if set(record) != RECORD_KEYS:
            missing, unknown = sorted(RECORD_KEYS - set(record)), sorted(set(record) - RECORD_KEYS)
            raise fail(
                "lacks " + ", ".join(missing)
                if missing
                else "has unknown field(s) " + ", ".join(unknown)
            )
        features = record["features"]
        if (
            not isinstance(features, list)
            or not features
            or not all(isinstance(f, str) and f for f in features)
            or len(set(features)) != len(features)
        ):
            raise fail("has no valid feature list (features)")
        levels = record["categorical_levels"]
        if not isinstance(levels, dict) or not set(levels) <= set(features):
            raise fail("names categorical levels for unknown features (categorical_levels)")
        for feature, values in levels.items():
            if not isinstance(values, list) or not all(
                v is None or isinstance(v, str) for v in values
            ):
                raise fail(f"has invalid levels for {feature!r} (categorical_levels)")
        task, link = record["task"], record["link"]
        if not isinstance(task, str) or task not in {"regression", "classification"}:
            raise fail(f"has an unknown task {task!r} (task)")
        if (
            not isinstance(link, str)
            or link not in _RECORD_LINKS
            or (link == "logit") != (task == "classification")
        ):
            raise fail(f"has link {link!r} for a {task} model (link)")
        offset_column, offset_link = record["offset_column"], record["offset_link"]
        if offset_column is None:
            if offset_link is not None:
                raise fail("has an offset link without an offset column (offset_link)")
        elif not isinstance(offset_column, str) or not offset_column:
            raise fail("has an invalid offset column (offset_column)")
        elif not isinstance(offset_link, str) or offset_link not in _RECORD_LINKS:
            raise fail(f"has an invalid offset link {offset_link!r} (offset_link)")
        labels = record["class_labels"]
        class_labels: tuple[Any, Any] | None = None
        if task == "classification":
            if (
                not isinstance(labels, list)
                or len(labels) != 2
                or not all(isinstance(label, (bool, int, str)) for label in labels)
                or type(labels[0]) is not type(labels[1])
                or labels[0] == labels[1]
            ):
                raise fail(
                    "has no (negative, positive) pair of distinct labels of one type (class_labels)"
                )
            class_labels = (labels[0], labels[1])
        elif labels is not None:
            raise fail("has class labels for a regression model (class_labels)")
        return {
            "features": list(features),
            "categorical_levels": {feature: list(values) for feature, values in levels.items()},
            "task": task,
            "link": link,
            "offset_column": offset_column,
            "offset_link": offset_link,
            "class_labels": class_labels,
        }
