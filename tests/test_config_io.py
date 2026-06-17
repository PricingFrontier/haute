"""Tests for haute._config_io — config file I/O and path conventions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import (
    FOLDER_TO_NODE_TYPE,
    NODE_TYPE_TO_FOLDER,
    _prepare_config_for_sidecar,
    collect_node_configs,
    config_load_errors,
    config_path_for_node,
    find_config_by_func_name,
    has_config_folder,
    load_node_config,
    remove_config_file,
)
from haute._types import NodeType
from tests.conftest import make_graph, make_output_config


def _write_node_config_sidecar(
    node_type: NodeType,
    node_name: str,
    config: dict[str, Any],
    base_dir: Path,
) -> Path:
    rel_path = config_path_for_node(node_type, node_name)
    abs_path = base_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    filtered = _prepare_config_for_sidecar(node_type, config)
    abs_path.write_text(
        json.dumps(filtered, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return rel_path


# ---------------------------------------------------------------------------
# Mapping consistency
# ---------------------------------------------------------------------------


class TestMappings:
    def test_folder_to_node_type_is_reverse_of_node_type_to_folder(self):
        assert len(FOLDER_TO_NODE_TYPE) == len(NODE_TYPE_TO_FOLDER)
        for nt, folder in NODE_TYPE_TO_FOLDER.items():
            assert FOLDER_TO_NODE_TYPE[folder] is nt

    def test_all_non_transform_non_submodel_types_mapped(self):
        excluded = {
            NodeType.POLARS,
            NodeType.EXPLORE,
            # edge-join stores its config inline (decorator kwargs + body,
            # like POLARS) — no sidecar config folder.
            NodeType.EDGE_JOIN,
            NodeType.SUBMODEL,
            NodeType.SUBMODEL_PORT,
        }
        for nt in NodeType:
            if nt in excluded:
                assert not has_config_folder(nt), f"{nt} should NOT have config folder"
            else:
                assert has_config_folder(nt), f"{nt} should have config folder"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestConfigPathForNode:
    def test_relative_path(self):
        p = config_path_for_node(NodeType.BANDING, "my_banding")
        assert p == Path("config/banding/my_banding.json")

    def test_absolute_path_with_base_dir(self, tmp_path):
        p = config_path_for_node(NodeType.BANDING, "my_banding", base_dir=tmp_path)
        assert p == tmp_path / "config" / "banding" / "my_banding.json"

    def test_all_types_produce_valid_paths(self):
        for nt, folder in NODE_TYPE_TO_FOLDER.items():
            p = config_path_for_node(nt, "test_node")
            assert p == Path(f"config/{folder}/test_node.json")

    @pytest.mark.parametrize("node_type", [NodeType.POLARS, NodeType.EXPLORE])
    def test_no_config_folder_type_raises(self, node_type):
        with pytest.raises(ValueError, match="No config folder"):
            config_path_for_node(node_type, "my_transform")


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_save_creates_directories_and_file(self, tmp_path):
        config = {"path": "data/input.parquet", "sourceType": "flat_file"}
        rel = _write_node_config_sidecar(NodeType.DATA_SOURCE, "my_source", config, tmp_path)
        assert rel == Path("config/data_source/my_source.json")
        assert (tmp_path / rel).is_file()

    def test_saved_content_is_valid_json(self, tmp_path):
        config = {"path": "data/input.parquet"}
        _write_node_config_sidecar(NodeType.DATA_SOURCE, "src", config, tmp_path)
        loaded = load_node_config("config/data_source/src.json", base_dir=tmp_path)
        assert loaded == config

    def test_code_key_excluded_from_json(self, tmp_path):
        config = {"path": "model.pkl", "fileType": "pickle", "code": "df = obj.predict(df)"}
        _write_node_config_sidecar(NodeType.EXTERNAL_FILE, "ext", config, tmp_path)
        loaded = load_node_config("config/load_file/ext.json", base_dir=tmp_path)
        assert "code" not in loaded
        assert loaded["path"] == "model.pkl"
        assert loaded["fileType"] == "pickle"

    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_node_config("config/data_source/nope.json", base_dir=tmp_path)

    def test_round_trip_complex_config(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "continuous",
                    "column": "DrivAge",
                    "outputColumn": "DrivAgeBand",
                    "rules": [
                        {"op1": ">", "val1": "0", "op2": "<=", "val2": "20", "assignment": "0-20"},
                    ],
                },
            ],
        }
        _write_node_config_sidecar(NodeType.BANDING, "band", config, tmp_path)
        loaded = load_node_config("config/banding/band.json", base_dir=tmp_path)
        assert loaded == config

    def test_banding_categorical_rules_saved_as_map_and_loaded_as_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "outputColumn": "fuel_band",
                    "rules": [
                        {"value": "Petrol", "assignment": "Standard"},
                        {"value": "Diesel", "assignment": "Standard"},
                        {"value": "Electric", "assignment": "Green"},
                    ],
                    "default": "Other",
                },
            ],
        }

        rel = _write_node_config_sidecar(NodeType.BANDING, "fuel_band", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["factors"][0]["rules"] == {
            "Petrol": "Standard",
            "Diesel": "Standard",
            "Electric": "Green",
        }
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_banding_breakpoint_rules_saved_as_map_and_loaded_as_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "driver_age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "65", "label": "adult"},
                        {"boundary": "", "label": "senior"},
                    ],
                    "rightClosed": True,
                    "default": "unknown",
                },
            ],
        }

        rel = _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["factors"][0]["rules"] == {
            "25": "young",
            "65": "adult",
            "": "senior",
        }
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_banding_continuous_rules_stay_explicit_in_sidecar(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "continuous",
                    "column": "driver_age",
                    "outputColumn": "age_band",
                    "rules": [
                        {
                            "op1": ">=",
                            "val1": "18",
                            "op2": "<=",
                            "val2": "25",
                            "assignment": "18-25",
                        },
                    ],
                },
            ],
        }

        rel = _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["factors"][0]["rules"] == config["factors"][0]["rules"]

    def test_banding_compact_save_rejects_duplicate_categorical_keys(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "outputColumn": "fuel_band",
                    "rules": [
                        {"value": "Petrol", "assignment": "Standard"},
                        {"value": "Petrol", "assignment": "Premium"},
                    ],
                },
            ],
        }

        with pytest.raises(ValueError, match="duplicate categorical rule key"):
            _write_node_config_sidecar(NodeType.BANDING, "fuel_band", config, tmp_path)

    def test_banding_compact_save_rejects_duplicate_breakpoint_keys(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "25", "label": "adult"},
                    ],
                },
            ],
        }

        with pytest.raises(ValueError, match="duplicate breakpoint rule key"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)

    def test_banding_compact_save_rejects_json_key_collisions(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "age",
                    "outputColumn": "age_band",
                    "rules": {25: "young", "25": "adult"},
                },
            ],
        }

        with pytest.raises(ValueError, match="duplicate breakpoint rule key '25'"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)

    def test_banding_compact_save_rejects_non_list_factors(self, tmp_path):
        config = {"factors": "not_a_list"}

        with pytest.raises(ValueError, match="banding factors must be a list"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)

    def test_banding_compact_save_rejects_incomplete_categorical_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "outputColumn": "fuel_band",
                    "rules": [
                        {"value": "Petrol", "assignment": "Standard"},
                        {"value": "", "assignment": "Draft"},
                    ],
                },
            ],
        }

        with pytest.raises(ValueError, match="categorical rules\\[1\\] requires value"):
            _write_node_config_sidecar(NodeType.BANDING, "fuel_band", config, tmp_path)

    def test_banding_compact_save_rejects_unlabelled_breakpoint_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "65", "label": ""},
                    ],
                },
            ],
        }

        with pytest.raises(ValueError, match="breakpoint rule '65' requires label"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)


class TestRemoveConfigFile:
    def test_remove_existing_file(self, tmp_path):
        config = {"path": "data.parquet"}
        _write_node_config_sidecar(NodeType.DATA_SOURCE, "src", config, tmp_path)
        assert remove_config_file(NodeType.DATA_SOURCE, "src", tmp_path)
        assert not (tmp_path / "config" / "data_source" / "src.json").exists()

    def test_remove_nonexistent_returns_false(self, tmp_path):
        assert not remove_config_file(NodeType.DATA_SOURCE, "nope", tmp_path)

    def test_remove_transform_returns_false(self, tmp_path):
        assert not remove_config_file(NodeType.POLARS, "t", tmp_path)


# ---------------------------------------------------------------------------
# collect_node_configs
# ---------------------------------------------------------------------------


class TestCollectNodeConfigs:
    def test_datasource_and_transform(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "clean",
                            "nodeType": "polars",
                            "config": {"code": "df = df.filter()"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "t"}],
            }
        )
        configs = collect_node_configs(graph)
        assert "config/data_source/src.json" in configs
        # Transform should NOT have a config file
        assert not any("transform" in k for k in configs)

    def test_code_key_excluded(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "ext",
                        "data": {
                            "label": "ext",
                            "nodeType": "externalFile",
                            "config": {
                                "path": "m.pkl",
                                "fileType": "pickle",
                                "code": "df = obj(df)",
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        content = json.loads(configs["config/load_file/ext.json"])
        assert "code" not in content
        assert content["path"] == "m.pkl"

    def test_instance_nodes_skipped(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "orig",
                        "data": {
                            "label": "orig",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "inst",
                        "data": {
                            "label": "inst",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet", "instanceOf": "orig"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        assert "config/data_source/orig.json" in configs
        assert "config/data_source/inst.json" not in configs

    def test_all_node_types_produce_config(self):
        """Every non-transform, non-submodel node type should generate a config file."""
        nodes = [
            {
                "id": "a",
                "data": {"label": "a", "nodeType": "apiInput", "config": {"path": "d.json"}},
            },
            {
                "id": "b",
                "data": {"label": "b", "nodeType": "dataSource", "config": {"path": "d.parquet"}},
            },
            {
                "id": "c",
                "data": {"label": "c", "nodeType": "liveSwitch", "config": {"mode": "live"}},
            },
            {
                "id": "d",
                "data": {"label": "d", "nodeType": "modelScore", "config": {"task": "regression"}},
            },
            {"id": "e", "data": {"label": "e", "nodeType": "banding", "config": {"factors": []}}},
            {"id": "f", "data": {"label": "f", "nodeType": "ratingStep", "config": {"tables": []}}},
            {
                "id": "g",
                "data": {"label": "g", "nodeType": "output", "config": make_output_config([])},
            },
            {
                "id": "h",
                "data": {"label": "h", "nodeType": "dataSink", "config": {"path": "o.parquet"}},
            },
            {
                "id": "i",
                "data": {"label": "i", "nodeType": "externalFile", "config": {"path": "m.pkl"}},
            },
            {"id": "j", "data": {"label": "j", "nodeType": "modelling", "config": {"target": "y"}}},
            {
                "id": "k",
                "data": {"label": "k", "nodeType": "optimiser", "config": {"mode": "online"}},
            },
            {"id": "l", "data": {"label": "l", "nodeType": "optimiserApply", "config": {}}},
            {"id": "m", "data": {"label": "m", "nodeType": "scenarioExpander", "config": {}}},
            {"id": "n", "data": {"label": "n", "nodeType": "constant", "config": {"values": []}}},
        ]
        graph = make_graph({"nodes": nodes, "edges": []})
        configs = collect_node_configs(graph)
        assert len(configs) == 14

    def test_config_paths_always_use_forward_slashes(self):
        """Config path keys must use forward slashes (not OS-dependent backslashes)."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "b",
                        "data": {
                            "label": "age_band",
                            "nodeType": "banding",
                            "config": {"factors": []},
                        },
                    },
                    {
                        "id": "r",
                        "data": {
                            "label": "area_rate",
                            "nodeType": "ratingStep",
                            "config": {"tables": []},
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        for path in configs:
            assert "\\" not in path, f"Config path contains backslash: {path}"
            assert path.startswith("config/"), f"Config path should start with config/: {path}"

    def test_banding_configs_are_collected_with_compact_categorical_rules(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "b",
                        "data": {
                            "label": "fuel_band",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "banding": "categorical",
                                        "column": "fuel_type",
                                        "outputColumn": "fuel_band",
                                        "rules": [
                                            {"value": "Petrol", "assignment": "Standard"},
                                            {"value": "Electric", "assignment": "Green"},
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )

        configs = collect_node_configs(graph)
        content = json.loads(configs["config/banding/fuel_band.json"])

        assert content["factors"][0]["rules"] == {
            "Petrol": "Standard",
            "Electric": "Green",
        }


# ---------------------------------------------------------------------------
# _load_error protection — prevents config loss on save
# ---------------------------------------------------------------------------


class TestLoadErrorProtection:
    """Verify that nodes with _load_error are excluded from save output,
    preserving the original config file on disk."""

    def test_load_error_node_skipped_in_collect(self):
        """A node with _load_error should not appear in collect_node_configs."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "ok",
                        "data": {
                            "label": "ok",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "bad",
                        "data": {
                            "label": "bad",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "file not found"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        assert "config/data_source/ok.json" in configs
        assert "config/data_source/bad.json" not in configs

    def test_load_error_node_appears_in_config_load_errors(self):
        """config_load_errors should return paths for nodes with _load_error."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "ok",
                        "data": {
                            "label": "ok",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "bad",
                        "data": {
                            "label": "bad",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "missing"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        errors = config_load_errors(graph)
        assert "config/data_source/bad.json" in errors
        assert "config/data_source/ok.json" not in errors

    def test_load_error_not_written_to_json(self, tmp_path):
        """_load_error is filtered before config data is written to a sidecar."""

        config = {"path": "d.parquet", "_load_error": "test error"}
        rel_path = _write_node_config_sidecar(NodeType.DATA_SOURCE, "test", config, tmp_path)
        content = json.loads((tmp_path / rel_path).read_text())
        assert "_load_error" not in content
        assert content["path"] == "d.parquet"

    def test_healthy_node_has_no_load_error(self):
        """A successfully loaded config should not have _load_error."""
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "ok",
                        "data": {
                            "label": "ok",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        errors = config_load_errors(graph)
        assert len(errors) == 0

    def test_user_edit_clears_load_error(self):
        """When user edits a config (frontend sends clean dict), _load_error is gone
        and the node is no longer skipped by collect_node_configs."""
        # Simulate: node initially had _load_error
        graph_before = make_graph(
            {
                "nodes": [
                    {
                        "id": "n",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "missing"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        assert "config/data_source/src.json" not in collect_node_configs(graph_before)

        # Simulate: user edits the node (frontend sends clean config without _load_error)
        graph_after = make_graph(
            {
                "nodes": [
                    {
                        "id": "n",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
                            "config": {"path": "new.parquet"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph_after)
        assert "config/data_source/src.json" in configs
        content = json.loads(configs["config/data_source/src.json"])
        assert content["path"] == "new.parquet"

    def test_save_preserves_original_file(self, tmp_path):
        """End-to-end: saving a graph with _load_error should not overwrite
        the original config file on disk."""

        # Write the original config to disk
        _write_node_config_sidecar(
            NodeType.DATA_SOURCE,
            "src",
            {"path": "data/real.parquet", "sourceType": "flat_file"},
            tmp_path,
        )
        original_path = tmp_path / "config" / "data_source" / "src.json"
        assert original_path.exists()
        original_content = original_path.read_text()

        # Build a graph where that node has _load_error
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "src",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "test"},
                        },
                    },
                ],
                "edges": [],
            }
        )

        # collect_node_configs skips the error node
        configs = collect_node_configs(graph)
        assert "config/data_source/src.json" not in configs

        # The original file is still on disk, untouched
        assert original_path.read_text() == original_content


# ---------------------------------------------------------------------------
# find_config_by_func_name
# ---------------------------------------------------------------------------


class TestFindConfigByFuncName:
    def _write_config(self, tmp_path: Path, folder: str, name: str, data: dict) -> Path:
        p = tmp_path / "config" / folder / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_recovers_config_for_valid_func_name(self, tmp_path):
        cfg = {"path": "data.parquet", "sourceType": "flat_file"}
        self._write_config(tmp_path, "data_source", "my_source", cfg)
        result = find_config_by_func_name("my_source", tmp_path)
        assert result is not None
        config_dict, node_type = result
        assert config_dict == cfg
        assert node_type is NodeType.DATA_SOURCE

    def test_recovers_banding_config_with_expanded_compact_rules(self, tmp_path):
        compact_cfg = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "outputColumn": "fuel_band",
                    "rules": {"Petrol": "Standard", "Electric": "Green"},
                }
            ]
        }
        self._write_config(tmp_path, "banding", "fuel_band", compact_cfg)

        result = find_config_by_func_name("fuel_band", tmp_path)

        assert result is not None
        config_dict, node_type = result
        assert node_type is NodeType.BANDING
        assert config_dict["factors"][0]["rules"] == [
            {"value": "Petrol", "assignment": "Standard"},
            {"value": "Electric", "assignment": "Green"},
        ]

    def test_returns_tuple_format(self, tmp_path):
        self._write_config(tmp_path, "banding", "age_band", {"factors": []})
        result = find_config_by_func_name("age_band", tmp_path)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert isinstance(result[1], NodeType)

    def test_scans_all_14_config_folders(self, tmp_path):
        for folder in FOLDER_TO_NODE_TYPE:
            self._write_config(tmp_path, folder, f"func_{folder}", {"ok": True})
        for folder, expected_nt in FOLDER_TO_NODE_TYPE.items():
            result = find_config_by_func_name(f"func_{folder}", tmp_path)
            assert result is not None, f"Failed to find config in folder {folder}"
            assert result[1] is expected_nt

    def test_returns_none_when_not_found(self, tmp_path):
        (tmp_path / "config").mkdir()
        assert find_config_by_func_name("nonexistent_func", tmp_path) is None

    def test_returns_none_for_empty_func_name(self, tmp_path):
        (tmp_path / "config").mkdir()
        assert find_config_by_func_name("", tmp_path) is None

    def test_rejects_path_traversal(self, tmp_path):
        assert find_config_by_func_name("../etc/passwd", tmp_path) is None
        assert find_config_by_func_name("foo/bar", tmp_path) is None
        assert find_config_by_func_name("foo\\bar", tmp_path) is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        p = tmp_path / "config" / "banding" / "bad.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not valid json", encoding="utf-8")
        assert find_config_by_func_name("bad", tmp_path) is None


# ---------------------------------------------------------------------------
# config_path_for_node edge cases
# ---------------------------------------------------------------------------


class TestConfigPathForNodeEdgeCases:
    def test_empty_node_name(self):
        p = config_path_for_node(NodeType.BANDING, "")
        assert p == Path("config/banding/.json")

    def test_single_dot_raises(self):
        with pytest.raises(ValueError, match="must not contain"):
            config_path_for_node(NodeType.BANDING, "..")

    def test_triple_dot_contains_double_dot_raises(self):
        with pytest.raises(ValueError, match="must not contain"):
            config_path_for_node(NodeType.BANDING, "...")

    def test_single_dot_name_allowed(self):
        p = config_path_for_node(NodeType.BANDING, ".")
        assert p == Path("config/banding/..json")

    @pytest.mark.skipif(
        __import__("sys").platform != "win32",
        reason="Windows-specific reserved name test",
    )
    def test_windows_reserved_names_produce_paths(self):
        for name in ("CON", "PRN", "AUX"):
            p = config_path_for_node(NodeType.BANDING, name)
            assert p == Path(f"config/banding/{name}.json")


# ---------------------------------------------------------------------------
# load_node_config edge cases
# ---------------------------------------------------------------------------


class TestLoadNodeConfigEdgeCases:
    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "bad.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{invalid json!!!", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_node_config(str(p))

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "empty.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_node_config(str(p))

    def test_config_file_with_bom_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "bom.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"key": "value"}).encode("utf-8"))
        with pytest.raises(json.JSONDecodeError, match="BOM"):
            load_node_config(str(p))

    def test_banding_continuous_rule_map_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "bad.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": "age",
                            "outputColumn": "age_band",
                            "rules": {"25": "young"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="continuous banding rules must be a list"):
            load_node_config(str(p))

    def test_banding_sidecar_with_non_list_factors_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "bad_factors.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"factors": "not_a_list"}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="banding factors must be a list"):
            load_node_config(str(p))

    def test_banding_compact_duplicate_json_key_raises(self, tmp_path):
        p = tmp_path / "config" / "banding" / "duplicate.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            """
{
  "factors": [
    {
      "banding": "categorical",
      "column": "fuel_type",
      "outputColumn": "fuel_band",
      "rules": {
        "Petrol": "Standard",
        "Petrol": "Premium"
      }
    }
  ]
}
""",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="duplicate JSON key 'Petrol'"):
            load_node_config(str(p))


# ---------------------------------------------------------------------------
# sidecar preparation edge cases
# ---------------------------------------------------------------------------


class TestSidecarPreparationEdgeCases:
    def test_none_values_saved(self, tmp_path):
        config = {"path": None, "sourceType": None}
        _write_node_config_sidecar(NodeType.DATA_SOURCE, "src", config, tmp_path)
        loaded = load_node_config("config/data_source/src.json", base_dir=tmp_path)
        assert loaded["path"] is None
        assert loaded["sourceType"] is None

    def test_underscore_keys_filtered(self, tmp_path):
        config = {"path": "d.parquet", "_internal": "secret", "_cache": 42}
        _write_node_config_sidecar(NodeType.DATA_SOURCE, "src", config, tmp_path)
        loaded = load_node_config("config/data_source/src.json", base_dir=tmp_path)
        assert loaded == {"path": "d.parquet"}
        assert "_internal" not in loaded
        assert "_cache" not in loaded

    def test_code_key_excluded(self, tmp_path):
        config = {"path": "m.pkl", "code": "x = 1"}
        _write_node_config_sidecar(NodeType.EXTERNAL_FILE, "ext", config, tmp_path)
        loaded = load_node_config("config/load_file/ext.json", base_dir=tmp_path)
        assert "code" not in loaded
        assert loaded == {"path": "m.pkl"}

    def test_empty_config_saved_as_empty_object(self, tmp_path):
        _write_node_config_sidecar(NodeType.BANDING, "empty", {}, tmp_path)
        loaded = load_node_config("config/banding/empty.json", base_dir=tmp_path)
        assert loaded == {}

    def test_returns_relative_path(self, tmp_path):
        rel = _write_node_config_sidecar(NodeType.BANDING, "b", {"factors": []}, tmp_path)
        assert isinstance(rel, Path)
        assert not rel.is_absolute()
        assert str(rel) == str(Path("config/banding/b.json"))


def test_dead_save_node_config_api_is_absent() -> None:
    import haute._config_io as config_io

    assert not hasattr(config_io, "save_node_config")


# ---------------------------------------------------------------------------
# collect_node_configs edge cases
# ---------------------------------------------------------------------------


class TestCollectNodeConfigsEdgeCases:
    def test_empty_graph_returns_empty_dict(self):
        graph = make_graph({"nodes": [], "edges": []})
        assert collect_node_configs(graph) == {}

    def test_graph_with_only_transforms_returns_empty(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "t1",
                        "data": {
                            "label": "clean",
                            "nodeType": "polars",
                            "config": {"code": "df"},
                        },
                    },
                    {
                        "id": "t2",
                        "data": {
                            "label": "filter",
                            "nodeType": "polars",
                            "config": {"code": "df = df.filter()"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "t1", "target": "t2"}],
            }
        )
        assert collect_node_configs(graph) == {}

    def test_config_paths_use_forward_slashes(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "my_src",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        for path in configs:
            assert "\\" not in path


# ---------------------------------------------------------------------------
# config_load_errors edge cases
# ---------------------------------------------------------------------------


class TestConfigLoadErrorsEdgeCases:
    def test_error_node_returned(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "bad",
                        "data": {
                            "label": "broken",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "file corrupt"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        errors = config_load_errors(graph)
        assert "config/data_source/broken.json" in errors
        assert errors["config/data_source/broken.json"] == "file corrupt"

    def test_healthy_node_excluded(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "ok",
                        "data": {
                            "label": "good",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                ],
                "edges": [],
            }
        )
        assert config_load_errors(graph) == {}

    def test_multiple_error_nodes(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "b1",
                        "data": {
                            "label": "bad1",
                            "nodeType": "dataSource",
                            "config": {"_load_error": "err1"},
                        },
                    },
                    {
                        "id": "b2",
                        "data": {
                            "label": "bad2",
                            "nodeType": "banding",
                            "config": {"_load_error": "err2"},
                        },
                    },
                    {
                        "id": "ok",
                        "data": {
                            "label": "healthy",
                            "nodeType": "output",
                            "config": make_output_config([]),
                        },
                    },
                ],
                "edges": [],
            }
        )
        errors = config_load_errors(graph)
        assert len(errors) == 2
        assert "config/data_source/bad1.json" in errors
        assert "config/banding/bad2.json" in errors
        assert errors["config/data_source/bad1.json"] == "err1"
        assert errors["config/banding/bad2.json"] == "err2"


# ---------------------------------------------------------------------------
# remove_config_file edge cases
# ---------------------------------------------------------------------------


class TestRemoveConfigFileEdgeCases:
    def test_remove_existing_returns_true(self, tmp_path):
        _write_node_config_sidecar(NodeType.DATA_SOURCE, "src", {"path": "d"}, tmp_path)
        assert remove_config_file(NodeType.DATA_SOURCE, "src", tmp_path) is True
        assert not (tmp_path / "config" / "data_source" / "src.json").exists()

    def test_remove_nonexistent_returns_false(self, tmp_path):
        assert remove_config_file(NodeType.BANDING, "nope", tmp_path) is False

    def test_remove_transform_type_returns_false(self, tmp_path):
        assert remove_config_file(NodeType.POLARS, "t", tmp_path) is False


class TestRatingStepCompactSidecars:
    def test_save_writes_compact_entries_and_load_expands_to_rows(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "area_factor",
                    "factors": ["area"],
                    "outputColumn": "area_factor",
                    "defaultValue": "1.0",
                    "entries": [
                        {"area": "London", "value": "1.25"},
                        {"area": "Rural", "value": 0.85},
                    ],
                },
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "outputColumn": "vehicle_factor",
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "value": 0.9,
                        },
                        {"vehicle_age_band": "1-3", "cover_type": "tpft", "value": 1.1},
                        {
                            "vehicle_age_band": "10+",
                            "cover_type": "comprehensive",
                            "value": 1.4,
                        },
                    ],
                },
            ],
            "combinedOutputs": [
                {"outputColumn": "premium", "operation": "multiply", "baseValue": 100}
            ],
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["tables"][0]["entries"] == {"London": "1.25", "Rural": 0.85}
        assert saved["tables"][1]["entries"] == {
            "1-3": {"comprehensive": 0.9, "tpft": 1.1},
            "10+": {"comprehensive": 1.4},
        }
        assert saved["combinedOutputs"] == config["combinedOutputs"]
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_load_expands_three_factor_entries_from_nested_maps(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "vehicle.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "vehicle_factor",
                            "factors": ["vehicle_age_band", "cover_type", "channel"],
                            "outputColumn": "vehicle_factor",
                            "entries": {
                                "direct": {
                                    "comprehensive": {
                                        "1-3": 0.9,
                                        "10+": 1.0,
                                    }
                                }
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        loaded = load_node_config(path)

        assert loaded["tables"][0]["entries"] == [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "channel": "direct",
                "value": 0.9,
            },
            {
                "vehicle_age_band": "10+",
                "cover_type": "comprehensive",
                "channel": "direct",
                "value": 1.0,
            },
        ]

    def test_save_writes_three_factor_entries_in_editor_axis_order(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "outputColumn": "vehicle_factor",
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "channel": "confused",
                            "value": 0.91,
                        },
                        {
                            "vehicle_age_band": "4-5",
                            "cover_type": "comprehensive",
                            "channel": "confused",
                            "value": 0.96,
                        },
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "third_party_only",
                            "channel": "compare_the_market",
                            "value": 1.08,
                        },
                    ],
                }
            ]
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["tables"][0]["entries"] == {
            "confused": {
                "comprehensive": {
                    "1-3": 0.91,
                    "4-5": 0.96,
                }
            },
            "compare_the_market": {
                "third_party_only": {
                    "1-3": 1.08,
                }
            },
        }
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_save_keeps_sparse_three_factor_entries_sparse(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "sparse_factor",
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "outputColumn": "sparse_factor",
                    "entries": [
                        {
                            "vehicle_age_band": "1-3",
                            "cover_type": "comprehensive",
                            "channel": "confused",
                            "value": 0.91,
                        },
                        {
                            "vehicle_age_band": "10+",
                            "cover_type": "third_party_only",
                            "channel": "broker",
                            "value": 1.25,
                        },
                    ],
                }
            ]
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "sparse", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["tables"][0]["entries"] == {
            "confused": {"comprehensive": {"1-3": 0.91}},
            "broker": {"third_party_only": {"10+": 1.25}},
        }
        assert load_node_config(rel, base_dir=tmp_path)["tables"][0]["entries"] == [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "channel": "confused",
                "value": 0.91,
            },
            {
                "vehicle_age_band": "10+",
                "cover_type": "third_party_only",
                "channel": "broker",
                "value": 1.25,
            },
        ]

    def test_collect_node_configs_writes_compact_rating_entries(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "r",
                        "data": {
                            "label": "adjustments",
                            "nodeType": "ratingStep",
                            "config": {
                                "tables": [
                                    {
                                        "name": "vehicle_factor",
                                        "factors": ["vehicle_age_band", "cover_type"],
                                        "outputColumn": "vehicle_factor",
                                        "entries": [
                                            {
                                                "vehicle_age_band": "1-3",
                                                "cover_type": "comprehensive",
                                                "value": 0.9,
                                            }
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )

        configs = collect_node_configs(graph)
        content = json.loads(configs["config/rating_step/adjustments.json"])

        assert content["tables"][0]["entries"] == {"1-3": {"comprehensive": 0.9}}

    def test_collect_node_configs_writes_three_factor_entries_in_editor_axis_order(self):
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "r",
                        "data": {
                            "label": "adjustments",
                            "nodeType": "ratingStep",
                            "config": {
                                "tables": [
                                    {
                                        "name": "vehicle_factor",
                                        "factors": [
                                            "vehicle_age_band",
                                            "cover_type",
                                            "channel",
                                        ],
                                        "outputColumn": "vehicle_factor",
                                        "entries": [
                                            {
                                                "vehicle_age_band": "1-3",
                                                "cover_type": "comprehensive",
                                                "channel": "confused",
                                                "value": 0.9,
                                            }
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )

        configs = collect_node_configs(graph)
        content = json.loads(configs["config/rating_step/adjustments.json"])

        assert content["tables"][0]["entries"] == {"confused": {"comprehensive": {"1-3": 0.9}}}

    def test_find_config_by_func_name_expands_compact_rating_entries(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "adjustments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "vehicle_factor",
                            "factors": ["vehicle_age_band", "cover_type"],
                            "outputColumn": "vehicle_factor",
                            "entries": {"1-3": {"comprehensive": 0.9}},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = find_config_by_func_name("adjustments", tmp_path)

        assert result is not None
        config, node_type = result
        assert node_type is NodeType.RATING_STEP
        assert config["tables"][0]["entries"] == [
            {
                "vehicle_age_band": "1-3",
                "cover_type": "comprehensive",
                "value": 0.9,
            }
        ]

    def test_save_rejects_duplicate_rating_factor_keys(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "area_factor",
                    "factors": ["area"],
                    "outputColumn": "area_factor",
                    "entries": [
                        {"area": 1, "value": 1.1},
                        {"area": "1", "value": 1.2},
                    ],
                }
            ]
        }

        with pytest.raises(ValueError, match="duplicate ratingStep tables\\[0\\].entries key"):
            _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)

    def test_save_rejects_entries_missing_factor_values(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "vehicle_factor",
                    "factors": ["vehicle_age_band", "cover_type"],
                    "outputColumn": "vehicle_factor",
                    "entries": [{"vehicle_age_band": "1-3", "value": 0.9}],
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match="ratingStep tables\\[0\\].entries\\[0\\] requires factor 'cover_type'",
        ):
            _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)

    def test_save_rejects_blank_rating_values(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "area_factor",
                    "factors": ["area"],
                    "outputColumn": "area_factor",
                    "entries": [{"area": "London", "value": ""}],
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match="ratingStep tables\\[0\\].entries key 'London' requires value",
        ):
            _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)

    def test_save_compacts_rows_that_use_output_column_as_value_key(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "area_factor",
                    "factors": ["area"],
                    "outputColumn": "area_factor",
                    "entries": [
                        {"area": "London", "area_factor": 1.25},
                        {"area": "Rural", "area_factor": 0.85},
                    ],
                }
            ]
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        assert saved["tables"][0]["entries"] == {"London": 1.25, "Rural": 0.85}
        assert load_node_config(rel, base_dir=tmp_path)["tables"][0]["entries"] == [
            {"area": "London", "value": 1.25},
            {"area": "Rural", "value": 0.85},
        ]

    def test_save_rejects_rating_entries_with_unrepresentable_extra_keys(self, tmp_path):
        config = {
            "tables": [
                {
                    "name": "area_factor",
                    "factors": ["area"],
                    "outputColumn": "area_factor",
                    "entries": [{"area": "London", "value": 1.25, "note": "legacy"}],
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match="ratingStep tables\\[0\\].entries\\[0\\] contains unsupported keys",
        ):
            _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)

    def test_load_rejects_non_list_rating_tables(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "bad_tables.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"tables": "not_a_list"}), encoding="utf-8")

        with pytest.raises(ValueError, match="ratingStep tables must be a list"):
            load_node_config(path)

    def test_load_rejects_non_list_rating_factors(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "bad_factors.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "area_factor",
                            "factors": "area",
                            "outputColumn": "area_factor",
                            "entries": {"London": 1.25},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="ratingStep tables\\[0\\].factors must be a list"):
            load_node_config(path)

    def test_load_rejects_shallow_rating_entries_map(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "shallow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tables": [
                        {
                            "name": "vehicle_factor",
                            "factors": ["vehicle_age_band", "cover_type"],
                            "outputColumn": "vehicle_factor",
                            "entries": {"1-3": 0.9},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="must be nested to match 2 factors"):
            load_node_config(path)

    def test_load_rejects_duplicate_rating_json_keys(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "duplicate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            """
{
  "tables": [
    {
      "name": "area_factor",
      "factors": ["area"],
      "outputColumn": "area_factor",
      "entries": {
        "London": 1.25,
        "London": 1.1
      }
    }
  ]
}
""",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="duplicate JSON key 'London'"):
            load_node_config(path)
