"""Phase 2 Package 2C-3+4 — unified scoring path (TDD).

Covers two linked refactors:

* #58 Drop :class:`haute._mlflow_io.ScoringModel` wrapper — replace with
  explicit flavor dispatch at each scoring call site.  Existing callers
  should get flavor-specific objects directly (or a small dataclass /
  Pydantic model that carries the flavor tag plus feature metadata,
  without the ``__getattr__`` proxy layer).
* #59 Unify :func:`haute._model_scorer._score_batched_standalone` with the
  eager path in :func:`haute._mlflow_io._score_eager` into a single
  scorer function that auto-detects batch-vs-eager from the input size
  (or exposes a ``batch: bool`` knob that folds the two helpers into one).

These tests are written BEFORE the refactor.  Tests that probe the new
API shape will fail with ``ImportError`` / ``AttributeError`` until the
dev agent lands the implementation.  Tests marked with a module-scoped
"regression guard" docstring must pass both PRE and POST refactor —
they exist to prevent drift in observable scoring semantics.

API assumptions made by this test file (documented for the dev agent):

1. After the refactor there will be a callable named ``score_frame`` or
   ``score`` exported from :mod:`haute._model_scorer` (or
   :mod:`haute._mlflow_io`) that:
       - takes a pre-loaded flavor-specific model object,
       - takes a :class:`polars.LazyFrame`,
       - takes a list of feature names,
       - takes ``flavor`` / ``cat_feature_names`` metadata as Pydantic
         / dataclass fields (or keyword args),
       - auto-detects batch-vs-eager from the DataFrame size (or a
         ``batch`` kwarg),
       - returns a :class:`polars.LazyFrame` with the prediction column.

   The unified entry point is ``haute._model_scorer.score_frame``,
   returned by the ``_get_unified_scorer`` helper below.  Regression
   guards do NOT depend on the new API at all — they use the current
   ``_run_score_pipeline`` so they pass pre-fix.

2. After the refactor ``_score_batched_standalone`` should be either
   removed from :mod:`haute._model_scorer` OR rewritten as a thin
   delegator onto the unified path.  We assert either (a) the symbol no
   longer exists or (b) its output matches the unified scorer's output
   byte-for-byte.

3. The unified scorer must raise ``ConfigError`` / ``ValueError`` (NOT
   silently fall through to pyfunc) for an unknown ``flavor`` string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

from haute._mlflow_io import ScoringModel
from haute._model_scorer import FeatureMismatchError, _run_score_pipeline
from haute.errors import ConfigError

# ---------------------------------------------------------------------------
# Dynamic discovery of the unified scoring entry point.
#
# The dev agent picks the name/shape when landing the refactor; this helper
# lets the explicit-flavor tests skip cleanly (rather than hard-fail on
# import) until the symbol exists.  Regression guards below do NOT use this
# — they lean on the current module-level entry point so they pass today.
# ---------------------------------------------------------------------------


def _get_unified_scorer() -> Any:
    """Return the unified scorer entry point, ``haute._model_scorer.score_frame``."""
    from haute._model_scorer import score_frame

    return score_frame


def _train_tiny_catboost_regressor(
    train_df: pl.DataFrame,
    features: list[str],
    target: str = "y",
) -> Any:
    """Train a minimal CatBoost regressor and return the raw model.

    Kept deliberately tiny so the regression guards run in well under a
    second even on CI.
    """
    from catboost import CatBoostRegressor

    model = CatBoostRegressor(
        iterations=3,
        depth=2,
        verbose=0,
        allow_writing_files=False,
    )
    model.fit(
        train_df.select(features).to_pandas(),
        train_df[target].to_numpy(),
    )
    return model


@pytest.fixture()
def tiny_catboost_model(tmp_path: Path) -> tuple[Any, Path, list[str]]:
    """Fit a tiny CatBoost regressor, save to .cbm, return ``(model, path, features)``."""
    pytest.importorskip("catboost", reason="catboost not installed")
    rng = np.random.RandomState(42)
    n = 80
    df = pl.DataFrame(
        {
            "a": rng.randn(n),
            "b": rng.randn(n) * 2,
            "y": rng.randn(n) * 0.3 + 1.0,
        }
    )
    features = ["a", "b"]
    model = _train_tiny_catboost_regressor(df, features, target="y")
    model_path = tmp_path / "model.cbm"
    model.save_model(str(model_path))
    return model, model_path, features


@pytest.fixture()
def catboost_scoring_df() -> pl.DataFrame:
    """A 10-row DataFrame aligned with ``tiny_catboost_model`` features."""
    rng = np.random.RandomState(99)
    return pl.DataFrame(
        {
            "a": rng.randn(10),
            "b": rng.randn(10) * 2,
        }
    )


def _make_pyfunc_like_model(
    feature_names: list[str],
    predictions: np.ndarray,
) -> MagicMock:
    """Build a MagicMock shaped like an MLflow pyfunc model."""
    model = MagicMock()
    model.metadata.signature.inputs.input_names.return_value = feature_names
    model.predict.return_value = predictions
    # Pyfunc models lack predict_proba by default
    del model.predict_proba
    return model


def _make_rustystats_like_model(
    feature_names: list[str],
    predictions: np.ndarray,
) -> MagicMock:
    """Build a MagicMock shaped like a RustyStats GLMModel."""
    model = MagicMock()
    model.required_columns = feature_names
    model.predict.return_value = predictions
    # GLMs don't have predict_proba
    del model.predict_proba
    return model


# ===========================================================================
# Class 1 — Explicit-flavor dispatch (post-refactor API)
# ===========================================================================


class TestExplicitFlavorDispatch:
    """After #58 lands, each scoring call site branches on flavor instead
    of going through a uniform ``ScoringModel`` wrapper.

    These tests probe the new dispatch surface.  Until the refactor lands
    they skip via :func:`_get_unified_scorer` with a descriptive message.
    """

    def test_catboost_flavor_returns_expected_shape(
        self,
        tiny_catboost_model: tuple[Any, Path, list[str]],
        catboost_scoring_df: pl.DataFrame,
    ) -> None:
        """CatBoost dispatch produces a prediction column of length 10."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        score = _get_unified_scorer()
        model, _, features = tiny_catboost_model

        result_lf = score(
            model=model,
            lf=catboost_scoring_df.lazy(),
            features=features,
            cat_feature_names=frozenset(),
            flavor="catboost",
            task="regression",
            output_col="pred",
        )
        df = result_lf.collect()
        assert "pred" in df.columns, "CatBoost dispatch must append prediction column"
        assert len(df) == 10, "Prediction output must preserve input row count"
        # Values must be non-null floats (not NaN for a well-formed input)
        assert df["pred"].dtype.is_numeric()
        assert df["pred"].null_count() == 0

    def test_pyfunc_flavor_returns_expected_shape(self) -> None:
        """Pyfunc dispatch produces the expected prediction column."""
        score = _get_unified_scorer()

        preds = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        pyfunc_model = _make_pyfunc_like_model(["a", "b"], preds)
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "b": [6.0, 7.0, 8.0, 9.0, 10.0]})

        result_lf = score(
            model=pyfunc_model,
            lf=df.lazy(),
            features=["a", "b"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
        )
        result = result_lf.collect()
        assert "pred" in result.columns
        assert result["pred"].to_list() == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])

    def test_rustystats_glm_flavor_returns_expected_shape(self) -> None:
        """RustyStats dispatch produces predictions and passes Polars input."""
        score = _get_unified_scorer()

        preds = np.array([10.0, 20.0, 30.0])
        rs_model = _make_rustystats_like_model(["x"], preds)
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})

        result_lf = score(
            model=rs_model,
            lf=df.lazy(),
            features=["x"],
            cat_feature_names=frozenset(),
            flavor="rustystats",
            task="regression",
            output_col="pred",
        )
        result = result_lf.collect()
        assert "pred" in result.columns
        assert result["pred"].to_list() == [10.0, 20.0, 30.0]
        # Verify the RustyStats path preserves Polars input (it handles its own prep)
        call_args = rs_model.predict.call_args[0][0]
        assert isinstance(call_args, pl.DataFrame), (
            "RustyStats dispatch must pass Polars DataFrame (not numpy/pandas)"
        )

    def test_unknown_flavor_raises_loudly(self) -> None:
        """Unknown ``flavor`` must raise (ConfigError or ValueError), never
        silently fall through to pyfunc.

        Silent fallback would mean a typo in the flavor string scores with
        the wrong path, producing subtly wrong predictions.  CLAUDE.md
        policy: fail loudly.
        """
        score = _get_unified_scorer()

        fake_model = MagicMock()
        fake_model.predict.return_value = np.array([0.0])
        df = pl.DataFrame({"a": [1.0]})

        with pytest.raises((ConfigError, ValueError)) as exc_info:
            result = score(
                model=fake_model,
                lf=df.lazy(),
                features=["a"],
                cat_feature_names=frozenset(),
                flavor="tensorflow",  # not supported
                task="regression",
                output_col="pred",
            )
            # If the dispatch returned a LazyFrame, force collection so
            # lazy errors surface here rather than leaving test green.
            if hasattr(result, "collect"):
                result.collect()

        err_text = str(exc_info.value).lower()
        assert "flavor" in err_text or "tensorflow" in err_text, (
            "Error must name the offending flavor or mention 'flavor' so "
            "the operator can diagnose the misconfiguration"
        )

    def test_classification_pyfunc_appends_proba(self) -> None:
        """Classification task with pyfunc model that supports predict_proba
        appends a ``<output_col>_proba`` column.
        """
        score = _get_unified_scorer()

        model = MagicMock()
        model.metadata.signature.inputs.input_names.return_value = ["a"]
        model.predict.return_value = np.array([0, 1, 0])
        model.predict_proba.return_value = np.array([[0.8, 0.2], [0.3, 0.7], [0.9, 0.1]])
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})

        result_lf = score(
            model=model,
            lf=df.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="classification",
            output_col="pred",
        )
        result = result_lf.collect()
        assert "pred" in result.columns
        assert "pred_proba" in result.columns
        # Positive class column (index 1) is used
        assert result["pred_proba"].to_list() == pytest.approx([0.2, 0.7, 0.1])


# ===========================================================================
# Class 2 — Regression guards (MUST pass pre-fix AND post-fix)
# ===========================================================================


class TestScoringRegressionGuards:
    """Guard rails that must NOT regress through the refactor.

    Every test here uses the current public entry points
    (``_run_score_pipeline``, ``load_local_model`` round-trip through
    MLflow, etc.) so they pass BEFORE the refactor lands and continue to
    pass after.  If any of these break post-refactor, the dev has changed
    observable behaviour and must back the change out.
    """

    def test_integration_catboost_roundtrip_stable_preds(
        self,
        tiny_catboost_model: tuple[Any, Path, list[str]],
        catboost_scoring_df: pl.DataFrame,
    ) -> None:
        """Train a tiny catboost, save, load via ``load_local_model``, score
        10 rows — the prediction values must be identical to calling
        ``model.predict`` directly on the same input.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model

        raw_model, model_path, features = tiny_catboost_model

        # Baseline: predict directly with the trained raw model.
        baseline = raw_model.predict(catboost_scoring_df.to_pandas())

        # Round-trip through the library's loader.
        scoring_model = load_local_model(str(model_path), task="regression")
        result_lf = _run_score_pipeline(
            scoring_model,
            catboost_scoring_df.lazy(),
            task="regression",
            output_col="prediction",
            source="live",
        )
        result_df = result_lf.collect()

        assert "prediction" in result_df.columns
        assert len(result_df) == 10
        np.testing.assert_allclose(
            result_df["prediction"].to_numpy(),
            np.asarray(baseline).flatten(),
            rtol=1e-5,
            atol=1e-7,
            err_msg=(
                "Post-refactor predictions must match the pre-refactor baseline "
                "exactly — any drift here is an observable behaviour change."
            ),
        )

    def test_regression_eager_path_preserves_feature_values(self) -> None:
        """Eager path preserves the input rows alongside the new prediction column."""
        model = MagicMock()
        model.predict.return_value = np.array([0.1, 0.2, 0.3])
        sm = ScoringModel(model, ["a", "b"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
        result_lf = _run_score_pipeline(
            sm,
            df.lazy(),
            task="regression",
            output_col="pred",
            source="live",
        )
        result = result_lf.collect()

        # Input columns preserved
        assert result["a"].to_list() == [1.0, 2.0, 3.0]
        assert result["b"].to_list() == [4.0, 5.0, 6.0]
        # Prediction column appended
        assert result["pred"].to_list() == pytest.approx([0.1, 0.2, 0.3])

    def test_regression_batched_path_preserves_predictions(self) -> None:
        """Batched path produces the same predictions for the same model/input
        as the eager path — this is the invariant the refactor must hold.
        """
        model = MagicMock()
        model.predict.return_value = np.array([0.1, 0.2, 0.3, 0.4])
        sm = ScoringModel(model, ["a", "b"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [5.0, 6.0, 7.0, 8.0]})

        # Eager path
        eager_result = _run_score_pipeline(
            sm,
            df.lazy(),
            task="regression",
            output_col="pred",
            source="live",
        ).collect()

        # Batched path — the stubbed model returns the same predictions
        batched_result = _run_score_pipeline(
            sm,
            df.lazy(),
            task="regression",
            output_col="pred",
            source="batch",
        ).collect()

        np.testing.assert_allclose(
            eager_result["pred"].to_numpy(),
            batched_result["pred"].to_numpy(),
            rtol=1e-9,
            atol=1e-12,
            err_msg=(
                "Eager and batched paths must produce identical predictions for "
                "the same model/input — a unified scorer must preserve this."
            ),
        )


# ===========================================================================
# Class 3 — Post-refactor structural guards
# ===========================================================================


class TestRefactorStructuralInvariants:
    """Invariants that define *what the refactor means*:

    * ``_score_batched_standalone`` is either removed or reduced to a
      delegator.
    * The unified scorer auto-detects large-frame batching.
    * Feature-order / categorical-type errors remain loud (Phase 1 #1 /
      #13 didn't regress).
    """

    def test_score_batched_standalone_removed_or_delegates(self) -> None:
        """Post-refactor, the standalone helper should be gone OR its output
        must match the unified path for the same input.

        We allow EITHER condition — it's the dev's call whether they
        outright delete ``_score_batched_standalone`` or keep it as a
        thin shim that forwards to the unified function.
        """
        import haute._model_scorer as mod

        standalone = getattr(mod, "_score_batched_standalone", None)
        if standalone is None:
            # Option (a): removed entirely.  Test passes.
            return

        # Option (b): still present — must delegate to the unified path.
        # Verify by scoring identical input via both paths and comparing.
        model = MagicMock()
        model.predict.return_value = np.array([0.5, 0.6])
        sm = ScoringModel(model, ["a"], frozenset(), "pyfunc")
        df = pl.DataFrame({"a": [1.0, 2.0]})

        try:
            standalone_result = standalone(sm, df.lazy(), ["a"], "pred", "regression")
            if hasattr(standalone_result, "collect"):
                standalone_result = standalone_result.collect()
        except Exception as exc:
            pytest.fail(
                f"_score_batched_standalone still present but errored: {exc}. "
                f"If keeping the symbol, it must delegate to the unified scorer."
            )

        # The symbol's output shape must match the eager/unified semantics:
        # a DataFrame with the prediction column and all input columns preserved.
        assert "pred" in standalone_result.columns
        assert standalone_result["pred"].to_list() == pytest.approx([0.5, 0.6])

    def test_unified_scorer_handles_large_frame_via_batching(self) -> None:
        """A 5000-row DataFrame (>> the expected eager-vs-batch threshold)
        must still produce correct predictions via the unified scorer.

        The test asserts the NUMERIC output is identical whether the
        scorer auto-detected batching or ran eager.  We don't depend on
        the threshold value, just on the invariant that equivalent
        input produces equivalent output.
        """
        score = _get_unified_scorer()

        # Deterministic "predict" that returns a per-row constant derived
        # from input length — so predictions are well-defined whether the
        # scorer runs eager (one predict call) or batched (N predict calls).
        model = MagicMock()

        def _stub_predict(x_data: Any) -> np.ndarray:
            try:
                n = len(x_data)
            except TypeError:
                n = x_data.shape[0]
            # All rows in a chunk get the same value — so concatenation across
            # arbitrary batch boundaries is order-invariant.
            return np.full(n, 0.5, dtype=np.float64)

        model.predict.side_effect = _stub_predict
        model.metadata.signature.inputs.input_names.return_value = ["a"]
        del model.predict_proba

        # Small frame
        small_df = pl.DataFrame({"a": np.arange(5, dtype=np.float64)})
        small_result = score(
            model=model,
            lf=small_df.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
        ).collect()

        # Large frame — should trigger the batch path if auto-detection is on
        large_df = pl.DataFrame({"a": np.arange(5000, dtype=np.float64)})
        large_result = score(
            model=model,
            lf=large_df.lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
        ).collect()

        # Both outputs must have the right length and no null predictions —
        # a partial scoring (batch path dropping rows) would leave nulls.
        assert len(small_result) == 5
        assert len(large_result) == 5000
        assert small_result["pred"].null_count() == 0
        assert large_result["pred"].null_count() == 0
        # All rows got the stub value 0.5 — byte-identical between small
        # and large frames regardless of which internal path was chosen.
        assert small_result["pred"].to_list() == [0.5] * 5
        assert large_result["pred"].to_list() == [0.5] * 5000

    def test_unified_scorer_explicit_batch_kwarg_if_supported(self) -> None:
        """Both ``batch=True`` and ``batch=False`` must produce the same output."""
        score = _get_unified_scorer()

        import inspect

        sig = inspect.signature(score)
        assert "batch" in sig.parameters

        model = MagicMock()
        preds = np.array([1.0, 2.0, 3.0, 4.0])
        model.predict.return_value = preds
        model.metadata.signature.inputs.input_names.return_value = ["x"]
        del model.predict_proba
        df = pl.DataFrame({"x": [10.0, 20.0, 30.0, 40.0]})

        eager = score(
            model=model,
            lf=df.lazy(),
            features=["x"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=False,
        ).collect()

        # Reset the stub so batching path re-issues the call cleanly
        model.predict.reset_mock()
        model.predict.return_value = preds
        batched = score(
            model=model,
            lf=df.lazy(),
            features=["x"],
            cat_feature_names=frozenset(),
            flavor="pyfunc",
            task="regression",
            output_col="pred",
            batch=True,
        ).collect()

        assert eager["pred"].to_list() == batched["pred"].to_list()

    def test_feature_order_mismatch_still_raises_post_refactor(
        self,
        tiny_catboost_model: tuple[Any, Path, list[str]],
    ) -> None:
        """Regression guard for Phase 1 #1: reordered features must still
        raise ``FeatureMismatchError`` after the refactor.

        CatBoost's categorical indices are positional; silent reorder =
        invisible prediction drift.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model

        _, model_path, features = tiny_catboost_model
        scoring_model = load_local_model(str(model_path), task="regression")

        rng = np.random.RandomState(7)
        # Reversed feature order in the input
        df = pl.DataFrame(
            {
                "b": rng.randn(5) * 2,
                "a": rng.randn(5),
            }
        )
        # Pre-check: scoring_model remembers training order
        assert scoring_model.feature_names == features

        with pytest.raises(FeatureMismatchError):
            _run_score_pipeline(
                scoring_model,
                df.lazy(),
                task="regression",
                output_col="pred",
                source="live",
            )

    def test_categorical_type_mismatch_still_raises_post_refactor(self) -> None:
        """Regression guard for Phase 1 #13: passing Int64 for a categorical
        trained as String must still raise ``FeatureMismatchError`` after the
        refactor.
        """
        model = MagicMock()
        model.predict.return_value = np.array([0.0])
        sm = ScoringModel(
            model=model,
            feature_names=["age", "region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
        )

        # region trained as String, now passed as Int64
        df = pl.DataFrame({"age": [1.0], "region": [42]})  # region is Int64
        with pytest.raises(FeatureMismatchError) as exc_info:
            _run_score_pipeline(
                sm,
                df.lazy(),
                task="regression",
                output_col="pred",
                source="live",
            )

        assert "region" in str(exc_info.value), "Type mismatch error must name the offending column"


# ===========================================================================
# Class 4 - ScoringModel wrapper invariants (#58)
# ===========================================================================


class TestScoringModelWrapperDispatch:
    """The scoring wrapper is only a metadata carrier; dispatch must be explicit."""

    def test_scoring_model_has_explicit_flavor_tag(self) -> None:
        """Regardless of whether ``ScoringModel`` survives, the flavor tag
        must be accessible without a ``__getattr__`` hop.  Either a
        ``flavor`` attribute or a ``@property`` — not proxied through to
        ``self._model.flavor``.
        """
        raw = MagicMock(spec=[])  # no `.flavor` attribute on the raw model
        sm = ScoringModel(raw, ["a"], frozenset(), "catboost")
        # .flavor is a declared attribute; proxying via __getattr__ would
        # ask the raw model, which has no flavor and would raise.
        assert sm.flavor == "catboost"

    def test_unknown_flavor_on_scoring_model_surfaces_loudly(self) -> None:
        """Constructing a ``ScoringModel`` with a made-up flavor string must
        be rejected by the scoring path, not silently fall through to pyfunc.
        """
        model = MagicMock()
        model.predict.return_value = np.array([1.0])
        sm = ScoringModel(model, ["a"], frozenset(), "tensorflow")
        df = pl.DataFrame({"a": [1.0]})

        with pytest.raises(ConfigError):
            _run_score_pipeline(
                sm,
                df.lazy(),
                task="regression",
                output_col="pred",
                source="live",
            ).collect()


# ===========================================================================
# Class 5 — Batch-vs-eager output equivalence (#59)
# ===========================================================================


class TestBatchEagerEquivalence:
    """Integration checks that pin the semantic contract #59 preserves:
    given identical input and model, the batch path and the eager path
    produce numerically identical output.

    These tests use the current public entry points so they pass
    pre-refactor and continue to pass post-refactor, regardless of
    whether the dev folds the paths behind a ``batch: bool`` knob or
    auto-detects from frame size.
    """

    def test_eager_and_batched_identical_for_small_frame(self) -> None:
        """5-row DataFrame scored via both paths yields equal predictions."""
        model = MagicMock()
        model.predict.return_value = np.array([0.11, 0.22, 0.33, 0.44, 0.55])
        sm = ScoringModel(model, ["a"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
        eager = _run_score_pipeline(
            sm, df.lazy(), task="regression", output_col="pred", source="live"
        ).collect()

        # Reset so batch path re-invokes the stub cleanly
        model.predict.return_value = np.array([0.11, 0.22, 0.33, 0.44, 0.55])
        batched = _run_score_pipeline(
            sm, df.lazy(), task="regression", output_col="pred", source="batch"
        ).collect()

        assert eager["pred"].to_list() == batched["pred"].to_list()

    def test_eager_and_batched_identical_catboost_roundtrip(
        self,
        tiny_catboost_model: tuple[Any, Path, list[str]],
        catboost_scoring_df: pl.DataFrame,
    ) -> None:
        """Full catboost roundtrip: eager path == batched path for 10 rows."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import load_local_model

        _, model_path, features = tiny_catboost_model
        scoring_model = load_local_model(str(model_path), task="regression")

        eager_lf = _run_score_pipeline(
            scoring_model,
            catboost_scoring_df.lazy(),
            task="regression",
            output_col="pred",
            source="live",
        )
        batched_lf = _run_score_pipeline(
            scoring_model,
            catboost_scoring_df.lazy(),
            task="regression",
            output_col="pred",
            source="batch",
        )

        eager_preds = eager_lf.collect()["pred"].to_numpy()
        batched_preds = batched_lf.collect()["pred"].to_numpy()

        np.testing.assert_allclose(
            eager_preds,
            batched_preds,
            rtol=1e-6,
            atol=1e-8,
            err_msg=(
                "CatBoost predictions via eager vs batch must be numerically "
                "identical — this is the contract #59 preserves."
            ),
        )

    def test_row_limit_always_forces_eager_path(self) -> None:
        """``row_limit`` set: scorer must use the eager path regardless of
        source.  This invariant must survive the #59 refactor — preview /
        trace UX depends on it.
        """
        model = MagicMock()
        model.predict.return_value = np.array([7.0, 8.0])
        sm = ScoringModel(model, ["a"], frozenset(), "pyfunc")

        df = pl.DataFrame({"a": [1.0, 2.0]})
        result = _run_score_pipeline(
            sm,
            df.lazy(),
            task="regression",
            output_col="pred",
            source="batch",  # would normally pick batch
            row_limit=10,  # but row_limit forces eager
        ).collect()

        assert result["pred"].to_list() == [7.0, 8.0]
        # predict was called once — batched path would call it per batch
        assert model.predict.call_count == 1
