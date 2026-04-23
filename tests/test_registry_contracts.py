"""Focused contracts for registry readiness and duplicate wiring."""

from __future__ import annotations

import pytest

from haute import _registry as registry
from haute._types import NodeType


def _codegen_builder(*_args: object, **_kwargs: object) -> str:
    return "def node():\n    pass\n"


def test_register_codegen_decorator_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "NODE_REGISTRY", {})

    registry.register_codegen(NodeType.POLARS)(_codegen_builder)

    with pytest.raises(RuntimeError, match="duplicate codegen registration"):
        registry.register_codegen(NodeType.POLARS)(lambda *_args, **_kwargs: "pass")


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
