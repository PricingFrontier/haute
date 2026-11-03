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

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from haute._model_scorer import _cleanup_registered_temp_files

# This module needs the real core MLflow package. Keep the import guard so
# deliberately partial test environments skip cleanly instead of erroring.
mlflow = pytest.importorskip(
    "mlflow",
    reason="core mlflow dependency is unavailable",
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
        sm = _wrap_pyfunc(named_signature_pyfunc, task="regression")
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
        sm = _wrap_pyfunc(named_signature_pyfunc, task="regression")
        df = pl.DataFrame({"a": [5.0, 9.0], "b": [1.0, 3.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "prediction", "regression").collect()
        assert result["prediction"].to_list() == [2.0, 0.0]

    def test_batch_scoring_real_pyfunc_end_to_end(self, named_signature_pyfunc, tmp_path):
        """The batch production surface scores a real named-signature model."""

        from haute._model_scorer import _batch_score_to_parquet

        sm = _wrap_pyfunc(named_signature_pyfunc, task="regression")
        df = pl.DataFrame({"a": [5.0, 9.0], "b": [1.0, 3.0]})
        input_path = str(tmp_path / "input.parquet")
        df.write_parquet(input_path)

        out_path = _batch_score_to_parquet(sm, input_path, ["a", "b"], "pred", "regression")
        try:
            result = pl.read_parquet(out_path)
        finally:
            _cleanup_registered_temp_files([out_path])
        assert result["pred"].to_list() == [2.0, 0.0]

    def test_signatureless_pyfunc_still_scores_named_frame(self, signatureless_pyfunc):
        """Models without a declared schema receive the same named frame —
        no special-casing, no silent fallback to positional numpy."""
        df = pl.DataFrame({"a": [5.0], "b": [1.0]})
        x_data = _prepare_predict_frame(df, ["a", "b"], frozenset(), "pyfunc")
        preds = np.asarray(signatureless_pyfunc.predict(x_data))
        np.testing.assert_array_equal(preds, np.array([2.0]))

    def test_real_pyfunc_classification_skips_proba_column(self, named_signature_pyfunc):
        """A pyfunc whose signature does not declare the classifier pair
        scores its point prediction and emits no ``_proba`` column."""
        assert not hasattr(named_signature_pyfunc, "predict_proba")
        sm = _wrap_pyfunc(named_signature_pyfunc, task="classification")
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
        sm = _wrap_pyfunc(named_signature_pyfunc, task="regression")
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
        sm = _wrap_pyfunc(named_signature_pyfunc, task="regression")
        df = pl.DataFrame({"a": [5.0, None], "b": [1.0, 1.0]})
        result = _score_eager(sm, df.lazy(), ["a", "b"], "pred", "regression").collect()
        values = result["pred"].to_list()
        assert values[0] == 2.0
        assert values[1] is None or np.isnan(values[1])


# ---------------------------------------------------------------------------
# Haute's own logged pyfunc: the classifier output pair and the optional
# exposure its signature declares
# ---------------------------------------------------------------------------


def _training_frame(n: int = 300, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    region = rng.choice(["east", "north", "south"], n)
    age = rng.uniform(18, 80, n)
    exposure = rng.uniform(0.2, 1.0, n)
    rate = np.exp(-2 + 0.01 * (age - 40) + np.where(region == "north", 0.4, 0.0))
    positive = rng.uniform(size=n) < 1 / (1 + np.exp(-(age - 50) / 8))
    return pl.DataFrame(
        {
            "region": region.tolist(),
            "age": age,
            "exposure": exposure,
            "claims": rng.poisson(rate * exposure).astype(float),
            "flag": np.where(positive, "yes", "no").tolist(),
            # An integer input: MLflow refuses the NaN of an all-null probe row.
            "years": rng.integers(0, 20, n),
        }
    )


def _saved_haute_pyfunc(directory: Path, **training: Any) -> tuple[Any, Any]:
    """Train a CatBoost model, save it as training logs it, and load both forms.

    Returns the loaded MLflow ``PyFuncModel`` and the native ``ScoringModel``.
    """
    from haute._mlflow_io import load_local_model
    from haute.modelling._feature_contract import load_contract
    from haute.modelling._native_pyfunc import package_native_model
    from haute.modelling._signature import build_signature
    from haute.modelling._training_job import TrainingJob, model_contract_filename

    result = TrainingJob(
        name="m",
        data=_training_frame(),
        algorithm="catboost",
        params={"iterations": 20, "depth": 3},
        output_dir=str(directory),
        evaluation={
            "schema_version": 1,
            "strategy": "random",
            "seed": 5,
            "validation": {"method": "single", "size": 0.2},
        },
        feature_columns=["region", "age", "years"],
        **training,
    ).run()
    model_path = Path(result.model_path)
    contract_path = model_path.parent / model_contract_filename(model_path.stem)
    contract = load_contract(contract_path)
    signature = build_signature(
        features=contract.features,
        feature_types=contract.feature_types,
        categorical_features=contract.categorical_features,
        target_name=contract.target_name,
        target_type=contract.target_type,
        task=contract.task,
        offset_name=contract.offset_column,
        offset_type=contract.offset_type,
        offset_link=contract.offset_link,
    )
    package = package_native_model(model_path, contract_path, directory / "package")
    model_dir = directory / "pyfunc"
    mlflow.pyfunc.save_model(
        path=str(model_dir),
        loader_module="haute.modelling._native_pyfunc",
        data_path=str(package),
        signature=signature,
    )
    native = load_local_model(str(model_path), task=contract.task)
    return mlflow.pyfunc.load_model(str(model_dir)), native


@pytest.fixture(scope="module")
def haute_classifier(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Any]:
    return _saved_haute_pyfunc(
        tmp_path_factory.mktemp("haute_classifier"),
        target="flag",
        loss_function="Logloss",
        task="classification",
        positive_class="yes",
        metrics=["auc"],
    )


@pytest.fixture(scope="module")
def haute_exposure_model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Any]:
    return _saved_haute_pyfunc(
        tmp_path_factory.mktemp("haute_exposure"),
        target="claims",
        loss_function="Poisson",
        metrics=["poisson_deviance"],
        offset="exposure",
    )


def _scored(
    scoring_model: Any,
    frame: pl.DataFrame,
    task: str,
    source: str,
    row_limit: int | None = None,
) -> pl.DataFrame:
    from haute._model_scorer import _run_score_pipeline

    return _run_score_pipeline(
        scoring_model,
        frame.lazy(),
        task=task,
        output_col="pred",
        source=source,
        row_limit=row_limit,
    ).collect()


class TestHauteClassifierPyfunc:
    """The label and probability columns stay separate, row for row."""

    @pytest.mark.parametrize(
        ("source", "row_limit"),
        [("live", None), ("batch", None), ("live", 100)],
        ids=["eager", "batch", "preview"],
    )
    def test_labels_and_probabilities_match_the_native_model(
        self, haute_classifier, source, row_limit
    ):
        pyfunc, native = haute_classifier
        frame = _training_frame(n=20, seed=7)

        scoring = _wrap_pyfunc(pyfunc, task="classification")
        scored = _scored(scoring, frame, "classification", source, row_limit)
        reference = _scored(native, frame, "classification", source, row_limit)

        assert scored.height == frame.height
        assert scored["age"].to_list() == frame["age"].to_list()
        assert scored["pred"].dtype == reference["pred"].dtype == pl.String
        assert scored["pred"].to_list() == reference["pred"].to_list()
        np.testing.assert_allclose(
            scored["pred_proba"].to_numpy(), reference["pred_proba"].to_numpy(), rtol=1e-9
        )
        assert set(scored["pred"].to_list()) <= {"yes", "no"}

    @pytest.mark.parametrize("source", ["live", "batch"])
    def test_an_empty_input_keeps_the_typed_columns(self, haute_classifier, source):
        pyfunc, native = haute_classifier
        empty = _training_frame(n=20, seed=7).head(0)

        scoring = _wrap_pyfunc(pyfunc, task="classification")
        scored = _scored(scoring, empty, "classification", source)
        reference = _scored(native, empty, "classification", source)

        assert scored.height == 0
        assert scored.schema["pred"] == reference.schema["pred"]
        assert scored.schema["pred_proba"] == reference.schema["pred_proba"] == pl.Float64

    def test_planning_declares_the_probability_column(self, haute_classifier):
        """A projection asking for ``<output>_proba`` gets it from the scorer."""
        from unittest.mock import patch

        from haute._builders import _model_score_columns

        pyfunc, _native = haute_classifier
        config = {
            "sourceType": "run",
            "run_id": "run",
            "artifact_path": "model",
            "task": "classification",
            "output_column": "pred",
        }
        with patch(
            "haute._model_source.load_scoring_model",
            return_value=_wrap_pyfunc(pyfunc, task="classification"),
        ):
            contract = _model_score_columns(config)

        assert contract.outputs == frozenset({"pred", "pred_proba"})
        assert "pred_proba" not in contract.inputs

    def test_a_classifier_output_scored_as_regression_is_refused(self, haute_classifier):
        from haute.errors import ConfigError

        pyfunc, _native = haute_classifier
        with pytest.raises(ConfigError, match="classification"):
            _wrap_pyfunc(pyfunc, task="regression")


class _TwoColumnPythonModel(mlflow.pyfunc.PythonModel):
    """Returns a two-column table that is not the classifier output pair."""

    def predict(self, context: Any, model_input: Any, params: Any = None) -> Any:
        import pandas as pd

        return pd.DataFrame({"low": model_input["a"], "high": model_input["b"]})


class _ShapedPythonModel(mlflow.pyfunc.PythonModel):
    """Returns the output shape named by *kind* for each input of ``a`` values."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def predict(self, context: Any, model_input: Any, params: Any = None) -> Any:
        import pandas as pd

        a = model_input["a"].to_numpy()
        if self.kind == "one_column_table":
            return pd.DataFrame({"pred": a * 2})
        if self.kind == "column_vector":
            return (a * 2).reshape(-1, 1)
        if self.kind == "matrix":
            return np.stack([a, a], axis=1)
        if self.kind == "one_too_many":
            return np.append(a * 2, 0.0)
        if self.kind == "pair":
            return pd.DataFrame({"pred_label": np.where(a > 1, "yes", "no"), "pred_proba": a / 4})
        if self.kind == "pair_missing_a_row":
            return pd.DataFrame(
                {"pred_label": ["no"] * (len(a) - 1), "pred_proba": [0.1] * (len(a) - 1)}
            )
        if self.kind == "labels_only":
            return pd.DataFrame({"pred_label": np.where(a > 1, "yes", "no")})
        raise AssertionError(self.kind)


def _shaped_pyfunc(tmp_path: Path, kind: str, outputs: list[Any] | None = None) -> Any:
    """A real saved and loaded pyfunc returning *kind*, with *outputs* as its signature's."""
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, Schema

    signature = ModelSignature(
        inputs=Schema([ColSpec("double", "a")]),
        outputs=Schema(outputs) if outputs is not None else None,
    )
    model_dir = tmp_path / kind
    mlflow.pyfunc.save_model(
        path=str(model_dir), python_model=_ShapedPythonModel(kind), signature=signature
    )
    return mlflow.pyfunc.load_model(str(model_dir))


def _pair_outputs() -> list[Any]:
    from mlflow.types import ColSpec

    return [ColSpec("string", "pred_label"), ColSpec("double", "pred_proba")]


_A = pl.DataFrame({"a": [1.0, 2.0, 3.0]})


class TestUndeclaredPyfuncOutputs:
    def test_a_multicolumn_output_is_refused_not_flattened(self, tmp_path):
        model_dir = tmp_path / "two_columns"
        mlflow.pyfunc.save_model(path=str(model_dir), python_model=_TwoColumnPythonModel())
        scoring = _wrap_pyfunc(mlflow.pyfunc.load_model(str(model_dir)), task="regression")
        frame = pl.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})

        with pytest.raises(ValueError, match="2 columns"):
            _score_eager(scoring, frame.lazy(), ["a", "b"], "pred", "regression").collect()

    @pytest.mark.parametrize("kind", ["one_column_table", "column_vector"])
    def test_one_prediction_per_row_scores_in_either_shape(self, tmp_path, kind):
        scoring = _wrap_pyfunc(_shaped_pyfunc(tmp_path, kind), task="regression")

        scored = _score_eager(scoring, _A.lazy(), ["a"], "pred", "regression").collect()

        assert scored["pred"].to_list() == [2.0, 4.0, 6.0]

    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            ("matrix", r"shape \(3, 2\)"),
            ("one_too_many", "4 predictions for 3 rows"),
        ],
    )
    def test_a_result_that_is_not_one_per_row_is_refused(self, tmp_path, kind, message):
        scoring = _wrap_pyfunc(_shaped_pyfunc(tmp_path, kind), task="regression")

        with pytest.raises(ValueError, match=message):
            _score_eager(scoring, _A.lazy(), ["a"], "pred", "regression").collect()

    def test_several_declared_outputs_of_another_kind_are_refused_at_load(self, tmp_path):
        from mlflow.types import ColSpec

        from haute.errors import ConfigError

        outputs = [ColSpec("double", "low"), ColSpec("double", "high")]
        with pytest.raises(ConfigError, match=r"declares 2 outputs \(low, high\)"):
            _wrap_pyfunc(_shaped_pyfunc(tmp_path, "matrix", outputs), task="regression")


class TestDeclaredClassifierPyfunc:
    """A pyfunc declaring the classifier pair must return it, one row per input."""

    def test_probabilities_asked_for_alone_come_from_their_own_call(self, tmp_path):
        scoring = _wrap_pyfunc(
            _shaped_pyfunc(tmp_path, "pair", _pair_outputs()), task="classification"
        )
        raw = scoring.raw_model
        x_data = _prepare_predict_frame(_A, ["a"], frozenset(), "pyfunc")

        np.testing.assert_allclose(raw.predict_proba(x_data), [0.25, 0.5, 0.75])
        assert raw.predict(x_data).tolist() == ["no", "yes", "yes"]

    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            ("labels_only", r"returned columns \['pred_label'\]"),
            ("pair_missing_a_row", "2 predictions for 3 rows"),
        ],
    )
    def test_a_result_breaking_the_declared_pair_is_refused(self, tmp_path, kind, message):
        scoring = _wrap_pyfunc(
            _shaped_pyfunc(tmp_path, kind, _pair_outputs()), task="classification"
        )

        with pytest.raises(ValueError, match=message):
            _score_eager(scoring, _A.lazy(), ["a"], "pred", "classification").collect()


class TestHauteExposurePyfunc:
    """A logged model's exposure is an optional signature input, as natively."""

    def test_the_exposure_is_optional(self, haute_exposure_model):
        pyfunc, _native = haute_exposure_model
        scoring = _wrap_pyfunc(pyfunc, task="regression")
        assert "exposure" not in scoring.feature_names
        assert scoring.optional_feature_names == frozenset({"exposure"})

    @pytest.mark.parametrize("source", ["live", "batch"])
    @pytest.mark.parametrize("with_exposure", [True, False], ids=["exposure", "full_term"])
    def test_prices_as_the_native_artifact_does(self, haute_exposure_model, source, with_exposure):
        pyfunc, native = haute_exposure_model
        frame = _training_frame(n=20, seed=8)
        if not with_exposure:
            frame = frame.drop("exposure")

        scored = _scored(_wrap_pyfunc(pyfunc, task="regression"), frame, "regression", source)
        reference = _scored(native, frame, "regression", source)

        np.testing.assert_allclose(
            scored["pred"].to_numpy(), reference["pred"].to_numpy(), rtol=1e-9
        )
