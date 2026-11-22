"""Shared HTTP/background adapter for versioned public contract errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import polars as pl

from haute._api_input_schema import ApiInputSchemaError
from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionMemoryLimitExceededError
from haute._failure_answers import memory_answer
from haute._output_assembler import OutputNestingKeyError
from haute._workbench_input import WorkbenchInputError
from haute._workbench_tables import WorkbenchTablesError
from haute.errors import (
    BatchSourceAmbiguousError,
    ConfigError,
    ContractMismatchError,
    ContractResolutionError,
    DataOutputRowsError,
    GroupByExecutionUnsupportedError,
    HauteError,
    InputPreparationError,
    InsufficientMemoryError,
    LiveSwitchScenarioError,
    NodeConfigError,
    NodeFailedError,
    ParseError,
    PreambleError,
    QuoteAnswerRowsError,
    QuoteInputError,
    RatingExtremaUndefinedError,
    RatingFactorDtypeContractError,
    RatingFactorMissingError,
    RequestRefusedError,
    SchemaMismatchError,
    SeedPlanExpiredError,
    SnapshotCorruptError,
    SnapshotPlanInputsChangedError,
    TraceCorrelationUnsupportedError,
    is_public_contract_error,
)

CONTRACT_ERROR_HTTP_STATUS = 422
CONTRACT_ERROR_TERMINAL_REASON: Literal["contract_error"] = "contract_error"
# Automatic input preparation is the one public contract error that can report
# memory exhaustion. A background job records the same terminal state and the
# same ``memory_limit`` code the in-thread memory-limited paths already use.
MEMORY_LIMITED_TERMINAL_REASON: Literal["memory_limited"] = "memory_limited"
MEMORY_LIMITED_HTTP_STATUS = 507
MEMORY_LIMITED_ERROR_CODE = "memory_limit"
# A trace whose preview's seed plan expired conflicts with data that moved
# underneath it: the preview is refreshed, not the request corrected.
SEED_PLAN_EXPIRED_HTTP_STATUS = 409

# ``except`` accepts a tuple stored in a variable.  Exporting one canonical
# tuple prevents synchronous and background adapters from drifting apart.
PUBLIC_CONTRACT_ERROR_TYPES: tuple[type[HauteError], ...] = (
    ApiInputSchemaError,
    PreambleError,
    ContractResolutionError,
    GroupByExecutionUnsupportedError,
    TraceCorrelationUnsupportedError,
    NodeFailedError,
    RatingExtremaUndefinedError,
    RatingFactorMissingError,
    RatingFactorDtypeContractError,
    LiveSwitchScenarioError,
    BatchSourceAmbiguousError,
    NodeConfigError,
    OutputNestingKeyError,
    InputPreparationError,
    SnapshotCorruptError,
    SnapshotPlanInputsChangedError,
    SeedPlanExpiredError,
    WorkbenchTablesError,
    WorkbenchInputError,
    QuoteAnswerRowsError,
    QuoteInputError,
)


#: The pipeline's own failures: its code, contracts, settings or data, which a preview of
#: the node shows as its error. A job or a write reports one as the user's to fix, with
#: ``pipeline_error_message``, never as an internal failure. Checked after the public
#: contract errors, one of which (``NodeConfigError``) is also a ``ConfigError``.
PIPELINE_ERROR_TYPES: tuple[type[Exception], ...] = (
    ContractMismatchError,
    SchemaMismatchError,
    ConfigError,
    ParseError,
    DataOutputRowsError,
    pl.exceptions.PolarsError,
)


def is_pipeline_error(exc: BaseException) -> bool:
    """Whether *exc* is one of the ``PIPELINE_ERROR_TYPES`` with no ``MemoryError`` behind it:
    running out of memory under any translation is a memory outcome, never the user's error."""
    from haute._memory_errors import memory_error_in

    return isinstance(exc, PIPELINE_ERROR_TYPES) and memory_error_in(exc) is None


def memory_error_refusal(exc: BaseException, *, operation: str) -> InsufficientMemoryError:
    """Out of memory behind *exc* (``memory_error_in``) as a service refuses with it (507): the
    ``memory_limit`` payload a hard-capped worker's memory outcome carries."""
    from haute._worker_failures import worker_memory_detail

    return InsufficientMemoryError(
        worker_memory_detail(exc, operation=operation, memory_limit_bytes=None)
    )


def raise_memory_behind(exc: BaseException) -> None:
    """Raise the ``MemoryError`` behind *exc*, if any, so a worker reports it as memory."""
    from haute._memory_errors import memory_error_in

    memory = memory_error_in(exc)
    if memory is not None:
        raise memory


def pipeline_error_message(exc: Exception) -> str:
    """What a job or a write says of one of the ``PIPELINE_ERROR_TYPES``: its message, a
    Polars error's as a preview records it for the node (without the plan Polars appends,
    whose scans print absolute cache paths)."""
    from haute._execute_lazy import node_failure_message

    return node_failure_message(exc)


def _is_memory_limited_contract_error(exc: BaseException) -> bool:
    """Whether a public contract error reports memory exhaustion."""
    return isinstance(exc, InputPreparationError) and exc.reason_code == "memory_limited"


def contract_error_terminal_reason(
    exc: BaseException,
) -> Literal["contract_error", "memory_limited"]:
    """Terminal background-job reason for a public contract error."""
    contract_error_payload(exc)
    if _is_memory_limited_contract_error(exc):
        return MEMORY_LIMITED_TERMINAL_REASON
    return CONTRACT_ERROR_TERMINAL_REASON


def contract_error_payload(exc: BaseException) -> dict[str, Any]:
    """Return the stable public payload or reject a non-public exception."""

    if not isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES) or not is_public_contract_error(exc):
        raise TypeError(f"{type(exc).__name__} is not a public contract error")
    payload = exc.to_payload()
    if not isinstance(payload.get("error_code"), str):
        raise TypeError(f"{type(exc).__name__} has no stable public error code")
    return payload


def contract_error_refusal(exc: BaseException) -> RequestRefusedError:
    """A public contract error as a service refuses with it: its status and stable payload."""

    return RequestRefusedError(
        contract_error_payload(exc), status_code=_contract_error_http_status(exc)
    )


def _contract_error_http_status(exc: BaseException) -> int:
    if _is_memory_limited_contract_error(exc):
        return MEMORY_LIMITED_HTTP_STATUS
    if isinstance(exc, SeedPlanExpiredError):
        return SEED_PLAN_EXPIRED_HTTP_STATUS
    return CONTRACT_ERROR_HTTP_STATUS


def memory_limit_refusal(
    exc: ExecutionAdmissionError | ExecutionMemoryLimitExceededError,
    *,
    operation_noun: str | None = None,
) -> InsufficientMemoryError:
    """A memory refusal or overrun as a service refuses with it (507): the exception's
    structured payload and its curated message (``haute._failure_answers.memory_answer``),
    naming the work as *operation_noun* when the surface gives one. ``str(exc)`` names
    internal operations and raw byte counts, so it never becomes the message.
    """
    answer = memory_answer(exc, noun=operation_noun)
    if answer is None or not isinstance(answer.detail, Mapping):  # pragma: no cover - typed
        raise TypeError(f"{type(exc).__name__} is not a memory refusal")
    return InsufficientMemoryError(answer.detail)


def contract_error_job_fields(exc: BaseException) -> dict[str, Any]:
    """Map a public contract error to stable background-job fields."""

    payload = contract_error_payload(exc)
    if _is_memory_limited_contract_error(exc):
        return {
            "error": str(exc),
            "error_detail": payload,
            "error_code": MEMORY_LIMITED_ERROR_CODE,
            "http_status_code": MEMORY_LIMITED_HTTP_STATUS,
        }
    return {
        "error": str(exc),
        "error_detail": payload,
        "error_code": payload["error_code"],
        "http_status_code": _contract_error_http_status(exc),
    }
