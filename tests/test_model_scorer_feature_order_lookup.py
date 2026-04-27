from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import polars as pl
import pytest

from haute._model_scorer import (
    FeatureMismatchError,
    _clear_feature_validation_cache,
    _validate_features,
    _validate_features_uncached,
)


class _ScoringModel:
    def __init__(
        self,
        feature_names: list[str],
        cat_feature_names: Iterable[str] = (),
    ) -> None:
        self.feature_names = feature_names
        self.cat_feature_names = frozenset(cat_feature_names)


class _NoLinearIndexNames(list[str]):
    def index(self, *_args: Any, **_kwargs: Any) -> int:  # type: ignore[override]
        raise AssertionError("feature order lookup must not call list.index")


class _SchemaLike:
    def __init__(self, names: Iterable[str], *, forbid_linear_index: bool = False) -> None:
        names_type = _NoLinearIndexNames if forbid_linear_index else list
        self._names = names_type(names)

    def names(self) -> list[str]:
        return self._names

    def __getitem__(self, name: str) -> pl.DataType:
        if name not in self._names:
            raise KeyError(name)
        return pl.Float64

    def items(self) -> Iterator[tuple[str, pl.DataType]]:
        for name in self._names:
            yield name, pl.Float64


def test_feature_order_uses_precomputed_schema_positions_for_wide_inputs() -> None:
    expected = [f"feature_{idx}" for idx in range(12)]
    schema_names: list[str] = []
    for idx, feature in enumerate(expected):
        schema_names.extend(f"noise_{idx}_{j}" for j in range(125))
        schema_names.append(feature)
    schema_names.extend(f"tail_noise_{j}" for j in range(500))

    usable, missing = _validate_features_uncached(
        _ScoringModel(expected),
        _SchemaLike(schema_names, forbid_linear_index=True),  # type: ignore[arg-type]
    )

    assert usable == expected
    assert missing == []


def test_duplicate_schema_names_keep_first_position_semantics() -> None:
    """Preserve the old list.index behaviour if a schema-like caller has duplicates."""
    usable, missing = _validate_features_uncached(
        _ScoringModel(["dup", "extra"]),
        _SchemaLike(["dup", "extra", "dup"]),  # type: ignore[arg-type]
    )

    assert usable == ["dup", "extra"]
    assert missing == []


def test_order_mismatch_still_reports_actual_relative_order() -> None:
    with pytest.raises(FeatureMismatchError) as exc_info:
        _validate_features_uncached(
            _ScoringModel(["first", "second"]),
            pl.Schema({"second": pl.Float64, "first": pl.Float64}),
        )

    assert exc_info.value.context["actual"] == ["second", "first"]


def test_schema_order_change_still_misses_validation_cache() -> None:
    model = _ScoringModel(["first", "second"])
    _clear_feature_validation_cache()

    usable, missing = _validate_features(
        model,
        pl.Schema({"first": pl.Float64, "second": pl.Float64}),
    )
    assert usable == ["first", "second"]
    assert missing == []

    with pytest.raises(FeatureMismatchError) as exc_info:
        _validate_features(
            model,
            pl.Schema({"second": pl.Float64, "first": pl.Float64}),
        )

    assert exc_info.value.context["actual"] == ["second", "first"]
