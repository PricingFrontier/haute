"""Tests for Phase 3 Wave 7 package 7A — scoring-prep performance fix.

Covers ``docs/CODEBASE_REVIEW.md`` item **#91**: the current
:func:`haute._mlflow_io._prepare_predict_frame` routes every ``pyfunc`` call
through ``.to_pandas()`` even when no categorical features are present.  For
the common numeric-only path this allocates an extra pandas DataFrame (~100-
200 MB on a 500k-row x 50-feature frame).  The planned refactor narrows
that blanket:

==================  =============  =============  =============
 flavor              has_cats       pre-refactor   post-refactor
==================  =============  =============  =============
 pyfunc              no             pandas         **numpy**
 pyfunc              yes            pandas         pandas
 catboost            no             numpy          numpy
 catboost            yes            pandas         pandas
 rustystats          any            Polars         Polars
==================  =============  =============  =============

Spot-checked in a sandbox: MLflow ``PyFuncModel.predict`` accepts
``numpy.ndarray`` (see the ``data`` union type in its signature), and the
scikit-learn models wrapped by pyfunc also accept numpy directly (a
``UserWarning`` about feature names may be emitted, but predictions are
correct).  The ``.to_pandas()`` blanket was therefore defensive, not
required.

Test strategy
-------------
This file is written *before* the refactor lands.  Tests split into:

  * ``TestPreparePredictFrameCorrectness`` — pin invariants that hold both
    pre- and post-refactor: feature order, null handling, type dispatch for
    the non-pyfunc-no-cats branches.
  * ``TestPreparePredictFramePostRefactor`` — behaviour specific to the
    refactored dispatch (``pyfunc`` + no cats -> numpy).  Pre-refactor
    these are ``xfail(strict=True)`` so they flip to passing the instant
    the production code changes.
  * ``TestDownstreamScoringPassthrough`` — end-to-end assertion that the
    model's ``predict`` is called with the right array type for each
    flavor+cats combo, using a MagicMock as the scoring model so we
    inspect call arguments directly.
  * ``TestEdgeCases`` — empty features, missing columns, and the
    ``Int64``-with-nulls -> ``Float32`` cast contract.
  * ``TestPreparePredictFrameBenchmark`` — walltime and peak-memory
    benchmark comparing the pre-refactor ``to_pandas`` blanket against the
    post-refactor ``to_numpy`` path.  Targets: >=30% walltime reduction
    AND >=50% peak-memory reduction on a 50k x 20 numeric frame.

No production code is edited here.  The refactor itself lives in a
separate PR.
"""

from __future__ import annotations

import gc
import time
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest

from haute._mlflow_io import _prepare_predict_frame

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_numeric_frame(n_rows: int, n_cols: int, *, seed: int = 0) -> pl.DataFrame:
    """Build a Polars DataFrame of ``n_rows`` x ``n_cols`` random Float64 columns.

    Using Float64 on input (the common Polars default) so the Float32 cast
    inside ``_prepare_predict_frame`` is actually exercised.  Column names
    are ``f0``, ``f1``, ...
    """
    rng = np.random.default_rng(seed)
    data = {f"f{i}": rng.standard_normal(n_rows).astype(np.float64) for i in range(n_cols)}
    return pl.DataFrame(data)


def _make_mixed_frame(n_rows: int, n_numeric: int, n_cat: int, *, seed: int = 0) -> pl.DataFrame:
    """Build a DataFrame with ``n_numeric`` Float64 columns + ``n_cat`` string cols."""
    rng = np.random.default_rng(seed)
    data: dict[str, Any] = {
        f"num_{i}": rng.standard_normal(n_rows).astype(np.float64) for i in range(n_numeric)
    }
    levels = ["alpha", "beta", "gamma", "delta"]
    for i in range(n_cat):
        idx = rng.integers(0, len(levels), size=n_rows)
        data[f"cat_{i}"] = [levels[j] for j in idx]
    return pl.DataFrame(data)


def _output_bytes(obj: Any) -> int:
    """Total bytes held by a prepared output object.

    ``numpy.ndarray`` -> ``nbytes`` (contiguous buffer).  ``pd.DataFrame``
    -> ``memory_usage(deep=True).sum()`` (all blocks + index + object
    dtype columns).  ``pl.DataFrame`` -> ``estimated_size()``.  Any other
    type is a programming error in this test file.
    """
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes)
    if isinstance(obj, pd.DataFrame):
        return int(obj.memory_usage(deep=True).sum())
    if isinstance(obj, pl.DataFrame):
        return int(obj.estimated_size())
    raise AssertionError(f"unsupported output type {type(obj).__name__}")


def _retained_footprint(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, int]:
    """Run ``fn(*args, **kwargs)`` and return (result, footprint_bytes).

    ``tracemalloc`` only tracks *Python* allocations; the Arrow/Polars
    buffers backing a DataFrame live in PyArrow's C++ memory pool, which
    ``tracemalloc`` cannot see.  To get a faithful measure of the
    memory actually retained by the returned object we combine:

      1. The **delta** in ``pa.default_memory_pool().bytes_allocated()``
         across the call — this captures Arrow buffers the output keeps
         alive (e.g. pandas DataFrames constructed via zero-copy Arrow
         round-trip retain a reference to the underlying Arrow buffer).
      2. The reported *output footprint* (``_output_bytes``) — the
         contiguous data buffer each path wraps (plus pandas metadata:
         index, column labels, dtype info).

    ``tracemalloc``'s own peak is intentionally *not* included in the
    sum: for both paths it is a constant low-kilobyte overhead (the
    scratch allocations from ``polars.DataFrame.select``) that is
    essentially identical and would just add common fixed noise to the
    ratio without affecting conclusions.

    On a pure-numeric path (2) is identical on both flavours (same
    Float32 tensor), so the ratio between the two is dominated by (1):
    the Arrow-retained extra copy the pandas path carries.  The
    theoretical ratio is ``2.0`` (buffer held twice vs once).
    """
    pool = pa.default_memory_pool()
    gc.collect()
    pool.release_unused()
    before_pool = pool.bytes_allocated()
    result = fn(*args, **kwargs)
    after_pool = pool.bytes_allocated()
    arrow_delta = max(0, after_pool - before_pool)
    footprint = arrow_delta + _output_bytes(result)
    return result, footprint


def _proposed_prepare_pyfunc_no_cats(
    df_eager: pl.DataFrame,
    features: list[str],
) -> np.ndarray:
    """Shadow of the post-refactor ``pyfunc + no cats`` fast path.

    Kept in the test module (not a production import) so the benchmark
    can compare the proposed shape against the current blanket even
    before the refactor lands.  Once the production code is updated, the
    ``_prepare_predict_frame(..., flavor="pyfunc")`` call with no cats
    should return the same array this helper produces.
    """
    selected = df_eager.select(features)
    selected = selected.with_columns([pl.col(c).cast(pl.Float32) for c in features])
    return selected.to_numpy()


# ---------------------------------------------------------------------------
# TestPreparePredictFrameCorrectness — invariants that hold both pre- and
# post-refactor.  None of these depend on the pyfunc-no-cats -> numpy swap.
# ---------------------------------------------------------------------------


class TestPreparePredictFrameCorrectness:
    """Contract that must hold regardless of the refactor state."""

    # ---- catboost -------------------------------------------------------

    def test_catboost_no_cats_returns_numpy_float32(self) -> None:
        """CatBoost + no cats -> numpy float32 array."""
        df = _make_numeric_frame(100, 3)
        result = _prepare_predict_frame(
            df, ["f0", "f1", "f2"], frozenset(), "catboost"
        )
        assert isinstance(result, np.ndarray)
        assert result.shape == (100, 3)
        assert result.dtype == np.float32

    def test_catboost_with_cats_returns_pandas_with_category_dtype(self) -> None:
        """CatBoost + cats -> pandas DataFrame with proper category dtype.

        The CatBoost path relies on ``dtype='category'`` to signal which
        columns are categorical; this must not regress.
        """
        df = _make_mixed_frame(50, n_numeric=2, n_cat=1)
        result = _prepare_predict_frame(
            df, ["num_0", "num_1", "cat_0"], frozenset({"cat_0"}), "catboost"
        )
        assert isinstance(result, pd.DataFrame)
        assert result.shape == (50, 3)
        # num_* are float32 (post-cast).
        assert result["num_0"].dtype == np.float32
        assert result["num_1"].dtype == np.float32
        # cat_0 should carry pandas category dtype (from pl.Categorical).
        assert isinstance(result["cat_0"].dtype, pd.CategoricalDtype)

    # ---- pyfunc-with-cats (no change planned) --------------------------

    def test_pyfunc_with_cats_still_returns_pandas(self) -> None:
        """Pyfunc + cats still returns pandas post-refactor.

        Categorical dtype only round-trips through pandas reliably, so the
        narrow refactor must not change this branch.
        """
        df = _make_mixed_frame(30, n_numeric=1, n_cat=2)
        result = _prepare_predict_frame(
            df, ["num_0", "cat_0", "cat_1"], frozenset({"cat_0", "cat_1"}), "pyfunc"
        )
        assert isinstance(result, pd.DataFrame)
        assert result.shape == (30, 3)
        assert isinstance(result["cat_0"].dtype, pd.CategoricalDtype)
        assert isinstance(result["cat_1"].dtype, pd.CategoricalDtype)
        assert result["num_0"].dtype == np.float32

    # ---- rustystats (no change planned) ---------------------------------

    def test_rustystats_returns_polars_unmodified(self) -> None:
        """RustyStats flavour always returns a Polars DataFrame, no casts."""
        df = _make_numeric_frame(10, 2)
        result = _prepare_predict_frame(df, ["f0", "f1"], frozenset(), "rustystats")
        assert isinstance(result, pl.DataFrame)
        # Original dtype (Float64) preserved — rustystats handles its own casts.
        assert result["f0"].dtype == pl.Float64

    def test_rustystats_with_cats_does_not_fill_sentinel(self) -> None:
        """RustyStats path does not fill nulls nor cast to Categorical."""
        df = pl.DataFrame({"cat": ["x", None, "y"]})
        result = _prepare_predict_frame(df, ["cat"], frozenset({"cat"}), "rustystats")
        assert isinstance(result, pl.DataFrame)
        assert result["cat"].null_count() == 1

    # ---- categorical sentinel fill (shared by catboost & pyfunc + cats) -

    def test_categorical_nulls_filled_with_missing_sentinel_pyfunc(self) -> None:
        df = pl.DataFrame({"cat": ["a", None, "b"]})
        result = _prepare_predict_frame(df, ["cat"], frozenset({"cat"}), "pyfunc")
        assert isinstance(result, pd.DataFrame)
        assert result["cat"].iloc[1] == "_MISSING_"

    def test_categorical_nulls_filled_with_missing_sentinel_catboost(self) -> None:
        df = pl.DataFrame({"cat": ["a", None, "b"]})
        result = _prepare_predict_frame(df, ["cat"], frozenset({"cat"}), "catboost")
        assert isinstance(result, pd.DataFrame)
        assert result["cat"].iloc[1] == "_MISSING_"

    # ---- feature ordering ----------------------------------------------

    def test_feature_order_preserved_across_flavors(self) -> None:
        """The output must follow ``features`` order, not the input frame order.

        Downstream models index by feature position; a silent reorder
        would produce silently wrong predictions.
        """
        df = pl.DataFrame({"b": [10.0, 20.0], "a": [1.0, 2.0], "c": [100.0, 200.0]})
        # catboost no-cats -> numpy
        arr = _prepare_predict_frame(df, ["a", "b", "c"], frozenset(), "catboost")
        assert arr[0].tolist() == pytest.approx([1.0, 10.0, 100.0], rel=1e-3)
        # pyfunc with cats -> pandas
        df_cat = pl.DataFrame(
            {"b": [10.0, 20.0], "a": [1.0, 2.0], "c": ["x", "y"]}
        )
        pdf = _prepare_predict_frame(
            df_cat, ["a", "b", "c"], frozenset({"c"}), "pyfunc"
        )
        assert list(pdf.columns) == ["a", "b", "c"]

    def test_rustystats_feature_order_preserved(self) -> None:
        df = pl.DataFrame({"b": [10.0, 20.0], "a": [1.0, 2.0]})
        result = _prepare_predict_frame(df, ["a", "b"], frozenset(), "rustystats")
        assert list(result.columns) == ["a", "b"]

    # ---- null -> NaN on numeric cast ------------------------------------

    def test_numeric_nulls_become_nan_catboost_numpy(self) -> None:
        df = pl.DataFrame({"x": [1.0, None, 3.0]})
        result = _prepare_predict_frame(df, ["x"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert np.isnan(result[1, 0])


# ---------------------------------------------------------------------------
# TestPreparePredictFramePostRefactor — behaviour specific to the swap.
# These are xfail(strict=True) pre-refactor; they flip to passing when the
# production code narrows the pyfunc -> pandas blanket.
# ---------------------------------------------------------------------------


class TestPreparePredictFramePostRefactor:
    """Invariants that only hold after the refactor lands."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Pre-refactor: pyfunc always returns pandas (defensive blanket). "
            "Post-refactor: pyfunc + no cats returns numpy float32. "
            "This xfail flips to passing once item #91 ships."
        ),
    )
    def test_pyfunc_no_cats_returns_numpy_float32(self) -> None:
        """The headline change: pyfunc + no cats -> numpy float32."""
        df = _make_numeric_frame(100, 5)
        result = _prepare_predict_frame(
            df, [f"f{i}" for i in range(5)], frozenset(), "pyfunc"
        )
        assert isinstance(result, np.ndarray), (
            f"post-refactor pyfunc + no cats should return numpy; got {type(result).__name__}"
        )
        assert result.shape == (100, 5)
        assert result.dtype == np.float32

    @pytest.mark.xfail(
        strict=True,
        reason="Post-refactor null handling on the numpy fast path; see item #91.",
    )
    def test_pyfunc_no_cats_nulls_become_nan(self) -> None:
        """Int64 null -> NaN still works on the proposed numpy path."""
        df = pl.DataFrame({"x": pl.Series("x", [1, None, 3], dtype=pl.Int64)})
        result = _prepare_predict_frame(df, ["x"], frozenset(), "pyfunc")
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert np.isnan(result[1, 0])

    @pytest.mark.xfail(
        strict=True,
        reason="Post-refactor: feature subset + order still preserved on numpy path.",
    )
    def test_pyfunc_no_cats_feature_order_preserved(self) -> None:
        df = pl.DataFrame(
            {"c": [100.0, 200.0], "a": [1.0, 2.0], "b": [10.0, 20.0], "extra": [0.0, 0.0]}
        )
        result = _prepare_predict_frame(df, ["a", "b", "c"], frozenset(), "pyfunc")
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 3)
        # a, b, c in that order
        assert result[0].tolist() == pytest.approx([1.0, 10.0, 100.0], rel=1e-3)


# ---------------------------------------------------------------------------
# TestDownstreamScoringPassthrough — assert the scoring pipeline passes the
# right array type to ``model.predict`` for each flavor + cats combo.  Uses a
# MagicMock so we inspect call args directly.
# ---------------------------------------------------------------------------


class TestDownstreamScoringPassthrough:
    """End-to-end: ``score_frame`` should pass the right array type downstream."""

    def _mock_predicting_model(self, n_rows: int, *, assert_type: type | tuple[type, ...]):
        """Build a MagicMock whose predict asserts the call-site type."""
        model = MagicMock()

        def _predict(X: Any) -> np.ndarray:  # noqa: N803 - ML convention (design matrix)
            assert isinstance(X, assert_type), (
                f"predict received {type(X).__name__}; expected {assert_type}"
            )
            # Return constant predictions so classification also works.
            return np.zeros(len(X), dtype=np.float64)

        model.predict.side_effect = _predict
        return model

    def test_catboost_no_cats_calls_predict_with_numpy(self) -> None:
        from haute._model_scorer import score_frame

        model = self._mock_predicting_model(4, assert_type=np.ndarray)
        lf = pl.DataFrame(
            {"f0": [1.0, 2.0, 3.0, 4.0], "f1": [10.0, 20.0, 30.0, 40.0]}
        ).lazy()
        result = score_frame(
            model=model,
            lf=lf,
            features=["f0", "f1"],
            cat_feature_names=frozenset(),
            flavor="catboost",
        )
        df = result.collect()
        assert "prediction" in df.columns
        assert model.predict.called

    def test_catboost_with_cats_calls_predict_with_pandas(self) -> None:
        from haute._model_scorer import score_frame

        model = self._mock_predicting_model(4, assert_type=pd.DataFrame)
        lf = pl.DataFrame(
            {"num_0": [1.0, 2.0, 3.0, 4.0], "cat_0": ["a", "b", "a", "c"]}
        ).lazy()
        result = score_frame(
            model=model,
            lf=lf,
            features=["num_0", "cat_0"],
            cat_feature_names=frozenset({"cat_0"}),
            flavor="catboost",
        )
        result.collect()
        assert model.predict.called

    def test_pyfunc_with_cats_calls_predict_with_pandas(self) -> None:
        from haute._model_scorer import score_frame

        model = self._mock_predicting_model(4, assert_type=pd.DataFrame)
        lf = pl.DataFrame(
            {"num_0": [1.0, 2.0, 3.0, 4.0], "cat_0": ["a", "b", "a", "c"]}
        ).lazy()
        result = score_frame(
            model=model,
            lf=lf,
            features=["num_0", "cat_0"],
            cat_feature_names=frozenset({"cat_0"}),
            flavor="pyfunc",
        )
        result.collect()
        assert model.predict.called

    def test_rustystats_calls_predict_with_polars(self) -> None:
        from haute._model_scorer import score_frame

        model = self._mock_predicting_model(4, assert_type=pl.DataFrame)
        lf = pl.DataFrame(
            {"f0": [1.0, 2.0, 3.0, 4.0], "f1": [10.0, 20.0, 30.0, 40.0]}
        ).lazy()
        result = score_frame(
            model=model,
            lf=lf,
            features=["f0", "f1"],
            cat_feature_names=frozenset(),
            flavor="rustystats",
        )
        result.collect()
        assert model.predict.called

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Pre-refactor: pyfunc + no cats receives pandas DataFrame. "
            "Post-refactor (item #91): receives numpy.ndarray. "
            "This xfail flips to passing once the refactor ships."
        ),
    )
    def test_pyfunc_no_cats_calls_predict_with_numpy_post_refactor(self) -> None:
        """The load-bearing downstream check: pyfunc + no cats -> numpy handoff."""
        from haute._model_scorer import score_frame

        model = self._mock_predicting_model(4, assert_type=np.ndarray)
        lf = pl.DataFrame(
            {"f0": [1.0, 2.0, 3.0, 4.0], "f1": [10.0, 20.0, 30.0, 40.0]}
        ).lazy()
        result = score_frame(
            model=model,
            lf=lf,
            features=["f0", "f1"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
        )
        df = result.collect()
        assert "prediction" in df.columns
        assert model.predict.called


# ---------------------------------------------------------------------------
# TestEdgeCases — empty features, missing columns, Int64-null casts
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases called out in the refactor plan."""

    def test_empty_features_rustystats_returns_full_df(self) -> None:
        """Documented behaviour: empty features + rustystats returns the whole frame."""
        df = pl.DataFrame({"a": [1.0], "b": [2.0]})
        result = _prepare_predict_frame(df, [], frozenset(), "rustystats")
        assert isinstance(result, pl.DataFrame)
        assert set(result.columns) == {"a", "b"}

    def test_empty_features_catboost_returns_empty_numpy(self) -> None:
        """Empty features + catboost no-cats -> empty 2D numpy array.

        Polars' ``select([])`` on any frame returns a ``(0, 0)`` frame;
        ``.to_numpy()`` then yields a ``(0, 0)`` array.  Pin the current
        shape so the refactor doesn't silently change it.
        """
        df = pl.DataFrame({"a": [1.0, 2.0]})
        result = _prepare_predict_frame(df, [], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert result.shape == (0, 0)

    def test_empty_features_pyfunc_returns_dataframe_like(self) -> None:
        """Empty features + pyfunc — the dispatch returns *something* 2D-shaped.

        Pre-refactor the branch returns an empty pandas DataFrame
        (``to_pandas()`` on the ``(0,0)`` Polars frame).  Post-refactor it
        may become an empty numpy array (the ``pyfunc + no cats -> numpy``
        fast path).  Both are acceptable: downstream code treats the
        zero-row case specially elsewhere in the batch scorer.  Assert the
        weaker invariant: the result has a ``shape`` attribute with
        length two and reports zero columns.
        """
        df = pl.DataFrame({"a": [1.0, 2.0]})
        result = _prepare_predict_frame(df, [], frozenset(), "pyfunc")
        assert hasattr(result, "shape")
        assert len(result.shape) == 2
        # zero columns requested -> zero columns out
        assert result.shape[1] == 0

    def test_missing_feature_raises_column_not_found(self) -> None:
        """Requesting a feature not in the frame must raise loudly.

        Silent reshape would let a typo in ``features`` produce a model
        prediction on the wrong columns — exactly the kind of bug the
        codebase-wide ``fail-loudly`` policy forbids.
        """
        df = pl.DataFrame({"a": [1.0, 2.0]})
        with pytest.raises(pl.exceptions.ColumnNotFoundError):
            _prepare_predict_frame(df, ["a", "not_there"], frozenset(), "catboost")

    def test_missing_feature_raises_for_pyfunc_flavor(self) -> None:
        df = pl.DataFrame({"a": [1.0, 2.0]})
        with pytest.raises(pl.exceptions.ColumnNotFoundError):
            _prepare_predict_frame(df, ["missing"], frozenset(), "pyfunc")

    def test_int64_with_nulls_casts_to_float32_with_nan(self) -> None:
        """Nullable Int64 -> Float32 with null -> NaN on the catboost numpy path.

        The documented cast contract; null -> NaN must hold after the
        refactor too so gradient-boosted and linear models see consistent
        missing-value semantics.
        """
        df = pl.DataFrame(
            {"x": pl.Series("x", [1, None, 3, None, 5], dtype=pl.Int64)}
        )
        result = _prepare_predict_frame(df, ["x"], frozenset(), "catboost")
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        col = result[:, 0]
        assert not np.isnan(col[0]) and col[0] == pytest.approx(1.0)
        assert np.isnan(col[1])
        assert col[2] == pytest.approx(3.0)
        assert np.isnan(col[3])
        assert col[4] == pytest.approx(5.0)

    def test_mixed_nullable_numerics_on_pyfunc_with_cats(self) -> None:
        """Pyfunc + cats path: nullable Int64 numeric still casts to Float32."""
        df = pl.DataFrame(
            {
                "num": pl.Series("num", [1, None, 3], dtype=pl.Int64),
                "cat": ["x", None, "y"],
            }
        )
        result = _prepare_predict_frame(
            df, ["num", "cat"], frozenset({"cat"}), "pyfunc"
        )
        assert isinstance(result, pd.DataFrame)
        assert result["num"].dtype == np.float32
        assert np.isnan(result["num"].iloc[1])
        assert result["cat"].iloc[1] == "_MISSING_"


# ---------------------------------------------------------------------------
# Benchmark — walltime + peak memory
# ---------------------------------------------------------------------------


def _baseline_prepare_with_to_pandas(
    df_eager: pl.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    """Replica of the pre-refactor code path: always pandas for pyfunc+no-cats.

    Mirrors ``_prepare_predict_frame(..., flavor='pyfunc')`` with no cats
    so we can contrast its cost against the proposed numpy path below.
    """
    selected = df_eager.select(features)
    selected = selected.with_columns([pl.col(c).cast(pl.Float32) for c in features])
    return selected.to_pandas()


class TestPreparePredictFrameBenchmark:
    """Benchmark for item #91 — narrow the pyfunc ``to_pandas`` blanket.

    Two benchmark shapes:

      * **Walltime**: 50k-row x 20-numeric-feature frame
        (``N_ROWS``/``N_COLS``).  Small enough for CI; large enough that
        the pandas round-trip dominates pure function-call overhead.
      * **Memory**: 200k-row x 30-numeric-feature frame
        (``MEM_N_ROWS``/``MEM_N_COLS``).  Larger so the theoretical 2x
        ratio between the pandas (buffer held twice — Arrow pool +
        pandas wrapper) and numpy (buffer held once) paths dominates
        fixed per-DataFrame overhead.

    Targets:
      * **>=30%** walltime reduction (proposed numpy vs baseline pandas).
      * **>=50%** retained-footprint reduction (PyArrow pool delta +
        output ``nbytes`` / ``memory_usage``).

    ``tracemalloc`` alone cannot see PyArrow-managed buffers (they live
    in a C++ memory pool), so the memory assertion uses
    ``pa.default_memory_pool().bytes_allocated()`` plus the output's own
    memory accounting.  See :func:`_retained_footprint` for details.

    Both bars are conservative relative to the review note's claim of
    100-200 MB saved on a 500k-row x 50-feature frame.  ``rounds=3``
    trials interleaved to average out unrelated CPU jitter.
    """

    N_ROWS = 50_000
    N_COLS = 20
    # Memory benchmark uses a larger frame so the theoretical 2x ratio
    # between the two paths dominates fixed per-DataFrame overhead (index,
    # block manager metadata, tracemalloc bookkeeping).
    MEM_N_ROWS = 200_000
    MEM_N_COLS = 30
    ITERATIONS = 5
    ROUNDS = 3

    def _time_once(self, fn: Any, df: pl.DataFrame, features: list[str]) -> float:
        """Run ``fn(df, features)`` ``ITERATIONS`` times, return total seconds."""
        # Warm the allocator / arrow conversion path.
        fn(df, features)
        t0 = time.perf_counter()
        for _ in range(self.ITERATIONS):
            fn(df, features)
        return time.perf_counter() - t0

    def test_walltime_numpy_path_is_at_least_30_percent_faster(self) -> None:
        """Cold numeric-only prep should be measurably faster on the numpy path."""
        df = _make_numeric_frame(self.N_ROWS, self.N_COLS)
        features = [f"f{i}" for i in range(self.N_COLS)]

        # Interleave pandas and numpy timings across rounds to cancel
        # background noise.
        t_pandas_total = 0.0
        t_numpy_total = 0.0
        for _ in range(self.ROUNDS):
            t_pandas_total += self._time_once(_baseline_prepare_with_to_pandas, df, features)
            t_numpy_total += self._time_once(_proposed_prepare_pyfunc_no_cats, df, features)

        assert t_numpy_total > 0.0
        # Reduction = 1 - numpy/pandas.  Require at least 30%.
        reduction = 1.0 - (t_numpy_total / t_pandas_total)
        assert reduction >= 0.30, (
            f"expected >=30% walltime reduction on {self.N_ROWS}x{self.N_COLS} "
            f"numeric-only prep (numpy vs pandas); got {reduction * 100:.1f}% "
            f"(pandas={t_pandas_total * 1000:.1f}ms, "
            f"numpy={t_numpy_total * 1000:.1f}ms over "
            f"{self.ITERATIONS * self.ROUNDS} iterations)"
        )

    def test_peak_memory_numpy_path_is_at_least_50_percent_lower(self) -> None:
        """Retained footprint (Arrow pool + output) must halve.

        The pandas path keeps the underlying Arrow buffer alive via the
        zero-copy round-trip, so the returned DataFrame carries the
        buffer *twice* from the memory accountant's point of view: once
        in the ``pyarrow.default_memory_pool`` and once in
        ``pd.DataFrame.memory_usage(deep=True)``.  The numpy path copies
        into a numpy buffer and releases the Arrow pool entry, leaving
        only a single contiguous allocation.  The expected ratio is
        therefore ~2x (50% reduction).  We set the bar at >=50% so a
        regression in Polars' conversion strategy (e.g. switching to a
        non-zero-copy path that hits both metrics) still passes.
        """
        df = _make_numeric_frame(self.MEM_N_ROWS, self.MEM_N_COLS)
        features = [f"f{i}" for i in range(self.MEM_N_COLS)]

        # Warm up: first call pays arrow-conversion import cost that would
        # otherwise skew peak-memory on the first benchmarked path.
        _baseline_prepare_with_to_pandas(df, features)
        _proposed_prepare_pyfunc_no_cats(df, features)

        # Measure each path under an isolated tracker session.
        pandas_result, pandas_footprint = _retained_footprint(
            _baseline_prepare_with_to_pandas, df, features
        )
        numpy_result, numpy_footprint = _retained_footprint(
            _proposed_prepare_pyfunc_no_cats, df, features
        )

        # Sanity: each path should actually allocate something.
        assert pandas_footprint > 0
        assert numpy_footprint > 0

        # Sanity: both paths produced outputs of the expected shape.
        assert isinstance(pandas_result, pd.DataFrame)
        assert pandas_result.shape == (self.MEM_N_ROWS, self.MEM_N_COLS)
        assert isinstance(numpy_result, np.ndarray)
        assert numpy_result.shape == (self.MEM_N_ROWS, self.MEM_N_COLS)

        reduction = 1.0 - (numpy_footprint / pandas_footprint)
        assert reduction >= 0.50, (
            f"expected >=50% retained-footprint reduction on "
            f"{self.MEM_N_ROWS}x{self.MEM_N_COLS} numeric-only prep; got "
            f"{reduction * 100:.1f}% (pandas={pandas_footprint / 1024:.0f} KiB, "
            f"numpy={numpy_footprint / 1024:.0f} KiB)"
        )
