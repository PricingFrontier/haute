"""Focused contracts for registry readiness and duplicate wiring."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_type_hints

import polars as pl
import pytest

from haute import _registry as registry
from haute import _standalone_nodes as standalone_nodes
from haute._node_specs import STANDALONE_PASSTHROUGH_TYPES
from haute._types import GraphNode, NodeData, NodeType
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
        NodeType.WORKBENCH_OUTPUT,
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
        )
        for node_type in NodeType
    }


def test_register_exec_signature_keeps_metadata_keyword_only() -> None:
    signature = inspect.signature(registry.register_exec)

    assert signature.parameters["node_type"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["column_contract"].kind is inspect.Parameter.KEYWORD_ONLY
    assert set(signature.parameters) == {"node_type", "column_contract"}


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

    entry_repr = repr(registry.NodeRegistryEntry(column_contract=_column_contract))
    assert "column_contract" not in entry_repr
    with pytest.raises(AttributeError):
        entry.unexpected_attribute = object()  # type: ignore[attr-defined]


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
    assert registry.NODE_REGISTRY[NodeType.DATA_INPUT].exec is _exec_builder
    assert entry.column_contract is _column_contract
    assert entry.column_contract(config) == ("contract", config)


def test_register_exec_then_codegen_preserves_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_exec(NodeType.POLARS)(_exec_builder)
    registry.register_codegen(NodeType.POLARS)(_codegen_builder)

    entry = registry.NODE_REGISTRY[NodeType.POLARS]
    assert entry.exec is _exec_builder
    assert entry.codegen is _codegen_builder
    assert registry.NODE_REGISTRY[NodeType.POLARS].exec is _exec_builder
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


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
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


def test_register_codegen_then_exec_preserves_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_codegen(NodeType.POLARS)(_codegen_builder)
    registry.register_exec(NodeType.POLARS)(_exec_builder)

    entry = registry.NODE_REGISTRY[NodeType.POLARS]
    assert entry.exec is _exec_builder
    assert entry.codegen is _codegen_builder
    assert registry.NODE_REGISTRY[NodeType.POLARS].exec is _exec_builder
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


def test_set_codegen_rejects_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.set_codegen(NodeType.POLARS, _codegen_builder)

    with pytest.raises(RuntimeError, match="duplicate codegen registration"):
        registry.set_codegen(NodeType.POLARS, lambda *_args, **_kwargs: "pass")
    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


def test_set_codegen_stores_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.set_codegen(NodeType.POLARS, _codegen_builder)

    assert registry.NODE_REGISTRY[NodeType.POLARS].codegen is _codegen_builder


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
    # A standalone run holds each sidecar to its node type's config model, so each
    # type reads a config of its own.
    sidecars: dict[NodeType, dict[str, Any]] = {
        NodeType.DATA_OUTPUT: {
            "output_type": "file",
            "format": "parquet",
            "mode": "sink",
            "path": "output/node.parquet",
            "arguments": {},
        },
        NodeType.EXPLORE: {},
        NodeType.EXTERNAL_FILE: {"path": "models/node.pkl", "file_type": "pickle"},
        NodeType.MODELLING: {"target": "y", "algorithm": "catboost"},
        NodeType.OPTIMISER: {"data_input": "factors"},
    }
    assert set(sidecars) == STANDALONE_PASSTHROUGH_TYPES
    for node_type, sidecar_config in sidecars.items():
        sidecar = tmp_path / "config" / node_type.value / "node.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(json.dumps(sidecar_config), encoding="utf-8")
    scored = pl.LazyFrame({"x": [1]})
    factors = pl.LazyFrame({"y": [2]})
    declaration = _declaration_in(tmp_path)

    for node_type in STANDALONE_PASSTHROUGH_TYPES:
        config = {"config": f"config/{node_type.value}/node.json"}
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


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param({}, id="no-entry"),
        pytest.param(
            {NodeType.POLARS: registry.NodeRegistryEntry(codegen=_codegen_builder)},
            id="no-exec-builder",
        ),
    ],
)
def test_build_node_fn_without_an_exec_builder_names_the_node(
    monkeypatch: pytest.MonkeyPatch,
    entries: dict[NodeType, registry.NodeRegistryEntry],
) -> None:
    from haute import _builders

    monkeypatch.setattr(_builders, "NODE_REGISTRY", entries)
    node = GraphNode(
        id="t1",
        data=NodeData(label="transform", nodeType=NodeType.POLARS, config={"code": "df"}),
    )

    with pytest.raises(KeyError, match="no exec builder registered") as exc_info:
        _builders._build_node_fn(node, config_checked=True)

    assert "node id='t1'" in str(exc_info.value)


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param({}, id="no-entry"),
        pytest.param(
            {NodeType.POLARS: registry.NodeRegistryEntry(exec=_exec_builder)},
            id="no-codegen-builder",
        ),
    ],
)
def test_generate_node_code_without_a_codegen_builder_names_the_node(
    monkeypatch: pytest.MonkeyPatch,
    entries: dict[NodeType, registry.NodeRegistryEntry],
) -> None:
    from haute import codegen

    monkeypatch.setattr(codegen, "NODE_REGISTRY", entries)
    node = GraphNode(
        id="t1",
        data=NodeData(label="transform", nodeType=NodeType.POLARS, config={"code": "df"}),
    )

    with pytest.raises(KeyError, match="no codegen builder registered") as exc_info:
        codegen._generate_node_code(node)

    assert "node id='t1'" in str(exc_info.value)
