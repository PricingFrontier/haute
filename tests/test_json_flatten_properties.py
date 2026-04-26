"""Property-based tests for JSON flattening invariants."""

from __future__ import annotations

import json
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._json_flatten import (
    _arrow_schema_from_flatten,
    _infer_schema_streaming,
    _schema_leaf_types,
    flatten,
    infer_schema,
    schema_columns,
)

_JSON_KEY = st.builds(
    str.__add__,
    st.sampled_from(tuple(string.ascii_lowercase + "_")),
    st.text(
        alphabet=string.ascii_lowercase + string.digits + "_",
        max_size=9,
    ),
)
_JSON_SCALAR = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-1000, max_value=1000)
    | st.floats(
        allow_nan=False,
        allow_infinity=False,
        min_value=-1000,
        max_value=1000,
    )
    | st.text(
        alphabet=string.ascii_letters + string.digits + " _-",
        max_size=12,
    )
)
_JSON_VALUE = st.recursive(
    _JSON_SCALAR,
    lambda children: (
        st.dictionaries(_JSON_KEY, children, max_size=3) | st.lists(children, max_size=3)
    ),
    max_leaves=20,
)
_JSON_OBJECT = st.dictionaries(_JSON_KEY, _JSON_VALUE, max_size=4)


class TestJsonFlattenProperties:
    @given(samples=st.lists(_JSON_OBJECT, min_size=1, max_size=6))
    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_infer_schema_is_order_invariant(self, samples: list[dict[str, object]]) -> None:
        assert infer_schema(samples) == infer_schema(list(reversed(samples)))

    @given(samples=st.lists(_JSON_OBJECT, min_size=1, max_size=5))
    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_flattened_keys_match_schema_columns_for_all_samples(
        self,
        samples: list[dict[str, object]],
    ) -> None:
        schema = infer_schema(samples)
        expected_columns = schema_columns(schema)

        assert list(flatten(None, schema).keys()) == expected_columns
        for sample in samples:
            assert list(flatten(sample, schema).keys()) == expected_columns

    @given(samples=st.lists(_JSON_OBJECT, min_size=1, max_size=5))
    @settings(
        max_examples=60,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_inferred_columns_are_unique_and_lossless_for_supported_key_space(
        self,
        samples: list[dict[str, object]],
    ) -> None:
        schema = infer_schema(samples)
        expected_columns = schema_columns(schema)

        assert len(expected_columns) == len(set(expected_columns))
        for sample in [None, *samples]:
            assert len(flatten(sample, schema)) == len(expected_columns)

    @given(samples=st.lists(_JSON_OBJECT, min_size=1, max_size=5))
    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_leaf_and_arrow_contracts_follow_schema_columns(
        self,
        samples: list[dict[str, object]],
    ) -> None:
        schema = infer_schema(samples)
        expected_columns = schema_columns(schema)

        assert [name for name, _ in _schema_leaf_types(schema)] == expected_columns
        assert _arrow_schema_from_flatten(schema).names == expected_columns

    @pytest.mark.parametrize("suffix", [".json", ".jsonl"])
    @given(samples=st.lists(_JSON_OBJECT, min_size=1, max_size=8))
    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    def test_streaming_schema_inference_matches_batch_inference(
        self,
        samples: list[dict[str, object]],
        suffix: str,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        path = tmp_path_factory.mktemp("json_flatten_schema") / f"sample{suffix}"
        if suffix == ".jsonl":
            path.write_text(
                "\n".join(json.dumps(sample) for sample in samples) + "\n",
                encoding="utf-8",
            )
        else:
            path.write_text(json.dumps(samples), encoding="utf-8")

        assert _infer_schema_streaming(path, max_samples=len(samples)) == infer_schema(samples)
