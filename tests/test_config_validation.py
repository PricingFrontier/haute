"""Tests for _config_validation – declared config keys and their enforcement."""

from __future__ import annotations

from typing import Any, Literal, get_type_hints

import pytest

from haute._config_validation import (
    _UNIVERSAL_KEYS,
    VALID_KEYS,
    reject_unrecognized_config_keys,
    unrecognized_config_keys,
)
from haute._types import (
    MODEL_SCORE_CONFIG_KEYS,
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    EdgeJoinConfig,
    ExploreChartAxes,
    ExploreChartCategory,
    ExploreChartConfig,
    ExploreChartLegend,
    ExploreChartSeriesOverride,
    ExploreChartValueEncoding,
    ExploreConfig,
    ExploreOverviewConfig,
    ExplorePivotAxisPlacement,
    ExplorePivotConfig,
    ExplorePivotFilterPlacement,
    ExplorePivotFormula,
    ExplorePivotOptions,
    ExplorePivotPersistedConfig,
    ExplorePivotRowPlacement,
    ExplorePivotValuePlacement,
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
# unrecognized_config_keys / reject_unrecognized_config_keys
# ---------------------------------------------------------------------------


class TestUnrecognizedConfigKeys:
    def test_declared_keys_are_recognised(self):
        assert (
            unrecognized_config_keys(
                NodeType.API_INPUT,
                {"path": "/data.json", "contract": "opaque", "tables": []},
            )
            == []
        )

    def test_undeclared_keys_are_reported_sorted(self):
        assert unrecognized_config_keys(
            NodeType.OUTPUT,
            {**make_output_config(["a"]), "zebra": 1, "alpha": 2},
        ) == ["alpha", "zebra"]

    def test_universal_keys_are_declared_for_every_node_type(self):
        assert (
            unrecognized_config_keys(
                NodeType.POLARS,
                {"code": "x", "inputMapping": {"a": "b"}, "instanceOf": "other"},
            )
            == []
        )

    def test_editor_state_and_body_code_are_not_config_keys(self):
        """`_`-prefixed editor state and the `.py` body code are not persisted keys."""
        assert (
            unrecognized_config_keys(
                NodeType.BANDING,
                {"factors": [], "_columns": ["a"], "_nodeId": "n1", "code": "df = df"},
            )
            == []
        )

    def test_a_node_type_without_a_typed_dict_has_no_key_check(self):
        assert unrecognized_config_keys(NodeType.SUBMODEL_PORT, {"anything": 1}) == []

    def test_reject_names_the_node_its_type_and_every_undeclared_key(self):
        with pytest.raises(
            ConfigError, match=r"'quote_response'.*output.*'alpha', 'zebra'"
        ) as refused:
            reject_unrecognized_config_keys(
                NodeType.OUTPUT,
                {**make_output_config(["a"]), "zebra": 1, "alpha": 2},
                node_label="quote_response",
            )
        assert refused.value.context["unrecognized_config_keys"] == ["alpha", "zebra"]

    def test_reject_accepts_a_declared_config(self):
        reject_unrecognized_config_keys(
            NodeType.OUTPUT, make_output_config(["a"]), node_label="quote_response"
        )


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
                {"scenario_expander": True, "quote_id": "qid", "stepCount": 10},
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
        bad = unrecognized_config_keys(node_type, config)
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
        bad = unrecognized_config_keys(NodeType.MODEL_SCORE, config)
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
        assert unrecognized_config_keys(NodeType.API_INPUT, config) == []

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
        bad = unrecognized_config_keys(NodeType.MODEL_SCORE, config)
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


class TestSharedColumnSettingsUniversal:
    """Shared authored column settings are accepted consistently across nodes."""

    def test_shared_column_settings_in_universal_keys(self):
        assert {"selected_columns", "column_renames", "categorical_levels"} <= _UNIVERSAL_KEYS

    @pytest.mark.parametrize("node_type", list(VALID_KEYS.keys()))
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("selected_columns", ["_id", "premium"]),
            ("column_renames", {"_id": "identifier"}),
            ("categorical_levels", {"_category": ["low", "high", None]}),
        ],
    )
    def test_shared_column_settings_valid_for_all_node_types(self, node_type, key, value):
        bad = unrecognized_config_keys(
            node_type,
            {key: value},
        )
        assert bad == [], f"{key} flagged as unrecognized for {node_type}"

    @pytest.mark.parametrize("config_type", [TransformConfig, EdgeJoinConfig, ExploreConfig])
    def test_shared_column_settings_in_inline_typed_dicts(self, config_type):
        hints = get_type_hints(config_type)
        assert hints["selected_columns"] == list[str]
        assert hints["column_renames"] == dict[str, str]
        assert hints["categorical_levels"] == dict[str, list[str | None]]

    def test_explore_config_allows_polars_code(self):
        """Explore can store the Polars snippet used to prepare analysis data."""
        assert get_type_hints(ExploreConfig) == {
            "code": str,
            "steps": list[dict[str, Any]],
            "overview": ExploreOverviewConfig,
            "pivot_formulas": list[ExplorePivotFormula],
            "pivots": list[ExplorePivotPersistedConfig],
            "charts": list[ExploreChartConfig],
            "selected_columns": list[str],
            "column_renames": dict[str, str],
            "categorical_levels": dict[str, list[str | None]],
        }
        assert get_type_hints(ExploreChartConfig) == {
            "version": Literal[1],
            "id": str,
            "name": str,
            "enabled": bool,
            "pivot_id": str | None,
            "kind": Literal["combo"],
            "orientation": Literal["vertical", "horizontal"],
            "category": ExploreChartCategory,
            "value_encodings": list[ExploreChartValueEncoding],
            "series_overrides": list[ExploreChartSeriesOverride],
            "axes": ExploreChartAxes,
            "legend": ExploreChartLegend,
        }
        assert get_type_hints(ExplorePivotConfig) == {
            "version": Literal[1],
            "id": str,
            "name": str,
            "enabled": bool,
            "filters": list[ExplorePivotFilterPlacement],
            "columns": list[ExplorePivotAxisPlacement],
            "rows": list[ExplorePivotRowPlacement],
            "values": list[ExplorePivotValuePlacement],
            "formulas": list[ExplorePivotFormula],
            "value_order": list[str],
            "options": ExplorePivotOptions,
        }
        assert get_type_hints(ExplorePivotPersistedConfig) == {
            **{
                key: value
                for key, value in get_type_hints(ExplorePivotConfig).items()
                if key != "formulas"
            },
            "formulas": list[str],
        }
        assert get_type_hints(ExplorePivotOptions) == {
            "row_grand_totals": bool,
            "column_grand_totals": bool,
            "sort_by": str | None,
        }
        assert get_type_hints(ExplorePivotAxisPlacement)["decimal_places"] == int | None
        assert get_type_hints(ExplorePivotRowPlacement)["decimal_places"] == int | None
        assert get_type_hints(ExplorePivotValuePlacement)["decimal_places"] == int | None
        assert get_type_hints(ExplorePivotValuePlacement)["reference"] is str
        assert get_type_hints(ExplorePivotFormula)["reference"] is str
        assert get_type_hints(ExplorePivotFormula)["expression"] is str
        assert get_type_hints(ExplorePivotFormula)["decimal_places"] == int | None
        pivot_number_format = Literal[
            "general",
            "number",
            "percent",
            "currency_gbp",
            "currency_usd",
            "currency_eur",
        ]
        assert get_type_hints(ExplorePivotAxisPlacement)["number_format"] == pivot_number_format
        assert get_type_hints(ExplorePivotRowPlacement)["number_format"] == pivot_number_format
        assert get_type_hints(ExplorePivotValuePlacement)["number_format"] == pivot_number_format
        assert get_type_hints(ExplorePivotFormula)["number_format"] == pivot_number_format
        assert get_type_hints(ExplorePivotAxisPlacement)["use_grouping"] is bool
        assert get_type_hints(ExplorePivotRowPlacement)["use_grouping"] is bool
        assert get_type_hints(ExplorePivotValuePlacement)["use_grouping"] is bool
        assert get_type_hints(ExplorePivotFormula)["use_grouping"] is bool
        assert get_type_hints(ExplorePivotValuePlacement)["color_scale_split_by"] == str | None
        assert "decimal_places" not in get_type_hints(ExplorePivotFilterPlacement)
        assert "number_format" not in get_type_hints(ExplorePivotFilterPlacement)
        assert "use_grouping" not in get_type_hints(ExplorePivotFilterPlacement)
        overview_hints = get_type_hints(ExploreOverviewConfig)
        assert overview_hints == {
            "dataset_snapshot": bool,
            "data_quality": bool,
            "numeric_summary": bool,
            "categorical_summary": bool,
            "schema": bool,
        }
        assert unrecognized_config_keys(NodeType.EXPLORE, {}) == []
        assert unrecognized_config_keys(NodeType.EXPLORE, {"code": "df = df.head(10)"}) == []
        # Overview block is a recognised key on explore nodes.
        assert (
            unrecognized_config_keys(NodeType.EXPLORE, {"overview": {"dataset_snapshot": True}})
            == []
        )
        assert unrecognized_config_keys(NodeType.EXPLORE, {"pivots": [{"id": "pivot_1"}]}) == []
        assert (
            unrecognized_config_keys(
                NodeType.EXPLORE, {"charts": [{"id": "chart_1", "enabled": True}]}
            )
            == []
        )
        assert (
            unrecognized_config_keys(
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
        bad = unrecognized_config_keys(NodeType.OPTIMISER_APPLY, config)
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
        bad = unrecognized_config_keys(NodeType.OPTIMISER, config)
        assert bad == [], f"Unrecognized keys: {bad}"
        assert config["data_input"] == "node_1"
        assert config["banding_source"] == "node_2"
