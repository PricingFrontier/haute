"""Source-file content proofs: strong native revisions and SHA-256 signatures.

A signature is the sole raw-file content proof consumed by cache identity and
loading. Persisted proofs are reused only behind an exact native-revision
match, and a fresh host rebinds a content-matching manifest after one full
hash instead of hashing the source on every process start."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat as stat_module
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from haute._json_shred import _publication
from haute._json_shred._publication import _META_FILENAME
from haute._logging import get_logger

logger = get_logger(component="json_shred")


# ---------------------------------------------------------------------------
# Data-file signature (W2 item 2.4) — validity must see data edits
# ---------------------------------------------------------------------------


_DATA_FILE_SIGNATURE_MEMO_MAX_ENTRIES = 256


_NATIVE_REVISION_SCHEMA_VERSION = 1


_WINDOWS_EPOCH_OFFSET_100NS = 116_444_736_000_000_000


@dataclass(frozen=True, slots=True)
class _StrongFileRevision:
    """A file generation token that detects changes hidden from size/mtime."""

    file_identity: tuple[int, int | bytes]  # pragma: no mutate
    size: int
    mtime_ns: int
    change_token: int


def _native_revision_record(revision: _StrongFileRevision) -> dict[str, Any]:
    """Return the strict JSON representation persisted beside a source hash."""

    volume_or_device, file_id = revision.file_identity
    if isinstance(file_id, bytes):
        kind = "windows_usn_v1"
        identity: list[int | str]  # pragma: no mutate
        identity = [volume_or_device, file_id.hex()]
    else:
        kind = "posix_ctime_v1"
        identity = [volume_or_device, file_id]
    return {
        "schema_version": _NATIVE_REVISION_SCHEMA_VERSION,
        "kind": kind,
        "file_identity": identity,
        "size": revision.size,
        "mtime_ns": revision.mtime_ns,
        "change_token": revision.change_token,
    }


def _parse_native_revision_record(value: Any) -> _StrongFileRevision | None:  # pragma: no mutate
    """Parse one persisted native revision, rejecting partial/weaker shapes."""

    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "file_identity",
        "size",
        "mtime_ns",
        "change_token",
    }:
        return None
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version != _NATIVE_REVISION_SCHEMA_VERSION:
        return None
    kind = value.get("kind")
    identity = value.get("file_identity")
    size = value.get("size")
    mtime_ns = value.get("mtime_ns")
    change_token = value.get("change_token")
    if (
        not isinstance(identity, list)
        or len(identity) != 2
        or type(identity[0]) is not int
        or identity[0] < 0
        or type(size) is not int
        or size < 0
        or type(mtime_ns) is not int
        or type(change_token) is not int
        or change_token <= 0
    ):
        return None
    if kind == "posix_ctime_v1":
        if type(identity[1]) is not int or identity[1] <= 0:
            return None
        file_identity: tuple[int, int | bytes]  # pragma: no mutate
        file_identity = (identity[0], identity[1])
    elif kind == "windows_usn_v1":
        if not isinstance(identity[1], str) or len(identity[1]) != 32:
            return None
        try:
            file_id = bytes.fromhex(identity[1])
        except ValueError:
            return None
        if not any(file_id):
            return None
        file_identity = (identity[0], file_id)
    else:
        return None
    return _StrongFileRevision(
        file_identity=file_identity,
        size=size,
        mtime_ns=mtime_ns,
        change_token=change_token,
    )


def _persisted_source_proof_digest(
    *,  # pragma: no mutate
    size: int,
    mtime_ns: int,
    sha256: str,
    native_revision: _StrongFileRevision,
) -> str:
    """Bind a persisted source signature to its native revision."""

    payload = {
        "schema_version": _NATIVE_REVISION_SCHEMA_VERSION,
        "size": size,
        "mtime_ns": mtime_ns,
        "sha256": sha256,
        "native_revision": _native_revision_record(native_revision),
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


@dataclass(frozen=True, slots=True)
class _DataFileSignatureRecord:
    """Immutable memo payload; callers receive a fresh mapping view."""

    size: int
    mtime_ns: int
    sha256: str
    native_revision: _StrongFileRevision | None  # pragma: no mutate

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "native_revision": (
                None
                if self.native_revision is None
                else _native_revision_record(self.native_revision)
            ),
        }
        payload["native_revision_proof_sha256"] = (
            None
            if self.native_revision is None
            else _persisted_source_proof_digest(
                size=self.size,
                mtime_ns=self.mtime_ns,
                sha256=self.sha256,
                native_revision=self.native_revision,
            )
        )
        return payload


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


def _uncached_data_file_signature(data_path: Path) -> _DataFileSignatureRecord:
    """Hash without retaining a proof when no strong revision is available."""
    observed = data_path.stat()
    before = (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )
    digest = _hash_file(data_path)
    final = data_path.stat()
    after = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
        final.st_ctime_ns,
    )
    if before != after:
        raise OSError(f"data file changed while its signature was computed: {data_path}")
    return _DataFileSignatureRecord(
        size=int(final.st_size),
        mtime_ns=int(final.st_mtime_ns),
        sha256=digest,
        native_revision=None,
    )


def _revision_gated_data_file_signature(
    data_path: Path,
    revision: _StrongFileRevision,
) -> _DataFileSignatureRecord:
    """Hash one source generation and reject a moving native revision."""
    digest = _hash_file(data_path)
    if _strong_file_revision(data_path) != revision:
        raise OSError(f"data file changed while its signature was computed: {data_path}")
    return _DataFileSignatureRecord(
        size=revision.size,
        mtime_ns=revision.mtime_ns,
        sha256=digest,
        native_revision=revision,
    )


def _persisted_data_file_signature(
    data_path: Path,
    revision: _StrongFileRevision,
) -> _DataFileSignatureRecord | None:  # pragma: no mutate
    """Load an agreeing cache-build proof for the exact current generation."""

    from haute._json_flatten import _json_cache_dir

    candidates: list[_DataFileSignatureRecord] = []
    matching_record_invalid = False
    matching_paths: list[str] = []
    for layer in ("working", "committed"):
        meta_path = _json_cache_dir(data_path, layer) / _META_FILENAME
        try:
            meta = orjson.loads(meta_path.read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("schema_mode") != "v2":
            continue
        recorded = meta.get("data_file")
        if not isinstance(recorded, dict):
            continue
        persisted_revision = _parse_native_revision_record(recorded.get("native_revision"))
        if persisted_revision != revision:
            continue
        matching_paths.append(str(meta_path))
        parts = _content_signature_parts(recorded)
        recorded_mtime_ns = recorded.get("mtime_ns")
        proof_digest = recorded.get("native_revision_proof_sha256")
        if (
            parts is None
            or type(recorded_mtime_ns) is not int
            or parts[0] != revision.size
            or recorded_mtime_ns != revision.mtime_ns
            or not isinstance(proof_digest, str)
            or proof_digest
            != _persisted_source_proof_digest(
                size=parts[0],
                mtime_ns=recorded_mtime_ns,
                sha256=parts[1],
                native_revision=revision,
            )
        ):
            matching_record_invalid = True
            continue
        candidates.append(
            _DataFileSignatureRecord(
                size=parts[0],
                mtime_ns=recorded_mtime_ns,
                sha256=parts[1],
                native_revision=revision,
            )
        )

    if (
        matching_record_invalid
        or not candidates
        or any(candidate != candidates[0] for candidate in candidates[1:])
    ):
        if matching_paths:
            logger.warning(
                "json_source_persisted_proof_rejected",
                data_path=str(data_path),
                matching_meta_paths=matching_paths,
                reason=(
                    "invalid_matching_signature"
                    if matching_record_invalid
                    else "conflicting_matching_signatures"
                ),
                action="full_source_hash",
            )
        return None
    if _strong_file_revision(data_path) != revision:
        return None
    return candidates[0]  # pragma: no mutate - candidates are proven value-identical above


def _rebind_persisted_source_proofs(
    data_path: Path,
    signature: _DataFileSignatureRecord,
    revision: _StrongFileRevision,
) -> None:
    """Atomically bind content-matching manifests to one freshly hashed revision.

    This is the fresh-host/first-observation path, not a version shim: a
    manifest whose content signature matches but whose recorded native
    revision differs (a cache built on another volume, or before this host
    could observe a revision) is rebound so later processes here can reuse
    the proof without re-hashing the whole source.
    """

    from haute._json_flatten import _json_cache_dir

    source_parts = (signature.size, signature.sha256)
    source_payload = signature.as_dict()
    for layer in ("working", "committed"):
        cache_dir = _json_cache_dir(data_path, layer)
        meta_path = cache_dir / _META_FILENAME
        with _publication._build_lock_for(cache_dir):
            try:
                meta = orjson.loads(meta_path.read_bytes())
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("schema_mode") != "v2":
                continue
            recorded = meta.get("data_file")
            if (
                not isinstance(recorded, dict)
                or _content_signature_parts(recorded) != source_parts
                or recorded == source_payload
            ):
                continue
            # Do not publish a proof after the source generation that justified
            # it has moved. The already-computed signature remains valid for the
            # caller's observation, but a future process must hash again.
            if _strong_file_revision(data_path) != revision:
                return
            upgraded_meta = {**meta, "data_file": source_payload}
            temp_path = cache_dir / f".{_META_FILENAME}.{uuid.uuid4().hex}.tmp"
            try:
                temp_path.write_bytes(orjson.dumps(upgraded_meta))
                os.replace(temp_path, meta_path)
            except OSError as exc:
                logger.warning(
                    "json_source_persisted_proof_upgrade_failed",
                    data_path=str(data_path),
                    cache_dir=str(cache_dir),
                    error=str(exc),
                    action="retain_full_hash_result",
                )
            else:
                logger.info(
                    "json_source_persisted_proof_upgraded",
                    data_path=str(data_path),
                    cache_dir=str(cache_dir),
                )
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "json_source_persisted_proof_temp_cleanup_failed",
                        data_path=str(data_path),
                        cache_dir=str(cache_dir),
                        temp_path=str(temp_path),
                        error=str(exc),
                    )


class _DataFileSignatureLoadGate:
    """Per-path single-flight state retained only within the cache bound."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.participants = 0


class _DataFileSignatureMemo:
    """Bounded LRU of content hashes admitted by a strong file revision."""

    def __init__(self, *, max_entries: int = _DATA_FILE_SIGNATURE_MEMO_MAX_ENTRIES) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        self._max_entries = max_entries
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries: OrderedDict[
            str,
            tuple[_StrongFileRevision, _DataFileSignatureRecord],
        ] = OrderedDict()
        self._load_gates: dict[str, _DataFileSignatureLoadGate] = {}
        self._unavailable_warnings: OrderedDict[str, None] = OrderedDict()

    def _ensure_current_process(self) -> None:
        process_id = os.getpid()
        if process_id == self._process_id:
            return
        # After fork there is one surviving thread. Replace, rather than
        # acquire, inherited locks: another parent thread may have held them.
        self._process_id = process_id
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._load_gates = {}
        self._unavailable_warnings = OrderedDict()

    def _warn_unavailable_once(self, key: str, path: Path) -> None:
        with self._lock:
            if key in self._unavailable_warnings:
                self._unavailable_warnings.move_to_end(key)
                return
            self._unavailable_warnings[key] = None
            while len(self._unavailable_warnings) > self._max_entries:
                self._unavailable_warnings.popitem(last=False)
        logger.warning(
            "json_source_signature_revision_unavailable",
            data_path=str(path),
            action="full_source_hash_per_operation",
        )

    def get(
        self,
        data_path: Path,
        *,  # pragma: no mutate
        rebind_persisted_proofs: bool = True,
    ) -> dict[str, Any]:
        """Return a source signature, hashing once per unchanged generation."""
        self._ensure_current_process()
        resolved_path = data_path.expanduser().resolve()
        key = os.path.normcase(str(resolved_path))
        revision = _strong_file_revision(resolved_path)
        if revision is None:
            self._warn_unavailable_once(key, resolved_path)
            return _uncached_data_file_signature(resolved_path).as_dict()

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] == revision:
                self._entries.move_to_end(key)
                return entry[1].as_dict()
            load_gate = self._load_gates.setdefault(key, _DataFileSignatureLoadGate())
            load_gate.participants += 1
        try:
            with load_gate.lock:
                # A waiter must observe the revision again: the generation may
                # have moved while another caller owned the flight.
                current_revision = _strong_file_revision(resolved_path)
                if current_revision is None:
                    self._warn_unavailable_once(key, resolved_path)
                    return _uncached_data_file_signature(resolved_path).as_dict()
                with self._lock:
                    entry = self._entries.get(key)
                    if entry is not None and entry[0] == current_revision:
                        self._entries.move_to_end(key)
                        return entry[1].as_dict()

                signature = _persisted_data_file_signature(resolved_path, current_revision)
                if signature is None:
                    signature = _revision_gated_data_file_signature(
                        resolved_path,
                        current_revision,
                    )
                    if rebind_persisted_proofs:
                        _rebind_persisted_source_proofs(
                            resolved_path,
                            signature,
                            current_revision,
                        )
                with self._lock:
                    self._entries[key] = (current_revision, signature)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self._max_entries:
                        evicted_key, _ = self._entries.popitem(last=False)
                        evicted_gate = self._load_gates.get(evicted_key)
                        if evicted_gate is not None and evicted_gate.participants == 0:
                            del self._load_gates[evicted_key]
                return signature.as_dict()
        finally:
            with self._lock:
                load_gate.participants -= 1
                if (
                    load_gate.participants == 0
                    and self._load_gates.get(key) is load_gate
                    and key not in self._entries
                ):
                    del self._load_gates[key]

    def __len__(self) -> int:
        self._ensure_current_process()
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Drop retained proofs without invalidating an active single flight."""
        self._ensure_current_process()
        with self._lock:
            self._entries.clear()
            self._unavailable_warnings.clear()
            self._load_gates = {
                key: load_gate
                for key, load_gate in self._load_gates.items()
                if load_gate.participants
            }


_DATA_FILE_SIGNATURE_MEMO = _DataFileSignatureMemo()


def _clear_data_file_signature_memo() -> None:
    """Test seam for isolating process-wide source-signature proofs."""
    _DATA_FILE_SIGNATURE_MEMO.clear()


def _data_file_signature(
    data_path: Path,
    *,  # pragma: no mutate
    rebind_persisted_proofs: bool = True,
) -> dict[str, Any]:
    """Return the size/mtime/SHA-256 identity recorded in cache metadata.

    The complete content hash remains authoritative. It is reused from memory
    or cache-build metadata only when an OS-native identity/change token proves
    that the same file generation is unchanged; unsupported filesystems take
    the conservative full-hash path. Raises ``OSError`` for an unreadable or
    concurrently changing file.
    """
    return _DATA_FILE_SIGNATURE_MEMO.get(
        data_path,
        rebind_persisted_proofs=rebind_persisted_proofs,
    )


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # pragma: no mutate
            h.update(chunk)
    return h.hexdigest()


def _file_content_signature(path: Path) -> dict[str, Any]:
    """Return the size/SHA-256 identity recorded for a cache artifact."""
    st = path.stat()
    digest = _hash_file(path)
    final_st = path.stat()
    if (st.st_size, st.st_mtime_ns) != (final_st.st_size, final_st.st_mtime_ns):
        raise OSError(f"file changed while its content signature was computed: {path}")
    return {"size": final_st.st_size, "sha256": digest}


def _content_signature_parts(recorded: Any) -> tuple[int, str] | None:  # pragma: no mutate
    """Parse a strict size/SHA-256 record, rejecting bool sizes and bad hex."""
    if not isinstance(recorded, dict):
        return None
    size = recorded.get("size")
    digest = recorded.get("sha256")
    if type(size) is not int or size < 0:  # bool is not a valid byte count
        return None
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        return None
    return size, digest


def _file_content_matches(recorded: Any, path: Path) -> bool:
    """Return whether *path* exactly matches a strict size/SHA-256 record."""
    parts = _content_signature_parts(recorded)
    if parts is None:
        return False
    size, digest = parts
    try:
        if path.stat().st_size != size:
            return False
        return _hash_file(path) == digest
    except OSError:
        return False


def _data_file_matches(
    recorded: Any,
    data_path: Path,
    *,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None = None,  # pragma: no mutate
) -> bool:
    """True iff the data file on disk still matches the recorded signature.

    Order of checks: missing/garbled signature → stale; stat failure → stale
    (serving cached rows
    for a deleted source would be silent wrongness); size mismatch → stale
    (a cheap pre-reject); otherwise the recorded content hash is the sole
    authority.

    The recorded content hash is always compared with an observed content
    proof, never replaced by an ``mtime_ns`` match. The proof may come from
    the strong-revision memo above; a byte-changing rewrite that preserves
    both ``size`` and ``mtime_ns`` changes that revision and forces a fresh
    hash. The deploy-copy case (mtime moved, content identical) still
    validates because the fresh hash matches.
    """
    if data_file_signature is None:
        try:
            data_file_signature = _data_file_signature(data_path)
        except OSError:
            return False
    recorded_parts = _content_signature_parts(recorded)
    observed_parts = _content_signature_parts(data_file_signature)
    return recorded_parts is not None and recorded_parts == observed_parts
