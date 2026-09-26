"""Fail-loud codegen contract tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from haute._codegen_builders import _gen_submodel_placeholder_unreachable
from haute._config_builder import resolve_parse_time_contract
from haute._config_io import collect_node_configs
from haute._contracts import Contract
from haute._mlflow_io import ScoringModel
from haute._types import NodeType
from haute.codegen import (
    _contract_keyword,
    _contract_value,
    _error_on_name_collisions,
    _node_to_code,
    graph_to_code,
    graph_to_code_multi,
)
from haute.errors import ConfigError, HauteError, ParseError
from haute.parser import parse_pipeline_source
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_edge, make_file_input_config
from tests.conftest import make_graph as _g
from tests.conftest import make_node as _n


def test_config_backed_node_without_decorator_mapping_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute._codegen_builders as builders
    import haute.codegen as codegen

    node = _n(
        {
            "id": "configured",
            "data": {"label": "Configured", "nodeType": "banding", "config": {}},
        }
    )
    monkeypatch.setattr(builders, "NODE_TYPE_TO_DECORATOR", {})

    with pytest.raises(HauteError, match="no registered decorator") as exc_info:
        codegen._node_to_code(node)

    assert exc_info.value.context["node_id"] == "configured"
    assert exc_info.value.context["node_type"] == "banding"


# ---------------------------------------------------------------------------
# A decorator keyword value is printed by the literal printer, which refuses a
# value it cannot write as a Python literal instead of falling back to repr.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column_config", "message", "detail"),
    [
        pytest.param(
            {"selected_columns": ("premium",)},
            "no Python literal form",
            ("value_type", "tuple"),
            id="tuple",
        ),
        pytest.param(
            {"column_renames": {"premium": object()}},
            "no Python literal form",
            ("value_type", "object"),
            id="object",
        ),
        pytest.param(
            {"categorical_levels": {"band": [float("nan")]}},
            "must be a finite number",
            ("value", "nan"),
            id="non-finite-float",
        ),
    ],
)
def test_decorator_value_without_a_literal_form_fails_loudly(
    column_config: dict[str, object],
    message: str,
    detail: tuple[str, str],
) -> None:
    node = _n(
        {
            "id": "unprintable",
            "data": {
                "label": "Unprintable",
                "nodeType": "polars",
                "config": {"code": "df = source", **column_config},
            },
        }
    )

    with pytest.raises(HauteError, match=message) as exc_info:
        _node_to_code(node, source_names=["source"])

    key, value = detail
    assert exc_info.value.context[key] == value
    # The printing failure names the node it was printing.
    assert exc_info.value.context["node_id"] == "unprintable"
    assert exc_info.value.context["node_label"] == "Unprintable"
    assert exc_info.value.context["node_type"] == "polars"


# ---------------------------------------------------------------------------
# The contract keyword: rendered with the rest of the decorator, and only
# when it tells the parser something it cannot derive offline.
# ---------------------------------------------------------------------------


def test_contract_keyword_is_rendered_last_in_a_broken_decorator() -> None:
    """A decorator too long for one line breaks one keyword per line, as ruff
    lays it out, with the contract keyword last and every string value intact
    — however many parentheses the values carry."""
    node = _n(
        {
            "id": "step",
            "data": {
                "label": "Step",
                "nodeType": "polars",
                "config": {
                    "code": "df = source",
                    "selected_columns": ["size (mm)", "x ) y"],
                    "contract": {"inputs": ["size (mm)"], "outputs": ["x ) y"]},
                },
            },
        }
    )

    code = _node_to_code(node, source_names=["source"])

    assert code.startswith(
        "@pipeline.polars(\n"
        '    selected_columns=["size (mm)", "x ) y"],\n'
        '    contract={"inputs": ["size (mm)"], "outputs": ["x ) y"]},\n'
        ")\n"
        "def Step(source: pl.LazyFrame) -> pl.LazyFrame:\n"
    )
    _compile_node_code(code)


def test_contract_keyword_turns_a_bare_decorator_into_a_call() -> None:
    node = _n(
        {
            "id": "step",
            "data": {
                "label": "Step",
                "nodeType": "polars",
                "config": {"code": "df = source", "contract": {"inputs": ["a"], "outputs": ["b"]}},
            },
        }
    )

    code = _node_to_code(node, source_names=["source"])

    assert code.splitlines()[0] == '@pipeline.polars(contract={"inputs": ["a"], "outputs": ["b"]})'
    _compile_node_code(code)


def test_opaque_contract_leaves_the_decorator_bare() -> None:
    node = _n(
        {
            "id": "step",
            "data": {
                "label": "Step",
                "nodeType": "polars",
                "config": {"code": "df = source", "contract": "opaque"},
            },
        }
    )

    assert _contract_keyword(node) is None
    assert _node_to_code(node, source_names=["source"]).splitlines()[0] == "@pipeline.polars"


def test_contract_keyword_preserves_inputs_by_parent() -> None:
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

    assert _contract_keyword(node) == {
        "inputs": ["key", "left_value", "right_value"],
        "outputs": [],
        "inputs_by_parent": {
            "left": ["key", "left_value"],
            "right": ["key", "right_value"],
        },
    }


def test_contract_keyword_derives_concrete_sides_over_a_declared_contract() -> None:
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

    # The scenario expander derives both sides from its config, so neither the
    # stale declaration (which the parser would reject) nor the builder
    # contract (which the parser derives itself) is emitted.
    assert _contract_keyword(node) is None
    code = _node_to_code(node, source_names=["quotes"])
    assert "contract=" not in code
    assert "premium" not in code


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
    ("node_type", "config", "edit", "keyword", "expected"),
    [
        pytest.param(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "breakpoints",
                        "rules": [{"boundary": "25", "label": "0"}],
                    }
                ]
            },
            lambda config: config["factors"][0].update(outputColumn="age_group"),
            None,
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
            'contract={"inputs": ["a", "b"], "outputs": ["competitor_premium"]}',
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
    keyword: str | None,
    expected: dict[str, list[str]],
) -> None:
    with patch(
        "haute._mlflow_io.load_mlflow_model",
        return_value=_scoring_model_with_features(["a", "b"]),
    ):
        first_code, first_parse = _save_and_parse(_single_parent_graph(node_type, config), tmp_path)
        edited = first_parse.model_copy(deep=True)
        edited_node = next(node for node in edited.nodes if node.id == "node")
        # A keyword the parser cannot derive (the model's features) is carried
        # onto the config, so the edit below leaves a contract that describes
        # the previous config. A contract the settings already imply is never
        # emitted, so for banding there is nothing to go stale.
        assert ("contract=" in first_code) == (keyword is not None)
        assert (edited_node.data.config.get("contract") is not None) == (keyword is not None)
        edit(edited_node.data.config)

        code, second_parse = _save_and_parse(edited, tmp_path)

    reparsed = next(node for node in second_parse.nodes if node.id == "node")
    if keyword is None:
        assert "contract=" not in code
        assert "contract" not in reparsed.data.config
    else:
        assert keyword in code
        assert reparsed.data.config["contract"] == expected
    effective = Contract.from_user_declared(
        reparsed.data.config.get("contract")
    ) or resolve_parse_time_contract(NodeType(node_type), reparsed.data.config)
    assert _contract_value(effective) == expected


def test_contract_keyword_keeps_declared_model_inputs_when_mlflow_is_unreachable() -> None:
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
        contract = _contract_keyword(node)

    # Feature names need the model, so the declaration keeps supplying them;
    # the output column is local config and still matches the parse-time check.
    assert contract == {"inputs": ["a", "b"], "outputs": ["competitor_premium"]}


def test_offline_contract_derivation_never_loads_a_model() -> None:
    """Recovery generation derives what the parse-time check derives: the
    output column from config, the model's features from the declaration."""
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

    with patch("haute._mlflow_io.load_mlflow_model") as load_model:
        contract = _contract_keyword(node, contract_source="offline")

    load_model.assert_not_called()
    assert contract == {"inputs": ["a", "b"], "outputs": ["competitor_premium"]}


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

    assert '"Left_Input": ["key", "left_value"]' in code
    assert '"Right_Input": ["key", "right_value"]' in code
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

    assert '@pipeline.polars(contract={"inputs": ["price"], "outputs": []})' in code
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
    assert '"inputs": ["id", "left_price", "right_price"]' in code
    assert '"outputs": []' in code
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
    assert '"inputs": ["discount", "price"]' in code
    assert '"outputs": []' in code
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

    assert (
        "@pipeline.instance(\n"
        '    of="competitor_features",\n'
        "    contract={\n"
        '        "inputs": ["competitor_premium", "premium"],\n'
        '        "outputs": ["difference_to_market"],\n'
        "    },\n"
        ")\n"
        "def competitor_features_scenarios(premium): ...\n"
    ) in code


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

    with pytest.raises(ConfigError, match="is not valid Python") as excinfo:
        graph_to_code(graph, pipeline_name="main")

    # The error names the file, the line and the offending code block.
    assert excinfo.value.context["file"] == "main.py"
    assert isinstance(excinfo.value.context["line"], int)
    assert excinfo.value.context["offending_text"] == "df = df.filter("


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

    with pytest.raises(ConfigError, match="is not valid Python") as excinfo:
        graph_to_code_multi(graph, pipeline_name="main")

    assert excinfo.value.context["file"] == "modules/sm.py"
    assert isinstance(excinfo.value.context["line"], int)
    assert excinfo.value.context["offending_text"] == "df = ((("


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
