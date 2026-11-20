"""The words a deployed service's 507 carries: the limit that refused it, and its setting."""

from __future__ import annotations

import pytest

from haute._execution_context import ExecutionProfile
from haute.deploy._memory_answers import memory_answer

_GIB = 1024**3


@pytest.fixture(autouse=True)
def _no_memory_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_BYTES",
        "HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB",
        "HAUTE_EXECUTION_MEMORY_LIMIT_BYTES",
        "HAUTE_EXECUTION_MEMORY_LIMIT_MB",
        "HAUTE_DEPLOY_BATCH_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_DEPLOY_BATCH_PROCESS_RSS_LIMIT_MB",
        "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_BYTES",
        "HAUTE_EXECUTION_PROCESS_RSS_LIMIT_MB",
    ):
        monkeypatch.delenv(name, raising=False)


def _refusal(**fields: object) -> dict[str, object]:
    return {"error_code": "memory_limit", "operation": "deploy_quote", **fields}


@pytest.mark.parametrize(
    ("payload", "words"),
    [
        (
            _refusal(
                reason="in_flight_memory_budget_exceeded",
                memory_limit_bytes=4 * _GIB,
                in_flight_reserved_bytes=0,
                in_flight_limit_bytes=3 * _GIB,
            ),
            [
                "this server can give only 3.0 GiB",
                "gives a request of several quotes 4.0 GiB of memory",
                "set by HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB",
                "Lower HAUTE_DEPLOY_BATCH_MEMORY_LIMIT_MB",
            ],
        ),
        (
            _refusal(
                reason="in_flight_memory_budget_exceeded",
                memory_limit_bytes=2 * _GIB,
                in_flight_reserved_bytes=2 * _GIB,
                in_flight_limit_bytes=3 * _GIB,
            ),
            ["requests being priced already hold 2.0 GiB of the 3.0 GiB", "once they finish"],
        ),
        (
            _refusal(reason="worker_rss_limit_exceeded", memory_limit_bytes=_GIB),
            ["needed more memory than the service gives", "Raise HAUTE_DEPLOY_BATCH_MEMORY"],
        ),
        (
            _refusal(
                reason="process_rss_limit_exceeded",
                memory_limit_bytes=_GIB,
                process_rss_limit_bytes=2 * _GIB,
            ),
            ["memory cap of 2.0 GiB", "HAUTE_DEPLOY_BATCH_PROCESS_RSS_LIMIT_MB"],
        ),
    ],
    ids=["budget-larger-than-server", "other-requests", "worker-cap", "process-cap"],
)
def test_a_memory_refusal_names_the_limit_and_the_setting(
    payload: dict[str, object], words: list[str]
) -> None:
    answer = memory_answer(payload, profile=ExecutionProfile.DEPLOY_BATCH)

    assert {key: answer[key] for key in payload} == payload
    for phrase in words:
        assert phrase in answer["message"], answer["message"]


def test_the_setting_in_force_is_the_one_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAUTE_EXECUTION_MEMORY_LIMIT_MB", "1024")

    answer = memory_answer(
        _refusal(reason="worker_rss_limit_exceeded", memory_limit_bytes=_GIB),
        profile=ExecutionProfile.DEPLOY_BATCH,
    )

    assert "set by HAUTE_EXECUTION_MEMORY_LIMIT_MB" in answer["message"]
