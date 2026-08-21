"""Per-frame JSON shred for the current schema mapping.

The shred produces one frame per emit-true ``tables[]`` entry, each frame
materialised at its own JSON iteration depth.

Algorithm in one paragraph: single-pass walk over the top-level records.
For each record, walk down the tree; when the current iteration depth
matches an emit-true table's path, extract that table's columns and
append a row to its buffer. Lists at any path are treated as repeated
singletons (canonical equivalence) so a JSON array of N objects produces
N row emissions to that path's table. Children are entered only if some
table's path goes deeper; we don't waste work descending into branches
that no table cares about.

On-disk layout in a cache directory (working/<hash>/ or committed/<hash>/):

- one ``<sanitised_label>.parquet`` artifact per current emit-true table.
- one ``meta.json`` carrying ``{schema_mode: "v2", schema_fingerprint,
  tables: [{label, parquet, row_count, column_count, columns,
  content_signature}, ...]}``.

At runtime each requested compressed parquet is pinned into a private,
process-owned file snapshot, its signature is verified in bounded chunks, and
Polars scans that stable path. LazyFrames and their clones therefore keep the
selected generation even if the visible cache is later rebuilt, mirrored, or
cleared, without retaining the complete compressed payload in Python memory.

The per-frame schema for each parquet is also embedded in the parquet's
footer key-value metadata (DUAL_CACHE.md §3) so each file is
self-describing: the schema is co-located with its data, no
schema-wrote-but-data-failed race.
"""

from __future__ import annotations

import atexit
import ctypes
import hashlib
import math
import os
import shutil
import stat as stat_module
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any, Literal, NoReturn, cast
from weakref import WeakValueDictionary
from xml.parsers import expat

import orjson
import polars as pl

from haute._api_input_schema import (
    _RESERVED_LEAF as _SCALAR_VALUE_LEAF,
)
from haute._api_input_schema import (
    ApiInputSchemaError,
    ColumnType,
    PathSeg,
    array_depth,
    derive_identifier_label,
    make_table_path,
    parse_column_path_full,
    parse_table_path,
    validate_v2_schema,
)
from haute._api_input_schema import (
    sanitise_label_for_filesystem as _sanitise_label,
)
from haute._env import int_env
from haute._execution_context import (
    ExecutionCacheProofMissReason,
    ExecutionContext,
    current_execution_context,
)
from haute._jsonpath import is_identifier_name
from haute._logging import get_logger
from haute._native_memory_limit import current_native_memory_backend
from haute._process_memory import process_is_alive

logger = get_logger(component="json_shred")


_META_FILENAME = "meta.json"
_SHRED_EXECUTION_CHECKPOINT_ROWS = 1_024
_DIRECT_SPILL_DIRNAME = ".runtime-spills"
_DIRECT_SPILL_MAX_ROWS_DEFAULT = 10_000
_DIRECT_SPILL_MAX_BYTES_DEFAULT = 16 * 1024 * 1024
_STRUCTURED_INPUT_MAX_RECORD_BYTES_DEFAULT = 64 * 1024 * 1024
_STRUCTURED_INPUT_PARSE_CHUNK_BYTES = 64 * 1024

# Direct spill bundles are deliberately not cache artifacts.  An unmanaged
# LazyFrame can be cloned and retained after this function returns, so its
# bundle has the same process-exit lifetime rule as runtime snapshots.
_DIRECT_SPILL_PROCESS_ID = os.getpid()
_DIRECT_SPILL_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
_DIRECT_SPILL_DIRS: set[Path] = set()
_DIRECT_SPILL_LOCK = threading.Lock()
_DIRECT_SPILL_ATEXIT_REGISTERED = False


@dataclass(slots=True)
class _ShredExecutionProgress:
    """Bound cancellation/RSS-check distance in Python shred materialisation."""

    execution_context: ExecutionContext | None
    work_since_checkpoint: int = 0

    @classmethod
    def current(cls) -> _ShredExecutionProgress:
        return cls(current_execution_context())

    def checkpoint(self, label: str) -> None:
        if self.execution_context is not None:
            self.execution_context.checkpoint(label=label)
        self.work_since_checkpoint = 0

    def advance(self, label: str) -> None:
        if self.execution_context is None:
            return
        self.work_since_checkpoint += 1
        if self.work_since_checkpoint >= _SHRED_EXECUTION_CHECKPOINT_ROWS:
            self.checkpoint(label)


# A JSON *scalar* array (e.g. ``coverages: ["TPFT", "comprehensive"]``)
# becomes its own child table with a single ``value`` column — exactly how
# an array of objects becomes a child table. The column's ``path`` carries a
# reserved ``$value`` leaf meaning "the element itself".
#
# A literal JSON key *can* be spelled ``$value`` (JSON keys are arbitrary
# strings), and such a key WOULD collide with this sentinel: the shred would
# read ``$value`` as "the element itself" instead of that field, silently
# dropping the real value. The collision is made impossible to express
# silently by failing LOUD at inference time — :func:`infer_v2_schema_from_data`
# rejects a source key equal to ``$value`` (and, for the same reason, any key
# containing ``.``, which the path grammar reserves as the object-nesting
# separator). A hand-edited config that mixes a ``$value`` leaf with real-key
# columns on one table is likewise rejected at shred time
# (:func:`shred_to_buffers`). The sentinel string is single-sourced in
# ``_api_input_schema`` (imported above as ``_SCALAR_VALUE_LEAF``) because the
# INPUT path parser must know to accept this one non-identifier leaf; this is
# the downstream consumer.
_SCALAR_VALUE_COLUMN = "value"


def _scalar_to_str(value: Any) -> str:
    """Render a JSON scalar as a string (JSON-style booleans)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce_scalar(value: Any, type_token: str) -> Any:
    """Coerce a genuine JSON scalar to its inferred column type.

    Inference can widen mixed scalar observations (for example ``int`` +
    ``str`` → ``str`` or ``int`` + ``float`` → ``float``). Emission applies
    that same rule to object-table columns and scalar-array ``$value`` columns
    so a schema accepted by inference builds consistently. Shape values
    (dicts/lists) are not converted here; their callers reject or skip them
    under the table-shape contract.
    """
    if value is None:
        return None
    if type_token == "str":
        return value if isinstance(value, str) else _scalar_to_str(value)
    if type_token == "float":
        if isinstance(value, bool):
            # Leave bools alone; `_buffer_to_frame` rejects a bool in a numeric
            # column rather than letting Polars silently coerce it to 0.0/1.0.
            return value
        if isinstance(value, int):
            return float(value)
    return value


def _v2_fingerprint(config: dict[str, Any]) -> str:
    """Stable content hash over the v2 schema's shred-relevant fields.

    Two equivalent v2 configs hash identically; any change to the tables
    or their columns moves the fingerprint. Excludes fields that don't
    affect the shred output (the apiInput's ``path``, ``contract``).
    """
    tables = config.get("tables", [])
    if not isinstance(tables, list):
        raise ApiInputSchemaError("v2 tables must be a list")
    canonical: list[dict[str, Any]] = []
    for ti, table in enumerate(tables):
        if not isinstance(table, dict):
            # Fail LOUD rather than silently dropping a malformed table: a
            # skipped entry would let two structurally-different on-disk
            # configs collapse to the SAME fingerprint, so a schema change
            # from one broken shape to another would not invalidate a stale
            # cache. Distinct configs must hash distinctly (W1).
            raise ApiInputSchemaError(f"v2 tables[{ti}] is not a dict")
        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise ApiInputSchemaError(f"v2 tables[{ti}].columns must be a list")
        cols_canon: list[dict[str, Any]] = []
        for ci, col in enumerate(columns):
            if not isinstance(col, dict):
                raise ApiInputSchemaError(
                    f"v2 tables[{ti}].columns[{ci}] is not a dict",
                )
            cols_canon.append(
                {
                    "name": col.get("name"),
                    "path": col.get("path"),
                    "type": col.get("type"),
                    "selected": bool(col.get("selected")),
                    "levels": col.get("levels"),
                },
            )
        # Sort columns by path for canonical ordering — independent of
        # the user's row-order in the editor.
        cols_canon.sort(key=lambda c: (c.get("path") or "", c.get("name") or ""))
        canonical.append(
            {
                "path": table.get("path"),
                "label": table.get("label"),
                "emit": bool(table.get("emit")),
                "row_id_column": table.get("row_id_column"),
                "columns": cols_canon,
            },
        )
    canonical.sort(key=lambda t: t.get("path") or "")
    payload = orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Shared emitting predicate (W2 item 2.5)
# ---------------------------------------------------------------------------


def table_is_emitting(table: Any) -> bool:
    """THE single definition of "this table contributes a data frame".

    ``emitting = emit AND at least one selected column``. Build, validity
    and load all route through this predicate; before W2 they each
    re-derived their own variant, and the disagreement (build skipped the
    parquet for an emit-true zero-selected-column table while validity
    demanded it) wedged the cache permanently — re-clicking "Cache as
    Parquet" could never repair it.

    Tolerates non-dict tables/columns (returns ``False``) because validity
    runs against arbitrary on-disk configs, mirroring the defensive
    iteration in :func:`_v2_fingerprint`.
    """
    if not isinstance(table, dict):
        return False
    if not table.get("emit"):
        return False
    columns = table.get("columns") or []
    if not isinstance(columns, list):
        return False
    return any(isinstance(col, dict) and col.get("selected") for col in columns)


# ---------------------------------------------------------------------------
# Skip accounting (W2 item 2.7) — zero silent record loss
# ---------------------------------------------------------------------------


@dataclass
class ShredSkipStats:
    """Counts of inputs the shred dropped because their shape didn't fit.

    Two units, never conflated:

    - ``skipped_records`` — top-level inputs that aren't JSON objects (a
      JSONL line holding a number/string/array, a non-object element of a
      root array). They produce no rows in ANY table.
    - ``skipped_rows_by_table`` — array elements at an emitting table's
      depth whose shape mismatched that table (a scalar/null in an
      object-table array, an object in a scalar-table array). Each one is
      a row that table silently lost before W2.

    The build records these in its summary, in ``meta.json``, and the
    route surfaces them in the build/status responses.
    """

    skipped_records: int = 0
    skipped_rows_by_table: dict[str, int] = field(default_factory=dict)

    def count_record_skip(self) -> None:
        self.skipped_records += 1

    def count_row_skip(self, label: str) -> None:
        self.skipped_rows_by_table[label] = self.skipped_rows_by_table.get(label, 0) + 1

    @property
    def total(self) -> int:
        return self.skipped_records + sum(self.skipped_rows_by_table.values())

    def as_meta(self) -> dict[str, Any]:
        """The ``skipped`` payload shape written to meta.json / build summary."""
        return {
            "records": self.skipped_records,
            "rows_by_table": dict(self.skipped_rows_by_table),
        }


# ---------------------------------------------------------------------------
# Data-file signature (W2 item 2.4) — validity must see data edits
# ---------------------------------------------------------------------------


_DATA_FILE_SIGNATURE_MEMO_MAX_ENTRIES = 256
_NATIVE_REVISION_SCHEMA_VERSION = 1
_WINDOWS_EPOCH_OFFSET_100NS = 116_444_736_000_000_000


@dataclass(frozen=True, slots=True)
class _StrongFileRevision:
    """A file generation token that detects changes hidden from size/mtime."""

    file_identity: tuple[int, int | bytes]
    size: int
    mtime_ns: int
    change_token: int


def _native_revision_record(revision: _StrongFileRevision) -> dict[str, Any]:
    """Return the strict JSON representation persisted beside a source hash."""

    volume_or_device, file_id = revision.file_identity
    if isinstance(file_id, bytes):
        kind = "windows_usn_v1"
        identity: list[int | str] = [volume_or_device, file_id.hex()]
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


def _parse_native_revision_record(value: Any) -> _StrongFileRevision | None:
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
    if value.get("schema_version") != _NATIVE_REVISION_SCHEMA_VERSION:
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
        file_identity: tuple[int, int | bytes] = (identity[0], identity[1])
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
    *,
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
    native_revision: _StrongFileRevision | None

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


def _windows_strong_file_revision(path: Path) -> _StrongFileRevision | None:
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


def _posix_strong_file_revision(path: Path) -> _StrongFileRevision | None:
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


def _strong_file_revision(path: Path) -> _StrongFileRevision | None:
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
) -> _DataFileSignatureRecord | None:
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
    return candidates[0]


def _upgrade_legacy_persisted_source_proofs(
    data_path: Path,
    signature: _DataFileSignatureRecord,
    revision: _StrongFileRevision,
) -> None:
    """Atomically bind matching legacy manifests to one freshly hashed revision."""

    from haute._json_flatten import _json_cache_dir

    source_parts = (signature.size, signature.sha256)
    source_payload = signature.as_dict()
    for layer in ("working", "committed"):
        cache_dir = _json_cache_dir(data_path, layer)
        meta_path = cache_dir / _META_FILENAME
        with _build_lock_for(cache_dir):
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
        *,
        upgrade_legacy_proofs: bool = True,
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
                    if upgrade_legacy_proofs:
                        _upgrade_legacy_persisted_source_proofs(
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
    *,
    upgrade_legacy_proofs: bool = True,
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
        upgrade_legacy_proofs=upgrade_legacy_proofs,
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


# ---------------------------------------------------------------------------
# Build serialization (W2 item 2.6)
# ---------------------------------------------------------------------------

# One re-entrant lock per canonical cache directory. The thread lock protects
# same-process callers; the stable sibling file lock protects independent CLI,
# server, and test processes across a complete generation-selection or publish
# transaction. Different cache identities retain independent locks.
_BUILD_LOCKS: WeakValueDictionary[str, _CacheBuildLock]
_BUILD_LOCKS_GUARD = threading.Lock()
_BUILD_LOCKS_PROCESS_ID = os.getpid()
_RENAME_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1)  # pragma: no mutate


class JsonCacheRecoveryError(RuntimeError):
    """A crash-left cache publication cannot be recovered unambiguously."""


def _cache_lock_path(cache_dir: Path) -> Path:
    return cache_dir.with_name(f".{cache_dir.name}.build.lock")


def _cache_lock_file_stat(path: Path, path_stat: os.stat_result) -> None:
    if (
        not stat_module.S_ISREG(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise JsonCacheRecoveryError(f"Cache lock path is not a plain regular file: {path}")


def _open_cache_lock_file(lock_path: Path) -> Any:
    """Open one stable lock inode without trusting a link or replacement path."""
    try:
        existing_stat = lock_path.lstat()
    except FileNotFoundError:
        existing_stat = None
    if existing_stat is not None:
        _cache_lock_file_stat(lock_path, existing_stat)

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise JsonCacheRecoveryError(
            f"Cache lock path could not be opened safely: {lock_path}"
        ) from exc
    try:
        try:
            path_stat = lock_path.lstat()
        except OSError as exc:
            raise JsonCacheRecoveryError(
                f"Cache lock path changed while it was being opened: {lock_path}"
            ) from exc
        descriptor_stat = os.fstat(descriptor)
        _cache_lock_file_stat(lock_path, path_stat)
        _cache_lock_file_stat(lock_path, descriptor_stat)
        if (
            path_stat.st_ino
            and descriptor_stat.st_ino
            and (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise JsonCacheRecoveryError(
                f"Cache lock path changed file identity while it was being opened: {lock_path}"
            )
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _assert_cache_path_ancestors_plain(path: Path) -> None:
    """Reject a cache root or existing descendant reached through a link/reparse point."""
    absolute = Path(os.path.abspath(path))
    cache_root = next(
        (
            candidate
            for candidate in (absolute, *absolute.parents)
            if candidate.name == ".haute_cache"
        ),
        absolute.parent,
    )
    chain: list[Path] = []
    current = absolute.parent
    while True:
        chain.append(current)
        if current == cache_root:
            break
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(chain):
        if not directory.exists() and not directory.is_symlink():
            continue
        _plain_directory_stat(directory)


def _acquire_file_lock(
    handle: Any,
    *,
    blocking: bool = True,
    timeout_seconds: float | None = None,
) -> bool:
    """Acquire one OS file lock, optionally without waiting.

    ``timeout_seconds`` applies only when ``blocking`` is true.  The polling
    implementation is shared by POSIX and Windows so the public lock wrapper
    retains the timeout/non-blocking contract of the ``RLock`` it replaced.
    """
    deadline = (
        None if not blocking or timeout_seconds is None else time.monotonic() + timeout_seconds
    )
    if os.name != "nt":
        import fcntl

        fcntl_module = cast(Any, fcntl)
        if blocking and deadline is None:
            fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_EX)
            return True
        while True:
            try:
                fcntl_module.flock(
                    handle.fileno(),
                    fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
                )
                return True
            except OSError as exc:
                if exc.errno not in {11, 13}:
                    raise
                if not blocking or (deadline is not None and time.monotonic() >= deadline):
                    return False
                time.sleep(0.01)

    import msvcrt

    msvcrt_module = cast(Any, msvcrt)
    while True:
        handle.seek(0)
        try:
            msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {33, 36} and exc.errno not in {
                11,
                13,
                36,
            }:
                raise
            if not blocking or (deadline is not None and time.monotonic() >= deadline):
                return False
            time.sleep(0.01)


def _release_file_lock(handle: Any) -> None:
    if os.name != "nt":
        import fcntl

        fcntl_module = cast(Any, fcntl)
        fcntl_module.flock(handle.fileno(), fcntl_module.LOCK_UN)
        return

    import msvcrt

    msvcrt_module = cast(Any, msvcrt)
    handle.seek(0)
    msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    return bool(getattr(path_stat, "st_file_attributes", 0) & 0x400)


def _plain_directory_stat(path: Path) -> os.stat_result:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        raise
    if (
        not stat_module.S_ISDIR(path_stat.st_mode)
        or stat_module.S_ISLNK(path_stat.st_mode)
        or _is_reparse_point(path_stat)
    ):
        raise JsonCacheRecoveryError(f"Cache recovery refused non-plain directory entry: {path}")
    return path_stat


def _remove_plain_cache_directory(path: Path) -> None:
    _plain_directory_stat(path)
    shutil.rmtree(path)


def _publication_siblings(cache_dir: Path, kind: str) -> list[tuple[Path, os.stat_result]]:
    prefix = f"{cache_dir.name}.build-{kind}-"
    try:
        children = tuple(cache_dir.parent.iterdir())
    except FileNotFoundError:
        return []
    matches: list[tuple[Path, os.stat_result]] = []
    for child in children:
        if not child.name.startswith(prefix):
            continue
        matches.append((child, _plain_directory_stat(child)))
    return matches


def _recover_cache_publication(cache_dir: Path) -> None:
    """Restore or remove only verified crash-left publication siblings."""
    old_generations = _publication_siblings(cache_dir, "old")
    staged_generations = _publication_siblings(cache_dir, "tmp")
    if cache_dir.exists() or cache_dir.is_symlink():
        _plain_directory_stat(cache_dir)
        for path, _path_stat in (*old_generations, *staged_generations):
            _remove_plain_cache_directory(path)
        if old_generations or staged_generations:
            logger.info(
                "json_cache_publication_recovered",
                cache_dir=str(cache_dir),
                action="removed_superseded_siblings",
                old_count=len(old_generations),
                staged_count=len(staged_generations),
            )
        return

    for path, _path_stat in staged_generations:
        _remove_plain_cache_directory(path)
    if not old_generations:
        return
    newest_mtime = max(path_stat.st_mtime_ns for _path, path_stat in old_generations)
    newest = [path for path, path_stat in old_generations if path_stat.st_mtime_ns == newest_mtime]
    if len(newest) != 1:
        raise JsonCacheRecoveryError(
            f"Cache recovery found ambiguous newest backup generations for {cache_dir}"
        )
    restored = newest[0]
    _rename_dir_with_retry(restored, cache_dir)
    for path, _path_stat in old_generations:
        if path != restored:
            _remove_plain_cache_directory(path)
    logger.info(
        "json_cache_publication_recovered",
        cache_dir=str(cache_dir),
        action="restored_backup_generation",
        restored=str(restored),
        discarded_old_count=len(old_generations) - 1,
        discarded_staged_count=len(staged_generations),
    )


class _CacheBuildLock:
    """Thread-reentrant wrapper around one cross-process cache file lock."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle: Any | None = None
        self._owner_thread_id: int | None = None

    def __enter__(self) -> _CacheBuildLock:
        acquired = self.acquire()
        if not acquired:  # pragma: no cover - an unbounded acquire cannot time out
            raise RuntimeError("cache build lock could not be acquired")
        return self

    def owned_by_current_thread(self) -> bool:
        """Return whether this process/thread owns the publication lock."""
        return self._depth > 0 and self._owner_thread_id == threading.get_ident()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the thread and process lock with ``RLock``-compatible controls."""
        if not blocking and timeout != -1:
            raise ValueError("can't specify a timeout for a non-blocking call")
        started_at = time.monotonic()
        if not blocking:
            thread_acquired = self._thread_lock.acquire(blocking=False)
        elif timeout == -1:
            thread_acquired = self._thread_lock.acquire()
        else:
            thread_acquired = self._thread_lock.acquire(timeout=timeout)
        if not thread_acquired:
            return False
        if self._depth:
            self._depth += 1
            return True
        handle: Any | None = None
        try:
            lock_path = _cache_lock_path(self._cache_dir)
            _assert_cache_path_ancestors_plain(lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = _open_cache_lock_file(lock_path)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            remaining_timeout = None
            if timeout != -1:
                remaining_timeout = max(0.0, timeout - (time.monotonic() - started_at))
            if not _acquire_file_lock(
                handle,
                blocking=blocking,
                timeout_seconds=remaining_timeout,
            ):
                handle.close()
                self._thread_lock.release()
                return False
            _recover_cache_publication(self._cache_dir)
            self._handle = handle
            self._depth = 1
            self._owner_thread_id = threading.get_ident()
            return True
        except BaseException:
            if handle is not None:
                try:
                    _release_file_lock(handle)
                except BaseException:
                    pass
                handle.close()
            self._thread_lock.release()
            raise

    def release(self) -> None:
        """Release one re-entrant acquisition."""
        self._release(primary_exception=None)

    def _release(self, *, primary_exception: BaseException | None) -> None:
        if self._owner_thread_id != threading.get_ident() or self._depth <= 0:
            raise RuntimeError("cannot release un-acquired cache build lock")
        try:
            self._depth -= 1
            if self._depth:
                return
            handle, self._handle = self._handle, None
            self._owner_thread_id = None
            if handle is None:
                raise RuntimeError("cache build lock lost its file handle")
            try:
                _release_file_lock(handle)
            except BaseException as release_exc:
                if primary_exception is None:
                    raise
                primary_exception.add_note(f"cache file lock release failed: {release_exc}")
            finally:
                handle.close()
        finally:
            self._thread_lock.release()

    def __exit__(
        self,
        exc_type: Any,
        exc: BaseException | None,
        traceback: Any,
    ) -> Literal[False]:
        del exc_type, traceback
        self._release(primary_exception=exc)
        return False


_BUILD_LOCKS = WeakValueDictionary()

# File-backed runtime snapshots live outside a cache generation so replacing or
# clearing that generation cannot retarget an already-returned LazyFrame. Repeated
# access to one artifact generation can share a private path across managed
# executions. Its verification-cache pin is independently bounded; unmanaged direct
# callers pin it for the process:
# a derived Polars plan can outlive the original LazyFrame, so there is no safe
# Python-object lifetime at which to reclaim its source. Orderly process exit
# removes every remaining private snapshot directory.
_RUNTIME_SNAPSHOT_DIRNAME = ".runtime-snapshots"
_RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX = 32
_RUNTIME_SNAPSHOT_PROCESS_ID = os.getpid()
_RUNTIME_SNAPSHOT_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
_RUNTIME_SNAPSHOT_DIRS: set[Path] = set()
_RUNTIME_SNAPSHOT_REFERENCES: dict[Path, int] = {}
_RUNTIME_SNAPSHOT_PROCESS_PINS: set[Path] = set()
_RUNTIME_SNAPSHOT_LOCK = threading.Lock()
_RUNTIME_SNAPSHOT_ATEXIT_REGISTERED = False
_RUNTIME_OWNER_META_FILENAME = ".owner.json"
_RUNTIME_OWNER_FORMAT_VERSION = 1
_RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES = 4 * 1024 * 1024 * 1024
_RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS = 60 * 60
_RUNTIME_STORAGE_RECOVERY_PROCESS_ID = os.getpid()
_RUNTIME_STORAGE_RECOVERED_ROOTS: set[Path] = set()
RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES = int_env("HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES", 64)
RUNTIME_SNAPSHOT_CACHE_MAX_BYTES = int_env(
    "HAUTE_JSON_RUNTIME_SNAPSHOT_CACHE_MAX_BYTES", 512 * 1024 * 1024
)


class JsonRuntimeDiskBudgetExceededError(RuntimeError):
    """A runtime snapshot/spill allocation exceeded the project disk budget."""

    def __init__(self, *, used_bytes: int, budget_bytes: int) -> None:
        super().__init__(
            f"JSON runtime storage requires {used_bytes} bytes, exceeding its "
            f"{budget_bytes} byte disk budget"
        )
        self.used_bytes = used_bytes
        self.budget_bytes = budget_bytes


class JsonRuntimeStorageIntegrityError(RuntimeError):
    """Runtime storage contains an entry whose size cannot be trusted."""

    def __init__(self, *, path: Path, reason: str) -> None:
        super().__init__(
            f"JSON runtime storage cannot be measured safely because {path} is {reason}"
        )
        self.path = path
        self.reason = reason


class SourceChangedDuringCacheBuildError(RuntimeError):
    """The structured source no longer matches the generation a worker staged."""


@dataclass(frozen=True, slots=True)
class PreparedPerPortCacheBuild:
    """Pickle-safe evidence for one complete but not-yet-visible generation."""

    data_path: str
    cache_dir: str
    staging_dir: str | None
    schema_fingerprint: str
    data_file_signature: dict[str, Any]
    summary: dict[str, Any]
    no_op: bool = False


def _runtime_storage_root_for_cache(cache_dir: Path) -> Path:
    absolute = Path(os.path.abspath(cache_dir))
    for candidate in (absolute, *absolute.parents):
        if candidate.name == ".haute_cache":
            return candidate
    return absolute.parent


def _runtime_storage_parents(cache_root: Path) -> tuple[Path, ...]:
    return (
        cache_root / _RUNTIME_SNAPSHOT_DIRNAME,
        cache_root / _DIRECT_SPILL_DIRNAME,
        cache_root / "working" / _RUNTIME_SNAPSHOT_DIRNAME,
        cache_root / "committed" / _RUNTIME_SNAPSHOT_DIRNAME,
    )


def _runtime_owner_payload() -> dict[str, int | float]:
    return {
        "format_version": _RUNTIME_OWNER_FORMAT_VERSION,
        "pid": os.getpid(),
        "created_at": time.time(),
    }


def _ensure_runtime_owner_metadata(owner_dir: Path) -> None:
    meta_path = owner_dir / _RUNTIME_OWNER_META_FILENAME
    if meta_path.exists():
        return
    temp_path = owner_dir / f".{_RUNTIME_OWNER_META_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_bytes(orjson.dumps(_runtime_owner_payload()))
        os.replace(temp_path, meta_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _remove_empty_runtime_owner_dir(owner_dir: Path) -> None:
    """Remove an owner directory only when no runtime artifacts remain."""
    try:
        children = tuple(owner_dir.iterdir())
    except FileNotFoundError:
        return
    if any(child.name != _RUNTIME_OWNER_META_FILENAME for child in children):
        return
    (owner_dir / _RUNTIME_OWNER_META_FILENAME).unlink(missing_ok=True)
    owner_dir.rmdir()


def _runtime_owner_record(owner_dir: Path) -> tuple[int, float] | None:
    try:
        payload = orjson.loads((owner_dir / _RUNTIME_OWNER_META_FILENAME).read_bytes())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or type(payload.get("format_version")) is not int
        or payload.get("format_version") != _RUNTIME_OWNER_FORMAT_VERSION
    ):
        return None
    pid = payload.get("pid")
    created_at = payload.get("created_at")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(created_at)
    ):
        return None
    return pid, float(created_at)


def _recover_runtime_storage_parent(
    runtime_parent: Path,
    *,
    now: float,
    grace_seconds: int,
) -> dict[str, int]:
    report = {"inspected": 0, "removed": 0, "preserved": 0}
    if not runtime_parent.exists() and not runtime_parent.is_symlink():
        return report
    try:
        _plain_directory_stat(runtime_parent)
        children = tuple(runtime_parent.iterdir())
    except (OSError, JsonCacheRecoveryError) as exc:
        logger.warning(
            "json_runtime_storage_parent_preserved",
            path=str(runtime_parent),
            reason="non_plain_or_unreadable",
            error_type=type(exc).__name__,
        )
        report["preserved"] += 1
        return report

    for owner_dir in children:
        report["inspected"] += 1
        try:
            _plain_directory_stat(owner_dir)
        except (OSError, JsonCacheRecoveryError) as exc:
            report["preserved"] += 1
            logger.warning(
                "json_runtime_storage_owner_preserved",
                path=str(owner_dir),
                reason="non_plain_or_unreadable",
                error_type=type(exc).__name__,
            )
            continue
        owner = _runtime_owner_record(owner_dir)
        if owner is None:
            report["preserved"] += 1
            logger.warning(
                "json_runtime_storage_owner_preserved",
                path=str(owner_dir),
                reason="malformed_owner_metadata",
            )
            continue
        pid, created_at = owner
        if now - created_at < grace_seconds:
            report["preserved"] += 1
            continue
        if process_is_alive(pid):
            report["preserved"] += 1
            continue
        _remove_plain_cache_directory(owner_dir)
        report["removed"] += 1
        logger.info(
            "json_runtime_storage_owner_reaped",
            path=str(owner_dir),
            pid=pid,
        )
    try:
        runtime_parent.rmdir()
    except OSError:
        pass
    return report


def recover_json_runtime_storage(
    cache_root: str | Path | None = None,
    *,
    now: float | None = None,
) -> dict[str, int]:
    """Reap only old, plain, ownership-marked directories from dead processes."""
    root = (
        Path(os.path.abspath(Path.cwd() / ".haute_cache"))
        if cache_root is None
        else Path(os.path.abspath(cache_root))
    )
    grace_seconds = int_env(
        "HAUTE_JSON_RUNTIME_ORPHAN_GRACE_SECONDS",
        _RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS,
    )
    current_time = time.time() if now is None else now
    if (
        isinstance(current_time, bool)
        or not isinstance(current_time, (int, float))
        or not math.isfinite(current_time)
    ):
        raise ValueError("now must be finite")
    aggregate = {"inspected": 0, "removed": 0, "preserved": 0}
    if root.exists() or root.is_symlink():
        try:
            _plain_directory_stat(root)
        except (OSError, JsonCacheRecoveryError) as exc:
            logger.warning(
                "json_runtime_storage_root_preserved",
                path=str(root),
                reason="non_plain_or_unreadable",
                error_type=type(exc).__name__,
            )
            aggregate["preserved"] = 1
            return aggregate
    for runtime_parent in _runtime_storage_parents(root):
        report = _recover_runtime_storage_parent(
            runtime_parent,
            now=current_time,
            grace_seconds=grace_seconds,
        )
        for key, value in report.items():
            aggregate[key] += value
    return aggregate


def _runtime_file_identity(path: Path, path_stat: os.stat_result) -> tuple[object, ...]:
    if path_stat.st_ino:
        return ("inode", path_stat.st_dev, path_stat.st_ino)
    return ("path", os.path.normcase(str(path.resolve())))


def _runtime_storage_usage_bytes(cache_root: Path) -> int:
    identities: set[tuple[object, ...]] = set()
    total = 0

    def _visit(directory: Path) -> None:
        nonlocal total
        try:
            _plain_directory_stat(directory)
            children = tuple(directory.iterdir())
        except FileNotFoundError:
            return
        except (OSError, JsonCacheRecoveryError) as exc:
            raise JsonRuntimeStorageIntegrityError(
                path=directory,
                reason="a non-plain or unreadable directory",
            ) from exc
        for child in children:
            try:
                child_stat = child.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="unreadable",
                ) from exc
            if stat_module.S_ISDIR(child_stat.st_mode) and not _is_reparse_point(child_stat):
                _visit(child)
                continue
            if (
                not stat_module.S_ISREG(child_stat.st_mode)
                or stat_module.S_ISLNK(child_stat.st_mode)
                or _is_reparse_point(child_stat)
            ):
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="not a plain file or directory",
                )
            try:
                identity = _runtime_file_identity(child, child_stat)
            except OSError as exc:
                raise JsonRuntimeStorageIntegrityError(
                    path=child,
                    reason="unreadable while resolving its file identity",
                ) from exc
            if identity in identities:
                continue
            identities.add(identity)
            total += child_stat.st_size

    for runtime_parent in _runtime_storage_parents(cache_root):
        _visit(runtime_parent)
    return total


def _recover_runtime_storage_once(cache_root: Path) -> None:
    global _RUNTIME_STORAGE_RECOVERY_PROCESS_ID, _RUNTIME_STORAGE_RECOVERED_ROOTS
    if os.getpid() != _RUNTIME_STORAGE_RECOVERY_PROCESS_ID:
        _RUNTIME_STORAGE_RECOVERY_PROCESS_ID = os.getpid()
        _RUNTIME_STORAGE_RECOVERED_ROOTS = set()
    if cache_root in _RUNTIME_STORAGE_RECOVERED_ROOTS:
        return
    recover_json_runtime_storage(cache_root)
    _RUNTIME_STORAGE_RECOVERED_ROOTS.add(cache_root)


@contextmanager
def _runtime_disk_budget_transaction(
    cache_root: Path,
    *,
    allow_existing_excess: bool = False,
) -> Iterator[None]:
    root = Path(os.path.abspath(cache_root))
    budget_bytes = int_env(
        "HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES",
        _RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES,
    )
    with _build_lock_for(root / ".runtime-storage-budget"):
        _recover_runtime_storage_once(root)
        used_before = _runtime_storage_usage_bytes(root)
        if used_before > budget_bytes and not allow_existing_excess:
            raise JsonRuntimeDiskBudgetExceededError(
                used_bytes=used_before,
                budget_bytes=budget_bytes,
            )
        yield
        used_after = _runtime_storage_usage_bytes(root)
        if used_after > budget_bytes:
            raise JsonRuntimeDiskBudgetExceededError(
                used_bytes=used_after,
                budget_bytes=budget_bytes,
            )


@dataclass(frozen=True, slots=True)
class _VerifiedRuntimeSnapshot:
    revision: _StrongFileRevision
    snapshot_path: Path
    size: int


class _VerifiedRuntimeSnapshotLoadGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.participants = 0


class _VerifiedRuntimeSnapshotCache:
    """Bounded, fork-safe LRU of already verified private parquet snapshots."""

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        for name, value in (("max_entries", max_entries), ("max_bytes", max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, int, str], _VerifiedRuntimeSnapshot] = OrderedDict()
        self._path_counts: dict[Path, int] = {}
        self._path_sizes: dict[Path, int] = {}
        self._bytes = 0
        self._gates: dict[tuple[str, int, str], _VerifiedRuntimeSnapshotLoadGate] = {}
        self._warnings: OrderedDict[str, None] = OrderedDict()

    def _ensure_current_process(self) -> None:
        if os.getpid() == self._process_id:
            return
        # Do not acquire an inherited lock after fork: a vanished parent thread
        # may have owned it. These are child-local copies of all bookkeeping.
        self._process_id = os.getpid()
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._path_counts = {}
        self._path_sizes = {}
        self._bytes = 0
        self._gates = {}
        self._warnings = OrderedDict()

    def reset_after_fork(self) -> None:
        """Forget inherited state without touching parent-owned files."""
        self._ensure_current_process()
        with self._lock:
            self._entries.clear()
            self._path_counts.clear()
            self._path_sizes.clear()
            self._bytes = 0
            self._gates = {key: gate for key, gate in self._gates.items() if gate.participants}
            self._warnings.clear()

    def warn_revision_unavailable_once(self, path_key: str, path: Path) -> None:
        self._ensure_current_process()
        with self._lock:
            if path_key in self._warnings:
                self._warnings.move_to_end(path_key)
                return
            self._warnings[path_key] = None
            while len(self._warnings) > self._max_entries:
                self._warnings.popitem(last=False)
        logger.warning(
            "json_cache_artifact_revision_unavailable",
            parquet_path=str(path),
            action="full_artifact_hash_per_operation",
        )

    def begin(self, key: tuple[str, int, str]) -> _VerifiedRuntimeSnapshotLoadGate:
        self._ensure_current_process()
        with self._lock:
            gate = self._gates.setdefault(key, _VerifiedRuntimeSnapshotLoadGate())
            gate.participants += 1
            return gate

    def finish(self, key: tuple[str, int, str], gate: _VerifiedRuntimeSnapshotLoadGate) -> None:
        self._ensure_current_process()
        with self._lock:
            gate.participants -= 1
            if gate.participants == 0 and self._gates.get(key) is gate and key not in self._entries:
                del self._gates[key]

    def _drop_entry_locked(self, key: tuple[str, int, str]) -> list[Path]:
        entry = self._entries.pop(key)
        path = entry.snapshot_path
        count = self._path_counts[path] - 1
        if count:
            self._path_counts[path] = count
            return []
        del self._path_counts[path]
        self._bytes -= self._path_sizes.pop(path)
        return [path]

    def get(
        self, key: tuple[str, int, str], revision: _StrongFileRevision
    ) -> tuple[Path | None, list[Path]]:
        self._ensure_current_process()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None, []
            if entry.revision != revision or not entry.snapshot_path.exists():
                return None, self._drop_entry_locked(key)
            self._entries.move_to_end(key)
            return entry.snapshot_path, []

    def store(
        self,
        key: tuple[str, int, str],
        revision: _StrongFileRevision,
        snapshot_path: Path,
        size: int,
    ) -> tuple[bool, list[Path]]:
        """Pin a verified snapshot if it fits, returning cache-pin evictions."""
        self._ensure_current_process()
        if size > self._max_bytes:
            return False, []
        evicted: list[Path] = []
        with self._lock:
            if key in self._entries:
                evicted.extend(self._drop_entry_locked(key))
            self._entries[key] = _VerifiedRuntimeSnapshot(revision, snapshot_path, size)
            self._entries.move_to_end(key)
            if snapshot_path in self._path_counts:
                self._path_counts[snapshot_path] += 1
            else:
                self._path_counts[snapshot_path] = 1
                self._path_sizes[snapshot_path] = size
                self._bytes += size
            while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                old_key = next(iter(self._entries))
                evicted.extend(self._drop_entry_locked(old_key))
            self._gates = {
                gate_key: gate
                for gate_key, gate in self._gates.items()
                if gate.participants or gate_key in self._entries
            }
            retained = key in self._entries
        return retained, evicted

    def is_pinned(self, snapshot_path: Path) -> bool:
        self._ensure_current_process()
        with self._lock:
            return snapshot_path in self._path_counts

    def clear(self) -> None:
        self.reset_after_fork()

    def stats(self) -> dict[str, int]:
        self._ensure_current_process()
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "inflight": sum(gate.participants > 0 for gate in self._gates.values()),
            }


_VERIFIED_RUNTIME_SNAPSHOT_CACHE = _VerifiedRuntimeSnapshotCache(
    RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES,
    RUNTIME_SNAPSHOT_CACHE_MAX_BYTES,
)


def _build_lock_for(cache_dir: Path) -> _CacheBuildLock:
    global _BUILD_LOCKS_PROCESS_ID, _BUILD_LOCKS_GUARD, _BUILD_LOCKS
    if os.getpid() != _BUILD_LOCKS_PROCESS_ID:
        # A forked child must not acquire locks inherited from vanished parent
        # threads or reuse inherited file-lock ownership.
        _BUILD_LOCKS_PROCESS_ID = os.getpid()
        _BUILD_LOCKS_GUARD = threading.Lock()
        _BUILD_LOCKS = WeakValueDictionary()
    absolute = Path(os.path.abspath(cache_dir))
    key = os.path.normcase(str(absolute))
    with _BUILD_LOCKS_GUARD:
        return _BUILD_LOCKS.setdefault(key, _CacheBuildLock(absolute))


@contextmanager
def per_port_cache_publication_lock(cache_dir: str | Path) -> Iterator[None]:
    """Serialize prepare/validate/commit for one visible cache generation."""
    with _build_lock_for(Path(cache_dir)):
        yield


def _cleanup_runtime_snapshot_dirs() -> None:
    """Remove every private parquet snapshot directory owned by this process."""
    with _RUNTIME_SNAPSHOT_LOCK:
        # A forked child inherits Python globals and atexit callbacks but must
        # never remove the parent's still-live generation snapshots.
        if os.getpid() != _RUNTIME_SNAPSHOT_PROCESS_ID:
            return
        snapshot_dirs = tuple(_RUNTIME_SNAPSHOT_DIRS)
        _RUNTIME_SNAPSHOT_DIRS.clear()
        _RUNTIME_SNAPSHOT_REFERENCES.clear()
        _RUNTIME_SNAPSHOT_PROCESS_PINS.clear()
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.clear()
    for snapshot_dir in snapshot_dirs:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    for parent in {snapshot_dir.parent for snapshot_dir in snapshot_dirs}:
        try:
            parent.rmdir()
        except OSError:
            # Another live process token, or a crash-left directory, still owns
            # entries beneath the shared snapshot parent.
            pass


def _ensure_runtime_snapshot_process_state() -> None:
    """Discard snapshot ownership inherited from another process."""
    global _RUNTIME_SNAPSHOT_PROCESS_ID, _RUNTIME_SNAPSHOT_PROCESS_TOKEN, _RUNTIME_SNAPSHOT_LOCK

    # This check deliberately precedes acquiring the lock. A forked child can
    # inherit a lock held by a parent thread which no longer exists in the child.
    if os.getpid() != _RUNTIME_SNAPSHOT_PROCESS_ID:
        _RUNTIME_SNAPSHOT_PROCESS_ID = os.getpid()
        _RUNTIME_SNAPSHOT_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
        _RUNTIME_SNAPSHOT_LOCK = threading.Lock()
        _RUNTIME_SNAPSHOT_DIRS.clear()
        _RUNTIME_SNAPSHOT_REFERENCES.clear()
        _RUNTIME_SNAPSHOT_PROCESS_PINS.clear()
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.reset_after_fork()


def _runtime_snapshot_dir(cache_dir: Path) -> Path:
    """Return this process's private snapshot directory beside *cache_dir*."""
    global _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED

    _ensure_runtime_snapshot_process_state()
    snapshot_dir = cache_dir.parent / _RUNTIME_SNAPSHOT_DIRNAME / _RUNTIME_SNAPSHOT_PROCESS_TOKEN
    cache_root = _runtime_storage_root_for_cache(cache_dir)
    try:
        with _runtime_disk_budget_transaction(cache_root):
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _ensure_runtime_owner_metadata(snapshot_dir)
    except BaseException:
        try:
            _remove_empty_runtime_owner_dir(snapshot_dir)
        except OSError:
            pass
        raise
    with _RUNTIME_SNAPSHOT_LOCK:
        _RUNTIME_SNAPSHOT_DIRS.add(snapshot_dir)
        if not _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED:
            atexit.register(_cleanup_runtime_snapshot_dirs)
            _RUNTIME_SNAPSHOT_ATEXIT_REGISTERED = True
    return snapshot_dir


def _stream_copy_with_signature(source: Path, target: Path) -> tuple[int, str]:
    """Copy *source* to exclusive *target* with bounded memory and one hash pass."""
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as source_file, target.open("xb") as target_file:
            for chunk in iter(lambda: source_file.read(1 << 20), b""):
                target_file.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _release_runtime_snapshot(snapshot_path: Path) -> None:
    """Release one transient or execution-owned reference to *snapshot_path*."""
    with _RUNTIME_SNAPSHOT_LOCK:
        references = _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0)
        if references <= 0:
            raise RuntimeError(f"runtime parquet snapshot was released twice: {snapshot_path}")
        if references > 1:
            _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = references - 1
            return
        del _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path]
        if (
            snapshot_path in _RUNTIME_SNAPSHOT_PROCESS_PINS
            or _VERIFIED_RUNTIME_SNAPSHOT_CACHE.is_pinned(snapshot_path)
        ):
            return
        try:
            snapshot_path.unlink()
        except FileNotFoundError:
            pass
        try:
            _remove_empty_runtime_owner_dir(snapshot_path.parent)
        except OSError:
            # Another content snapshot in this process directory is still live.
            pass


def _remove_unpinned_runtime_snapshot(snapshot_path: Path) -> None:
    """Drop an evicted cache pin once no execution/process lease remains."""
    with _RUNTIME_SNAPSHOT_LOCK:
        if (
            _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0) > 0
            or snapshot_path in _RUNTIME_SNAPSHOT_PROCESS_PINS
            or _VERIFIED_RUNTIME_SNAPSHOT_CACHE.is_pinned(snapshot_path)
        ):
            return
        try:
            snapshot_path.unlink()
        except FileNotFoundError:
            pass
        try:
            _remove_empty_runtime_owner_dir(snapshot_path.parent)
        except OSError:
            pass


def _retain_runtime_snapshot(snapshot_path: Path) -> None:
    """Convert a transient snapshot reference into its execution lifetime."""
    execution_context = current_execution_context()
    if execution_context is None:
        # Outside a managed execution there is no sound lifetime boundary for
        # arbitrary derived LazyFrames. Pin once for this process and consume the
        # transient reference without deleting the content-addressed path.
        with _RUNTIME_SNAPSHOT_LOCK:
            references = _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0)
            if references <= 0:
                raise RuntimeError(
                    f"runtime parquet snapshot has no transient owner: {snapshot_path}"
                )
            _RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)
            if references == 1:
                del _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path]
            else:
                _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = references - 1
        return

    # Keep the transient reference as this context's lease. The execution
    # context releases resources only after its collection and cleanup finish.
    try:
        execution_context.add_cleanup(lambda: _release_runtime_snapshot(snapshot_path))
    except BaseException:
        _release_runtime_snapshot(snapshot_path)
        raise


def _capture_runtime_snapshot(
    cache_dir: Path,
    parquet_path: Path,
    snapshot_dir: Path,
    expected_size: int,
    expected_digest: str,
    cache_key: tuple[str, int, str] | None,
    source_revision: _StrongFileRevision | None = None,
) -> Path | None:
    """Capture and verify one generation, retaining it only when proof permits."""
    # A stale-entry eviction can remove the now-empty process directory after
    # `_runtime_snapshot_dir` returned it for this operation.
    candidate = snapshot_dir / f".{uuid.uuid4().hex}.parquet.tmp"
    copied = False
    captured_revision: _StrongFileRevision | None = None
    try:
        cache_root = _runtime_storage_root_for_cache(cache_dir)
        with _runtime_disk_budget_transaction(cache_root):
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _ensure_runtime_owner_metadata(snapshot_dir)
            try:
                os.link(parquet_path, candidate)
            except FileNotFoundError:
                raise
            except OSError as exc:
                copied = True
                logger.warning(
                    "json_shred_runtime_snapshot_copy_fallback",
                    cache_dir=str(cache_dir),
                    parquet_path=str(parquet_path),
                    error_type=type(exc).__name__,
                )
                # A regular Windows read handle blocks the rename-based publisher.
                # This fallback is serialized with same-process builders.
                with _build_lock_for(cache_dir):
                    observed_size, observed_digest = _stream_copy_with_signature(
                        parquet_path, candidate
                    )
            else:
                # Linking itself may change inode metadata. Observe the captured
                # generation only after the link exists, then prove that revision
                # survived the complete verification hash below.
                captured_revision = _strong_file_revision(candidate)
                observed = _file_content_signature(candidate)
                observed_size = observed["size"]
                observed_digest = observed["sha256"]

        if (observed_size, observed_digest) != (expected_size, expected_digest):
            candidate.unlink(missing_ok=True)
            return None

        cacheable_revision: _StrongFileRevision | None = None
        if cache_key is not None and source_revision is not None:
            if copied:
                # Copying captures bytes rather than identity, so require the
                # visible generation to be unchanged over the copy interval.
                visible_after = _strong_file_revision(parquet_path)
                if visible_after == source_revision:
                    cacheable_revision = source_revision
            else:
                captured_after = _strong_file_revision(candidate)
                if captured_after != captured_revision:
                    candidate.unlink(missing_ok=True)
                    return None
                cacheable_revision = captured_after

        # The complete digest remains in the cache key and was verified above.
        # A fixed 128-bit filename address stays shorter than the temporary name,
        # avoiding a rename-only failure at the legacy Windows path boundary.
        snapshot_path = snapshot_dir / (
            f"{expected_digest[:_RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX]}.parquet"
        )
        # Publication, cache pinning, and the first execution lease are one
        # runtime-lock transaction. This establishes runtime-lock -> cache-lock
        # ordering and prevents another key's eviction from unlinking the file
        # between its cache admission and returned lease.
        with _RUNTIME_SNAPSHOT_LOCK:
            if snapshot_path.exists():
                if candidate.samefile(snapshot_path):
                    candidate.unlink(missing_ok=True)
                else:
                    # A content-address collision can be an independently
                    # captured inode (for example two source paths with equal
                    # bytes). Never substitute that file for the generation
                    # verified above: a later in-place edit through its source
                    # link must not corrupt this lease or its cached proof.
                    # Keep the collision name no longer than the already
                    # created candidate. Deep project paths can otherwise
                    # cross legacy Windows path-length handling during rename.
                    snapshot_path = snapshot_dir / f"{uuid.uuid4().hex}.parquet"
                    candidate.rename(snapshot_path)
            else:
                candidate.rename(snapshot_path)
            if cacheable_revision is not None:
                # The visible path must still name the stable captured inode at
                # admission time; a publisher race merely makes this call
                # transient rather than authorising reuse.
                visible_after = _strong_file_revision(parquet_path)
                if copied:
                    if visible_after != cacheable_revision:
                        cacheable_revision = None
                elif (
                    visible_after is None
                    or visible_after.file_identity != cacheable_revision.file_identity
                    or visible_after.size != cacheable_revision.size
                ):
                    cacheable_revision = None
                else:
                    # The snapshot's native change token proves its own bytes
                    # were stable during hashing; the visible path's token is
                    # the generation gate for later cache hits.
                    cacheable_revision = visible_after
            evicted: list[Path] = []
            if cacheable_revision is not None:
                assert cache_key is not None
                _retained, evicted = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.store(
                    cache_key, cacheable_revision, snapshot_path, expected_size
                )
            _RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = (
                _RUNTIME_SNAPSHOT_REFERENCES.get(snapshot_path, 0) + 1
            )
        for evicted_path in evicted:
            _remove_unpinned_runtime_snapshot(evicted_path)
        return snapshot_path
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def _snapshot_cache_artifact(
    cache_dir: Path,
    parquet_path: Path,
    recorded_signature: Any,
) -> Path | None:
    with _build_lock_for(cache_dir):
        return _snapshot_cache_artifact_locked(
            cache_dir,
            parquet_path,
            recorded_signature,
        )


def _snapshot_cache_artifact_locked(
    cache_dir: Path,
    parquet_path: Path,
    recorded_signature: Any,
) -> Path | None:
    """Pin and verify one cache artifact without materialising its payload.

    A hard link captures one rename-published generation atomically and does not
    duplicate its disk blocks. The link is hashed in bounded chunks, then moved
    to a content-addressed private path that lazy Polars plans can safely retain.
    Filesystems without hard-link support use an equivalently bounded copy while
    holding the same-process build lock; the copied bytes are hashed as written.
    ``None`` means the captured generation did not match its manifest signature.
    """
    # A child must discard inherited lock and cache state before touching any
    # cache-owned synchronization primitive. Runtime storage itself is allocated
    # only after the verified-generation lookup misses.
    _ensure_runtime_snapshot_process_state()
    expected = _content_signature_parts(recorded_signature)
    assert expected is not None
    expected_size, expected_digest = expected
    visible_path = parquet_path.expanduser().resolve()
    path_key = os.path.normcase(str(visible_path))
    initial_revision = _strong_file_revision(visible_path)
    if initial_revision is None:
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.warn_revision_unavailable_once(path_key, visible_path)
        snapshot_dir = _runtime_snapshot_dir(cache_dir)
        return _capture_runtime_snapshot(
            cache_dir, visible_path, snapshot_dir, expected_size, expected_digest, None
        )

    key = (path_key, expected_size, expected_digest)
    gate = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.begin(key)
    try:
        with gate.lock:
            current_revision = _strong_file_revision(visible_path)
            if current_revision is None:
                _VERIFIED_RUNTIME_SNAPSHOT_CACHE.warn_revision_unavailable_once(
                    path_key, visible_path
                )
                snapshot_dir = _runtime_snapshot_dir(cache_dir)
                return _capture_runtime_snapshot(
                    cache_dir, visible_path, snapshot_dir, expected_size, expected_digest, None
                )
            # Take the execution lease while the cache pin is still observed,
            # so a concurrent LRU eviction cannot unlink a just-hit snapshot.
            with _RUNTIME_SNAPSHOT_LOCK:
                hit, evicted = _VERIFIED_RUNTIME_SNAPSHOT_CACHE.get(key, current_revision)
                if hit is not None:
                    _RUNTIME_SNAPSHOT_REFERENCES[hit] = _RUNTIME_SNAPSHOT_REFERENCES.get(hit, 0) + 1
            for evicted_path in evicted:
                _remove_unpinned_runtime_snapshot(evicted_path)
            if hit is not None:
                return hit
            snapshot_dir = _runtime_snapshot_dir(cache_dir)
            return _capture_runtime_snapshot(
                cache_dir,
                visible_path,
                snapshot_dir,
                expected_size,
                expected_digest,
                key,
                current_revision,
            )
    finally:
        _VERIFIED_RUNTIME_SNAPSHOT_CACHE.finish(key, gate)


def _unique_build_tmp_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-tmp-{uuid.uuid4().hex}")


def new_per_port_cache_staging_dir(cache_dir: str | Path) -> Path:
    """Return one parent-owned, validated private generation path."""
    cd = _normalised_build_path(cache_dir)
    return _validated_build_staging_dir(cd, _unique_build_tmp_dir(cd))


def _unique_build_old_dir(cache_dir: Path) -> Path:
    return cache_dir.with_name(f"{cache_dir.name}.build-old-{uuid.uuid4().hex}")


def _rename_dir_with_retry(source: Path, target: Path) -> None:
    """Rename a fully-built cache dir, retrying transient Windows handle locks."""
    for delay in (*_RENAME_RETRY_DELAYS_SECONDS, None):
        try:
            source.rename(target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Record iteration
# ---------------------------------------------------------------------------


def _xml_local_name(name: str) -> str:
    """Strip an XML namespace while retaining the source element name."""
    return name.rsplit("}", 1)[-1].split(":", 1)[-1]


def _xml_element_value(element: ET.Element) -> Any:
    """Convert an XML element into the object/list/scalar shape used by shredding."""
    result: dict[str, Any] = {}

    for raw_name, value in element.attrib.items():
        name = _xml_local_name(raw_name)
        if name in result:
            raise ApiInputSchemaError(f"duplicate XML attribute name {name!r}")
        result[name] = value

    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if not result:
            return text
        if text:
            if "value" in result:
                raise ApiInputSchemaError(
                    "XML element has both a 'value' attribute and text content"
                )
            result["value"] = text
        return result

    if (element.text or "").strip() or any((child.tail or "").strip() for child in children):
        raise ApiInputSchemaError(
            f"mixed text and child elements are not supported in XML element "
            f"{_xml_local_name(element.tag)!r}"
        )

    grouped: dict[str, list[Any]] = {}
    for child in children:
        name = _xml_local_name(child.tag)
        grouped.setdefault(name, []).append(_xml_element_value(child))

    for name, values in grouped.items():
        if name in result:
            raise ApiInputSchemaError(f"XML attribute and child element share the name {name!r}")
        result[name] = values[0] if len(values) == 1 else values
    return result


def _structured_input_record_limit() -> int:
    return int_env(
        "HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES",
        _STRUCTURED_INPUT_MAX_RECORD_BYTES_DEFAULT,
    )


def _record_limit_error(kind: str, limit: int) -> ApiInputSchemaError:
    return ApiInputSchemaError(
        f"{kind} exceeds HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES={limit}; "
        "split the source into smaller logical records or raise the limit "
        "within the execution memory budget"
    )


def _scan_xml_declaration_chunk(carry: bytes, chunk: bytes) -> bytes:
    """Reject unsafe declarations in the exact bytes about to reach the parser."""
    tokens = (b"<!DOCTYPE", b"<!ENTITY")
    overlap = max(len(token) for token in tokens) - 1
    # Removing NULs also recognises the ASCII declaration tokens in UTF-16/32
    # XML encodings. The XML parser still remains authoritative for encoding.
    upper = (carry + chunk).upper().replace(b"\x00", b"")
    if any(token in upper for token in tokens):
        raise ApiInputSchemaError("XML DTD and entity declarations are not supported")
    return upper[-overlap:]


def _validate_xml_record_value_size(value: dict[str, Any], limit: int) -> None:
    if len(orjson.dumps(value)) > limit:
        raise _record_limit_error("XML record", limit)


class _XmlDirectChildByteTracker:
    """Enforce a conservative encoded-byte bound without retaining source bytes."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._depth = 0
        self._record_start: int | None = None
        self._closing_tag_allowance = 0
        self._parser = expat.ParserCreate()
        self._parser.StartElementHandler = self._start_element
        self._parser.EndElementHandler = self._end_element

    def _start_element(self, name: str, _attributes: dict[str, str]) -> None:
        self._depth += 1
        if self._depth == 2:
            self._record_start = self._parser.CurrentByteIndex
            # XML supports UTF-8/16/32. Reserving four bytes per closing-tag
            # code point makes the limit fail closed without buffering the tag.
            self._closing_tag_allowance = 4 * (len(name) + 3)

    def _end_element(self, _name: str) -> None:
        if self._depth == 2:
            self._check_open_record()
            self._record_start = None
            self._closing_tag_allowance = 0
        self._depth -= 1

    def _check_open_record(self) -> None:
        if self._record_start is None:
            return
        encoded_bytes = self._parser.CurrentByteIndex - self._record_start
        if encoded_bytes + self._closing_tag_allowance > self._limit:
            raise _record_limit_error("XML record", self._limit)

    def feed(self, chunk: bytes) -> None:
        self._parser.Parse(chunk, False)
        self._check_open_record()

    def close(self) -> None:
        self._parser.Parse(b"", True)


@dataclass(frozen=True, slots=True)
class _XmlRecordShape:
    repeated_object_children: bool


def _read_xml_events(
    parser: ET.XMLPullParser,
) -> Iterator[tuple[str, ET.Element[str]]]:
    """Narrow typeshed's event union to this parser's start/end contract."""
    return cast("Iterator[tuple[str, ET.Element[str]]]", parser.read_events())


def _require_xml_root(root: ET.Element[str] | None) -> ET.Element[str]:
    """Reject an impossible pull-parser event order with explicit evidence."""
    if root is None:
        raise RuntimeError("XML parser emitted a direct child before the document root")
    return root


def _inspect_xml_record_shape(data_path: Path, limit: int) -> _XmlRecordShape:
    """Validate XML and classify its top-level record shape with bounded retention."""
    parser = ET.XMLPullParser(events=("start", "end"))
    byte_tracker = _XmlDirectChildByteTracker(limit)
    chunk_size = min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, limit + 1)
    root: ET.Element | None = None
    depth = 0
    direct_child_count = 0
    direct_child_name: str | None = None
    all_direct_children_are_objects = True
    root_has_attributes = False
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                byte_tracker.feed(chunk)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start":
                        depth += 1
                        if depth == 1:
                            root = element
                            root_has_attributes = bool(element.attrib)
                        continue

                    if depth == 2:
                        root_element = _require_xml_root(root)
                        if (element.tail or "").strip():
                            root_name = _xml_local_name(root_element.tag)
                            raise ApiInputSchemaError(
                                f"mixed text and child elements are not supported in XML element "
                                f"{root_name!r}"
                            )
                        value = _xml_element_value(element)
                        if isinstance(value, dict):
                            _validate_xml_record_value_size(value, limit)
                        child_name = _xml_local_name(element.tag)
                        if direct_child_name is None:
                            direct_child_name = child_name
                        elif child_name != direct_child_name:
                            all_direct_children_are_objects = False
                        if not isinstance(value, dict):
                            all_direct_children_are_objects = False
                        direct_child_count += 1
                        root_element.remove(element)
                        element.clear()
                    elif depth == 1 and (element.text or "").strip() and direct_child_count:
                        raise ApiInputSchemaError(
                            f"mixed text and child elements are not supported in XML element "
                            f"{_xml_local_name(element.tag)!r}"
                        )
                    depth -= 1
            byte_tracker.close()
            parser.close()
    except (ET.ParseError, expat.ExpatError) as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc

    return _XmlRecordShape(
        repeated_object_children=(
            direct_child_count > 0
            and not root_has_attributes
            and all_direct_children_are_objects
            and direct_child_name is not None
        )
    )


def _iter_repeated_xml_records(data_path: Path, limit: int) -> Iterator[dict[str, Any]]:
    """Yield and release homogeneous direct-child XML records."""
    parser = ET.XMLPullParser(events=("start", "end"))
    byte_tracker = _XmlDirectChildByteTracker(limit)
    chunk_size = min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, limit + 1)
    root: ET.Element | None = None
    depth = 0
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                byte_tracker.feed(chunk)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start":
                        depth += 1
                        if depth == 1:
                            root = element
                        continue

                    if depth == 2:
                        root_element = _require_xml_root(root)
                        value = _xml_element_value(element)
                        if not isinstance(value, dict):
                            raise RuntimeError(
                                "XML record shape changed between validation and emission"
                            )
                        _validate_xml_record_value_size(value, limit)
                        root_element.remove(element)
                        element.clear()
                        yield value
                    depth -= 1
            byte_tracker.close()
            parser.close()
    except (ET.ParseError, expat.ExpatError) as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc


def _parse_bounded_xml_root(data_path: Path, limit: int) -> ET.Element:
    """Parse one-root-record XML while enforcing its encoded-byte bound."""
    parser = ET.XMLPullParser(events=("start", "end"))
    total = 0
    root: ET.Element | None = None
    declaration_carry = b""
    try:
        with data_path.open("rb") as source:
            while chunk := source.read(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES):
                declaration_carry = _scan_xml_declaration_chunk(declaration_carry, chunk)
                total += len(chunk)
                if total > limit:
                    raise _record_limit_error("XML record", limit)
                parser.feed(chunk)
                for event, element in _read_xml_events(parser):
                    if event == "start" and root is None:
                        root = element
            parser.close()
    except ET.ParseError as exc:
        raise ApiInputSchemaError(f"Invalid XML in data file: {exc}") from exc
    if root is None:
        raise ApiInputSchemaError("Invalid XML in data file: no document element")
    return root


def _iter_xml_records(data_path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a single XML document.

    An attribute-free container whose children all share one element name is
    treated like a JSON root array. Otherwise the document root itself is one
    record so root attributes are never discarded.
    """
    limit = _structured_input_record_limit()
    shape = _inspect_xml_record_shape(data_path, limit)
    if shape.repeated_object_children:
        yield from _iter_repeated_xml_records(data_path, limit)
        return

    root = _parse_bounded_xml_root(data_path, limit)
    value = _xml_element_value(root)
    if isinstance(value, dict):
        yield value
    else:
        yield {_xml_local_name(root.tag): value}


def _iter_records(
    data_path: Path,
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    """Yield top-level records from a JSON or JSONL file.

    JSONL: one record per non-empty line.
    JSON: if the file's root is an array, yields each element; if the
    root is an object, yields that single object.

    A top-level input that parses as valid JSON but isn't an object (a
    JSONL line holding ``5`` / ``"x"`` / ``[...]``, a non-object element of
    a root array, a scalar root) is not a record and is skipped — *stats*,
    when provided, counts each one so the build can surface the loss
    (W2 item 2.7). Blank JSONL lines are formatting, not records, and are
    never counted. Malformed JSON still raises.
    """

    def _count_record_skip() -> None:
        if stats is not None:
            stats.count_record_skip()

    record_limit = _structured_input_record_limit()
    suffix = data_path.suffix.lower()
    if suffix == ".xml":
        yield from _iter_xml_records(data_path)
        return
    if suffix in (".jsonl", ".ndjson"):
        with data_path.open("rb") as f:
            while raw_line := f.readline(record_limit + 1):
                if len(raw_line) > record_limit:
                    raise _record_limit_error("JSONL record", record_limit)
                stripped = raw_line.strip()
                if not stripped:
                    continue
                obj = orjson.loads(stripped)
                if isinstance(obj, dict):
                    yield obj
                else:
                    _count_record_skip()
        return
    # A root array can be arbitrarily large.  Read precisely one complete
    # element at a time, while still consuming and validating the complete
    # document (including the closing bracket and any trailing bytes).
    pos = 0
    with data_path.open("rb") as f:

        def _read_byte() -> bytes:
            nonlocal pos
            byte = f.read(1)
            if byte:
                pos += 1
            return byte

        def _read_non_ws() -> bytes:
            while True:
                byte = _read_byte()
                if not byte or byte not in b" \t\r\n":
                    return byte

        first = _read_non_ws()
        if not first:
            return
        if first != b"[":
            # Root-object/scalar semantics remain exactly the existing JSON
            # semantics, with one explicit hard logical-record bound.
            document = bytearray(first)
            while chunk := f.read(min(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES, record_limit + 1)):
                document.extend(chunk)
                if len(document) > record_limit:
                    raise _record_limit_error("JSON root record", record_limit)
            obj = orjson.loads(document)
            if isinstance(obj, dict):
                yield obj
            else:
                _count_record_skip()
            return

        expect_value = False
        while True:
            first = _read_non_ws()
            if not first:
                raise _json_decode_error("unexpected end of data", pos)
            if first == b"]":
                if expect_value:
                    raise _json_decode_error("trailing comma in array", pos)
                pos = _validate_json_trailing_whitespace(f, pos)
                return
            value, delimiter = _read_root_array_value(
                first,
                _read_byte,
                lambda: pos,
                max_bytes=record_limit,
            )
            obj = orjson.loads(value)
            if isinstance(obj, dict):
                yield obj
            else:
                _count_record_skip()
            expect_value = delimiter == b","
            if delimiter == b"]":
                pos = _validate_json_trailing_whitespace(f, pos)
                return


def _json_decode_error(message: str, pos: int) -> orjson.JSONDecodeError:
    return orjson.JSONDecodeError(message, "", pos)


def _validate_json_trailing_whitespace(source: Any, pos: int) -> int:
    """Consume JSON trailing whitespace without allocating it as one byte string."""
    while chunk := source.read(_STRUCTURED_INPUT_PARSE_CHUNK_BYTES):
        for offset, byte in enumerate(chunk):
            if byte not in b" \t\r\n":
                # ``pos`` is the count of bytes already consumed, which is
                # also the zero-based index of the first byte in ``chunk``.
                raise _json_decode_error("unexpected trailing data", pos + offset)
        pos += len(chunk)
    return pos


def _iter_sampled_json_array_records(
    data_path: Path,
    sample_size: int,
) -> Iterator[dict[str, Any]]:
    """Yield up to ``sample_size`` object records from a root JSON array.

    This is intentionally used only for inference sampling. Full builds and
    unsampled inference still parse the whole file so malformed data is caught
    before any cache is materialised.
    """
    yielded = 0
    pos = 0
    expect_value = False

    with data_path.open("rb") as f:

        def _read_byte() -> bytes:
            nonlocal pos
            b = f.read(1)
            if b:
                pos += 1
            return b

        def _read_non_ws() -> bytes:
            while True:
                b = _read_byte()
                if not b or b not in b" \t\r\n":  # pragma: no mutate
                    return b

        first = _read_non_ws()
        if not first:
            return
        if first != b"[":
            yield from islice(_iter_records(data_path), sample_size)
            return

        def _validate_eof() -> None:
            nonlocal pos
            pos = _validate_json_trailing_whitespace(f, pos)

        while yielded < sample_size:  # pragma: no mutate
            first = _read_non_ws()
            if not first:
                raise _json_decode_error("unexpected end of data", pos)
            if first == b"]":  # pragma: no mutate
                if expect_value:
                    raise _json_decode_error("trailing comma in array", pos)
                _validate_eof()
                return

            value, delimiter = _read_root_array_value(
                first,
                _read_byte,
                lambda: pos,
                max_bytes=_structured_input_record_limit(),
            )
            obj = orjson.loads(value)
            if isinstance(obj, dict):
                yield obj
                yielded += 1
            expect_value = delimiter == b","  # pragma: no mutate
            if delimiter == b"]":  # pragma: no mutate
                _validate_eof()
                return


def _read_root_array_value(
    first: bytes,
    read_byte: Callable[[], bytes],
    current_pos: Callable[[], int],
    *,
    max_bytes: int | None = None,
) -> tuple[bytes, bytes]:
    """Read one value from a root JSON array and return its delimiter."""
    limit = _structured_input_record_limit() if max_bytes is None else max_bytes
    buf = bytearray(first)
    depth = 1 if first in {b"{", b"["} else 0
    in_string = first == b'"'  # pragma: no mutate
    escaped = False

    while True:
        b = read_byte()
        if not b:
            raise _json_decode_error("unexpected end of data", current_pos())

        if in_string:
            buf.extend(b)
            if len(buf) > limit:
                raise _record_limit_error("JSON array element", limit)
            if escaped:
                escaped = False
            elif b == b"\\":  # pragma: no mutate
                escaped = True
            elif b == b'"':  # pragma: no mutate
                in_string = False
            continue

        if b == b'"':  # pragma: no mutate
            buf.extend(b)
            if len(buf) > limit:
                raise _record_limit_error("JSON array element", limit)
            in_string = True
            continue

        if b in {b"{", b"["}:
            depth += 1
            buf.extend(b)
            if len(buf) > limit:
                raise _record_limit_error("JSON array element", limit)
            continue

        if b in {b"}", b"]"}:
            if depth > 0:  # pragma: no mutate
                depth -= 1
                buf.extend(b)
                if len(buf) > limit:
                    raise _record_limit_error("JSON array element", limit)
                continue
            if b == b"]":  # pragma: no mutate
                return bytes(buf).rstrip(), b
            raise _json_decode_error("unexpected '}'", current_pos())

        # A depth-0 ``]`` is already handled by the close-delimiter block above,
        # so only the comma remains as a value terminator here.
        if depth == 0 and b == b",":
            return bytes(buf).rstrip(), b

        buf.extend(b)
        if len(buf) > limit:
            raise _record_limit_error("JSON array element", limit)


def _iter_records_for_inference(
    data_path: Path,
    *,  # pragma: no mutate
    sample_size: int | None,  # pragma: no mutate
) -> Iterator[dict[str, Any]]:
    if sample_size is None or sample_size <= 0:
        yield from _iter_records(data_path)
        return
    if data_path.suffix.lower() in (".jsonl", ".ndjson", ".xml"):  # pragma: no mutate
        yield from islice(_iter_records(data_path), sample_size)
        return
    yield from _iter_sampled_json_array_records(data_path, sample_size)


# ---------------------------------------------------------------------------
# Shred core
# ---------------------------------------------------------------------------


_LeafSpec = tuple[str, str, str]  # (column_name, leaf_path_dotted, type_token)
# As _LeafSpec, plus the array-iteration depth at which the column's value
# lives (W1): equal to the table's depth for a normal column, shallower for
# an ancestor column whose value distributes over descendant rows.
_WalkSpec = tuple[str, str, str, int]
# As _WalkSpec, plus the two per-column constants the walk would otherwise
# re-derive on every emitted row: the leaf pre-split into hops, and whether the
# leaf is the reserved scalar sentinel. Built once per shred in
# :func:`shred_to_buffers`; never part of a stored config.
# (column_name, leaf_path_dotted, leaf_parts, type_token, source_depth, is_scalar_leaf)
_PreparedCol = tuple[str, str, tuple[str, ...], str, int, bool]


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _EmittingTableSpec:
    """One validated emitting table, parsed once for every shred consumer.

    ``columns`` carries the full walk-time form (including source array depth)
    needed for ancestor broadcasts. Cache writes and in-memory runtime loads
    derive their leaf-only frame schema from the same objects, so the two paths
    cannot disagree about selected columns, names, types, or paths.
    """

    label: str
    segments: tuple[PathSeg, ...]
    columns: tuple[_WalkSpec, ...]

    @property
    def leaf_specs(self) -> list[_LeafSpec]:
        return [(name, leaf, type_token) for name, leaf, type_token, _depth in self.columns]


def _leaf_parts(leaf: str) -> tuple[str, ...]:
    """Split a dotted leaf into its hops once, for reuse across every row.

    The leaf path is a constant of the column spec, but the shred resolves it
    per row per column (millions of calls on a large file). Splitting there
    re-derived the same tuple every time; callers on the hot path precompute
    it with this and hand it to :func:`_resolve_leaf`.
    """
    return tuple(leaf.split("."))


def _resolve_leaf(
    value: Any,
    leaf: str,
    parts: tuple[str, ...] | None = None,  # pragma: no mutate - type declaration
) -> Any:
    """Resolve a dotted leaf path within a single dict.

    *parts* is ``leaf`` pre-split by :func:`_leaf_parts`. It is purely a hot-path
    optimisation — omitted, the split happens here and behaviour is identical.

    For ``leaf = "policy_id"`` returns ``value["policy_id"]`` (or None).
    For ``leaf = "profile.age"`` walks one level deeper.

    A dotted leaf addresses 1-1 OBJECT nesting only (that is the only shape
    inference ever produces a dotted leaf for — an array becomes a child
    table, never a dotted hop). A NON-EMPTY list encountered mid-walk does
    not match that shape: collapsing it to one element would violate
    conservation, so the array field must be modelled as its own child table.

    An EMPTY list mid-walk discards nothing — there is no element to drop —
    so it is not a conservation violation. It resolves to None (the value is
    simply absent at this key), so data that legitimately mixes an object
    with an occasional empty-array/missing value at that key does not
    hard-fail the whole build.

    The reserved ``$value`` leaf means "the scalar element itself" (scalar
    array child tables): ``value`` is then the element, returned as-is. A
    dict/list under that leaf is a shape mismatch and resolves to None — the
    caller guards against emitting those rows.
    """
    if leaf == _SCALAR_VALUE_LEAF:
        return value if not isinstance(value, (dict, list)) else None
    if not isinstance(value, dict):
        return None
    if parts is None:
        parts = _leaf_parts(leaf)
    if len(parts) == 1:
        return value.get(parts[0])
    cur: Any = value
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            if not cur:
                # An empty list discards nothing — not a conservation
                # violation. Resolve to None (value absent at this key)
                # rather than hard-failing the build (W1).
                return None
            raise ApiInputSchemaError(
                f"dotted column leaf {leaf!r} crosses an array at segment "
                f"{part!r}, but a dotted leaf addresses 1-1 object nesting only "
                "(silently taking the first element would drop the rest); model "
                "this array as its own child table instead",
                column=leaf,
            )
        else:
            return None
    return cur


def _reject_reserved_leaf_collision(label: str, own_depth: int, col_specs: list[_WalkSpec]) -> None:
    """Fail loud if a table mixes the reserved ``$value`` leaf with a real sibling.

    The ``$value`` sentinel means "the scalar element itself"; a scalar-array
    child table carries it as the ONE column sourced at the table's own array
    depth (``own_depth``). It MAY additionally carry ancestor columns sourced at
    a SHALLOWER depth — those distribute a parent value over the scalar rows (a
    legitimate W1 pattern), so they don't collide.

    What IS malformed is a ``$value`` leaf coexisting with ANOTHER own-depth
    column — a real sibling field on what is actually an object table. The whole
    table is then treated as scalar (``is_scalar_table`` keys off the presence
    of any ``$value`` leaf), so every object row is silently dropped as a shape
    mismatch and the sibling fields never read. Inference can no longer produce
    this shape (it rejects a literal ``$value`` source key), but a hand-edited
    config could; reject it at shred time rather than emit a table whose rows all
    vanish.
    """
    own_depth_cols = [(n, leaf) for n, leaf, _t, d in col_specs if d == own_depth]
    if any(leaf == _SCALAR_VALUE_LEAF for _n, leaf in own_depth_cols) and len(own_depth_cols) > 1:
        names = [n for n, _leaf in own_depth_cols]
        raise ApiInputSchemaError(
            f"table {label!r} mixes the reserved '{_SCALAR_VALUE_LEAF}' "
            "scalar-array leaf with a real sibling column at the same depth; a "
            f"'$value' column must be the table's only own-depth column "
            f"(own-depth columns: {names})",
            table=label,
        )


def _emitting_table_specs(v2_config: dict[str, Any]) -> tuple[_EmittingTableSpec, ...]:
    """Validate *v2_config* and parse every emitting table exactly one way."""
    validate_v2_schema(v2_config)

    table_specs: list[_EmittingTableSpec] = []
    for table in v2_config["tables"]:
        if not table_is_emitting(table):
            continue
        segments = parse_table_path(table["path"])
        columns: list[_WalkSpec] = []
        for col in table.get("columns", []) or []:
            if not col.get("selected"):
                continue
            locating, leaf = parse_column_path_full(col["path"])
            columns.append(
                (
                    col["name"],
                    leaf,
                    col["type"],
                    array_depth(locating),
                ),
            )
        _reject_reserved_leaf_collision(table["label"], array_depth(segments), columns)
        table_specs.append(
            _EmittingTableSpec(
                label=table["label"],
                segments=segments,
                columns=tuple(columns),
            ),
        )
    return tuple(table_specs)


def shred_to_buffers(
    records: Iterable[dict[str, Any]],
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    stats: ShredSkipStats | None = None,  # pragma: no mutate
    _table_specs: tuple[_EmittingTableSpec, ...] | None = None,  # pragma: no mutate
    _row_sink: Callable[[str, dict[str, Any]], None] | None = None,  # pragma: no mutate
    _emitted_counts: dict[str, int] | None = None,  # pragma: no mutate
) -> dict[str, list[dict[str, Any]]]:
    """Shred *records* according to *v2_config*, returning per-frame row buffers.

    Output is a dict keyed by ``table.label`` (the frame name); each value
    is a list of rows. Each row is a dict mapping ``column.name`` to the
    extracted value (or ``None`` when the path doesn't resolve).

    Validates the schema before walking, so a malformed config raises
    upfront rather than silently producing empty buffers.

    Only *emitting* tables (per :func:`table_is_emitting` — emit AND ≥1
    selected column) get a buffer, matching exactly the set of parquets
    the build writes and validity demands. *stats*, when provided, counts
    every array element dropped at an emitting table's depth because its
    shape mismatched that table (W2 item 2.7); cache builds and direct runtime
    materialisation both pass one through :func:`_shred_data_file`.
    """
    progress = _ShredExecutionProgress.current()
    progress.checkpoint("json_shred_before_rows")
    table_specs = _emitting_table_specs(v2_config) if _table_specs is None else _table_specs

    # Each table's POSITION is its full ``(key, is_array)`` segment tuple: the
    # array hops set its relational depth, the object hops only LOCATE it. A
    # column spec is (name, leaf, type_token, source_depth); source_depth is the
    # ARRAY depth at which the column's value lives — the table's own array
    # depth for a normal column, or a SHALLOWER array depth for an ancestor
    # column (W1), filled into every descendant row at emission (walk-time
    # distribution, never a post-shred join). validate_v2_schema (above) has
    # guaranteed source_depth is the table's depth or a proper-ancestor prefix.
    emit_tables = [(spec.label, spec.segments, list(spec.columns)) for spec in table_specs]

    # Group tables by their full-segment position — the place the walk emits.
    #
    # Everything constant for a (position, table) pair is derived ONCE here
    # rather than per emitted row: the leaf path pre-split into hops, whether
    # the leaf is the scalar sentinel, and whether the table is a scalar child
    # table. On a large file the walk runs these millions of times, and they
    # depend only on the spec and the position's array depth (fixed, since the
    # position is the key) — never on the record being walked.
    tables_by_pos: dict[tuple[PathSeg, ...], list[tuple[str, bool, list[_PreparedCol]]]] = {}
    for label, segments, col_specs in emit_tables:
        pos_depth = array_depth(segments)
        prepared: list[_PreparedCol] = [
            (name, leaf, _leaf_parts(leaf), type_token, source_depth, leaf == _SCALAR_VALUE_LEAF)
            for name, leaf, type_token, source_depth in col_specs
        ]
        is_scalar_table = any(
            is_scalar_leaf and source_depth == pos_depth
            for _n, _l, _p, _t, source_depth, is_scalar_leaf in prepared
        )
        tables_by_pos.setdefault(segments, []).append((label, is_scalar_table, prepared))

    # Descents: at each position, the (object-prefix, array-key) hops to reach a
    # child array, with the resulting child position. Object hops between arrays
    # locate the array without advancing depth; the parent array element is the
    # ancestor for the child level. Intermediate non-table positions still get
    # their descents registered so a deeper table is reachable.
    _Descent = tuple[tuple[str, ...], str, tuple[PathSeg, ...]]  # noqa: N806 (a type alias, conventionally PascalCase)
    descents_by_pos: dict[tuple[PathSeg, ...], set[_Descent]] = {}
    for _label, segments, _cols in emit_tables:
        parent_pos: tuple[PathSeg, ...] = ()
        obj_prefix: list[str] = []
        for j, (key, is_array) in enumerate(segments):
            if is_array:
                child_pos = tuple(segments[: j + 1])
                descents_by_pos.setdefault(parent_pos, set()).add(
                    (tuple(obj_prefix), key, child_pos)
                )
                parent_pos = child_pos
                obj_prefix = []
            else:
                obj_prefix.append(key)

    # The public path retains its historic per-table buffers.  Direct runtime
    # loading supplies a sink, so the walk instead hands each row to its bounded
    # spill bundle immediately and does not accumulate file-sized Python lists.
    buffers: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in emit_tables}

    def _deliver_row(label: str, row: dict[str, Any]) -> None:
        if _emitted_counts is not None:
            _emitted_counts[label] = _emitted_counts.get(label, 0) + 1
        if _row_sink is None:
            buffers[label].append(row)
        else:
            _row_sink(label, row)

    def _count_row_skip(label: str) -> None:
        if stats is not None:
            stats.count_row_skip(label)

    def _emit_row(
        col_specs: list[_PreparedCol],
        value: Any,
        ancestors: tuple[Any, ...],
        depth: int,
    ) -> dict[str, Any]:
        """Build one output row. Each column's value is sourced at its own
        depth: the current node when ``source_depth == depth``, else the
        ancestor dict carried at that shallower depth — the same value
        distributed across every descendant row (W1)."""
        row: dict[str, Any] = {}
        for col_name, leaf, parts, type_token, src_depth, is_scalar_leaf in col_specs:
            src = value if src_depth == depth else ancestors[src_depth]  # pragma: no mutate
            resolved = _resolve_leaf(src, leaf, parts)
            if is_scalar_leaf or (type_token == "str" and not isinstance(resolved, (dict, list))):
                resolved = _coerce_scalar(resolved, type_token)
            row[col_name] = resolved
        progress.advance("json_shred_rows")
        return row

    def _emit_at(pos: tuple[PathSeg, ...], record: Any, ancestors: tuple[Any, ...]) -> None:
        # Process one element located at ``pos`` (a root or array element):
        # emit rows for the tables at ``pos`` and descend into child arrays.
        # ``ancestors[d]`` is the array element at array-depth ``d`` enclosing
        # this one, so ``len(ancestors) == array_depth(pos)``; a row pulls an
        # ancestor (W1) column's value from the right enclosing element.
        depth = array_depth(pos)
        is_dict = isinstance(record, dict)
        is_scalar = not isinstance(record, (dict, list))

        # A scalar child table (single ``$value`` column) takes only scalar
        # elements; an object table takes only dict records. Skip the mismatched
        # shape — but COUNT it (W2 item 2.7): a mixed array loses that element's
        # row for this table, and the loss must be surfaced, never silent.
        for label, is_scalar_table, col_specs in tables_by_pos.get(pos, []):
            shape_matches = is_scalar if is_scalar_table else is_dict
            if not shape_matches:
                _count_row_skip(label)
                continue
            _deliver_row(label, _emit_row(col_specs, record, ancestors, depth))

        if not is_dict:
            return

        # Descend to each child array, navigating any 1-1 object hops that
        # locate it. The object hops don't advance depth; this dict is the
        # ancestor element for the child array's level.
        child_ancestors = ancestors + (record,)
        for obj_prefix, array_key, child_pos in descents_by_pos.get(pos, ()):
            container: Any = record
            for okey in obj_prefix:
                container = container.get(okey) if isinstance(container, dict) else None
            arr = container.get(array_key) if isinstance(container, dict) else None
            _walk_array(arr, child_pos, child_ancestors)

    def _walk_array(arr: Any, pos: tuple[PathSeg, ...], ancestors: tuple[Any, ...]) -> None:
        # Iterate the array at ``pos``, emitting a row per element. A missing
        # key or non-array value yields nothing.
        if not isinstance(arr, list):
            return
        depth = array_depth(pos)
        for item in arr:
            if item is None:
                # A null *element* is a real value for a scalar child table (its
                # $value resolves to None; ancestor columns still distribute), a
                # non-record for an object table (counted as a dropped row).
                for label, is_scalar_table, col_specs in tables_by_pos.get(pos, []):
                    if is_scalar_table:
                        _deliver_row(label, _emit_row(col_specs, None, ancestors, depth))
                    else:
                        _count_row_skip(label)
                continue
            _emit_at(pos, item, ancestors)

    for record in records:
        _emit_at((), record, ())
        progress.advance("json_shred_rows")

    progress.checkpoint("json_shred_after_rows")
    return buffers


def _assert_root_conservation(
    table_specs: tuple[_EmittingTableSpec, ...],
    buffers: Mapping[str, Sequence[object]],
    skip_stats: ShredSkipStats,
    record_count: int,
    *,
    location: str = "",
    emitted_counts: Mapping[str, int] | None = None,
) -> None:
    """Assert that every root input record was emitted or explicitly skipped."""
    for table_spec in table_specs:
        if table_spec.segments:
            continue
        emitted = (
            emitted_counts.get(table_spec.label, 0)
            if emitted_counts is not None
            else len(buffers.get(table_spec.label, ()))
        )
        skipped = skip_stats.skipped_rows_by_table.get(table_spec.label, 0)
        if emitted + skipped != record_count:
            raise RuntimeError(
                "json shred conservation violation for root table "
                f"{table_spec.label!r}{location}: {emitted} emitted + {skipped} skipped "
                f"!= {record_count} records read — a row was lost or "
                "duplicated without accounting",
            )


# ---------------------------------------------------------------------------
# Parallel JSON processing (newline-delimited sources only)
#
# Shredding is a per-record walk: ancestor values are distributed at walk time
# and no state crosses records (``row_id_column`` names an EXISTING data column,
# never a generated counter). Inference records only mergeable path/type
# evidence. The build preserves row order and skip/conservation accounting;
# inference preserves first-observation order and applies associative widening.
#
# Chunk size and worker count are deliberately separate knobs. Decoded records
# cost several times their JSON size as Python objects, so chunk size bounds
# peak memory (one chunk resident per worker) while worker count bounds
# parallelism. Sizing chunks by ``file_size / n_workers`` instead would make
# memory grow with the file and OOM on exactly the large inputs this exists for.
#
# Only newline-delimited sources are split: a line boundary is findable without
# parsing. A root JSON array would need a serial byte-level scan to locate
# element boundaries, which costs about what it saves; XML is not delimited at
# all. Both keep the serial path.
# ---------------------------------------------------------------------------


# Below this, process startup (plus build-only part-file round-trips) costs more
# than the serial walk saves.
_PARALLEL_MIN_BYTES = 64 * 1024 * 1024  # pragma: no mutate - performance tuning knob
# Target bytes of source JSON per chunk — the memory knob (see above).
_PARALLEL_CHUNK_BYTES = 64 * 1024 * 1024  # pragma: no mutate - performance tuning knob
# Workers beyond this show little gain and multiply peak memory.
_PARALLEL_MAX_WORKERS = 8  # pragma: no mutate - performance tuning knob


def _parallel_worker_count(chunk_count: int) -> int:
    """Workers to run for *chunk_count* chunks — never more than there is work."""
    cpu = os.cpu_count() or 1
    return max(1, min(_PARALLEL_MAX_WORKERS, cpu - 1, chunk_count))


def _jsonl_byte_ranges(data_path: Path, chunk_bytes: int) -> list[tuple[int, int]]:
    """Split a newline-delimited file into ``[start, end)`` byte ranges.

    Each boundary is advanced to just past the next newline so no range splits
    a record; consecutive ranges therefore tile the file exactly, with no gap
    and no overlap. Returned in file order — the order rows must keep.
    """
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    size = data_path.stat().st_size
    if size == 0:
        return []
    if size <= chunk_bytes:
        return [(0, size)]

    bounds = [0]
    with data_path.open("rb") as f:
        target = chunk_bytes
        while target < size:
            f.seek(target)
            f.readline()  # discard the partial line; the next one starts a record
            pos = f.tell()
            if pos >= size:
                break
            if pos > bounds[-1]:
                bounds.append(pos)
            target = pos + chunk_bytes
    bounds.append(size)
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def _iter_range_records(
    data_path: Path,
    start: int,
    end: int,
    stats: ShredSkipStats | None = None,  # pragma: no mutate - type declaration
) -> Iterator[dict[str, Any]]:
    """Yield records from ``[start, end)`` of a newline-delimited file.

    Mirrors the JSONL arm of :func:`_iter_records` exactly — blank lines are
    formatting and never counted, and a non-object line is recorded through
    optional *stats* — but reads bytes so a range can be seeked to directly.
    ``orjson`` validates UTF-8 itself, so decoding stays inside the JSON parse.
    """
    with data_path.open("rb") as f:
        f.seek(start)
        remaining = end - start
        for raw_line in f:
            if remaining <= 0:
                break
            remaining -= len(raw_line)
            stripped = raw_line.strip()
            if not stripped:
                continue
            obj = orjson.loads(stripped)
            if isinstance(obj, dict):
                yield obj
            elif stats is not None:
                stats.count_record_skip()


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _ChunkFailure:
    """Pickle-safe evidence needed to reconstruct a worker exception."""

    type_name: str
    module: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    doc: str | None = None
    pos: int | None = None
    errno: int | None = None
    strerror: str | None = None
    filename: Any = None
    winerror: int | None = None
    filename2: Any = None


def _failure_from_exception(exc: Exception) -> _ChunkFailure:
    """Capture only the stable, pickle-safe evidence for a worker failure."""
    if isinstance(exc, ApiInputSchemaError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=exc.message,
            context=dict(exc.context),
        )
    if isinstance(exc, orjson.JSONDecodeError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=exc.msg,
            doc=exc.doc,
            pos=exc.pos,
        )
    if isinstance(exc, OSError):
        return _ChunkFailure(
            type_name=type(exc).__name__,
            module=type(exc).__module__,
            message=str(exc),
            errno=exc.errno,
            strerror=exc.strerror,
            filename=exc.filename,
            winerror=getattr(exc, "winerror", None),
            filename2=exc.filename2,
        )
    return _ChunkFailure(
        type_name=type(exc).__name__,
        module=type(exc).__module__,
        message=str(exc),
    )


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _ChunkResult:
    """One chunk's contribution, as returned across the process boundary."""

    index: int
    record_count: int
    skipped_records: int
    skipped_rows_by_table: dict[str, int]
    row_counts: dict[str, int]
    # label -> bounded Parquet part path. Rows stay on disk: piping millions of rows
    # back through the pool's result channel would cost more than the shred.
    part_paths: dict[str, str]
    failure: _ChunkFailure | None = None


def _shred_chunk(
    args: tuple[str, int, int, int, dict[str, Any], str],
) -> _ChunkResult:
    """Shred one byte range into bounded Parquet row-group parts.

    Module-level and argument-driven so it survives ``spawn`` pickling on
    Windows. Ordinary failures return structured evidence for the parent to
    re-raise; process-control ``BaseException`` subclasses deliberately escape.
    """
    data_path_s, start, end, index, v2_config, tmp_dir_s = args
    data_path = Path(data_path_s)
    tmp_dir = Path(tmp_dir_s)
    writer: _BoundedParquetRowGroupWriter | None = None
    try:
        table_specs = _emitting_table_specs(v2_config)
        stats = ShredSkipStats()
        record_count = 0
        emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
        writer = _BoundedParquetRowGroupWriter(
            tmp_dir,
            table_specs,
            filename_suffix=f".{index:06d}.part",
        )

        def _counted() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _iter_range_records(data_path, start, end, stats):
                record_count += 1
                yield record

        shred_to_buffers(
            _counted(),
            v2_config,
            stats=stats,
            _table_specs=table_specs,
            _row_sink=writer.emit,
            _emitted_counts=emitted_counts,
        )

        # Ranges tile the file exactly, so holding conservation on every chunk
        # holds it on the whole file and localises a violation to its range.
        _assert_root_conservation(
            table_specs,
            {},
            stats,
            record_count,
            location=f" in byte range [{start}, {end})",
            emitted_counts=emitted_counts,
        )
        writer.flush()
        writer.close()

        return _ChunkResult(
            index=index,
            record_count=record_count,
            skipped_records=stats.skipped_records,
            skipped_rows_by_table=dict(stats.skipped_rows_by_table),
            row_counts=dict(writer.row_counts),
            part_paths={label: str(path) for label, path in writer.paths.items()},
        )
    except Exception as exc:  # noqa: BLE001 — reported, then re-raised in the parent
        if writer is not None:
            try:
                writer.close()
            except BaseException as cleanup_exc:
                exc.add_note(f"bounded chunk writer cleanup failed: {cleanup_exc}")
        return _ChunkResult(
            index=index,
            record_count=0,
            skipped_records=0,
            skipped_rows_by_table={},
            row_counts={},
            part_paths={},
            failure=_failure_from_exception(exc),
        )


def _raise_worker_failure(failure: _ChunkFailure) -> NoReturn:
    """Reconstruct one ordinary worker failure in the parent process."""
    if failure.type_name == "ApiInputSchemaError":
        raise ApiInputSchemaError(failure.message, **failure.context)
    if failure.type_name == "JSONDecodeError":
        raise orjson.JSONDecodeError(failure.message, failure.doc or "", failure.pos or 0)

    os_error_types: dict[str, type[OSError]] = {
        "OSError": OSError,
        "BlockingIOError": BlockingIOError,
        "ChildProcessError": ChildProcessError,
        "ConnectionError": ConnectionError,
        "BrokenPipeError": BrokenPipeError,
        "ConnectionAbortedError": ConnectionAbortedError,
        "ConnectionRefusedError": ConnectionRefusedError,
        "ConnectionResetError": ConnectionResetError,
        "FileExistsError": FileExistsError,
        "FileNotFoundError": FileNotFoundError,
        "InterruptedError": InterruptedError,
        "IsADirectoryError": IsADirectoryError,
        "NotADirectoryError": NotADirectoryError,
        "PermissionError": PermissionError,
        "ProcessLookupError": ProcessLookupError,
        "TimeoutError": TimeoutError,
    }
    os_error_type = os_error_types.get(failure.type_name)
    if failure.module == "builtins" and os_error_type is not None:
        if failure.errno is None:
            raise os_error_type(failure.message)
        args: list[Any] = [failure.errno, failure.strerror]
        if failure.filename is not None:
            args.append(failure.filename)
            if failure.winerror is not None or failure.filename2 is not None:
                args.append(failure.winerror)
                if failure.filename2 is not None:
                    args.append(failure.filename2)
        raise os_error_type(*args)

    builtin_error_types: dict[str, type[Exception]] = {
        "MemoryError": MemoryError,
        "RuntimeError": RuntimeError,
        "ValueError": ValueError,
    }
    builtin_error_type = builtin_error_types.get(failure.type_name)
    if failure.module == "builtins" and builtin_error_type is not None:
        raise builtin_error_type(failure.message)

    qualified_type = f"{failure.module}.{failure.type_name}"
    raise RuntimeError(
        f"parallel json shred worker failed with {qualified_type}: {failure.message}"
    )


def _raise_chunk_error(result: _ChunkResult) -> NoReturn:
    """Re-raise a shred worker failure without pickling arbitrary exceptions."""
    if result.failure is None:
        raise RuntimeError("parallel json shred chunk has no recorded failure")
    _raise_worker_failure(result.failure)


def _should_shred_in_parallel(data_path: Path) -> bool:
    """True when splitting *data_path* is both possible and worth it."""
    if data_path.suffix.lower() not in (".jsonl", ".ndjson"):
        return False
    if current_execution_context() is not None and current_native_memory_backend() not in {
        "cgroup",
        "windows_job",
    }:
        return False
    try:
        return data_path.stat().st_size >= _PARALLEL_MIN_BYTES
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Frame construction and write
# ---------------------------------------------------------------------------


# Values are Polars DataType *classes*, not instances. Polars's Schema /
# DataFrame constructors accept either; we keep the classes here so the
# table is constant-folded and cheap to look up.
_POLARS_TYPE_MAP: dict[ColumnType, type[pl.DataType]] = {
    "int": pl.Int64,
    "float": pl.Float64,
    "str": pl.String,
    "bool": pl.Boolean,
    "date": pl.Date,
}


def _declared_frame_schema(table_spec: _EmittingTableSpec) -> pl.Schema:
    """Return the exact selected-column schema a cache frame must expose."""
    return pl.Schema(
        {
            name: _POLARS_TYPE_MAP[cast(ColumnType, type_token)]
            for name, _leaf, type_token, _depth in table_spec.columns
        },
    )


@dataclass(frozen=True)
class _CacheProbeFailure:
    reason: str
    label: str | None = None
    expected_schema: pl.Schema | None = None
    actual_schema: pl.Schema | None = None


def _cache_manifest_structure_failure(
    meta: dict[str, Any],
    *,  # pragma: no mutate
    expected_labels: tuple[str, ...] | None = None,  # pragma: no mutate
) -> _CacheProbeFailure | None:  # pragma: no mutate
    """Validate signed table entries and their derived parquet names.

    Manifest ``parquet`` values are checked for consistency but never trusted
    as paths: every artifact name is derived from its validated table label.
    Missing, extra, duplicate, or unsigned entries make the candidate
    unusable. Artifact bytes are deliberately checked by the caller: runtime
    probes must verify the exact payload they subsequently hand to Polars.
    """
    raw_tables = meta.get("tables")
    if not isinstance(raw_tables, list):
        return _CacheProbeFailure("malformed_manifest")

    entries_by_label: dict[str, dict[str, Any]] = {}
    seen_casefolded: set[str] = set()
    for raw_entry in raw_tables:
        if not isinstance(raw_entry, dict):
            return _CacheProbeFailure("malformed_manifest")
        label = raw_entry.get("label")
        if not isinstance(label, str) or not label:
            return _CacheProbeFailure("malformed_manifest")
        folded = label.casefold()
        if folded in seen_casefolded:
            return _CacheProbeFailure("duplicate_manifest_table", label=label)
        seen_casefolded.add(folded)
        entries_by_label[label] = raw_entry

    if expected_labels is not None:
        expected_set = set(expected_labels)
        actual_set = set(entries_by_label)
        if actual_set != expected_set:
            missing = next((label for label in expected_labels if label not in actual_set), None)
            extra = next((label for label in entries_by_label if label not in expected_set), None)
            return _CacheProbeFailure("manifest_table_mismatch", label=missing or extra)

    for label, entry in entries_by_label.items():
        signature = entry.get("content_signature")
        signature_parts = _content_signature_parts(signature)
        if signature_parts is None:
            return _CacheProbeFailure("missing_content_signature", label=label)
        filename = f"{_sanitise_label(label)}.parquet"
        if entry.get("parquet") != filename:
            return _CacheProbeFailure("manifest_parquet_name_mismatch", label=label)
    return None


def _cache_manifest_failure(
    cache_dir: Path,
    meta: dict[str, Any],
    *,  # pragma: no mutate
    expected_labels: tuple[str, ...] | None = None,  # pragma: no mutate
) -> _CacheProbeFailure | None:  # pragma: no mutate
    """Validate one manifest and every path-backed artifact it signs."""
    structure_failure = _cache_manifest_structure_failure(
        meta,
        expected_labels=expected_labels,
    )
    if structure_failure is not None:
        return structure_failure

    for entry in meta["tables"]:
        label = entry["label"]
        signature = entry["content_signature"]
        signature_parts = _content_signature_parts(signature)
        assert signature_parts is not None
        parquet_path = cache_dir / f"{_sanitise_label(label)}.parquet"
        if not parquet_path.exists():
            return _CacheProbeFailure("missing_frame", label=label)
        if not _file_content_matches(signature, parquet_path):
            return _CacheProbeFailure("content_signature_mismatch", label=label)
    return None


def _cache_manifest_files_match(cache_dir: Path, meta: dict[str, Any]) -> bool:
    """Return whether a self-contained cache manifest matches its artifacts."""
    return _cache_manifest_failure(cache_dir, meta) is None


def _cache_bundle_failure_in_place(
    cache_dir: Path,
    table_specs: tuple[_EmittingTableSpec, ...],
    meta: dict[str, Any],
) -> _CacheProbeFailure | None:
    """Validate a generation while an external publication lock makes it stable."""
    manifest_failure = _cache_manifest_failure(
        cache_dir,
        meta,
        expected_labels=tuple(spec.label for spec in table_specs),
    )
    if manifest_failure is not None:
        return manifest_failure
    for table_spec in table_specs:
        parquet_path = cache_dir / f"{_sanitise_label(table_spec.label)}.parquet"
        path_stat = parquet_path.lstat()
        if (
            not stat_module.S_ISREG(path_stat.st_mode)
            or stat_module.S_ISLNK(path_stat.st_mode)
            or _is_reparse_point(path_stat)
        ):
            return _CacheProbeFailure("non_plain_frame", label=table_spec.label)
        expected_schema = _declared_frame_schema(table_spec)
        try:
            actual_schema = pl.scan_parquet(parquet_path).collect_schema()
        except (OSError, pl.exceptions.PolarsError):
            return _CacheProbeFailure("unreadable_frame", label=table_spec.label)
        if dict(actual_schema.items()) != dict(expected_schema.items()):
            return _CacheProbeFailure(
                "schema_mismatch",
                label=table_spec.label,
                expected_schema=expected_schema,
                actual_schema=actual_schema,
            )
    return None


def _cache_is_valid_under_external_lock(
    cache_dir: Path,
    v2_config: dict[str, Any],
    *,
    data_path: Path,
    data_file_signature: Mapping[str, Any],
) -> bool:
    meta = _read_per_port_cache_meta_unlocked(cache_dir)
    if meta is None or not _cache_meta_matches_config_and_source(
        meta,
        v2_config,
        data_path=data_path,
        data_file_signature=data_file_signature,
    ):
        return False
    table_specs = _emitting_table_specs(v2_config)
    return _cache_bundle_failure_in_place(cache_dir, table_specs, meta) is None


def _probe_cache_bundle(
    cache_dir: Path,
    table_specs: tuple[_EmittingTableSpec, ...],
    meta: dict[str, Any],
    *,
    complete_table_specs: tuple[_EmittingTableSpec, ...] | None = None,
    retain_snapshots: bool,
) -> tuple[dict[str, pl.LazyFrame], _CacheProbeFailure | None]:  # pragma: no mutate
    """Load cache frames whose name→dtype mappings match current specs.

    Physical parquet column order is deliberately irrelevant to the schema
    fingerprint. Each accepted lazy frame is projected into the current editor
    order, preserving that invariant without allowing missing/extra/renamed or
    differently typed columns through the fast path.

    Each requested parquet is pinned to a private file-backed snapshot before
    its signature is verified in bounded chunks. Polars receives that stable
    path, so native Parquet projection remains lazy and an already-returned plan
    is independent of later rebuilds, mirrors, or explicit cache deletion. The
    complete compressed payload is never retained in Python memory.
    """
    complete_specs = complete_table_specs or table_specs
    manifest_failure = _cache_manifest_structure_failure(
        meta,
        expected_labels=tuple(spec.label for spec in complete_specs),
    )
    if manifest_failure is not None:
        return {}, manifest_failure

    bundle: dict[str, pl.LazyFrame] = {}
    transient_snapshots: list[Path] = []
    manifest_entries = {entry["label"]: entry for entry in meta["tables"]}
    complete_specs_by_label = {spec.label: spec for spec in complete_specs}
    try:
        for table_spec in table_specs:
            complete_spec = complete_specs_by_label[table_spec.label]
            expected_schema = _declared_frame_schema(complete_spec)
            entry = manifest_entries[table_spec.label]
            signature_parts = _content_signature_parts(entry["content_signature"])
            assert signature_parts is not None
            parquet_path = cache_dir / f"{_sanitise_label(table_spec.label)}.parquet"
            try:
                snapshot_path = _snapshot_cache_artifact(
                    cache_dir,
                    parquet_path,
                    entry["content_signature"],
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "json_cache_snapshot_source_missing",
                    cache_dir=str(cache_dir),
                    parquet_path=str(parquet_path),
                    error=str(exc),
                )
                return bundle, _CacheProbeFailure(
                    "missing_frame",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                )
            if snapshot_path is None:
                return bundle, _CacheProbeFailure(
                    "content_signature_mismatch",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                )
            transient_snapshots.append(snapshot_path)
            frame = pl.scan_parquet(snapshot_path)
            actual_schema = frame.collect_schema()
            if dict(actual_schema.items()) != dict(expected_schema.items()):
                return bundle, _CacheProbeFailure(
                    "schema_mismatch",
                    label=table_spec.label,
                    expected_schema=expected_schema,
                    actual_schema=actual_schema,
                )
            bundle[table_spec.label] = frame.select(_declared_frame_schema(table_spec).names())
        if retain_snapshots:
            while transient_snapshots:
                _retain_runtime_snapshot(transient_snapshots.pop())
        return bundle, None
    finally:
        for snapshot_path in transient_snapshots:
            _release_runtime_snapshot(snapshot_path)


def _buffer_to_frame(
    rows: list[dict[str, Any]],
    col_specs: list[_LeafSpec],
) -> pl.DataFrame:
    """Turn a row buffer into a typed Polars DataFrame.

    Builds a per-column accumulator from the rows (preserves the
    declared column order). Empty buffers produce an empty DataFrame
    with the right schema so downstream readers see a consistent shape.
    Missing column values are ``None``.
    """
    # Build each column as a strictly-typed Series so a value that doesn't
    # match the declared type fails LOUD and SPECIFIC — naming the offending
    # column — rather than as an opaque 500. ``col_type`` is `str` at the
    # call site (from `_LeafSpec`); validate_v2_schema (B1) has already
    # guaranteed it's one of the five tokens, so the map lookup can't miss.
    progress = _ShredExecutionProgress.current()
    progress.checkpoint("json_shred_frame_before")
    series_list: list[pl.Series] = []
    for col_name, _leaf, col_type in col_specs:
        dtype = _POLARS_TYPE_MAP[cast(ColumnType, col_type)]
        if progress.execution_context is None:
            values = [row.get(col_name) for row in rows]
        else:
            values = []
            for row in rows:
                values.append(row.get(col_name))
                progress.advance("json_shred_frame_values")
        # Polars strict-builds a bool into an int/float column SILENTLY
        # (True → 1/1.0), which would hide a genuine type mismatch — reject it
        # loudly instead. (bool is a subclass of int, so the strict build won't
        # raise on its own here.)
        if col_type in ("int", "float") and any(isinstance(v, bool) for v in values):
            raise ApiInputSchemaError(
                f"column {col_name!r} is declared {col_type!r} but contains "
                "boolean values (Polars would silently coerce them to 0/1); "
                "change the column's type or fix the source data",
                column=col_name,
                declared_type=col_type,
            )
        # Same guard family for dates (W2 item 2.8): Polars strict-builds a
        # raw JSON int/bool into a Date column SILENTLY as a days-since-epoch
        # offset (2024 → 1975-07-18, True → 1970-01-02) — a garbage date from
        # a successful "strict" build. Reject loudly instead. ISO-8601 strings
        # parse correctly and floats already fail loud in the strict build.
        if col_type == "date" and any(isinstance(v, int) for v in values):
            raise ApiInputSchemaError(
                f"column {col_name!r} is declared 'date' but contains raw JSON "
                "numbers/booleans (Polars would silently reinterpret them as "
                "days since 1970-01-01, e.g. 2024 becomes 1975-07-18); use "
                'ISO-8601 date strings (e.g. "2024-01-15") or change the '
                "column's type",
                column=col_name,
                declared_type=col_type,
            )
        try:
            series_list.append(pl.Series(col_name, values, dtype=dtype, strict=True))
        except (pl.exceptions.PolarsError, TypeError, OverflowError, ValueError) as exc:
            raise ApiInputSchemaError(
                f"column {col_name!r} has values that don't match its declared "
                f"type {col_type!r}; re-infer the schema or change the column's "
                "type to match the data",
                column=col_name,
                declared_type=col_type,
            ) from exc
    frame = pl.DataFrame(series_list)
    progress.checkpoint("json_shred_frame_after")
    return frame


def _cleanup_direct_spill_dirs() -> None:
    """Remove direct-spill bundles owned by this process only."""
    with _DIRECT_SPILL_LOCK:
        if os.getpid() != _DIRECT_SPILL_PROCESS_ID:
            return
        spill_dirs = tuple(_DIRECT_SPILL_DIRS)
        _DIRECT_SPILL_DIRS.clear()
    for spill_dir in spill_dirs:
        try:
            shutil.rmtree(spill_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "json_direct_spill_cleanup_failed",
                path=str(spill_dir),
                error=repr(exc),
            )
    for parent in {spill_dir.parent for spill_dir in spill_dirs}:
        try:
            shutil.rmtree(parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "json_direct_spill_owner_cleanup_failed",
                path=str(parent),
                error=repr(exc),
            )


def _new_direct_spill_dir(cache_dir: Path) -> Path:
    """Create a private direct-runtime bundle outside both cache layers."""
    global _DIRECT_SPILL_ATEXIT_REGISTERED
    global _DIRECT_SPILL_PROCESS_ID, _DIRECT_SPILL_PROCESS_TOKEN, _DIRECT_SPILL_LOCK
    if os.getpid() != _DIRECT_SPILL_PROCESS_ID:
        # A child must forget inherited ownership before it can register its
        # own cleanup; its atexit callback must never remove parent spills.
        _DIRECT_SPILL_PROCESS_ID = os.getpid()
        _DIRECT_SPILL_PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex}"
        _DIRECT_SPILL_LOCK = threading.Lock()
        _DIRECT_SPILL_DIRS.clear()
    cache_root = _runtime_storage_root_for_cache(cache_dir)
    with _DIRECT_SPILL_LOCK:
        owner_dir = cache_root / _DIRECT_SPILL_DIRNAME / _DIRECT_SPILL_PROCESS_TOKEN
        spill_dir = owner_dir / uuid.uuid4().hex
        try:
            with _runtime_disk_budget_transaction(cache_root):
                owner_dir.mkdir(parents=True, exist_ok=True)
                _ensure_runtime_owner_metadata(owner_dir)
                spill_dir.mkdir(exist_ok=False)
        except BaseException as exc:
            try:
                shutil.rmtree(spill_dir)
            except FileNotFoundError:
                pass
            except OSError as cleanup_exc:
                exc.add_note(f"direct spill staging cleanup failed: {cleanup_exc}")
            try:
                _remove_empty_runtime_owner_dir(owner_dir)
            except OSError as cleanup_exc:
                exc.add_note(f"direct spill owner cleanup failed: {cleanup_exc}")
            raise
        _DIRECT_SPILL_DIRS.add(spill_dir)
        if not _DIRECT_SPILL_ATEXIT_REGISTERED:
            atexit.register(_cleanup_direct_spill_dirs)
            _DIRECT_SPILL_ATEXIT_REGISTERED = True
    return spill_dir


def _release_direct_spill_dir(spill_dir: Path) -> None:
    """Release a managed direct-spill bundle once its execution has ended."""
    with _DIRECT_SPILL_LOCK:
        _DIRECT_SPILL_DIRS.discard(spill_dir)
    try:
        shutil.rmtree(spill_dir)
    except FileNotFoundError:
        pass
    with _DIRECT_SPILL_LOCK:
        owner_dir = spill_dir.parent
        try:
            _remove_empty_runtime_owner_dir(owner_dir)
        except FileNotFoundError:
            pass


class _BoundedParquetRowGroupWriter:
    """Shared aggregate-bounded writer for cache artifacts and runtime spills."""

    def __init__(
        self,
        output_dir: Path,
        table_specs: tuple[_EmittingTableSpec, ...],
        *,
        disk_budget_root: Path | None = None,
        filename_suffix: str = "",
    ) -> None:
        import pyarrow.parquet as pq

        self.output_dir = output_dir
        self.cache_root = disk_budget_root
        self.table_specs = table_specs
        self.max_rows = int_env("HAUTE_JSON_DIRECT_SPILL_MAX_ROWS", _DIRECT_SPILL_MAX_ROWS_DEFAULT)
        self.max_bytes = int_env(
            "HAUTE_JSON_DIRECT_SPILL_MAX_BYTES", _DIRECT_SPILL_MAX_BYTES_DEFAULT
        )
        self.buffers: dict[str, list[dict[str, Any]]] = {spec.label: [] for spec in table_specs}
        self.buffered_rows = 0
        self.buffered_bytes = 0
        self.paths: dict[str, Path] = {}
        self.writers: dict[str, Any] = {}
        self.row_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
        self.schema_frames: dict[str, pl.DataFrame] = {}
        try:
            with self._disk_transaction():
                for spec in table_specs:
                    frame = _buffer_to_frame([], spec.leaf_specs)
                    path = output_dir / (f"{_sanitise_label(spec.label)}{filename_suffix}.parquet")
                    arrow_schema = frame.to_arrow().schema.with_metadata(
                        _per_frame_metadata(spec.label, spec.leaf_specs)
                    )
                    self.schema_frames[spec.label] = frame
                    self.paths[spec.label] = path
                    self.writers[spec.label] = pq.ParquetWriter(
                        path,
                        arrow_schema,
                        compression="zstd",
                    )
        except BaseException as exc:
            try:
                self.close()
            except BaseException as cleanup_exc:
                exc.add_note(f"bounded parquet writer cleanup failed: {cleanup_exc}")
            raise

    def _disk_transaction(self, *, allow_existing_excess: bool = False) -> Any:
        if self.cache_root is None:
            return nullcontext()
        return _runtime_disk_budget_transaction(
            self.cache_root,
            allow_existing_excess=allow_existing_excess,
        )

    def emit(self, label: str, row: dict[str, Any]) -> None:
        if label not in self.buffers:
            raise RuntimeError(f"bounded parquet writer received unknown table {label!r}")
        self.buffers[label].append(row)
        self.row_counts[label] += 1
        self.buffered_rows += 1
        # This is an accounting estimate, not serialisation retained in memory.
        self.buffered_bytes += len(orjson.dumps(row))
        if self.buffered_rows >= self.max_rows or self.buffered_bytes >= self.max_bytes:
            self.flush()

    def flush(self) -> None:
        progress = _ShredExecutionProgress.current()
        progress.checkpoint("json_shred_row_group_before_flush")
        with self._disk_transaction():
            for spec in self.table_specs:
                rows = self.buffers[spec.label]
                if not rows:
                    continue
                frame = _buffer_to_frame(rows, spec.leaf_specs)
                self.writers[spec.label].write_table(frame.to_arrow())
                rows.clear()
        self.buffered_rows = 0
        self.buffered_bytes = 0
        progress.checkpoint("json_shred_row_group_after_flush")

    def write_arrow_table(self, label: str, table: Any) -> None:
        """Append one already-bounded Arrow table, preserving caller order."""
        if label not in self.writers:
            raise RuntimeError(f"bounded parquet writer received unknown table {label!r}")
        if table.num_rows > self.max_rows:
            raise RuntimeError(
                f"bounded parquet part for table {label!r} contains {table.num_rows} rows; "
                f"configured maximum is {self.max_rows}"
            )
        if self.buffered_rows:
            self.flush()
        with self._disk_transaction():
            self.writers[label].write_table(table)
        self.row_counts[label] += table.num_rows

    def close(self) -> None:
        if not self.writers:
            return
        errors: list[BaseException] = []
        try:
            with self._disk_transaction(allow_existing_excess=True):
                for writer in self.writers.values():
                    try:
                        writer.close()
                    except BaseException as exc:
                        errors.append(exc)
        finally:
            self.writers.clear()
        if errors:
            first, *rest = errors
            for error in rest:
                first.add_note(f"additional bounded parquet writer cleanup failure: {error}")
            raise first

    def lazy_bundle(self) -> dict[str, pl.LazyFrame]:
        return {spec.label: pl.scan_parquet(self.paths[spec.label]) for spec in self.table_specs}

    def table_summaries(self) -> list[dict[str, Any]]:
        if self.writers:
            raise RuntimeError("bounded parquet writers must be closed before summarising")
        return [
            _table_summary(
                spec.label,
                self.paths[spec.label],
                self.row_counts[spec.label],
                self.schema_frames[spec.label],
            )
            for spec in self.table_specs
        ]


class _DirectSpillBundle(_BoundedParquetRowGroupWriter):
    """Runtime lifecycle wrapper around the shared bounded row-group writer."""

    def __init__(
        self,
        cache_dir: Path,
        table_specs: tuple[_EmittingTableSpec, ...],
    ) -> None:
        cache_root = _runtime_storage_root_for_cache(cache_dir)
        self.spill_dir = _new_direct_spill_dir(cache_dir)
        try:
            super().__init__(
                self.spill_dir,
                table_specs,
                disk_budget_root=cache_root,
            )
        except BaseException as exc:
            try:
                _release_direct_spill_dir(self.spill_dir)
            except BaseException as cleanup_exc:
                exc.add_note(f"direct spill directory cleanup failed: {cleanup_exc}")
            raise


def _shred_data_file_to_direct_spill(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    cache_dir: Path,
) -> tuple[dict[str, pl.LazyFrame], ShredSkipStats]:
    """Shred one uncached source into a leased, bounded Parquet spill bundle."""
    skip_stats = ShredSkipStats()
    emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
    record_count = 0
    bundle = _DirectSpillBundle(cache_dir, table_specs)
    try:

        def _counted_records() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _iter_records(data_path, stats=skip_stats):
                record_count += 1
                yield record

        shred_to_buffers(
            _counted_records(),
            v2_config,
            stats=skip_stats,
            _table_specs=table_specs,
            _row_sink=bundle.emit,
            _emitted_counts=emitted_counts,
        )
        _assert_root_conservation(
            table_specs,
            {},
            skip_stats,
            record_count,
            emitted_counts=emitted_counts,
        )
        bundle.flush()
        bundle.close()
        direct_bundle = bundle.lazy_bundle()
        execution_context = current_execution_context()
        if execution_context is not None:
            spill_dir = bundle.spill_dir
            try:
                execution_context.add_cleanup(lambda: _release_direct_spill_dir(spill_dir))
            except BaseException:
                _release_direct_spill_dir(bundle.spill_dir)
                raise
        # Unmanaged bundles intentionally stay in _DIRECT_SPILL_DIRS for the
        # process atexit hook: derived LazyFrames have no observable lifetime.
        return direct_bundle, skip_stats
    except BaseException as exc:
        try:
            bundle.close()
        except BaseException as cleanup_exc:
            exc.add_note(f"direct spill writer cleanup failed: {cleanup_exc}")
        try:
            _release_direct_spill_dir(bundle.spill_dir)
        except BaseException as cleanup_exc:
            exc.add_note(f"direct spill directory cleanup failed: {cleanup_exc}")
        raise


def _per_frame_metadata(label: str, col_specs: list[_LeafSpec]) -> dict[bytes, bytes]:
    """Build the parquet-footer key-value metadata describing this frame.

    Each parquet carries its own schema (column name + JSON path + type)
    so the file is self-describing — DUAL_CACHE.md §3.
    """
    payload = {
        "port_label": label,
        "columns": [
            {"name": col_name, "leaf": leaf, "type": col_type}
            for col_name, leaf, col_type in col_specs
        ],
    }
    return {b"haute_per_frame_schema": orjson.dumps(payload)}


def _table_summary(
    label: str,
    parquet_path: Path,
    row_count: int,
    schema_frame: pl.DataFrame,
) -> dict[str, Any]:
    """One ``tables[]`` manifest entry. Column shape comes from an empty frame
    built through :func:`_buffer_to_frame`, so the serial and parallel paths
    report identical dtypes by construction rather than by agreement."""
    return {
        "label": label,
        "parquet": parquet_path.name,
        "row_count": row_count,
        "column_count": schema_frame.width,
        "columns": {name: str(dtype) for name, dtype in schema_frame.schema.items()},
        "content_signature": _file_content_signature(parquet_path),
    }


def _write_tables_streaming(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    tmp_dir: Path,
) -> tuple[list[dict[str, Any]], ShredSkipStats]:
    """Stream one source into bounded staged Parquet row groups."""
    skip_stats = ShredSkipStats()
    emitted_counts: dict[str, int] = {spec.label: 0 for spec in table_specs}
    record_count = 0
    writer = _BoundedParquetRowGroupWriter(tmp_dir, table_specs)
    try:

        def _counted_records() -> Iterator[dict[str, Any]]:
            nonlocal record_count
            for record in _iter_records(data_path, stats=skip_stats):
                record_count += 1
                yield record

        shred_to_buffers(
            _counted_records(),
            v2_config,
            stats=skip_stats,
            _table_specs=table_specs,
            _row_sink=writer.emit,
            _emitted_counts=emitted_counts,
        )
        _assert_root_conservation(
            table_specs,
            {},
            skip_stats,
            record_count,
            emitted_counts=emitted_counts,
        )
        writer.flush()
        writer.close()
        return writer.table_summaries(), skip_stats
    except BaseException as exc:
        try:
            writer.close()
        except BaseException as cleanup_exc:
            exc.add_note(f"bounded cache writer cleanup failed: {cleanup_exc}")
        raise


def _merge_chunk_skip_stats(results: Iterable[_ChunkResult]) -> ShredSkipStats:
    """Combine worker skip evidence without losing counts shared by chunks."""
    combined = ShredSkipStats()
    for result in results:
        combined.skipped_records += result.skipped_records
        for label, count in result.skipped_rows_by_table.items():
            combined.skipped_rows_by_table[label] = (
                combined.skipped_rows_by_table.get(label, 0) + count
            )
    return combined


def _write_tables_in_parallel(
    data_path: Path,
    v2_config: dict[str, Any],
    table_specs: tuple[_EmittingTableSpec, ...],
    tmp_dir: Path,
    ranges: list[tuple[int, int]],
) -> tuple[list[dict[str, Any]], ShredSkipStats]:
    """Shred *ranges* across worker processes, then assemble one parquet each.

    Parts are streamed one row group at a time into the final parquet in chunk
    order, so row order matches the serial shred exactly. Each part is released
    as it is consumed; parent and workers share the same aggregate bounds.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    import pyarrow.parquet as pq

    tasks = [
        (str(data_path), start, end, index, v2_config, str(tmp_dir))
        for index, (start, end) in enumerate(ranges)
    ]
    workers = _parallel_worker_count(len(tasks))
    logger.info(
        "json_shred_parallel_start",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
    )
    started = time.perf_counter()

    # "spawn" explicitly: it is the only start method available on Windows and
    # the only one safe alongside the server's threads elsewhere, so every
    # platform exercises the same picklable-arguments path.
    results: list[_ChunkResult] = []
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    try:
        # ``map`` yields in submission order, which is file order.
        for result in pool.map(_shred_chunk, tasks):
            if result.failure is not None:
                _raise_chunk_error(result)
            results.append(result)
    finally:
        # ``map`` submits every chunk up front, so a plain shutdown would wait
        # for the whole file even when chunk 2 of 200 already failed. Cancelling
        # the queued futures bounds a failed build by the chunks already in
        # flight (at most one per worker) rather than by the file's size.
        pool.shutdown(wait=True, cancel_futures=True)

    skip_stats = _merge_chunk_skip_stats(results)

    writer = _BoundedParquetRowGroupWriter(tmp_dir, table_specs)
    try:
        for spec in table_specs:
            expected_rows = 0
            for result in results:
                part = result.part_paths.get(spec.label)
                if part is None:
                    # Every successful chunk writes one part per emitting table
                    # (worker and parent parse the same config). A missing part
                    # means the two disagree about the table set — publishing a
                    # parquet with silently absent rows would be worse than any
                    # failure, so stop here.
                    raise RuntimeError(
                        f"parallel json shred chunk {result.index} wrote no part "
                        f"for table {spec.label!r} — worker and parent table "
                        "specs diverged",
                    )
                with pq.ParquetFile(part) as part_file:
                    for row_group_index in range(part_file.num_row_groups):
                        part_table = part_file.read_row_group(row_group_index)
                        writer.write_arrow_table(spec.label, part_table)
                        del part_table
                expected_rows += result.row_counts.get(spec.label, 0)
                Path(part).unlink(missing_ok=True)
            if writer.row_counts[spec.label] != expected_rows:
                raise RuntimeError(
                    f"parallel json shred row-count mismatch for table {spec.label!r}: "
                    f"assembled {writer.row_counts[spec.label]} != "
                    f"worker-reported {expected_rows}"
                )
        writer.close()
        summaries = writer.table_summaries()
    except BaseException as exc:
        try:
            writer.close()
        except BaseException as cleanup_exc:
            exc.add_note(f"bounded parallel writer cleanup failed: {cleanup_exc}")
        raise

    logger.info(
        "json_shred_parallel_complete",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
        duration_seconds=round(  # pragma: no mutate - diagnostic timing only
            time.perf_counter() - started,
            3,  # pragma: no mutate
        ),
    )
    return summaries, skip_stats


def _normalised_build_path(path: str | Path) -> Path:
    return Path(os.path.abspath(path))


def _validated_build_staging_dir(cache_dir: Path, staging_dir: str | Path) -> Path:
    staging = _normalised_build_path(staging_dir)
    expected_prefix = f"{cache_dir.name}.build-tmp-"
    suffix = staging.name.removeprefix(expected_prefix)
    if (
        staging.parent != cache_dir.parent
        or not staging.name.startswith(expected_prefix)
        or len(suffix) != 32
        or any(character not in "0123456789abcdef" for character in suffix)
    ):
        raise ValueError("cache staging directory must be an exact private sibling generation")
    _assert_cache_path_ancestors_plain(staging)
    return staging


def _cache_summary(meta: Mapping[str, Any], cache_dir: Path) -> dict[str, Any]:
    return {
        "schema_mode": meta["schema_mode"],
        "schema_fingerprint": meta["schema_fingerprint"],
        "tables": meta["tables"],
        "data_file": meta["data_file"],
        "skipped": meta["skipped"],
        "cache_dir": str(cache_dir),
    }


def _remove_prepared_staging(staging: Path) -> None:
    try:
        _remove_plain_cache_directory(staging)
    except FileNotFoundError:
        return


def prepare_per_port_cache(
    data_path: str | Path,
    v2_config: dict[str, Any],
    cache_dir: str | Path,
    *,
    staging_dir: str | Path,
) -> PreparedPerPortCacheBuild:
    """Materialise and validate a private generation without publishing it.

    A parent must hold :func:`per_port_cache_publication_lock` across this call
    (or across an isolated child invocation of it) and the later commit. The
    explicit staging path lets the parent clean up safely even when the worker
    times out or crashes before returning a manifest.
    """
    dp = _normalised_build_path(data_path)
    cd = _normalised_build_path(cache_dir)
    staging = _validated_build_staging_dir(cd, staging_dir)
    table_specs = _emitting_table_specs(v2_config)
    fingerprint = _v2_fingerprint(v2_config)
    data_file_sig = _data_file_signature(dp, upgrade_legacy_proofs=False)

    if _cache_is_valid_under_external_lock(
        cd,
        v2_config,
        data_path=dp,
        data_file_signature=data_file_sig,
    ):
        existing_meta = _read_per_port_cache_meta_unlocked(cd)
        if existing_meta is None:
            raise RuntimeError("valid cache generation omitted its metadata")
        summary = _cache_summary(existing_meta, cd)
        logger.info(
            "json_shred_build_noop",
            data_path=str(dp),
            cache_dir=str(cd),
            fingerprint=fingerprint[:8],
        )
        return PreparedPerPortCacheBuild(
            data_path=str(dp),
            cache_dir=str(cd),
            staging_dir=None,
            schema_fingerprint=fingerprint,
            data_file_signature=dict(data_file_sig),
            summary=summary,
            no_op=True,
        )

    if staging.exists() or staging.is_symlink():
        raise FileExistsError(f"cache staging generation already exists: {staging}")
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        ranges = (
            _jsonl_byte_ranges(dp, _PARALLEL_CHUNK_BYTES) if _should_shred_in_parallel(dp) else []
        )
        if len(ranges) > 1:
            table_summaries, skip_stats = _write_tables_in_parallel(
                dp, v2_config, table_specs, staging, ranges
            )
        else:
            table_summaries, skip_stats = _write_tables_streaming(
                dp,
                v2_config,
                table_specs,
                staging,
            )
        if _data_file_signature(dp, upgrade_legacy_proofs=False) != data_file_sig:
            raise SourceChangedDuringCacheBuildError(
                f"structured source changed while its cache was built: {dp}"
            )
        meta_payload = {
            "schema_mode": "v2",
            "schema_fingerprint": fingerprint,
            "tables": table_summaries,
            "data_file": data_file_sig,
            "skipped": skip_stats.as_meta(),
        }
        (staging / _META_FILENAME).write_bytes(orjson.dumps(meta_payload))
        failure = _cache_manifest_failure(
            staging,
            meta_payload,
            expected_labels=tuple(spec.label for spec in table_specs),
        )
        if failure is not None:
            raise RuntimeError(f"prepared cache manifest failed validation: {failure.reason}")
    except BaseException as exc:
        try:
            _remove_prepared_staging(staging)
        except BaseException as cleanup_exc:
            exc.add_note(f"cache staging cleanup failed: {cleanup_exc}")
        raise

    return PreparedPerPortCacheBuild(
        data_path=str(dp),
        cache_dir=str(cd),
        staging_dir=str(staging),
        schema_fingerprint=fingerprint,
        data_file_signature=dict(data_file_sig),
        summary=_cache_summary(meta_payload, cd),
    )


def _validate_prepared_cache(
    prepared: PreparedPerPortCacheBuild,
    v2_config: dict[str, Any],
) -> tuple[Path, Path | None, dict[str, Any]]:
    if not isinstance(prepared, PreparedPerPortCacheBuild):
        raise TypeError("prepared must be a PreparedPerPortCacheBuild")
    dp = _normalised_build_path(prepared.data_path)
    cd = _normalised_build_path(prepared.cache_dir)
    expected_fingerprint = _v2_fingerprint(v2_config)
    if prepared.schema_fingerprint != expected_fingerprint:
        raise ValueError("prepared cache schema fingerprint does not match the requested config")
    if _data_file_signature(dp) != prepared.data_file_signature:
        raise SourceChangedDuringCacheBuildError(
            f"structured source changed before its cache could be published: {dp}"
        )
    if prepared.no_op:
        if prepared.staging_dir is not None:
            raise ValueError("no-op cache preparation must not name a staging directory")
        if not _cache_is_valid_under_external_lock(
            cd,
            v2_config,
            data_path=dp,
            data_file_signature=prepared.data_file_signature,
        ):
            raise RuntimeError("the no-op cache generation changed before publication")
        meta = _read_per_port_cache_meta_unlocked(cd)
        if meta is None or prepared.summary != _cache_summary(meta, cd):
            raise RuntimeError("the no-op cache summary does not match the visible generation")
        return cd, None, meta

    if prepared.staging_dir is None:
        raise ValueError("prepared cache generation omitted its staging directory")
    staging = _validated_build_staging_dir(cd, prepared.staging_dir)
    _plain_directory_stat(staging)
    meta = _read_per_port_cache_meta_unlocked(staging)
    expected_meta = {
        key: prepared.summary.get(key)
        for key in ("schema_mode", "schema_fingerprint", "tables", "data_file", "skipped")
    }
    if meta != expected_meta or meta.get("data_file") != prepared.data_file_signature:
        raise RuntimeError("prepared cache metadata does not match its returned manifest")
    table_specs = _emitting_table_specs(v2_config)
    expected_names = {
        _META_FILENAME,
        *(f"{_sanitise_label(spec.label)}.parquet" for spec in table_specs),
    }
    children = tuple(staging.iterdir())
    if {child.name for child in children} != expected_names:
        raise RuntimeError("prepared cache generation contains unexpected or missing artifacts")
    for child in children:
        child_stat = child.lstat()
        if (
            not stat_module.S_ISREG(child_stat.st_mode)
            or stat_module.S_ISLNK(child_stat.st_mode)
            or _is_reparse_point(child_stat)
        ):
            raise RuntimeError(f"prepared cache artifact is not a plain regular file: {child}")
    failure = _cache_bundle_failure_in_place(staging, table_specs, meta)
    if failure is not None:
        raise RuntimeError(f"prepared cache generation failed validation: {failure.reason}")
    return cd, staging, meta


def commit_prepared_per_port_cache(
    prepared: PreparedPerPortCacheBuild,
    v2_config: dict[str, Any],
    *,
    publication_guard: AbstractContextManager[None] | None = None,
) -> dict[str, Any]:
    """Validate and atomically publish a child-prepared cache generation."""
    cd = _normalised_build_path(prepared.cache_dir)
    if not _build_lock_for(cd).owned_by_current_thread():
        raise RuntimeError("cache publication requires the parent-owned build lock")
    cd, staging, meta = _validate_prepared_cache(prepared, v2_config)
    if staging is None:
        with publication_guard or nullcontext():
            return _cache_summary(meta, cd)
    with publication_guard or nullcontext():
        _swap_dir_into_place(staging, cd)
    skipped = meta["skipped"]
    if skipped.get("records", 0) or skipped.get("rows_by_table"):
        logger.warning(
            "json_shred_records_skipped",
            data_path=prepared.data_path,
            cache_dir=str(cd),
            skipped_records=skipped.get("records", 0),
            skipped_rows_by_table=skipped.get("rows_by_table", {}),
        )
    logger.info(
        "json_shred_built",
        data_path=prepared.data_path,
        cache_dir=str(cd),
        table_count=len(meta["tables"]),
        fingerprint=prepared.schema_fingerprint[:8],
    )
    return _cache_summary(meta, cd)


def discard_prepared_per_port_cache(prepared: PreparedPerPortCacheBuild) -> None:
    """Remove only the exact unpublished staging generation named by *prepared*."""
    if prepared.staging_dir is None:
        return
    cd = _normalised_build_path(prepared.cache_dir)
    staging = _validated_build_staging_dir(cd, prepared.staging_dir)
    _remove_prepared_staging(staging)


def discard_per_port_cache_staging(
    cache_dir: str | Path,
    staging_dir: str | Path,
) -> None:
    """Remove one exact parent-selected staging path after a worker failure."""
    cd = _normalised_build_path(cache_dir)
    if not _build_lock_for(cd).owned_by_current_thread():
        raise RuntimeError("cache staging cleanup requires the parent-owned build lock")
    staging = _validated_build_staging_dir(cd, staging_dir)
    _remove_prepared_staging(staging)


def build_per_port_cache(
    data_path: str | Path,
    v2_config: dict[str, Any],
    cache_dir: str | Path,
) -> dict[str, Any]:
    """Build one serialized generation through the shared prepare/commit path."""
    cd = _normalised_build_path(cache_dir)
    with per_port_cache_publication_lock(cd):
        staging = _unique_build_tmp_dir(cd)
        prepared: PreparedPerPortCacheBuild | None = None
        try:
            prepared = prepare_per_port_cache(
                data_path,
                v2_config,
                cd,
                staging_dir=staging,
            )
            return commit_prepared_per_port_cache(prepared, v2_config)
        finally:
            if prepared is not None:
                discard_prepared_per_port_cache(prepared)
            elif staging.exists() or staging.is_symlink():
                _remove_prepared_staging(staging)


def _swap_dir_into_place(tmp_dir: Path, live_dir: Path) -> None:
    """Atomically replace *live_dir* with the fully-built *tmp_dir*.

    Same rename dance as :func:`haute._json_flatten.mirror_cache_to_committed`:
    rename the live dir aside, rename the temp dir in, then best-effort
    remove the old copy. If the second rename fails the old dir is restored
    before re-raising, so the cache is never left missing.
    """
    if live_dir.exists():
        backup = _unique_build_old_dir(live_dir)
        try:
            _rename_dir_with_retry(live_dir, backup)
        except BaseException:  # pragma: no mutate
            shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:  # pragma: no mutate
            try:
                _rename_dir_with_retry(backup, live_dir)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise
        shutil.rmtree(backup, ignore_errors=True)  # pragma: no mutate
    else:
        try:
            _rename_dir_with_retry(tmp_dir, live_dir)
        except BaseException:  # pragma: no mutate
            shutil.rmtree(tmp_dir, ignore_errors=True)  # pragma: no mutate
            raise


def load_per_port_cache(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
) -> dict[str, pl.LazyFrame]:
    """Return the complete signed snapshot bundle selected by ``meta.json``.

    "Emitting" uses the shared :func:`table_is_emitting` predicate (emit and
    at least one selected column), exactly the set the build writes. Every
    label-derived artifact must exist, match its signature, and expose the
    declared schema. Exact verified, generation-pinned paths seed the returned
    file-backed LazyFrames; a missing or mismatched member rejects the whole
    bundle and returns ``{}`` rather than serving a partial generation.

    Callers needing source-file freshness must additionally use
    :func:`is_per_port_cache_valid` or :func:`load_v2_api_source`.
    """
    cd = Path(cache_dir)
    with _build_lock_for(cd):
        table_specs = _emitting_table_specs(v2_config)
        meta = read_per_port_cache_meta(cd)
        if (
            meta is None
            or meta.get("schema_mode") != "v2"
            or meta.get("schema_fingerprint") != _v2_fingerprint(v2_config)
        ):
            return {}
        try:
            bundle, failure = _probe_cache_bundle(
                cd,
                table_specs,
                meta,
                retain_snapshots=True,
            )
        except (OSError, pl.exceptions.PolarsError):
            return {}
        return bundle if failure is None else {}


def _cache_meta_matches_config_and_source(
    meta: dict[str, Any],
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None = None,  # pragma: no mutate
) -> bool:
    """Return whether captured metadata identifies this schema and source."""
    if meta.get("schema_mode") != "v2":
        return False
    try:
        expected_fingerprint = _v2_fingerprint(v2_config)
    except ApiInputSchemaError:
        # Preserve the bool contract for status/save callers that probe a
        # malformed in-memory config. Build and load boundaries validate loud.
        return False
    return meta.get("schema_fingerprint") == expected_fingerprint and _data_file_matches(
        meta.get("data_file"),
        Path(data_path),
        data_file_signature=data_file_signature,
    )


def _read_matching_cache_meta_unlocked(
    cd: Path,
    v2_config: dict[str, Any],
    *,
    data_path: str | Path,
    data_file_signature: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    meta_path = cd / _META_FILENAME
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if not _cache_meta_matches_config_and_source(
        meta,
        v2_config,
        data_path=data_path,
        data_file_signature=data_file_signature,
    ):
        return None
    return meta


def load_v2_api_source(
    data_path: str,
    config: dict[str, Any],
    *,
    port_columns: Mapping[str, frozenset[str] | set[str] | None] | None = None,
) -> dict[str, pl.LazyFrame]:  # pragma: no mutate
    """Load a v2 apiInput as an emit-gated per-port frame bundle.

    The shared frame-bundle loader reached through
    :func:`haute._node_apply.resolve_api_input_from_config` by both executor
    and generated/deploy code, so those paths cannot drift. Validates *config*
    itself so direct callers receive the same typed schema errors as the
    executor and generated module boundaries.

    Behaviour:

    - 0 emit-true tables → ``RuntimeError`` (tick an ``emit`` toggle).
    - emit-true tables but none with a selected column → ``RuntimeError``.
    - prefers a valid, readable, schema-matching ``working/`` parquet cache,
      then ``committed/`` (the deploy / fresh-server case).
    - when neither cache can serve the current schema and source signature,
      shreds JSON, JSONL, or XML directly for this run without writing cache state.
    - 1+ emitting labels → a ``dict[port_label, LazyFrame]`` in schema order.

    Frame resolution uses the shared :func:`table_is_emitting` predicate, so
    an emit-true table with zero selected columns contributes no frame and —
    crucially — no longer wedges validity (W2 item 2.5).
    """
    from haute._json_flatten import _json_cache_dir

    # Parse the complete config before considering a demand. Cache identity and
    # every opened parquet remain governed by this full schema; the demand only
    # controls which ports and columns are materialised for this caller.
    complete_table_specs = _emitting_table_specs(config)
    tables = config["tables"]
    emit_true_tables = [t for t in tables if t.get("emit")]
    if not emit_true_tables:
        raise RuntimeError(
            "API Input has no emitting tables. Open the node, tick the 'emit' "
            "toggle on at least one table, then preview again.",
        )
    emit_labels = [spec.label for spec in complete_table_specs]
    if not emit_labels:
        labels = [t["label"] for t in emit_true_tables]
        raise RuntimeError(
            "API Input has emit-true tables but none has any selected columns. "
            f"Open the node and tick at least one column on the emitting "
            f"table(s): {labels}, then preview again.",
        )

    table_specs = complete_table_specs
    if port_columns is not None:
        if not isinstance(port_columns, Mapping) or not port_columns:
            raise ValueError("port_columns must be a non-empty mapping")
        complete_by_label = {spec.label: spec for spec in complete_table_specs}
        projected_specs: list[_EmittingTableSpec] = []
        for label, requested_columns in port_columns.items():
            if label not in complete_by_label:
                raise ValueError(f"port_columns requests unknown emitting port {label!r}")
            if requested_columns is not None and not isinstance(
                requested_columns,
                (frozenset, set),
            ):
                raise ValueError(
                    f"port_columns[{label!r}] must be None or a frozenset/set",
                )
            complete_spec = complete_by_label[label]
            if requested_columns is None:
                projected_specs.append(complete_spec)
                continue
            if any(not isinstance(column, str) or not column for column in requested_columns):
                raise ValueError(
                    f"port_columns[{label!r}] must contain non-empty string column names",
                )
            available = {name for name, _leaf, _type, _depth in complete_spec.columns}
            missing = set(requested_columns) - available
            if missing:
                raise ValueError(
                    f"port_columns[{label!r}] requests missing declared column(s): "
                    f"{sorted(missing)!r}",
                )
            # Preserve config order, not demand-set iteration order. An empty
            # logical demand is cardinality-only; one physical carrier keeps
            # Polars from collapsing the frame to zero rows.
            physical_columns = (
                set(requested_columns) if requested_columns else {complete_spec.columns[0][0]}
            )
            projected_specs.append(
                _EmittingTableSpec(
                    label=complete_spec.label,
                    segments=complete_spec.segments,
                    columns=tuple(
                        column for column in complete_spec.columns if column[0] in physical_columns
                    ),
                ),
            )
        # Preserve v2 schema order even when the caller supplied its mapping in
        # a different order.
        requested_by_label = {spec.label: spec for spec in projected_specs}
        table_specs = tuple(
            requested_by_label[spec.label]
            for spec in complete_table_specs
            if spec.label in requested_by_label
        )
    # Defer the complete source proof until a cache layer has plausible schema
    # metadata. A cold uncached execution should parse the source once, not hash
    # the whole file first merely to prove that two absent caches are absent.
    expected_fingerprint = _v2_fingerprint(config)
    data_file_sig: dict[str, Any] | None = None
    execution_context = current_execution_context()
    # A valid parquet cache is an optimization, not a runtime prerequisite.
    # Prefer the user's current working cache, then the saved/deployable
    # committed cache. If either disappears between validation and scanning,
    # continue to the next candidate rather than restoring the old hard cache
    # dependency.
    for layer in ("working", "committed"):
        cache_dir = _json_cache_dir(data_path, layer)
        with _build_lock_for(cache_dir):
            candidate_meta = _read_per_port_cache_meta_unlocked(cache_dir)
            plausible_meta = (
                candidate_meta is not None
                and candidate_meta.get("schema_mode") == "v2"
                and candidate_meta.get("schema_fingerprint") == expected_fingerprint
            )
            cache_meta: dict[str, Any] | None = None
            if plausible_meta:
                assert candidate_meta is not None
                if data_file_sig is None:
                    data_file_sig = _data_file_signature(Path(data_path))
                if _cache_meta_matches_config_and_source(
                    candidate_meta,
                    config,
                    data_path=data_path,
                    data_file_signature=data_file_sig,
                ):
                    cache_meta = candidate_meta
            if cache_meta is None:
                if execution_context is not None:
                    reason = (
                        ExecutionCacheProofMissReason.METADATA_SOURCE_MISMATCH
                        if (cache_dir / _META_FILENAME).is_file()
                        else ExecutionCacheProofMissReason.PROOF_UNAVAILABLE
                    )
                    execution_context.record_cache_proof_miss(reason)
                continue
            try:
                bundle, probe_failure = _probe_cache_bundle(
                    cache_dir,
                    table_specs,
                    cache_meta,
                    complete_table_specs=complete_table_specs,
                    retain_snapshots=True,
                )
            except (OSError, pl.exceptions.PolarsError) as exc:
                if execution_context is not None:
                    execution_context.record_cache_proof_miss(
                        ExecutionCacheProofMissReason.UNREADABLE_ARTIFACT
                    )
                logger.warning(
                    "json_shred_cache_candidate_rejected",
                    data_path=data_path,
                    cache_dir=str(cache_dir),
                    layer=layer,
                    reason="unreadable_parquet",
                    error_type=type(exc).__name__,
                )
                continue
            if probe_failure is not None:
                if execution_context is not None:
                    execution_context.record_cache_proof_miss(
                        ExecutionCacheProofMissReason.ARTIFACT_INTEGRITY_SCHEMA_FAILURE
                    )
                logger.warning(
                    "json_shred_cache_candidate_rejected",
                    data_path=data_path,
                    cache_dir=str(cache_dir),
                    layer=layer,
                    reason=probe_failure.reason,
                    label=probe_failure.label,
                    expected_schema=(
                        str(probe_failure.expected_schema)
                        if probe_failure.expected_schema is not None
                        else None
                    ),
                    actual_schema=(
                        str(probe_failure.actual_schema)
                        if probe_failure.actual_schema is not None
                        else None
                    ),
                )
                continue
            if execution_context is not None:
                execution_context.record_cache_proof_hit()
            return {table_spec.label: bundle[table_spec.label] for table_spec in table_specs}

    # Neither cache can serve the current post-schema shape. Shred the source
    # for this execution only; do not write, refresh, or promote cache state.
    direct_bundle, skip_stats = _shred_data_file_to_direct_spill(
        Path(data_path),
        config,
        table_specs,
        _json_cache_dir(data_path, "working"),
    )
    if execution_context is not None:
        execution_context.record_cache_direct_fallback()
    if skip_stats.total:
        logger.warning(
            "json_shred_direct_records_skipped",
            data_path=data_path,
            skipped_records=skip_stats.skipped_records,
            skipped_rows_by_table=skip_stats.skipped_rows_by_table,
        )
    logger.info(
        "json_shred_loaded_direct",
        data_path=data_path,
        table_count=len(table_specs),
    )
    return direct_bundle


def is_per_port_cache_valid(
    cache_dir: str | Path,  # pragma: no mutate
    v2_config: dict[str, Any],
    *,  # pragma: no mutate
    data_path: str | Path,  # pragma: no mutate
    data_file_signature: Mapping[str, Any] | None = None,  # pragma: no mutate
) -> bool:
    """Return whether a complete, readable cache can serve the current input.

    ``meta.json`` must match the v2 schema fingerprint and recorded source-file
    signature (W2 item 2.4). Every emitting table must have exactly one signed
    manifest entry whose derived parquet matches its size/SHA-256, then expose
    the exact declared name-to-Polars-dtype mapping. Physical parquet column
    order does not affect validity; accepted frames are projected into current
    editor order by :func:`_probe_cache_bundle` at load time.

    The data-file check always compares the recorded content hash with an
    observed content proof. An unchanged strong native revision may reuse a
    prior full hash; ``mtime_ns`` alone cannot authorise that reuse, so a
    same-size, same-mtime byte-changing rewrite remains stale (matching
    :func:`_data_file_matches`). The committed-layer deploy fallback still
    survives file copies because the fresh hash matches when only metadata
    moved. Metadata without a recorded source or per-parquet signature is
    invalid.
    """
    cd = Path(cache_dir)
    with _build_lock_for(cd):
        return _is_per_port_cache_valid_unlocked(
            cd,
            v2_config,
            data_path=data_path,
            data_file_signature=data_file_signature,
        )


def _is_per_port_cache_valid_unlocked(
    cache_dir: Path,
    v2_config: dict[str, Any],
    *,
    data_path: str | Path,
    data_file_signature: Mapping[str, Any] | None,
) -> bool:
    try:
        expected_fingerprint = _v2_fingerprint(v2_config)
    except ApiInputSchemaError:
        return False
    cache_meta = _read_per_port_cache_meta_unlocked(cache_dir)
    if (
        cache_meta is None
        or cache_meta.get("schema_mode") != "v2"
        or cache_meta.get("schema_fingerprint") != expected_fingerprint
    ):
        return False
    try:
        signature = (
            _data_file_signature(Path(data_path))
            if data_file_signature is None
            else data_file_signature
        )
    except OSError:
        return False
    if not _cache_meta_matches_config_and_source(
        cache_meta,
        v2_config,
        data_path=data_path,
        data_file_signature=signature,
    ):
        return False
    try:
        table_specs = _emitting_table_specs(v2_config)
        _bundle, probe_failure = _probe_cache_bundle(
            cache_dir,
            table_specs,
            cache_meta,
            retain_snapshots=False,
        )
    except (ApiInputSchemaError, OSError, pl.exceptions.PolarsError):
        return False
    return probe_failure is None


def _infer_type(value: Any) -> str:
    """Infer a v2 column type token from a single JSON scalar."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _widen_type(existing: str | None, new: str) -> str:  # pragma: no mutate
    """Combine two observed type tokens into the narrowest that fits both.

    ``int`` + ``float`` → ``float``; any other disagreement → ``str``. This
    makes inference reflect the WHOLE file (e.g. an ``int`` column whose
    151st row is a float is typed ``float``, not ``int``), so the strict
    parquet build doesn't fail on a value that appears past an early sample.
    """
    if existing is None:
        return new
    if existing == new:
        return existing
    if {existing, new} == {"int", "float"}:  # pragma: no mutate
        return "float"
    return "str"


def _assign_column_names(object_paths: list[tuple[str, ...]]) -> dict[tuple[str, ...], str]:
    """Name flattened object-leaf columns: bare leaf where unique, else qualify.

    The 2026-06-17 ruling: a 1-1 object's scalars flatten into one table. Each
    column's NAME is its bare leaf key when that's unique within the table; on
    collision (e.g. two add-ons each carrying ``selected``) the colliding
    columns take their full underscore-joined object path
    (``breakdown_cover_selected``). A residual pathological clash gets a numeric
    suffix so names stay unique (validate_v2_schema rejects duplicates
    downstream). The column PATH always carries the full address regardless of
    the chosen name.
    """
    bare = {op: op[-1] for op in object_paths}
    bare_counts: dict[str, int] = {}
    for leaf in bare.values():
        bare_counts[leaf] = bare_counts.get(leaf, 0) + 1
    names: dict[tuple[str, ...], str] = {}
    for op in object_paths:
        names[op] = "_".join(op) if bare_counts[bare[op]] > 1 else bare[op]  # pragma: no mutate
    # Deterministic final dedup for any residual collision.
    seen: set[str] = set()
    for op in object_paths:
        nm = names[op]
        if nm in seen:
            i = 2
            while f"{nm}_{i}" in seen:
                i += 1
            nm = f"{nm}_{i}"
            names[op] = nm
        seen.add(nm)
    return names


def _reject_unexpressible_key(key: str) -> None:
    """Fail loud on a source JSON key the v2 path grammar can't address cleanly.

    Inference must not manufacture a column path for any key outside the
    shared ASCII identifier grammar. Two cases receive more specific
    diagnostics because they previously parsed but resolved to the wrong
    value at shred time (W1):

    - ``$value`` collides with the reserved scalar-array sentinel — the shred
      would read it as "the element itself" and never touch the field.
    - a key containing ``.`` is split by the dotted-leaf walker into two
      object hops (``{"a.b": v}`` becomes ``value["a"]["b"]`` → ``None``).

    Every rejected key must be renamed in the source data; there is no
    unambiguous mapping.
    """
    if key == _SCALAR_VALUE_LEAF:
        raise ApiInputSchemaError(
            f"source JSON key '{_SCALAR_VALUE_LEAF}' collides with the reserved "
            "scalar-array sentinel and cannot be addressed as a column; rename "
            "this field in the source data",
            column=key,
        )
    if "." in key:
        raise ApiInputSchemaError(
            f"source JSON key {key!r} contains '.', which the path grammar "
            "reserves as the object-nesting separator, so it cannot be expressed "
            "as a column path; rename this field in the source data",
            column=key,
        )
    if not is_identifier_name(key):
        raise ApiInputSchemaError(
            f"source JSON key {key!r} is not an addressable identifier; keys "
            "must match [A-Za-z_][A-Za-z0-9_]*, so rename this field in the "
            "source data",
            column=key,
        )


_InferenceLevel = tuple[PathSeg, ...]
_InferenceObjectPath = tuple[str, ...]


@dataclass
class _InferenceState:
    """Compact, mergeable evidence collected by exact schema inference."""

    levels: dict[_InferenceLevel, dict[_InferenceObjectPath, str]] = field(
        default_factory=lambda: {(): {}}
    )
    scalar_levels: dict[_InferenceLevel, str | None] = field(  # pragma: no mutate
        default_factory=dict
    )
    container_paths: dict[_InferenceLevel, set[_InferenceObjectPath]] = field(default_factory=dict)
    null_leaves: dict[_InferenceLevel, dict[_InferenceObjectPath, None]] = field(
        default_factory=dict
    )
    validated_keys: set[str] = field(  # pragma: no mutate - repr metadata only
        default_factory=set, repr=False
    )

    def _validate_key_once(self, key: str) -> None:
        if key in self.validated_keys:
            return
        _reject_unexpressible_key(key)
        self.validated_keys.add(key)

    def walk(
        self,
        value: Any,
        level: _InferenceLevel = (),
        obj_prefix: _InferenceObjectPath = (),
    ) -> None:
        """Merge one JSON value's schema evidence into this state."""
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                self.walk(item, level, obj_prefix)
            return
        if not isinstance(value, dict):
            return

        cols = self.levels.setdefault(level, {})
        for key, child_value in value.items():
            self._validate_key_once(key)
            object_path = obj_prefix + (key,)
            if isinstance(child_value, dict):
                self.container_paths.setdefault(level, set()).add(object_path)
                self.walk(child_value, level, object_path)
            elif isinstance(child_value, list):
                self.container_paths.setdefault(level, set()).add(object_path)
                child_level = level + tuple((part, False) for part in obj_prefix) + ((key, True),)
                if not child_value:
                    self.scalar_levels.setdefault(child_level, None)
                elif any(isinstance(item, dict) for item in child_value):
                    self.walk(child_value, child_level, ())
                else:
                    element_type: str | None = None
                    for item in child_value:
                        if isinstance(item, list):
                            dotted_path = ".".join(object_path)
                            raise ApiInputSchemaError(
                                f"column {dotted_path!r}: nested arrays "
                                "(array of arrays) cannot be expressed as a flat "
                                "table column; flatten this field in the source data",
                                column=dotted_path,
                            )
                        if item is None:
                            continue
                        element_type = _widen_type(element_type, _infer_type(item))
                    self.scalar_levels[child_level] = _widen_type(
                        self.scalar_levels.get(child_level), element_type or "str"
                    )
            elif child_value is None:
                self.null_leaves.setdefault(level, {})[object_path] = None
            else:
                cols[object_path] = _widen_type(cols.get(object_path), _infer_type(child_value))

    def merge(self, other: _InferenceState) -> None:
        """Merge a later file range while preserving serial observation order."""
        for level, observed_columns in other.levels.items():
            columns = self.levels.setdefault(level, {})
            for object_path, observed_type in observed_columns.items():
                columns[object_path] = _widen_type(columns.get(object_path), observed_type)

        for level, scalar_type in other.scalar_levels.items():
            if level not in self.scalar_levels:
                self.scalar_levels[level] = scalar_type
            elif scalar_type is not None:
                self.scalar_levels[level] = _widen_type(self.scalar_levels[level], scalar_type)

        for level, container_paths in other.container_paths.items():
            self.container_paths.setdefault(level, set()).update(container_paths)
        for level, null_paths in other.null_leaves.items():
            self.null_leaves.setdefault(level, {}).update(null_paths)
        self.validated_keys.update(other.validated_keys)


def _infer_records(records: Iterable[dict[str, Any]]) -> _InferenceState:
    state = _InferenceState()
    for record in records:
        state.walk(record)
    return state


@dataclass(frozen=True)  # pragma: no mutate - declaration metadata, not runtime logic
class _InferenceChunkResult:
    index: int
    state: _InferenceState | None = None
    failure: _ChunkFailure | None = None


def _infer_chunk(args: tuple[str, int, int, int]) -> _InferenceChunkResult:
    """Infer one newline-delimited byte range in a spawned worker."""
    data_path_s, start, end, index = args
    try:
        state = _infer_records(_iter_range_records(Path(data_path_s), start, end))
        return _InferenceChunkResult(index=index, state=state)
    except Exception as exc:  # noqa: BLE001 — reconstructed in the parent
        return _InferenceChunkResult(index=index, failure=_failure_from_exception(exc))


def _infer_jsonl_in_parallel(data_path: Path, ranges: list[tuple[int, int]]) -> _InferenceState:
    """Infer all *ranges* concurrently and merge them in file order."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    tasks = [(str(data_path), start, end, index) for index, (start, end) in enumerate(ranges)]
    workers = _parallel_worker_count(len(tasks))
    logger.info(
        "json_schema_infer_parallel_start",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
    )

    started = time.perf_counter()
    merged = _InferenceState()
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    try:
        for result in pool.map(_infer_chunk, tasks):
            if result.failure is not None:
                _raise_worker_failure(result.failure)
            if result.state is None:
                raise RuntimeError(
                    f"parallel schema inference chunk {result.index} returned no state"
                )
            merged.merge(result.state)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    logger.info(
        "json_schema_infer_parallel_complete",
        data_path=str(data_path),
        chunks=len(tasks),
        workers=workers,
        duration_seconds=round(  # pragma: no mutate - diagnostic timing only
            time.perf_counter() - started,
            3,  # pragma: no mutate
        ),
    )
    return merged


def _assemble_inference_schema(state: _InferenceState) -> dict[str, Any]:
    """Convert accumulated evidence into the public v2 inference payload."""
    levels = state.levels
    scalar_levels = state.scalar_levels

    # A leaf seen ONLY as null across every scanned record still earns a
    # column — typed ``str``, the same default an all-empty scalar array
    # takes. A path ever holding a container earns none: the dotted leaves or
    # the child table already carry it.
    for level, null_paths in state.null_leaves.items():
        cols = levels.setdefault(level, {})
        containers = state.container_paths.get(level, set())
        for object_path in null_paths:
            if object_path not in cols and object_path not in containers:
                cols[object_path] = "str"

    table_entries: list[tuple[_InferenceLevel, dict[str, Any], str]] = []
    all_levels = set(levels) | set(scalar_levels)
    for level in sorted(all_levels, key=lambda s: (array_depth(s), len(s), tuple(s))):
        table_path = make_table_path(level)
        if level in scalar_levels and level not in levels:
            columns: list[dict[str, Any]] = [
                {
                    "name": _SCALAR_VALUE_COLUMN,
                    "path": f"{table_path}.{_SCALAR_VALUE_LEAF}",
                    "type": scalar_levels[level] or "str",
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
            ]
        else:
            column_paths = list(levels.get(level, {}).keys())
            names = _assign_column_names(column_paths)
            columns = [
                {
                    "name": names[object_path],
                    "path": f"{table_path}." + ".".join(object_path),
                    "type": levels[level][object_path],
                    "status": "Inferred",
                    "selected": True,
                    "levels": None,
                }
                for object_path in column_paths
            ]
        base_label = "quote_info" if not level else derive_identifier_label(level[-1][0])
        table_entries.append(
            (
                level,
                {
                    "path": table_path,
                    "label": base_label,
                    "displayPath": table_path,
                    "emit": array_depth(level) == 0,  # pragma: no mutate  # root only
                    "row_id_column": None,
                    "columns": columns,
                },
                base_label,
            ),
        )

    base_label_counts: dict[str, int] = {}
    for _level, _table, base_label in table_entries:
        folded = base_label.casefold()
        base_label_counts[folded] = base_label_counts.get(folded, 0) + 1

    assigned_labels: list[tuple[dict[str, Any], str]] = []
    for level, table, base_label in table_entries:
        if base_label_counts[base_label.casefold()] > 1 and level:
            label = "_".join(derive_identifier_label(key) for key, _is_array in level)
        else:
            label = base_label
        assigned_labels.append((table, label))

    used_labels: set[str] = set()
    for table, label in assigned_labels:
        candidate = label
        suffix = 2
        while candidate.casefold() in used_labels:
            candidate = f"{label}_{suffix}"
            suffix += 1
        table["label"] = candidate
        used_labels.add(candidate.casefold())

    return {"tables": [table for _level, table, _base_label in table_entries]}


def infer_v2_schema_from_data(
    data_path: str | Path,  # pragma: no mutate
    *,  # pragma: no mutate
    sample_size: int | None = None,  # pragma: no mutate
) -> dict[str, Any]:
    """Sniff the v2 schema mapping from the records of *data_path*.

    Produces a v2 config (without the apiInput's ``path`` / ``contract``
    metadata) — the caller stitches them in. Relational depth is ARRAY
    (``[:]``) depth only (the 2026-06-17 object-nesting ruling). Walks the
    records and records:

    - every ARRAY-of-objects depth as a candidate table — a 1-1 OBJECT mints
      NO table; its scalars fold into the enclosing array level as dotted-leaf
      columns (``$[:].quote_metadata.quote_id``), and an array nested inside a
      1-1 object is a child table located through the object hop
      (``$[:].proposer.claims[:]``);
    - the leaf keys at each level, with types inferred and *widened* across
      all scanned records (so a late-appearing float widens an int column
      rather than crashing the build), and named bare-where-unique /
      qualified-on-collision (see :func:`_assign_column_names`);
    - a JSON **scalar array** (e.g. ``["TPFT", "comprehensive"]``) as its own
      child table with a single ``value`` column — mirroring how an array of
      objects becomes a child table (Option 2). Element types are widened
      the same way.

    Types are inferred across the whole file by default; pass ``sample_size``
    to cap the number of records scanned. For JSONL and root JSON arrays, the
    iterator stops after the requested object records instead of reading the
    rest of the file.

    Raises :class:`ApiInputSchemaError` for a nested array (array of arrays),
    which can't be expressed as a flat table.

    Each table is ``emit=True`` only for the root; nested tables are off so
    the user opts in explicitly.
    """
    source_path = Path(data_path)
    unbounded = sample_size is None or sample_size <= 0
    if unbounded and _should_shred_in_parallel(source_path):
        initial_stat = source_path.stat()
        ranges = _jsonl_byte_ranges(source_path, _PARALLEL_CHUNK_BYTES)
        if len(ranges) > 1:
            state = _infer_jsonl_in_parallel(source_path, ranges)
            final_stat = source_path.stat()
            initial_identity = (
                initial_stat.st_dev,
                initial_stat.st_ino,
                initial_stat.st_size,
                initial_stat.st_mtime_ns,
            )
            final_identity = (
                final_stat.st_dev,
                final_stat.st_ino,
                final_stat.st_size,
                final_stat.st_mtime_ns,
            )
            if final_identity != initial_identity:
                raise ApiInputSchemaError(
                    "data file changed while its schema was inferred; retry inference",
                    path=str(source_path),
                )
            return _assemble_inference_schema(state)

    records = _iter_records_for_inference(source_path, sample_size=sample_size)
    return _assemble_inference_schema(_infer_records(records))


def read_per_port_cache_meta(cache_dir: str | Path) -> dict[str, Any] | None:  # pragma: no mutate
    """Return the cached ``meta.json`` payload, or ``None`` if absent / corrupt.

    Used by the cache routes' status endpoint to report what's on disk
    without re-shredding.
    """
    cd = Path(cache_dir)
    with _build_lock_for(cd):
        return _read_per_port_cache_meta_unlocked(cd)


def _read_per_port_cache_meta_unlocked(cd: Path) -> dict[str, Any] | None:
    meta_path = cd / _META_FILENAME
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return meta
