"""Comprehensive tests for haute.routes._helpers.

Covers:
  - validate_safe_path  — valid paths, traversal attempts, absolute paths
  - raise_node_not_found / raise_node_type_error / raise_pipeline_not_found / raise_validation_error
  - mark_self_write / is_self_write — timing-based self-write detection
  - load_sidecar / load_sidecar_positions — valid JSON, corrupt JSON, missing file
  - save_sidecar — round-trip test, scenario state
  - broadcast — WebSocket message fan-out
  - parse_pipeline_to_graph — sidecar merging
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes._helpers import (
    _SELF_WRITE_COOLDOWN,
    _WATCHER_PAUSE_SETTLE_SECONDS,
    broadcast,
    invalidate_pipeline_index,
    is_self_write,
    load_sidecar,
    load_sidecar_positions,
    mark_self_write,
    pause_watcher,
    raise_node_not_found,
    raise_node_type_error,
    raise_pipeline_not_found,
    raise_validation_error,
    save_sidecar,
    validate_safe_path,
    watcher_is_paused,
    ws_clients,
)

# ===========================================================================
# validate_safe_path
# ===========================================================================


class TestValidateSafePath:
    def test_valid_relative_path(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        result = validate_safe_path(tmp_path, "subdir")
        assert result == sub

    def test_valid_file_path(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = validate_safe_path(tmp_path, "file.txt")
        assert result == f

    def test_traversal_attempt_raises_403(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "../../../etc/passwd")
        assert exc_info.value.status_code == 403
        assert "outside the project root" in exc_info.value.detail

    def test_double_traversal(self, tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "foo/../../..")
        assert exc_info.value.status_code == 403

    def test_absolute_path_within_base(self, tmp_path):
        """Absolute path that happens to be inside base should work."""
        f = tmp_path / "inner.txt"
        f.write_text("ok")
        result = validate_safe_path(tmp_path, str(f))
        assert result == f

    def test_nested_path(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = validate_safe_path(tmp_path, "a/b/c")
        assert result == nested

    def test_path_object_input(self, tmp_path):
        result = validate_safe_path(tmp_path, Path("subdir"))
        # The path may not exist but should be resolved
        assert str(result).startswith(str(tmp_path))

    def test_symlink_escape(self, tmp_path):
        """Symlink pointing outside base should be caught after resolve()."""
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "sneaky_link"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Cannot create symlinks in this environment")
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "sneaky_link")
        assert exc_info.value.status_code == 403


# ===========================================================================
# HTTP error helpers
# ===========================================================================


class TestRaiseNodeNotFound:
    def test_raises_404(self):
        with pytest.raises(HTTPException) as exc_info:
            raise_node_not_found("node_123")
        assert exc_info.value.status_code == 404
        assert "node_123" in exc_info.value.detail


class TestRaiseNodeTypeError:
    def test_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            raise_node_type_error("n1", "dataInput", "polars")
        assert exc_info.value.status_code == 400
        assert "dataInput" in exc_info.value.detail
        assert "polars" in exc_info.value.detail


class TestRaisePipelineNotFound:
    def test_raises_404(self):
        with pytest.raises(HTTPException) as exc_info:
            raise_pipeline_not_found("my_pipeline")
        assert exc_info.value.status_code == 404
        assert "my_pipeline" in exc_info.value.detail


class TestRaiseValidationError:
    def test_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            raise_validation_error("bad input")
        assert exc_info.value.status_code == 400
        assert "bad input" in exc_info.value.detail


# ===========================================================================
# mark_self_write / is_self_write
# ===========================================================================


class TestSelfWriteTracking:
    def test_mark_then_check(self):
        mark_self_write()
        assert is_self_write() is True

    def test_expires_after_cooldown(self):
        """After the cooldown window, is_self_write returns False."""
        # We can't actually wait 2+ seconds in a unit test. Instead, we patch
        # time.monotonic to simulate time passing.

        mark_self_write()
        original = time.monotonic

        # Simulate time having passed beyond cooldown
        with patch.object(time, "monotonic", return_value=original() + _SELF_WRITE_COOLDOWN + 1):
            assert is_self_write() is False

    def test_path_mark_survives_global_cooldown_until_watcher_consumes_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.routes._helpers as helpers

        helpers._self_write_paths.clear()
        fake_time = [100.0]
        monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])
        path = tmp_path / "pipeline.py"

        try:
            mark_self_write(path)
            fake_time[0] += _SELF_WRITE_COOLDOWN + 5.0

            assert is_self_write() is False
            assert is_self_write(path) is True
        finally:
            helpers._self_write_paths.clear()

    def test_path_mark_can_be_consumed_once(self, tmp_path: Path):
        import haute.routes._helpers as helpers

        helpers._self_write_paths.clear()
        path = tmp_path / "pipeline.py"

        try:
            mark_self_write(path)
            assert is_self_write(path, consume=True) is True
            assert is_self_write(path) is False
        finally:
            helpers._self_write_paths.clear()

    def test_marking_new_path_prunes_stale_path_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A stale self-write marker must not keep suppressing watcher events."""
        import haute.routes._helpers as helpers

        helpers._self_write_paths.clear()
        fake_time = [10.0]
        monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])
        stale_path = tmp_path / "stale.py"
        fresh_path = tmp_path / "fresh.py"

        try:
            mark_self_write(stale_path)
            fake_time[0] += helpers._SELF_WRITE_RETENTION + 1.0
            mark_self_write(fresh_path)

            assert is_self_write(stale_path) is False
            assert is_self_write(fresh_path) is True
        finally:
            helpers._self_write_paths.clear()


# ===========================================================================
# pause_watcher / watcher_is_paused (S30 — pause during haute-initiated git ops)
# ===========================================================================


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch):
    """Deterministic monotonic clock + clean watcher-pause globals per test."""
    import haute.routes._helpers as helpers

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    helpers._watcher_pause_depth = 0
    helpers._watcher_pause_deadline = 0.0
    helpers._watcher_pause_released_at = 0.0
    helpers._watcher_pause_watchdog_fired = False
    try:
        yield clock
    finally:
        helpers._watcher_pause_depth = 0
        helpers._watcher_pause_deadline = 0.0
        helpers._watcher_pause_released_at = 0.0
        helpers._watcher_pause_watchdog_fired = False


class TestWatcherPause:
    def test_not_paused_by_default(self, fake_clock):
        assert watcher_is_paused() is False

    def test_paused_inside_context(self, fake_clock):
        with pause_watcher():
            assert watcher_is_paused() is True

    def test_settle_window_then_resume_after_clean_exit(self, fake_clock):
        with pause_watcher():
            pass
        # Immediately after release the settle window still suppresses the
        # checkout's trailing debounced events.
        assert watcher_is_paused() is True
        fake_clock[0] += _WATCHER_PAUSE_SETTLE_SECONDS + 0.1
        assert watcher_is_paused() is False

    def test_resume_guaranteed_when_body_raises(self, fake_clock):
        """S30: a git op that fails mid-pause must still resume the watcher."""
        import haute.routes._helpers as helpers

        with pytest.raises(ValueError):
            with pause_watcher():
                raise ValueError("git op blew up")

        # try/finally unwound the depth even though the body raised.
        assert helpers._watcher_pause_depth == 0
        fake_clock[0] += _WATCHER_PAUSE_SETTLE_SECONDS + 0.1
        assert watcher_is_paused() is False

    def test_watchdog_force_resumes_on_overrun(self, fake_clock):
        """A git op that holds the pause past its deadline is force-resumed."""
        with pause_watcher(max_seconds=5.0):
            assert watcher_is_paused() is True
            fake_clock[0] += 5.1  # overrun the deadline while still inside the op
            assert watcher_is_paused() is False  # watchdog tripped

    def test_reentrant_nested_pause(self, fake_clock):
        import haute.routes._helpers as helpers

        with pause_watcher():
            with pause_watcher():
                assert helpers._watcher_pause_depth == 2
                assert watcher_is_paused() is True
            # Inner exit leaves the outer pause intact.
            assert helpers._watcher_pause_depth == 1
            assert watcher_is_paused() is True
        assert helpers._watcher_pause_depth == 0

    def test_nested_pause_extends_but_never_shrinks_deadline(self, fake_clock):
        import haute.routes._helpers as helpers

        with pause_watcher(max_seconds=100.0):
            outer_deadline = helpers._watcher_pause_deadline
            with pause_watcher(max_seconds=1.0):
                # A shorter nested pause must not pull the deadline in.
                assert helpers._watcher_pause_deadline == outer_deadline


# ===========================================================================
# load_sidecar / load_sidecar_positions
# ===========================================================================


class TestLoadSidecar:
    def test_valid_json(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = tmp_path / "pipeline.haute.json"
        data = {"positions": {"a": {"x": 10, "y": 20}}, "sources": ["live", "test"]}
        sidecar.write_text(json.dumps(data))

        result = load_sidecar(py_path)
        assert result["positions"]["a"] == {"x": 10, "y": 20}
        assert result["sources"] == ["live", "test"]

    def test_missing_sidecar(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        result = load_sidecar(py_path)
        assert result == {}

    def test_corrupt_json(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = tmp_path / "pipeline.haute.json"
        sidecar.write_text("{bad json content")

        result = load_sidecar(py_path)
        assert result == {}

    def test_empty_file(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = tmp_path / "pipeline.haute.json"
        sidecar.write_text("")

        result = load_sidecar(py_path)
        assert result == {}


class TestLoadSidecarPositions:
    def test_returns_positions(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = tmp_path / "pipeline.haute.json"
        sidecar.write_text(json.dumps({"positions": {"n1": {"x": 5, "y": 10}}}))

        result = load_sidecar_positions(py_path)
        assert result == {"n1": {"x": 5, "y": 10}}

    def test_no_positions_key(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = tmp_path / "pipeline.haute.json"
        sidecar.write_text(json.dumps({"sources": ["live"]}))

        result = load_sidecar_positions(py_path)
        assert result == {}


# ===========================================================================
# save_sidecar
# ===========================================================================


class TestSaveSidecar:
    def test_basic_save(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    position={"x": 100.0, "y": 200.0},
                    data=NodeData(label="A", nodeType=NodeType.DATA_INPUT),
                ),
            ],
            edges=[],
        )
        save_sidecar(py_path, graph)

        sidecar = tmp_path / "pipeline.haute.json"
        assert sidecar.exists()
        data = json.loads(sidecar.read_text())
        assert "positions" in data
        assert data["positions"]["A"] == {"x": 100.0, "y": 200.0}

    def test_submodel_node_keyed_by_parser_id(self, tmp_path):
        """Submodel placeholder positions must be keyed by ``submodel__<name>``.

        Regression: the parser rebuilds a submodel placeholder with
        ``id = "submodel__<name>"`` but ``data.label = "<name>"``.  The save
        path used to key the sidecar by ``sanitize(label)`` alone, writing
        ``"model_stuff"`` while the load read ``"submodel__model_stuff"`` — a
        guaranteed miss that snapped every submodel node back to (0, 0) on
        reload.  Before the fix this asserts on the wrong key and fails.
        """
        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="submodel__model_stuff",
                    position={"x": 321.0, "y": 654.0},
                    data=NodeData(label="model_stuff", nodeType=NodeType.SUBMODEL),
                ),
            ],
            edges=[],
        )
        save_sidecar(py_path, graph)

        data = json.loads((tmp_path / "pipeline.haute.json").read_text())
        assert data["positions"] == {
            "submodel__model_stuff": {"x": 321.0, "y": 654.0},
        }
        # And the bare-label key must NOT be written (that was the bug).
        assert "model_stuff" not in data["positions"]

    def test_source_state_saved(self, tmp_path):
        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(
            nodes=[],
            sources=["live", "test_batch"],
            active_source="test_batch",
        )
        save_sidecar(py_path, graph)

        data = json.loads((tmp_path / "pipeline.haute.json").read_text())
        assert data["sources"] == ["live", "test_batch"]
        assert data["active_source"] == "test_batch"

    def test_default_source_not_saved(self, tmp_path):
        """Default source state (["live"], "live") is not persisted."""
        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(nodes=[], sources=["live"], active_source="live")
        save_sidecar(py_path, graph)

        data = json.loads((tmp_path / "pipeline.haute.json").read_text())
        assert "sources" not in data
        assert "active_source" not in data

    def test_source_keys_round_trip_verbatim(self, tmp_path):
        """Source keys survive save→load byte-for-byte, case included.

        The sidecar treats source keys as opaque strings in both directions:
        no folding or reformatting, only exact membership checks.
        """
        py_path = tmp_path / "pipeline.py"
        keys = ["live", "my_src", "My_Src", "node_2024"]
        graph = PipelineGraph(nodes=[], sources=list(keys), active_source="My_Src")
        save_sidecar(py_path, graph)

        loaded = load_sidecar(py_path)
        assert loaded["sources"] == keys
        assert loaded["active_source"] == "My_Src"

    def test_writes_atomically_via_atomic_write_text(self, tmp_path, monkeypatch):
        """save_sidecar must write the sidecar via ``atomic_write_text``.

        Background (Bundle 5.M2 — OPUS race report scenario S2): the
        sidecar is read by the file-watcher's reparse path and by
        ``load_sidecar`` on every pipeline GET. If save_sidecar uses
        a non-atomic write (``Path.write_text``), a concurrent reader
        between the truncate and the write completion sees partial or
        empty bytes → ``JSONDecodeError`` → positions snap to default
        on broadcast. This is a P0 data-corruption window in the OPUS
        race-conditions report.

        Pin the contract: save_sidecar invokes
        ``haute._file_ops.atomic_write_text`` (or routes through it),
        not raw ``Path.write_text``. If a future refactor replaces the
        atomic helper with another atomic-write primitive (Writer,
        etc.) update this spy — the load-bearing property is *no
        partial-write window for the .haute.json file*.
        """
        import haute.routes._helpers as helpers

        calls: list[tuple[Path, str]] = []
        original = helpers.atomic_write_text

        def spy(path: Path, data: str, encoding: str = "utf-8") -> None:
            calls.append((path, data))
            original(path, data, encoding)

        monkeypatch.setattr(helpers, "atomic_write_text", spy)

        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    position={"x": 1.0, "y": 2.0},
                    data=NodeData(label="A", nodeType=NodeType.DATA_INPUT),
                ),
            ],
            edges=[],
        )
        save_sidecar(py_path, graph)

        sidecar = tmp_path / "pipeline.haute.json"
        assert sidecar.exists(), "sidecar must still be written"
        assert calls, "atomic_write_text must have been called"
        assert any(p == sidecar for p, _ in calls), (
            "atomic_write_text must have been called for the sidecar path; "
            f"got calls for {[p.name for p, _ in calls]}"
        )

    def test_roundtrip(self, tmp_path):
        """save_sidecar then load_sidecar should produce consistent data."""
        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    position={"x": 1.0, "y": 2.0},
                    data=NodeData(label="alpha", nodeType=NodeType.POLARS),
                ),
                GraphNode(
                    id="b",
                    position={"x": 3.0, "y": 4.0},
                    data=NodeData(label="beta", nodeType=NodeType.OUTPUT),
                ),
            ],
            sources=["live", "test"],
            active_source="test",
        )
        save_sidecar(py_path, graph)
        loaded = load_sidecar(py_path)

        assert loaded["positions"]["alpha"] == {"x": 1.0, "y": 2.0}
        assert loaded["positions"]["beta"] == {"x": 3.0, "y": 4.0}
        assert loaded["sources"] == ["live", "test"]
        assert loaded["active_source"] == "test"

    def test_label_sanitized_as_key(self, tmp_path):
        """Position keys use sanitized label (matching parser node IDs)."""
        py_path = tmp_path / "pipeline.py"
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="n1",
                    position={"x": 10.0, "y": 20.0},
                    data=NodeData(label="My Node", nodeType=NodeType.POLARS),
                ),
            ],
        )
        save_sidecar(py_path, graph)
        from haute._graph_utils import _sanitize_func_name

        data = json.loads((tmp_path / "pipeline.haute.json").read_text())
        expected_key = _sanitize_func_name("My Node")
        assert expected_key in data["positions"]


# ===========================================================================
# broadcast
# ===========================================================================


@pytest.fixture()
def _clean_ws_clients():
    """Clear ws_clients before and after each test to prevent cross-test pollution."""
    ws_clients.clear()
    yield
    ws_clients.clear()


class TestBroadcast:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_sends_to_all_clients(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws_clients.add(ws1)
        ws_clients.add(ws2)
        await broadcast({"type": "test"})
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        payload = json.loads(ws1.send_text.call_args[0][0])
        assert payload["type"] == "test"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_removes_dead_clients(self):
        live_ws = AsyncMock()
        dead_ws = AsyncMock()
        dead_ws.send_text.side_effect = Exception("connection closed")
        ws_clients.add(live_ws)
        ws_clients.add(dead_ws)
        await broadcast({"type": "ping"})
        assert dead_ws not in ws_clients
        assert live_ws in ws_clients

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_non_serializable_payload_skipped(self):
        """Payload that can't be JSON-serialized should not crash."""
        ws = AsyncMock()
        ws_clients.add(ws)
        await broadcast({"bad": object()})
        ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_slow_client_times_out_without_blocking_fast_client(self):
        """One slow socket should not hold every other client hostage."""
        fast_ws = AsyncMock()
        slow_ws = AsyncMock()
        fast_sent = asyncio.Event()

        async def _slow_send(_payload: str) -> None:
            await asyncio.sleep(0.05)

        async def _fast_send(_payload: str) -> None:
            fast_sent.set()

        fast_ws.send_text.side_effect = _fast_send
        slow_ws.send_text.side_effect = _slow_send

        ws_clients.add(slow_ws)
        ws_clients.add(fast_ws)

        with patch("haute.routes._helpers._WS_SEND_TIMEOUT_SECONDS", 0.01):
            broadcast_task = asyncio.create_task(broadcast({"type": "ping"}))
            # Generous wall-clock deadline: the assertion is that the fast
            # client is not serialized behind the slow one, not that it lands
            # within scheduler jitter of the 10ms cutoff - 20ms flaked under
            # a loaded parallel suite.
            await asyncio.wait_for(fast_sent.wait(), timeout=2.0)
            await broadcast_task

        fast_ws.send_text.assert_called_once()
        assert slow_ws not in ws_clients

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_stalled_send_that_suppresses_cancellation_is_closed_and_removed(self):
        """A wedged socket must be closed and evicted, not silently muted."""

        class _CancellationResistantWs:
            def __init__(self) -> None:
                self.cancel_suppressed = asyncio.Event()
                self.release = asyncio.Event()
                self.closed = asyncio.Event()
                self.payloads: list[str] = []

            async def send_text(self, payload: str) -> None:
                self.payloads.append(json.loads(payload)["type"])
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancel_suppressed.set()
                    await self.release.wait()

            async def close(self) -> None:
                self.closed.set()

        ws = _CancellationResistantWs()
        ws_clients.add(ws)

        with patch("haute.routes._helpers._WS_SEND_TIMEOUT_SECONDS", 0.01):
            broadcast_task = asyncio.create_task(broadcast({"type": "ping"}))
            await asyncio.wait_for(ws.cancel_suppressed.wait(), timeout=2.0)

            try:
                await asyncio.wait_for(asyncio.shield(broadcast_task), timeout=0.2)
                completed_before_release = True
            except TimeoutError:
                completed_before_release = False
            finally:
                ws.release.set()
                await asyncio.wait_for(broadcast_task, timeout=2.0)

        assert completed_before_release
        assert ws.closed.is_set()
        assert ws not in ws_clients
        assert ws.payloads == ["ping"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_queued_payloads_are_dropped_when_stalled_socket_is_closed(self):
        """Queued broadcasts behind a wedged socket must not leave it half-alive."""

        class _StalledWs:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.cancel_suppressed = asyncio.Event()
                self.release = asyncio.Event()
                self.closed = asyncio.Event()
                self.payloads: list[str] = []

            async def send_text(self, payload: str) -> None:
                self.payloads.append(json.loads(payload)["type"])
                self.started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancel_suppressed.set()
                    await self.release.wait()

            async def close(self) -> None:
                self.closed.set()

        ws = _StalledWs()
        ws_clients.add(ws)

        with patch("haute.routes._helpers._WS_SEND_TIMEOUT_SECONDS", 0.01):
            first = asyncio.create_task(broadcast({"type": "first"}))
            await asyncio.wait_for(ws.started.wait(), timeout=2.0)

            await asyncio.wait_for(broadcast({"type": "second"}), timeout=2.0)
            await asyncio.wait_for(ws.cancel_suppressed.wait(), timeout=2.0)

            ws.release.set()
            await asyncio.wait_for(first, timeout=2.0)

        assert ws.closed.is_set()
        assert ws not in ws_clients
        assert ws.payloads == ["first"]

        await broadcast({"type": "third"})
        assert ws.payloads == ["first"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_cancelling_broadcast_during_stalled_close_drains_close_task(self):
        """Cancelling broadcast during close must not leak a shielded close task."""

        class _CloseStallingWs:
            def __init__(self) -> None:
                self.send_started = asyncio.Event()
                self.close_started = asyncio.Event()
                self.close_cancelled = asyncio.Event()
                self.close_release = asyncio.Event()

            async def send_text(self, _payload: str) -> None:
                self.send_started.set()
                await asyncio.Future()

            async def close(self) -> None:
                self.close_started.set()
                try:
                    await self.close_release.wait()
                except asyncio.CancelledError:
                    self.close_cancelled.set()
                    raise

        ws = _CloseStallingWs()
        ws_clients.add(ws)

        with patch("haute.routes._helpers._WS_SEND_TIMEOUT_SECONDS", 0.01):
            broadcast_task = asyncio.create_task(broadcast({"type": "ping"}))
            await asyncio.wait_for(ws.close_started.wait(), timeout=2.0)

            broadcast_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await broadcast_task

            try:
                await asyncio.wait_for(ws.close_cancelled.wait(), timeout=0.2)
            finally:
                ws.close_release.set()
                await asyncio.sleep(0)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_overlapping_broadcasts_serialize_per_client(self):
        """A slow client should see serialized sends in original broadcast order."""

        class _SerializingWs:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.payloads: list[str] = []
                self.active = 0
                self.max_active = 0

            async def send_text(self, payload: str) -> None:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.payloads.append(json.loads(payload)["type"])
                if len(self.payloads) == 1:
                    self.started.set()
                    await self.release.wait()
                self.active -= 1

        ws = _SerializingWs()
        ws_clients.add(ws)

        first = asyncio.create_task(broadcast({"type": "first"}))
        await ws.started.wait()
        await broadcast({"type": "second"})
        await broadcast({"type": "third"})
        assert ws.max_active == 1
        assert ws.payloads == ["first"]

        ws.release.set()
        await first
        await asyncio.sleep(0)

        assert ws.max_active == 1
        assert ws.payloads == ["first", "second", "third"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_clean_ws_clients")
    async def test_cancelled_broadcast_clears_inflight_state(self):
        """Cancelling one broadcast must not wedge future sends for that client."""

        class _CancellableWs:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.payloads: list[str] = []
                self._first = True

            async def send_text(self, payload: str) -> None:
                self.payloads.append(json.loads(payload)["type"])
                if self._first:
                    self._first = False
                    self.started.set()
                    await asyncio.Future()

        ws = _CancellableWs()
        ws_clients.add(ws)

        first = asyncio.create_task(broadcast({"type": "first"}))
        await ws.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        await broadcast({"type": "second"})

        assert ws.payloads == ["first", "second"]


# ===========================================================================
# invalidate_pipeline_index
# ===========================================================================


class TestScenarioNormalization:
    """Tests for scenario normalization in parse_pipeline_to_graph."""

    def test_live_is_moved_to_first_position(self, tmp_path):
        """When sidecar has 'live' not in first position, it must be normalized to first."""
        from haute.routes._helpers import parse_pipeline_to_graph

        # Write a minimal pipeline file
        py_path = tmp_path / "pipeline.py"
        py_path.write_text(
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "@pipeline.polars\n"
            "def transform(df):\n"
            "    return df\n"
        )

        # Write sidecar with "live" NOT in first position
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(
            json.dumps(
                {
                    "sources": ["test_batch", "live", "source_b"],
                    "active_source": "live",
                }
            )
        )

        graph = parse_pipeline_to_graph(py_path)

        assert graph.sources[0] == "live", f"Expected 'live' first, got: {graph.sources}"
        # All original sources must still be present
        assert set(graph.sources) == {"live", "test_batch", "source_b"}
        # No duplicates
        assert len(graph.sources) == 3


class TestInvalidatePipelineIndex:
    def test_clears_cache(self, monkeypatch):
        """Calling invalidate should set module-level caches to None."""
        import haute.routes._helpers as helpers

        monkeypatch.setattr(helpers, "_pipeline_index", {"old": Path("old.py")})
        monkeypatch.setattr(helpers, "_module_deps", {"old": set()})
        invalidate_pipeline_index()
        assert helpers._pipeline_index is None
        assert helpers._module_deps is None


# ===========================================================================
# pipeline_dir() — haute.toml resolution
# ===========================================================================


class TestPipelineDir:
    """Tests for pipeline_dir() — haute.toml presence/absence/malformed."""

    def test_returns_cwd_when_no_toml(self, tmp_path, monkeypatch):
        """When haute.toml is absent, falls back to cwd."""
        from haute.routes._helpers import pipeline_dir

        pipeline_dir.cache_clear()
        monkeypatch.chdir(tmp_path)
        result = pipeline_dir()
        assert result == tmp_path.resolve()
        pipeline_dir.cache_clear()

    def test_returns_parent_of_configured_pipeline(self, tmp_path, monkeypatch):
        """When haute.toml has [project].pipeline, return its parent."""
        from haute.routes._helpers import pipeline_dir

        pipeline_dir.cache_clear()
        monkeypatch.chdir(tmp_path)
        # Create a subdirectory with a pipeline file
        sub = tmp_path / "pipelines"
        sub.mkdir()
        (sub / "main.py").write_text("")
        # Write haute.toml
        toml = tmp_path / "haute.toml"
        toml.write_text('[project]\npipeline = "pipelines/main.py"\n')
        result = pipeline_dir()
        assert result == sub.resolve()
        pipeline_dir.cache_clear()

    def test_falls_back_when_toml_has_no_pipeline_key(self, tmp_path, monkeypatch):
        """When haute.toml exists but has no [project].pipeline, falls back to cwd."""
        from haute.routes._helpers import pipeline_dir

        pipeline_dir.cache_clear()
        monkeypatch.chdir(tmp_path)
        toml = tmp_path / "haute.toml"
        toml.write_text("[project]\n")
        result = pipeline_dir()
        assert result == tmp_path.resolve()
        pipeline_dir.cache_clear()

    def test_rejects_configured_pipeline_outside_project_root(self, tmp_path, monkeypatch):
        import pytest

        from haute.errors import ConfigError
        from haute.routes._helpers import pipeline_dir

        pipeline_dir.cache_clear()
        monkeypatch.chdir(tmp_path)
        toml = tmp_path / "haute.toml"
        toml.write_text('[project]\npipeline = "../outside/main.py"\n')

        with pytest.raises(ConfigError, match="outside the project root"):
            pipeline_dir()

        pipeline_dir.cache_clear()

    def test_raises_config_error_when_toml_is_corrupt(self, tmp_path, monkeypatch):
        """Malformed haute.toml raises ConfigError (changed in Phase 2 audit).

        Previous behaviour silently fell back to cwd, which routed every
        downstream save/load at the wrong directory and surfaced as
        confusing "file not found" errors far from the real cause.
        Narrowing the catch to ``TOMLDecodeError``/``OSError`` and re-
        raising as ``ConfigError`` keeps the error close to its cause
        so the user can fix the toml in one edit.
        """
        import pytest

        from haute.errors import ConfigError
        from haute.routes._helpers import pipeline_dir

        pipeline_dir.cache_clear()
        monkeypatch.chdir(tmp_path)
        toml = tmp_path / "haute.toml"
        toml.write_text("not valid toml {{{}}}}")
        with pytest.raises(ConfigError):
            pipeline_dir()
        pipeline_dir.cache_clear()


# ===========================================================================
# find_typed_node — found, not found, wrong type
# ===========================================================================


class TestFindTypedNode:
    """Tests for find_typed_node — lookup + type validation."""

    def test_returns_node_when_found_and_type_matches(self):
        from haute.routes._helpers import find_typed_node

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="m1",
                    data=NodeData(label="model", nodeType=NodeType.MODELLING),
                ),
            ],
        )
        node = find_typed_node(graph, "m1", NodeType.MODELLING, "modelling")
        assert node.id == "m1"
        assert node.data.nodeType == NodeType.MODELLING

    def test_raises_404_when_node_not_found(self):
        from haute.routes._helpers import find_typed_node

        graph = PipelineGraph(nodes=[])
        with pytest.raises(HTTPException) as exc_info:
            find_typed_node(graph, "missing", NodeType.MODELLING, "modelling")
        assert exc_info.value.status_code == 404

    def test_raises_400_when_wrong_type(self):
        from haute.routes._helpers import find_typed_node

        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="s1",
                    data=NodeData(label="source", nodeType=NodeType.DATA_INPUT),
                ),
            ],
        )
        with pytest.raises(HTTPException) as exc_info:
            find_typed_node(graph, "s1", NodeType.MODELLING, "modelling")
        assert exc_info.value.status_code == 400
        assert "modelling" in exc_info.value.detail
        assert "dataInput" in exc_info.value.detail


# ===========================================================================
# discover_pipelines and lookup_pipeline_by_name
# ===========================================================================


class TestDiscoverPipelines:
    """Tests for discover_pipelines wrapper."""

    def test_delegates_to_discovery_module(self):
        """discover_pipelines should delegate to haute.discovery.discover_pipelines."""
        from haute.routes._helpers import discover_pipelines

        with patch("haute.discovery.discover_pipelines", return_value=[Path("/a.py")]):
            result = discover_pipelines()
            assert result == [Path("/a.py")]

    def test_returns_empty_list_when_no_pipelines(self):
        from haute.routes._helpers import discover_pipelines

        with patch("haute.discovery.discover_pipelines", return_value=[]):
            result = discover_pipelines()
            assert result == []


class TestLookupPipelineByName:
    """Tests for lookup_pipeline_by_name — O(1) name→path lookup."""

    def test_returns_path_for_known_pipeline(self):
        import haute.routes._helpers as helpers
        from haute.routes._helpers import lookup_pipeline_by_name

        # Manually inject a known pipeline index
        old_index = helpers._pipeline_index
        try:
            helpers._pipeline_index = {"my_pipeline": Path("/path/to/my_pipeline.py")}
            result = lookup_pipeline_by_name("my_pipeline")
            assert result == Path("/path/to/my_pipeline.py")
        finally:
            helpers._pipeline_index = old_index

    def test_returns_none_for_unknown_pipeline(self):
        import haute.routes._helpers as helpers
        from haute.routes._helpers import lookup_pipeline_by_name

        old_index = helpers._pipeline_index
        try:
            helpers._pipeline_index = {"other": Path("/other.py")}
            result = lookup_pipeline_by_name("nonexistent")
            assert result is None
        finally:
            helpers._pipeline_index = old_index


# ===========================================================================
# parse_pipeline_to_graph — sidecar position merging and source normalization
# ===========================================================================


class TestParsePipelineToGraph:
    """Tests for parse_pipeline_to_graph — merges sidecar state into parsed graph."""

    def test_positions_applied_from_sidecar(self, tmp_path):
        """Node positions from sidecar file are applied to the parsed graph."""
        from haute.routes._helpers import parse_pipeline_to_graph

        py_path = tmp_path / "pipeline.py"
        py_path.write_text(
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "@pipeline.polars\n"
            "def my_node(df):\n"
            "    return df\n"
        )
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(json.dumps({"positions": {"my_node": {"x": 42.0, "y": 99.0}}}))

        graph = parse_pipeline_to_graph(py_path)
        # Find the node with id "my_node"
        node = next((n for n in graph.nodes if n.id == "my_node"), None)
        assert node is not None
        assert node.position == {"x": 42.0, "y": 99.0}

    def test_submodel_position_round_trips_through_save_then_parse(self, tmp_path):
        """save_sidecar → parse_pipeline_to_graph preserves a submodel position.

        The parser hands ``parse_pipeline_to_graph`` a graph whose submodel
        placeholder has ``id="submodel__<name>"`` / ``label="<name>"``.  Pin
        the full save→load round-trip for that node type: with the fix, the
        saved key matches the parser id and the position survives.
        """
        from haute.routes._helpers import parse_pipeline_to_graph, save_sidecar

        py_path = tmp_path / "pipeline.py"
        py_path.write_text("# parsed back via patched parser\n")

        sm_node = GraphNode(
            id="submodel__model_stuff",
            position={"x": 700.0, "y": 800.0},
            data=NodeData(label="model_stuff", nodeType=NodeType.SUBMODEL),
        )
        save_sidecar(py_path, PipelineGraph(nodes=[sm_node], edges=[]))

        # The parser always rebuilds the placeholder id from the name; mimic
        # that by returning a freshly-defaulted (0, 0) node from the parse step.
        parsed = PipelineGraph(
            nodes=[
                GraphNode(
                    id="submodel__model_stuff",
                    position={"x": 0.0, "y": 0.0},
                    data=NodeData(label="model_stuff", nodeType=NodeType.SUBMODEL),
                ),
            ],
            edges=[],
        )
        with patch("haute.parser.parse_pipeline_file", return_value=parsed):
            graph = parse_pipeline_to_graph(py_path)

        node = next(n for n in graph.nodes if n.id == "submodel__model_stuff")
        assert node.position == {"x": 700.0, "y": 800.0}

    def test_sources_without_live_get_live_prepended(self, tmp_path):
        """When sidecar sources list does not contain 'live', it is prepended."""
        from haute.routes._helpers import parse_pipeline_to_graph

        py_path = tmp_path / "pipeline.py"
        py_path.write_text(
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "@pipeline.polars\n"
            "def node(df):\n"
            "    return df\n"
        )
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(json.dumps({"sources": ["batch_a", "batch_b"]}))

        graph = parse_pipeline_to_graph(py_path)
        assert graph.sources[0] == "live"
        assert "batch_a" in graph.sources
        assert "batch_b" in graph.sources

    def test_active_source_added_to_sources_if_missing(self, tmp_path):
        """If active_source is not in the sources list, it should be appended."""
        from haute.routes._helpers import parse_pipeline_to_graph

        py_path = tmp_path / "pipeline.py"
        py_path.write_text(
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "@pipeline.polars\n"
            "def node(df):\n"
            "    return df\n"
        )
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(
            json.dumps({"sources": ["live", "batch_a"], "active_source": "batch_new"})
        )

        graph = parse_pipeline_to_graph(py_path)
        assert "batch_new" in graph.sources
        assert graph.active_source == "batch_new"

    def test_no_sidecar_uses_defaults(self, tmp_path):
        """Without a sidecar file, graph gets default source state."""
        from haute.routes._helpers import parse_pipeline_to_graph

        py_path = tmp_path / "pipeline.py"
        py_path.write_text(
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "@pipeline.polars\n"
            "def node(df):\n"
            "    return df\n"
        )

        graph = parse_pipeline_to_graph(py_path)
        assert graph.sources == ["live"]
        assert graph.active_source == "live"
