"""Tests for the assistant agent loop (``haute.assistant._loop``) and session
retention.

Spec: docs/specs/assistant/low-level.md — Control flow (Message turn) and Edge
cases.  ``run_turn`` is an async generator of typed stream events; the tests
consume it with a scripted fake provider and an injected tool executor, so no
SDK, filesystem, or save service is involved.

API pinned here:

- ``run_turn(store, session_id, user_text, *, provider, tools, execute_tool,
  system_prompt, turn_timeout, max_tool_calls)`` — async generator.
  ``execute_tool(name, arguments) -> awaitable structured payload`` (a dict;
  a payload containing ``"error"`` marks the tool result as an error fed back
  to the model, never a turn failure).
- Emitted events are objects with a ``type`` attribute:
  ``text_delta`` (``.text``), ``tool_started`` (``.id``, ``.name``),
  ``tool_finished`` (``.id``, ``.name``, ``.is_error``), ``completed``
  (``.usage.input_tokens`` / ``.usage.output_tokens`` aggregated across
  round-trips), ``failed`` (``.message``), ``cancelled``.
- Exactly one terminal event (``completed`` / ``failed`` / ``cancelled``)
  ends every stream the consumer can still read, and it is the last event.
- ``build_system_prompt()`` embeds the catalog rendering, the authoring
  guide, and the exemplar index.

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from haute.assistant._providers import (
    AssistantProviderError,
    ProviderUsage,
    TextDelta,
    ToolCallRequest,
    TurnStop,
)
from haute.assistant._session import SessionStore

TERMINAL_TYPES = {"completed", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """Feeds one scripted event list per provider round-trip."""

    def __init__(self, rounds: list[list[object]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict[str, Any]] = []

    async def stream_turn(self, *, system, messages, tools):
        self.calls.append(
            {"system": system, "messages": [dict(m) for m in messages], "tools": list(tools)}
        )
        if not self._rounds:
            raise AssertionError("provider called more times than scripted")
        for event in self._rounds.pop(0):
            if isinstance(event, Exception):
                raise event
            if event == "hang":
                await asyncio.sleep(60)
            yield event


def _usage(inp: int = 5, out: int = 7) -> ProviderUsage:
    return ProviderUsage(input_tokens=inp, output_tokens=out)


async def _run(
    store: SessionStore,
    session_id: str,
    text: str,
    *,
    provider,
    execute_tool=None,
    turn_timeout: float = 5.0,
    max_tool_calls: int = 8,
):
    from haute.assistant._loop import run_turn

    async def default_executor(name: str, arguments: dict) -> dict:
        raise AssertionError(f"unexpected tool execution: {name}")

    events = []
    async for event in run_turn(
        store,
        session_id,
        text,
        provider=provider,
        tools=[{"name": "get_pipeline", "description": "d", "input_schema": {"type": "object"}}],
        execute_tool=execute_tool or default_executor,
        system_prompt="system prompt under test",
        turn_timeout=turn_timeout,
        max_tool_calls=max_tool_calls,
    ):
        events.append(event)
    return events


def _assert_single_terminal(events: list) -> object:
    terminals = [event for event in events if event.type in TERMINAL_TYPES]
    assert len(terminals) == 1, [event.type for event in events]
    assert events[-1] is terminals[0], "terminal event must be last"
    return terminals[0]


@pytest.fixture()
def store() -> SessionStore:
    return SessionStore()


@pytest.fixture()
def session_id(store: SessionStore) -> str:
    return store.create("main.py").id


# ---------------------------------------------------------------------------
# Turn shapes
# ---------------------------------------------------------------------------


class TestTextOnlyTurn:
    async def test_text_deltas_then_completed_with_usage(self, store, session_id):
        provider = ScriptedProvider(
            [[TextDelta("Hel"), TextDelta("lo"), TurnStop("end", _usage(5, 7))]]
        )
        events = await _run(store, session_id, "hi", provider=provider)

        assert [event.type for event in events[:2]] == ["text_delta", "text_delta"]
        assert (events[0].text, events[1].text) == ("Hel", "lo")
        terminal = _assert_single_terminal(events)
        assert terminal.type == "completed"
        assert (terminal.usage.input_tokens, terminal.usage.output_tokens) == (5, 7)

    async def test_turn_recorded_in_history_as_one_turn(self, store, session_id):
        provider = ScriptedProvider([[TextDelta("Hello"), TurnStop("end", _usage())]])
        await _run(store, session_id, "hi", provider=provider)

        session = store.lookup(session_id)
        assert session is not None
        assert len(session.history) == 1
        roles = [message.role for message in session.history[0].messages]
        assert roles[0] == "user"
        assert "assistant" in roles

    async def test_windowed_history_reaches_provider_on_next_turn(self, store, session_id):
        provider = ScriptedProvider(
            [
                [TextDelta("first"), TurnStop("end", _usage())],
                [TextDelta("second"), TurnStop("end", _usage())],
            ]
        )
        await _run(store, session_id, "one", provider=provider)
        await _run(store, session_id, "two", provider=provider)

        second_call_messages = provider.calls[1]["messages"]
        contents = [message.get("content") for message in second_call_messages]
        assert "one" in contents, "prior turn's user message must be in the window"
        assert "two" in contents, "current user message must be sent"


class TestToolRoundTrip:
    async def test_tool_dispatch_result_feedback_and_second_round(self, store, session_id):
        provider = ScriptedProvider(
            [
                [
                    ToolCallRequest("t1", "get_pipeline", {"a": 1}),
                    TurnStop("tool_use", _usage(5, 7)),
                ],
                [TextDelta("done"), TurnStop("end", _usage(11, 13))],
            ]
        )
        executed: list[tuple[str, dict]] = []

        async def execute_tool(name: str, arguments: dict) -> dict:
            executed.append((name, arguments))
            return {"nodes": []}

        events = await _run(
            store, session_id, "read it", provider=provider, execute_tool=execute_tool
        )

        assert executed == [("get_pipeline", {"a": 1})]
        types = [event.type for event in events]
        assert "tool_started" in types and "tool_finished" in types
        started = next(event for event in events if event.type == "tool_started")
        finished = next(event for event in events if event.type == "tool_finished")
        assert (started.id, started.name) == ("t1", "get_pipeline")
        assert (finished.id, finished.name, finished.is_error) == ("t1", "get_pipeline", False)

        assert len(provider.calls) == 2
        second_messages = provider.calls[1]["messages"]
        assert any(
            message.get("tool_results") or message.get("role") == "tool"
            for message in second_messages
        ), "tool result must be fed back to the provider"

        terminal = _assert_single_terminal(events)
        assert terminal.type == "completed"
        assert (terminal.usage.input_tokens, terminal.usage.output_tokens) == (16, 20)

    async def test_tool_error_feeds_model_not_turn_failure(self, store, session_id):
        provider = ScriptedProvider(
            [
                [ToolCallRequest("t1", "get_pipeline", {}), TurnStop("tool_use", _usage())],
                [TextDelta("recovered"), TurnStop("end", _usage())],
            ]
        )

        async def execute_tool(name: str, arguments: dict) -> dict:
            return {"error": {"code": "unknown_node", "message": "no such node"}}

        events = await _run(store, session_id, "edit", provider=provider, execute_tool=execute_tool)

        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is True
        terminal = _assert_single_terminal(events)
        assert terminal.type == "completed", "a tool error must not fail the turn"


# ---------------------------------------------------------------------------
# Limits and failures
# ---------------------------------------------------------------------------


class TestLimits:
    async def test_tool_call_cap_is_named_terminal_failure(self, store, session_id):
        endless_tools = [
            [ToolCallRequest(f"t{i}", "get_pipeline", {}), TurnStop("tool_use", _usage())]
            for i in range(10)
        ]
        provider = ScriptedProvider(endless_tools)

        async def execute_tool(name: str, arguments: dict) -> dict:
            return {"ok": True}

        events = await _run(
            store,
            session_id,
            "loop",
            provider=provider,
            execute_tool=execute_tool,
            max_tool_calls=2,
        )
        terminal = _assert_single_terminal(events)
        assert terminal.type == "failed"
        assert "tool" in terminal.message.lower()

    async def test_wall_clock_timeout_is_named_terminal_failure(self, store, session_id):
        provider = ScriptedProvider([["hang"]])
        events = await _run(store, session_id, "slow", provider=provider, turn_timeout=0.05)
        terminal = _assert_single_terminal(events)
        assert terminal.type == "failed"
        assert "time" in terminal.message.lower()

    async def test_provider_error_becomes_failed_event(self, store, session_id):
        provider = ScriptedProvider(
            [[TextDelta("par"), AssistantProviderError("anthropic", "rate_limit")]]
        )
        events = await _run(store, session_id, "hi", provider=provider)
        terminal = _assert_single_terminal(events)
        assert terminal.type == "failed"
        assert "anthropic" in terminal.message.lower()

    async def test_unexpected_exception_is_sanitized_failed_event(self, store, session_id):
        secret = "sqlite:///c/private/path/db?password=hunter2"
        provider = ScriptedProvider([[RuntimeError(secret)]])
        events = await _run(store, session_id, "hi", provider=provider)
        terminal = _assert_single_terminal(events)
        assert terminal.type == "failed"
        assert "hunter2" not in terminal.message


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_inflight_tool_execution_completes_despite_cancel(self, store, session_id):
        """The spec's shield invariant at loop level: a tool execution already
        in flight (the mutation save path in production) is awaited to
        completion even when the consumer is cancelled mid-turn."""

        from haute.assistant._loop import run_turn

        started = asyncio.Event()
        finished = {"value": False}

        async def slow_tool(name: str, arguments: dict) -> dict:
            started.set()
            await asyncio.sleep(0.1)
            finished["value"] = True
            return {"ok": True}

        provider = ScriptedProvider(
            [
                [ToolCallRequest("t1", "get_pipeline", {}), TurnStop("tool_use", _usage())],
                [TextDelta("never reached"), TurnStop("end", _usage())],
            ]
        )

        async def consume() -> None:
            async for _event in run_turn(
                store,
                session_id,
                "edit",
                provider=provider,
                tools=[],
                execute_tool=slow_tool,
                system_prompt="s",
                turn_timeout=5.0,
                max_tool_calls=8,
            ):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The shielded execution must have run to completion.
        assert finished["value"] is True
        # And the session lock must have been released for the next turn.
        session = store.lookup(session_id)
        assert session is not None and not session.lock.locked()


class TestDisconnectHistoryIntegrity:
    @pytest.mark.parametrize(
        "close_at",
        ["tool_started", "tool_finished", "graph_updated"],
    )
    async def test_closing_at_each_tool_yield_never_persists_an_orphan(
        self,
        store,
        session_id,
        close_at,
    ):
        """Every externally visible tool lifecycle yield is a generator-close
        boundary. History committed from each boundary must remain acceptable
        to both providers: every call id has one matching result id."""

        from haute.assistant._loop import run_turn

        async def execute_tool(name: str, arguments: dict) -> dict:
            return {"ok": True, "graph_fingerprint": "fp-1"}

        turn = run_turn(
            store,
            session_id,
            "edit",
            provider=ScriptedProvider(
                [[ToolCallRequest("t1", "get_pipeline", {}), TurnStop("tool_use", _usage())]]
            ),
            tools=[],
            execute_tool=execute_tool,
            system_prompt="s",
            turn_timeout=5.0,
            max_tool_calls=8,
        )

        while True:
            event = await anext(turn)
            if event.type == close_at:
                break
        await turn.aclose()

        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked()
        assert len(session.history) == 1
        call_ids = {
            call.id for message in session.history[0].messages for call in message.tool_calls
        }
        result_ids = {
            message.tool_call_id
            for message in session.history[0].messages
            if message.role == "tool"
        }
        expected_ids = set() if close_at == "tool_started" else {"t1"}
        assert call_ids == result_ids == expected_ids

    async def test_raising_history_append_still_releases_the_turn_lock(self):
        class RaisingAppendStore(SessionStore):
            def append(self, session_ref, turn):
                raise RuntimeError("append failed")

        store = RaisingAppendStore()
        session = store.create("main.py")

        with pytest.raises(RuntimeError, match="append failed"):
            await _run(
                store,
                session.id,
                "hi",
                provider=ScriptedProvider([[TextDelta("done"), TurnStop("end", _usage())]]),
            )

        assert not session.lock.locked()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestSessionLock:
    async def test_second_turn_on_same_session_is_rejected_while_running(self, store, session_id):
        from haute.assistant._loop import ConcurrentTurnError, run_turn

        provider = ScriptedProvider([["hang"]])
        first = run_turn(
            store,
            session_id,
            "one",
            provider=provider,
            tools=[],
            execute_tool=None,
            system_prompt="s",
            turn_timeout=5.0,
            max_tool_calls=8,
        )
        first_event_task = asyncio.create_task(anext(first))
        await asyncio.sleep(0.01)  # let the first turn acquire the lock

        with pytest.raises(ConcurrentTurnError):
            second = run_turn(
                store,
                session_id,
                "two",
                provider=ScriptedProvider([[TextDelta("x"), TurnStop("end", _usage())]]),
                tools=[],
                execute_tool=None,
                system_prompt="s",
                turn_timeout=5.0,
                max_tool_calls=8,
            )
            await anext(second)

        first_event_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_event_task
        await first.aclose()


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_build_system_prompt_embeds_catalog_guide_and_example_index(self):
        from haute.assistant._assets import authoring_guide, example_index
        from haute.assistant._loop import build_system_prompt

        prompt = build_system_prompt(pipeline_name="main", source_file="main.py")
        assert "Haute node catalog" in prompt
        guide_first_line = authoring_guide().strip().splitlines()[0].lstrip("# ").strip()
        assert guide_first_line in prompt
        for name, summary in example_index():
            assert name in prompt
            assert summary in prompt
        assert "main.py" in prompt


# ---------------------------------------------------------------------------
# Session retention (spec: dedicated block)
# ---------------------------------------------------------------------------


def _turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


class TestSessionRetention:
    def test_stored_history_cap_evicts_whole_oldest_turns(self):
        store = SessionStore(max_stored_messages=6)
        session = store.create("main.py")
        for index in range(5):
            store.append(session, _turn(f"u{index}", f"a{index}"))
        session = store.lookup(session.id)
        assert session is not None
        assert session.stored_message_count <= 6
        first_user = session.history[0].messages[0].content
        assert first_user == "u2", "oldest whole turns must be evicted first"

    def test_provider_window_carries_newest_complete_turns_only(self):
        store = SessionStore(max_provider_messages=4)
        session = store.create("main.py")
        for index in range(4):
            store.append(session, _turn(f"u{index}", f"a{index}"))
        window = store.history_window(session)
        contents = [message["content"] for message in window]
        assert contents == ["u2", "a2", "u3", "a3"]

    def test_lru_evicts_idle_session_and_evicted_id_is_unknown(self):
        store = SessionStore(max_live_sessions=2)
        first = store.create("a.py")
        second = store.create("b.py")
        store.lookup(first.id)  # first becomes most recently used
        third = store.create("c.py")
        assert store.lookup(second.id) is None, "LRU idle session must be evicted"
        assert store.lookup(first.id) is not None
        assert store.lookup(third.id) is not None

    async def test_lru_never_evicts_session_with_running_turn(self):
        store = SessionStore(max_live_sessions=1)
        busy = store.create("a.py")
        async with busy.lock:
            replacement = store.create("b.py")
            assert store.lookup(busy.id) is not None, "locked session must survive eviction"
            assert store.lookup(replacement.id) is not None
        store.create("c.py")  # now that busy is idle it may be evicted
        assert len(store) <= 2


# ---------------------------------------------------------------------------
# Neutral-record validation (the session module's JSON boundary)
# ---------------------------------------------------------------------------


class TestNeutralRecordValidation:
    def test_message_rejects_unknown_role(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(ValueError, match="role"):
            AssistantMessage(role="operator", content="x")

    def test_message_rejects_non_json_content(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(TypeError, match="JSON"):
            AssistantMessage(role="user", content=object())

    def test_message_rejects_non_finite_numbers(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(ValueError, match="finite"):
            AssistantMessage(role="user", content=float("nan"))

    def test_tool_call_mapping_requires_all_fields(self):
        from haute.assistant._session import AssistantToolCall

        with pytest.raises(ValueError, match="missing"):
            AssistantToolCall.from_mapping({"id": "t1", "name": "x"})

    def test_tool_result_mapping_requires_all_fields(self):
        from haute.assistant._session import AssistantToolResult

        with pytest.raises(ValueError, match="missing"):
            AssistantToolResult.from_mapping({"tool_call_id": "t1", "name": "x", "content": {}})

    def test_message_round_trips_tool_calls_and_results(self):
        from haute.assistant._session import AssistantMessage

        message = AssistantMessage(
            role="assistant",
            content="done",
            tool_calls=({"id": "t1", "name": "get_pipeline", "arguments": {"a": 1}},),
        )
        dumped = message.as_dict()
        assert dumped["tool_calls"] == [{"id": "t1", "name": "get_pipeline", "arguments": {"a": 1}}]
        rebuilt = AssistantMessage.from_mapping(dumped)
        assert rebuilt.tool_calls[0].arguments == {"a": 1}

    def test_turn_must_contain_exactly_one_leading_user_message(self):
        from haute.assistant._session import AssistantTurn

        with pytest.raises(ValueError, match="user"):
            AssistantTurn.from_messages([{"role": "assistant", "content": "x"}])
        with pytest.raises(ValueError, match="exactly one|one user"):
            AssistantTurn.from_messages(
                [
                    {"role": "user", "content": "a"},
                    {"role": "user", "content": "b"},
                ]
            )

    def test_empty_turn_is_rejected(self):
        from haute.assistant._session import AssistantTurn

        with pytest.raises(ValueError, match="at least one"):
            AssistantTurn.from_messages([])


class TestSessionStoreMisuse:
    def test_append_to_unknown_session_id_raises_key_error(self):
        store = SessionStore()
        with pytest.raises(KeyError):
            store.append("ghost", _turn("u", "a"))

    def test_append_to_evicted_session_object_raises_key_error(self):
        from haute.assistant._session import AssistantSession

        store = SessionStore()
        foreign = AssistantSession(id="foreign", source_file="a.py")
        with pytest.raises(KeyError):
            store.append(foreign, _turn("u", "a"))

    def test_history_window_rejects_negative_limits(self):
        store = SessionStore()
        session = store.create("a.py")
        with pytest.raises(ValueError, match="non-negative"):
            store.history_window(session, max_messages=-1)

    def test_store_limits_must_be_positive(self):
        with pytest.raises(ValueError, match="positive"):
            SessionStore(max_live_sessions=0)

    def test_oversized_single_turn_is_never_split(self):
        store = SessionStore(max_stored_messages=3)
        session = store.create("a.py")
        big_turn = [{"role": "user", "content": "u"}] + [
            {"role": "assistant", "content": f"a{i}"} for i in range(6)
        ]
        store.append(session, big_turn)
        session = store.lookup(session.id)
        assert session is not None
        assert len(session.history) == 1
        assert session.history[0].message_count == 7

    def test_len_and_contains(self):
        store = SessionStore()
        session = store.create("a.py")
        assert len(store) == 1
        assert session.id in store
        assert "ghost" not in store

    def test_session_as_dict_excludes_the_live_lock(self):
        store = SessionStore()
        session = store.create("a.py")
        store.append(session, _turn("u", "a"))
        dumped = store.lookup(session.id).as_dict()
        assert "lock" not in dumped
        assert dumped["history"][0]["messages"][0]["content"] == "u"


class TestNeutralRecordValidationSweep:
    """Parametrized sweep of the remaining JSON-boundary rejections."""

    def test_tool_result_field_type_validation(self):
        from haute.assistant._session import AssistantToolResult

        with pytest.raises(ValueError):
            AssistantToolResult(tool_call_id="", name="x", content={})
        with pytest.raises(ValueError):
            AssistantToolResult(tool_call_id="t", name="", content={})
        with pytest.raises(TypeError):
            AssistantToolResult(tool_call_id="t", name="x", content={}, is_error="no")  # type: ignore[arg-type]

    def test_tool_call_field_type_validation(self):
        from haute.assistant._session import AssistantToolCall

        with pytest.raises(ValueError):
            AssistantToolCall(id="", name="x", arguments={})
        with pytest.raises(ValueError):
            AssistantToolCall(id="t", name="", arguments={})
        with pytest.raises(TypeError):
            AssistantToolCall.from_mapping({"id": "t", "name": "x", "arguments": "not-an-object"})

    def test_message_optional_string_fields_must_be_non_empty(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(ValueError, match="tool_call_id"):
            AssistantMessage(role="tool", content="x", tool_call_id="")
        with pytest.raises(ValueError, match="name"):
            AssistantMessage(role="tool", content="x", tool_call_id="t", name="")

    def test_message_tool_containers_reject_strings(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(TypeError):
            AssistantMessage.from_mapping({"role": "assistant", "tool_calls": "broken"})
        with pytest.raises(TypeError):
            AssistantMessage.from_mapping({"role": "assistant", "tool_results": "broken"})

    def test_message_from_mapping_requires_role(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(ValueError, match="role"):
            AssistantMessage.from_mapping({"content": "x"})

    def test_message_content_object_keys_must_be_strings(self):
        from haute.assistant._session import AssistantMessage

        with pytest.raises(TypeError, match="keys"):
            AssistantMessage(role="user", content={1: "x"})  # type: ignore[dict-item]

    def test_message_as_dict_includes_tool_results_and_names(self):
        from haute.assistant._session import AssistantMessage

        message = AssistantMessage(
            role="tool",
            content={"ok": True},
            tool_call_id="t1",
            name="get_pipeline",
            tool_results=(
                {
                    "tool_call_id": "t1",
                    "name": "get_pipeline",
                    "content": {"ok": True},
                    "is_error": False,
                },
            ),
        )
        dumped = message.as_dict()
        assert dumped["tool_call_id"] == "t1"
        assert dumped["name"] == "get_pipeline"
        assert dumped["tool_results"][0]["is_error"] is False

    def test_tool_role_message_round_trips_error_flag(self):
        from haute.assistant._session import AssistantMessage

        message = AssistantMessage.from_mapping(
            {
                "role": "tool",
                "tool_call_id": "t1",
                "name": "get_pipeline",
                "content": {"error": {"code": "failed"}},
                "is_error": True,
            }
        )

        assert message.as_dict()["is_error"] is True

    def test_coerce_turn_mapping_requires_messages_field(self):
        store = SessionStore()
        session = store.create("a.py")
        with pytest.raises(ValueError, match="messages"):
            store.append(session, {"not_messages": []})
        with pytest.raises(TypeError):
            store.append(session, {"messages": "broken"})

    def test_session_rejects_blank_id_and_bytes_source(self):
        from haute.assistant._session import AssistantSession

        with pytest.raises(ValueError):
            AssistantSession(id="", source_file="a.py")
        with pytest.raises(TypeError):
            AssistantSession(id="s", source_file=b"a.py")  # type: ignore[arg-type]


class TestLimitHistoryIntegrity:
    async def test_cap_aborted_turn_leaves_no_dangling_tool_call(self, store, session_id):
        """Regression: a limit abort must never persist an assistant tool
        call without its result — the next provider request would be
        rejected wholesale for the orphaned call."""

        endless = [
            [ToolCallRequest(f"t{i}", "get_pipeline", {}), TurnStop("tool_use", _usage())]
            for i in range(5)
        ]

        async def execute_tool(name: str, arguments: dict) -> dict:
            return {"ok": True}

        events = await _run(
            store,
            session_id,
            "loop",
            provider=ScriptedProvider(endless),
            execute_tool=execute_tool,
            max_tool_calls=2,
        )
        assert events[-1].type == "failed"

        session = store.lookup(session_id)
        assert session is not None
        for turn in session.history:
            call_ids = {call.id for message in turn.messages for call in message.tool_calls}
            result_ids = {
                message.tool_call_id
                for message in turn.messages
                if message.role == "tool" and message.tool_call_id
            }
            assert call_ids == result_ids, "every persisted tool call must have its result"


class TestSystemPromptSummary:
    def test_node_summary_project_fact_is_included(self):
        from haute.assistant._loop import build_system_prompt

        prompt = build_system_prompt(
            pipeline_name="main",
            source_file="main.py",
            node_summary="3 nodes (2× polars, 1× dataInput)",
        )
        assert "3 nodes (2× polars, 1× dataInput)" in prompt

    def test_summarise_graph_nodes_counts_by_type(self):
        from types import SimpleNamespace

        from haute.assistant._loop import summarise_graph_nodes

        graph = SimpleNamespace(
            nodes=[
                SimpleNamespace(data=SimpleNamespace(nodeType="polars")),
                SimpleNamespace(data=SimpleNamespace(nodeType="polars")),
                SimpleNamespace(data=SimpleNamespace(nodeType="dataInput")),
            ]
        )
        assert summarise_graph_nodes(graph) == "3 nodes (1× dataInput, 2× polars)"


class TestProviderStreamTeardown:
    async def test_closing_the_turn_closes_the_provider_stream(self, store, session_id):
        """Regression: an abnormally closed turn must aclose the provider's
        own stream generator (SDK cleanup), never leave it to GC."""

        from haute.assistant._loop import run_turn

        closed = {"value": False}

        class TrackedProvider:
            async def stream_turn(self, *, system, messages, tools):
                try:
                    yield TextDelta("first")
                    await asyncio.sleep(60)
                    yield TextDelta("never delivered")
                finally:
                    closed["value"] = True

        turn = run_turn(
            store,
            session_id,
            "hi",
            provider=TrackedProvider(),
            tools=[],
            execute_tool=None,
            system_prompt="s",
            turn_timeout=30.0,
            max_tool_calls=8,
        )
        first = await anext(turn)
        assert first.type == "text_delta"

        await turn.aclose()

        assert closed["value"] is True, "the provider stream's finally must have run"
        session = store.lookup(session_id)
        assert session is not None
        assert not session.lock.locked()
        assert len(session.history) == 1, "the interrupted turn's history landed"


class TestPerRoundProviderStreamClosure:
    async def test_every_rounds_stream_closes_before_the_next_opens(self, store, session_id):
        """Regression: breaking on TurnStop leaves the provider generator
        suspended; each round's stream must be closed before the next round
        opens, not just the final one at turn teardown."""

        from haute.assistant._loop import run_turn

        timeline: list[str] = []

        class TimelineProvider:
            def __init__(self) -> None:
                self._round = 0

            async def stream_turn(self, *, system, messages, tools):
                self._round += 1
                round_number = self._round
                timeline.append(f"open-{round_number}")
                try:
                    if round_number == 1:
                        yield ToolCallRequest("t1", "get_pipeline", {})
                        yield TurnStop("tool_use", _usage())
                        yield TextDelta("after stop — never pulled")
                    else:
                        yield TextDelta("done")
                        yield TurnStop("end", _usage())
                finally:
                    timeline.append(f"close-{round_number}")

        async def execute_tool(name: str, arguments: dict) -> dict:
            return {"ok": True}

        events = []
        async for event in run_turn(
            store,
            session_id,
            "two rounds",
            provider=TimelineProvider(),
            tools=[],
            execute_tool=execute_tool,
            system_prompt="s",
            turn_timeout=30.0,
            max_tool_calls=8,
        ):
            events.append(event)

        assert events[-1].type == "completed"
        assert timeline == ["open-1", "close-1", "open-2", "close-2"], timeline
