"""Tests for haute._logging — structured logging configuration."""

from __future__ import annotations

import logging
import sys
from unittest.mock import patch

import pytest
import structlog
from structlog._config import BoundLoggerLazyProxy

from haute._logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _clean_logging_state() -> None:
    """Reset root logger and structlog state before each test."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    structlog.reset_defaults()


# ---------------------------------------------------------------------------
# configure_logging — renderer selection
# ---------------------------------------------------------------------------


class TestRendererSelection:
    def test_default_uses_console_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        formatter = logging.getLogger().handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        renderer = formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)

    def test_json_mode_uses_json_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "json")
        configure_logging()
        formatter = logging.getLogger().handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
        renderer = formatter.processors[-1]
        assert isinstance(renderer, structlog.processors.JSONRenderer)

    def test_json_mode_case_insensitive_upper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "JSON")
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.processors.JSONRenderer)

    def test_json_mode_case_insensitive_mixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "Json")
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.processors.JSONRenderer)

    def test_empty_format_env_uses_console(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "")
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)

    def test_non_json_format_uses_console(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "text")
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)


# ---------------------------------------------------------------------------
# configure_logging — log level
# ---------------------------------------------------------------------------


class TestLogLevel:
    def test_default_level_is_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        assert logging.getLogger().level == logging.INFO

    @pytest.mark.parametrize(
        "level_name,level_const",
        [
            ("DEBUG", logging.DEBUG),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ],
    )
    def test_level_from_env(
        self, monkeypatch: pytest.MonkeyPatch, level_name: str, level_const: int
    ) -> None:
        monkeypatch.setenv("HAUTE_LOG_LEVEL", level_name)
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        assert logging.getLogger().level == level_const

    def test_level_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_LEVEL", "debug")
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    @pytest.mark.parametrize("bad_value", ["NONEXISTENT", ""])
    def test_invalid_level_falls_back_to_info(
        self, monkeypatch: pytest.MonkeyPatch, bad_value: str
    ) -> None:
        monkeypatch.setenv("HAUTE_LOG_LEVEL", bad_value)
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        assert logging.getLogger().level == logging.INFO


# ---------------------------------------------------------------------------
# configure_logging — third-party logger suppression
# ---------------------------------------------------------------------------


class TestThirdPartyLoggerSuppression:
    def test_watchfiles_set_to_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        assert logging.getLogger("watchfiles").level == logging.WARNING

    def test_uvicorn_access_set_to_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        assert logging.getLogger("uvicorn.access").level == logging.WARNING


# ---------------------------------------------------------------------------
# configure_logging — handler management
# ---------------------------------------------------------------------------


class TestHandlerManagement:
    def test_root_has_exactly_one_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        assert len(logging.getLogger().handlers) == 1

    def test_existing_handlers_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        root = logging.getLogger()
        # Record how many handlers exist before we add ours (pytest may have some)
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        pre_count = len(root.handlers)
        assert pre_count >= 2
        configure_logging()
        # After configure_logging, all prior handlers are cleared and exactly one remains
        assert len(root.handlers) == 1

    def test_handler_writes_to_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr

    def test_handler_uses_structlog_formatter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        handler = logging.getLogger().handlers[0]
        assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)


# ---------------------------------------------------------------------------
# configure_logging — idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_multiple_calls_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        configure_logging()
        configure_logging()
        assert len(logging.getLogger().handlers) == 1

    def test_multiple_calls_preserve_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_LEVEL", "DEBUG")
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG


# ---------------------------------------------------------------------------
# configure_logging — isatty detection
# ---------------------------------------------------------------------------


class TestIsattyDetection:
    def test_console_colors_when_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        with patch.object(sys.stderr, "isatty", return_value=True):
            configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)
        assert renderer._colors is True

    def test_console_no_colors_when_not_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        with patch.object(sys.stderr, "isatty", return_value=False):
            configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)
        assert renderer._colors is False


# ---------------------------------------------------------------------------
# get_logger — basic usage
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_bound_logger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger()
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_is_usable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger()
        # Should not raise
        logger.info("test_event")

    def test_logger_with_string_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(component="parser")
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_with_int_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(node_count=42)
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_with_none_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(optional_field=None)
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_with_dict_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(metadata={"key": "value"})
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_with_list_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(tags=["a", "b", "c"])
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_with_multiple_context_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.delenv("HAUTE_LOG_LEVEL", raising=False)
        configure_logging()
        logger = get_logger(
            component="executor",
            node_count=5,
            debug=None,
            extra={"nested": True},
        )
        assert isinstance(logger, BoundLoggerLazyProxy)

    def test_logger_without_configure_still_returns(self) -> None:
        # get_logger delegates to structlog.get_logger which works even
        # without configure_logging, just with structlog defaults
        logger = get_logger()
        assert logger is not None


# ---------------------------------------------------------------------------
# Integration — configure then log
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_configure_then_log_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "json")
        monkeypatch.setenv("HAUTE_LOG_LEVEL", "DEBUG")
        configure_logging()
        logger = get_logger(service="test")
        logger.info("hello", extra_key="val")
        # JSON output goes to stderr; just verify no exception was raised
        # and the logger chain works end-to-end

    def test_configure_then_log_console(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        monkeypatch.setenv("HAUTE_LOG_LEVEL", "DEBUG")
        configure_logging()
        logger = get_logger(service="test")
        logger.debug("debug_event", count=3)

    def test_configure_json_then_reconfigure_console(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAUTE_LOG_FORMAT", "json")
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.processors.JSONRenderer)

        monkeypatch.delenv("HAUTE_LOG_FORMAT", raising=False)
        configure_logging()
        renderer = logging.getLogger().handlers[0].formatter.processors[-1]
        assert isinstance(renderer, structlog.dev.ConsoleRenderer)
