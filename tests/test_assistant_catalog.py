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

import pytest

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import VALID_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.assistant import _catalog
from haute.assistant._catalog import (
    NODE_CATALOG,
    capability_manifest,
    compact_manifest,
    render_catalog,
    validate_catalog_complete,
)
from haute.routes._save_pipeline import _SINGLETON_NODE_TYPES


class TestCompleteness:
    def test_every_node_type_has_an_entry(self):
        assert set(NODE_CATALOG.keys()) == set(NodeType)

    def test_every_entry_has_a_hand_authored_usage_note(self):
        for node_type, entry in NODE_CATALOG.items():
            assert entry.usage_note.strip(), f"{node_type.value} has no usage note"

    def test_validate_catalog_complete_passes_on_the_real_catalog(self):
        validate_catalog_complete()

    def test_validate_catalog_complete_raises_on_a_missing_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        depleted = dict(NODE_CATALOG)
        removed = depleted.pop(NodeType.POLARS)
        assert removed is not None
        monkeypatch.setattr(_catalog, "NODE_CATALOG", depleted)
        with pytest.raises(RuntimeError, match="polars"):
            validate_catalog_complete()


class TestFactAgreement:
    def test_decorators_agree_with_the_type_registry(self):
        for node_type, entry in NODE_CATALOG.items():
            assert entry.decorator == NODE_TYPE_TO_DECORATOR.get(node_type), node_type

    def test_sidecar_folders_agree_with_config_io(self):
        for node_type, entry in NODE_CATALOG.items():
            assert entry.config_folder == NODE_TYPE_TO_FOLDER.get(node_type), node_type

    def test_config_keys_agree_with_the_validation_allowlist(self):
        for node_type, entry in NODE_CATALOG.items():
            allowed = VALID_KEYS.get(node_type)
            expected = tuple(sorted(allowed)) if allowed is not None else ()
            assert tuple(sorted(entry.config_keys)) == expected, node_type

    def test_singleton_flags_agree_with_the_save_service(self):
        singleton_types = {node_type for node_type, _label in _SINGLETON_NODE_TYPES}
        for node_type, entry in NODE_CATALOG.items():
            assert entry.singleton == (node_type in singleton_types), node_type


class TestRendering:
    def test_render_names_every_node_type(self):
        rendered = render_catalog()
        for node_type in NodeType:
            assert node_type.value in rendered

    def test_render_carries_the_usage_notes(self):
        rendered = render_catalog()
        for entry in NODE_CATALOG.values():
            first_words = " ".join(entry.usage_note.split()[:4])
            assert first_words in " ".join(rendered.split())


class TestEntryShapes:
    def test_as_dict_is_json_shaped(self):
        entry = next(iter(NODE_CATALOG.values()))
        dumped = entry.as_dict()
        assert set(dumped.keys()) == {
            "node_type",
            "decorator",
            "config_keys",
            "config_shapes",
            "config_folder",
            "singleton",
            "usage_note",
        }

    def test_types_without_a_config_typeddict_have_empty_shapes(self):
        from haute._config_validation import _TYPED_DICT_BY_NODE_TYPE

        shapeless = [
            node_type for node_type in NodeType if node_type not in _TYPED_DICT_BY_NODE_TYPE
        ]
        for node_type in shapeless:
            assert NODE_CATALOG[node_type].config_shapes == ()

    def test_validate_catalog_complete_raises_on_unexpected_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        inflated = dict(NODE_CATALOG)
        inflated["not-a-node-type"] = next(iter(NODE_CATALOG.values()))
        monkeypatch.setattr(_catalog, "NODE_CATALOG", inflated)
        with pytest.raises(RuntimeError, match="Unexpected"):
            validate_catalog_complete()


def test_validate_catalog_complete_raises_on_fact_mismatch(monkeypatch: pytest.MonkeyPatch):
    from dataclasses import replace

    tampered = dict(NODE_CATALOG)
    entry = tampered[NodeType.POLARS]
    tampered[NodeType.POLARS] = replace(entry, decorator="not_the_real_decorator")
    monkeypatch.setattr(_catalog, "NODE_CATALOG", tampered)
    with pytest.raises(RuntimeError):
        validate_catalog_complete()


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
        operation_ids = {operation["id"] for operation in dumped["operations"]}
        assert "dry_run_graph_edits" in operation_ids
        assert "dry_run_recipe_plan" in operation_ids
        assert "apply_graph_plan" in operation_ids
        assert "apply_graph_edits" not in operation_ids

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
        assert set(compact["node_index"][0]) == {"id", "decorator", "summary"}
        assert "config_schema" not in compact["node_index"][0]
        assert {item["id"] for item in compact["recipe_index"]} == {
            "categorical_banding",
            "continuous_banding",
            "parquet_showcase",
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
        assert by_id["banding"].examples == ("continuous_banding",)
        assert by_id["banding"].recipes == (
            "categorical_banding",
            "continuous_banding",
        )
        assert by_id["dataInput"].recipes == ("parquet_showcase",)
        assert by_id["edgeJoin"].recipes == ("parquet_showcase", "reference_join")
        assert by_id["polars"].recipes == ("parquet_showcase",)
        assert by_id["output"].recipes == ("parquet_showcase", "response_output")

        assert all(
            any("connected" in anti_pattern for anti_pattern in node.anti_patterns)
            for node in by_id.values()
        )
        assert any(
            "df" in anti_pattern and "discard" in anti_pattern
            for anti_pattern in by_id["polars"].anti_patterns
        )

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
        recipe_dry_run_errors = {error["code"] for error in by_id["dry_run_recipe_plan"].errors}
        recipe_dry_run = by_id["dry_run_recipe_plan"]
        plan_recipe = by_id["plan_recipe"]
        plan_recipe_errors = {error["code"] for error in by_id["plan_recipe"].errors}
        apply_errors = {error["code"] for error in by_id["apply_graph_plan"].errors}

        assert {
            "invalid_ops",
            "invalid_plan",
            "recipe_plan_requires_handle",
        } <= dry_run_errors
        assert "recipe_plan_not_found" in recipe_dry_run_errors
        lexical_error_codes = {
            "material_input_required",
            "recipe_name_mismatch",
            "recipe_route_mismatch",
            "recipe_route_required",
        }
        for error_codes in (
            dry_run_errors,
            recipe_dry_run_errors,
            plan_recipe_errors,
            apply_errors,
        ):
            assert lexical_error_codes.isdisjoint(error_codes)
        assert "structured" in plan_recipe.description.lower()
        assert "dry_run_recipe_plan" in plan_recipe.description
        assert set(recipe_dry_run.input_schema["properties"]) == {"recipe_plan_hash"}
        assert set(plan_recipe.output_schema["properties"]) == {
            "recipe_id",
            "version",
            "recipe_plan_hash",
            "capability_hash",
            "operation_version",
            "error",
        }
        recipe_schema = by_id["plan_recipe"].input_schema
        recipe_branches = recipe_schema["oneOf"]
        assert {branch["properties"]["recipe_id"]["const"] for branch in recipe_branches} == {
            "categorical_banding",
            "continuous_banding",
            "parquet_showcase",
            "reference_join",
            "response_output",
            "rating_step",
        }
        continuous = next(
            branch
            for branch in recipe_branches
            if branch["properties"]["recipe_id"]["const"] == "continuous_banding"
        )
        assert "rules" in continuous["required"]
        assert "output_name" in continuous["properties"]
        assert "arguments" not in continuous["properties"]
        assert {
            "plan_aborted",
            "plan_already_applied",
        } <= apply_errors

    def test_graph_edit_provider_schema_is_derived_from_wire_models(self):
        from haute.assistant._wire_ops import (
            AddEdgeOp,
            AddNodeOp,
            DeleteEdgeOp,
            DeleteNodeOp,
            RenameNodeOp,
            UpdateNodeOp,
            UpdatePreambleOp,
            graph_edit_operations_schema,
        )

        models = (
            AddNodeOp,
            UpdateNodeOp,
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
        "continuous",
        "categorical",
        "breakpoints",
    ]
