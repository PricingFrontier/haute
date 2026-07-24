"""Regression coverage for preview JSON payload shaping."""

from __future__ import annotations

from datetime import date

import polars as pl

from haute._types import GraphNode, NodeData, PipelineGraph
from haute.executor import PreviewProjectionError, execute_graph
from haute.schemas import ColumnInfo, NodeResult
from tests.conftest import make_file_input_config


def _source_node(nid: str, path: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType="dataInput",
            config=make_file_input_config(path),
        ),
    )


def test_wide_preview_caps_rows_by_cell_budget(monkeypatch, tmp_path) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_MAX_CELLS", 12)
    path = tmp_path / "wide.parquet"
    pl.DataFrame({f"c{i}": list(range(10)) for i in range(6)}).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        max_preview_rows=10,
    )["src"]

    assert result.row_count == 10
    assert result.column_count == 6
    assert len(result.preview) == 2
    assert set(result.preview[0]) == {f"c{i}" for i in range(6)}
    assert result.preview_row_count == 2
    assert result.preview_row_limit == 2
    assert result.preview_truncated is True


def test_narrow_preview_keeps_existing_row_limit(monkeypatch, tmp_path) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_MAX_CELLS", 100)
    path = tmp_path / "narrow.parquet"
    pl.DataFrame({"a": list(range(10)), "b": list(range(10))}).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        max_preview_rows=10,
    )["src"]

    assert len(result.preview) == 10
    assert result.preview_row_count == 10
    assert result.preview_row_limit == 10
    assert result.preview_truncated is False


def test_ultra_wide_preview_returns_zero_rows_to_honor_cell_budget(monkeypatch, tmp_path) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_MAX_CELLS", 3)
    path = tmp_path / "ultra_wide.parquet"
    pl.DataFrame({f"c{i}": [1, 2] for i in range(4)}).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        max_preview_rows=10,
    )["src"]

    assert result.row_count == 2
    assert result.column_count == 4
    assert result.preview == []
    assert result.preview_row_count == 0
    assert result.preview_row_limit == 0
    assert result.preview_truncated is True


def test_preview_route_preserves_truncation_metadata_and_json_safety(
    client,
    monkeypatch,
) -> None:
    import haute.routes.pipeline as pipeline

    def fake_execute_graph(*_args, **_kwargs) -> dict[str, NodeResult]:
        return {
            "target": NodeResult(
                status="ok",
                row_count=10,
                column_count=2,
                columns=[
                    ColumnInfo(name="when", dtype="Date"),
                    ColumnInfo(name="value", dtype="Int64"),
                ],
                available_columns=[
                    ColumnInfo(name="when", dtype="Date"),
                    ColumnInfo(name="value", dtype="Int64"),
                ],
                preview=[{"when": date(2026, 1, 2), "value": 7}],
                preview_row_count=1,
                preview_row_limit=1,
                preview_truncated=True,
            )
        }

    monkeypatch.setattr(pipeline, "execute_graph", fake_execute_graph)

    response = client.post(
        "/api/pipeline/preview",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "target",
                        "data": {
                            "label": "Target",
                            "nodeType": "polars",
                            "config": {},
                        },
                    }
                ],
                "edges": [],
            },
            "node_id": "target",
            "row_limit": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] == [{"when": "2026-01-02", "value": 7}]
    assert body["preview_row_count"] == 1
    assert body["preview_row_limit"] == 1
    assert body["preview_truncated"] is True


def test_requested_preview_columns_project_rows_but_keep_full_schema(tmp_path) -> None:
    path = tmp_path / "wide.parquet"
    pl.DataFrame(
        {
            "age": [25, 30],
            "premium": [100.5, 200.0],
            "segment": ["A", "B"],
        }
    ).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        max_preview_rows=10,
        requested_preview_columns=["premium", "segment"],
    )["src"]

    assert [column.name for column in result.columns] == ["age", "premium", "segment"]
    assert result.column_count == 3
    assert result.preview_columns == ["premium", "segment"]
    assert result.preview == [
        {"premium": 100.5, "segment": "A"},
        {"premium": 200.0, "segment": "B"},
    ]


def test_first_click_target_preview_caps_materialized_columns_but_keeps_schema(
    monkeypatch,
    tmp_path,
) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_INITIAL_COLUMN_LIMIT", 3)
    executor._preview_cache.invalidate()
    path = tmp_path / "wide_first_click.parquet"
    pl.DataFrame({f"c{i}": [i] for i in range(5)}).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        target_preview_only=True,
    )["src"]

    assert [column.name for column in result.columns] == [f"c{i}" for i in range(5)]
    assert result.column_count == 5
    assert result.preview_columns == ["c0", "c1", "c2"]
    assert result.preview == [{"c0": 0, "c1": 1, "c2": 2}]

    fp = executor._preview_cache.fingerprint
    assert fp is not None
    cache_entry = executor._preview_cache.try_get(fp)
    assert cache_entry is not None
    assert cache_entry["eager_outputs"]["src"].columns == ["c0", "c1", "c2"]
    assert [name for name, _dtype in cache_entry["output_columns"]["src"]] == [
        f"c{i}" for i in range(5)
    ]
    executor._preview_cache.invalidate()


def test_first_click_target_preview_does_not_collect_columns_beyond_initial_cap(
    monkeypatch,
    tmp_path,
) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_INITIAL_COLUMN_LIMIT", 2)
    executor._preview_cache.invalidate()
    path = tmp_path / "first_click_pushdown.parquet"
    pl.DataFrame({"feature": [1, 2], "keep": [3, 4]}).write_parquet(path)

    graph = PipelineGraph(
        nodes=[
            _source_node("source", str(path)),
            GraphNode(
                id="target",
                data=NodeData(
                    label="target",
                    nodeType="polars",
                    config={
                        "code": """
df = df.with_columns(
    unused_bomb=pl.col("feature").map_elements(
        lambda _value: (_ for _ in ()).throw(RuntimeError("unused column evaluated")),
        return_dtype=pl.Int64,
    )
)
""",
                    },
                ),
            ),
        ],
        edges=[{"id": "source-target", "source": "source", "target": "target"}],
    )

    result = execute_graph(
        graph,
        target_node_id="target",
        target_preview_only=True,
    )["target"]

    assert result.status == "ok"
    assert [column.name for column in result.columns] == [
        "feature",
        "keep",
        "unused_bomb",
    ]
    assert result.column_count == 3
    assert result.preview_columns == ["feature", "keep"]
    assert result.preview == [{"feature": 1, "keep": 3}, {"feature": 2, "keep": 4}]

    fp = executor._preview_cache.fingerprint
    assert fp is not None
    cache_entry = executor._preview_cache.try_get(fp)
    assert cache_entry is not None
    assert cache_entry["eager_outputs"]["target"].columns == ["feature", "keep"]
    assert [name for name, _dtype in cache_entry["output_columns"]["target"]] == [
        "feature",
        "keep",
        "unused_bomb",
    ]
    executor._preview_cache.invalidate()


def test_first_click_capped_cache_does_not_satisfy_broad_preview(
    monkeypatch,
    tmp_path,
) -> None:
    import haute.executor as executor

    monkeypatch.setattr(executor, "PREVIEW_INITIAL_COLUMN_LIMIT", 2)
    executor._preview_cache.invalidate()
    path = tmp_path / "wide_then_broad.parquet"
    pl.DataFrame({f"c{i}": [i] for i in range(5)}).write_parquet(path)
    graph = PipelineGraph(nodes=[_source_node("src", str(path))])

    first_click = execute_graph(
        graph,
        target_node_id="src",
        target_preview_only=True,
    )["src"]
    assert first_click.preview_columns == ["c0", "c1"]

    broad = execute_graph(
        graph,
        target_node_id="src",
        target_preview_only=False,
    )["src"]

    assert broad.status == "ok"
    assert [column.name for column in broad.columns] == [f"c{i}" for i in range(5)]
    assert broad.preview_columns == [f"c{i}" for i in range(5)]
    assert broad.preview == [{f"c{i}": i for i in range(5)}]

    fp = executor._preview_cache.fingerprint
    assert fp is not None
    cache_entry = executor._preview_cache.try_get(fp)
    assert cache_entry is not None
    assert cache_entry["eager_outputs"]["src"].columns == [f"c{i}" for i in range(5)]
    executor._preview_cache.invalidate()


def test_target_only_preview_reports_upstream_error_on_target(tmp_path) -> None:
    path = tmp_path / "upstream_error.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    graph = PipelineGraph(
        nodes=[
            _source_node("source", str(path)),
            GraphNode(
                id="bad",
                data=NodeData(
                    label="bad",
                    nodeType="polars",
                    config={"code": 'raise RuntimeError("bad upstream schema")'},
                ),
            ),
            GraphNode(
                id="target",
                data=NodeData(
                    label="target",
                    nodeType="polars",
                    config={"code": "df = df"},
                ),
            ),
        ],
        edges=[
            {"id": "source-bad", "source": "source", "target": "bad"},
            {"id": "bad-target", "source": "bad", "target": "target"},
        ],
    )

    result = execute_graph(
        graph,
        target_node_id="target",
        target_preview_only=True,
    )["target"]

    assert result.status == "error"
    assert result.error is not None
    assert "Upstream node(s) failed" in result.error
    assert "bad" in result.error
    assert "bad upstream schema" in result.error


def test_requested_preview_columns_fail_for_missing_columns(tmp_path) -> None:
    path = tmp_path / "wide.parquet"
    pl.DataFrame({"age": [25], "premium": [100.5]}).write_parquet(path)

    try:
        execute_graph(
            PipelineGraph(nodes=[_source_node("src", str(path))]),
            target_node_id="src",
            requested_preview_columns=["age", "missing"],
        )
    except PreviewProjectionError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - makes the test failure clearer
        raise AssertionError("missing requested preview column should fail loudly")


def test_requested_preview_columns_dedupe_and_reject_empty_names(tmp_path) -> None:
    path = tmp_path / "wide.parquet"
    pl.DataFrame({"age": [25], "premium": [100.5]}).write_parquet(path)

    result = execute_graph(
        PipelineGraph(nodes=[_source_node("src", str(path))]),
        target_node_id="src",
        requested_preview_columns=["premium", "premium", "age"],
    )["src"]

    assert result.preview_columns == ["premium", "age"]
    assert result.preview == [{"premium": 100.5, "age": 25}]

    try:
        execute_graph(
            PipelineGraph(nodes=[_source_node("src", str(path))]),
            target_node_id="src",
            requested_preview_columns=["age", ""],
        )
    except PreviewProjectionError as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover - makes the test failure clearer
        raise AssertionError("empty requested preview column should fail loudly")


def test_preview_route_forwards_requested_columns_and_reports_projection(
    client,
    monkeypatch,
) -> None:
    import haute.routes.pipeline as pipeline

    seen_requested_columns: list[list[str] | None] = []

    def fake_execute_graph(*_args, **kwargs) -> dict[str, NodeResult]:
        seen_requested_columns.append(kwargs.get("requested_preview_columns"))
        return {
            "target": NodeResult(
                status="ok",
                row_count=1,
                column_count=3,
                columns=[
                    ColumnInfo(name="age", dtype="Int64"),
                    ColumnInfo(name="premium", dtype="Float64"),
                    ColumnInfo(name="segment", dtype="String"),
                ],
                available_columns=[
                    ColumnInfo(name="age", dtype="Int64"),
                    ColumnInfo(name="premium", dtype="Float64"),
                    ColumnInfo(name="segment", dtype="String"),
                ],
                preview=[{"premium": 100.5}],
                preview_columns=["premium"],
                preview_row_count=1,
                preview_row_limit=100,
            )
        }

    monkeypatch.setattr(pipeline, "execute_graph", fake_execute_graph)

    response = client.post(
        "/api/pipeline/preview",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "target",
                        "data": {
                            "label": "Target",
                            "nodeType": "polars",
                            "config": {},
                        },
                    }
                ],
                "edges": [],
            },
            "node_id": "target",
            "row_limit": 100,
            "requested_preview_columns": ["premium"],
        },
    )

    assert response.status_code == 200
    assert seen_requested_columns == [["premium"]]
    body = response.json()
    assert [column["name"] for column in body["columns"]] == ["age", "premium", "segment"]
    assert body["preview_columns"] == ["premium"]
    assert body["preview"] == [{"premium": 100.5}]


def test_preview_route_returns_400_for_missing_requested_columns(
    client,
    monkeypatch,
) -> None:
    import haute.routes.pipeline as pipeline

    def fake_execute_graph(*_args, **_kwargs) -> dict[str, NodeResult]:
        raise PreviewProjectionError("Requested preview column(s) not found on target: missing")

    monkeypatch.setattr(pipeline, "execute_graph", fake_execute_graph)

    response = client.post(
        "/api/pipeline/preview",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "target",
                        "data": {
                            "label": "Target",
                            "nodeType": "polars",
                            "config": {},
                        },
                    }
                ],
                "edges": [],
            },
            "node_id": "target",
            "row_limit": 100,
            "requested_preview_columns": ["missing"],
        },
    )

    assert response.status_code == 400
    assert "missing" in response.json()["detail"]


def test_preview_route_keeps_non_projection_value_errors_internal(
    client,
    monkeypatch,
) -> None:
    import haute.routes.pipeline as pipeline

    def fake_execute_graph(*_args, **_kwargs) -> dict[str, NodeResult]:
        raise ValueError("internal execution detail")

    monkeypatch.setattr(pipeline, "execute_graph", fake_execute_graph)

    response = client.post(
        "/api/pipeline/preview",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "target",
                        "data": {
                            "label": "Target",
                            "nodeType": "polars",
                            "config": {},
                        },
                    }
                ],
                "edges": [],
            },
            "node_id": "target",
            "row_limit": 100,
        },
    )

    assert response.status_code == 500
    assert "internal execution detail" not in response.text
