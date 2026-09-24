"""Source freshness: one observation and one content signature for every file.

Every consumer that asks whether a local file changed asks here: Data Input
and API Input snapshot freshness, runtime-input identity, preamble utility
hashes, JSON schema inference, and the artifact caches built on
:class:`haute._stat_gated_cache.StatGatedCache`.

The guarantee: a proof or loaded value is reused only while the file's
freshness token is unchanged. The token is the file's native revision
(Windows volume, file id and USN; POSIX device, inode and ctime, with size and
mtime), which every write moves. Where the platform has no native revision the
token is the file's stat, trusted only for a file last modified at least
:data:`SETTLE_SECONDS` before it was observed; a younger file is proved again
on every use, because a same-size rewrite inside the filesystem's timestamp
granularity keeps its size and mtime.
"""

from __future__ import annotations

import ctypes
import os
import stat as stat_module
import time
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from haute._hashing import HASH_ALGO, content_hash
from haute._logging import get_logger
from haute._lru_cache import LRUCache

if TYPE_CHECKING:
    from haute._stat_gated_cache import StatGatedCache

logger = get_logger(component="source_proof")


SETTLE_SECONDS = 2.0
"""Age below which a file without a native revision is never reused."""

_UNAVAILABLE_WARNINGS_MAX_ENTRIES = 256


class SourceChangedError(OSError, RuntimeError):
    """A file kept changing while it was being proved or loaded."""


_WINDOWS_EPOCH_OFFSET_100NS = 116_444_736_000_000_000


@dataclass(frozen=True, slots=True)
class _StrongFileRevision:
    """A file generation token that detects changes hidden from size/mtime."""

    file_identity: tuple[int, int | bytes]  # pragma: no mutate
    size: int
    mtime_ns: int
    change_token: int


class _WindowsFileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_int64),
        ("LastAccessTime", ctypes.c_int64),
        ("LastWriteTime", ctypes.c_int64),
        ("ChangeTime", ctypes.c_int64),
        ("FileAttributes", ctypes.c_uint32),
    ]


class _WindowsFileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_int64),
        ("EndOfFile", ctypes.c_int64),
        ("NumberOfLinks", ctypes.c_uint32),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class _WindowsFileId128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _WindowsFileIdInfo(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_uint64),
        ("FileId", _WindowsFileId128),
    ]


class _WindowsReadFileUsnData(ctypes.Structure):
    _fields_ = [
        ("MinMajorVersion", ctypes.c_uint16),
        ("MaxMajorVersion", ctypes.c_uint16),
    ]


_FSCTL_READ_FILE_USN_DATA = 0x000900EB


_WINDOWS_USN_OUTPUT_BUFFER_SIZE = 4_096


def _windows_strong_file_revision(path: Path) -> _StrongFileRevision | None:  # pragma: no mutate
    """Read one Windows file identity/USN token, or decline memoisation."""
    windll_factory = getattr(ctypes, "WinDLL", None)
    if windll_factory is None:
        return None
    try:
        kernel32 = windll_factory("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        get_information = kernel32.GetFileInformationByHandleEx
        get_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        get_information.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        device_io_control = kernel32.DeviceIoControl
        device_io_control.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        device_io_control.restype = ctypes.c_int

        # Read attributes only; the file-level USN query does not require a
        # data-read handle. Sharing remains fully permissive for the publisher.
        handle = create_file(
            str(path),
            0x80,  # FILE_READ_ATTRIBUTES
            0x7,  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            basic = _WindowsFileBasicInfo()
            standard = _WindowsFileStandardInfo()
            file_id = _WindowsFileIdInfo()
            queries = (
                (0, basic),  # FileBasicInfo
                (1, standard),  # FileStandardInfo
                (18, file_id),  # FileIdInfo
            )
            for info_class, target in queries:
                if not get_information(
                    handle,
                    info_class,
                    ctypes.byref(target),
                    ctypes.sizeof(target),
                ):
                    return None
            usn_input = _WindowsReadFileUsnData(2, 3)
            usn_buffer = (ctypes.c_ubyte * _WINDOWS_USN_OUTPUT_BUFFER_SIZE)()
            returned = ctypes.c_uint32()
            if not device_io_control(
                handle,
                _FSCTL_READ_FILE_USN_DATA,
                ctypes.byref(usn_input),
                ctypes.sizeof(usn_input),
                ctypes.byref(usn_buffer),
                ctypes.sizeof(usn_buffer),
                ctypes.byref(returned),
                None,
            ):
                return None
            returned_length = int(returned.value)
            if returned_length < 8 or returned_length > ctypes.sizeof(usn_buffer):
                return None
            record = bytes(usn_buffer[:returned_length])
            record_length = int.from_bytes(record[:4], "little")
            major_version = int.from_bytes(record[4:6], "little")
            usn_offset = 24 if major_version == 2 else 40 if major_version == 3 else None
            if (
                usn_offset is None
                or record_length < usn_offset + 8
                or record_length > returned_length
            ):
                return None
            usn = int.from_bytes(record[usn_offset : usn_offset + 8], "little", signed=True)
            if usn <= 0:
                return None
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        return None

    identity = bytes(file_id.FileId.Identifier)
    if standard.Directory or standard.EndOfFile < 0 or not any(identity):
        return None
    return _StrongFileRevision(
        file_identity=(int(file_id.VolumeSerialNumber), identity),
        size=int(standard.EndOfFile),
        mtime_ns=(int(basic.LastWriteTime) - _WINDOWS_EPOCH_OFFSET_100NS) * 100,
        change_token=usn,
    )


def _posix_strong_file_revision(path: Path) -> _StrongFileRevision | None:  # pragma: no mutate
    """Read the POSIX inode/ctime generation gate, if the filesystem has one."""
    observed = path.stat()
    if (
        not stat_module.S_ISREG(observed.st_mode)
        or observed.st_ino <= 0
        or observed.st_ctime_ns <= 0
    ):
        return None
    return _StrongFileRevision(
        file_identity=(int(observed.st_dev), int(observed.st_ino)),
        size=int(observed.st_size),
        mtime_ns=int(observed.st_mtime_ns),
        change_token=int(observed.st_ctime_ns),
    )


def _strong_file_revision(path: Path) -> _StrongFileRevision | None:  # pragma: no mutate
    """Return an OS-native revision safe for content-proof reuse.

    ``None`` means that this observation must take the conservative full-hash
    path. Missing POSIX paths still raise normally; a Windows native-query
    failure falls through to the ordinary stat/hash path, which preserves the
    source reader's existing error type.
    """
    if os.name == "nt":
        return _windows_strong_file_revision(path)
    return _posix_strong_file_revision(path)


@dataclass(frozen=True, slots=True)
class Freshness:
    """One observation of a file's freshness token."""

    token: Hashable
    reusable: bool


def observe_freshness(path: Path) -> Freshness:
    """Observe *path*'s freshness token and whether a proof under it may be reused.

    Raises ``OSError`` (``FileNotFoundError`` for a missing file) when the file
    cannot be observed.
    """
    revision = _strong_file_revision(path)
    if revision is not None:
        return Freshness(revision, reusable=True)
    observed = path.stat()
    _warn_revision_unavailable_once(path)
    return Freshness(
        (
            "stat",
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        ),
        reusable=time.time() - observed.st_mtime >= SETTLE_SECONDS,
    )


_UNAVAILABLE_WARNINGS: LRUCache[str, bool] = LRUCache(max_size=_UNAVAILABLE_WARNINGS_MAX_ENTRIES)


def _warn_revision_unavailable_once(path: Path) -> None:
    key = os.path.normcase(str(path))
    if _UNAVAILABLE_WARNINGS.get(key):
        return
    _UNAVAILABLE_WARNINGS.put(key, True)
    logger.warning(
        "source_revision_unavailable",
        path=str(path),
        action="stat_gate_after_settle",
    )


@dataclass(frozen=True, slots=True)
class FileSignature:
    """The complete-content proof of one file state."""

    size: int
    mtime_ns: int
    digest: str

    @property
    def source_signature(self) -> str:
        """The ``<algorithm>:<digest>:<size>`` string a snapshot records."""
        return f"{HASH_ALGO}:{self.digest}:{self.size}"


_SIGNATURES: StatGatedCache[str, FileSignature] | None = None


def _signatures() -> StatGatedCache[str, FileSignature]:
    # Built on first use: the gated cache observes freshness through this module.
    global _SIGNATURES
    if _SIGNATURES is None:
        from haute._stat_gated_cache import StatGatedCache

        _SIGNATURES = StatGatedCache[str, FileSignature](artifact_kind="Source file")
    return _SIGNATURES


def file_signature(path: Path) -> FileSignature:
    """Return *path*'s content signature, hashed once per unchanged freshness token.

    Raises ``OSError`` for a missing or unreadable file and
    :class:`SourceChangedError` for one that keeps changing while it is hashed.
    """
    from haute._stat_gated_cache import artifact_cache_key

    resolved = path.expanduser().resolve()
    return _signatures().get_or_load(
        artifact_cache_key(resolved),
        str(resolved),
        lambda: _signature(resolved),
    )


def _signature(path: Path) -> FileSignature:
    observed = path.stat()
    return FileSignature(
        size=int(observed.st_size),
        mtime_ns=int(observed.st_mtime_ns),
        digest=_hash_file(path),
    )


def clear_file_signatures() -> None:
    """Forget every retained proof (a test seam; an active flight is kept)."""
    _signatures().clear()
    _UNAVAILABLE_WARNINGS.clear()


def _hash_file(path: Path) -> str:
    return content_hash(path)
