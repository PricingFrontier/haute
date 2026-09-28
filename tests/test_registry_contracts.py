"""Focused contracts for registry readiness and duplicate wiring."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, get_type_hints

import polars as pl
import pytest

from haute import _registry as registry
from haute import _standalone_nodes as standalone_nodes
from haute._standalone_nodes import STANDALONE_PASSTHROUGH_TYPES
from haute._types import MODELLING_CONFIG_KEYS, NodeType
from haute.errors import ConfigError


def _exec_builder(*_args: object, **_kwargs: object) -> tuple[str, object, bool]:
    return "node", object(), False


def _codegen_builder(*_args: object, **_kwargs: object) -> str:
    return "def node():\n    pass\n"


def _column_contract(config: dict[str, object]) -> tuple[str, dict[str, object]]:
    return "contract", config


#: The node types the executor registers as behavioural (stateful apply).
_BEHAVIOURAL_TYPES = frozenset(
    {
        NodeType.BANDING,
        NodeType.LIVE_SWITCH,
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
        NodeType.OUTPUT,
        NodeType.RATING_STEP,
        NodeType.SCENARIO_EXPANDER,
    }
)


def _complete_registry() -> dict[NodeType, registry.NodeRegistryEntry]:
    """A registry with exec, codegen, AND column_contract populated for every type.

    Used by the ``validate_registry_complete`` isolation tests below: starting
    fully-complete lets each test knock out exactly one field on one entry, so
    the resulting ``Missing …`` lists pin down which branch fired — otherwise a
    globally-absent column_contract masks the exec/codegen distinctions.
    """
    return {
        node_type: registry.NodeRegistryEntry(
            exec=_exec_builder,
            codegen=_codegen_builder,
            column_contract=_column_contract,
            recompute_cost="cheap",
        )
        for node_type in NodeType
    }


def test_register_exec_signature_keeps_metadata_keyword_only() -> None:
    signature = inspect.signature(registry.register_exec)

    assert signature.parameters["node_type"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["column_contract"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["recompute_cost"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["slice_transparent"].kind is inspect.Parameter.KEYWORD_ONLY


def test_register_exec_type_hints_are_valid_for_optional_column_contract() -> None:
    hints = get_type_hints(registry.register_exec)

    assert hints["column_contract"] == Callable[[dict[str, Any]], Any] | None


def test_graph_node_is_type_checking_only_runtime_import() -> None:
    assert "GraphNode" not in vars(registry)


def test_registry_module_starts_unready_in_fresh_interpreter() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from haute import _registry as registry; print(registry._REGISTRY_READY)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_registry_entry_uses_slots() -> None:
    entry = registry.NodeRegistryEntry()
    assert entry.slice_transparent is True

    entry_repr = repr(registry.NodeRegistryEntry(column_contract=_column_contract))
    assert "column_contract" not in entry_repr
    with pytest.raises(AttributeError):
        entry.unexpected_attribute = object()  # type: ignore[attr-defined]


def test_modelling_shared_semantics_declaration_is_closed() -> None:
    semantics = registry.MODELLING_NODE_SEMANTICS

    assert semantics.node_type is NodeType.MODELLING
    assert semantics.input_policy is registry.NodeInputPolicy.FIRST_CONNECTED
    assert semantics.decorator_config_keys == MODELLING_CONFIG_KEYS
    assert not hasattr(semantics, "__dict__")
    with pytest.raises(FrozenInstanceError):
        semantics.node_type = NodeType.POLARS  # type: ignore[misc]


def test_register_exec_decorator_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registered = registry.register_exec(NodeType.POLARS)(_exec_builder)

    assert registered is _exec_builder
    with pytest.raises(RuntimeError, match="duplicate exec registration"):
        registry.register_exec(NodeType.POLARS)(
            lambda *_args, **_kwargs: ("duplicate", object(), False)
        )
    assert registry.NODE_REGISTRY[NodeType.POLARS].exec is _exec_builder


def test_register_exec_stores_builder_and_column_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registered = registry.register_exec(
        NodeType.DATA_INPUT,
        column_contract=_column_contract,
    )(_exec_builder)

    entry = registry.NODE_REGISTRY[NodeType.DATA_INPUT]
    config = {"path": "input.csv"}

    assert registered is _exec_builder
    assert entry.exec is _exec_builder
    assert registry.get_exec(NodeType.DATA_INPUT) is _exec_builder
    assert entry.slice_transparent is True
    assert entry.column_contract is _column_contract
    assert entry.column_contract(config) == ("contract", config)


def test_register_exec_preserves_explicit_slice_barrier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})
    registry.register_exec(NodeType.SCENARIO_EXPANDER, slice_transparent=False)(_exec_builder)
    assert registry.NODE_REGISTRY[NodeType.SCENARIO_EXPANDER].slice_transparent is False


def test_register_exec_then_codegen_preserves_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_exec(NodeType.POLARS)(_exec_builder)
    registry.register_codegen(NodeType.POLARS)(_codegen_builder)

    entry = registry.NODE_REGISTRY[NodeType.POLARS]
    assert entry.exec is _exec_builder
    assert entry.codegen is _codegen_builder
    assert registry.get_exec(NodeType.POLARS) is _exec_builder
    assert registry.get_codegen(NodeType.POLARS) is _codegen_builder


def test_register_codegen_decorator_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registered = registry.register_codegen(NodeType.POLARS)(_codegen_builder)

    assert registered is _codegen_builder
    with pytest.raises(RuntimeError, match="duplicate codegen registration"):
        registry.register_codegen(NodeType.POLARS)(lambda *_args, **_kwargs: "pass")
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


def test_register_codegen_stores_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registered = registry.register_codegen(NodeType.POLARS)(_codegen_builder)

    assert registered is _codegen_builder
    assert registry.get_codegen(NodeType.POLARS) is _codegen_builder


def test_register_codegen_then_exec_preserves_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_codegen(NodeType.POLARS)(_codegen_builder)
    registry.register_exec(NodeType.POLARS)(_exec_builder)

    entry = registry.NODE_REGISTRY[NodeType.POLARS]
    assert entry.exec is _exec_builder
    assert entry.codegen is _codegen_builder
    assert registry.get_exec(NodeType.POLARS) is _exec_builder
    assert registry.get_codegen(NodeType.POLARS) is _codegen_builder


def test_set_codegen_rejects_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.set_codegen(NodeType.POLARS, _codegen_builder)

    with pytest.raises(RuntimeError, match="duplicate codegen registration"):
        registry.set_codegen(NodeType.POLARS, lambda *_args, **_kwargs: "pass")
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


def test_set_codegen_stores_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.set_codegen(NodeType.POLARS, _codegen_builder)

    assert registry.get_codegen(NodeType.POLARS) is _codegen_builder


def test_get_exec_missing_entry_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    with pytest.raises(KeyError) as exc_info:
        registry.get_exec(NodeType.POLARS)

    assert "no exec builder registered" in str(exc_info.value)
    assert repr(NodeType.POLARS) in str(exc_info.value)


def test_get_exec_missing_builder_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {NodeType.POLARS: registry.NodeRegistryEntry(codegen=_codegen_builder)},
    )

    with pytest.raises(KeyError) as exc_info:
        registry.get_exec(NodeType.POLARS)

    assert "no exec builder registered" in str(exc_info.value)
    assert repr(NodeType.POLARS) in str(exc_info.value)


def test_get_codegen_missing_entry_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    with pytest.raises(KeyError) as exc_info:
        registry.get_codegen(NodeType.POLARS)

    assert "no codegen builder registered" in str(exc_info.value)
    assert repr(NodeType.POLARS) in str(exc_info.value)


def test_get_codegen_missing_builder_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {NodeType.POLARS: registry.NodeRegistryEntry(exec=_exec_builder)},
    )

    with pytest.raises(KeyError) as exc_info:
        registry.get_codegen(NodeType.POLARS)

    assert "no codegen builder registered" in str(exc_info.value)
    assert repr(NodeType.POLARS) in str(exc_info.value)


def test_validate_registry_complete_accepts_fully_registered_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            node_type: registry.NodeRegistryEntry(
                exec=_exec_builder,
                codegen=_codegen_builder,
                column_contract=_column_contract,
                recompute_cost="cheap",
            )
            for node_type in NodeType
        },
    )

    registry.validate_registry_complete()


def test_validate_registry_complete_reports_missing_exec_and_codegen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_registry = {
        node_type: registry.NodeRegistryEntry(
            exec=_exec_builder,
            codegen=_codegen_builder,
        )
        for node_type in NodeType
    }
    node_registry[NodeType.API_INPUT].exec = None
    node_registry[NodeType.SUBMODEL].codegen = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "NODE_REGISTRY is incomplete" in message
    assert "Missing exec:" in message
    assert "apiInput" in message
    assert "Missing codegen:" in message
    assert "submodel" in message


def test_validate_registry_complete_reports_only_missing_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_registry = {
        node_type: registry.NodeRegistryEntry(
            exec=_exec_builder,
            codegen=_codegen_builder,
        )
        for node_type in NodeType
    }
    node_registry[NodeType.API_INPUT].exec = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "apiInput" in message
    assert "Missing codegen: []" in message


def test_validate_registry_complete_reports_only_missing_codegen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_registry = {
        node_type: registry.NodeRegistryEntry(
            exec=_exec_builder,
            codegen=_codegen_builder,
        )
        for node_type in NodeType
    }
    node_registry[NodeType.SUBMODEL].codegen = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "Missing exec:    []" in message
    assert "submodel" in message


def test_ensure_registry_ready_skips_work_when_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_validate() -> None:
        raise AssertionError("already-ready registry should not validate again")

    monkeypatch.setattr(registry, "_REGISTRY_READY", True)
    monkeypatch.setattr(registry, "validate_registry_complete", _unexpected_validate)

    registry.ensure_registry_ready()


def test_ensure_registry_ready_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_validate() -> None:
        calls.append("validated")

    monkeypatch.setattr(registry, "_REGISTRY_READY", False)
    monkeypatch.setattr(registry, "validate_registry_complete", _fake_validate)

    registry.ensure_registry_ready()
    registry.ensure_registry_ready()

    assert calls == ["validated"]
    assert registry._REGISTRY_READY is True


def test_ensure_registry_ready_failure_does_not_mark_registry_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise RuntimeError("registry incomplete")

    monkeypatch.setattr(registry, "_REGISTRY_READY", False)
    monkeypatch.setattr(registry, "validate_registry_complete", _boom)

    with pytest.raises(RuntimeError, match="registry incomplete"):
        registry.ensure_registry_ready()

    assert registry._REGISTRY_READY is False


def test_register_exec_defaults_is_behavioural_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``is_behavioural=True`` the entry stays non-behavioural.

    Pins the ``is_behavioural: bool = False`` default in ``register_exec``'s
    signature: flipping it to ``True`` would silently mark every registered
    node behavioural.
    """
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_exec(NodeType.POLARS)(_exec_builder)

    assert registry.NODE_REGISTRY[NodeType.POLARS].is_behavioural is False


def test_register_exec_sets_is_behavioural_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_behavioural=True`` is recorded on the entry.

    Pins the ``entry.is_behavioural = True`` assignment in the decorator body:
    flipping the assigned value to ``False`` would drop the behavioural flag on
    a stateful-apply node.
    """
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_exec(NodeType.BANDING, is_behavioural=True)(_exec_builder)

    assert registry.NODE_REGISTRY[NodeType.BANDING].is_behavioural is True


def test_validate_registry_complete_isolates_missing_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single missing exec surfaces in ``Missing exec`` alone.

    With codegen and column_contract complete for every type, only the
    exec-side ``or`` (and the outer ``if missing_exec or …`` guard) can put this
    entry into the raise — so turning either ``or`` into ``and`` drops the
    report and the raise entirely.
    """
    node_registry = _complete_registry()
    node_registry[NodeType.API_INPUT].exec = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "Missing exec:    ['apiInput']" in message
    assert "Missing codegen: []" in message
    assert "Missing contract: []" in message


def test_validate_registry_complete_isolates_missing_codegen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single missing codegen surfaces in ``Missing codegen`` alone."""
    node_registry = _complete_registry()
    node_registry[NodeType.SUBMODEL].codegen = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "Missing exec:    []" in message
    assert "Missing codegen: ['submodel']" in message
    assert "Missing contract: []" in message


def test_validate_registry_complete_isolates_missing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single missing column_contract surfaces in ``Missing contract`` alone."""
    node_registry = _complete_registry()
    node_registry[NodeType.SUBMODEL].column_contract = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "Missing exec:    []" in message
    assert "Missing codegen: []" in message
    assert "Missing contract: ['submodel']" in message


def test_behavioural_standalone_passthrough_type_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A behavioural type that a standalone run passes straight through is rejected.

    Drives ``_validate_behavioural_types_not_standalone_passthrough`` end to
    end: the loop over the runtime's passthrough types must run, the registered
    entry must NOT be skipped, and its behavioural flag must raise naming it.
    """
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {NodeType.DATA_OUTPUT: registry.NodeRegistryEntry(is_behavioural=True)},
    )

    with pytest.raises(RuntimeError) as exc_info:
        registry._validate_behavioural_types_not_standalone_passthrough()

    message = str(exc_info.value)
    assert "passed straight through by a standalone" in message
    assert "['dataOutput']" in message


def test_behavioural_standalone_passthrough_offenders_are_all_named_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every offender is reported, sorted, and a non-behavioural one is not."""
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            NodeType.OPTIMISER: registry.NodeRegistryEntry(is_behavioural=True),
            NodeType.DATA_OUTPUT: registry.NodeRegistryEntry(is_behavioural=True),
            NodeType.EXPLORE: registry.NodeRegistryEntry(is_behavioural=False),
        },
    )

    with pytest.raises(RuntimeError) as exc_info:
        registry._validate_behavioural_types_not_standalone_passthrough()

    assert "['dataOutput', 'optimiser']" in str(exc_info.value)


def test_behavioural_type_outside_standalone_passthrough_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A behavioural type whose decorator performs real work is accepted."""
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {node_type: registry.NodeRegistryEntry(is_behavioural=True) for node_type in NodeType}
        | {
            node_type: registry.NodeRegistryEntry(is_behavioural=False)
            for node_type in STANDALONE_PASSTHROUGH_TYPES
        },
    )

    registry._validate_behavioural_types_not_standalone_passthrough()


def test_non_behavioural_standalone_passthrough_type_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pure passthrough type is exempt.

    Pins the ``entry.is_behavioural`` arm of the check: dropping it would
    start rejecting every passthrough type the runtime legitimately lists.
    """
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            node_type: registry.NodeRegistryEntry(is_behavioural=False)
            for node_type in STANDALONE_PASSTHROUGH_TYPES
        },
    )

    registry._validate_behavioural_types_not_standalone_passthrough()


def test_unregistered_standalone_passthrough_type_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passthrough type with no registry entry is skipped, not dereferenced.

    Pins the ``entry is not None`` arm: without it the check would read
    ``is_behavioural`` from ``None`` instead of leaving completeness to
    ``validate_registry_complete``'s own report.
    """
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry._validate_behavioural_types_not_standalone_passthrough()


def test_validate_registry_complete_rejects_a_behavioural_passthrough_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry validation runs the invariant once every type is registered."""
    node_registry = _complete_registry()
    node_registry[NodeType.MODELLING].is_behavioural = True
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError, match=r"standalone.*\['modelling'\]"):
        registry.validate_registry_complete()


def test_real_registry_keeps_behavioural_types_out_of_the_standalone_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped registry satisfies the invariant, and the check reads the runtime's list.

    The negative half lists a behavioural type (Banding) as a standalone
    passthrough in ``haute._standalone_nodes`` itself and proves validation
    then fails naming it.
    """
    registry.ensure_registry_ready()
    behavioural = {
        node_type for node_type, entry in registry.NODE_REGISTRY.items() if entry.is_behavioural
    }
    assert behavioural == _BEHAVIOURAL_TYPES
    assert behavioural.isdisjoint(STANDALONE_PASSTHROUGH_TYPES)
    registry.validate_registry_complete()

    monkeypatch.setattr(
        standalone_nodes,
        "STANDALONE_PASSTHROUGH_TYPES",
        STANDALONE_PASSTHROUGH_TYPES | {NodeType.BANDING},
    )
    with pytest.raises(RuntimeError, match=r"\['banding'\]"):
        registry.validate_registry_complete()


def _declaration_in(directory: Path) -> Callable[..., Any]:
    """A declaration defined in a pipeline file under *directory*.

    Its ``config=`` paths resolve there, as they do for a saved pipeline.
    """
    namespace: dict[str, Any] = {"__file__": str(directory / "main.py")}
    exec("def node(scored, factors): ...", namespace)
    function: Callable[..., Any] = namespace["node"]
    return function


def test_standalone_passthrough_list_matches_what_a_standalone_run_does(
    tmp_path: Path,
) -> None:
    """The list the invariant reads is the runtime's truth, in both directions.

    Each listed type's standalone work hands back one of its input frames
    untouched; each behavioural type's work needs its sidecar, so it can never
    hand its input back unchanged without reading its settings.
    """
    sidecar = tmp_path / "config" / "optimiser" / "node.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"data_input": "factors"}), encoding="utf-8")
    scored = pl.LazyFrame({"x": [1]})
    factors = pl.LazyFrame({"y": [2]})
    declaration = _declaration_in(tmp_path)

    for node_type in STANDALONE_PASSTHROUGH_TYPES:
        config = {"config": "config/optimiser/node.json"}
        result = standalone_nodes.run_configured_node(
            node_type,
            "declaration",
            name="node",
            config=config,
            fn=declaration,
            frames=(scored, factors),
        )
        expected = factors if node_type is NodeType.OPTIMISER else scored
        assert result is expected, node_type

    for node_type in _BEHAVIOURAL_TYPES:
        with pytest.raises(ConfigError, match="needs its config= sidecar path"):
            standalone_nodes.run_configured_node(
                node_type,
                "declaration",
                name="node",
                config={},
                fn=declaration,
                frames=(scored, factors),
            )


def test_every_node_type_declares_recompute_cost() -> None:
    registry.ensure_registry_ready()
    expected_costs: dict[NodeType, registry.RecomputeCost] = {
        NodeType.API_INPUT: "source",
        NodeType.DATA_INPUT: "source",
        NodeType.CONSTANT: "source",
        NodeType.POLARS: "code",
        NodeType.EXTERNAL_FILE: "code",
        NodeType.EDGE_JOIN: "costly",
        NodeType.RATING_STEP: "costly",
        NodeType.MODEL_SCORE: "costly",
        NodeType.OPTIMISER_APPLY: "costly",
        NodeType.BANDING: "cheap",
        NodeType.OUTPUT: "cheap",
        NodeType.DATA_OUTPUT: "cheap",
        NodeType.LIVE_SWITCH: "cheap",
        NodeType.OPTIMISER: "cheap",
        NodeType.MODELLING: "cheap",
        NodeType.SUBMODEL: "cheap",
        NodeType.SUBMODEL_PORT: "cheap",
        NodeType.EXPLORE: "cheap",
        NodeType.SCENARIO_EXPANDER: "cheap",
    }
    assert set(NodeType) == set(expected_costs.keys())
    for node_type, expected_cost in expected_costs.items():
        entry = registry.NODE_REGISTRY[node_type]
        assert entry.recompute_cost == expected_cost
        if node_type is NodeType.SCENARIO_EXPANDER:
            assert entry.slice_transparent is False
        else:
            assert entry.slice_transparent is True


def test_validation_rejects_a_type_without_recompute_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_registry = _complete_registry()
    node_registry[NodeType.POLARS].recompute_cost = None
    monkeypatch.setattr(registry, "NODE_REGISTRY", node_registry)

    with pytest.raises(RuntimeError) as exc_info:
        registry.validate_registry_complete()

    message = str(exc_info.value)
    assert "Missing cost:    ['polars']" in message
    assert "Missing exec:    []" in message
    assert "Missing codegen: []" in message
    assert "Missing contract: []" in message
