"""Fail-loud codegen contract tests."""

from __future__ import annotations

import pytest

from haute._codegen_builders import (
    _gen_submodel_placeholder_unreachable,
    _portable_path_expr,
)
from haute.codegen import (
    _error_on_name_collisions,
    _format_contract_kwarg,
    _inject_contract_kwarg,
    graph_to_code,
    graph_to_code_multi,
)
from haute.errors import HauteError, ParseError
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_graph as _g
from tests.conftest import make_node as _n


def test_inject_contract_kwarg_handles_multiline_decorator_arguments() -> None:
    code = """@pipeline.banding(
    factors=[{"column": "x", "output_column": "band"}]
)
def Step(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

    injected = _inject_contract_kwarg(code, 'contract={"inputs": ["x"], "outputs": ["band"]}')

    assert 'contract={"inputs": ["x"], "outputs": ["band"]}' in injected
    _compile_node_code(injected)


def test_inject_contract_kwarg_rewrites_bare_decorator() -> None:
    code = """@pipeline.polars
def Step(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected.splitlines()[0] == '@pipeline.polars(contract="opaque")'
    _compile_node_code(injected)


def test_format_contract_kwarg_preserves_inputs_by_parent() -> None:
    node = _n(
        {
            "id": "join",
            "data": {
                "label": "join",
                "nodeType": "polars",
                "config": {
                    "contract": {
                        "inputs": ["key", "left_value", "right_value"],
                        "outputs": [],
                        "inputs_by_parent": {
                            "left": ["key", "left_value"],
                            "right": ["key", "right_value"],
                        },
                    }
                },
            },
        }
    )

    contract_kwarg = _format_contract_kwarg(node)

    assert contract_kwarg is not None
    assert "inputs_by_parent" in contract_kwarg
    assert "'left': ['key', 'left_value']" in contract_kwarg
    assert "'right': ['key', 'right_value']" in contract_kwarg


def test_format_contract_kwarg_preserves_declared_non_polars_contract() -> None:
    node = _n(
        {
            "id": "scenario",
            "data": {
                "label": "scenario_expander",
                "nodeType": "scenarioExpander",
                "config": {
                    "contract": {
                        "inputs": ["quote_id", "premium"],
                        "outputs": ["quote_id", "premium", "scenario_index"],
                    }
                },
            },
        }
    )

    contract_kwarg = _format_contract_kwarg(node)

    assert contract_kwarg is not None
    assert "opaque" not in contract_kwarg
    assert "'inputs': ['premium', 'quote_id']" in contract_kwarg
    assert "'outputs': ['premium', 'quote_id', 'scenario_index']" in contract_kwarg


def test_graph_to_code_remaps_inputs_by_parent_ids_to_function_names() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "left-uuid",
                    "data": {
                        "label": "Left Input",
                        "nodeType": "dataSource",
                        "config": {"path": "left.parquet"},
                    },
                },
                {
                    "id": "right-uuid",
                    "data": {
                        "label": "Right Input",
                        "nodeType": "dataSource",
                        "config": {"path": "right.parquet"},
                    },
                },
                {
                    "id": "join-uuid",
                    "data": {
                        "label": "Join Node",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left_input.join(right_input, on='key')",
                            "contract": {
                                "inputs": ["key", "left_value", "right_value"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "left-uuid": ["key", "left_value"],
                                    "right-uuid": ["key", "right_value"],
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e-left-join", "source": "left-uuid", "target": "join-uuid"},
                {"id": "e-right-join", "source": "right-uuid", "target": "join-uuid"},
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="p")

    assert "'Left_Input': ['key', 'left_value']" in code
    assert "'Right_Input': ['key', 'right_value']" in code
    assert "left-uuid" not in code
    assert "right-uuid" not in code


def test_graph_to_code_preserves_instance_contract() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "premium",
                        "nodeType": "dataSource",
                        "config": {"path": "premium.parquet"},
                    },
                },
                {
                    "id": "feature-original",
                    "data": {
                        "label": "competitor_features",
                        "nodeType": "polars",
                        "config": {
                            "code": (
                                "df = premium.with_columns(difference_to_market=pl.col('premium'))"
                            ),
                            "contract": {
                                "inputs": ["premium"],
                                "outputs": ["difference_to_market"],
                            },
                        },
                    },
                },
                {
                    "id": "feature-instance",
                    "data": {
                        "label": "competitor_features_scenarios",
                        "nodeType": "polars",
                        "config": {
                            "instanceOf": "feature-original",
                            "contract": {
                                "inputs": ["premium", "competitor_premium"],
                                "outputs": ["difference_to_market"],
                            },
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e-src-original", "source": "src", "target": "feature-original"},
                {"id": "e-src-instance", "source": "src", "target": "feature-instance"},
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="p")

    assert '@pipeline.instance(of="competitor_features", contract=' in code
    assert "'inputs': ['competitor_premium', 'premium']" in code
    assert "'outputs': ['difference_to_market']" in code


def test_inject_contract_kwarg_raises_when_no_pipeline_decorator_exists() -> None:
    code = """def Step(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

    with pytest.raises(HauteError, match="no @pipeline"):
        _inject_contract_kwarg(code, 'contract="opaque"')


def test_error_on_name_collisions_raises_for_root_and_submodel_labels() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "root",
                    "data": {
                        "label": "My Node",
                        "nodeType": "polars",
                        "config": {"code": ""},
                    },
                }
            ],
            "edges": [],
            "submodels": {
                "pricing": {
                    "file": "modules/pricing.py",
                    "childNodeIds": ["child"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "child",
                                "data": {
                                    "label": "My-Node",
                                    "nodeType": "polars",
                                    "config": {"code": ""},
                                },
                            }
                        ],
                        "edges": [],
                    },
                }
            },
        }
    )

    with pytest.raises(ParseError, match="sanitize to the same Python function name"):
        graph_to_code_multi(graph, pipeline_name="main")


def test_submodel_placeholder_codegen_is_unreachable() -> None:
    node = _n(
        {
            "id": "submodel__pricing",
            "data": {
                "label": "pricing",
                "nodeType": "submodel",
                "config": {},
            },
        }
    )

    with pytest.raises(RuntimeError, match="submodel placeholder node"):
        _gen_submodel_placeholder_unreachable(node, [])


def test_portable_path_expr_normalizes_windows_paths() -> None:
    assert _portable_path_expr(r"C:\models\score.cbm") == '"C:/models/score.cbm"'
    assert (
        _portable_path_expr(r"nested\data.parquet")
        == 'Path(__file__).parent / "nested/data.parquet"'
    )


def test_error_on_name_collisions_reports_all_buckets() -> None:
    with pytest.raises(ParseError) as exc_info:
        _error_on_name_collisions(["Rate Step", "Rate-Step", "Quoted Name", "Quoted-Name"])

    rendered = str(exc_info.value)
    assert "Rate_Step" in rendered
    assert "Quoted_Name" in rendered
