"""Pin-down tests for Phase 5 Wave 9A CLI UX polish.

Covers codebase-review items:

- **#105** ``--endpoint-suffix`` help drift — three commands spell the help
  text differently. Introduce a shared ``ENDPOINT_SUFFIX_HELP`` constant in
  :mod:`haute.cli._helpers` and point every command at it so the text can
  never drift again.
- **#108** ``haute init --force`` — today ``haute init`` exits non-zero with
  an "already exists" message when a ``haute.toml`` is present, but it
  offers no escape hatch. Add a ``--force`` flag that overwrites the
  existing scaffold.
- **#109** ``_train.py`` progress bar flush — ``click.echo(..., nl=False)``
  keeps the line buffered on many terminals so the bar appears to hang.
  Add an explicit ``sys.stdout.flush()`` after each update.
- **#110** ``--version-only`` exit code — ``haute status --version-only``
  currently prints ``0`` and exits ``0`` when the model is not registered.
  Scripts cannot distinguish "no version yet" from "version 0", so change
  the contract to exit non-zero with a stderr message.
- **#114** Pre-commit chmod on Windows — ``Path.chmod(0o755)`` is a silent
  no-op on NTFS (and the underlying Git shim needs ``git update-index
  --chmod=+x`` instead). Skip the ``chmod`` call on ``sys.platform ==
  "win32"`` and document the manual workaround inline.

Each test pins one observable behaviour. Tests are expected to fail until
the dev implementation lands; import-time collection errors also count as
pinning failures because the shared constant is part of the contract.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import click
import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FlushCountingStream(io.StringIO):
    """Real text stream that counts ``flush()`` calls.

    Used in place of a ``MagicMock`` for ``sys.stdout`` in the
    ``_progress`` tests: Click's echo path reads ``stream.encoding``
    and calls ``codecs.lookup`` on it, which fails on a MagicMock
    whose ``.encoding`` is itself a mock.  A real :class:`io.StringIO`
    subclass with a fixed ``encoding`` attribute and a flush counter
    gives us accurate observability without breaking the code under
    test.
    """

    encoding = "utf-8"

    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:  # type: ignore[override]
        self.flush_count += 1
        super().flush()


def _render_help(command_name: str) -> str:
    """Return the rendered ``--help`` output of the named subcommand.

    Uses the programmatic Click API so we exercise the same help string
    that users see on the terminal.  ``resilient_parsing=True`` avoids any
    "missing argument" short-circuits that can happen on commands which
    take a positional argument.
    """
    with click.Context(cli, resilient_parsing=True) as ctx:
        sub = cli.get_command(ctx, command_name)
        assert sub is not None, f"CLI group is missing subcommand: {command_name!r}"
        with click.Context(sub, info_name=command_name, parent=ctx) as sub_ctx:
            return sub.get_help(sub_ctx)


# Every command that currently exposes --endpoint-suffix.  Sourced by
# grepping ``src/haute/cli/*.py``; any new command that adds the option
# must be added here and read from the shared constant.
_ENDPOINT_SUFFIX_COMMANDS: tuple[str, ...] = ("deploy", "impact", "smoke")


# ---------------------------------------------------------------------------
# #105 — ``--endpoint-suffix`` help drift
# ---------------------------------------------------------------------------


class TestEndpointSuffixHelpSharedConstant:
    """Pin the contract that ``--endpoint-suffix`` help text is centralised.

    A single shared ``ENDPOINT_SUFFIX_HELP`` constant in
    :mod:`haute.cli._helpers` must be the only definition of the help
    string; every command using the option must reference it so the
    strings cannot drift again.
    """

    def test_shared_constant_is_importable_and_non_empty(self) -> None:
        """``ENDPOINT_SUFFIX_HELP`` is exported from ``haute.cli._helpers``.

        Collection-time ``ImportError`` if the constant is missing —
        that counts as a pinning failure until dev introduces it.
        """
        from haute.cli._helpers import ENDPOINT_SUFFIX_HELP

        assert isinstance(ENDPOINT_SUFFIX_HELP, str)
        assert ENDPOINT_SUFFIX_HELP.strip(), "ENDPOINT_SUFFIX_HELP must be a non-empty string"

    def test_shared_constant_mentions_staging_example(self) -> None:
        """The shared help text should include an example suffix.

        The existing per-command strings all use ``-staging`` as the
        illustrative example.  Preserve that so users recognise the copy
        after the refactor.
        """
        from haute.cli._helpers import ENDPOINT_SUFFIX_HELP

        assert "-staging" in ENDPOINT_SUFFIX_HELP, (
            f"ENDPOINT_SUFFIX_HELP should reference the canonical "
            f"'-staging' example; got: {ENDPOINT_SUFFIX_HELP!r}"
        )

    @pytest.mark.parametrize("command_name", _ENDPOINT_SUFFIX_COMMANDS)
    def test_command_help_uses_shared_constant(self, command_name: str) -> None:
        """Every ``--endpoint-suffix`` command renders the shared help text.

        Asserts that the substring from the shared constant appears verbatim
        in the rendered ``--help`` for each subcommand — this catches both
        (a) commands that still hard-code their own wording, and (b) the
        shared constant failing to be wired through.
        """
        from haute.cli._helpers import ENDPOINT_SUFFIX_HELP

        help_text = _render_help(command_name)
        # Click can wrap long help strings across lines; collapse whitespace
        # on both sides before substring-matching.
        help_flat = re.sub(r"\s+", " ", help_text).strip()
        shared_flat = re.sub(r"\s+", " ", ENDPOINT_SUFFIX_HELP).strip()
        assert shared_flat in help_flat, (
            f"Command {command_name!r} does not render the shared "
            f"ENDPOINT_SUFFIX_HELP text.\n"
            f"Expected substring: {shared_flat!r}\n"
            f"Got help: {help_text!r}"
        )

    def test_all_endpoint_suffix_commands_share_identical_help(self) -> None:
        """Meta-test: the help line for ``--endpoint-suffix`` is identical
        across every command that exposes the flag.

        Drift detection without referencing the shared constant — this
        fails today because the three commands disagree, and it must stay
        green after the refactor to prevent future drift.
        """
        suffix_lines: dict[str, str] = {}
        for command_name in _ENDPOINT_SUFFIX_COMMANDS:
            help_text = _render_help(command_name)
            # Locate the ``--endpoint-suffix`` block and grab the help half
            # of the option description regardless of Click's column widths.
            match = re.search(
                r"--endpoint-suffix\b[^\n]*\n(?:\s{2,}[^\n]+\n)*",
                help_text,
            )
            if match is None:
                # Option appears on a single line — fall back to that line.
                match = re.search(r"--endpoint-suffix\b[^\n]*", help_text)
            assert match is not None, (
                f"Could not locate --endpoint-suffix help block in "
                f"{command_name!r} --help output:\n{help_text}"
            )
            suffix_lines[command_name] = re.sub(r"\s+", " ", match.group(0)).strip()

        unique = set(suffix_lines.values())
        assert len(unique) == 1, (
            "--endpoint-suffix help text has drifted across commands. "
            "Every command must use the shared ENDPOINT_SUFFIX_HELP constant "
            "so the text stays in sync.\nPer-command lines:\n"
            + "\n".join(f"  {cmd}: {line!r}" for cmd, line in suffix_lines.items())
        )


# ---------------------------------------------------------------------------
# #108 — ``haute init --force``
# ---------------------------------------------------------------------------


class TestInitForceFlag:
    """Pin the contract for ``haute init`` idempotency and ``--force``.

    - Empty directory: ``haute init`` succeeds and scaffolds the project.
    - Existing ``haute.toml``: ``haute init`` exits non-zero with a
      stderr message that mentions ``--force`` so users know the escape
      hatch.
    - Existing ``haute.toml`` + ``--force``: succeeds and overwrites the
      existing scaffold files.
    - ``haute init --help`` documents the flag.
    """

    def test_init_succeeds_in_empty_directory(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Baseline: ``haute init`` in an empty dir creates ``haute.toml``.

        Guards against regressions in the existing happy path while
        ``--force`` is added.
        """
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "haute.toml").exists()

    def test_init_without_force_fails_when_haute_toml_exists(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ``--force``, re-running ``init`` on an existing project
        exits non-zero and the stderr mentions both "already exists" and
        ``--force`` so the user is pointed at the override.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "existing"\n')
        result = runner.invoke(cli, ["init"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when haute.toml exists; got {result.exit_code}.\n"
            f"Output: {result.output!r}"
        )
        combined = (result.output or "") + (result.stderr or "")
        lower = combined.lower()
        assert "already exists" in lower, (
            f"Error message should say 'already exists'; got: {combined!r}"
        )
        assert "--force" in combined, (
            f"Error message should mention --force escape hatch; got: {combined!r}"
        )

    def test_init_force_overwrites_existing_haute_toml(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``haute init --force`` succeeds in a populated dir and rewrites
        the ``haute.toml`` with fresh scaffold content.

        The discriminator: the old file's sentinel name ``"stale-project"``
        is overwritten with content generated from the current directory
        name.
        """
        project_dir = tmp_path / "fresh_project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)

        stale_toml = '[project]\nname = "stale-project"\npipeline = "old.py"\n'
        (project_dir / "haute.toml").write_text(stale_toml, encoding="utf-8")

        result = runner.invoke(cli, ["init", "--force"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        new_contents = (project_dir / "haute.toml").read_text(encoding="utf-8")
        assert "stale-project" not in new_contents, (
            "Expected --force to overwrite the old haute.toml, but the "
            f"stale sentinel is still present:\n{new_contents}"
        )
        # Fresh scaffold must reference the new project name.
        assert "fresh_project" in new_contents

    def test_init_force_overwrites_starter_pipeline(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--force`` also overwrites other scaffold files, not just
        ``haute.toml``. The starter pipeline is the second most important
        file to keep in sync — pin it too.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        rating_dir = tmp_path / "rating"
        rating_dir.mkdir()
        (rating_dir / "main.py").write_text(
            "# STALE STARTER — must be replaced by --force\n",
            encoding="utf-8",
        )

        result = runner.invoke(cli, ["init", "--force"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        fresh = (rating_dir / "main.py").read_text(encoding="utf-8")
        assert "STALE STARTER" not in fresh, (
            f"--force did not regenerate rating/main.py; got: {fresh!r}"
        )
        assert "haute.Pipeline" in fresh, (
            "Regenerated starter pipeline should instantiate haute.Pipeline."
        )

    def test_init_help_documents_force_flag(self, runner: CliRunner) -> None:
        """``haute init --help`` must list the new ``--force`` flag so
        users can discover it without reading the source.
        """
        result = runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output, (
            f"'--force' missing from 'haute init --help'. Got:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# #109 — ``_train.py`` progress bar flushes stdout
# ---------------------------------------------------------------------------


class TestTrainProgressFlush:
    """Pin that the train-progress reporter flushes stdout.

    Click's ``echo(..., nl=False)`` does not flush by default — on many
    terminals this causes the progress bar to stall until the buffer
    fills.  The dev will expose a module-level ``_progress`` (or
    equivalent) callable that flushes explicitly, and wire the ``train``
    command to reuse it.
    """

    def test_progress_reporter_flushes_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling the module-level progress reporter flushes ``sys.stdout``.

        A buffered-write without flush is what causes the "hanging" bar;
        calling ``.flush()`` is the only user-visible invariant we care
        about — the exact message format is unit-tested elsewhere.

        Uses a real :class:`io.StringIO` subclass (not ``MagicMock``) so
        that :func:`click.echo` keeps working — Click reads
        ``stream.encoding`` during its write path and a bare ``MagicMock``
        returns a non-codec value that makes the echo blow up.  Counting
        ``flush`` calls on a real stream is just as precise.
        """
        from haute.cli import _train

        progress_fn = getattr(_train, "_progress", None)
        assert progress_fn is not None, (
            "haute.cli._train must expose a module-level `_progress` "
            "function that flushes stdout after each update. It currently "
            "lives as a closure inside train() and cannot be unit-tested."
        )

        stream = _FlushCountingStream()
        monkeypatch.setattr(sys, "stdout", stream)

        progress_fn("Training", 0.5)

        assert stream.flush_count >= 1, (
            "_progress must call sys.stdout.flush() so the progress bar "
            "appears live on line-buffered terminals."
        )

    def test_progress_reporter_flushes_on_every_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every call to ``_progress`` must flush — not just the first.

        A common partial-fix is to flush only on completion (``frac == 1.0``);
        that defeats the purpose because the intermediate updates still look
        stalled.  Pin that each of three representative progress updates
        triggers at least one flush.
        """
        from haute.cli import _train

        progress_fn = getattr(_train, "_progress", None)
        assert progress_fn is not None, (
            "haute.cli._train must expose a module-level `_progress` "
            "function that flushes stdout after each update."
        )

        stream = _FlushCountingStream()
        monkeypatch.setattr(sys, "stdout", stream)

        for msg, frac in [
            ("Loading data", 0.0),
            ("Training", 0.5),
            ("Done", 1.0),
        ]:
            before = stream.flush_count
            progress_fn(msg, frac)
            after = stream.flush_count
            assert after > before, (
                f"_progress({msg!r}, {frac}) did not flush stdout — "
                f"intermediate updates will stall on line-buffered terminals."
            )

    def test_progress_reporter_renders_bar_and_percent(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``_progress`` still emits the existing bar format ``[====] 50% msg``.

        Regression guard: the fix is "add flush", not "rewrite the bar".
        The dev must preserve the visible output format so scripts and
        snapshot-style human inspection of the terminal output keep working.
        """
        from haute.cli import _train

        progress_fn = getattr(_train, "_progress", None)
        assert progress_fn is not None, "haute.cli._train must expose a module-level `_progress`."

        progress_fn("Training", 0.5)
        captured = capsys.readouterr()
        # Accept either stdout (direct write/print) or the click.echo
        # path which also lands on stdout; the exact bar width or glyph
        # is unimportant as long as the percent and message are present.
        output = captured.out + captured.err
        assert "50%" in output, (
            f"_progress must include the percentage in its output; got: {output!r}"
        )
        assert "Training" in output, (
            f"_progress must include the message in its output; got: {output!r}"
        )


# ---------------------------------------------------------------------------
# #110 — ``--version-only`` exits non-zero when nothing is registered
# ---------------------------------------------------------------------------


@pytest.fixture()
def toml_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal ``haute.toml`` + chdir so ``haute status`` can load config."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "t"\npipeline = "main.py"\n[deploy]\nmodel_name = "motor-pricing"\n',
    )
    return tmp_path


class TestVersionOnlyExitCode:
    """Pin the contract for ``haute status --version-only``.

    Before the fix, ``info.get("latest_version", 0)`` silently prints ``0``
    for both "no model registered" and "genuine version 0", then exits 0.
    Script callers cannot tell the difference.  After the fix, the CLI
    must exit non-zero and write a clear message to stderr when there is
    no version to report.
    """

    def test_version_only_exits_nonzero_when_model_not_found(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """``not_found`` from MLflow must translate to a non-zero exit."""
        mock_info = {"status": "not_found"}
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "--version-only"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when model is not found; got {result.exit_code}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        combined = (result.stdout or "") + (result.stderr or "")
        assert re.search(r"not found|no version|never registered", combined, re.I), (
            f"Expected a human-readable 'not found' message; got: {combined!r}"
        )

    def test_version_only_exits_nonzero_when_latest_version_missing(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """Missing/None ``latest_version`` must also exit non-zero.

        Today the CLI silently defaults to ``0`` via ``info.get(..., 0)``
        which is indistinguishable from a real ``0`` version.  Pin the
        stronger contract: absence must fail loudly.
        """
        mock_info = {"model_name": "m", "status": "READY"}
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "--version-only"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when latest_version is absent; "
            f"got {result.exit_code}.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

    def test_version_only_emits_message_to_stderr(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """The explanatory message goes to stderr so that ``$(haute status
        --version-only)`` stays clean for script consumption — even when
        it errors, stdout should not contain a stray version number.
        """
        mock_info = {"status": "not_found"}
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "--version-only"])
        assert result.exit_code != 0
        assert (result.stderr or "").strip(), (
            "Missing-version message must go to stderr so scripts can still parse stdout reliably."
        )
        # stdout must not contain a standalone version-like line such as
        # ``0`` that callers would otherwise interpret as a real version
        # number.  The config-loading banner is allowed; we only forbid
        # the numeric-only line that the old code emitted.
        stdout_lines = [ln.strip() for ln in (result.stdout or "").splitlines()]
        bogus_lines = [ln for ln in stdout_lines if ln.isdigit()]
        assert not bogus_lines, (
            f"stdout should not contain a bogus numeric version line when "
            f"no version is registered; got lines: {bogus_lines!r}\n"
            f"Full stdout: {result.stdout!r}"
        )

    def test_version_only_exits_zero_when_version_exists(
        self,
        runner: CliRunner,
        toml_project: Path,
    ) -> None:
        """Happy path: a registered version prints cleanly and exits 0.

        Guards against an overcorrection that breaks the existing contract
        for callers who rely on ``haute status --version-only`` returning
        the version number.
        """
        mock_info = {
            "model_name": "motor-pricing",
            "latest_version": 7,
            "status": "READY",
        }
        with patch("haute.deploy._mlflow.get_deploy_status", return_value=mock_info):
            result = runner.invoke(cli, ["status", "--version-only"])
        assert result.exit_code == 0, (
            f"Expected exit 0 when a version is registered; got "
            f"{result.exit_code}.\noutput: {result.output!r}"
        )
        # The last non-empty line of stdout must be the version.
        stdout = result.stdout or ""
        last = [line for line in stdout.strip().splitlines() if line.strip()][-1]
        assert last.strip() == "7", (
            f"Expected last stdout line to be the version number '7'; got: {last!r}"
        )


# ---------------------------------------------------------------------------
# #114 — Pre-commit ``chmod`` skipped on Windows, workaround documented
# ---------------------------------------------------------------------------


class TestPreCommitChmodWindowsSkip:
    """Pin that ``haute init`` does not attempt ``chmod`` on Windows.

    ``Path.chmod(0o755)`` is a no-op on NTFS and the git shim needs
    ``git update-index --chmod=+x`` to mark the hook executable.  The
    dev fix is (a) skip ``chmod`` when ``sys.platform == "win32"`` and
    (b) document the manual workaround as a comment next to the skip
    so future maintainers know why.
    """

    def test_no_chmod_on_win32(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On ``win32``, scaffold must not invoke ``Path.chmod``.

        We patch :meth:`pathlib.Path.chmod` and monkeypatch
        ``sys.platform``; if the current code invokes ``chmod`` on Windows
        the spy captures the call and the test fails.
        """
        monkeypatch.chdir(tmp_path)
        # Force the code path to believe it's running on Windows regardless
        # of the actual host OS.
        monkeypatch.setattr(sys, "platform", "win32")

        chmod_spy = MagicMock(name="Path.chmod")
        with patch("pathlib.Path.chmod", chmod_spy):
            result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert not chmod_spy.called, (
            "haute init should NOT call Path.chmod on Windows — chmod is "
            "a no-op on NTFS and the git shim requires "
            "`git update-index --chmod=+x` instead. "
            f"Got {chmod_spy.call_count} chmod calls: {chmod_spy.call_args_list}"
        )
        # Sanity check: the hook file should still exist so tests pinning
        # content continue to pass.
        assert (tmp_path / ".githooks" / "pre-commit").exists()

    def test_chmod_still_called_on_non_win32(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On non-Windows platforms, ``chmod(0o755)`` is still applied.

        Regression guard — the Windows skip must not accidentally neuter
        the real-POSIX path. We simulate Linux by monkeypatching
        ``sys.platform`` to ``"linux"``.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")

        chmod_spy = MagicMock(name="Path.chmod")
        with patch("pathlib.Path.chmod", chmod_spy):
            result = runner.invoke(cli, ["init"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert chmod_spy.called, (
            "On non-Windows, haute init must still call chmod(0o755) on "
            "the pre-commit hook so it is executable."
        )
        # At least one call must be the POSIX-executable bit.
        called_modes = [
            call.args[0] if call.args else call.kwargs.get("mode")
            for call in chmod_spy.call_args_list
        ]
        assert any(mode == 0o755 for mode in called_modes), (
            f"Expected at least one chmod(0o755) call; got modes: {called_modes!r}"
        )

    def test_source_documents_manual_workaround(self) -> None:
        """The source file that holds the skip must document the manual
        ``git update-index --chmod=+x`` workaround.

        Rationale: the ``chmod`` skip is only half the fix — users on
        Windows still need the hook to be executable under git.  A
        comment or docstring that names the workaround is the minimum
        cost to save a future maintainer from rediscovering this.
        """
        source_path = (
            Path(__file__).resolve().parent.parent / "src" / "haute" / "cli" / "_init_cmd.py"
        )
        text = source_path.read_text(encoding="utf-8")
        assert re.search(r"git\s+update-index\s+--chmod=\+x", text), (
            f"{source_path} must document the manual "
            "`git update-index --chmod=+x` workaround near the Windows "
            "chmod skip so maintainers understand why the skip is safe."
        )
