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


def _passthrough_codegen(*_args: object, **_kwargs: object) -> str:
    """Codegen body that returns its input frame verbatim (a bare passthrough)."""
    return "def node(src):\n    return src\n"


def _wrapped_codegen(*_args: object, **_kwargs: object) -> str:
    """Codegen body that routes through a helper (not a bare passthrough)."""
    return "def node(src):\n    return apply_thing_from_config(src)\n"


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
    assert registry.get_exec(NodeType.DATA_INPUT) is _exec_builder
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


def test_behavioural_passthrough_body_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A behavioural node whose codegen returns a bare frame is rejected.

    Drives the body of ``_validate_behavioural_bodies_not_passthrough`` end to
    end: the loop must run (not zero-iterate), the entry must NOT be skipped,
    and the bare-passthrough probe must append the offender and raise.
    """
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            NodeType.BANDING: registry.NodeRegistryEntry(
                codegen=_passthrough_codegen,
                is_behavioural=True,
            )
        },
    )

    with pytest.raises(RuntimeError) as exc_info:
        registry._validate_behavioural_bodies_not_passthrough()

    message = str(exc_info.value)
    assert "passthrough codegen body" in message
    assert "banding" in message


def test_behavioural_wrapped_body_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A behavioural node whose codegen routes through a helper is accepted."""
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            NodeType.BANDING: registry.NodeRegistryEntry(
                codegen=_wrapped_codegen,
                is_behavioural=True,
            )
        },
    )

    registry._validate_behavioural_bodies_not_passthrough()


def test_non_behavioural_passthrough_body_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NON-behavioural node is exempt even if its codegen is a bare passthrough.

    Pins the ``not entry.is_behavioural`` arm of the skip guard: inverting it
    would start policing passthrough bodies on pure-passthrough node types and
    wrongly raise here.
    """
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            NodeType.BANDING: registry.NodeRegistryEntry(
                codegen=_passthrough_codegen,
                is_behavioural=False,
            )
        },
    )

    registry._validate_behavioural_bodies_not_passthrough()


def test_behavioural_missing_codegen_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A behavioural node with no codegen builder is skipped, not probed.

    Pins the ``entry.codegen is None`` arm of the skip guard: inverting it would
    try to call ``None(node, …)`` and blow up instead of skipping.
    """
    monkeypatch.setattr(
        registry,
        "NODE_REGISTRY",
        {
            NodeType.BANDING: registry.NodeRegistryEntry(
                codegen=None,
                is_behavioural=True,
            )
        },
    )

    registry._validate_behavioural_bodies_not_passthrough()


def test_codegen_body_is_bare_passthrough_detects_bare_return() -> None:
    """A function whose sole return is a bare parameter is a passthrough.

    Exercises every branch of ``_codegen_body_is_bare_passthrough``: both loops
    must iterate, the FunctionDef ``continue`` must skip the module node, and
    the ``return True`` must fire.
    """
    import ast

    tree = ast.parse("def node(src):\n    return src\n")

    assert registry._codegen_body_is_bare_passthrough(tree) is True


def test_codegen_body_is_bare_passthrough_allows_wrapped_return() -> None:
    """A function that returns a call (not a bare name) is not a passthrough."""
    import ast

    tree = ast.parse("def node(src):\n    return apply_thing_from_config(src)\n")

    assert registry._codegen_body_is_bare_passthrough(tree) is False


def test_codegen_body_is_bare_passthrough_allows_non_param_return() -> None:
    """Returning a name that is NOT a parameter is not a passthrough."""
    import ast

    tree = ast.parse("def node(src):\n    return unrelated_local\n")

    assert registry._codegen_body_is_bare_passthrough(tree) is False
