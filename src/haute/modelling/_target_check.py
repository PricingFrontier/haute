"""Data-dependent target vs task/metric gate for model training.

``training_target_task_issue`` mirrors ``training_objective_issue``
(`_train_config.py`): return an actionable, user-facing message — naming the
user-model objects involved (target column, task, metrics) and what to do
next — or ``None`` when the pairing is valid. It is shared by the train
route's pre-dispatch validation (over the sunk training parquet) and
``TrainingJob._prepare_data`` (which also covers the CLI and exported-script
paths), so the two gates can never drift.

The motivating failures: a continuous target under ``task="classification"``
— and a continuous target whose *effective* metric set includes AUC/log loss
(the defaults implied by a binomial family or Logloss/CrossEntropy loss even
under ``task="regression"``) — trained all the way to the metric stage and
surfaced sklearn's bare "ValueError: continuous format is not supported" —
no target column, no task, no fix.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import polars as pl

# Reported metrics that are undefined without discrete class labels. A
# continuous target reaching sklearn's roc_auc_score/log_loss with any of
# these requested dies at the metric stage, so the gate rejects it up front.
# This is the complete set of classification metrics in _metrics.py's
# registry today (gini is a native Lorenz implementation, defined on
# continuous targets); extend it alongside any new registry entry that
# needs discrete labels.
_DISCRETE_LABEL_METRICS = frozenset({"auc", "logloss"})


def _has_fractional_values(
    data: pl.LazyFrame,
    target: str,
    collect: Callable[[pl.LazyFrame], pl.DataFrame],
    *,
    decimal: bool = False,
) -> bool:
    """Return whether any finite, non-null target value has a fractional part.

    Decimal columns compare in native decimal arithmetic — casting to
    ``Float64`` first would round away the fractional part of values beyond
    float's ~15-16 significant digits and misclassify them as integral.
    Decimals cannot hold NaN/Inf (and ``is_finite`` is unsupported on them),
    so the finite mask applies only to floats, where NaN is treated as
    missing rather than continuous.
    """
    column = pl.col(target)
    if decimal:
        predicate = column != column.floor()
    else:
        predicate = column.is_finite() & (column != column.floor())
    frame = collect(data.select(predicate.any()))
    return bool(frame.item())


def training_target_task_issue(
    data: pl.LazyFrame,
    *,
    target: str,
    task: str,
    metrics: Sequence[str],
    collect: Callable[[pl.LazyFrame], pl.DataFrame] | None = None,
) -> str | None:
    """Return an actionable message when the target's values cannot serve the run.

    ``metrics`` is the EFFECTIVE reported-metric set — explicit config metrics
    or the objective-implied defaults (``effective_metrics`` /
    ``TrainingJob.metrics``), which is what the metric stage will actually
    compute. The gate fires in two situations:

    - ``task="classification"``: the target must hold discrete class labels —
      boolean, integer, string/categorical/enum, or a float/decimal column
      whose finite values are all integral (a materialised 0/1 flag). A
      fractional float/decimal target, or a type that cannot act as class
      labels at all, gates regardless of the metric set: the classification
      fit itself is undefined on it.
    - Any other task whose effective metrics include AUC/log loss (implied by
      a binomial family or Logloss/CrossEntropy loss under
      ``task="regression"``, or set explicitly): a fractional float/decimal
      target gates, because those metrics need discrete class labels and the
      run would only die later at the metric stage, stripped of this context.
      A binomial fit on a continuous proportion target stays legitimate and
      reachable — set the reported metrics explicitly to regression metrics
      and no classification metric is ever computed. Non-float targets defer
      to the fit's own validation on this branch.

    ``collect`` lets callers route the one boolean fractional-values scan
    through their own (streaming, execution-context-aware) collector; the
    default collects through Polars' streaming engine.
    """
    classification_task = str(task).lower() == "classification"
    label_metrics = sorted({str(metric).lower() for metric in metrics} & _DISCRETE_LABEL_METRICS)
    if not classification_task and not label_metrics:
        return None
    schema = data.collect_schema()
    if target not in schema:
        # A missing target column has its own gates (config validation and
        # TrainingJob._validate_columns); this check owns only the pairing.
        return None
    dtype = schema[target]
    base_type = dtype.base_type()
    if base_type == pl.Null:
        # An all-null target is a null-count problem, not a type problem —
        # TrainingJob._prepare_data's zero-non-null-rows gate owns that
        # message, and it is more accurate than a dtype complaint here.
        return None
    if base_type == pl.Boolean or dtype.is_integer():
        return None
    if base_type in (pl.String, pl.Categorical, pl.Enum):
        # Class labels for classification; under any other task a string
        # target is a target/task problem the fit's own validation owns.
        return None
    if dtype.is_float() or base_type == pl.Decimal:
        if collect is None:
            from haute._polars_utils import streaming_collect

            collect = streaming_collect
        if not _has_fractional_values(
            data,
            target,
            collect,
            decimal=base_type == pl.Decimal,
        ):
            return None
        if classification_task:
            return (
                f"Target column '{target}' contains continuous values, but the training "
                "task is classification. A classification target must hold discrete class "
                "labels (e.g. a 0/1 claim flag) — classification training and its metrics "
                "(AUC, log loss) are undefined on a continuous target. Choose a discrete "
                "target column, or set the task to regression to model a continuous target."
            )
        return (
            f"Target column '{target}' contains continuous values, but the reported "
            f"metrics include classification metrics ({', '.join(label_metrics)}), "
            f"which need a discrete 0/1 target. The training task is {task}, so "
            "these metrics are either set explicitly or implied by a "
            "classification-flavoured objective (e.g. a binomial family). Choose a "
            "discrete target column (e.g. a 0/1 claim flag), or — for objectives "
            "that accept a continuous target, such as a binomial GLM family — set "
            "the reported metrics explicitly to regression metrics (e.g. gini, "
            "rmse) to keep the continuous target."
        )
    if not classification_task:
        return None
    return (
        f"Target column '{target}' has type {dtype}, which cannot be used as "
        "classification class labels. Choose a target column with discrete class "
        "labels (e.g. a 0/1 claim flag or a category column), or set the task to "
        "regression."
    )
