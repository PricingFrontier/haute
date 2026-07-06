"""Focused contracts for registry readiness and duplicate wiring."""

from __future__ import annotations

import inspect
import subprocess
import sys
from collections.abc import Callable
from typing import Any, get_type_hints

import pytest

from haute import _registry as registry
from haute._types import NodeType


def _exec_builder(*_args: object, **_kwargs: object) -> tuple[str, object, bool]:
    return "node", object(), False


def _codegen_builder(*_args: object, **_kwargs: object) -> str:
    return "def node():\n    pass\n"


def _column_contract(config: dict[str, object]) -> tuple[str, dict[str, object]]:
    return "contract", config


def test_register_exec_signature_keeps_metadata_keyword_only() -> None:
    signature = inspect.signature(registry.register_exec)

    assert signature.parameters["node_type"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert signature.parameters["column_contract"].kind is inspect.Parameter.KEYWORD_ONLY


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
        NodeType.DATA_SOURCE,
        column_contract=_column_contract,
    )(_exec_builder)

    entry = registry.NODE_REGISTRY[NodeType.DATA_SOURCE]
    config = {"path": "input.csv"}

    assert registered is _exec_builder
    assert entry.exec is _exec_builder
    assert registry.get_exec(NodeType.DATA_SOURCE) is _exec_builder
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
