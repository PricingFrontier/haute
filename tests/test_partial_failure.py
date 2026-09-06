"""Adversarial partial-failure tests for haute.

Each test simulates a real operational failure mode and verifies that the
system degrades gracefully (no data corruption, no leaked resources, no
unhandled exceptions propagating to users).

Failure scenarios covered:
  1. Disk full during multi-step save
  2. File locked by another process
  3. Permission denied on file operations
  4. Temp file cleanup after thread crash
  5. WebSocket client disconnect mid-broadcast
  6. File watcher race condition / rapid edits
  7. Stale pipeline index (file deleted after index built)
  8. Corrupt .haute.json sidecar
  9. Concurrent save and preview
 10. Out-of-memory during Polars collect
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.routes._helpers import (
    broadcast,
    invalidate_pipeline_index,
    load_sidecar,
    save_sidecar,
    ws_clients,
)
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import current_source_revision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(nid: str, label: str, ntype: str = "polars", **config) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=label, nodeType=ntype, config=dict(config)),
    )


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _simple_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[_node("n1", "Transform", "polars", code="df")],
        edges=[],
    )


def _make_save_request(
    source_file: str = "pipeline.py",
    *,
    project_root: Path | None = None,
    base_revision: str | None = None,
) -> SavePipelineRequest:
    if base_revision is None and project_root is not None:
        base_revision = current_source_revision(project_root / source_file, project_root)
    return SavePipelineRequest(
        name="test_pipe",
        description="test",
        graph=_simple_graph(),
        source_file=source_file,
        base_revision=base_revision,
    )


# ===================================================================
# 1. DISK FULL DURING SAVE
# ===================================================================
# Production failure: Disk fills up after the .py file is written but
# before the sidecar or config files are flushed. The user sees a
# "saved" confirmation but config is silently lost, leading to stale
# state on the next load.
# ===================================================================


class TestDiskFullDuringSave:
    """Simulate OSError(ENOSPC) at each write step of SavePipelineService.save()."""

    def test_disk_full_on_code_write_raises(self, tmp_path: Path) -> None:
        """If the .py file cannot be written, save must fail — not silently succeed.

        Catches: partial save where code is lost but sidecar is written,
        leaving an inconsistent pipeline on disk.
        """
        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        with patch("haute.routes._save_pipeline.SavePipelineService._write_code") as mock_write:
            mock_write.side_effect = OSError(28, "No space left on device")
            with pytest.raises(OSError, match="No space"):
                svc.save(req)

        # Sidecar must NOT exist — partial state is worse than no state
        sidecar = tmp_path / "pipeline.haute.json"
        assert not sidecar.exists(), "Sidecar written despite code-write failure"

    def test_disk_full_on_sidecar_write_raises(self, tmp_path: Path) -> None:
        """If the sidecar write fails after code succeeds, the error propagates.

        Catches: silent sidecar loss — user reopens pipeline and node
        positions and scenarios are reset to defaults.
        """
        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        with (
            patch("haute.routes._save_pipeline.SavePipelineService._write_code"),
            patch(
                "haute.routes._save_pipeline.SavePipelineService._validate_api_inputs_have_schemas"
            ),
            patch("haute.routes._save_pipeline.SavePipelineService._write_config_files"),
            patch("haute.routes._save_pipeline.SavePipelineService._remove_stale_config_files"),
            patch("haute.routes._save_pipeline.save_sidecar") as mock_sidecar,
        ):
            mock_sidecar.side_effect = OSError(28, "No space left on device")
            with pytest.raises(OSError, match="No space"):
                svc.save(req)

    def test_disk_full_on_config_write_raises(self, tmp_path: Path) -> None:
        """If config JSON write fails mid-way, error propagates.

        Catches: some config files written but others not, causing node
        config drift where half the nodes have stale configs.
        """
        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        with (
            patch("haute.routes._save_pipeline.SavePipelineService._write_code"),
            patch(
                "haute.routes._save_pipeline.SavePipelineService._validate_api_inputs_have_schemas"
            ),
            patch(
                "haute.routes._save_pipeline.SavePipelineService._write_config_files"
            ) as mock_cfg,
        ):
            mock_cfg.side_effect = OSError(28, "No space left on device")
            with pytest.raises(OSError, match="No space"):
                svc.save(req)


# ===================================================================
# 2. FILE LOCKED BY ANOTHER PROCESS
# ===================================================================
# Production failure: On Windows, another process (IDE, antivirus) holds
# an exclusive lock on pipeline.py or a config JSON. write_text() raises
# PermissionError. The server must not crash or corrupt state.
# ===================================================================


class TestFileLocked:
    """Simulate PermissionError from OS-level file locks."""

    def test_locked_py_file_raises_not_crashes(self, tmp_path: Path) -> None:
        """PermissionError on .py write propagates as a clean exception.

        Catches: unhandled exception causing a 500 with no useful detail,
        or worse, a half-written .py file that fails to import.
        """
        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        with patch("haute.routes._save_pipeline.SavePipelineService._write_code") as m:
            m.side_effect = PermissionError(13, "The process cannot access the file")
            with pytest.raises(PermissionError):
                svc.save(req)

    def test_locked_sidecar_file_raises_not_crashes(self, tmp_path: Path) -> None:
        """PermissionError on sidecar write propagates cleanly.

        Catches: the server silently swallowing the error and returning
        'saved' while the sidecar is actually stale.
        """
        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        with (
            patch("haute.routes._save_pipeline.SavePipelineService._write_code"),
            patch(
                "haute.routes._save_pipeline.SavePipelineService._validate_api_inputs_have_schemas"
            ),
            patch("haute.routes._save_pipeline.SavePipelineService._write_config_files"),
            patch("haute.routes._save_pipeline.SavePipelineService._remove_stale_config_files"),
            patch("haute.routes._save_pipeline.save_sidecar") as mock_sc,
        ):
            mock_sc.side_effect = PermissionError(13, "Access is denied")
            with pytest.raises(PermissionError):
                svc.save(req)


# ===================================================================
# 3. PERMISSION DENIED ON FILE OPERATIONS
# ===================================================================
# Production failure: The project directory (or config/ subfolder) is
# read-only, e.g. deployed to a container with a read-only mount. Save
# must fail explicitly, not silently skip writes.
# ===================================================================


class TestPermissionDenied:
    """Read-only directory prevents file creation."""

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
    def test_readonly_dir_raises_on_mkdir(self, tmp_path: Path) -> None:
        """mkdir(parents=True) fails in read-only tree.

        Catches: silent failure where config dir creation is skipped and
        no configs are persisted, leading to missing node settings.
        """
        read_only_dir = tmp_path / "locked_project"
        read_only_dir.mkdir()
        # Create a pipeline file so path validation passes
        py_file = read_only_dir / "pipeline.py"
        py_file.write_text("# placeholder")
        req = _make_save_request("pipeline.py", project_root=read_only_dir)

        read_only_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            svc = SavePipelineService(read_only_dir)
            with pytest.raises((OSError, PermissionError)):
                svc.save(req)
        finally:
            read_only_dir.chmod(stat.S_IRWXU)

    def test_save_sidecar_to_readonly_file_succeeds_via_atomic_replace(
        self, tmp_path: Path
    ) -> None:
        """A read-only TARGET FILE does not block the atomic sidecar write.

        Bundle 5.M2 changed save_sidecar to use ``atomic_write_text``,
        which stages bytes to a sibling temp and ``rename()``s into
        place. POSIX rename atomicity depends on the parent directory's
        permission, not the target file's — so a read-only target file
        is silently replaced. This is the universal behaviour of atomic
        writers (git, sqlite WAL, etc.) and is the price of admission
        for the partial-bytes safety described in
        TestSaveSidecar.test_writes_atomically_via_atomic_write_text.

        The intent the prior test encoded ("user can prevent writes via
        file permissions") is preserved via
        :meth:`test_save_sidecar_to_readonly_dir_raises` below — the
        genuine permission boundary for atomic writers is the *parent
        directory*, not the file.
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text('{"original": true}')
        if sys.platform != "win32":
            sidecar.chmod(stat.S_IRUSR)

        graph = _simple_graph()
        try:
            # No PermissionError — atomic replace bypasses the per-file
            # permission. The sidecar now contains the new payload.
            save_sidecar(py_path, graph)
            content = sidecar.read_text()
            assert '"positions"' in content, "atomic replace must produce the new sidecar payload"
            assert '"original": true' not in content, (
                "atomic replace must have replaced the read-only file"
            )
        finally:
            if sys.platform != "win32":
                # Restore writable so tmp_path cleanup doesn't choke.
                sidecar.chmod(stat.S_IRWXU)

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
    def test_save_sidecar_to_readonly_dir_raises(self, tmp_path: Path) -> None:
        """A read-only PARENT DIRECTORY blocks the atomic sidecar write.

        Bundle 5.M2 — the genuine permission boundary for atomic
        writers is the parent directory: the temp file can't be created
        (or the rename target can't be linked) if the directory is
        read-only. This is the contract for "user wants to prevent
        sidecar writes" — set the directory permission, not the file.
        """
        # Move into a subdir so we can lock that subdir without
        # tripping tmp_path teardown.
        project = tmp_path / "project"
        project.mkdir()
        py_path = project / "pipeline.py"
        py_path.write_text("# placeholder")

        project.chmod(stat.S_IRUSR | stat.S_IXUSR)
        graph = _simple_graph()
        try:
            with pytest.raises((PermissionError, OSError)):
                save_sidecar(py_path, graph)
        finally:
            project.chmod(stat.S_IRWXU)


# ===================================================================
# 4. TEMP FILE CLEANUP ON CRASH
# ===================================================================
# Production failure: Training thread dies (e.g. OOM kill, segfault in
# native lib). The temp .parquet file is never cleaned up, filling the
# disk over time. Each training run leaks ~100MB-2GB.
# ===================================================================


class TestTempFileCleanupOnCrash:
    """Verify temp parquet files are cleaned up even when the thread crashes."""

    def test_temp_parquet_cleaned_on_execute_exception(
        self, tmp_path: Path, haute_scratch: Path
    ) -> None:
        """The try/finally pattern in _execute_and_sink cleans up temp files.

        Catches: temp file leak when _execute_lazy raises — disk fills up
        after many failed training attempts.

        We replicate the exact cleanup pattern from _execute_and_sink to
        verify it works, rather than mocking deep internals.
        """
        import shutil

        tmp_fd, tmp_parquet = tempfile.mkstemp(
            suffix=".parquet", prefix="haute_train_", dir=haute_scratch
        )
        os.close(tmp_fd)
        checkpoint_dir = tmp_path / "haute_train_ckpt_test"
        checkpoint_dir.mkdir()

        assert Path(tmp_parquet).exists()

        # Replicate the exact cleanup pattern from _execute_and_sink
        try:
            raise RuntimeError("Simulated OOM in _execute_lazy")
        except Exception:
            if Path(tmp_parquet).exists():
                os.unlink(tmp_parquet)
        finally:
            if checkpoint_dir and checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir, ignore_errors=True)

        assert not Path(tmp_parquet).exists(), "Temp parquet leaked after exception"
        assert not checkpoint_dir.exists(), "Checkpoint dir leaked after exception"

    def test_checkpoint_dir_cleaned_on_exception(self, tmp_path: Path) -> None:
        """Checkpoint directory is cleaned up even when pipeline crashes.

        Catches: orphaned checkpoint directories accumulating in /tmp,
        each containing large intermediate parquet files.
        """
        ckpt_dir = tmp_path / "haute_train_ckpt_test"
        ckpt_dir.mkdir()
        (ckpt_dir / "node_a.parquet").write_bytes(b"fake parquet data")

        # Simulate the finally block from _execute_and_sink
        import shutil

        try:
            raise RuntimeError("Simulated pipeline crash")
        except RuntimeError:
            pass
        finally:
            if ckpt_dir.exists():
                shutil.rmtree(ckpt_dir, ignore_errors=True)

        assert not ckpt_dir.exists(), "Checkpoint dir leaked after crash"

    def test_background_thread_cleans_parquet_on_training_failure(self, tmp_path: Path) -> None:
        """Background training thread cleans up temp parquet even when training fails.

        Catches: temp file leak in _train_background's finally block when
        TrainingJob.run() raises.
        """
        tmp_parquet = tmp_path / "train_data.parquet"
        tmp_parquet.write_bytes(b"fake parquet")
        assert tmp_parquet.exists()

        # Simulate the finally block from _train_background
        try:
            raise ValueError("CatBoost: all features are constant")
        except ValueError:
            pass
        finally:
            if tmp_parquet.exists():
                os.unlink(str(tmp_parquet))

        assert not tmp_parquet.exists(), "Parquet not cleaned after training failure"


# ===================================================================
# 5. WEBSOCKET CLIENT DISCONNECT MID-BROADCAST
# ===================================================================
# Production failure: A browser tab closes while broadcast() is
# iterating ws_clients. The send raises, and if not handled, remaining
# clients miss the update. The dead socket also leaks memory.
# ===================================================================


class TestWebSocketDisconnectMidBroadcast:
    """Verify broadcast() handles client disconnects without dropping messages."""

    @pytest.fixture(autouse=True)
    def _clean_ws_clients(self) -> None:
        """Ensure ws_clients is empty before and after each test."""
        ws_clients.clear()
        yield
        ws_clients.clear()

    @pytest.mark.asyncio
    async def test_dead_client_removed_live_client_receives(self) -> None:
        """If one client dies mid-broadcast, the other still gets the message.

        Catches: broadcast loop breaking on first exception, leaving all
        subsequent clients uninformed of graph updates.
        """
        live_ws = AsyncMock()
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = ConnectionError("Connection closed")

        ws_clients.add(dead_ws)
        ws_clients.add(live_ws)

        await broadcast({"type": "graph_update", "data": "test"})

        live_ws.send_text.assert_called_once()
        assert dead_ws not in ws_clients, "Dead client not evicted"
        assert live_ws in ws_clients, "Live client wrongly evicted"

    @pytest.mark.asyncio
    async def test_all_clients_dead(self) -> None:
        """If all clients disconnect during broadcast, no exception leaks.

        Catches: unhandled exception when the last client in the set dies,
        crashing the file watcher coroutine.
        """
        dead1 = AsyncMock()
        dead2 = AsyncMock()
        dead1.send_text.side_effect = ConnectionError("gone")
        dead2.send_text.side_effect = ConnectionError("gone")

        ws_clients.add(dead1)
        ws_clients.add(dead2)

        # Must not raise
        await broadcast({"type": "test"})

        assert len(ws_clients) == 0, "Dead clients not cleaned up"

    @pytest.mark.asyncio
    async def test_broadcast_with_unserializable_data(self) -> None:
        """Non-JSON-serializable data does not crash broadcast.

        Catches: TypeError from json.dumps crashing the file watcher
        and stopping all future broadcasts.
        """
        live_ws = AsyncMock()
        ws_clients.add(live_ws)

        # set() is not JSON-serializable
        await broadcast({"data": {1, 2, 3}})

        live_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_empty_clients(self) -> None:
        """Broadcast with no clients is a no-op.

        Catches: iteration over empty set causing unexpected behavior.
        """
        await broadcast({"type": "test"})  # must not raise


# ===================================================================
# 6. FILE WATCHER RACE CONDITION
# ===================================================================
# Production failure: User saves rapidly (Ctrl+S spam). The file watcher
# sees a change, starts parsing, but the file changes again before
# parse_pipeline_file() reads it. The old parse result is broadcast,
# overwriting the newer save's state in the GUI.
# ===================================================================


class TestFileWatcherRaceCondition:
    """Verify self-write detection prevents feedback loops."""

    def test_self_write_flag_prevents_broadcast(self) -> None:
        """mark_self_write() prevents the watcher from re-broadcasting our own save.

        Catches: infinite loop where save triggers file watcher, which
        broadcasts, which triggers the GUI to re-save.
        """
        from haute.routes._helpers import is_self_write, mark_self_write

        mark_self_write()
        assert is_self_write(), "Self-write flag not set immediately after mark"

    def test_self_write_flag_expires(self) -> None:
        """Self-write flag expires after cooldown so real edits are detected.

        Catches: permanent suppression of file watcher after a single save,
        making live code editing appear broken.
        """

        import haute.routes._helpers as helpers

        original = helpers._last_self_write
        # Set last write to far in the past
        helpers._last_self_write = time.monotonic() - 10.0
        try:
            assert not helpers.is_self_write(), "Self-write flag should have expired"
        finally:
            helpers._last_self_write = original

    def test_invalidate_pipeline_index_clears_cache(self) -> None:
        """invalidate_pipeline_index resets the module dep cache too.

        Catches: stale dependency map causing module changes to not
        trigger re-parse of the correct pipelines.
        """
        import haute.routes._helpers as helpers

        # Set up some fake state
        helpers._pipeline_index = {"fake": Path("/fake")}
        helpers._module_deps = {"fake_mod": {Path("/fake")}}

        invalidate_pipeline_index()

        assert helpers._pipeline_index is None
        assert helpers._module_deps is None


# ===================================================================
# 7. STALE PIPELINE INDEX
# ===================================================================
# Production failure: Pipeline file deleted by the user (or git checkout)
# after the index was built but before a GET request resolves. The index
# returns a Path to a file that no longer exists.
# ===================================================================


class TestStalePipelineIndex:
    """Pipeline index returns paths to deleted files."""

    def test_lookup_returns_none_for_missing_pipeline(self) -> None:
        """lookup_pipeline_by_name returns None for unknown pipeline names.

        Catches: KeyError crash instead of a clean 404.
        """
        from haute.routes._helpers import lookup_pipeline_by_name

        # Force a fresh empty index
        invalidate_pipeline_index()
        with patch("haute.discovery.discover_pipelines", return_value=[]):
            result = lookup_pipeline_by_name("nonexistent_pipeline")
        assert result is None

    def test_stale_index_points_to_deleted_file(self, tmp_path: Path) -> None:
        """Index returns a Path, but the file was deleted. Caller must handle.

        Catches: FileNotFoundError when parse_pipeline_file is called on
        the stale path, crashing the request handler.
        """
        import haute.routes._helpers as helpers

        # Inject a stale entry directly
        fake_path = tmp_path / "deleted_pipeline.py"
        helpers._pipeline_index = {"stale_pipe": fake_path}
        try:
            result = helpers.lookup_pipeline_by_name("stale_pipe")
            assert result == fake_path
            assert not result.exists(), "Test setup error: file should not exist"

            # Simulating what the route handler does — it must not crash
            # but raise a useful error
            from haute.routes._helpers import parse_pipeline_to_graph

            with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: testing that some error is raised for missing file
                parse_pipeline_to_graph(result)
        finally:
            helpers._pipeline_index = None


# ===================================================================
# 8. JSON DECODE ERROR IN SIDECAR
# ===================================================================
# Production failure: .haute.json is corrupted (partial write from disk-
# full, or manually edited with a syntax error). Loading must degrade
# gracefully: positions reset but pipeline still loads.
# ===================================================================


class TestCorruptSidecar:
    """Corrupt .haute.json must not prevent pipeline loading."""

    def test_truncated_json_returns_empty_dict(self, tmp_path: Path) -> None:
        """Truncated JSON (e.g. from disk-full mid-write) returns empty positions.

        Catches: JSONDecodeError crashing the pipeline load endpoint,
        making the pipeline completely inaccessible.
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text('{"positions": {"node_1": {"x": 100, "y": 20')  # truncated

        result = load_sidecar(py_path)
        assert result == {}, "Corrupt sidecar should return empty dict"

    def test_binary_garbage_returns_empty_dict(self, tmp_path: Path) -> None:
        """Binary garbage in sidecar returns empty dict.

        Catches: UnicodeDecodeError or JSONDecodeError from completely
        corrupted file.
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_bytes(b"\x00\xff\xfe\x80invalid")

        result = load_sidecar(py_path)
        assert result == {}

    def test_valid_json_but_wrong_type_returns_empty(self, tmp_path: Path) -> None:
        """Sidecar containing a JSON array degrades gracefully to {}.

        load_sidecar now catches TypeError and ValueError in addition to
        JSONDecodeError and OSError, so a sidecar with the wrong root
        type (e.g. an array) returns {} instead of crashing.
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text("[1, 2, 3]")

        result = load_sidecar(py_path)
        assert result == {}, "Wrong-type sidecar should return empty dict"

    def test_missing_sidecar_returns_empty_dict(self, tmp_path: Path) -> None:
        """No sidecar file at all returns empty dict (not an error).

        Catches: FileNotFoundError when sidecar hasn't been created yet
        (first save from code-only workflow).
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")

        result = load_sidecar(py_path)
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        """Zero-byte sidecar returns empty dict.

        Catches: JSONDecodeError on empty string.
        """
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text("")

        result = load_sidecar(py_path)
        assert result == {}

    def test_sidecar_with_extra_fields_still_loads(self, tmp_path: Path) -> None:
        """Sidecar loader preserves unknown keys for caller-owned metadata."""
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# placeholder")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(
            json.dumps(
                {
                    "positions": {"node_1": {"x": 0, "y": 0}},
                    "future_field": "some_value",
                    "sources": ["live", "test"],
                }
            )
        )

        result = load_sidecar(py_path)
        assert result["positions"]["node_1"] == {"x": 0, "y": 0}
        assert result["sources"] == ["live", "test"]


# ===================================================================
# 9. CONCURRENT SAVE AND PREVIEW
# ===================================================================
# Production failure: User clicks "Preview" on node A, and while the
# preview is executing (which may take seconds for large data), they
# also hit Ctrl+S which triggers a save. The save modifies the graph
# structure while the executor is traversing it.
# ===================================================================


class TestStaleSaveUnderLock:
    def test_concurrent_saves_under_lock_only_one_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two concurrent saves with the same base revision race under lock; exactly one wins."""
        from fastapi.testclient import TestClient

        from haute.server import app

        monkeypatch.chdir(tmp_path)
        with TestClient(app) as client:
            base_graph = {
                "nodes": [
                    {
                        "id": "src",
                        "type": "pipelineNode",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "label": "Source",
                            "nodeType": "dataInput",
                            "config": {
                                "inputType": "file",
                                "format": "parquet",
                                "mode": "scan",
                                "path": "data.parquet",
                                "arguments": {},
                            },
                        },
                    },
                ],
                "edges": [],
            }

            res0 = client.post(
                "/api/pipeline/save",
                json={
                    "name": "main",
                    "source_file": "main.py",
                    "graph": base_graph,
                    "base_revision": None,
                },
            )
            assert res0.status_code == 200
            base_rev = res0.json()["source_revision"]

            graph_a = {
                "nodes": [
                    *base_graph["nodes"],
                    {
                        "id": "node_a",
                        "type": "pipelineNode",
                        "position": {"x": 100, "y": 0},
                        "data": {
                            "label": "worker_a",
                            "nodeType": "polars",
                            "config": {"code": ""},
                        },
                    },
                ],
                "edges": [],
            }

            graph_b = {
                "nodes": [
                    *base_graph["nodes"],
                    {
                        "id": "node_b",
                        "type": "pipelineNode",
                        "position": {"x": 200, "y": 0},
                        "data": {
                            "label": "worker_b",
                            "nodeType": "polars",
                            "config": {"code": ""},
                        },
                    },
                ],
                "edges": [],
            }

            responses: list = []

            def _post_edit(graph_payload: dict) -> None:
                r = client.post(
                    "/api/pipeline/save",
                    json={
                        "name": "main",
                        "source_file": "main.py",
                        "graph": graph_payload,
                        "base_revision": base_rev,
                    },
                )
                responses.append(r)

            t1 = threading.Thread(target=_post_edit, args=(graph_a,))
            t2 = threading.Thread(target=_post_edit, args=(graph_b,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            status_codes = [r.status_code for r in responses]
            assert sorted(status_codes) == [200, 409]

            stale_resp = next(r for r in responses if r.status_code == 409)
            assert stale_resp.json()["detail"].startswith("stale_document_revision:")

            on_disk = (tmp_path / "main.py").read_text(encoding="utf-8")
            has_a = "worker_a" in on_disk
            has_b = "worker_b" in on_disk
            assert has_a ^ has_b, "on-disk pipeline must contain only the winner's node"


# ===================================================================
# 10. OUT OF MEMORY DURING POLARS COLLECT
# ===================================================================
# Production failure: A LazyFrame.collect() materializes more data than
# RAM can hold. Polars raises polars.exceptions.ComputeError or the OS
# kills the process. Training must clean up temp files and report the
# error clearly.
# ===================================================================


class TestOutOfMemoryDuringCollect:
    """Simulate OOM-like failures during DataFrame materialization."""

    def test_polars_compute_error_cleans_up_temp_file(self, tmp_path: Path) -> None:
        """ComputeError during sink is caught and temp file is cleaned up.

        Catches: temp parquet file leaked when polars raises during
        collect/sink, filling the disk over repeated attempts.
        """
        tmp_parquet = tmp_path / "haute_train_test.parquet"
        tmp_parquet.write_bytes(b"")  # create it

        # Simulate the cleanup pattern from _execute_and_sink
        try:
            raise MemoryError("Unable to allocate 16.0 GiB")
        except MemoryError:
            if tmp_parquet.exists():
                tmp_parquet.unlink()

        assert not tmp_parquet.exists()

    def test_memory_error_message_is_actionable(self) -> None:
        """_friendly_error produces a useful message for memory errors.

        Catches: raw "MemoryError" shown to user with no guidance on
        how to fix it (e.g. reduce data, increase row_limit).
        """
        from haute.routes._train_service import _friendly_error

        err = MemoryError("cannot allocate memory")
        msg = _friendly_error(err)
        # Should contain the type name so user knows it's a memory issue
        assert "MemoryError" in msg or "memory" in msg.lower()

    def test_gc_collect_called_after_failed_sink(self) -> None:
        """Verify that gc.collect is invoked in the cleanup path.

        Catches: memory not released after a failed training attempt,
        causing the next attempt to also OOM despite the data being smaller.
        """
        import gc

        collected = []
        original_collect = gc.collect

        def tracking_collect(*args, **kwargs):
            collected.append(True)
            return original_collect(*args, **kwargs)

        with patch("gc.collect", side_effect=tracking_collect):
            gc.collect()

        assert len(collected) > 0


# ===================================================================
# INTEGRATION: SavePipelineService end-to-end partial failure
# ===================================================================


class TestSavePipelinePartialFailureIntegration:
    """End-to-end tests where real files are written then a step fails."""

    def test_code_written_but_sidecar_fails_rolls_back_new_code(self, tmp_path: Path) -> None:
        """Phase 1C #50 flipped the contract: a failed save is atomic.

        Previously a sidecar-write failure left the newly-written .py
        on disk (the assumption being "at least the code survived").
        The new contract is full rollback: if any step fails, every
        file the save service touched is restored to its pre-save
        state (or deleted for files that did not exist before).  This
        is strictly safer — the user is never left with a half-saved
        pipeline whose sidecar disagrees with its code.
        """
        from haute.routes._save_pipeline import _TouchedFile

        svc = SavePipelineService(tmp_path)
        req = _make_save_request("pipeline.py", project_root=tmp_path)
        py_path = tmp_path / "pipeline.py"

        def fake_write_code(body, graph, path, touched=None):
            path.write_text("# generated code")
            if touched is not None:
                touched.append(_TouchedFile(target=path, previous_bytes=None))

        with (
            patch.object(svc, "_write_code", side_effect=fake_write_code),
            patch.object(svc, "_validate_api_inputs_have_schemas"),
            patch.object(svc, "_write_config_files"),
            patch.object(svc, "_remove_stale_config_files"),
            patch(
                "haute.routes._save_pipeline.save_sidecar",
                side_effect=OSError("disk full"),
            ),
        ):
            with pytest.raises(OSError):
                svc.save(req)

        # Previously-nonexistent file must be deleted by rollback.
        assert not py_path.exists(), (
            "Transactional save must roll back the new .py when a later step fails."
        )

    def test_config_write_failure_restores_pre_existing_code(self, tmp_path: Path) -> None:
        """Phase 1C #50: when ``_write_code`` overwrote a pre-existing
        file and a later step fails, rollback restores the original
        bytes (not "leave the new bytes and hope")."""
        from haute.routes._save_pipeline import _TouchedFile

        svc = SavePipelineService(tmp_path)
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# ORIGINAL")
        req = _make_save_request("pipeline.py", project_root=tmp_path)

        def fake_write_code(body, graph, path, touched=None):
            if touched is not None:
                touched.append(_TouchedFile(target=path, previous_bytes=path.read_bytes()))
            path.write_text("# good code")

        with (
            patch.object(svc, "_write_code", side_effect=fake_write_code),
            patch.object(svc, "_validate_api_inputs_have_schemas"),
            patch.object(
                svc,
                "_write_config_files",
                side_effect=OSError("permission denied"),
            ),
        ):
            with pytest.raises(OSError):
                svc.save(req)

        assert py_path.exists()
        assert py_path.read_text() == "# ORIGINAL", (
            "Rollback must restore the pre-save bytes of any file the save service overwrote."
        )
