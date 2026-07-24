"""Case-ambiguity audit for user-facing runtime input paths.

Haute pins **no Unicode/case normalization** on user-supplied data paths
(dataInput / apiInput / externalFile ``path`` config): the string in the
config is the string handed to the filesystem.  That makes the checkout
portable only as long as the spelling in the config and the spelling on
disk agree *under every platform's identity relation*.  Two ways they can
silently disagree:

- On a case-SENSITIVE filesystem (Linux) two entries differing only in
  case (``Foo.csv`` / ``foo.csv``) can coexist; picked up on macOS or
  Windows the pair collapses to one file and the config's reference is
  ambiguous.
- On a case-INSENSITIVE filesystem a config path spelled differently from
  the on-disk entry (``foo.csv`` vs on-disk ``Foo.csv``) opens fine — and
  breaks the moment the checkout lands on Linux.

Neither is haute's to *fix* (no-normalization is the pinned contract, see
``notes-haute/common/INVARIANTS.md`` §Invariant 1), but both are worth a
loud warning at access time.  This module provides that audit as a
standalone check, plus a generic function wrapper so any path-consuming
callable can opt in.  The runtime chokepoint every standard input funnels
through (:func:`haute._builders._resolve_runtime_data_path`) calls
:func:`warn_if_case_ambiguous` on each resolved path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from haute._logging import get_logger

logger = get_logger(component="path_case_audit")

_F = TypeVar("_F", bound=Callable[..., Any])

# Paths already warned about in this process.  The audit runs on every node
# execution, so an interactive preview loop would otherwise repeat the same
# warning ad infinitum.  Only positive findings are cached: a clean path is
# re-scanned each time so a twin introduced later is still caught.
_warned: set[str] = set()


def case_equivalent_siblings(
    path: str | Path,
    *,
    stop: str | Path | None = None,
) -> dict[str, list[str]]:
    """Map path segments of *path* to their case-equivalent siblings on disk.

    For the file itself and each ancestral directory, list the parent and
    collect entries whose ``casefold()`` matches the segment while the
    spelling differs.  Both hazard directions surface this way: coexisting
    case-twins on a case-sensitive filesystem, and a requested spelling that
    differs from the single on-disk entry on a case-insensitive one.

    When *stop* is given and *path* sits under it, ancestors are walked up
    to (and excluding) *stop* — the portability concern is spellings inside
    the project checkout, and listing directories above it (home, ``/``)
    would be wasted work.  Otherwise only the final segment is checked.

    Returns ``{ambiguous_path: [equivalent sibling spellings]}``; empty when
    unambiguous.  Unreadable directories are skipped, never raised.
    """
    resolved = Path(os.path.abspath(str(path)))
    stop_resolved = Path(os.path.abspath(str(stop))) if stop is not None else None

    segments: list[tuple[Path, str]] = []
    if stop_resolved is not None and resolved.is_relative_to(stop_resolved):
        node = resolved
        while node != stop_resolved:
            segments.append((node.parent, node.name))
            node = node.parent
    else:
        segments.append((resolved.parent, resolved.name))

    ambiguous: dict[str, list[str]] = {}
    for parent, name in segments:
        try:
            entries = os.listdir(parent)
        except OSError:
            continue
        folded = name.casefold()
        twins = sorted(e for e in entries if e.casefold() == folded and e != name)
        if twins:
            ambiguous[str(parent / name)] = twins
    return ambiguous


def warn_if_case_ambiguous(
    path: str | Path,
    *,
    stop: str | Path | None = None,
) -> dict[str, list[str]]:
    """Run the case audit on *path* and log a warning on any finding.

    Returns the (possibly empty) finding map so callers can act on it too.
    Findings are logged once per path per process.
    """
    key = str(path)
    if key in _warned:
        return {}
    found = case_equivalent_siblings(path, stop=stop)
    if found:
        _warned.add(key)
        logger.warning(
            "input_path_case_ambiguous",
            path=key,
            equivalents=found,
            detail=(
                "This path has case-equivalent sibling spellings on disk. "
                "It resolves here, but on a filesystem with the other case "
                "sensitivity (macOS/Windows vs Linux) the same checkout will "
                "read a different file or fail to resolve. Align the config "
                "spelling with the on-disk entry, or rename the twins apart."
            ),
        )
    return found


def wrap_path_case_audit(
    func: _F,
    index: int | str,
    *,
    stop: str | Path | None = None,
) -> _F:
    """Wrap *func* so the argument at *index* is case-audited on every call.

    *index* selects the path argument: an ``int`` for a positional, a
    ``str`` for a keyword.  The audit is advisory — a missing argument or a
    finding never changes the call, it only warns.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        target: Any = None
        if isinstance(index, str):
            target = kwargs.get(index)
        elif 0 <= index < len(args):
            target = args[index]
        if target is not None:
            warn_if_case_ambiguous(target, stop=stop)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
