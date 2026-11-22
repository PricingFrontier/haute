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
from collections.abc import Callable, Sequence
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


class RuntimePathWrittenOnWindowsError(RuntimePathOutsideProjectError):
    """A path with a drive letter read where it is not absolute: it names a folder on the
    Windows computer it was written on, and no file in this checkout."""


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


#: The characters Windows does not allow in a file or folder name, beside control characters.
_WINDOWS_FORBIDDEN_NAME_CHARACTERS = frozenset('<>:"|?*')


def reject_unportable_destination(
    display_path: str,
    target: Path,
    *,
    project_root: Path,
    staged_as: Callable[[Path], Sequence[Path]] | None = None,
) -> None:
    """Refuse a destination a person typed that a file cannot have, before anything is
    written, naming the path and the rule.

    The one rule of the saves that write where the analyst says (an optimiser result, a
    model, a Data Output), on every platform so a project stays loadable on Windows: a
    character Windows does not allow in a name, a name ending with a dot or a space, which
    Windows drops, a device name (:func:`_reject_reserved_device_components`), a folder of
    the path that is a file, and a name or path too long for the disk as the save writes it
    (*staged_as*, the sibling names its write makes: an atomic write's unless said;
    :func:`haute._file_ops.written_file_length_problem`). *display_path* is the path as
    typed, which Windows would have changed by resolving it; *target* its resolved file.
    """
    from haute._file_ops import atomic_write_names, written_file_length_problem

    text = _normalise_path_text(display_path)
    _reject_reserved_device_components(display_path, Path(text))
    typed = PureWindowsPath(text)
    for name in typed.parts[1:] if typed.anchor else typed.parts:
        if name in (".", ".."):
            continue
        refused = sorted(
            {c for c in name if c in _WINDOWS_FORBIDDEN_NAME_CHARACTERS or ord(c) < 32}
        )
        if refused:
            shown = " ".join(repr(c) for c in refused)
            raise MalformedRuntimePathError(
                f"Path {display_path!r} has {shown} in {name!r}: a file or folder name cannot "
                'hold < > : " | ? * or a control character on Windows, so it is refused on '
                "every platform. Choose another name."
            )
        if name.endswith((".", " ")):
            raise MalformedRuntimePathError(
                f"Path {display_path!r} has {name!r}, which ends with a dot or a space: "
                "Windows drops it, so the file would have another name. Choose another name."
            )
    root = project_root.resolve()
    for folder in target.parents:
        if not is_contained(root, folder) or folder == root:
            break
        if folder.exists() and not folder.is_dir():
            raise MalformedRuntimePathError(
                f"Path {display_path!r} puts the file in "
                f"{folder.relative_to(root).as_posix()!r}, which is a file, not a folder. "
                "Choose another folder."
            )
    too_long = written_file_length_problem([target], staged_as=staged_as or atomic_write_names)
    if too_long is not None:
        raise MalformedRuntimePathError(
            f"Path {display_path!r} cannot be saved: {too_long}. Choose a shorter name."
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


def reject_unportable_path_text(raw_path: str | Path, *, project_root: Path) -> None:
    """Refuse *raw_path* for what its text says on its own, before anything anchors or
    extends it: an embedded null byte, a reserved device name, and a drive letter where the
    path is not absolute. Every read path and every Data Output's path go through it."""
    _reject_reserved_device_components(raw_path, Path(_normalise_path_text(raw_path)))
    reject_windows_drive_path(raw_path, project_root=project_root)


def reject_windows_drive_path(raw_path: str | Path, *, project_root: Path) -> None:
    """Refuse *raw_path* when it starts with a drive letter and is not absolute here.

    On Linux and macOS ``C:\\Users\\...`` is a relative path, so read as written it
    would leave the project (an input) or land in a folder named ``C:`` inside it (an
    output). It names a folder on the Windows computer it was written on, which this
    checkout does not have, and the refusal says so. ``C:file.csv`` is relative to a
    drive's current folder on Windows too, so it is refused there as well.
    """
    normalised = _normalise_path_text(raw_path)
    windows_path = PureWindowsPath(normalised)
    if not windows_path.drive or Path(normalised).is_absolute():
        return
    found = _project_suffix(windows_path.parts[1:], project_root)
    remedy = (
        f"Write it from the project root: {found}."
        if found is not None
        else "Write it from the project root, as the editor's file browser does."
    )
    raise RuntimePathWrittenOnWindowsError(
        f"Path {str(raw_path)!r} was written on Windows: its drive letter names a folder "
        f"on that computer only, which this checkout of the project does not have. {remedy}"
    )


def _project_suffix(parts: tuple[str, ...], project_root: Path) -> str | None:
    """The longest tail of *parts* naming an entry under *project_root*, ``/``-separated."""
    if any(part in {".", ".."} for part in parts):
        return None
    for start in range(len(parts)):
        tail = parts[start:]
        if (project_root.joinpath(*tail)).exists():
            return "/".join(tail)
    return None


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
    reject_unportable_path_text(raw_path, project_root=root)

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


def _spelled_on_disk(project_root: Path, parts: tuple[str, ...]) -> tuple[str, ...]:
    """*parts*, a path under *project_root*, with each entry spelled as it is on disk.

    A part no entry of its folder matches exactly takes the one entry it matches in
    another letter case. The walk stops at a glob pattern, at a folder that cannot be
    listed, and at a part that matches no entry or more than one: the folders a writer
    would make, and the suggestion for an absolute path, are spelled as far as they exist.
    """
    spelled: list[str] = []
    folder = project_root
    for index, part in enumerate(parts):
        if any(marker in part for marker in _GLOB_MARKERS):
            return (*spelled, *parts[index:])
        try:
            entries = os.listdir(folder)
        except OSError:
            return (*spelled, *parts[index:])
        if part not in entries:
            matches = [entry for entry in entries if entry.casefold() == part.casefold()]
            if len(matches) != 1:
                return (*spelled, *parts[index:])
            part = matches[0]
        spelled.append(part)
        folder = folder / part
    return tuple(spelled)


def _inside(path: Path, project_root: Path) -> tuple[str, ...]:
    """*path*'s parts below *project_root*, which contains it once resolved."""
    try:
        return path.relative_to(project_root).parts
    except ValueError:
        return path.resolve().relative_to(project_root).parts


def _matches_on_disk(
    project_root: Path, parts: tuple[str, ...], *, fold: bool
) -> list[tuple[str, ...]]:
    """Every entry under *project_root* that *parts* names, each as its names on disk.

    Each part is compared with the entries of its folder exactly, or with letter case
    ignored (*fold*) as Windows and macOS compare it; a glob part (``*``, ``?``, ``[...]``)
    matches as a pattern and ``**`` any depth of folders, symbolic links not followed.
    Comparing the folders' listings, never asking the filesystem whether a spelling
    exists, gives the same answer on every system.
    """
    # Insertion-ordered and hashed: a pattern over a folder of many files stays linear.
    found: dict[tuple[str, ...], None] = {}

    def same(entry: str, part: str) -> bool:
        if fold:
            entry, part = entry.casefold(), part.casefold()
        if any(marker in part for marker in _GLOB_MARKERS):
            return fnmatch.fnmatchcase(entry, part)
        return entry == part

    def walk(folder: Path, index: int, names: tuple[str, ...]) -> None:
        if index == len(parts):
            found[names] = None
            return
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            return
        if parts[index] == "**":
            walk(folder, index + 1, names)
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    walk(folder / entry.name, index, (*names, entry.name))
            return
        for entry in entries:
            if same(entry.name, parts[index]):
                walk(folder / entry.name, index + 1, (*names, entry.name))

    walk(project_root, 0, ())
    return list(found)


def _letter_case_refusal(
    raw_path: str, own: tuple[str, ...], match: tuple[str, ...], *, found_as_written: bool
) -> str:
    """The refusal of *raw_path* (its own parts *own*), which names *match* only where
    letter case is ignored; *found_as_written* when it also names something as written."""
    pattern = any(marker in part for part in own for marker in _GLOB_MARKERS)
    if found_as_written:
        return (
            f"Path {raw_path!r} also matches {'/'.join(match)!r} where letter case is ignored, "
            "as Windows and macOS read it, and not on Linux, so the systems read different "
            "files. Rename the files apart, or write the path to match one spelling."
        )
    if pattern:
        return (
            f"Path {raw_path!r} matches {'/'.join(match)!r} only where letter case is ignored, "
            "as Windows and macOS read it, so a checkout on Linux finds nothing. Write the "
            "pattern in the letter case of the files it reads."
        )
    tail = len(own) <= len(match) and not any(part in {".", ".."} for part in own)
    spelled = "/".join(match[len(match) - len(own) :] if tail else match)
    return (
        f"Path {raw_path!r} is spelled {spelled!r} on disk. Windows and macOS read it in "
        "either letter case and Linux only as it is spelled, so a checkout on Linux cannot "
        f"find it. Write {spelled}."
    )


def portable_path_refusal(
    raw_path: str,
    *,
    source_file: str | Path | None = None,
    pipeline_dir: str | Path | None = None,
    project_root: str | Path | None = None,
) -> str | None:
    """Why *raw_path*, a path a node reads, would not name the same file in another
    checkout of the project, on another computer or system; ``None`` when it would.

    A path that leaves the project, an absolute one, one with a Windows drive letter and
    one that names something only where letter case is ignored are each refused, saying
    what to write: the path from the project root, or the spelling on disk. Windows and
    macOS read a file in any letter case and Linux only in its own. The anchoring rule's
    candidates are taken in its order: one that names an entry only with letter case
    ignored is refused, and the first that names one as written is the file every system
    reads. A glob pattern is judged by what it matches. The save and ``haute lint`` ask
    this of every path a node reads (:func:`portable_output_path_refusal` for one it writes).
    """
    root = current_project().root if project_root is None else Path(project_root).resolve()
    try:
        candidates = _anchored_candidates(
            raw_path, source_file=source_file, pipeline_dir=pipeline_dir, project_root=root
        )
    except RuntimePathWrittenOnWindowsError as exc:
        return str(exc)
    except RuntimePathOutsideProjectError as exc:
        return f"{exc}. Copy the file into the project and write its path from the project root."
    except RuntimePathError as exc:
        return str(exc)
    written = Path(_normalise_path_text(raw_path))
    if written.is_absolute():
        found = next((candidate for candidate in candidates if _exists_or_matches(candidate)), None)
        inside = "/".join(_spelled_on_disk(root, _inside(found or candidates[0], root)))
        return (
            f"Path {raw_path!r} is absolute: it names folders on this computer, which another "
            f"checkout of the project does not have. Write it from the project root: {inside}."
        )
    for candidate in candidates:
        parts = _inside(candidate, root)
        exact = set(_matches_on_disk(root, parts, fold=False))
        extra = [match for match in _matches_on_disk(root, parts, fold=True) if match not in exact]
        if extra:
            return _letter_case_refusal(
                raw_path, written.parts, extra[0], found_as_written=bool(exact)
            )
        if exact:
            return None
    return None


def portable_output_path_refusal(
    raw_path: str, written: str, *, project_root: str | Path
) -> str | None:
    """Why a node writing *written* (its setting *raw_path* as the writer reads it, from
    the project root) would write another file in another checkout; ``None`` when not.

    The setting's own text is held to :func:`reject_unportable_path_text` first; a path
    outside the project and an absolute one are refused as a read path is, and so is one
    whose folders, or the file itself, exist on disk in another letter case: Windows and
    macOS write into them, and Linux makes new ones.
    """
    root = Path(project_root).resolve()
    shown = repr(raw_path) if raw_path == written else f"{raw_path!r}, which writes {written!r},"
    try:
        # The setting as written: ``C:priced.csv`` loses its drive once put in ``outputs/``.
        reject_unportable_path_text(raw_path, project_root=root)
    except RuntimePathError as exc:
        return str(exc)
    normalised = Path(_normalise_path_text(written))
    target = Path(os.path.abspath(normalised if normalised.is_absolute() else root / normalised))
    if not is_contained(root, target.resolve()):
        return (
            f"Path {shown} resolves outside the project root. Write a path inside the "
            "project, from its root."
        )
    parts = _inside(target, root)
    spelled = "/".join(_spelled_on_disk(root, parts))
    if normalised.is_absolute():
        return (
            f"Path {shown} is absolute: it names folders on this computer, which another "
            f"checkout of the project does not have. Write it from the project root: {spelled}."
        )
    if spelled != "/".join(parts):
        return (
            f"Path {shown} is spelled {spelled!r} on disk. Windows and macOS write into it in "
            "either letter case and Linux makes another, so a checkout on Linux writes "
            f"elsewhere. Write {spelled}."
        )
    return None


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
