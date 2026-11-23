"""The one module that puts a failure into words (``haute._failure_answers``).

Each kind of failure Haute names is answered with the same status, code and message
wherever it is raised; anything else is answered with an incident id and its traceback is
recorded under it.
"""

from __future__ import annotations

import errno
import sqlite3
import stat
import sys
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy.exc import OperationalError
from structlog.testing import capture_logs

from haute._execution_admission import ExecutionAdmissionError
from haute._execution_context import ExecutionProfile
from haute._failure_answers import (
    UNEXPECTED_FAILURE_CODE,
    answer_failure,
    answer_from_payload,
    classify_failure,
    failure_payload,
    pipeline_failure,
)
from haute._file_ops import PathTooLongError
from haute._memory_errors import memory_error_in
from haute._topo import CycleError, UnknownEdgeEndpointError
from haute._worker_failures import WorkerRemoteError
from haute.errors import (
    ContractMismatchError,
    InvalidRequestError,
    NodeConfigError,
    mark_failing_node,
)


def _held(path: Path) -> PermissionError:
    """The error Windows gives a replace onto a file another program holds open."""
    if sys.platform == "win32":
        return PermissionError(None, "The process cannot access the file", str(path), 32)
    exc = PermissionError(errno.EACCES, "The process cannot access the file", str(path))
    exc.winerror = 32  # type: ignore[attr-defined]
    return exc


class TestMemory:
    @pytest.mark.parametrize(
        "exc",
        [
            MemoryError(),
            OSError(errno.ENOMEM, "Cannot allocate memory"),
            pl.exceptions.ComputeError("Cannot allocate memory (os error 12)"),
        ],
        ids=["memory_error", "os_enomem", "polars_allocation"],
    )
    def test_an_allocation_refused_in_any_form_is_a_memory_outcome(
        self, exc: BaseException
    ) -> None:
        wrapped = RuntimeError("preparation failed")
        wrapped.__cause__ = exc

        answer = classify_failure(wrapped)

        assert answer is not None
        assert (answer.status, answer.code, answer.terminal_reason) == (
            507,
            "memory_limit",
            "memory_limited",
        )
        assert "fewer rows or columns" in answer.message
        assert memory_error_in(wrapped) is not None

    def test_a_users_error_mentioning_memory_is_not_one(self) -> None:
        assert memory_error_in(ValueError("out of memory in my lookup")) is None

    def test_an_admission_refused_for_the_process_cap_names_the_setting_that_raises_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAUTE_SINK_PROCESS_RSS_LIMIT_MB", "50")
        refused = ExecutionAdmissionError(
            "cli_run",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=1 << 30,
            rss_at_admission_bytes=120 << 20,
            process_rss_limit_bytes=50 << 20,
            reason="process_rss_limit_exceeded",
        )

        answer = classify_failure(refused)

        assert answer is not None and answer.code == "memory_limit"
        assert answer.message.startswith("The run was not started")
        assert "50.0 MiB memory cap" in answer.message
        assert "HAUTE_SINK_PROCESS_RSS_LIMIT_MB" in answer.message


class TestNamedFailures:
    def test_a_refusal_keeps_its_status_detail_and_headers(self) -> None:
        refusal = InvalidRequestError("Name the node.", headers={"Retry-After": "2"})

        answer = classify_failure(refusal)

        assert answer is not None
        assert (answer.status, answer.code, answer.message) == (
            400,
            "invalid_request",
            "Name the node.",
        )
        assert answer.headers == {"Retry-After": "2"}

    def test_a_public_contract_error_keeps_its_payload(self) -> None:
        answer = classify_failure(NodeConfigError("Pick a column.", setting="column"))

        assert answer is not None
        assert answer.status == 422
        assert answer.detail["error_code"] == "node_config_invalid"  # type: ignore[index]

    def test_an_io_setting_haute_cannot_read_with_is_said_as_written(self) -> None:
        """IOL-01: a reader package the install lacks reaches every edge, a worker's parent
        included, in the words Haute wrote."""
        from haute._polars_io_registry import PolarsIoConfigError

        refused = PolarsIoConfigError(
            "Format 'excel' needs an engine package to read: install one of ['fastexcel']."
        )

        answer = classify_failure(refused)
        sent = failure_payload(refused)

        assert answer is not None
        assert (answer.status, answer.code, answer.message) == (422, "config_invalid", str(refused))
        assert sent is not None and answer_from_payload(sent["failure_answer"]) == answer

    def test_an_input_file_that_cannot_be_read_keeps_its_file_and_place(self) -> None:
        from haute.errors import InputFileError

        refused = InputFileError(
            "data/quote.json is not valid JSON: unexpected end of data at line 2, column 9.",
            file="data/quote.json",
            line=2,
            column=9,
        )

        answer = classify_failure(refused)

        assert answer is not None
        assert (answer.status, answer.terminal_reason) == (422, "contract_error")
        assert answer.detail == {
            "error_code": "input_file_unreadable",
            "message": refused.message,
            "file": "data/quote.json",
            "line": 2,
            "column": 9,
        }

    def test_a_nodes_own_failure_names_the_node(self) -> None:
        exc = ZeroDivisionError("division by zero")
        mark_failing_node(exc, "rate_step")

        answer = classify_failure(exc)

        assert answer is not None
        assert (answer.status, answer.code) == (422, "node_failed")
        assert answer.message == "'rate_step' failed: division by zero"
        assert answer.detail["node_id"] == "rate_step"  # type: ignore[index]

    def test_a_contract_mismatch_in_a_node_is_the_runs_not_the_nodes(self) -> None:
        exc = ContractMismatchError("columns differ")
        mark_failing_node(exc, "rate_step")

        assert classify_failure(exc) is None
        answer = pipeline_failure(exc)
        assert answer is not None and answer.code == "pipeline_failed"


class TestFiles:
    def test_a_file_another_program_holds_is_named_relative_to_the_project(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "outputs" / "result.csv"
        target.parent.mkdir()
        target.write_text("a\n", encoding="utf-8")

        answer = classify_failure(_held(target))

        assert answer is not None
        assert (answer.status, answer.code) == (409, "file_in_use")
        assert answer.message == (
            "outputs/result.csv is open in Haute or another program. Try again once it's closed."
        )

    def test_a_read_only_file_is_called_read_only(self, tmp_path: Path) -> None:
        target = tmp_path / "rates.csv"
        target.write_text("a\n", encoding="utf-8")
        Path(target).chmod(stat.S_IREAD)
        try:
            answer = classify_failure(
                PermissionError(errno.EACCES, "Permission denied", str(target))
            )
        finally:
            Path(target).chmod(stat.S_IREAD | stat.S_IWRITE)

        assert answer is not None
        assert answer.code == "file_read_only"
        assert answer.message.startswith("rates.csv is read-only.")

    def test_a_full_disk_names_the_file(self, tmp_path: Path) -> None:
        answer = classify_failure(
            OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "main.py"))
        )

        assert answer is not None
        assert (answer.status, answer.code) == (500, "disk_full")
        assert answer.message.startswith("There isn't room on disk for main.py.")

    def test_a_file_outside_the_project_is_named_by_its_name_alone(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "elsewhere" / "secret.csv"

        answer = classify_failure(OSError(errno.ENOSPC, "No space left", str(outside)))

        assert answer is not None
        assert str(tmp_path.parent) not in answer.message
        assert "secret.csv" in answer.message

    def test_a_file_moved_while_named_says_it_is_not_there(self, tmp_path: Path) -> None:
        answer = classify_failure(
            FileNotFoundError(errno.ENOENT, "No such file", str(tmp_path / "rating" / "main.py"))
        )

        assert answer is not None
        assert (answer.status, answer.code) == (404, "file_missing")
        assert answer.message.startswith("rating/main.py is not there")

    def test_a_path_too_long_says_why(self, tmp_path: Path) -> None:
        answer = classify_failure(PathTooLongError(str(tmp_path / ("x" * 300))))

        assert answer is not None
        assert answer.code == "path_too_long"
        assert "enable long paths" in answer.message


class TestQuoteStore:
    @pytest.mark.parametrize(
        ("driver_text", "code", "status"),
        [
            ("database is locked", "quote_store_busy", 409),
            ("attempt to write a readonly database", "quote_store_read_only", 409),
            ("file is not a database", "quote_store_damaged", 503),
        ],
    )
    def test_sqlites_refusals_are_called_what_they_are(
        self, driver_text: str, code: str, status: int
    ) -> None:
        refused = OperationalError("COMMIT", {}, sqlite3.OperationalError(driver_text))

        answer = classify_failure(refused)

        assert answer is not None
        assert (answer.code, answer.status) == (code, status)

    def test_a_connection_lost_mid_write_says_send_it_again(self) -> None:
        lost = OperationalError(
            "INSERT INTO quote_details ...",
            {},
            Exception("server closed the connection unexpectedly"),
            connection_invalidated=True,
        )

        answer = classify_failure(lost)

        assert answer is not None
        assert (answer.status, answer.code) == (503, "quote_store_connection_lost")
        assert answer.message.endswith("Send it again.")

    def test_a_statement_the_database_refuses_is_said_with_an_incident(self) -> None:
        refused = OperationalError("UPDATE quote_details ...", {}, Exception("trigger said no"))

        with capture_logs() as logs:
            answer = answer_failure(refused)

        assert answer.code == "quote_store_refused"
        assert answer.incident is not None and answer.incident in answer.message
        [record] = [log for log in logs if log.get("incident") == answer.incident]
        assert record["database_message"] == "trigger said no"


class TestUnexpected:
    @pytest.mark.parametrize(
        ("failure", "named"),
        [
            (CycleError(["loop_b", "loop_a"]), "loop_a, loop_b"),
            (UnknownEdgeEndpointError(("ghost",), ()), "ghost"),
        ],
        ids=["a loop", "an edge to a missing node"],
    )
    def test_a_graph_that_cannot_run_is_refused_in_its_own_words(
        self, failure: Exception, named: str
    ) -> None:
        answer = answer_failure(failure, operation="save")
        assert (answer.status, answer.code) == (400, "graph_refused")
        assert named in answer.message
        assert answer.incident is None

    def test_a_failure_nothing_names_is_words_and_an_incident(self) -> None:
        try:
            raise KeyError("internal_slot")
        except KeyError as exc:
            with capture_logs() as logs:
                answer = answer_failure(exc, operation="preview")

        assert (answer.status, answer.code) == (500, UNEXPECTED_FAILURE_CODE)
        assert answer.incident is not None
        assert f"incident {answer.incident}" in answer.message
        assert "internal_slot" not in answer.message
        assert "server log" not in answer.message
        [record] = [log for log in logs if log.get("incident") == answer.incident]
        assert "KeyError" in record["traceback"]
        assert record["operation"] == "preview"


class TestAcrossAWorker:
    def test_a_child_names_a_held_file_and_its_parent_answers_the_same(
        self, tmp_path: Path
    ) -> None:
        raised = _held(tmp_path / "result.csv")

        payload = failure_payload(raised)
        remote = WorkerRemoteError(
            remote_type="PermissionError",
            remote_module="builtins",
            remote_message=str(raised),
            remote_traceback="",
            is_memory=False,
            public_payload=payload,
        )

        assert classify_failure(remote) == classify_failure(raised)

    def test_an_answer_survives_its_payload(self) -> None:
        answer = answer_failure(InvalidRequestError({"error_code": "x", "message": "No."}))

        assert answer_from_payload(answer.payload()) == answer


def _hold_a_project_file() -> None:
    """Fail as a file another program holds fails, in the project's ``outputs`` folder."""
    from haute._project_context import current_project

    path = str(current_project().root / "outputs" / "rates.csv")
    if sys.platform == "win32":
        raise PermissionError(None, "The process cannot access the file", path, 32)
    held = PermissionError(errno.EACCES, "The process cannot access the file", path)
    held.winerror = 32  # type: ignore[attr-defined]
    raise held


def test_a_worker_names_a_project_file_as_the_parent_names_it() -> None:
    """The child classifies its failure in the request's project, so a file in a folder of
    the project is named by its path in the project, as the same failure in-process is."""
    from haute._worker_isolation import run_isolated_worker

    with pytest.raises(PermissionError) as in_process:
        _hold_a_project_file()
    with pytest.raises(WorkerRemoteError) as remote:
        run_isolated_worker(_hold_a_project_file)

    said = answer_failure(remote.value).message
    assert said == answer_failure(in_process.value).message
    assert said.startswith("outputs/rates.csv is open")


@pytest.mark.parametrize("named", [True, False], ids=["a file", "no file"])
def test_a_full_disk_is_an_error_never_a_memory_outcome(tmp_path: Path, named: bool) -> None:
    """507 is Haute's memory answer alone: a full disk is a 500 whose job ends ``error``."""
    filename = str(tmp_path / "rates.csv") if named else None
    answer = answer_failure(OSError(errno.ENOSPC, "No space left on device", filename))

    assert (answer.status, answer.code, answer.terminal_reason) == (500, "disk_full", "error")
    assert "Free some space" in answer.message
    assert answer.incident is None


def test_an_answers_job_fields_survive_training_preparation_and_the_optimiser_worker() -> None:
    """A refusal raised from an answer keeps the answer's code, detail and incident on the
    job, and an optimiser child's job record carries its incident to the parent."""
    from haute._failure_answers import unexpected_failure
    from haute.routes._optimiser_worker import OptimiserWorkerFailure
    from haute.routes._training_preparation import _preparation_failure_from_refusal

    answer = unexpected_failure(RuntimeError("internal_slot"))

    failure = _preparation_failure_from_refusal(answer.refusal(), job_id="job")

    assert failure.terminal_reason == "error"
    assert failure.fields["error_code"] == "unexpected_failure"
    assert failure.fields["incident"] == answer.incident
    assert failure.http_detail == answer.message
    replayed = OptimiserWorkerFailure.from_job_record(
        {"status": "error", "message": answer.message, **answer.job_fields()}
    )
    assert replayed.fields["incident"] == answer.incident
    assert replayed.http_detail == answer.message
