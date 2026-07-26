"""Safety contract tests for crash-surviving artifact cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from haute._artifact_housekeeping import (
    create_owned_artifact_directory,
    reap_stale_artifact_directories,
)


def _marker(directory: Path, *, owner: str = "test-owner", created_at: float = 0.0) -> None:
    directory.mkdir()
    (directory / ".haute-artifact.json").write_text(
        json.dumps({"schema_version": 1, "owner": owner, "created_at": created_at}),
        encoding="utf-8",
    )


def _directory_symlink_for_test(
    target: Path,
    link: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a POSIX directory symlink or classify a Windows stand-in as one."""
    if os.name != "nt":
        os.symlink(target, link, target_is_directory=True)
        return

    link.mkdir()
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == link or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)


def test_reaper_removes_only_marked_owned_stale_direct_child(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    _marker(stale)
    (stale / "payload").write_bytes(b"abc")
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / ".haute-artifact.json").write_text("{", encoding="utf-8")
    wrong_owner = tmp_path / "wrong-owner"
    _marker(wrong_owner, owner="someone-else")
    fresh = tmp_path / "fresh"
    _marker(fresh, created_at=91.0)
    nested = tmp_path / "container"
    nested.mkdir()
    _marker(nested / "nested", created_at=0.0)

    report = reap_stale_artifact_directories(tmp_path, "test-owner", 10, now=100.0)

    assert report == {
        "inspected": 6,
        "removed": 1,
        "skipped": 5,
        "failed": 0,
        "reclaimed_bytes": 66,
    }
    assert not stale.exists()
    assert all(
        path.exists() for path in (unmarked, malformed, wrong_owner, fresh, nested / "nested")
    )


def test_reaper_skips_symlink_and_reaps_exact_stale_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    stale = root / "stale"
    _marker(stale, created_at=90.0)
    target = tmp_path / "outside"
    _marker(target, created_at=0.0)
    link = root / "linked"
    _directory_symlink_for_test(target, link, monkeypatch)

    report = reap_stale_artifact_directories(root, "test-owner", 10, now=100.0)

    assert report["removed"] == 1
    assert report["skipped"] == 1
    assert not stale.exists()
    assert target.exists()
    assert link.is_symlink()


@pytest.mark.parametrize(
    ("owner", "created_at"),
    [
        pytest.param(" \t", 0.0, id="blank-owner"),
        pytest.param("test-owner", -1.0, id="negative-time"),
    ],
)
def test_reaper_skips_semantically_invalid_marker(
    tmp_path: Path, owner: str, created_at: float
) -> None:
    invalid = tmp_path / "invalid"
    _marker(invalid, owner=owner, created_at=created_at)

    report = reap_stale_artifact_directories(tmp_path, "test-owner", 10, now=100.0)

    assert report["removed"] == 0
    assert report["skipped"] == 1
    assert invalid.exists()


def test_create_owned_directory_writes_valid_marker(tmp_path: Path) -> None:
    directory = create_owned_artifact_directory(tmp_path, "apply_", "test-owner")

    marker = json.loads((directory / ".haute-artifact.json").read_text(encoding="utf-8"))
    assert directory.parent == tmp_path
    assert marker["schema_version"] == 1
    assert marker["owner"] == "test-owner"
    assert isinstance(marker["created_at"], float)


def test_create_owned_directory_cleans_up_when_marker_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(OSError, match="disk full"):
        create_owned_artifact_directory(tmp_path, "apply_", "test-owner")

    assert list(tmp_path.iterdir()) == []


def test_create_owned_directory_cleans_up_invalid_clock_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("haute._artifact_housekeeping.time.time", lambda: float("nan"))

    with pytest.raises(RuntimeError, match="invalid artifact creation time"):
        create_owned_artifact_directory(tmp_path, "apply_", "test-owner")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stale_after_seconds", [False, float("nan"), float("inf"), -1])
def test_reaper_rejects_invalid_stale_interval(tmp_path: Path, stale_after_seconds: object) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        reap_stale_artifact_directories(
            tmp_path,
            "test-owner",
            stale_after_seconds,  # type: ignore[arg-type]
            now=100.0,
        )


@pytest.mark.parametrize("now", [False, float("nan"), float("inf"), -1])
def test_reaper_rejects_invalid_current_time(tmp_path: Path, now: object) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        reap_stale_artifact_directories(
            tmp_path,
            "test-owner",
            10,
            now=now,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "prefix",
    ["", ".", "..", "../escape_", "nested/escape_", r"nested\escape_"],
)
def test_create_owned_directory_rejects_non_component_prefix(tmp_path: Path, prefix: str) -> None:
    with pytest.raises(ValueError, match="path component"):
        create_owned_artifact_directory(tmp_path, prefix, "test-owner")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("owner", ["", " \t"])
def test_housekeeping_rejects_empty_owner(tmp_path: Path, owner: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        create_owned_artifact_directory(tmp_path, "apply_", owner)
    with pytest.raises(ValueError, match="non-empty"):
        reap_stale_artifact_directories(tmp_path, owner, 10, now=100.0)


def test_housekeeping_refuses_symlink_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root-link"
    _directory_symlink_for_test(target, root, monkeypatch)

    with pytest.raises(ValueError, match="root must not be a symlink"):
        create_owned_artifact_directory(root, "apply_", "test-owner")

    assert reap_stale_artifact_directories(root, "test-owner", 10, now=100.0) == {
        "inspected": 0,
        "removed": 0,
        "skipped": 0,
        "failed": 0,
        "reclaimed_bytes": 0,
    }
    assert list(target.iterdir()) == []


def test_optimiser_artifact_creators_write_owner_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.routes import _optimiser_service as service

    apply_root = tmp_path / "apply"
    factors_root = tmp_path / "factors"
    apply_root.mkdir()
    factors_root.mkdir()
    monkeypatch.setattr(service, "_prepare_apply_artifact_root", lambda: apply_root)
    monkeypatch.setattr(service, "_prepare_ratebook_factors_artifact_root", lambda: factors_root)

    apply_handle = service._persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"value": [1]}))
    )
    factors_handle = service._persist_ratebook_factors_artifact(
        pl.DataFrame({"factor": ["a"], "value": [1.0]})
    )

    assert apply_handle is not None
    assert factors_handle is not None
    apply_marker = json.loads(
        (Path(apply_handle["directory"]) / ".haute-artifact.json").read_text(encoding="utf-8")
    )
    factors_marker = json.loads(
        (Path(factors_handle["directory"]) / ".haute-artifact.json").read_text(encoding="utf-8")
    )
    assert apply_marker["owner"] == "optimiser_apply"
    assert factors_marker["owner"] == "optimiser_ratebook_factors"


def test_optimiser_reaper_targets_only_owned_marked_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.routes import _optimiser_service as service

    apply_root = tmp_path / "apply"
    factors_root = tmp_path / "factors"
    apply_root.mkdir()
    factors_root.mkdir()
    _marker(apply_root / "stale", owner="optimiser_apply")
    _marker(factors_root / "stale", owner="optimiser_ratebook_factors")
    unrelated = apply_root / "unrelated"
    unrelated.mkdir()
    monkeypatch.setattr(service, "_apply_artifact_root", lambda: apply_root)
    monkeypatch.setattr(service, "_ratebook_factors_artifact_root", lambda: factors_root)
    reports = service.reap_stale_optimiser_artifacts(0)

    assert reports["apply"]["removed"] == 1
    assert reports["ratebook_factors"]["removed"] == 1
    assert unrelated.exists()


def test_optimiser_reaper_rejects_invalid_stale_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import _optimiser_service as service

    monkeypatch.setenv("HAUTE_ARTIFACT_STALE_SECONDS", "1.5")

    with pytest.raises(ValueError, match="non-negative integer"):
        service._artifact_stale_seconds()


def test_server_lifespan_reaps_artifacts_without_delaying_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.deploy._config as deploy_config
    import haute.server as server

    calls: list[str] = []
    lifespan_thread = threading.get_ident()
    reaper_started = threading.Event()
    allow_reaper_finish = threading.Event()

    monkeypatch.setattr(server, "_clear_bytecache", lambda: calls.append("clear_bytecache"))
    monkeypatch.setattr(server, "configure_logging", lambda: calls.append("configure_logging"))
    monkeypatch.setattr(deploy_config, "_load_env", lambda _path: calls.append("load_env"))
    monkeypatch.setattr(server, "configure_execution_telemetry", lambda: calls.append("telemetry"))
    monkeypatch.setattr(
        server,
        "_artifact_stale_seconds",
        lambda: calls.append("artifact_config") or 86_400,
        raising=False,
    )

    def blocking_reaper(*_args: object, **_kwargs: object) -> None:
        calls.append(f"reap_artifacts:{threading.get_ident()}")
        reaper_started.set()
        if not allow_reaper_finish.wait(timeout=5):
            raise TimeoutError("test did not release optimiser artifact reaper")

    monkeypatch.setattr(
        server,
        "reap_stale_optimiser_artifacts",
        blocking_reaper,
    )
    monkeypatch.setattr(server, "_ensure_pipeline_index", lambda: calls.append("pipeline_index"))

    async def noop_watcher() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_watcher_forever", noop_watcher)

    async def exercise_lifespan() -> None:
        entered = asyncio.Event()
        leave = asyncio.Event()

        async def run_lifespan() -> None:
            async with server._lifespan(server.app):
                calls.append("ready")
                entered.set()
                await leave.wait()

        task = asyncio.create_task(run_lifespan())
        try:
            assert await asyncio.to_thread(reaper_started.wait, 1)
            await asyncio.wait_for(entered.wait(), timeout=1)
            reaper_call = next(call for call in calls if call.startswith("reap_artifacts:"))
            assert reaper_call != f"reap_artifacts:{lifespan_thread}"
            assert calls.index("load_env") < calls.index("telemetry")
            assert calls.index("artifact_config") < calls.index("pipeline_index")
            assert "ready" in calls
        finally:
            allow_reaper_finish.set()
            leave.set()
            await task

    asyncio.run(exercise_lifespan())
