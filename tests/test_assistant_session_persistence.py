"""Session persistence: assistant chat history survives server restarts.

The server is a locally-run distribution vehicle, restarted constantly, so
committed turns write through to ``.haute/assistant/sessions/<id>.json`` and a
fresh process revives a session the browser still remembers.  Memory remains
the runtime authority; files follow the `_git_state` posture — unreadable or
invalid state is a logged warning treated as absent, never a crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog.testing

from haute.assistant._session import MAX_PERSISTED_SESSIONS, SessionStore


def _store(tmp_path: Path, **kwargs: object) -> SessionStore:
    return SessionStore(storage_dir=lambda: tmp_path / "sessions", **kwargs)  # type: ignore[arg-type]


def _turn(text: str = "hi", reply: str = "hello") -> dict:
    return {
        "messages": [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]
    }


class TestWriteThrough:
    def test_create_persists_a_session_file(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        data = json.loads(
            (tmp_path / "sessions" / f"{session.id}.json").read_text(encoding="utf-8")
        )
        assert data["id"] == session.id
        assert data["source_file"] == "rating/main.py"
        assert data["history"] == []

    def test_append_persists_committed_turns(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        store.append(session, _turn("question", "answer"))
        data = json.loads(
            (tmp_path / "sessions" / f"{session.id}.json").read_text(encoding="utf-8")
        )
        assert len(data["history"]) == 1
        contents = [m["content"] for m in data["history"][0]["messages"]]
        assert contents == ["question", "answer"]

    def test_no_storage_dir_means_no_files(self, tmp_path: Path):
        store = SessionStore()
        session = store.create(tmp_path / "main.py")
        store.append(session, _turn())
        assert list(tmp_path.iterdir()) == []


class TestRevival:
    def test_restart_revives_a_session_from_disk(self, tmp_path: Path):
        first = _store(tmp_path)
        session = first.create("rating/main.py")
        first.append(session, _turn())

        second = _store(tmp_path)  # simulated server restart
        revived = second.lookup(session.id)
        assert revived is not None
        assert revived.id == session.id
        assert revived.source_file == "rating/main.py"
        assert [m.content for m in revived.history[0].messages] == ["hi", "hello"]
        assert not revived.lock.locked()

    def test_lru_eviction_is_invisible_with_persistence(self, tmp_path: Path):
        store = _store(tmp_path, max_live_sessions=1)
        first = store.create("rating/main.py")
        store.append(first, _turn())
        store.create("rating/main.py")  # evicts the idle first session
        assert first.id not in store

        revived = store.lookup(first.id)
        assert revived is not None
        assert [m.content for m in revived.history[0].messages] == ["hi", "hello"]

    def test_revived_session_appends_and_repersists(self, tmp_path: Path):
        first = _store(tmp_path)
        session = first.create("rating/main.py")
        first.append(session, _turn("one", "1"))

        second = _store(tmp_path)
        revived = second.lookup(session.id)
        assert revived is not None
        second.append(revived, _turn("two", "2"))

        third = _store(tmp_path)
        again = third.lookup(session.id)
        assert again is not None
        assert len(again.history) == 2


class TestCorruption:
    def _plant(self, tmp_path: Path, session_id: str, text: str) -> None:
        base = tmp_path / "sessions"
        base.mkdir(parents=True, exist_ok=True)
        (base / f"{session_id}.json").write_text(text, encoding="utf-8")

    def test_corrupt_json_is_a_logged_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        session_id = "a" * 32
        self._plant(tmp_path, session_id, "{not json")
        with structlog.testing.capture_logs() as logs:
            assert store.lookup(session_id) is None
        assert any(e["event"] == "assistant_session_unreadable" for e in logs)

    def test_invalid_history_shape_is_a_logged_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        session_id = "b" * 32
        payload = {
            "id": session_id,
            "source_file": "rating/main.py",
            # A turn must begin with a user message; this one violates that.
            "history": [{"messages": [{"role": "assistant", "content": "x"}]}],
            "created_at": 1.0,
            "last_used": 2.0,
        }
        self._plant(tmp_path, session_id, json.dumps(payload))
        with structlog.testing.capture_logs() as logs:
            assert store.lookup(session_id) is None
        assert any(e["event"] == "assistant_session_unreadable" for e in logs)

    def test_mismatched_id_inside_file_is_a_logged_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        session_id = "c" * 32
        payload = {
            "id": "d" * 32,
            "source_file": "rating/main.py",
            "history": [],
            "created_at": 1.0,
            "last_used": 2.0,
        }
        self._plant(tmp_path, session_id, json.dumps(payload))
        with structlog.testing.capture_logs() as logs:
            assert store.lookup(session_id) is None
        assert any(e["event"] == "assistant_session_unreadable" for e in logs)

    def test_non_hex_session_ids_never_touch_the_filesystem(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.lookup("../../evil") is None
        assert store.lookup("A" * 32) is None  # uppercase is not a uuid4 hex
        assert not (tmp_path / "sessions").exists()


class TestBounds:
    def test_persisted_files_are_pruned_oldest_first(self, tmp_path: Path):
        store = _store(tmp_path, max_persisted_sessions=2)
        first = store.create("rating/main.py")
        second = store.create("rating/main.py")
        base = tmp_path / "sessions"
        os.utime(base / f"{first.id}.json", (1_000, 1_000))
        os.utime(base / f"{second.id}.json", (2_000, 2_000))

        third = store.create("rating/main.py")
        remaining = {p.stem for p in base.glob("*.json")}
        assert remaining == {second.id, third.id}

    def test_default_persisted_cap_is_documented_constant(self):
        assert MAX_PERSISTED_SESSIONS == 100

    def test_under_cap_creation_never_deletes_existing_files(self, tmp_path: Path):
        """Regression: `others[:excess]` with a negative excess slices from the
        END, so an under-cap create deleted the oldest sessions — silent data
        loss that grew worse the closer the store got to the cap (97 of 98
        files at the default cap of 100)."""
        store = _store(tmp_path, max_persisted_sessions=5)
        existing = [store.create("rating/main.py") for _ in range(3)]
        base = tmp_path / "sessions"
        for stamp, session in enumerate(existing, start=1):
            os.utime(base / f"{session.id}.json", (stamp * 1_000, stamp * 1_000))

        newest = store.create("rating/main.py")
        remaining = {p.stem for p in base.glob("*.json")}
        assert remaining == {session.id for session in existing} | {newest.id}

    def test_creation_prunes_abandoned_atomic_write_temp_files(self, tmp_path: Path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        abandoned = sessions / f"{'0' * 32}.json.tmp"
        unrelated = sessions / "notes.json.tmp"
        abandoned.write_text("partial", encoding="utf-8")
        unrelated.write_text("leave me", encoding="utf-8")

        _store(tmp_path).create("main.py")

        assert not abandoned.exists()
        assert unrelated.exists()

    def test_tool_error_flag_survives_persistence_and_revival(self, tmp_path: Path):
        first = _store(tmp_path)
        session = first.create("main.py")
        first.append(
            session,
            {
                "messages": [
                    {"role": "user", "content": "inspect"},
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": "t1", "name": "get_pipeline", "arguments": {}}],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "t1",
                        "name": "get_pipeline",
                        "content": {"error": {"code": "failed"}},
                        "is_error": True,
                    },
                ]
            },
        )

        restarted = _store(tmp_path)
        revived = restarted.lookup(session.id)

        assert revived is not None
        tool_message = next(
            message for message in restarted.history_window(revived) if message["role"] == "tool"
        )
        assert tool_message["is_error"] is True


class TestDegradation:
    def test_persist_failure_warns_and_keeps_the_session_live(self, tmp_path: Path):
        (tmp_path / "sessions").write_text("a file where the dir should be")
        store = _store(tmp_path)
        with structlog.testing.capture_logs() as logs:
            session = store.create("rating/main.py")
            store.append(session, _turn())
        assert any(e["event"] == "assistant_session_persist_failed" for e in logs)
        assert store.lookup(session.id) is session
        assert len(session.history) == 1
