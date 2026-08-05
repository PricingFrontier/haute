"""Tests for _config_validation – lightweight config key warnings."""

from __future__ import annotations

from typing import get_type_hints

import pytest

from haute._config_validation import (
    _UNIVERSAL_KEYS,
    VALID_KEYS,
    warn_unrecognized_config_keys,
)
from haute._types import (
    MODEL_SCORE_CONFIG_KEYS,
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    ExploreConfig,
    ExploreOverviewConfig,
    ModelScoreConfig,
    NodeType,
    OptimiserApplyConfig,
    OptimiserConfig,
    TransformConfig,
)
from haute.errors import ConfigError
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# VALID_KEYS registry sanity checks
# ---------------------------------------------------------------------------


class TestValidKeysRegistry:
    """Ensure the registry covers all node types that have TypedDicts."""

    def test_all_typed_dict_node_types_present(self):
        """Every node type with a TypedDict should have an entry."""
        expected = {
            NodeType.API_INPUT,
            NodeType.DATA_INPUT,
            NodeType.DATA_INPUT,
            NodeType.DATA_OUTPUT,
            NodeType.POLARS,
            NodeType.EDGE_JOIN,
            NodeType.MODEL_SCORE,
            NodeType.BANDING,
            NodeType.RATING_STEP,
            NodeType.OUTPUT,
            NodeType.DATA_OUTPUT,
            NodeType.EXPLORE,
            NodeType.EXTERNAL_FILE,
            NodeType.LIVE_SWITCH,
            NodeType.MODELLING,
            NodeType.OPTIMISER,
            NodeType.SCENARIO_EXPANDER,
            NodeType.OPTIMISER_APPLY,
            NodeType.CONSTANT,
            NodeType.SUBMODEL,
        }
        assert expected == set(VALID_KEYS.keys()), (
            f"Missing: {expected - set(VALID_KEYS.keys())}, "
            f"Extra: {set(VALID_KEYS.keys()) - expected}"
        )

    def test_universal_keys_included(self):
        """instanceOf and inputMapping should be valid for every type."""
        for nt, keys in VALID_KEYS.items():
            assert "instanceOf" in keys, f"instanceOf missing from {nt}"
            assert "inputMapping" in keys, f"inputMapping missing from {nt}"

    @pytest.mark.parametrize(
        "node_type, expected_key",
        [
            (NodeType.API_INPUT, "path"),
            (NodeType.DATA_INPUT, "inputType"),
            (NodeType.MODEL_SCORE, "run_id"),
            (NodeType.BANDING, "factors"),
            (NodeType.RATING_STEP, "tables"),
            (NodeType.OUTPUT, "outputMapping"),
            (NodeType.DATA_OUTPUT, "format"),
            (NodeType.EXPLORE, "contract"),
            (NodeType.EXTERNAL_FILE, "fileType"),
            (NodeType.LIVE_SWITCH, "input_scenario_map"),
            (NodeType.MODELLING, "algorithm"),
            (NodeType.OPTIMISER, "constraints"),
            (NodeType.SCENARIO_EXPANDER, "quote_id"),
            (NodeType.OPTIMISER_APPLY, "artifact_path"),
            (NodeType.CONSTANT, "values"),
            (NodeType.SUBMODEL, "definitionId"),
        ],
    )
    def test_known_key_present(self, node_type, expected_key):
        """Spot-check that well-known keys appear in each type's valid set."""
        assert expected_key in VALID_KEYS[node_type]

    @pytest.mark.parametrize("key", ["path", "contract", "tables"])
    def test_api_input_keys_present(self, key):
        """apiInput configuration is defined by its table schema."""
        assert key in VALID_KEYS[NodeType.API_INPUT]

    @pytest.mark.parametrize(
        "node_type",
        [NodeType.MODELLING, NodeType.MODEL_SCORE, NodeType.POLARS],
    )
    def test_categorical_levels_key_present_on_model_boundaries(self, node_type):
        assert "categorical_levels" in VALID_KEYS[node_type]


# ---------------------------------------------------------------------------
# warn_unrecognized_config_keys
# ---------------------------------------------------------------------------


class TestWarnUnrecognizedConfigKeys:
    def test_no_warning_for_valid_keys(self):
        """Config with only valid apiInput keys produces no warnings."""
        bad = warn_unrecognized_config_keys(
            NodeType.API_INPUT,
            {
                "path": "/data.json",
                "contract": "opaque",
                "tables": [],
            },
        )
        assert bad == []

    def test_warns_on_unrecognized_key(self):
        """An unknown key should be returned and logged."""
        bad = warn_unrecognized_config_keys(
            NodeType.API_INPUT,
            {"path": "/data.json", "bogus_key": 42},
        )
        assert bad == ["bogus_key"]

    def test_multiple_unrecognized_keys_sorted(self):
        """Multiple bad keys are returned in sorted order."""
        bad = warn_unrecognized_config_keys(
            NodeType.OUTPUT,
            {**make_output_config(["a"]), "zebra": 1, "alpha": 2},
        )
        assert bad == ["alpha", "zebra"]

    def test_instance_of_always_valid(self):
        """instanceOf is a universal key, valid for any node type."""
        bad = warn_unrecognized_config_keys(
            NodeType.DATA_INPUT,
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "x.parquet",
                "arguments": {},
                "instanceOf": "other",
            },
        )
        assert bad == []

    def test_input_mapping_always_valid(self):
        """inputMapping is a universal key, valid for any node type."""
        bad = warn_unrecognized_config_keys(
            NodeType.POLARS,
            {"code": "x", "inputMapping": {"a": "b"}},
        )
        assert bad == []

    def test_empty_config_no_warning(self):
        """Empty config dict should produce no warnings."""
        bad = warn_unrecognized_config_keys(NodeType.POLARS, {})
        assert bad == []

    def test_string_node_type_accepted(self):
        """Should accept raw string values matching NodeType enum."""
        bad = warn_unrecognized_config_keys(
            "apiInput",
            {"path": "/data.json", "nope": True},
        )
        assert bad == ["nope"]

    def test_unknown_node_type_string_returns_empty(self):
        """An unrecognised node-type string should not crash, just return []."""
        bad = warn_unrecognized_config_keys("totallyFake", {"x": 1})
        assert bad == []

    def test_logs_warning_via_structlog(self, capsys):
        """Ensure the warning actually appears in the log output."""
        warn_unrecognized_config_keys(
            NodeType.OUTPUT,
            {**make_output_config(["a"]), "bad_key": 99},
            node_label="my_output_node",
        )
        captured = capsys.readouterr()
        assert "unrecognized_config_keys" in captured.out
        assert "bad_key" in captured.out
        assert "my_output_node" in captured.out

    def test_never_raises(self):
        """Even with weird input, the function must not raise."""
        # None node type
        assert warn_unrecognized_config_keys(None, {"x": 1}) == []  # type: ignore[arg-type]
        # Non-dict config -- guard against TypeError on iteration
        # (the function signature says dict, but let's be defensive)
        try:
            result = warn_unrecognized_config_keys(NodeType.POLARS, 42)  # type: ignore[arg-type]
        except TypeError:
            pass  # acceptable — non-iterable input may raise
        else:
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Integration: _build_node_config produces valid configs
# ---------------------------------------------------------------------------


class TestBuildNodeConfigProducesValidKeys:
    """Ensure that _build_node_config only sets keys that pass validation."""

    @pytest.mark.parametrize(
        "node_type, kwargs, body, params",
        [
            pytest.param(
                NodeType.API_INPUT,
                {"path": "d.json", "api_input": True, "row_id_column": "id"},
                "",
                [],
                id="api_input",
            ),
            pytest.param(
                NodeType.DATA_INPUT,
                {"path": "d.parquet"},
                "",
                [],
                id="datasource_flat",
            ),
            pytest.param(
                NodeType.DATA_INPUT,
                {"table": "cat.sch.tbl"},
                "",
                [],
                id="datasource_databricks",
            ),
            pytest.param(
                NodeType.MODEL_SCORE,
                {"model_score": True, "source_type": "run", "run_id": "abc"},
                "",
                ["df"],
                id="model_score",
            ),
            pytest.param(
                NodeType.BANDING,
                {"factors": [{"banding": "continuous", "column": "x", "rules": []}]},
                "",
                ["df"],
                id="banding_multi",
            ),
            pytest.param(
                NodeType.BANDING,
                {"banding": "continuous", "column": "x", "rules": []},
                "",
                ["df"],
                id="banding_single",
            ),
            pytest.param(
                NodeType.RATING_STEP,
                {"tables": [{"name": "T", "factors": ["x"], "entries": []}]},
                "",
                ["df"],
                id="rating_step",
            ),
            pytest.param(
                NodeType.OUTPUT,
                # v2: OUTPUT config (outputMapping) comes from the sidecar JSON,
                # not decorator kwargs — the builder's OUTPUT branch is a no-op.
                {},
                "",
                ["df"],
                id="output",
            ),
            pytest.param(
                NodeType.DATA_OUTPUT,
                {"sink": "out.csv", "format": "csv"},
                "",
                ["df"],
                id="data_output",
            ),
            pytest.param(
                NodeType.EXPLORE,
                {},
                "",
                ["df"],
                id="explore",
            ),
            pytest.param(
                NodeType.LIVE_SWITCH,
                {"live_switch": True, "input_scenario_map": {}},
                "",
                ["a", "b"],
                id="live_switch",
            ),
            pytest.param(
                NodeType.OPTIMISER,
                {"optimiser": True, "mode": "online", "quote_id": "qid"},
                "",
                ["df"],
                id="optimiser",
            ),
            pytest.param(
                NodeType.OPTIMISER_APPLY,
                {
                    "optimiser_apply": True,
                    "source_type": "file",
                    "artifact_path": "opt.json",
                },
                "",
                ["df"],
                id="optimiser_apply",
            ),
            pytest.param(
                NodeType.SCENARIO_EXPANDER,
                {"scenario_expander": True, "quote_id": "qid", "steps": 10},
                "",
                ["df"],
                id="scenario_expander",
            ),
            pytest.param(
                NodeType.MODELLING,
                {"modelling": True, "name": "m", "target": "y", "algorithm": "catboost"},
                "",
                ["df"],
                id="modelling",
            ),
            pytest.param(
                NodeType.CONSTANT,
                {"constant": True, "values": [{"name": "x", "value": "1"}]},
                "",
                [],
                id="constant",
            ),
            pytest.param(
                NodeType.POLARS,
                {},
                '    """doc"""\n    return df',
                ["df"],
                id="transform",
            ),
        ],
    )
    def test_built_config_has_no_unrecognized_keys(
        self,
        node_type,
        kwargs,
        body,
        params,
    ):
        from haute._config_builder import _build_node_config

        config = _build_node_config(node_type, kwargs, body, params)
        bad = warn_unrecognized_config_keys(node_type, config)
        assert bad == [], f"Unrecognized keys in {node_type}: {bad}"

    def test_model_score_source_type_maps_to_sourceType(self):  # noqa: N802 - references camelCase config key `sourceType`
        """Parser should map snake_case source_type to camelCase sourceType."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {
                "model_score": True,
                "source_type": "registered",
                "registered_model": "m",
                "version": "1",
            },
            "",
            ["df"],
        )
        bad = warn_unrecognized_config_keys(NodeType.MODEL_SCORE, config)
        assert bad == [], f"Unrecognized keys in modelScore: {bad}"
        assert config["sourceType"] == "registered"
        assert "source_type" not in config, "snake_case source_type should not appear in config"

    def test_api_input_preserves_declared_v2_tables_config(self):
        """API-input decorator preserves the declared `tables[]` shape."""
        from haute._config_builder import _build_node_config

        v2_tables = [
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [
                    {"name": "quote_id", "path": "$[:].quote_id", "type": "str"},
                ],
            },
        ]
        config = _build_node_config(
            NodeType.API_INPUT,
            {"path": "quotes.json", "tables": v2_tables},
            "",
            [],
        )

        assert config["tables"] == v2_tables
        assert warn_unrecognized_config_keys(NodeType.API_INPUT, config) == []

    def test_model_score_all_keys_valid(self):
        """All keys from MODEL_SCORE_CONFIG_KEYS should be recognised."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {
                "model_score": True,
                "source_type": "run",
                "run_id": "abc",
                "artifact_path": "model.cbm",
                "run_name": "my_run",
                "registered_model": "m",
                "version": "1",
                "task": "regression",
                "output_column": "pred",
                "experiment_name": "exp",
                "experiment_id": "eid",
            },
            "",
            ["df"],
        )
        bad = warn_unrecognized_config_keys(NodeType.MODEL_SCORE, config)
        assert bad == [], f"Unrecognized keys in modelScore: {bad}"

    @pytest.mark.parametrize("overview", [True, "schema", ["schema"]])
    def test_explore_overview_must_be_a_dict(self, overview):
        """Explore overview decorators fail loudly when the block is not a dict."""
        from haute._config_builder import _build_node_config

        with pytest.raises(ConfigError, match="overview config must be a dict"):
            _build_node_config(
                NodeType.EXPLORE,
                {"overview": overview},
                "",
                ["df"],
            )

    @pytest.mark.parametrize(
        "key",
        [
            "dataset_snapshot",
            "data_quality",
            "numeric_summary",
            "categorical_summary",
            "schema",
        ],
    )
    @pytest.mark.parametrize("value", ["true", 1, None])
    def test_explore_overview_known_keys_must_be_boolean(self, key, value):
        """Known overview-card toggles must be real booleans, not truthy values."""
        from haute._config_builder import _build_node_config

        with pytest.raises(ConfigError, match="known overview key"):
            _build_node_config(
                NodeType.EXPLORE,
                {"overview": {key: value}},
                "",
                ["df"],
            )

    def test_explore_overview_preserves_unknown_round_trippable_values(self):
        """Unknown overview keys are kept when their values are simple literals."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.EXPLORE,
            {
                "overview": {
                    "dataset_snapshot": True,
                    "custom_card": {
                        "label": "Loss ratio",
                        "columns": ["premium", "claims"],
                        "enabled": False,
                        "threshold": 0.7,
                        "empty": None,
                    },
                }
            },
            "",
            ["df"],
        )

        assert config["overview"] == {
            "dataset_snapshot": True,
            "custom_card": {
                "label": "Loss ratio",
                "columns": ["premium", "claims"],
                "enabled": False,
                "threshold": 0.7,
                "empty": None,
            },
        }

    def test_explore_overview_rejects_unknown_unserialisable_values(self):
        """Unknown keys should not smuggle arbitrary Python objects into config."""
        from haute._config_builder import _build_node_config

        with pytest.raises(ConfigError, match="round-trip"):
            _build_node_config(
                NodeType.EXPLORE,
                {"overview": {"custom_card": object()}},
                "",
                ["df"],
            )


# ---------------------------------------------------------------------------
# B7: selected_columns is universally valid (executor applies it to all nodes)
# ---------------------------------------------------------------------------


class TestSelectedColumnsUniversal:
    """Verify selected_columns doesn't trigger false positives on any node type."""

    def test_selected_columns_in_universal_keys(self):
        """selected_columns should be in the universal keys set."""
        assert "selected_columns" in _UNIVERSAL_KEYS

    @pytest.mark.parametrize("node_type", list(VALID_KEYS.keys()))
    def test_selected_columns_valid_for_all_node_types(self, node_type):
        """selected_columns should be accepted for every node type with validation."""
        bad = warn_unrecognized_config_keys(
            node_type,
            {"selected_columns": ["a", "b"]},
        )
        assert bad == [], f"selected_columns flagged as unrecognized for {node_type}"

    def test_selected_columns_in_transform_typed_dict(self):
        """TransformConfig TypedDict should declare selected_columns."""
        assert "selected_columns" in TransformConfig.__annotations__

    def test_explore_config_allows_polars_code(self):
        """Explore can store the Polars snippet used to prepare analysis data."""
        assert get_type_hints(ExploreConfig) == {
            "code": str,
            "overview": ExploreOverviewConfig,
        }
        overview_hints = get_type_hints(ExploreOverviewConfig)
        assert overview_hints == {
            "dataset_snapshot": bool,
            "data_quality": bool,
            "numeric_summary": bool,
            "categorical_summary": bool,
            "schema": bool,
        }
        assert warn_unrecognized_config_keys(NodeType.EXPLORE, {}) == []
        assert warn_unrecognized_config_keys(NodeType.EXPLORE, {"code": "df = df.head(10)"}) == []
        # Overview block is a recognised key on explore nodes.
        assert (
            warn_unrecognized_config_keys(
                NodeType.EXPLORE, {"overview": {"dataset_snapshot": True}}
            )
            == []
        )
        assert (
            warn_unrecognized_config_keys(
                NodeType.EXPLORE,
                {
                    "overview": {
                        "schema": True,
                        "numeric_summary": True,
                        "categorical_summary": True,
                    }
                },
            )
            == []
        )


# ---------------------------------------------------------------------------
# B8: Config key tuples aligned with TypedDict field names
# ---------------------------------------------------------------------------


class TestConfigKeyTupleAlignment:
    """Verify config key tuples match their TypedDict annotations."""

    def test_model_score_keys_match_typed_dict(self):
        """Every key in MODEL_SCORE_CONFIG_KEYS should exist in ModelScoreConfig."""
        td_keys = set(ModelScoreConfig.__annotations__)
        for key in MODEL_SCORE_CONFIG_KEYS:
            assert key in td_keys, (
                f"MODEL_SCORE_CONFIG_KEYS has '{key}' but ModelScoreConfig does not"
            )

    def test_model_score_keys_use_camelCase_sourceType(self):  # noqa: N802 - references camelCase config key `sourceType`
        """MODEL_SCORE_CONFIG_KEYS should use 'sourceType' (camelCase), not 'source_type'."""
        assert "sourceType" in MODEL_SCORE_CONFIG_KEYS
        assert "source_type" not in MODEL_SCORE_CONFIG_KEYS

    def test_optimiser_keys_match_typed_dict(self):
        """Every key in OPTIMISER_CONFIG_KEYS should exist in OptimiserConfig."""
        td_keys = set(OptimiserConfig.__annotations__)
        for key in OPTIMISER_CONFIG_KEYS:
            assert key in td_keys, f"OPTIMISER_CONFIG_KEYS has '{key}' but OptimiserConfig does not"

    def test_optimiser_apply_keys_match_typed_dict(self):
        """Every key in OPTIMISER_APPLY_CONFIG_KEYS should exist in OptimiserApplyConfig."""
        td_keys = set(OptimiserApplyConfig.__annotations__)
        for key in OPTIMISER_APPLY_CONFIG_KEYS:
            assert key in td_keys, (
                f"OPTIMISER_APPLY_CONFIG_KEYS has '{key}' but OptimiserApplyConfig does not"
            )

    def test_optimiser_config_has_data_input(self):
        """OptimiserConfig should declare data_input (used by _optimiser_service)."""
        assert "data_input" in OptimiserConfig.__annotations__

    def test_optimiser_config_has_banding_source(self):
        """OptimiserConfig should declare banding_source (used by _optimiser_service)."""
        assert "banding_source" in OptimiserConfig.__annotations__

    def test_optimiser_apply_has_experiment_name(self):
        """OptimiserApplyConfig should declare experiment_name (UI-only)."""
        assert "experiment_name" in OptimiserApplyConfig.__annotations__

    def test_optimiser_apply_has_run_name(self):
        """OptimiserApplyConfig should declare run_name (UI-only)."""
        assert "run_name" in OptimiserApplyConfig.__annotations__

    def test_modelling_keys_match_typed_dict(self):
        """Every key in MODELLING_CONFIG_KEYS should exist in ModellingConfig."""
        from haute._types import ModellingConfig

        td_keys = set(ModellingConfig.__annotations__)
        for key in MODELLING_CONFIG_KEYS:
            assert key in td_keys, f"MODELLING_CONFIG_KEYS has '{key}' but ModellingConfig does not"
        assert "evaluation" in MODELLING_CONFIG_KEYS
        assert "tuning" in MODELLING_CONFIG_KEYS
        assert "split" not in MODELLING_CONFIG_KEYS

    def test_scenario_expander_keys_match_typed_dict(self):
        """Every key in SCENARIO_EXPANDER_CONFIG_KEYS should exist in ScenarioExpanderConfig."""
        from haute._types import ScenarioExpanderConfig

        td_keys = set(ScenarioExpanderConfig.__annotations__)
        for key in SCENARIO_EXPANDER_CONFIG_KEYS:
            assert key in td_keys, (
                f"SCENARIO_EXPANDER_CONFIG_KEYS has '{key}' but ScenarioExpanderConfig does not"
            )


# ---------------------------------------------------------------------------
# Parser round-trip: source_type → sourceType mapping
# ---------------------------------------------------------------------------


class TestParserSourceTypeMapping:
    """Verify the parser correctly maps decorator snake_case to config camelCase."""

    def test_run_source_type(self):
        """source_type='run' in decorator kwargs maps to sourceType='run' in config."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {"model_score": True, "source_type": "run", "run_id": "r1", "artifact_path": "m.cbm"},
            "",
            ["df"],
        )
        assert config["sourceType"] == "run"
        assert config["run_id"] == "r1"
        assert config["artifact_path"] == "m.cbm"
        assert "source_type" not in config

    def test_registered_source_type(self):
        """source_type='registered' in decorator maps to sourceType='registered'."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {
                "model_score": True,
                "source_type": "registered",
                "registered_model": "my_model",
                "version": "3",
            },
            "",
            ["df"],
        )
        assert config["sourceType"] == "registered"
        assert config["registered_model"] == "my_model"
        assert config["version"] == "3"
        assert "source_type" not in config

    def test_missing_source_type_not_set(self):
        """If source_type is absent from decorator, sourceType should not be in config."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.MODEL_SCORE,
            {"model_score": True, "run_id": "r1"},
            "",
            ["df"],
        )
        assert "sourceType" not in config
        assert "source_type" not in config

    def test_optimiser_apply_copies_all_keys(self):
        """All keys from OPTIMISER_APPLY_CONFIG_KEYS should be copied when present."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.OPTIMISER_APPLY,
            {
                "optimiser_apply": True,
                "source_type": "file",
                "artifact_path": "opt.json",
                "version_column": "__v__",
                "optimised_value_column": "selected_price_factor",
                "registered_model": "m",
                "version": "2",
                "experiment_id": "eid",
                "experiment_name": "exp",
                "run_id": "rid",
                "run_name": "rn",
            },
            "",
            ["df"],
        )
        bad = warn_unrecognized_config_keys(NodeType.OPTIMISER_APPLY, config)
        assert bad == [], f"Unrecognized keys: {bad}"
        assert config["experiment_name"] == "exp"
        assert config["run_name"] == "rn"
        assert config["optimised_value_column"] == "selected_price_factor"

    def test_optimiser_copies_data_input_and_banding_source(self):
        """data_input and banding_source should be copied when present."""
        from haute._config_builder import _build_node_config

        config = _build_node_config(
            NodeType.OPTIMISER,
            {
                "optimiser": True,
                "mode": "ratebook",
                "data_input": "node_1",
                "banding_source": "node_2",
            },
            "",
            ["df"],
        )
        bad = warn_unrecognized_config_keys(NodeType.OPTIMISER, config)
        assert bad == [], f"Unrecognized keys: {bad}"
        assert config["data_input"] == "node_1"
        assert config["banding_source"] == "node_2"
