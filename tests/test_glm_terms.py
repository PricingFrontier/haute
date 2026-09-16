"""Pure GLM term contract: expression grammar, column resolution, schema checks."""

from __future__ import annotations

import pytest

from haute.errors import HauteValidationError
from haute.modelling._glm_terms import (
    SUPPORTED_TERM_TYPES,
    expression_identifiers,
    glm_model_columns,
    validate_glm_model_columns,
)


class TestExpressionIdentifiers:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("age", ["age"]),
            ("age ** 2", ["age"]),
            ("age**2.5", ["age"]),
            ("age * income", ["age", "income"]),
            ("age + 1", ["age"]),
            ("age - income", ["age", "income"]),
            ("age / 12", ["age"]),
            ("  age ** 2  ", ["age"]),
        ],
    )
    def test_accepts_the_rustystats_grammar(self, expr, expected):
        assert expression_identifiers(expr) == expected

    @pytest.mark.parametrize(
        "expr",
        ["", "age ** 2 + 1", "log(age)", "age > 65", "2 * age", "age ** income ** 2", "a.b * c"],
    )
    def test_rejects_anything_outside_the_grammar(self, expr):
        with pytest.raises(HauteValidationError, match="Supported forms"):
            expression_identifiers(expr)


class TestGlmModelColumns:
    def test_reads_expression_identifiers_and_interaction_factors(self):
        terms = {
            "age": {"type": "bs", "df": 4},
            "age_sq": {"type": "expression", "expr": "age ** 2"},
            "ratio": {"type": "expression", "expr": "income / age"},
            "region": {"type": "categorical"},
        }
        interactions = [
            {"factors": ["region", "vehicle_age"], "include_main": True},
            {"factors": ["", ""], "include_main": True},
        ]
        assert glm_model_columns(terms, interactions) == [
            "age",
            "region",
            "income",
            "vehicle_age",
        ]

    def test_empty_terms_and_no_interactions_resolve_to_nothing(self):
        assert glm_model_columns({}, None) == []

    def test_rejects_unsupported_expression_grammar(self):
        with pytest.raises(HauteValidationError, match="'age_sq'"):
            glm_model_columns({"age_sq": {"type": "expression", "expr": "log(age)"}}, [])

    def test_rejects_unknown_types_and_redirecting_keys(self):
        with pytest.raises(HauteValidationError, match="frequency_encoding"):
            glm_model_columns({"region": {"type": "frequency_encoding"}}, [])
        with pytest.raises(HauteValidationError, match="variable"):
            glm_model_columns({"brand_te": {"type": "target_encoding", "variable": "brand"}}, [])
        with pytest.raises(HauteValidationError, match="interaction"):
            glm_model_columns(
                {"brand": {"type": "target_encoding", "interaction": ["brand", "region"]}},
                [],
            )

    def test_rejects_expression_keyed_by_a_column_it_reads(self):
        with pytest.raises(HauteValidationError, match="'age'"):
            glm_model_columns({"age": {"type": "expression", "expr": "age ** 2"}}, [])
        with pytest.raises(HauteValidationError, match="'income'"):
            glm_model_columns({"income": {"type": "expression", "expr": "age * income"}}, [])

    def test_accepts_an_expression_beside_the_native_term_it_reads(self):
        terms = {"age": {"type": "linear"}, "age2": {"type": "expression", "expr": "age ** 2"}}
        assert glm_model_columns(terms, []) == ["age"]

    def test_rejects_malformed_entries(self):
        with pytest.raises(HauteValidationError, match="term names"):
            glm_model_columns({"": {"type": "linear"}}, [])
        with pytest.raises(HauteValidationError, match="'age'"):
            glm_model_columns({"age": "linear"}, [])
        with pytest.raises(HauteValidationError, match="'age_sq'"):
            glm_model_columns({"age_sq": {"type": "expression"}}, [])

    def test_supported_types_are_exactly_the_seven(self):
        assert SUPPORTED_TERM_TYPES == frozenset(
            {"linear", "categorical", "bs", "ns", "ms", "target_encoding", "expression"}
        )


class TestValidateGlmModelColumns:
    SCHEMA = ["age", "income", "region", "vehicle_age", "target", "x_sq"]

    def test_returns_resolved_columns_when_everything_is_present(self):
        terms = {"age": {"type": "linear"}, "age_sq": {"type": "expression", "expr": "age ** 2"}}
        assert validate_glm_model_columns(terms, [{"factors": ["age", "region"]}], self.SCHEMA) == [
            "age",
            "region",
        ]

    def test_rejects_unknown_column(self):
        with pytest.raises(HauteValidationError, match=r"not found in training data: \['height'\]"):
            validate_glm_model_columns(
                {"age": {"type": "linear"}, "h2": {"type": "expression", "expr": "height ** 2"}},
                [],
                self.SCHEMA,
            )

    def test_rejects_unknown_interaction_factor(self):
        with pytest.raises(HauteValidationError, match=r"\['zone'\]"):
            validate_glm_model_columns(
                {"age": {"type": "linear"}}, [{"factors": ["age", "zone"]}], self.SCHEMA
            )

    def test_rejects_expression_keyed_by_a_schema_column(self):
        with pytest.raises(HauteValidationError, match="'x_sq' .*names a column"):
            validate_glm_model_columns(
                {"x_sq": {"type": "expression", "expr": "age ** 2"}}, [], self.SCHEMA
            )
