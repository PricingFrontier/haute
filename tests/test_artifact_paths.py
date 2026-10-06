"""Contained artifact paths and the cross-process project mutation lock."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from haute._artifact_paths import safe_path
from haute._pipeline_repair import PipelineRepairError
from haute._project_mutation_lock import ProjectMutationLock


def test_safe_path_rejects_lexical_and_alias_paths(tmp_path: Path) -> None:
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
