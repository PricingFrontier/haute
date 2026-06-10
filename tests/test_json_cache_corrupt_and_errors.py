"""Route-level error contracts for the JSON cache endpoints.

Regression coverage for the multi-frame review findings:

- a *present-but-corrupt* config file was collapsed into ``None`` and
  surfaced the misleading "No v2 schema source" message (a migration prompt)
  instead of naming the corruption — the precise "incorrect and hard to
  notice" fallback the project forbids. It must now return a distinct 422.
- a scalar array used to crash the strict build with an opaque 500; it must
  now build cleanly through the HTTP layer.
- a declared-vs-actual type mismatch, and a nested array, must surface a
  structured 422 naming the field — never an opaque 500.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    from haute.server import app

    return TestClient(app)


def _root_schema(columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": "data.json",
        "contract": "json",
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": columns,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Corrupt vs absent vs legacy config — distinct, honest messages
# ---------------------------------------------------------------------------


def test_build_present_but_corrupt_config_returns_distinct_422(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    # A config file that EXISTS but is byte-level corrupt (truncated write).
    (tmp_path / "cfg.json").write_bytes(b'{ "tables": [ {"path": "$[*]" ')

    resp = client.post(
        "/api/json-cache/build",
        json={"path": "data.json", "config_path": "cfg.json"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "ApiInputSchemaError"
    detail = body["detail"].lower()
    assert "not valid json" in detail or "corrupt" in detail
    # Crucially NOT the misleading migration message.
    assert "no v2 schema source" not in detail


def test_post_status_present_but_corrupt_config_returns_422(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    (tmp_path / "cfg.json").write_bytes(b"\xff\xfe not json at all")

    resp = client.post(
        "/api/json-cache/status",
        json={"path": "data.json", "config_path": "cfg.json"},
    )
    assert resp.status_code == 422
    assert resp.json()["type"] == "ApiInputSchemaError"


def test_get_status_corrupt_config_returns_cached_false(client: TestClient, tmp_path: Path) -> None:
    """GET /status is a read-only poll: a corrupt config means 'no valid schema',
    so it returns 200 cached=False (not a 500) — the precise corruption error
    surfaces on the build / POST-status paths instead."""
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    (tmp_path / "cfg.json").write_bytes(b"{ not valid json")
    resp = client.get(
        "/api/json-cache/status",
        params={"path": "data.json", "config_path": "cfg.json"},
    )
    assert resp.status_code == 200
    assert resp.json()["cached"] is False


def test_build_absent_config_returns_no_schema_source(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    resp = client.post(
        "/api/json-cache/build",
        json={"path": "data.json", "config_path": "does_not_exist.json"},
    )
    assert resp.status_code == 422
    assert "No v2 schema source" in resp.json()["detail"]


def test_build_legacy_config_is_migration_not_corruption(
    client: TestClient, tmp_path: Path
) -> None:
    """A valid-JSON v1 config (no tables[]) is the migration path, not corruption."""
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    (tmp_path / "cfg.json").write_text(json.dumps({"flattenSchema": {"id": "int"}}))

    resp = client.post(
        "/api/json-cache/build",
        json={"path": "data.json", "config_path": "cfg.json"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "No v2 schema source" in detail  # migration prompt, not corruption


# ---------------------------------------------------------------------------
# Loud, structured failures (never an opaque 500)
# ---------------------------------------------------------------------------


def test_infer_nested_array_returns_422_naming_field(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1, "matrix": [[1, 2], [3, 4]]}]))
    resp = client.post("/api/json-cache/infer", json={"path": "data.json"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "ApiInputSchemaError"
    assert "matrix" in body["detail"]


def test_build_type_mismatch_returns_422_naming_column(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"age": 30}, {"age": "oops"}]))
    schema = _root_schema(
        [
            {
                "name": "age",
                "path": "$[*].age",
                "type": "int",
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            }
        ]
    )
    resp = client.post(
        "/api/json-cache/build",
        json={"path": "data.json", "volatile_schema": schema},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "ApiInputSchemaError"
    assert "age" in body["detail"]


def test_read_v2_config_unreadable_path_raises(tmp_path: Path) -> None:
    """A present-but-unreadable config (here: a directory) raises a distinct
    error rather than being silently treated as 'no schema source'."""
    from haute._api_input_schema import ApiInputSchemaError
    from haute.routes.json_cache import _read_v2_config

    d = tmp_path / "a_dir"
    d.mkdir()  # exists() is True, but read_bytes() raises OSError on a directory
    with pytest.raises(ApiInputSchemaError) as ei:
        _read_v2_config(str(d))
    assert "could not be read" in str(ei.value)


def test_read_v2_config_strips_legacy_keys(tmp_path: Path) -> None:
    """A v2 config carrying stray legacy apiInput keys is returned with them stripped."""
    from haute.routes.json_cache import _read_v2_config

    cfg = tmp_path / "c.json"
    cfg.write_text(
        json.dumps(
            {
                "tables": [{"path": "$[*]", "label": "r", "emit": True, "columns": []}],
                "flattenSchema": {"x": "int"},
                "selected_columns": ["x"],
            }
        )
    )
    out = _read_v2_config(str(cfg))
    assert out is not None
    assert "tables" in out
    assert "flattenSchema" not in out
    assert "selected_columns" not in out


def test_infer_missing_data_file_returns_404(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/json-cache/infer", json={"path": "missing.json"})
    assert resp.status_code == 404


def test_get_status_without_config_returns_cached_false(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"id": 1}]))
    resp = client.get("/api/json-cache/status", params={"path": "data.json"})
    assert resp.status_code == 200
    assert resp.json()["cached"] is False


def test_aggregators_skip_missing_or_nonstr_parquet(tmp_path: Path) -> None:
    """The build/status response aggregators defensively skip a table whose
    parquet is absent or whose 'parquet' field isn't a string (internally
    inconsistent cache) without crashing — counts still aggregate."""
    from haute.routes.json_cache import (
        _aggregate_v2_build_response,
        _aggregate_v2_status_response,
    )

    summary = {
        "tables": [
            {"label": "a", "parquet": "gone.parquet", "row_count": 2, "column_count": 1},
            {"label": "b", "parquet": None, "row_count": 1, "column_count": 1},
        ],
        # Part of the build-summary contract since W2 item 2.7.
        "skipped": {"records": 0, "rows_by_table": {}},
    }
    build = _aggregate_v2_build_response(summary, tmp_path, "data.json", 0.1)
    assert build.row_count == 3
    assert build.size_bytes == 0  # neither parquet contributed a size

    status = _aggregate_v2_status_response(tmp_path, "data.json", summary)
    assert status.cached is True
    assert status.row_count == 3


def test_infer_then_build_scalar_array_end_to_end(client: TestClient, tmp_path: Path) -> None:
    """The headline repro, through the HTTP layer: infer → enable child → build → 200."""
    (tmp_path / "data.json").write_text(
        json.dumps(
            [
                {"policy_id": 1, "coverages": ["TPFT", "comprehensive"]},
                {"policy_id": 2, "coverages": ["home"]},
            ]
        )
    )
    infer = client.post("/api/json-cache/infer", json={"path": "data.json"})
    assert infer.status_code == 200, infer.text
    tables = infer.json()["tables"]
    labels = {t["label"] for t in tables}
    assert "$[*].coverages[*]" in labels  # scalar array became a child table
    for t in tables:
        t["emit"] = True  # user opts the child table in

    build = client.post(
        "/api/json-cache/build",
        json={"path": "data.json", "volatile_schema": {"tables": tables}},
    )
    assert build.status_code == 200, build.text
    # 2 policy rows + 3 coverage rows = 5 across the two emitted ports.
    assert build.json()["row_count"] == 5
