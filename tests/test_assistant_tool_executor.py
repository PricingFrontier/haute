"""The assistant's remaining read tools and the tool executor's dispatch.

Each read tool returns a structured payload, the executor dispatches every tool name to its arm,
and tool schemas are closed to unknown keywords.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

from tests import _assistant_tool_helpers
from tests._assistant_tool_helpers import (
    _stored_plan,
)
from tests._project import use_project

# Fixtures shared with the other modules split from one suite.
project_root = _assistant_tool_helpers.project_root


# ---------------------------------------------------------------------------
# Remaining read tools + the executor dispatch seam
# ---------------------------------------------------------------------------


class TestReadTools:
    def test_get_pipeline_renders_compact_graph(self, project_root: Path):
        from haute.assistant._tools import get_pipeline

        rendered = get_pipeline("main.py")
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}
        assert rendered["name"] == "main"
        assert set(rendered["nodes"][0].keys()) == {"id", "type", "label", "config", "authoring"}
        assert rendered["nodes"][0]["authoring"] == {"state": "code"}
        assert rendered["preamble"]["present"] is True
        assert len(rendered["preamble"]["sha256"]) == 64
        assert len(rendered["project_revision"]) == 64
        assert "def add_flag" not in repr(rendered)

    def test_get_pipeline_missing_source_is_structured_error(self, project_root: Path):
        """Assistant read tools stay strict; a missing file is unavailable."""

        from haute.assistant._tools import get_pipeline

        result = get_pipeline("missing.py")
        assert result["error"]["code"] == "pipeline_unavailable"

    def test_get_node_config_returns_redacted_policy_eligible_config(self, project_root: Path):
        from haute.assistant._tools import node_config

        result = node_config("main.py", "quotes")
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
                allow_aggregate_statistics=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.node_config("main.py", "quotes")
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
                allow_aggregate_statistics=False,
            ),
        )
        monkeypatch.setattr(
            tools_module,
            "_parse_graph",
            lambda _source: (_ for _ in ()).throw(
                AssertionError("policy must be checked before node config is read")
            ),
        )

        result = tools_module.node_config("main.py", "quotes")
        assert result["error"]["code"] == "egress_policy_denied"
        assert result["error"]["required_sensitivity"] == "restricted"

    def test_get_node_config_unknown_node(self, project_root: Path):
        from haute.assistant._tools import node_config

        assert node_config("main.py", "ghost")["error"]["code"] == "unknown_node"

    def test_read_reference_serves_each_namespace_in_request_order(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import read_reference

        example = example_index()[0][0]
        ids = ["node:banding", "recipe:reference_join", f"example:{example}", "guide"]

        result = read_reference(ids)

        assert result["count"] == 4
        assert [item["id"] for item in result["references"]] == ids
        node, recipe, example_view, guide = (item["content"] for item in result["references"])
        assert node == next(
            descriptor.as_dict()
            for descriptor in capability_manifest().nodes
            if descriptor.id == "banding"
        )
        assert node["config_schema"]["additionalProperties"] is False
        assert node["card"]["configs"]
        assert recipe["id"] == "reference_join"
        assert recipe["argument_schema"]["additionalProperties"] is False
        assert "graph" in example_view
        assert guide["approval_status"] == "reviewed"
        assert len(guide["sha256"]) == 64
        assert guide["step_grammar"]["kinds"]

    @pytest.mark.parametrize(
        ("reference", "close"),
        [
            # An unprefixed node type names its namespaced id first.
            ("banding", "node:banding"),
            ("node:bandng", "node:banding"),
            # `rating_step` is both a recipe and an example, so ids are namespaced.
            ("rating_step", "recipe:rating_step"),
        ],
    )
    def test_an_unknown_reference_is_refused_with_close_valid_ids(
        self, project_root: Path, reference: str, close: str
    ):
        from haute.assistant._tools import read_reference

        result = read_reference(["node:edgeJoin", reference])

        assert result["error"]["code"] == "unknown_reference"
        assert result["error"]["id"] == reference
        assert result["error"]["did_you_mean"][0] == close
        assert result["error"]["fix"] == f"Use {close!r}."
        assert "references" not in result

    async def test_every_reference_is_json_safe_and_bounded_through_executor(
        self, project_root: Path
    ):
        """Every id is served, twelve to a call, within the model-context bound;
        the guide shares its batch with eleven node descriptors."""

        from haute.assistant._tools import _reference_ids, build_tool_executor

        execute_tool = build_tool_executor("main.py")
        ids = list(_reference_ids())
        assert ids[0] == "guide"

        returned_ids: list[str] = []
        for offset in range(0, len(ids), 12):
            expected_ids = ids[offset : offset + 12]
            result = await execute_tool("read_reference", {"ids": expected_ids})
            assert "error" not in result, result
            assert result["count"] == len(expected_ids)
            returned_ids.extend(item["id"] for item in result["references"])
            json.dumps(result, allow_nan=False)

        assert returned_ids == ids

    @pytest.mark.parametrize("tool", ["list_datasets", "get_dataset_schema"])
    def test_a_path_outside_the_project_reports_the_bare_containment_message(
        self, project_root: Path, tool: str
    ):
        from haute.assistant._tools import dataset_listing, dataset_schema

        if tool == "list_datasets":
            result = dataset_listing("../outside")
        else:
            result = dataset_schema("../outside/quotes.parquet")

        assert result["error"]["message"] == "Cannot access paths outside the project root"

    def test_list_datasets_uses_the_installed_input_extension_registry(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_routes
        from haute.assistant._tools import dataset_listing

        (project_root / "data" / "notes.txt").write_text("x", encoding="utf-8")
        (project_root / "data" / "supported.feather").write_text("x", encoding="utf-8")
        (project_root / "data" / "unsupported.xml").write_text("x", encoding="utf-8")
        (project_root / "data" / ".hidden.parquet").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            files_routes,
            "_installed_input_extensions",
            lambda: (".parquet", ".feather"),
        )

        result = dataset_listing("data")
        names = {item["name"] for item in result["datasets"]}
        assert names == {"quotes.parquet", "supported.feather"}

    def test_list_datasets_names_subdirectories_for_navigation(self, project_root: Path):
        """The project root lists visible subdirectories so the model can
        navigate to nested data instead of guessing paths.

        Regression: with datasets only under ``data/``, the root listing
        returned bare ``{"datasets": []}`` — no clue any subdirectory existed.
        """
        from haute.assistant._tools import dataset_listing

        (project_root / ".git").mkdir(exist_ok=True)
        result = dataset_listing(None)
        assert result["datasets"] == []
        assert "data" in result["directories"]
        assert all(not d.startswith(".") for d in result["directories"])

    def test_list_datasets_subdirectory_listing_still_navigable(self, project_root: Path):
        from haute.assistant._tools import dataset_listing

        (project_root / "data" / "nested").mkdir()
        result = dataset_listing("data")
        assert {item["name"] for item in result["datasets"]} == {"quotes.parquet"}
        assert result["directories"] == ["data/nested"]
        assert result["datasets"][0]["path"] == "data/quotes.parquet"

    def test_list_datasets_recursively_finds_nested_project_data(self, project_root: Path):
        from haute.assistant._tools import dataset_listing

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )

        result = dataset_listing("data", recursive=True)

        assert [item["path"] for item in result["datasets"]] == [
            "data/competitor_premiums/competitor_insight.parquet",
            "data/quotes.parquet",
        ]
        assert result["directories"] == ["data/competitor_premiums"]
        assert result["recursive"] is True
        assert result["truncated"] is False

    async def test_executor_runs_the_dataset_listing_the_model_asked_for(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        nested = project_root / "data" / "competitor_premiums"
        nested.mkdir()
        pl.DataFrame({"quote_id": ["q1"], "premium": [123.0]}).write_parquet(
            nested / "competitor_insight.parquet"
        )
        execute_tool = build_tool_executor("main.py")

        result = await execute_tool("find_data", {"directory": "data", "recursive": False})

        assert [item["path"] for item in result["datasets"]] == ["data/quotes.parquet"]
        assert result["recursive"] is False
        assert "schema" not in result

    async def test_find_data_with_a_path_also_returns_that_file_s_schema(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "find_data", {"directory": "data", "path": "data/quotes.parquet"}
        )

        assert [item["path"] for item in result["datasets"]] == ["data/quotes.parquet"]
        schema = result["schema"]
        assert schema["path"] == "data/quotes.parquet"
        assert {column["name"] for column in schema["columns"]} == {
            "quote_id",
            "vehicle_year",
            "notes",
        }
        assert len(schema["source_digest"]) == 64
        assert len(result["project_revision"]) == 64
        missing = await build_tool_executor("main.py")("find_data", {"path": "data/nope.parquet"})
        assert missing["error"]["code"] == "dataset_not_found"

    def test_list_datasets_missing_directory(self, project_root: Path):
        from haute.assistant._tools import dataset_listing

        assert dataset_listing("nope")["error"]["code"] == "directory_not_found"

    def test_list_datasets_rejects_path_escape(self, project_root: Path):
        from haute.assistant._tools import dataset_listing

        result = dataset_listing("../..")
        assert "error" in result

    def test_dataset_tools_reject_hidden_state_paths(self, project_root: Path):
        from haute.assistant._tools import dataset_listing, dataset_schema

        state_dir = project_root / ".haute"
        state_dir.mkdir()
        (state_dir / "session.json").write_text('[{"secret": "value"}]', encoding="utf-8")

        listed = dataset_listing(".haute")
        previewed = dataset_schema(".haute/session.json")

        assert listed["error"]["code"] == "dataset_path_forbidden"
        assert previewed["error"]["code"] == "dataset_path_forbidden"

    def test_dataset_tools_hide_denylisted_credential_files(self, project_root: Path):
        from haute.assistant._tools import dataset_listing, dataset_schema

        credentials = project_root / "credentials.json"
        credentials.write_text('[{"token": "do-not-preview"}]', encoding="utf-8")
        credential_dir = project_root / "credentials"
        credential_dir.mkdir()
        (credential_dir / "token.json").write_text(
            '[{"token": "also-do-not-preview"}]', encoding="utf-8"
        )

        listed = dataset_listing(None)
        previewed = dataset_schema("credentials.json")
        nested = dataset_schema("credentials/token.json")

        assert "credentials.json" not in {item["name"] for item in listed["datasets"]}
        assert "credentials" not in listed["directories"]
        assert previewed["error"]["code"] == "dataset_path_forbidden"
        assert nested["error"]["code"] == "dataset_path_forbidden"

    def test_get_dataset_schema_reads_real_file(self, project_root: Path):
        from haute.assistant._tools import dataset_schema

        result = dataset_schema("data/quotes.parquet")
        names = {column["name"] for column in result["columns"]}
        assert {"quote_id", "vehicle_year", "notes"} <= names
        assert "preview" not in result
        assert "do-not-preview" not in repr(result)

    def test_get_dataset_schema_never_collects_preview_rows(
        self, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.files as files_route
        from haute.assistant._tools import dataset_schema

        def forbidden_preview(_frame):
            raise AssertionError("schema-only assistant reads must not collect preview rows")

        monkeypatch.setattr(files_route, "_collect_file_preview", forbidden_preview)
        result = dataset_schema("data/quotes.parquet")
        assert "columns" in result
        assert "preview" not in result

    def test_get_dataset_schema_missing_file(self, project_root: Path):
        from haute.assistant._tools import dataset_schema

        assert dataset_schema("data/nope.parquet")["error"]["code"] == "dataset_not_found"


class TestToolExecutorDispatch:
    async def test_dispatches_read_tools_and_rejects_unknown(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        rendered = await execute_tool("get_pipeline", {})
        assert {node["id"] for node in rendered["nodes"]} == {"quotes", "enriched"}
        assert len(rendered["capability_hash"]) == 64
        assert rendered["operation_version"] == "1.0"

        schema = await execute_tool("inspect_node", {"node": "quotes"})
        assert "columns" in schema["schema"]
        assert "withheld" not in schema

        unknown = await execute_tool("explode", {})
        assert unknown["error"]["code"] == "unknown_tool"
        assert "get_pipeline" in unknown["error"]["valid_names"]

        references = await execute_tool("read_reference", {"ids": ["node:banding"]})
        assert references["references"][0]["content"]["id"] == "banding"

    async def test_tool_input_and_result_context_are_bounded(
        self,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.assistant._tools as tools_module

        execute_tool = tools_module.build_tool_executor("main.py")
        oversized_input = await execute_tool("read_reference", {"ids": ["x" * 1_000_001]})
        assert oversized_input["error"]["code"] == "tool_payload_too_large"

        monkeypatch.setattr(
            tools_module,
            "_authoring_guide_reference",
            lambda: {"content": "x" * 256_001},
        )
        oversized_result = await execute_tool("read_reference", {"ids": ["guide"]})
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
                allow_aggregate_statistics=False,
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
        mutation = await execute_tool("dry_run_graph_edits", {"summary": "Test plan.", "ops": []})

        assert read["error"]["code"] == "egress_policy_denied"
        assert mutation["error"]["code"] == "egress_policy_denied"

    async def test_missing_required_argument_is_closed_invalid_request(self, project_root: Path):
        from haute.assistant._catalog import capability_manifest
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("inspect_node", {})
        assert result["error"]["code"] == "invalid_request"
        assert "KeyError" not in result["error"]["message"]
        assert result["capability_hash"] == capability_manifest().capability_hash
        assert result["operation_version"] == "1.0"

    async def test_a_malformed_reference_query_is_a_closed_invalid_request(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        missing = await execute_tool("read_reference", {})
        extra = await execute_tool("read_reference", {"ids": ["guide"], "unexpected": True})
        empty = await execute_tool("read_reference", {"ids": []})
        too_many = await execute_tool(
            "read_reference", {"ids": [f"node:{index}" for index in range(13)]}
        )
        duplicate = await execute_tool("read_reference", {"ids": ["guide", "guide"]})

        assert [
            result["error"]["validation_reason"]
            for result in (missing, extra, empty, too_many, duplicate)
        ] == [
            "missing_required",
            "unknown_field",
            "too_few_items",
            "too_many_items",
            "duplicate_items",
        ]
        assert {
            result["error"]["code"] for result in (missing, extra, empty, too_many, duplicate)
        } == {"invalid_request"}

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
            # Every recipe operation has op "recipe": `recipe` then selects its branch.
            (
                {"op": "recipe", "arguments": {}},
                "dry_run_graph_edits.ops[0].recipe",
                "missing_discriminator",
            ),
            (
                {"op": "recipe", "recipe": "numeric_banding", "arguments": {}},
                "dry_run_graph_edits.ops[0].recipe",
                "unsupported_discriminator",
            ),
            (
                {"op": "recipe", "recipe": "response_output", "arguments": {"source": "quotes"}},
                "dry_run_graph_edits.ops[0].arguments.output_columns",
                "missing_required",
            ),
        ],
    )
    async def test_discriminated_operation_validation_is_precise(
        self, project_root: Path, operation: dict, path: str, reason: str
    ):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {"summary": "Test plan.", "ops": [operation]},
        )

        assert result["error"]["code"] == "invalid_request"
        assert result["error"]["validation_path"] == path
        assert result["error"]["validation_reason"] == reason

    async def test_unknown_field_names_the_rejected_field_and_the_allowlist(
        self, project_root: Path
    ):
        """`get_pipeline` reports handles in the operation vocabulary, but a
        model can still reach for the persisted camel-case spelling. Naming
        both the rejected key and the closed allowlist is what makes that
        correctable inside the turn's single retry."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {"op": "delete_node", "node": "quotes"},
                    {
                        "op": "add_edge",
                        "source": "quotes",
                        "target": "enriched",
                        "sourceHandle": "out",
                    },
                ],
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_path"] == "dry_run_graph_edits.ops[1]"
        assert error["validation_reason"] == "unknown_field"
        assert error["unknown_fields"] == ["sourceHandle"]
        assert error["allowed_fields"] == [
            "op",
            "source",
            "source_handle",
            "target",
            "target_handle",
        ]
        assert "sourceHandle" in error["message"]
        assert "source_handle" in error["message"]

    def test_rendered_edges_use_the_graph_edit_operation_field_names(self, project_root: Path):
        """The read shape and the write shape must name handles identically;
        echoing the persisted camel-case spelling invited edit operations the
        closed operation schema then rejected."""

        from haute.assistant._tools import get_pipeline

        edge = get_pipeline("main.py")["edges"][0]

        assert "source_handle" in edge and "target_handle" in edge
        assert "sourceHandle" not in edge and "targetHandle" not in edge

    async def test_recipe_arguments_reject_duplicate_unique_items(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Band vehicle years.",
                "ops": [
                    {
                        "op": "recipe",
                        "recipe": "categorical_banding",
                        "arguments": {
                            "source": "quotes",
                            "name": "year_band",
                            "column": "vehicle_year",
                            "output_column": "vehicle_year_band",
                            "rules": [{"value": "2019", "assignment": "older"}],
                            "output_name": "year_response",
                            "output_columns": ["vehicle_year_band", "vehicle_year_band"],
                            "default": "unknown",
                        },
                    }
                ],
            },
        )

        assert result["error"]["code"] == "invalid_request"
        assert (
            result["error"]["validation_path"]
            == "dry_run_graph_edits.ops[0].arguments.output_columns"
        )
        assert result["error"]["validation_reason"] == "duplicate_items"

    @pytest.mark.parametrize(
        ("ops", "received"),
        [
            ('[{"op": "delete_node", "node": "quotes"}]', "string"),
            ({"op": "delete_node", "node": "quotes"}, "object"),
        ],
    )
    async def test_wrong_type_names_the_expected_and_received_json_types(
        self, project_root: Path, ops: object, received: str
    ):
        """A gateway that encodes a container as a string, or a model that
        sends one operation instead of a batch, both land here. "has the wrong
        JSON type" gave no way to tell those apart or to correct either."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": ops}
        )

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_path"] == "dry_run_graph_edits.ops"
        assert error["validation_reason"] == "wrong_type"
        assert error["expected_types"] == ["array"]
        assert error["received_type"] == received
        assert "must be JSON array" in error["message"]
        # The string case additionally names the fix, because a stringified
        # container is not something the model can see from the type alone.
        assert ("not a JSON-encoded string" in error["message"]) is (received == "string")

    async def test_json_text_that_does_not_decode_is_located_at_its_error(self, project_root: Path):
        """A model whose provider sends arrays as JSON text cannot send the value
        itself; when its text does not decode, the error names the decoder's
        message, the character and the text around it, so the retry can correct
        that spot. Seen live: an unterminated string in a free-code step."""

        from haute.assistant._tools import build_tool_executor

        code = '# Add a column\\ndf = df.with_columns(k=pl.col(\\"v\\") / 1000)'
        ops = (
            '[{"op": "update_node", "node": "quotes", "config": {"steps": [{"id": "logic", '
            f'"kind": "free_code", "code": "{code}}}]}}}}, {{"op": "delete_node", "node": "x"}}]'
        )
        position = ops.index('op": "delete_node')

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits", {"summary": "Test plan.", "ops": ops}
        )

        error = result["error"]
        assert (error["code"], error["retryable"]) == ("invalid_request", True)
        assert error["validation_path"] == "dry_run_graph_edits.ops"
        assert error["validation_reason"] == "invalid_json_text"
        assert error["message"].startswith(
            "dry_run_graph_edits.ops is JSON text with an error at character "
            f"{position} (Expecting ',' delimiter): "
        )
        assert '1000)}]}}, {"<<here>>op": "delete_node"' in error["message"]
        assert "not a JSON-encoded string" not in error["message"]
        assert "character" in error["fix"] and "escape" in error["fix"]

    # Seen live (simplified): the edit object around a free-code step lacks its
    # "}", so "]" arrives while it is open. The code's own brackets are text.
    _MISSING_BRACE = '[{"op": "edit_steps", "edits": [{"step": {"code": "x = {\\"a\\": [1"}]}]'
    _UNTERMINATED = '[{"op": "update_node", "code": "df[\\"a\\"]}]'
    _BALANCED = '[{"op": "add_node" "node": "x"}]'

    def test_the_bracket_scan_skips_strings_and_their_escapes(self):
        from haute.assistant._tools import _JsonOpener, _scan_json_brackets

        text = self._MISSING_BRACE
        edits = text.index('[{"step"')
        scan = _scan_json_brackets(text, text.index("}]}]") + 1)
        assert scan.opened == (
            _JsonOpener("[", 0, None),
            _JsonOpener("{", 1, None),
            _JsonOpener("[", edits, "edits"),
            _JsonOpener("{", edits + 1, None),
        )
        assert scan.after_value

        # The brackets inside the string that never closes are not counted, and
        # no value ends inside it.
        scan = _scan_json_brackets(self._UNTERMINATED, len(self._UNTERMINATED))
        assert scan.opened == (_JsonOpener("[", 0, None), _JsonOpener("{", 1, None))
        assert not scan.after_value

        scan = _scan_json_brackets(self._BALANCED, len(self._BALANCED))
        assert scan.opened == () and scan.after_value

    @pytest.mark.parametrize(
        ("text", "still_open", "fix"),
        [
            (
                _MISSING_BRACE,
                'brackets still open at character 67, innermost first: "{" from character 32 '
                '(an item of "edits"), "[" from character 31 (the value of "edits"), "{" from '
                "character 1 (an item of the top-level list).",
                'Close the object opened at character 32 with "}" before the "]" at '
                "character 67, then resend the call.",
            ),
            (
                '[{"op": "add_node", "config": {"steps": []',
                'brackets still open at the end of the text, innermost first: "{" from '
                'character 30 (the value of "config"), "{" from character 1 (an item of the '
                'top-level list), "[" from character 0 (the top level).',
                'Close them at the end of the text with "}}]", then resend the call.',
            ),
            (_UNTERMINATED, None, None),
            (_BALANCED, None, None),
        ],
        ids=["missing_brace", "ends_open", "unterminated_string", "balanced"],
    )
    def test_undecodable_text_names_the_brackets_its_error_leaves_open(
        self, text: str, still_open: str | None, fix: str | None
    ):
        """Seen live: the located error alone did not show Qwen which "}" it had
        left out, and it resent the same text. A fault the brackets explain names
        the innermost three still open and how to close them; any other fault
        keeps the located message and the escaping fix. Nothing is repaired."""

        from haute.assistant._tools import (
            _refuse_undecodable_json_text,
            _ToolArgumentValidationError,
        )

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _refuse_undecodable_json_text(text, "ops")

        message = str(excinfo.value)
        assert excinfo.value.reason == "invalid_json_text"
        assert message.startswith("ops is JSON text with an error at character ")
        if still_open is None:
            assert "brackets still open" not in message
            assert excinfo.value.fields["fix"].startswith(
                "Correct the JSON text at that character and resend the call."
            )
        else:
            assert "<<here>>" in message and message.endswith(f"; {still_open}")
            assert excinfo.value.fields["fix"] == fix

    async def test_wrong_type_spells_the_json_boolean_literals_without_the_value(
        self, project_root: Path
    ):
        """A Databricks model that writes Python's `True` arrives as a string;
        naming only the type left it guessing at the literal to send."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")("find_data", {"recursive": "True"})

        error = result["error"]
        assert error["validation_reason"] == "wrong_type"
        assert error["expected_types"] == ["boolean"]
        assert "must be JSON boolean (true or false), but a string was sent" in error["message"]
        assert "True" not in error["message"]

    @pytest.mark.parametrize(("value", "text_form"), [(True, '"true"'), (3, "digits")])
    async def test_non_string_categorical_rule_value_is_refused_with_its_text_form(
        self, project_root: Path, value: object, text_form: str
    ):
        """Banding matches the column's text form, so a boolean or number rule
        would never match; the refusal says how to write the value instead."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(
            "dry_run_graph_edits",
            {
                "summary": "Band claims.",
                "ops": [
                    {
                        "op": "recipe",
                        "recipe": "categorical_banding",
                        "arguments": {
                            "source": "quotes",
                            "name": "Claims band",
                            "column": "has_claims",
                            "output_column": "claims_group",
                            "rules": [{"value": value, "assignment": "claimed"}],
                            "default": "other",
                        },
                    }
                ],
            },
        )

        error = result["error"]
        assert error["code"] == "invalid_request"
        assert error["validation_reason"] == "wrong_type"
        assert error["validation_path"] == "dry_run_graph_edits.ops[0].arguments.rules[0].value"
        assert text_form in error["message"]

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
            "inspect_node",
            {"node": invalid_value},
        )

        assert result["error"]["code"] == "invalid_request"
        assert "not-json" not in result["error"]["message"]

    async def test_the_removed_node_catalogue_tool_names_its_replacement(self, project_root: Path):
        from haute.assistant._tools import TOOL_DEFINITIONS, build_tool_executor

        assert "list_node_types" not in {definition["name"] for definition in TOOL_DEFINITIONS}
        execute_tool = build_tool_executor("main.py")
        result = await execute_tool("list_node_types", {})
        assert result["error"]["code"] == "tool_removed"
        assert result["error"]["name"] == "list_node_types"
        assert result["error"]["message"].startswith("list_node_types was removed;")
        assert "read_reference" in result["error"]["message"]

    @pytest.mark.parametrize(
        ("name", "replacement"),
        [
            ("get_node_schema", "inspect_node"),
            ("get_node_config", "inspect_node"),
            ("get_column_profiles", "inspect_node"),
            ("list_datasets", "find_data"),
            ("get_dataset_schema", "find_data"),
            ("get_capability_descriptors", "read_reference"),
            ("get_example", "read_reference"),
            ("get_authoring_guide", "read_reference"),
            ("plan_recipe", "dry_run_graph_edits"),
            ("dry_run_recipe_plan", "dry_run_graph_edits"),
        ],
    )
    async def test_a_consolidated_tool_names_what_replaces_it(
        self, project_root: Path, name: str, replacement: str
    ):
        """A resumed session's history can still name a tool the task-shaped
        surface consolidated."""

        from haute.assistant._tools import build_tool_executor

        result = await build_tool_executor("main.py")(name, {})

        assert result["error"]["code"] == "tool_removed"
        assert replacement in result["error"]["message"]

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
                "summary": "Test plan.",
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ],
            },
        )
        assert len(dry_run["plan_hash"]) == 64
        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert dry_run["changes"]["nodes"] == [
            {"id": "renamed", "type": "Transform", "change": "renamed", "renamed_from": "enriched"}
        ]

        refused = await execute_tool(
            "apply_graph_plan",
            {"plan_hash": dry_run["plan_hash"]},
        )
        assert refused["error"]["code"] == "authority_denied"

    async def test_rename_with_a_coded_consumer_fails_at_dry_run(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py", session_id="session-1")
        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [{"op": "rename_node", "node": "quotes", "new_name": "policies"}],
            },
        )

        error = result["error"]
        assert error["code"] == "rename_has_consumers"
        assert error["consumers"] == [{"node": "enriched", "field": "code"}]
        assert "'enriched' code" in error["message"]

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
            {"summary": "Test plan.", "ops": [{"op": "delete_node", "node": "enriched"}]},
        )

        assert "risk" not in dry_run
        assert "confirmation_required" not in dry_run
        assert shared_store.get(dry_run["plan_hash"]).plan_hash == dry_run["plan_hash"]


def _revision_sources(plan: Mapping[str, object]) -> dict[str, str]:
    """The revision sources a dry-run's stored plan is bound to."""

    return dict(_stored_plan(plan).source_manifest)


class TestExecutorArms:
    async def test_every_read_tool_dispatches_through_the_executor(self, project_root: Path):
        from haute.assistant._assets import example_index
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        inspected = await execute_tool("inspect_node", {"node": "quotes", "parts": ["config"]})
        assert "config" in inspected["config"]
        found = await execute_tool(
            "find_data", {"directory": "data", "path": "data/quotes.parquet"}
        )
        assert found["datasets"][0]["name"] == "quotes.parquet"
        assert "columns" in found["schema"]
        knowledge = await execute_tool(
            "get_project_knowledge",
            {"query": "pipeline", "limit": 1},
        )
        assert "items" in knowledge
        plan = await execute_tool(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ],
            },
        )
        assert "schema:data/quotes.parquet" in _revision_sources(plan)
        references = await execute_tool(
            "read_reference", {"ids": [f"example:{example_index()[0][0]}", "guide"]}
        )
        example, guide = (item["content"] for item in references["references"])
        assert "graph" in example
        assert "node" in guide["content"].casefold()

    async def test_dry_run_rejects_dataset_schema_changed_after_retrieval(
        self,
        project_root: Path,
    ):
        from haute.assistant._tools import build_tool_executor

        execute_tool = build_tool_executor("main.py")
        schema = await execute_tool("find_data", {"path": "data/quotes.parquet"})
        assert "source_digest" in schema["schema"]
        pl.DataFrame({"replacement": [1]}).write_parquet(project_root / "data" / "quotes.parquet")

        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ],
            },
        )

        assert result["error"]["code"] == "stale_project_evidence"
        assert "data/quotes.parquet" in result["error"]["message"]

    async def test_a_renamed_dataset_blocks_planning_only_until_datasets_are_listed(
        self, project_root: Path
    ):
        from haute.assistant._tools import build_tool_executor

        pl.DataFrame({"id": [1]}).write_parquet(project_root / "data" / "extra.parquet")
        execute_tool = build_tool_executor("main.py")
        await execute_tool("find_data", {"path": "data/extra.parquet"})
        (project_root / "data" / "extra.parquet").rename(project_root / "data" / "moved.parquet")
        rename = {
            "summary": "Rename enriched.",
            "ops": [{"op": "rename_node", "node": "enriched", "new_name": "renamed"}],
        }

        blocked = await execute_tool("dry_run_graph_edits", rename)
        assert blocked["error"]["code"] == "project_source_missing"
        assert "data/extra.parquet" in blocked["error"]["message"]

        await execute_tool("find_data", {"directory": "data"})
        plan = await execute_tool("dry_run_graph_edits", rename)

        assert "error" not in plan, plan
        assert "schema:data/extra.parquet" not in _revision_sources(plan)

    @pytest.mark.parametrize("change", ["rewritten", "renamed"])
    async def test_a_follow_up_dry_run_releases_evidence_an_earlier_turn_saw_go_stale(
        self, project_root: Path, change: str
    ):
        """The model no longer sees an earlier turn's schema result, so a dataset
        that changed since never fails the next turn's dry-run."""

        from haute.assistant._ops import SourceEvidenceLedger
        from haute.assistant._tools import build_tool_executor

        dataset = project_root / "data" / "extra.parquet"
        pl.DataFrame({"id": [1]}).write_parquet(dataset)
        ledger = SourceEvidenceLedger()
        first_turn = build_tool_executor("main.py", evidence=ledger)
        await first_turn("find_data", {"path": "data/extra.parquet"})
        if change == "rewritten":
            pl.DataFrame({"id": [1, 2]}).write_parquet(dataset)
        else:
            dataset.rename(project_root / "data" / "moved.parquet")

        next_turn = build_tool_executor("main.py", evidence=ledger)
        plan = await next_turn(
            "dry_run_graph_edits",
            {
                "summary": "Rename enriched.",
                "ops": [{"op": "rename_node", "node": "enriched", "new_name": "renamed"}],
            },
        )

        assert "error" not in plan, plan
        assert "schema:data/extra.parquet" not in _revision_sources(plan)
        assert ledger.sources() == ()

    async def test_a_new_excel_input_is_refused_with_the_preview_remedy(self, project_root: Path):
        from haute.assistant._tools import build_tool_executor

        use_project(project_root)  # restored by the autouse _restore_project_root
        execute_tool = build_tool_executor("main.py")
        result = await execute_tool(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {
                        "op": "add_node",
                        "node_type": "dataInput",
                        "name": "book",
                        "ref": "book",
                        "config": {
                            "input_type": "file",
                            "format": "excel",
                            "mode": "read",
                            "path": "book.xlsx",
                        },
                    },
                    {"op": "add_node", "node_type": "explore", "name": "look", "ref": "look"},
                    {"op": "add_edge", "source": "$book", "target": "$look"},
                ],
            },
        )

        assert result["error"]["code"] == "schema_unresolvable"
        assert "format 'excel' reads only eagerly" in result["error"]["message"]
        assert "Preview this input first" in result["error"]["message"]

    async def test_a_schema_an_earlier_turn_saw_binds_the_next_turn_s_plan(
        self,
        project_root: Path,
    ):
        """The session's ledger carries the evidence, not the provider history."""

        from haute.assistant._ops import SourceEvidenceLedger
        from haute.assistant._tools import build_tool_executor

        ledger = SourceEvidenceLedger()
        first_turn = build_tool_executor("main.py", evidence=ledger)
        await first_turn("find_data", {"path": "data/quotes.parquet"})
        second_turn = build_tool_executor("main.py", evidence=ledger)

        plan = await second_turn(
            "dry_run_graph_edits",
            {
                "summary": "Test plan.",
                "ops": [
                    {
                        "op": "rename_node",
                        "node": "enriched",
                        "new_name": "renamed",
                    }
                ],
            },
        )

        assert "schema:data/quotes.parquet" in _revision_sources(plan)


class TestClosedSchemaKeywords:
    def test_max_length_rejects_long_strings(self):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        schema = {"type": "string", "maxLength": 3}
        _validate_tool_value("abc", schema, path="tool.field")

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value("abcd", schema, path="tool.field")

        assert excinfo.value.path == "tool.field"
        assert excinfo.value.reason == "too_long"
        assert str(excinfo.value) == "tool.field is too long: at most 3 characters"

    @pytest.mark.parametrize(
        ("schema", "value", "message"),
        [
            ({"type": "integer", "maximum": 10}, 12, "tool.field is above its maximum of 10"),
            ({"type": "integer", "minimum": 1}, 0, "tool.field is below its minimum of 1"),
            (
                {"type": "array", "maxItems": 2},
                [1, 2, 3],
                "tool.field has too many items: at most 2",
            ),
            ({"type": "array", "minItems": 1}, [], "tool.field has too few items: at least 1"),
            ({"type": "string", "minLength": 2}, "a", "tool.field is too short: at least 2"),
        ],
    )
    def test_a_bound_rejection_states_the_bound(self, schema, value, message):
        from haute.assistant._tools import _ToolArgumentValidationError, _validate_tool_value

        with pytest.raises(_ToolArgumentValidationError) as excinfo:
            _validate_tool_value(value, schema, path="tool.field")

        assert str(excinfo.value).startswith(message)

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

    def test_unique_items_rejects_non_finite_json_members(self):
        from haute.assistant._tools import _validate_tool_value

        with pytest.raises(ValueError, match="Out of range float values"):
            _validate_tool_value(
                [float("nan"), float("nan")],
                {"type": "array", "uniqueItems": True},
                path="tool.items",
            )

    def test_unique_items_is_inactive_unless_declared(self):
        from haute.assistant._tools import _validate_tool_value

        _validate_tool_value(["a", "a"], {"type": "array"}, path="tool.items")
