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
from haute.errors import ConfigError, HauteError, ParseError
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


def test_graph_to_code_repairs_single_parent_stale_inputs_by_parent_key() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "current-parent",
                    "data": {
                        "label": "Current Parent",
                        "nodeType": "dataSource",
                        "config": {"path": "current.parquet"},
                    },
                },
                {
                    "id": "consumer",
                    "data": {
                        "label": "Consumer",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = Current_Parent.with_columns(pl.col('price'))",
                            "contract": {
                                "inputs": ["price"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "old_parent": ["price"],
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e-current-consumer", "source": "current-parent", "target": "consumer"},
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="p")

    assert "'Current_Parent': ['price']" in code
    assert "old_parent" not in code


def test_graph_to_code_drops_ambiguous_stale_inputs_by_parent_metadata() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "left-parent",
                    "data": {
                        "label": "Left Parent",
                        "nodeType": "dataSource",
                        "config": {"path": "left.parquet"},
                    },
                },
                {
                    "id": "right-parent",
                    "data": {
                        "label": "Right Parent",
                        "nodeType": "dataSource",
                        "config": {"path": "right.parquet"},
                    },
                },
                {
                    "id": "consumer",
                    "data": {
                        "label": "Consumer",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left_parent.join(right_parent, on='id')",
                            "contract": {
                                "inputs": ["id", "left_price", "right_price"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "old_parent": ["id", "left_price"],
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e-left-consumer", "source": "left-parent", "target": "consumer"},
                {"id": "e-right-consumer", "source": "right-parent", "target": "consumer"},
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="p")

    assert "def Consumer(Left_Parent: pl.LazyFrame, Right_Parent: pl.LazyFrame)" in code
    assert "'inputs': ['id', 'left_price', 'right_price']" in code
    assert "'outputs': []" in code
    assert "inputs_by_parent" not in code
    assert "old_parent" not in code


def test_graph_to_code_drops_multiple_stale_keys_for_single_current_parent() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "current-parent",
                    "data": {
                        "label": "Current Parent",
                        "nodeType": "dataSource",
                        "config": {"path": "current.parquet"},
                    },
                },
                {
                    "id": "consumer",
                    "data": {
                        "label": "Consumer",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = Current_Parent.with_columns(pl.col('price'))",
                            "contract": {
                                "inputs": ["price", "discount"],
                                "outputs": [],
                                "inputs_by_parent": {
                                    "old_parent_a": ["price"],
                                    "old_parent_b": ["discount"],
                                },
                            },
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e-current-consumer", "source": "current-parent", "target": "consumer"},
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="p")

    assert "def Consumer(Current_Parent: pl.LazyFrame)" in code
    assert "'inputs': ['discount', 'price']" in code
    assert "'outputs': []" in code
    assert "inputs_by_parent" not in code
    assert "old_parent_a" not in code
    assert "old_parent_b" not in code


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


# ---------------------------------------------------------------------------
# Remediation 5.4: the paren scanner must be string-aware (unit level).
# ---------------------------------------------------------------------------


def test_inject_contract_kwarg_ignores_close_paren_inside_string() -> None:
    """A ``)`` inside a string kwarg must not be taken as the decorator close."""
    code = (
        "@pipeline.polars(selected_columns=[':)'])\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    expected = "@pipeline.polars(selected_columns=[':)'], contract=\"opaque\")"
    assert injected.splitlines()[0] == expected
    _compile_node_code(injected)


def test_inject_contract_kwarg_ignores_open_paren_inside_string() -> None:
    """A lone ``(`` inside a string must not push the scan past the real close."""
    code = (
        "@pipeline.polars(selected_columns=['col('])\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    expected = "@pipeline.polars(selected_columns=['col('], contract=\"opaque\")"
    assert injected.splitlines()[0] == expected
    _compile_node_code(injected)


def test_inject_contract_kwarg_paren_in_string_multiline_decorator() -> None:
    """Multi-line decorator args with paren-bearing strings keep working."""
    code = (
        "@pipeline.banding(\n"
        '    factors=[{"column": "size (mm)", "output_column": "x ) y"}]\n'
        ")\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert 'contract="opaque"' in injected
    _compile_node_code(injected)
    # The string values must be untouched.
    assert '"size (mm)"' in injected
    assert '"x ) y"' in injected


def test_inject_contract_kwarg_does_not_read_past_the_decorator_close() -> None:
    """Token consumption is lazy: garbage in the BODY (caught later by the
    emission parse gate) must not break injection into a healthy decorator."""
    code = (
        "@pipeline.polars(selected_columns=['x'])\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        '    df = df.filter("unterminated\n'
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    expected = "@pipeline.polars(selected_columns=['x'], contract=\"opaque\")"
    assert injected.splitlines()[0] == expected


# ---------------------------------------------------------------------------
# Final-emission parse gate: codegen must never hand back unparseable files.
# ---------------------------------------------------------------------------


def test_graph_to_code_refuses_to_emit_unparseable_file() -> None:
    """Invalid user code in a node body must fail the save loudly (the save
    route maps ConfigError to HTTP 400 and rolls back) instead of silently
    writing a corrupt ``.py`` the parser can never load again."""
    graph = _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Src",
                        "nodeType": "dataSource",
                        "config": {"path": "d.parquet"},
                    },
                },
                {
                    "id": "t",
                    "data": {
                        "label": "Broken",
                        "nodeType": "polars",
                        "config": {"code": "df = df.filter("},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "t"}],
        }
    )

    with pytest.raises(ConfigError) as excinfo:
        graph_to_code(graph, pipeline_name="main")

    message = str(excinfo.value)
    assert "main.py" in message
    assert "not valid Python" in message


def test_graph_to_code_multi_refuses_unparseable_submodel_file() -> None:
    """The gate covers every emitted file, including submodel modules."""
    graph = _g(
        {
            "nodes": [],
            "edges": [],
            "submodels": {
                "sm": {
                    "file": "modules/sm.py",
                    "childNodeIds": ["src", "t"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "src",
                                "data": {
                                    "label": "Src",
                                    "nodeType": "dataSource",
                                    "config": {"path": "d.parquet"},
                                },
                            },
                            {
                                "id": "t",
                                "data": {
                                    "label": "Broken",
                                    "nodeType": "polars",
                                    "config": {"code": "df = ((("},
                                },
                            },
                        ],
                        "edges": [{"id": "e", "source": "src", "target": "t"}],
                    },
                },
            },
        }
    )

    with pytest.raises(ConfigError) as excinfo:
        graph_to_code_multi(graph, pipeline_name="main")

    assert "modules/sm.py" in str(excinfo.value)


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
