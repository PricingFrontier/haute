"""Tests for the assistant read tools (``haute.assistant._tools``), chiefly
``get_node_schema``.

Spec: docs/specs/assistant/low-level.md — Control flow step 5 and Edge cases.
``get_node_schema(source_file, node)`` parses the saved pipeline, validates
the target id against the ORIGINAL hierarchical graph (submodel placeholder
or submodel-internal id → boundary error; nowhere → unknown-node error),
then reproduces the production graph preparation (flatten → compile preamble
→ ``execute_lazy_graph`` with the graph's active source) and reads the
schema without collecting anything.  Results are structured payloads —
``{"node", "columns": [{"name", "dtype"}]}`` for single-frame nodes,
``{"node", "ports": {port: [...]}}`` for multi-frame sources, and
``{"error": {"code", "message"}}`` for failures — tools never raise into
the loop.

Seam pinned for patchability: ``_tools`` imports
``parse_pipeline_to_graph`` (from ``haute.routes._helpers``) and
``execute_lazy_graph`` (from ``haute.execution``) at module level, so tests
patch ``haute.assistant._tools.<name>``.

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._types import GraphNode, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Fixtures — a real tmp project with a parquet source
# ---------------------------------------------------------------------------


PIPELINE_SOURCE = '''\
import polars as pl

import haute

def add_flag(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(flag=pl.lit(1))


pipeline = haute.Pipeline("main", description="schema tool fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    """Read the quote rows."""

    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive, rename, and drop columns so the schema visibly changes."""

    return add_flag(
        quotes.rename({"vehicle_year": "year"}).drop("notes")
    ).with_columns(age=2026 - pl.col("year"))
'''


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "vehicle_year": [2019, 2021],
            "notes": ["a", "b"],
        }
    ).write_parquet(tmp_path / "data" / "quotes.parquet")
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    return tmp_path


def _columns(result: dict) -> dict[str, str]:
    assert "columns" in result, result
    return {column["name"]: column["dtype"] for column in result["columns"]}


# ---------------------------------------------------------------------------
# End-to-end schema resolution (no mocks)
# ---------------------------------------------------------------------------


class TestGetNodeSchemaEndToEnd:
    def test_source_node_schema_matches_file(self, project_root: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        cols = _columns(result)
        assert cols == {
            "quote_id": "String",
            "vehicle_year": "Int64",
            "notes": "String",
        }

    def test_downstream_node_reflects_transforms_and_preamble(self, project_root: Path):
        """Rename, drop, derived column, and the preamble-defined helper all
        resolve — proving flatten/preamble/engine preparation is wired."""

        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")
        cols = _columns(result)
        assert "year" in cols and "vehicle_year" not in cols
        assert "notes" not in cols
        assert "age" in cols
        assert "flag" in cols  # created by the preamble helper add_flag

    def test_unknown_node_is_structured_error(self, project_root: Path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "ghost")
        assert result["error"]["code"] == "unknown_node"
        assert "ghost" in result["error"]["message"]

    def test_schema_resolution_never_collects(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The collect-poisoning invariant: plan construction plus
        ``collect_schema()`` must never invoke ``LazyFrame.collect``."""

        from haute.assistant._tools import get_node_schema

        def poisoned_collect(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("get_node_schema must never collect data")

        monkeypatch.setattr(pl.LazyFrame, "collect", poisoned_collect)
        result = get_node_schema("main.py", "enriched")
        assert "columns" in result, result


# ---------------------------------------------------------------------------
# Pre-flatten target validation (crafted hierarchical graph)
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str = "polars") -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config={}),
        position={"x": 0.0, "y": 0.0},
    )


def _graph_with_submodel() -> PipelineGraph:
    graph = PipelineGraph(nodes=[_node("a"), _node("submodel__sm1", "submodel")], edges=[])
    return graph.model_copy(
        update={
            "submodels": {
                "sm1": {
                    "graph": {
                        "nodes": [_node("inner_child").model_dump()],
                        "edges": [],
                    }
                }
            }
        }
    )


class TestSubmodelBoundaryValidation:
    @pytest.fixture()
    def patched_parse(self, monkeypatch: pytest.MonkeyPatch) -> PipelineGraph:
        import haute.assistant._tools as tools_module

        graph = _graph_with_submodel()
        monkeypatch.setattr(tools_module, "parse_pipeline_to_graph", lambda _path: graph)
        return graph

    def test_submodel_placeholder_is_boundary_error(self, patched_parse, tmp_path):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "submodel__sm1")
        assert result["error"]["code"] == "submodel_boundary"

    def test_submodel_internal_child_is_boundary_error_never_resolved(
        self, patched_parse, monkeypatch: pytest.MonkeyPatch
    ):
        """The id exists in the FLATTENED executable graph, so this asserts
        validation happens against the hierarchical graph before flattening."""

        import haute.assistant._tools as tools_module

        def must_not_execute(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("engine must not run for a boundary-rejected target")

        monkeypatch.setattr(tools_module, "execute_lazy_graph", must_not_execute)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "inner_child")
        assert result["error"]["code"] == "submodel_boundary"

    def test_id_found_nowhere_is_unknown_node(self, patched_parse):
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "nowhere")
        assert result["error"]["code"] == "unknown_node"


# ---------------------------------------------------------------------------
# Engine invocation contract + shaped results (patched facade)
# ---------------------------------------------------------------------------


class TestEngineInvocation:
    def test_facade_called_with_active_source_and_production_kwargs(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The facade's ``source`` default is ``"live"`` — the tool must pass
        the graph's saved active source, the target/preserve ids, a compiled
        preamble namespace, and the production contract-enforcement flag."""

        import haute.assistant._tools as tools_module
        from haute.executor import ENFORCE_CONTRACTS

        captured: dict = {}
        real_facade = tools_module.execute_lazy_graph

        def capturing_facade(graph, build_node_fn, **kwargs):
            captured.update(kwargs)
            captured["graph"] = graph
            return real_facade(graph, build_node_fn, **kwargs)

        monkeypatch.setattr(tools_module, "execute_lazy_graph", capturing_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "enriched")
        assert "columns" in result, result
        assert captured["target_node_id"] == "enriched"
        assert captured["preserve_node_ids"] == {"enriched"}
        assert captured["enforce_contracts"] is ENFORCE_CONTRACTS
        parsed_active_source = tools_module.parse_pipeline_to_graph(Path("main.py")).active_source
        assert captured["source"] == parsed_active_source
        assert captured["preamble_ns"], "preamble namespace must be compiled and passed"
        assert "add_flag" in captured["preamble_ns"]

    def test_multi_frame_output_reports_per_port_schemas(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A multi-frame source stores dict[port_name, LazyFrame] in
        lazy_outputs — the tool must render per-port, never call
        collect_schema() on the dict."""

        import haute.assistant._tools as tools_module

        def fake_facade(graph, build_node_fn, **kwargs):
            lazy_outputs = {
                kwargs["target_node_id"]: {
                    "quotes_a": pl.LazyFrame({"x": [1]}),
                    "quotes_b": pl.LazyFrame({"y": ["s"], "z": [1.0]}),
                }
            }
            return (lazy_outputs,)

        monkeypatch.setattr(tools_module, "execute_lazy_graph", fake_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        assert "ports" in result, result
        ports = {
            port: {column["name"]: column["dtype"] for column in columns}
            for port, columns in result["ports"].items()
        }
        assert ports == {
            "quotes_a": {"x": "Int64"},
            "quotes_b": {"y": "String", "z": "Float64"},
        }

    def test_engine_raise_becomes_structured_error(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A source snapshot failure keeps its analyst-facing message."""

        import haute.assistant._tools as tools_module
        from haute._source_cache import SourceCacheCorruptError

        def raising_facade(graph, build_node_fn, **kwargs):
            raise SourceCacheCorruptError(
                "The Data Input snapshot is corrupt. Rebuild its cache in the node editor."
            )

        monkeypatch.setattr(tools_module, "execute_lazy_graph", raising_facade)
        from haute.assistant._tools import get_node_schema

        result = get_node_schema("main.py", "quotes")
        assert result["error"]["code"] == "schema_unresolvable"
        assert "Rebuild" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Remaining read tools + the executor dispatch seam
# ---------------------------------------------------------------------------


class TestReadTools:
    def test_get_pipeline_renders_compact_graph(self, project_root: Path):
        from haute.assistant._tools import get_pipeline

        rendered = get_pipeline("main.py")
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}
        assert rendered["name"] == "main"
        assert set(rendered["nodes"][0].keys()) == {"id", "type", "label", "config"}

    def test_get_pipeline_missing_source_is_structured_error(self, project_root: Path):
        """A syntax-broken file still parses via the regex fallback (product
        behaviour); a missing file is the genuine unavailable path."""

        from haute.assistant._tools import get_pipeline

        result = get_pipeline("missing.py")
        assert result["error"]["code"] == "pipeline_unavailable"

    def test_get_node_config_returns_full_config(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        result = get_node_config("main.py", "quotes")
        assert result["node"] == "quotes"
        assert isinstance(result["config"], dict)

    def test_get_node_config_unknown_node(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        assert get_node_config("main.py", "ghost")["error"]["code"] == "unknown_node"

    def test_list_node_types_covers_all_19(self, project_root: Path):
        from haute.assistant._tools import list_node_types

        entries = list_node_types()["node_types"]
        assert len(entries) == 19
        assert all("usage_note" in entry for entry in entries)

    def test_list_datasets_applies_the_extension_allowlist(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        (project_root / "data" / "notes.txt").write_text("x", encoding="utf-8")
        (project_root / "data" / ".hidden.parquet").write_text("x", encoding="utf-8")
        result = list_datasets("data")
        names = {item["name"] for item in result["datasets"]}
        assert names == {"quotes.parquet"}

    def test_list_datasets_names_subdirectories_for_navigation(self, project_root: Path):
        """The project root lists visible subdirectories so the model can
        navigate to nested data instead of guessing paths.

        Regression: with datasets only under ``data/``, the root listing
        returned bare ``{"datasets": []}`` — no clue any subdirectory existed.
        """
        from haute.assistant._tools import list_datasets

        (project_root / ".git").mkdir(exist_ok=True)
        result = list_datasets(None)
        assert result["datasets"] == []
        assert "data" in result["directories"]
        assert all(not d.startswith(".") for d in result["directories"])

    def test_list_datasets_subdirectory_listing_still_navigable(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        (project_root / "data" / "nested").mkdir()
        result = list_datasets("data")
        assert {item["name"] for item in result["datasets"]} == {"quotes.parquet"}
        assert result["directories"] == ["data/nested"]
        assert result["datasets"][0]["path"] == "data/quotes.parquet"

    def test_list_datasets_missing_directory(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        assert list_datasets("nope")["error"]["code"] == "directory_not_found"

    def test_list_datasets_rejects_path_escape(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        result = list_datasets("../..")
        assert "error" in result

    def test_get_dataset_schema_reads_real_file(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema

        result = get_dataset_schema("data/quotes.parquet")
        names = {column["name"] for column in result["columns"]}
        assert {"quote_id", "vehicle_year", "notes"} <= names

    def test_get_dataset_schema_missing_file(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema

        assert get_dataset_schema("data/nope.parquet")["error"]["code"] == "dataset_not_found"

    def test_get_example_passthrough(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._tools import get_example

        name = example_index()[0][0]
        assert "graph" in get_example(name)
        assert get_example("nope")["error"]["code"] == "unknown_example"


class TestToolExecutorDispatch:
    async def test_dispatches_read_tools_and_rejects_unknown(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        rendered = await execute_tool("get_pipeline", {})
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}

        schema = await execute_tool("get_node_schema", {"node": "quotes"})
        assert "columns" in schema

        unknown = await execute_tool("explode", {})
        assert unknown["error"]["code"] == "unknown_tool"
        assert "get_pipeline" in unknown["error"]["valid_names"]

    async def test_missing_required_argument_is_sanitized_tool_failure(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("get_node_schema", {})
        assert result["error"]["code"] == "tool_failed"
        assert "KeyError" not in result["error"]["message"]

    async def test_apply_graph_edits_arm_reaches_the_mutation_tool(self, project_root: Path):
        """The dispatch arm forwards to apply_graph_edits, whose precondition
        refuses on this non-git project — proving the wiring end to end."""

        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("apply_graph_edits", {"ops": []})
        assert result["error"]["code"] == "mutations_disabled"


class TestExecutorArms:
    async def test_every_read_tool_dispatches_through_the_executor(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        assert "config" in await execute_tool("get_node_config", {"node": "quotes"})
        assert len((await execute_tool("list_node_types", {}))["node_types"]) == 19
        listed = await execute_tool("list_datasets", {"project_root": "data"})
        assert listed["datasets"][0]["name"] == "quotes.parquet"
        schema = await execute_tool("get_dataset_schema", {"path": "data/quotes.parquet"})
        assert "columns" in schema
        example = await execute_tool("get_example", {"name": example_index()[0][0]})
        assert "graph" in example
