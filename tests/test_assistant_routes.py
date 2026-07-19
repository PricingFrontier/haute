"""Tests for the assistant HTTP surface (``haute.routes.assistant``).

Spec: docs/specs/assistant/low-level.md — Control flow (Status / Session
create / Message turn) and Error handling.  Routes are thin: readiness and
sessions come from ``haute.assistant``; the turn streams as SSE.

Seams pinned for batch 9:

- ``haute.routes.assistant.session_store`` — the module-level ``SessionStore``
  singleton (tests reset it per test).
- ``haute.routes.assistant._provider_factory(config)`` — builds the provider
  for a turn; tests patch it to inject a scripted fake.
- Endpoints: ``GET /api/assistant/status`` → ``AssistantStatusResponse``;
  ``POST /api/assistant/session`` ``{pipeline?}`` → ``{"session_id": ...}``
  (unknown pipeline name → 404); ``POST /api/assistant/message``
  ``{session_id, message}`` → ``text/event-stream`` of ``data:``-framed
  ``AssistantStreamEvent`` JSON (unconfigured → 400 naming the reason;
  unknown session → 404; concurrent turn → 409; provider failure before
  the stream opens → 502 with a sanitized message).

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute.assistant._providers import TextDelta, TurnStop
from haute.assistant._session import SessionStore

pytestmark = pytest.mark.usefixtures("project_root")

_PIPELINE = 'import haute\npipeline = haute.Pipeline("main", description="d")\n'


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(_PIPELINE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def configured(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (project_root / "haute.toml").write_text(
        '[assistant]\nprovider = "anthropic"\nmodel = "test-model"\n', encoding="utf-8"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    return project_root


@pytest.fixture()
def client(project_root: Path) -> TestClient:
    from haute.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    """A fresh session store per test, swapped into the route module."""

    import haute.routes.assistant as assistant_routes

    fresh = SessionStore()
    monkeypatch.setattr(assistant_routes, "session_store", fresh)
    return fresh


class _ScriptedProvider:
    def __init__(self, events: list[object]) -> None:
        self._events = list(events)

    async def stream_turn(self, *, system, messages, tools):
        for event in self._events:
            if isinstance(event, Exception):
                raise event
            yield event


def _patch_provider(monkeypatch: pytest.MonkeyPatch, events: list[object]) -> None:
    import haute.routes.assistant as assistant_routes

    monkeypatch.setattr(
        assistant_routes, "_provider_factory", lambda config: _ScriptedProvider(events)
    )


def _sse_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        text = line.decode() if isinstance(line, bytes) else line
        if text.startswith("data:"):
            events.append(json.loads(text[len("data:") :].strip()))
    return events


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_unconfigured_reports_reason(self, client: TestClient):
        body = client.get("/api/assistant/status").json()
        assert body["configured"] is False
        assert "[assistant]" in body["reason"] or "assistant" in body["reason"]

    def test_configured_reports_echoes_and_mutation_gate(
        self, client: TestClient, configured: Path
    ):
        body = client.get("/api/assistant/status").json()
        assert body["configured"] is True
        assert body["reason"] is None
        assert (body["provider"], body["model"]) == ("anthropic", "test-model")
        # tmp project has no recorded git working branch -> mutations disabled
        assert body["mutations_enabled"] is False
        assert body["mutations_reason"]


# ---------------------------------------------------------------------------
# Session create
# ---------------------------------------------------------------------------


class TestSessionCreate:
    def test_creates_session_for_default_pipeline(self, client: TestClient, store: SessionStore):
        response = client.post("/api/assistant/session", json={})
        assert response.status_code == 200, response.text
        session_id = response.json()["session_id"]
        assert store.lookup(session_id) is not None

    def test_unknown_pipeline_name_is_404(self, client: TestClient, store: SessionStore):
        response = client.post("/api/assistant/session", json={"pipeline": "nope"})
        assert response.status_code == 404


def _persistent_store(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> SessionStore:
    """Swap in a store persisting to the project's `.haute/` sessions dir."""

    import haute.routes.assistant as assistant_routes

    fresh = SessionStore(storage_dir=lambda: project_root / ".haute" / "assistant" / "sessions")
    monkeypatch.setattr(assistant_routes, "session_store", fresh)
    return fresh


class TestSessionResume:
    """POST /session with a prior session_id: resume is an offer, never an error."""

    def test_resume_after_restart_returns_same_id_and_transcript(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        store = _persistent_store(monkeypatch, project_root)
        session_id = client.post("/api/assistant/session", json={}).json()["session_id"]
        store.append(
            session_id,
            {
                "messages": [
                    {"role": "user", "content": "add a node"},
                    {
                        "role": "assistant",
                        "content": "Working on it",
                        "tool_calls": [
                            {"id": "c1", "name": "apply_graph_edits", "arguments": {"ops": []}}
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "c1",
                        "name": "apply_graph_edits",
                        "content": {"applied": 1},
                    },
                    {"role": "assistant", "content": "Done"},
                ]
            },
        )

        _persistent_store(monkeypatch, project_root)  # simulated server restart
        resumed = client.post("/api/assistant/session", json={"session_id": session_id})
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["session_id"] == session_id
        kinds = [entry["kind"] for entry in body["history"]]
        assert kinds == ["user", "assistant", "tool", "assistant"]
        assert body["history"][0]["text"] == "add a node"
        assert body["history"][1]["text"] == "Working on it"
        tool = body["history"][2]
        assert tool["name"] == "apply_graph_edits"
        assert "applied" in tool["summary"]
        assert tool["is_error"] is False
        assert body["history"][3]["text"] == "Done"

    def test_tool_error_entries_carry_the_error_flag_and_message(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        store = _persistent_store(monkeypatch, project_root)
        session_id = client.post("/api/assistant/session", json={}).json()["session_id"]
        store.append(
            session_id,
            {
                "messages": [
                    {"role": "user", "content": "break"},
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": "c1", "name": "get_node_schema", "arguments": {}}],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "c1",
                        "name": "get_node_schema",
                        "content": {"error": {"code": "unknown_node", "message": "No node x"}},
                    },
                ]
            },
        )
        body = client.post("/api/assistant/session", json={"session_id": session_id}).json()
        tool = body["history"][-1]
        assert tool["kind"] == "tool"
        assert tool["is_error"] is True
        assert tool["summary"] == "No node x"

    def test_unknown_session_id_yields_a_fresh_session(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _persistent_store(monkeypatch, project_root)
        body = client.post("/api/assistant/session", json={"session_id": "f" * 32}).json()
        assert body["session_id"] != "f" * 32
        assert body["history"] == []

    def test_session_bound_to_another_pipeline_yields_a_fresh_session(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (project_root / "other.py").write_text(
            'import haute\npipeline = haute.Pipeline("second", description="d")\n',
            encoding="utf-8",
        )
        # The name→path index is process-cached (startup/watcher rebuild it in
        # production); tests poke it directly so this cwd's pipelines are seen.
        from haute.routes._helpers import invalidate_pipeline_index

        invalidate_pipeline_index()
        _persistent_store(monkeypatch, project_root)
        session_id = client.post("/api/assistant/session", json={}).json()["session_id"]

        body = client.post(
            "/api/assistant/session",
            json={"pipeline": "second", "session_id": session_id},
        ).json()
        assert body["session_id"] != session_id
        assert body["history"] == []


# ---------------------------------------------------------------------------
# Message turn
# ---------------------------------------------------------------------------


class TestMessageTurn:
    def _session(self, client: TestClient) -> str:
        return client.post("/api/assistant/session", json={}).json()["session_id"]

    def test_unconfigured_send_is_400_naming_the_reason(
        self, client: TestClient, store: SessionStore
    ):
        session_id = self._session(client)
        response = client.post(
            "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
        )
        assert response.status_code == 400
        assert "assistant" in response.json()["detail"].lower()

    def test_unknown_session_is_404(
        self, client: TestClient, store: SessionStore, configured: Path
    ):
        response = client.post(
            "/api/assistant/message", json={"session_id": "missing", "message": "hi"}
        )
        assert response.status_code == 404

    def test_concurrent_turn_is_409(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio

        session_id = self._session(client)
        session = store.lookup(session_id)
        assert session is not None
        _patch_provider(monkeypatch, [TextDelta("x")])

        # Acquire the one-turn lock as a running turn would; the route must
        # reject rather than queue.  asyncio.Lock state survives the helper
        # loop that acquired it.
        asyncio.run(session.lock.acquire())
        try:
            response = client.post(
                "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
            )
            assert response.status_code == 409
        finally:
            session.lock.release()

    def test_happy_path_streams_sse_events(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.assistant._providers import ProviderUsage

        session_id = self._session(client)
        _patch_provider(
            monkeypatch,
            [TextDelta("Hel"), TextDelta("lo"), TurnStop("end", ProviderUsage(3, 4))],
        )
        with client.stream(
            "POST", "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(response)

        types = [event["type"] for event in events]
        assert types[:2] == ["text_delta", "text_delta"]
        assert types[-1] == "completed"
        assert events[-1]["usage"] == {"input_tokens": 3, "output_tokens": 4}

    def test_provider_failure_before_stream_is_502_sanitized(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.routes.assistant as assistant_routes

        def broken_factory(config):
            raise RuntimeError("internal secret detail xyzzy")

        monkeypatch.setattr(assistant_routes, "_provider_factory", broken_factory)
        session_id = self._session(client)
        response = client.post(
            "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
        )
        assert response.status_code >= 500
        assert "xyzzy" not in response.text


class TestRouteEdges:
    def test_session_create_with_explicit_pipeline_name(
        self, client: TestClient, store: SessionStore
    ):
        response = client.post("/api/assistant/session", json={"pipeline": "main"})
        assert response.status_code == 200, response.text
        assert store.lookup(response.json()["session_id"]) is not None

    def test_status_translates_unexpected_readiness_failure_to_sanitized_500(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.assistant as assistant_routes

        def broken_readiness():
            raise RuntimeError("internal secret zzyzx")

        monkeypatch.setattr(assistant_routes, "assistant_readiness", broken_readiness)
        response = client.get("/api/assistant/status")
        assert response.status_code == 500
        assert "zzyzx" not in response.text

    def test_message_maps_config_error_to_400(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import haute.routes.assistant as assistant_routes
        from haute.errors import ConfigError

        session_id = client.post("/api/assistant/session", json={}).json()["session_id"]

        def broken_resolve():
            raise ConfigError("assistant config went away")

        monkeypatch.setattr(assistant_routes, "resolve_assistant_config", broken_resolve)
        response = client.post(
            "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
        )
        assert response.status_code == 400
        assert "config" in response.json()["detail"].lower()


class TestProviderFactory:
    def test_builds_each_configured_adapter(self):
        from haute.assistant._config import AssistantConfig
        from haute.assistant._providers import AnthropicProvider, OpenAIProvider
        from haute.routes.assistant import _provider_factory

        anthropic_config = AssistantConfig(
            provider="anthropic", model="m", base_url=None, api_key="k", max_output_tokens=8192
        )
        assert isinstance(_provider_factory(anthropic_config), AnthropicProvider)
        openai_config = AssistantConfig(
            provider="openai",
            model="m",
            base_url="https://dbx",
            api_key="k",
            max_output_tokens=8192,
        )
        assert isinstance(_provider_factory(openai_config), OpenAIProvider)

    def test_no_pipeline_in_project_is_404(
        self, client: TestClient, store: SessionStore, project_root: Path
    ):
        (project_root / "main.py").unlink()
        response = client.post("/api/assistant/session", json={})
        assert response.status_code == 404


class TestTurnReservation:
    """Regression: the one-turn lock is reserved atomically BEFORE the
    route's awaited pre-work, so a concurrent send gets its 409 pre-stream
    instead of an unhandled mid-stream ConcurrentTurnError."""

    async def test_concurrent_sends_get_exactly_one_stream_and_one_409(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import asyncio
        import time as time_module

        from fastapi import HTTPException

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id

        real_parse = assistant_routes.parse_pipeline_to_graph

        def slow_parse(path):
            time_module.sleep(0.2)  # runs in to_thread — widens the await window
            return real_parse(path)

        monkeypatch.setattr(assistant_routes, "parse_pipeline_to_graph", slow_parse)
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )

        body = AssistantMessageRequest(session_id=session_id, message="hi")
        first_task = asyncio.create_task(assistant_routes.post_assistant_message(body))
        await asyncio.sleep(0.05)  # first is inside the slow parse, lock held

        with pytest.raises(HTTPException) as excinfo:
            await assistant_routes.post_assistant_message(body)
        assert excinfo.value.status_code == 409

        response = await first_task
        chunks = [chunk async for chunk in response.body_iterator]
        assert any("completed" in chunk for chunk in chunks)
        # After the stream finishes the lock is free again.
        session = store.lookup(session_id)
        assert session is not None and not session.lock.locked()

    async def test_pre_stream_failure_after_reservation_releases_the_lock(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from fastapi import HTTPException

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )

        def broken_prompt(**kwargs):
            raise RuntimeError("prompt assembly failed")

        monkeypatch.setattr(assistant_routes._loop, "build_system_prompt", broken_prompt)
        body = AssistantMessageRequest(session_id=session_id, message="hi")
        with pytest.raises(HTTPException) as excinfo:
            await assistant_routes.post_assistant_message(body)
        assert excinfo.value.status_code == 500

        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked(), "a pre-stream failure must not leak the reservation"


class TestReservationNeverLeaks:
    async def test_disconnect_before_body_iteration_releases_the_lock(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Regression: a client that aborts before the ASGI layer starts the
        body iterator must not leave the session locked forever."""

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )

        body = AssistantMessageRequest(session_id=session_id, message="hi")
        response = await assistant_routes.post_assistant_message(body)
        session = store.lookup(session_id)
        assert session is not None
        assert session.lock.locked(), "the reservation is held while the response is pending"

        async def receive():
            return {"type": "http.disconnect"}

        async def failing_send(message):
            raise RuntimeError("client went away before headers")

        with pytest.raises(RuntimeError):
            await response({"type": "http"}, receive, failing_send)

        assert not session.lock.locked(), "the response lifecycle must release the reservation"
        # And the session accepts a new turn afterwards.
        second = await assistant_routes.post_assistant_message(body)
        chunks = [chunk async for chunk in second.body_iterator]
        assert any("completed" in chunk for chunk in chunks)
        assert not session.lock.locked()


class TestMidStreamDisconnectTeardown:
    async def test_send_failure_mid_stream_closes_the_turn_before_freeing_the_session(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Regression: a send() failure after events have flowed must drive
        the suspended turn's teardown (history appended, provider closed)
        BEFORE the reservation frees the session — never leave a zombie turn
        behind an unlocked session."""

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [
                    TextDelta("partial"),
                    TextDelta("never delivered"),
                    TurnStop("end", ProviderUsage(1, 1)),
                ]
            ),
        )

        body = AssistantMessageRequest(session_id=session_id, message="hi")
        response = await assistant_routes.post_assistant_message(body)

        sent: list[dict] = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and b"partial" in message.get("body", b""):
                raise RuntimeError("client dropped mid-stream")

        with pytest.raises(RuntimeError):
            await response({"type": "http"}, receive, send)

        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked(), "reservation must be free after teardown"
        assert len(session.history) == 1, "the interrupted turn's history must have landed"
        contents = [message.content for message in session.history[0].messages]
        assert "hi" in contents, "the user message is part of the persisted turn"

        # The session is immediately usable for a clean follow-up turn.
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [TextDelta("fresh"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )
        follow_up = await assistant_routes.post_assistant_message(body)
        chunks = [chunk async for chunk in follow_up.body_iterator]
        assert any("completed" in chunk for chunk in chunks)
        assert len(store.lookup(session_id).history) == 2
