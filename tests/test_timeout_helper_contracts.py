"""Focused contracts for blocking-route timeout helpers."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from haute.routes._timeouts import run_blocking_with_response_timeout


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
