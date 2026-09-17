"""Pure GLM term contract: dtype classes, grammar, parameters, schema checks, resolution."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute.errors import HauteValidationError
from haute.modelling._glm_terms import (
    DEFAULT_FIT_BY_CLASS,
    MAIN_FITS_BY_CLASS,
    SLOT_FITS_BY_CLASS,
    SUPPORTED_TERM_TYPES,
    TERM_KEYS,
    expression_identifiers,
    glm_dtype_class,
    glm_model_columns,
    monotone_constraint_terms,
    penalised_smooth_terms,
    resolve_categorical_levels,
    resolve_glm_design,
    validate_glm_model_columns,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "frontend/src/panels/modelling/__tests__/fixtures"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class TestDtypeClasses:
    @pytest.mark.parametrize(
        "dtype",
        [
            pl.Float16,
            pl.Float32,
            pl.Float64,
            pl.Decimal(10, 2),
            pl.Int8,
            pl.Int64,
            pl.Int128,
            pl.UInt8,
            pl.UInt64,
            pl.Boolean,
            pl.String,
            pl.Categorical,
            pl.Enum(["a", "b"]),
            pl.Date,
            pl.Datetime("us"),
            pl.Datetime("ms", "UTC"),
            pl.Duration("us"),
            pl.Time,
            pl.List(pl.Int64),
            pl.Array(pl.Int64, 2),
            pl.Struct({"a": pl.Int64}),
            pl.Binary,
            pl.Null,
            pl.Object,
        ],
    )
    def test_every_real_polars_dtype_name_is_pinned_by_the_shared_fixture(self, dtype):
        assert str(dtype) in _fixture("glmDtypeClasses.json")["polars"]

    def test_backend_classification_matches_the_shared_fixture(self):
        fixture = _fixture("glmDtypeClasses.json")
        cases = {**fixture["polars"], **fixture["aliases"]}
        assert {name: glm_dtype_class(name) for name in cases} == cases


class TestExpressionIdentifiers:
    def test_expression_grammar_matches_the_shared_fixture(self):
        for case in _fixture("glmExpressionGrammar.json")["accepted"]:
            assert expression_identifiers(case["expr"]) == case["identifiers"], case["expr"]
        for expr in _fixture("glmExpressionGrammar.json")["rejected"]:
            with pytest.raises(HauteValidationError, match="expression"):
                expression_identifiers(expr)

    def test_a_float_word_column_is_refused_with_a_rename_instruction(self):
        with pytest.raises(HauteValidationError, match="'inf'.*read as a number; rename"):
            expression_identifiers("age * inf")


class TestTermParameterContract:
    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ({"type": "linear", "df": 4}, r"Linear terms do not accept \['df'\]"),
            ({"type": "bs", "variable": "age"}, r"B-spline terms do not accept \['variable'\]"),
            ({"type": "target_encoding", "interaction": ["a"]}, r"do not accept \['interaction'\]"),
            (
                {"type": "linear", "monotonicity": "up"},
                "monotonicity must be 'increasing' or 'decreasing'",
            ),
            ({"type": "bs", "df": 5, "k": 6}, "set only one of df, k, or knots"),
            ({"type": "bs", "df": 3}, r"df must be an integer from degree \+ 1 = 4 to 20"),
            ({"type": "bs", "df": 5, "degree": 5}, r"df must be an integer from degree \+ 1 = 6"),
            ({"type": "ns", "df": 1}, "df must be an integer from 2 to 20"),
            ({"type": "ms", "k": 21}, "k must be an integer from 2 to 20"),
            ({"type": "bs", "df": 4.5}, "df must be an integer"),
            ({"type": "bs", "degree": 6}, "degree must be an integer from 1 to 5"),
            (
                {"type": "bs", "knots": []},
                "knots must be 1 to 20 finite, strictly increasing numbers",
            ),
            ({"type": "ns", "knots": [3.0, 2.0]}, "strictly increasing"),
            (
                {"type": "ns", "boundary_knots": [5.0, 1.0]},
                "exactly two finite, increasing numbers",
            ),
            (
                {"type": "bs", "knots": [2.0, 8.0], "boundary_knots": [0.0, 5.0]},
                "enclose every knot",
            ),
            (
                {"type": "target_encoding", "prior_weight": -1},
                "prior_weight must be 'auto' or a finite",
            ),
            (
                {"type": "target_encoding", "n_permutations": 101},
                "n_permutations must be an integer from 1 to 100",
            ),
            (
                {"type": "categorical", "levels": ["a"], "reference": "b"},
                "set levels or reference, not both",
            ),
            ({"type": "categorical", "reference": ""}, "reference must be a non-empty string"),
            (
                {"type": "categorical", "levels": [1]},
                "levels must be a non-empty list of unique strings",
            ),
            (
                {"type": "expression", "expr": "age ** 2", "monotonicity": "flat"},
                "monotonicity must be",
            ),
            ({}, "unsupported type None"),
        ],
    )
    def test_term_parameters_outside_the_contract_are_refused(self, spec, message):
        with pytest.raises(HauteValidationError, match=message):
            glm_model_columns({"age": spec}, [])

    @pytest.mark.parametrize(
        "spec",
        [
            {"type": "bs"},
            {"type": "bs", "df": 4},
            {"type": "bs", "df": 2, "degree": 1},
            {"type": "bs", "k": 8, "degree": 2, "boundary_knots": [0.0, 100.0]},
            {"type": "bs", "knots": [10.0, 50.0], "boundary_knots": [0.0, 100.0]},
            {"type": "ns", "df": 2},
            {"type": "ms", "df": 20, "monotonicity": "decreasing"},
            {"type": "linear", "monotonicity": "increasing"},
            {"type": "categorical", "reference": "north"},
            {"type": "categorical", "levels": ["1", "1.0"]},
            {"type": "target_encoding", "prior_weight": "auto", "n_permutations": 100},
        ],
    )
    def test_accepts_the_contract(self, spec):
        assert glm_model_columns({"age": spec}, []) == ["age"]

    def test_stored_keys_are_rustystats_keys_plus_reference(self):
        assert SUPPORTED_TERM_TYPES == frozenset(TERM_KEYS)
        assert TERM_KEYS["categorical"] == frozenset({"type", "levels", "reference"})


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
        assert glm_model_columns(terms, interactions) == ["age", "region", "income", "vehicle_age"]

    def test_empty_terms_and_no_interactions_resolve_to_nothing(self):
        assert glm_model_columns({}, None) == []

    def test_rejects_expression_keyed_by_a_column_it_reads(self):
        with pytest.raises(HauteValidationError, match="'age'.*keyed by a column it reads"):
            glm_model_columns({"age": {"type": "expression", "expr": "age ** 2"}}, [])
        with pytest.raises(HauteValidationError, match="'income'.*keyed by a column it reads"):
            glm_model_columns({"income": {"type": "expression", "expr": "age * income"}}, [])

    def test_rejects_malformed_entries(self):
        with pytest.raises(HauteValidationError, match="term names"):
            glm_model_columns({"": {"type": "linear"}}, [])
        with pytest.raises(HauteValidationError, match="'age' must be a mapping"):
            glm_model_columns({"age": "linear"}, [])
        with pytest.raises(HauteValidationError, match="'age_sq' has no 'expr'"):
            glm_model_columns({"age_sq": {"type": "expression"}}, [])
        with pytest.raises(HauteValidationError, match="terms must be a mapping"):
            glm_model_columns([], [])

    @pytest.mark.parametrize("encoding", ["target_encoding", "frequency_encoding"])
    def test_encoding_alias_reads_its_source(self, encoding):
        assert glm_model_columns(
            {"region_encoded": {"type": encoding, "variable": "region"}}, []
        ) == ["region"]

    @pytest.mark.parametrize("encoding", ["target_encoding", "frequency_encoding"])
    def test_rejects_duplicate_encoding_for_one_source(self, encoding):
        with pytest.raises(HauteValidationError, match="duplicates.*'region'.*already 'region'"):
            glm_model_columns(
                {
                    "region": {"type": encoding},
                    "region_encoded": {"type": encoding, "variable": "region"},
                },
                [],
            )

    @pytest.mark.parametrize(
        ("interaction", "message"),
        [
            ("not a card", "must be a mapping"),
            ({"factors": "ab"}, "factors must be a list of column names"),
            ({"factors": ["a", 1]}, "factors must be a list"),
            ({"factors": ["a", "a"]}, "names a factor more than once"),
            ({"factors": ["a", "b"], "include_main": "yes"}, "include_main must be true or false"),
            ({"factors": ["a", "b"], "encoding": "invalid"}, "unsupported encoding"),
            ({"factors": ["a", "b"], "specs": {"a": "linear"}}, "override must be a mapping"),
            ({"factors": ["a", "b"], "specs": {"c": {"type": "linear"}}}, r"not picked: \['c'\]"),
            (
                {"factors": ["a", "b"], "specs": {"a": {"type": "ms"}}},
                "not allowed inside an interaction",
            ),
            (
                {
                    "factors": ["a", "b"],
                    "specs": {"a": {"type": "linear", "monotonicity": "increasing"}},
                },
                "monotonicity is not applied inside interactions",
            ),
            (
                {"factors": ["a", "b"], "specs": {"a": {"type": "categorical", "levels": ["x"]}}},
                "levels is not applied",
            ),
            (
                {"factors": ["a", "b"], "specs": {"a": {"type": "bs", "df": 2}}},
                r"df must be an integer from degree \+ 1",
            ),
            (
                {
                    "factors": ["a", "b"],
                    "encoding": "frequency_encoding",
                    "specs": {"a": {"type": "linear"}},
                },
                "cannot carry per-factor 'specs'",
            ),
            ({"factors": ["a", "b"], "prior_weight": 2}, "prior_weight requires target_encoding"),
            (
                {"factors": ["a", "b"], "encoding": "target_encoding", "n_permutations": 0},
                "n_permutations must be an integer",
            ),
            ({"factors": ["include_main", "b"]}, "reserved RustyStats interaction factor names"),
            ({"factors": ["a", "b"], "levels": ["x"]}, "unknown interaction settings"),
        ],
    )
    def test_malformed_interaction_entries_are_validation_errors(self, interaction, message):
        with pytest.raises(HauteValidationError, match=message):
            glm_model_columns({"a": {"type": "linear"}}, [interaction])

    def test_interactions_must_be_a_list(self):
        with pytest.raises(HauteValidationError, match="must be a list of interaction cards"):
            glm_model_columns({}, {"factors": ["a", "b"]})


class TestValidateGlmModelColumns:
    SCHEMA = {
        "age": "Int64",
        "income": "Float64",
        "region": "String",
        "flag": "Boolean",
        "vehicle_age": "Int64",
        "start": "Date",
        "target": "Float64",
        "x_sq": "Float64",
    }

    def test_returns_resolved_columns_when_everything_is_present(self):
        terms = {"age": {"type": "linear"}, "age_sq": {"type": "expression", "expr": "age ** 2"}}
        assert validate_glm_model_columns(terms, [{"factors": ["age", "region"]}], self.SCHEMA) == [
            "age",
            "region",
        ]

    def test_rejects_unknown_columns_with_a_bounded_available_list(self):
        wide = {f"c{index}": "Float64" for index in range(30)}
        with pytest.raises(HauteValidationError) as excinfo:
            validate_glm_model_columns({"height": {"type": "linear"}}, [], wide)
        message = str(excinfo.value)
        assert "not found in training data: ['height']" in message
        assert "… 10 more of 30]" in message

    def test_role_columns_cannot_be_terms_or_factors(self):
        roles = {"target": "target", "income": "weight"}
        with pytest.raises(HauteValidationError, match=r"role columns: 'income' \(weight\)"):
            validate_glm_model_columns(
                {"age": {"type": "linear"}},
                [{"factors": ["age", "income"]}],
                self.SCHEMA,
                role_columns=roles,
            )
        with pytest.raises(HauteValidationError, match=r"'target' \(target\)"):
            validate_glm_model_columns(
                {"ratio": {"type": "expression", "expr": "age / target"}},
                [],
                self.SCHEMA,
                role_columns=roles,
            )

    @pytest.mark.parametrize(
        ("terms", "interactions", "message"),
        [
            (
                {"income": {"type": "categorical"}},
                [],
                "Categorical is not available for 'income', a continuous column",
            ),
            (
                {"income": {"type": "target_encoding"}},
                [],
                "Target encoding is not available for 'income'",
            ),
            (
                {"region": {"type": "linear"}},
                [],
                "Linear is not available for 'region', a categorical column",
            ),
            (
                {"flag": {"type": "bs"}},
                [],
                "B-spline is not available for 'flag', a boolean column",
            ),
            (
                {"start": {"type": "linear"}},
                [],
                "'start' has dtype Date, which GLM fits do not support",
            ),
            (
                {"r": {"type": "expression", "expr": "region * 2"}},
                [],
                "reads 'region', a categorical column",
            ),
            (
                {"age": {"type": "linear"}},
                [{"factors": ["age", "start"]}],
                "'start' has dtype Date",
            ),
            (
                {"age": {"type": "linear"}},
                [{"factors": ["age", "income"], "encoding": "target_encoding"}],
                "joint encodings need integer, boolean, or categorical factors; 'income'",
            ),
            (
                {"age": {"type": "linear"}},
                [{"factors": ["age", "region"], "specs": {"region": {"type": "linear"}}}],
                "Linear is not available for a categorical column",
            ),
        ],
    )
    def test_fits_outside_a_dtype_class_are_refused(self, terms, interactions, message):
        with pytest.raises(HauteValidationError, match=message):
            validate_glm_model_columns(terms, interactions, self.SCHEMA)

    def test_dtype_classes_drive_defaults_and_allowed_fits(self):
        native = {"linear", "bs", "ns", "ms"}
        encodings = {"categorical", "target_encoding", "frequency_encoding"}
        assert MAIN_FITS_BY_CLASS == {
            "continuous": native,
            "integer": native | encodings,
            "boolean": encodings,
            "categorical": encodings,
            "unsupported": set(),
        }
        # Interactions honour neither monotone splines nor frequency encoding.
        assert SLOT_FITS_BY_CLASS == {
            dtype_class: fits - {"ms", "frequency_encoding"}
            for dtype_class, fits in MAIN_FITS_BY_CLASS.items()
        }
        assert DEFAULT_FIT_BY_CLASS == {
            "continuous": "linear",
            "integer": "linear",
            "boolean": "categorical",
            "categorical": "categorical",
        }
        for column, fits in (("income", native), ("age", native | encodings), ("flag", encodings)):
            for fit in fits:
                spec = {"type": fit, "df": 4} if fit in {"bs", "ns", "ms"} else {"type": fit}
                assert validate_glm_model_columns({column: spec}, [], self.SCHEMA) == [column]

    @pytest.mark.parametrize("dtype", ["Date", "List(Int64)", "Struct({'a': Int64})", "Null"])
    def test_unsupported_dtype_terms_are_refused(self, dtype):
        schema = {**self.SCHEMA, "when": dtype}
        refusal = f"'when' has dtype {dtype}, which GLM fits do not support"
        for terms, interactions in (
            ({"when": {"type": "categorical"}}, []),
            ({"ratio": {"type": "expression", "expr": "age / when"}}, []),
            ({"when_fe": {"type": "frequency_encoding", "variable": "when"}}, []),
            ({"age": {"type": "linear"}}, [{"factors": ["age", "when"]}]),
            (
                {"age": {"type": "linear"}},
                [{"factors": ["region", "when"], "encoding": "target_encoding"}],
            ),
        ):
            with pytest.raises(HauteValidationError) as excinfo:
                validate_glm_model_columns(terms, interactions, schema)
            assert refusal in str(excinfo.value), (terms, interactions)

    def test_integer_column_can_be_categorical_and_target_encoded(self):
        terms = {
            "age": {"type": "categorical"},
            "age_te": {"type": "target_encoding", "variable": "age"},
            "flag": {"type": "frequency_encoding"},
        }
        interactions = [
            {"factors": ["vehicle_age", "region"], "encoding": "target_encoding"},
            {
                "factors": ["vehicle_age", "income"],
                "specs": {"vehicle_age": {"type": "target_encoding"}},
            },
        ]
        assert validate_glm_model_columns(terms, interactions, self.SCHEMA) == [
            "age",
            "flag",
            "vehicle_age",
            "region",
            "income",
        ]

    def test_rejects_expression_or_alias_keyed_by_a_schema_column(self):
        with pytest.raises(HauteValidationError, match="'x_sq' names a column"):
            validate_glm_model_columns(
                {"x_sq": {"type": "expression", "expr": "age ** 2"}}, [], self.SCHEMA
            )
        with pytest.raises(HauteValidationError, match="'age' names a column"):
            validate_glm_model_columns(
                {"age": {"type": "target_encoding", "variable": "region"}}, [], self.SCHEMA
            )


class TestDesignSummaries:
    def test_penalised_smooth_terms_include_main_terms_and_slot_overrides(self):
        terms = {
            "age": {"type": "bs"},
            "income": {"type": "ns", "k": 6},
            "fixed": {"type": "bs", "df": 5},
            "knotted": {"type": "ms", "knots": [1.0]},
        }
        interactions = [
            {"factors": ["age", "region"], "specs": {"age": {"type": "ns"}}},
            {"factors": ["income", "region"], "specs": {"income": {"type": "bs", "df": 4}}},
        ]
        assert penalised_smooth_terms(terms, interactions) == [
            "'age'",
            "'income'",
            "Interaction 1 factor 'age'",
        ]

    def test_monotone_constraint_terms_cover_linear_expression_and_bs(self):
        terms = {
            "a": {"type": "linear", "monotonicity": "increasing"},
            "b": {"type": "expression", "expr": "a ** 2", "monotonicity": "decreasing"},
            "c": {"type": "bs", "df": 5, "monotonicity": "increasing"},
            "d": {"type": "ms"},
            "e": {"type": "linear"},
        }
        assert monotone_constraint_terms(terms) == ["a", "b", "c"]


class TestCategoricalLevels:
    def test_reference_level_translates_to_levels_and_refuses_unobserved_labels(self):
        resolved = resolve_categorical_levels(
            {"region": {"type": "categorical", "reference": "b"}, "x": {"type": "linear"}},
            {"region": ["a", "b", "c"]},
        )
        assert resolved == {
            "region": {"type": "categorical", "levels": ["a", "c"]},
            "x": {"type": "linear"},
        }
        with pytest.raises(
            HauteValidationError,
            match=(
                r"reference level '2' is not in the training data\. "
                r"Observed levels: \['2\.0', '3\.0'\]"
            ),
        ):
            resolve_categorical_levels(
                {"g": {"type": "categorical", "reference": "2"}}, {"g": ["2.0", "3.0"]}
            )
        with pytest.raises(
            HauteValidationError, match=r"levels \['z'\] are not in the training data"
        ):
            resolve_categorical_levels(
                {"g": {"type": "categorical", "levels": ["a", "z"]}}, {"g": ["a"]}
            )
        with pytest.raises(HauteValidationError, match="only its reference level"):
            resolve_categorical_levels(
                {"g": {"type": "categorical", "reference": "a"}}, {"g": ["a"]}
            )


_CLASSES = {
    "x": "continuous",
    "z": "continuous",
    "w": "continuous",
    "grp": "integer",
    "c": "categorical",
    "d": "categorical",
    "region": "categorical",
    "flag": "boolean",
}


class TestResolveGlmDesign:
    def test_materialises_missing_main_effects_once_and_never_duplicates(self):
        interactions, terms = resolve_glm_design(
            {"x": {"type": "linear"}},
            [
                {"factors": ["x", "z"], "include_main": True},
                {"factors": ["z", "w"], "include_main": True},
            ],
            _CLASSES,
        )
        assert terms == {"x": {"type": "linear"}, "z": {"type": "linear"}, "w": {"type": "linear"}}
        assert all(interaction["include_main"] is False for interaction in interactions)

    def test_dtype_defaults_follow_the_column_class(self):
        interactions, terms = resolve_glm_design(
            {"x": {"type": "linear"}},
            [
                {"factors": ["flag", "x"], "include_main": True},
                {"factors": ["grp", "x"], "include_main": True},
            ],
            _CLASSES,
        )
        assert interactions[0]["flag"] == {"type": "categorical"}
        assert terms["flag"] == {"type": "categorical"}
        assert interactions[1]["grp"] == {"type": "linear"}

    def test_materialised_main_effect_is_independent_of_card_order(self):
        base = {"c": {"type": "categorical"}, "d": {"type": "categorical"}}
        spline_card = {
            "factors": ["x", "c"],
            "specs": {"x": {"type": "bs", "df": 4}},
            "include_main": True,
        }
        linear_card = {"factors": ["x", "d"], "include_main": True}
        for order in ([spline_card, linear_card], [linear_card, spline_card]):
            with pytest.raises(
                HauteValidationError,
                match=r"Interactions 1, 2 each add a main effect for 'x' with different fits",
            ):
                resolve_glm_design(base, order, _CLASSES)

    def test_agreeing_cards_materialise_one_main_effect(self):
        card = {
            "factors": ["x", "c"],
            "specs": {"x": {"type": "bs", "df": 4}},
            "include_main": True,
        }
        other = {
            "factors": ["x", "d"],
            "specs": {"x": {"type": "bs", "df": 4}},
            "include_main": True,
        }
        _interactions, terms = resolve_glm_design(
            {"c": {"type": "categorical"}, "d": {"type": "categorical"}}, [card, other], _CLASSES
        )
        assert terms["x"] == {"type": "bs", "df": 4}

    def test_different_local_splines_for_one_column_across_cards_are_allowed(self):
        interactions, terms = resolve_glm_design(
            {"x": {"type": "linear"}, "c": {"type": "categorical"}, "z": {"type": "linear"}},
            [
                {
                    "factors": ["x", "c"],
                    "specs": {"x": {"type": "bs", "df": 4}},
                    "include_main": True,
                },
                {
                    "factors": ["x", "z"],
                    "specs": {"x": {"type": "ns", "df": 3}},
                    "include_main": True,
                },
            ],
            _CLASSES,
        )
        assert interactions[0]["x"] == {"type": "bs", "df": 4}
        assert interactions[1]["x"] == {"type": "ns", "df": 3}
        assert terms["x"] == {"type": "linear"}

    @pytest.mark.parametrize(
        ("main", "reason"),
        [
            ({"type": "ms"}, "monotone spline main effect"),
            (
                {"type": "linear", "monotonicity": "increasing"},
                "main effect's monotonicity is not applied",
            ),
            (
                {"type": "bs", "df": 5, "monotonicity": "decreasing"},
                "main effect's monotonicity is not applied",
            ),
            ({"type": "frequency_encoding"}, "frequency-encoded main effect"),
            (
                {"type": "categorical", "levels": ["a"]},
                "main effect's levels or reference level are not applied",
            ),
            (
                {"type": "categorical", "reference": "a"},
                "main effect's levels or reference level are not applied",
            ),
        ],
    )
    def test_inherited_levels_or_monotonicity_require_an_explicit_slot_fit(self, main, reason):
        column = "region" if main["type"] in {"frequency_encoding", "categorical"} else "x"
        with pytest.raises(
            HauteValidationError, match=f"factor '{column}': its {reason}.*choose a fit"
        ):
            resolve_glm_design(
                {column: main, "z": {"type": "linear"}}, [{"factors": [column, "z"]}], _CLASSES
            )

    def test_several_main_effects_require_an_explicit_slot_fit(self):
        terms = {
            "region": {"type": "categorical"},
            "region_te": {"type": "target_encoding", "variable": "region"},
        }
        with pytest.raises(HauteValidationError, match="several main-effect terms"):
            resolve_glm_design(terms, [{"factors": ["region", "x"]}], _CLASSES)

    def test_named_target_encoding_counts_as_a_main_effect(self):
        terms = {
            "region_te": {"type": "target_encoding", "variable": "region", "n_permutations": 6},
            "x": {"type": "linear"},
        }
        interactions, effective = resolve_glm_design(
            terms, [{"factors": ["region", "x"], "include_main": True}], _CLASSES
        )
        assert interactions[0]["region"] == {"type": "target_encoding"}
        assert effective == terms

    def test_target_encoding_slot_over_categorical_main_is_refused(self):
        with pytest.raises(HauteValidationError, match="collinear with its indicators"):
            resolve_glm_design(
                {"region": {"type": "categorical"}, "x": {"type": "linear"}},
                [{"factors": ["region", "x"], "specs": {"region": {"type": "target_encoding"}}}],
                _CLASSES,
            )

    def test_categorical_slot_over_a_materialised_numeric_main_is_refused_in_any_order(self):
        first = {"factors": ["grp", "c"], "include_main": True}
        second = {
            "factors": ["grp", "d"],
            "specs": {"grp": {"type": "categorical"}},
            "include_main": False,
        }
        terms = {"c": {"type": "categorical"}, "d": {"type": "categorical"}}
        for order in ([first, second], [second, first]):
            with pytest.raises(
                HauteValidationError, match="re-type the column's Linear main effect"
            ):
                resolve_glm_design(terms, order, _CLASSES)

    def test_product_target_encoding_registers_one_main_effect_with_merged_settings(self):
        cards = [
            {
                "factors": ["grp", "x"],
                "specs": {"grp": {"type": "target_encoding", "prior_weight": 2}},
            },
            {
                "factors": ["grp", "z"],
                "specs": {"grp": {"type": "target_encoding", "n_permutations": 6}},
            },
        ]
        for order in (cards, cards[::-1]):
            interactions, terms = resolve_glm_design(
                {"x": {"type": "linear"}, "z": {"type": "linear"}}, order, _CLASSES
            )
            assert terms["grp"] == {
                "type": "target_encoding",
                "prior_weight": 2,
                "n_permutations": 6,
            }
            assert {"type": "target_encoding", "prior_weight": 2} in [
                card["grp"] for card in interactions
            ]

    def test_product_target_encoding_conflicts_are_refused_and_existing_aliases_reused(self):
        with pytest.raises(
            HauteValidationError, match="prior_weight 3 conflicts with Interaction 1"
        ):
            resolve_glm_design(
                {},
                [
                    {
                        "factors": ["region", "x"],
                        "specs": {"region": {"type": "target_encoding", "prior_weight": 2}},
                    },
                    {
                        "factors": ["region", "z"],
                        "specs": {"region": {"type": "target_encoding", "prior_weight": 3}},
                    },
                ],
                _CLASSES,
            )
        _interactions, terms = resolve_glm_design(
            {"region": {"type": "frequency_encoding"}},
            [{"factors": ["region", "x"], "specs": {"region": {"type": "target_encoding"}}}],
            _CLASSES,
            reserved_names={"region_te"},
        )
        assert terms["region_te_2"] == {"type": "target_encoding", "variable": "region"}

    def test_product_target_encoding_needs_linear_partners(self):
        with pytest.raises(HauteValidationError, match="exactly one target-encoded factor"):
            resolve_glm_design(
                {"c": {"type": "categorical"}},
                [{"factors": ["region", "c"], "specs": {"region": {"type": "target_encoding"}}}],
                _CLASSES,
            )

    def test_joint_encodings_use_linear_placeholders_and_class_defaults(self):
        interactions, terms = resolve_glm_design(
            {"x": {"type": "linear"}},
            [
                {
                    "factors": ["grp", "flag"],
                    "encoding": "target_encoding",
                    "prior_weight": 2,
                    "include_main": True,
                }
            ],
            _CLASSES,
        )
        assert interactions == [
            {
                "grp": {"type": "linear"},
                "flag": {"type": "linear"},
                "target_encoding": True,
                "include_main": False,
                "prior_weight": 2,
            }
        ]
        assert terms == {
            "x": {"type": "linear"},
            "grp": {"type": "linear"},
            "flag": {"type": "categorical"},
        }

    def test_duplicate_cards_per_encoding_mode_are_refused_but_modes_can_coexist(self):
        with pytest.raises(HauteValidationError, match="Interaction 2 duplicates Interaction 1"):
            resolve_glm_design({}, [{"factors": ["c", "d"]}, {"factors": ["d", "c"]}], _CLASSES)
        interactions, _terms = resolve_glm_design(
            {},
            [{"factors": ["c", "d"]}, {"factors": ["d", "c"], "encoding": "frequency_encoding"}],
            _CLASSES,
        )
        assert len(interactions) == 2

    def test_partial_cards_are_skipped(self):
        assert resolve_glm_design(
            {"x": {"type": "linear"}}, [{"factors": ["x", ""]}], _CLASSES
        ) == (
            [],
            {"x": {"type": "linear"}},
        )

    def test_unsupported_and_unknown_factors_are_refused(self):
        with pytest.raises(HauteValidationError, match="'when' is not in the training data"):
            resolve_glm_design({}, [{"factors": ["x", "when"]}], _CLASSES)
        with pytest.raises(HauteValidationError, match="dtype GLM fits do not support"):
            resolve_glm_design(
                {}, [{"factors": ["x", "when"]}], {**_CLASSES, "when": "unsupported"}
            )
