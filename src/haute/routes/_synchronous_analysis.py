"""Short analyses of a data point that answer within the request.

Banding statistics, rating levels, and pivot members read a leased point under
an admitted execution context, so they share the memory controls of every other
analysis: admission and memory-limit failures are HTTP 507 with the execution
error payload, and a client that disconnects cancels the running collection.
Results are memoised by ``(point identity digest, data_version, request
digest)``, so a refreshed, widened, or rewritten point is always recomputed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from fastapi import HTTPException, Request

from haute._analysis_results import SynchronousAnalysisCache
from haute._data_points import (
    CacheRequiredError,
    DataPoint,
    DataPointResolver,
    LeasedPointFrame,
    PointDataChangedError,
)
from haute._execution_admission import ExecutionAdmissionError, create_admitted_execution_context
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._node_snapshots import NodeSnapshotColumns

ResultT = TypeVar("ResultT")

CLIENT_DISCONNECT_POLL_SECONDS = 0.2
CLIENT_CLOSED_REQUEST_STATUS = 499


def run_synchronous_analysis(
    resolver: DataPointResolver,
    point: DataPoint,
    demand: NodeSnapshotColumns,
    *,
    operation: str,
    request: object,
    cache: SynchronousAnalysisCache,
    compute: Callable[[LeasedPointFrame, ExecutionContext], ResultT],
    cancellation_token: ExecutionCancellationToken | None = None,
) -> ResultT:
    """Return the analysis of the point's current data, computing it at most once per version.

    ``request`` is the JSON-safe analysis request; ``compute`` must finish its
    collections before returning, because the lease ends with this call.
    Raises :class:`CacheRequiredError` when the point is not current, and
    :class:`PointDataChangedError` when a direct file was rewritten while the
    analysis read it — that result is neither returned nor memoised.
    """
    resolution = resolver.resolve(point, demand)
    if resolution.state != "current" or resolution.data_version is None:
        raise CacheRequiredError(resolution)
    point_digest = resolver.point_digest(point)
    request_digest = SynchronousAnalysisCache.request_digest(request)
    cached = cache.get(point_digest, resolution.data_version, request_digest)
    if cached is not None:
        return cast(ResultT, cached)
    context: ExecutionContext | None = None
    try:
        context = create_admitted_execution_context(
            operation=operation,
            profile=ExecutionProfile.EXPLORE_ANALYSIS,
            job_id=None,
            cancellation_token=cancellation_token,
        )
        with resolver.lease_resolved(resolution, execution_context=context) as leased:
            result = compute(leased, context)
            data_version = leased.data_version
            if resolution.kind == "data_input" and resolution.input_identity is None:
                # A direct file is not pinned by the lease. An analysis that
                # read it while it was being rewritten describes data no
                # version names, so it is neither memoised nor returned: the
                # client asks again for whatever the file holds now.
                if resolver.resolve(point, demand).data_version != data_version:
                    raise PointDataChangedError(resolution)
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        raise HTTPException(status_code=507, detail=exc.to_payload()) from None
    finally:
        if context is not None:
            context.release_admission()
    cache.put(point_digest, data_version, request_digest, result)
    return result


async def run_until_disconnected(
    request: Request,
    analysis: Callable[[ExecutionCancellationToken], ResultT],
    *,
    poll_seconds: float = CLIENT_DISCONNECT_POLL_SECONDS,
) -> ResultT:
    """Run a blocking analysis off the event loop; a disconnected client cancels it.

    The analysis always finishes (normally or by cancellation) before this
    returns, so its admission is released before the request ends.
    """
    token = ExecutionCancellationToken()
    return await await_until_disconnected(
        request,
        asyncio.to_thread(analysis, token),
        cancel=token.cancel,
        poll_seconds=poll_seconds,
        detail="The client closed the request before the analysis finished.",
    )


async def await_until_disconnected(
    request: Request,
    work: Awaitable[ResultT],
    *,
    cancel: Callable[[], None],
    started: Callable[[], bool] | None = None,
    poll_seconds: float = CLIENT_DISCONNECT_POLL_SECONDS,
    detail: str = "The client closed the request before it finished.",
) -> ResultT:
    """Await *work*; a disconnected client calls *cancel* and waits for it to stop.

    *cancel* asks running work to stop through its own cancellation token
    rather than cancelling the task, so it unwinds through its own cleanup and
    releases its admission before this returns. Work that has not *started*
    (still queued for a slot) holds nothing yet, so its task is cancelled
    outright instead of waiting for a slot it no longer needs.
    """
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                return task.result()
            if await request.is_disconnected():
                _abandon(task, cancel, started)
                await _stopped(task)
                raise HTTPException(status_code=CLIENT_CLOSED_REQUEST_STATUS, detail=detail)
    except asyncio.CancelledError:
        _abandon(task, cancel, started)
        await _stopped(task)
        raise


def _abandon(
    task: asyncio.Future[ResultT],
    cancel: Callable[[], None],
    started: Callable[[], bool] | None,
) -> None:
    """Ask abandoned work to stop: running work through its token, queued work outright."""
    cancel()
    if started is not None and not started():
        task.cancel()


async def _stopped(task: asyncio.Future[ResultT]) -> None:
    """Wait for an abandoned analysis to finish so it releases its admission.

    The caller no longer wants the result, so every outcome the analysis reports
    (its own cancellation, a memory failure, or an unexpected error) is discarded
    here; the point of waiting is that nothing keeps running, holding admission
    and a lease, once the request is gone.
    """
    # The request task may be cancelled again while it waits (a client that
    # goes away is cancelled more than once); each time, it keeps waiting, so
    # admission is never released under work that is still running.
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(task)
