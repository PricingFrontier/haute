"""The process's own output: every character a command prints, whatever the locale."""

from __future__ import annotations

import codecs
import io
import os
import sys


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
