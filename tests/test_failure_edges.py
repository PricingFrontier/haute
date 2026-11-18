"""Every edge answers a failure in the same words.

The editor's server, the underwriter app, a job's outcome and the command line each answer a
failure through ``haute._failure_answers``: a failure no list names is answered in words with
an incident number, never "Internal Server Error" or a traceback, and each failure Haute names
is answered with the same message, and the same code wherever the edge carries one.
"""

from __future__ import annotations

import errno
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from haute._workbench_app import write_release
from haute._workbench_form import FormSpec
from haute.cli import HauteGroup
from haute.deploy._utils import shipped_graph
from haute.errors import InvalidRequestError, mark_failing_node
from haute.routes._background_jobs import IsolatedJobSupervisor
from tests._project import project_app
from tests.test_workbench_app import FORM, RUNTIME, _client, _graph, _price

Failure = Callable[[], BaseException]


@pytest.fixture()
def release(tmp_path: Path) -> Callable[[], Path]:
    """The underwriter app's release, a bundle as a deploy writes it."""

    def build() -> Path:
        directory = tmp_path / "release"
        directory.mkdir(exist_ok=True)
        manifest = {
            "haute_version": "9.9.9",
            "pipeline_name": "motor",
            "created_at": "2026-10-12T09:00:00+00:00",
            "created_by": "analyst",
            "input_node_ids": ["request"],
            "output_node_id": "response",
            "output_fields": None,
            "artifacts": {},
            "pruned_graph": shipped_graph(_graph()),
        }
        (directory / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_release(
            directory, FormSpec.model_validate(FORM), "azure-container-apps", runtime=RUNTIME
        )
        return directory

    return build


def _unnamed() -> BaseException:
    return KeyError("internal_slot")


def _held_file() -> BaseException:
    from haute._project_context import current_project

    path = str(current_project().root / "outputs" / "result.csv")
    if sys.platform == "win32":
        return PermissionError(None, "The process cannot access the file", path, 32)
    exc = PermissionError(errno.EACCES, "The process cannot access the file", path)
    exc.winerror = 32  # type: ignore[attr-defined]
    return exc


def _node_failure() -> BaseException:
    exc = ZeroDivisionError("division by zero")
    mark_failing_node(exc, "priced")
    return exc


NAMED: dict[str, Failure] = {
    "held_file": _held_file,
    "out_of_memory": MemoryError,
    "node_failure": _node_failure,
    "refusal": lambda: InvalidRequestError("Name the node to run to."),
}


def _message_and_code(detail: Any) -> tuple[str, str | None]:
    if isinstance(detail, dict):
        return detail["message"], detail.get("error_code")
    return detail, None


# The four edges: each raises the failure where that edge's work runs, and says what the
# edge answers (its message, and its code when it carries one).


def _editor(monkeypatch: pytest.MonkeyPatch, failure: Failure) -> tuple[int, str, str | None, str]:
    def listing() -> list[Path]:
        raise failure()

    monkeypatch.setattr("haute.routes.pipeline.discover_pipelines", listing)
    response = TestClient(project_app()).get("/api/pipelines")
    message, code = _message_and_code(response.json()["detail"])
    return response.status_code, message, code, response.text


def _underwriter_app(
    monkeypatch: pytest.MonkeyPatch, release: Any, failure: Failure
) -> tuple[int, str, str | None, str]:
    def pricing(**_kwargs: Any) -> Any:
        raise failure()

    monkeypatch.setattr("haute._workbench_app.score_graph", pricing)
    response = _price(_client(release()))
    message, code = _message_and_code(response.json()["detail"])
    return response.status_code, message, code, response.text


def _job(failure: Failure) -> tuple[str, str | None, str]:
    def execute() -> Any:
        raise failure()

    outcome = IsolatedJobSupervisor._produce_outcome(
        execute, completed_fields=lambda result: {}, completed_message="Completed"
    )
    return outcome.message, outcome.fields.get("error_code"), repr(outcome.fields)


def _command(failure: Failure) -> tuple[int, str]:
    group = HauteGroup()

    @group.command()
    def work() -> None:
        raise failure()

    result = CliRunner().invoke(group, ["work"])
    return result.exit_code, result.output


def test_a_failure_no_list_names_is_words_and_an_incident_at_every_edge(
    monkeypatch: pytest.MonkeyPatch, release: Any, tmp_path: Path
) -> None:
    status, message, code, body = _editor(monkeypatch, _unnamed)
    assert (status, code) == (500, None)
    assert "incident" in message

    app_status, app_message, app_code, app_body = _underwriter_app(monkeypatch, release, _unnamed)
    assert (app_status, app_code) == (500, None)

    job_message, job_code, _fields = _job(_unnamed)
    assert job_code == "unexpected_failure"

    exit_code, output = _command(_unnamed)
    assert exit_code == 1

    for said in (body, app_body, job_message, output):
        assert "Internal Server Error" not in said
        assert "Traceback" not in said
        assert "internal_slot" not in said
        assert "server log" not in said
        assert "does not recognise (incident " in said
    [record] = list((tmp_path / ".haute" / "incidents").iterdir())
    assert record.stem in output
    assert "KeyError: 'internal_slot'" in record.read_text(encoding="utf-8")


@pytest.mark.parametrize("named", sorted(NAMED))
def test_a_failure_haute_names_is_said_in_the_same_words_at_every_edge(
    monkeypatch: pytest.MonkeyPatch, release: Any, named: str
) -> None:
    failure = NAMED[named]

    _status, editor_message, editor_code, _body = _editor(monkeypatch, failure)
    _app_status, app_message, app_code, _app_body = _underwriter_app(monkeypatch, release, failure)
    job_message, job_code, _fields = _job(failure)
    exit_code, output = _command(failure)

    assert editor_message == app_message == job_message
    assert output == f"Error: {editor_message}\n"
    assert exit_code == 1
    codes = {code for code in (editor_code, app_code, job_code) if code is not None}
    assert len(codes) == 1, codes


def test_a_command_says_where_an_incident_could_not_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("haute._file_ops.atomic_write_text", refuse)

    exit_code, output = _command(_unnamed)

    assert exit_code == 1
    assert "could not be written" in output
    assert "KeyError: 'internal_slot'" in output


def test_a_pipeline_file_deleted_while_the_editor_runs_is_named_in_one_line(
    tmp_path: Path,
) -> None:
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "main.py"\n', encoding="utf-8")
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("motor")\n', encoding="utf-8"
    )
    client = TestClient(project_app())
    assert client.get("/api/pipeline/motor").status_code == 200
    (tmp_path / "main.py").unlink()

    answered = client.get("/api/pipeline/motor")

    assert answered.status_code == 404, answered.text
    assert answered.json()["detail"] == (
        "Pipeline file configured in haute.toml [project].pipeline does not exist: main.py. "
        "Fix the path in haute.toml or create the file."
    )


def test_click_still_answers_its_own_usage_errors() -> None:
    group = HauteGroup()

    @group.command()
    @click.option("--port", type=click.IntRange(0, 65535))
    def serve(port: int) -> None:
        raise AssertionError("never reached")

    result = CliRunner().invoke(group, ["serve", "--port", "70000"])

    assert result.exit_code == 2
    assert "70000 is not in the range" in result.output
