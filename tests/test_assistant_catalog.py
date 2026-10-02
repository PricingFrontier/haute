"""Tests for the assistant node catalog (``haute.assistant._catalog``).

Spec: specs/assistant/low-level.md — `_catalog.py` row and § Testing:
completeness against ``NodeType`` (the ``validate_registry_complete``
pattern applied to the catalog) and agreement of every mechanical fact
with the canonical registries — the catalog must never become a second
source of truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import VALID_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.assistant import _catalog
from haute.assistant._catalog import (
    NodeCapabilityDescriptor,
    capability_manifest,
    compact_manifest,
    validate_manifest_complete,
)
from haute.routes._save_pipeline import _SINGLETON_NODE_TYPES


class TestNodeDescriptors:
    """The manifest is the one node catalogue; its facts come from the registries."""

    @staticmethod
    def _nodes() -> dict[str, NodeCapabilityDescriptor]:
        return {descriptor.id: descriptor for descriptor in capability_manifest().nodes}

    def test_every_node_type_has_a_descriptor_with_a_usage_note(self):
        nodes = self._nodes()
        assert set(nodes) == {node_type.value for node_type in NodeType}
        for node_id, descriptor in nodes.items():
            assert descriptor.usage.strip(), f"{node_id} has no usage note"

    def test_a_missing_usage_note_fails_manifest_validation(self, monkeypatch: pytest.MonkeyPatch):
        notes = dict(_catalog._USAGE_NOTES)
        del notes[NodeType.POLARS]
        monkeypatch.setattr(_catalog, "_USAGE_NOTES", notes)
        with pytest.raises(RuntimeError, match="polars"):
            validate_manifest_complete()

    def test_an_unexpected_usage_note_fails_manifest_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        notes = {**_catalog._USAGE_NOTES, "not-a-node-type": "stray"}
        monkeypatch.setattr(_catalog, "_USAGE_NOTES", notes)
        with pytest.raises(RuntimeError, match="Unexpected"):
            validate_manifest_complete()

    def test_decorators_agree_with_the_type_registry(self):
        for node_type in NodeType:
            descriptor = self._nodes()[node_type.value]
            assert descriptor.decorator == NODE_TYPE_TO_DECORATOR.get(node_type), node_type

    def test_sidecar_folders_agree_with_config_io(self):
        for node_type in NodeType:
            descriptor = self._nodes()[node_type.value]
            assert descriptor.config_folder == NODE_TYPE_TO_FOLDER.get(node_type), node_type

    def test_config_fields_agree_with_the_validation_allowlist(self):
        for node_type in NodeType:
            descriptor = self._nodes()[node_type.value]
            fields = {*descriptor.required_fields, *descriptor.optional_fields}
            assert fields == set(VALID_KEYS.get(node_type, ())), node_type

    def test_singleton_flags_agree_with_the_save_service(self):
        singleton_types = {node_type for node_type, _label in _SINGLETON_NODE_TYPES}
        for node_type in NodeType:
            descriptor = self._nodes()[node_type.value]
            assert descriptor.singleton == (node_type in singleton_types), node_type

    def test_the_legacy_catalogue_is_gone(self):
        for name in ("NODE_CATALOG", "NodeCatalogEntry", "render_catalog"):
            assert not hasattr(_catalog, name), name


class TestCapabilityManifest:
    def test_identity_and_node_completeness(self):
        manifest = capability_manifest()
        dumped = manifest.as_dict()

        assert dumped["schema_version"] == "1.0"
        assert isinstance(dumped["haute_version"], str) and dumped["haute_version"]
        assert re.fullmatch(r"[0-9a-f]{64}", dumped["capability_hash"])
        assert {node["id"] for node in dumped["nodes"]} == {
            node_type.value for node_type in NodeType
        }
        assert dumped["installed_capabilities"]["io"]["schema_version"] == 1
        operation_ids = [operation["id"] for operation in dumped["operations"]]
        assert operation_ids == [
            "get_pipeline",
            "inspect_node",
            "find_data",
            "read_reference",
            "get_project_knowledge",
            "dry_run_graph_edits",
            "apply_graph_plan",
            "update_build_plan",
        ]

    def test_hash_is_sha256_of_canonical_material(self):
        manifest = capability_manifest()
        material = manifest.as_dict()
        reported = material.pop("capability_hash")
        canonical = json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()

        assert hashlib.sha256(canonical).hexdigest() == reported

    def test_manifest_cache_reuses_identity_and_invalidates_on_installed_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _catalog._clear_manifest_cache()
        first = capability_manifest()
        second = capability_manifest()
        assert first is second

        original = _catalog._installed_capabilities

        def changed_capabilities():
            changed = original()
            return {**changed, "test_engine": {"available": True}}

        monkeypatch.setattr(_catalog, "_installed_capabilities", changed_capabilities)
        changed = capability_manifest()
        assert changed is not first
        assert changed.capability_hash != first.capability_hash

    def test_cached_manifest_material_is_immutable(self):
        manifest = capability_manifest()

        with pytest.raises(TypeError):
            manifest.installed_capabilities["new"] = True
        with pytest.raises(TypeError):
            manifest.nodes[0].config_schema["new"] = True

    def test_compact_manifest_is_an_index_not_a_second_descriptor_copy(self):
        compact = compact_manifest(capability_manifest())

        assert set(compact) == {
            "schema_version",
            "haute_version",
            "capability_hash",
            "installed_capabilities",
            "feature_flags",
            "node_index",
            "operation_index",
            "recipe_index",
        }
        assert set(compact["node_index"][0]) == {"id", "display_name", "decorator", "summary"}
        assert "config_schema" not in compact["node_index"][0]
        assert {item["id"] for item in compact["recipe_index"]} == {
            "categorical_banding",
            "reference_join",
            "response_output",
            "rating_step",
        }


class TestResolvedDescriptors:
    def test_every_config_schema_is_closed_and_keys_match_runtime_allowlist(self):
        manifest = capability_manifest()
        by_id = {node.id: node for node in manifest.nodes}

        for node_type in NodeType:
            descriptor = by_id[node_type.value]
            schema = descriptor.config_schema
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False
            assert set(schema["properties"]) == set(VALID_KEYS.get(node_type, ()))

    def test_discriminated_io_branches_and_nested_types_are_resolved(self):
        manifest = capability_manifest()
        by_id = {node.id: node for node in manifest.nodes}

        data_input = by_id[NodeType.DATA_INPUT.value].config_schema
        assert len(data_input["oneOf"]) == 5
        assert any(
            branch["properties"]["inputType"].get("const") == "databricks"
            for branch in data_input["oneOf"]
        )

        banding = by_id[NodeType.BANDING.value].config_schema
        assert banding["properties"]["factors"]["type"] == "array"
        assert banding["properties"]["factors"]["items"]["type"] == "object"
        thawed_banding = by_id[NodeType.BANDING.value].as_dict()["config_schema"]
        default_schema = thawed_banding["properties"]["factors"]["items"]["properties"]["default"]
        assert default_schema == {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ]
        }

    def test_node_descriptors_include_the_complete_closed_contract(self):
        required = {
            "id",
            "display_name",
            "decorator",
            "config_schema",
            "required_fields",
            "optional_fields",
            "defaults",
            "enum_values",
            "conditional_branches",
            "cross_field_constraints",
            "config_folder",
            "singleton",
            "sidecar_behavior",
            "summary",
            "ports",
            "input_cardinality",
            "wiring_rules",
            "schema_effect",
            "execution",
            "side_effects",
            "usage",
            "anti_patterns",
            "examples",
            "recipes",
            "errors",
            "step_authoring",
            "card",
        }

        for node in capability_manifest().nodes:
            dumped = node.as_dict()
            assert set(dumped) == required
            assert dumped["usage"]
            assert dumped["wiring_rules"]
            assert dumped["errors"]

    def test_node_semantics_are_type_specific_and_teaching_links_are_real(self):
        by_id = {node.id: node for node in capability_manifest().nodes}

        assert by_id["dataInput"].input_cardinality == "zero"
        assert by_id["edgeJoin"].input_cardinality == "exactly two"
        assert "scenario" in by_id["liveSwitch"].ports["inputs"]
        assert by_id["modelling"].execution.startswith("explicit long-running")
        assert by_id["dataOutput"].side_effects.startswith("writes")
        assert by_id["banding"].examples == ("discrete_banding",)
        assert by_id["banding"].recipes == ("categorical_banding",)
        assert by_id["dataInput"].recipes == ()
        assert by_id["edgeJoin"].recipes == ("reference_join",)
        assert by_id["polars"].recipes == ()
        assert by_id["output"].recipes == ("response_output",)

        assert all(
            any("connected" in anti_pattern for anti_pattern in node.anti_patterns)
            for node in by_id.values()
        )
        assert any(
            "df" in anti_pattern and "discard" in anti_pattern
            for anti_pattern in by_id["polars"].anti_patterns
        )
        assert any(
            "pivots entry on the Explore node, never a step" in anti_pattern
            for anti_pattern in by_id["explore"].anti_patterns
        )

    def test_node_descriptors_serve_their_card_without_its_test_fixture(self):
        from haute.assistant._node_cards import CARD_CONFIG_NAMES

        by_id = {node.id: node.as_dict() for node in capability_manifest().nodes}

        for node_id, descriptor in by_id.items():
            card = descriptor["card"]
            assert card["node_type"] == node_id
            assert "fixture" not in card
            if card["authorable"]:
                assert [config["name"] for config in card["configs"]] == list(CARD_CONFIG_NAMES)
                assert card["fields"]
            else:
                assert "not authorable" in card["note"].lower()
        banding = by_id["banding"]["card"]["configs"][1]["config"]["factors"]
        assert {"boundary": "2024-06-30", "label": "2024 H1"} in banding[1]["rules"]
        assert banding[0]["rules"][-1]["boundary"] == ""
        assert {"value": "01", "assignment": "London"} in banding[2]["rules"]

    def test_every_operation_has_a_plain_words_activity_title(self):
        from haute.assistant._catalog import capability_manifest, tool_title

        for descriptor in capability_manifest().operations:
            title = tool_title(descriptor.id, {})
            assert title != descriptor.id and title[0].isupper(), descriptor.id
        ops = {"ops": [{}, {}, {}]}
        assert tool_title("dry_run_graph_edits", ops) == "Checking 3 changes"
        assert tool_title("dry_run_graph_edits", ops, {"operations": 1}) == "Checking 1 change"
        assert tool_title("dry_run_graph_edits", ops, {"error": {}}) == "Checking the plan"
        assert tool_title("apply_graph_plan", {"plan_hash": "a"}) == "Applying the plan"
        assert tool_title("apply_graph_plan", {}, {"applied_operations": 3}) == "Applying 3 changes"
        assert tool_title("update_build_plan", {"complete": "rating"}) == "Updating the checklist"
        assert tool_title("no_such_tool", {}) == "no_such_tool"

    def test_operation_descriptors_are_closed_and_policy_complete(self):
        required = {
            "id",
            "version",
            "description",
            "input_schema",
            "output_schema",
            "state_access",
            "project_state",
            "revision_semantics",
            "risk",
            "egress",
            "side_effects",
            "cost",
            "idempotency",
            "retry",
            "cancellable",
            "cacheable",
            "parallel_safe",
            "concurrency_group",
            "ordering",
            "limits",
            "errors",
        }

        for operation in capability_manifest().operations:
            dumped = operation.as_dict()
            assert set(dumped) == required
            assert dumped["input_schema"]["additionalProperties"] is False
            assert dumped["output_schema"]["additionalProperties"] is False
            assert set(dumped["output_schema"]["required"]) == {
                "capability_hash",
                "operation_version",
            }
            assert len(dumped["output_schema"]["oneOf"]) == 2
            assert all(branch.get("required") for branch in dumped["output_schema"]["oneOf"])
            assert dumped["version"] == "1.0"
            assert dumped["limits"]["timeout_seconds"] > 0
            assert dumped["limits"]["max_operations"] > 0
            assert dumped["limits"]["max_payload_bytes"] > 0
            assert dumped["limits"]["max_context_bytes"] > 0
            assert dumped["errors"]

        by_id = {operation.id: operation for operation in capability_manifest().operations}
        dry_run_errors = {error["code"] for error in by_id["dry_run_graph_edits"].errors}
        apply_errors = {error["code"] for error in by_id["apply_graph_plan"].errors}

        assert {
            "invalid_ops",
            "invalid_plan",
            "unknown_recipe",
            "recipe_argument_invalid",
        } <= dry_run_errors
        assert "egress_policy_denied" in {error["code"] for error in by_id["inspect_node"].errors}
        assert "unknown_reference" in {error["code"] for error in by_id["read_reference"].errors}
        lexical_error_codes = {
            "material_input_required",
            "recipe_name_mismatch",
            "recipe_route_mismatch",
            "recipe_route_required",
        }
        for error_codes in (dry_run_errors, apply_errors):
            assert lexical_error_codes.isdisjoint(error_codes)
        # Each recipe is one closed operation branch, selected by `op` then `recipe`,
        # whose arguments are the recipe's own closed argument schema.
        branches = by_id["dry_run_graph_edits"].input_schema["properties"]["ops"]["items"]["oneOf"]
        recipe_branches = [
            branch for branch in branches if branch["properties"]["op"].get("const") == "recipe"
        ]
        assert {branch["properties"]["recipe"]["const"] for branch in recipe_branches} == {
            "categorical_banding",
            "reference_join",
            "response_output",
            "rating_step",
        }
        categorical = next(
            branch
            for branch in recipe_branches
            if branch["properties"]["recipe"]["const"] == "categorical_banding"
        )
        assert categorical["required"] == ("op", "recipe", "arguments")
        assert categorical["additionalProperties"] is False
        arguments = categorical["properties"]["arguments"]
        assert "rules" in arguments["required"]
        assert "output_name" in arguments["properties"]
        assert arguments["additionalProperties"] is False
        assert arguments["description"].startswith(
            "categorical_banding arguments: source, name, column, output_column, "
            "rules [{value, assignment}], default; optional output_name, output_columns"
        )
        # The recipe's ref is add_node's, so the provider projection keeps one description.
        add_node = next(
            branch for branch in branches if branch["properties"]["op"].get("const") == "add_node"
        )
        assert categorical["properties"]["ref"] == add_node["properties"]["ref"]
        assert {
            "plan_aborted",
            "plan_already_applied",
            "unknown_plan_item",
        } <= apply_errors
        # The build plan's update changes session state only: never the project.
        plan = by_id["update_build_plan"].as_dict()
        assert {error["code"] for error in plan["errors"]} >= {
            "empty_plan_update",
            "duplicate_plan_item",
            "unknown_plan_item",
            "plan_item_unsaved",
        }
        assert (
            plan["state_access"],
            plan["side_effects"],
            plan["egress"],
            plan["parallel_safe"],
            plan["ordering"],
        ) == ("session", "session build plan", "none", False, "ordered")
        assert by_id["apply_graph_plan"].input_schema["required"] == ("plan_hash",)
        assert "item" in by_id["apply_graph_plan"].input_schema["properties"]

    def test_graph_edit_provider_schema_is_derived_from_wire_models(self):
        from haute.assistant._wire_ops import (
            AddEdgeOp,
            AddNodeOp,
            DeleteEdgeOp,
            DeleteNodeOp,
            EditStepsOp,
            RenameNodeOp,
            UpdateNodeOp,
            UpdatePreambleOp,
            graph_edit_operations_schema,
        )

        models = (
            AddNodeOp,
            UpdateNodeOp,
            EditStepsOp,
            RenameNodeOp,
            DeleteNodeOp,
            AddEdgeOp,
            DeleteEdgeOp,
            UpdatePreambleOp,
        )
        schema = graph_edit_operations_schema()
        branches = schema["items"]["oneOf"]

        assert schema["maxItems"] == 100
        assert len(branches) == len(models)
        for model in models:
            discriminator = model.model_fields["op"].default
            branch = next(
                item for item in branches if item["properties"]["op"]["const"] == discriminator
            )
            assert set(branch["properties"]) == set(model.model_fields)
            assert set(branch["required"]) == {
                "op",
                *(name for name, field in model.model_fields.items() if field.is_required()),
            }
            assert branch["additionalProperties"] is False

        add_node = next(
            item for item in branches if item["properties"]["op"]["const"] == "add_node"
        )
        assert add_node["properties"]["node_type"]["enum"] == [
            node_type.value for node_type in NodeType
        ]

    def test_wire_descriptions_are_model_prose_and_state_update_merge_rules(self) -> None:
        from haute.assistant._wire_ops import graph_edit_operations_schema

        schema = graph_edit_operations_schema()

        def descriptions(value: object) -> list[str]:
            if isinstance(value, dict):
                found = [value["description"]] if isinstance(value.get("description"), str) else []
                return found + [text for child in value.values() for text in descriptions(child)]
            if isinstance(value, list):
                return [text for child in value for text in descriptions(child)]
            return []

        # A Python class docstring (RST markup, hard-wrapped lines) must never
        # reach the provider-visible schema.
        for text in descriptions(schema):
            assert "``" not in text and "\n" not in text, text

        branches = {item["properties"]["op"]["const"]: item for item in schema["items"]["oneOf"]}
        assert "node index" in branches["add_node"]["properties"]["node_type"]["description"]
        update_config = branches["update_node"]["properties"]["config"]["description"]
        assert "replaces" in update_config and "null" in update_config


def test_polars_usage_names_inputs_by_edge_without_input_mapping() -> None:
    # A stepped Transform rejects inputMapping, so the descriptor must not teach it.
    usage = _descriptors()["polars"].usage
    assert "inputMapping" not in usage
    assert "upstream node" in usage


def test_every_text_that_names_inputs_states_the_one_naming_rule() -> None:
    """An edge from a Quote Input frame or a submodel output is not named by
    its node; a model told otherwise invented frame names (live transcript)."""

    from importlib import resources

    from haute.assistant._catalog import INPUT_NAMING_RULE
    from haute.assistant._loop import build_system_prompt
    from haute.assistant._node_cards import node_card

    def flat(text: str) -> str:
        return " ".join(text.split())

    guide = (
        resources.files("haute.assistant")
        .joinpath("assets", "authoring_guide.md")
        .read_text(encoding="utf-8")
    )
    assert INPUT_NAMING_RULE in _descriptors()["polars"].usage
    assert INPUT_NAMING_RULE in build_system_prompt(source_file="main.py")
    assert INPUT_NAMING_RULE in flat(guide)
    for node_type in (NodeType.POLARS, NodeType.OUTPUT, NodeType.LIVE_SWITCH):
        fields = node_card(node_type)["fields"]
        assert INPUT_NAMING_RULE in fields["input names"], node_type
        assert not any("(the upstream node's name)" in text for text in fields.values())


class TestStepAuthoring:
    """Stepped descriptors say how steps start, what they see and how new
    logic is written, read from the step builder's surface table."""

    def test_it_follows_the_stepped_surface_table(self) -> None:
        from haute._polars_steps import STEP_KINDS, STEPPED_NODE_TYPES

        for node_type in NodeType:
            authoring = _descriptors()[node_type.value].as_dict()["step_authoring"]
            surface = STEPPED_NODE_TYPES.get(node_type)
            if surface is None:
                assert authoring is None, node_type
                continue
            assert (authoring["start"], authoring["inputs"]) == (surface.start, surface.inputs)
            steps = authoring["new_logic"]
            assert [step["kind"] for step in steps] == (
                ["source", "free_code"] if surface.start == "input" else ["free_code"]
            )
            assert all(isinstance(step["id"], str) and step["id"] for step in steps)
            assert {step["kind"] for step in steps} <= set(STEP_KINDS)
            assert steps[-1]["code"].startswith("# ")
            assert ("steps: []" in authoring["rule"]) == (surface.start == "frame")
            assert ("edge names" in authoring["rule"]) == (surface.inputs == "edges")

    def test_the_guide_carries_the_renderer_s_step_grammar(self) -> None:
        from haute._polars_steps import STEP_KINDS, PolarsStepError, validate_polars_steps
        from haute.assistant._tools import read_reference

        (guide,) = read_reference(["guide"])["references"]
        grammar = guide["content"]["step_grammar"]

        assert list(grammar["kinds"]) == list(STEP_KINDS)
        for kind, fields in grammar["kinds"].items():
            if not fields["required"]:
                continue
            with pytest.raises(PolarsStepError) as excinfo:
                validate_polars_steps([{"id": "a", "kind": kind}])
            assert str(fields["required"]) in str(excinfo.value)
        json.dumps(grammar)


def test_edge_join_descriptor_teaches_strict_role_handles() -> None:
    from haute.assistant._catalog import capability_manifest

    descriptor = next(node for node in capability_manifest().nodes if node.id == "edgeJoin")

    assert 'target_handle="base"' in descriptor.wiring_rules
    assert 'target_handle="join"' in descriptor.wiring_rules


def test_banding_descriptor_exposes_canonical_type_enum() -> None:
    from haute.assistant._catalog import capability_manifest

    descriptor = next(node for node in capability_manifest().nodes if node.id == "banding")
    factor_schema = descriptor.as_dict()["config_schema"]["properties"]["factors"]["items"]

    assert factor_schema["properties"]["banding"]["enum"] == [
        "breakpoints",
        "categorical",
    ]


# The editor's own declarations, read from source: the catalogue mirrors them.
_NODE_TYPES_TS = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "nodeTypes.ts"
).read_text(encoding="utf-8")


def _editor_set(name: str) -> set[str]:
    match = re.search(rf"export const {name} = new Set<\w+>\(\[(.*?)\]\)", _NODE_TYPES_TS, re.S)
    assert match, f"nodeTypes.ts declares no {name}"
    return {NodeType[member].value for member in re.findall(r"NODE_TYPES\.(\w+)", match.group(1))}


def _editor_meta(field: str) -> dict[str, str]:
    """One field of each ``NODE_TYPE_META`` row, keyed by node type value."""
    return {
        NodeType[member].value: value.strip()
        for member, value in re.findall(
            rf'^\s+\[NODE_TYPES\.(\w+)\]:[^\n]*?\b{field}: "?([^",}}]+)"?', _NODE_TYPES_TS, re.M
        )
    }


def _descriptors() -> dict[str, NodeCapabilityDescriptor]:
    return {descriptor.id: descriptor for descriptor in capability_manifest().nodes}


class TestRegistryFacts:
    """Source, sink, pass-through and cardinality come from the product's registries."""

    def test_sources_are_the_standalone_source_types_and_the_editor_agrees(self) -> None:
        from haute._standalone_nodes import SOURCE_NODE_TYPES

        zero_input = {
            node_id
            for node_id, descriptor in _descriptors().items()
            if descriptor.input_cardinality == "zero"
        }
        assert zero_input == {node_type.value for node_type in SOURCE_NODE_TYPES}
        assert _editor_set("SOURCE_ONLY_TYPES") == zero_input

    def test_sinks_are_the_sink_only_types_and_the_editor_agrees(self) -> None:
        from haute._types import SINK_ONLY_NODE_TYPES

        no_output = {
            node_id
            for node_id, descriptor in _descriptors().items()
            if descriptor.ports["outputs"] == ()
        }
        assert no_output == {node_type.value for node_type in SINK_ONLY_NODE_TYPES}
        assert _editor_set("SINK_ONLY_TYPES") == no_output
        assert no_output == {"output", "dataOutput", "explore", "modelling", "optimiser"}

    def test_single_input_types_are_the_palette_max_one_types(self) -> None:
        single = {
            node_id
            for node_id, descriptor in _descriptors().items()
            if descriptor.input_cardinality == "exactly one"
        }
        max_inputs = _editor_meta("maxInputs")
        assert single == {node_id for node_id, value in max_inputs.items() if value == "1"}
        assert max_inputs["edgeJoin"] == "2"
        assert _descriptors()["edgeJoin"].input_cardinality == "exactly two"
        assert "ratingStep" in single

    def test_a_load_file_with_an_incoming_edge_validates_against_its_descriptor(self) -> None:
        from haute._standalone_nodes import STANDALONE_PASSTHROUGH_TYPES

        load_file = _descriptors()["externalFile"].as_dict()

        assert NodeType.EXTERNAL_FILE in STANDALONE_PASSTHROUGH_TYPES
        assert load_file["input_cardinality"] == "zero or more"
        assert "`df`" in load_file["ports"]["inputs"]
        assert load_file["ports"]["outputs"] == ["frame"]
        assert "`obj`" in load_file["usage"]
        assert "first input" in load_file["schema_effect"]
        assert "no upstream" not in load_file["wiring_rules"].lower()

    def test_apply_optimisation_takes_several_inputs_chosen_by_ratebook_input(self) -> None:
        apply = _descriptors()["optimiserApply"]

        assert apply.input_cardinality == "one or more, subject to the descriptor configuration"
        assert "ratebook_input" in apply.wiring_rules

    def test_every_node_type_has_an_explicit_input_cardinality(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_catalog, "_SINGLE_INPUT_TYPES", frozenset())
        with pytest.raises(RuntimeError, match="input cardinality"):
            _catalog._input_cardinality(NodeType.BANDING)


class TestPaletteFacts:
    def test_display_names_are_the_palette_names(self) -> None:
        names = _editor_meta("name")

        assert {node_id: d.display_name for node_id, d in _descriptors().items()} == names
        assert names["externalFile"] == "Load File"
        assert names["liveSwitch"] == "Source Switch"

    def test_defaults_are_the_palette_defaults(self) -> None:
        from haute._config_io import palette_default_config

        for node_type in NodeType:
            descriptor = _descriptors()[node_type.value].as_dict()
            assert descriptor["defaults"] == palette_default_config(node_type)
        assert _descriptors()["scenarioExpander"].as_dict()["defaults"]["stepCount"] == 21

    def test_the_node_index_names_each_type_with_its_palette_name_and_purpose(self) -> None:
        from haute.assistant._loop import build_system_prompt

        index = compact_manifest(capability_manifest())["node_index"]
        load_file = next(entry for entry in index if entry["id"] == "externalFile")
        assert load_file["display_name"] == "Load File"
        assert load_file["summary"] != _descriptors()["externalFile"].usage

        prompt = build_system_prompt(source_file="p.py")
        assert f"- `externalFile` (Load File): {load_file['summary']}" in prompt
        assert "- `liveSwitch` (Source Switch): " in prompt


class TestIoBranches:
    def test_data_input_and_output_enums_merge_every_branch(self) -> None:
        data_input = _descriptors()["dataInput"]
        data_output = _descriptors()["dataOutput"]

        assert data_input.as_dict()["enum_values"]["inputType"] == [
            "file",
            "database",
            "lakehouse",
            "databricks",
            "inline",
        ]
        assert data_input.config_schema["properties"]["inputType"]["enum"] == (
            "file",
            "database",
            "lakehouse",
            "databricks",
            "inline",
        )
        # The file branch accepts any installed format, so format is not closed.
        assert "format" not in data_input.enum_values
        assert data_output.as_dict()["enum_values"]["outputType"] == [
            "file",
            "database",
            "lakehouse",
        ]
        assert data_input.required_fields == ("inputType",)
        assert data_output.required_fields == ("format", "outputType")


class TestUsageNotes:
    def test_notes_name_real_fields_and_shapes(self) -> None:
        nodes = _descriptors()

        expander = nodes["scenarioExpander"].usage
        assert "stepCount" in expander
        assert "source column" not in expander
        for field in ("column_name", "min_value", "max_value", "step_column"):
            assert field in expander

        banding = nodes["banding"].usage
        assert "date" in banding
        assert "{boundary, label}" in banding
        assert "{value, assignment}" in banding

        optimiser = nodes["optimiser"].usage
        assert "scored" not in optimiser
        assert "data_input" in optimiser

        assert "first input" in nodes["ratingStep"].usage
