"""How ``haute init`` writes a project's files: LF line endings, kept by ``.gitattributes``.

Text-mode writes give CRLF on Windows, and a checkout with
``core.autocrlf=true`` turns LF back into CRLF, which breaks a shell hook. So
every scaffold file is written as UTF-8 bytes with LF endings (READ-03), and
the project's ``.gitattributes`` pins LF for the formats Haute writes, in every
checkout. CSV files are left out: analysts save parameter files from other
tools. ``uv.lock`` is marked generated, so GitHub collapses it in a pull
request.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["GITATTRIBUTES_ENTRIES", "ensure_gitattributes", "lf_bytes", "write_lf"]

GITATTRIBUTES_ENTRIES: tuple[str, ...] = (
    "*.py text eol=lf",
    "*.json text eol=lf",
    "*.toml text eol=lf",
    "*.yml text eol=lf",
    "*.md text eol=lf",
    ".githooks/pre-commit text eol=lf",
    "uv.lock linguist-generated=true",
)


def lf_bytes(text: str) -> bytes:
    """*text* as the UTF-8 bytes Haute writes: LF line endings only."""
    return text.replace("\r\n", "\n").encode("utf-8")


def write_lf(path: Path, text: str) -> None:
    """Write *text* to *path* as UTF-8 with LF line endings, on every platform."""
    path.write_bytes(lf_bytes(text))


def ensure_gitattributes(project_dir: Path) -> list[str]:
    """Ensure every entry is in ``project_dir/.gitattributes``; return those added.

    A team's own file keeps its lines: the missing entries are appended under a
    ``# Haute`` heading, and a file that holds them all is left byte-identical.
    """
    path = project_dir / ".gitattributes"
    if not path.exists():
        write_lf(path, "\n".join(GITATTRIBUTES_ENTRIES) + "\n")
        return list(GITATTRIBUTES_ENTRIES)
    text = path.read_bytes().decode("utf-8", errors="replace")
    existing = {line.strip() for line in text.splitlines()}
    missing = [entry for entry in GITATTRIBUTES_ENTRIES if entry not in existing]
    if missing:
        separator = "" if text.endswith("\n") or not text else "\n"
        write_lf(path, text + separator + "\n# Haute\n" + "\n".join(missing) + "\n")
    return missing
