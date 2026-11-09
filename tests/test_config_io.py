"""Tests for haute._config_io — config file I/O and path conventions."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import (
    collect_node_configs,
    config_path_for_node,
    has_config_folder,
    is_windows_reserved_filename,
    load_node_config,
    node_config_json,
)
from haute._node_specs import FOLDER_TO_NODE_TYPE, NODE_TYPE_TO_FOLDER
from haute._types import NodeType
from haute._validation_error import ConfigSettingError
from haute.errors import NodeConfigError
from tests.conftest import make_graph, make_output_config


def _write_node_config_sidecar(
    node_type: NodeType,
    node_name: str,
    config: dict[str, Any],
    base_dir: Path,
) -> Path:
    """Write *config*'s sidecar as every save path writes it (``node_config_json``)."""
    rel_path = config_path_for_node(node_type, node_name)
    abs_path = base_dir / rel_path
    text = node_config_json(node_type, config, node_label=node_name)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(text.encode("utf-8"))
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
        # An Explore node's settings live in config/explore/ (READ-43).
        excluded = {
            NodeType.POLARS,
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

    @pytest.mark.parametrize("node_type", [NodeType.EDGE_JOIN, NodeType.POLARS])
    def test_no_config_folder_type_raises(self, node_type):
        with pytest.raises(ValueError, match="No config folder"):
            config_path_for_node(node_type, "my_transform")

    def test_a_transform_has_no_sidecar(self):
        # A transform's steps live in its code alone (READ-22): no ``config/polars/``.
        assert not has_config_folder(NodeType.POLARS)
        assert NodeType.POLARS not in NODE_TYPE_TO_FOLDER
        assert "polars" not in FOLDER_TO_NODE_TYPE


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    @pytest.mark.parametrize(
        ("node_type", "config"),
        [
            pytest.param(
                NodeType.DATA_INPUT,
                {
                    "input_type": "file",
                    "format": "csv",
                    "path": "data/input.csv",
                    "arguments": {
                        "schema": {
                            "_id": "Int64",
                            "payload": {"type": "Struct", "fields": {"_prevRules": "String"}},
                        }
                    },
                },
                id="input-schema-and-struct-fields",
            ),
            pytest.param(
                NodeType.DATA_INPUT,
                {
                    "input_type": "inline",
                    "format": "records",
                    "records": [
                        {"_id": 1, "payload": {"_cache": [{"_id": "kept"}]}},
                    ],
                    "arguments": {},
                },
                id="inline-records",
            ),
            pytest.param(
                NodeType.MODELLING,
                {
                    "monotone_constraints": {"_id": 1},
                    "feature_weights": {"_id": 0.5},
                    "params": {"custom": {"_id": "parameter"}},
                },
                id="model-feature-maps-and-parameters",
            ),
            pytest.param(
                NodeType.OPTIMISER,
                {
                    "constraints": {"_premium": {"min": 100.0}},
                    "frontier_ranges": {"_premium": {"min": 100.0, "max": 200.0}},
                },
                id="optimiser-constraint-names",
            ),
            pytest.param(
                NodeType.LIVE_SWITCH,
                {"input_scenario_map": {"_input": "live"}},
                id="source-switch-input-names",
            ),
            pytest.param(
                NodeType.RATING_STEP,
                {
                    "tables": [
                        {
                            "factors": ["_id"],
                            "output_column": "factor",
                            "entries": [{"_id": "A", "value": 1.25}],
                        }
                    ],
                    # Left out of the file at its default and read back as it (READ-10).
                    "combined_outputs": [],
                },
                id="rating-factor-names",
            ),
        ],
    )
    def test_user_mapping_keys_survive_repeated_save(self, tmp_path, node_type, config):
        original = deepcopy(config)
        rel = _write_node_config_sidecar(node_type, "settings", config, tmp_path)
        loaded = load_node_config(rel, base_dir=tmp_path)
        assert loaded == original
        first_bytes = (tmp_path / rel).read_bytes()
        _write_node_config_sidecar(node_type, "settings", loaded, tmp_path)
        assert (tmp_path / rel).read_bytes() == first_bytes
        assert config == original

    def test_compact_category_keys_are_data_not_editor_metadata(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "category",
                    "output_column": "band",
                    "rules": {"_id": "identifier", "_prevRules": "previous", "ordinary": "normal"},
                }
            ]
        }
        original = deepcopy(config)
        rel = _write_node_config_sidecar(NodeType.BANDING, "categories", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        assert saved["factors"][0]["rules"] == config["factors"][0]["rules"]
        loaded = load_node_config(rel, base_dir=tmp_path)
        _write_node_config_sidecar(NodeType.BANDING, "categories", loaded, tmp_path)
        assert json.loads((tmp_path / rel).read_text(encoding="utf-8")) == saved
        assert config == original

    def test_an_underscore_key_on_a_factor_is_undeclared(self, tmp_path):
        """A factor holds only its declared settings: editor state never rides in it."""
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "category",
                    "output_column": "band",
                    "_prevRules": {"breakpoints": []},
                    "rules": {"ordinary": "normal"},
                }
            ]
        }
        with pytest.raises(NodeConfigError, match=r"'factors\[0\]\._prevRules'") as refused:
            _write_node_config_sidecar(NodeType.BANDING, "categories", config, tmp_path)
        assert refused.value.setting == "factors[0]._prevRules"

    def test_save_creates_directories_and_file(self, tmp_path):
        config = {
            "input_type": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "data/input.parquet",
            "arguments": {},
        }
        rel = _write_node_config_sidecar(NodeType.DATA_INPUT, "my_source", config, tmp_path)
        assert rel == Path("config/data_input/my_source.json")
        assert (tmp_path / rel).is_file()

    def test_saved_content_is_valid_json(self, tmp_path):
        config = {"input_type": "file", "format": "parquet", "path": "data/input.parquet"}
        rel = _write_node_config_sidecar(NodeType.DATA_INPUT, "src", config, tmp_path)
        assert json.loads((tmp_path / rel).read_text(encoding="utf-8")) == config
        loaded = load_node_config("config/data_input/src.json", base_dir=tmp_path)
        # ``arguments`` is left out at its default, and the loader restores it (READ-10).
        assert loaded == {**config, "arguments": {}}

    def test_api_input_projection_fields_round_trip(self, tmp_path):
        config = {
            "selected_columns": ["policy_id", "premium"],
            "column_renames": {"written_premium": "premium"},
        }
        rel = _write_node_config_sidecar(NodeType.API_INPUT, "quotes", config, tmp_path)
        assert load_node_config(rel, base_dir=tmp_path) == config

    @pytest.mark.parametrize(
        ("node_type", "config", "says"),
        [
            (
                NodeType.WORKBENCH_INPUT,
                {"tables": [], "sample": {}},
                "workbenchInput config contains the removed tables and sample fields. A "
                "Workbench Input reads its tables and sample from the form folder: delete them.",
            ),
            (
                NodeType.WORKBENCH_INPUT,
                {"sample": {}},
                "workbenchInput config contains the removed sample field. A Workbench Input "
                "reads its tables and sample from the form folder: delete it.",
            ),
            (
                NodeType.WORKBENCH_OUTPUT,
                {"tables": []},
                "workbenchOutput config contains the removed tables field. A Workbench Output "
                "reads its tables from the form folder: delete it.",
            ),
        ],
        ids=["input", "input-sample", "output"],
    )
    def test_a_workbench_nodes_tables_never_reach_its_config_file(
        self, tmp_path, node_type, config, says
    ):
        """A workbench node is given its tables, and a Workbench Input its sample, from the
        form where it runs: a write holding them is refused, nothing written, and a file an
        earlier Haute wrote with its copies is refused naming the keys (specs/workbench)."""
        from haute.errors import ConfigError

        with pytest.raises(ConfigError, match=re.escape(says)):
            _write_node_config_sidecar(node_type, "node", config, tmp_path)
        rel = config_path_for_node(node_type, "node")
        assert not (tmp_path / rel).exists()
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(ConfigError, match=re.escape(says)) as refused:
            load_node_config(rel, base_dir=tmp_path)
        assert refused.value.context["removed_config_keys"] == [
            key for key in ("tables", "sample") if key in config
        ]

    def test_a_workbench_nodes_config_file_holds_nothing_of_the_form(self, tmp_path):
        """A Workbench Input's or Output's config file holds no tables: it reads back as
        written, `{}`."""
        for node_type, name in (
            (NodeType.WORKBENCH_INPUT, "quote"),
            (NodeType.WORKBENCH_OUTPUT, "response"),
        ):
            rel = _write_node_config_sidecar(node_type, name, {}, tmp_path)
            assert json.loads((tmp_path / rel).read_text(encoding="utf-8")) == {}
            assert load_node_config(rel, base_dir=tmp_path) == {}

    def test_a_workbench_input_config_with_a_path_is_refused(self, tmp_path):
        """A Workbench Input reads no file, so its config declares no ``path``."""
        from haute.errors import ConfigError

        config = {"path": "tests/quotes/example.json"}
        with pytest.raises(ConfigError, match="'path'"):
            _write_node_config_sidecar(NodeType.WORKBENCH_INPUT, "quote", config, tmp_path)
        assert not (tmp_path / "config" / "workbench_input" / "quote.json").exists()

    def test_code_key_excluded_from_json(self, tmp_path):
        config = {"path": "model.pkl", "file_type": "pickle", "code": "df = obj.predict(df)"}
        _write_node_config_sidecar(NodeType.EXTERNAL_FILE, "ext", config, tmp_path)
        loaded = load_node_config("config/load_file/ext.json", base_dir=tmp_path)
        assert "code" not in loaded
        assert loaded["path"] == "model.pkl"
        assert loaded["file_type"] == "pickle"

    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_node_config("config/data_input/nope.json", base_dir=tmp_path)

    def test_round_trip_complex_config(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "DrivAge",
                    "output_column": "DrivAgeBand",
                    "rules": [
                        {"boundary": "20", "label": "0-20"},
                        {"boundary": "", "label": "20+"},
                    ],
                    # A null default is left unwritten and reads back as null (READ-27).
                    "default": None,
                },
            ],
        }
        rel = _write_node_config_sidecar(NodeType.BANDING, "band", config, tmp_path)
        assert '"default"' not in (tmp_path / rel).read_text(encoding="utf-8")
        loaded = load_node_config("config/banding/band.json", base_dir=tmp_path)
        assert loaded == config

    def test_banding_categorical_rules_saved_as_map_and_loaded_as_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "output_column": "fuel_band",
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

    def test_banding_breakpoint_rules_saved_as_bands_and_loaded_as_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "driver_age",
                    "output_column": "age_band",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "65", "label": "adult"},
                        {"boundary": "", "label": "senior"},
                    ],
                    "right_closed": True,
                    "default": "unknown",
                },
            ],
        }

        rel = _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        # A list of bands in order, the open-ended one last with no upper edge (READ-27).
        assert saved["factors"][0]["rules"] == [
            {"upper": 25, "label": "young"},
            {"upper": 65, "label": "adult"},
            {"label": "senior"},
        ]
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_banding_compact_save_rejects_duplicate_categorical_keys(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "output_column": "fuel_band",
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
                    "output_column": "age_band",
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
                    "output_column": "age_band",
                    "rules": {25: "young", "25": "adult"},
                },
            ],
        }

        # A JSON object's keys are text, so the model refuses the integer key that would collide.
        with pytest.raises(
            ValueError, match=r"'factors\[0\]\.rules': Input should be a valid string"
        ):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)

    def test_banding_compact_save_rejects_non_list_factors(self, tmp_path):
        config = {"factors": "not_a_list"}

        with pytest.raises(ValueError, match="'factors': Input should be a valid list"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)

    def test_banding_compact_save_rejects_incomplete_categorical_rows(self, tmp_path):
        config = {
            "factors": [
                {
                    "banding": "categorical",
                    "column": "fuel_type",
                    "output_column": "fuel_band",
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
                    "output_column": "age_band",
                    "rules": [
                        {"boundary": "25", "label": "young"},
                        {"boundary": "65", "label": ""},
                    ],
                },
            ],
        }

        with pytest.raises(ValueError, match="breakpoint rule '65' requires label"):
            _write_node_config_sidecar(NodeType.BANDING, "age_band", config, tmp_path)


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
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "path": "d.parquet",
                            },
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
        assert "config/data_input/src.json" in configs
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
                                "file_type": "pickle",
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

    def test_all_node_types_produce_config(self):
        """Every non-transform, non-submodel node type should generate a config file."""
        nodes = [
            {
                "id": "a",
                "data": {"label": "a", "nodeType": "apiInput", "config": {"path": "d.json"}},
            },
            {
                "id": "b",
                "data": {
                    "label": "b",
                    "nodeType": "dataInput",
                    "config": {"input_type": "file", "format": "parquet", "path": "d.parquet"},
                },
            },
            {
                "id": "c",
                "data": {"label": "c", "nodeType": "liveSwitch", "config": {"inputs": ["a", "b"]}},
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
                "data": {
                    "label": "h",
                    "nodeType": "dataOutput",
                    "config": {"output_type": "file", "format": "parquet", "path": "o.parquet"},
                },
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
            {
                "id": "m",
                "data": {"label": "m", "nodeType": "scenarioExpander", "config": {"step_count": 3}},
            },
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
                                        "output_column": "fuel_band",
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
# Editor state stays out of the sidecar
# ---------------------------------------------------------------------------


def test_an_underscore_key_is_refused_at_the_sidecar_write(tmp_path):
    """Editor state travels beside the config, so an underscore key reaching the writer
    is undeclared: it is refused, never written and never silently dropped."""
    from haute.errors import ConfigError

    config = {"path": "d.parquet", "_steps_error": "Step 1: Pick a column."}
    with pytest.raises(ConfigError, match="_steps_error"):
        _write_node_config_sidecar(NodeType.DATA_INPUT, "test", config, tmp_path)
    assert not (tmp_path / config_path_for_node(NodeType.DATA_INPUT, "test")).exists()


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

    def test_windows_reserved_names_produce_paths_here_but_flag_as_reserved(self):
        """Path construction stays permissive; the predicate is the guard.

        ``config_path_for_node`` is not the reserved-name choke point — the
        save pipeline rejects reserved device stems on EVERY platform via
        ``is_windows_reserved_filename`` (see
        ``routes/_save_pipeline.py``), so this runs everywhere, not just
        on win32.
        """
        for name in ("CON", "PRN", "AUX"):
            p = config_path_for_node(NodeType.BANDING, name)
            assert p == Path(f"config/banding/{name}.json")
            assert is_windows_reserved_filename(p.name)


class TestIsWindowsReservedFilename:
    """All-platform truth table for the reserved device-name predicate.

    The stem before the FIRST dot is what Windows compares, case-
    insensitively and regardless of extension.
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "CON",
            "CON.json",
            "con.json",
            "Con.py",
            "PRN.json",
            "AUX.json",
            "NUL.py",
            "nul.tar.gz",  # stem before the FIRST dot
            "Com1.json",
            "COM9.py",
            "lpt1.json",
            "LPT9.py",
            "CON.",  # trailing dot stripped by Windows name resolution
            "CON .json",  # trailing space in stem stripped likewise
        ],
    )
    def test_reserved(self, filename):
        assert is_windows_reserved_filename(filename)

    @pytest.mark.parametrize(
        "filename",
        [
            "CONTRACT.json",
            "CONS.json",
            "COM.json",  # bare COM is not reserved, only COM1-COM9
            "COM10.json",  # two-digit units are ordinary names
            "COM0.json",
            "LPT.json",
            "LPT0.json",
            "LPT10.json",
            "NULL.json",
            "banding.json",
            ".json",  # empty stem
            "",
        ],
    )
    def test_not_reserved(self, filename):
        assert not is_windows_reserved_filename(filename)


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

    def test_banding_rule_map_under_an_unsupported_type_raises(self, tmp_path):
        """Only breakpoints and categorical rules have a compact map form."""
        p = tmp_path / "config" / "banding" / "bad.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": "age",
                            "output_column": "age_band",
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
      "output_column": "fuel_band",
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
        """A key that takes null (a cleared setting) keeps it through the write."""
        config = {"algorithm": "glm", "tuning": None, "alpha": None}
        _write_node_config_sidecar(NodeType.MODELLING, "model", config, tmp_path)
        loaded = load_node_config("config/model_training/model.json", base_dir=tmp_path)
        assert loaded["tuning"] is None
        assert loaded["alpha"] is None

    def test_null_where_a_key_does_not_take_it_is_refused(self, tmp_path):
        config = {"input_type": "file", "format": "parquet", "path": None}
        with pytest.raises(NodeConfigError, match="'path': Input should be a valid string"):
            _write_node_config_sidecar(NodeType.DATA_INPUT, "src", config, tmp_path)

    def test_underscore_keys_are_refused(self, tmp_path):
        from haute.errors import ConfigError

        config = {
            "input_type": "file",
            "format": "parquet",
            "path": "d.parquet",
            "_internal": "secret",
            "_cache": 42,
        }
        with pytest.raises(ConfigError, match="_cache"):
            _write_node_config_sidecar(NodeType.DATA_INPUT, "src", config, tmp_path)

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
                            "nodeType": "dataInput",
                            "config": {
                                "input_type": "file",
                                "format": "parquet",
                                "path": "d.parquet",
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )
        configs = collect_node_configs(graph)
        for path in configs:
            assert "\\" not in path


class TestRatingStepSidecars:
    def test_save_and_load_preserve_rating_rows(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["area"],
                    "output_column": "area_factor",
                    "default_value": "1.0",
                    "entries": [
                        {"area": "London", "value": "1.25"},
                        {"area": "Rural", "value": 0.85},
                    ],
                },
                {
                    "factors": ["vehicle_age_band", "cover_type"],
                    "output_column": "vehicle_factor",
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
            "combined_outputs": [
                {"output_column": "premium", "operation": "multiply", "base_value": 100}
            ],
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)
        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))

        # Each table is written as a grid (READ-26): one factor, a level and its rate a row;
        # two, the last factor's levels as columns and null where no entry is. The default
        # and each rate are written as held.
        one, two = saved["tables"]
        assert "entries" not in one and "columns" not in one
        assert one["default_value"] == "1.0"
        assert one["rows"] == [["London", "1.25"], ["Rural", 0.85]]
        assert "entries" not in two
        assert two["columns"] == ["comprehensive", "tpft"]
        assert two["rows"] == [["1-3", 0.9, 1.1], ["10+", 1.4, None]]
        assert saved["combined_outputs"] == config["combined_outputs"]
        assert load_node_config(rel, base_dir=tmp_path) == config

    def test_save_writes_three_factors_as_rows_of_two_and_columns_of_the_third(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "output_column": "vehicle_factor",
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

        # The last factor's levels are the columns and each row one combination of the
        # others, both in the order the entries first give them (READ-26).
        table = saved["tables"][0]
        assert "entries" not in table
        assert table["columns"] == ["confused", "compare_the_market"]
        assert table["rows"] == [
            ["1-3", "comprehensive", 0.91, None],
            ["4-5", "comprehensive", 0.96, None],
            ["1-3", "third_party_only", None, 1.08],
        ]
        # The entries come back in the grid's row-major order, here the order written, and
        # the combined outputs left out at their default read back as it (READ-10).
        assert load_node_config(rel, base_dir=tmp_path) == {**config, "combined_outputs": []}

    def test_save_keeps_sparse_three_factor_entries_sparse(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["vehicle_age_band", "cover_type", "channel"],
                    "output_column": "sparse_factor",
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

        # A cell with no entry is null, and reads back as no entry (READ-26).
        assert saved["tables"][0]["columns"] == ["confused", "broker"]
        assert saved["tables"][0]["rows"] == [
            ["1-3", "comprehensive", 0.91, None],
            ["10+", "third_party_only", None, 1.25],
        ]
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

    def test_collect_node_configs_writes_rating_rows(self):
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
                                        "factors": ["vehicle_age_band", "cover_type"],
                                        "output_column": "vehicle_factor",
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

        table = content["tables"][0]
        assert "entries" not in table
        assert (table["columns"], table["rows"]) == (["comprehensive"], [["1-3", 0.9]])

    def test_collect_node_configs_writes_three_factors_as_a_grid(self):
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
                                        "factors": [
                                            "vehicle_age_band",
                                            "cover_type",
                                            "channel",
                                        ],
                                        "output_column": "vehicle_factor",
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

        table = content["tables"][0]
        assert "entries" not in table
        assert table["factors"] == ["vehicle_age_band", "cover_type", "channel"]
        assert (table["columns"], table["rows"]) == (
            ["confused"],
            [["1-3", "comprehensive", 0.9]],
        )

    def test_save_preserves_ordered_duplicate_rating_factor_keys(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["area"],
                    "output_column": "area_factor",
                    "entries": [
                        {"area": 1, "value": 1.1},
                        {"area": "1", "value": 1.2},
                    ],
                }
            ]
        }

        rel = _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)
        expected_entries = config["tables"][0]["entries"]

        saved = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        # Levels keep their JSON type in the grid, so 1 and "1" stay two rows (READ-26).
        assert saved["tables"][0]["rows"] == [[1, 1.1], ["1", 1.2]]
        assert load_node_config(rel, base_dir=tmp_path)["tables"][0]["entries"] == expected_entries

    def test_save_rejects_entries_missing_factor_values(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["vehicle_age_band", "cover_type"],
                    "output_column": "vehicle_factor",
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
                    "factors": ["area"],
                    "output_column": "area_factor",
                    "entries": [{"area": "London", "value": ""}],
                }
            ]
        }

        with pytest.raises(
            ValueError,
            match="ratingStep tables\\[0\\].entries\\[0\\] requires value",
        ):
            _write_node_config_sidecar(NodeType.RATING_STEP, "adjustments", config, tmp_path)

    def test_save_refuses_a_rating_entry_key_its_grid_cannot_keep(self, tmp_path):
        config = {
            "tables": [
                {
                    "factors": ["area"],
                    "output_column": "area_factor",
                    "entries": [{"area": "London", "value": 1.25, "note": "kept"}],
                }
            ]
        }

        # A grid holds each entry's levels and rate alone (READ-26): a key it cannot keep is
        # refused rather than lost.
        with pytest.raises(ConfigSettingError, match="holding 'note'"):
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
                            "factors": "area",
                            "output_column": "area_factor",
                            "rows": [["London", 1.25]],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(
            ValueError, match="ratingStep tables\\[0\\].factors must be a non-empty list"
        ):
            load_node_config(path)

    def test_load_rejects_duplicate_rating_json_keys(self, tmp_path):
        path = tmp_path / "config" / "rating_step" / "duplicate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            """
{
  "tables": [
    {
      "factors": ["area"],
      "output_column": "area_factor",
      "default_value": 1.25,
      "default_value": 1.1,
      "rows": [["London", 1.25]]
    }
  ]
}
""",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="duplicate JSON key 'default_value'"):
            load_node_config(path)


class TestGlobalConstantsFile:
    """The canonical ``config/global_constants.json`` loader and serialiser."""

    CANONICAL = (
        "{\n"
        '  "constants": [\n'
        "    {\n"
        '      "name": "inflation",\n'
        '      "type": "float",\n'
        '      "value": 1.05\n'
        "    },\n"
        "    {\n"
        '      "name": "constant_x",\n'
        '      "type": "integer",\n'
        # A map of numbers alone is written on one line (READ-10).
        '      "by_source": {"live": 1, "nb_batch": 2}\n'
        "    },\n"
        "    {\n"
        '      "name": "label",\n'
        '      "type": "text",\n'
        '      "value": "café"\n'
        "    },\n"
        "    {\n"
        '      "name": "flag",\n'
        '      "type": "boolean",\n'
        '      "value": false\n'
        "    },\n"
        "    {\n"
        '      "name": "as_at",\n'
        '      "type": "date",\n'
        '      "by_source": {\n'
        '        "live": "2026-10-02"\n'
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )

    def test_the_canonical_file_round_trips_byte_identically(self) -> None:
        from haute._config_io import global_constants_json, parse_global_constants

        constants = parse_global_constants(self.CANONICAL.encode("utf-8"))

        assert [constant.name for constant in constants] == [
            "inflation",
            "constant_x",
            "label",
            "flag",
            "as_at",
        ]
        assert constants[1].by_source == {"live": 1, "nb_batch": 2}
        assert global_constants_json(constants) == self.CANONICAL

    def test_a_float_written_as_a_whole_number_reads_and_writes_as_a_float(self) -> None:
        from haute._config_io import global_constants_json, parse_global_constants

        raw = json.dumps({"constants": [{"name": "rate", "type": "float", "value": 1}]})
        constants = parse_global_constants(raw.encode("utf-8"))

        assert constants[0].value == 1.0
        assert isinstance(constants[0].value, float)
        assert '"value": 1.0' in global_constants_json(constants)

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"constants": [], "extra": 1}, "unknown key(s) ['extra']"),
            ({}, "must have a 'constants' list"),
            (
                {"constants": [{"name": "a", "type": "float", "value": 1, "oops": 1}]},
                "constant 1 ('a'): unknown key 'oops'",
            ),
            (
                {"constants": [{"name": "a", "type": "float", "value": 1, "by_source": {}}]},
                "needs exactly one of 'value'",
            ),
            ({"constants": [{"name": "a", "type": "float"}]}, "needs exactly one of 'value'"),
            (
                {"constants": [{"name": "1a", "type": "float", "value": 1}]},
                "must start with a letter",
            ),
            (
                {"constants": [{"name": "_a", "type": "float", "value": 1}]},
                "must start with a letter",
            ),
            ({"constants": [{"name": "class", "type": "float", "value": 1}]}, "Python keyword"),
            (
                {
                    "constants": [
                        {"name": "a", "type": "float", "value": 1},
                        {"name": "a", "type": "float", "value": 2},
                    ]
                },
                "constant 2 ('a'): the name 'a' is already defined",
            ),
            ({"constants": [{"name": "a", "type": "integer", "value": 1.5}]}, "a whole number"),
            ({"constants": [{"name": "a", "type": "integer", "value": True}]}, "a whole number"),
            ({"constants": [{"name": "a", "type": "float", "value": "1"}]}, "a finite number"),
            ({"constants": [{"name": "a", "type": "text", "value": 1}]}, "must be text"),
            ({"constants": [{"name": "a", "type": "boolean", "value": 1}]}, "true or false"),
            ({"constants": [{"name": "a", "type": "date", "value": "2026-13-01"}]}, "a real date"),
            ({"constants": [{"name": "a", "type": "date", "value": "2026-1-1"}]}, "a real date"),
            (
                {"constants": [{"name": "a", "type": "float", "by_source": {"": 1}}]},
                "a source name must be non-empty",
            ),
            (
                {"constants": [{"name": "a", "type": "decimal", "value": 1}]},
                "type: Input should be",
            ),
        ],
    )
    def test_an_invalid_file_names_the_entry_and_the_field(
        self,
        payload: dict[str, Any],
        message: str,
    ) -> None:
        from haute._config_io import parse_global_constants
        from haute.errors import ConfigError

        with pytest.raises(ConfigError) as excinfo:
            parse_global_constants(json.dumps(payload).encode("utf-8"))

        assert str(excinfo.value).startswith("config/global_constants.json ")
        assert message in str(excinfo.value)

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b'\xef\xbb\xbf{"constants": []}', "Unexpected UTF-8 BOM"),
            (b'{"constants": [], "constants": []}', "duplicate JSON key 'constants'"),
            (b"[]", "must hold a JSON object"),
            (b"\xff\xfe", "not valid JSON"),
        ],
    )
    def test_bytes_that_are_not_one_strict_utf8_json_object_are_refused(
        self,
        raw: bytes,
        message: str,
    ) -> None:
        from haute._config_io import parse_global_constants
        from haute.errors import ConfigError

        with pytest.raises(ConfigError, match=message):
            parse_global_constants(raw)
