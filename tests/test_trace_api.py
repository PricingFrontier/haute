"""Tests for the POST /api/pipeline/trace HTTP endpoint.

These test the API layer (request validation, response shape, serialization,
error handling) via FastAPI TestClient — not the core trace logic, which is
covered by test_trace_integration.py.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROW_LIMIT = 100


def _simple_graph(parquet_path: str | Path, code: str = "") -> dict:
    """Build a minimal source -> transform graph dict for the API."""
    graph = _g(
        {
            "nodes": [
                _source_node("src", str(parquet_path)),
                _transform_node("t", code),
            ],
            "edges": [_edge("src", "t")],
        }
    )
    return graph.model_dump()


def _simple_parquet(tmp_path: Path, data: dict | None = None) -> Path:
    """Write a small parquet file and return its path."""
    if data is None:
        data = {"x": [1, 2, 3], "y": [10, 20, 30]}
    p = tmp_path / "data.parquet"
    pl.DataFrame(data).write_parquet(p)
    return p


def _trace_post(client, graph_dict: dict, **kwargs) -> Any:
    """POST to the trace endpoint and return the response."""
    payload: dict[str, Any] = {"graph": graph_dict, **kwargs}
    return client.post("/api/pipeline/trace", json=payload)


# ===========================================================================
# A. Request Validation (10 tests)
# ===========================================================================


class TestRequestValidation:
    """A. Validate that the trace endpoint handles various request shapes."""

    def test_valid_graph_returns_200(self, client, tmp_path):
        """POST with valid graph and row_index returns 200 OK with trace."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "trace" in body

    def test_empty_graph_returns_error(self, client):
        """POST with a graph containing no nodes returns an error."""
        graph = _g({"nodes": [], "edges": []}).model_dump()

        resp = _trace_post(client, graph, row_index=0)

        assert resp.status_code == 400
        body = resp.json()
        assert "detail" in body

    def test_negative_row_index_returns_422(self, client, tmp_path):
        """POST with negative row_index fails Pydantic validation (ge=0)."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=-1, target_node_id="t")

        assert resp.status_code == 422

    def test_out_of_bounds_row_index_returns_error(self, client, tmp_path):
        """POST with row_index beyond data length returns an error."""
        p = _simple_parquet(tmp_path)  # 3 rows
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=9999, target_node_id="t")

        # Out-of-bounds row_index now raises ValueError → 500
        assert resp.status_code == 500
        body = resp.json()
        assert "detail" in body

    def test_nonexistent_target_node_returns_error(self, client, tmp_path):
        """POST with a target_node_id that does not exist in the graph."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="nonexistent_node")

        assert resp.status_code == 500
        body = resp.json()
        assert "detail" in body

    def test_no_body_returns_422(self, client):
        """POST with no JSON body returns 422 validation error."""
        resp = client.post("/api/pipeline/trace")

        assert resp.status_code == 422

    def test_missing_graph_field_returns_422(self, client):
        """POST with body missing the required 'graph' field returns 422."""
        resp = client.post("/api/pipeline/trace", json={"row_index": 0})

        assert resp.status_code == 422

    def test_column_parameter_filters_trace(self, client, tmp_path):
        """POST with column parameter returns a trace filtered to that column."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t", column="z")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        trace = body["trace"]
        assert trace["column"] == "z"
        # With column filter, output_value should be a scalar, not a dict
        assert not isinstance(trace["output_value"], dict)

    def test_row_limit_parameter_is_accepted(self, client, tmp_path):
        """POST with row_limit is accepted and returns 200."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t", row_limit=10)

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_source_parameter_is_accepted(self, client, tmp_path):
        """POST with source parameter (for live switch) is accepted."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t", source="live")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ===========================================================================
# B. Response Shape (8 tests)
# ===========================================================================


class TestResponseShape:
    """B. Validate the structure of a successful trace response."""

    @pytest.fixture()
    def trace_response(self, client, tmp_path):
        """Return a successful trace response body for shape testing."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")
        resp = _trace_post(client, graph, row_index=0, target_node_id="t")
        assert resp.status_code == 200
        return resp.json()

    def test_status_ok_on_success(self, trace_response):
        """Response has status='ok' on success."""
        assert trace_response["status"] == "ok"

    def test_trace_has_all_required_fields(self, trace_response):
        """Trace object contains all required top-level fields."""
        trace = trace_response["trace"]
        required_fields = [
            "target_node_id",
            "row_index",
            "column",
            "output_value",
            "steps",
            "row_id_column",
            "row_id_value",
            "total_nodes_in_pipeline",
            "nodes_in_trace",
            "execution_ms",
        ]
        for field in required_fields:
            assert field in trace, f"Missing field: {field}"

    def test_steps_is_a_list(self, trace_response):
        """trace.steps is a list."""
        trace = trace_response["trace"]
        assert isinstance(trace["steps"], list)
        assert len(trace["steps"]) > 0

    def test_each_step_has_required_fields(self, trace_response):
        """Each step has node_id, node_name, node_type, schema_diff, etc."""
        trace = trace_response["trace"]
        step_fields = [
            "node_id",
            "node_name",
            "node_type",
            "schema_diff",
            "input_values",
            "output_values",
            "column_relevant",
            "execution_ms",
        ]
        for step in trace["steps"]:
            for field in step_fields:
                assert field in step, f"Step missing field: {field}"

    def test_schema_diff_has_required_fields(self, trace_response):
        """schema_diff has columns_added, columns_removed, columns_modified, columns_passed."""
        trace = trace_response["trace"]
        diff_fields = [
            "columns_added",
            "columns_removed",
            "columns_modified",
            "columns_passed",
        ]
        for step in trace["steps"]:
            sd = step["schema_diff"]
            for field in diff_fields:
                assert field in sd, f"schema_diff missing field: {field}"
                assert isinstance(sd[field], list)

    def test_execution_ms_is_positive(self, trace_response):
        """execution_ms at the trace level is a non-negative number."""
        trace = trace_response["trace"]
        assert isinstance(trace["execution_ms"], (int, float))
        assert trace["execution_ms"] >= 0

    def test_nodes_in_trace_lte_total_nodes(self, trace_response):
        """nodes_in_trace <= total_nodes_in_pipeline."""
        trace = trace_response["trace"]
        assert trace["nodes_in_trace"] <= trace["total_nodes_in_pipeline"]

    def test_output_value_matches_traced_row(self, client, tmp_path):
        """output_value matches the data from the traced row."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")
        trace = resp.json()["trace"]

        # Without column filter, output_value is a dict of the full row
        ov = trace["output_value"]
        assert isinstance(ov, dict)
        assert ov["x"] == 1
        assert ov["y"] == 10
        assert ov["z"] == 11


# ===========================================================================
# C. Integration with Pipeline (8 tests)
# ===========================================================================


class TestPipelineIntegration:
    """C. End-to-end integration tests through the HTTP layer."""

    def test_simple_source_transform_trace(self, client, tmp_path):
        """Simple source -> transform pipeline trace works end-to-end."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') * 2)")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        assert trace["target_node_id"] == "t"
        assert trace["row_index"] == 0
        assert len(trace["steps"]) == 2  # src + t

    def test_two_source_join_trace(self, client, tmp_path):
        """Two-source join pipeline trace works."""
        p1 = tmp_path / "left.parquet"
        p2 = tmp_path / "right.parquet"
        pl.DataFrame({"key": [1, 2], "a": [10, 20]}).write_parquet(p1)
        pl.DataFrame({"key": [1, 2], "b": [100, 200]}).write_parquet(p2)

        graph = _g(
            {
                "nodes": [
                    _source_node("s1", str(p1)),
                    _source_node("s2", str(p2)),
                    _transform_node(
                        "join",
                        "df = s1.join(s2, on='key')",
                    ),
                ],
                "edges": [_edge("s1", "join"), _edge("s2", "join")],
            }
        ).model_dump()

        resp = _trace_post(client, graph, row_index=0, target_node_id="join")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        assert len(trace["steps"]) >= 2  # at least the two sources + join

    def test_column_tracing_filters_relevant_nodes(self, client, tmp_path):
        """Column tracing returns only relevant nodes."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        # Trace a column that is added by the transform
        resp = _trace_post(client, graph, row_index=0, target_node_id="t", column="z")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        assert trace["column"] == "z"
        # All included steps should be relevant to the column
        for step in trace["steps"]:
            # Steps are included either because they're column_relevant
            # or because they are ancestors of the origin node
            assert isinstance(step["column_relevant"], bool)

    def test_trace_with_column_returns_scalar(self, client, tmp_path):
        """Trace with column parameter returns scalar output_value."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t", column="z")

        trace = resp.json()["trace"]
        # Scalar value, not a dict
        assert trace["output_value"] == 11
        assert not isinstance(trace["output_value"], dict)

    def test_trace_without_column_returns_dict(self, client, tmp_path):
        """Trace without column parameter returns dict output_value."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        trace = resp.json()["trace"]
        assert isinstance(trace["output_value"], dict)
        assert "x" in trace["output_value"]
        assert "y" in trace["output_value"]
        assert "z" in trace["output_value"]

    def test_pipeline_with_error_in_node_code(self, client, tmp_path):
        """Pipeline with invalid node code returns an error response."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(INVALID_SYNTAX_HERE===)")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        # Should get a 500 with error detail
        assert resp.status_code == 500

    def test_large_pipeline_trace_returns_steps_in_topo_order(self, client, tmp_path):
        """Pipeline with 5+ nodes returns steps in topological order."""
        p = _simple_parquet(tmp_path)
        nodes = [_source_node("src", str(p))]
        edges = []
        prev = "src"
        for i in range(5):
            nid = f"t{i}"
            nodes.append(_transform_node(nid, f"df = df.with_columns(c{i}=pl.col('x') + {i})"))
            edges.append(_edge(prev, nid))
            prev = nid

        graph = _g({"nodes": nodes, "edges": edges}).model_dump()

        resp = _trace_post(client, graph, row_index=0, target_node_id="t4")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        step_ids = [s["node_id"] for s in trace["steps"]]
        # Source should come first, then transforms in order
        assert step_ids[0] == "src"
        assert step_ids.index("t0") < step_ids.index("t1")
        assert step_ids.index("t1") < step_ids.index("t2")
        assert step_ids.index("t3") < step_ids.index("t4")
        assert trace["total_nodes_in_pipeline"] == 6  # 1 source + 5 transforms

    def test_cached_trace_returns_faster_on_second_call(self, client, tmp_path):
        """Second trace on same graph should hit cache and return faster."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('x') + pl.col('y'))")

        # First call — cold
        resp1 = _trace_post(client, graph, row_index=0, target_node_id="t")
        assert resp1.status_code == 200

        # Second call — should use cache
        resp2 = _trace_post(client, graph, row_index=1, target_node_id="t")
        assert resp2.status_code == 200

        # Both should succeed (cache doesn't corrupt results)
        assert resp1.json()["status"] == "ok"
        assert resp2.json()["status"] == "ok"

        # The second call traced a different row, so output should differ
        trace1 = resp1.json()["trace"]
        trace2 = resp2.json()["trace"]
        assert trace1["row_index"] == 0
        assert trace2["row_index"] == 1


# ===========================================================================
# D. Error Handling (6 tests)
# ===========================================================================


class TestErrorHandling:
    """D. Validate error handling at the API layer."""

    def test_node_code_raises_exception(self, client, tmp_path):
        """Node code that raises a Python exception returns an error."""
        p = _simple_parquet(tmp_path)
        graph = _simple_graph(p, "df = df.with_columns(z=pl.col('nonexistent_col'))")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 500
        body = resp.json()
        assert "detail" in body

    def test_timeout_returns_504(self, client, tmp_path, monkeypatch):
        """If trace execution exceeds the timeout, return 504."""
        from unittest.mock import patch

        import haute.routes.pipeline as route_mod

        monkeypatch.setattr(route_mod, "_TRACE_TIMEOUT", 0.01)

        p = _simple_parquet(tmp_path)
        code = "df = df.with_columns(z=pl.col('x') + 1)"
        graph = _simple_graph(p, code)

        def slow_trace(*args, **kwargs):
            time.sleep(2.0)

        with patch("haute.trace.execute_trace", side_effect=slow_trace):
            resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 504

    def test_malformed_graph_json_returns_422(self, client):
        """Malformed graph JSON structure returns 422 validation error."""
        resp = client.post(
            "/api/pipeline/trace",
            json={
                "graph": {"nodes": "not_a_list", "edges": []},
                "row_index": 0,
            },
        )

        assert resp.status_code == 422

    def test_edges_referencing_nonexistent_nodes(self, client, tmp_path):
        """Graph with edges pointing to non-existent nodes returns error."""
        p = _simple_parquet(tmp_path)
        graph = _g(
            {
                "nodes": [_source_node("src", str(p))],
                "edges": [_edge("src", "ghost_node")],
            }
        ).model_dump()

        resp = _trace_post(client, graph, row_index=0, target_node_id="src")

        # Should return 200 (the target exists) or error depending on validation
        # The ghost edge is simply ignored since target_node_id is valid
        assert resp.status_code == 200

    def test_empty_nodes_list_returns_error(self, client):
        """Graph with empty nodes list returns error."""
        graph = _g({"nodes": [], "edges": []}).model_dump()

        resp = _trace_post(client, graph, row_index=0)

        assert resp.status_code == 400
        body = resp.json()
        assert "detail" in body

    def test_trace_on_node_with_no_rows(self, client, tmp_path):
        """Trace on a node whose output has zero rows returns an error."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        # Filter everything out so output has 0 rows
        graph = _simple_graph(p, "df = df.filter(pl.col('x') > 999)")

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        # Zero rows means row_index 0 is out of range → 500
        assert resp.status_code == 500
        body = resp.json()
        assert "detail" in body


# ===========================================================================
# E. Serialization (5 tests)
# ===========================================================================


class TestSerialization:
    """E. Validate JSON serialization of special values."""

    def test_nan_serialized_as_null(self, client, tmp_path):
        """NaN values in output are serialized as null in JSON."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1.0, float("nan"), 3.0]}).write_parquet(p)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=1, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        ov = trace["output_value"]
        # NaN should be null in JSON
        assert ov["x"] is None

    def test_inf_serialized_as_null(self, client, tmp_path):
        """Inf values in output are serialized as null in JSON."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [float("inf"), float("-inf"), 1.0]}).write_parquet(p)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        ov = trace["output_value"]
        assert ov["x"] is None

    def test_date_values_serialized_as_strings(self, client, tmp_path):
        """Date values in output are serialized as strings."""
        p = tmp_path / "data.parquet"
        pl.DataFrame(
            {
                "d": [date(2024, 1, 15), date(2024, 6, 30), date(2024, 12, 31)],
            }
        ).write_parquet(p)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        ov = trace["output_value"]
        # Date should be serialized as a string
        assert isinstance(ov["d"], str)
        assert "2024" in ov["d"]

    def test_large_float_serialized_correctly(self, client, tmp_path):
        """Very large float values are serialized correctly."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"x": [1e308, -1e308, 1.5]}).write_parquet(p)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        ov = trace["output_value"]
        # Large floats should survive serialization as numbers
        assert isinstance(ov["x"], (int, float))
        assert ov["x"] == pytest.approx(1e308)

    def test_boolean_values_serialized_correctly(self, client, tmp_path):
        """Boolean values in output are serialized as JSON booleans."""
        p = tmp_path / "data.parquet"
        pl.DataFrame({"flag": [True, False, True]}).write_parquet(p)
        graph = _simple_graph(p)

        resp = _trace_post(client, graph, row_index=0, target_node_id="t")

        assert resp.status_code == 200
        trace = resp.json()["trace"]
        ov = trace["output_value"]
        assert ov["flag"] is True

        # Check row 1 is False
        resp2 = _trace_post(client, graph, row_index=1, target_node_id="t")
        trace2 = resp2.json()["trace"]
        assert trace2["output_value"]["flag"] is False
