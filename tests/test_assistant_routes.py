"""Tests for the assistant HTTP surface (``haute.routes.assistant``).

Spec: specs/assistant/low-level.md — Control flow (Status / Session
create / Message turn) and Error handling.  Routes are thin: readiness and
sessions come from ``haute.assistant``; the turn streams as SSE.

Seams pinned for batch 9:

- ``haute.routes.assistant.session_store`` — the module-level ``SessionStore``
  singleton (tests reset it per test).
- ``haute.routes.assistant._provider_factory(config)`` — builds the provider
  for a turn; tests patch it to inject a scripted fake.
- Endpoints: ``GET /api/assistant/status`` → ``AssistantStatusResponse``;
  ``POST /api/assistant/session`` ``{source_file, session_id?}`` →
  ``{"session_id": ..., "source_file": ...}`` (a source file that is not a
  discovered pipeline → 404); ``POST /api/assistant/message``
  ``{session_id, message, source_file}`` → ``text/event-stream`` of ``data:``-framed
  ``AssistantStreamEvent`` JSON (unconfigured → 400 naming the reason;
  unknown session → 404; concurrent turn → 409; another pipeline than the
  session's → 409; provider failure before the stream opens → 502 with a
  sanitized message).

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute.assistant._providers import TextDelta, TurnStop
from haute.assistant._session import SessionStore

pytestmark = pytest.mark.usefixtures("project_root")

_PIPELINE = 'import haute\npipeline = haute.Pipeline("main", description="d")\n'
_OTHER_PIPELINE = 'import haute\npipeline = haute.Pipeline("second", description="d")\n'
# Every assistant request names the pipeline the canvas shows.
_CANVAS = {"source_file": "main.py"}


def _message(session_id: str, message: str = "hi", source_file: str = "main.py") -> dict:
    return {"session_id": session_id, "message": message, "source_file": source_file}


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(_PIPELINE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def configured(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (project_root / "haute.toml").write_text(
        '[assistant]\nprovider = "anthropic"\nmodel = "test-model"\n'
        '[assistant.egress]\ntrust = "external"\nmax_sensitivity = "public"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n",
        encoding="utf-8",
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
        self.calls: list[dict[str, object]] = []

    async def stream_turn(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
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
    def test_creates_session_bound_to_the_canvas_pipeline(
        self, client: TestClient, store: SessionStore
    ):
        response = client.post("/api/assistant/session", json=_CANVAS)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_file"] == "main.py"
        session = store.lookup(body["session_id"])
        assert session is not None
        assert session.source_file == "main.py"

    def test_source_file_that_is_not_a_pipeline_is_404(
        self, client: TestClient, store: SessionStore, project_root: Path
    ):
        (project_root / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
        for source_file in ("nope.py", "helpers.py"):
            response = client.post("/api/assistant/session", json={"source_file": source_file})
            assert response.status_code == 404, response.text
            assert source_file in response.json()["detail"]

    def test_source_file_outside_the_project_is_403(self, client: TestClient, store: SessionStore):
        response = client.post("/api/assistant/session", json={"source_file": "../main.py"})
        assert response.status_code == 403, response.text

    @pytest.mark.parametrize("body", [{}, {"source_file": ""}, {"pipeline": "main"}])
    def test_request_without_a_source_file_is_422(
        self, client: TestClient, store: SessionStore, body: dict
    ):
        response = client.post("/api/assistant/session", json=body)
        assert response.status_code == 422, response.text


class TestSessionList:
    """GET /sessions backs the panel's chat list, which opens before any send."""

    def test_lists_this_pipeline_s_conversations(self, client: TestClient, store: SessionStore):
        session = store.create("main.py")
        store.append(
            session,
            {"messages": [{"role": "user", "content": "aggregate claims"}], "outcome": None},
        )

        response = client.get("/api/assistant/sessions", params=_CANVAS)

        assert response.status_code == 200, response.text
        assert response.json()["source_file"] == "main.py"
        sessions = response.json()["sessions"]
        assert [item["session_id"] for item in sessions] == [session.id]
        assert sessions[0]["title"] == "aggregate claims"
        assert sessions[0]["message_count"] == 1

    def test_lists_only_the_requested_pipeline_s_conversations(
        self, client: TestClient, store: SessionStore, project_root: Path
    ):
        (project_root / "other.py").write_text(_OTHER_PIPELINE, encoding="utf-8")
        main = store.create("main.py")
        store.append(
            main, {"messages": [{"role": "user", "content": "main chat"}], "outcome": None}
        )
        other = store.create("other.py")
        store.append(
            other, {"messages": [{"role": "user", "content": "other chat"}], "outcome": None}
        )

        listed = {
            source_file: [
                item["title"]
                for item in client.get(
                    "/api/assistant/sessions", params={"source_file": source_file}
                ).json()["sessions"]
            ]
            for source_file in ("main.py", "other.py")
        }

        assert listed == {"main.py": ["main chat"], "other.py": ["other chat"]}

    def test_empty_project_lists_nothing(self, client: TestClient, store: SessionStore):
        response = client.get("/api/assistant/sessions", params=_CANVAS)
        assert response.status_code == 200
        assert response.json()["sessions"] == []

    def test_list_requires_a_source_file(self, client: TestClient, store: SessionStore):
        response = client.get("/api/assistant/sessions")
        assert response.status_code == 422

    def test_reads_the_store_on_its_event_loop_thread(
        self,
        client: TestClient,
        store: SessionStore,
        monkeypatch: pytest.MonkeyPatch,
    ):
        original = store.list_sessions

        def list_sessions(source_file: str):
            asyncio.get_running_loop()
            return original(source_file)

        monkeypatch.setattr(store, "list_sessions", list_sessions)

        response = client.get("/api/assistant/sessions", params=_CANVAS)

        assert response.status_code == 200, response.text

    def test_unknown_source_file_is_404(self, client: TestClient, store: SessionStore):
        response = client.get("/api/assistant/sessions", params={"source_file": "nope.py"})
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
        session_id = client.post("/api/assistant/session", json=_CANVAS).json()["session_id"]
        store.append(
            session_id,
            {
                "messages": [
                    {"role": "user", "content": "add a node"},
                    {
                        "role": "assistant",
                        "content": "Working on it",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "name": "apply_graph_plan",
                                "arguments": {
                                    "plan_hash": "a" * 64,
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "c1",
                        "name": "apply_graph_plan",
                        "content": {"applied": 1, "graph_fingerprint": "abc123"},
                        "is_error": False,
                    },
                    {
                        "role": "controller",
                        "content": "Continue the mutation workflow.",
                    },
                    {"role": "assistant", "content": "Done"},
                ],
                "outcome": {"kind": "applied", "detail": None},
            },
        )

        _persistent_store(monkeypatch, project_root)  # simulated server restart
        resumed = client.post("/api/assistant/session", json={**_CANVAS, "session_id": session_id})
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["session_id"] == session_id
        kinds = [entry["kind"] for entry in body["history"]]
        assert kinds == ["user", "assistant", "tool", "tool", "assistant", "outcome"]
        assert body["history"][0]["text"] == "add a node"
        assert body["history"][1]["text"] == "Working on it"
        tool = body["history"][2]
        assert tool["name"] == "apply_graph_plan"
        assert "redacted" in tool["summary"]
        assert "graph_fingerprint" in tool["summary"]
        assert tool["is_error"] is False
        graph_updated = body["history"][3]
        assert graph_updated == {
            "kind": "tool",
            "text": "",
            "name": "graph_updated",
            "summary": "Canvas updated",
            "is_error": False,
            "outcome": None,
        }
        assert body["history"][4]["text"] == "Done"
        # The stored outcome closes the resumed turn, as the live completed event did.
        assert body["history"][5]["outcome"] == {"kind": "applied", "detail": None}

    def test_tool_error_entries_carry_the_error_flag_and_message(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        store = _persistent_store(monkeypatch, project_root)
        session_id = client.post("/api/assistant/session", json=_CANVAS).json()["session_id"]
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
                        "is_error": True,
                    },
                ],
                "outcome": None,
            },
        )
        body = client.post(
            "/api/assistant/session", json={**_CANVAS, "session_id": session_id}
        ).json()
        tool = body["history"][-1]
        assert tool["kind"] == "tool"
        assert tool["is_error"] is True
        assert tool["summary"] == "No node x"

    def test_unknown_session_id_yields_a_fresh_session(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _persistent_store(monkeypatch, project_root)
        body = client.post(
            "/api/assistant/session", json={**_CANVAS, "session_id": "f" * 32}
        ).json()
        assert body["session_id"] != "f" * 32
        assert body["history"] == []

    def test_session_bound_to_another_pipeline_yields_a_fresh_session(
        self, client: TestClient, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (project_root / "other.py").write_text(_OTHER_PIPELINE, encoding="utf-8")
        _persistent_store(monkeypatch, project_root)
        session_id = client.post("/api/assistant/session", json=_CANVAS).json()["session_id"]

        body = client.post(
            "/api/assistant/session",
            json={"source_file": "other.py", "session_id": session_id},
        ).json()
        assert body["session_id"] != session_id
        assert body["source_file"] == "other.py"
        assert body["history"] == []


# ---------------------------------------------------------------------------
# Message turn
# ---------------------------------------------------------------------------


class TestMessageTurn:
    def _session(self, client: TestClient) -> str:
        return client.post("/api/assistant/session", json=_CANVAS).json()["session_id"]

    def test_unconfigured_send_is_400_naming_the_reason(
        self, client: TestClient, store: SessionStore
    ):
        session_id = self._session(client)
        response = client.post("/api/assistant/message", json=_message(session_id))
        assert response.status_code == 400
        assert "assistant" in response.json()["detail"].lower()

    def test_unknown_session_is_404(
        self, client: TestClient, store: SessionStore, configured: Path
    ):
        response = client.post("/api/assistant/message", json=_message("missing"))
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
            response = client.post("/api/assistant/message", json=_message(session_id))
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
        with client.stream("POST", "/api/assistant/message", json=_message(session_id)) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(response)

        types = [event["type"] for event in events]
        assert types[:2] == ["text_delta", "text_delta"]
        assert types[-1] == "completed"
        assert events[-1]["usage"] == {"input_tokens": 3, "output_tokens": 4}

    def test_turn_edits_the_pipeline_the_canvas_shows(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Two-pipeline repro: the chat for the second pipeline must never fall
        back to the project's first pipeline."""

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage

        (configured / "other.py").write_text(_OTHER_PIPELINE, encoding="utf-8")
        executor_sources: list[str] = []

        def build_executor(source_file, *, session_id, prior_messages):
            executor_sources.append(source_file)

            async def execute_tool(_name, _arguments):
                return {}

            return execute_tool

        provider = _ScriptedProvider([TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))])
        monkeypatch.setattr(assistant_routes, "build_tool_executor", build_executor)
        monkeypatch.setattr(assistant_routes, "_provider_factory", lambda _config: provider)

        created = client.post("/api/assistant/session", json={"source_file": "other.py"})
        session_id = created.json()["session_id"]
        with client.stream(
            "POST",
            "/api/assistant/message",
            json=_message(session_id, source_file="other.py"),
        ) as response:
            assert response.status_code == 200, response.read()
            events = _sse_events(response)

        assert events[-1]["type"] == "completed"
        assert executor_sources == ["other.py"]
        assert "other.py" in provider.calls[0]["system"]
        assert "main.py" not in provider.calls[0]["system"]

    def test_message_for_another_pipeline_is_409_naming_the_chat_s_pipeline(
        self,
        client: TestClient,
        store: SessionStore,
        configured: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        (configured / "other.py").write_text(_OTHER_PIPELINE, encoding="utf-8")
        _patch_provider(monkeypatch, [TextDelta("x")])
        session_id = self._session(client)

        response = client.post(
            "/api/assistant/message", json=_message(session_id, source_file="other.py")
        )

        assert response.status_code == 409, response.text
        assert "main.py" in response.json()["detail"]
        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked(), "a refused message must free the session"
        assert session.history == []

    def test_message_without_a_source_file_is_422(
        self, client: TestClient, store: SessionStore, configured: Path
    ):
        session_id = self._session(client)
        response = client.post(
            "/api/assistant/message", json={"session_id": session_id, "message": "hi"}
        )
        assert response.status_code == 422

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
        response = client.post("/api/assistant/message", json=_message(session_id))
        assert response.status_code >= 500
        assert "xyzzy" not in response.text


class TestRouteEdges:
    def test_host_without_git_reports_a_reason_not_a_server_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        from haute import _git
        from haute.schemas import GitWorkingBranchResponse

        monkeypatch.setattr(
            _git,
            "working_branch_status",
            lambda _root, cwd=None: GitWorkingBranchResponse(
                state="git-unavailable", current_branch="", identity_set=False
            ),
        )

        response = client.get("/api/assistant/status")

        assert response.status_code == 200, response.text
        assert response.json()["mutations_enabled"] is False
        assert "Git is not available" in response.json()["mutations_reason"]

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

        session_id = client.post("/api/assistant/session", json=_CANVAS).json()["session_id"]

        def broken_resolve():
            raise ConfigError("assistant config went away")

        monkeypatch.setattr(assistant_routes, "resolve_assistant_config", broken_resolve)
        response = client.post("/api/assistant/message", json=_message(session_id))
        assert response.status_code == 400
        assert "config" in response.json()["detail"].lower()


class TestProviderFactory:
    def test_builds_each_configured_adapter(self):
        from haute.assistant._config import AssistantConfig, EgressPolicy
        from haute.assistant._providers import (
            AnthropicProvider,
            DatabricksProvider,
            OpenAIProvider,
        )
        from haute.routes.assistant import _provider_factory

        egress = EgressPolicy(
            trust="organization",
            max_sensitivity="internal",
            allow_project_knowledge=False,
            allow_executable_source=False,
            allow_row_samples=False,
        )
        anthropic_config = AssistantConfig(
            provider="anthropic",
            model="m",
            base_url=None,
            api_key="k",
            max_output_tokens=8192,
            egress=egress,
            endpoint_host="api.anthropic.com",
        )
        assert isinstance(_provider_factory(anthropic_config), AnthropicProvider)
        openai_config = AssistantConfig(
            provider="openai",
            model="m",
            base_url="https://dbx",
            api_key="k",
            max_output_tokens=8192,
            egress=egress,
            endpoint_host="dbx",
        )
        assert isinstance(_provider_factory(openai_config), OpenAIProvider)
        databricks_config = AssistantConfig(
            provider="databricks",
            model="m",
            base_url="https://workspace.cloud.databricks.com/serving-endpoints",
            api_key="k",
            max_output_tokens=8192,
            egress=egress,
            endpoint_host="workspace.cloud.databricks.com",
        )
        assert isinstance(_provider_factory(databricks_config), DatabricksProvider)

    def test_no_pipeline_in_project_is_404(
        self, client: TestClient, store: SessionStore, project_root: Path
    ):
        (project_root / "main.py").unlink()
        response = client.post("/api/assistant/session", json=_CANVAS)
        assert response.status_code == 404


class TestTurnReservation:
    """Regression: the one-turn lock is reserved atomically BEFORE the
    route's awaited pre-work, so a concurrent send gets its 409 pre-stream
    instead of an unhandled mid-stream ConcurrentTurnError."""

    async def test_concurrent_sends_get_exactly_one_stream_and_one_409(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import asyncio
        import threading
        import time as time_module

        from fastapi import HTTPException

        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id

        real_context = assistant_routes.build_turn_context
        parse_started = threading.Event()

        def slow_context(*args, **kwargs):
            parse_started.set()
            time_module.sleep(0.2)  # runs in to_thread — widens the await window
            return real_context(*args, **kwargs)

        monkeypatch.setattr(assistant_routes, "build_turn_context", slow_context)
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda config: _ScriptedProvider(
                [TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )

        body = AssistantMessageRequest(session_id=session_id, message="hi", source_file="main.py")
        first_task = asyncio.create_task(assistant_routes.post_assistant_message(body))
        assert await asyncio.to_thread(parse_started.wait, 2)

        with pytest.raises(HTTPException) as excinfo:
            await assistant_routes.post_assistant_message(body)
        assert excinfo.value.status_code == 409

        response = await first_task
        chunks = [chunk async for chunk in response.body_iterator]
        assert any("completed" in chunk for chunk in chunks)
        # After the stream finishes the lock is free again.
        session = store.lookup(session_id)
        assert session is not None and not session.lock.locked()

    async def test_tool_executor_receives_exact_provider_history_window(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session = store.create("main.py")
        store.append(
            session,
            [
                {"role": "user", "content": "inspect the data"},
                {"role": "assistant", "content": "done"},
            ],
        )
        expected_history = store.history_window(session)
        captured: dict[str, object] = {}

        def build_executor(source_file, *, session_id, prior_messages):
            captured.update(
                {
                    "source_file": source_file,
                    "session_id": session_id,
                    "prior_messages": prior_messages,
                }
            )

            async def execute_tool(_name, _arguments):
                return {}

            return execute_tool

        monkeypatch.setattr(assistant_routes, "build_tool_executor", build_executor)
        monkeypatch.setattr(
            assistant_routes,
            "_provider_factory",
            lambda _config: _ScriptedProvider(
                [TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))]
            ),
        )

        response = await assistant_routes.post_assistant_message(
            AssistantMessageRequest(
                session_id=session.id, message="continue", source_file="main.py"
            )
        )
        chunks = [chunk async for chunk in response.body_iterator]

        assert any("completed" in chunk for chunk in chunks)
        assert captured == {
            "source_file": "main.py",
            "session_id": session.id,
            "prior_messages": expected_history,
        }

    async def test_a_reply_to_a_question_sends_only_the_rendered_turn_context(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session = store.create("main.py")
        original = "Band region into discrete region groups."
        store.append(
            session,
            [
                {"role": "user", "content": original},
                {
                    "role": "assistant",
                    "content": "NEEDS_INPUT: which regions go together?",
                },
            ],
        )
        captured: dict[str, object] = {}

        def build_executor(source_file, *, session_id, prior_messages):
            captured.update(
                {
                    "source_file": source_file,
                    "session_id": session_id,
                    "prior_messages": prior_messages,
                }
            )

            async def execute_tool(_name, _arguments):
                raise AssertionError("clarification response must not execute a tool")

            return execute_tool

        provider = _ScriptedProvider(
            [
                TextDelta("NEEDS_INPUT: what is the default group?"),
                TurnStop("end", ProviderUsage(1, 1)),
            ]
        )
        monkeypatch.setattr(assistant_routes, "build_tool_executor", build_executor)
        monkeypatch.setattr(assistant_routes, "_provider_factory", lambda _config: provider)

        response = await assistant_routes.post_assistant_message(
            AssistantMessageRequest(
                session_id=session.id,
                message="north and south are core",
                source_file="main.py",
            )
        )
        chunks = [chunk async for chunk in response.body_iterator]

        assert any("completed" in chunk for chunk in chunks)
        assert set(captured) == {"source_file", "session_id", "prior_messages"}
        assert len(provider.calls) == 1
        context = provider.calls[0]["messages"][-1]
        assert context["role"] == "context"
        assert context["content"].startswith("## Turn context")
        # Nothing is derived from the words of this or the earlier request.
        for call in provider.calls:
            for text in (call["system"], context["content"]):
                assert "Suggested recipe" not in text
                assert "Current-request" not in text
        dataset_tool = next(
            tool for tool in provider.calls[0]["tools"] if tool["name"] == "list_datasets"
        )
        assert dataset_tool["input_schema"]["properties"]["project_root"] == {"type": "string"}
        assert dataset_tool["input_schema"]["properties"]["recursive"] == {"type": "boolean"}
        recipe_tool = next(
            tool for tool in provider.calls[0]["tools"] if tool["name"] == "plan_recipe"
        )
        assert len(recipe_tool["input_schema"]["oneOf"]) == 4

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
        body = AssistantMessageRequest(session_id=session_id, message="hi", source_file="main.py")
        with pytest.raises(HTTPException) as excinfo:
            await assistant_routes.post_assistant_message(body)
        assert excinfo.value.status_code == 500

        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked(), "a pre-stream failure must not leak the reservation"

    async def test_history_evidence_failure_releases_the_turn_reservation(
        self, configured: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.assistant as assistant_routes
        from haute.schemas import AssistantMessageRequest

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session = store.create("main.py")

        def reject_history(*args, **kwargs):
            raise ValueError("invalid project evidence")

        monkeypatch.setattr(assistant_routes, "build_tool_executor", reject_history)

        with pytest.raises(ValueError, match="invalid project evidence"):
            await assistant_routes.post_assistant_message(
                AssistantMessageRequest(
                    session_id=session.id, message="continue", source_file="main.py"
                )
            )

        assert not session.lock.locked()


_QUOTES_PIPELINE = """import polars as pl

import haute

pipeline = haute.Pipeline("main", description="d")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.LazyFrame({"quote_id": [1], "driver_age": [30]})
"""

_TWO_NODE_PIPELINE = (
    _QUOTES_PIPELINE
    + """

@pipeline.polars
def adults(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.filter(pl.col("driver_age") >= 18)


pipeline.connect("quotes", "adults")
"""
)


def _egress_toml(*, max_sensitivity: str, allow_row_samples: bool) -> str:
    return (
        '[assistant]\nprovider = "anthropic"\nmodel = "test-model"\n'
        '[assistant.egress]\ntrust = "organization"\n'
        f'max_sensitivity = "{max_sensitivity}"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        f"allow_row_samples = {'true' if allow_row_samples else 'false'}\n"
    )


class TestTurnContext:
    """The system prompt is one prefix for a session; the turn context carries
    the graph, the policy and the selection, each as the turn starts."""

    @pytest.fixture()
    def internal(self, project_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (project_root / "main.py").write_text(_QUOTES_PIPELINE, encoding="utf-8")
        (project_root / "haute.toml").write_text(
            _egress_toml(max_sensitivity="internal", allow_row_samples=False), encoding="utf-8"
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        return project_root

    async def _turn(self, monkeypatch, store, session_id, context=None) -> _ScriptedProvider:
        import haute.routes.assistant as assistant_routes
        from haute.assistant._providers import ProviderUsage
        from haute.schemas import AssistantMessageContext, AssistantMessageRequest

        provider = _ScriptedProvider([TextDelta("ok"), TurnStop("end", ProviderUsage(1, 1))])
        monkeypatch.setattr(assistant_routes, "_provider_factory", lambda _config: provider)
        response = await assistant_routes.post_assistant_message(
            AssistantMessageRequest(
                session_id=session_id,
                message="hi",
                source_file="main.py",
                context=None if context is None else AssistantMessageContext(**context),
            )
        )
        chunks = [chunk async for chunk in response.body_iterator]
        assert any("completed" in chunk for chunk in chunks)
        return provider

    async def test_every_turn_sends_one_system_prompt_while_the_context_follows_the_project(
        self, internal: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import haute.routes.assistant as assistant_routes

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id

        first = await self._turn(monkeypatch, store, session_id, {"selected_node_ids": ["quotes"]})
        (internal / "main.py").write_text(_TWO_NODE_PIPELINE, encoding="utf-8")
        (internal / "haute.toml").write_text(
            _egress_toml(max_sensitivity="restricted", allow_row_samples=True), encoding="utf-8"
        )
        second = await self._turn(monkeypatch, store, session_id)

        (first_call,) = first.calls
        (second_call,) = second.calls
        assert first_call["system"] == second_call["system"]
        assert "Pipeline:" not in first_call["system"]
        assert "egress policy\n" not in first_call["system"]
        first_context = first_call["messages"][-1]
        second_context = second_call["messages"][-1]
        assert first_context["role"] == second_context["role"] == "context"
        assert first_call["messages"][-2] == {"role": "user", "content": "hi"}
        assert "- Selected on the canvas: `quotes`" in first_context["content"]
        assert '["quote_id", "driver_age"]' in first_context["content"]
        assert "- Column value profiles: not permitted" in first_context["content"]
        assert "`adults`" not in first_context["content"]
        assert "- Selected on the canvas: none" in second_context["content"]
        assert "- `adults` (Polars)" in second_context["content"]
        assert "  - input `quotes` from `quotes`: [" in second_context["content"]
        assert "- Column value profiles: permitted" in second_context["content"]
        # The first turn's context is not replayed with its history.
        assert all(
            message.get("content") != first_context["content"]
            for message in second_call["messages"][:-1]
        )

    async def test_a_selection_the_saved_pipeline_lacks_is_refused_before_the_turn(
        self, internal: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from fastapi import HTTPException

        import haute.routes.assistant as assistant_routes

        store = SessionStore()
        monkeypatch.setattr(assistant_routes, "session_store", store)
        session_id = store.create("main.py").id

        with pytest.raises(HTTPException) as excinfo:
            await self._turn(monkeypatch, store, session_id, {"selected_node_ids": ["ghost"]})

        assert excinfo.value.status_code == 409
        assert "'ghost'" in str(excinfo.value.detail)
        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked()
        assert session.history == []

    def test_the_context_is_closed_and_bounded(self):
        from pydantic import ValidationError

        from haute.schemas import AssistantMessageContext

        assert AssistantMessageContext(selected_node_ids=[]).preview_error_node_id is None
        with pytest.raises(ValidationError):
            AssistantMessageContext(selected_node_ids=[f"n{index}" for index in range(21)])
        with pytest.raises(ValidationError, match="must not repeat"):
            AssistantMessageContext(selected_node_ids=["a", "a"])
        with pytest.raises(ValidationError):
            AssistantMessageContext(selected_node_ids=[], unknown=True)


class TestReservationNeverLeaks:
    async def test_response_close_failure_still_releases_the_reservation(self):
        import haute.routes.assistant as assistant_routes
        from haute.assistant._loop import reserve_turn

        store = SessionStore()
        session = store.create("main.py")
        reservation = await reserve_turn(store, session.id)

        class RaisingBody:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                raise RuntimeError("body close failed")

        response = assistant_routes._ReservedStreamingResponse(
            RaisingBody(),
            reservation=reservation,
        )

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        with pytest.raises(RuntimeError, match="body close failed"):
            await response({"type": "http"}, receive, send)

        assert not session.lock.locked()

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

        body = AssistantMessageRequest(session_id=session_id, message="hi", source_file="main.py")
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

        body = AssistantMessageRequest(session_id=session_id, message="hi", source_file="main.py")
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
