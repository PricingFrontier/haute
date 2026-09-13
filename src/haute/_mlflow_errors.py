"""Classify MLflow failures without repeating their text.

MLflow and transport exceptions can carry tokens, credential-bearing URIs and
infrastructure detail, so haute never shows or logs their text. It reports a
stable category instead, with copy written for the operation that failed.

Connectivity is reported only for transport failures (refused or reset
connections, DNS failures, timeouts). Any other ``OSError`` is not
connectivity: a local folder that cannot be written is unknown, so a disk
problem is never presented as an unreachable server.
"""

from __future__ import annotations

import socket
from typing import Literal

from haute.errors import HauteError

MlflowFailureCategory = Literal[
    "authentication",
    "permission",
    "missing_resource",
    "connectivity",
    "unknown",
]

MLFLOW_NOT_INSTALLED_STATUS = 503
MLFLOW_NOT_INSTALLED_DETAIL = "MLflow is not installed. Install it with: pip install mlflow"

_AUTHENTICATION_CODES = frozenset({"UNAUTHENTICATED", "INVALID_LOGIN", "CUSTOMER_UNAUTHORIZED"})
_PERMISSION_CODES = frozenset({"PERMISSION_DENIED"})
_MISSING_RESOURCE_CODES = frozenset({"RESOURCE_DOES_NOT_EXIST", "ENDPOINT_NOT_FOUND"})
_HTTP_STATUS_CATEGORIES: dict[int, MlflowFailureCategory] = {
    401: "authentication",
    403: "permission",
    404: "missing_resource",
}

MLFLOW_LOG_FAILURE_MESSAGES: dict[MlflowFailureCategory, str] = {
    "authentication": (
        "MLflow rejected the credentials, so the run was not logged. Check the MLflow "
        "credentials and test the connection in MLflow settings."
    ),
    "permission": (
        "MLflow denied permission to log the run. Choose an experiment path you can "
        "write to, or ask for access to this one."
    ),
    "missing_resource": (
        "MLflow could not find the experiment or its workspace folder, so the run was "
        "not logged. Check the experiment path."
    ),
    "connectivity": (
        "Could not reach the MLflow tracking server, so the run was not logged. Test "
        "the connection in MLflow settings, then log again."
    ),
    "unknown": "Logging to MLflow failed. Check the server logs for details.",
}


class MlflowRemoteError(HauteError):
    """An MLflow write failed for a classified reason, with copy for the user.

    Raised where haute knows more than the category alone says (for example
    which workspace folder could not be created); routes report its message.
    """

    def __init__(self, category: MlflowFailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_mlflow_error(exc: BaseException) -> MlflowFailureCategory:
    """Map an MLflow or transport failure to a stable category.

    Inspects the whole ``__cause__``/``__context__`` chain, because MLflow wraps
    transport failures in ``MlflowException``: a structured MLflow error code or
    HTTP status decides first, then a transport exception means connectivity.

    Classification runs inside error handlers, so it never raises itself: when
    ``mlflow.exceptions`` or ``requests`` cannot be imported (MLflow missing or
    replaced by a stub), no link can be one of their exceptions and only the
    remaining rules apply.
    """
    mlflow_exception_types: tuple[type[BaseException], ...] = ()
    rest_exception_types: tuple[type[BaseException], ...] = ()
    http_error_types: tuple[type[BaseException], ...] = ()
    transport_types: tuple[type[BaseException], ...] = (
        ConnectionError,
        TimeoutError,
        socket.gaierror,
    )
    try:
        from mlflow.exceptions import MlflowException, RestException
    except ImportError:
        pass
    else:
        mlflow_exception_types = (MlflowException,)
        rest_exception_types = (RestException,)
    try:
        import requests
    except ImportError:
        pass
    else:
        http_error_types = (requests.exceptions.HTTPError,)
        transport_types = (
            *transport_types,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )
    for link in _exception_chain(exc):
        if isinstance(link, MlflowRemoteError):
            return link.category
        if isinstance(link, mlflow_exception_types):
            code = getattr(link, "error_code", "")
            if code in _AUTHENTICATION_CODES:
                return "authentication"
            if code in _PERMISSION_CODES:
                return "permission"
            if code in _MISSING_RESOURCE_CODES:
                return "missing_resource"
            if isinstance(link, rest_exception_types):
                return "unknown"
            # A plain MlflowException usually wraps the transport failure.
            continue
        if isinstance(link, http_error_types):
            status = getattr(getattr(link, "response", None), "status_code", None)
            if status in _HTTP_STATUS_CATEGORIES:
                return _HTTP_STATUS_CATEGORIES[status]
            return "unknown"
        if isinstance(link, transport_types):
            return "connectivity"
    return "unknown"
