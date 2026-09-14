"""Fail-loud codegen contract tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from haute._codegen_builders import _gen_submodel_placeholder_unreachable
from haute._config_io import collect_node_configs
from haute._mlflow_io import ScoringModel
from haute.codegen import (
    _error_on_name_collisions,
    _format_contract_kwarg,
    _inject_contract_kwarg,
    graph_to_code,
    graph_to_code_multi,
)
from haute.errors import HauteError, ParseError
from haute.parser import parse_pipeline_source
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_edge, make_file_input_config
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


def test_inject_contract_kwarg_preserves_multiline_comments_and_trailing_style() -> None:
    code = (
        "@pipeline.banding(\n"
        "    value=1,  # keep this authored explanation\n"
        ")\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected == (
        "@pipeline.banding(\n"
        "    value=1,  # keep this authored explanation\n"
        '    contract="opaque",\n'
        ")\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )


def test_inject_contract_kwarg_preserves_crlf_newlines() -> None:
    code = (
        "@pipeline.polars(\r\n"
        "    selected_columns=['x'],\r\n"
        ")\r\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\r\n"
        "    return df\r\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected == (
        "@pipeline.polars(\r\n"
        "    selected_columns=['x'],\r\n"
        '    contract="opaque",\r\n'
        ")\r\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\r\n"
        "    return df\r\n"
    )


def test_inject_contract_kwarg_preserves_single_line_trailing_comma_style() -> None:
    code = (
        "@pipeline.polars(selected_columns=['x'],)\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected.startswith("@pipeline.polars(selected_columns=['x'], contract=\"opaque\",)\n")


def test_inject_contract_kwarg_handles_same_line_close_after_multiline_open() -> None:
    code = (
        "@pipeline.polars(\n"
        "    selected_columns=['x'],)\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected.startswith(
        "@pipeline.polars(\n    selected_columns=['x'], contract=\"opaque\",)\n"
    )


def test_inject_contract_kwarg_ignores_decorator_text_inside_a_string() -> None:
    code = (
        'banner = """\n'
        "@pipeline.not_a_real_decorator()\n"
        '"""\n'
        "@pipeline.polars()\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert "@pipeline.not_a_real_decorator()" in injected
    assert '@pipeline.polars(contract="opaque")' in injected


def test_inject_contract_kwarg_targets_first_function_decorator_in_source_order() -> None:
    code = (
        "@pipeline.class_lookalike()\n"
        "class Ignored:\n"
        "    pass\n"
        "\n"
        "@pipeline.outer()\n"
        "def outer() -> None:\n"
        "    @pipeline.inner()\n"
        "    def inner() -> None:\n"
        "        pass\n"
    )

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert "@pipeline.class_lookalike()" in injected
    assert '@pipeline.outer(contract="opaque")' in injected
    assert "@pipeline.inner()" in injected


def test_inject_contract_kwarg_rejects_an_existing_contract() -> None:
    code = (
        '@pipeline.polars(contract="opaque")\n'
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    return df\n"
    )

    with pytest.raises(HauteError) as exc_info:
        _inject_contract_kwarg(code, 'contract="opaque"')

    assert exc_info.value.context["reason"] == "decorator_keyword_exists"


def test_inject_contract_kwarg_rewrites_bare_decorator() -> None:
    code = """@pipeline.polars
def Step(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""

    injected = _inject_contract_kwarg(code, 'contract="opaque"')

    assert injected.splitlines()[0] == '@pipeline.polars(contract="opaque")'
    _compile_node_code(injected)


def test_config_backed_node_without_decorator_mapping_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.codegen as codegen

    node = _n(
        {
            "id": "configured",
            "data": {"label": "Configured", "nodeType": "banding", "config": {}},
        }
    )
    monkeypatch.setattr(codegen, "has_config_folder", lambda _node_type: True)
    monkeypatch.setattr(
        codegen,
        "_generate_node_code",
        lambda *_args, **_kwargs: (
            "@pipeline.polars()\ndef Configured(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n"
        ),
    )
    monkeypatch.setattr(codegen, "NODE_TYPE_TO_DECORATOR", {})

    with pytest.raises(HauteError, match="no registered decorator") as exc_info:
        codegen._node_to_code(node)

    assert exc_info.value.context["node_id"] == "configured"


def test_config_backed_builder_without_function_definition_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.codegen as codegen

    node = _n(
        {
            "id": "configured",
            "data": {"label": "Configured", "nodeType": "banding", "config": {}},
        }
    )
    monkeypatch.setattr(codegen, "has_config_folder", lambda _node_type: True)
    monkeypatch.setattr(
        codegen,
        "_generate_node_code",
        lambda *_args, **_kwargs: "@pipeline.polars()\nConfigured = object()\n",
    )
    monkeypatch.setattr(
        codegen,
        "NODE_TYPE_TO_DECORATOR",
        {node.data.nodeType: "polars"},
    )

    with pytest.raises(HauteError, match="no function definition") as exc_info:
        codegen._node_to_code(node)

    assert exc_info.value.context["node_label"] == "Configured"


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


def test_format_contract_kwarg_derives_concrete_sides_over_a_declared_contract() -> None:
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

    # The scenario expander derives both sides from its config, so the builder
    # contract is emitted rather than a declaration the parser would reject.
    assert contract_kwarg == "contract={'inputs': [], 'outputs': ['scenario_index']}"


def _save_and_parse(graph, base_dir: Path):
    """Generate a pipeline file, write its sidecars, and parse it back."""
    code = graph_to_code(graph, pipeline_name="stale_contract")
    for rel_path, content in collect_node_configs(graph).items():
        cfg_file = base_dir / rel_path
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(content, encoding="utf-8")
    return code, parse_pipeline_source(code, _base_dir=base_dir)


def _single_parent_graph(node_type: str, config: dict[str, object]):
    return _g(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_file_input_config("data.parquet"),
                    },
                },
                {
                    "id": "node",
                    "data": {"label": "node", "nodeType": node_type, "config": config},
                },
            ],
            "edges": [make_edge("source", "node").model_dump()],
        }
    )


def _scoring_model_with_features(features: list[str]) -> ScoringModel:
    model = MagicMock()
    model.feature_names_ = features
    return ScoringModel(
        model=model,
        feature_names=features,
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


@pytest.mark.parametrize(
    ("node_type", "config", "edit", "expected"),
    [
        pytest.param(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [{"max": 25, "value": "0"}],
                    }
                ]
            },
            lambda config: config["factors"][0].update(outputColumn="age_group"),
            {"inputs": ["age"], "outputs": ["age_group"]},
            id="banding-output-column",
        ),
        pytest.param(
            "modelScore",
            {
                "sourceType": "run",
                "run_id": "run123",
                "artifact_path": "model.cbm",
                "task": "regression",
                "output_column": "prediction",
            },
            lambda config: config.update(output_column="competitor_premium"),
            {"inputs": ["a", "b"], "outputs": ["competitor_premium"]},
            id="model-score-output-column",
        ),
    ],
)
def test_graph_to_code_refreshes_a_parsed_contract_after_a_config_edit(
    tmp_path: Path,
    node_type: str,
    config: dict[str, object],
    edit: Callable[[dict[str, Any]], None],
    expected: dict[str, list[str]],
) -> None:
    with patch(
        "haute._mlflow_io.load_mlflow_model",
        return_value=_scoring_model_with_features(["a", "b"]),
    ):
        _, first_parse = _save_and_parse(_single_parent_graph(node_type, config), tmp_path)
        edited = first_parse.model_copy(deep=True)
        edited_node = next(node for node in edited.nodes if node.id == "node")
        # The first parse carries the generated contract onto the config, so
        # the edit below leaves a contract that describes the previous config.
        assert edited_node.data.config["contract"] is not None
        edit(edited_node.data.config)

        code, second_parse = _save_and_parse(edited, tmp_path)

    assert f"contract={expected!r}" in code
    reparsed = next(node for node in second_parse.nodes if node.id == "node")
    assert reparsed.data.config["contract"] == expected


def test_format_contract_kwarg_keeps_declared_model_inputs_when_mlflow_is_unreachable() -> None:
    node = _n(
        {
            "id": "score",
            "data": {
                "label": "score",
                "nodeType": "modelScore",
                "config": {
                    "sourceType": "run",
                    "run_id": "run123",
                    "artifact_path": "model.cbm",
                    "task": "regression",
                    "output_column": "competitor_premium",
                    "contract": {"inputs": ["a", "b"], "outputs": ["prediction"]},
                },
            },
        }
    )

    with patch(
        "haute._mlflow_io.load_mlflow_model",
        side_effect=OSError("tracking server unreachable"),
    ):
        contract_kwarg = _format_contract_kwarg(node)

    # Feature names need the model, so the declaration keeps supplying them;
    # the output column is local config and still matches the parse-time check.
    assert contract_kwarg == "contract={'inputs': ['a', 'b'], 'outputs': ['competitor_premium']}"


def test_graph_to_code_remaps_inputs_by_parent_ids_to_function_names() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "left-uuid",
                    "data": {
                        "label": "Left Input",
                        "nodeType": "dataInput",
                        "config": {"path": "left.parquet"},
                    },
                },
                {
                    "id": "right-uuid",
                    "data": {
                        "label": "Right Input",
                        "nodeType": "dataInput",
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


def test_graph_to_code_drops_single_parent_stale_inputs_by_parent_key() -> None:
    # A single stale ownership key is NOT re-attributed to the lone current
    # parent — reassigning across a rewire would guess ownership it has no
    # evidence for (F003).  Edges/body remain the source of truth, so the
    # stale inputs_by_parent metadata is dropped, not repaired.
    graph = _g(
        {
            "nodes": [
                {
                    "id": "current-parent",
                    "data": {
                        "label": "Current Parent",
                        "nodeType": "dataInput",
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

    assert "'inputs': ['price']" in code
    assert "inputs_by_parent" not in code
    assert "old_parent" not in code


def test_graph_to_code_drops_ambiguous_stale_inputs_by_parent_metadata() -> None:
    graph = _g(
        {
            "nodes": [
                {
                    "id": "left-parent",
                    "data": {
                        "label": "Left Parent",
                        "nodeType": "dataInput",
                        "config": {"path": "left.parquet"},
                    },
                },
                {
                    "id": "right-parent",
                    "data": {
                        "label": "Right Parent",
                        "nodeType": "dataInput",
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
                        "nodeType": "dataInput",
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
                        "nodeType": "dataInput",
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


def test_inject_contract_kwarg_rejects_invalid_generated_body_with_position() -> None:
    """The valid-source boundary never partially rewrites an invalid module."""
    code = (
        "@pipeline.polars(selected_columns=['x'])\n"
        "def Step(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        '    df = df.filter("unterminated\n'
        "    return df\n"
    )

    with pytest.raises(HauteError) as exc_info:
        _inject_contract_kwarg(code, 'contract="opaque"')

    assert exc_info.value.context["reason"] == "source_syntax_invalid"
    assert isinstance(exc_info.value.context["line"], int)
    assert isinstance(exc_info.value.context["column"], int)


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
                        "nodeType": "dataInput",
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

    with pytest.raises(HauteError) as excinfo:
        graph_to_code(graph, pipeline_name="main")

    assert excinfo.value.context["reason"] == "source_syntax_invalid"
    assert isinstance(excinfo.value.context["line"], int)
    assert isinstance(excinfo.value.context["column"], int)
    assert excinfo.value.context["node_id"] == "t"


def test_graph_to_code_multi_refuses_unparseable_submodel_file() -> None:
    """The gate covers every emitted file, including submodel modules."""
    graph = _g(
        {
            "nodes": [
                {
                    "id": "sm-instance",
                    "type": "submodel",
                    "data": {
                        "label": "sm",
                        "nodeType": "submodel",
                        "config": {"definitionId": "sm", "alias": "sm"},
                    },
                }
            ],
            "edges": [],
            "submodels": {
                "sm": {
                    "definitionId": "sm",
                    "file": "modules/sm.py",
                    "graph": {
                        "nodes": [
                            {
                                "id": "src",
                                "data": {
                                    "label": "Src",
                                    "nodeType": "dataInput",
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
                    "inputPorts": [],
                    "outputPorts": [],
                },
            },
        }
    )

    with pytest.raises(HauteError) as excinfo:
        graph_to_code_multi(graph, pipeline_name="main")

    assert excinfo.value.context["reason"] == "source_syntax_invalid"
    assert excinfo.value.context["node_id"] == "t"


def test_graph_to_code_multi_refuses_parent_binding_to_unrouted_input_port() -> None:
    """A declared-but-unrouted public input may be serialised; a binding to it may not.

    Emitting the parent ``connect`` would produce a parseable file whose
    submodel call binds nothing, deferring the failure to a later flatten.
    """
    graph = _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Src",
                        "nodeType": "dataInput",
                        "config": {"path": "d.parquet"},
                    },
                },
                {
                    "id": "sm-instance",
                    "type": "submodel",
                    "data": {
                        "label": "sm",
                        "nodeType": "submodel",
                        "config": {"definitionId": "sm", "alias": "sm"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "bind",
                    "source": "src",
                    "target": "sm-instance",
                    "targetHandle": "in__policy",
                }
            ],
            "submodels": {
                "sm": {
                    "definitionId": "sm",
                    "file": "modules/sm.py",
                    "graph": {
                        "nodes": [
                            {
                                "id": "child",
                                "data": {
                                    "label": "Child",
                                    "nodeType": "polars",
                                    "config": {"code": "df = df"},
                                },
                            }
                        ],
                        "edges": [],
                    },
                    "inputPorts": [{"name": "policy", "targets": []}],
                    "outputPorts": [],
                },
            },
        }
    )

    with pytest.raises(ParseError, match="no internal targets") as excinfo:
        graph_to_code_multi(graph, pipeline_name="main")

    assert excinfo.value.context == {
        "edge_id": "bind",
        "instance_id": "sm-instance",
        "definition_id": "sm",
        "port_name": "policy",
    }


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
                },
                {
                    "id": "pricing-instance",
                    "type": "submodel",
                    "data": {
                        "label": "pricing",
                        "nodeType": "submodel",
                        "config": {"definitionId": "pricing", "alias": "pricing"},
                    },
                },
            ],
            "edges": [],
            "submodels": {
                "pricing": {
                    "definitionId": "pricing",
                    "file": "modules/pricing.py",
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
                    "inputPorts": [],
                    "outputPorts": [],
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


def test_error_on_name_collisions_reports_all_buckets() -> None:
    with pytest.raises(ParseError) as exc_info:
        _error_on_name_collisions(["Rate Step", "Rate-Step", "Quoted Name", "Quoted-Name"])

    rendered = str(exc_info.value)
    assert "Rate_Step" in rendered
    assert "Quoted_Name" in rendered
