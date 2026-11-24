"""The process's own output: every character a command prints, whatever the locale, and a
reader that stops reading (``| head``) answered as no failure of the command."""

from __future__ import annotations

import codecs
import errno
import io
import os
import sys
from typing import NoReturn


def utf8_output() -> None:
    """Let the process's stdout and stderr write every character a command prints.

    Python writes a stream that is not a console in the locale's encoding, which on
    Windows is the ANSI code page: it has no ✓ or ✗, so a command piped or redirected to a
    file raised ``UnicodeEncodeError`` at its first one. A redirected stream is written in
    UTF-8 instead, unless ``PYTHONIOENCODING`` chose its encoding; that one, and a terminal
    of another encoding, keep their encoding and print what they cannot hold as ``?``. A
    stream already UTF-8, and one a caller put in the process's place (a test runner's),
    are left alone.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None or stream is not getattr(sys, f"__{name}__"):
            continue
        if not isinstance(stream, io.TextIOWrapper):
            continue
        if codecs.lookup(stream.encoding or "ascii").name == "utf-8":
            continue
        if stream.isatty() or os.environ.get("PYTHONIOENCODING"):
            stream.reconfigure(errors="replace")
        else:
            stream.reconfigure(encoding="utf-8")


class OutputClosed(BaseException):  # a signal of the reader, as KeyboardInterrupt is
    """The reader of the process's standard output or error stopped reading: a pipe into
    ``head`` that took its lines, a pager quit early.

    Nothing is wrong with what the command was doing, so it ends quietly (the ``haute``
    group). A ``BaseException``, as ``KeyboardInterrupt`` is, and no ``OSError``: no handler
    of a command's own failures, which take ``OSError`` among ``EXPECTED_FAILURES``, can call
    it a parse error or a failed run.
    """


def _reader_gone(failure: OSError) -> bool:
    """Whether a write failed because the stream's reader has gone: a broken pipe, which
    Windows reports as an invalid argument for a pipe whose reader closed."""
    return isinstance(failure, BrokenPipeError) or (
        sys.platform == "win32" and failure.errno == errno.EINVAL
    )


class _ReaderGuard(io.TextIOWrapper):
    """The process's own output stream, a pipe or a file, which raises :class:`OutputClosed`
    when its reader has gone, and any other failure as the ``OSError`` it is.

    Once the reader has gone the stream's descriptor is pointed at the null device, so what
    is still buffered, and anything written after (another thread's log line, the
    interpreter's last flush at exit), is dropped without failing again: no "Exception
    ignored" notice and no exit 120.
    """

    def write(self, text: str) -> int:
        try:
            return super().write(text)
        except OSError as failure:
            self._closed_by(failure)

    def flush(self) -> None:
        try:
            super().flush()
        except OSError as failure:
            self._closed_by(failure)

    def _closed_by(self, failure: OSError) -> NoReturn:
        if not _reader_gone(failure):
            raise failure
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, self.fileno())
            finally:
                os.close(null)
        except (OSError, io.UnsupportedOperation):
            # A stream with no descriptor of its own (a test's): nothing to point elsewhere.
            pass
        raise OutputClosed from failure


def guard_closed_readers() -> None:
    """Put a :class:`_ReaderGuard` in place of the process's own stdout and stderr where they
    are not a terminal, keeping their encoding, errors and buffering, so every write to
    them, a command's ``click.echo`` and a log line alike, answers a reader that stopped
    reading in one way (CLI-11). A terminal's reader does not go, and a stream a caller put
    in the process's place (a test runner's) is left alone. Run after :func:`utf8_output`.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None or stream is not getattr(sys, f"__{name}__"):
            continue
        if not isinstance(stream, io.TextIOWrapper) or stream.isatty():
            continue
        stream.flush()
        guarded = _ReaderGuard(
            stream.buffer,
            encoding=stream.encoding,
            errors=stream.errors,
            # As Python opens them: "\n" becomes "\r\n" on Windows alone.
            newline=None if sys.platform == "win32" else "\n",
            line_buffering=stream.line_buffering,
            write_through=stream.write_through,
        )
        setattr(sys, name, guarded)
