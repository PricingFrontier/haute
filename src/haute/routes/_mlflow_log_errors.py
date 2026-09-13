"""HTTP outcomes for the modelling and optimiser "log to MLflow" routes.

Both routes report failures the same way: a configuration problem is a ``400``
with haute's own message, a missing MLflow package is the shared ``503``, a
classified remote failure is a ``502`` whose structured detail carries
``error_code`` (``mlflow_<category>``) and write-specific copy, and anything
unclassified is the generic ``500``. The underlying exception text is never
logged or returned.
"""

from __future__ import annotations

from fastapi import HTTPException

from haute._logging import get_logger
from haute._mlflow_errors import (
    MLFLOW_LOG_FAILURE_MESSAGES,
    MLFLOW_NOT_INSTALLED_DETAIL,
    MLFLOW_NOT_INSTALLED_STATUS,
    MlflowRemoteError,
    classify_mlflow_error,
)
from haute.errors import MlflowConfigError
from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

logger = get_logger(component="server.mlflow_log")

MLFLOW_REMOTE_FAILURE_STATUS = 502


def require_mlflow_installed() -> None:
    """Fail with the shared ``503`` before any work when MLflow cannot be imported."""
    try:
        import mlflow  # noqa: F401
    except ImportError:
        raise HTTPException(
            status_code=MLFLOW_NOT_INSTALLED_STATUS, detail=MLFLOW_NOT_INSTALLED_DETAIL
        ) from None


def mlflow_log_http_exception(exc: BaseException, *, job_id: str) -> HTTPException:
    """Map a failed MLflow log to its HTTP outcome, logging category and type only."""
    if isinstance(exc, MlflowConfigError):
        return HTTPException(status_code=400, detail=str(exc))
    category = classify_mlflow_error(exc)
    logger.error(
        "mlflow_log_failed",
        category=category,
        error_type=type(exc).__name__,
        job_id=job_id,
    )
    if isinstance(exc, MlflowRemoteError):
        message = exc.message
    elif category == "unknown":
        return HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
    else:
        message = MLFLOW_LOG_FAILURE_MESSAGES[category]
    # Structured so the Export surfaces can dispatch on the code (for example to
    # offer "Test connection") instead of parsing text or the HTTP status.
    remote_detail = {"error_code": f"mlflow_{category}", "message": message}
    return HTTPException(status_code=MLFLOW_REMOTE_FAILURE_STATUS, detail=remote_detail)
