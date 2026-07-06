"""Bug regression tests for issues found in deep code review.

Each test is designed to FAIL on buggy code and PASS once the fix is applied.
Tests are grouped by bug ID from the review report.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# B9: RustyStats scoring passes unfiltered DataFrame to predict
# ---------------------------------------------------------------------------


class TestBugB9RustystatsUnfilteredPredict:
    def test_prepare_predict_frame_filters_rustystats(self) -> None:
        """_prepare_predict_frame should select only feature columns for rustystats."""
        from haute._mlflow_io import _prepare_predict_frame

        df = pl.DataFrame(
            {
                "feat_a": [1.0, 2.0],
                "feat_b": [3.0, 4.0],
                "target": [10.0, 20.0],
                "weight": [1.0, 1.0],
            }
        )
        result = _prepare_predict_frame(
            df,
            features=["feat_a", "feat_b"],
            cat_feature_names=frozenset(),
            flavor="rustystats",
        )
        # Should only have feature columns, not target/weight
        assert set(result.columns) == {"feat_a", "feat_b"}


# ---------------------------------------------------------------------------
# B12: Zero-row DataFrame crashes batched scoring path
# ---------------------------------------------------------------------------


class TestBugB12ZeroRowBatchScoring:
    def test_batch_score_empty_input(self, tmp_path: Path) -> None:
        """Batched scoring on zero-row input should produce empty output, not crash."""
        from haute._model_scorer import _batch_score_to_parquet

        # Create empty input parquet with schema
        input_path = str(tmp_path / "empty_input.parquet")
        pl.DataFrame(
            {"a": pl.Series([], dtype=pl.Float64), "b": pl.Series([], dtype=pl.Float64)}
        ).write_parquet(input_path)

        # The zero-row path runs a 1-row synthetic probe through
        # _prepare_predict_frame to derive output dtypes (F676), so the mock
        # must satisfy the real scoring contract: a valid SSOT flavor string, a
        # concrete cat_feature_names set, and a predict that returns a genuine
        # prediction for the probe row (from which the empty column dtype is
        # derived).
        scoring_model = MagicMock()
        scoring_model.flavor = "catboost"
        scoring_model.cat_feature_names = frozenset()
        scoring_model.feature_names = ["a", "b"]
        scoring_model.predict.return_value = [0.0]

        out_path = _batch_score_to_parquet(
            scoring_model,
            input_path,
            ["a", "b"],
            "pred",
            "regression",
        )
        # Should produce a valid parquet file, not crash
        result = pl.read_parquet(out_path)
        assert len(result) == 0
        os.unlink(out_path)


# ---------------------------------------------------------------------------
# B13/B14: Streaming chunk size not restored
# ---------------------------------------------------------------------------


class TestBugB13StreamingChunkSizeRestore:
    def test_execute_sink_never_restores_auto_chunk_size_to_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto chunk-size mode must not be restored via ``set_streaming_chunk_size(0)``."""
        from haute.executor import execute_sink
        from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

        out_path = tmp_path / "out.parquet"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src",
                        nodeType="dataSource",
                        config={"path": "unused.parquet"},
                    ),
                ),
                GraphNode(
                    id="sink",
                    data=NodeData(
                        label="sink",
                        nodeType="dataSink",
                        config={"path": str(out_path), "format": "parquet"},
                    ),
                ),
            ],
            edges=[GraphEdge(id="e1", source="src", target="sink")],
        )
        lazy_outputs = {"sink": pl.DataFrame({"x": [1, 2, 3]}).lazy()}

        calls: list[int] = []
        original_set_chunk_size = pl.Config.set_streaming_chunk_size

        def record_chunk_size(value: int) -> None:
            calls.append(int(value))
            original_set_chunk_size(value)

        monkeypatch.setattr(pl.Config, "set_streaming_chunk_size", record_chunk_size)
        with (
            patch("haute.executor.pl.Config.state", return_value={}),
            patch(
                "haute.executor._execute_lazy",
                return_value=(lazy_outputs, ["src", "sink"], {}, {}),
            ),
        ):
            result = execute_sink(graph, "sink")

        assert result.status == "ok"
        assert result.row_count == 3
        assert 0 not in calls


# ---------------------------------------------------------------------------
# B15: Empty Databricks table fetch crashes
# ---------------------------------------------------------------------------


class TestBugB15EmptyDatabricksFetch:
    def test_empty_fetch_writes_valid_parquet(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fetching a table with zero rows should produce valid empty parquet.

        The real connector materializes every ``fetchmany_arrow`` result —
        including the terminating empty batch — against the query's result
        manifest schema, so the fake returns a schema-bearing empty table
        and the cache must preserve those REAL column types (4a.8: not an
        all-string rebuild from cursor.description).
        """
        import databricks.sql as dbsql
        import pyarrow as pa

        from haute._databricks_io import fetch_and_cache, fetch_progress

        empty_result = pa.schema(
            [("quote_id", pa.int64()), ("premium", pa.float64())]
        ).empty_table()

        class FakeCursor:
            rownumber = 0

            def __init__(self) -> None:
                self.executed: list[str] = []

            def __enter__(self) -> FakeCursor:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def execute(self, query: str) -> None:
                self.executed.append(query)

            def fetchmany_arrow(self, batch_size: int) -> pa.Table:
                assert batch_size == 17
                return empty_result

        class FakeConnection:
            def __init__(self, cursor: FakeCursor) -> None:
                self._cursor = cursor

            def __enter__(self) -> FakeConnection:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def cursor(self) -> FakeCursor:
                return self._cursor

        fake_cursor = FakeCursor()

        def fake_connect(**kwargs: object) -> FakeConnection:
            assert kwargs == {
                "server_hostname": "example.cloud.databricks.com",
                "http_path": "/sql/warehouse",
                "access_token": "token",
            }
            return FakeConnection(fake_cursor)

        monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "token")
        monkeypatch.setattr(dbsql, "connect", fake_connect)

        table = "catalog.schema.empty_table"
        result = fetch_and_cache(
            table,
            http_path="/sql/warehouse",
            project_root=tmp_path,
            batch_size=17,
        )

        out_path = Path(result["path"])
        assert result["row_count"] == 0
        assert out_path.is_file()
        cached = pl.read_parquet(out_path)
        assert cached.height == 0
        assert dict(cached.schema) == {"quote_id": pl.Int64, "premium": pl.Float64}
        assert fake_cursor.executed == ["SELECT * FROM catalog.schema.empty_table"]
        assert fetch_progress(table) is None


# ---------------------------------------------------------------------------
# B17: ws_clients set mutation during async iteration
# ---------------------------------------------------------------------------


class TestBugB17WsClientsSetIteration:
    @pytest.mark.asyncio
    async def test_broadcast_tolerates_client_set_mutation_during_send(self) -> None:
        """A client connecting during broadcast must not mutate the active iteration."""
        from haute.routes import _helpers

        class FakeWebSocket:
            def __init__(self, on_send=None) -> None:
                self.messages: list[dict[str, object]] = []
                self._on_send = on_send

            async def send_text(self, payload: str) -> None:
                self.messages.append(json.loads(payload))
                if self._on_send is not None:
                    self._on_send()

        late_client = FakeWebSocket()
        mutating_client = FakeWebSocket(lambda: _helpers.ws_clients_add(late_client))
        stable_client = FakeWebSocket()

        with _helpers.ws_clients_lock:
            _helpers.ws_clients.clear()
            _helpers.ws_clients.update({mutating_client, stable_client})
        try:
            await _helpers.broadcast({"type": "test", "data": 42})
        finally:
            with _helpers.ws_clients_lock:
                _helpers.ws_clients.clear()

        assert mutating_client.messages == [{"type": "test", "data": 42}]
        assert stable_client.messages == [{"type": "test", "data": 42}]
        assert late_client.messages == []


# ---------------------------------------------------------------------------
# B18: TOCTOU race in cache lookup
# ---------------------------------------------------------------------------


class TestBugB18CacheTOCTOU:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_same_timestamp_overwrite_invalidates_external_object_cache(
        self,
        tmp_path: Path,
    ) -> None:
        """Cache keys must include file content, not just a path/timestamp probe."""
        from haute._io import _load_cached, load_external_object

        path = tmp_path / "payload.json"
        path.write_text('{"value": 1}', encoding="utf-8")
        original_stat = path.stat()

        _load_cached.cache_clear()
        first = load_external_object(str(path), "json")

        path.write_text('{"value": 2}', encoding="utf-8")
        os.utime(
            path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        second = load_external_object(str(path), "json")

        assert first == {"value": 1}
        assert second == {"value": 2}


# ---------------------------------------------------------------------------
# B19: Mutable cached dicts shared by reference
# ---------------------------------------------------------------------------


class TestBugB19MutableCachedDicts:
    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_cached_artifact_is_not_shared_reference(self, tmp_path: Path) -> None:
        """Returned artifact dicts should be copies, not shared cache references."""
        from haute._optimiser_io import _load_artifact_cached, load_optimiser_artifact

        # Create a test artifact file
        artifact = {"lambdas": {"a": 1.0}, "version": "1", "mode": "online"}
        path = tmp_path / "artifact.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")

        _load_artifact_cached.cache_clear()

        result1 = load_optimiser_artifact(str(path))
        # Mutate the returned dict
        result1["lambdas"]["a"] = 999.0

        # Load again - should get original value, not mutated
        result2 = load_optimiser_artifact(str(path))
        assert result2["lambdas"]["a"] == 1.0, (
            "Cached dict was mutated by caller - should return a copy"
        )


# ---------------------------------------------------------------------------
# B20: Feature importance not sorted before truncation
# ---------------------------------------------------------------------------


class TestBugB20FeatureImportanceSorting:
    def test_top_features_are_most_important(self) -> None:
        """render_horizontal_bars_svg should show top-N by importance, not first-N."""
        from haute.modelling._charts import render_horizontal_bars_svg

        # Features in alphabetical order with importance values
        data = [
            {"feature": "a_feature", "importance": 0.01},
            {"feature": "b_feature", "importance": 0.02},
            {"feature": "c_feature", "importance": 0.50},
            {"feature": "d_feature", "importance": 0.30},
            {"feature": "e_feature", "importance": 0.10},
            {"feature": "f_feature", "importance": 0.05},
            {"feature": "g_feature", "importance": 0.02},
        ]
        svg = render_horizontal_bars_svg(
            data, name_key="feature", value_key="importance", max_items=3
        )
        # The top 3 by importance are c_feature(0.50), d_feature(0.30), e_feature(0.10)
        assert "c_feature" in svg
        assert "d_feature" in svg
        # a_feature (0.01) should NOT be in the top 3
        assert "a_feature" not in svg


# ---------------------------------------------------------------------------
# B22: JSON files read without encoding="utf-8"
# ---------------------------------------------------------------------------


class TestBugB22MissingUtf8Encoding:
    def test_io_uses_utf8_encoding(self) -> None:
        """All JSON file reads in _io.py should use encoding='utf-8'."""
        import inspect

        import haute._io as io_mod

        source = inspect.getsource(io_mod)
        # Count open() calls - they should all have encoding
        import re

        opens = re.findall(r"open\([^)]+\)", source)
        for call in opens:
            if (
                "encoding" not in call
                and "'wb'" not in call
                and '"wb"' not in call
                and "'rb'" not in call
                and '"rb"' not in call
            ):
                # open() without encoding and not binary mode
                if (
                    "json" in source[source.index(call) - 100 : source.index(call)].lower()
                    or "read" in call
                ):
                    pytest.fail(f"open() call without encoding='utf-8': {call}")

    def test_optimiser_io_uses_utf8_encoding(self) -> None:
        """All JSON file reads in _optimiser_io.py should use encoding='utf-8'."""
        import inspect

        import haute._optimiser_io as opt_io

        source = inspect.getsource(opt_io)
        # Check that open() calls for reading have encoding
        import re

        opens = re.findall(r"with open\([^)]+\) as", source)
        for call in opens:
            if "encoding" not in call and "'rb'" not in call and '"rb"' not in call:
                pytest.fail(f"open() call without encoding='utf-8': {call}")


# ---------------------------------------------------------------------------
# B8: GLM cross_validate drops alpha/l1_ratio
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B5: Pruner assumes inputs[0] is live branch
# ---------------------------------------------------------------------------


class TestBugB5PrunerLiveBranchSelection:
    def test_live_branch_from_scenario_map_not_position(self) -> None:
        """Pruner should use input_scenario_map, not hardcode inputs[0] as live."""
        from haute._types import PipelineGraph
        from haute.deploy._pruner import _live_only_edges

        def _node(nid, ntype="polars", config=None):
            return {
                "id": nid,
                "position": {"x": 0, "y": 0},
                "data": {"label": nid, "nodeType": ntype, "config": config or {}},
            }

        def _edge(src, tgt):
            return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt}

        # live source is the SECOND input, not the first
        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    _node("batch_src"),
                    _node("live_src"),
                    _node(
                        "sw",
                        "liveSwitch",
                        {
                            "inputs": ["batch_src", "live_src"],
                            "input_scenario_map": {"live_src": "live", "batch_src": "batch"},
                        },
                    ),
                ],
                "edges": [
                    _edge("batch_src", "sw"),
                    _edge("live_src", "sw"),
                ],
            }
        )
        result = _live_only_edges(graph.nodes, graph.edges)
        result_pairs = {(e.source, e.target) for e in result}
        # Should keep the live edge (live_src->sw), not batch (batch_src->sw)
        assert ("live_src", "sw") in result_pairs
        assert ("batch_src", "sw") not in result_pairs


# ---------------------------------------------------------------------------
# B7: dissolve_submodel flattens ALL submodels
# ---------------------------------------------------------------------------


class TestBugB7DissolveTargetOnly:
    def test_dissolve_preserves_other_submodels(self) -> None:
        """Dissolving one submodel should not flatten others."""
        from haute.graph_utils import (
            GraphEdge,
            GraphNode,
            NodeData,
            NodeType,
            PipelineGraph,
            flatten_graph,
        )

        def node(node_id: str, node_type: NodeType) -> GraphNode:
            return GraphNode(
                id=node_id,
                data=NodeData(label=node_id, nodeType=node_type, config={}),
            )

        graph = PipelineGraph(
            nodes=[
                node("src", NodeType.DATA_SOURCE),
                node("submodel__rating", NodeType.SUBMODEL),
                node("submodel__pricing", NodeType.SUBMODEL),
                node("out", NodeType.OUTPUT),
            ],
            edges=[
                GraphEdge(
                    id="e_src_rating",
                    source="src",
                    target="submodel__rating",
                    targetHandle="in__rating_step_1",
                ),
                GraphEdge(
                    id="e_rating_pricing",
                    source="submodel__rating",
                    target="submodel__pricing",
                    sourceHandle="out__rating_step_2",
                    targetHandle="in__pricing_step_1",
                ),
                GraphEdge(
                    id="e_pricing_out",
                    source="submodel__pricing",
                    target="out",
                    sourceHandle="out__pricing_step_2",
                ),
            ],
            submodels={
                "rating": {
                    "graph": {
                        "nodes": [
                            node("rating_step_1", NodeType.POLARS).model_dump(),
                            node("rating_step_2", NodeType.POLARS).model_dump(),
                        ],
                        "edges": [
                            GraphEdge(
                                id="e_rating_internal",
                                source="rating_step_1",
                                target="rating_step_2",
                            ).model_dump()
                        ],
                    }
                },
                "pricing": {
                    "graph": {
                        "nodes": [
                            node("pricing_step_1", NodeType.POLARS).model_dump(),
                            node("pricing_step_2", NodeType.POLARS).model_dump(),
                        ],
                        "edges": [
                            GraphEdge(
                                id="e_pricing_internal",
                                source="pricing_step_1",
                                target="pricing_step_2",
                            ).model_dump()
                        ],
                    }
                },
            },
        )

        flattened = flatten_graph(graph, target_name="rating")
        node_ids = {n.id for n in flattened.nodes}
        edge_pairs = {(e.source, e.target) for e in flattened.edges}

        assert "submodel__rating" not in node_ids
        assert {"rating_step_1", "rating_step_2"} <= node_ids
        assert "submodel__pricing" in node_ids
        assert "pricing_step_1" not in node_ids
        assert "pricing_step_2" not in node_ids
        assert set((flattened.submodels or {}).keys()) == {"pricing"}
        assert ("src", "rating_step_1") in edge_pairs
        assert ("rating_step_2", "submodel__pricing") in edge_pairs


# ---------------------------------------------------------------------------
# B11: selected_columns not applied to instance nodes
# ---------------------------------------------------------------------------


class TestBugB11InstanceSelectedColumns:
    def test_instance_inherits_selected_columns(self) -> None:
        """Instance nodes should apply the original's selected_columns filter."""
        from haute._builders import resolve_instance_node
        from haute._types import PipelineGraph

        def _node(nid, ntype="polars", config=None):
            return {
                "id": nid,
                "position": {"x": 0, "y": 0},
                "data": {"label": nid, "nodeType": ntype, "config": config or {}},
            }

        graph = PipelineGraph.model_validate(
            {
                "nodes": [
                    _node("original", "polars", {"selected_columns": ["a", "b"], "code": ""}),
                    _node("instance", "polars", {"instanceOf": "original"}),
                ],
                "edges": [],
            }
        )
        node_map = {n.id: n for n in graph.nodes}
        resolved = resolve_instance_node(node_map["instance"], node_map)
        # The resolved config should include selected_columns from the original
        assert resolved.data.config.get("selected_columns") == ["a", "b"]


# ---------------------------------------------------------------------------
# B13/B14: Streaming chunk size not restored
# ---------------------------------------------------------------------------


class TestBugB13B14ChunkSizeRestore:
    def test_explicit_prior_chunk_size_is_restored(self, tmp_path: Path) -> None:
        """Explicit pre-existing chunk size must survive a sink execution."""
        from haute.executor import execute_sink
        from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

        out_path = tmp_path / "out.parquet"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="src",
                    data=NodeData(
                        label="src",
                        nodeType="dataSource",
                        config={"path": "unused.parquet"},
                    ),
                ),
                GraphNode(
                    id="sink",
                    data=NodeData(
                        label="sink",
                        nodeType="dataSink",
                        config={"path": str(out_path), "format": "parquet"},
                    ),
                ),
            ],
            edges=[GraphEdge(id="e1", source="src", target="sink")],
        )
        lazy_outputs = {"sink": pl.DataFrame({"x": [1, 2]}).lazy()}

        pl.Config.set_streaming_chunk_size(75_000)
        with patch(
            "haute.executor._execute_lazy",
            return_value=(lazy_outputs, ["src", "sink"], {}, {}),
        ):
            result = execute_sink(graph, "sink")

        assert result.status == "ok"
        assert pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE") == "75000"

    @pytest.mark.skip(reason="Superseded by behavioral chunk-size restoration tests.")
    def test_chunk_size_restore_does_not_pass_zero(self) -> None:
        """Polars streaming chunk size restore must not call set_streaming_chunk_size(0).

        Polars rejects 0 with ``ValueError: number of rows per chunk must be >= 1``.
        When the previous chunk size was None (Polars auto-default), the restore
        must be skipped — there is no API to "unset" the streaming chunk size.
        """
        import inspect

        from haute import executor

        source = inspect.getsource(executor.execute_sink)
        # The old buggy pattern passed 0 when _prev_chunk_size was None:
        #   set_streaming_chunk_size(int(x) if x is not None else 0)
        # This raises ValueError. The fix guards the restore with an if check.
        assert "else 0" not in source, (
            "Chunk size restore must not fall back to 0 — Polars rejects it"
        )


# ---------------------------------------------------------------------------
# B16: validate_deploy not called in programmatic path
# ---------------------------------------------------------------------------


class TestBugB16ValidateDeployCall:
    def test_deploy_calls_validate_before_backend_deploy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The programmatic deploy() function should call validate_deploy."""
        import haute.deploy as deploy_mod

        config = MagicMock()
        config.target = "databricks"
        resolved = MagicMock()
        resolved.config = config
        deployed = object()
        calls: list[str] = []

        def fake_resolve_config(received_config: object) -> object:
            assert received_config is config
            calls.append("resolve")
            return resolved

        def fake_validate_deploy(received_resolved: object) -> None:
            assert received_resolved is resolved
            calls.append("validate")

        def fake_deploy_to_mlflow(received_resolved: object) -> object:
            assert received_resolved is resolved
            calls.append("deploy")
            return deployed

        monkeypatch.setattr(deploy_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(deploy_mod, "validate_deploy", fake_validate_deploy)
        monkeypatch.setattr(deploy_mod, "deploy_to_mlflow", fake_deploy_to_mlflow)

        assert deploy_mod.deploy(config) is deployed
        assert calls == ["resolve", "validate", "deploy"]


# TestBugB8GlmCvRegularization deleted in Phase 2 Package 2C-5: the
# ``GLMAlgorithm.cross_validate`` method was removed along with the dead
# GLM CV code path in ``TrainingJob``. Alpha is still forwarded by
# ``GLMAlgorithm.fit`` (covered by tests/test_rustystats_algorithm.py).
