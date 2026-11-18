"""Every worker failure has one HTTP answer, whichever worker raised it."""

from __future__ import annotations

import pytest

from haute._worker_failures import (
    WorkerBusyError,
    WorkerCrashedError,
    WorkerError,
    WorkerHostError,
    WorkerMemoryLimitError,
    WorkerMemoryLimitUnsupportedError,
    WorkerPreemptedError,
    WorkerProtocolError,
    WorkerRemoteError,
    WorkerStartError,
    WorkerStoppedError,
    WorkerTerminationError,
    WorkerTimeoutError,
)
from haute.errors import RequestRefusedError
from haute.routes._worker_failure_refusals import worker_failure_refusal
from tests._failure_assertions import assert_unnamed, without_message


def _remote(
    *, module: str, type_: str = "MemoryError", is_memory: bool = True
) -> WorkerRemoteError:
    return WorkerRemoteError(
        remote_type=type_,
        remote_module=module,
        remote_message="boom",
        remote_traceback="traceback",
        is_memory=is_memory,
    )


def _answer(exc: WorkerError, **kwargs: object) -> RequestRefusedError:
    return worker_failure_refusal(
        exc, operation="pipeline_write_output", timed_out="took too long", **kwargs
    )


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        pytest.param(WorkerMemoryLimitError(rss_bytes=2, rss_limit_bytes=1), 507, id="rss"),
        pytest.param(WorkerMemoryLimitUnsupportedError(memory_limit_bytes=1), 507, id="no-cap"),
        pytest.param(WorkerCrashedError(-9, memory_limited=True), 507, id="memory-crash"),
        pytest.param(WorkerCrashedError(3), 500, id="crash"),
        pytest.param(WorkerTimeoutError(5), 504, id="timeout"),
        pytest.param(WorkerStoppedError("cancelled"), 409, id="stopped"),
        pytest.param(WorkerStartError("not ready"), 503, id="start"),
        pytest.param(WorkerHostError("resource tracker died"), 503, id="host"),
        pytest.param(WorkerProtocolError("malformed envelope"), 500, id="protocol"),
        pytest.param(WorkerTerminationError(), 500, id="termination"),
        # Another request holds or took the worker: a conflict to retry, not a fault.
        pytest.param(WorkerBusyError("slot held"), 409, id="busy"),
        pytest.param(WorkerPreemptedError(), 409, id="preempted"),
    ],
)
def test_each_failure_class_has_one_status(failure: WorkerError, status: int) -> None:
    assert _answer(failure).status_code == status


def test_a_timeout_answers_the_routes_own_detail() -> None:
    assert _answer(WorkerTimeoutError(5)).detail == "took too long"


def test_a_memory_outcome_carries_the_cap_the_worker_ran_under() -> None:
    answered = _answer(WorkerMemoryLimitError(rss_bytes=2, rss_limit_bytes=1), memory_limit_bytes=8)

    assert without_message(answered.detail) == {
        "error_code": "memory_limit",
        "operation": "pipeline_write_output",
        "reason": "worker_rss_limit_exceeded",
        "memory_limit_bytes": 8,
        "rss_bytes": 2,
        "rss_limit_bytes": 1,
    }


def test_a_one_shot_workers_remote_memory_error_is_a_memory_outcome() -> None:
    """A one-shot worker names no module, so the child's own MemoryError check decides."""
    answered = _answer(_remote(module=""))

    assert answered.status_code == 507
    assert answered.detail["reason"] == "worker_memory_limit"  # type: ignore[index]


def test_a_remote_naming_another_module_is_a_memory_outcome_only_by_identity() -> None:
    assert _answer(_remote(module="builtins")).status_code == 507
    assert _answer(_remote(module="third_party.plugin")).status_code == 500


def test_anything_else_is_a_generic_500() -> None:
    answered = _answer(_remote(module="", type_="ValueError", is_memory=False))

    assert answered.status_code == 500
    assert_unnamed(answered.detail)


def test_a_routes_own_remote_answer_is_asked_before_the_generic_500() -> None:
    teapot = RequestRefusedError("mine", status_code=418)
    answered = _answer(
        _remote(module="haute.mine", type_="MineError", is_memory=False),
        remote=lambda exc: teapot if exc.remote_type == "MineError" else None,
    )

    assert answered is teapot
