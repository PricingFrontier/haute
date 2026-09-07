"""Crash-recovery and transaction boundary regressions for recovery drafts."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from haute._pipeline_repair import PipelineRepairError
from haute._project_mutation_lock import ProjectMutationLock
from haute._recovery_schemas import RecoveryDraftApply
from haute._recovery_storage import load_record, safe_path
from tests.test_recovery_drafts import _apply_review, _create, _project, _review


def _bytes(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in (root / "main.py", root / "custom.json")}


def test_startup_recovery_rolls_back_interrupted_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _pipeline_recovery_drafts as service
    from haute.routes import _save_pipeline as writes

    _project(tmp_path)
    before = _bytes(tmp_path)
    draft, preview = _review(tmp_path, _create(tmp_path))
    original_write, original_recover = writes._stage_artifact_write_bytes, service._recover_journal
    writes_seen = 0

    def crash_second_write(path: Path, payload: bytes, touched: list[Path]) -> None:
        nonlocal writes_seen
        writes_seen += 1
        if writes_seen == 2:
            raise SystemExit("simulated process crash")
        original_write(path, payload, touched)

    monkeypatch.setattr(writes, "_stage_artifact_write_bytes", crash_second_write)
    monkeypatch.setattr(
        service, "_recover_journal", lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit())
    )
    with pytest.raises(SystemExit):
        _apply_review(tmp_path, draft, preview)
    monkeypatch.setattr(writes, "_stage_artifact_write_bytes", original_write)
    monkeypatch.setattr(service, "_recover_journal", original_recover)
    recovered = service.get_draft(tmp_path, draft.draft_id)
    assert _bytes(tmp_path) == before
    assert recovered.draft_revision == draft.draft_revision


def test_startup_recovery_finishes_all_written_commit_and_reuses_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _pipeline_recovery_drafts as service

    _project(tmp_path)
    draft, preview = _review(tmp_path, _create(tmp_path))
    original_finish, original_recover = service._finish_operation, service._recover_journal
    monkeypatch.setattr(
        service, "_finish_operation", lambda *_args: (_ for _ in ()).throw(SystemExit())
    )
    monkeypatch.setattr(
        service, "_recover_journal", lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit())
    )
    with pytest.raises(SystemExit):
        _apply_review(tmp_path, draft, preview, operation_id="lost-response")
    monkeypatch.setattr(service, "_finish_operation", original_finish)
    monkeypatch.setattr(service, "_recover_journal", original_recover)
    applied = service.get_draft(tmp_path, draft.draft_id)
    assert applied.state == "applied"
    assert (
        _apply_review(tmp_path, draft, preview, operation_id="lost-response").draft.state
        == "applied"
    )
    with pytest.raises(PipelineRepairError, match="operation identity"):
        service.apply_draft(
            tmp_path,
            draft.draft_id,
            RecoveryDraftApply(
                draft_revision=draft.draft_revision,
                source_revision="different",
                plan_hash=preview.plan_hash,
                operation_id="lost-response",
            ),
        )


def test_interrupted_journal_never_overwrites_external_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _pipeline_recovery_drafts as service
    from haute.routes import _save_pipeline as writes

    _project(tmp_path)
    draft, preview = _review(tmp_path, _create(tmp_path))
    original_write, original_recover = writes._stage_artifact_write_bytes, service._recover_journal
    seen = 0

    def crash_second_write(path: Path, payload: bytes, touched: list[Path]) -> None:
        nonlocal seen
        seen += 1
        if seen == 2:
            raise SystemExit("crash")
        original_write(path, payload, touched)

    monkeypatch.setattr(writes, "_stage_artifact_write_bytes", crash_second_write)
    monkeypatch.setattr(
        service, "_recover_journal", lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit())
    )
    with pytest.raises(SystemExit):
        _apply_review(tmp_path, draft, preview, operation_id="external-overlap")
    monkeypatch.setattr(writes, "_stage_artifact_write_bytes", original_write)
    monkeypatch.setattr(service, "_recover_journal", original_recover)
    external = b'{"external":true}'
    (tmp_path / "custom.json").write_bytes(external)
    with pytest.raises(PipelineRepairError, match="interrupted recovery overlaps") as error:
        service.get_draft(tmp_path, draft.draft_id)
    assert error.value.code == "recovery_journal_conflict"
    assert (tmp_path / "custom.json").read_bytes() == external
    assert load_record(tmp_path, draft.draft_id)["journal"] is not None


def test_recovery_storage_rejects_lexical_and_alias_paths(tmp_path: Path) -> None:
    for value in ("../x", ".git/x", ".haute/x", str((tmp_path / "x").resolve())):
        with pytest.raises(PipelineRepairError):
            safe_path(tmp_path, value)
    target = tmp_path / "target.py"
    target.write_text("x")
    hard_link = tmp_path / "hard-linked.py"
    os.link(target, hard_link)
    with pytest.raises(PipelineRepairError):
        safe_path(tmp_path, "hard-linked.py")
    hard_link.unlink()
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(PipelineRepairError):
        safe_path(tmp_path, "linked.py")


def test_external_edit_after_last_write_is_not_committed_as_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _pipeline_recovery_drafts as service

    _project(tmp_path)
    draft, preview = _review(tmp_path, _create(tmp_path))
    original_finish = service._finish_operation
    external = b'{"constants":[{"name":"external","value":2}]}'

    def external_edit_then_finish(root: Path, record: dict):
        (root / "custom.json").write_bytes(external)
        return original_finish(root, record)

    monkeypatch.setattr(service, "_finish_operation", external_edit_then_finish)
    with pytest.raises(PipelineRepairError, match="interrupted recovery overlaps"):
        _apply_review(tmp_path, draft, preview)
    assert (tmp_path / "custom.json").read_bytes() == external
    assert load_record(tmp_path, draft.draft_id)["journal"] is not None


@pytest.mark.asyncio
async def test_project_mutation_locks_serialize_and_cancel_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first, second = ProjectMutationLock(), ProjectMutationLock()
    await first.acquire()
    waiting = asyncio.create_task(second.acquire())
    await asyncio.sleep(0.05)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not second.locked()
    first.release()
    assert await second.acquire()
    second.release()


@pytest.mark.asyncio
async def test_server_lifespan_remains_available_after_recovery_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import haute.deploy._config as deploy_config
    import haute.server as server

    _project(tmp_path)
    draft = _create(tmp_path)
    record_path = tmp_path / ".haute" / "recovery" / f"{draft.draft_id}.json"
    expected_record_bytes = record_path.read_bytes()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "_clear_bytecache", lambda: None)
    monkeypatch.setattr(server, "configure_logging", lambda: None)
    monkeypatch.setattr(deploy_config, "_load_env", lambda _path: None)
    monkeypatch.setattr(server, "configure_execution_telemetry", lambda: None)
    monkeypatch.setattr(server, "recover_json_runtime_storage", lambda: None)
    monkeypatch.setattr(server, "_ensure_pipeline_index", lambda: None)
    monkeypatch.setattr(server, "_artifact_stale_seconds", lambda: 86_400)

    lifecycle: list[str] = []
    monkeypatch.setattr(
        server,
        "start_interactive_worker_pool",
        lambda: lifecycle.append("started"),
    )
    monkeypatch.setattr(
        server,
        "shutdown_interactive_worker_pool",
        lambda: lifecycle.append("stopped"),
    )

    async def noop_watcher() -> None:
        await asyncio.Event().wait()

    async def noop_reaper(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(server, "_watcher_forever", noop_watcher)
    monkeypatch.setattr(server, "_reap_stale_optimiser_artifacts_in_background", noop_reaper)

    sensitive_marker = "sensitive-reconciliation-marker-key"

    def fail_recover_pending_drafts(root: Path) -> None:
        assert root == tmp_path.resolve()
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(
        "haute._pipeline_recovery_drafts.recover_pending_drafts",
        fail_recover_pending_drafts,
    )

    warning_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def mock_warning(*args: object, **kwargs: object) -> None:
        warning_calls.append((args, kwargs))

    monkeypatch.setattr(server.logger, "warning", mock_warning)

    async with server._lifespan(server.app):
        lifecycle.append("ready")

    reconcile_failures = [
        (args, kwargs)
        for args, kwargs in warning_calls
        if args and args[0] == "recovery_journal_reconcile_failed"
    ]
    assert len(reconcile_failures) == 1
    _args, kwargs = reconcile_failures[0]
    assert kwargs.get("code") == "RuntimeError"
    for args, kwargs in warning_calls:
        assert sensitive_marker not in str(args)
        assert sensitive_marker not in str(kwargs)

    assert record_path.read_bytes() == expected_record_bytes
    assert lifecycle == ["started", "ready", "stopped"]
    assert server._watcher_task is None
    assert server._optimiser_reaper_task is None
