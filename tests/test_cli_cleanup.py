"""Tests for Phase 2 Package 3E — CLI cleanup bundle.

Each test here is a TDD target: it should FAIL against the pre-fix code and
PASS once the corresponding cleanup is applied. The package covers five CLI
code-quality issues whose common theme is "fewer silent fallbacks, more
fail-loud behaviour, one canonical path for each decision".

- #78  ``haute.cli._helpers._node_env`` — delete the defensive Windows
       PATH-injection branch. If ``node`` is not on ``PATH``, fail with a
       clear "install Node" error; do not silently prepend
       ``C:\\Program Files\\nodejs``.
- #79  ``haute.cli._helpers._open_browser`` — collapse the
       ``xdg-open`` → ``open`` → ``webbrowser`` → ``webbrowser`` cascade
       into a single :func:`webbrowser.open` call. When that fails, print
       the URL so the user can click it themselves rather than chaining
       more fallbacks.
- #80  ``haute.cli._helpers._find_frontend_dir`` — returning ``None``
       silently when no ``frontend/`` directory is found hides the missing
       front-end from callers. It must raise; callers (``serve`` etc.)
       decide whether dev-mode-vs-production is an error and handle the
       exception themselves.
- #81  ``haute.cli._smoke.smoke`` — when the user needs a better error
       message (e.g. missing databricks endpoint name), use the project-wide
       convention of ``click.echo("Error: ...", err=True); raise SystemExit(1)``
       (seven other commands do this). Do *not* raise ``click.UsageError``
       (which exits with code 2 and dumps Click's usage header).
- #82  ``haute.cli._impact`` — ``staging_suffix`` must have exactly one
       source of truth: ``config.ci.staging_endpoint_suffix`` from
       ``haute.toml``. The CLI flag ``--endpoint-suffix`` overrides it
       only when given. The literal ``"_staging"`` fall-through is a code
       smell: it silently masks a missing config.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _write_smoke_toml(
    tmp_path: Path,
    *,
    target: str = "databricks",
    with_endpoint_name: bool = True,
    with_endpoint_suffix: bool = False,
    staging_url: str = "",
) -> None:
    """Write a haute.toml + test_quotes for ``haute smoke`` tests."""
    container_section = (
        '[deploy.container]\nbase_image = "python:3.11.9-slim"\n'
        if target in {"container", "azure-container-apps", "aws-ecs", "gcp-run"}
        else ""
    )
    deploy_lines = ["[deploy]", 'model_name = "test-model"', f'target = "{target}"']
    if with_endpoint_name:
        deploy_lines.append('endpoint_name = "test-ep"')
    if with_endpoint_suffix:
        deploy_lines.append('endpoint_suffix = "-suff"')
    deploy_section = "\n".join(deploy_lines) + "\n"

    toml = (
        f'[project]\nname = "t"\npipeline = "main.py"\n'
        f"{deploy_section}"
        f"{container_section}"
        f'[test_quotes]\ndir = "tests/quotes"\n'
        f'[ci]\nprovider = "github"\n'
        f'[ci.staging]\nendpoint_suffix = "-staging"\n'
        f'endpoint_url = "{staging_url}"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)

    quotes_dir = tmp_path / "tests" / "quotes"
    quotes_dir.mkdir(parents=True)
    (quotes_dir / "basic.json").write_text(json.dumps([{"VehPower": 5, "Area": "A"}]))


def _write_impact_project(
    tmp_path: Path,
    *,
    target: str = "databricks",
    staging_suffix_in_toml: str | None = "-staging",
    staging_url: str = "",
) -> None:
    """Write a haute.toml + impact dataset for ``haute impact`` tests.

    ``staging_suffix_in_toml`` — when ``None``, the ``[ci.staging]`` section
    omits ``endpoint_suffix`` entirely so the caller can exercise the
    "missing config" path.
    """
    container_section = (
        '[deploy.container]\nbase_image = "python:3.11.9-slim"\n'
        if target in {"container", "azure-container-apps", "aws-ecs", "gcp-run"}
        else ""
    )
    ci_suffix_line = (
        f'endpoint_suffix = "{staging_suffix_in_toml}"\n'
        if staging_suffix_in_toml is not None
        else ""
    )
    toml = (
        f'[project]\nname = "t"\npipeline = "main.py"\n'
        f'[deploy]\nmodel_name = "test-model"\nendpoint_name = "test-ep"\n'
        f'target = "{target}"\n'
        f"{container_section}"
        f'[safety]\nimpact_dataset = "data/impact.parquet"\n'
        f'[ci]\nprovider = "github"\n'
        f"[ci.staging]\n"
        f"{ci_suffix_line}"
        f'endpoint_url = "{staging_url}"\n'
    )
    (tmp_path / "haute.toml").write_text(toml)
    (tmp_path / ".git").mkdir(exist_ok=True)

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    df = pl.DataFrame(
        {
            "VehPower": [5, 6, 7],
            "Area": ["A", "B", "C"],
            "premium": [100.0, 200.0, 300.0],
        }
    )
    df.write_parquet(data_dir / "impact.parquet")


# ---------------------------------------------------------------------------
# #78 — ``_node_env`` must not silently patch PATH on Windows
# ---------------------------------------------------------------------------


class TestNodeEnvFailsLoudlyWithoutNode:
    """Direction: delete the Windows ``C:\\Program Files\\nodejs`` injection.

    The current implementation prepends that directory to ``PATH`` when
    ``shutil.which('node')`` returns ``None`` on Windows. That is a silent,
    machine-specific hack: it hides the real problem (Node isn't installed
    / on PATH) from the user and only works when Node happens to be in the
    default MSI location.

    Correct behaviour:

    1. When Node is on PATH, return ``None`` (no env override needed).
    2. When Node is NOT on PATH, raise a :class:`click.ClickException`
       (or an equally visible error) whose message names Node and points
       at https://nodejs.org. Do not read the registry, the MSI default
       path, or anywhere else.
    """

    def test_returns_none_when_node_on_path(self) -> None:
        """If ``which('node')`` succeeds, no override is needed."""
        with patch("haute.cli._helpers.shutil.which", return_value="/usr/local/bin/node"):
            from haute.cli._helpers import _node_env

            assert _node_env() is None

    def test_missing_node_on_windows_fails_loudly(self) -> None:
        """``_node_env`` must raise a clear error when Node is absent.

        Pre-fix this test fails because ``_node_env`` either silently
        returns ``None`` (no nodejs dir) or silently returns a patched
        env dict (nodejs dir exists). Either way the user never learns
        they need to install Node.
        """
        import click

        with patch("haute.cli._helpers.shutil.which", return_value=None):
            from haute.cli._helpers import _node_env

            with pytest.raises((click.ClickException, RuntimeError, FileNotFoundError)) as exc_info:
                _node_env()

            msg = str(exc_info.value).lower()
            # Must name Node so the user knows what's missing
            assert "node" in msg, f"Error must mention Node: {exc_info.value}"
            # Must point at the official install URL so the user knows the fix
            assert "nodejs.org" in msg, (
                f"Error must direct user to https://nodejs.org: {exc_info.value}"
            )

    def test_missing_node_does_not_inject_program_files_path(self) -> None:
        """The Windows ``C:\\Program Files\\nodejs`` branch must be gone.

        If Node is missing, ``_node_env`` must NOT return a patched env
        dict — regardless of whether ``C:\\Program Files\\nodejs\\node.exe``
        happens to exist on the test machine. Pre-fix this test fails
        because the function silently returns ``{'PATH': '...nodejs;...'}``.
        """
        import click

        # The correct implementation never branches on platform at all —
        # it just uses shutil.which and raises on miss.  No Path.exists
        # check exists to trick anymore, so the assertion is simply
        # "missing node raises, full stop".
        with patch("haute.cli._helpers.shutil.which", return_value=None):
            from haute.cli._helpers import _node_env

            with pytest.raises((click.ClickException, RuntimeError, FileNotFoundError)):
                _node_env()


# ---------------------------------------------------------------------------
# #79 — ``_open_browser`` must use a single ``webbrowser.open`` call
# ---------------------------------------------------------------------------


class TestOpenBrowserSingleCall:
    """Direction: one :func:`webbrowser.open` call; on failure, print the URL.

    The current cascade (``xdg-open`` → ``open`` → ``webbrowser`` →
    ``webbrowser`` again via the ``except Exception`` arm) is a textbook
    defensive anti-pattern: four attempts at the same job, each more
    desperate than the last. :mod:`webbrowser` already handles platform
    detection internally; delegating to it once is sufficient.

    When ``webbrowser.open`` fails or returns ``False``, the URL must be
    printed to the user so they can click it themselves — that's the
    only sensible "fallback" (hand the URL to the human).
    """

    def test_uses_single_webbrowser_open_on_success(self) -> None:
        """Happy path: one call to ``webbrowser.open``, no subprocess hack.

        The correctly-refactored implementation routes to
        :func:`webbrowser.open` regardless of platform and must never
        touch ``subprocess.*``.  The old cascade's platform branch is
        gone entirely, so this test no longer needs to pin
        ``sys.platform`` — any platform it runs on should take the same
        single-call path.
        """
        with (
            patch("haute.cli._helpers.webbrowser") as mock_wb,
            patch("haute.cli._helpers.subprocess") as mock_sub,
        ):
            mock_wb.open.return_value = True

            from haute.cli._helpers import _open_browser

            _open_browser("http://localhost:8000")

            # Exactly one webbrowser.open call — no retries.
            assert mock_wb.open.call_count == 1, (
                f"Expected exactly 1 webbrowser.open call, got {mock_wb.open.call_count}"
            )
            mock_wb.open.assert_called_once_with("http://localhost:8000")
            # No subprocess hacks — platform dispatch is webbrowser's job.
            mock_sub.call.assert_not_called()
            mock_sub.Popen.assert_not_called()

    def test_failure_prints_url_to_user(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When the browser can't be launched, the URL must be surfaced.

        Pre-fix the ``except Exception`` arm silently calls
        ``webbrowser.open`` *again* — which, if the first call raised,
        will raise again and crash the CLI. The correct behaviour is to
        show the user the URL so they can paste it into their browser.
        """
        url = "http://localhost:8000"

        with patch("haute.cli._helpers.webbrowser") as mock_wb:
            mock_wb.open.side_effect = RuntimeError("no display")

            from haute.cli._helpers import _open_browser

            # Must not raise — the user-facing fallback is "print the URL".
            _open_browser(url)

            # URL must appear in stdout or stderr so the user can see it.
            captured = capsys.readouterr()
            combined = captured.out + captured.err
            assert url in combined, (
                f"Expected URL {url!r} in output when browser failed. "
                f"stdout={captured.out!r} stderr={captured.err!r}"
            )
            # Must not have retried — the whole point is one attempt.
            assert mock_wb.open.call_count == 1, (
                f"Expected exactly 1 open() attempt, got {mock_wb.open.call_count}"
            )

    def test_no_platform_dispatch(self) -> None:
        """The function must not read ``sys.platform``.

        ``webbrowser.open`` is already platform-aware.  The correct
        implementation doesn't even import ``sys`` in this module, so
        asserting ``subprocess`` is never called (plus ``sys`` not being
        present at ``haute.cli._helpers.sys``) together pin the cascade
        cruft as gone.
        """
        import haute.cli._helpers as helpers_mod

        # ``sys`` must not be imported at module scope — the platform
        # branch is gone entirely, so the import would be dead weight.
        assert not hasattr(helpers_mod, "sys"), (
            "`sys` is still imported in haute.cli._helpers — "
            "platform dispatch should be handled by webbrowser.open() alone."
        )

        with (
            patch("haute.cli._helpers.webbrowser") as mock_wb,
            patch("haute.cli._helpers.subprocess") as mock_sub,
        ):
            mock_wb.open.return_value = True

            from haute.cli._helpers import _open_browser

            _open_browser("http://example.com")

            mock_sub.call.assert_not_called()
            mock_sub.Popen.assert_not_called()


# ---------------------------------------------------------------------------
# #80 — ``_find_frontend_dir`` must raise when no frontend/ is found
# ---------------------------------------------------------------------------


class TestFindFrontendDirRaisesWhenAbsent:
    """Direction: raise instead of returning ``None``.

    Pre-fix ``_find_frontend_dir`` walks ``cwd`` and its parents for a
    ``frontend/package.json`` and returns ``None`` when it can't find one.
    The caller (``haute serve``) then has to do its own ``frontend_dir is
    not None`` ternary dance to decide dev-vs-prod mode — which duplicates
    logic across call-sites and hides "no frontend installed at all" as
    an implicit production signal.

    Correct behaviour: raise a :class:`FileNotFoundError` (or specific
    subclass) with a clear message. The caller wraps the call in
    ``try/except`` to implement the dev-vs-prod branch. The frontend
    dir's presence/absence is now an *explicit* signal, not a silent
    ``None``.
    """

    def test_found_in_cwd_returns_path(self, tmp_path: Path) -> None:
        """Happy path: a frontend/ in cwd is returned as a Path."""
        fe = tmp_path / "frontend"
        fe.mkdir()
        (fe / "package.json").write_text("{}")

        with patch("haute.cli._helpers.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path

            from haute.cli._helpers import _find_frontend_dir

            result = _find_frontend_dir()

        assert result == fe

    def test_missing_frontend_raises(self, tmp_path: Path) -> None:
        """No frontend/ anywhere in the ancestor chain must raise.

        Pre-fix this test fails because ``_find_frontend_dir`` returns
        ``None`` silently. After the fix, a missing frontend directory
        is a loud error — the caller is expected to catch it if a
        missing frontend is acceptable (e.g. production mode).
        """
        with patch("haute.cli._helpers.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path

            from haute.cli._helpers import _find_frontend_dir

            with pytest.raises(FileNotFoundError) as exc_info:
                _find_frontend_dir()

            msg = str(exc_info.value).lower()
            assert "frontend" in msg, f"Error must name 'frontend' directory: {exc_info.value}"

    def test_serve_handles_missing_frontend_in_prod_mode(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``haute serve`` must catch the exception and choose prod mode.

        The cleanup moves dev-vs-prod decision into the caller. When
        ``_find_frontend_dir`` raises, ``serve`` should fall back to
        production (serve built static files) — not crash. This test
        exercises the integration: missing frontend + existing static
        dir → prod serve succeeds.
        """
        monkeypatch.chdir(tmp_path)
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (static / "assets").mkdir()

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.cli._serve._port_is_available", return_value=True),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# #81 — ``haute smoke`` must use click.echo + SystemExit(1), not UsageError
# ---------------------------------------------------------------------------


class TestSmokeErrorStyleIsProjectConvention:
    """Direction: match the seven other commands' error-echo + exit(1) pattern.

    Pre-fix, ``_smoke.py:48-50`` raises ``click.UsageError`` when the
    databricks target has no endpoint name. Every other CLI command
    (``train``, ``run``, ``status``, ``init``, ``deploy``, ``impact``,
    ``serve``) uses::

        click.echo("Error: ...", err=True)
        raise SystemExit(1)

    The UsageError branch produces two user-observable differences:

    1. Exit code 2 instead of 1 — which CI scripts that check
       ``$? -eq 1`` will misread.
    2. Click prepends its ``Usage: smoke [OPTIONS]`` / ``Try
       'smoke --help' for help.`` header — noise for an error that
       isn't a bad argument, just missing config.

    The fix is to delete the ``click.UsageError`` call and replace it
    with the standard echo + SystemExit(1) pair.
    """

    def test_missing_databricks_endpoint_uses_exit_code_1(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing endpoint_name on databricks must exit with code 1.

        Pre-fix this exits with 2 (click.UsageError). Post-fix it must
        exit 1 to match every other command's "config missing" failure.
        """
        monkeypatch.chdir(tmp_path)
        # databricks target without endpoint_name or endpoint_suffix →
        # effective_endpoint_name is None → triggers the error path.
        _write_smoke_toml(
            tmp_path,
            target="databricks",
            with_endpoint_name=False,
            with_endpoint_suffix=False,
        )

        mock_ws = MagicMock()
        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("time.sleep"),
        ):
            result = runner.invoke(cli, ["smoke"])

        assert result.exit_code == 1, (
            f"Expected exit code 1 to match other commands' echo+exit pattern, "
            f"got {result.exit_code}. Output:\n{result.output}"
        )

    def test_missing_databricks_endpoint_has_no_usage_header(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``click.UsageError`` dumps a ``Usage: ...`` header — the fixed
        path must not.

        ``click.UsageError`` renders as::

            Usage: smoke [OPTIONS]
            Try 'smoke --help' for help.

            Error: ...

        The project convention (plain ``click.echo("Error: ...", err=True)``)
        produces only::

            Error: ...
        """
        monkeypatch.chdir(tmp_path)
        _write_smoke_toml(
            tmp_path,
            target="databricks",
            with_endpoint_name=False,
            with_endpoint_suffix=False,
        )

        mock_ws = MagicMock()
        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("time.sleep"),
        ):
            result = runner.invoke(cli, ["smoke"])

        # The Click-specific usage header must not appear.
        assert "Usage: " not in result.output, (
            f"click.UsageError leaks 'Usage:' header — the command should "
            f"use echo+SystemExit(1) instead. Output:\n{result.output}"
        )
        assert "Try '" not in result.output, (
            f"click.UsageError leaks 'Try --help' suggestion. Output:\n{result.output}"
        )

    def test_missing_databricks_endpoint_echoes_clear_error(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The user-facing error must still be clear and actionable.

        Swapping the error-raising style must NOT weaken the message.
        It should still name ``endpoint_name`` / ``endpoint_suffix`` /
        ``haute.toml`` so the user knows exactly what to fix.
        """
        monkeypatch.chdir(tmp_path)
        _write_smoke_toml(
            tmp_path,
            target="databricks",
            with_endpoint_name=False,
            with_endpoint_suffix=False,
        )

        mock_ws = MagicMock()
        with (
            patch("databricks.sdk.WorkspaceClient", return_value=mock_ws),
            patch("time.sleep"),
        ):
            result = runner.invoke(cli, ["smoke"])

        output_lower = result.output.lower()
        assert "error" in output_lower, (
            f"Output must flag an error condition. Output:\n{result.output}"
        )
        assert "endpoint" in output_lower, (
            f"Error must name 'endpoint' so the user knows what's missing. Output:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# #82 — ``haute impact`` staging_suffix must have one canonical source
# ---------------------------------------------------------------------------


class TestImpactStagingSuffixSingleSource:
    """Direction: single source — ``config.ci.staging_endpoint_suffix``.

    Pre-fix ``_impact.py:49`` resolves ``staging_suffix`` as::

        endpoint_suffix or config.ci.staging_endpoint_suffix or "_staging"

    Three fallbacks, the last of which is a silent hard-coded literal that
    masks both missing CLI flags and missing TOML config. Post-fix:

    - ``config.ci.staging_endpoint_suffix`` is the canonical value (loaded
      from ``[ci.staging].endpoint_suffix`` in ``haute.toml``).
    - ``--endpoint-suffix FOO`` on the CLI overrides it only when given.
    - The ``"_staging"`` literal fallback is removed. (The config default
      is already ``"-staging"`` via :class:`CIConfig`; if a deployment
      needs a different suffix, it must be configured explicitly.)
    """

    def test_cli_flag_overrides_config(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--endpoint-suffix FOO`` must win over the TOML value."""
        _write_impact_project(tmp_path, staging_suffix_in_toml="-from-toml")
        monkeypatch.chdir(tmp_path)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact", "--endpoint-suffix", "-from-cli"])

        assert result.exit_code == 0, result.output
        # The staging endpoint name passed into the transport must have
        # used the CLI-provided suffix, not the TOML one.
        staging_name_arg = mock_db.call_args[0][0]  # 1st positional = staging_name
        assert staging_name_arg.endswith("-from-cli"), (
            f"Expected staging name to end with CLI suffix '-from-cli', got {staging_name_arg!r}"
        )
        assert "-from-toml" not in staging_name_arg, (
            f"CLI flag must override TOML value, but '-from-toml' leaked into "
            f"staging name: {staging_name_arg!r}"
        )

    def test_config_used_when_cli_flag_absent(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ``--endpoint-suffix``, ``config.ci.staging_endpoint_suffix``
        is the single source of truth."""
        _write_impact_project(tmp_path, staging_suffix_in_toml="-from-toml")
        monkeypatch.chdir(tmp_path)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact"])

        assert result.exit_code == 0, result.output
        staging_name_arg = mock_db.call_args[0][0]
        assert staging_name_arg.endswith("-from-toml"), (
            f"Expected staging name to end with TOML suffix '-from-toml', got {staging_name_arg!r}"
        )

    def test_no_hardcoded_underscore_staging_fallback(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The ``"_staging"`` literal fallback must not silently activate.

        Pre-fix, if both ``endpoint_suffix`` and
        ``config.ci.staging_endpoint_suffix`` were falsy, the code
        appended the literal ``"_staging"`` — a hard-coded value that
        only accidentally matches common convention and silently masks
        an empty config.

        Post-fix: the ``CIConfig.staging_endpoint_suffix`` default
        (``"-staging"``) is the only source. Even if the user explicitly
        sets an empty string in TOML (a pathological case), the code must
        not fall through to the literal ``"_staging"``. The staging name
        should reflect what's configured, nothing else.
        """
        # Write a TOML where staging.endpoint_suffix is explicitly empty.
        _write_impact_project(tmp_path, staging_suffix_in_toml="")
        monkeypatch.chdir(tmp_path)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact"])

        # Whether the result is a clear error or a run with no suffix is
        # an implementation choice — but the hard-coded ``_staging``
        # literal must not leak into the staging name. That's the smoke
        # signal for the three-fallback anti-pattern.
        if result.exit_code == 0:
            staging_name_arg = mock_db.call_args[0][0]
            assert "_staging" not in staging_name_arg, (
                f"Hard-coded '_staging' fallback leaked into staging name: "
                f"{staging_name_arg!r}. Expected no silent literal fallback."
            )
        else:
            # If the fix chose to fail loudly on empty suffix, the error
            # must name the config key, not silently default.
            output_lower = result.output.lower()
            assert "endpoint_suffix" in output_lower or "staging" in output_lower, (
                f"Error must name the missing config. Output:\n{result.output}"
            )
            # And still must not print the literal _staging as a hint.
            assert "_staging" not in result.output, (
                f"Error output should not suggest the hard-coded "
                f"'_staging' literal: {result.output!r}"
            )

    def test_cli_flag_wins_even_when_empty_config(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With empty TOML suffix but a CLI flag, the CLI flag wins.

        Confirms that the CLI flag is an unconditional override when
        provided — it must not be discarded just because TOML happens
        to be empty.
        """
        _write_impact_project(tmp_path, staging_suffix_in_toml="")
        monkeypatch.chdir(tmp_path)

        with (
            patch("haute.cli._impact._impact_databricks") as mock_db,
            patch("haute.deploy._impact.format_terminal", return_value="Report"),
            patch("haute.deploy._impact.format_markdown", return_value="# Report"),
        ):
            mock_db.return_value = ([], [], False)
            result = runner.invoke(cli, ["impact", "--endpoint-suffix", "-override"])

        assert result.exit_code == 0, result.output
        staging_name_arg = mock_db.call_args[0][0]
        assert staging_name_arg.endswith("-override"), (
            f"CLI flag '-override' must win. Got: {staging_name_arg!r}"
        )
        # Literal fallback must never appear.
        assert "_staging" not in staging_name_arg, (
            f"Hard-coded '_staging' literal leaked: {staging_name_arg!r}"
        )
