"""The three commands, run whole on every shipped example and on wrong inputs.

``haute lint``, ``haute run`` and ``haute deploy --dry-run`` are run through the command
line on each shipped example as an analyst copies it into a project, with nothing
patched. Each exits as the example expects and raises nothing to the command line (whose
edge would print a traceback) and says no failure it does not recognise; a command that
refuses an example says why in one error line. A table of wrong request files on one
example holds the line the analyst is given for each, by both commands that read it.

The settings no deploy can use (an unknown target among them) and a ``haute.toml``
naming a pipeline file that is not there are held, unpatched, by
``tests/test_cli_deploy.py::TestDryRunSettings``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from haute.assistant._assets import example_bundle_manifests, materialize_example_bundle
from haute.cli import cli
from tests._project import use_project

COMMANDS: Mapping[str, list[str]] = {
    "lint": ["lint"],
    "run": ["run"],
    "dry run": ["deploy", "--dry-run"],
}

#: The words of the one error line a command refuses an example in.
_REFUSED: Mapping[tuple[str, str], tuple[str, ...]] = {
    # The deployment's meant refusal (specs/deploy): two sources and no request input.
    ("reference_join", "dry run"): ("none is a request input",),
}

#: The example whose Model Scoring names a run nobody has trained yet; another package
#: owns how its run and dry run refuse it (test_an_untrained_run_is_refused_naming_it).
_UNTRAINED = "model_lifecycle"

_UNRECOGNISED = "Haute hit a failure it does not recognise"


#: The folder, in a test's temporary folder, an example is copied into.
_PROJECT = "p"


def _project(tmp_path: Path, example: str) -> None:
    """*example* as an analyst copies it into a project: its files, in a git repository."""
    root = tmp_path / _PROJECT
    materialize_example_bundle(example, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    use_project(root)


def _error_lines(result: Result) -> list[str]:
    return [
        line.strip()
        for line in result.output.splitlines()
        if line.lstrip().startswith(("Error", "✗"))
    ]


def _assert_answered(result: Result, refused_in: tuple[str, ...] | None) -> None:
    """The command exited as expected, raised nothing to the command line, and said any
    refusal in one error line holding each of *refused_in*."""
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"raised {result.exception!r}\n{result.output}"
    )
    assert "Traceback" not in result.output, result.output
    assert _UNRECOGNISED not in result.output, result.output
    errors = _error_lines(result)
    if refused_in is None:
        assert (result.exit_code, errors) == (0, []), result.output
        return
    assert result.exit_code == 1, result.output
    assert len(errors) == 1 and all(words in errors[0] for words in refused_in), result.output


def _example_commands() -> list[Any]:
    names = sorted(str(manifest["id"]) for manifest in example_bundle_manifests())
    return [
        pytest.param(name, command, id=f"{name}-{command.replace(' ', '-')}")
        for name in names
        for command in COMMANDS
        if not (name == _UNTRAINED and command != "lint")
    ]


@pytest.mark.parametrize(("example", "command"), _example_commands())
def test_a_command_run_whole_on_a_shipped_example_answers_in_its_own_words(
    runner: CliRunner, tmp_path: Path, example: str, command: str
) -> None:
    _project(tmp_path, example)

    result = runner.invoke(cli, COMMANDS[command])

    _assert_answered(result, _REFUSED.get((example, command)))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MLR-04: a Model Scoring run that does not exist is an unrecognised failure, "
    "not the node's configuration error naming the node and the run",
)
@pytest.mark.parametrize("command", ["run", "dry run"])
def test_an_untrained_run_is_refused_naming_it(
    runner: CliRunner, tmp_path: Path, command: str
) -> None:
    _project(tmp_path, _UNTRAINED)

    result = runner.invoke(cli, COMMANDS[command])

    _assert_answered(result, ("scored", "replace-with-training-run-id"))


# ---------------------------------------------------------------------------
# Wrong request files
# ---------------------------------------------------------------------------

_REQUEST_EXAMPLE = "minimal_live_quote"
_REQUEST_FILE = "data/quote.json"
_UTF8_BOM = b"\xef\xbb\xbf"

#: A wrong request file, and the words both commands refuse it in (``None``: read
#: through). The run's own words add the remedy; the dry run's, the schema it was reading.
_WRONG_REQUESTS: Mapping[str, tuple[Any, tuple[str, ...] | None]] = {
    "value-of-another-type": (
        b'[{"quote_id": "q1", "fixture_value": "ten"}]',
        (
            "column 'fixture_value' has values that don't match its declared type 'int'; "
            "re-infer the schema or change the column's type to match the data",
        ),
    ),
    "cut-short": (
        b'[{"quote_id": "q1",\n  "fixture_value": 10',
        ("data/quote.json is not valid JSON: unexpected end of data at line 2, column 22.",),
    ),
    "empty": (b"", ("data/quote.json is empty, so it is not JSON.",)),
    # Windows PowerShell 5.1 writes one; the file is read through it.
    "byte-order-mark": (lambda raw: _UTF8_BOM + raw, None),
}


@pytest.mark.parametrize("command", ["run", "dry run"])
@pytest.mark.parametrize("case", list(_WRONG_REQUESTS))
def test_a_wrong_request_file_is_named_with_its_fault_by_each_command(
    runner: CliRunner, tmp_path: Path, case: str, command: str
) -> None:
    """The run and the dry run read one request file alike, in the IO layer's words
    (specs/io-layer, "Reading an input's bytes")."""
    _project(tmp_path, _REQUEST_EXAMPLE)
    request = tmp_path / _PROJECT / _REQUEST_FILE
    wrong, said = _WRONG_REQUESTS[case]
    request.write_bytes(wrong(request.read_bytes()) if callable(wrong) else wrong)

    result = runner.invoke(cli, COMMANDS[command])

    _assert_answered(result, said)
    if said is not None and command == "run":
        # The refusal's remedy, and not the advice a run short of memory is given.
        assert (
            "\nCorrect what the message names in this input's file or tables, then try again."
            in result.output
        )
        assert "memory headroom" not in result.output
