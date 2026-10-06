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
from typing import Any

import numpy as np
import polars as pl

# (iteration, total, readout metrics, the loss-history row this iteration adds or None).
IterationCallback = Callable[[int, int, dict[str, float], dict[str, float] | None], None]


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
    #: The level list each categorical feature was encoded against, for the
    #: contract (new-family adapters; ``None`` keeps the declared levels).
    categorical_levels: dict[str, list[str | None]] | None = None
    #: The device a GPU-capable fit actually trained on (``cuda:0``);
    #: ``None`` for a CPU fit.
    device: str | None = None
    #: EBM's native ``best_iteration_``: executed term updates per boosting
    #: stage. Neither a tree count nor a round budget, and never converted.
    term_update_steps: list[int] | None = None


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
