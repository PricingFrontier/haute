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
from haute.assistant._providers import AnthropicProvider, AssistantProvider, OpenAIProvider
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
    AssistantSessionRequest,
    AssistantSessionResponse,
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

    return Path.cwd() / ".haute" / "assistant" / "sessions"


session_store = SessionStore(storage_dir=_sessions_storage_dir)


def _provider_factory(config: AssistantConfig) -> AssistantProvider:
    """Construct the configured adapter without importing optional SDKs here."""

    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "openai":
        return OpenAIProvider(config)
    raise ConfigError(f"Unknown assistant provider: {config.provider!r}.")


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
    a structured tool error is recognised by its `{"error": ...}` content
    shape — the shape `_tools` produces and `_result_summary` renders.
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
                is_error = "error" in content
                entries.append(
                    AssistantTranscriptEntry(
                        kind="tool",
                        name=message.name or "",
                        summary=_loop._result_summary(content, is_error),
                        is_error=is_error,
                    )
                )
    return entries


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
        existing = session_store.lookup(body.session_id)
        if existing is not None and existing.source_file == source_file:
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
            if close is not None:
                await close()
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
    except BaseException:
        # Pre-stream failure after the reservation: the turn will never run,
        # so the lock must be released here or the session deadlocks.
        reservation.release()
        raise

    execute_tool = build_tool_executor(session.source_file)
    return _ReservedStreamingResponse(
        _event_stream(
            request=body,
            provider=provider,
            execute_tool=execute_tool,
            system_prompt=system_prompt,
            reservation=reservation,
        ),
        media_type="text/event-stream",
        reservation=reservation,
    )


__all__ = ["router", "session_store"]
