"""Structured logging configuration for Haute.

Logging convention (keep this split consistent across the codebase):

* Server / internal code uses **structlog** via :func:`get_logger`.
  Structured key-value events are machine-parseable, request-scoped, and
  configured centrally by :func:`configure_logging`.
* CLI user-facing output uses **click.echo** (and ``click.secho``) — and
  lives only under ``src/haute/cli/``.  Plain ``print(...)`` is banned
  anywhere in ``src/haute/``; the test suite enforces this via AST
  walks.

In short: if the caller is the server, a route handler, or any internal
module, import ``get_logger`` from this module; if the caller is the CLI
and the message is meant for a human reading the terminal, use
``click.echo``.

Dev mode (default):  colored console output, human-readable.
Prod mode (HAUTE_LOG_FORMAT=json):  JSON lines to stdout for log aggregators.

Usage::

    from haute._logging import get_logger

    logger = get_logger()
    logger.info("pipeline_executed", node_count=12, duration_ms=42.3)

Request-scoped context (bind a request_id per API call)::

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=rid)
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging() -> None:
    """Configure structlog + stdlib logging.

    Call once at startup (server lifespan).  Safe to call multiple times.

    Environment variables:
        HAUTE_LOG_FORMAT:  "json" for machine-readable output (default: console)
        HAUTE_LOG_LEVEL:   Python log level name (default: INFO)

    Implementation note — processors-list identity is stable:
        We mutate the existing ``structlog`` processors list in place rather
        than passing a fresh list to :func:`structlog.configure`.  This
        matters because ``cache_logger_on_first_use=True`` causes
        :class:`~structlog._config.BoundLoggerLazyProxy` instances to capture
        a reference to the processors list at first use; if a subsequent
        ``configure_logging`` call replaced that list, the cached bound
        loggers would still emit through the OLD list.

        :func:`structlog.testing.capture_logs` exploits the same invariant —
        it mutates the list in place — so preserving the list instance here
        keeps log capture working after any number of reconfigurations.
        Without this, tests that use ``capture_logs`` become order-dependent
        flakes when they follow any test that calls ``configure_logging``.
    """
    json_mode = os.environ.get("HAUTE_LOG_FORMAT", "").lower() == "json"
    log_level = os.environ.get("HAUTE_LOG_LEVEL", "INFO").upper()

    # Processors shared between structlog-native and stdlib foreign loggers
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_mode:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    new_processors: list[structlog.types.Processor] = [
        *shared,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    # Mutate the existing processors list in place so cached bound loggers
    # (held by any module that did ``logger = get_logger(...)`` at import
    # time) keep emitting through the current pipeline after reconfigure.
    # See the docstring above for the full rationale.
    current_processors = structlog.get_config().get("processors")
    if isinstance(current_processors, list):
        current_processors.clear()
        current_processors.extend(new_processors)
        structlog.configure(
            processors=current_processors,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    else:
        # First-time configuration (or tests that just called
        # reset_defaults) — no list to preserve.
        structlog.configure(
            processors=new_processors,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    # Bridge stdlib logging (uvicorn, watchfiles, etc.) through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Quieten noisy third-party loggers
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(**initial_ctx: object) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally pre-bound with context."""
    return structlog.get_logger(**initial_ctx)  # type: ignore[no-any-return]
