"""Coverage gap tests for haute._config_io — defensive branches not hit elsewhere.

Targets the specific uncovered lines in src/haute/_config_io.py:

- ``_load_json_object`` rejecting non-object JSON (line 95)
- ``_remap_config_ids_for_saved_graph`` unresolved-ratebook warning path (141/146)
- ``config_path_for_node`` base_dir escape guard (line 186)
- ``config_load_errors`` swallowing a ValueError from path building (349/350)
"""

from __future__ import annotations

import json

import pytest

from haute import _config_io
from haute._config_io import (
    _load_json_object,
    _remap_config_ids_for_saved_graph,
    config_load_errors,
    config_path_for_node,
)
from haute._types import NodeType
from tests.conftest import make_graph

# ---------------------------------------------------------------------------
# _load_json_object — non-object JSON is rejected
# ---------------------------------------------------------------------------


class TestLoadJsonObjectNonDict:
    def test_top_level_array_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "arr.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="must contain an object"):
            _load_json_object(p)

    def test_top_level_scalar_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "scalar.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(42), encoding="utf-8")
        with pytest.raises(ValueError, match="must contain an object"):
            _load_json_object(p)


# ---------------------------------------------------------------------------
# _remap_config_ids_for_saved_graph — OPTIMISER_APPLY id translation
# ---------------------------------------------------------------------------


class TestRemapConfigIds:
    def test_non_apply_node_returned_unchanged(self):
        config = {"ratebook_input": "graph_id"}
        result = _remap_config_ids_for_saved_graph(NodeType.BANDING, config, {})
        assert result is config

    def test_apply_with_resolvable_ratebook_is_remapped(self):
        config = {"ratebook_input": "graph_id"}
        result = _remap_config_ids_for_saved_graph(
            NodeType.OPTIMISER_APPLY,
            config,
            {"graph_id": "saved_id"},
        )
        assert result["ratebook_input"] == "saved_id"
        # original config left untouched
        assert config["ratebook_input"] == "graph_id"

    def test_apply_without_ratebook_input_returned_unchanged(self):
        config = {"other": "value"}
        result = _remap_config_ids_for_saved_graph(NodeType.OPTIMISER_APPLY, config, {})
        assert result is config

    def test_apply_with_empty_ratebook_input_returned_unchanged(self):
        config = {"ratebook_input": ""}
        result = _remap_config_ids_for_saved_graph(NodeType.OPTIMISER_APPLY, config, {})
        assert result is config

    def test_apply_with_non_string_ratebook_input_returned_unchanged(self):
        config = {"ratebook_input": 123}
        result = _remap_config_ids_for_saved_graph(NodeType.OPTIMISER_APPLY, config, {})
        assert result is config

    def test_apply_with_unresolved_ratebook_warns_and_keeps_config(self):
        config = {"ratebook_input": "missing_upstream"}
        # No mapping entry for the configured upstream id -> warning path.
        result = _remap_config_ids_for_saved_graph(
            NodeType.OPTIMISER_APPLY,
            config,
            {"other_id": "saved_other"},
            node_label="apply_node",
        )
        assert result is config
        assert result["ratebook_input"] == "missing_upstream"


# ---------------------------------------------------------------------------
# config_path_for_node — base_dir escape guard
# ---------------------------------------------------------------------------


class TestConfigPathEscapeGuard:
    def test_escape_guard_triggers_on_resolved_outside(self, tmp_path):
        # Build a base_dir whose `config` directory is a symlink pointing
        # elsewhere, then point the node into a sibling so .resolve() lands
        # outside the (symlink-resolved) config root.
        real_config = tmp_path / "realconfig"
        real_config.mkdir()
        base = tmp_path / "proj"
        base.mkdir()
        # `config` is a real dir; create a sibling the resolved path escapes to.
        (base / "config").mkdir()
        # A node name cannot contain separators, so the only escape route is a
        # symlinked subfolder. Make the type folder a symlink out of config.
        folder = _config_io.NODE_TYPE_TO_FOLDER[NodeType.BANDING]
        (base / "config" / folder).symlink_to(real_config, target_is_directory=True)
        with pytest.raises(ValueError, match="escapes config directory"):
            config_path_for_node(NodeType.BANDING, "node", base_dir=base)


# ---------------------------------------------------------------------------
# config_load_errors — ValueError from path building is swallowed
# ---------------------------------------------------------------------------


class TestConfigLoadErrorsPathFailure:
    def test_path_build_failure_is_skipped(self, monkeypatch):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "bad",
                        "data": {
                            "label": "broken",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "boom"},
                        },
                    },
                ],
                "edges": [],
            }
        )

        def raising_path(node_type, func_name):
            raise ValueError("synthetic path failure")

        monkeypatch.setattr(_config_io, "config_path_for_node", raising_path)
        # The error node is dropped (path build failed) -> empty result.
        assert config_load_errors(graph) == {}
