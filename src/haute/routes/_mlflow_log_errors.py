"""Refusals for the modelling and optimiser "log to MLflow" routes.

Both routes report failures the same way: a configuration problem is a ``400``
with haute's own message, a missing MLflow package is the shared ``503``, a
classified remote failure is a ``502`` whose structured detail carries
``error_code`` (``mlflow_<category>``) and write-specific copy, and anything
unclassified is an incident (``haute._failure_answers.unexpected_failure``) whose
record holds the category and type only. The underlying exception text is never
logged or returned.
"""

from __future__ import annotations

from haute._failure_answers import unexpected_failure
from haute._logging import get_logger
from haute._mlflow_errors import (
    MLFLOW_LOG_FAILURE_MESSAGES,
    MLFLOW_NOT_INSTALLED_DETAIL,
    MLFLOW_NOT_INSTALLED_STATUS,
    MlflowRemoteError,
    classify_mlflow_error,
)
from haute.errors import InvalidRequestError, MlflowConfigError, RequestRefusedError

logger = get_logger(component="server.mlflow_log")

MLFLOW_REMOTE_FAILURE_STATUS = 502


def require_mlflow_installed() -> None:
    """Fail with the shared ``503`` before any work when MLflow cannot be imported."""
    try:
        import mlflow  # noqa: F401
    except ImportError:
        raise RequestRefusedError(
            MLFLOW_NOT_INSTALLED_DETAIL, status_code=MLFLOW_NOT_INSTALLED_STATUS
        ) from None


def mlflow_log_refusal(exc: BaseException, *, job_id: str) -> RequestRefusedError:
    """Map a failed MLflow log to its refusal, logging category and type only: an MLflow
    error's text can hold a tracking server's address and its credentials."""
    if isinstance(exc, MlflowConfigError):
        return InvalidRequestError(str(exc))
    category = classify_mlflow_error(exc)
    if category == "unknown" and not isinstance(exc, MlflowRemoteError):

        def record(incident: str, failed: BaseException, **_known: object) -> None:
            logger.error(
                "mlflow_log_failed",
                category=category,
                error_type=type(failed).__name__,
                job_id=job_id,
                incident=incident,
            )

        return unexpected_failure(exc, operation="mlflow_log", record=record).refusal()
    logger.error(
        "mlflow_log_failed",
        category=category,
        error_type=type(exc).__name__,
        job_id=job_id,
    )
    if isinstance(exc, MlflowRemoteError):
        message = exc.message
    else:
        message = MLFLOW_LOG_FAILURE_MESSAGES[category]
    # Structured so the Export surfaces can dispatch on the code (for example to
    # offer "Test connection") instead of parsing text or the HTTP status.
    remote_detail = {"error_code": f"mlflow_{category}", "message": message}
    return RequestRefusedError(remote_detail, status_code=MLFLOW_REMOTE_FAILURE_STATUS)
