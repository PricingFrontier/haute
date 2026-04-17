"""Tests for Phase 1 Package 1I — discovery fail-loudly.

Item #23: ``discover_pipelines`` used to silently swallow ``OSError`` when a
candidate ``.py`` file could not be read (unreadable due to permissions,
corrupt device, etc.).  The fix replaces the bare ``pass``/``continue`` with a
WARNING log that names the path and the underlying error reason, optionally
raising :class:`~haute.errors.ConfigError` when ``strict=True`` is passed.

These tests cover:

* Regression guard — a readable file is still discovered normally.
* A permission-denied file logs at WARNING and is excluded from the result.
* A file whose ``read_text`` raises a generic ``OSError`` logs at WARNING.
* The configured-pipeline branch (lines 63-66) has the same bug; it must also
  log at WARNING.
* Non-strict mode continues to return the other discovered files — the failure
  is visible via the log, not the return value.
* Optional ``strict=True`` raises :class:`ConfigError` (skipped if the strict
  kwarg is not implemented — the OPTIONAL extension in the task spec).

The tests use pytest's ``caplog`` fixture to capture structured log records.
``configure_logging`` is invoked once per test to bridge structlog into stdlib
``logging`` so ``caplog`` sees the records.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest
import structlog

from haute._logging import configure_logging
from haute.discovery import discover_pipelines
from haute.errors import ConfigError

PIPELINE_CONTENT = """\
import haute

pipeline = haute.Pipeline("test")
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _structlog_to_caplog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge structlog output through stdlib logging so ``caplog`` sees it.

    The discovery module creates ``logger = get_logger(component="discovery")``
    at import time via a :class:`structlog._config.BoundLoggerLazyProxy`.  The
    proxy caches the underlying logger on first use (``cache_logger_on_first_use
    =True`` in :func:`haute._logging.configure_logging`), so if an earlier test
    has already emitted through it, the cached logger may predate our
    configuration.  We therefore:

    1. Reset structlog defaults.
    2. Call ``configure_logging`` to install the stdlib ``LoggerFactory``.
    3. Replace ``haute.discovery.logger`` with a fresh proxy bound to the new
       configuration — this guarantees emissions route through stdlib logging.

    ``monkeypatch`` undoes step 3 after the test; step 2's global config is
    harmless between tests and cheap to re-apply.
    """
    import haute.discovery as _discovery_mod

    structlog.reset_defaults()
    configure_logging()
    fresh = structlog.get_logger(component="discovery")
    monkeypatch.setattr(_discovery_mod, "logger", fresh)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _records_mentioning(
    caplog: pytest.LogCaptureFixture, needle: str
) -> list[logging.LogRecord]:
    """Return WARNING-level records that contain ``needle`` in message or args."""
    out: list[logging.LogRecord] = []
    for rec in caplog.records:
        if rec.levelno != logging.WARNING:
            continue
        # structlog renders kwargs into getMessage() output via the stdlib
        # ProcessorFormatter, but the raw record also exposes them as attrs
        # (e.g. rec.path, rec.error) when the kwargs are forwarded as extras.
        haystack_parts = [rec.getMessage(), str(getattr(rec, "args", "") or "")]
        for key, value in rec.__dict__.items():
            # Skip stdlib builtins; only include the structlog-injected kwargs.
            if key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "taskName",
                "_record",
                "_from_structlog",
            }:
                continue
            haystack_parts.append(str(value))
        if any(needle in part for part in haystack_parts):
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Regression guard — readable files still work
# ---------------------------------------------------------------------------


class TestRegressionGuardReadable:
    def test_readable_file_discovered_without_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        _structlog_to_caplog: None,
    ) -> None:
        """A normal readable pipeline file is found and no WARNING is logged."""
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "pipeline.py").write_text(PIPELINE_CONTENT)

        with caplog.at_level(logging.WARNING):
            result = discover_pipelines(tmp_path)

        assert len(result) == 1
        assert result[0].name == "pipeline.py"
        # No WARNING should have been emitted for the happy path.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        # Allow unrelated watchfiles/uvicorn warnings; but none should mention
        # pipeline.py as a read failure.
        assert not _records_mentioning(caplog, "pipeline.py"), (
            f"Unexpected warning mentioning pipeline.py: {[r.getMessage() for r in warnings]}"
        )


# ---------------------------------------------------------------------------
# Unreadable files must log WARNING
# ---------------------------------------------------------------------------


class TestUnreadableFilesLogWarning:
    def test_permission_denied_glob_file_logs_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _structlog_to_caplog: None,
    ) -> None:
        """A PermissionError on an otherwise-globbed file must log WARNING.

        PermissionError is a subclass of OSError — the code path under test is
        the ``except OSError`` at discovery.py:78-81.  The warning must name
        the offending path and include the error reason so users can act on it.
        """
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "locked.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "good.py").write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "locked.py":
                raise PermissionError(13, "Permission denied", str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with caplog.at_level(logging.WARNING):
            result = discover_pipelines(tmp_path)

        # Result still contains the other readable file — fail loud, not silent,
        # but don't nuke the whole discovery.
        assert [p.name for p in result] == ["good.py"]

        # WARNING record must mention the path.
        matches = _records_mentioning(caplog, "locked.py")
        assert matches, (
            "Expected a WARNING log naming 'locked.py'. "
            f"Records captured: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        # And must mention the underlying reason so the user can diagnose.
        assert _records_mentioning(caplog, "Permission denied"), (
            "Expected the WARNING to include the OS error reason. "
            f"Records captured: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_oserror_read_failure_logs_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _structlog_to_caplog: None,
    ) -> None:
        """A generic OSError (e.g. corrupted device) must log WARNING."""
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "corrupt.py").write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "corrupt.py":
                raise OSError("I/O error reading from disk")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with caplog.at_level(logging.WARNING):
            result = discover_pipelines(tmp_path)

        assert result == []  # the only candidate was the corrupt file
        matches = _records_mentioning(caplog, "corrupt.py")
        assert matches, (
            f"Expected a WARNING log naming 'corrupt.py'. Records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        assert _records_mentioning(caplog, "I/O error reading from disk"), (
            f"Expected error reason in warning. Records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_unreadable_configured_pipeline_logs_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _structlog_to_caplog: None,
    ) -> None:
        """The configured-pipeline branch (discovery.py:63-66) also fails loud.

        The same silent-skip bug exists in the ``_configured_pipeline`` branch.
        When the toml-configured path is unreadable, we must still warn.
        """
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "target.py"\n')
        (tmp_path / "target.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "backup.py").write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "target.py":
                raise PermissionError(13, "Permission denied", str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with caplog.at_level(logging.WARNING):
            result = discover_pipelines(tmp_path)

        # The configured pipeline read failed, but the glob-fallback branch
        # will attempt to re-read target.py too and also fail.  Either way,
        # backup.py should be returned.
        assert any(p.name == "backup.py" for p in result)
        assert _records_mentioning(caplog, "target.py"), (
            f"Expected a WARNING naming the unreadable configured pipeline. "
            f"Records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Non-strict default keeps returning discovered files
# ---------------------------------------------------------------------------


class TestNonStrictPreservesOtherFiles:
    def test_unreadable_file_does_not_kill_discovery(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _structlog_to_caplog: None,
    ) -> None:
        """Unreadable files are excluded; the rest are still returned.

        This is the contract the CLI and server rely on: one broken file in a
        directory shouldn't hide every other pipeline.  Users see the broken
        one via the WARNING log.
        """
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "alpha.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "broken.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "gamma.py").write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "broken.py":
                raise OSError("device not ready")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with caplog.at_level(logging.WARNING):
            result = discover_pipelines(tmp_path)

        names = sorted(p.name for p in result)
        assert names == ["alpha.py", "gamma.py"]
        # The warning is how the user learns broken.py was skipped.
        assert _records_mentioning(caplog, "broken.py"), (
            f"Expected WARNING about broken.py. Records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Optional strict mode — if implemented, raises ConfigError
# ---------------------------------------------------------------------------


def _supports_strict() -> bool:
    """Return True if ``discover_pipelines`` accepts a ``strict`` kwarg.

    The spec lists strict mode as OPTIONAL.  We only assert the strict contract
    when the parameter has actually been added to the signature — otherwise
    the test skips.  This keeps the fail-loudly tests useful whether or not
    the developer chooses to implement the opt-in extension.
    """
    try:
        sig = inspect.signature(discover_pipelines)
    except (TypeError, ValueError):
        return False
    return "strict" in sig.parameters


class TestStrictMode:
    @pytest.mark.skipif(
        not _supports_strict(),
        reason="strict= kwarg not implemented (OPTIONAL per spec)",
    )
    def test_strict_raises_config_error_on_unreadable_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _structlog_to_caplog: None,
    ) -> None:
        """In strict mode, an unreadable file must raise ``ConfigError``.

        Non-strict (the default) warns and skips.  Strict mode is for CI
        pipelines and scripts where silent skipping could mask real bugs.
        """
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "locked.py").write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "locked.py":
                raise PermissionError(13, "Permission denied", str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with pytest.raises(ConfigError) as excinfo:
            discover_pipelines(tmp_path, strict=True)  # type: ignore[call-arg]
        # The error message / context must name the offending path so the
        # caller can surface it to the user.
        assert "locked.py" in str(excinfo.value)

