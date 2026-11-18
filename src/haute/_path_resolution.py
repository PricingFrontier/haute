"""Which file a relative path in a node's settings names: the one anchoring rule.

The editor, a run, an explicit snapshot build and every deploy reader resolve a
node's path through :func:`resolve_node_input_path`, so a path names one file in
the preview and in the deployed bundle. The rule takes no preference; outside
this module only the places ``tests/test_path_anchoring_by_hand.py`` lists, each
with its reason, anchor a relative path on the pipeline directory themselves.
"""

from __future__ import annotations

import fnmatch
import glob
import os
from pathlib import Path, PureWindowsPath

from haute._project_context import current_project
from haute._sandbox import is_contained
from haute._validation_error import HauteValidationError

_GLOB_MARKERS = "*?["
#: How much of each copy a comparison holds in memory at once.
_COMPARE_CHUNK_BYTES = 1 << 20


class RuntimePathError(HauteValidationError):
    """Base class for user-facing runtime path validation failures."""


class MalformedRuntimePathError(RuntimePathError):
    """Raised when a path cannot be parsed safely."""


class RuntimePathOutsideProjectError(RuntimePathError):
    """Raised when a runtime path resolves outside its execution root."""


def _normalise_path_text(path: str | Path) -> str:  # pragma: no mutate
    """Normalise path separators before constructing a ``Path``.

    Config files may contain Windows backslashes.  Forward slashes are
    accepted on Windows, Linux, and macOS, so normalising early keeps cache
    keys and existence checks consistent across operating systems.
    """
    text = str(path)
    if "\x00" in text:
        raise MalformedRuntimePathError("Path contains an embedded null byte")
    return text.replace("\\", "/")


def _reject_reserved_device_components(raw_path: str | Path, path: Path) -> None:
    """Reject DOS device names in a runtime path, on every platform.

    Windows resolves a component such as ``NUL`` or ``CON.csv`` to the device
    (``\\\\.\\NUL``) rather than to a file inside its directory, so the path
    escapes its root and the failure surfaces as a misleading "outside the
    project root". On Linux and macOS the same path is an ordinary file, so
    without this check one configured path resolves differently depending on
    who runs it.

    Rejecting everywhere mirrors the save-time guards in
    ``routes/_save_pipeline.py`` and ``routes/submodel.py``, which share this
    predicate for the same portability reason: a pipeline authored on one
    platform must stay loadable on another.
    """

    from haute._config_io import is_windows_reserved_filename

    for component in path.parts:
        if is_windows_reserved_filename(component):
            raise MalformedRuntimePathError(
                f"Path {raw_path!r} contains the reserved device name {component!r}. "
                "CON, PRN, AUX, NUL, COM1-COM9 and LPT1-LPT9 (any casing, any "
                "extension) cannot name a file on Windows, so they are rejected on "
                "every platform to keep a project portable."
            )


def _resolve_source_file_parent(  # pragma: no mutate
    source_file: str | Path | None,  # pragma: no mutate
    project_root: Path,  # pragma: no mutate
) -> Path | None:  # pragma: no mutate
    if not source_file:
        return None
    source = Path(_normalise_path_text(source_file))
    if not source.is_absolute():
        source = project_root / source
    return source.resolve().parent


def _contained_candidate(candidate: Path, project_root: Path) -> Path | None:
    """*candidate* made absolute with its spelling kept, or ``None`` when it leaves the root."""
    spelling_preserved = Path(os.path.abspath(candidate))
    if not is_contained(project_root, spelling_preserved.resolve()):
        return None
    return spelling_preserved


def _anchored_candidates(
    raw_path: str | Path,
    *,  # pragma: no mutate
    source_file: str | Path | None,  # pragma: no mutate
    pipeline_dir: str | Path | None,  # pragma: no mutate
    project_root: str | Path | None,  # pragma: no mutate
) -> list[Path]:
    """What *raw_path* can name, in the order the anchoring rule tries them.

    An absolute path is its one candidate. A relative one names the file under the
    project root, then the one under the pipeline directory (given, or the parent of
    *source_file*), when that is another place. A candidate that leaves the project root
    is dropped, and a path with none inside it raises
    :class:`RuntimePathOutsideProjectError`.
    """
    root = current_project().root if project_root is None else Path(project_root).resolve()
    normalised_path = _normalise_path_text(raw_path)
    raw = Path(normalised_path)
    _reject_reserved_device_components(raw_path, raw)
    windows_path = PureWindowsPath(normalised_path)
    if windows_path.drive and not raw.is_absolute():
        raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")

    if raw.is_absolute():
        anchored = [raw]
    else:
        pdir = (
            Path(pipeline_dir).resolve()
            if pipeline_dir is not None
            else _resolve_source_file_parent(source_file, root)
        )
        anchored = [anchor / raw for anchor in (root, pdir) if anchor is not None]
    candidates: list[Path] = []
    for path in anchored:
        candidate = _contained_candidate(path, root)
        if candidate is not None and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")
    return candidates


def resolve_node_input_path(
    raw_path: str | Path,
    *,
    source_file: str | Path | None = None,
    pipeline_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> Path:
    """Resolve a local path a node's config reads from: the one anchoring rule.

    A relative path names the file under the project root, where the editor's
    file browser writes paths from, and otherwise the one under the pipeline
    directory, a glob pattern counting as found where it matches. When both
    exist the project root's wins, so the registry, the engine, an explicit
    snapshot build and every deploy reader name the same file, and the bundle
    ships the file the preview read. Windows backslashes read as separators;
    neither candidate may leave the project root (the current project's unless
    *project_root* is given), a path found in neither place keeps the
    project-root candidate, and one with no candidate inside the root raises
    :class:`RuntimePathOutsideProjectError`.
    """
    candidates = _anchored_candidates(
        raw_path, source_file=source_file, pipeline_dir=pipeline_dir, project_root=project_root
    )
    return next((found for found in candidates if _exists_or_matches(found)), candidates[0])


def node_input_path_copies(
    raw_path: str | Path,
    *,
    source_file: str | Path | None = None,
    pipeline_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> tuple[Path, Path] | None:
    """The project root's and the pipeline directory's copy of a relative *raw_path*.

    Both are returned when the path names an existing file, folder or matching glob
    pattern in both places, the project root's first: the one
    :func:`resolve_node_input_path` names. ``None`` for an absolute path, a path found
    in at most one place, or two candidates that are one entry (the pipeline directory
    is the project root, or a link leads one to the other).
    """
    candidates = _anchored_candidates(
        raw_path, source_file=source_file, pipeline_dir=pipeline_dir, project_root=project_root
    )
    if len(candidates) != 2 or not all(_exists_or_matches(found) for found in candidates):
        return None
    project_copy, pipeline_copy = candidates
    if _real_entry(project_copy) == _real_entry(pipeline_copy):
        return None
    return project_copy, pipeline_copy


def copies_differ(first: Path, second: Path, *, project_root: str | Path | None = None) -> bool:
    """Whether two copies of one relative path hold different data.

    A copy is a file, a folder or a glob pattern; a folder or a pattern stands for the
    files under it or matched, followed through links, keyed by their path below the
    copy's fixed part (the pattern's directory). Every one of them is held inside the
    project root (the current project's unless *project_root* is given) before any is
    opened, and one that leads out raises :class:`RuntimePathOutsideProjectError`. The
    copies are the same when they name the same keys and each pair is one file or holds
    the same bytes, read afresh every time: a file rewritten with its size and time kept
    still compares by its content.
    """
    root = current_project().root if project_root is None else Path(project_root).resolve()
    first_files = _copy_files(first, root)
    second_files = _copy_files(second, root)
    if first_files.keys() != second_files.keys():
        return True
    return not all(_same_bytes(path, second_files[key]) for key, path in first_files.items())


def _same_bytes(first: Path, second: Path) -> bool:
    """Whether two files hold the same bytes, read in chunks with nothing cached."""
    if os.path.samefile(first, second):
        return True
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as one, second.open("rb") as other:
        while True:
            chunk = one.read(_COMPARE_CHUNK_BYTES)
            if chunk != other.read(_COMPARE_CHUNK_BYTES):
                return False
            if not chunk:
                return True


def _fixed_part(copy: Path) -> Path:
    """The part of *copy* no glob marker can widen: all of an existing entry, else the
    components before the first holding a marker, as :func:`_exists_or_matches` reads it."""
    if copy.exists():
        return copy
    for index, part in enumerate(copy.parts):
        if any(marker in part for marker in _GLOB_MARKERS):
            return Path(*copy.parts[:index])
    return copy


def _real_entry(copy: Path) -> Path:
    """*copy* with links in its fixed part followed, so two spellings of one entry compare."""
    fixed = _fixed_part(copy)
    return Path(os.path.realpath(fixed)) / copy.relative_to(fixed)


def _copy_files(copy: Path, project_root: Path) -> dict[str, Path]:
    """Every file *copy* stands for, keyed by its posix path below the copy's fixed part.

    The walk starts at the fixed part and follows links. A glob's remaining components
    are matched as the walk goes (``**`` for any number of folders), so it enters only a
    folder a match could lie in, and a matched folder stands for every file under it.
    Every entry it uses is held inside *project_root* before it is listed or read, and a
    link back to a folder above it (a cycle) is not entered again; an alias of a folder
    elsewhere is, so its files keep their own paths.
    """
    base = _fixed_part(copy)
    pattern = copy.relative_to(base).parts
    files: dict[str, Path] = {}
    pending: list[tuple[Path, tuple[str, ...], frozenset[str], bool]] = [
        (base, (), frozenset(), not pattern)
    ]
    while pending:
        entry, parts, above, named = pending.pop()
        named = named or (bool(parts) and _pattern_matches(parts, pattern))
        real = os.path.realpath(entry)
        is_dir = os.path.isdir(real)
        if not named and not (is_dir and _pattern_continues(parts, pattern)):
            continue
        if not is_contained(project_root, Path(real)):
            raise RuntimePathOutsideProjectError(
                f"{entry} leads outside the project root, to {real}"
            )
        if is_dir:
            if real not in above:
                pending.extend(
                    (child, (*parts, child.name), above | {real}, named)
                    for child in entry.iterdir()
                )
        elif named and os.path.isfile(real):
            files["/".join(parts) or "."] = entry
    return files


def _pattern_matches(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Whether the path *parts* matches the glob *pattern*, ``**`` matching any folders."""
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_pattern_matches(parts[index:], rest) for index in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatch(parts[0], head) and _pattern_matches(parts[1:], rest)


def _pattern_continues(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Whether a path below the folder *parts* could match the glob *pattern*."""
    if not parts:
        return bool(pattern)
    if not pattern:
        return False
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return True
    return fnmatch.fnmatch(parts[0], head) and _pattern_continues(parts[1:], rest)


def _exists_or_matches(candidate: Path) -> bool:
    """Whether *candidate* exists, or is a glob pattern matching at least one path."""
    if candidate.exists():
        return True
    text = str(candidate)
    if not any(marker in text for marker in _GLOB_MARKERS):
        return False
    return next(glob.iglob(text, recursive=True, include_hidden=True), None) is not None


def resolve_graph_source_path(raw_path: str, source_file: str | Path) -> str:
    """A node config's relative *raw_path*, resolved by the one rule, made absolute.

    Deploy's schema inference and scoring read a graph's paths through here: the
    project root's file first, then the one beside the pipeline file, as the editor
    reads it (:func:`resolve_node_input_path`), within the current project's root
    (deploy's validated one).
    """
    return str(
        resolve_node_input_path(
            raw_path, source_file=source_file, pipeline_dir=Path(source_file).parent
        ).resolve()
    )
