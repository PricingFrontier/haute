"""Eager scoring must execute the upstream plan exactly once (W2 4a.2).

The pre-fix eager path collected ``lf.select(features)`` to compute
predictions, then attached those predictions to the ORIGINAL lazy plan —
so collecting the returned frame re-executed every upstream op a second
time and spliced predictions positionally onto whatever row order that
second execution produced.  An order-unstable upstream op (``group_by``
without ``maintain_order=True``, ``unique``, a streaming join) silently
lands predictions on the wrong rows: wrong scores = wrong prices.

These tests pin both layers of the fix:

* the mechanism — the upstream plan executes exactly once, counted at an
  opaque ``map_batches`` boundary (this is the deterministic RED even
  when polars happens to return a stable order in-test); and
* the outcome — row alignment holds even when each plan execution
  returns the same rows in a different order (simulated determinis-
  tically with a stateful reordering node, which is exactly the
  behaviour polars reserves the right to exhibit for ``group_by``
  without ``maintain_order``).

The batched path is pinned unchanged: it already sinks the upstream plan
exactly once and joins predictions within each materialised chunk.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import ScoringModel
from haute._model_scorer import (
    FeatureMismatchError,
    _run_score_pipeline,
    score_frame,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UpstreamSpy:
    """Wrap a DataFrame in a lazy plan with an opaque, call-counting node.

    ``map_batches`` with an explicit schema and every pushdown disabled is
    an optimisation barrier: the function body runs once per execution of
    the plan, never for schema resolution.  ``reorder_every_other=True``
    additionally returns the row-reversed frame on every second execution
    — a deterministic stand-in for an order-unstable upstream op.
    """

    def __init__(self, df: pl.DataFrame, *, reorder_every_other: bool = False) -> None:
        self._df = df
        self._reorder = reorder_every_other
        self.executions = 0

    def lazy(self) -> pl.LazyFrame:
        def _node(batch: pl.DataFrame) -> pl.DataFrame:
            self.executions += 1
            if self._reorder and self.executions % 2 == 0:
                return batch.reverse()
            return batch

        return self._df.lazy().map_batches(
            _node,
            schema=dict(self._df.schema),
            predicate_pushdown=False,
            projection_pushdown=False,
            slice_pushdown=False,
            streamable=False,
        )


def _times_ten_model() -> MagicMock:
    """A pyfunc-shaped mock whose prediction is a pure function of its input.

    ``pred(row) == 10 * a(row)`` lets every test assert row alignment
    structurally: if predictions were computed from one execution of the
    plan and attached to another, some row will violate the identity.
    """
    model = MagicMock()
    model.predict.side_effect = lambda x: np.asarray(x, dtype=np.float64).flatten() * 10.0
    del model.predict_proba
    return model


def _assert_rowwise_alignment(result: pl.DataFrame, feature: str, output: str) -> None:
    np.testing.assert_allclose(
        result[output].to_numpy(),
        result[feature].to_numpy() * 10.0,
        err_msg=(
            "Predictions are misaligned with their input rows — they were "
            "computed from one execution of the upstream plan and attached "
            "to another."
        ),
    )


# ---------------------------------------------------------------------------
# score_frame(batch=False) — the unified eager path
# ---------------------------------------------------------------------------


class TestScoreFrameEagerSingleExecution:
    def test_upstream_executes_exactly_once(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0, 3.0], "keep": ["x", "y", "z"]}))

        result = score_frame(
            model=_times_ten_model(),
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
        ).collect()

        assert spy.executions == 1, (
            f"Eager scoring executed the upstream plan {spy.executions} times; "
            "it must collect once and attach predictions to that same "
            "materialised frame."
        )
        _assert_rowwise_alignment(result, "a", "pred")
        assert result["keep"].to_list() == ["x", "y", "z"]

    def test_alignment_survives_order_unstable_upstream(self) -> None:
        """Even if a second execution WOULD return a different row order,
        predictions land on the rows they were computed from."""
        spy = _UpstreamSpy(
            pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}),
            reorder_every_other=True,
        )

        result = score_frame(
            model=_times_ten_model(),
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
        ).collect()

        _assert_rowwise_alignment(result, "a", "pred")

    def test_classification_proba_aligned_under_order_unstable_upstream(self) -> None:
        spy = _UpstreamSpy(
            pl.DataFrame({"a": [10.0, 20.0, 30.0, 40.0]}),
            reorder_every_other=True,
        )
        model = MagicMock()
        model.predict.side_effect = lambda x: (
            np.asarray(x, dtype=np.float64).flatten() > 25.0
        ).astype(np.float64)

        def _proba(x: Any) -> np.ndarray:
            flat = np.asarray(x, dtype=np.float64).flatten() / 100.0
            return np.column_stack([1.0 - flat, flat])

        model.predict_proba.side_effect = _proba

        result = score_frame(
            model=model,
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="classification",
            output_col="pred",
            batch=False,
        ).collect()

        assert spy.executions == 1
        np.testing.assert_allclose(
            result["pred_proba"].to_numpy(),
            result["a"].to_numpy() / 100.0,
            err_msg="Probability column misaligned with its input rows.",
        )
        np.testing.assert_allclose(
            result["pred"].to_numpy(),
            (result["a"].to_numpy() > 25.0).astype(np.float64),
        )

    def test_write_projection_single_execution_and_columns_pinned(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0], "keep": [7.0, 8.0], "drop": [0.0, 0.0]}))

        result = score_frame(
            model=_times_ten_model(),
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
            required_output_columns=frozenset({"keep", "pred"}),
        ).collect()

        assert spy.executions == 1
        assert result.columns == ["keep", "pred"]
        np.testing.assert_allclose(result["pred"].to_numpy(), [10.0, 20.0])
        assert result["keep"].to_list() == [7.0, 8.0]

    def test_categorical_levels_validated_once_against_scored_rows(self) -> None:
        """Domain validation must not trigger an extra execution of the
        upstream plan — it runs against the same materialised frame that
        is scored."""
        spy = _UpstreamSpy(pl.DataFrame({"region": ["north", "south"]}))
        model = MagicMock()
        model.predict.side_effect = lambda pdf: np.full(len(pdf), 1.5, dtype=np.float64)
        del model.predict_proba

        result = score_frame(
            model=model,
            lf=spy.lazy(),
            features=["region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
            task="regression",
            output_col="pred",
            batch=False,
            categorical_levels={"region": ["north", "south"]},
        ).collect()

        assert spy.executions == 1, (
            f"Eager scoring with categorical domains executed the upstream plan "
            f"{spy.executions} times; validation must reuse the single "
            "materialised frame."
        )
        assert result["pred"].to_list() == [1.5, 1.5]

    def test_categorical_domain_violation_still_raises_before_predict(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"region": ["north", "west"]}))
        model = MagicMock()

        with pytest.raises(FeatureMismatchError, match="outside declared"):
            score_frame(
                model=model,
                lf=spy.lazy(),
                features=["region"],
                cat_feature_names=frozenset({"region"}),
                flavor="catboost",
                task="regression",
                output_col="pred",
                batch=False,
                categorical_levels={"region": ["north", "south"]},
            )

        model.predict.assert_not_called()

    def test_empty_frame_single_execution(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)}))
        model = MagicMock()
        model.predict.side_effect = lambda x: np.asarray(x, dtype=np.float64).flatten()
        del model.predict_proba

        result = score_frame(
            model=model,
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
        ).collect()

        assert spy.executions == 1
        assert result.height == 0
        assert "pred" in result.columns

    def test_result_stays_lazy_and_column_order_pinned(self) -> None:
        """Behaviour pin: the eager path still returns a LazyFrame whose
        schema is the input columns in order plus the prediction column."""
        spy = _UpstreamSpy(pl.DataFrame({"b": [4.0], "a": [1.0]}))

        result_lf = score_frame(
            model=_times_ten_model(),
            lf=spy.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
        )

        assert isinstance(result_lf, pl.LazyFrame)
        assert result_lf.collect().columns == ["b", "a", "pred"]


# ---------------------------------------------------------------------------
# _run_score_pipeline — the live / preview entry used by executor + deploy
# ---------------------------------------------------------------------------


class TestRunScorePipelineLiveSingleExecution:
    def _scoring_model(self) -> ScoringModel:
        return ScoringModel(
            model=_times_ten_model(),
            feature_names=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
        )

    def test_live_source_executes_upstream_exactly_once(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0, 3.0]}))

        result = _run_score_pipeline(
            self._scoring_model(),
            spy.lazy(),
            task="regression",
            output_col="pred",
            source="live",
        ).collect()

        assert spy.executions == 1, (
            f"Live scoring executed the upstream plan {spy.executions} times."
        )
        _assert_rowwise_alignment(result, "a", "pred")

    def test_live_alignment_survives_order_unstable_upstream(self) -> None:
        spy = _UpstreamSpy(
            pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]}),
            reorder_every_other=True,
        )

        result = _run_score_pipeline(
            self._scoring_model(),
            spy.lazy(),
            task="regression",
            output_col="pred",
            source="live",
        ).collect()

        _assert_rowwise_alignment(result, "a", "pred")

    def test_row_limit_forced_eager_executes_upstream_exactly_once(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0]}))

        result = _run_score_pipeline(
            self._scoring_model(),
            spy.lazy(),
            task="regression",
            output_col="pred",
            source="batch",
            row_limit=10,
        ).collect()

        assert spy.executions == 1
        _assert_rowwise_alignment(result, "a", "pred")

    def test_group_by_without_maintain_order_single_execution_and_alignment(self) -> None:
        """The canonical order-unstable upstream: a user ``group_by`` without
        ``maintain_order``.  Polars makes no ordering promise across
        executions, so the only safe contract is a single execution — pinned
        here by the source counter — with predictions attached to that same
        materialisation."""
        source = pl.DataFrame(
            {
                "g": ["a", "b", "a", "c", "b", "c", "d", "d"],
                "v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            }
        )
        spy = _UpstreamSpy(source)
        lf = spy.lazy().group_by("g").agg(pl.col("v").sum().alias("total"))

        scoring_model = ScoringModel(
            model=MagicMock(
                predict=MagicMock(
                    side_effect=lambda x: np.asarray(x, dtype=np.float64).flatten() * 10.0
                ),
                spec=["predict"],
            ),
            feature_names=["total"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
        )

        result = _run_score_pipeline(
            scoring_model,
            lf,
            task="regression",
            output_col="pred",
            source="live",
        ).collect()

        assert spy.executions == 1, (
            f"group_by upstream executed {spy.executions} times — a second "
            "execution may legally return groups in a different order, "
            "silently landing predictions on the wrong rows."
        )
        np.testing.assert_allclose(
            result["pred"].to_numpy(),
            result["total"].to_numpy() * 10.0,
            err_msg="Predictions misaligned with their group rows.",
        )
        assert dict(zip(result["g"].to_list(), result["total"].to_list())) == {
            "a": 4.0,
            "b": 7.0,
            "c": 10.0,
            "d": 15.0,
        }

    def test_live_with_categorical_levels_single_execution(self) -> None:
        """Domain validation in the live path must reuse the materialised
        frame instead of running a separate narrow collection of the plan."""
        spy = _UpstreamSpy(pl.DataFrame({"region": ["north", "south", "north"]}))
        model = MagicMock()
        model.predict.side_effect = lambda pdf: np.full(len(pdf), 2.5, dtype=np.float64)
        del model.predict_proba
        scoring_model = ScoringModel(
            model=model,
            feature_names=["region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
        )

        result = _run_score_pipeline(
            scoring_model,
            spy.lazy(),
            task="regression",
            output_col="pred",
            source="live",
            categorical_levels={"region": ["north", "south"]},
        ).collect()

        assert spy.executions == 1, (
            f"Live scoring with categorical domains executed the upstream plan "
            f"{spy.executions} times."
        )
        assert result["pred"].to_list() == [2.5, 2.5, 2.5]

    def test_live_categorical_violation_raises_before_scoring(self) -> None:
        spy = _UpstreamSpy(pl.DataFrame({"region": ["west"]}))
        model = MagicMock()
        scoring_model = ScoringModel(
            model=model,
            feature_names=["region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
        )

        with pytest.raises(FeatureMismatchError, match="outside declared"):
            _run_score_pipeline(
                scoring_model,
                spy.lazy(),
                task="regression",
                output_col="pred",
                source="live",
                categorical_levels={"region": ["north", "south"]},
            )

        model.predict.assert_not_called()

    def test_user_post_processing_code_sees_materialised_result(self) -> None:
        """User code downstream of scoring must not trigger a re-execution
        of the upstream plan when it is finally collected."""
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0]}))

        result = _run_score_pipeline(
            self._scoring_model(),
            spy.lazy(),
            task="regression",
            output_col="pred",
            code="df = df.with_columns((pl.col('pred') * 2).alias('pred2'))",
            source_names=["df0"],
            source="live",
        ).collect()

        assert spy.executions == 1
        np.testing.assert_allclose(result["pred2"].to_numpy(), [20.0, 40.0])


# ---------------------------------------------------------------------------
# Batched path — pinned unchanged
# ---------------------------------------------------------------------------


class TestBatchedPathPinnedUnchanged:
    def test_batch_source_executes_upstream_exactly_once(self) -> None:
        """The batched path already sinks the plan exactly once; pin it so
        the eager fix cannot regress the other branch."""
        spy = _UpstreamSpy(pl.DataFrame({"a": [1.0, 2.0, 3.0]}))
        scoring_model = ScoringModel(
            model=_times_ten_model(),
            feature_names=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
        )

        result = _run_score_pipeline(
            scoring_model,
            spy.lazy(),
            task="regression",
            output_col="pred",
            source="batch",
        ).collect()

        assert spy.executions == 1
        _assert_rowwise_alignment(result, "a", "pred")

    def test_eager_and_batch_outputs_identical(self) -> None:
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "keep": [9.0, 8.0, 7.0, 6.0]})

        def _make() -> ScoringModel:
            return ScoringModel(
                model=_times_ten_model(),
                feature_names=["a"],
                cat_feature_names=frozenset(),
                flavor="pyfunc",
            )

        eager = _run_score_pipeline(
            _make(), df.lazy(), task="regression", output_col="pred", source="live"
        ).collect()
        batched = _run_score_pipeline(
            _make(), df.lazy(), task="regression", output_col="pred", source="batch"
        ).collect()

        assert eager.columns == batched.columns
        assert eager.equals(batched)
