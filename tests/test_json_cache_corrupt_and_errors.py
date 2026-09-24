"""Error contracts of schema inference, and inferred schemas building cleanly.

Regression coverage for the multi-frame review findings:

- a scalar array used to crash the strict build with an opaque 500; an
  inferred schema must now build its table snapshots cleanly.
- a nested array must surface a structured 422 naming the field — never an
  opaque 500.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._json_shred._cache import load_v2_api_source
from tests.conftest import build_test_api_input_snapshots


def _built_frames(data_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Build the inferred schema's table snapshots and read every table back."""
    build_test_api_input_snapshots(data_path, config)
    return {
        label: frame.collect()
        for label, frame in load_v2_api_source(str(data_path), config, read_snapshots=True).items()
    }


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
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": columns,
            }
        ],
    }


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


def test_infer_rejects_non_identifier_key_before_returning_schema(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "data.json").write_text(json.dumps([{"policy-id": 1}]))

    resp = client.post("/api/json-cache/infer", json={"path": "data.json"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "ApiInputSchemaError"
    assert "policy-id" in body["detail"]
    assert "rename" in body["detail"].lower()


def test_infer_missing_data_file_returns_404(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/json-cache/infer", json={"path": "missing.json"})
    assert resp.status_code == 404


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
    labels_by_path = {t["path"]: t["label"] for t in tables}
    assert labels_by_path == {
        "$[:]": "quote_info",
        "$[:].coverages[:]": "coverages",
    }
    for t in tables:
        t["emit"] = True  # user opts the child table in

    frames = _built_frames(tmp_path / "data.json", {"tables": tables})
    # 2 policy rows + 3 coverage rows = 5 across the two emitted ports.
    assert {label: frame.height for label, frame in frames.items()} == {
        "quote_info": 2,
        "coverages": 3,
    }


def test_ndjson_alias_infers_and_builds_end_to_end(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "events.ndjson").write_text(
        '{"id":1,"kind":"start"}\n{"id":2,"kind":"finish"}\n',
        encoding="utf-8",
    )

    infer = client.post("/api/json-cache/infer", json={"path": "events.ndjson"})
    assert infer.status_code == 200, infer.text

    frames = _built_frames(tmp_path / "events.ndjson", {"tables": infer.json()["tables"]})

    assert frames["quote_info"].height == 2
    assert dict(frames["quote_info"].schema) == {"id": pl.Int64, "kind": pl.String}


def test_inferred_string_widening_builds_mixed_json_scalars(
    client: TestClient, tmp_path: Path
) -> None:
    data_path = tmp_path / "data.json"
    data_path.write_text(
        json.dumps([{"code": 100}, {"code": "A1"}, {"code": True}]),
        encoding="utf-8",
    )
    infer = client.post("/api/json-cache/infer", json={"path": "data.json"})
    assert infer.status_code == 200, infer.text
    tables = infer.json()["tables"]
    code = next(column for column in tables[0]["columns"] if column["name"] == "code")
    assert code["type"] == "str"

    frames = _built_frames(data_path, {"tables": tables})
    assert dict(frames["quote_info"].schema) == {"code": pl.String}
    assert frames["quote_info"]["code"].to_list() == ["100", "A1", "true"]
