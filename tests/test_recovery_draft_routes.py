"""Recovery draft HTTP contract coverage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from haute._cache import canonical_json
from haute._event_bus import default_bus
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._recovery_schemas import RecoveryDraftList, RecoveryDraftPatch
from haute.routes._helpers import save_lock


def _project(root: Path) -> tuple[Path, bytes, bytes]:
    source = root / "main.py"
    config = root / "custom.json"
    (root / "haute.toml").write_text('[project]\nname="route-draft"\n')
    config.write_text('{"values":[{"name":"kept","value":"2"}],"retired":true}')
    source.write_text(
        'import haute\nimport polars as pl\npipeline = haute.Pipeline("route-draft")\n\n'
        '@pipeline.constant(config="custom.json")\ndef value():\n'
        '    return pl.LazyFrame({"kept": [2.0]})\n'
    )
    return source, source.read_bytes(), config.read_bytes()


def _create(client: TestClient, root: Path) -> dict:
    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    response = client.post(
        "/api/pipeline/repair/drafts",
        json={
            "source_file": document.source_file,
            "source_revision": document.source_revision,
            "targets": [{"source_file": "main.py", "recovery_id": "value"}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_recovery_contracts_endpoint_exposes_every_node_schema(client: TestClient) -> None:
    response = client.get("/api/pipeline/repair/contracts")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["fingerprint"], str)
    assert len(payload["schemas"]) == 19


def test_create_draft_rejects_extra_dto_fields(client: TestClient) -> None:
    response = client.post(
        "/api/pipeline/repair/drafts",
        json={
            "source_file": "pipeline.py",
            "source_revision": "revision",
            "targets": [{"source_file": "pipeline.py", "recovery_id": "node"}],
            "unexpected": True,
        },
    )
    assert response.status_code == 422


def test_invalid_draft_discriminator_is_saved_as_an_editable_field_issue(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    source = tmp_path / "main.py"
    source.write_text(source.read_text().replace("pipeline.constant", "pipeline.data_input"))
    (tmp_path / "custom.json").write_text(
        '{"inputType":"file","format":"parquet","mode":"scan","path":"quotes.parquet"}'
    )
    before = source.read_bytes()
    draft = _create(client, tmp_path)
    node = draft["nodes"][0]
    invalid = {**node["config"], "inputType": []}
    base = f"/api/pipeline/repair/drafts/{draft['draft_id']}"
    response = client.post(
        base + "/edit",
        json={"draft_revision": draft["draft_revision"], "configs": {node["key"]: invalid}},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["state"] == "needs_configuration"
    assert edited["nodes"][0]["editable"]
    assert edited["nodes"][0]["config"]["inputType"] == []
    assert any(issue["path"] == "inputType" for issue in edited["nodes"][0]["issues"])
    assert client.get(base).json() == edited
    preview = client.post(base + "/preview", json={"draft_revision": edited["draft_revision"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["plan_hash"] is None
    corrected = client.post(
        base + "/edit",
        json={"draft_revision": edited["draft_revision"], "configs": {node["key"]: node["config"]}},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["nodes"][0]["issues"] == []
    assert source.read_bytes() == before


def test_http_draft_lifecycle_applies_idempotently_and_restores_exact_bytes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.routes import recovery

    monkeypatch.chdir(tmp_path)
    source, before_source, before_config = _project(tmp_path)
    events: list[dict] = []
    invalidations: list[bool] = []
    monkeypatch.setattr(
        recovery, "invalidate_dataframe_execution_cache", lambda: invalidations.append(True)
    )
    unsubscribe = default_bus.subscribe("pipeline.document.update", events.append)
    draft = _create(client, tmp_path)
    node = draft["nodes"][0]
    assert node["config"]["values"] == [{"name": "kept", "value": "2"}]
    assert any(change["path"] == "/retired" for change in node["changes"])
    edited = client.post(
        f"/api/pipeline/repair/drafts/{draft['draft_id']}/edit",
        json={"draft_revision": draft["draft_revision"], "reviewed": True},
    )
    assert edited.status_code == 200, edited.text
    draft = edited.json()
    preview = client.post(
        f"/api/pipeline/repair/drafts/{draft['draft_id']}/preview",
        json={"draft_revision": draft["draft_revision"]},
    )
    assert preview.status_code == 200, preview.text
    assert events == []
    plan_hash = preview.json()["plan_hash"]
    assert plan_hash, preview.json()
    request = {
        "draft_revision": draft["draft_revision"],
        "source_revision": draft["source_revision"],
        "plan_hash": plan_hash,
        "operation_id": "route-apply-1",
    }
    applied = client.post(f"/api/pipeline/repair/drafts/{draft['draft_id']}/apply", json=request)
    assert applied.status_code == 200, applied.text
    assert len(events) == 1
    assert invalidations == [True]
    event = events[0]
    assert event["source_file"] == "main.py"
    assert event["document"]["source_file"] == "main.py"
    assert (
        event["document_fingerprint"]
        == hashlib.sha256(canonical_json(event["document"]).encode("utf-8")).hexdigest()
    )
    assert (
        client.post(f"/api/pipeline/repair/drafts/{draft['draft_id']}/apply", json=request).json()
        == applied.json()
    )
    assert json.loads((tmp_path / "custom.json").read_text()) == {
        "values": [{"name": "kept", "value": "2"}]
    }
    assert (
        client.get("/api/pipeline/repair/drafts", params={"source_file": "main.py"}).status_code
        == 200
    )
    assert client.get(f"/api/pipeline/repair/drafts/{draft['draft_id']}").status_code == 200
    applied_payload = applied.json()
    restore_preview = client.post(
        f"/api/pipeline/repair/drafts/{draft['draft_id']}/restore-preview",
        json={"draft_revision": applied_payload["draft"]["draft_revision"]},
    )
    assert restore_preview.status_code == 200, restore_preview.text
    restored = client.post(
        f"/api/pipeline/repair/drafts/{draft['draft_id']}/restore",
        json={
            "draft_revision": applied_payload["draft"]["draft_revision"],
            "source_revision": applied_payload["document"]["source_revision"],
            "plan_hash": restore_preview.json()["plan_hash"],
            "operation_id": "route-restore-1",
        },
    )
    assert restored.status_code == 200, restored.text
    assert len(events) == 3  # apply, idempotent replay, and restore each publish current state.
    assert invalidations == [True, True, True]
    assert source.read_bytes() == before_source
    assert (tmp_path / "custom.json").read_bytes() == before_config

    def unavailable_cache() -> None:
        raise OSError("cache is unavailable")

    monkeypatch.setattr(recovery, "invalidate_dataframe_execution_cache", unavailable_cache)
    # Notification/cache trouble cannot turn a durable commit into an HTTP failure.
    replay = client.post(f"/api/pipeline/repair/drafts/{draft['draft_id']}/apply", json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json() == applied.json()
    unsubscribe()


def test_route_rejects_stale_and_malicious_draft_paths(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    draft = _create(client, tmp_path)
    stale = client.post(
        f"/api/pipeline/repair/drafts/{draft['draft_id']}/edit",
        json={"draft_revision": "stale", "reviewed": True},
    )
    assert stale.status_code == 409
    assert client.get("/api/pipeline/repair/drafts/../outside").status_code in {404, 409}
    traversal = client.post(
        "/api/pipeline/repair/drafts",
        json={
            "source_file": "../outside.py",
            "source_revision": "revision",
            "targets": [{"source_file": "../outside.py", "recovery_id": "x"}],
        },
    )
    assert traversal.status_code == 409


def test_patch_schema_rejects_nonfinite_json_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        RecoveryDraftPatch.model_validate(
            {"draft_revision": "revision", "configs": {"node": {"value": float("nan")}}}
        )


@pytest.mark.asyncio
async def test_draft_list_holds_shared_save_lock_during_threadpool_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft read cannot race an in-progress draft apply/save transaction."""
    from haute.routes import recovery
    from haute.server import app

    started = threading.Event()
    release = threading.Event()

    def blocking_list(_root: Path, _source_file: str) -> RecoveryDraftList:
        started.set()
        assert release.wait(timeout=5)
        return RecoveryDraftList(drafts=[])

    monkeypatch.setattr(recovery, "list_drafts", blocking_list)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        task = asyncio.create_task(client.get("/api/pipeline/repair/drafts?source_file=main.py"))
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert save_lock.locked()
        release.set()
        response = await task
    assert response.status_code == 200
