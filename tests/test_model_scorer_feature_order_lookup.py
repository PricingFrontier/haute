from __future__ import annotations

from collections.abc import Iterable, Iterator

import polars as pl

from haute._model_scorer import (
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


class _SchemaLike:
    def __init__(self, names: Iterable[str]) -> None:
        self._names = list(names)

    def names(self) -> list[str]:
        return self._names

    def __getitem__(self, name: str) -> pl.DataType:
        if name not in self._names:
            raise KeyError(name)
        return pl.Float64

    def items(self) -> Iterator[tuple[str, pl.DataType]]:
        for name in self._names:
            yield name, pl.Float64


def test_features_scattered_through_a_wide_input_validate_in_the_model_order() -> None:
    expected = [f"feature_{idx}" for idx in range(12)]
    schema_names: list[str] = []
    for idx, feature in enumerate(expected):
        schema_names.extend(f"noise_{idx}_{j}" for j in range(125))
        schema_names.append(feature)
    schema_names.extend(f"tail_noise_{j}" for j in range(500))

    usable, missing = _validate_features_uncached(
        _ScoringModel(expected),
        _SchemaLike(schema_names),  # type: ignore[arg-type]
    )

    assert usable == expected
    assert missing == []


def test_duplicate_schema_names_keep_first_position_semantics() -> None:
    """A schema-like caller that repeats a name still finds every feature once."""
    usable, missing = _validate_features_uncached(
        _ScoringModel(["dup", "extra"]),
        _SchemaLike(["dup", "extra", "dup"]),  # type: ignore[arg-type]
    )

    assert usable == ["dup", "extra"]
    assert missing == []


def test_a_reordered_input_validates_in_the_model_order() -> None:
    """Scoring selects features by name, so the input's column order never matters."""
    usable, missing = _validate_features_uncached(
        _ScoringModel(["first", "second"]),
        pl.Schema({"second": pl.Float64, "first": pl.Float64}),
    )

    assert (usable, missing) == (["first", "second"], [])


def test_either_schema_order_validates_through_the_cache() -> None:
    model = _ScoringModel(["first", "second"])
    _clear_feature_validation_cache()

    for schema in (
        pl.Schema({"first": pl.Float64, "second": pl.Float64}),
        pl.Schema({"second": pl.Float64, "first": pl.Float64}),
    ):
        assert _validate_features(model, schema) == (["first", "second"], [])
