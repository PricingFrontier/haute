"""HTTP routes for the pricing assistant."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from haute._git import GitHistoryReadError
from haute._logging import get_logger
from haute._sandbox import contained_path
from haute.assistant import _loop, assistant_readiness
from haute.assistant._catalog import tool_title
from haute.assistant._config import AssistantConfig, resolve_assistant_config
from haute.assistant._ops import AssistantOperationError
from haute.assistant._providers import AssistantProvider, create_provider
from haute.assistant._render import render_turn_context
from haute.assistant._session import AssistantSession, SessionStore
from haute.assistant._tools import (
    TOOL_DEFINITIONS,
    TurnContextError,
    application_service,
    build_tool_executor,
    build_turn_context,
    context_update,
)
from haute.errors import ConfigError, HauteError, InvalidPathError, PathOutsideProjectError
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    save_lock,
)
from haute.routes._save_pipeline import StaleDocumentRevisionError
from haute.schemas import (
    AssistantCancelledEvent,
    AssistantChangeRecord,
    AssistantMessageRequest,
    AssistantSessionListResponse,
    AssistantSessionRequest,
    AssistantSessionResponse,
    AssistantSessionSummary,
    AssistantStatusResponse,
    AssistantTranscriptEntry,
    AssistantUndoRequest,
    AssistantUndoResponse,
)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
logger = get_logger(component="server.assistant")


def _sessions_storage_dir() -> Path:
    """Per-clone chat history lives in the untracked `.haute/` state dir.

    Resolved per call (like the tool layer's cwd use) — the server's working
    directory is the project root.
    """

    project_root = Path.cwd().resolve()
    storage = project_root / ".haute" / "assistant" / "sessions"
    if not storage.resolve().is_relative_to(project_root):
        raise ConfigError("Assistant session storage must resolve inside the project root")
    return storage


session_store = SessionStore(storage_dir=_sessions_storage_dir)


def _provider_factory(config: AssistantConfig) -> AssistantProvider:
    """Construct the configured adapter without importing optional SDKs here."""

    return create_provider(config)


def _canonical_source_file(source_file: str) -> str:
    """Resolve the canvas document's source file to the session binding.

    The path must stay inside the project root and name a discovered pipeline;
    the binding is its POSIX project-relative spelling, the same spelling the
    editor document's ``source_file`` carries. There is no default pipeline:
    the assistant edits exactly the file the canvas shows.
    """

    root = Path.cwd().resolve()
    target = contained_path(root, source_file)
    for path in discover_pipelines():
        resolved = path.resolve()
        if resolved == target:
            return resolved.relative_to(root).as_posix()
    raise HTTPException(
        status_code=404,
        detail=f"No pipeline file '{source_file}' was found in this project",
    )


async def _bound_source_file(source_file: str) -> str:
    """Resolve a request's source file, translating failures at the HTTP edge.

    Path containment errors propagate to the shared path-error handlers (403
    for a path outside the project, 400 for an unusable one).
    """

    try:
        return await asyncio.to_thread(_canonical_source_file, source_file)
    except (HTTPException, PathOutsideProjectError, InvalidPathError):
        raise
    except HauteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        detail = _http_error_detail(exc, "session_source_resolution")
        raise HTTPException(status_code=500, detail=detail) from None


def _http_error_detail(exc: Exception, operation: str) -> str:
    """Expose typed domain errors and sanitize unexpected implementation errors."""

    if isinstance(exc, HauteError):
        return str(exc)
    logger.error(
        "assistant_route_operation_failed",
        operation=operation,
        error=type(exc).__name__,
        exc_info=True,
    )
    return _INTERNAL_ERROR_DETAIL


def _readiness() -> AssistantStatusResponse:
    """Read readiness and translate malformed configuration at the HTTP edge."""

    try:
        status = assistant_readiness()
    except HauteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_http_error_detail(exc, "readiness")) from None
    return AssistantStatusResponse(
        configured=status.configured,
        reason=status.reason,
        provider=status.provider,
        model=status.model,
        endpoint_host=status.endpoint_host,
        trust=status.trust,
        max_sensitivity=status.max_sensitivity,
        mutations_enabled=status.mutations_enabled,
        mutations_reason=status.mutations_reason,
    )


@router.get("/status", response_model=AssistantStatusResponse)
def get_assistant_status() -> AssistantStatusResponse:
    """Return configuration and working-branch readiness without network calls."""

    return _readiness()


def _transcript_entries(session: AssistantSession) -> list[AssistantTranscriptEntry]:
    """Map a session's stored neutral history to rehydratable transcript entries.

    Tool entries reuse the same compact result summary and finished title the
    live stream shows; the persisted message's explicit ``is_error`` flag is
    authoritative. A successful apply's stored change record follows its tool
    entry as a ``change`` entry, as the live ``change_applied`` event did. A
    turn stored with an outcome ends with one ``outcome`` entry carrying it,
    the value its live ``completed`` event carried.
    """

    entries: list[AssistantTranscriptEntry] = []
    for turn in session.history:
        for message in turn.messages:
            if message.role in {"user", "assistant"} and isinstance(message.content, str):
                if message.content:
                    entries.append(
                        AssistantTranscriptEntry(
                            kind="user" if message.role == "user" else "assistant",
                            text=message.content,
                        )
                    )
            if message.role == "tool":
                content = message.content if isinstance(message.content, dict) else {}
                is_error = message.is_error
                name = message.name or ""
                entries.append(
                    AssistantTranscriptEntry(
                        kind="tool",
                        name=name,
                        title=tool_title(name, {}, content),
                        summary=_loop._result_summary(content, is_error),
                        is_error=is_error,
                    )
                )
                if "change" in content:
                    entries.append(
                        AssistantTranscriptEntry(
                            kind="change",
                            change=AssistantChangeRecord.model_validate(content["change"]),
                        )
                    )
        if turn.outcome is not None:
            entries.append(AssistantTranscriptEntry(kind="outcome", outcome=turn.outcome))
        entries.extend(
            AssistantTranscriptEntry(kind="undo", change=change) for change in turn.undone
        )
    return entries


@router.get("/sessions", response_model=AssistantSessionListResponse)
async def list_assistant_sessions(
    source_file: str = Query(min_length=1),
) -> AssistantSessionListResponse:
    """List the canvas pipeline's saved conversations, most recently used first.

    A conversation belongs to the source file it was bound to, and another
    pipeline's chats are never offered here.
    """

    bound_source = await _bound_source_file(source_file)
    summaries = session_store.list_sessions(bound_source)
    return AssistantSessionListResponse(
        source_file=bound_source,
        sessions=[
            AssistantSessionSummary(
                session_id=summary.session_id,
                title=summary.title,
                created_at=summary.created_at,
                last_used=summary.last_used,
                message_count=summary.message_count,
            )
            for summary in summaries
        ],
    )


@router.post("/session", response_model=AssistantSessionResponse)
async def create_assistant_session(body: AssistantSessionRequest) -> AssistantSessionResponse:
    """Create a session bound to the canvas pipeline's source file.

    A prior `session_id` is a resume offer: when it revives (memory or disk)
    and is bound to the same source file, the same session returns with
    its transcript; any other case creates a fresh session.
    """

    source_file = await _bound_source_file(body.source_file)
    if body.session_id is not None:
        existing = session_store.resume(body.session_id, source_file)
        if existing is not None:
            return AssistantSessionResponse(
                session_id=existing.id,
                source_file=existing.source_file,
                history=_transcript_entries(existing),
                build_plan=existing.build_plan.current,
            )

    session = session_store.create(source_file)
    return AssistantSessionResponse(
        session_id=session.id,
        source_file=session.source_file,
        build_plan=session.build_plan.current,
    )


async def _event_stream(
    *,
    request: AssistantMessageRequest,
    provider: AssistantProvider,
    execute_tool: _loop.ToolExecutor,
    system_prompt: str,
    reservation: _loop.TurnReservation,
    turn_context: str,
    refresh_context: _loop.ContextRefresher,
) -> AsyncIterator[str]:
    """Frame loop events as server-sent events."""

    turn = _loop.run_turn(
        session_store,
        request.session_id,
        request.message,
        provider=provider,
        tools=TOOL_DEFINITIONS,
        execute_tool=execute_tool,
        system_prompt=system_prompt,
        turn_timeout=None,
        max_tool_calls=None,
        reservation=reservation,
        turn_context=turn_context,
        refresh_context=refresh_context,
    )
    try:
        async for event in turn:
            yield f"data: {event.model_dump_json()}\n\n"
    except asyncio.CancelledError:
        yield f"data: {AssistantCancelledEvent().model_dump_json()}\n\n"
        raise
    finally:
        # Deterministic teardown: when this generator is closed while parked
        # at a yield (mid-stream send failure), the inner turn must be closed
        # NOW — not whenever GC finalises it — so history lands and the
        # provider stream shuts before the reservation frees the session.
        await turn.aclose()


class _ReservedStreamingResponse(StreamingResponse):
    """A streaming response that can never leak its turn reservation.

    ``run_turn``'s ``finally`` releases in every path where the body
    iterator actually runs — but a client that disconnects before the ASGI
    layer starts iterating would otherwise leave the session locked forever
    (permanent 409).  The response's own lifecycle is awaited exactly once
    by the server, so releasing in its ``finally`` closes that gap; the
    reservation is idempotent, so the double release in normal operation is
    a no-op.
    """

    def __init__(self, *args: Any, reservation: _loop.TurnReservation, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._reservation = reservation

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Close the (possibly still-suspended) turn BEFORE releasing: a
            # mid-stream send failure leaves the generator parked at a yield,
            # and aclose() drives run_turn's teardown — provider stream
            # closed, in-flight shielded tool drained, history appended —
            # so the next turn can never interleave with a zombie turn.
            close = getattr(self.body_iterator, "aclose", None)
            try:
                if close is not None:
                    await close()
            finally:
                self._reservation.release()


@router.post("/message", response_class=StreamingResponse)
async def post_assistant_message(body: AssistantMessageRequest) -> StreamingResponse:
    """Start one provider turn and return its typed SSE event stream."""

    readiness = await asyncio.to_thread(_readiness)
    if not readiness.configured:
        raise HTTPException(
            status_code=400, detail=readiness.reason or "Assistant is not configured"
        )
    # The source file needs no session, so it resolves before the reservation;
    # comparing it with the session's binding waits until the session is held.
    source_file = await _bound_source_file(body.source_file)

    # Reserve the one-turn lock atomically BEFORE any awaited pre-work: a
    # bare `lock.locked()` check here would let two simultaneous sends both
    # pass during the parse await below, turning the specified pre-stream
    # 409 into an unhandled mid-stream failure for the loser.
    try:
        reservation = await _loop.reserve_turn(session_store, body.session_id)
    except _loop.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Unknown assistant session") from None
    except _loop.ConcurrentTurnError:
        raise HTTPException(
            status_code=409, detail="An assistant turn is already running"
        ) from None
    session = reservation.session

    try:
        if source_file != session.source_file:
            # The canvas shows another pipeline: running the turn would edit
            # a file the analyst is not looking at.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This chat belongs to pipeline '{session.source_file}'. "
                    "Open that pipeline to continue it, or start a new chat."
                ),
            )
        try:
            config = resolve_assistant_config()
            provider = _provider_factory(config)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except HauteError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except Exception as exc:
            detail = _http_error_detail(exc, "provider_factory")
            raise HTTPException(status_code=502, detail=detail) from None

        request_context = body.context
        # The changes the analyst undid since the model's last turn.
        undone = session.history[-1].undone if session.history else ()
        try:
            system_prompt = _loop.build_system_prompt(source_file=session.source_file)
            async with save_lock:
                gathered = await asyncio.to_thread(
                    build_turn_context,
                    session.source_file,
                    config.egress,
                    selected_node_ids=(
                        () if request_context is None else request_context.selected_node_ids
                    ),
                    preview_error_node_id=(
                        None if request_context is None else request_context.preview_error_node_id
                    ),
                    undone=undone,
                    build_plan=session.build_plan.current,
                )
            turn_context = render_turn_context(gathered)
        except TurnContextError as exc:
            # The canvas that sent this context shows a graph the saved file no
            # longer has.
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except HauteError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        except Exception as exc:
            detail = _http_error_detail(exc, "turn_context")
            raise HTTPException(status_code=500, detail=detail) from None
        execute_tool = build_tool_executor(
            session.source_file,
            session_id=session.id,
            evidence=session.evidence,
            plan=session.build_plan,
        )
    except BaseException:
        # Pre-stream failure after the reservation: the turn will never run,
        # so the lock must be released here or the session deadlocks.
        reservation.release()
        raise

    return _ReservedStreamingResponse(
        _event_stream(
            request=body,
            provider=provider,
            execute_tool=execute_tool,
            system_prompt=system_prompt,
            reservation=reservation,
            turn_context=turn_context,
            refresh_context=partial(context_update, session.source_file, config.egress),
        ),
        media_type="text/event-stream",
        reservation=reservation,
    )


def _latest_change(session: AssistantSession, change_id: str) -> AssistantChangeRecord:
    """The latest stored record of *change_id*: one plan can be saved again after an undo."""

    for turn in reversed(session.history):
        for message in reversed(turn.messages):
            content = message.content
            if message.role != "tool" or not isinstance(content, dict):
                continue
            change = content.get("change")
            if isinstance(change, dict) and change.get("id") == change_id:
                return AssistantChangeRecord.model_validate(change)
    raise HTTPException(status_code=404, detail="This chat has no change with that id")


@router.post("/changes/undo", response_model=AssistantUndoResponse)
async def undo_assistant_change(body: AssistantUndoRequest) -> AssistantUndoResponse:
    """Undo one change card: save the version before it, then note it in the chat
    and the build plan.

    The session is reserved like a turn, so an undo never interleaves with one.
    """

    source_file = await _bound_source_file(body.source_file)
    try:
        reservation = await _loop.reserve_turn(session_store, body.session_id)
    except _loop.UnknownSessionError:
        raise HTTPException(status_code=404, detail="Unknown assistant session") from None
    except _loop.ConcurrentTurnError:
        raise HTTPException(
            status_code=409, detail="An assistant turn is running; undo after it ends"
        ) from None
    session = reservation.session
    try:
        if source_file != session.source_file:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This chat belongs to pipeline '{session.source_file}'. "
                    "Open that pipeline to undo its changes."
                ),
            )
        change = _latest_change(session, body.change_id)
        try:
            result = await application_service(session_id=session.id).undo(
                session.source_file, change
            )
        except (AssistantOperationError, GitHistoryReadError, StaleDocumentRevisionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        session_store.record_undo(session, change)
    finally:
        reservation.release()
    return AssistantUndoResponse(
        change_id=change.id, git_sha=result.git_sha, build_plan=session.build_plan.current
    )


__all__ = ["router", "session_store"]
