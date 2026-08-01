"""Tests for the assistant read tools (``haute.assistant._tools``), chiefly
``get_node_schema``.

Spec: specs/assistant/low-level.md — Control flow step 5 and Edge cases.
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

import json
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
    (tmp_path / "haute.toml").write_text(
        '[assistant]\nprovider = "openai"\nmodel = "test"\n'
        'base_url = "https://api.openai.com/v1"\n'
        '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "restricted"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n",
        encoding="utf-8",
    )
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
        assert captured["enforce_contracts"] is True
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
        assert rendered["preamble"]["present"] is True
        assert len(rendered["preamble"]["sha256"]) == 64
        assert len(rendered["project_revision"]) == 64
        assert "def add_flag" not in repr(rendered)

    def test_get_pipeline_missing_source_is_structured_error(self, project_root: Path):
        """A syntax-broken file still parses via the regex fallback (product
        behaviour); a missing file is the genuine unavailable path."""

        from haute.assistant._tools import get_pipeline

        result = get_pipeline("missing.py")
        assert result["error"]["code"] == "pipeline_unavailable"

    def test_get_node_config_returns_redacted_policy_eligible_config(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        result = get_node_config("main.py", "quotes")
        assert result["node"] == "quotes"
        assert isinstance(result["config"], dict)
        assert result["config"]["code"] == "<redacted: executable_source>"
        assert len(result["project_revision"]) == 64

    def test_get_node_config_is_denied_before_read_for_public_only_policy(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="external",
                max_sensitivity="public",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.get_node_config("main.py", "quotes")
        assert result["error"]["code"] == "egress_policy_denied"

    def test_get_node_config_is_denied_before_read_for_internal_policy(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="organization",
                max_sensitivity="internal",
                allow_project_knowledge=False,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.get_node_config("main.py", "quotes")
        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["required_sensitivity"] == "restricted"

    def test_get_node_config_unknown_node(self, project_root: Path):
        from haute.assistant._tools import get_node_config

        assert get_node_config("main.py", "ghost")["error"]["code"] == "unknown_node"

    def test_list_node_types_covers_all_19(self, project_root: Path):
        from haute.assistant._tools import list_node_types

        entries = list_node_types()["node_types"]
        assert len(entries) == 19
        assert all("usage_note" in entry for entry in entries)

    def test_capability_manifest_and_descriptor_batch_are_registry_views(self, project_root: Path):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import (
            get_capability_descriptors,
            get_capability_manifest,
        )

        manifest = get_capability_manifest()
        assert manifest["capability_hash"] == capability_manifest().capability_hash
        assert "node_index" in manifest
        assert "nodes" not in manifest

        node_batch = get_capability_descriptors("node", ["banding", "edgeJoin"])
        assert node_batch["kind"] == "node"
        assert node_batch["count"] == 2
        assert [descriptor["id"] for descriptor in node_batch["descriptors"]] == [
            "banding",
            "edgeJoin",
        ]
        node = node_batch["descriptors"][0]
        assert node["id"] == "banding"
        assert node["config_schema"]["additionalProperties"] is False

        operation = get_capability_descriptors("operation", ["get_pipeline"])["descriptors"][0]
        assert operation["id"] == "get_pipeline"
        assert operation["risk"] == "none"

        unknown = get_capability_descriptors("node", ["banding", "not-real"])
        assert unknown["error"]["code"] == "unsupported_capability"
        assert "descriptors" not in unknown
        duplicate = get_capability_descriptors("node", ["banding", "banding"])

        assert duplicate["error"]["code"] == "invalid_capability_query"

    async def test_every_capability_descriptor_batch_is_json_safe_through_executor(
        self, project_root: Path
    ):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")

        manifest = capability_manifest()
        descriptor_ids = {
            "node": [descriptor.id for descriptor in manifest.nodes],
            "recipe": [str(descriptor["id"]) for descriptor in manifest.recipes],
            "operation": [descriptor.id for descriptor in manifest.operations],
        }

        for kind, ids in descriptor_ids.items():
            returned_ids: list[str] = []
            for offset in range(0, len(ids), 12):
                expected_ids = ids[offset : offset + 12]
                result = await execute_tool(
                    "get_capability_descriptors",
                    {"kind": kind, "ids": expected_ids},
                )
                is_error = "error" in result
                assert is_error is False
                assert result["count"] == len(expected_ids)
                returned_ids.extend(descriptor["id"] for descriptor in result["descriptors"])
                json.dumps(result, allow_nan=False)

            assert returned_ids == ids

    def test_list_datasets_uses_the_installed_input_extension_registry(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_routes
        from haute.assistant._tools import list_datasets

        (project_root / "data" / "notes.txt").write_text("x", encoding="utf-8")
        (project_root / "data" / "supported.feather").write_text("x", encoding="utf-8")
        (project_root / "data" / "unsupported.xml").write_text("x", encoding="utf-8")
        (project_root / "data" / ".hidden.parquet").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            files_routes,
            "_installed_input_extensions",
            lambda: (".parquet", ".feather"),
        )

        result = list_datasets("data")
        names = {item["name"] for item in result["datasets"]}
        assert names == {"quotes.parquet", "supported.feather"}

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

    def test_list_datasets_recursively_finds_nested_project_data(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )

        result = list_datasets("data", recursive=True)

        assert [item["path"] for item in result["datasets"]] == [
            "data/competitor_premiums/competitor_insight.parquet",
            "data/quotes.parquet",
        ]
        assert result["directories"] == ["data/competitor_premiums"]
        assert result["recursive"] is True
        assert result["truncated"] is False

    async def test_routed_showcase_defaults_to_named_folder_recursively(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )
        execute_tool = build_tool_executor(
            "main.py",
            authoring_request=(
                "can you make a pipeline with the parquets in the data folder. "
                "use as many nodee types as you can"
            ),
        )

        result = await execute_tool(
            "list_datasets",
            {"project_root": "wrong-folder", "recursive": "true"},
        )

        assert {item["path"] for item in result["datasets"]} == {
            "data/competitor_premiums/competitor_insight.parquet",
            "data/quotes.parquet",
        }
        assert result["recursive"] is True

    def test_list_datasets_missing_directory(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        assert list_datasets("nope")["error"]["code"] == "directory_not_found"

    def test_list_datasets_rejects_path_escape(self, project_root: Path):
        from haute.assistant._tools import list_datasets

        result = list_datasets("../..")
        assert "error" in result

    def test_dataset_tools_reject_hidden_state_paths(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema, list_datasets

        state_dir = project_root / ".haute"
        state_dir.mkdir()
        (state_dir / "session.json").write_text('[{"secret": "value"}]', encoding="utf-8")

        listed = list_datasets(".haute")
        previewed = get_dataset_schema(".haute/session.json")

        assert listed["error"]["code"] == "dataset_path_forbidden"
        assert previewed["error"]["code"] == "dataset_path_forbidden"

    def test_dataset_tools_hide_denylisted_credential_files(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema, list_datasets

        credentials = project_root / "credentials.json"
        credentials.write_text('[{"token": "do-not-preview"}]', encoding="utf-8")
        credential_dir = project_root / "credentials"
        credential_dir.mkdir()
        (credential_dir / "token.json").write_text(
            '[{"token": "also-do-not-preview"}]', encoding="utf-8"
        )

        listed = list_datasets(None)
        previewed = get_dataset_schema("credentials.json")
        nested = get_dataset_schema("credentials/token.json")

        assert "credentials.json" not in {item["name"] for item in listed["datasets"]}
        assert "credentials" not in listed["directories"]
        assert previewed["error"]["code"] == "dataset_path_forbidden"
        assert nested["error"]["code"] == "dataset_path_forbidden"

    def test_get_dataset_schema_reads_real_file(self, project_root: Path):
        from haute.assistant._tools import get_dataset_schema

        result = get_dataset_schema("data/quotes.parquet")
        names = {column["name"] for column in result["columns"]}
        assert {"quote_id", "vehicle_year", "notes"} <= names
        assert "preview" not in result
        assert "do-not-preview" not in repr(result)

    def test_get_dataset_schema_never_collects_preview_rows(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_route
        from haute.assistant._tools import get_dataset_schema

        def forbidden_preview(_frame):
            raise AssertionError("schema-only assistant reads must not collect preview rows")

        monkeypatch.setattr(files_route, "_collect_file_preview", forbidden_preview)
        result = get_dataset_schema("data/quotes.parquet")
        assert "columns" in result
        assert "preview" not in result

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
        assert len(rendered["capability_hash"]) == 64
        assert rendered["operation_version"] == "1.0"

        schema = await execute_tool("get_node_schema", {"node": "quotes"})
        assert "columns" in schema

        unknown = await execute_tool("explode", {})
        assert unknown["error"]["code"] == "unknown_tool"
        assert "get_pipeline" in unknown["error"]["valid_names"]

        manifest = await execute_tool("get_capability_manifest", {})
        assert "capability_hash" in manifest

        descriptors = await execute_tool(
            "get_capability_descriptors",
            {"kind": "operation", "ids": ["get_pipeline"]},
        )
        assert descriptors["descriptors"][0]["id"] == "get_pipeline"

        recipe = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "reference_join",
                "base_source": "quotes",
                "reference_source": "regions",
                "name": "Attach region",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
            },
        )
        assert recipe["recipe_id"] == "reference_join"
        assert set(recipe) == {
            "recipe_id",
            "version",
            "recipe_plan_hash",
            "capability_hash",
            "operation_version",
        }
        assert len(recipe["recipe_plan_hash"]) == 64

    async def test_pending_recipe_dry_runs_by_handle_without_relaying_operations(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        recipe = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "continuous_banding",
                "source": "quotes",
                "name": "year_band",
                "column": "vehicle_year",
                "output_column": "vehicle_year_band",
                "rules": [
                    {"op1": "<=", "val1": 2020, "assignment": "older"},
                    {"op1": ">", "val1": 2020, "assignment": "newer"},
                ],
                "output_name": "year_response",
                "output_columns": ["vehicle_year_band"],
                "default": "unknown",
            },
        )

        rewritten = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "year_band",
                        "config": {"code": "df = df"},
                    },
                    {"op": "add_edge", "source": "quotes", "target": "$missing"},
                ]
            },
        )
        assert rewritten["error"]["code"] == "recipe_plan_requires_handle"

        exact = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": recipe["recipe_plan_hash"]},
        )
        assert "plan_hash" in exact
        normalized = exact["normalized_operations"]
        assert [operation["op"] for operation in normalized] == [
            "add_node",
            "add_edge",
            "add_node",
            "add_edge",
        ]
        assert normalized[0]["node_type"] == "banding"
        assert normalized[2]["node_type"] == "output"
        assert normalized[2]["name"] == "year_response"
        assert normalized[3]["source"] == "$recipe_banding"
        assert normalized[3]["target"] == "$recipe_output"

    async def test_latest_recipe_handle_replaces_prior_and_rejects_provider_authored_extras(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        arguments = {
            "recipe_id": "continuous_banding",
            "source": "quotes",
            "name": "year_band",
            "column": "vehicle_year",
            "output_column": "vehicle_year_band",
            "rules": [{"op1": "<=", "val1": 2020, "assignment": "older"}],
            "default": "unknown",
        }
        prior = await execute_tool("plan_recipe", arguments)
        latest = await execute_tool(
            "plan_recipe",
            {**arguments, "name": "replacement_year_band"},
        )
        assert prior["recipe_plan_hash"] != latest["recipe_plan_hash"]

        replaced = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": prior["recipe_plan_hash"]},
        )
        assert replaced["error"]["code"] == "recipe_plan_not_found"

        rejected = await execute_tool(
            "dry_run_recipe_plan",
            {
                "recipe_plan_hash": latest["recipe_plan_hash"],
                "extra_ops": [
                    {
                        "op": "add_node",
                        "node_type": "polars",
                        "name": "after_banding",
                        "ref": "after_banding",
                        "config": {"code": "df = df.with_columns(pl.lit(1).alias('test_flag'))"},
                    },
                    {
                        "op": "add_edge",
                        "source": "$recipe_banding",
                        "target": "$after_banding",
                    },
                ],
                "extra_postconditions": [
                    {
                        "kind": "edge_exists",
                        "source": "$recipe_banding",
                        "target": "$after_banding",
                    }
                ],
            },
        )
        assert rejected["error"]["code"] == "invalid_request"
        assert rejected["error"]["validation_reason"] == "unknown_field"

        planned = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": latest["recipe_plan_hash"]},
        )
        assert "plan_hash" in planned
        assert [operation["op"] for operation in planned["normalized_operations"]] == [
            "add_node",
            "add_edge",
        ]

        consumed = await execute_tool(
            "dry_run_recipe_plan",
            {"recipe_plan_hash": latest["recipe_plan_hash"]},
        )
        assert consumed["error"]["code"] == "recipe_plan_not_found"

    async def test_explicit_request_recipe_route_is_enforced_before_dry_run(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor(
            "main.py",
            authoring_request=(
                "After quotes, add age_band: continuously band driver_age into driver_age_band."
            ),
        )
        primitive = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "update_node",
                        "node": "quotes",
                        "config": {},
                    }
                ]
            },
        )
        assert primitive["error"]["code"] == "recipe_route_required"
        assert primitive["error"]["recipe_id"] == "continuous_banding"

        mismatch = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "reference_join",
                "base_source": "quotes",
                "reference_source": "regions",
                "name": "wrong_route",
                "how": "left",
                "left_on": ["region"],
                "right_on": ["region"],
            },
        )
        assert mismatch["error"]["code"] == "recipe_route_mismatch"
        assert mismatch["error"]["recipe_id"] == "continuous_banding"

        wrong_name = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "continuous_banding",
                "source": "quotes",
                "name": "age_banding",
                "column": "driver_age",
                "output_column": "driver_age_band",
                "rules": [{"op1": "<=", "val1": 25, "assignment": "young"}],
                "default": "unknown",
            },
        )
        assert wrong_name["error"]["code"] == "recipe_name_mismatch"
        assert wrong_name["error"]["expected_name"] == "age_band"

        matched = await execute_tool(
            "plan_recipe",
            {
                "recipe_id": "continuous_banding",
                "source": "quotes",
                "name": "age_band",
                "column": "driver_age",
                "output_column": "driver_age_band",
                "rules": [{"op1": "<=", "val1": 25, "assignment": "young"}],
                "default": "unknown",
            },
        )
        assert matched["recipe_id"] == "continuous_banding"

    async def test_explicitly_withheld_rating_material_blocks_every_mutation_planner(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor(
            "main.py",
            authoring_request=(
                "Add rating factors, but do not supply missing-factor policy or factor values."
            ),
        )
        calls = (
            (
                "dry_run_graph_edits",
                {
                    "ops": [
                        {
                            "op": "update_node",
                            "node": "quotes",
                            "config": {},
                        }
                    ]
                },
            ),
            (
                "plan_recipe",
                {
                    "recipe_id": "rating_step",
                    "source": "quotes",
                    "name": "rating_factors",
                    "tables": [
                        {
                            "factors": ["region"],
                            "output_column": "region_factor",
                            "entries": [{"factor_values": ["north"], "value": 1.1}],
                            "default_value": 1.0,
                        }
                    ],
                },
            ),
            ("dry_run_recipe_plan", {"recipe_plan_hash": "a" * 64}),
            ("apply_graph_plan", {"plan_hash": "a" * 64}),
        )

        for name, arguments in calls:
            result = await execute_tool(name, arguments)
            assert result["error"]["code"] == "material_input_required"

    async def test_tool_input_and_result_context_are_bounded(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        execute_tool = tools_module.build_tool_executor("main.py")
        oversized_input = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["x" * 1_000_001]},
        )
        assert oversized_input["error"]["code"] == "tool_payload_too_large"

        monkeypatch.setattr(
            tools_module,
            "get_authoring_guide",
            lambda: {"content": "x" * 256_001},
        )
        oversized_result = await execute_tool("get_authoring_guide", {})
        assert oversized_result["error"]["code"] == "tool_result_too_large"

    async def test_public_only_policy_denies_internal_project_tools_before_read(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module
        from haute.assistant._config import EgressPolicy

        monkeypatch.setattr(
            tools_module,
            "resolve_egress_policy",
            lambda _root: EgressPolicy(
                trust="external",
                max_sensitivity="public",
                allow_project_knowledge=True,
                allow_executable_source=False,
                allow_row_samples=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "get_pipeline",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy denial must happen before project read")
            ),
        )
        execute_tool = tools_module.build_tool_executor("main.py")

        read = await execute_tool("get_pipeline", {})
        mutation = await execute_tool("dry_run_graph_edits", {"ops": []})

        assert read["error"]["code"] == "egress_policy_denied"
        assert mutation["error"]["code"] == "egress_policy_denied"

    async def test_missing_required_argument_is_closed_invalid_request(self, project_root: Path):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("get_node_schema", {})
        assert result["error"]["code"] == "invalid_request"
        assert "KeyError" not in result["error"]["message"]
        assert result["capability_hash"] == capability_manifest().capability_hash
        assert result["operation_version"] == "1.0"

    async def test_malformed_capability_query_has_its_stable_error(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        missing = await execute_tool("get_capability_descriptors", {"kind": "node"})
        extra = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["banding"], "unexpected": True},
        )
        empty = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": []},
        )
        too_many = await execute_tool(
            "get_capability_descriptors",
            {"kind": "node", "ids": ["banding"] * 13},
        )

        assert missing["error"]["code"] == "invalid_capability_query"
        assert extra["error"]["code"] == "invalid_capability_query"
        assert empty["error"]["code"] == "invalid_capability_query"
        assert too_many["error"]["code"] == "invalid_capability_query"

    @pytest.mark.parametrize(
        ("operation", "path", "reason"),
        [
            ({}, "dry_run_graph_edits.ops[0].op", "missing_discriminator"),
            (
                {"op": "not_real"},
                "dry_run_graph_edits.ops[0].op",
                "unsupported_discriminator",
            ),
            (
                {
                    "op": "add_node",
                    "node_type": "dataInput",
                    "name": "Input",
                    "config": "not-an-object",
                },
                "dry_run_graph_edits.ops[0].config",
                "wrong_type",
            ),
        ],
    )
    async def test_discriminated_operation_validation_is_precise(
        self, project_root: Path, operation: dict, path: str, reason: str
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {"ops": [operation]},
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_path"] == path
        assert result["error"]["validation_reason"] == reason

    async def test_recipe_arguments_reject_duplicate_unique_items(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "plan_recipe",
            {
                "recipe_id": "continuous_banding",
                "source": "quotes",
                "name": "year_band",
                "column": "vehicle_year",
                "output_column": "vehicle_year_band",
                "rules": [{"op1": "<=", "val1": 2020, "assignment": "older"}],
                "output_name": "year_response",
                "output_columns": ["vehicle_year_band", "vehicle_year_band"],
                "default": "unknown",
            },
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_path"] == "plan_recipe.output_columns"
        assert result["error"]["validation_reason"] == "duplicate_items"

    @pytest.mark.parametrize(
        ("name", "arguments"),
        [
            ("apply_graph_plan", {"plan_hash": "not-a-hash"}),
            (
                "dry_run_graph_edits",
                {
                    "ops": [
                        {
                            "op": "add_node",
                            "node_type": "polars",
                            "name": "new",
                            "unexpected": True,
                        }
                    ]
                },
            ),
            ("get_project_knowledge", {"query": "rating", "limit": 11}),
            ("dry_run_graph_edits", {"ops": "not-json"}),
        ],
    )
    async def test_closed_tool_schema_enforces_patterns_variants_and_bounds(
        self,
        project_root: Path,
        name: str,
        arguments: dict,
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(name, arguments)

        assert result["error"]["code"] == "invalid_request"

    @pytest.mark.parametrize(
        "invalid_value",
        [Path("not-json"), float("nan"), float("inf")],
    )
    async def test_executor_never_raises_for_non_json_argument_values(
        self,
        project_root: Path,
        invalid_value: object,
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "get_node_schema",
            {"node": invalid_value},
        )

        assert result["error"]["code"] == "invalid_request"
        assert "not-json" not in result["error"]["message"]

    async def test_combined_apply_graph_edits_tool_is_not_provider_visible(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("apply_graph_edits", {"ops": []})
        assert result["error"]["code"] == "unknown_tool"
        assert "apply_graph_edits" not in result["error"]["valid_names"]

    async def test_plan_tools_share_exact_single_use_authority(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py", session_id="session-1")
        dry_run = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )
        assert len(dry_run["plan_hash"]) == 64
        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert "resulting_graph_shape" in dry_run

        refused = await execute_tool(
            "apply_graph_plan",
            {"plan_hash": dry_run["plan_hash"]},
        )
        assert refused["error"]["code"] == "authority_denied"

    async def test_destructive_dry_run_survives_shared_service_boundary(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.assistant import _tools
        from haute.assistant._ops import PlanStore

        shared_store = PlanStore()
        monkeypatch.setattr(_tools, "_PLAN_STORE", shared_store)
        execute_tool = _tools.build_tool_executor("main.py", session_id="session-1")

        dry_run = await execute_tool(
            "dry_run_graph_edits",
            {"ops": [{"op": "delete_node", "node": "enriched"}]},
        )

        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert shared_store.get(dry_run["plan_hash"]).plan_hash == dry_run["plan_hash"]


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
        knowledge = await execute_tool(
            "get_project_knowledge",
            {"query": "pipeline", "limit": 1},
        )
        assert "items" in knowledge
        plan = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )
        assert "schema:data/quotes.parquet" in plan["revision_sources"]
        example = await execute_tool("get_example", {"name": example_index()[0][0]})
        assert "graph" in example
        guide = await execute_tool("get_authoring_guide", {})
        assert guide["approval_status"] == "reviewed"
        assert len(guide["sha256"]) == 64
        assert "node" in guide["content"].casefold()

    async def test_dry_run_rejects_dataset_schema_changed_after_retrieval(
        self,
        project_root: Path,
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        schema = await execute_tool(
            "get_dataset_schema",
            {"path": "data/quotes.parquet"},
        )
        assert "source_digest" in schema
        pl.DataFrame({"replacement": [1]}).write_parquet(project_root / "data" / "quotes.parquet")

        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )

        assert result["error"]["code"] == "stale_project_evidence"

    async def test_new_turn_carries_provider_visible_schema_evidence_into_plan(
        self,
        project_root: Path,
    ):
        from haute.assistant._tools import build_tool_executor

        first_turn = build_tool_executor("main.py")
        schema = await first_turn(
            "get_dataset_schema",
            {"path": "data/quotes.parquet"},
        )
        second_turn = build_tool_executor(
            "main.py",
            prior_messages=[
                {
                    "role": "tool",
                    "tool_call_id": "schema-1",
                    "name": "get_dataset_schema",
                    "content": schema,
                    "is_error": False,
                }
            ],
        )

        plan = await second_turn(
            "dry_run_graph_edits",
            {
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ]
            },
        )

        assert "schema:data/quotes.parquet" in plan["revision_sources"]


class TestClosedSchemaKeywords:
    def test_max_length_rejects_long_strings(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "string", "maxLength": 3}
        _validate_tool_value("abc", schema, path="tool.field")

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value("abcd", schema, path="tool.field")

        assert excinfo.value.path == "tool.field"
        assert excinfo.value.reason == "too_long"
        assert str(excinfo.value) == "tool.field is too long"

    def test_unique_items_rejects_repeated_members(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
        _validate_tool_value(["a", "b"], schema, path="tool.items")

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value(["a", "a"], schema, path="tool.items")

        assert excinfo.value.path == "tool.items"
        assert excinfo.value.reason == "duplicate_items"
        assert str(excinfo.value) == "tool.items contains duplicate items"

    def test_unique_items_compares_unhashable_members_canonically(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "array", "uniqueItems": True}
        _validate_tool_value([{"a": 1}, {"a": 2}], schema, path="tool.items")

        with pytest.raises(_ToolArgumentValidationError):
            _validate_tool_value([{"a": 1, "b": 2}, {"b": 2, "a": 1}], schema, path="tool.items")

    def test_unique_items_ignores_unencodable_members(self):
        from haute.assistant._tools import _validate_tool_value

        _validate_tool_value(
            [float("nan"), float("nan")],
            {"type": "array", "uniqueItems": True},
            path="tool.items",
        )

    def test_unique_items_is_inactive_unless_declared(self):
        from haute.assistant._tools import _validate_tool_value

        _validate_tool_value(["a", "a"], {"type": "array"}, path="tool.items")
