"""The one dtype vocabulary: ``haute._polars_dtypes`` and its named views."""

from __future__ import annotations

import inspect

import polars as pl
import pytest
from mlflow.types import DataType

from haute._polars_dtypes import (
    contract_dtype_name,
    contract_mlflow_type_name,
    dtype_to_spec,
    holds_wide_integer,
    is_contract_decimal_name,
    is_string_or_categorical,
    parse_dtype,
    parse_rendered_dtype,
    rendered_dtype_mlflow_type_name,
    wide_integer_column,
)
from haute._rating import (
    normalise_rating_key,
    rating_dtype_descriptor,
    rating_dtype_from_descriptor,
)
from haute._validation_error import ConfigSettingError
from haute.errors import SchemaMismatchError


@pytest.mark.parametrize("name", ["col", "DataFrame", "Expr", "Config", "when"])
def test_a_polars_attribute_that_is_not_a_dtype_is_rejected(name: str) -> None:
    with pytest.raises(SchemaMismatchError, match=rf"column=premium, dtype={name}\)"):
        parse_dtype(name, column="premium")


RATING_DTYPES = [
    pl.Int8,
    pl.Int128,
    pl.UInt32,
    pl.Float32,
    pl.Boolean,
    pl.String,
    pl.Date,
    pl.Time,
    pl.Null,
    pl.Categorical(),
    pl.Datetime("ms", "Europe/London"),
    pl.Duration("ns"),
    pl.Decimal(12, 3),
    pl.Enum(["low", "high"]),
]


@pytest.mark.parametrize("dtype", RATING_DTYPES, ids=str)
def test_a_rating_descriptor_is_the_shared_spec_under_ratings_key_names(dtype) -> None:
    descriptor = rating_dtype_descriptor(dtype)
    spec = dtype_to_spec(dtype)

    if isinstance(spec, str):
        assert descriptor == {"kind": spec}
    else:
        renamed = {"type": "kind", "time_unit": "timeUnit", "time_zone": "timeZone"}
        assert descriptor == {renamed.get(key, key): value for key, value in spec.items()}
    assert rating_dtype_from_descriptor(descriptor) == dtype
    assert rating_dtype_from_descriptor(descriptor) == parse_dtype(spec)


@pytest.mark.parametrize(
    "dtype",
    [pl.Binary, pl.List(pl.Int64), pl.Struct({"a": pl.Int64}), pl.Array(pl.Int8, 2), pl.Object],
    ids=str,
)
def test_a_dtype_the_codec_knows_but_rating_does_not_support_is_rejected(dtype) -> None:
    with pytest.raises(ValueError, match="unsupported rating factor dtype"):
        rating_dtype_descriptor(dtype)


def test_a_map_which_the_codec_refuses_is_not_a_rating_factor() -> None:
    with pytest.raises(ValueError, match="unsupported rating factor dtype"):
        rating_dtype_descriptor(pl.Map(pl.String, pl.Int64))


@pytest.mark.parametrize(
    ("dtype", "contract_name", "mlflow_type"),
    [
        (pl.Boolean, "Boolean", DataType.boolean),
        (pl.String, "String", DataType.string),
        (pl.Categorical, "String", DataType.string),
        (pl.Int8, "Int64", DataType.long),
        (pl.UInt64, "Int64", DataType.long),
        (pl.Float32, "Float64", DataType.double),
        (pl.Date, "Date", DataType.datetime),
        (
            pl.Datetime("ms", "UTC"),
            "Datetime(time_unit='ms', time_zone='UTC')",
            DataType.datetime,
        ),
    ],
    ids=str,
)
def test_the_feature_contract_view_and_its_mlflow_projection(
    dtype, contract_name: str, mlflow_type: DataType
) -> None:
    assert contract_dtype_name(dtype) == contract_name
    assert DataType[contract_mlflow_type_name(contract_name)] == mlflow_type  # type: ignore[misc]


def test_a_contract_name_mlflow_cannot_represent_has_no_mlflow_type() -> None:
    decimal_name = contract_dtype_name(pl.Decimal(12, 3))

    assert contract_mlflow_type_name(decimal_name) is None
    assert is_contract_decimal_name(decimal_name)
    assert contract_mlflow_type_name("Datetime(time_unit='seconds', time_zone=None)") is None
    assert not is_contract_decimal_name("Float64")


@pytest.mark.parametrize(
    ("dtype", "mlflow_type"),
    [
        (pl.Int32, DataType.integer),
        (pl.Int64, DataType.long),
        (pl.Float32, DataType.float),
        (pl.Float64, DataType.double),
        (pl.Enum(["a"]), DataType.string),
        (pl.Datetime("us", "UTC"), DataType.datetime),
    ],
    ids=str,
)
def test_the_rendered_dtype_mlflow_view_keeps_widths(dtype, mlflow_type: DataType) -> None:
    rendered = rendered_dtype_mlflow_type_name(str(dtype))

    assert rendered is not None
    assert DataType[rendered] == mlflow_type


@pytest.mark.parametrize("dtype", [pl.Decimal(10, 2), pl.List(pl.Int64), pl.Binary], ids=str)
def test_a_rendered_dtype_without_an_mlflow_type_is_reported_as_none(dtype) -> None:
    assert rendered_dtype_mlflow_type_name(str(dtype)) is None


@pytest.mark.parametrize(
    "dtype",
    [
        pl.Int64(),
        pl.String(),
        pl.Boolean(),
        pl.Date(),
        pl.Categorical(),
        pl.Datetime("us"),
        pl.Datetime("ms", "Europe/London"),
        pl.Duration("ns"),
        pl.Decimal(10, 2),
        pl.Enum(["a", "b"]),
        pl.List(pl.Int64),
        pl.Array(pl.Int8, 3),
        pl.Struct({"a": pl.Int64, "b": pl.List(pl.String)}),
    ],
    ids=str,
)
def test_a_rendered_dtype_is_rebuilt_from_its_rendering(dtype) -> None:
    assert parse_rendered_dtype(str(dtype)) == dtype


@pytest.mark.parametrize(
    "rendered",
    ["col", "DataFrame", "Int64(", "__import__('os')", "List(col('a'))", "Datetime(time_unit=x)"],
)
def test_a_rendering_that_is_not_a_dtype_is_rejected(rendered: str) -> None:
    with pytest.raises(SchemaMismatchError, match="Unsupported rendered dtype"):
        parse_rendered_dtype(rendered, column="premium")


# One sample at least of every dtype class Polars exports, including the
# parametrised and nested forms Polars 2 produces.
DTYPE_SAMPLES = [
    pl.Int8(),
    pl.Int16(),
    pl.Int32(),
    pl.Int64(),
    pl.Int128(),
    pl.UInt8(),
    pl.UInt16(),
    pl.UInt32(),
    pl.UInt64(),
    pl.UInt128(),
    pl.Float16(),
    pl.Float32(),
    pl.Float64(),
    pl.Boolean(),
    pl.String(),
    pl.Binary(),
    pl.Null(),
    pl.Object(),
    pl.Unknown(),
    pl.Date(),
    pl.Time(),
    pl.Datetime("us"),
    pl.Datetime("ms", "Europe/London"),
    pl.Duration("ns"),
    pl.Decimal(10, 2),
    pl.Decimal(38, 0),
    pl.Enum(["low", "high"]),
    pl.Categorical(),
    pl.Categorical("lexical"),
    pl.Categorical(pl.Categories("pool", "haute", pl.UInt8)),
    pl.List(pl.Int128),
    pl.List(pl.Categorical("lexical")),
    pl.Array(pl.Int8, 3),
    pl.Struct({"a": pl.UInt128, "b": pl.List(pl.String)}),
    pl.Map(pl.String, pl.Int64),
    pl.Extension("haute.test", pl.Int64),
]

# Haute reads a map as a list of key-value structs and an unknown extension as
# its storage type, so neither has a spec.
_REFUSED_BY_THE_CODEC = (pl.Map, pl.BaseExtension)


def test_every_dtype_class_polars_exports_has_a_codec_sample() -> None:
    exported = {
        obj
        for _name, obj in inspect.getmembers(pl)
        if isinstance(obj, type) and issubclass(obj, pl.DataType) and obj is not pl.DataType
    }

    uncovered = sorted(
        cls.__name__
        for cls in exported
        if not any(isinstance(sample, cls) for sample in DTYPE_SAMPLES)
    )

    assert uncovered == []


@pytest.mark.parametrize("dtype", DTYPE_SAMPLES, ids=str)
def test_every_dtype_round_trips_through_the_codec_or_is_refused(dtype) -> None:
    if isinstance(dtype, _REFUSED_BY_THE_CODEC):
        with pytest.raises(SchemaMismatchError, match="has no dtype spec"):
            dtype_to_spec(dtype)
        return

    spec = dtype_to_spec(dtype)

    assert parse_dtype(spec) == dtype
    assert dtype_to_spec(parse_dtype(spec)) == spec
    assert parse_rendered_dtype(str(dtype)) == dtype


def test_a_categorical_records_its_pool_and_the_default_pool_records_none() -> None:
    pooled = pl.Categorical(pl.Categories("pool", "haute", pl.UInt8))

    assert dtype_to_spec(pl.Categorical()) == {"type": "Categorical"}
    assert dtype_to_spec(pooled) == {
        "type": "Categorical",
        "categories": {"name": "pool", "namespace": "haute", "physical": "UInt8"},
    }
    assert pooled != pl.Categorical


@pytest.mark.parametrize(
    "categories",
    [
        {"name": "pool"},
        ["pool", "", "UInt32"],
        {"name": "p", "namespace": "", "physical": "x"},
        {"name": 1, "namespace": "", "physical": "UInt32"},
        {"name": "p", "namespace": "", "physical": "Int64"},
    ],
)
def test_a_malformed_category_pool_is_refused(categories) -> None:
    with pytest.raises(SchemaMismatchError):
        parse_dtype({"type": "Categorical", "categories": categories}, column="region")


@pytest.mark.parametrize(
    "dtype",
    [pl.String(), pl.Categorical(), pl.Categorical("lexical"), pl.Categorical(pl.Categories("p"))],
    ids=str,
)
def test_a_categorical_of_any_pool_is_text_and_a_string_feature(dtype) -> None:
    assert is_string_or_categorical(dtype)
    assert contract_dtype_name(dtype) == "String"


@pytest.mark.parametrize("dtype", [pl.Enum(["a"]), pl.Binary(), pl.Int64()], ids=str)
def test_other_dtypes_are_not_string_or_categorical(dtype) -> None:
    assert not is_string_or_categorical(dtype)


@pytest.mark.parametrize("dtype", [pl.Int128(), pl.UInt128()], ids=str)
def test_a_128_bit_integer_has_no_contract_name(dtype) -> None:
    with pytest.raises(SchemaMismatchError) as exc_info:
        contract_dtype_name(dtype, column="policy_key")

    message = str(exc_info.value)
    assert f"Column 'policy_key' is {dtype}, a 128-bit integer" in message
    assert "cast it upstream to Int64, or to Float64 if its values can exceed Int64" in message


def test_a_128_bit_integer_is_found_at_any_depth() -> None:
    schema = {
        "premium": pl.Float64(),
        "keys": pl.List(pl.Struct({"key": pl.String, "value": pl.UInt128})),
        "policy_key": pl.Int128(),
    }

    assert not holds_wide_integer(pl.Int64())
    assert not holds_wide_integer(pl.List(pl.Struct({"a": pl.Int64})))
    assert holds_wide_integer(pl.Array(pl.Int128, 2))
    assert wide_integer_column(schema) == ("keys", schema["keys"])
    assert wide_integer_column({"premium": pl.Float64()}) is None


@pytest.mark.parametrize(
    "dtype",
    [pl.Categorical("lexical"), pl.Categorical(pl.Categories("pool", "haute", pl.UInt8))],
    ids=str,
)
def test_a_pooled_categorical_rating_factor_is_looked_up_by_its_text(dtype) -> None:
    descriptor = rating_dtype_descriptor(dtype)

    assert descriptor == {"kind": "Categorical"}
    assert rating_dtype_from_descriptor(descriptor) == pl.Categorical()
    assert normalise_rating_key("north", dtype) == normalise_rating_key("north", pl.Categorical())


@pytest.mark.parametrize(
    "dtype", [pl.Map(pl.String, pl.Int64), pl.Extension("haute.test", pl.Int64)], ids=str
)
def test_a_map_or_extension_rating_factor_is_unsupported(dtype) -> None:
    with pytest.raises(ConfigSettingError, match="unsupported rating factor dtype"):
        rating_dtype_descriptor(dtype)
