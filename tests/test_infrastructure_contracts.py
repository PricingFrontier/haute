"""Focused coverage for shared infrastructure fail-loudly contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from haute import _cache
from haute import _registry as registry
from haute._types import NodeType


def _exec_builder(*_args: object, **_kwargs: object) -> tuple[str, Any, bool]:
    return "node", lambda: None, False


def _codegen_builder(*_args: object, **_kwargs: object) -> str:
    return "def node():\n    pass\n"


class TestNodeRegistryContracts:
    def test_register_exec_rejects_duplicate_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        registry.register_exec(NodeType.POLARS)(_exec_builder)

        with pytest.raises(RuntimeError, match="duplicate exec registration"):
            registry.register_exec(NodeType.POLARS)(_exec_builder)

    def test_register_exec_stores_column_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        def contract(config: dict[str, Any]) -> set[str]:
            return set(config)

        registry.register_exec(NodeType.POLARS, column_contract=contract)(_exec_builder)

        assert registry.NODE_REGISTRY[NodeType.POLARS].column_contract is contract

    def test_set_codegen_rejects_duplicate_registration(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        registry.set_codegen(NodeType.POLARS, _codegen_builder)

        with pytest.raises(RuntimeError, match="duplicate codegen registration"):
            registry.set_codegen(NodeType.POLARS, _codegen_builder)

    def test_getters_fail_loudly_when_entry_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        with pytest.raises(KeyError, match="no exec builder"):
            registry.get_exec(NodeType.POLARS)
        with pytest.raises(KeyError, match="no codegen builder"):
            registry.get_codegen(NodeType.POLARS)

    def test_validate_registry_complete_reports_both_missing_sides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(registry, "NODE_REGISTRY", {})

        with pytest.raises(RuntimeError) as exc_info:
            registry.validate_registry_complete()

        msg = str(exc_info.value)
        assert "Missing exec" in msg
        assert "Missing codegen" in msg


class TestGraphFingerprintCanonicalisation:
    def test_rejects_dicts_with_non_string_keys(self) -> None:
        with pytest.raises(TypeError, match="non-string key"):
            _cache._canonicalise({1: "not-json-object-compatible"})

    def test_rejects_values_without_deterministic_canonical_form(self) -> None:
        with pytest.raises(TypeError, match="no deterministic canonical form"):
            _cache._canonicalise(object())

    def test_rejects_unknown_canonical_sort_key_types(self) -> None:
        with pytest.raises(TypeError, match="Cannot produce sort key"):
            _cache._sort_key(object())

    def test_canonicalises_sets_by_value_not_insertion_order(self) -> None:
        left = _cache._canonicalise({"items": {"b", "a", "c"}})
        right = _cache._canonicalise({"items": {"c", "b", "a"}})

        assert left == right == {"items": ["a", "b", "c"]}


class TestApiRouteContracts:
    def test_all_api_routes_declare_pydantic_response_models(self) -> None:
        from haute.server import app

        def is_pydantic_response(model: object) -> bool:
            if isinstance(model, type) and issubclass(model, BaseModel):
                return True
            if get_origin(model) is list:
                return all(is_pydantic_response(arg) for arg in get_args(model))
            return False

        def is_declared_stream(route: APIRoute) -> bool:
            """A route that explicitly declares a streaming transport.

            SSE/streaming responses carry no JSON body to model; their wire
            contract lives in the per-event schema union instead (the assistant
            message stream is the first such route).  The exemption requires
            the EXPLICIT ``response_class=StreamingResponse`` declaration —
            an undeclared route still fails this contract.
            """

            from fastapi.responses import StreamingResponse

            response_class = getattr(route, "response_class", None)
            actual = getattr(response_class, "value", response_class)
            return isinstance(actual, type) and issubclass(actual, StreamingResponse)

        offenders = sorted(
            f"{','.join(route.methods or [])} {route.path}: {route.response_model!r}"
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and not is_pydantic_response(route.response_model)
            and not is_declared_stream(route)
        )

        assert offenders == []


class TestLocalToolingContracts:
    def test_pre_commit_config_avoids_platform_shell_wrappers(self) -> None:
        config = (Path(__file__).resolve().parents[1] / ".pre-commit-config.yaml").read_text(
            encoding="utf-8"
        )

        assert "bash -c" not in config
        assert "cd frontend" not in config
        assert "npm --prefix frontend run typecheck" in config
        assert "npm --prefix frontend run lint" in config


class TestTestFileNamingContracts:
    def test_test_paths_describe_contracts_not_review_phases(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        roots = (repo_root / "tests", repo_root / "frontend" / "src")
        stage_name = re.compile(r"(^|[\\/_.-])(phase|wave)\d+", re.IGNORECASE)

        offenders = sorted(
            str(path.relative_to(repo_root))
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".ts", ".tsx"}
            and stage_name.search(str(path.relative_to(repo_root)))
        )

        assert offenders == []
