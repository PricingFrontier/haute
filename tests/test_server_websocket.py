"""The server's ``/ws/sync`` WebSocket and its broadcasts.

Clients connect, keep alive and resync on reconnect; event-bus messages are broadcast to every
client; and after a git action rewrites the files the server publishes what is on disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests import _server_helpers
from tests._project import use_project
from tests._server_helpers import (
    DOCUMENT_FINGERPRINT_HEADER,
    _file_input_config,
)
from tests.conftest import (
    current_source_revision,
)

# Fixtures shared with the other modules split from one suite.
client = _server_helpers.client
pipeline_dir = _server_helpers.pipeline_dir


# ---------------------------------------------------------------------------
# WebSocket -- /ws/sync connect/disconnect and broadcast
# ---------------------------------------------------------------------------


class TestWebSocket:
    def test_connect_and_disconnect(self, client: TestClient):
        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ) as ws:
            # Connection should be accepted -- sending a keep-alive message works
            ws.send_text("ping")
        # No error means connect + clean disconnect succeeded

    def test_broadcast_reaches_connected_client(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        """Save endpoint writes files and triggers sidecar -- verify the
        full HTTP flow still works with an active WebSocket connection."""
        from haute.routes._helpers import ws_clients

        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ):
            assert len(ws_clients) >= 1

            # Use a save call to exercise the full stack (which calls mark_self_write)
            graph = {
                "nodes": [
                    {
                        "id": "s",
                        "type": "pipelineNode",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "label": "S",
                            "nodeType": "dataInput",
                            "config": _file_input_config("d.parquet"),
                        },
                    },
                ],
                "edges": [],
            }
            resp = client.post(
                "/api/pipeline/save",
                json={
                    "name": "ws_test",
                    "description": "",
                    "graph": graph,
                    "source_file": "ws_test.py",
                    "base_revision": current_source_revision(
                        pipeline_dir / "ws_test.py", pipeline_dir
                    ),
                },
            )
            assert resp.status_code == 200

        # After disconnect, client should be removed
        assert len(ws_clients) == 0


class TestWebSocketResync:
    """Client-requested reconnect resync over /ws/sync."""

    class _CollectingWebSocket:
        def __init__(self) -> None:
            from starlette.datastructures import Headers, QueryParams

            from haute._local_security import SESSION_TOKEN_COOKIE, local_session_token

            self.headers = Headers(
                {
                    "host": "localhost",
                    "origin": "http://localhost",
                    "cookie": f"{SESSION_TOKEN_COOKIE}={local_session_token()}",
                }
            )
            self.scope = {"scheme": "ws"}
            self.query_params = QueryParams("")
            self.frames: list[dict[str, object]] = []

        async def send_text(self, payload: str) -> None:
            self.frames.append(json.loads(payload))

        async def close(self, *, code: int, reason: str) -> None:
            self.frames.append({"type": "close", "code": code, "reason": reason})

    def test_resync_without_current_schema_version_is_rejected_without_loading(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"
        loaded_paths: list[Path] = []

        def _recording_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            raise AssertionError(f"unexpected document load for {path}")

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_recording_load,
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

        assert loaded_paths == []
        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "Unsupported pipeline document schema version",
                "source_file": "test_pipeline.py",
            }
        ]

    def test_resync_does_not_use_watcher_fingerprint_to_silence_stale_client(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute._pipeline_recovery import pipeline_document_fingerprint
        from haute.routes._helpers import load_pipeline_editor_document
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"
        current_fingerprint = pipeline_document_fingerprint(
            load_pipeline_editor_document(pipeline_file, project_root=pipeline_dir).model_dump(
                mode="json", by_alias=True
            )
        )

        ws = self._CollectingWebSocket()
        with patch("haute.server.discover_pipelines", return_value=[pipeline_file]):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )
            assert [frame["type"] for frame in ws.frames] == ["pipeline_document_update"]
            ws.frames.clear()

            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                            "document_fingerprint": hashlib.sha256(
                                b"client missed broadcast"
                            ).hexdigest(),
                        }
                    ),
                )
            )

        assert [frame["type"] for frame in ws.frames] == ["pipeline_document_update"]
        assert ws.frames[0]["document_fingerprint"] == current_fingerprint

    def test_resync_sidecar_only_change_sends_new_document(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"

        ws = self._CollectingWebSocket()
        with patch("haute.server.discover_pipelines", return_value=[pipeline_file]):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )
            assert len(ws.frames) == 1
            first_document = ws.frames[0]["document"]
            assert isinstance(first_document, dict)
            first_fingerprint = ws.frames[0]["document_fingerprint"]
            first_node = first_document["nodes"][0]
            assert isinstance(first_node, dict)
            node_id = first_node["authored_id"]
            assert isinstance(node_id, str)

            pipeline_file.with_suffix(".haute.json").write_text(
                json.dumps({"positions": {node_id: {"x": 321, "y": 654}}}),
                encoding="utf-8",
            )

            ws.frames.clear()
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                            "document_fingerprint": first_fingerprint,
                        }
                    ),
                )
            )

        assert [frame["type"] for frame in ws.frames] == ["pipeline_document_update"]
        updated_document = ws.frames[0]["document"]
        assert isinstance(updated_document, dict)
        updated_node = next(
            node
            for node in updated_document["nodes"]
            if isinstance(node, dict) and node.get("authored_id") == node_id
        )
        assert updated_node["display_position"] == {"x": 321.0, "y": 654.0}

    def test_resync_offloads_discovery_and_recovery_work(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.routes._helpers import load_pipeline_editor_document
        from haute.server import _handle_ws_sync_message, _last_broadcast_fp

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        _last_broadcast_fp.clear()
        in_worker = False
        offload_calls = 0

        async def _recording_threadpool(func, *args, **kwargs):
            nonlocal in_worker, offload_calls
            offload_calls += 1
            in_worker = True
            try:
                return func(*args, **kwargs)
            finally:
                in_worker = False

        def _worker_only_discover() -> list[Path]:
            assert in_worker, "resync discovery must run outside the event loop"
            return [pipeline_file]

        def _worker_only_load(path: Path, **kwargs: object):
            assert in_worker, "resync document recovery must run outside the event loop"
            return load_pipeline_editor_document(path, **kwargs)  # type: ignore[arg-type]

        ws = self._CollectingWebSocket()
        with (
            patch(
                "haute.server.run_in_threadpool",
                side_effect=_recording_threadpool,
                create=True,
            ),
            patch("haute.server.discover_pipelines", side_effect=_worker_only_discover),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_worker_only_load,
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )

        assert offload_calls == 1
        assert [frame["type"] for frame in ws.frames] == ["pipeline_document_update"]

    def test_first_resync_with_the_loaded_fingerprint_sends_nothing(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        """A page that just loaded the document must not be sent it again on connect."""
        from unittest.mock import patch

        from haute.server import _handle_ws_sync_message

        loaded = client.get("/api/pipeline")
        assert loaded.json()["source_file"] == "test_pipeline.py"
        ws = self._CollectingWebSocket()
        with patch(
            "haute.server.discover_pipelines",
            return_value=[pipeline_dir / "test_pipeline.py"],
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                            "document_fingerprint": loaded.headers[DOCUMENT_FINGERPRINT_HEADER],
                        }
                    ),
                )
            )

        assert ws.frames == []

    def test_v1_resync_sends_editor_document_and_suppresses_equal_fingerprint(
        self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        from haute._pipeline_recovery import pipeline_document_fingerprint
        from haute.routes._helpers import load_pipeline_editor_document
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"
        document = load_pipeline_editor_document(pipeline_file, project_root=pipeline_dir)
        fingerprint = pipeline_document_fingerprint(document.model_dump(mode="json", by_alias=True))
        ws = self._CollectingWebSocket()
        with patch("haute.server.discover_pipelines", return_value=[pipeline_file]):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )
            assert ws.frames[0]["type"] == "pipeline_document_update"
            assert ws.frames[0]["schema_version"] == 1
            assert ws.frames[0]["document_fingerprint"] == fingerprint
            assert ws.frames[0]["document"]["load_status"] == "ready"  # type: ignore[index]
            ws.frames.clear()
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                            "document_fingerprint": fingerprint,
                        }
                    ),
                )
            )
        assert ws.frames == []

    @pytest.mark.parametrize(
        ("expected_status", "source"),
        [
            (
                "degraded",
                'import haute\npipeline = haute.Pipeline("broken")\n'
                "@pipeline.removed_node\ndef unavailable():\n    return None\n",
            ),
            (
                "source_only",
                'import haute\npipeline = haute.Pipeline("broken")\ndef invalid(:\n',
            ),
        ],
    )
    def test_v1_resync_delivers_non_ready_editor_documents(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        expected_status: str,
        source: str,
    ) -> None:
        from haute.server import _handle_ws_sync_message

        pipeline_file = tmp_path / "broken.py"
        pipeline_file.write_text(source, encoding="utf-8")
        use_project(tmp_path)
        ws = self._CollectingWebSocket()

        with patch("haute.server.discover_pipelines", return_value=[pipeline_file]):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "broken.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )

        assert len(ws.frames) == 1
        frame = ws.frames[0]
        assert frame["type"] == "pipeline_document_update"
        assert frame["document"]["load_status"] == expected_status  # type: ignore[index]
        assert frame["document"]["source_text"] == source  # type: ignore[index]

    def test_v1_resync_contains_unexpected_recovery_failure_as_source_only(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"
        ws = self._CollectingWebSocket()

        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch(
                "haute._pipeline_recovery.read_sidecar_state",
                side_effect=RuntimeError("private recovery failure"),
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )

        document = ws.frames[0]["document"]
        assert document["load_status"] == "source_only"  # type: ignore[index]
        diagnostic = document["diagnostics"][0]  # type: ignore[index]
        assert diagnostic["code"] == "pipeline_recovery_internal_error"
        assert diagnostic["incident_id"]
        assert "private recovery failure" not in diagnostic["message"]

    def test_v1_resync_sanitizes_system_load_failure(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)
        pipeline_file = pipeline_dir / "test_pipeline.py"
        ws = self._CollectingWebSocket()

        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=PermissionError("private absolute path"),
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )

        assert ws.frames == [
            {
                "type": "parse_error",
                "error": (
                    "Pipeline document could not be loaded. Check the server logs for details."
                ),
                "source_file": "test_pipeline.py",
            }
        ]

    def test_resync_rejects_non_discovered_python_file_without_parsing(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _handle_ws_sync_message

        use_project(pipeline_dir)

        ordinary_file = pipeline_dir / "helper.py"
        ordinary_file.write_text("def helper():\n    return 1\n")

        loaded_paths: list[Path] = []

        def _recording_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            raise AssertionError(f"unexpected document load for {path}")

        ws = self._CollectingWebSocket()
        with (
            patch(
                "haute.server.discover_pipelines",
                return_value=[pipeline_dir / "test_pipeline.py"],
            ),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_recording_load,
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "helper.py",
                            "document_schema_version": 1,
                        }
                    ),
                )
            )

        assert loaded_paths == []
        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "Resync source is not a discovered pipeline",
                "source_file": "helper.py",
            }
        ]

    def test_ws_sync_dispatches_resync_message(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from fastapi import WebSocketDisconnect

        from haute.server import ws_sync

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"

        class _OneMessageWebSocket(self._CollectingWebSocket):
            def __init__(self) -> None:
                super().__init__()
                self.accepted = False
                self.messages = [
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "document_schema_version": 1,
                        }
                    )
                ]

            async def accept(self) -> None:
                self.accepted = True

            async def receive_text(self) -> str:
                if self.messages:
                    return self.messages.pop(0)
                raise WebSocketDisconnect()

        ws = _OneMessageWebSocket()
        with patch("haute.server.discover_pipelines", return_value=[pipeline_file]):
            asyncio.run(ws_sync(ws))  # type: ignore[arg-type]

        assert ws.accepted is True
        # The socket learns its connection id first, then gets its resync's answer.
        assert [frame["type"] for frame in ws.frames] == [
            "sync_connection",
            "pipeline_document_update",
        ]

    def test_keep_alive_text_is_ignored(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(_handle_ws_sync_message(ws, "ping"))  # type: ignore[arg-type]

        assert ws.frames == []

    def test_non_resync_json_message_is_ignored(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(
            _handle_ws_sync_message(  # type: ignore[arg-type]
                ws,
                json.dumps({"type": "heartbeat"}),
            )
        )

        assert ws.frames == []

    def test_resync_without_string_source_file_sends_parse_error(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(
            _handle_ws_sync_message(  # type: ignore[arg-type]
                ws,
                json.dumps({"type": "resync", "source_file": None}),
            )
        )

        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "Resync request requires a source_file",
                "source_file": "",
            }
        ]


class TestBroadcast:
    def test_broadcast_removes_dead_clients(self):
        """Dead WebSocket clients should be pruned during broadcast."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        dead_ws = MagicMock()
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))

        ws_clients.add(dead_ws)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(broadcast({"type": "test"}))
            finally:
                loop.close()
        finally:
            ws_clients.discard(dead_ws)

        assert dead_ws not in ws_clients

    def test_broadcast_delivers_to_live_client(self):
        """A live mock client should receive the broadcast message."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        live_ws = MagicMock()
        live_ws.send_text = AsyncMock()

        ws_clients.add(live_ws)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    broadcast({"type": "pipeline_document_update", "document": {}})
                )
            finally:
                loop.close()

            live_ws.send_text.assert_called_once()
            payload = live_ws.send_text.call_args[0][0]
            assert '"type": "pipeline_document_update"' in payload
        finally:
            ws_clients.discard(live_ws)


class TestEventBusBroadcastBridge:
    def test_skips_cleanly_without_running_loop(self) -> None:
        from haute.server import _broadcast_event_as_ws_message

        with patch("haute.server.logger.debug") as mock_debug:
            _broadcast_event_as_ws_message(
                "pipeline_document_update", {"document": {}, "source_file": "x.py"}
            )

        mock_debug.assert_called_once()
        assert mock_debug.call_args.args[0] == "event_bus_broadcast_skipped"

    def test_schedules_broadcast_on_running_loop(self) -> None:
        from unittest.mock import AsyncMock

        from haute.server import _broadcast_event_as_ws_message

        async def _run() -> None:
            fake_broadcast = AsyncMock()
            with patch("haute.server.broadcast", fake_broadcast):
                _broadcast_event_as_ws_message(
                    "pipeline_document_update",
                    {"document": {"nodes": []}, "source_file": "x.py"},
                )
                await asyncio.sleep(0)

            fake_broadcast.assert_awaited_once_with(
                {
                    "type": "pipeline_document_update",
                    "document": {"nodes": []},
                    "source_file": "x.py",
                },
                exclude=None,
            )

        asyncio.run(_run())

    def test_a_publisher_on_a_worker_thread_reaches_the_server_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A git route's background task publishes from a threadpool worker, which
        has no running loop: its frames went nowhere, so the canvas never followed a
        switch. They are handed to the server's loop instead."""
        from unittest.mock import AsyncMock

        from haute import server

        async def _run() -> None:
            fake_broadcast = AsyncMock()
            monkeypatch.setattr(server, "broadcast", fake_broadcast)
            monkeypatch.setattr(server, "_server_loop", asyncio.get_running_loop())
            await asyncio.to_thread(
                server._broadcast_event_as_ws_message,
                "parse_error",
                {"error": "gone", "source_file": "x.py"},
            )
            for _ in range(3):
                await asyncio.sleep(0)

            fake_broadcast.assert_awaited_once_with(
                {"type": "parse_error", "error": "gone", "source_file": "x.py"},
                exclude=None,
            )

        asyncio.run(_run())

    def test_ws_message_frame_rejects_payload_type_collision(self) -> None:
        from haute.server import _ws_message_frame

        with pytest.raises(ValueError, match="reserved WebSocket frame key"):
            _ws_message_frame("pipeline_document_update", {"type": "parse_error"})

    def test_logs_failed_broadcast_task(self) -> None:
        from haute.server import _broadcast_event_as_ws_message

        async def _boom(frame: dict[str, object], **_kw: object) -> None:
            raise RuntimeError("ws boom")

        async def _run() -> None:
            with (
                patch("haute.server.broadcast", _boom),
                patch("haute.server.logger.error") as mock_error,
            ):
                _broadcast_event_as_ws_message(
                    "parse_error",
                    {"error": "bad syntax", "source_file": "x.py"},
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            mock_error.assert_called()
            assert any(
                call.args
                and call.args[0] == "event_bus_broadcast_failed"
                and call.kwargs.get("error") == "ws boom"
                for call in mock_error.call_args_list
            )

        asyncio.run(_run())

    def test_default_bus_document_and_parse_events_reach_registered_ws_client(self) -> None:
        """Exercise the real event-bus subscribers through the broadcast layer."""
        from haute._event_bus import default_bus
        from haute.routes._helpers import ws_clients_add, ws_clients_discard

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[dict[str, object]] = []

            async def send_text(self, payload: str) -> None:
                self.frames.append(json.loads(payload))

        async def _run() -> list[dict[str, object]]:
            ws = _FakeWebSocket()
            ws_clients_add(ws, connection_id="tab", owner=None)  # type: ignore[arg-type]
            try:
                default_bus.publish(
                    "pipeline.document.update",
                    {
                        "document": {"load_status": "ready"},
                        "document_fingerprint": "fp",
                        "source_file": "x.py",
                    },
                )
                default_bus.publish(
                    "parse.error",
                    {"error": "bad syntax", "source_file": "x.py"},
                )
                for _ in range(20):
                    if len(ws.frames) >= 2:
                        break
                    await asyncio.sleep(0)
                return ws.frames
            finally:
                ws_clients_discard(ws)  # type: ignore[arg-type]

        frames = asyncio.run(_run())

        assert frames == [
            {
                "type": "pipeline_document_update",
                "schema_version": 1,
                "document": {"load_status": "ready"},
                "document_fingerprint": "fp",
                "source_file": "x.py",
            },
            {
                "type": "parse_error",
                "error": "bad syntax",
                "source_file": "x.py",
            },
        ]

    def test_default_bus_document_event_uses_versioned_wire_envelope(self) -> None:
        from haute._event_bus import default_bus
        from haute.routes._helpers import ws_clients_add, ws_clients_discard

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[dict[str, object]] = []

            async def send_text(self, payload: str) -> None:
                self.frames.append(json.loads(payload))

        async def _run() -> list[dict[str, object]]:
            ws = _FakeWebSocket()
            ws_clients_add(ws, connection_id="tab", owner=None)  # type: ignore[arg-type]
            try:
                default_bus.publish(
                    "pipeline.document.update",
                    {
                        "document": {"load_status": "degraded"},
                        "document_fingerprint": "abc",
                        "source_file": "x.py",
                    },
                )
                for _ in range(20):
                    if ws.frames:
                        break
                    await asyncio.sleep(0)
                return ws.frames
            finally:
                ws_clients_discard(ws)  # type: ignore[arg-type]

        assert asyncio.run(_run()) == [
            {
                "type": "pipeline_document_update",
                "schema_version": 1,
                "document": {"load_status": "degraded"},
                "document_fingerprint": "abc",
                "source_file": "x.py",
            }
        ]


# ---------------------------------------------------------------------------
# WebSocket keep-alive
# ---------------------------------------------------------------------------


class TestWebSocketKeepAlive:
    def test_keep_alive_messages_accepted(self, client: TestClient):
        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ) as ws:
            ws.send_text("keep-alive")
            ws.send_text("ping")
            ws.send_text("")

    def test_dead_client_removed_from_set(self, client: TestClient):
        from haute.routes._helpers import ws_clients

        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ):
            assert len(ws_clients) >= 1
        assert len(ws_clients) == 0


# ---------------------------------------------------------------------------
# broadcast sends to all WebSocket clients
# ---------------------------------------------------------------------------


class TestBroadcastMultipleClients:
    def test_broadcast_sends_to_all_clients(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        ws1 = MagicMock()
        ws1.send_text = AsyncMock()
        ws2 = MagicMock()
        ws2.send_text = AsyncMock()

        ws_clients.add(ws1)
        ws_clients.add(ws2)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(broadcast({"type": "test", "data": 42}))
            finally:
                loop.close()

            ws1.send_text.assert_called_once()
            ws2.send_text.assert_called_once()
            payload1 = json.loads(ws1.send_text.call_args[0][0])
            payload2 = json.loads(ws2.send_text.call_args[0][0])
            assert payload1 == {"type": "test", "data": 42}
            assert payload2 == {"type": "test", "data": 42}
        finally:
            ws_clients.discard(ws1)
            ws_clients.discard(ws2)


# ---------------------------------------------------------------------------
# mark_self_write and is_self_write timing
# ---------------------------------------------------------------------------


class TestBroadcastTreeChange:
    """After a git action that rewrote the files (the watcher paused), the server
    publishes what is on disk: each pipeline's document, or its load failure on its
    own, and the form's change."""

    def test_each_pipeline_and_the_form_are_announced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute import server
        from haute._event_bus import default_bus

        use_project(tmp_path)
        # Pipelines other tests broadcast would count as known, and gone.
        monkeypatch.setattr(server, "_last_broadcast_fp", {})
        server.invalidate_pipeline_index()
        for name in ("good", "broken"):
            (tmp_path / f"{name}.py").write_text(
                f'import haute\n\npipeline = haute.Pipeline("{name}")\n'
            )
        real_load = server.load_pipeline_editor_document

        def load(path: Path, **kwargs: object) -> object:
            if path.name == "broken.py":
                raise RuntimeError("cannot recover")
            return real_load(path, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(server, "load_pipeline_editor_document", load)
        monkeypatch.setattr(
            server, "_workbench_form_file", lambda _root: ("workbench", tmp_path / "workbench")
        )
        events: list[tuple[str, dict[str, object]]] = []
        unsubscribes = [
            default_bus.subscribe(kind, lambda payload, kind=kind: events.append((kind, payload)))
            for kind in ("pipeline.document.update", "parse.error", "workbench.form.changed")
        ]
        try:
            server._broadcast_tree_change({})
        finally:
            for unsubscribe in unsubscribes:
                unsubscribe()

        assert sorted((kind, payload.get("source_file")) for kind, payload in events) == [
            ("parse.error", "broken.py"),
            ("pipeline.document.update", "good.py"),
            ("workbench.form.changed", None),
        ]
        assert ("workbench.form.changed", {"form": "workbench"}) in events

    def test_the_configured_pipeline_missing_from_the_version_is_announced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Discovery refuses when haute.toml names a pipeline the version lacks; the
        announcement must still go out, naming it, not stop at that refusal."""
        from haute import server
        from haute._event_bus import default_bus
        from haute.routes._helpers import invalidate_pipeline_index

        use_project(tmp_path)
        monkeypatch.setattr(server, "_last_broadcast_fp", {})
        monkeypatch.setattr(server, "_workbench_form_file", lambda _root: None)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "main.py"\n')
        (tmp_path / "main.py").write_text('import haute\n\npipeline = haute.Pipeline("p")\n')
        before = [str((tmp_path / "main.py").resolve())]
        (tmp_path / "main.py").unlink()  # the switch's destination lacks it
        invalidate_pipeline_index()
        events: list[dict[str, object]] = []
        unsubscribe = default_bus.subscribe("parse.error", events.append)
        try:
            server._broadcast_tree_change({"pipelines_before": before})
        finally:
            unsubscribe()
            invalidate_pipeline_index()

        assert events == [{"error": server.PIPELINE_NOT_IN_VERSION, "source_file": "main.py"}]

    def test_a_pipeline_the_action_took_away_is_announced_as_not_in_this_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute import server
        from haute._event_bus import default_bus
        from haute.routes._helpers import invalidate_pipeline_index

        use_project(tmp_path)
        monkeypatch.setattr(server, "_last_broadcast_fp", {})
        monkeypatch.setattr(server, "_workbench_form_file", lambda _root: None)
        invalidate_pipeline_index()
        for name in ("kept", "gone"):
            (tmp_path / f"{name}.py").write_text(
                f'import haute\n\npipeline = haute.Pipeline("{name}")\n'
            )
        server._broadcast_tree_change({})  # both known, as before a switch
        (tmp_path / "gone.py").unlink()  # the switch's destination lacks it
        events: list[tuple[str, dict[str, object]]] = []
        unsubscribes = [
            default_bus.subscribe(kind, lambda payload, kind=kind: events.append((kind, payload)))
            for kind in ("pipeline.document.update", "parse.error")
        ]
        try:
            server._broadcast_tree_change({})
            server._broadcast_tree_change({})  # announced once, not on every change
        finally:
            for unsubscribe in unsubscribes:
                unsubscribe()
            invalidate_pipeline_index()

        assert sorted((kind, payload.get("source_file")) for kind, payload in events) == [
            ("parse.error", "gone.py"),
            ("pipeline.document.update", "kept.py"),
            ("pipeline.document.update", "kept.py"),
        ]
        assert (
            "parse.error",
            {"error": server.PIPELINE_NOT_IN_VERSION, "source_file": "gone.py"},
        ) in events

    def test_a_pipeline_no_cache_still_holds_is_named_from_the_route_inventory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A save invalidates the index and the watcher forgets the broadcast hash,
        so after a switch only the route's inventory, taken before the action,
        still knows the pipeline the destination lacks."""
        from haute import server
        from haute._event_bus import default_bus
        from haute.routes._helpers import invalidate_pipeline_index

        use_project(tmp_path)
        monkeypatch.setattr(server, "_last_broadcast_fp", {})
        monkeypatch.setattr(server, "_workbench_form_file", lambda _root: None)
        for name in ("kept", "gone"):
            (tmp_path / f"{name}.py").write_text(
                f'import haute\n\npipeline = haute.Pipeline("{name}")\n'
            )
        before = [str((tmp_path / name).resolve()) for name in ("kept.py", "gone.py")]
        (tmp_path / "gone.py").unlink()  # the switch's destination lacks it
        invalidate_pipeline_index()  # nothing cached from before the switch
        events: list[tuple[str, dict[str, object]]] = []
        unsubscribe = default_bus.subscribe(
            "parse.error", lambda p: events.append(("parse.error", p))
        )
        try:
            server._broadcast_tree_change({"pipelines_before": before})
        finally:
            unsubscribe()
            invalidate_pipeline_index()

        assert events == [
            ("parse.error", {"error": server.PIPELINE_NOT_IN_VERSION, "source_file": "gone.py"})
        ]
