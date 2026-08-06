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

import pytest
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

    def test_persisted_tool_payload_is_redacted_but_attribution_survives(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("main.py")
        store.append(
            session,
            {
                "messages": [
                    {"role": "user", "content": "inspect"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "t1",
                                "name": "get_node_config",
                                "arguments": {"node": "rating"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "t1",
                        "name": "get_node_config",
                        "content": {
                            "config": {"customer_name": "Ada", "api_token": "secret-value"},
                            "base_revision": "a" * 64,
                        },
                        "is_error": False,
                    },
                ]
            },
        )

        raw = (tmp_path / "sessions" / f"{session.id}.json").read_text(encoding="utf-8")
        assert "Ada" not in raw
        assert "secret-value" not in raw
        assert "payload_sha256" not in raw
        data = json.loads(raw)
        assistant = next(
            message
            for turn in data["history"]
            for message in turn["messages"]
            if message["role"] == "assistant"
        )
        tool = next(
            message
            for turn in data["history"]
            for message in turn["messages"]
            if message["role"] == "tool"
        )
        assert assistant["tool_calls"][0]["arguments"] == {"redacted": True}
        assert tool["content"] == {
            "redacted": True,
            "base_revision": "a" * 64,
        }

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

    def test_persisted_tool_error_retains_only_approved_diagnostic_evidence(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("main.py")
        store.append(
            session,
            {
                "messages": [
                    {"role": "user", "content": "edit"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "t1",
                                "name": "dry_run_graph_edits",
                                "arguments": {"ops": [{"op": "secret-op"}]},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "t1",
                        "name": "dry_run_graph_edits",
                        "content": {
                            "error": {
                                "code": "invalid_request",
                                "message": "secret-value",
                                "validation_path": "dry_run_graph_edits.ops[0].op",
                                "validation_reason": "unsupported_discriminator",
                                "value": "secret-op",
                            }
                        },
                        "is_error": True,
                    },
                ]
            },
        )

        raw = (tmp_path / "sessions" / f"{session.id}.json").read_text(encoding="utf-8")
        assert "secret-value" not in raw
        assert "secret-op" not in raw
        assert "payload_sha256" not in raw
        data = json.loads(raw)
        tool = data["history"][0]["messages"][2]
        assert tool["content"] == {
            "redacted": True,
            "error": {
                "code": "invalid_request",
                "validation_path": "dry_run_graph_edits.ops[0].op",
                "validation_reason": "unsupported_discriminator",
            },
        }

    def test_credential_assignments_and_provider_keys_are_redacted_from_text(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "provider-secret-canary")
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        store.append(
            session,
            _turn(
                "api_token=inline-secret",
                "The observed key was provider-secret-canary.",
            ),
        )

        raw = (tmp_path / "sessions" / f"{session.id}.json").read_text(encoding="utf-8")
        assert "inline-secret" not in raw
        assert "provider-secret-canary" not in raw
        assert "<redacted>" in raw

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

    def test_controller_continuation_survives_restart(self, tmp_path: Path):
        first = _store(tmp_path)
        session = first.create("main.py")
        first.append(
            session,
            {
                "messages": [
                    {"role": "user", "content": "add a pipeline"},
                    {"role": "assistant", "content": "Let me inspect the example."},
                    {
                        "role": "controller",
                        "content": "Continue the mutation workflow.",
                    },
                    {"role": "assistant", "content": "BLOCKED: invalid request."},
                ]
            },
        )

        revived = _store(tmp_path).lookup(session.id)
        assert revived is not None
        assert [message.role for message in revived.history[0].messages] == [
            "user",
            "assistant",
            "controller",
            "assistant",
        ]

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

    def test_mismatched_disk_resume_does_not_promote_or_evict_live_sessions(self, tmp_path: Path):
        persisted = _store(tmp_path).create("persisted.py")
        store = _store(tmp_path, max_live_sessions=2)
        first = store.create("first.py")
        second = store.create("second.py")

        assert store.resume(persisted.id, "other.py") is None
        assert persisted.id not in store
        store.create("third.py")

        assert first.id not in store
        assert second.id in store
        assert persisted.id not in store


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

    def test_symlinked_session_file_is_a_logged_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        session_id = "e" * 32
        outside = tmp_path / "outside.json"
        outside.write_text(
            json.dumps(
                {
                    "id": session_id,
                    "source_file": "main.py",
                    "history": [],
                    "created_at": 1.0,
                    "last_used": 1.0,
                }
            ),
            encoding="utf-8",
        )
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        try:
            os.symlink(outside, sessions / f"{session_id}.json")
        except OSError:
            pytest.skip("file symlinks are unavailable on this platform")

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

    def test_tool_message_without_error_flag_is_a_logged_miss(self, tmp_path: Path):
        store = _store(tmp_path)
        session_id = "e" * 32
        payload = {
            "id": session_id,
            "source_file": "rating/main.py",
            "history": [
                {
                    "messages": [
                        {"role": "user", "content": "inspect"},
                        {
                            "role": "tool",
                            "tool_call_id": "t1",
                            "name": "get_pipeline",
                            "content": {"error": {"code": "failed"}},
                        },
                    ]
                }
            ],
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


class TestSessionListing:
    """The chat list is served from persisted state, not from the browser.

    A client-remembered "last session" id is what made the panel open blank
    and then surface an earlier transcript mid-conversation; the store is now
    the single record of which conversations exist.
    """

    def test_lists_this_pipeline_s_conversations_most_recent_first(self, tmp_path: Path):
        clock = iter(float(stamp) for stamp in range(1, 7))
        store = _store(tmp_path, clock=clock.__next__)
        older = store.create("rating/main.py")
        store.append(older, _turn("first question"))
        newer = store.create("rating/main.py")
        store.append(newer, _turn("second question"))
        other_pipeline = store.create("other/main.py")
        store.append(other_pipeline, _turn("elsewhere"))

        listed = store.list_sessions("rating/main.py")

        assert [item.session_id for item in listed] == [newer.id, older.id]
        assert [item.title for item in listed] == ["second question", "first question"]
        assert all(item.message_count == 2 for item in listed)

    def test_empty_conversations_are_not_listed(self, tmp_path: Path):
        """`create` persists immediately, so an abandoned new chat exists on
        disk with nothing to show and no title to show it under."""

        store = _store(tmp_path)
        store.create("rating/main.py")

        assert store.list_sessions("rating/main.py") == ()

    def test_survives_a_restart_without_reviving_every_session(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        store.append(session, _turn("persisted question"))

        restarted = _store(tmp_path)
        listed = restarted.list_sessions("rating/main.py")

        assert [item.session_id for item in listed] == [session.id]
        assert listed[0].title == "persisted question"
        # Listing must not pull conversations into memory: they share the LRU
        # bound with live sessions and would evict them.
        assert len(restarted) == 0

    def test_a_long_first_message_is_bounded_and_collapsed(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        store.append(session, _turn("word  \n  spaced " + "x" * 200))

        (listed,) = store.list_sessions("rating/main.py")

        assert listed.title.startswith("word spaced ")
        assert len(listed.title) <= 80
        assert listed.title.endswith("…")

    def test_an_unreadable_session_file_is_skipped_with_a_warning(self, tmp_path: Path):
        store = _store(tmp_path)
        session = store.create("rating/main.py")
        store.append(session, _turn("readable"))
        (tmp_path / "sessions" / f"{'0' * 32}.json").write_text("{not json")

        with structlog.testing.capture_logs() as logs:
            listed = store.list_sessions("rating/main.py")

        assert [item.session_id for item in listed] == [session.id]
        assert any(e["event"] == "assistant_session_list_unreadable" for e in logs)

    def test_valid_json_that_cannot_be_revived_is_not_advertised(self, tmp_path: Path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        valid_turn = _turn("should not appear")
        payloads = (
            {
                "id": "f" * 32,
                "source_file": "rating/main.py",
                "history": [valid_turn],
                "created_at": 1.0,
                "last_used": 2.0,
            },
            {
                "id": "1" * 32,
                "source_file": "rating/main.py",
                "history": [{"messages": [{"role": "assistant", "content": "invalid"}]}],
                "created_at": 1.0,
                "last_used": 2.0,
            },
            {
                "id": "2" * 32,
                "source_file": "rating/main.py",
                "history": [valid_turn],
                "created_at": 1.0,
                "last_used": float("nan"),
            },
        )
        file_ids = ("0" * 32, "1" * 32, "2" * 32)
        for file_id, payload in zip(file_ids, payloads, strict=True):
            (sessions / f"{file_id}.json").write_text(json.dumps(payload), encoding="utf-8")

        with structlog.testing.capture_logs() as logs:
            listed = _store(tmp_path).list_sessions("rating/main.py")

        assert listed == ()
        assert sum(e["event"] == "assistant_session_list_unreadable" for e in logs) == 3
