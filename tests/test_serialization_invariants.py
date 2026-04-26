"""Serialization invariants for UI-facing preview and schema payloads."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute.schemas import ColumnInfo, NodeResult
from haute.server import app
from tests.conftest import make_edge as _edge
from tests.conftest import make_graph as _g
from tests.conftest import make_source_node as _source_node
from tests.conftest import make_transform_node as _transform_node


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(project_root: Path) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _preview_graph(data_path: Path) -> dict:
    return _g(
        {
            "nodes": [
                _source_node("src", str(data_path)),
                _transform_node("target", ""),
            ],
            "edges": [_edge("src", "target")],
        }
    ).model_dump()


def test_schema_preview_serializes_non_finite_values_as_null(
    client: TestClient,
    project_root: Path,
) -> None:
    data_path = project_root / "data.parquet"
    pl.DataFrame({"value": [1.0, float("nan"), float("inf"), float("-inf")]}).write_parquet(
        data_path
    )

    resp = client.get("/api/schema", params={"path": "data.parquet"})

    assert resp.status_code == 200
    assert [row["value"] for row in resp.json()["preview"]] == [1.0, None, None, None]


def test_databricks_schema_preview_serializes_non_finite_values_as_null(
    client: TestClient,
    project_root: Path,
) -> None:
    cache_file = project_root / "cached.parquet"
    pl.DataFrame({"value": [float("nan"), float("inf")]}).write_parquet(cache_file)

    with patch("haute._databricks_io.cached_path", return_value=cache_file):
        resp = client.get(
            "/api/schema/databricks",
            params={"table": "catalog.schema.table"},
        )

    assert resp.status_code == 200
    assert [row["value"] for row in resp.json()["preview"]] == [None, None]


def test_preview_response_serializes_non_finite_values_as_null(
    client: TestClient,
    project_root: Path,
) -> None:
    data_path = project_root / "data.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(data_path)
    graph = _preview_graph(data_path)
    fake_result = NodeResult(
        status="ok",
        row_count=2,
        column_count=1,
        columns=[ColumnInfo(name="value", dtype="Float64")],
        available_columns=[ColumnInfo(name="value", dtype="Float64")],
        preview=[{"value": float("nan")}, {"value": float("inf")}],
    )

    with patch(
        "haute.routes.pipeline.run_blocking_with_response_timeout",
        new_callable=AsyncMock,
        return_value={"target": fake_result},
    ):
        resp = client.post(
            "/api/pipeline/preview",
            json={"graph": graph, "node_id": "target"},
        )

    assert resp.status_code == 200
    assert [row["value"] for row in resp.json()["preview"]] == [None, None]
