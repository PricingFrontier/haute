"""Edge-case tests for the builder module (_builders.py).

Covers: resolve_instance_node, _build_constant, _build_output,
_build_live_switch, _build_scenario_expander, _build_banding,
and the _build_node_fn dispatcher.
"""

from __future__ import annotations

import polars as pl
import pytest

from haute.executor import _build_node_fn, resolve_instance_node
from haute.graph_utils import GraphNode, NodeData
from tests.conftest import make_node as _n
from tests.conftest import make_output_config


def _build(
    node_type: str,
    config: dict,
    label: str = "test_node",
    source_names: list[str] | None = None,
    source: str | None = None,
    node_map: dict[str, GraphNode] | None = None,
):
    node = _n(
        {
            "id": "n1",
            "data": {"label": label, "nodeType": node_type, "config": config},
        }
    )
    return _build_node_fn(
        node,
        source_names=source_names or [],
        source=source,
        node_map=node_map,
    )


class TestResolveInstanceNode:
    def test_valid_instance_merges_original_config(self) -> None:
        original = _n(
            {
                "id": "orig",
                "data": {
                    "label": "Original",
                    "nodeType": "banding",
                    "config": {
                        "factors": [
                            {
                                "column": "age",
                                "outputColumn": "age_band",
                                "banding": "continuous",
                                "rules": [
                                    {
                                        "op1": ">=",
                                        "val1": 0,
                                        "op2": "<",
                                        "val2": 50,
                                        "assignment": "young",
                                    },
                                ],
                            }
                        ],
                    },
                },
            }
        )
        instance = _n(
            {
                "id": "inst",
                "data": {
                    "label": "Instance",
                    "nodeType": "polars",
                    "config": {"instanceOf": "orig"},
                },
            }
        )
        node_map = {"orig": original, "inst": instance}
        resolved = resolve_instance_node(instance, node_map)
        assert resolved.data.nodeType == "banding"
        assert "factors" in resolved.data.config
        assert resolved.data.config["instanceOf"] == "orig"

    def test_empty_instanceOf_returns_node_unchanged(self) -> None:  # noqa: N802 - references camelCase config key `instanceOf`
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Plain",
                    "nodeType": "polars",
                    "config": {"instanceOf": ""},
                },
            }
        )
        result = resolve_instance_node(node, {"n1": node})
        assert result is node

    def test_missing_original_in_node_map_returns_unchanged(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Orphan",
                    "nodeType": "polars",
                    "config": {"instanceOf": "nonexistent"},
                },
            }
        )
        result = resolve_instance_node(node, {"n1": node})
        assert result is node

    def test_instance_preserves_own_id_and_label(self) -> None:
        original = _n(
            {
                "id": "orig",
                "data": {
                    "label": "Original",
                    "nodeType": "constant",
                    "config": {"values": [{"name": "x", "value": "1"}]},
                },
            }
        )
        instance = _n(
            {
                "id": "inst_42",
                "data": {
                    "label": "My Instance",
                    "nodeType": "polars",
                    "config": {"instanceOf": "orig"},
                },
            }
        )
        node_map = {"orig": original, "inst_42": instance}
        resolved = resolve_instance_node(instance, node_map)
        assert resolved.id == "inst_42"
        assert resolved.data.label == "My Instance"
        assert resolved.data.nodeType == "constant"

    def test_instance_preserves_input_mapping(self) -> None:
        original = _n(
            {
                "id": "orig",
                "data": {
                    "label": "Original",
                    "nodeType": "polars",
                    "config": {"code": "df"},
                },
            }
        )
        instance = _n(
            {
                "id": "inst",
                "data": {
                    "label": "Inst",
                    "nodeType": "polars",
                    "config": {
                        "instanceOf": "orig",
                        "inputMapping": {"a": "b"},
                    },
                },
            }
        )
        node_map = {"orig": original, "inst": instance}
        resolved = resolve_instance_node(instance, node_map)
        assert resolved.data.config["inputMapping"] == {"a": "b"}
        assert resolved.data.config["code"] == "df"

    def test_no_instanceOf_key_returns_unchanged(self) -> None:  # noqa: N802 - references camelCase config key `instanceOf`
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "NoRef",
                    "nodeType": "polars",
                    "config": {"code": "df"},
                },
            }
        )
        result = resolve_instance_node(node, {"n1": node})
        assert result is node


class TestBuildConstantEdgeCases:
    def test_empty_values_produces_default_column(self) -> None:
        _, fn, _ = _build("constant", {"values": []})
        result = fn().collect()
        assert result.columns == ["constant"]
        assert result.shape == (1, 1)

    def test_none_values_handled(self) -> None:
        _, fn, _ = _build("constant", {"values": None})
        result = fn().collect()
        assert "constant" in result.columns

    def test_numeric_string_coerced_to_float(self) -> None:
        _, fn, _ = _build("constant", {"values": [{"name": "pi", "value": "3.14"}]})
        result = fn().collect()
        assert result["pi"].dtype == pl.Float64
        assert result["pi"].to_list() == [3.14]

    def test_non_numeric_string_kept_as_string(self) -> None:
        _, fn, _ = _build("constant", {"values": [{"name": "region", "value": "north"}]})
        result = fn().collect()
        assert result["region"].dtype == pl.String
        assert result["region"].to_list() == ["north"]

    def test_value_with_empty_name_skipped(self) -> None:
        _, fn, _ = _build(
            "constant",
            {"values": [{"name": "", "value": "99"}, {"name": "kept", "value": "1"}]},
        )
        result = fn().collect()
        assert "kept" in result.columns
        assert result.shape[1] == 1

    def test_mixed_numeric_and_string_values(self) -> None:
        _, fn, _ = _build(
            "constant",
            {
                "values": [
                    {"name": "amount", "value": "42.5"},
                    {"name": "label", "value": "premium"},
                    {"name": "count", "value": "100"},
                ]
            },
        )
        result = fn().collect()
        assert result["amount"].to_list() == [42.5]
        assert result["label"].to_list() == ["premium"]
        assert result["count"].to_list() == [100.0]

    def test_none_value_kept_as_string(self) -> None:
        _, fn, _ = _build("constant", {"values": [{"name": "x", "value": None}]})
        result = fn().collect()
        assert result["x"].to_list() == [None]

    def test_all_empty_names_produces_default(self) -> None:
        _, fn, _ = _build(
            "constant",
            {"values": [{"name": "", "value": "1"}, {"name": "", "value": "2"}]},
        )
        result = fn().collect()
        assert result.columns == ["constant"]


class TestBuildOutputEdgeCases:
    def test_specified_fields_selects_only_those_columns(self) -> None:
        _, fn, _ = _build("output", make_output_config(["a", "c"]), source_names=["up"])
        lf = pl.DataFrame({"a": [1], "b": [2], "c": [3]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["a", "c"]

    def test_no_fields_returns_empty_document(self) -> None:
        # v2: an empty outputMapping selects no columns, so the assembled
        # document is empty — unlike the retired v1 ``{"fields": []}`` which
        # passed all upstream columns through.
        _, fn, _ = _build("output", make_output_config([]), source_names=["up"])
        lf = pl.DataFrame({"x": [1], "y": [2], "z": [3]}).lazy()
        result = fn(lf).collect()
        assert result.columns == []
        assert result.shape == (0, 0)

    def test_none_fields_returns_empty_document(self) -> None:
        # v2: ``make_output_config([])`` (formerly ``{"fields": None}``) yields an
        # empty outputMapping → empty document, not a passthrough of all columns.
        _, fn, _ = _build("output", make_output_config([]), source_names=["up"])
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        result = fn(lf).collect()
        assert result.columns == []
        assert result.shape == (0, 0)

    def test_nonexistent_field_raises(self) -> None:
        _, fn, _ = _build("output", make_output_config(["a", "missing"]), source_names=["up"])
        lf = pl.DataFrame({"a": [1], "b": [2]}).lazy()
        with pytest.raises(Exception):
            fn(lf).collect()


class TestBuildLiveSwitchEdgeCases:
    def test_live_scenario_selects_live_input(self) -> None:
        _, fn, _ = _build(
            "liveSwitch",
            {"input_scenario_map": {"src_live": "live", "src_batch": "batch"}},
            source_names=["src_live", "src_batch"],
            source="live",
        )
        live_df = pl.DataFrame({"origin": ["live"]}).lazy()
        batch_df = pl.DataFrame({"origin": ["batch"]}).lazy()
        result = fn(live_df, batch_df).collect()
        assert result["origin"].to_list() == ["live"]

    def test_non_live_scenario_selects_mapped_input(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Switch",
                    "nodeType": "liveSwitch",
                    "config": {
                        "input_scenario_map": {"src_live": "live", "src_batch": "batch"},
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            source_names=["src_live", "src_batch"],
            source="batch",
        )
        live_df = pl.DataFrame({"v": [1]}).lazy()
        batch_df = pl.DataFrame({"v": [2]}).lazy()
        result = fn(live_df, batch_df).collect()
        assert result["v"].to_list() == [2]

    def test_unmapped_scenario_falls_back_to_first_input(self) -> None:
        node = _n(
            {
                "id": "n1",
                "data": {
                    "label": "Switch",
                    "nodeType": "liveSwitch",
                    "config": {
                        "input_scenario_map": {"a": "live", "b": "test"},
                    },
                },
            }
        )
        _, fn, _ = _build_node_fn(
            node,
            source_names=["a", "b"],
            source="totally_unknown",
        )
        df_a = pl.DataFrame({"val": [10]}).lazy()
        df_b = pl.DataFrame({"val": [20]}).lazy()
        result = fn(df_a, df_b).collect()
        assert result["val"].to_list() == [10]

    def test_empty_input_raises_value_error(self) -> None:
        _, fn, _ = _build(
            "liveSwitch",
            {"input_scenario_map": {}},
            source_names=[],
            source="live",
        )
        with pytest.raises(ValueError, match="no input"):
            fn()


class TestBuildScenarioExpanderEdgeCases:
    def test_steps_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="steps >= 1"):
            _build(
                "scenarioExpander",
                {"column_name": "sv", "steps": 0},
                source_names=["up"],
            )

    def test_negative_steps_raises(self) -> None:
        with pytest.raises(ValueError, match="steps >= 1"):
            _build(
                "scenarioExpander",
                {"column_name": "sv", "steps": -5},
                source_names=["up"],
            )

    def test_empty_config_uses_defaults(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {},
            source_names=["up"],
        )
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = fn(lf).collect()
        assert "scenario_index" in result.columns
        assert result.shape[0] == 21

    def test_cross_join_produces_correct_row_count(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.9, "max_value": 1.1, "steps": 5},
            source_names=["up"],
        )
        lf = pl.DataFrame({"id": [1, 2, 3]}).lazy()
        result = fn(lf).collect()
        assert result.shape[0] == 15
        assert "sv" in result.columns
        assert "scenario_index" in result.columns

    def test_cross_join_10_rows_7_steps(self) -> None:
        _, fn, _ = _build(
            "scenarioExpander",
            {"column_name": "sv", "min_value": 0.8, "max_value": 1.2, "steps": 7},
            source_names=["up"],
        )
        lf = pl.DataFrame({"id": list(range(10))}).lazy()
        result = fn(lf).collect()
        assert result.shape[0] == 70


class TestBuildBandingEdgeCases:
    def test_empty_factors_passthrough(self) -> None:
        _, fn, _ = _build("banding", {"factors": []}, source_names=["up"])
        lf = pl.DataFrame({"age": [25, 50]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["age"]
        assert result["age"].to_list() == [25, 50]

    def test_no_factors_key_passthrough(self) -> None:
        _, fn, _ = _build("banding", {}, source_names=["up"])
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["x"]
        assert result["x"].to_list() == [1, 2]

    def test_factor_with_missing_column_skipped(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "",
                        "outputColumn": "out",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"},
                        ],
                    },
                ],
            },
            source_names=["up"],
        )
        lf = pl.DataFrame({"age": [25]}).lazy()
        result = fn(lf).collect()
        assert "out" not in result.columns
        assert "age" in result.columns

    def test_factor_with_empty_output_column_skipped(self) -> None:
        _, fn, _ = _build(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 50, "assignment": "young"},
                        ],
                    },
                ],
            },
            source_names=["up"],
        )
        lf = pl.DataFrame({"age": [25]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["age"]


class TestBuildNodeFnDispatcher:
    def test_unknown_node_type_falls_back_to_passthrough(self) -> None:
        node = GraphNode(
            id="n1",
            data=NodeData(label="Mystery", nodeType="polars", config={"code": ""}),
        )
        func_name, fn, is_source = _build_node_fn(node, source_names=["up"])
        assert is_source is False
        lf = pl.DataFrame({"x": [42]}).lazy()
        result = fn(lf).collect()
        assert result["x"].to_list() == [42]

    def test_instance_resolution_before_building(self) -> None:
        original = _n(
            {
                "id": "orig",
                "data": {
                    "label": "Original",
                    "nodeType": "constant",
                    "config": {"values": [{"name": "rate", "value": "0.05"}]},
                },
            }
        )
        instance = _n(
            {
                "id": "inst",
                "data": {
                    "label": "Instance",
                    "nodeType": "polars",
                    "config": {"instanceOf": "orig"},
                },
            }
        )
        node_map = {"orig": original, "inst": instance}
        func_name, fn, is_source = _build_node_fn(instance, source_names=[], node_map=node_map)
        assert is_source is True
        result = fn().collect()
        assert result["rate"].to_list() == [0.05]

    def test_dispatcher_returns_valid_func_name(self) -> None:
        func_name, _, _ = _build(
            "output",
            make_output_config(["a"]),
            label="Final Output (v2)",
            source_names=["up"],
        )
        assert func_name.isidentifier()


class TestBuildOutputEmptyDataFrame:
    def test_build_output_empty_dataframe(self) -> None:
        """A 0-row source frame assembles to an empty v2 document (no rows, no paths).

        Under the retired v1 ``{"fields": [...]}`` passthrough this returned a
        0-row frame that still carried the selected columns ``["a", "c"]``. The
        v2 assembler produces the document from the actual rows, so an empty
        input yields an empty array-of-rows and therefore an empty frame.
        """
        _, fn, _ = _build("output", make_output_config(["a", "c"]), source_names=["up"])
        lf = pl.DataFrame(
            {
                "a": pl.Series([], dtype=pl.Int64),
                "b": pl.Series([], dtype=pl.Int64),
                "c": pl.Series([], dtype=pl.Int64),
            }
        ).lazy()
        result = fn(lf).collect()
        assert result.columns == []
        assert result.shape == (0, 0)
