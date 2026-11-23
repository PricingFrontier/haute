"""Every failure put into words once (specs/server-api/low-level.md, "Failure answers").

The editor's server, the underwriter app, a job's or a worker's outcome and the command line
each answer a failure through this module and through nothing else: an HTTP status, a stable
code, the message and what a job ends as, the same at every edge.

:func:`classify_failure` names the failures Haute knows wherever they are raised: a refusal a
service raised, a public contract error, memory running out however it arrives, a git or
settings or path refusal, a worker that could not finish, a node's own code failing, the
operating system refusing a file and a database refusing a statement or losing its
connection. :func:`pipeline_failure` adds, for an edge that ran the pipeline, the pipeline's
own contracts and data. :func:`unexpected_failure` answers anything else with an incident id
and records the traceback under it, so nothing is answered as a bare 500, a traceback or
"check the server logs". :func:`answer_failure` is the two together.

A failure raised in a worker is classified there (:func:`failure_payload`) and answered in
its parent from that payload (:func:`answer_from_payload`), so a file another program holds
or a node's own failure is named on both sides of the process boundary alike.
"""

from __future__ import annotations

import errno
import os
import pickle
import sys
import traceback
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from haute._logging import get_logger
from haute._memory_errors import memory_error_in
from haute.errors import (
    ConfigError,
    ContractMismatchError,
    HauteValidationError,
    InvalidPathError,
    NodeFailedError,
    ParseError,
    PathOutsideProjectError,
    RequestRefusedError,
    SchemaMismatchError,
    TraceRequestError,
    failing_node,
    failing_node_name,
    is_public_contract_error,
)

logger = get_logger(component="failure_answers")

JobTerminalReason = Literal[
    "contract_error", "memory_limited", "error", "timed_out", "cancelled", "superseded"
]

#: The code of an answer to a failure nothing names.
UNEXPECTED_FAILURE_CODE = "unexpected_failure"
#: The key a worker's failure payload carries an answer under.
FAILURE_ANSWER_PAYLOAD_KEY = "failure_answer"

#: A refusal's code when its detail carries none, by its status.
_REFUSAL_CODES: dict[int, str] = {
    400: "invalid_request",
    401: "not_signed_in",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    412: "precondition_failed",
    413: "too_large",
    422: "unprocessable",
    499: "cancelled",
    500: "service_failed",
    502: "upstream_failed",
    503: "unavailable",
    504: "timed_out",
    507: "memory_limit",
}

# Windows' sharing and lock violations: another program has the file open.
_WINDOWS_FILE_IN_USE = frozenset({32, 33})
# The access denied Windows gives for a file it cannot replace or remove.
_WINDOWS_ACCESS_DENIED = 5


@dataclass(frozen=True)
class FailureAnswer:
    """What every edge says of one failure.

    *detail* is what an HTTP answer carries under ``detail``: the message alone, or a
    structured detail holding ``error_code`` and ``message`` with the fields a client reads.
    *incident* is set when nothing named the failure, and its traceback is recorded under
    it.
    """

    status: int
    code: str
    message: str
    terminal_reason: JobTerminalReason
    detail: str | Mapping[str, Any]
    incident: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def refusal(self) -> AnsweredFailureError:
        """This answer as a service raises it, for an edge further out to answer again."""
        return AnsweredFailureError(self)

    def job_fields(self) -> dict[str, Any]:
        """The fields a job's failed state records for this answer."""
        fields: dict[str, Any] = {
            "error": self.message,
            "error_code": self.code,
            # The detail an edge answers with, so a job replayed as a response says the same.
            "error_detail": dict(self.detail) if isinstance(self.detail, Mapping) else self.detail,
            "http_status_code": self.status,
        }
        if self.incident is not None:
            fields["incident"] = self.incident
        return fields

    def payload(self) -> dict[str, Any]:
        """The answer as plain data, for a worker to send its parent."""
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "terminal_reason": self.terminal_reason,
            "detail": dict(self.detail) if isinstance(self.detail, Mapping) else self.detail,
            "incident": self.incident,
            "headers": dict(self.headers),
        }


class AnsweredFailureError(RequestRefusedError):
    """A failure already put into words: every edge answers it with its :class:`FailureAnswer`."""

    def __init__(self, answer: FailureAnswer) -> None:
        super().__init__(answer.detail, status_code=answer.status, headers=answer.headers)
        self.answer = answer


def _loaded(module: str, *names: str) -> tuple[type[BaseException], ...]:
    """The exception types *names* of *module* when it is loaded, else none.

    An exception of a type whose module is not loaded cannot have been raised, so a check
    against it needs no import: classifying a failure imports nothing, wherever it runs (a
    worker's command thread included).
    """
    loaded = sys.modules.get(module)
    if loaded is None:
        return ()
    found = (getattr(loaded, name, None) for name in names)
    return tuple(kind for kind in found if isinstance(kind, type))


def _structured(code: str, message: str, **fields: Any) -> dict[str, Any]:
    return {"error_code": code, "message": message, **fields}


def _terminal_for_status(status: int) -> JobTerminalReason:
    if status == 499:
        return "cancelled"
    if status == 504:
        return "timed_out"
    if status == 507:
        return "memory_limited"
    return "error" if status >= 500 else "contract_error"


# ---------------------------------------------------------------------------
# Named failures
# ---------------------------------------------------------------------------


def classify_failure(exc: BaseException) -> FailureAnswer | None:
    """The answer to *exc* when Haute knows what it is, else ``None``.

    Memory is decided before anything else, so running out of it under any translation is
    never answered as something the request did. A failure the graph walk marked with the
    node it was running is that node's own and public (specs/optimiser/error-detail-policy.md),
    worded with what the operating system or the database said when one of them refused it.
    """
    if isinstance(exc, AnsweredFailureError):
        return exc.answer
    if isinstance(exc, RequestRefusedError):
        # A refusal a service worded deliberately, whatever it was raised during.
        return _refusal_answer(exc)
    memory = _memory_answer(exc)
    if memory is not None:
        return memory
    public = _public_contract_answer(exc)
    if public is not None:
        return public
    for classify in (_worker_answer, _domain_answer):
        answer = classify(exc)
        if answer is not None:
            return answer
    node = _node_answer(exc)
    if node is not None:
        return node
    if isinstance(exc, ParseError):
        # A graph the user can fix (a name collision, an undeclared handle): the message says
        # what to change and its context which element it is.
        return _plain(400, "graph_refused", str(exc))
    if isinstance(exc, ConfigError):
        # The message names the setting and what is wrong; its context (a path) is logged.
        return _plain(422, "config_invalid", exc.message or str(exc))
    if isinstance(exc, _loaded("haute._polars_io_registry", "PolarsIoConfigError")):
        # A Data Input's or Data Output's settings Haute cannot read with: a format whose
        # reader package this install lacks, arguments the format does not take. The
        # message names the setting; a route that must not echo posted settings refuses
        # them before they reach a reader.
        return _plain(422, "config_invalid", str(exc))
    if isinstance(exc, HauteValidationError):
        # A message Haute wrote about what was sent: the request is refused, never failed.
        return _plain(400, "validation_refused", str(exc))
    return file_answer(exc) or _database_answer(exc)


def pipeline_failure(exc: BaseException) -> FailureAnswer | None:
    """:func:`classify_failure`, and for an edge that ran the pipeline its own contracts,
    schemas and data too: a failure a preview of the node would show, said as it shows it
    (a Polars error without the plan it appends, whose scans print the cache's paths)."""
    answer = classify_failure(exc)
    if answer is not None:
        return answer
    import polars as pl

    from haute.errors import DataOutputRowsError

    if isinstance(
        exc,
        ContractMismatchError
        | SchemaMismatchError
        | DataOutputRowsError
        | pl.exceptions.PolarsError,
    ):
        return _plain(422, "pipeline_failed", node_failure_message(exc))
    return None


def node_failure_message(exc: BaseException) -> str:
    """A failure's message as a preview records it on its node."""
    from haute._execute_lazy import node_failure_message as recorded

    return recorded(exc) if isinstance(exc, Exception) else str(exc)


def _plain(status: int, code: str, message: str) -> FailureAnswer:
    """An answer whose HTTP detail is its message alone."""
    return FailureAnswer(status, code, message, _terminal_for_status(status), message)


def _refusal_answer(exc: RequestRefusedError) -> FailureAnswer:
    detail = exc.detail
    if isinstance(detail, Mapping):
        message = detail.get("message")
        code = detail.get("error_code")
    else:
        message, code = detail, None
    status = exc.status_code
    return FailureAnswer(
        status=status,
        code=code if isinstance(code, str) and code else _REFUSAL_CODES.get(status, "refused"),
        message=message if isinstance(message, str) and message else str(exc),
        terminal_reason=_terminal_for_status(status),
        detail=detail,
        headers=exc.headers,
    )


def _public_contract_answer(exc: BaseException) -> FailureAnswer | None:
    if not is_public_contract_error(exc):
        return None
    from haute.routes._contract_errors import (
        PUBLIC_CONTRACT_ERROR_TYPES,
        contract_error_job_fields,
        contract_error_refusal,
        contract_error_terminal_reason,
    )

    if not isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return None
    answer = _refusal_answer(contract_error_refusal(exc))
    return FailureAnswer(
        status=answer.status,
        # A memory-limited one is a memory outcome's code; its payload keeps its own.
        code=contract_error_job_fields(exc)["error_code"],
        message=answer.message,
        terminal_reason=contract_error_terminal_reason(exc),
        detail=answer.detail,
    )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

#: What each operation is called in a memory message; anything else is "This work".
_OPERATION_NOUNS: dict[str, str] = {
    "cli_run": "The run",
    "preview": "The preview",
    "trace": "The trace",
    "pipeline_write_output": "Writing the Data Output",
    "training_preparation": "Training",
    "training_job": "Training",
    "optimiser_setup": "The optimisation",
    "optimiser_solve": "The optimisation",
    "workbench_app_price": "Pricing the quote",
}


def operation_noun(operation: str | None) -> str:
    """What *operation* is called at the start of a sentence."""
    return _OPERATION_NOUNS.get(operation or "", "This work")


def memory_answer(
    exc: BaseException,
    *,
    operation: str | None = None,
    memory_limit_bytes: int | None = None,
    noun: str | None = None,
) -> FailureAnswer | None:
    """The memory outcome behind *exc*, or ``None``: a refusal before work started or a
    budget overrun with its own payload, and an allocation refused anywhere (a
    ``MemoryError``, the operating system's ``ENOMEM``, a Polars error carrying it) with
    the limit in force when the caller knows it. The work is named *noun* when the caller
    gives one, else by its *operation* (:func:`operation_noun`)."""
    found = memory_error_in(exc)
    if found is None:
        return None
    from haute._memory_messages import format_byte_size, memory_limit_user_message

    budgeted = _loaded("haute._execution_admission", "ExecutionAdmissionError") + _loaded(
        "haute._execution_context", "ExecutionMemoryLimitExceededError"
    )
    if isinstance(found, budgeted):
        # Loaded already: the refusal is one of their types.
        from haute._execution_admission import ExecutionAdmissionError
        from haute._execution_context import ExecutionMemoryLimitExceededError

        refused = cast(ExecutionAdmissionError | ExecutionMemoryLimitExceededError, found)
        named = noun or operation_noun(operation or refused.operation)
        message = memory_limit_user_message(refused, operation_noun=named)
        detail = {**refused.to_payload(), "message": message}
        return FailureAnswer(507, "memory_limit", message, "memory_limited", detail)
    limit = (
        ""
        if memory_limit_bytes is None
        else f" than the {format_byte_size(memory_limit_bytes)} it may use"
    )
    message = (
        f"{noun or operation_noun(operation)} needed more memory{limit} and was stopped. Use "
        "fewer rows or columns, or give Haute more memory, then try again."
    )
    detail = _structured("memory_limit", message, reason="out_of_memory")
    if operation is not None:
        detail["operation"] = operation
    if memory_limit_bytes is not None:
        detail["memory_limit_bytes"] = memory_limit_bytes
    return FailureAnswer(507, "memory_limit", message, "memory_limited", detail)


def _memory_answer(exc: BaseException) -> FailureAnswer | None:
    # A worker's memory outcome carries its own evidence (the watchdog's figures).
    if isinstance(exc, _loaded("haute._worker_failures", "WorkerError")):
        return None
    return memory_answer(exc)


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def worker_memory_answer(
    exc: BaseException, *, operation: str, memory_limit_bytes: int | None
) -> FailureAnswer:
    """A worker's memory outcome: the closed ``memory_limit`` payload, worded."""
    from haute._worker_failures import (
        WorkerMemoryLimitError,
        worker_failure_is_cap_unavailable,
        worker_memory_detail,
    )

    detail: dict[str, Any] = worker_memory_detail(
        exc, operation=operation, memory_limit_bytes=memory_limit_bytes
    )
    if worker_failure_is_cap_unavailable(exc):
        message = "This can't run because Haute can't enforce its memory limit on this machine."
    elif isinstance(exc, WorkerMemoryLimitError):
        message = (
            f"{operation_noun(operation)} used more memory than its limit and was stopped. Use "
            "fewer rows or columns, or give Haute more memory, then try again."
        )
    else:
        message = (
            f"{operation_noun(operation)} stopped abruptly, most likely because it ran out of "
            "memory. Use fewer rows or columns, or give Haute more memory, then try again."
        )
    detail["message"] = message
    return FailureAnswer(507, "memory_limit", message, "memory_limited", detail)


def _worker_answer(exc: BaseException) -> FailureAnswer | None:
    if not isinstance(exc, _loaded("haute._worker_failures", "WorkerError")):
        return None
    from haute._worker_failures import (
        WorkerBusyError,
        WorkerError,
        WorkerHostError,
        WorkerPreemptedError,
        WorkerRemoteError,
        WorkerRestartedError,
        WorkerStartError,
        WorkerStoppedError,
        WorkerTimeoutError,
        worker_failure_is_cap_unavailable,
        worker_failure_is_memory,
    )

    if not isinstance(exc, WorkerError):
        return None
    if isinstance(exc, WorkerRemoteError):
        remote = answer_from_remote(exc, operation=_WORKER_OPERATION)
        if remote is not None or exc.remote_module:
            # A remote that names its module is decided by its identity alone: a
            # same-named MemoryError from another module is not running out of memory.
            return remote
    # A worker that ran out of memory however it said so (an unnamed remote by the child's
    # own check), its own words or none.
    if worker_failure_is_cap_unavailable(exc) or worker_failure_is_memory(exc):
        return worker_memory_answer(exc, operation=_WORKER_OPERATION, memory_limit_bytes=None)
    message = str(exc)
    if isinstance(exc, WorkerTimeoutError):
        return _plain(504, "timed_out", message)
    if isinstance(exc, WorkerStoppedError):
        return FailureAnswer(
            409, "worker_stopped", message, _terminal_reason(exc.terminal_reason), message
        )
    if isinstance(exc, WorkerBusyError | WorkerPreemptedError):
        return FailureAnswer(409, "superseded", message, "superseded", message)
    if isinstance(exc, WorkerStartError | WorkerHostError):
        # No worker could run it: the host, not the request, is at fault, and the next
        # request starts a worker again.
        return _plain(503, "worker_unavailable", message)
    if isinstance(exc, WorkerRestartedError):
        # The worker stopped before the request reached it, and a new one is running.
        return _plain(503, "worker_restarted", message)
    # A crash with no memory evidence, a broken protocol or a worker that would not stop:
    # nothing the request did, so an incident.
    return None


#: The operation a worker's failure is answered for when no edge names its own.
_WORKER_OPERATION = "worker"

_TERMINAL_REASONS: tuple[JobTerminalReason, ...] = (
    "contract_error",
    "memory_limited",
    "error",
    "timed_out",
    "cancelled",
    "superseded",
)


def _terminal_reason(reason: object) -> JobTerminalReason:
    return cast(JobTerminalReason, reason) if reason in _TERMINAL_REASONS else "error"


def answer_from_remote(exc: BaseException, *, operation: str) -> FailureAnswer | None:
    """The answer a worker's own failure gave, classified in the worker, or ``None`` when it
    named nothing: memory by exact identity, a child's curated public contract payload, and
    any other failure the child put into words (:func:`failure_payload`)."""
    from haute._execution_admission import ExecutionAdmissionError
    from haute._execution_context import ExecutionMemoryLimitExceededError
    from haute._native_memory_limit import NativeMemoryLimitUnsupportedError
    from haute._worker_failures import WorkerRemoteError
    from haute.routes._contract_errors import PUBLIC_CONTRACT_ERROR_TYPES

    if not isinstance(exc, WorkerRemoteError):
        return None
    payload = exc.public_payload
    identity = (exc.remote_module, exc.remote_type)
    sent = payload.get(FAILURE_ANSWER_PAYLOAD_KEY) if isinstance(payload, Mapping) else None
    if isinstance(sent, Mapping):
        return answer_from_payload(sent)
    memory_identities = {
        (error_type.__module__, error_type.__name__)
        for error_type in (ExecutionAdmissionError, ExecutionMemoryLimitExceededError)
    }
    if (
        isinstance(payload, Mapping)
        and identity in memory_identities
        and payload.get("error_code") == "memory_limit"
    ):
        message = payload.get("message")
        worded = message if isinstance(message, str) and message else _memory_sentence(operation)
        return FailureAnswer(
            507, "memory_limit", worded, "memory_limited", {**payload, "message": worded}
        )
    # Memory outcomes the child cannot curate: classified by exact identity and answered
    # with a parent-authored detail. A same-named exception from any other module is not one.
    if identity == ("builtins", "MemoryError") or identity == (
        NativeMemoryLimitUnsupportedError.__module__,
        NativeMemoryLimitUnsupportedError.__name__,
    ):
        reason = (
            "worker_memory_exhausted"
            if identity[0] == "builtins"
            else "native_memory_cap_unavailable"
        )
        worded = _memory_sentence(operation)
        detail = _structured("memory_limit", worded, operation=operation, reason=reason)
        return FailureAnswer(507, "memory_limit", worded, "memory_limited", detail)
    public_codes = {
        (error_type.__module__, error_type.__name__): error_type.error_code
        for error_type in PUBLIC_CONTRACT_ERROR_TYPES
    }
    expected = public_codes.get(identity)
    if (
        isinstance(payload, Mapping)
        and expected is not None
        and payload.get("error_code") == expected
    ):
        from haute.errors import SeedPlanExpiredError
        from haute.routes._contract_errors import (
            CONTRACT_ERROR_HTTP_STATUS,
            SEED_PLAN_EXPIRED_HTTP_STATUS,
        )

        status = (
            SEED_PLAN_EXPIRED_HTTP_STATUS
            if expected == SeedPlanExpiredError.error_code
            else CONTRACT_ERROR_HTTP_STATUS
        )
        message = payload.get("message")
        return FailureAnswer(
            status,
            expected,
            message if isinstance(message, str) and message else exc.remote_message,
            "contract_error",
            dict(payload),
        )
    return None


def _memory_sentence(operation: str | None) -> str:
    return (
        f"{operation_noun(operation)} needed more memory than this machine could give it and "
        "was stopped. Use fewer rows or columns, or give Haute more memory, then try again."
    )


def failure_payload(exc: BaseException) -> dict[str, Any] | None:
    """What a worker sends its parent of *exc*: its own public payload when it has one, and
    its answer under :data:`FAILURE_ANSWER_PAYLOAD_KEY` when Haute names it here, where it
    was raised; ``None`` when it has neither."""
    payload: dict[str, Any] = {}
    to_payload = getattr(exc, "to_payload", None)
    if callable(to_payload):
        own = to_payload()
        if isinstance(own, dict):
            payload.update((str(key), value) for key, value in own.items())
    answer = classify_failure(exc)
    if answer is not None:
        payload[FAILURE_ANSWER_PAYLOAD_KEY] = answer.payload()
    return payload or None


def worker_failure_payload(exc: BaseException) -> dict[str, Any] | None:
    """:func:`failure_payload` as a worker's transport sends it: one that cannot be built or
    pickled is none, so the failure still crosses as its type, message and traceback, and
    its parent answers it with an incident."""
    try:
        payload = failure_payload(exc)
        pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # noqa: BLE001 - the worker's own failure is what is being reported
        return None
    return payload


def answer_from_payload(payload: Mapping[str, Any]) -> FailureAnswer:
    """The :class:`FailureAnswer` a worker sent (:meth:`FailureAnswer.payload`)."""
    detail = payload.get("detail")
    reason = payload.get("terminal_reason")
    headers = payload.get("headers")
    return FailureAnswer(
        status=int(payload["status"]),
        code=str(payload["code"]),
        message=str(payload["message"]),
        terminal_reason=_terminal_reason(reason),
        detail=detail if isinstance(detail, str | Mapping) else str(payload["message"]),
        incident=incident if isinstance(incident := payload.get("incident"), str) else None,
        headers={str(k): str(v) for k, v in headers.items()}
        if isinstance(headers, Mapping)
        else {},
    )


# ---------------------------------------------------------------------------
# Haute's own refusals that are not RequestRefusedErrors
# ---------------------------------------------------------------------------


def _domain_answer(exc: BaseException) -> FailureAnswer | None:
    if isinstance(exc, _loaded("haute._git_core", "GitUnrecordedFilesError")):
        # The editor offers to record the files or put them back, then asks again.
        message = str(exc)
        files = list(getattr(exc, "files", []))
        token = getattr(exc, "token", "")
        detail = _structured("unrecorded_files", message, files=files, token=token)
        return FailureAnswer(409, "unrecorded_files", message, "contract_error", detail)
    if isinstance(exc, _loaded("haute._git_core", "GitGuardrailError")):
        return _plain(403, "git_guardrail", str(exc))
    if isinstance(exc, _loaded("haute._git_core", "GitDomainError")):
        return _plain(400, "git_refused", str(exc))
    if isinstance(exc, _loaded("haute._git_core", "GitError")):
        # Git's own message can hold paths and remote addresses: it is recorded, never said.
        return None
    if isinstance(exc, _loaded("haute._pipeline_settings", "PipelineSettingsError")):
        # The message names the settings file and the key, which is what to fix.
        return _plain(409, "pipeline_settings_invalid", str(exc))
    if isinstance(exc, _loaded("haute._workbench_config", "WorkbenchError")):
        # The message names the haute.toml key or the form file; the path stays in context.
        return _plain(409, "workbench_invalid", getattr(exc, "message", "") or str(exc))
    if isinstance(exc, PathOutsideProjectError):
        return _plain(403, "path_outside_project", exc.message or str(exc))
    if isinstance(exc, InvalidPathError):
        return _plain(400, "invalid_path", exc.message or str(exc))
    if isinstance(exc, _loaded("haute._path_resolution", "RuntimePathOutsideProjectError")):
        return _plain(403, "path_outside_project", str(exc))
    if isinstance(exc, _loaded("haute._path_resolution", "MalformedRuntimePathError")):
        return _plain(400, "invalid_path", str(exc))
    return None


# ---------------------------------------------------------------------------
# A node's own failure
# ---------------------------------------------------------------------------


def own_failing_node(exc: BaseException) -> str | None:
    """The node whose own code raised *exc*, or ``None`` for a failure of the run.

    The graph walk marks what it raises with the node it was building or running.
    Contract and schema mismatches, public contract errors, a trace's own refusals, a Data
    Output's rows failing (found only when its write collects the whole plan, and worded
    with the output's name) and execution control (cancellation, memory, a deadline) end
    the run wherever they are raised, so they are never a node's.
    """
    from haute.errors import DataOutputRowsError

    node_id = failing_node(exc)
    if node_id is None or is_public_contract_error(exc):
        return None
    run_level: tuple[type[BaseException], ...] = (
        ContractMismatchError,
        SchemaMismatchError,
        TraceRequestError,
        DataOutputRowsError,
        *_loaded("haute._execution_context", "ExecutionCancelledError"),
        MemoryError,
        TimeoutError,
        RequestRefusedError,
    )
    return None if isinstance(exc, run_level) else node_id


def node_failed_error(
    exc: BaseException, *, node: dict[str, str | None] | None = None
) -> NodeFailedError | None:
    """*exc* as the public error naming the node whose own code raised it, or ``None``."""
    node_id = own_failing_node(exc)
    if node_id is None:
        return None
    inner = file_answer(exc) or _database_answer(exc)
    said = inner.message if inner is not None else node_failure_message(exc)
    return NodeFailedError(
        f"'{failing_node_name(exc) or node_id}' failed: {said}", node_id=node_id, node=node
    )


def _node_answer(exc: BaseException) -> FailureAnswer | None:
    failed = node_failed_error(exc)
    if failed is None:
        return None
    detail = failed.to_payload()
    return FailureAnswer(
        422, failed.error_code or "node_failed", failed.message, "contract_error", detail
    )


# ---------------------------------------------------------------------------
# The operating system refusing a file
# ---------------------------------------------------------------------------


def _os_error_in(exc: BaseException) -> OSError | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, OSError):
            return current
        seen.add(id(current))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return None


def display_file(name: object) -> str:
    """A file as a message names it: relative to the project when it is inside it, else by
    its own name, so no answer holds a path outside the project."""
    if not isinstance(name, str | os.PathLike) or not str(name):
        return "a file"
    from haute._project_context import installed_project

    path = Path(name)
    project = installed_project()
    if project is not None:
        try:
            return path.resolve().relative_to(project.root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return path.name or "a file"


def _target_file(exc: OSError) -> object:
    # A replace or a rename names its destination second: that is the file refused.
    return exc.filename2 if exc.filename2 else exc.filename


def file_answer(exc: BaseException, *, named: str | None = None) -> FailureAnswer | None:
    """The operating system's refusal of a file behind *exc*, or ``None``: held open by
    another program, read-only, a refused permission, a full disk, a path too long. The
    file is *named* as the caller names it when it knows it better (a request's own
    path), else as :func:`display_file` names the error's."""
    from haute._file_ops import FolderInUseError, path_too_long_error
    from haute._project import PipelineFileError

    if isinstance(exc, PipelineFileError):
        # haute.toml names a pipeline file that is not there or holds no pipeline: the
        # resolver's sentence and remedy, the file named relative to the project.
        code = "pipeline_file_missing" if exc.reason == "missing" else "pipeline_file_invalid"
        return _plain(404, code, exc.words(named or display_file(exc.filename)))
    too_long = path_too_long_error(exc)
    if too_long is not None:
        message = f"Haute could not write {named or display_file(too_long.filename)}: "
        return _plain(422, "path_too_long", f"{message}{too_long.strerror}.")
    refused = _os_error_in(exc)
    if isinstance(refused, FolderInUseError):
        # The folder is a staging or cache folder the user never sees, and the program
        # holding it lets go by itself: a scanner or an indexer reading what was just written.
        return _plain(
            409,
            "folder_in_use",
            "Another program, such as an antivirus scanner or an indexer, has files Haute has "
            "just written open. Try again in a moment.",
        )
    if (
        refused is not None
        and refused.errno == errno.ENOSPC
        and not (named or _target_file(refused))
    ):
        # A full disk is actionable whichever file it refused.
        return _plain(
            500, "disk_full", "The disk Haute writes to is full. Free some space, then try again."
        )
    if refused is None or not (named or _target_file(refused)):
        # An OSError naming no file says nothing a person could act on: unnamed.
        return None
    named = named or display_file(_target_file(refused))
    if refused.errno == errno.ENOSPC:
        # 500, never 507: Haute answers 507 for memory alone, and its consumers read it so.
        return _plain(
            500,
            "disk_full",
            f"There isn't room on disk for {named}. Free some space, then try again.",
        )
    if refused.errno == errno.EROFS:
        return _plain(
            409, "file_read_only", f"{named} is on a read-only drive, so Haute cannot change it."
        )
    if isinstance(refused, FileNotFoundError) and _target_file(refused):
        # A file renamed, moved or deleted while Haute had it named (a pipeline file in the
        # file manager, a branch switched in a terminal).
        return _plain(
            404,
            "file_missing",
            f"{named} is not there: it may have been moved, renamed or deleted.",
        )
    winerror = getattr(refused, "winerror", None)
    if winerror in _WINDOWS_FILE_IN_USE:
        return _in_use(named)
    if isinstance(refused, PermissionError):
        target = _target_file(refused)
        if _read_only(target):
            return _plain(
                409,
                "file_read_only",
                f"{named} is read-only. Clear its read-only setting, then try again.",
            )
        if winerror == _WINDOWS_ACCESS_DENIED or sys.platform == "win32":
            # Windows refuses to replace or remove a file another program holds open.
            return _in_use(named)
        return _plain(
            409,
            "file_permission_denied",
            f"The operating system refused Haute permission to change {named}. Check who may "
            "change it and its folder, then try again.",
        )
    return None


def _in_use(named: str) -> FailureAnswer:
    return _plain(
        409,
        "file_in_use",
        f"{named} is open in Haute or another program. Try again once it's closed.",
    )


def _read_only(target: object) -> bool:
    if not isinstance(target, str | os.PathLike) or not str(target):
        return False
    try:
        return os.path.isfile(target) and not os.access(target, os.W_OK)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The quote store's database
# ---------------------------------------------------------------------------

_STORE_VALUES_KEPT = "so the change was not saved; the quote's values are kept"


def _database_answer(exc: BaseException) -> FailureAnswer | None:
    """A database's refusal, said as the store's: SQLAlchemy is the quote store's driver
    layer and nothing else of Haute's, so its errors are the quote store's."""
    sqlalchemy_exc = sys.modules.get("sqlalchemy.exc")
    if sqlalchemy_exc is None:
        return None
    dbapi_error = sqlalchemy_exc.DBAPIError
    refused = exc if isinstance(exc, dbapi_error) else None
    if refused is None:
        return None
    text = str(getattr(refused, "orig", refused)).lower()
    if "database is locked" in text or "database table is locked" in text:
        return _plain(
            409,
            "quote_store_busy",
            f"Another program holds the quote store's file, {_STORE_VALUES_KEPT}. Close what has "
            "it open (a database browser with unsaved changes, a backup or a report), then try "
            "again.",
        )
    if "readonly database" in text or "read-only database" in text:
        return _plain(
            409,
            "quote_store_read_only",
            f"The quote store's file is read-only, {_STORE_VALUES_KEPT}. Make the file "
            "writable, then try again.",
        )
    if "file is not a database" in text or "database disk image is malformed" in text:
        return _plain(
            503,
            "quote_store_damaged",
            "The quote store's file is damaged: it is not a database Haute can read. Restore "
            "it from a backup.",
        )
    if bool(getattr(refused, "connection_invalidated", False)):
        # Lost after a statement ran: the transaction was never committed.
        return _plain(
            503,
            "quote_store_connection_lost",
            "The connection to the quote store's database was lost part-way through, so "
            "nothing was saved. Send it again.",
        )
    from haute._quote_store._store import unreached

    if unreached(refused):
        # The driver's own text names the server, its port and who signs in: none of it said.
        message = "The app cannot reach its quote store just now."
        detail = _structured("quote_store_unreachable", message, differences=[])
        return FailureAnswer(503, "quote_store_unreachable", message, "error", detail)
    return None


def git_refusal_answer(
    exc: BaseException, *, record: IncidentRecord | None = None
) -> FailureAnswer | None:
    """Git's own refusal of a command Haute ran, said in words with an incident under which
    git's message is recorded: it can hold paths, remote addresses and credentials, so it is
    never said. ``None`` for anything else."""
    if not isinstance(exc, _loaded("haute._git_core", "GitError")) or isinstance(
        exc, _loaded("haute._git_core", "GitDomainError")
    ):
        return None
    incident = _new_incident()
    (record or _log_incident)(incident, exc, event="git_refused", git_message=str(exc))
    message = f"Git refused this. Its reason is recorded under incident {incident}."
    return FailureAnswer(400, "git_failed", message, "contract_error", message, incident)


def database_refusal_answer(
    exc: BaseException, *, record: Callable[..., None] | None = None
) -> FailureAnswer | None:
    """A statement the quote store's database refused for its own reason (a trigger, a
    constraint, a value it will not hold), said in words with an incident under which its
    message is recorded; ``None`` for anything else."""
    sqlalchemy_exc = sys.modules.get("sqlalchemy.exc")
    if sqlalchemy_exc is None or not isinstance(exc, sqlalchemy_exc.DBAPIError):
        return None
    if getattr(exc, "statement", None) is None:
        return None
    incident = _new_incident()
    (record or _log_incident)(
        incident,
        exc,
        event="quote_store_statement_refused",
        database_message=str(getattr(exc, "orig", exc)),
    )
    message = (
        f"The quote store refused this change, {_STORE_VALUES_KEPT}. The database's reason is "
        f"recorded under incident {incident}."
    )
    return FailureAnswer(422, "quote_store_refused", message, "contract_error", message, incident)


# ---------------------------------------------------------------------------
# Anything else
# ---------------------------------------------------------------------------

#: How an incident's record is kept: by its id, the failure and what else is known of it.
IncidentRecord = Callable[..., None]


def _new_incident() -> str:
    return uuid.uuid4().hex[:12]


def record_incident(exc: BaseException | None = None, *, event: str, **known: Any) -> str:
    """Log what Haute knows of a failure, its traceback when there is one, under a new
    incident id, and return the id for the words that name it: for a message written where
    the failure's own text cannot be said (git's stderr, a database's message)."""
    incident = _new_incident()
    if exc is None:
        logger.error(event, incident=incident, **known)
    else:
        _log_incident(incident, exc, event=event, **known)
    return incident


def incident_message(incident: str) -> str:
    """What every edge says of a failure nothing names."""
    return (
        f"Haute hit a failure it does not recognise (incident {incident}). Try again; if it "
        "happens again, report the incident, whose details are recorded under it."
    )


def failure_traceback(exc: BaseException) -> str:
    """*exc*'s traceback, a worker's own when it failed in one."""
    remote = getattr(exc, "remote_traceback", None)
    if isinstance(remote, str) and remote:
        return remote
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _log_incident(
    incident: str, exc: BaseException, *, event: str = "unexpected_failure", **known: Any
) -> None:
    remote_message = getattr(exc, "remote_message", None)
    if isinstance(remote_message, str):
        # What the worker raised, which its traceback may not carry whole.
        known.setdefault("remote_message", remote_message)
    logger.error(
        event,
        incident=incident,
        error_class=getattr(exc, "remote_type", None) or type(exc).__name__,
        traceback=failure_traceback(exc),
        **known,
    )


def unexpected_failure(
    exc: BaseException,
    *,
    operation: str | None = None,
    record: IncidentRecord | None = None,
) -> FailureAnswer:
    """The answer to a failure nothing names: words and an incident id, its traceback
    recorded under that id by *record* (the log unless an edge keeps its own)."""
    incident = _new_incident()
    known: dict[str, Any] = {}
    if operation is not None:
        known["operation"] = operation
    exitcode = getattr(exc, "exitcode", None)
    if isinstance(exitcode, int):
        known["exitcode"] = exitcode
    (record or _log_incident)(incident, exc, **known)
    message = incident_message(incident)
    # A flat sentence, as every error detail is (the incident id inside it); the code and the
    # id are a job's fields and the record's.
    return FailureAnswer(500, UNEXPECTED_FAILURE_CODE, message, "error", message, incident)


def answer_failure(
    exc: BaseException,
    *,
    operation: str | None = None,
    record: IncidentRecord | None = None,
) -> FailureAnswer:
    """*exc* in words: :func:`classify_failure`'s answer, else an incident."""
    answer = (
        classify_failure(exc)
        or git_refusal_answer(exc, record=record)
        or database_refusal_answer(exc, record=record)
    )
    if answer is not None:
        _log_answered(exc, answer, operation)
        return answer
    return unexpected_failure(exc, operation=operation, record=record)


def failure_refusal(exc: BaseException, *, operation: str | None = None) -> AnsweredFailureError:
    """*exc*'s answer (:func:`answer_failure`) as a refusal, for a caller that hands a
    failure on ahead of a broader clause of its own."""
    return answer_failure(exc, operation=operation).refusal()


def answer_pipeline_failure(
    exc: BaseException,
    *,
    operation: str | None = None,
    record: IncidentRecord | None = None,
) -> FailureAnswer:
    """:func:`answer_failure` for an edge that ran the pipeline (:func:`pipeline_failure`)."""
    answer = pipeline_failure(exc)
    if answer is not None:
        _log_answered(exc, answer, operation)
        return answer
    return answer_failure(exc, operation=operation, record=record)


def answer_app_pricing_failure(exc: BaseException, *, operation: str) -> FailureAnswer:
    """An underwriter app's pricing that failed, as the deployed pricing API answers one
    (specs/workbench): a refusal worded for its request, memory and a public contract error
    (a quote's values the Workbench Input does not take, a rating factor missing) as every
    edge says them; anything else, the pipeline failing on the quote, in one fixed sentence
    naming an incident, under which what failed and its text are logged for the analyst.
    Never the text itself: a node's can hold paths, internal names and secrets, and an
    underwriter is no one to read them."""
    answer = classify_failure(exc)
    if answer is not None and (
        isinstance(exc, RequestRefusedError)
        or answer.terminal_reason == "memory_limited"
        or is_public_contract_error(exc)
    ):
        _log_answered(exc, answer, operation)
        return answer
    incident = _new_incident()
    _log_incident(incident, exc, event="pricing_failed", operation=operation)
    message = (
        f"The pipeline failed on this quote (incident {incident}). Pass the incident to "
        "whoever looks after this app: what failed is recorded under it."
    )
    detail = _structured("pricing_failed", message, incident=incident)
    return FailureAnswer(500, "pricing_failed", message, "error", detail, incident=incident)


def _log_answered(exc: BaseException, answer: FailureAnswer, operation: str | None) -> None:
    # A server fault is an error, once: an incident's record is already logged under it.
    server_fault = answer.status >= 500 and answer.status != 507 and answer.incident is None
    log = logger.error if server_fault else logger.warning
    log(
        "failure_answered",
        code=answer.code,
        status=answer.status,
        error_class=type(exc).__name__,
        operation=operation,
    )
