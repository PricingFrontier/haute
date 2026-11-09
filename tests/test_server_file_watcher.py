"""The server's file watcher and live sync.

The watcher suppresses the server's own writes and broadcasts external ones, re-parses pipelines
when JSON configs or imported modules change, reports parse errors, skips unchanged fingerprints,
recovers sidecars, follows the configured pipeline directory, and a save never round-trips through
it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from fastapi.testclient import TestClient
from watchfiles import Change

from haute._project_context import ProjectContext
from tests import _server_helpers
from tests._project import use_project
from tests._server_helpers import (
    _file_input_config,
)

# Fixtures shared with the other modules split from one suite.
client = _server_helpers.client
pipeline_dir = _server_helpers.pipeline_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _run_file_watcher_and_drain() -> None:
    """Run the file watcher once and yield to scheduled broadcast tasks."""
    from haute.server import _file_watcher

    await _file_watcher()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Self-write tracking
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# File watcher logic (unit test with mocked awatch)
# ---------------------------------------------------------------------------


def test_a_preview_of_several_frames_is_its_own_request_for_supersession() -> None:
    """A preview of several frames supersedes only an earlier one of the same frames."""
    from haute._types import PipelineGraph
    from haute.routes.pipeline import _preview_supersession_key

    graph = PipelineGraph(nodes=[], edges=[])

    def key(port_label: str | None, port_labels: list[str] | None) -> tuple[str, ...]:
        return _preview_supersession_key(
            graph, "live", "response", 100, None, port_label, port_labels
        )

    keys = {
        key(None, None),
        key("pricing", None),
        key(None, ["pricing"]),
        key(None, ["pricing", "summary"]),
        key(None, ["summary", "pricing"]),
    }
    assert len(keys) == 5
    assert key(None, ["pricing", "summary"]) == key(None, ["pricing", "summary"])


class TestFileWatcher:
    def test_py_change_triggers_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A .py file change should recover and broadcast a pipeline document."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")
        fake_changes = [(Change.modified, py_file)]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        # awatch is imported locally inside _file_watcher via
        # ``from watchfiles import awatch``, so patch it on the watchfiles module.
        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["type"] for call in broadcast_calls] == [
            "pipeline_document_update",
        ]
        assert "document" in broadcast_calls[0]

    def test_paused_skips_broadcast(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """S30: while the watcher is paused (a git op in flight), a .py change
        is dropped -- the wholesale tree replacement of a move/checkout must not
        be broadcast as user edits."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "test_pipeline.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.watcher_is_paused", return_value=True),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_non_py_files_ignored(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """Non-.py files should be ignored by the watcher."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "readme.txt"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    @pytest.mark.parametrize(
        ("workbench", "changed", "announced", "own"),
        [
            ("enabled = true", ["workbench/form.json"], ["workbench_form_changed"], False),
            # Once for a batch, however many files the save wrote or deleted.
            (
                "enabled = true",
                ["workbench/schema/policy.json", "workbench/components/layers.json"],
                ["workbench_form_changed"],
                False,
            ),
            # The server's own save is announced too: another window learns of it.
            ("enabled = true", ["workbench/sheets/sheet_1.json"], ["workbench_form_changed"], True),
            # Where the form's one file used to be is no part of the form.
            ("enabled = true", ["forms/form.json"], [], False),
            ("enabled = false", ["workbench/form.json"], [], False),
        ],
    )
    def test_the_workbench_form_changed_on_disk_is_announced(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        workbench: str,
        changed: list[str],
        announced: list[str],
        own: bool,
    ):
        """A change to a file in the form folder, by hand, by git outside Haute or by a save,
        is announced so the workbench can read it again; other JSON is not the form."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)
        (pipeline_dir / "haute.toml").write_text(
            f'[project]\nname = "x"\n\n[workbench]\n{workbench}\n', encoding="utf-8"
        )
        fake_changes = [(Change.modified, str(pipeline_dir / path)) for path in changed]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=own),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run_file_watcher_and_drain())
            finally:
                loop.close()

        assert [call["type"] for call in broadcast_calls] == announced
        for call in broadcast_calls:
            # The form folder as the status names it: the root pipeline's own.
            assert call["form"] == "workbench"

    def test_self_write_skips_broadcast(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """Changes during self-write cooldown should be skipped."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "test_pipeline.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=True),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_path_specific_self_write_does_not_skip_unrelated_user_edit(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A server write for one path must not suppress another file in the same batch."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        self_written = pipeline_dir / "server_saved.py"
        user_edited = pipeline_dir / "test_pipeline.py"
        content = b"import haute\n\npipeline = haute.Pipeline('server_saved')\n"
        self_written.write_bytes(content)
        mark_self_write(self_written, content=content)

        fake_changes = [
            (Change.modified, str(self_written)),
            (Change.modified, str(user_edited)),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["source_file"] for call in broadcast_calls] == [
            "test_pipeline.py",
        ]

    def test_external_write_to_self_written_path_before_flush_is_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A path marked for self-write that receives external modifications
        before flush must broadcast.
        """
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        content_x = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="Pipeline X")


@pipeline.polars
def node_x():
    pass
"""
        pipeline_file.write_bytes(content_x)
        mark_self_write(pipeline_file, content=content_x)

        content_y = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="Pipeline Y")


@pipeline.polars
def unique_node_y():
    pass
"""
        pipeline_file.write_bytes(content_y)

        fake_changes = [(Change.modified, str(pipeline_file))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 1
        call = broadcast_calls[0]
        assert call["source_file"] == "test_pipeline.py"
        assert call["document"]["pipeline_description"] == "Pipeline Y"
        assert any(n["authored_id"] == "unique_node_y" for n in call["document"]["nodes"])

    def test_unchanged_self_write_to_same_path_is_suppressed(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A self-written file whose contents match the recorded mark is suppressed."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        content_x = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="Pipeline X")


@pipeline.polars
def node_x():
    pass
"""
        mark_self_write(pipeline_file, content=content_x)
        pipeline_file.write_bytes(content_x)

        fake_changes = [(Change.modified, str(pipeline_file))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_self_delete_is_suppressed_but_external_recreate_is_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Self-deletion of a sidecar is suppressed.

        External deletion and recreation broadcast.
        """
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        sidecar = pipeline_dir / "test_pipeline.haute.json"
        sidecar_content = json.dumps(
            {"positions": {"source": {"x": 100, "y": 200}, "transform": {"x": 300, "y": 400}}}
        ).encode("utf-8")
        sidecar.write_bytes(sidecar_content)

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        def _run_watcher(changes: list[tuple[Change, str]]) -> None:
            async def _fake_awatch(*dirs, **kw):
                yield changes

            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()

        # 1. Unmarked control: delete the sidecar without a marker,
        # inject Change.deleted, assert one broadcast
        sidecar.unlink()
        _run_watcher([(Change.deleted, str(sidecar))])
        assert len(broadcast_calls) == 1
        assert broadcast_calls[0]["source_file"] == "test_pipeline.py"

        # 2. Recreate the sidecar, mark mark_self_write(sidecar, deleted=True),
        # delete it, inject Change.deleted, assert no additional broadcast
        sidecar.write_bytes(sidecar_content)
        mark_self_write(sidecar, deleted=True)
        sidecar.unlink()
        _run_watcher([(Change.deleted, str(sidecar))])
        assert len(broadcast_calls) == 1

        # 3. Recreate it externally (no marker), inject Change.added,
        # assert one more broadcast
        sidecar.write_bytes(sidecar_content)
        _run_watcher([(Change.added, str(sidecar))])
        assert len(broadcast_calls) == 2
        assert broadcast_calls[1]["source_file"] == "test_pipeline.py"

    def test_external_restore_of_previously_broadcast_content_is_broadcast_after_a_self_write(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Restoring previously-broadcast content after a self-write triggers broadcast."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"

        content_a = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="Pipeline A")


@pipeline.polars
def node_a():
    pass
"""
        content_b = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="Pipeline B")


@pipeline.polars
def node_b():
    pass
"""

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        async def _fake_awatch(*dirs, **kw):
            # Batch 1: external write of valid pipeline A (no marker) -> expect a broadcast
            pipeline_file.write_bytes(content_a)
            yield [(Change.modified, str(pipeline_file))]
            for _ in range(5):
                await asyncio.sleep(0)
            assert len(broadcast_calls) == 1

            # Batch 2: server writes valid pipeline B and marks with content=
            # B's bytes before event -> expect no new broadcast
            pipeline_file.write_bytes(content_b)
            mark_self_write(pipeline_file, content=content_b)
            yield [(Change.modified, str(pipeline_file))]
            for _ in range(5):
                await asyncio.sleep(0)
            assert len(broadcast_calls) == 1

            # Batch 3: external restore of A's exact bytes (no marker) ->
            # expect a broadcast whose document reflects A
            pipeline_file.write_bytes(content_a)
            yield [(Change.modified, str(pipeline_file))]
            for _ in range(5):
                await asyncio.sleep(0)
            assert len(broadcast_calls) == 2

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 2
        assert broadcast_calls[0]["document"]["pipeline_description"] == "Pipeline A"
        assert any(n["authored_id"] == "node_a" for n in broadcast_calls[0]["document"]["nodes"])
        assert broadcast_calls[1]["document"]["pipeline_description"] == "Pipeline A"
        assert any(n["authored_id"] == "node_a" for n in broadcast_calls[1]["document"]["nodes"])

    def test_unrelated_external_edit_in_same_batch_is_broadcast_with_its_content(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """In a batch containing both a self-write and an external edit,
        only the external edit broadcasts.
        """
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        use_project(pipeline_dir)

        self_written = pipeline_dir / "server_saved.py"
        user_edited = pipeline_dir / "test_pipeline.py"

        server_content = b"import haute\n\npipeline = haute.Pipeline('server_saved')\n"
        self_written.write_bytes(server_content)
        mark_self_write(self_written, content=server_content)

        user_content = b"""\
import haute

pipeline = haute.Pipeline("test_pipeline", description="External User Edit")


@pipeline.polars
def distinctive_node():
    pass
"""
        user_edited.write_bytes(user_content)

        fake_changes = [
            (Change.modified, str(self_written)),
            (Change.modified, str(user_edited)),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 1
        call = broadcast_calls[0]
        assert call["source_file"] == "test_pipeline.py"
        assert (
            isinstance(call.get("document_fingerprint"), str)
            and len(call["document_fingerprint"]) > 0
        )
        assert call["document"]["pipeline_description"] == "External User Edit"
        assert any(n["authored_id"] == "distinctive_node" for n in call["document"]["nodes"])

    def test_direct_non_discovered_python_files_are_not_parsed_or_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Only discovery-positive direct .py edits should enter the parser."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        ordinary_file = pipeline_dir / "helper.py"
        ordinary_file.write_text("def helper():\n    return 1\n")
        utility_file = pipeline_dir / "utility" / "helper.py"
        utility_file.parent.mkdir()
        utility_file.write_text("def helper():\n    return 1\n")
        cache_file = pipeline_dir / "__pycache__" / "cached.py"
        cache_file.parent.mkdir()
        cache_file.write_text("def cached():\n    return 1\n")

        fake_changes = [
            (Change.modified, str(ordinary_file)),
            (Change.modified, str(utility_file)),
            (Change.modified, str(cache_file)),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        loaded_paths: list[Path] = []

        def _recording_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            raise AssertionError(f"unexpected document load for {path}")

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.discover_pipelines",
                return_value=[pipeline_dir / "test_pipeline.py"],
            ),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_recording_load,
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert loaded_paths == []
        assert broadcast_calls == []

    def test_direct_discovered_python_file_is_parsed_and_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Direct changes to discovered pipeline files still live-sync."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        fake_changes = [(Change.modified, str(pipeline_file))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        loaded_paths: list[Path] = []
        from haute.routes._helpers import load_pipeline_editor_document as _real_load

        def _recording_load(path: Path, **kwargs: object):
            loaded_paths.append(path)
            return _real_load(path, **kwargs)  # type: ignore[arg-type]

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_recording_load,
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert loaded_paths == [pipeline_file]
        assert [call["type"] for call in broadcast_calls] == [
            "pipeline_document_update",
        ]


# ---------------------------------------------------------------------------
# Phase 3: Server infrastructure tests
# ---------------------------------------------------------------------------


class TestFileWatcherRecoverySidecars:
    def test_parent_sidecar_corruption_then_repair_publishes_status_transition(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        use_project(pipeline_dir)
        sidecar = pipeline_dir / "test_pipeline.haute.json"
        broadcast_calls: list[dict] = []

        async def _fake_awatch(*_dirs, **_kwargs):
            sidecar.write_bytes(b'{"positions":')
            yield [(Change.modified, str(sidecar))]
            await asyncio.sleep(0.02)
            sidecar.write_text(
                '{"positions": {}, "sources": ["live"], "active_source": "live"}',
                encoding="utf-8",
            )
            yield [(Change.modified, str(sidecar))]

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch.object(
                ProjectContext, "pipeline_dir", new_callable=PropertyMock, return_value=pipeline_dir
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):
            asyncio.run(_run_file_watcher_and_drain())

        document_frames = [
            call for call in broadcast_calls if call["type"] == "pipeline_document_update"
        ]
        assert [frame["document"]["load_status"] for frame in document_frames] == [
            "degraded",
            "ready",
        ]
        assert [frame["document"]["source_selection_trusted"] for frame in document_frames] == [
            False,
            True,
        ]
        assert (
            document_frames[0]["document_fingerprint"] != document_frames[1]["document_fingerprint"]
        )

    def test_child_sidecar_has_only_its_manifest_owner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.routes._helpers import pipelines_naming_artifacts

        modules = tmp_path / "modules"
        modules.mkdir()
        child = modules / "shared.py"
        child.write_text(
            'import haute\nsubmodel = haute.Submodel("shared", definition_id="shared", '
            'input_ports=[], output_ports=[], pipeline_dir="..")\n',
            encoding="utf-8",
        )
        owner = tmp_path / "owner.py"
        owner.write_text(
            'import haute\nfrom modules import shared\npipeline = haute.Pipeline("owner")\n'
            'pipeline.submodel(shared.submodel, "shared_one")\n',
            encoding="utf-8",
        )
        unrelated = tmp_path / "unrelated.py"
        unrelated.write_text(
            'import haute\npipeline = haute.Pipeline("unrelated")\n',
            encoding="utf-8",
        )
        use_project(tmp_path)

        owners = pipelines_naming_artifacts(
            [child.with_suffix(".haute.json")], [owner, unrelated], project_root=tmp_path
        )

        assert owners == [owner]


class TestFileWatcherJsonConfig:
    """JSON config changes in config/ re-parse all pipelines."""

    def test_json_config_change_triggers_full_reparse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        # Create a config directory with a JSON file
        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "factors").mkdir(parents=True)
        (config_dir / "factors" / "test.json").write_text('{"key": "value"}')

        json_file = str(config_dir / "factors" / "test.json")
        fake_changes = [(Change.modified, json_file)]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch.object(
                ProjectContext, "pipeline_dir", new_callable=PropertyMock, return_value=pipeline_dir
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["type"] for call in broadcast_calls] == [
            "pipeline_document_update",
        ]

    def test_json_config_without_pipeline_no_broadcast(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If config JSON changes but there are no pipelines, no broadcast."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(tmp_path)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "test.json").write_text("{}")

        fake_changes = [(Change.modified, str(config_dir / "test.json"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch.object(
                ProjectContext, "pipeline_dir", new_callable=PropertyMock, return_value=tmp_path
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_json_config_change_batched_with_py_change_still_reparses_all_pipelines(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config changes affect any pipeline, even when batched with a .py edit."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        other_pipeline = pipeline_dir / "other_pipeline.py"
        other_pipeline.write_text(
            'import haute\n\npipeline = haute.Pipeline("other_pipeline", description="")\n'
        )

        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')

        fake_changes = [
            (Change.modified, str(config_file)),
            (Change.modified, str(pipeline_dir / "test_pipeline.py")),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch.object(
                ProjectContext, "pipeline_dir", new_callable=PropertyMock, return_value=pipeline_dir
            ),
            patch(
                "haute.server.discover_pipelines",
                return_value=[
                    pipeline_dir / "test_pipeline.py",
                    other_pipeline,
                ],
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        source_files = {call["source_file"] for call in broadcast_calls}
        assert source_files == {"test_pipeline.py", "other_pipeline.py"}

    def test_json_config_change_reparses_when_pipeline_file_bytes_are_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config-only edits can change graph shape even when pipeline.py bytes match."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        use_project(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        fp_key = str(py_path.resolve())
        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')
        fake_changes = [(Change.modified, str(config_file))]

        _last_broadcast_fp[fp_key] = hashlib.sha256(py_path.read_bytes()).hexdigest()

        class _FakeDocument:
            load_status = "ready"
            nodes = [object()]

            def model_dump(self, **_kwargs: object) -> dict[str, object]:
                return {"nodes": [{"id": "after-config"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        loaded_paths: list[Path] = []

        def _fake_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            return _FakeDocument()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch.object(
                    ProjectContext,
                    "pipeline_dir",
                    new_callable=PropertyMock,
                    return_value=pipeline_dir,
                ),
                patch("haute.server.discover_pipelines", return_value=[py_path]),
                patch(
                    "haute.server.load_pipeline_editor_document",
                    side_effect=_fake_load,
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        document_updates = [
            call for call in broadcast_calls if call["type"] == "pipeline_document_update"
        ]
        assert loaded_paths == [py_path]
        assert len(document_updates) == 1
        assert document_updates[0]["document"]["nodes"] == [{"id": "after-config"}]

    def test_json_config_change_rebroadcasts_even_when_graph_payload_is_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config bytes can affect execution even when the graph payload is unchanged."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        use_project(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        fp_key = str(py_path.resolve())
        _last_broadcast_fp.pop(fp_key, None)

        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')

        class _FakeDocument:
            load_status = "ready"
            nodes = [object()]

            def model_dump(self, **_kwargs: object) -> dict[str, object]:
                return {"nodes": [{"id": "same-graph"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            config_file.write_text('{"factor": 1.3}')
            yield [(Change.modified, str(config_file))]
            await asyncio.sleep(0.02)
            config_file.write_text('{"factor": 1.4}')
            yield [(Change.modified, str(config_file))]

        loaded_paths: list[Path] = []

        def _fake_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            return _FakeDocument()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch.object(
                    ProjectContext,
                    "pipeline_dir",
                    new_callable=PropertyMock,
                    return_value=pipeline_dir,
                ),
                patch("haute.server.discover_pipelines", return_value=[py_path]),
                patch(
                    "haute.server.load_pipeline_editor_document",
                    side_effect=_fake_load,
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        document_updates = [
            call for call in broadcast_calls if call["type"] == "pipeline_document_update"
        ]
        assert loaded_paths == [py_path, py_path]
        assert len(document_updates) == 2


class TestFileWatcherModuleChange:
    """Module .py changes in modules/ only re-parse importing pipelines."""

    def test_module_change_triggers_importing_pipeline(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        # Create a modules directory
        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir()
        (modules_dir / "helper.py").write_text("def helper(): pass")

        # Mock pipelines_importing_module to return our test pipeline
        test_py = pipeline_dir / "test_pipeline.py"

        fake_changes = [(Change.modified, str(modules_dir / "helper.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipelines_importing_module", return_value=[test_py]),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["type"] for call in broadcast_calls] == [
            "pipeline_document_update",
        ]

    def test_nested_module_change_triggers_importing_pipeline_not_module_parse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A nested module file is dependency code, not a standalone pipeline."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        nested_module = pipeline_dir / "modules" / "pricing" / "helper.py"
        nested_module.parent.mkdir(parents=True)
        nested_module.write_text("def helper(): pass")
        test_py = pipeline_dir / "test_pipeline.py"

        fake_changes = [(Change.modified, str(nested_module))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        loaded_paths: list[Path] = []
        from haute.routes._helpers import load_pipeline_editor_document as _real_load

        def _tracking_load(path: Path, **kwargs: object):
            loaded_paths.append(path)
            return _real_load(path, **kwargs)  # type: ignore[arg-type]

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipelines_importing_module", return_value=[test_py]),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=_tracking_load,
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert loaded_paths == [test_py]
        assert [call["source_file"] for call in broadcast_calls] == [
            "test_pipeline.py",
        ]

    def test_module_change_reparses_when_pipeline_file_bytes_are_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Module-only edits must not be deduped by the importing file's byte hash."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        use_project(pipeline_dir)

        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir(exist_ok=True)
        module_path = modules_dir / "helper.py"
        module_path.write_text("def helper(): return 42")

        test_py = pipeline_dir / "test_pipeline.py"
        fp_key = str(test_py.resolve())
        _last_broadcast_fp[fp_key] = hashlib.sha256(test_py.read_bytes()).hexdigest()

        fake_changes = [(Change.modified, str(module_path))]

        class _FakeDocument:
            load_status = "ready"
            nodes = [object()]

            def model_dump(self, **_kwargs: object) -> dict[str, object]:
                return {"nodes": [{"id": "after-module"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        loaded_paths: list[Path] = []

        def _fake_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            return _FakeDocument()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipelines_importing_module", return_value=[test_py]),
                patch(
                    "haute.server.load_pipeline_editor_document",
                    side_effect=_fake_load,
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        document_updates = [
            call for call in broadcast_calls if call["type"] == "pipeline_document_update"
        ]
        assert loaded_paths == [test_py]
        assert len(document_updates) == 1
        assert document_updates[0]["document"]["nodes"] == [{"id": "after-module"}]

    def test_module_change_rebroadcasts_even_when_graph_payload_is_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Module bytes can affect execution even when the graph payload is unchanged."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        use_project(pipeline_dir)

        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir(exist_ok=True)
        module_path = modules_dir / "helper.py"
        module_path.write_text("def helper(): return 1")

        test_py = pipeline_dir / "test_pipeline.py"
        fp_key = str(test_py.resolve())
        _last_broadcast_fp.pop(fp_key, None)

        class _FakeDocument:
            load_status = "ready"
            nodes = [object()]

            def model_dump(self, **_kwargs: object) -> dict[str, object]:
                return {"nodes": [{"id": "same-graph"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            module_path.write_text("def helper(): return 2")
            yield [(Change.modified, str(module_path))]
            await asyncio.sleep(0.02)
            module_path.write_text("def helper(): return 3")
            yield [(Change.modified, str(module_path))]

        loaded_paths: list[Path] = []

        def _fake_load(path: Path, **_kwargs: object):
            loaded_paths.append(path)
            return _FakeDocument()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipelines_importing_module", return_value=[test_py]),
                patch(
                    "haute.server.load_pipeline_editor_document",
                    side_effect=_fake_load,
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        document_updates = [
            call for call in broadcast_calls if call["type"] == "pipeline_document_update"
        ]
        assert loaded_paths == [test_py, test_py]
        assert len(document_updates) == 2


class TestFileWatcherParseError:
    """Parse error broadcasts a parse_error message."""

    def test_document_load_system_failure_uses_versioned_safe_error(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.load_pipeline_editor_document",
                side_effect=PermissionError("private absolute path"),
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run_file_watcher_and_drain())
            finally:
                loop.close()

        assert broadcast_calls == [
            {
                "type": "parse_error",
                "error": (
                    "Pipeline document could not be loaded. Check the server logs for details."
                ),
                "source_file": "test_pipeline.py",
            }
        ]

    def test_parse_error_then_success_recovers_without_restart(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A fixed file should recover inside the same watcher lifetime."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            Path(py_file).write_text(
                'import haute\npipeline = haute.Pipeline("test_pipeline")\ndef broken(:\n',
                encoding="utf-8",
            )
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)
            Path(py_file).write_text(
                'import haute\npipeline = haute.Pipeline("test_pipeline")\n',
                encoding="utf-8",
            )
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["type"] for call in broadcast_calls] == [
            "pipeline_document_update",
            "pipeline_document_update",
        ]
        documents = [
            call["document"]
            for call in broadcast_calls
            if call["type"] == "pipeline_document_update"
        ]
        assert [document["load_status"] for document in documents] == [
            "source_only",
            "ready",
        ]

    def test_reverting_to_last_good_file_after_degraded_state_rebroadcasts_document(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A degraded document must clear when the user reverts to the last good bytes."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        use_project(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        good_bytes = py_path.read_bytes()
        fp_key = str(py_path.resolve())
        _last_broadcast_fp[fp_key] = hashlib.sha256(good_bytes).hexdigest()

        async def _fake_awatch(*dirs, **kw):
            py_path.write_text("this is not valid python syntax !!!")
            yield [(Change.modified, str(py_path))]
            await asyncio.sleep(0.02)
            py_path.write_bytes(good_bytes)
            yield [(Change.modified, str(py_path))]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        event_types = [call["type"] for call in broadcast_calls]
        assert event_types == [
            "pipeline_document_update",
            "pipeline_document_update",
        ]
        documents = [
            call["document"]
            for call in broadcast_calls
            if call["type"] == "pipeline_document_update"
        ]
        assert [document["load_status"] for document in documents] == [
            "source_only",
            "ready",
        ]


class TestFileWatcherFingerprintDedup:
    """Unchanged graph fingerprint skips re-broadcast."""

    def test_same_fingerprint_skips_second_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            # Allow first flush to complete before yielding second
            await asyncio.sleep(0)
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        # Pre-clear the fingerprint cache
        from haute.server import _last_broadcast_fp

        _last_broadcast_fp.clear()

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        # First change broadcasts, second with same fingerprint is skipped
        document_updates = [c for c in broadcast_calls if c["type"] == "pipeline_document_update"]
        assert len(document_updates) == 1


class TestFileWatcherRecovery:
    def test_crashed_watcher_cancels_pending_debounce_flush(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A crashed watcher must not leave a stale debounce flush running."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")
        published: list[tuple[str, dict[str, object]]] = []

        async def _crashy_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            raise RuntimeError("awatch boom")

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _crashy_awatch),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._DEBOUNCE_SECONDS", 0.05),
            ):
                with pytest.raises(RuntimeError, match="awatch boom"):
                    await _file_watcher()
                await asyncio.sleep(0.1)

        asyncio.run(_run())

        assert published == []

    def test_flush_error_requeues_same_batch_without_new_event(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed flush should retry the current batch even without a second edit."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")
        published: list[tuple[str, dict[str, object]]] = []

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        async def _single_change_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_change_awatch),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server.is_self_write", return_value=False),
                patch(
                    "haute.server.invalidate_pipeline_index",
                    side_effect=[RuntimeError("cache boom"), None],
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()

        asyncio.run(_run())

        assert any(event == "pipeline.document.update" for event, _ in published)

    def test_module_only_flush_does_not_invalidate_pipeline_index(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dependency edit changes graphs, not the pipeline name-to-path index."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir()
        module_file = modules_dir / "shared.py"
        module_file.write_text("VALUE = 2\n")

        async def _single_module_change(*dirs, **kw):
            yield [(Change.modified, str(module_file))]

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_module_change),
                patch("haute.server.is_self_write", return_value=False),
                # The module a pipeline imports: the edit is that pipeline's dependency.
                patch(
                    "haute.server.pipelines_importing_module",
                    return_value=[pipeline_dir / "test_pipeline.py"],
                ),
                patch("haute.server.invalidate_pipeline_index") as invalidate,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                assert invalidate.call_count == 0

        asyncio.run(_run())

    def test_deleted_pipeline_invalidates_index_without_reparse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A deleted pipeline must invalidate the index without a parse attempt."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        deleted_file = pipeline_dir / "deleted_pipeline.py"

        async def _single_deletion(*dirs, **kw):
            yield [(Change.deleted, str(deleted_file))]

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_deletion),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._known_pipeline_paths", return_value={}),
                patch("haute.server.invalidate_pipeline_index") as invalidate,
                patch("haute.server.load_pipeline_editor_document") as load,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                invalidate.assert_called_once_with()
                load.assert_not_called()

        asyncio.run(_run())

    def test_cancelled_flush_requeues_snapshotted_batch(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation after snapshotting must not discard the interrupted change."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        interrupted_file = pipeline_dir / "interrupted.py"
        later_file = pipeline_dir / "later.py"
        interrupted_file.write_text("interrupted = True\n")
        later_file.write_text("later = True\n")
        known = {
            str(interrupted_file.resolve()): interrupted_file,
            str(later_file.resolve()): later_file,
        }

        class _FakeDocument:
            load_status = "ready"
            nodes: list[object] = []

            def model_dump(self, **_kwargs: object) -> dict[str, object]:
                return {"nodes": [], "edges": []}

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                del event, payload

        async def _run() -> list[Path]:
            cancellation_injected = asyncio.Event()
            original_read_bytes = Path.read_bytes
            interrupted_reads = 0

            def _read_bytes(path: Path) -> bytes:
                nonlocal interrupted_reads
                if path.resolve() == interrupted_file.resolve():
                    interrupted_reads += 1
                    if interrupted_reads == 1:
                        cancellation_injected.set()
                        raise asyncio.CancelledError
                return original_read_bytes(path)

            async def _two_changes(*dirs, **kw):
                del dirs, kw
                yield [(Change.modified, str(interrupted_file))]
                await cancellation_injected.wait()
                await asyncio.sleep(0)
                yield [(Change.modified, str(later_file))]

            with (
                patch("watchfiles.awatch", _two_changes),
                patch.object(Path, "read_bytes", _read_bytes),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._known_pipeline_paths", return_value=known),
                patch("haute.server._discovered_pipeline_paths", return_value=known),
                patch("haute.server.invalidate_pipeline_index"),
                patch(
                    "haute.server.load_pipeline_editor_document",
                    return_value=_FakeDocument(),
                ) as load,
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                return [call.args[0] for call in load.call_args_list]

        assert set(asyncio.run(_run())) == {interrupted_file, later_file}

    def test_persistently_failing_flush_stops_after_bounded_retries(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A poisoned change gets bounded batch retries plus one isolated attempt."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _single_change(*dirs, **kw):
            yield [(Change.modified, py_file)]

        async def _run() -> int:
            with (
                patch("watchfiles.awatch", _single_change),
                patch("haute.server.is_self_write", return_value=False),
                patch(
                    "haute.server.invalidate_pipeline_index",
                    side_effect=RuntimeError("persistent cache failure"),
                ) as invalidate,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_RETRY_BASE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_MAX_RETRIES", 2),
            ):
                await _file_watcher()
                return invalidate.call_count

        assert asyncio.run(_run()) == 4

    def test_retry_exhaustion_isolates_poisoned_change_and_processes_healthy_change(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One bad event must not remain queued or suppress a healthy sibling forever."""
        from haute.server import _file_watcher

        use_project(pipeline_dir)
        healthy_file = pipeline_dir / "test_pipeline.py"
        poisoned_file = pipeline_dir / "poisoned.py"
        published: list[tuple[str, dict[str, object]]] = []

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        def _self_write(path: Path, *, consume: bool) -> bool:
            del consume
            if path.name == poisoned_file.name:
                raise RuntimeError("poisoned watcher event")
            return False

        async def _mixed_batch(*dirs, **kw):
            yield [
                (Change.modified, str(poisoned_file)),
                (Change.modified, str(healthy_file)),
            ]

        known = {
            str(poisoned_file.resolve()): poisoned_file,
            str(healthy_file.resolve()): healthy_file,
        }

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _mixed_batch),
                patch("haute.server.is_self_write", _self_write),
                patch("haute.server._known_pipeline_paths", return_value=known),
                patch("haute.server._discovered_pipeline_paths", return_value=known),
                patch("haute.server.invalidate_pipeline_index"),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_RETRY_BASE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_MAX_RETRIES", 1),
            ):
                await _file_watcher()

        asyncio.run(_run())

        assert [event for event, _ in published] == [
            "pipeline.document.update",
        ]

    def test_flush_error_recovery_allows_later_change(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """One unexpected flush failure should not kill later watcher work."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        use_project(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.invalidate_pipeline_index",
                side_effect=[RuntimeError("cache boom"), None],
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert any(call["type"] == "pipeline_document_update" for call in broadcast_calls)

    def test_watcher_forever_restarts_after_crash(self):
        """The outer watcher loop should restart after an unexpected crash."""
        from haute.server import _watcher_forever

        async def _run() -> None:
            restarted = asyncio.Event()
            calls: list[str] = []

            async def _flaky_file_watcher() -> None:
                calls.append("call")
                if len(calls) == 1:
                    raise RuntimeError("watcher boom")
                restarted.set()
                await asyncio.Future()

            with (
                patch("haute.server._file_watcher", _flaky_file_watcher),
                patch("haute.server._WATCHER_RESTART_DELAY_SECONDS", 0),
            ):
                task = asyncio.create_task(_watcher_forever())
                try:
                    await asyncio.wait_for(restarted.wait(), timeout=1.0)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task

            assert len(calls) >= 2

        asyncio.run(_run())

    def test_watcher_forever_returns_when_file_watcher_exits_normally(self) -> None:
        """A clean watcher shutdown should not spin the restart loop."""
        from haute.server import _watcher_forever

        calls = 0

        async def _clean_file_watcher() -> None:
            nonlocal calls
            calls += 1

        with patch("haute.server._file_watcher", _clean_file_watcher):
            asyncio.run(_watcher_forever())

        assert calls == 1

    def test_file_watcher_returns_when_watchfiles_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Without watchfiles, live sync is disabled without crashing startup."""
        from haute.server import _file_watcher

        use_project(tmp_path)

        with patch.dict(sys.modules, {"watchfiles": None}):
            asyncio.run(_file_watcher())


# ---------------------------------------------------------------------------
# A save never round-trips the watcher (specs/server-api, live sync)
# ---------------------------------------------------------------------------


def _input_node(node_id: str, label: str) -> dict:
    return {
        "id": node_id,
        "type": "pipelineNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": label,
            "nodeType": "dataInput",
            "config": _file_input_config("data/input.parquet"),
        },
    }


def _save_two_inputs(client: TestClient, **fields: Any) -> dict:
    """Create ``two.py`` holding two Data Inputs, each with its config file."""
    response = client.post(
        "/api/pipeline/save",
        json={
            "name": "two",
            "graph": {
                "nodes": [_input_node("a", "rates"), _input_node("b", "claims")],
                "edges": [],
            },
            "source_file": "two.py",
            "base_revision": None,
            **fields,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _project_bytes(root: Path) -> dict[str, bytes]:
    """Every project file a save can write, by path (data and dot-folders left out)."""
    return {
        str(path): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
        and path.relative_to(root).parts[0] != "data"
    }


def _shared_child_graph(child_code: str) -> dict:
    """``owner.py``'s graph: one submodel, ``modules/child.py``, its child node running
    *child_code*."""
    child = {
        "id": "child_node",
        "position": {"x": 17.0, "y": 29.0},
        "data": {"label": "child_node", "nodeType": "polars", "config": {"code": child_code}},
    }
    return {
        "pipeline_name": "owner",
        "nodes": [
            {
                "id": "child_instance",
                "type": "submodel",
                "position": {"x": 50.0, "y": 60.0},
                "data": {
                    "label": "child",
                    "nodeType": "submodel",
                    "config": {"definition_id": "child", "alias": "child"},
                },
            }
        ],
        "edges": [],
        "submodels": {
            "child": {
                "definitionId": "child",
                "file": "modules/child.py",
                "inputPorts": [],
                "outputPorts": [],
                "graph": {
                    "nodes": [child],
                    "edges": [],
                    "pipeline_name": "child",
                    "source_file": "modules/child.py",
                },
            }
        },
    }


def _save_owner(client: TestClient, child_code: str, base_revision: str | None) -> dict:
    response = client.post(
        "/api/pipeline/save",
        json={
            "name": "owner",
            "graph": _shared_child_graph(child_code),
            "source_file": "owner.py",
            "base_revision": base_revision,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _write_other_importing_the_child(pipeline_dir: Path) -> None:
    from haute.routes._helpers import invalidate_pipeline_index

    (pipeline_dir / "other.py").write_text(
        "import haute\n\nfrom modules import child\n\n"
        'pipeline = haute.Pipeline("other")\n'
        'pipeline.submodel(child.submodel, "other_child")\n',
        encoding="utf-8",
    )
    invalidate_pipeline_index()


def _frames_of_a_save(
    client: TestClient, child_code: str, base_revision: str
) -> tuple[dict, list[dict]]:
    """A save of ``owner.py``'s answer, and the frames a tab receives for it."""
    with client.websocket_connect("/ws/sync", headers={"origin": "http://localhost"}) as tab:
        assert tab.receive_json()["type"] == "sync_connection"
        saved = _save_owner(client, child_code, base_revision)
        # A probe sent after the save: its answer follows every frame the save sent.
        tab.send_json({"type": "resync", "source_file": "missing.py", "document_schema_version": 1})
        published = []
        while (frame := tab.receive_json())["type"] != "parse_error":
            published.append(frame)
    return saved, published


class TestSaveNeverRoundTripsTheWatcher:
    def test_every_file_a_save_writes_or_retires_publishes_nothing_through_the_watcher(
        self, client: TestClient, pipeline_dir: Path
    ):
        """The changes a real save makes (here one removing a node, so its config file is
        retired) are replayed into the watcher: none is published."""
        from unittest.mock import patch

        from watchfiles import Change

        first = _save_two_inputs(client)
        before = _project_bytes(pipeline_dir)
        second = client.post(
            "/api/pipeline/save",
            json={
                "name": "two",
                "graph": {"nodes": [_input_node("a", "rates")], "edges": []},
                "source_file": "two.py",
                "base_revision": first["source_revision"],
            },
        )
        assert second.status_code == 200, second.text
        after = _project_bytes(pipeline_dir)
        changes = (
            {(Change.added, path) for path in after.keys() - before.keys()}
            | {(Change.deleted, path) for path in before.keys() - after.keys()}
            | {(Change.modified, path) for path in before.keys() & after.keys()}
        )
        changes = {(kind, path) for kind, path in changes if before.get(path) != after.get(path)}
        retired = [path for kind, path in changes if kind == Change.deleted]
        assert any(path.endswith("claims.json") for path in retired)
        assert any(path.endswith("two.haute.json") for _kind, path in changes)

        async def _fake_awatch(*dirs, **kw):
            yield changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict, **_kw: object) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run_file_watcher_and_drain())
            finally:
                loop.close()

        assert broadcast_calls == []

    def test_the_other_tabs_get_the_saved_document_and_the_saving_tab_gets_no_echo(
        self, client: TestClient, pipeline_dir: Path
    ):
        headers = {"origin": "http://localhost"}
        with (
            client.websocket_connect("/ws/sync", headers=headers) as saving_tab,
            client.websocket_connect("/ws/sync", headers=headers) as other_tab,
        ):
            hello = saving_tab.receive_json()
            assert hello["type"] == "sync_connection"
            assert other_tab.receive_json()["type"] == "sync_connection"

            saved = _save_two_inputs(client, connection_id=hello["connection_id"])

            update = other_tab.receive_json()
            assert update["type"] == "pipeline_document_update"
            assert update["source_file"] == "two.py"
            assert update["document_fingerprint"] == saved["document_fingerprint"]
            assert "exclude_connection" not in update
            # The saving tab's next frame answers a probe sent after the save, so no
            # document update reached it before.
            saving_tab.send_json(
                {"type": "resync", "source_file": "missing.py", "document_schema_version": 1}
            )
            assert saving_tab.receive_json()["type"] == "parse_error"

    def test_a_save_also_publishes_the_other_documents_reading_a_submodel_it_wrote(
        self, client: TestClient, pipeline_dir: Path
    ):
        """A save's writes never reach the watcher, so another open pipeline importing the
        submodel the save rewrote is published by the save itself."""
        first = _save_owner(client, "return df", None)
        _write_other_importing_the_child(pipeline_dir)

        _saved, published = _frames_of_a_save(client, "return df.head(1)", first["source_revision"])

        assert [frame["type"] for frame in published] == ["pipeline_document_update"] * 2
        assert [frame["source_file"] for frame in published] == ["owner.py", "other.py"]

    def test_a_document_the_save_cannot_load_is_left_out_and_the_save_stands(
        self, client: TestClient, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes._helpers as helpers

        first = _save_owner(client, "return df", None)
        _write_other_importing_the_child(pipeline_dir)
        load = helpers.load_pipeline_editor_document

        def locked(path: Path, *args: Any, **kwargs: Any) -> Any:
            if Path(path).name == "other.py":
                raise PermissionError("other.py is locked by another program")
            return load(path, *args, **kwargs)

        monkeypatch.setattr(helpers, "load_pipeline_editor_document", locked)

        _saved, published = _frames_of_a_save(client, "return df.head(1)", first["source_revision"])

        assert [frame["source_file"] for frame in published] == ["owner.py"]

    def test_a_discovery_that_fails_after_the_save_leaves_the_save_standing(
        self, client: TestClient, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Finding the other documents reads every candidate pipeline file, one of which
        may be unreadable: the save has committed by then, and answers as it did."""
        import haute.routes._helpers as helpers
        from haute.errors import ConfigError

        first = _save_owner(client, "return df", None)
        _write_other_importing_the_child(pipeline_dir)

        def unreadable() -> list[Path]:
            raise ConfigError("Could not read candidate pipeline file", path="locked.py")

        monkeypatch.setattr(helpers, "discover_pipelines", unreadable)

        saved, published = _frames_of_a_save(client, "return df.head(1)", first["source_revision"])

        assert saved["status"] == "saved"
        assert saved["source_revision"] not in {None, first["source_revision"]}
        assert [frame["source_file"] for frame in published] == ["owner.py"]

    def test_a_connection_of_another_owner_is_never_left_out(self) -> None:
        from haute.routes import _helpers

        class _Socket:
            """A stand-in socket: the registry only keys on it."""

        mine, theirs = _Socket(), _Socket()
        with _helpers.ws_clients_lock:
            saved_clients = set(_helpers.ws_clients)
            _helpers.ws_clients.clear()
        try:
            _helpers.ws_clients_add(mine, connection_id="tab-1", owner="ana@example.com")  # type: ignore[arg-type]
            _helpers.ws_clients_add(theirs, connection_id="tab-2", owner="ben@example.com")  # type: ignore[arg-type]

            same_owner = _helpers.ws_clients_snapshot(
                {"connection_id": "tab-1", "owner": "ana@example.com"}
            )
            other_owner = _helpers.ws_clients_snapshot(
                {"connection_id": "tab-1", "owner": "ben@example.com"}
            )
        finally:
            _helpers.ws_clients_discard(mine)  # type: ignore[arg-type]
            _helpers.ws_clients_discard(theirs)  # type: ignore[arg-type]
            with _helpers.ws_clients_lock:
                _helpers.ws_clients.update(saved_clients)

        assert same_owner == [theirs]
        assert sorted(map(id, other_owner)) == sorted(map(id, [mine, theirs]))

    def test_an_active_source_that_is_not_a_source_is_refused_before_anything_is_written(
        self, client: TestClient, pipeline_dir: Path
    ):
        before = _project_bytes(pipeline_dir)

        response = client.post(
            "/api/pipeline/save",
            json={
                "name": "two",
                "graph": {"nodes": [_input_node("a", "rates")], "edges": []},
                "source_file": "two.py",
                "sources": ["live"],
                "active_source": "test_batch",
                "base_revision": None,
            },
        )

        assert response.status_code == 422
        assert "active_source 'test_batch' is not one of the pipeline's sources" in response.text
        assert _project_bytes(pipeline_dir) == before


# ---------------------------------------------------------------------------
# Module watching follows the configured pipeline directory
# ---------------------------------------------------------------------------

_RATES_MODULE = """\
import haute
import polars as pl

submodel = haute.Submodel(
    "rates",
    definition_id="rates",
    input_ports=[],
    output_ports=[],
    pipeline_dir="..",
)


@submodel.polars
def premium() -> pl.LazyFrame:
    return pl.LazyFrame({{"premium": [{premium}]}})
"""


_IMPORTING_PIPELINE = (
    "import haute\n\nfrom modules import rates\n\n"
    'pipeline = haute.Pipeline("main")\n'
    'pipeline.submodel(rates.submodel, "rates_1")\n'
)


def _module_project(root: Path, folder: str) -> tuple[Path, Path]:
    """A project whose configured pipeline sits in *folder* (``""`` for the root) and
    imports ``modules/rates.py`` beside it; the importer and the module's paths."""
    base = root / folder if folder else root
    (base / "modules").mkdir(parents=True)
    pipeline = base / "main.py"
    pipeline.write_text(_IMPORTING_PIPELINE, encoding="utf-8")
    module = base / "modules" / "rates.py"
    module.write_text(_RATES_MODULE.format(premium=100), encoding="utf-8")
    configured = f"{folder}/main.py" if folder else "main.py"
    (root / "haute.toml").write_text(f'[project]\npipeline = "{configured}"\n', encoding="utf-8")
    return pipeline, module


def _watch(changes: set[tuple[Change, str]]) -> list[dict]:
    """The frames one watcher batch of *changes* broadcasts, discovery and parsing real."""
    from haute.routes._helpers import invalidate_pipeline_index
    from haute.server import _last_broadcast_fp

    invalidate_pipeline_index()
    _last_broadcast_fp.clear()

    async def _fake_awatch(*dirs, **kw):
        yield changes

    frames: list[dict] = []

    async def _capture_broadcast(data: dict, **_kw: object) -> None:
        frames.append(data)

    with (
        patch("watchfiles.awatch", _fake_awatch),
        patch("haute.server.broadcast", _capture_broadcast),
        patch("haute.server._DEBOUNCE_SECONDS", 0),
    ):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run_file_watcher_and_drain())
        finally:
            loop.close()
    return frames


class TestModuleWatchingFollowsTheConfiguredPipeline:
    @pytest.mark.parametrize("folder", ["rating", ""], ids=["nested", "root-level"])
    def test_an_imported_module_edit_refreshes_its_importer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str
    ) -> None:
        use_project(tmp_path)
        pipeline, _ = _module_project(tmp_path, folder)
        module = tmp_path / folder / "modules" / "rates.py"
        module.write_text(_RATES_MODULE.format(premium=150), encoding="utf-8")

        frames = _watch({(Change.modified, str(module))})

        assert [frame["type"] for frame in frames] == ["pipeline_document_update"]
        assert frames[0]["source_file"] == pipeline.relative_to(tmp_path).as_posix()
        document = json.dumps(frames[0]["document"])
        assert "[150]" in document and "[100]" not in document

    @pytest.mark.parametrize("change", [Change.added, Change.deleted], ids=["added", "deleted"])
    def test_an_added_or_deleted_imported_module_refreshes_its_importer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Change
    ) -> None:
        use_project(tmp_path)
        pipeline, module = _module_project(tmp_path, "rating")
        if change == Change.deleted:
            module.unlink()

        frames = _watch({(change, str(module))})

        assert [frame["source_file"] for frame in frames] == ["rating/main.py"]

    def test_an_unrelated_python_file_beside_the_pipeline_is_not_broadcast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_project(tmp_path)
        _module_project(tmp_path, "rating")
        scratch = tmp_path / "rating" / "modules" / "scratch.py"
        scratch.write_text("x = 1\n", encoding="utf-8")

        assert _watch({(Change.modified, str(scratch))}) == []

    def test_the_server_s_own_write_of_an_imported_module_is_not_broadcast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute.routes._helpers import mark_self_write

        use_project(tmp_path)
        _module_project(tmp_path, "rating")
        module = tmp_path / "rating" / "modules" / "rates.py"
        content = _RATES_MODULE.format(premium=175).encode("utf-8")
        mark_self_write(module, content=content)
        module.write_bytes(content)

        assert _watch({(Change.modified, str(module))}) == []
