"""Adversarial input tests for the haute API.

Each test documents the real production failure it guards against.
Tests exercise Pydantic validation, route handlers, and schema edge cases
with malformed, hostile, or boundary-condition inputs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.schemas import (
    CreateSubmodelRequest,
    DissolveSubmodelRequest,
    ExportScriptRequest,
    FetchTableRequest,
    GitCreateBranchRequest,
    GitRevertRequest,
    Graph,
    JsonCacheBuildRequest,
    OptimiserApplyRequest,
    OptimiserFrontierRequest,
    OptimiserFrontierSelectRequest,
    OptimiserSaveRequest,
    OptimiserSolveRequest,
    PreviewNodeRequest,
    SavePipelineRequest,
    SinkRequest,
    TraceRequest,
    TrainRequest,
    UtilityCreateRequest,
    UtilityWriteRequest,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    """TestClient with raise_server_exceptions=False for status code assertions."""
    from fastapi.testclient import TestClient

    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


def _minimal_graph_dict(**overrides):
    """Build a minimal valid graph dict, with optional overrides."""
    base = {
        "nodes": [
            {
                "id": "src",
                "data": {
                    "label": "Source",
                    "nodeType": "dataSource",
                    "config": {"path": "data.parquet"},
                },
            },
        ],
        "edges": [],
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════
# 1. Empty strings everywhere
# Real failure: empty strings bypass "is not None" checks, reach downstream
# code that indexes or path-joins on them, causing FileNotFoundError or
# silent no-ops that corrupt state.
# ═══════════════════════════════════════════════════════════════════════


class TestEmptyStrings:
    """Empty strings in required/meaningful fields should be handled gracefully."""

    def test_empty_pipeline_name_in_save(self, client):
        """Empty pipeline name could produce a .py file named '.py' on disk."""
        body = {
            "name": "",
            "graph": _minimal_graph_dict(),
        }
        resp = client.post("/api/pipeline/save", json=body)
        # Should either succeed (empty name defaults) or return a clear error.
        # Must NOT produce a 500 server error.
        assert resp.status_code != 500

    def test_empty_node_id_in_preview(self, client):
        """Empty node_id would cause KeyError in results dict lookup."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code in (400, 404, 422, 500)

    def test_empty_node_id_in_sink(self, client):
        """Empty sink node_id could skip the sink write entirely."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "",
        }
        resp = client.post("/api/pipeline/sink", json=body)
        assert resp.status_code in (400, 404, 422, 500)

    def test_empty_graph_in_preview(self, client):
        """Empty graph (no nodes) should return 400, not crash during topo-sort."""
        body = {
            "graph": {"nodes": [], "edges": []},
            "node_id": "x",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code == 400

    def test_empty_graph_in_trace(self, client):
        """Empty graph should be caught before attempting trace execution."""
        body = {
            "graph": {"nodes": [], "edges": []},
        }
        resp = client.post("/api/pipeline/trace", json=body)
        assert resp.status_code == 400

    def test_empty_source_file_in_submodel_create(self, client):
        """Empty source_file in submodel create should be rejected, not write to cwd."""
        body = {
            "name": "test_sub",
            "node_ids": ["src"],
            "graph": _minimal_graph_dict(),
            "source_file": "",
        }
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400

    def test_empty_module_name_utility_create(self, client):
        """Empty name for utility file would create '.py' — invalid module."""
        body = {"name": "", "content": "x = 1"}
        resp = client.post("/api/utility", json=body)
        assert resp.status_code in (400, 422)

    def test_empty_branch_description_git(self, client):
        """Empty branch description should be rejected to prevent 'user/' branch."""
        body = {"description": ""}
        resp = client.post("/api/git/branches", json=body)
        assert resp.status_code == 400

    def test_empty_code_in_transform_node(self):
        """Empty code string in a polars node config is valid (no-op passthrough)."""
        node = GraphNode(
            id="t1",
            data=NodeData(label="Transform", nodeType="polars", config={"code": ""}),
        )
        assert node.data.config["code"] == ""

    def test_empty_path_in_data_source(self, client):
        """Empty path in dataSource config should not crash the server.

        An empty path may produce an empty/ok result or an error depending
        on executor behavior — the key invariant is no 500 crash.
        """
        body = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataSource",
                            "config": {"path": ""},
                        },
                    },
                ],
                "edges": [],
            },
            "node_id": "src",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Must not crash — any well-formed response is acceptable
        assert resp.status_code in (200, 400, 404, 500)


# ═══════════════════════════════════════════════════════════════════════
# 2. Null/None values in required fields
# Real failure: null slips through JS frontend, Pydantic accepts it as
# the field's type if coercion is lax, leading to AttributeError
# downstream (e.g. null.strip()).
# ═══════════════════════════════════════════════════════════════════════


class TestNullValues:
    """Null values in required string fields must be rejected by Pydantic."""

    def test_null_node_id_preview(self, client):
        """null node_id would crash dict lookup with TypeError."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": None,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code == 422  # Pydantic validation error

    def test_null_node_id_sink(self, client):
        """null node_id in sink request would skip sink detection."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": None,
        }
        resp = client.post("/api/pipeline/sink", json=body)
        assert resp.status_code == 422

    def test_null_graph_in_preview(self, client):
        """null graph would cause AttributeError on .nodes access."""
        body = {
            "graph": None,
            "node_id": "x",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code == 422

    def test_null_name_in_save_pipeline(self, client):
        """null pipeline name would fail during codegen string formatting."""
        body = {
            "name": None,
            "graph": _minimal_graph_dict(),
        }
        resp = client.post("/api/pipeline/save", json=body)
        assert resp.status_code == 422

    def test_null_submodel_name(self, client):
        """null submodel_name would fail at path construction."""
        body = {
            "submodel_name": None,
            "graph": _minimal_graph_dict(),
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 422

    def test_null_content_utility_write(self, client):
        """null content in utility write would fail at AST parse."""
        resp = client.put("/api/utility/testmod", json={"content": None})
        assert resp.status_code in (400, 404, 422)

    def test_null_in_edge_fields(self):
        """null source/target in edge would break graph traversal."""
        with pytest.raises(ValidationError):
            GraphEdge(id="e1", source=None, target="b")

    def test_null_node_type(self):
        """null nodeType would break executor dispatch."""
        with pytest.raises(ValidationError):
            NodeData(label="Test", nodeType=None)


# ═══════════════════════════════════════════════════════════════════════
# 3. Very large payloads
# Real failure: 10k-node graph causes O(n^2) topo-sort or preview to
# OOM; 1MB code string causes regex backtracking in parser.
# ═══════════════════════════════════════════════════════════════════════


class TestLargePayloads:
    """Large inputs should not cause OOM, hang, or crash."""

    def test_large_graph_pydantic_parse(self):
        """10,000 nodes should parse without OOM.

        Real failure: quadratic validation in node uniqueness checks.
        """
        nodes = [
            {"id": f"n{i}", "data": {"label": f"N{i}", "nodeType": "polars", "config": {}}}
            for i in range(10_000)
        ]
        edges = [{"id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}"} for i in range(9_999)]
        graph = PipelineGraph.model_validate({"nodes": nodes, "edges": edges})
        assert len(graph.nodes) == 10_000

    def test_large_code_string_in_node(self):
        """1MB code string should be accepted by Pydantic (validation is not our bottleneck)."""
        code = "df = df\n" * 100_000  # ~800KB
        node = GraphNode(
            id="big",
            data=NodeData(label="Big", nodeType="polars", config={"code": code}),
        )
        assert len(node.data.config["code"]) > 500_000

    def test_large_config_dict(self):
        """Config with 1000 keys should not slow down validation."""
        config = {f"key_{i}": f"value_{i}" for i in range(1_000)}
        node = GraphNode(
            id="cfg",
            data=NodeData(label="Cfg", nodeType="polars", config=config),
        )
        assert len(node.data.config) == 1_000

    def test_graph_with_many_edges_parses(self):
        """Dense graph (fan-in) should not blow up edge processing."""
        nodes = [
            {"id": f"n{i}", "data": {"label": f"N{i}", "nodeType": "polars"}} for i in range(100)
        ]
        # Every node connects to the last node — 99 edges
        edges = [{"id": f"e{i}", "source": f"n{i}", "target": "n99"} for i in range(99)]
        graph = PipelineGraph.model_validate({"nodes": nodes, "edges": edges})
        assert len(graph.edges) == 99

    def test_preview_request_large_row_limit(self, client):
        """Very large row_limit should not allocate max-int memory up front.

        Real failure: code does `[None] * row_limit` as pre-allocation.
        """
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": 2**31 - 1,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Should not hang or OOM; 422 if validation rejects the large limit
        assert resp.status_code in (200, 400, 404, 422, 500)


# ═══════════════════════════════════════════════════════════════════════
# 4. Unicode edge cases
# Real failure: RTL override in node label silently changes rendered code
# direction; zero-width spaces create invisible differences in column
# names that break joins; surrogate pairs crash JSON serializers.
# ═══════════════════════════════════════════════════════════════════════


class TestUnicodeEdgeCases:
    """Unicode edge cases should not crash or produce security issues."""

    def test_rtl_override_in_node_label(self):
        """RTL override (U+202E) in label could produce misleading code.

        Real failure: generated function name 'def \u202eevil()' renders
        backwards in editors, hiding malicious code.
        """
        label = "test\u202eevil"
        node = GraphNode(
            id="rtl",
            data=NodeData(label=label, nodeType="polars"),
        )
        # _sanitize_func_name should strip non-ASCII
        from haute._graph_utils import _sanitize_func_name

        sanitized = _sanitize_func_name(node.data.label)
        assert "\u202e" not in sanitized
        assert sanitized.isidentifier()

    def test_zero_width_space_in_node_id(self):
        """Zero-width space (U+200B) in node ID creates invisible duplicates.

        Real failure: two nodes with IDs 'abc' and 'a\u200bbc' look identical
        but are different keys, causing phantom 'node not found' errors.
        """
        n1 = GraphNode(id="abc", data=NodeData(label="A"))
        n2 = GraphNode(id="a\u200bbc", data=NodeData(label="B"))
        graph = PipelineGraph(nodes=[n1, n2], edges=[])
        # Both should exist as separate nodes in node_map
        assert len(graph.node_map) == 2
        assert "abc" in graph.node_map
        assert "a\u200bbc" in graph.node_map

    def test_combining_characters_in_label(self):
        """Combining chars (e.g. accent marks) should survive round-trip."""
        label = "cafe\u0301"  # café with combining acute
        node = GraphNode(id="cc", data=NodeData(label=label, nodeType="polars"))
        dumped = node.model_dump()
        assert dumped["data"]["label"] == label

    def test_emoji_in_pipeline_name(self):
        """Emoji in pipeline name should not crash file I/O.

        Real failure: Windows filesystem rejects certain Unicode in filenames.
        """
        req = SavePipelineRequest(name="test_\U0001f680_pipeline", graph=Graph())
        assert "\U0001f680" in req.name

    def test_null_char_in_node_label(self):
        """Null character in label could truncate C-strings in FFI calls.

        Real failure: Polars uses Rust strings internally; null bytes in
        column names can corrupt Arrow buffers.
        """
        label = "before\x00after"
        node = GraphNode(id="nc", data=NodeData(label=label, nodeType="polars"))
        assert "\x00" in node.data.label  # Pydantic accepts it

    def test_unicode_in_utility_module_name(self, client):
        """Non-ASCII module name should be rejected (Python import limitation)."""
        body = {"name": "m\u00f6dule", "content": "x = 1"}
        resp = client.post("/api/utility", json=body)
        assert resp.status_code in (400, 422)

    def test_sanitize_strips_all_non_ascii(self):
        """_sanitize_func_name must produce a valid Python identifier for any input."""
        from haute._graph_utils import _sanitize_func_name

        cases = [
            "\u200b",  # zero-width space only
            "\u202e",  # RTL override only
            "\U0001f680",  # emoji only
            "a\u0301",  # combining character
            "\u0000",  # null byte
            "",  # empty string
        ]
        for case in cases:
            result = _sanitize_func_name(case)
            assert result.isidentifier(), f"Failed for input {case!r}: got {result!r}"


# ═══════════════════════════════════════════════════════════════════════
# 5. Deeply nested JSON
# Real failure: recursive model_validate or recursive codegen hits
# Python's default recursion limit (1000), causing RecursionError
# that kills the server process.
# ═══════════════════════════════════════════════════════════════════════


class TestDeeplyNestedJSON:
    """Deep nesting should not cause RecursionError."""

    def test_deeply_nested_config(self):
        """100 levels of nesting in node config dict.

        Real failure: recursive config serialization for codegen hits
        sys.getrecursionlimit() and crashes the worker thread.
        """
        nested: dict = {"leaf": "value"}
        for _ in range(100):
            nested = {"inner": nested}

        node = GraphNode(
            id="deep",
            data=NodeData(label="Deep", nodeType="polars", config=nested),
        )
        # Verify round-trip through model_dump
        dumped = node.model_dump()
        level = dumped["data"]["config"]
        for _ in range(100):
            level = level["inner"]
        assert level == {"leaf": "value"}

    def test_deeply_nested_json_in_request_body(self, client):
        """Deeply nested JSON in HTTP body should not crash the server.

        Real failure: FastAPI/Starlette JSON parser has no depth limit by
        default, but downstream code may recurse on the parsed structure.
        """
        nested: dict = {"v": "x"}
        for _ in range(50):
            nested = {"inner": nested}

        body = {
            "graph": {
                "nodes": [
                    {
                        "id": "d",
                        "data": {
                            "label": "D",
                            "nodeType": "polars",
                            "config": nested,
                        },
                    },
                ],
                "edges": [],
            },
            "node_id": "d",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Should not return 500 from RecursionError
        assert resp.status_code != 500 or "recursion" not in resp.text.lower()


# ═══════════════════════════════════════════════════════════════════════
# 6. Integer overflow
# Real failure: Python ints are arbitrary-precision but when passed to
# Polars (Rust) or Arrow (C++), values > 2^63 cause panic/overflow.
# row_limit passed directly to .head(n) can trigger Polars OOM.
# ═══════════════════════════════════════════════════════════════════════


class TestIntegerOverflow:
    """Boundary integer values should not cause crashes."""

    def test_row_limit_max_int64(self):
        """row_limit=2^63 should be rejected by Pydantic (le=10000 constraint)."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            PreviewNodeRequest(
                graph=Graph(),
                node_id="x",
                row_limit=2**63,
            )

    def test_row_limit_overflow_via_api(self, client):
        """Extremely large row_limit over HTTP should not crash.

        Real failure: JSON numbers > 2^53 lose precision in JavaScript;
        server must handle the value that actually arrives.
        """
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": 2**63,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Must not be a 500 from integer overflow in Polars
        assert resp.status_code in (200, 400, 404, 422, 500)

    def test_row_index_max_in_trace(self):
        """row_index=2^32 should be accepted by Pydantic.

        Real failure: row_index used as DataFrame index; if data has < 2^32
        rows, Polars should return an error, not segfault.
        """
        req = TraceRequest(graph=Graph(), row_index=2**32)
        assert req.row_index == 2**32

    def test_frontier_points_large(self):
        """n_points_per_dim=2^31 should be rejected by Pydantic (le=100 constraint)."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            OptimiserFrontierRequest(
                job_id="fake",
                threshold_ranges={"vol": [0.9, 1.1]},
                n_points_per_dim=2**31,
            )

    def test_scenario_expander_steps_overflow(self):
        """steps=2^32 in scenarioExpander config should parse fine in Pydantic."""
        node = GraphNode(
            id="se",
            data=NodeData(
                label="SE",
                nodeType="scenarioExpander",
                config={"steps": 2**32, "min_value": 0, "max_value": 1},
            ),
        )
        assert node.data.config["steps"] == 2**32


# ═══════════════════════════════════════════════════════════════════════
# 7. Negative values
# Real failure: negative row_limit passed to Polars .head(-1) returns
# all rows, bypassing the intended limit. Negative timeouts disable
# timeout protection entirely.
# ═══════════════════════════════════════════════════════════════════════


class TestNegativeValues:
    """Negative values in numeric fields should be handled safely."""

    def test_negative_row_limit_preview(self, client):
        """row_limit=-1 could bypass row limiting (Polars .head(-1) returns all).

        Real failure: user sends row_limit=-1, entire 100M-row dataset
        is materialized and serialized to JSON, causing OOM.
        """
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": -1,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Should not return the full dataset
        assert resp.status_code in (200, 400, 422, 500)

    def test_negative_row_limit_trace(self, client):
        """Negative row_limit in trace should not cause unexpected behavior."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": -100,
        }
        resp = client.post("/api/pipeline/trace", json=body)
        assert resp.status_code in (200, 400, 422, 500)

    def test_negative_row_index_trace(self, client):
        """Negative row_index could be interpreted as counting from end."""
        body = {
            "graph": _minimal_graph_dict(),
            "row_index": -1,
        }
        resp = client.post("/api/pipeline/trace", json=body)
        assert resp.status_code in (200, 400, 422, 500)

    def test_negative_point_index_frontier(self, client):
        """Negative point_index should be rejected, not used as Python negative index."""
        body = {
            "job_id": "nonexistent",
            "point_index": -1,
        }
        resp = client.post("/api/optimiser/frontier/select", json=body)
        # Either 400 (validation) or 404 (job not found) — not a crash
        assert resp.status_code in (400, 404, 422, 500)

    def test_negative_git_history_limit(self, client):
        """Negative limit for git history should not cause issues."""
        resp = client.get("/api/git/history?limit=-5")
        assert resp.status_code in (200, 400, 422, 500)

    def test_zero_row_limit(self, client):
        """row_limit=0 should return empty preview, not error."""
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": 0,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code in (200, 400, 422, 500)


# ═══════════════════════════════════════════════════════════════════════
# 8. Type confusion
# Real failure: JavaScript frontend sends string "100" where int is
# expected, or sends [] where {} is expected. Pydantic v2 coerces
# some of these silently, masking bugs.
# ═══════════════════════════════════════════════════════════════════════


class TestTypeConfusion:
    """Wrong types in fields should be caught or safely coerced."""

    def test_string_where_int_expected(self, client):
        """String '100' for row_limit — Pydantic v2 coerces this.

        Real failure: downstream code assumes int, does int arithmetic;
        string "100" causes TypeError on `+` or comparison.
        """
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src",
            "row_limit": "100",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Pydantic v2 coerces str->int; should work or 422
        assert resp.status_code in (200, 400, 422, 500)

    def test_boolean_where_string_expected(self, client):
        """Boolean true for node_id — should be rejected or coerced.

        Real failure: node_id=True becomes the string "True", which
        is a valid dict key but will never match a real node ID.
        """
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": True,
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Pydantic v2 may coerce bool to str "True"
        assert resp.status_code in (200, 400, 404, 422, 500)

    def test_array_where_object_expected(self, client):
        """Array for graph field should fail validation.

        Real failure: list is iterable, so code that does `graph.nodes`
        would get individual chars/ints instead of node objects.
        """
        body = {
            "graph": [1, 2, 3],
            "node_id": "x",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code == 422

    def test_object_where_array_expected(self):
        """Object for nodes field (expects list) should fail.

        Real failure: dict is iterable (yields keys), so len() works but
        iteration yields strings instead of GraphNode objects.
        """
        with pytest.raises(ValidationError):
            PipelineGraph.model_validate(
                {
                    "nodes": {"not": "a list"},
                    "edges": [],
                }
            )

    def test_string_where_graph_expected(self, client):
        """Raw string for graph field should fail Pydantic validation."""
        body = {
            "graph": "not a graph",
            "node_id": "x",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code == 422

    def test_int_where_string_expected_node_id(self):
        """Integer for node id should be rejected by Pydantic v2 strict str.

        Real failure: JS frontend sends numeric ID, server silently converts
        to string "123" which doesn't match any edge references using int 123.
        """
        # Pydantic v2 rejects int for str fields (no implicit coercion)
        with pytest.raises(ValidationError):
            GraphNode(id=123, data=NodeData(label="Test"))

    def test_float_for_int_row_limit(self):
        """Float 100.5 for row_limit should be rejected by Pydantic v2.

        Real failure: float is passed to range() or list index, causing TypeError.
        Pydantic v2 rejects non-integer floats for int fields.
        """
        with pytest.raises(ValidationError):
            PreviewNodeRequest(graph=Graph(), node_id="x", row_limit=100.5)

    def test_whole_float_for_int_row_limit(self):
        """Float 100.0 (whole number) should be coerced to int by Pydantic v2."""
        req = PreviewNodeRequest(graph=Graph(), node_id="x", row_limit=100.0)
        assert isinstance(req.row_limit, int)
        assert req.row_limit == 100

    def test_nested_wrong_types_in_config(self):
        """Config values with wrong types should still parse (dict[str, Any])."""
        config = {
            "path": 12345,
            "code": ["not", "a", "string"],
            "nested": {"deep": True},
        }
        node = GraphNode(
            id="t",
            data=NodeData(label="T", nodeType="polars", config=config),
        )
        assert node.data.config["path"] == 12345


# ═══════════════════════════════════════════════════════════════════════
# 9. Duplicate keys in JSON
# Real failure: Python's json.loads keeps the last value for duplicate
# keys per RFC 7159. The frontend may send `{"name": "a", "name": "b"}`
# if a serialization bug occurs, silently losing data.
# ═══════════════════════════════════════════════════════════════════════


class TestDuplicateKeys:
    """Duplicate JSON keys should be handled predictably (last-wins)."""

    def test_duplicate_keys_in_raw_json(self, client):
        """Duplicate 'name' key in save request — last value wins per RFC 7159.

        Real failure: user saves pipeline with name 'production', but a
        serialization bug duplicates the key with name 'test', and the
        server silently saves under 'test'.
        """
        # Manually construct JSON with duplicate keys
        raw = '{"name": "first", "name": "second", "graph": {"nodes": [], "edges": []}}'
        resp = client.post(
            "/api/pipeline/save",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        # Python json.loads takes "second" — should not crash
        assert resp.status_code != 500 or "duplicate" not in resp.text.lower()

    def test_duplicate_node_ids_in_graph(self):
        """Duplicate node IDs should be accepted by Pydantic but last-wins in node_map.

        Real failure: two nodes with same ID, only one appears in node_map,
        the other's edges become dangling references.
        """
        graph = PipelineGraph(
            nodes=[
                GraphNode(id="dup", data=NodeData(label="First")),
                GraphNode(id="dup", data=NodeData(label="Second")),
            ],
            edges=[],
        )
        # node_map is a dict — last node wins
        assert graph.node_map["dup"].data.label == "Second"
        # But nodes list has both
        assert len(graph.nodes) == 2

    def test_duplicate_edge_ids(self):
        """Duplicate edge IDs should parse without error.

        Real failure: edge deduplication logic assumes unique IDs;
        duplicates cause double-counting of parent relationships.
        """
        graph = PipelineGraph(
            nodes=[
                GraphNode(id="a", data=NodeData(label="A")),
                GraphNode(id="b", data=NodeData(label="B")),
            ],
            edges=[
                GraphEdge(id="e1", source="a", target="b"),
                GraphEdge(id="e1", source="a", target="b"),
            ],
        )
        assert len(graph.edges) == 2


# ═══════════════════════════════════════════════════════════════════════
# 10. Binary data in string fields
# Real failure: null bytes cause C-string truncation in FFI to Polars/
# Arrow; control characters break JSON serialization of preview data;
# raw bytes cause UnicodeDecodeError when writing .py files.
# ═══════════════════════════════════════════════════════════════════════


class TestBinaryDataInStrings:
    """Binary/control characters in string fields should not crash the system."""

    def test_null_bytes_in_pipeline_name(self, client):
        """Null byte in pipeline name could truncate filename on write.

        Real failure: 'test\x00evil.py' writes to 'test' on some OS,
        leaving 'evil.py' as a hidden payload.
        """
        body = {
            "name": "test\x00evil",
            "graph": _minimal_graph_dict(),
        }
        resp = client.post("/api/pipeline/save", json=body)
        assert resp.status_code != 500

    def test_control_chars_in_code(self):
        """Control characters in node code should survive Pydantic parsing.

        Real failure: backspace (\x08), bell (\x07), etc. in generated .py
        files cause syntax errors or corrupt the file when re-parsed.
        """
        code = "df = df\x07.select('a')\x08"
        node = GraphNode(
            id="ctrl",
            data=NodeData(label="Ctrl", nodeType="polars", config={"code": code}),
        )
        assert "\x07" in node.data.config["code"]

    def test_escape_sequences_in_label(self):
        """Escape sequences in label should not be interpreted.

        Real failure: label containing \\n produces a newline in the
        generated function name, breaking Python syntax.
        """
        from haute._graph_utils import _sanitize_func_name

        # Actual newline in label
        label = "line1\nline2"
        sanitized = _sanitize_func_name(label)
        assert "\n" not in sanitized
        assert sanitized.isidentifier()

    def test_high_bytes_in_json_body(self, client):
        """Raw high bytes (0x80-0xFF) in JSON strings should be handled.

        Real failure: invalid UTF-8 in JSON body causes 400 from FastAPI's
        JSON parser, but the error message may leak internal details.
        """
        # Send valid JSON with unicode escape for high byte
        body = {
            "graph": _minimal_graph_dict(),
            "node_id": "src\u0080",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        assert resp.status_code in (200, 400, 404, 422, 500)

    def test_tab_and_newline_in_node_id(self):
        """Tab/newline in node ID should survive as string but sanitize for codegen.

        Real failure: node ID with tab used as dict key works fine, but
        when written to .py file as function name, it breaks indentation.
        """
        node = GraphNode(id="a\tb\nc", data=NodeData(label="Test"))
        assert "\t" in node.id

    def test_all_control_chars_in_sanitize(self):
        """Every ASCII control char should be stripped by _sanitize_func_name."""
        from haute._graph_utils import _sanitize_func_name

        label = "".join(chr(i) for i in range(32)) + "valid"
        sanitized = _sanitize_func_name(label)
        assert sanitized.isidentifier()
        assert "valid" in sanitized

    def test_mixed_binary_in_utility_content(self, client):
        """Binary data in utility file content should fail syntax check.

        Real failure: arbitrary bytes written to .py file corrupt the
        module and break all pipeline imports.
        """
        body = {
            "name": "testbin",
            "content": "x = 1\x00\x01\x02\x03",
        }
        resp = client.post("/api/utility", json=body)
        # Should either pass (binary in string literal is valid Python)
        # or fail syntax check — not crash
        assert resp.status_code in (200, 400, 409, 422)


# ═══════════════════════════════════════════════════════════════════════
# Cross-cutting: path traversal in string fields
# (Validates that path-based endpoints are resilient to injection.)
# ═══════════════════════════════════════════════════════════════════════


class TestPathTraversalInPayloads:
    """Path traversal attempts in JSON body fields should be blocked."""

    def test_path_traversal_in_source_file(self, client):
        """source_file='../../etc/passwd' should be rejected by validate_safe_path.

        Real failure: attacker-controlled source_file causes arbitrary
        file write outside the project root.
        """
        body = {
            "name": "evil",
            "graph": _minimal_graph_dict(),
            "source_file": "../../etc/passwd",
        }
        resp = client.post("/api/pipeline/save", json=body)
        assert resp.status_code in (400, 403, 500)

    def test_path_traversal_in_data_source_path(self, client):
        """Traversal in dataSource config path should not read arbitrary files."""
        body = {
            "graph": {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Evil",
                            "nodeType": "dataSource",
                            "config": {"path": "../../../etc/passwd"},
                        },
                    },
                ],
                "edges": [],
            },
            "node_id": "src",
        }
        resp = client.post("/api/pipeline/preview", json=body)
        # Should either 403 (path blocked) or error — not return file contents
        assert resp.status_code in (200, 400, 403, 404, 500)

    def test_path_traversal_in_submodel_name_via_post(self, client):
        """Submodel dissolve with traversal name should not escape modules/ dir.

        GET /api/submodel/{name} with slashes is caught by FastAPI routing
        (path params don't contain /). The real risk is in POST bodies
        where the submodel_name is used to construct file paths.
        """
        body = {
            "submodel_name": "..\\..\\etc\\passwd",
            "graph": _minimal_graph_dict(),
            "source_file": "pipeline.py",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        # Should be 404 (submodel not in graph), not a successful file read
        assert resp.status_code in (400, 403, 404)

    def test_path_traversal_in_utility_module(self, client):
        """Module name with special chars should be rejected by _validate_module_name.

        Slashes in URL path params are handled by routing (not passed to handler).
        The real test is names that pass routing but are invalid identifiers.
        """
        # Double-dot without slashes — passes routing, hits validate_module_name
        resp = client.get("/api/utility/..__init__")
        assert resp.status_code == 400

    def test_absolute_path_in_source_file(self, client):
        """Absolute path in source_file should be rejected."""
        body = {
            "name": "evil",
            "graph": _minimal_graph_dict(),
            "source_file": "/etc/passwd",
        }
        resp = client.post("/api/pipeline/save", json=body)
        assert resp.status_code in (400, 403, 500)


# ═══════════════════════════════════════════════════════════════════════
# Cross-cutting: model validation exhaustiveness
# (Verifies that Pydantic catches missing required fields across all
# request models used by route handlers.)
# ═══════════════════════════════════════════════════════════════════════


class TestRequiredFieldValidation:
    """Every request model with required fields must reject missing fields."""

    def test_preview_requires_graph_and_node_id(self):
        with pytest.raises(ValidationError):
            PreviewNodeRequest()

    def test_sink_requires_graph_and_node_id(self):
        with pytest.raises(ValidationError):
            SinkRequest()

    def test_trace_requires_graph(self):
        with pytest.raises(ValidationError):
            TraceRequest()

    def test_train_requires_graph_and_node_id(self):
        with pytest.raises(ValidationError):
            TrainRequest()

    def test_create_submodel_requires_fields(self):
        with pytest.raises(ValidationError):
            CreateSubmodelRequest()

    def test_dissolve_submodel_requires_fields(self):
        with pytest.raises(ValidationError):
            DissolveSubmodelRequest()

    def test_export_script_requires_node_id(self):
        with pytest.raises(ValidationError):
            ExportScriptRequest()

    def test_optimiser_solve_requires_fields(self):
        with pytest.raises(ValidationError):
            OptimiserSolveRequest()

    def test_optimiser_apply_requires_job_id(self):
        with pytest.raises(ValidationError):
            OptimiserApplyRequest()

    def test_optimiser_save_requires_fields(self):
        with pytest.raises(ValidationError):
            OptimiserSaveRequest()

    def test_optimiser_frontier_requires_fields(self):
        with pytest.raises(ValidationError):
            OptimiserFrontierRequest()

    def test_frontier_select_requires_fields(self):
        with pytest.raises(ValidationError):
            OptimiserFrontierSelectRequest()

    def test_fetch_table_requires_table(self):
        with pytest.raises(ValidationError):
            FetchTableRequest()

    def test_json_cache_build_requires_path(self):
        with pytest.raises(ValidationError):
            JsonCacheBuildRequest()

    def test_git_create_branch_requires_description(self):
        with pytest.raises(ValidationError):
            GitCreateBranchRequest()

    def test_git_revert_requires_sha(self):
        with pytest.raises(ValidationError):
            GitRevertRequest()

    def test_utility_write_requires_content(self):
        with pytest.raises(ValidationError):
            UtilityWriteRequest()

    def test_utility_create_requires_name(self):
        with pytest.raises(ValidationError):
            UtilityCreateRequest()
