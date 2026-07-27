"""Tests for haute.modelling._signature — MLflow ModelSignature builder.

The ``_signature`` helper exists so every trained haute model ships with an
authoritative, ordered feature/dtype contract via ``mlflow.models.ModelSignature``.
Without a signature, feature-order drift between training and scoring is silent
and produces wrong predictions.  This suite pins down the contract the helper
must satisfy:

1. The input schema preserves the caller's feature order exactly.
2. Polars dtype strings round-trip to MLflow ``DataType`` values.
3. Missing/extra metadata fails loudly — no silent fallbacks.
4. Output schema is regression-shaped (single ``pred`` column) or
   classification-shaped (``pred_label`` + ``pred_proba``).

These tests MUST fail until ``haute.modelling._signature.build_signature``
is implemented (import errors count as failures).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import mlflow
import pandas as pd
import polars as pl
import pytest
from mlflow.models import ModelSignature  # re-exported canonically from mlflow.models
from mlflow.types import DataType


class _TemporalEchoModel(mlflow.pyfunc.PythonModel):
    """Small real pyfunc model used to exercise MLflow schema enforcement."""

    def predict(self, context: object, model_input: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"pred": [1.0] * len(model_input)})


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _input_type_map(sig: ModelSignature) -> dict[str, DataType]:
    """Extract ``{col_name: DataType}`` from a signature's inputs."""
    return {col.name: col.type for col in sig.inputs.inputs}


def _input_name_order(sig: ModelSignature) -> list[str]:
    """Return the input column names in the order the signature stores them."""
    return [col.name for col in sig.inputs.inputs]


def _output_type_map(sig: ModelSignature) -> dict[str, DataType]:
    """Extract ``{col_name: DataType}`` from a signature's outputs."""
    return {col.name: col.type for col in sig.outputs.inputs}


# ---------------------------------------------------------------------------
# 1. Basic regression signature
# ---------------------------------------------------------------------------


class TestBasicRegression:
    def test_returns_model_signature_with_expected_inputs(self) -> None:
        """A regression call returns a signature whose inputs match the features."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "income"],
            feature_types={"age": "Int64", "income": "Float64"},
            categorical_features=[],
            target_name="loss",
            target_type="Float64",
            task="regression",
        )

        assert isinstance(sig, ModelSignature)
        assert _input_name_order(sig) == ["age", "income"]
        type_map = _input_type_map(sig)
        assert type_map["age"] == DataType.long
        assert type_map["income"] == DataType.double

    def test_regression_has_single_pred_output(self) -> None:
        """Regression output schema is a single ``pred`` float column."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "income"],
            feature_types={"age": "Int64", "income": "Float64"},
            categorical_features=[],
            target_name="loss",
            target_type="Float64",
            task="regression",
        )

        out_map = _output_type_map(sig)
        assert list(out_map.keys()) == ["pred"], (
            "Regression signature must expose exactly one output column named 'pred'"
        )
        assert out_map["pred"] == DataType.double

    def test_regression_is_default_task(self) -> None:
        """``task`` defaults to 'regression' per the spec."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age"],
            feature_types={"age": "Int64"},
            categorical_features=[],
            target_name="loss",
            target_type="Float64",
        )

        out_map = _output_type_map(sig)
        assert "pred" in out_map
        assert "pred_label" not in out_map


# ---------------------------------------------------------------------------
# 2. Basic classification signature
# ---------------------------------------------------------------------------


class TestBasicClassification:
    def test_classification_output_has_label_and_proba(self) -> None:
        """Classification output exposes ``pred_label`` and ``pred_proba`` columns."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "income"],
            feature_types={"age": "Int64", "income": "Float64"},
            categorical_features=[],
            target_name="claim_flag",
            target_type="Int64",
            task="classification",
        )

        assert isinstance(sig, ModelSignature)
        out_names = list(_output_type_map(sig).keys())
        assert "pred_label" in out_names, (
            "Classification signature must expose a 'pred_label' column"
        )
        assert "pred_proba" in out_names, (
            "Classification signature must expose a 'pred_proba' column"
        )

    def test_classification_pred_proba_is_float(self) -> None:
        """The probability column must be a float-family DataType."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age"],
            feature_types={"age": "Int64"},
            categorical_features=[],
            target_name="claim_flag",
            target_type="Int64",
            task="classification",
        )

        out_map = _output_type_map(sig)
        assert out_map["pred_proba"] in (DataType.double, DataType.float), (
            f"pred_proba must be a float DataType, got {out_map['pred_proba']!r}"
        )

    def test_classification_inputs_preserve_feature_types(self) -> None:
        """Classification keeps the caller's input dtypes intact."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="claim_flag",
            target_type="Int64",
            task="classification",
        )

        type_map = _input_type_map(sig)
        assert type_map["age"] == DataType.long
        assert type_map["region"] == DataType.string


# ---------------------------------------------------------------------------
# 3. Feature order preserved
# ---------------------------------------------------------------------------


class TestFeatureOrderPreserved:
    def test_non_alphabetical_order_preserved(self) -> None:
        """Feature order must match the ``features`` argument exactly, not sort."""
        from haute.modelling._signature import build_signature

        features = ["z", "a", "m"]
        sig = build_signature(
            features=features,
            feature_types={"z": "Int64", "a": "Float64", "m": "String"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )

        assert _input_name_order(sig) == ["z", "a", "m"], (
            "Signature inputs must preserve caller's feature order — "
            "order mismatch between training and scoring silently corrupts predictions"
        )

    def test_reverse_order_preserved(self) -> None:
        """Reversing the input list reverses the signature input order."""
        from haute.modelling._signature import build_signature

        features = ["delta", "charlie", "bravo", "alpha"]
        sig = build_signature(
            features=features,
            feature_types={
                "alpha": "Int64",
                "bravo": "Int64",
                "charlie": "Int64",
                "delta": "Int64",
            },
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )

        assert _input_name_order(sig) == features

    def test_many_features_order_preserved(self) -> None:
        """A larger feature list still preserves order."""
        from haute.modelling._signature import build_signature

        features = [f"f{i}" for i in range(20)]
        feature_types = {name: "Float64" for name in features}

        sig = build_signature(
            features=features,
            feature_types=feature_types,
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )

        assert _input_name_order(sig) == features


# ---------------------------------------------------------------------------
# 4. Feature types mapped correctly
# ---------------------------------------------------------------------------


class TestFeatureTypeMapping:
    def test_int64_maps_to_long(self) -> None:
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Int64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_type_map(sig)["x"] == DataType.long

    def test_float64_maps_to_double(self) -> None:
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_type_map(sig)["x"] == DataType.double

    def test_string_maps_to_string(self) -> None:
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "String"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_type_map(sig)["x"] == DataType.string

    def test_boolean_maps_to_boolean(self) -> None:
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Boolean"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_type_map(sig)["x"] == DataType.boolean

    def test_all_four_primary_dtypes_together(self) -> None:
        """The four primary polars dtypes coexist in one signature."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["i", "f", "s", "b"],
            feature_types={
                "i": "Int64",
                "f": "Float64",
                "s": "String",
                "b": "Boolean",
            },
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        type_map = _input_type_map(sig)
        assert type_map["i"] == DataType.long
        assert type_map["f"] == DataType.double
        assert type_map["s"] == DataType.string
        assert type_map["b"] == DataType.boolean


# ---------------------------------------------------------------------------
# 5. Categorical features present
# ---------------------------------------------------------------------------


class TestCategoricalFeatures:
    def test_categorical_subset_tolerated(self) -> None:
        """Categorical features that ARE in ``features`` don't break the build."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )

        assert isinstance(sig, ModelSignature)
        # Both columns appear; categorical metadata does not need to survive
        # through MLflow's input schema (MLflow doesn't have a dedicated
        # categorical type), but the column itself must be present.
        assert set(_input_name_order(sig)) == {"age", "region"}

    def test_all_features_categorical(self) -> None:
        """Works when every feature is marked categorical."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["region", "channel"],
            feature_types={"region": "String", "channel": "String"},
            categorical_features=["region", "channel"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_name_order(sig) == ["region", "channel"]

    def test_empty_categorical_list_is_fine(self) -> None:
        """An empty categorical list is explicitly allowed."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age"],
            feature_types={"age": "Int64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert _input_name_order(sig) == ["age"]


# ---------------------------------------------------------------------------
# 6. Missing feature type raises
# ---------------------------------------------------------------------------


class TestMissingFeatureTypeRaises:
    def test_one_missing_type_raises(self) -> None:
        """Missing type for feature 'b' must raise with a clear message naming 'b'."""
        from haute.modelling._signature import build_signature

        with pytest.raises((ValueError, KeyError)) as excinfo:
            build_signature(
                features=["a", "b"],
                feature_types={"a": "Int64"},  # 'b' missing
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            )
        assert "b" in str(excinfo.value), (
            "Error must name the missing feature 'b' so the user can fix it"
        )

    def test_multiple_missing_types_raises(self) -> None:
        """With multiple missing types, at least one missing feature is named."""
        from haute.modelling._signature import build_signature

        with pytest.raises((ValueError, KeyError)) as excinfo:
            build_signature(
                features=["a", "b", "c"],
                feature_types={"a": "Int64"},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            )
        msg = str(excinfo.value)
        # At least one of the missing features must appear in the error
        assert "b" in msg or "c" in msg, (
            f"Error message must name at least one missing feature; got: {msg!r}"
        )

    def test_empty_feature_types_with_features_raises(self) -> None:
        """``feature_types={}`` when features is non-empty must raise."""
        from haute.modelling._signature import build_signature

        with pytest.raises((ValueError, KeyError)):
            build_signature(
                features=["a"],
                feature_types={},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            )


# ---------------------------------------------------------------------------
# 7. Categorical feature not in features list raises
# ---------------------------------------------------------------------------


class TestCategoricalNotInFeaturesRaises:
    def test_extra_categorical_raises(self) -> None:
        """A categorical name not in ``features`` must raise naming 'c'."""
        from haute.modelling._signature import build_signature

        with pytest.raises(ValueError) as excinfo:
            build_signature(
                features=["a", "b"],
                feature_types={"a": "Int64", "b": "Float64"},
                categorical_features=["c"],  # 'c' not in features
                target_name="y",
                target_type="Float64",
                task="regression",
            )
        assert "c" in str(excinfo.value), "Error must name the bad categorical 'c' for the user"

    def test_mixed_valid_and_invalid_categorical_raises(self) -> None:
        """If some categoricals are valid and one is not, still raise and name the bad one."""
        from haute.modelling._signature import build_signature

        with pytest.raises(ValueError) as excinfo:
            build_signature(
                features=["a", "b"],
                feature_types={"a": "Int64", "b": "String"},
                categorical_features=["b", "phantom"],
                target_name="y",
                target_type="Float64",
                task="regression",
            )
        assert "phantom" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 8. Empty features raises
# ---------------------------------------------------------------------------


class TestEmptyFeaturesRaises:
    def test_empty_features_raises(self) -> None:
        """``features=[]`` is meaningless — a signature must have at least one input."""
        from haute.modelling._signature import build_signature

        with pytest.raises(ValueError):
            build_signature(
                features=[],
                feature_types={},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            )


# ---------------------------------------------------------------------------
# 9. Unknown polars dtype
# ---------------------------------------------------------------------------


class TestUnknownDtypeRaises:
    def test_bogus_dtype_raises_and_names_it(self) -> None:
        """An unrecognised dtype string must raise and name the bad dtype in the error."""
        from haute.modelling._signature import build_signature

        with pytest.raises(ValueError) as excinfo:
            build_signature(
                features=["a"],
                feature_types={"a": "NotARealDtype"},
                categorical_features=[],
                target_name="y",
                target_type="Float64",
                task="regression",
            )
        assert "NotARealDtype" in str(excinfo.value), (
            "Unknown dtype error must name the bad dtype string so users can fix it"
        )

    def test_bogus_target_dtype_raises(self) -> None:
        """An unrecognised target dtype must raise with the bad dtype named."""
        from haute.modelling._signature import build_signature

        with pytest.raises(ValueError) as excinfo:
            build_signature(
                features=["a"],
                feature_types={"a": "Int64"},
                categorical_features=[],
                target_name="y",
                target_type="AlsoBogus",
                task="regression",
            )
        assert "AlsoBogus" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 10. Returns real ModelSignature
# ---------------------------------------------------------------------------


class TestReturnsRealModelSignature:
    def test_is_instance_of_model_signature(self) -> None:
        """Result must be a genuine ``mlflow.models.ModelSignature`` object."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Int64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert isinstance(sig, ModelSignature), f"Expected ModelSignature, got {type(sig).__name__}"

    def test_not_a_dict(self) -> None:
        """Must not return a dict — MLflow serving requires the real object."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Int64"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        assert not isinstance(sig, dict)
        assert not isinstance(sig, tuple)


# ---------------------------------------------------------------------------
# 11. Signature serializable round-trip
# ---------------------------------------------------------------------------


class TestSerializableRoundTrip:
    def test_to_dict_from_dict_roundtrip_regression(self) -> None:
        """Regression signature must round-trip through dict form."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "region", "premium"],
            feature_types={
                "age": "Int64",
                "region": "String",
                "premium": "Float64",
            },
            categorical_features=["region"],
            target_name="loss",
            target_type="Float64",
            task="regression",
        )

        as_dict = sig.to_dict()
        round_tripped = ModelSignature.from_dict(as_dict)
        assert round_tripped == sig, (
            "Signature must round-trip through to_dict/from_dict; "
            "this is how MLflow persists signatures on disk"
        )

    def test_to_dict_from_dict_roundtrip_classification(self) -> None:
        """Classification signature must round-trip through dict form."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["age", "region"],
            feature_types={"age": "Int64", "region": "String"},
            categorical_features=["region"],
            target_name="claim",
            target_type="Int64",
            task="classification",
        )

        round_tripped = ModelSignature.from_dict(sig.to_dict())
        assert round_tripped == sig


# ---------------------------------------------------------------------------
# 12. Target type respected
# ---------------------------------------------------------------------------


class TestTargetTypeRespected:
    def test_int64_target_produces_long_output(self) -> None:
        """``target_type='Int64'`` yields a ``long`` output for regression."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="count",
            target_type="Int64",
            task="regression",
        )

        out_map = _output_type_map(sig)
        assert out_map["pred"] == DataType.long, (
            f"Int64 target must yield long regression output, got {out_map['pred']!r}"
        )

    def test_float64_target_produces_double_output(self) -> None:
        """``target_type='Float64'`` yields a ``double`` output for regression."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Int64"},
            categorical_features=[],
            target_name="loss",
            target_type="Float64",
            task="regression",
        )

        out_map = _output_type_map(sig)
        assert out_map["pred"] == DataType.double

    def test_classification_pred_label_uses_target_type(self) -> None:
        """For classification, ``pred_label`` should reflect the target's dtype."""
        from haute.modelling._signature import build_signature

        sig = build_signature(
            features=["x"],
            feature_types={"x": "Int64"},
            categorical_features=[],
            target_name="label",
            target_type="String",
            task="classification",
        )

        out_map = _output_type_map(sig)
        assert out_map["pred_label"] == DataType.string, (
            "String target must yield a string pred_label for classification"
        )


# ---------------------------------------------------------------------------
# 13. Temporal and Decimal contract boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dtype",
    [
        pl.Date,
        pl.Datetime,
        *[
            pl.Datetime(time_unit, time_zone)
            for time_unit in ("ns", "us", "ms")
            for time_zone in (None, "UTC", "Europe/London")
        ],
    ],
)
def test_real_polars_temporal_dtypes_map_to_mlflow_datetime(dtype: object) -> None:
    """Date and every canonical Polars Datetime form are preserved deliberately."""
    from haute.modelling._signature import _map_dtype
    from haute.modelling._training_job import _polars_dtype_name

    dtype_name = _polars_dtype_name(dtype)
    assert _map_dtype(dtype_name) == DataType.datetime


@pytest.mark.parametrize(
    "dtype_name",
    [
        "DatetimeGarbage",
        "Datetime(time_unit='seconds', time_zone=None)",
        "Datetime(time_unit='us', time_zone=UTC)",
        "Datetime(time_unit='us', time_zone=None) trailing",
    ],
)
def test_datetime_lookalikes_are_rejected(dtype_name: str) -> None:
    from haute.modelling._signature import _map_dtype

    with pytest.raises(ValueError, match="Unknown polars dtype"):
        _map_dtype(dtype_name)


@pytest.mark.parametrize(
    "dtype",
    [pl.Decimal, pl.Decimal(precision=12, scale=3)],
)
def test_decimal_dtypes_are_rejected_with_explicit_cast_guidance(dtype: object) -> None:
    from haute.modelling._signature import _map_dtype
    from haute.modelling._training_job import _polars_dtype_name

    dtype_name = _polars_dtype_name(dtype)
    with pytest.raises(ValueError) as excinfo:
        _map_dtype(dtype_name)

    message = str(excinfo.value)
    assert dtype_name in message
    assert "MLflow 3.x" in message
    assert "String" in message
    assert "Float64" in message


def test_temporal_signature_persists_through_mlflow_dict_roundtrip() -> None:
    from haute.modelling._signature import build_signature

    sig = build_signature(
        features=["event_date", "event_time", "zoned_event_time"],
        feature_types={
            "event_date": "Date",
            "event_time": "Datetime(time_unit='ns', time_zone=None)",
            "zoned_event_time": "Datetime(time_unit='ms', time_zone='Europe/London')",
        },
        categorical_features=[],
        target_name="loss",
        target_type="Float64",
    )

    restored = ModelSignature.from_dict(sig.to_dict())
    assert restored == sig
    assert set(_input_type_map(restored).values()) == {DataType.datetime}


def test_real_mlflow_pyfunc_roundtrip_enforces_temporal_signature(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signature built from production dtype descriptors survives log/load/predict."""
    from haute._mlflow_io import _prepare_predict_frame
    from haute.modelling._signature import build_signature
    from haute.modelling._training_job import _polars_dtype_name

    polars_frame = pl.DataFrame(
        {
            "event_date": [date(2025, 1, 2)],
            "event_time": [datetime(2025, 1, 2, 3, 4, 5)],
            "zoned_event_time": [datetime(2025, 7, 2, 3, 4, 5, tzinfo=ZoneInfo("Europe/London"))],
        },
        schema={
            "event_date": pl.Date,
            "event_time": pl.Datetime("us"),
            "zoned_event_time": pl.Datetime("ms", "Europe/London"),
        },
    )
    signature = build_signature(
        features=list(polars_frame.columns),
        feature_types={
            name: _polars_dtype_name(dtype) for name, dtype in polars_frame.schema.items()
        },
        categorical_features=[],
        target_name="loss",
        target_type="Float64",
    )
    pandas_frame = _prepare_predict_frame(
        polars_frame,
        list(polars_frame.columns),
        flavor="pyfunc",
    )
    assert not isinstance(pandas_frame["zoned_event_time"].dtype, pd.DatetimeTZDtype)
    assert pandas_frame["zoned_event_time"].iloc[0] == pd.Timestamp("2025-07-02 02:04:05")
    original_tracking_uri = mlflow.get_tracking_uri()
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(tmp_path.as_uri())
    try:
        mlflow.set_experiment("temporal-signature")
        with mlflow.start_run() as run:
            mlflow.pyfunc.log_model(
                artifact_path="model",
                python_model=_TemporalEchoModel(),
                signature=signature,
                pip_requirements=[],
            )
            model_uri = f"runs:/{run.info.run_id}/model"

        model_info = mlflow.models.get_model_info(model_uri)
        assert model_info.signature is not None
        assert set(_input_type_map(model_info.signature).values()) == {DataType.datetime}
        prediction = mlflow.pyfunc.load_model(model_uri).predict(pandas_frame)
        assert prediction["pred"].tolist() == [1.0]
    finally:
        mlflow.set_tracking_uri(original_tracking_uri)
