"""Edge-case tests for the executor module.

Covers gaps in:
  - _extract_column_refs
  - write_data_output
  - _apply_column_renames
  - _apply_selected_columns
  - _prune_live_switch_edges
  - row_limit edge cases
"""

from __future__ import annotations

import errno
from unittest.mock import patch

import polars as pl
import pytest

from haute._execute_lazy import (
    _apply_column_renames,
    _apply_selected_columns,
    _prune_live_switch_edges,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
)
from haute.executor import (
    _extract_column_refs,
    execute_graph,
    write_data_output,
)
from tests.conftest import (
    make_edge as _edge,
)
from tests.conftest import (
    make_graph as _g,
)
from tests.conftest import (
    make_node as _n,
)
from tests.conftest import (
    make_source_node as _source_node,
)
from tests.conftest import (
    make_transform_node as _transform_node,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _src_node(nid: str, label: str | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.DATA_INPUT),
    )


def _tx_node(nid: str, label: str | None = None, **extra_config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label or nid, nodeType=NodeType.POLARS, config=extra_config),
    )


def _live_switch_node(nid: str, ism: dict[str, str]) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.LIVE_SWITCH,
            config={"input_scenario_map": ism, "inputs": []},
        ),
    )


def _sink_graph(tmp_path, *, fmt="parquet", src_data=None, out_name=None):
    src_path = tmp_path / "in.parquet"
    ext = "csv" if fmt == "csv" else "parquet"
    out_path = tmp_path / (out_name or f"out.{ext}")
    pl.DataFrame(src_data or {"x": [1, 2, 3]}).write_parquet(src_path)
    graph = _g(
        {
            "nodes": [
                _source_node("src", str(src_path)),
                _n(
                    {
                        "id": "sink",
                        "data": {
                            "label": "sink",
                            "nodeType": "dataOutput",
                            "config": {
                                "outputType": "file",
                                "format": fmt,
                                "mode": "sink" if fmt in {"csv", "parquet"} else "write",
                                "path": str(out_path),
                                "arguments": {},
                            },
                        },
                    }
                ),
            ],
            "edges": [_edge("src", "sink")],
        }
    )
    return graph, out_path


# ===========================================================================
# _extract_column_refs
# ===========================================================================


class TestExtractColumnRefsEdgeCases:
    def test_selected_columns_with_valid_strings(self):
        config = {"selected_columns": ["col_a", "col_b"]}
        assert _extract_column_refs(config) == {"col_a", "col_b"}

    def test_target_weight_offset_extracted(self):
        config = {"target": "loss", "weight": "exposure", "offset": "log_exp"}
        assert _extract_column_refs(config) == {"loss", "exposure", "log_exp"}

    def test_factors_with_column_key(self):
        config = {"factors": [{"column": "age"}, {"column": "region"}]}
        assert _extract_column_refs(config) == {"age", "region"}

    def test_tables_with_nested_factors(self):
        config = {
            "tables": [
                {"factors": ["age_band", "region"]},
                {"factors": ["vehicle_type"]},
            ]
        }
        assert _extract_column_refs(config) == {"age_band", "region", "vehicle_type"}

    def test_empty_config_returns_empty_set(self):
        assert _extract_column_refs({}) == set()

    def test_none_values_filtered_out(self):
        config = {
            "selected_columns": None,
            "target": None,
            "weight": None,
            "offset": None,
            "factors": None,
            "tables": None,
            "exclude": None,
        }
        assert _extract_column_refs(config) == set()

    def test_mixed_none_and_valid_in_selected_columns(self):
        config = {"selected_columns": ["valid", None, "", "also_valid"]}
        assert _extract_column_refs(config) == {"valid", "also_valid"}

    def test_output_column_excluded(self):
        config = {
            "selected_columns": ["a", "b", "result"],
            "output_column": "result",
        }
        refs = _extract_column_refs(config)
        assert "result" not in refs
        assert refs == {"a", "b"}

    def test_tables_with_non_dict_entries_skipped(self):
        config = {"tables": ["not_a_dict", 42, {"factors": ["col_a"]}]}
        assert _extract_column_refs(config) == {"col_a"}

    def test_factors_with_non_dict_entries(self):
        config = {"factors": ["not_a_dict", {"column": "valid_col"}]}
        assert _extract_column_refs(config) == {"valid_col"}


# ===========================================================================
# write_data_output edge cases
# ===========================================================================


class TestExecuteSinkEdgeCases:
    def test_parent_directory_auto_created(self, tmp_path):
        src_path = tmp_path / "in.parquet"
        out_path = tmp_path / "nested" / "deep" / "out.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(src_path)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(src_path)),
                    _n(
                        {
                            "id": "sink",
                            "data": {
                                "label": "sink",
                                "nodeType": "dataOutput",
                                "config": {
                                    "outputType": "file",
                                    "format": "parquet",
                                    "mode": "sink",
                                    "path": str(out_path),
                                    "arguments": {},
                                },
                            },
                        }
                    ),
                ],
                "edges": [_edge("src", "sink")],
            }
        )
        assert not out_path.parent.exists()
        result = write_data_output(graph, output_node_id="sink")
        assert result.status == "ok"
        assert out_path.exists()


# ===========================================================================
# _apply_column_renames edge cases
# ===========================================================================


class TestApplyColumnRenamesEdgeCases:
    def test_renames_applied_from_config(self):
        df = pl.DataFrame({"old_a": [1], "old_b": [2]})
        config = {"column_renames": {"old_a": "new_a", "old_b": "new_b"}}
        result = _apply_column_renames(df, config)
        assert result.columns == ["new_a", "new_b"]

    def test_nonexistent_columns_skipped(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        config = {"column_renames": {"nonexistent": "renamed", "a": "aa"}}
        result = _apply_column_renames(df, config)
        assert "aa" in result.columns
        assert "b" in result.columns
        assert "nonexistent" not in result.columns

    def test_empty_renames_dict_is_noop(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        config = {"column_renames": {}}
        result = _apply_column_renames(df, config)
        assert result.columns == ["a", "b"]

    def test_no_renames_key_is_noop(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_column_renames(df, {"some_other_key": True})
        assert result.columns == ["a", "b"]

    def test_rename_collision_with_existing_column(self):
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
        config = {"column_renames": {"a": "b"}}
        with pytest.raises(pl.exceptions.DuplicateError):
            result = _apply_column_renames(df, config)
            if isinstance(result, pl.LazyFrame):
                result.collect()

    def test_renames_on_lazyframe(self):
        lf = pl.LazyFrame({"x": [1], "y": [2]})
        config = {"column_renames": {"x": "xx"}}
        result = _apply_column_renames(lf, config)
        assert isinstance(result, pl.LazyFrame)
        assert result.collect_schema().names() == ["xx", "y"]


# ===========================================================================
# _apply_selected_columns edge cases
# ===========================================================================


class TestApplySelectedColumnsEdgeCases:
    def test_selects_specified_columns(self):
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "c"]})
        assert result.columns == ["a", "c"]

    def test_empty_list_returns_all_columns(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": []})
        assert result.columns == ["a", "b"]

    def test_none_returns_all_columns(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": None})
        assert result.columns == ["a", "b"]

    def test_missing_key_returns_all_columns(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {})
        assert result.columns == ["a", "b"]

    def test_invalid_column_names_filtered_out(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "nonexistent"]})
        assert result.columns == ["a"]

    def test_all_invalid_returns_all_columns(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["x", "y", "z"]})
        assert result.columns == ["a", "b"]

    def test_all_columns_selected_is_noop(self):
        df = pl.DataFrame({"a": [1], "b": [2]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "b"]})
        assert result.columns == ["a", "b"]

    def test_works_on_lazyframe(self):
        lf = pl.LazyFrame({"a": [1], "b": [2], "c": [3]})
        result = _apply_selected_columns(lf, {"selected_columns": ["a", "c"]})
        assert isinstance(result, pl.LazyFrame)
        assert result.collect_schema().names() == ["a", "c"]


# ===========================================================================
# _prune_live_switch_edges edge cases
# ===========================================================================


class TestPruneLiveSwitchEdgesEdgeCases:
    def test_keeps_all_edges_when_scenario_not_in_ism(self):
        edges = [_e("a", "sw"), _e("b", "sw")]
        node_map = {
            "a": _src_node("a"),
            "b": _src_node("b"),
            "sw": _live_switch_node("sw", {"a": "live", "b": "batch"}),
        }
        result = _prune_live_switch_edges(edges, node_map, "unknown_scenario")
        assert len(result) == 2

    def test_empty_ism_keeps_all_edges(self):
        edges = [_e("a", "sw"), _e("b", "sw")]
        node_map = {
            "a": _src_node("a"),
            "b": _src_node("b"),
            "sw": _live_switch_node("sw", {}),
        }
        result = _prune_live_switch_edges(edges, node_map, "live")
        assert len(result) == 2

    def test_non_switch_edges_preserved(self):
        edges = [_e("a", "b"), _e("live_in", "sw")]
        node_map = {
            "a": _src_node("a"),
            "b": _tx_node("b"),
            "live_in": _src_node("live_in"),
            "sw": _live_switch_node("sw", {"live_in": "live"}),
        }
        result = _prune_live_switch_edges(edges, node_map, "live")
        non_sw = [e for e in result if e.target != "sw"]
        assert len(non_sw) == 1
        assert non_sw[0].source == "a"


# ===========================================================================
# Row limit edge cases
# ===========================================================================


class TestRowLimitEdgeCases:
    def test_row_limit_zero_means_no_limit(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(25))}).write_parquet(p)
        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        results = execute_graph(graph, row_limit=0)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 25

    def test_row_limit_one_returns_single_row(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(20))}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("t", "df = src.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "t")],
            }
        )
        results = execute_graph(graph, row_limit=1)
        assert results["src"].row_count == 1
        assert results["t"].row_count == 1

    def test_negative_row_limit_causes_error(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": list(range(15))}).write_parquet(p)
        graph = _g({"nodes": [_source_node("src", str(p))], "edges": []})
        with pytest.raises(ValueError, match="row_limit must be a non-negative integer"):
            execute_graph(graph, row_limit=-1)


# ===========================================================================
# write_data_output — I/O error propagation
# ===========================================================================


class TestExecuteSinkIOErrors:
    def test_permission_error_propagates(self, tmp_path):
        graph, _out = _sink_graph(tmp_path, fmt="parquet")
        with patch(
            "haute._polars_io_registry.write_polars_output",
            side_effect=PermissionError("Access denied"),
        ):
            with pytest.raises(PermissionError, match="Access denied"):
                write_data_output(graph, output_node_id="sink")

    def test_oserror_disk_full_propagates(self, tmp_path):
        graph, _out = _sink_graph(tmp_path, fmt="parquet")
        err = OSError(errno.ENOSPC, "No space left")
        with patch("haute._polars_io_registry.write_polars_output", side_effect=err):
            with pytest.raises(OSError, match="No space left"):
                write_data_output(graph, output_node_id="sink")

    def test_output_write_error_propagates(self, tmp_path):
        graph, _out = _sink_graph(tmp_path, fmt="parquet")
        with patch(
            "haute._polars_io_registry.write_polars_output",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                write_data_output(graph, output_node_id="sink")


# ===========================================================================
# Preamble compilation failure — source nodes still run
# ===========================================================================


class TestPreambleFailureSourceNodesRun:
    def test_source_node_succeeds_despite_bad_preamble(self, tmp_path):
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)
        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("tx", "df = df.with_columns(y=my_func(pl.col('x')))"),
                ],
                "edges": [_edge("src", "tx")],
                "preamble": "x = undefined_name_that_does_not_exist + 1",
            }
        )
        results = execute_graph(graph)
        assert results["src"].status == "ok"
        assert results["src"].row_count == 3
        assert results["tx"].status == "error"


# ===========================================================================
# Selected columns with duplicates
# ===========================================================================


class TestSelectedColumnsWithDuplicates:
    def test_duplicate_entries_no_error(self):
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "a", "b"]})
        assert "a" in result.columns
        assert "b" in result.columns

    def test_duplicate_entries_subset_no_error(self):
        df = pl.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]})
        result = _apply_selected_columns(df, {"selected_columns": ["a", "a", "b"]})
        assert set(result.columns) == {"a", "b"}
        assert "c" not in result.columns
        assert "d" not in result.columns
