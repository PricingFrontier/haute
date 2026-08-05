"""HTTP routes for the pricing assistant."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from haute._logging import get_logger
from haute.assistant import _loop, assistant_readiness
from haute.assistant._config import AssistantConfig, resolve_assistant_config
from haute.assistant._providers import AssistantProvider, create_provider
from haute.assistant._session import AssistantSession, SessionStore
from haute.assistant._tools import TOOL_DEFINITIONS, build_tool_executor
from haute.errors import ConfigError, HauteError
from haute.graph_utils import PipelineGraph
from haute.routes._helpers import (
    _INTERNAL_ERROR_DETAIL,
    discover_pipelines,
    lookup_pipeline_by_name,
    parse_pipeline_to_graph,
    raise_pipeline_not_found,
)
from haute.schemas import (
    AssistantCancelledEvent,
    AssistantMessageRequest,
    AssistantSessionListResponse,
    AssistantSessionRequest,
    AssistantSessionResponse,
    AssistantSessionSummary,
    AssistantStatusResponse,
    AssistantTranscriptEntry,
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


def _relative_source_file(path: Path) -> str:
    """Return the source spelling used by the existing pipeline route."""

    cwd = Path.cwd().resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(path)


def _find_default_pipeline() -> tuple[Path, PipelineGraph] | None:
    """Find the first non-empty pipeline using GET /api/pipeline's ordering."""

    cwd = Path.cwd()
    best: tuple[Path, PipelineGraph] | None = None
    for path in discover_pipelines():
        try:
            graph = parse_pipeline_to_graph(path)
            graph.source_file = str(path.relative_to(cwd))
            if graph.nodes:
                return path, graph
            if best is None:
                best = path, graph
        except Exception as exc:
            logger.warning(
                "assistant_pipeline_parse_failed",
                file=path.name,
                error=type(exc).__name__,
            )
    return best


def _resolve_pipeline(name: str | None) -> tuple[Path, PipelineGraph]:
    """Resolve an explicit named pipeline or the default active pipeline."""

    if name is not None:
        path = lookup_pipeline_by_name(name)
        if path is None:
            raise_pipeline_not_found(name)
        graph = parse_pipeline_to_graph(path)
        graph.source_file = _relative_source_file(path)
        return path, graph

    resolved = _find_default_pipeline()
    if resolved is None:
        raise HTTPException(status_code=404, detail="No pipeline was found")
    return resolved


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

    Tool entries reuse the same compact result summary the live stream shows;
    the persisted message's explicit ``is_error`` flag is authoritative.
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
                entries.append(
                    AssistantTranscriptEntry(
                        kind="tool",
                        name=message.name or "",
                        summary=_loop._result_summary(content, is_error),
                        is_error=is_error,
                    )
                )
                if not is_error and "graph_fingerprint" in content:
                    graph_fingerprint = content["graph_fingerprint"]
                    if not isinstance(graph_fingerprint, str):
                        raise TypeError("tool graph_fingerprint must be a string")
                    entries.append(
                        AssistantTranscriptEntry(
                            kind="tool",
                            name="graph_updated",
                            summary="Canvas updated",
                            is_error=False,
                        )
                    )
    return entries


@router.get("/sessions", response_model=AssistantSessionListResponse)
async def list_assistant_sessions(pipeline: str | None = None) -> AssistantSessionListResponse:
    """List this pipeline's saved conversations, most recently used first.

    The panel opens on this list, so it resolves the pipeline the same way
    session creation does: a conversation belongs to the source file it was
    bound to, and another pipeline's chats are never offered here.
    """

    try:
        path, _graph = await asyncio.to_thread(_resolve_pipeline, pipeline)
    except HTTPException:
        raise
    except HauteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        detail = _http_error_detail(exc, "session_pipeline_resolution")
        raise HTTPException(status_code=500, detail=detail) from None

    summaries = await asyncio.to_thread(
        session_store.list_sessions,
        _relative_source_file(path),
    )
    return AssistantSessionListResponse(
        sessions=[
            AssistantSessionSummary(
                session_id=summary.session_id,
                title=summary.title,
                created_at=summary.created_at,
                last_used=summary.last_used,
                message_count=summary.message_count,
            )
            for summary in summaries
        ]
    )


@router.post("/session", response_model=AssistantSessionResponse)
async def create_assistant_session(body: AssistantSessionRequest) -> AssistantSessionResponse:
    """Create a session bound to an existing pipeline source file.

    A prior `session_id` is a resume offer: when it revives (memory or disk)
    and is bound to the same resolved pipeline, the same session returns with
    its transcript; any other case creates a fresh session.
    """

    try:
        path, _graph = await asyncio.to_thread(_resolve_pipeline, body.pipeline)
    except HTTPException:
        raise
    except HauteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:
        detail = _http_error_detail(exc, "session_pipeline_resolution")
        raise HTTPException(status_code=500, detail=detail) from None

    source_file = _relative_source_file(path)
    if body.session_id is not None:
        existing = session_store.resume(body.session_id, source_file)
        if existing is not None:
            return AssistantSessionResponse(
                session_id=existing.id,
                history=_transcript_entries(existing),
            )

    session = session_store.create(source_file)
    return AssistantSessionResponse(session_id=session.id)


async def _event_stream(
    *,
    request: AssistantMessageRequest,
    provider: AssistantProvider,
    execute_tool: _loop.ToolExecutor,
    system_prompt: str,
    reservation: _loop.TurnReservation,
    authoring_request: str,
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
        authoring_request=authoring_request,
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

        try:
            graph = await asyncio.to_thread(parse_pipeline_to_graph, Path(session.source_file))
            pipeline_name = graph.pipeline_name or Path(session.source_file).stem
            system_prompt = _loop.build_system_prompt(
                pipeline_name=pipeline_name,
                source_file=session.source_file,
                node_summary=_loop.summarise_graph_nodes(graph),
            )
        except HauteError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        except Exception as exc:
            detail = _http_error_detail(exc, "system_prompt")
            raise HTTPException(status_code=500, detail=detail) from None
        authoring_request = _loop.effective_authoring_request(session, body.message)
        execute_tool = build_tool_executor(
            session.source_file,
            session_id=session.id,
            prior_messages=session_store.history_window(session),
            authoring_request=authoring_request,
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
            authoring_request=authoring_request,
        ),
        media_type="text/event-stream",
        reservation=reservation,
    )


__all__ = ["router", "session_store"]
