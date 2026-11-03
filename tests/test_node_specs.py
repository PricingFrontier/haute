"""The node specification: one entry per node type, its invariants and its views (PCFG-R09)."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from types import MappingProxyType

import pytest

from haute import _node_specs as specs
from haute._config_validation import selected_input_index
from haute._node_specs import (
    NODE_SPECS,
    node_spec,
    node_type_table,
)
from haute._node_type import NodeType
from haute.errors import ConfigError

T = NodeType


def test_every_node_type_has_one_entry_keyed_under_itself() -> None:
    assert set(NODE_SPECS) == set(NodeType)
    for key, spec in NODE_SPECS.items():
        assert spec.node_type is key
    assert isinstance(NODE_SPECS, MappingProxyType)
    assert node_spec("ratingStep") is NODE_SPECS[T.RATING_STEP]
    with pytest.raises(KeyError, match="no node specification"):
        node_spec("mystery")


# ---------------------------------------------------------------------------
# Each derived view equals its pre-specification set
# ---------------------------------------------------------------------------


def test_the_derived_views_keep_their_members() -> None:
    assert specs.REQUEST_INPUT_NODE_TYPES == {T.API_INPUT, T.WORKBENCH_INPUT}
    assert specs.REQUEST_INPUT_KINDS == {"apiInput", "workbenchInput"}
    assert specs.RESPONSE_NODE_TYPES == {T.OUTPUT, T.WORKBENCH_OUTPUT}
    assert specs.SINK_ONLY_NODE_TYPES == {
        T.OUTPUT,
        T.WORKBENCH_OUTPUT,
        T.DATA_OUTPUT,
        T.EXPLORE,
        T.MODELLING,
        T.OPTIMISER,
    }
    assert specs.SOURCE_NODE_TYPES == {T.API_INPUT, T.WORKBENCH_INPUT, T.DATA_INPUT, T.CONSTANT}
    assert specs.CODE_NODE_TYPES == {
        T.DATA_INPUT,
        T.EXTERNAL_FILE,
        T.RATING_STEP,
        T.MODEL_SCORE,
        T.SCENARIO_EXPANDER,
        T.EXPLORE,
    }
    assert specs.STANDALONE_PASSTHROUGH_TYPES == {
        T.DATA_OUTPUT,
        T.EXPLORE,
        T.EXTERNAL_FILE,
        T.MODELLING,
        T.OPTIMISER,
    }
    assert specs.PASS_THROUGH_NODE_TYPES == {T.DATA_OUTPUT, T.MODELLING, T.OPTIMISER}
    assert specs.WORKBENCH_NODE_TYPES == {T.WORKBENCH_INPUT, T.WORKBENCH_OUTPUT}
    assert specs.STRUCTURAL_NODE_TYPES == {T.SUBMODEL, T.SUBMODEL_PORT}
    assert specs.DEFAULT_NODE_TYPE is T.POLARS


def test_the_stepped_surfaces_keep_their_modes() -> None:
    surfaces = {key: (s.start, s.inputs) for key, s in specs.STEPPED_NODE_TYPES.items()}
    assert surfaces == {
        T.POLARS: ("input", "edges"),
        T.DATA_INPUT: ("frame", "none"),
        T.EXTERNAL_FILE: ("frame", "edges"),
        T.RATING_STEP: ("frame", "none"),
        T.MODEL_SCORE: ("frame", "none"),
        T.SCENARIO_EXPANDER: ("frame", "none"),
        T.EXPLORE: ("frame", "none"),
    }


def test_the_decorator_and_folder_maps_keep_their_entries() -> None:
    assert dict(specs.DECORATOR_TO_NODE_TYPE) == {
        "data_input": T.DATA_INPUT,
        "data_output": T.DATA_OUTPUT,
        "api_input": T.API_INPUT,
        "workbench_input": T.WORKBENCH_INPUT,
        "workbench_output": T.WORKBENCH_OUTPUT,
        "polars": T.POLARS,
        "edge_join": T.EDGE_JOIN,
        "model_score": T.MODEL_SCORE,
        "banding": T.BANDING,
        "rating_step": T.RATING_STEP,
        "output": T.OUTPUT,
        "explore": T.EXPLORE,
        "external_file": T.EXTERNAL_FILE,
        "live_switch": T.LIVE_SWITCH,
        "modelling": T.MODELLING,
        "optimiser": T.OPTIMISER,
        "scenario_expander": T.SCENARIO_EXPANDER,
        "optimiser_apply": T.OPTIMISER_APPLY,
        "constant": T.CONSTANT,
    }
    assert {v: k for k, v in specs.NODE_TYPE_TO_DECORATOR.items()} == dict(
        specs.DECORATOR_TO_NODE_TYPE
    )
    assert dict(specs.NODE_TYPE_TO_FOLDER) == {
        T.API_INPUT: "quote_input",
        T.WORKBENCH_INPUT: "workbench_input",
        T.WORKBENCH_OUTPUT: "workbench_output",
        T.DATA_INPUT: "data_input",
        T.DATA_OUTPUT: "data_output",
        T.LIVE_SWITCH: "source_switch",
        T.MODEL_SCORE: "model_scoring",
        T.BANDING: "banding",
        T.RATING_STEP: "rating_step",
        T.OUTPUT: "quote_response",
        T.EXTERNAL_FILE: "load_file",
        T.MODELLING: "model_training",
        T.OPTIMISER: "optimisation",
        T.OPTIMISER_APPLY: "apply_optimisation",
        T.SCENARIO_EXPANDER: "expander",
        T.CONSTANT: "constant",
        T.POLARS: "polars",
    }
    assert {key for key, s in NODE_SPECS.items() if s.sidecar == "optional"} == {T.POLARS}
    assert {key for key, s in NODE_SPECS.items() if s.sidecar == "decorator"} == {
        T.EDGE_JOIN,
        T.EXPLORE,
    }


def test_the_singleton_groups_keep_their_members_and_labels() -> None:
    assert specs.SINGLETON_NODE_GROUPS == (
        (frozenset({T.API_INPUT, T.WORKBENCH_INPUT}), "Quote Input or Workbench Input"),
        (frozenset({T.OUTPUT, T.WORKBENCH_OUTPUT}), "Quote Response or Workbench Output"),
    )


def test_the_runtime_facts_keep_their_values() -> None:
    costs = {key: s.recompute_cost for key, s in NODE_SPECS.items()}
    assert costs == {
        T.API_INPUT: "source",
        T.WORKBENCH_INPUT: "source",
        T.DATA_INPUT: "source",
        T.CONSTANT: "source",
        T.POLARS: "code",
        T.EXTERNAL_FILE: "code",
        T.EDGE_JOIN: "costly",
        T.RATING_STEP: "costly",
        T.MODEL_SCORE: "costly",
        T.OPTIMISER_APPLY: "costly",
        T.BANDING: "cheap",
        T.OUTPUT: "cheap",
        T.WORKBENCH_OUTPUT: "cheap",
        T.DATA_OUTPUT: "cheap",
        T.LIVE_SWITCH: "cheap",
        T.OPTIMISER: "cheap",
        T.MODELLING: "cheap",
        T.SUBMODEL: "cheap",
        T.SUBMODEL_PORT: "cheap",
        T.EXPLORE: "cheap",
        T.SCENARIO_EXPANDER: "cheap",
    }
    assert {key for key, s in NODE_SPECS.items() if not s.slice_transparent} == {
        T.SCENARIO_EXPANDER
    }
    assert {key for key, s in NODE_SPECS.items() if s.behavioural} == {
        T.BANDING,
        T.LIVE_SWITCH,
        T.MODEL_SCORE,
        T.OPTIMISER_APPLY,
        T.OUTPUT,
        T.RATING_STEP,
        T.SCENARIO_EXPANDER,
        T.WORKBENCH_OUTPUT,
    }
    assert {key: s.pass_through for key, s in NODE_SPECS.items() if s.pass_through != "none"} == {
        T.DATA_OUTPUT: "first",
        T.MODELLING: "first",
        T.OPTIMISER: "selected",
    }


def test_the_input_cardinalities_keep_the_assistants_distinctions() -> None:
    by_kind: dict[str, set[NodeType]] = {}
    for key, s in NODE_SPECS.items():
        by_kind.setdefault(s.inputs, set()).add(key)
    assert by_kind == {
        "none": {T.API_INPUT, T.WORKBENCH_INPUT, T.DATA_INPUT, T.CONSTANT},
        "exactly_one": {
            T.DATA_OUTPUT,
            T.EXPLORE,
            T.BANDING,
            T.SCENARIO_EXPANDER,
            T.RATING_STEP,
            T.MODELLING,
            T.MODEL_SCORE,
        },
        "exactly_two": {T.EDGE_JOIN},
        "zero_or_more": {T.EXTERNAL_FILE},
        "one_or_more": {
            T.POLARS,
            T.OUTPUT,
            T.WORKBENCH_OUTPUT,
            T.LIVE_SWITCH,
            T.OPTIMISER,
            T.OPTIMISER_APPLY,
            T.SUBMODEL,
        },
        "structural": {T.SUBMODEL_PORT},
    }
    assert {key for key, s in NODE_SPECS.items() if s.input_count_enforced} == {T.EXPLORE}
    assert node_spec(T.EDGE_JOIN).input_roles == ("base", "join")
    assert node_spec(T.OPTIMISER).input_selectors == (
        "data_input",
        "banding_source",
        "analysis_input",
    )
    assert node_spec(T.OPTIMISER_APPLY).input_selectors == ("ratebook_input",)


# ---------------------------------------------------------------------------
# Import-time invariants: each planted violation is refused
# ---------------------------------------------------------------------------


def _with(key: NodeType, **changes: object) -> dict[NodeType, specs.NodeSpec]:
    table = dict(NODE_SPECS)
    table[key] = dataclasses.replace(table[key], **changes)  # type: ignore[arg-type]
    return table


@pytest.mark.parametrize(
    ("table", "message"),
    [
        pytest.param(
            {k: v for k, v in NODE_SPECS.items() if k is not T.BANDING},
            "missing=['banding']",
            id="missing-type",
        ),
        pytest.param(_with(T.BANDING, node_type=T.RATING_STEP), "keyed under", id="wrong-key"),
        pytest.param(
            _with(T.BANDING, decorator="rating_step"), "decorator 'rating_step'", id="dup-decorator"
        ),
        pytest.param(
            _with(T.BANDING, config_folder="rating_step"), "config folder", id="dup-folder"
        ),
        pytest.param(
            _with(T.API_INPUT, source=False), "a request input is a source", id="request-source"
        ),
        pytest.param(
            _with(T.OUTPUT, sink_only=False), "a response is sink-only", id="response-sink"
        ),
        pytest.param(
            _with(T.SUBMODEL, decorator="submodel"), "structural types", id="structural-decorator"
        ),
        pytest.param(
            _with(T.EDGE_JOIN, config_folder="edge_join"), "sidecar", id="folder-without-sidecar"
        ),
        pytest.param(_with(T.EDGE_JOIN, input_roles=("base", "base")), "two distinct", id="roles"),
        pytest.param(
            _with(T.BANDING, inputs="exactly_two"), "two distinct", id="two-without-roles"
        ),
        pytest.param(
            _with(T.OPTIMISER, pass_through_selector=None),
            "selector",
            id="selected-without-selector",
        ),
        pytest.param(
            _with(T.OPTIMISER, pass_through_selector="ratebook_input"),
            "one of its input selectors",
            id="selector-not-an-input-selector",
        ),
        pytest.param(
            _with(T.BANDING, code="none", stepped=specs._FRAME_STEPS),
            "code surface",
            id="stepped-without-code",
        ),
        pytest.param(
            _with(T.DATA_OUTPUT, standalone_passthrough=False),
            "standalone passthrough",
            id="pass-through",
        ),
        pytest.param(
            _with(T.DATA_OUTPUT, behavioural=True), "behavioural", id="behavioural-passthrough"
        ),
    ],
)
def test_each_specification_invariant_refuses_its_violation(
    table: dict[NodeType, specs.NodeSpec], message: str
) -> None:
    with pytest.raises(RuntimeError) as refused:
        specs._validate_node_specs(table)

    assert message in str(refused.value)


# ---------------------------------------------------------------------------
# node_type_table
# ---------------------------------------------------------------------------


def _every(value: object) -> dict[NodeType, object]:
    return dict.fromkeys(NodeType, value)


def test_a_node_type_table_lists_every_type_and_is_read_only() -> None:
    table = node_type_table("demo", _every(1))

    assert set(table) == set(NodeType)
    assert isinstance(table, MappingProxyType)


def test_a_node_type_table_refuses_a_missing_type() -> None:
    entries = _every(1)
    del entries[T.BANDING]
    with pytest.raises(RuntimeError, match=r"'demo'.*missing=\['banding'\]"):
        node_type_table("demo", entries)


def test_a_node_type_table_refuses_an_extra_key() -> None:
    entries: dict[object, object] = {**_every(1), "mystery": 2}
    with pytest.raises(RuntimeError, match=r"extra=\['mystery'\]"):
        node_type_table("demo", entries)  # type: ignore[arg-type]


def test_a_node_type_table_refuses_a_string_key() -> None:
    entries: dict[object, object] = _every(1)
    del entries[T.BANDING]
    entries["banding"] = 1
    with pytest.raises(RuntimeError, match="keyed by NodeType members"):
        node_type_table("demo", entries)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# selected_input_index keeps resolve_optimiser_data_input's rules
# ---------------------------------------------------------------------------


def test_a_first_pass_through_returns_its_first_input() -> None:
    assert selected_input_index(T.DATA_OUTPUT, {}, ["a", "b"], node_label="out") == 0
    assert selected_input_index(T.MODELLING, {}, [], node_label="train") is None


def test_a_selected_pass_through_returns_the_named_input() -> None:
    config = {"data_input": "quotes"}
    assert selected_input_index(T.OPTIMISER, config, ["bands", "quotes"], node_label="opt") == 1
    # One input and no selector: that input.
    assert selected_input_index(T.OPTIMISER, {}, ["quotes"], node_label="opt") == 0
    assert selected_input_index(T.OPTIMISER, {}, [], node_label="opt") is None


def test_a_selected_pass_through_refuses_a_missing_or_stale_selector() -> None:
    with pytest.raises(ConfigError, match="'opt' data_input is required when multiple"):
        selected_input_index(T.OPTIMISER, {}, ["bands", "quotes"], node_label="opt")
    with pytest.raises(ConfigError, match="opt"):
        selected_input_index(T.OPTIMISER, {"data_input": "gone"}, ["quotes"], node_label="opt")


def test_a_non_pass_through_type_has_no_selected_input() -> None:
    with pytest.raises(ValueError, match="Banding is not a pass-through"):
        selected_input_index(T.BANDING, {}, ["a"], node_label="bands")


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def _python(code: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_specification_is_a_standard_library_leaf() -> None:
    loaded = _python(
        "import sys\n"
        "import haute._node_specs\n"
        "print(sorted(m for m in ('polars', 'pydantic', 'haute._types') if m in sys.modules))"
    )
    assert loaded == "[]"


@pytest.mark.parametrize(
    "order",
    [
        ("haute._node_specs", "haute._graph_utils", "haute._types", "haute"),
        ("haute._graph_utils", "haute._node_specs", "haute", "haute._types"),
        ("haute._types", "haute._graph_utils", "haute._node_specs", "haute"),
        ("haute", "haute._types", "haute._node_specs", "haute._graph_utils"),
    ],
)
def test_the_specification_imports_in_any_order(order: tuple[str, ...]) -> None:
    code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in order)
    _python(code + "print('ok')")
