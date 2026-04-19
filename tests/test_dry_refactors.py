"""Tests for DRY refactor issues D14, D15, D16, D19, A9, A13.

Covers:
  - D14: resolve_transport helper in cli/_helpers.py
  - D15: _build_manifest wrappers removed, dead code removed
  - D16: find_typed_node helper in routes/_helpers.py
  - D19: compile_node_code shared via conftest
  - A9:  Exception hierarchy (PreambleError, GitError, JsonCacheCancelledError)
  - A13: Dispatch table parity (codegen vs executor)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from haute._types import HauteError

# ── A9: Exception hierarchy ────────────────────────────────────────


class TestExceptionHierarchy:
    """All Haute-specific exceptions should inherit from HauteError."""

    def test_preamble_error_inherits_from_haute_error(self) -> None:
        from haute.executor import PreambleError

        assert issubclass(PreambleError, HauteError)
        exc = PreambleError("bad preamble")
        assert isinstance(exc, HauteError)
        assert isinstance(exc, Exception)

    def test_git_error_inherits_from_haute_error(self) -> None:
        from haute._git import GitError

        assert issubclass(GitError, HauteError)
        exc = GitError("git broke")
        assert isinstance(exc, HauteError)

    def test_git_guardrail_error_inherits_from_git_error_and_haute_error(self) -> None:
        from haute._git import GitGuardrailError

        assert issubclass(GitGuardrailError, HauteError)

    def test_json_cache_cancelled_error_inherits_from_haute_error(self) -> None:
        from haute._json_flatten import JsonCacheCancelledError

        assert issubclass(JsonCacheCancelledError, HauteError)
        exc = JsonCacheCancelledError("cancelled")
        assert isinstance(exc, HauteError)

    def test_preamble_error_preserves_source_line(self) -> None:
        from haute.executor import PreambleError

        exc = PreambleError("msg", source_line=42)
        assert exc.source_line == 42
        assert str(exc) == "msg"

    def test_catch_all_haute_errors(self) -> None:
        """Catching HauteError should catch all subclasses."""
        from haute._git import GitError
        from haute._json_flatten import JsonCacheCancelledError
        from haute.executor import PreambleError

        for exc_cls in (PreambleError, GitError, JsonCacheCancelledError):
            with pytest.raises(HauteError):
                raise exc_cls("test")


# ── A13: Dispatch table parity ─────────────────────────────────────


class TestDispatchTableParity:
    """Codegen and executor dispatch tables must cover the same node types.

    SUBMODEL and SUBMODEL_PORT are executor-only (runtime constructs used
    for inline submodel expansion); codegen never generates code for them
    since the parser expands them into the parent file.
    """

    # Types that are legitimately present in only one table.
    _EXECUTOR_ONLY: frozenset[str] = frozenset({"submodel", "submodelPort"})

    def test_codegen_covers_all_executor_types(self) -> None:
        from haute.codegen import _CODEGEN_BUILDERS
        from haute.executor import _NODE_BUILDERS

        executor_types = set(_NODE_BUILDERS.keys())
        codegen_types = set(_CODEGEN_BUILDERS.keys())

        missing_from_codegen = executor_types - codegen_types - self._EXECUTOR_ONLY
        assert missing_from_codegen == set(), (
            f"Executor has builders for {missing_from_codegen} "
            f"but codegen does not. Add a codegen builder or add to _EXECUTOR_ONLY."
        )

    def test_executor_covers_all_codegen_types(self) -> None:
        from haute.codegen import _CODEGEN_BUILDERS
        from haute.executor import _NODE_BUILDERS

        executor_types = set(_NODE_BUILDERS.keys())
        codegen_types = set(_CODEGEN_BUILDERS.keys())

        missing_from_executor = codegen_types - executor_types
        assert missing_from_executor == set(), (
            f"Codegen has builders for {missing_from_executor} "
            f"but executor does not. Add an executor builder."
        )

    def test_executor_only_types_are_documented(self) -> None:
        """Verify the executor-only types are the ones we expect."""
        from haute.executor import _NODE_BUILDERS
        from haute.graph_utils import NodeType

        for t in self._EXECUTOR_ONLY:
            nt = NodeType(t)
            assert nt in _NODE_BUILDERS, (
                f"Claimed executor-only type {t!r} is not actually in _NODE_BUILDERS"
            )


# ── D14: resolve_transport helper ──────────────────────────────────


class TestResolveTransport:
    """Tests for cli._helpers.resolve_transport()."""

    def _make_config(
        self,
        target: str = "databricks",
        staging_url: str = "",
        prod_url: str = "",
    ) -> MagicMock:
        config = MagicMock()
        config.target = target
        config.ci.staging_endpoint_url = staging_url
        config.ci.production_endpoint_url = prod_url
        return config

    def test_databricks_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="databricks")
        info = resolve_transport(config)
        assert info.kind == "databricks"
        assert info.staging_url == ""
        assert info.prod_url == ""

    def test_container_target_with_staging_url(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="container",
            staging_url="http://staging:8080/quote",
            prod_url="http://prod:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"
        assert info.staging_url == "http://staging:8080/quote"
        assert info.prod_url == "http://prod:8080/quote"

    def test_azure_container_apps_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="azure-container-apps",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_aws_ecs_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="aws-ecs",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_gcp_run_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(
            target="gcp-run",
            staging_url="http://staging:8080/quote",
        )
        info = resolve_transport(config)
        assert info.kind == "http"

    def test_container_target_missing_staging_url_exits(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="container", staging_url="")
        with pytest.raises(SystemExit):
            resolve_transport(config)

    def test_unsupported_target(self) -> None:
        from haute.cli._helpers import resolve_transport

        config = self._make_config(target="sagemaker")
        info = resolve_transport(config)
        assert info.kind == "unsupported"


# ── D15: Dead code removed ─────────────────────────────────────────


class TestDeadCodeRemoved:
    """Verify wrapper functions and dead code have been removed."""

    def test_container_no_build_manifest_wrapper(self) -> None:
        from haute.deploy import _container

        assert not hasattr(_container, "_build_manifest"), (
            "_build_manifest wrapper should have been removed from _container.py"
        )

    def test_container_no_get_haute_version(self) -> None:
        from haute.deploy import _container

        assert not hasattr(_container, "_get_haute_version"), (
            "_get_haute_version should have been removed from _container.py"
        )

    def test_container_no_safe_user(self) -> None:
        from haute.deploy import _container

        assert not hasattr(_container, "_safe_user"), (
            "_safe_user should have been removed from _container.py"
        )

    def test_mlflow_no_build_manifest_wrapper(self) -> None:
        from haute.deploy import _mlflow

        assert not hasattr(_mlflow, "_build_manifest"), (
            "_build_manifest wrapper should have been removed from _mlflow.py"
        )

    def test_mlflow_no_get_user_wrapper(self) -> None:
        from haute.deploy import _mlflow

        assert not hasattr(_mlflow, "_get_user"), (
            "_get_user wrapper should have been removed from _mlflow.py"
        )

    def test_build_manifest_imported_directly(self) -> None:
        """Both modules should import build_manifest from _utils."""
        from haute.deploy import _container, _mlflow

        assert hasattr(_container, "build_manifest")
        assert hasattr(_mlflow, "build_manifest")


# ── D16: find_typed_node helper ────────────────────────────────────


class TestFindTypedNode:
    """Tests for routes._helpers.find_typed_node()."""

    def _make_graph_with_node(self, node_type: str = "modelling") -> MagicMock:
        """Build a minimal mock graph with one node."""
        from haute._types import GraphNode, NodeData, PipelineGraph

        node = GraphNode(
            id="n1",
            data=NodeData(label="MyNode", nodeType=node_type, config={}),
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        return graph

    def test_finds_matching_node(self) -> None:
        from haute.graph_utils import NodeType
        from haute.routes._helpers import find_typed_node

        graph = self._make_graph_with_node("modelling")
        node = find_typed_node(graph, "n1", NodeType.MODELLING, "modelling")
        assert node.id == "n1"

    def test_raises_404_when_node_missing(self) -> None:
        from fastapi import HTTPException

        from haute.graph_utils import NodeType
        from haute.routes._helpers import find_typed_node

        graph = self._make_graph_with_node("modelling")
        with pytest.raises(HTTPException) as exc_info:
            find_typed_node(graph, "nonexistent", NodeType.MODELLING, "modelling")
        assert exc_info.value.status_code == 404

    def test_raises_400_when_wrong_type(self) -> None:
        from fastapi import HTTPException

        from haute.graph_utils import NodeType
        from haute.routes._helpers import find_typed_node

        graph = self._make_graph_with_node("polars")
        with pytest.raises(HTTPException) as exc_info:
            find_typed_node(graph, "n1", NodeType.MODELLING, "modelling")
        assert exc_info.value.status_code == 400
        assert "modelling" in exc_info.value.detail

    def test_optimiser_service_uses_find_typed_node(self) -> None:
        """_find_optimiser_node should delegate to find_typed_node."""
        from haute.routes._optimiser_service import _find_optimiser_node

        graph = self._make_graph_with_node("optimiser")
        node = _find_optimiser_node(graph, "n1")
        assert node.id == "n1"

    def test_train_service_uses_find_typed_node(self) -> None:
        """_find_modelling_node should delegate to find_typed_node."""
        from haute.routes._train_service import _find_modelling_node

        graph = self._make_graph_with_node("modelling")
        node = _find_modelling_node(graph, "n1")
        assert node.id == "n1"


# ── D19: compile_node_code shared helper ───────────────────────────


class TestCompileNodeCodeShared:
    """Verify compile_node_code is accessible from conftest and works."""

    def test_compile_node_code_available(self) -> None:
        from tests.conftest import compile_node_code

        assert callable(compile_node_code)

    def test_compile_node_code_valid_code(self) -> None:
        from tests.conftest import compile_node_code

        # Should not raise
        compile_node_code(
            "@pipeline.data_source(path='data.parquet')\n"
            "def load_data() -> pl.LazyFrame:\n"
            "    return pl.scan_parquet('data.parquet')\n"
        )

    def test_compile_node_code_invalid_code_raises(self) -> None:
        from tests.conftest import compile_node_code

        with pytest.raises(SyntaxError):
            compile_node_code("def broken(:\n")
