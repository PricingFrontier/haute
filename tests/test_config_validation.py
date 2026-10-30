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
            NodeType.WORKBENCH_INPUT,
            NodeType.WORKBENCH_OUTPUT,
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

    def test_the_removed_record_history_flag_is_undeclared(self):
        """Q5: every online solve records its history, so the flag is gone."""
        assert unrecognized_config_keys(
            NodeType.OPTIMISER, {"mode": "online", "record_history": True}
        ) == ["record_history"]

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
                NodeType.EDGE_JOIN,
                {"how": "left", "on": ["quote_id"], "maintain_order": "left"},
                "...",
                ["quotes", "rates"],
                id="edge_join",
            ),
            pytest.param(
                NodeType.EXPLORE,
                {},
                "",
                ["df"],
                id="explore",
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


# ---------------------------------------------------------------------------
# B7: selected_columns is universally valid (executor applies it to all nodes)
# ---------------------------------------------------------------------------


class TestSharedColumnSettingsUniversal:
    """Shared authored column settings are accepted consistently across nodes."""

    def test_shared_column_settings_in_universal_keys(self):
        assert {"selected_columns", "column_renames"} <= _UNIVERSAL_KEYS
        assert "categorical_levels" not in _UNIVERSAL_KEYS

    @pytest.mark.parametrize("node_type", list(VALID_KEYS.keys()))
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("selected_columns", ["_id", "premium"]),
            ("column_renames", {"_id": "identifier"}),
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


class TestOptimiserAnalysisConfig:
    """The analysis-column shape rules, shared by save and solve start."""

    @staticmethod
    def _validate(config: dict[str, Any]) -> None:
        from haute._config_validation import validate_node_config

        validate_node_config(NodeType.OPTIMISER, {"objective": "income", **config})

    def test_up_to_twelve_distinct_columns_are_accepted(self):
        self._validate(
            {"analysis_input": "regions", "analysis_columns": [f"c{i}" for i in range(12)]}
        )
        self._validate({"analysis_columns": []})
        self._validate({})

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"analysis_columns": [f"c{i}" for i in range(13)]}, "at most 12"),
            ({"analysis_columns": ["region", "region"]}, "duplicate"),
            ({"analysis_columns": ["region", ""]}, "non-empty"),
            ({"analysis_columns": "region"}, "list of column names"),
            ({"analysis_columns": [3]}, "non-empty"),
            ({"analysis_columns": ["quote_id"]}, "quote-id column"),
            ({"quote_id": "policy", "analysis_columns": ["policy"]}, "quote-id column"),
            ({"analysis_columns": ["__haute_x"]}, "reserved"),
            ({"analysis_input": 3}, "analysis_input"),
        ],
    )
    def test_invalid_shapes_are_refused(self, config: dict[str, Any], message: str):
        with pytest.raises(ConfigError, match=message):
            self._validate(config)

    def test_analysis_input_must_name_a_connected_input(self):
        from haute._config_validation import validate_optimiser_input_selectors

        config = {"data_input": "scored", "analysis_input": "regions"}
        validate_optimiser_input_selectors(
            NodeType.OPTIMISER, config, ["scored", "regions"], node_label="opt"
        )
        with pytest.raises(ConfigError, match="analysis_input"):
            validate_optimiser_input_selectors(
                NodeType.OPTIMISER, config, ["scored", "other"], node_label="opt"
            )
