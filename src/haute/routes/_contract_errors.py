"""Shared HTTP/background adapter for versioned public contract errors."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException

from haute._api_input_schema import ApiInputSchemaError
from haute._output_assembler import OutputNestingKeyError
from haute.errors import (
    ChunkMemoryRiskError,
    ContractResolutionError,
    GroupByExecutionUnsupportedError,
    HauteError,
    InputPreparationError,
    LiveSwitchScenarioError,
    PreambleError,
    RatingExtremaUndefinedError,
    RatingFactorDtypeContractError,
    RatingFactorMissingError,
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

# ``except`` accepts a tuple stored in a variable.  Exporting one canonical
# tuple prevents synchronous and background adapters from drifting apart.
PUBLIC_CONTRACT_ERROR_TYPES: tuple[type[HauteError], ...] = (
    ApiInputSchemaError,
    PreambleError,
    ContractResolutionError,
    ChunkMemoryRiskError,
    GroupByExecutionUnsupportedError,
    TraceCorrelationUnsupportedError,
    RatingExtremaUndefinedError,
    RatingFactorMissingError,
    RatingFactorDtypeContractError,
    LiveSwitchScenarioError,
    OutputNestingKeyError,
    InputPreparationError,
)


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


def contract_error_http_exception(exc: BaseException) -> HTTPException:
    """Map a public contract error to its synchronous-route response."""

    return HTTPException(
        status_code=CONTRACT_ERROR_HTTP_STATUS,
        detail=contract_error_payload(exc),
    )


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
        "http_status_code": CONTRACT_ERROR_HTTP_STATUS,
    }
