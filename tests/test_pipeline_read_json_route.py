from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute._sandbox import set_project_root


@pytest.fixture()
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    return tmp_path


@pytest.fixture()
def client(work_dir: Path) -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


def test_read_json_returns_object_payload(client: TestClient, work_dir: Path) -> None:
    artifact = work_dir / "artifacts" / "optimiser.json"
    artifact.parent.mkdir()
    artifact.write_text('{"version":"v1","mode":"online","lambdas":{"loss":0.5}}')

    resp = client.post("/api/pipeline/read-json", json={"path": "artifacts/optimiser.json"})

    assert resp.status_code == 200
    assert resp.json() == {
        "version": "v1",
        "mode": "online",
        "lambdas": {"loss": 0.5},
    }


def test_read_json_rejects_missing_file(client: TestClient) -> None:
    resp = client.post("/api/pipeline/read-json", json={"path": "missing.json"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "File not found: missing.json"


def test_read_json_rejects_non_json_extension(client: TestClient, work_dir: Path) -> None:
    artifact = work_dir / "artifact.txt"
    artifact.write_text("not json")

    resp = client.post("/api/pipeline/read-json", json={"path": "artifact.txt"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only .json files are supported"


def test_read_json_rejects_invalid_json(client: TestClient, work_dir: Path) -> None:
    artifact = work_dir / "broken.json"
    artifact.write_text("{not valid json")

    resp = client.post("/api/pipeline/read-json", json={"path": "broken.json"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid JSON file"


def test_read_json_rejects_non_object_payload(client: TestClient, work_dir: Path) -> None:
    artifact = work_dir / "array.json"
    artifact.write_text('["a","b"]')

    resp = client.post("/api/pipeline/read-json", json={"path": "array.json"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "JSON file must contain an object"


def test_read_json_rejects_path_traversal(client: TestClient) -> None:
    resp = client.post("/api/pipeline/read-json", json={"path": "../outside.json"})

    assert resp.status_code == 403
