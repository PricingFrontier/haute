"""Tests for the assistant node catalog (``haute.assistant._catalog``).

Spec: docs/specs/assistant/low-level.md — `_catalog.py` row and § Testing:
completeness against ``NodeType`` (the ``validate_registry_complete``
pattern applied to the catalog) and agreement of every mechanical fact
with the canonical registries — the catalog must never become a second
source of truth.
"""

from __future__ import annotations

import pytest

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import VALID_KEYS
from haute._types import NODE_TYPE_TO_DECORATOR, NodeType
from haute.assistant import _catalog
from haute.assistant._catalog import NODE_CATALOG, render_catalog, validate_catalog_complete
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

    def test_sidecar_properties_mirror_config_folder(self):
        for entry in NODE_CATALOG.values():
            assert entry.has_sidecar == (entry.sidecar_folder is not None)

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
