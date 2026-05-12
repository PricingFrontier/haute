"""Request-body limits for deployed scoring endpoints."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

_MIB = 1024 * 1024

DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES = 8 * _MIB
DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES_ENV = "HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES"
DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB_ENV = "HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB"


class RequestLike(Protocol):
    headers: Mapping[str, str]

    def stream(self) -> AsyncIterator[bytes]: ...


class RequestBodyLimitError(ValueError):
    """Raised when a request body exceeds the configured deploy wire limit."""

    def __init__(
        self,
        *,
        operation: str,
        max_request_body_bytes: int,
        observed_request_body_bytes: int,
        reason: str,
        content_length_bytes: int | None,
    ) -> None:
        super().__init__(reason)
        self.operation = operation
        self.max_request_body_bytes = max_request_body_bytes
        self.observed_request_body_bytes = observed_request_body_bytes
        self.reason = reason
        self.content_length_bytes = content_length_bytes

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "request_body_too_large",
            "operation": self.operation,
            "max_request_body_bytes": self.max_request_body_bytes,
            "content_length_bytes": self.content_length_bytes,
            "observed_request_body_bytes": self.observed_request_body_bytes,
            "reason": self.reason,
        }


class RequestBodyHeaderError(ValueError):
    """Raised when request size metadata is malformed."""

    def __init__(self, *, operation: str, reason: str, content_length_header: str) -> None:
        super().__init__(reason)
        self.operation = operation
        self.reason = reason
        self.content_length_header = content_length_header

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "invalid_request_body_header",
            "operation": self.operation,
            "header": "content-length",
            "content_length_header": self.content_length_header,
            "reason": self.reason,
        }


class RequestBodyParseError(ValueError):
    """Raised when a bounded request body is not valid JSON."""

    def __init__(self, *, operation: str, reason: str) -> None:
        super().__init__(reason)
        self.operation = operation
        self.reason = reason

    def to_payload(self) -> dict[str, object]:
        return {
            "error_code": "invalid_json",
            "operation": self.operation,
            "reason": self.reason,
        }


def deploy_quote_request_body_limit_bytes(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve the deployed `/quote` JSON request-body limit."""
    env = os.environ if environ is None else environ
    raw_bytes = env.get(DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES_ENV)
    if raw_bytes is not None:
        return _positive_int_from_config(DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES_ENV, raw_bytes)
    raw_mb = env.get(DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB_ENV)
    if raw_mb is not None:
        return _positive_int_from_config(
            DEPLOY_QUOTE_REQUEST_BODY_LIMIT_MB_ENV,
            raw_mb,
            multiplier=_MIB,
        )
    return DEFAULT_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES


def _positive_int_from_config(name: str, raw: str, *, multiplier: int = 1) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value * multiplier


async def read_limited_json_body(
    request: RequestLike,
    *,
    operation: str,
    limit_bytes: int,
) -> Any:
    """Read and decode JSON after enforcing a hard byte limit."""
    if limit_bytes < 1:
        raise ValueError("limit_bytes must be positive")

    content_length_bytes = _content_length_bytes(request, operation=operation)
    if content_length_bytes is not None and content_length_bytes > limit_bytes:
        raise RequestBodyLimitError(
            operation=operation,
            max_request_body_bytes=limit_bytes,
            observed_request_body_bytes=content_length_bytes,
            content_length_bytes=content_length_bytes,
            reason="content_length_exceeds_limit",
        )

    chunks: list[bytes] = []
    observed_bytes = 0
    async for chunk in request.stream():
        observed_bytes += len(chunk)
        if observed_bytes > limit_bytes:
            raise RequestBodyLimitError(
                operation=operation,
                max_request_body_bytes=limit_bytes,
                observed_request_body_bytes=observed_bytes,
                content_length_bytes=content_length_bytes,
                reason="stream_exceeds_limit",
            )
        chunks.append(chunk)

    try:
        return json.loads(b"".join(chunks))
    except json.JSONDecodeError as exc:
        raise RequestBodyParseError(operation=operation, reason="invalid_json") from exc


def _content_length_bytes(request: RequestLike, *, operation: str) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RequestBodyHeaderError(
            operation=operation,
            reason="invalid_content_length",
            content_length_header=raw,
        ) from exc
    if value < 0:
        raise RequestBodyHeaderError(
            operation=operation,
            reason="negative_content_length",
            content_length_header=raw,
        )
    return value
