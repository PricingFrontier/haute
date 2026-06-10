"""Real-MLflow pyfunc fixtures for the named-column input contract.

CODE_REVIEW HIGH "Model scoring" / remediation 4a.1 + 4a.6: the pyfunc
scoring branch of :func:`haute._mlflow_io._prepare_predict_frame` was
hidden behind MagicMock-only tests, which accept any input type.  Against
a REAL mlflow pyfunc model the numpy fast-path is rejected outright when
the model carries a named-column signature (the standard
``infer_signature`` case)::

    MlflowException: Failed to enforce schema ... Model is missing inputs
    ['a', 'b']. Note that there were extra inputs: [0, 1].

and the unconditional Float32 cast silently destroys float64 precision
(mlflow upcasts float32 back to double for a ``double`` schema without
any error — ``1.0000000000009095`` arrives as ``1.0``).

Fixtures here are the cheapest real flavor: ``mlflow.pyfunc.save_model``
with a ``PythonModel`` wrapping plain Python arithmetic — no tracking
server, no sklearn, one save/load per module.  The model is
order-asymmetric (``a - 3*b``) so positional mis-binding produces
detectably wrong values rather than coincidentally right ones.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import pytest

# Budgeted debt site: this module needs the real mlflow package (the
# ``databricks`` extra).  The main CI suite installs it via the dev
# group; a core-only install must skip cleanly instead of erroring.
mlflow = pytest.importorskip(
    "mlflow",
    reason="mlflow optional dependency (databricks extra) not installed",
)

from haute._mlflow_io import (  # noqa: E402 — after importorskip by design
    _prepare_predict_frame,
    _score_eager,
    _wrap_pyfunc,
)

# A float64 value that does NOT survive a round-trip through float32.
PRECISE_VALUE = 1.0 + 2.0**-40


class _AsymmetricPythonModel(mlflow.pyfunc.PythonModel):
    """``predict = a - 3*b`` — wrong column binding cannot produce the
    right answer by accident.

    Accepts both a named DataFrame (the contract under test) and a raw
    ndarray (used by the signature-less fixture, where mlflow performs no
    input conversion).
    """

    def predict(self, context: Any, model_input: Any, params: Any = None) -> np.ndarray:
        if hasattr(model_input, "columns"):
            return (model_input["a"] - 3.0 * model_input["b"]).to_numpy()
        arr = np.asarray(model_input)
        return arr[:, 0] - 3.0 * arr[:, 1]


@pytest.fixture(scope="module")
def named_signature_pyfunc(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A REAL loaded ``PyFuncModel`` with a named ``[a: double, b: double]``
    signature — the shape every model logged via ``infer_signature`` has."""
    import pandas as pd
    from mlflow.models import infer_signature

    train = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})
    signature = infer_signature(train, np.array([0.0, 0.0, 0.0]))
    model_dir = tmp_path_factory.mktemp("real_pyfunc_named") / "model"
    mlflow.pyfunc.save_model(
        path=str(model_dir),
        python_model=_AsymmetricPythonModel(),
        signature=signature,
    )
    return mlflow.pyfunc.load_model(str(model_dir))


@pytest.fixture(scope="module")
def signatureless_pyfunc(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A REAL loaded ``PyFuncModel`` saved WITHOUT a signature.

    MLflow performs no input enforcement for these — the python_model
    receives whatever we hand over.  Pins that the named-DataFrame input
    contract also flows through models with no declared schema.
    """
    model_dir = tmp_path_factory.mktemp("real_pyfunc_bare") / "model"
    mlflow.pyfunc.save_model(path=str(model_dir), python_model=_AsymmetricPythonModel())
    return mlflow.pyfunc.load_model(str(model_dir))


# ---------------------------------------------------------------------------
# Signature extraction against a real model (kills the MagicMock-only
# coverage of _wrap_pyfunc / _extract_pyfunc_features)
# ---------------------------------------------------------------------------


class TestRealPyfuncWrapping:
    def test_wrap_pyfunc_extracts_real_signature_features(self, named_signature_pyfunc):
        sm = _wrap_pyfunc(named_signature_pyfunc)
        assert sm.flavor == "pyfunc"
        assert sm.feature_names == ["a", "b"]
        assert sm.cat_feature_names == frozenset()

    def test_real_pyfunc_model_rejects_unnamed_numpy(self, named_signature_pyfunc):
        """The premise of 4a.1, pinned against the real library: a
        named-column signature hard-rejects positional numpy input.

        If a future mlflow version starts ACCEPTING numpy here, this test
        fails and the input-preparation contract must be re-reviewed —
        silent positional binding would be worse than the rejection.
        """
        from mlflow.exceptions import MlflowException

        with pytest.raises(MlflowException, match="missing inputs"):
            named_signature_pyfunc.predict(np.array([[5.0, 1.0]]))


# ---------------------------------------------------------------------------
# 4a.1 — pyfunc receives a named DataFrame per its signature
# ---------------------------------------------------------------------------


class TestNamedColumnContract:
    def test_prepare_frame_output_accepted_by_named_signature(self, named_signature_pyfunc):
        """RED pre-fix: the numpy fast-path output is rejected by mlflow's
        schema enforcement; the prepared input must score, with exact
        values proving by-name binding (a - 3b)."""
        df = pl.DataFrame({"a": [5.0, 8.0], "b": [1.0, 2.0]})
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        preds = np.asarray(named_signature_pyfunc.predict(x_data))
        np.testing.assert_array_equal(preds, np.array([2.0, 2.0]))

    def test_columns_bind_by_name_even_when_frame_order_differs(self, named_signature_pyfunc):
        """Input frame ordered (b, a, extra) + features [a, b] must still
        bind by NAME.  Positional mis-binding would yield b - 3a = -14."""
        df = pl.DataFrame({"b": [1.0], "a": [5.0], "extra": [99.0]})
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        preds = np.asarray(named_signature_pyfunc.predict(x_data))
        np.testing.assert_array_equal(preds, np.array([2.0]))

    def test_score_eager_real_pyfunc_end_to_end(self, named_signature_pyfunc):
        """The eager production surface scores a real named-signature model."""
        sm = _wrap_pyfunc(named_signature_pyfunc)
        df = pl.DataFrame({"a": [5.0, 9.0], "b": [1.0, 3.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "prediction", "regression").collect()
        assert result["prediction"].to_list() == [2.0, 0.0]

    def test_batch_scoring_real_pyfunc_end_to_end(self, named_signature_pyfunc, tmp_path):
        """The batch production surface scores a real named-signature model."""
        import os

        from haute._model_scorer import _batch_score_to_parquet

        sm = _wrap_pyfunc(named_signature_pyfunc)
        df = pl.DataFrame({"a": [5.0, 9.0], "b": [1.0, 3.0]})
        input_path = str(tmp_path / "input.parquet")
        df.write_parquet(input_path)

        out_path = _batch_score_to_parquet(sm, input_path, ["a", "b"], "pred", "regression")
        try:
            result = pl.read_parquet(out_path)
        finally:
            os.unlink(out_path)
        assert result["pred"].to_list() == [2.0, 0.0]

    def test_signatureless_pyfunc_still_scores_named_frame(self, signatureless_pyfunc):
        """Models without a declared schema receive the same named frame —
        no special-casing, no silent fallback to positional numpy."""
        df = pl.DataFrame({"a": [5.0], "b": [1.0]})
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        preds = np.asarray(signatureless_pyfunc.predict(x_data))
        np.testing.assert_array_equal(preds, np.array([2.0]))

    def test_real_pyfunc_classification_skips_proba_column(self, named_signature_pyfunc):
        """Real ``PyFuncModel`` objects expose no ``predict_proba`` — a
        classification task scores the point prediction and emits no
        ``_proba`` column (pins why proba shaping is native-flavor-only)."""
        assert not hasattr(named_signature_pyfunc, "predict_proba")
        sm = _wrap_pyfunc(named_signature_pyfunc)
        df = pl.DataFrame({"a": [5.0], "b": [1.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "pred", "classification").collect()
        assert "pred" in result.columns
        assert "pred_proba" not in result.columns


# ---------------------------------------------------------------------------
# 4a.6 — declared dtypes respected: float64 stays float64
# ---------------------------------------------------------------------------


class TestDeclaredDtypePrecision:
    def test_float64_precision_preserved_end_to_end(self, named_signature_pyfunc):
        """RED pre-fix: the Float32 cast collapsed ``1 + 2**-40`` to 1.0
        (mlflow silently upcasts float32 to the declared double).  The
        value must survive bit-exact through the eager scoring surface."""
        sm = _wrap_pyfunc(named_signature_pyfunc)
        df = pl.DataFrame({"a": [PRECISE_VALUE], "b": [0.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "pred", "regression").collect()
        assert result["pred"].to_list() == [PRECISE_VALUE], (
            "float64 feature precision was degraded before reaching the model"
        )

    def test_model_receives_declared_double_dtype(self, named_signature_pyfunc):
        """The frame handed to mlflow carries float64 — enforcement should
        find the declared dtype already in place, not repair a downcast."""
        import pandas as pd

        df = pl.DataFrame({"a": [PRECISE_VALUE], "b": [0.0]})
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        assert isinstance(x_data, pd.DataFrame)
        assert x_data["a"].dtype == np.float64
        assert x_data["a"].iloc[0] == PRECISE_VALUE

    def test_int32_features_upcast_by_mlflow_not_by_haute(self, named_signature_pyfunc):
        """Int32 input flows through natively; mlflow's own enforcement
        upcasts it to the declared ``double`` (a safe widening).  Haute
        adds no cast of its own — values stay exact."""
        df = pl.DataFrame(
            {
                "a": pl.Series("a", [7], dtype=pl.Int32),
                "b": pl.Series("b", [2], dtype=pl.Int32),
            }
        )
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        assert x_data["a"].dtype == np.int32
        preds = np.asarray(named_signature_pyfunc.predict(x_data))
        np.testing.assert_array_equal(preds, np.array([1.0]))

    def test_int64_vs_double_signature_fails_loudly_via_mlflow(self, named_signature_pyfunc):
        """Int64 against a ``double`` signature is REJECTED by mlflow as an
        unsafe conversion (int64 exceeds float64's exact-integer range).

        The pre-fix Float32 blanket masked this contract violation by
        downcasting everything — scoring "worked" while silently
        corrupting precision.  Post-fix the model's own signature
        enforcement raises with mlflow's actionable message naming the
        column; haute neither hides nor rewrites the contract.
        """
        from mlflow.exceptions import MlflowException

        df = pl.DataFrame(
            {
                "a": pl.Series("a", [7], dtype=pl.Int64),
                "b": pl.Series("b", [2], dtype=pl.Int64),
            }
        )
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        assert x_data["a"].dtype == np.int64
        with pytest.raises(MlflowException, match="Incompatible input types"):
            named_signature_pyfunc.predict(x_data)

    def test_nulls_reach_model_as_nan(self, named_signature_pyfunc):
        """A null float feature arrives as NaN and propagates through the
        model arithmetic — never silently imputed by the scoring layer."""
        sm = _wrap_pyfunc(named_signature_pyfunc)
        df = pl.DataFrame({"a": [5.0, None], "b": [1.0, 1.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "pred", "regression").collect()
        values = result["pred"].to_list()
        assert values[0] == 2.0
        assert values[1] is None or np.isnan(values[1])
