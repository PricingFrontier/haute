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
    broadcast,
    invalidate_pipeline_index,
    is_self_write,
    load_sidecar,
    load_sidecar_positions,
    mark_self_write,
    raise_node_not_found,
    raise_node_type_error,
    raise_pipeline_not_found,
    raise_validation_error,
    save_sidecar,
    validate_safe_path,
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
            raise_node_type_error("n1", "dataSource", "polars")
        assert exc_info.value.status_code == 400
        assert "dataSource" in exc_info.value.detail
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
                    data=NodeData(label="A", nodeType=NodeType.DATA_SOURCE),
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
                    data=NodeData(label="A", nodeType=NodeType.DATA_SOURCE),
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
                    data=NodeData(label="source", nodeType=NodeType.DATA_SOURCE),
                ),
            ],
        )
        with pytest.raises(HTTPException) as exc_info:
            find_typed_node(graph, "s1", NodeType.MODELLING, "modelling")
        assert exc_info.value.status_code == 400
        assert "modelling" in exc_info.value.detail
        assert "dataSource" in exc_info.value.detail


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
