"""The one dtype vocabulary: ``haute._polars_dtypes`` and its named views."""

from __future__ import annotations

import polars as pl
import pytest
from mlflow.types import DataType

from haute._polars_dtypes import (
    contract_dtype_name,
    contract_mlflow_type_name,
    dtype_to_spec,
    is_contract_decimal_name,
    parse_dtype,
    rendered_dtype_mlflow_type_name,
)
from haute._rating import rating_dtype_descriptor, rating_dtype_from_descriptor
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
