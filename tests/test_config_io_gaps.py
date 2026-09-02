"""Coverage gap tests for haute._config_io — defensive branches not hit elsewhere.

Targets the specific uncovered lines in src/haute/_config_io.py:

- ``_load_json_object`` rejecting non-object JSON (line 95)
- ``collect_node_configs`` preserving executable ratebook input names
- ``config_path_for_node`` base_dir escape guard (line 186)
- ``config_load_errors`` swallowing a ValueError from path building (349/350)
"""

from __future__ import annotations

import json

import pytest

from haute import _config_io
from haute._config_io import (
    _load_json_object,
    collect_node_configs,
    config_load_errors,
    config_path_for_node,
    load_node_config,
)
from haute._types import NodeType
from haute.errors import ConfigError
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
# collect_node_configs — OPTIMISER_APPLY frame-name preservation
# ---------------------------------------------------------------------------


class TestCollectNodeConfigs:
    def test_apply_ratebook_frame_name_is_preserved_verbatim(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "apply-node",
                        "data": {
                            "label": "apply_optimisation",
                            "nodeType": "optimiserApply",
                            "config": {"ratebook_input": "banded_quotes"},
                        },
                    },
                ],
                "edges": [],
            }
        )

        sidecar = next(iter(collect_node_configs(graph).values()))

        assert json.loads(sidecar)["ratebook_input"] == "banded_quotes"

    @pytest.mark.parametrize("removed_key", ["scored_input", "factors_input"])
    def test_removed_optimiser_input_fields_are_rejected_not_dropped(
        self,
        removed_key: str,
    ) -> None:
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "optimiser",
                        "data": {
                            "label": "optimiser",
                            "nodeType": "optimiser",
                            "config": {removed_key: "legacy-node-id"},
                        },
                    },
                ],
                "edges": [],
            }
        )

        with pytest.raises(ConfigError, match=removed_key):
            collect_node_configs(graph)


@pytest.mark.parametrize("removed_key", ["scored_input", "factors_input"])
def test_removed_optimiser_input_fields_are_rejected_when_loading_sidecar(
    tmp_path,
    removed_key: str,
) -> None:
    path = tmp_path / "config" / "optimisation" / "optimiser.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({removed_key: "legacy-node-id"}), encoding="utf-8")

    with pytest.raises(ConfigError, match=removed_key):
        load_node_config(path)


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
        try:
            (base / "config" / folder).symlink_to(real_config, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not supported (requires elevated privileges on Windows)")
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
                            "nodeType": "dataInput",
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
