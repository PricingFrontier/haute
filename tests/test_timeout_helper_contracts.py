"""Focused contracts for blocking-route timeout helpers."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from haute.routes._timeouts import (
    _drain_background_future_result,
    run_blocking_with_response_timeout,
)


def test_drain_background_future_result_logs_ordinary_exception() -> None:
    class FailedFuture:
        def result(self) -> None:
            raise RuntimeError("late worker failure")

    with patch("haute.routes._timeouts.logger") as mock_logger:
        _drain_background_future_result(FailedFuture())  # type: ignore[arg-type]

    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.args == ("route_work_background_failed",)
    assert mock_logger.error.call_args.kwargs["error"] == "late worker failure"
    assert mock_logger.error.call_args.kwargs["error_type"] == "RuntimeError"
    assert mock_logger.error.call_args.kwargs["exc_info"] is True


def test_drain_background_future_result_does_not_swallow_base_exception() -> None:
    class FatalFuture:
        def result(self) -> None:
            raise SystemExit("fatal")

    with pytest.raises(SystemExit, match="fatal"):
        _drain_background_future_result(FatalFuture())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_blocking_with_response_timeout_returns_worker_result() -> None:
    result = await run_blocking_with_response_timeout(
        lambda value: value * 2,
        21,
        timeout=0.5,
        operation="unit_test",
    )

    assert result == 42


@pytest.mark.asyncio
async def test_run_blocking_with_response_timeout_reraises_worker_exception() -> None:
    def blow_up() -> None:
        raise ValueError("worker failed")

    with pytest.raises(ValueError, match="worker failed"):
        await run_blocking_with_response_timeout(
            blow_up,
            timeout=0.5,
            operation="unit_test",
        )


@pytest.mark.asyncio
async def test_run_blocking_with_response_timeout_logs_and_reraises_timeout() -> None:
    def sleep_past_deadline() -> str:
        time.sleep(0.05)
        return "late"

    with patch("haute.routes._timeouts.logger") as mock_logger:
        with pytest.raises(TimeoutError):
            await run_blocking_with_response_timeout(
                sleep_past_deadline,
                timeout=0.01,
                operation="unit_timeout",
            )

    mock_logger.warning.assert_called_once()
    assert "unit_timeout" in str(mock_logger.warning.call_args)


@pytest.mark.asyncio
async def test_run_blocking_with_response_timeout_cancellation_waits_for_worker() -> None:
    started = threading.Event()
    release_worker = threading.Event()
    finished = threading.Event()

    def wait_for_release() -> str:
        started.set()
        release_worker.wait(1)
        finished.set()
        return "done"

    task = asyncio.create_task(
        run_blocking_with_response_timeout(
            wait_for_release,
            timeout=1,
            operation="unit_cancel",
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()
    await asyncio.sleep(0.05)

    assert not task.done()
    assert not finished.is_set()

    release_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set()


@pytest.mark.asyncio
async def test_request_cancellation_wins_over_late_worker_exception() -> None:
    started = threading.Event()
    release_worker = threading.Event()

    def fail_after_release() -> None:
        started.set()
        release_worker.wait(1)
        raise ValueError("late worker failure")

    task = asyncio.create_task(
        run_blocking_with_response_timeout(
            fail_after_release,
            timeout=1,
            operation="unit_cancel_failure",
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    with patch("haute.routes._timeouts.logger") as mock_logger:
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.kwargs["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_request_cancellation_wins_over_late_worker_base_exception() -> None:
    class FatalWorkerError(BaseException):
        pass

    started = threading.Event()
    release_worker = threading.Event()

    def fail_after_release() -> None:
        started.set()
        release_worker.wait(1)
        raise FatalWorkerError("fatal late worker failure")

    task = asyncio.create_task(
        run_blocking_with_response_timeout(
            fail_after_release,
            timeout=1,
            operation="unit_cancel_fatal_failure",
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    with patch("haute.routes._timeouts.logger") as mock_logger:
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.kwargs["error_type"] == "FatalWorkerError"
