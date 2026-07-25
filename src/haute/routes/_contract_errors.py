"""Shared HTTP/background adapter for versioned public contract errors."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from haute._output_assembler import OutputNestingKeyError
from haute.errors import (
    ChunkMemoryRiskError,
    ContractResolutionError,
    GroupByExecutionUnsupportedError,
    HauteError,
    LiveSwitchScenarioError,
    PreambleError,
    RatingExtremaUndefinedError,
    RatingFactorDtypeContractError,
    RatingFactorMissingError,
    TraceCorrelationUnsupportedError,
    is_public_contract_error,
)

CONTRACT_ERROR_HTTP_STATUS = 422
CONTRACT_ERROR_TERMINAL_REASON = "contract_error"

# ``except`` accepts a tuple stored in a variable.  Exporting one canonical
# tuple prevents synchronous and background adapters from drifting apart.
PUBLIC_CONTRACT_ERROR_TYPES: tuple[type[HauteError], ...] = (
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
)


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
    return {
        "error": str(exc),
        "error_detail": payload,
        "error_code": payload["error_code"],
        "http_status_code": CONTRACT_ERROR_HTTP_STATUS,
    }
