"""Host-binding invariants for the serve command.

These tests pin the behaviour of ``haute serve``'s host selection:

* The default host MUST be ``127.0.0.1`` (loopback-only). Exposing a
  local dev tool to the wider network has to be an explicit,
  deliberate action.
* Any non-loopback host (e.g. ``0.0.0.0``) MUST trigger a WARNING-level
  structlog event that mentions the risk of exposing the server
  beyond localhost. Missing warnings are regressions — an unaware user
  firing ``--host 0.0.0.0`` silently would be a security bug.
* ``haute.toml`` MAY declare ``[server] host = "..."`` as a
  project-wide override. When the override is non-loopback, the same
  WARNING must fire; CLI flags still win over the TOML.
* The safe default ``127.0.0.1`` MUST NOT emit the "exposed" warning
  — the warning is reserved for genuinely risky bindings.

No ``unittest.TestCase``; plain pytest classes. No network access —
``uvicorn.run`` is patched out on every path. The CLI static-dir check
is also patched so the ``serve`` command does not need a built
frontend to exercise the host-resolution logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import structlog.testing

from haute.cli import cli
from haute.cli._serve import ServeConfig, handle_serve

if TYPE_CHECKING:
    from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers — small seams so each test is a one-liner.
# ---------------------------------------------------------------------------


def _fake_static_dir(tmp_path: Path) -> Path:
    """Create a static directory so ``handle_serve`` can reach ``uvicorn.run``.

    The ``serve`` prod-mode branch exits early with "No built frontend
    found" when ``STATIC_DIR`` is missing; a host-binding test only
    cares about the host/port selection and must get past that gate.
    """
    static = tmp_path / "static"
    static.mkdir()
    return static


def _warnings_for_host_exposure(captured: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return captured log events that warn about exposing the server.

    The contract is intentionally loose on the *event name* — what
    matters is (a) a warning-level event fires and (b) its rendered
    payload mentions the risk of non-loopback binding. That lets the
    dev pick whatever event name feels idiomatic without forcing a
    rename on every test change.
    """
    hits: list[dict[str, object]] = []
    for ev in captured:
        if ev.get("log_level") != "warning":
            continue
        # Search all values in the event dict for the exposure phrase so
        # the warning can live in the ``event=`` field OR in any of the
        # structured key/value pairs (``msg=``, ``hint=``, etc.).
        payload = " ".join(str(v) for v in ev.values()).lower()
        if "beyond localhost" in payload or "exposing beyond localhost" in payload:
            hits.append(ev)
    return hits


# ---------------------------------------------------------------------------
# #118.1 — CLI default binding
# ---------------------------------------------------------------------------


class TestCliDefaultHost:
    """``haute serve`` without ``--host`` must bind to 127.0.0.1.

    Why this is a security invariant, not a style preference:
    Haute is a dev-only tool that ships no authentication. A default
    of ``0.0.0.0`` would expose the pipeline editor, file browser, and
    a Polars execution endpoint to every peer on the LAN — instantly.
    The default has to fail CLOSED (loopback-only).
    """

    def test_default_host_is_loopback(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``haute serve`` with no ``--host`` passes 127.0.0.1 to uvicorn."""
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "127.0.0.1", (
            "Default host MUST be loopback-only; got "
            f"{call_kwargs['host']!r}. Non-loopback defaults expose the "
            "unauthenticated dev server to the LAN."
        )

    def test_default_host_emits_no_exposure_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Default loopback bind must NOT emit the exposure warning.

        Crying wolf on the safe default conditions users to ignore the
        warning entirely — defeating its purpose when they genuinely
        bind to 0.0.0.0 later.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            structlog.testing.capture_logs() as captured,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        warnings = _warnings_for_host_exposure(captured)
        assert warnings == [], (
            f"Safe default (127.0.0.1) must not emit the exposure warning; got {warnings!r}"
        )


# ---------------------------------------------------------------------------
# #118.2 — Explicit non-loopback binding
# ---------------------------------------------------------------------------


class TestCliExplicitNonLoopback:
    """``--host 0.0.0.0`` must work but must also WARN loudly.

    The flag is the user's way of saying "yes, I know what I'm doing".
    We respect the choice — but we also record a structured warning so
    the intent is auditable and so anyone reading the server logs
    (e.g. later asking "why is my dev tool scraping credentials?") can
    find the exact moment the exposure started.
    """

    def test_explicit_all_interfaces_passes_through(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--host 0.0.0.0`` is honoured — uvicorn sees the literal value."""
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser", "--host", "0.0.0.0"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "0.0.0.0"

    def test_explicit_all_interfaces_emits_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Binding to 0.0.0.0 fires a warning that names the risk.

        Asserts both the level (``warning``, not ``info`` or ``debug``)
        and the phrasing ("exposing beyond localhost"). The tight
        assertion on wording is deliberate: silent security warnings
        are nearly as dangerous as the exposure itself.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            structlog.testing.capture_logs() as captured,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser", "--host", "0.0.0.0"])

        assert result.exit_code == 0, result.output
        warnings = _warnings_for_host_exposure(captured)
        assert warnings, (
            "Binding to 0.0.0.0 MUST emit a warning about exposure beyond "
            f"localhost. Captured events: {captured!r}"
        )

    def test_warning_has_warning_level_not_info(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exposure event's ``log_level`` is ``warning`` exactly.

        ``logger.info("exposed", ...)`` would pass a naive "is the
        string in the logs" check while being completely invisible in
        a production log aggregator filtered to WARN+. Assert the
        level explicitly.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            structlog.testing.capture_logs() as captured,
        ):
            runner.invoke(cli, ["serve", "--no-browser", "--host", "0.0.0.0"])

        warnings = _warnings_for_host_exposure(captured)
        assert warnings, "no exposure warning captured at all"
        for ev in warnings:
            assert ev["log_level"] == "warning", (
                f"exposure event fired at wrong level: {ev['log_level']!r}; "
                "the whole point of the warning is visibility"
            )

    def test_warning_mentions_exposure_phrase(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Warning payload names the risk in plain English.

        "exposing beyond localhost" is the exact phrasing the spec
        asks for — the warning needs to be self-explanatory so a user
        who hits it can act without grep-ing the codebase.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            structlog.testing.capture_logs() as captured,
        ):
            runner.invoke(cli, ["serve", "--no-browser", "--host", "0.0.0.0"])

        warnings = _warnings_for_host_exposure(captured)
        assert warnings, "no exposure warning captured"
        # Pool the payloads so the phrase is allowed to live in any
        # field (``event``, ``msg``, ``hint``, ...) — the exact wire
        # layout is up to the dev.
        payload = " ".join(str(v) for ev in warnings for v in ev.values()).lower()
        assert "exposing beyond localhost" in payload, (
            "warning must contain the phrase 'exposing beyond localhost' so "
            f"users understand the risk; got payload: {payload!r}"
        )


# ---------------------------------------------------------------------------
# #118.3 — Explicit safe host (no warning)
# ---------------------------------------------------------------------------


class TestCliExplicitLoopback:
    """Explicit ``--host 127.0.0.1`` behaves like the default — no warning.

    A user who re-types the default to make it explicit is not doing
    anything dangerous and should not be pestered with a warning.
    This is the counterexample that guards against naive
    implementations like ``logger.warning(f"host={host}")``.
    """

    def test_explicit_loopback_no_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
            structlog.testing.capture_logs() as captured,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser", "--host", "127.0.0.1"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["host"] == "127.0.0.1"
        warnings = _warnings_for_host_exposure(captured)
        assert warnings == [], (
            f"Explicit 127.0.0.1 must not trigger the exposure warning; got {warnings!r}"
        )


# ---------------------------------------------------------------------------
# #118.4 — haute.toml [server] host override
# ---------------------------------------------------------------------------


class TestHauteTomlServerHost:
    """A project can set ``[server] host`` in ``haute.toml``.

    When the project-wide override is a non-loopback host, the same
    exposure warning must fire — regardless of whether the user passed
    ``--host`` on the CLI.  Warnings guarding the CLI path would be
    useless if the same risky bind slipped through via a committed
    config file.
    """

    def _write_server_host_toml(self, project_dir: Path, host: str) -> None:
        """Write a minimal haute.toml with a ``[server]`` host override."""
        (project_dir / "haute.toml").write_text(
            f'[project]\nname = "host_binding_test"\n\n[server]\nhost = "{host}"\n',
            encoding="utf-8",
        )

    def test_toml_non_loopback_host_triggers_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``[server] host = "0.0.0.0"`` in haute.toml fires the same warning.

        The whole point of host-binding safety is that the warning
        fires on every path to a non-loopback bind. A missing warning
        here means a project could silently commit an exposure to its
        own config file.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)
        self._write_server_host_toml(tmp_path, "0.0.0.0")

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
            structlog.testing.capture_logs() as captured,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        # Uvicorn must see the TOML-supplied host (override of the
        # CLI default).
        assert mock_run.call_args.kwargs["host"] == "0.0.0.0", (
            "haute.toml [server] host override did not reach uvicorn; "
            f"got {mock_run.call_args.kwargs['host']!r}"
        )
        warnings = _warnings_for_host_exposure(captured)
        assert warnings, (
            "haute.toml override to 0.0.0.0 must emit the same exposure "
            f"warning as the CLI path; captured events: {captured!r}"
        )

    def test_toml_loopback_host_no_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``[server] host = "127.0.0.1"`` in haute.toml is silent.

        Mirror of the CLI loopback case — a project explicitly pinning
        the safe default is not risky.
        """
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)
        self._write_server_host_toml(tmp_path, "127.0.0.1")

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
            structlog.testing.capture_logs() as captured,
        ):
            result = runner.invoke(cli, ["serve", "--no-browser"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["host"] == "127.0.0.1"
        warnings = _warnings_for_host_exposure(captured)
        assert warnings == [], (
            f"TOML-supplied loopback host must not emit the exposure warning; got {warnings!r}"
        )


# ---------------------------------------------------------------------------
# #118.5 — handle_serve (pure function) is the shared host gate
# ---------------------------------------------------------------------------


class TestHandleServeBindingGate:
    """``handle_serve`` is the single point where host resolution happens.

    The click-decorated ``serve`` command is a thin wrapper — every
    non-CLI caller (tests, programmatic uses, future alternative
    frontends) should get the same host-binding guarantees. If the
    warning lived only in the ``serve`` Click wrapper, the pure path
    could quietly bind to 0.0.0.0 and never warn.
    """

    def test_handle_serve_warns_on_non_loopback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calling ``handle_serve`` directly still fires the warning."""
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run") as mock_run,
            structlog.testing.capture_logs() as captured,
        ):
            handle_serve(ServeConfig(host="0.0.0.0", port=8000, no_browser=True))

        # Uvicorn receives the literal host.
        assert mock_run.call_args.kwargs["host"] == "0.0.0.0"
        warnings = _warnings_for_host_exposure(captured)
        assert warnings, (
            "handle_serve must fire the exposure warning regardless of "
            "whether it was reached via Click or a programmatic caller; "
            f"captured events: {captured!r}"
        )

    def test_handle_serve_silent_on_loopback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``handle_serve(host="127.0.0.1")`` emits no exposure warning."""
        monkeypatch.chdir(tmp_path)
        static = _fake_static_dir(tmp_path)

        with (
            patch(
                "haute.cli._serve._find_frontend_dir",
                side_effect=FileNotFoundError("no frontend/ anywhere"),
            ),
            patch("haute.server.STATIC_DIR", static),
            patch("uvicorn.run"),
            structlog.testing.capture_logs() as captured,
        ):
            handle_serve(ServeConfig(host="127.0.0.1", port=8000, no_browser=True))

        warnings = _warnings_for_host_exposure(captured)
        assert warnings == [], f"handle_serve on loopback must not warn; got {warnings!r}"
