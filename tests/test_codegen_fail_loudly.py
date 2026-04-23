"""Fail-loud codegen contract tests."""

from __future__ import annotations

import pytest

from haute._codegen_builders import (
    _gen_submodel_placeholder_unreachable,
    _portable_path_expr,
)
from haute.codegen import (
    _error_on_name_collisions,
    _inject_contract_kwarg,
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
    assert _portable_path_expr(
        r"nested\data.parquet"
    ) == 'Path(__file__).parent / "nested/data.parquet"'


def test_error_on_name_collisions_reports_all_buckets() -> None:
    with pytest.raises(ParseError) as exc_info:
        _error_on_name_collisions(["Rate Step", "Rate-Step", "Quoted Name", "Quoted-Name"])

    rendered = str(exc_info.value)
    assert "Rate_Step" in rendered
    assert "Quoted_Name" in rendered
