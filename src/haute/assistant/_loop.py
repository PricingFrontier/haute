"""The provider-independent pricing assistant turn loop."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from typing import Any

from haute._env import int_env
from haute._logging import get_logger
from haute.assistant._assets import authoring_guide, example_index
from haute.assistant._catalog import render_catalog
from haute.assistant._providers import (
    AssistantProvider,
    AssistantProviderError,
    TextDelta,
    ToolCallRequest,
    TurnStop,
)
from haute.assistant._session import AssistantSession, SessionStore
from haute.errors import HauteError
from haute.schemas import (
    AssistantCompletedEvent,
    AssistantFailedEvent,
    AssistantGraphUpdatedEvent,
    AssistantStreamEvent,
    AssistantTextDeltaEvent,
    AssistantToolFinishedEvent,
    AssistantToolStartedEvent,
    AssistantUsage,
)

logger = get_logger(component="assistant.loop")

DEFAULT_TURN_TIMEOUT = 300
DEFAULT_MAX_TOOL_CALLS = 20
_INTERNAL_ERROR_DETAIL = "The assistant turn failed unexpectedly."

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any]]]


class UnknownSessionError(HauteError):
    """Raised when a turn references a session that is not live."""

    def __init__(self, session_id: str) -> None:
        super().__init__("Unknown assistant session", session_id=session_id)


class ConcurrentTurnError(HauteError):
    """Raised when a second turn is started while a session is busy."""

    def __init__(self, session_id: str) -> None:
        super().__init__("An assistant turn is already running", session_id=session_id)


class _TurnLimitError(Exception):
    """Internal control-flow marker for named turn limits."""


def _resolved_limit(value: float | int | None, env_name: str, default: int) -> float:
    if value is not None:
        return float(value)
    return float(int_env(env_name, default))


def summarise_graph_nodes(graph: Any) -> str:
    """Render the spec's node-count/type project fact from a parsed graph."""

    type_counts: dict[str, int] = {}
    for node in graph.nodes:
        node_type = node.data.nodeType
        key = getattr(node_type, "value", str(node_type))
        type_counts[key] = type_counts.get(key, 0) + 1
    if not type_counts:
        return "0 nodes"
    rendered = ", ".join(f"{count}× {name}" for name, count in sorted(type_counts.items()))
    return f"{len(graph.nodes)} nodes ({rendered})"


def build_system_prompt(
    *, pipeline_name: str, source_file: str, node_summary: str | None = None
) -> str:
    """Assemble the stable knowledge and project-facts system prompt."""

    exemplar_lines = [f"- `{name}` — {summary}" for name, summary in example_index()]
    facts = [f"- Pipeline: `{pipeline_name}`", f"- Source file: `{source_file}`"]
    if node_summary is not None:
        facts.append(f"- Nodes: {node_summary}")
    return "\n\n".join(
        (
            "You are Haute's pricing-pipeline assistant. Use the provided tools to author "
            "and edit the saved graph; never invent node types or config keys.",
            render_catalog(),
            "## Haute authoring guide\n" + authoring_guide(),
            "## Packaged exemplar pipelines\n" + "\n".join(exemplar_lines),
            "## Project facts\n" + "\n".join(facts),
        )
    )


_SUMMARY_LIMIT = 160


def _compact_summary(value: Mapping[str, Any]) -> str:
    """Render tool arguments compactly for the chat activity row."""

    import json

    rendered = json.dumps(value, separators=(", ", ": "), default=str)
    if len(rendered) > _SUMMARY_LIMIT:
        return rendered[: _SUMMARY_LIMIT - 1] + "…"
    return rendered


def _result_summary(payload: Mapping[str, Any], is_error: bool) -> str:
    """Render a tool result: the error message, or the payload's shape."""

    if is_error:
        error = payload.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str):
                return (
                    message
                    if len(message) <= _SUMMARY_LIMIT
                    else message[: _SUMMARY_LIMIT - 1] + "…"
                )
        return "tool error"
    return _compact_summary(dict(payload))


def _assistant_message(
    text_parts: list[str], tool_calls: list[ToolCallRequest]
) -> dict[str, Any] | None:
    if not text_parts and not tool_calls:
        return None
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
            for call in tool_calls
        ]
    return message


def _tool_result_message(
    request: ToolCallRequest,
    payload: Mapping[str, Any],
    is_error: bool,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": request.id,
        "name": request.name,
        "content": dict(payload),
        "is_error": is_error,
    }


def _append_round(
    turn_messages: list[dict[str, Any]],
    text_parts: list[str],
    tool_calls: list[ToolCallRequest],
    tool_results: list[dict[str, Any]],
) -> None:
    assistant = _assistant_message(text_parts, tool_calls)
    if assistant is not None:
        turn_messages.append(assistant)
    turn_messages.extend(tool_results)


async def _aclose_quietly(stream: object) -> None:
    """Close a provider stream generator; log (never mask) cleanup failures."""

    if stream is None:
        return
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001 - cleanup must not mask the turn's outcome
        logger.error("assistant_provider_stream_close_failed", exc_info=True)


async def _execute_shielded(
    execute_tool: ToolExecutor,
    request: ToolCallRequest,
) -> tuple[Mapping[str, Any], BaseException | None]:
    """Run the tool; on cancellation OR generator close, drain it first.

    Cancellation arrives as ``CancelledError``; a response-teardown
    ``aclose()`` arrives as ``GeneratorExit`` at this await.  In both cases
    a mutation already executing must complete (it owns ``save_lock``) and
    its result must be recorded before the interrupt continues, so the
    caller re-raises the returned interrupt AFTER recording the result.
    """

    task = asyncio.ensure_future(execute_tool(request.name, dict(request.arguments)))
    try:
        return await asyncio.shield(task), None
    except (asyncio.CancelledError, GeneratorExit) as exc:
        return await task, exc


class TurnReservation:
    """Idempotent owner of one acquired session-turn lock.

    The lock has two independent release paths — ``run_turn``'s ``finally``
    and the streaming response's lifecycle (which covers a client that
    disconnects before the body iterator ever starts).  Whichever fires
    first wins; the second is a no-op, so the lock can never double-release
    or leak.
    """

    __slots__ = ("_released", "session")

    def __init__(self, session: AssistantSession) -> None:
        self.session = session
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.session.lock.release()


async def reserve_turn(store: SessionStore, session_id: str) -> TurnReservation:
    """Atomically reserve a session's one-turn lock for an imminent turn.

    Lookup, the busy check, and the acquire run in one synchronous stretch on
    the event loop (``Lock.acquire`` does not suspend when the lock is free),
    so two concurrent callers can never both pass.  The route reserves BEFORE
    any awaited pre-work so the 409 is decided pre-stream.
    """

    session = store.lookup(session_id)
    if session is None:
        raise UnknownSessionError(session_id)
    if session.lock.locked():
        raise ConcurrentTurnError(session_id)
    await session.lock.acquire()
    return TurnReservation(session)


async def run_turn(
    store: SessionStore,
    session_id: str,
    user_text: str,
    *,
    provider: AssistantProvider,
    tools: Sequence[Mapping[str, Any]],
    execute_tool: ToolExecutor | None,
    system_prompt: str,
    turn_timeout: float | None,
    max_tool_calls: int | None,
    reservation: TurnReservation | None = None,
) -> AsyncGenerator[AssistantStreamEvent, None]:
    """Stream one complete provider/tool turn for a live session.

    ``reservation`` carries a lock already acquired via :func:`reserve_turn`
    (the route's pre-stream 409 path); when omitted the turn reserves for
    itself.  The ``finally`` releases through the idempotent reservation, so
    a second release from the response lifecycle is a no-op.
    """

    if reservation is None:
        reservation = await reserve_turn(store, session_id)
    session = reservation.session

    timeout_seconds = _resolved_limit(
        turn_timeout, "HAUTE_ASSISTANT_TURN_TIMEOUT", DEFAULT_TURN_TIMEOUT
    )
    tool_limit = int(
        _resolved_limit(max_tool_calls, "HAUTE_ASSISTANT_MAX_TOOL_CALLS", DEFAULT_MAX_TOOL_CALLS)
    )
    deadline = time.monotonic() + timeout_seconds
    user_message: dict[str, Any] = {"role": "user", "content": user_text}
    request_messages: list[Mapping[str, Any]] = [
        *store.history_window(session),
        user_message,
    ]
    turn_messages: list[dict[str, Any]] = [user_message]
    total_input_tokens = 0
    total_output_tokens = 0
    tool_count = 0
    round_text: list[str] = []
    round_calls: list[ToolCallRequest] = []
    round_results: list[dict[str, Any]] = []
    round_committed = False
    active_stream: AsyncGenerator[Any, None] | Any = None

    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                if time.monotonic() >= deadline:
                    raise _TurnLimitError("Assistant time limit exceeded.")
                round_text = []
                round_calls = []
                round_results = []
                round_committed = False
                stop: TurnStop | None = None
                # The stream is owned so the outer finally can aclose() it:
                # an abnormal turn exit must shut the provider's SDK stream
                # deterministically, never leave it to GC finalisation.
                active_stream = provider.stream_turn(
                    system=system_prompt,
                    messages=request_messages,
                    tools=tools,
                )
                async for event in active_stream:
                    if isinstance(event, TextDelta):
                        round_text.append(event.text)
                        yield AssistantTextDeltaEvent(text=event.text)
                    elif isinstance(event, ToolCallRequest):
                        # Limits are checked BEFORE the call is recorded: a
                        # turn aborted here must not persist a tool call with
                        # no result — the next provider request would carry an
                        # orphaned call and be rejected wholesale.
                        if tool_count >= tool_limit:
                            raise _TurnLimitError("Assistant tool-call limit exceeded.")
                        if time.monotonic() >= deadline:
                            raise _TurnLimitError("Assistant time limit exceeded.")
                        if execute_tool is None:
                            raise RuntimeError("assistant tool executor is not configured")
                        round_calls.append(event)
                        tool_count += 1
                        yield AssistantToolStartedEvent(
                            id=event.id,
                            name=event.name,
                            summary=_compact_summary(event.arguments),
                        )
                        payload, interrupt = await _execute_shielded(execute_tool, event)
                        is_error = "error" in payload
                        if interrupt is None:
                            yield AssistantToolFinishedEvent(
                                id=event.id,
                                name=event.name,
                                is_error=is_error,
                                summary=_result_summary(payload, is_error),
                            )
                        if "graph_fingerprint" in payload and interrupt is None:
                            fingerprint = payload["graph_fingerprint"]
                            if not isinstance(fingerprint, str):
                                raise RuntimeError("graph_fingerprint must be a string")
                            yield AssistantGraphUpdatedEvent(fingerprint=fingerprint)
                        round_results.append(_tool_result_message(event, payload, is_error))
                        if interrupt is not None:
                            # Re-raise the original interrupt (CancelledError
                            # or GeneratorExit) now that the completed tool
                            # result is recorded for the history append.
                            raise interrupt
                    elif isinstance(event, TurnStop):
                        stop = event
                        total_input_tokens += event.usage.input_tokens
                        total_output_tokens += event.usage.output_tokens
                        break
                    else:
                        raise RuntimeError("provider returned an unknown event")

                # Per-round close: `break` on TurnStop leaves the provider
                # generator suspended at its yield, and the next round would
                # reassign `active_stream` and orphan this one — every
                # round's SDK stream is shut before the next opens.  The
                # outer finally remains the abnormal-exit guard.
                await _aclose_quietly(active_stream)
                active_stream = None

                if stop is None:
                    raise RuntimeError("provider stream ended without a turn stop")
                if stop.reason == "end":
                    _append_round(turn_messages, round_text, round_calls, round_results)
                    round_committed = True
                    yield AssistantCompletedEvent(
                        usage=AssistantUsage(
                            input_tokens=total_input_tokens,
                            output_tokens=total_output_tokens,
                        )
                    )
                    return

                _append_round(turn_messages, round_text, round_calls, round_results)
                round_committed = True
                request_messages.extend(
                    [
                        message
                        for message in (
                            _assistant_message(round_text, round_calls),
                            *round_results,
                        )
                        if message is not None
                    ]
                )
    except _TurnLimitError as exc:
        yield AssistantFailedEvent(message=str(exc))
    except TimeoutError:
        yield AssistantFailedEvent(message="Assistant time limit exceeded.")
    except AssistantProviderError as exc:
        yield AssistantFailedEvent(message=str(exc))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("assistant_turn_failed", exc_info=True)
        yield AssistantFailedEvent(message=_INTERNAL_ERROR_DETAIL)
    finally:
        # Abnormal-exit guard: the happy path already closed each round's
        # stream; a limit, error, cancel, or generator close lands here with
        # the current round's stream still open.
        await _aclose_quietly(active_stream)
        if not round_committed:
            _append_round(turn_messages, round_text, round_calls, round_results)
        store.append(session, turn_messages)
        reservation.release()


__all__ = [
    "ConcurrentTurnError",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_TURN_TIMEOUT",
    "TurnReservation",
    "UnknownSessionError",
    "build_system_prompt",
    "reserve_turn",
    "run_turn",
    "summarise_graph_nodes",
]
