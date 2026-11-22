"""Atomic file write primitives and Writer context manager.

Implements Foundation tasks F2 (``atomic_write_bytes`` /
``atomic_write_text``) and F6 (``Writer`` with self-write callback).

Every atomic write goes through :func:`atomic_path`: the payload is staged
to a unique sibling ``.tmp`` file in the same directory as the target (so
``Path.replace`` is a same-filesystem rename), then renamed onto the target.
On any failure the temp file is unlinked and the original is left intact.
``atomic_write_bytes`` and ``haute._polars_utils.atomic_write`` are built on
it.

Cross-OS guarantee (be precise — these differ):

* A reader NEVER observes torn/partial bytes on any OS. It always sees
  either the complete previous payload or the complete new one, because
  the new bytes only become visible at the instant of the rename.
* On POSIX, ``rename(2)`` is atomic even under concurrent readers, so
  the replace also always *succeeds*.
* On Windows, ``Path.replace`` → ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)``
  is NOT robust under reader contention. If another process/thread holds
  the target open without ``FILE_SHARE_DELETE`` — which is the default
  for Python ``open()`` / ``read_bytes`` / ``read_text`` — the replace
  fails with ``PermissionError`` (ERROR_ACCESS_DENIED) or an
  ERROR_SHARING_VIOLATION. Those two errors receive a short bounded retry
  of the same atomic replace, accommodating transient antivirus/indexer
  handles without introducing a non-atomic fallback. An exhausted retry
  still fails loudly and leaves the old complete payload intact.

Temp filenames embed a random uuid4 fragment so that concurrent writers
to the same target never collide on the staging file. The committed
target is still last-rename-wins (one full payload).

The parent directory is never silently created — callers must ensure
the target directory exists. Failing loudly is preferable to a silent
mkdir that masks configuration bugs.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

from haute._file_lock import FileLock, remove_unheld_lock_file

_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1)
_WINDOWS_DIRECTORY_RENAME_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0)
_IS_WINDOWS = os.name == "nt"
_DISK_HEADROOM_BYTES = 64 * 1024

# Windows' traditional limit on a path: MAX_PATH (260) less the terminating NUL, and for a
# folder 12 fewer, so a folder can still hold an 8.3 file name. A longer path needs the
# "Enable Win32 long paths" setting, which is off by default.
WINDOWS_MAX_PATH_CHARS = 259
_WINDOWS_MAX_FOLDER_PATH_CHARS = 247
_ERROR_FILENAME_EXCED_RANGE = 206
# The extended-length and device prefixes, whose paths MAX_PATH does not limit.
_UNLIMITED_PATH_PREFIXES = ("\\\\?\\", "\\\\.\\")
_LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"

#: What to do about a path Windows refused for its length, in words that name no path.
PATH_TOO_LONG_REMEDIATION = (
    "Windows refused a path in this project's cache for its length: the project's folder "
    "path is too long while Windows' long paths are disabled. Move the project to a "
    "folder with a shorter path, or enable long paths (the 'Enable Win32 long paths' "
    "policy), and try again."
)


class PathTooLongError(OSError):
    """A file operation failed because Windows refused its path's length.

    Still an ``OSError``, so every handler that catches one keeps doing so; it names the
    cause and the remedies instead of the bare "file not found" Windows reports.
    """

    def __init__(self, path: str) -> None:
        super().__init__(
            errno.ENAMETOOLONG,
            f"Windows refused a path of {len(path)} characters for its length: unless long "
            f"paths are enabled, a file's path may have at most {WINDOWS_MAX_PATH_CHARS} "
            f"characters and a folder's {_WINDOWS_MAX_FOLDER_PATH_CHARS}. Move the project to a "
            "folder with a shorter path, or enable long paths (the 'Enable Win32 long paths' "
            "policy)",
            path,
        )


def windows_long_paths_enabled() -> bool:
    """Whether this Windows machine accepts paths past ``MAX_PATH`` (``LongPathsEnabled``)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LONG_PATHS_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return bool(value == 1)


#: The longest file name the filesystems Haute writes to hold (NTFS, ext4, APFS); a name
#: Haute makes from a node's is ASCII, so its characters are its bytes.
MAX_FILE_NAME_CHARS = 255


def literal_path(path: Path) -> Path:
    """*path*, absolute, in the form the operating system takes literally.

    On Windows the extended-length form (``\\\\?\\C:\\...``, ``\\\\?\\UNC\\server\\...``),
    which neither drops a name's trailing dot or space nor reads a device name in it: the
    one way to reach a file that a program outside Haute gave such a name. Elsewhere the
    path as it is.
    """
    text = str(path)
    if not _IS_WINDOWS or text.startswith(_UNLIMITED_PATH_PREFIXES):
        return path
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def atomic_write_names(target: Path) -> list[Path]:
    """The sibling names an atomic write of *target* makes before its rename."""
    return [_temp_path_for(target)]


def atomic_copy_names(target: Path) -> list[Path]:
    """The sibling names :func:`atomic_copy_files` makes for *target*: its staged copy and
    the backup of the file it replaces."""
    return [_temp_path_for(target), _backup_path_for(target)]


def name_units(name: str) -> int:
    """*name*'s length as the filesystem counts it: UTF-16 code units on Windows (NTFS),
    UTF-8 bytes elsewhere (ext4, APFS)."""
    return len(name.encode("utf-16-le")) // 2 if _IS_WINDOWS else len(name.encode("utf-8"))


def name_unit_word() -> str:
    return "characters" if _IS_WINDOWS else "bytes"


def written_file_length_problem(
    targets: Iterable[Path], *, staged_as: Callable[[Path], Sequence[Path]] = atomic_write_names
) -> str | None:
    """Why files written at *targets* cannot be held for their length, or ``None``.

    Counts every name of each path as the filesystem does (:func:`name_units`): each
    folder's, and the file's as a write of it is made, which stages it under longer
    sibling names first (*staged_as*: an atomic write's, :func:`_temp_path_for`, unless
    the caller writes another way), so the limit a target's own name must meet is the
    platform's less the longest difference. A name may be at most ``MAX_FILE_NAME_CHARS``
    long everywhere; on Windows without long paths, a path at most
    ``WINDOWS_MAX_PATH_CHARS``. The words name the first name or path past a limit and
    that limit, for a caller to say which name made it.
    """
    staging_extra = {
        target: max([0, *(name_units(s.name) - name_units(target.name) for s in staged_as(target))])
        for target in targets
    }
    units = name_unit_word()
    # A name too long is too long everywhere, so it is told before a path.
    for target, extra in staging_extra.items():
        folders = target.parent.parts[1:] if target.anchor else target.parent.parts
        for folder in folders:
            if name_units(folder) > MAX_FILE_NAME_CHARS:
                shown = folder if len(folder) <= 40 else folder[:40] + "…"
                return (
                    f"its folder {shown!r} would be {name_units(folder)} {units} "
                    f"long, more than the {MAX_FILE_NAME_CHARS} a folder's name may be"
                )
        name_limit = MAX_FILE_NAME_CHARS - extra
        if name_units(target.name) > name_limit:
            return (
                f"its file name would be {name_units(target.name)} {units} long, more than "
                f"the {name_limit} a saved file's name may be"
            )
    if not _IS_WINDOWS or windows_long_paths_enabled():
        return None
    for target, extra in staging_extra.items():
        path = os.path.abspath(target)
        path_limit = WINDOWS_MAX_PATH_CHARS - extra
        if name_units(path) > path_limit:
            return (
                f"its path would be {name_units(path)} characters long, more than the "
                f"{path_limit} Windows allows a saved file's path while long paths are disabled"
            )
    return None


def path_too_long_error(exc: BaseException) -> PathTooLongError | None:
    """The :class:`PathTooLongError` behind *exc*, or ``None`` when no path was too long.

    Walks the chain Python prints for *exc* (each ``__cause__``, else an unsuppressed
    ``__context__``) for an ``OSError`` Windows raised on a path past its limit while long
    paths are disabled: a folder it names as too long (``ERROR_FILENAME_EXCED_RANGE``), or
    a file it reports as missing. Any other error on a long path is that error.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PathTooLongError):
            return current
        if _IS_WINDOWS and isinstance(current, OSError):
            too_long = _windows_refused_for_length(current)
            if too_long is not None:
                return too_long
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return None


def _windows_refused_for_length(exc: OSError) -> PathTooLongError | None:
    named_too_long = getattr(exc, "winerror", None) == _ERROR_FILENAME_EXCED_RANGE
    for name in (exc.filename, exc.filename2):
        if not isinstance(name, str) or not name or name.startswith(_UNLIMITED_PATH_PREFIXES):
            continue
        path = os.path.abspath(name)
        refused = (named_too_long and len(path) > _WINDOWS_MAX_FOLDER_PATH_CHARS) or (
            isinstance(exc, FileNotFoundError) and len(path) > WINDOWS_MAX_PATH_CHARS
        )
        if refused and not windows_long_paths_enabled():
            return PathTooLongError(path)
    return None


def ensure_disk_headroom(directory: Path, additional_bytes: int = 0) -> None:
    """Fail before a write when its destination filesystem lacks headroom."""
    if (
        isinstance(additional_bytes, bool)
        or not isinstance(additional_bytes, int)
        or additional_bytes < 0
    ):
        raise ValueError("additional_bytes must be a non-negative integer")
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    required = additional_bytes + _DISK_HEADROOM_BYTES
    available = shutil.disk_usage(directory).free
    if available < required:
        raise OSError(
            errno.ENOSPC,
            f"insufficient disk space: required {required} bytes, available {available} bytes",
            str(directory),
        )


def _temp_path_for(target: Path) -> Path:
    """Return a unique sibling temp path for *target*.

    The name holds this process's writer token (:func:`_writer_token`), which says whose
    stage it is, and a random uuid4 fragment, so concurrent writers from different threads
    or processes do not clobber each other's staging files.
    The name replaces the target's last suffix rather than extending it: a
    staging file sits beside deep store paths, and Windows' traditional
    260-character path limit applies to it too.
    """
    return target.with_name(f"{target.stem}.{_writer_token()}{uuid.uuid4().hex[:4]}.tmp")


def _backup_path_for(target: Path) -> Path:
    """A unique hidden sibling that :func:`atomic_copy_files` moves *target* to while its
    copy replaces it."""
    return target.with_name(f".{target.name}.{uuid.uuid4().hex}.backup")


# A stage written by this version names its owner (eight hex digits, then four); one an
# earlier version wrote has eight random ones and no owner, so it is still a stage, never a
# project's file, but it is not removed: whose it is cannot be told.
_STAGING_NAME = re.compile(r"(?P<stem>.+)\.(?:(?P<owner>[0-9a-f]{8})[0-9a-f]{4}|[0-9a-f]{8})\.tmp")


#: Where each process keeps the lock that says its atomic writes are live. It is held for
#: the process's life and the operating system frees it when the process exits, however
#: it exits, so a stage whose owner's lock can be taken was left by a writer that is gone.
def _writer_locks() -> Path:
    """The folder of the writers' locks, read at each use, as the temporary folder is."""
    return Path(tempfile.gettempdir()) / "haute-writers"


_writer: tuple[int, str, FileLock] | None = None
_writer_guard = threading.Lock()


def _writer_token() -> str:
    """This process's writer token, its lock taken on first use and held until it exits."""
    global _writer
    with _writer_guard:
        if _writer is None or _writer[0] != os.getpid():
            while True:
                token = uuid.uuid4().hex[:8]
                lock = FileLock(_writer_locks() / f"{token}.lock")
                if lock.acquire(blocking=False):
                    break
            _writer = (os.getpid(), token, lock)
        return _writer[1]


def remove_dead_writers_stages(paths: Iterable[Path]) -> list[tuple[Path, OSError]]:
    """Remove each atomic-write stage of *paths* (:func:`is_staging_name`) whose writer is
    gone: the lock its name's token names (:func:`_writer_token`) can be taken, which only
    happens once the process that held it has exited. A live writer's stage, this
    process's own included, is left, however old.

    Returns the stages that could not be removed, each with its error, for the caller to
    log; one already gone is no failure. A link is never followed or removed.
    """
    own = _writer[1] if _writer is not None and _writer[0] == os.getpid() else None
    failed: list[tuple[Path, OSError]] = []
    for path in paths:
        named = _STAGING_NAME.fullmatch(path.name)
        if named is None or named["owner"] is None or named["owner"] == own:
            continue
        owner = FileLock(_writer_locks() / f"{named['owner']}.lock")
        if not owner.acquire(blocking=False):
            continue
        try:
            if stat.S_ISREG(path.lstat().st_mode):
                path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failed.append((path, exc))
        finally:
            owner.release()
            remove_unheld_lock_file(_writer_locks() / f"{named['owner']}.lock")
    return failed


def is_staging_name(name: str) -> bool:
    """Whether the file name *name* is an atomic write's staging file (:func:`_temp_path_for`):
    never a project's file to version."""
    return _STAGING_NAME.fullmatch(name) is not None


def _plain_directory(path: Path) -> bool:
    """Whether *path* is a directory reached as itself: not a link, junction or other
    reparse point, which would take a walk outside the folder it was asked to search."""
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    )
    return stat.S_ISDIR(info.st_mode) and not reparse


def staging_files_under(folder: Path) -> list[Path]:
    """Every atomic write's staging file (:func:`is_staging_name`) in *folder* and the
    folders under it; none when *folder* does not exist. Neither *folder* nor any folder
    under it is entered through a link or junction, so nothing outside it is listed."""
    if not _plain_directory(folder):
        return []
    found: list[Path] = []
    for directory, folders, names in os.walk(folder):
        folders[:] = [name for name in folders if _plain_directory(Path(directory, name))]
        found.extend(Path(directory, name) for name in names if is_staging_name(name))
    return found


def staging_files_of(targets: Iterable[Path]) -> list[Path]:
    """The staging files atomic writes of *targets* left beside them (:func:`_temp_path_for`):
    a writer killed before its rename leaves one, which nothing else would remove. Each
    folder is listed once."""
    stems: dict[Path, set[str]] = {}
    for target in targets:
        stems.setdefault(target.parent, set()).add(target.stem)
    found: list[Path] = []
    for folder, names in stems.items():
        try:
            entries = list(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            match = _STAGING_NAME.fullmatch(entry.name)
            if match is not None and match["stem"] in names and entry.is_file():
                found.append(entry)
    return found


def _replace_with_windows_contention_retry(source: Path, target: Path) -> None:
    """Retry only Win32 access/sharing failures from one atomic replace."""

    for delay in (*_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            source.replace(target)
            return
        except OSError as exc:
            if not _IS_WINDOWS or getattr(exc, "winerror", None) not in {5, 32} or delay is None:
                raise
            time.sleep(delay)


class FolderInUseError(PermissionError):
    """:func:`rename_directory` waited for a folder another program holds a file in, and the
    wait ran out. Still a ``PermissionError``, so every handler that catches one keeps doing
    so; it says the folder was in use, not that permission was refused."""


def rename_directory(source: Path, target: Path) -> None:
    """Rename the directory *source* to *target*, which must not exist.

    Windows refuses to rename a directory while any process holds a file inside it, as an
    antivirus scanner or indexer does for a while after the file is written, with
    ERROR_ACCESS_DENIED or ERROR_SHARING_VIOLATION. A freshly written tree holds many such
    files, so those two codes are retried for longer than one file's replace is, about
    four seconds in all, and :class:`FolderInUseError` is raised from the last refusal when
    the wait runs out; any other failure, or one off Windows, is raised at once.
    """
    for delay in (*_WINDOWS_DIRECTORY_RENAME_RETRY_DELAYS_SECONDS, None):
        try:
            source.rename(target)
            return
        except OSError as exc:
            if not _IS_WINDOWS or getattr(exc, "winerror", None) not in {5, 32}:
                raise
            if delay is None:
                raise FolderInUseError(
                    exc.errno, exc.strerror or str(exc), str(source), None, str(target)
                ) from exc
            time.sleep(delay)


def remove_tree(path: Path) -> bool:
    """Remove a directory tree best-effort, retrying transient Windows failures.

    Returns whether the tree is gone. Windows fails a delete with
    ERROR_ACCESS_DENIED or ERROR_SHARING_VIOLATION while an antivirus scanner or
    indexer briefly holds a handle on a file that was just written, so those two
    codes receive the same short bounded retry as an atomic replace. Callers use
    this for cleanup they must not abort on, and report a tree that survives.
    """
    for delay in (*_WINDOWS_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            # A missing *root* is the tree already being gone. A missing
            # descendant is not: Windows reports a path it cannot open — one
            # past its 260-character limit, say — the same way, and the tree
            # survives. Answering on the root keeps that visible to the caller.
            return not path.exists()
        except OSError as exc:
            if not _IS_WINDOWS or getattr(exc, "winerror", None) not in {5, 32} or delay is None:
                return not path.exists()
            time.sleep(delay)
    return not path.exists()


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Yield a unique staging path for *path*; publish it when the block exits.

    The caller writes the complete payload to the yielded path by any means (a
    Parquet or CSV writer, ``write_bytes``). A clean exit renames it onto
    *path*; an exception unlinks it and leaves the original *path* untouched
    (a block that wrote nothing raises ``FileNotFoundError`` the same way). The
    staging name embeds a random fragment, so concurrent writers to one target
    never share a stage and the target ends as one complete payload (last
    rename wins). The parent directory of *path* must already exist.

    A reader never observes torn/partial bytes on any OS. On POSIX the
    rename also always succeeds under concurrent readers. On Windows a
    concurrent open reader (default ``open()`` omits ``FILE_SHARE_DELETE``)
    can make the rename raise ``PermissionError`` (ERROR_ACCESS_DENIED) —
    those transient codes receive a bounded retry before the write fails
    loudly. A write Windows refused for its path's length raises
    :class:`PathTooLongError` from the original. See the module docstring for
    the full cross-OS contract.
    """
    tmp = _temp_path_for(path)
    try:
        yield tmp
        _replace_with_windows_contention_retry(tmp, path)
    except BaseException as exc:
        try:
            tmp.unlink(missing_ok=True)
        except BaseException as cleanup_exc:
            exc.add_note(f"atomic-write staging cleanup failed: {cleanup_exc}")
        too_long = path_too_long_error(exc)
        if too_long is not None and too_long is not exc:
            raise too_long from exc
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path* (see :func:`atomic_path`)."""
    with atomic_path(path) as tmp:
        tmp.write_bytes(data)


def publish_staged_file(staged: Path, target: Path, *, replace: bool) -> None:
    """Publish a caller's complete *staged* sibling file as *target* in one rename.

    ``replace=True`` replaces *target* with the bounded Windows contention retry.
    ``replace=False`` never clobbers: a *target* that appeared since the caller looked
    raises ``FileExistsError`` and *staged* is left for the caller to remove.
    """
    if replace:
        _replace_with_windows_contention_retry(staged, target)
    elif _IS_WINDOWS:
        # MoveFileExW without MOVEFILE_REPLACE_EXISTING refuses an existing target.
        os.rename(staged, target)
    else:
        # rename(2) would replace; link(2) refuses an existing target atomically.
        os.link(staged, target)
        staged.unlink()


def atomic_create_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Write *data* as the new file *path* in one rename, never replacing a file there.

    Raises ``FileExistsError`` when *path* exists, or appeared since the caller looked;
    the staged copy is removed either way.
    """
    tmp = _temp_path_for(path)
    try:
        tmp.write_bytes(data.encode(encoding))
        publish_staged_file(tmp, path, replace=False)
    finally:
        tmp.unlink(missing_ok=True)


def file_revision(info: os.stat_result) -> str:
    """A file's revision as an editor confirms it: its size and modification time.

    A write or delete made against a revision is refused when the file's revision is no
    longer that one, so a change made since the editor read the file is never overwritten.
    """
    return f"{info.st_size}-{info.st_mtime_ns}"


def atomic_copy_files(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Copy each (source, target) pair transactionally: all targets change or none do.

    Every source is first staged with ``shutil.copyfile`` to a unique sibling
    temp file of its target, so a copy failure changes nothing. Each existing
    target is then moved to a unique sibling backup before its staged copy
    replaces it (both renames use the Windows sharing-violation retry of
    ``atomic_write_bytes``). If any step fails, every newly published target is
    removed, every backup is restored, and every remaining staged copy is
    deleted before the error is re-raised. Backups are deleted only after every
    replacement succeeded, a read-only one made writable first; a backup that still
    cannot be deleted then is logged and left as a hidden sibling, because the copy
    has already committed. As with
    ``atomic_write_bytes``, the parent directory of each target must already exist.
    """
    staged: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for source, target in pairs:
            tmp = _temp_path_for(target)
            # Registered before copying: a copy that writes part of the file
            # and then fails still leaves a staged file to clean up.
            staged.append((tmp, target))
            shutil.copyfile(source, tmp)
        for tmp, target in staged:
            if target.exists() or target.is_symlink():
                backup = _backup_path_for(target)
                _replace_with_windows_contention_retry(target, backup)
                backups.append((backup, target))
            _replace_with_windows_contention_retry(tmp, target)
            published.append(target)
    except BaseException as exc:
        for target in reversed(published):
            try:
                target.unlink(missing_ok=True)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy rollback could not remove {target}: {cleanup_exc}")
        for backup, target in reversed(backups):
            try:
                _replace_with_windows_contention_retry(backup, target)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy rollback could not restore {target}: {cleanup_exc}")
        for tmp, _target in staged:
            try:
                tmp.unlink(missing_ok=True)
            except BaseException as cleanup_exc:
                exc.add_note(f"atomic-copy staging cleanup failed: {cleanup_exc}")
        raise
    for backup, _target in backups:
        try:
            # A read-only target's bytes keep its mode in the backup, and Windows refuses
            # to delete a read-only file: the backup is made writable first. A link's
            # target is never touched.
            if not backup.is_symlink():
                backup.chmod(backup.stat().st_mode | stat.S_IWRITE)
            backup.unlink()
        except OSError as exc:
            from haute._logging import get_logger

            get_logger(component="file_ops").warning(
                "atomic_copy_backup_cleanup_failed",
                path=str(backup),
                error_type=type(exc).__name__,
            )


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Atomically write *data* (text) to *path* using *encoding*."""
    atomic_write_bytes(path, data.encode(encoding))


class Writer:
    """Context manager for a single atomic file write.

    Within the ``with`` block the caller invokes ``write_text`` or
    ``write_bytes`` zero or more times.  Only the LAST call's payload
    is committed (last-wins buffering).  On clean exit the optional
    ``mark_self_write`` callback fires BEFORE the rename with the target
    path and the exact committed payload — this lets file-watcher
    coordination register the incoming write, by content identity, before
    the fs event is emitted.

    On exit with an exception, no file is written, ``mark_self_write``
    is not called, and any staged temp file is removed.
    """

    def __init__(
        self,
        path: Path,
        mark_self_write: Callable[[Path, bytes], None] | None = None,
    ) -> None:
        self._path = path
        self._mark_self_write = mark_self_write
        self._payload: bytes | None = None

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        """Buffer *data* as the pending payload (last call wins)."""
        self._payload = data.encode(encoding)

    def write_bytes(self, data: bytes) -> None:
        """Buffer *data* as the pending payload (last call wins)."""
        self._payload = data

    def __enter__(self) -> Writer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None or self._payload is None:
            return
        if self._mark_self_write is not None:
            self._mark_self_write(self._path, self._payload)
        atomic_write_bytes(self._path, self._payload)
