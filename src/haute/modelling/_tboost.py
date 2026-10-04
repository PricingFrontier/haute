"""t-boost adapter: rating-table boosting under Haute's data and prediction contract.

A t-boost model is exactly a set of rating tables: an intercept plus one value
per main-effect or interaction table is the raw score. Each fit sees only the
rows it is given; t-boost's own early stopping, bagging and table pruning run
on internal carves of those rows, so Haute never passes its validation
partition except as its early-stopping set: a fit that has validation rows
passes them as t-boost's ``eval_set``, so every bag stops at its own best round
on them and they never reach training, and the early-stopped fit is the model.
A fit without validation rows (the development refit) stops on t-boost's own
holdout of the rows it is given. No round count is carried between fits.

The saved ``.tboost`` file is t-boost's own JSON model document with Haute's
record (the feature order, categorical levels, task and link, the offset column
and how it enters the margin, and the binary class labels) in t-boost's
``metadata`` slot, so plain t-boost still loads it and no pickle is involved.

Behaviour the engine handles differently from Haute, per the t-boost probes: it
scores an unseen category in a default cell (Haute refuses it), and it refuses
temporal columns (Haute casts every non-categorical feature to ``Float64``, as
for the other families).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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
from haute.modelling._native_encoding import fit_categorical_levels, require_known_levels
from haute.modelling._shap import require_feature_order

RECORD_KEY = "haute"
_ESTIMATOR_TASKS = {"TBoostRegressor": "regression", "TBoostClassifier": "classification"}
# t-boost's stopping reasons in Haute's fit-evidence vocabulary. A callback stop is
# not listed: Haute's callback only ever stops a fit by raising (cancellation).
_STOPPING_REASONS = {
    "early_stopping": "validation",
    "max_trees": "none",
    "no_split": "native_exhaustion",
}
_MISSING_LEVEL = "__t_boost_missing__"
_MISSING_LABEL = "Missing"


def _inverse_link(link: str, margin: np.ndarray) -> np.ndarray:
    if link == "log":
        return np.asarray(np.exp(margin))
    if link == "logit":
        return np.asarray(1.0 / (1.0 + np.exp(-margin)))
    return margin


@contextmanager
def _float32_features_accepted() -> Iterator[None]:
    """Silence t-boost's notice that it scores features as float32 (a family property)."""
    from t_boost import PrecisionWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PrecisionWarning)
        yield


def _cell_labels(cuts: list[float]) -> list[str]:
    """Labels of a numeric axis: the missing cell, then t-boost's right-closed intervals."""
    if not cuts:
        return [_MISSING_LABEL, "All values"]
    labels = [_MISSING_LABEL, f"<= {cuts[0]:.6g}"]
    labels.extend(f"> {low:.6g} to <= {high:.6g}" for low, high in zip(cuts, cuts[1:]))
    labels.append(f"> {cuts[-1]:.6g}")
    return labels


def _report_axis(axis: dict[str, Any], feature: str) -> tuple[dict[str, Any], list[int]]:
    """A display axis and the cell indices it keeps.

    A numeric axis keeps every cell. A categorical axis keeps only the cells
    that hold levels, each labelled by its member levels in the model's order.
    """
    levels = axis.get("levels")
    if levels is None:
        cuts = [float(cut) for cut in axis["borders"]]
        labels = _cell_labels(cuts)
        if len(labels) != int(axis["cells"]):
            raise HauteValidationError(
                f"t-boost axis '{feature}' has {axis['cells']} cells but {len(cuts)} borders."
            )
        return (
            {"feature": feature, "type": "continuous", "labels": labels, "cuts": cuts},
            list(range(len(labels))),
        )
    members: dict[int, list[str]] = {}
    for level in levels:
        names = [
            _MISSING_LABEL if member == _MISSING_LEVEL else str(member)
            for member in level["members"]
        ]
        members.setdefault(int(level["cell"]), []).extend(names)
    cells = sorted(members)
    return (
        {
            "feature": feature,
            "type": "nominal",
            "labels": [", ".join(members[cell]) for cell in cells],
        },
        cells,
    )


def _report_table(table: dict[str, Any], link: str) -> dict[str, Any]:
    features = [str(name) for name in table["feature_names"]]
    shape = tuple(int(size) for size in table["shape"])
    axes, kept = zip(
        *(
            _report_axis(axis, feature)
            for axis, feature in zip(table["axes"], features, strict=True)
        ),
        strict=True,
    )
    index = np.ix_(*kept)
    scores = np.asarray(table["values"], dtype=np.float64).reshape(shape)[index]
    support = np.asarray(table["support"], dtype=np.float64).reshape(shape)[index]
    return {
        "term": " × ".join(features),
        "features": features,
        "order": len(features),
        "importance": float(table["sobol"]),
        "axes": list(axes),
        "scores": scores.tolist(),
        "relativities": np.exp(scores).tolist() if link == "log" else None,
        "support": support.tolist(),
    }


def _report_factored(effect: dict[str, Any]) -> dict[str, Any]:
    """A factored effect has no dense table: its features and importance only."""
    features = [str(name) for name in effect["feature_names"]]
    return {
        "term": " × ".join(features),
        "features": features,
        "importance": float(effect["sobol"]),
    }


@dataclass
class TBoostModel:
    """A fitted t-boost estimator plus the record every prediction path needs."""

    estimator: Any
    features: list[str]
    categorical_levels: dict[str, list[str | None]]
    task: str
    link: str
    offset_column: str | None = None
    offset_link: str | None = None
    class_labels: tuple[Any, Any] | None = None
    threads: int | None = field(default=None, compare=False)
    #: The table report on the fit's own rows (absent on a loaded model).
    fit_tables: dict[str, Any] | None = field(default=None, compare=False)

    # -- ScoringModel-facing surface ------------------------------------
    @property
    def feature_names_(self) -> list[str]:
        return list(self.features)

    @property
    def cat_feature_names(self) -> frozenset[str]:
        return frozenset(self.categorical_levels)

    def prepared(self, frame: pl.DataFrame) -> pl.DataFrame:
        """The features in contract order: categoricals as checked strings, the rest Float64."""
        columns = []
        for name in self.features:
            series = frame.get_column(name)
            if name in self.categorical_levels:
                columns.append(
                    require_known_levels(
                        series, name, self.categorical_levels[name], context="t-boost"
                    )
                )
            else:
                columns.append(series.cast(pl.Float64))
        return pl.DataFrame(columns)

    def baseline(self, frame: pl.DataFrame) -> np.ndarray | None:
        """The transformed offset each row's margin starts from.

        Under a log link the offset is an exposure and enters as its ``log``;
        under the identity link it is added verbatim.
        """
        if self.offset_column is None:
            return None
        from haute.modelling._algorithms import _extract_offset_baseline

        return _extract_offset_baseline(
            frame, self.offset_column, link=str(self.offset_link), context="t-boost"
        )

    def _raw(self, prepared: pl.DataFrame) -> np.ndarray:
        with _float32_features_accepted():
            if self.task == "classification":
                raw = self.estimator.decision_function(prepared)
            else:
                raw = self.estimator.predict_raw(prepared)
        return np.asarray(raw, dtype=np.float64)

    def predict_margin(self, frame: pl.DataFrame) -> np.ndarray:
        """Intercept plus every table value plus the offset (link scale)."""
        raw = self._raw(self.prepared(frame))
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
            raise HauteValidationError("t-boost classifier has no recorded class labels")
        return binary_labels(response, self.class_labels)

    def predict_proba(self, frame: pl.DataFrame) -> np.ndarray:
        if self.task != "classification":
            raise HauteValidationError("t-boost regression models have no class probabilities")
        positive = self.predict_response(frame)
        return np.column_stack([1.0 - positive, positive])

    def _contribution_matrix(self, frame: pl.DataFrame, *, split: bool) -> Any:
        with _float32_features_accepted():
            matrix = self.estimator.predict_contributions(
                self.prepared(frame),
                split_interactions=split,
                return_format="matrix",
                validate=False,
            )
        shape = np.asarray(matrix.values).shape
        if shape != (len(frame), len(matrix.terms)):
            raise HauteValidationError(
                f"t-boost returned a {shape} contribution matrix for {len(frame)} rows and "
                f"{len(matrix.terms)} terms."
            )
        return matrix

    def contributions(self, frame: pl.DataFrame) -> Contributions:
        """One value per table; the bias is t-boost's intercept plus the transformed offset."""
        matrix = self._contribution_matrix(frame, split=False)
        bias = np.asarray(matrix.base_value, dtype=np.float64)
        baseline = self.baseline(frame)
        if baseline is not None:
            bias = bias + baseline
        return Contributions(
            bias=bias,
            values=np.asarray(matrix.values, dtype=np.float64),
            terms=[tuple(str(name) for name in term) for term in matrix.terms],
        )

    def shapley_values(self, frame: pl.DataFrame) -> np.ndarray:
        """Exact interventional Shapley values, one column per contract feature."""
        matrix = self._contribution_matrix(frame, split=True)
        terms = [tuple(term) for term in matrix.terms]
        if terms != [(name,) for name in self.features]:
            raise HauteValidationError(
                f"t-boost returned Shapley values for {terms}, not the model's features."
            )
        return np.asarray(matrix.values, dtype=np.float64)

    def table_report(self, frame: pl.DataFrame) -> dict[str, Any]:
        """Every table's cells over the rows of *frame*, ranked by importance.

        Values are link-scale; ``relativities`` are their exponent under a log
        link. ``support`` is the training mass in each cell. A factored effect
        (over t-boost's dense-cell budget) has no table and is listed alone.
        """
        with _float32_features_accepted():
            export = json.loads(self.estimator.tables(self.prepared(frame)))
        tables = sorted(
            (_report_table(table, self.link) for table in export["tables"]),
            key=lambda table: -table["importance"],
        )
        factored = sorted(
            (_report_factored(effect) for effect in export["factored"]),
            key=lambda effect: -float(effect["importance"]),
        )
        return {
            "link": self.link,
            "base_value": float(export["f0"]),
            "tables": tables,
            "factored": factored,
        }

    def objective(self) -> str:
        """The native objective the estimator was trained with (``poisson``)."""
        return str(self.estimator.objective)

    # -- persistence ------------------------------------------------------
    def record(self) -> dict[str, Any]:
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
        """t-boost's JSON model document, Haute's record in its ``metadata`` slot."""
        self.estimator.metadata = {RECORD_KEY: self.record()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.estimator.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> TBoostModel:
        """Load a ``.tboost`` file, refusing a record its native model does not match.

        t-boost never reads its ``metadata`` slot, so the record is validated on
        its own and against the estimator: the estimator kind for the task, a
        binary 0/1 classifier, the link of its objective, the offset's link, the
        feature order and the categorical set.
        """
        import t_boost
        from t_boost import SerializationError, TBoostClassifier, TBoostRegressor

        name = Path(path).name
        text = Path(path).read_text(encoding="utf-8")
        document = json.loads(text)
        metadata = document.get("metadata") if isinstance(document, dict) else None
        if not isinstance(metadata, dict) or not isinstance(metadata.get(RECORD_KEY), dict):
            raise HauteValidationError(
                f"{name} is not a t-boost model Haute trained (no {RECORD_KEY!r} record in its "
                "metadata); retrain it in Haute."
            )
        record = _validated_record(metadata[RECORD_KEY], name)
        estimator_name = document.get("estimator")
        if estimator_name not in _ESTIMATOR_TASKS or document.get("kind") != "single":
            raise ConfigError(
                f"{name} holds a {estimator_name!r} model of kind {document.get('kind')!r}; "
                "Haute scores single-output t-boost regressors and binary classifiers."
            )
        if _ESTIMATOR_TASKS[estimator_name] != record["task"]:
            raise ConfigError(
                f"{name} holds a {estimator_name} but its record describes a {record['task']} "
                "model; retrain it in Haute."
            )
        estimator_class = (
            TBoostRegressor if estimator_name == "TBoostRegressor" else TBoostClassifier
        )
        try:
            estimator = estimator_class.from_json(text)
        except SerializationError as exc:
            raise ConfigError(
                f"t-boost {t_boost.__version__} cannot read {name}: {exc}. Install the "
                "t-boost version it was trained with, or retrain it."
            ) from exc
        model = cls(
            estimator=estimator,
            features=record["features"],
            categorical_levels=record["categorical_levels"],
            task=record["task"],
            link=record["link"],
            offset_column=record["offset_column"],
            offset_link=record["offset_link"],
            class_labels=record["class_labels"],
        )
        model.validate_native(document.get("cat_indices"), name)
        return model

    def validate_native(self, cat_indices: Any, name: str) -> None:
        """Refuse a native estimator that is not the model the record describes."""
        estimator = self.estimator
        if list(estimator.feature_names_in_) != self.features:
            raise ConfigError(
                f"{name}: the t-boost model's features do not match its record; retrain it in "
                "Haute."
            )
        from haute.modelling._descriptors import TBOOST

        native_links = {
            loss.objective: loss.link
            for losses in TBOOST.losses.values()
            for loss in losses.values()
        }
        objective = self.objective()
        if objective not in native_links:
            raise ConfigError(f"{name}: t-boost objective {objective!r} is not supported.")
        if native_links[objective] != self.link:
            raise ConfigError(
                f"{name}: the t-boost model was trained with {objective} "
                f"({native_links[objective]} link) but its record describes a {self.link} link; "
                "retrain it in Haute."
            )
        if self.task == "classification" and [int(c) for c in estimator.classes_] != [0, 1]:
            raise ConfigError(
                f"{name}: a Haute t-boost classifier is trained on the 0/1 positive-class "
                "encoding; this model's classes are different."
            )
        expected_categorical = [
            index
            for index, feature in enumerate(self.features)
            if feature in self.categorical_levels
        ]
        if cat_indices != expected_categorical:
            raise ConfigError(
                f"{name}: the t-boost model's categorical features do not match its record; "
                "retrain it in Haute."
            )


def _validated_record(record: dict[str, Any], name: str) -> dict[str, Any]:
    """The ``haute`` record with every field checked, or ``ConfigError`` naming the first fault."""

    def fail(problem: str) -> ConfigError:
        return ConfigError(f"{name}: the t-boost model's Haute record {problem}; retrain it.")

    expected_keys = {
        "features",
        "categorical_levels",
        "task",
        "link",
        "offset_column",
        "offset_link",
        "class_labels",
    }
    if set(record) != expected_keys:
        raise fail(f"has keys {sorted(record)}, not {sorted(expected_keys)}")
    features = record["features"]
    if (
        not isinstance(features, list)
        or not features
        or not all(isinstance(f, str) and f for f in features)
        or len(set(features)) != len(features)
    ):
        raise fail("has no valid feature list")
    levels = record["categorical_levels"]
    if not isinstance(levels, dict) or not set(levels) <= set(features):
        raise fail("names categorical levels for unknown features")
    for feature, values in levels.items():
        if not isinstance(values, list) or not all(v is None or isinstance(v, str) for v in values):
            raise fail(f"has invalid levels for {feature!r}")
    task, link = record["task"], record["link"]
    if task not in {"regression", "classification"}:
        raise fail(f"has an unknown task {task!r}")
    if link not in {"identity", "log", "logit"} or (link == "logit") != (task == "classification"):
        raise fail(f"has link {link!r} for a {task} model")
    offset_column, offset_link = record["offset_column"], record["offset_link"]
    if offset_column is None:
        if offset_link is not None:
            raise fail("has an offset link without an offset column")
    elif not isinstance(offset_column, str) or not offset_column:
        raise fail("has an invalid offset column")
    elif link == "logit" or offset_link != link:
        raise fail(f"has a {offset_link!r} offset under the {link} link")
    labels = record["class_labels"]
    if task == "classification":
        if not isinstance(labels, list) or len(labels) != 2:
            raise fail("has no (negative, positive) class labels")
        class_labels: tuple[Any, Any] | None = (labels[0], labels[1])
    elif labels is not None:
        raise fail("has class labels for a regression model")
    else:
        class_labels = None
    return {
        "features": list(features),
        "categorical_levels": {feature: list(values) for feature, values in levels.items()},
        "task": task,
        "link": link,
        "offset_column": offset_column,
        "offset_link": offset_link,
        "class_labels": class_labels,
    }


def _rows_kwargs(
    model: TBoostModel, frame: pl.DataFrame, target: str, weight: str | None, task: str
) -> dict[str, Any]:
    """One frame's features, label, weight and offset as t-boost ``fit`` arguments.

    A log-link offset is t-boost's ``exposure``; an identity-link offset is its
    link-scale ``offset``.
    """
    label = frame.get_column(target).cast(pl.Float64).to_numpy()
    rows: dict[str, Any] = {
        "X": model.prepared(frame),
        "y": label.astype(np.int64) if task == "classification" else label,
        "sample_weight": frame.get_column(weight).cast(pl.Float64).to_numpy() if weight else None,
    }
    baseline = model.baseline(frame)
    if baseline is not None and model.link == "log":
        rows["exposure"] = np.exp(baseline)
    elif baseline is not None:
        rows["offset"] = baseline
    return rows


class _RoundProgress:
    """t-boost's per-round callback as Haute progress.

    Bags boost in parallel and report interleaved, so the progress shown is the
    furthest round any bag has reached, which never goes back. Every report is
    forwarded, because Haute's callback also checks for cancellation: a later
    bag must reach that check on each of its rounds. Haute's progress gate
    always passes a fit's final round, and once one bag reaches ``n_trees`` the
    others keep reporting, so during the fit progress stops one round short of
    the ceiling and :meth:`finish` reports the final round once. The loss curve
    is built after the fit from the complete per-bag history.
    """

    def __init__(self, on_iteration: IterationCallback | None, total: int) -> None:
        self._on_iteration = on_iteration
        self._total = total
        self._reached = 0

    def __call__(self, info: dict[str, Any]) -> None:
        if self._on_iteration is None:
            return
        self._reached = max(self._reached, int(info["round"]))
        self._on_iteration(min(self._reached, self._total - 1), self._total, {}, None)

    def finish(self) -> None:
        """Report the final round once, when a bag boosted to the ceiling."""
        if self._on_iteration is not None and self._reached >= self._total:
            self._on_iteration(self._total, self._total, {}, None)


def _loss_history(evals: dict[str, Any] | None) -> list[dict[str, float]]:
    """Per-round mean deviance over the bags still boosting at that round."""
    if not evals:
        return []
    curves = {
        f"{split}_deviance": [np.asarray(bag, dtype=np.float64) for bag in values["deviance"]]
        for split, values in evals.items()
    }
    rounds = max(len(bag) for bags in curves.values() for bag in bags)
    history = []
    for index in range(rounds):
        row = {"iteration": float(index + 1)}
        for key, bags in curves.items():
            reached = [bag[index] for bag in bags if len(bag) > index]
            if reached:
                row[key] = float(np.mean(reached))
        history.append(row)
    return history


class TBoostAlgorithm(BaseAlgorithm):
    """t-boost behind Haute's descriptor, loss and offset contract."""

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
        from t_boost import TBoostClassifier, TBoostRegressor

        from haute.modelling._descriptors import TBOOST

        if train_df is None:
            raise HauteValidationError("t-boost needs the training frame")
        if feature_weights:
            raise HauteValidationError("t-boost does not support feature weights")
        loss = kwargs.get("loss")
        if not loss:
            raise HauteValidationError("t-boost needs an explicit loss")
        native = TBOOST.native_loss(task, str(loss))
        issue = TBOOST.config_issue(params, str(loss), monotone_constraints)
        if issue is not None:
            raise HauteValidationError(issue)
        if offset is not None and native.link == "logit":
            raise HauteValidationError(
                "t-boost classification does not support an offset; remove the offset column."
            )
        threads = kwargs.get("threads")
        model = TBoostModel(
            estimator=None,
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
        n_trees = int(params["n_trees"])
        estimator_params: dict[str, Any] = {
            **params,
            "seed": int(kwargs.get("seed") or 0),
            "n_jobs": threads,
        }
        if task == "classification":
            estimator: Any = TBoostClassifier(**estimator_params)
        else:
            estimator_params["objective"] = native.objective
            if loss == "Tweedie":
                estimator_params["tweedie_rho"] = float(kwargs["variance_power"])
            estimator = TBoostRegressor(**estimator_params)
        if monotone_constraints:
            estimator.set_params(
                monotone_constraints={
                    name: int(direction)
                    for name, direction in monotone_constraints.items()
                    if direction
                }
            )
        fit_kwargs = _rows_kwargs(model, train_df, target, weight, task)
        if eval_df is not None:
            # Haute's validation rows are the early-stopping set: each bag stops at
            # its own best round on them, and the early-stopped fit is the model.
            eval_kwargs = _rows_kwargs(model, eval_df, target, weight, task)
            fit_kwargs["eval_set"] = (eval_kwargs.pop("X"), eval_kwargs.pop("y"))
            fit_kwargs.update({f"eval_{key}": value for key, value in eval_kwargs.items()})
        groups = kwargs.get("groups")
        if groups is not None:
            fit_kwargs["groups"] = train_df.get_column(str(groups)).cast(pl.String).to_numpy()
        features_frame, label = fit_kwargs.pop("X"), fit_kwargs.pop("y")
        progress = _RoundProgress(on_iteration, n_trees)
        with _float32_features_accepted():
            estimator.fit(features_frame, label, **fit_kwargs, callbacks=progress)
        progress.finish()
        model.estimator = estimator
        model.fit_tables = model.table_report(train_df)
        reason = str(estimator.stopping_reason_)
        if reason not in _STOPPING_REASONS:
            raise HauteValidationError(f"t-boost stopped for an unexpected reason: {reason!r}.")
        return FitResult(
            model=model,
            best_iteration=None,
            loss_history=_loss_history(estimator.evals_result_),
            rounds_configured=n_trees,
            rounds_fitted=int(estimator.n_trees_),
            stopping_reason=_STOPPING_REASONS[reason],
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
        """t-boost's variance share per feature."""
        shares = np.asarray(model.estimator.feature_importances_, dtype=np.float64)
        rows = [
            {"feature": name, "importance": float(value)}
            for name, value in zip(model.features, shares, strict=True)
        ]
        return sorted(rows, key=lambda row: row["importance"], reverse=True)

    def shap_values(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        cat_features: list[str],
    ) -> np.ndarray:
        """Exact per-feature Shapley values for every row of *df*, no bias."""
        require_feature_order(model.features, features)
        return np.asarray(model.shapley_values(df), dtype=np.float64)

    def tboost_tables(self, model: Any) -> dict[str, Any]:
        """The table report computed on the fit's own rows."""
        if model.fit_tables is None:
            raise HauteValidationError("This t-boost model has no table report from its fit.")
        return dict(model.fit_tables)

    def save(self, model: Any, path: Path) -> None:
        model.save(path)
