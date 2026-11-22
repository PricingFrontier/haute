"""Content-addressed stage objects, revision manifests, reader pins and reachability.

One `StageObjects` manages one model scope's directory, `.haute/staged-training/<key>/`:

```text
revisions/<revision>.json   immutable revision manifests
objects/<sha256>            immutable content-addressed files
staging/<token>-<id>.tmp    ingest temporaries, named by the owning process token
leases/<revision>.<token>   reader pins: multi-holder marker files
processes/<token>.lock      process liveness tokens (`haute._process_tokens`)
objects.lock                serialises pin registration, installation and collection
```

The caller (`StageStore`) owns the pointer manifest and its lock. Collection receives the
pointer's root revisions from the caller, under that lock. The lock order is always the
pointer lock, then `objects.lock`. See "Stage storage (version 2)" in
`specs/modelling/low-level.md`.

**Publication** (`publishing`):
1. pin the new revision;
2. write its manifest (a root while pinned);
3. install its staged objects;
4. let the caller swap the pointer.

A failure never deletes an installed object; collection decides. The guarantee is
against process crashes; directory fsyncs are not performed. Every directory from the
project root down is checked for links and reparse points before any read, write or
deletion.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import threading
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any, Literal, Protocol

from haute._file_lock import (
    UnsafeCachePathError,
    _is_reparse_point,
    _plain_directory_stat,
    file_lock_for,
    remove_unheld_lock_file,
)
from haute._file_ops import atomic_path, publish_staged_file
from haute._logging import get_logger
from haute._process_tokens import ProcessTokens, is_token
from haute.errors import HauteValidationError

logger = get_logger(component="stage_objects")

REVISION_FORMAT = "haute-stage-revision"
ARCHIVE_FORMAT = "haute-stage-archive"
VERSION = 2
REVISION_LIMIT = 4 * 1024 * 1024
JSON_OBJECT_LIMIT = 256 * 1024 * 1024
EXPANDED_LIMIT = 4 * 1024**3
_HEADER_LIMIT = 64 * 1024
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_FIT = r"(?:development|validation-(?:[0-9]|10))"
_POINT = r"(?:unchanged|point-(?:[1-9]|1[0-6]))"
# Logical member names and the kind each must have.
_NAMES: tuple[tuple[re.Pattern[str], Literal["json", "binary"]], ...] = (
    (re.compile(r"source\.parquet\Z"), "binary"),
    (re.compile(r"plan\.json\Z"), "json"),
    (re.compile(r"result\Z"), "json"),
    (re.compile(r"view\Z"), "json"),
    (re.compile(rf"fit/{_FIT}/(?:evidence|chain)\Z"), "binary"),
    (re.compile(rf"path/[a-f0-9]{{64}}/{_POINT}/{_FIT}\.candidate\Z"), "binary"),
    (re.compile(rf"path/[a-f0-9]{{64}}/{_POINT}\.result\Z"), "json"),
    (re.compile(r"(?:snapshot|table|history)/[a-f0-9]{64}\Z"), "json"),
    (
        re.compile(
            r"export/(?:model\.native|feature-contract\.json|plan\.json"
            r"|evaluation-results\.json|evaluation-report\.json)\Z"
        ),
        "binary",
    ),
)
_LEGACY = re.compile(
    r"[a-f0-9]{32}\.(?:hstage|pending|result\.json|view\.json|lease"
    r"|(?:result|view)\.[a-f0-9]{8}\.tmp)\Z"
)
_STAGING = re.compile(r"([a-f0-9]{12})-[a-f0-9]{32}\.tmp\Z")
_REVISION_FILE = re.compile(r"([a-f0-9]{32})\.json\Z")
_CHUNK = 1 << 20


class CollectionHaltedError(HauteValidationError):
    """A root revision manifest is missing or corrupt, so nothing may be deleted."""


def _fault_point(name: str) -> None:
    """Deterministic interleaving point for transaction tests; a no-op in production."""
    del name


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Stage JSON requires finite numbers")
    return number


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Stage JSON cannot contain {value}")


def strict_json_loads(data: bytes) -> Any:
    """Decode stage JSON, refusing NaN, infinities and numbers that overflow to them."""
    return json.loads(data, parse_float=_finite_float, parse_constant=_invalid_constant)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def object_kind(name: str) -> Literal["json", "binary"]:
    for pattern, kind in _NAMES:
        if pattern.match(name):
            return kind
    raise HauteValidationError(f"Unknown stage member name {name!r}")


@dataclass(frozen=True)
class ObjectRef:
    sha256: str
    size: int
    kind: Literal["json", "binary"]

    def to_plain_data(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size, "kind": self.kind}


@dataclass(frozen=True)
class RevisionManifest:
    """One revision: its complete object closure, typed result envelope and metadata.

    Construction validates, so a manifest that could not be read back is never written.
    """

    revision: str
    objects: Mapping[str, ObjectRef]
    result: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        # Validate a private, read-only copy: later changes to the caller's mapping cannot
        # reach a validated manifest.
        if not isinstance(self.objects, Mapping):
            raise HauteValidationError("Invalid stage revision objects")
        object.__setattr__(self, "objects", MappingProxyType(dict(self.objects)))
        if not isinstance(self.revision, str) or not _HEX32.fullmatch(self.revision):
            raise HauteValidationError("Invalid stage revision id")
        if not isinstance(self.result, Mapping) or not isinstance(self.metadata, Mapping):
            raise HauteValidationError("Invalid stage revision envelope")
        total = 0
        for name, ref in self.objects.items():
            if (
                not isinstance(name, str)
                or not isinstance(ref, ObjectRef)
                or not isinstance(ref.sha256, str)
                or not _HEX64.fullmatch(ref.sha256)
                or type(ref.size) is not int
                or ref.size < 0
                or ref.kind != object_kind(name)
            ):
                raise HauteValidationError(f"Invalid stage object reference {name!r}")
            if ref.kind == "json" and ref.size > JSON_OBJECT_LIMIT:
                raise HauteValidationError(f"Stage JSON object {name!r} exceeds its size limit")
            total += ref.size
        if total > EXPANDED_LIMIT:
            raise HauteValidationError("Stage revision exceeds its expanded size budget")

    def encode(self) -> bytes:
        encoded = canonical_json(
            {
                "format": REVISION_FORMAT,
                "version": VERSION,
                "revision": self.revision,
                "objects": {name: ref.to_plain_data() for name, ref in self.objects.items()},
                "result": self.result,
                "metadata": self.metadata,
            }
        )
        if len(encoded) > REVISION_LIMIT:
            raise HauteValidationError("Stage revision manifest exceeds its size limit")
        return encoded

    @classmethod
    def decode(cls, data: bytes, *, revision: str | None = None) -> RevisionManifest:
        if len(data) > REVISION_LIMIT:
            raise HauteValidationError("Stage revision manifest exceeds its size limit")
        try:
            value = strict_json_loads(data)
        except (ValueError, UnicodeError) as exc:
            raise HauteValidationError("Invalid stage revision manifest") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "version", "revision", "objects", "result", "metadata"}
            or value["format"] != REVISION_FORMAT
            or type(value["version"]) is not int
            or value["version"] != VERSION
            or (revision is not None and value["revision"] != revision)
            or not isinstance(value["objects"], dict)
            or not isinstance(value["result"], dict)
            or not isinstance(value["metadata"], dict)
        ):
            raise HauteValidationError("Unsupported or invalid stage revision manifest")
        objects: dict[str, ObjectRef] = {}
        for name, raw in value["objects"].items():
            if not isinstance(raw, dict) or set(raw) != {"sha256", "size", "kind"}:
                raise HauteValidationError(f"Invalid stage object reference {name!r}")
            objects[name] = ObjectRef(raw["sha256"], raw["size"], raw["kind"])
        return cls(value["revision"], objects, value["result"], value["metadata"])

    def digests(self) -> set[str]:
        return {ref.sha256 for ref in self.objects.values()}


@dataclass(frozen=True)
class StagedObject:
    path: Path
    sha256: str
    size: int


@dataclass
class CollectionReport:
    roots: set[str] = field(default_factory=set)
    removed_objects: int = 0
    removed_revisions: int = 0
    removed_staging: int = 0
    skipped: list[str] = field(default_factory=list)


class _ChecksumCache:
    """Verified digests keyed by file identity; a change to the file is a cache miss."""

    def __init__(self, capacity: int = 65536) -> None:
        self._entries: OrderedDict[tuple[str, tuple[int, ...]], tuple[str, int]] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def get(self, key: tuple[str, tuple[int, ...]]) -> tuple[str, int] | None:
        with self._lock:
            found = self._entries.get(key)
            if found is not None:
                self._entries.move_to_end(key)
            return found

    def put(self, key: tuple[str, tuple[int, ...]], value: tuple[str, int]) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)


_CHECKSUMS = _ChecksumCache()
# (scope, revision) -> (holders in this process, the manifest digest they read)
_PIN_COUNTS: dict[tuple[str, str], tuple[int, str]] = {}
_PIN_LOCK = threading.Lock()


class _ObjectsLock:
    """The scope's store lock, taken only after its directory is checked (and created)."""

    def __init__(self, scope: StageObjects, path: Path) -> None:
        self._scope, self._path = scope, path
        self._lock: Any | None = None

    def __enter__(self) -> None:
        if self._lock is None:
            self._scope._plain(self._path.parent, create=True)
            self._scope._plain(self._scope._processes, create=True)
            self._lock = file_lock_for(self._path)
        self._lock.__enter__()

    def __exit__(self, *exc: Any) -> None:
        assert self._lock is not None
        self._lock.__exit__(*exc)


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _discard(path: Path) -> None:
    """Remove a temporary or marker; a failure is logged, never raised (cleanup paths)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("stage_file_not_removed", path=path.name, error=str(exc))


def _write_marker(marker: Path, sha256: str) -> None:
    """Create *marker* holding *sha256* without following or writing through a link.

    Anything already at the path (a stale marker, a link, a hard link) is unlinked as an
    entry, then the marker is created exclusively, so no other file is ever written.
    """
    for _ in range(2):
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
        except FileExistsError:
            marker.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(sha256)
        return
    raise HauteValidationError("Could not create a stage pin marker")


def _read_marker(marker: Path) -> str | None:
    """A marker's digest, or ``None`` unless it is a plain, singly linked regular file."""
    try:
        before = marker.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or _is_reparse_point(before):
            return None
        descriptor = os.open(marker, os.O_RDONLY | _NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            return None
        data = handle.read(65)
    digest = data.decode("ascii", errors="replace")
    return digest if _HEX64.fullmatch(digest) else None


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class StageObjects:
    """One model scope's objects; *anchor* is the project root, below which no link is allowed."""

    def __init__(self, directory: Path, *, anchor: Path, processes: Path | None = None) -> None:
        """Construction touches nothing on disk; each operation creates what it writes.

        *processes* holds the process tokens; a store shares one across its scopes, so a
        process holds one token file however many scopes it uses.
        """
        processes = directory / "processes" if processes is None else processes
        if (
            not anchor.is_absolute()
            or anchor not in directory.parents
            or anchor not in processes.parents
            or any(
                part in {"..", "."} for part in (*anchor.parts, *directory.parts, *processes.parts)
            )
        ):
            raise HauteValidationError("A stage scope must lie inside its project")
        self.directory = directory
        self.anchor = anchor
        self.revisions = directory / "revisions"
        self.objects = directory / "objects"
        self.staging = directory / "staging"
        self.leases = directory / "leases"
        self._lock = _ObjectsLock(self, directory / "objects.lock")
        self._processes = processes
        self._tokens = ProcessTokens.for_directory(processes)
        try:
            self._plain(directory)  # refuse a link on the existing part of the path now
        except FileNotFoundError:
            pass
        self._scope = str(directory.resolve())

    # -- paths ----------------------------------------------------------------------------

    def _own_token(self) -> str:
        """This process's token, its directory checked and created first."""
        self._plain(self._processes, create=True)
        return self._tokens.own()

    def _plain(self, path: Path, *, create: bool = False) -> Path:
        """Check every directory from the anchor down to *path*: no links or reparse points."""
        relative = path.relative_to(self.anchor)
        if any(part in {"..", "."} for part in relative.parts):
            raise HauteValidationError("Stage store paths cannot traverse directories")
        current = self.anchor
        for part in relative.parts:
            current = current / part
            try:
                _plain_directory_stat(current)
            except FileNotFoundError:
                if not create:
                    raise
                current.mkdir(exist_ok=True)
                _plain_directory_stat(current)
            except UnsafeCachePathError as exc:
                raise HauteValidationError("Stage store paths cannot be links") from exc
        return path

    def _plain_file(self, path: Path) -> os.stat_result:
        self._plain(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_reparse_point(info):
            raise HauteValidationError("Stage store files must be plain regular files")
        return info

    def _entries(self, directory: Path) -> tuple[Path, ...]:
        if not directory.exists():
            return ()
        self._plain(directory)
        return tuple(directory.iterdir())

    def object_path(self, digest: str) -> Path:
        if not _HEX64.fullmatch(digest):
            raise HauteValidationError("Invalid stage object digest")
        return self.objects / digest

    def revision_path(self, revision: str) -> Path:
        if not _HEX32.fullmatch(revision):
            raise HauteValidationError("Invalid stage revision id")
        return self.revisions / f"{revision}.json"

    # -- objects --------------------------------------------------------------------------

    def stage_stream(self, reader: IO[bytes], *, limit: int | None = None) -> StagedObject:
        """Copy *reader* into a staging file owned by this process, hashing as it goes."""
        directory = self._plain(self.staging, create=True)
        path = directory / f"{self._own_token()}-{uuid.uuid4().hex}.tmp"
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("xb") as writer:
                for chunk in iter(lambda: reader.read(_CHUNK), b""):
                    size += len(chunk)
                    if limit is not None and size > limit:
                        raise HauteValidationError("Stage object exceeds its size limit")
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StagedObject(path, digest.hexdigest(), size)

    def stage_bytes(self, data: bytes) -> StagedObject:
        return self.stage_stream(io.BytesIO(data))

    def install(self, staged: StagedObject) -> str:
        """Install *staged* as its digest's object without clobbering; dedupe if present.

        The store lock serialises installs, so no installer observes another's transient
        link-then-unlink. The staging copy is always removed.
        """
        target = self.object_path(staged.sha256)
        self._plain(self.objects, create=True)
        try:
            with self._lock:
                if not target.exists():
                    try:
                        publish_staged_file(staged.path, target, replace=False)
                        _fault_point("object_installed")
                        return staged.sha256
                    except FileExistsError:
                        pass
                with self.open(staged.sha256, size=staged.size):
                    pass  # an existing object must verify
                return staged.sha256
        finally:
            _discard(staged.path)

    @contextmanager
    def open(self, digest: str, *, size: int | None = None) -> Iterator[IO[bytes]]:
        """A verified read handle: the bytes it reads are the bytes that hash to *digest*."""
        path = self.object_path(digest)
        try:
            before = self._plain_file(path)
        except FileNotFoundError as exc:
            raise HauteValidationError("Stage object is missing; build the GBM again") from exc
        if before.st_nlink != 1:
            raise HauteValidationError("Stage objects must be unlinked regular files")
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or info.st_nlink != 1:
                raise HauteValidationError("Stage object changed while opening")
            key = (str(path), _signature(info))
            known = _CHECKSUMS.get(key)
            if known is None:
                hasher, length = hashlib.sha256(), 0
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    hasher.update(chunk)
                    length += len(chunk)
                if _signature(os.fstat(handle.fileno())) != key[1]:
                    raise HauteValidationError("Stage object changed while verifying")
                known = (hasher.hexdigest(), length)
                handle.seek(0)
            if known[0] != digest or (size is not None and known[1] != size):
                raise HauteValidationError("Stage object checksum/size mismatch")
            _CHECKSUMS.put(key, known)
            yield handle

    def read_bytes(self, ref: ObjectRef) -> bytes:
        with self.open(ref.sha256, size=ref.size) as handle:
            return handle.read()

    def read_json(self, ref: ObjectRef) -> Any:
        if ref.kind != "json":
            raise HauteValidationError("Stage object is not JSON")
        try:
            return strict_json_loads(self.read_bytes(ref))
        except (ValueError, UnicodeError) as exc:
            raise HauteValidationError("Invalid stage JSON object") from exc

    # -- revisions ------------------------------------------------------------------------

    def write_revision(self, manifest: RevisionManifest) -> None:
        """Write an immutable revision manifest; an existing different one is refused."""
        path = self.revision_path(manifest.revision)
        self._plain(self.revisions, create=True)
        encoded = manifest.encode()
        staged = self.stage_bytes(encoded)
        try:
            publish_staged_file(staged.path, path, replace=False)
        except FileExistsError:
            if self.read_revision(manifest.revision).encode() != encoded:
                raise HauteValidationError(
                    "Stage revision id already names another revision"
                ) from None
        finally:
            _discard(staged.path)

    def read_revision(
        self, revision: str, *, sha256: str | None = None, size: int | None = None
    ) -> RevisionManifest:
        """A revision manifest. With *sha256* (and *size*), its file's bytes must match."""
        path = self.revision_path(revision)
        try:
            before = self._plain_file(path)
        except FileNotFoundError as exc:
            raise HauteValidationError("This stage revision is no longer available") from exc
        if before.st_size > REVISION_LIMIT:
            raise HauteValidationError("Stage revision manifest exceeds its size limit")
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                raise HauteValidationError("Stage revision changed while opening")
            data = handle.read(REVISION_LIMIT + 1)
        if (sha256 is not None and hashlib.sha256(data).hexdigest() != sha256) or (
            size is not None and len(data) != size
        ):
            raise HauteValidationError("Stage revision checksum/size mismatch")
        return RevisionManifest.decode(data, revision=revision)

    # -- pins -----------------------------------------------------------------------------

    @contextmanager
    def pin(self, revision: str, sha256: str) -> Iterator[None]:
        """Keep *revision* a collection root while held; many holders may pin one revision.

        *sha256* is the digest of the manifest the holder reads. The marker records it, so
        collection verifies a pinned root as it verifies the pointer's. Registration and
        release happen under the store lock, so a collection either sees this pin or runs
        wholly before it.
        """
        self.revision_path(revision)  # validates the id
        if not _HEX64.fullmatch(sha256):
            raise HauteValidationError("A stage pin needs its revision manifest's digest")
        key = (self._scope, revision)
        leases = self._plain(self.leases, create=True)
        marker = leases / f"{revision}.{self._own_token()}"
        with self._lock, _PIN_LOCK:
            count, digest = _PIN_COUNTS.get(key, (0, sha256))
            if digest != sha256:
                raise HauteValidationError("Stage revision pinned with two different digests")
            if count == 0:
                _write_marker(marker, sha256)
            _PIN_COUNTS[key] = (count + 1, sha256)
        try:
            yield
        finally:
            self._release(key, marker, sha256)

    def _release(self, key: tuple[str, str], marker: Path, sha256: str) -> None:
        """Drop one hold on a pin. It never raises: it runs after the holder's work.

        A marker this process cannot remove names its live token without a hold, so a
        later collection removes it.
        """
        released = False
        try:
            with self._lock, _PIN_LOCK:
                remaining = _PIN_COUNTS[key][0] - 1
                released = True
                if remaining:
                    _PIN_COUNTS[key] = (remaining, sha256)
                else:
                    del _PIN_COUNTS[key]
                    _discard(marker)
        except Exception as exc:
            if not released:  # the store lock failed: release the hold without it
                with _PIN_LOCK:
                    count = _PIN_COUNTS[key][0] - 1
                    if count:
                        _PIN_COUNTS[key] = (count, sha256)
                    else:
                        del _PIN_COUNTS[key]
            logger.warning("stage_pin_release_failed", marker=marker.name, error=str(exc))

    def pinned(self) -> set[str]:
        """Revisions any live process pins. Markers of exited processes are removed."""
        with self._lock:
            return set(self._pinned_locked())

    def _pinned_locked(self) -> dict[str, str]:
        """Live pins: each revision and the manifest digest its holders read."""
        own = self._tokens.current
        live: dict[str, str] = {}
        for marker in self._entries(self.leases):
            revision, _, token = marker.name.partition(".")
            if not _HEX32.fullmatch(revision) or not is_token(token):
                continue
            if token == own:
                with _PIN_LOCK:
                    held = (self._scope, revision) in _PIN_COUNTS
            else:
                held = self._tokens.alive(token)
            if not held:
                marker.unlink(missing_ok=True)  # removes a link itself, never its target
                continue
            digest = _read_marker(marker)
            if digest is None or live.setdefault(revision, digest) != digest:
                raise CollectionHaltedError(f"Stage pin for {revision} is unreadable")
        return live

    # -- publication and collection --------------------------------------------------------

    @contextmanager
    def publishing(
        self, manifest: RevisionManifest, staged: Iterable[StagedObject]
    ) -> Iterator[None]:
        """Pin, write the manifest, install objects; the caller swaps the pointer inside."""
        pending = list(staged)
        sizes = {ref.sha256: ref.size for ref in manifest.objects.values()}
        try:
            for item in pending:
                if sizes.get(item.sha256) != item.size:
                    raise HauteValidationError("Staged object disagrees with the revision")
            with self.pin(manifest.revision, hashlib.sha256(manifest.encode()).hexdigest()):
                self.write_revision(manifest)
                _fault_point("revision_written")
                for item in pending:
                    self.install(item)
                for name, ref in manifest.objects.items():
                    try:
                        size: int | None = self._plain_file(self.object_path(ref.sha256)).st_size
                    except FileNotFoundError:
                        size = None
                    if size != ref.size:
                        raise HauteValidationError(f"Stage revision object {name!r} is missing")
                _fault_point("objects_installed")
                yield
        finally:
            for item in pending:
                _discard(item.path)  # may follow the caller's commit, so never raises

    def collect(self, roots: Mapping[str, str]) -> CollectionReport:
        """Delete everything unreachable from *roots* and live pins.

        *roots* maps each root revision to its manifest's expected digest; pins carry
        theirs. The caller holds the pointer lock, so the root set cannot change meanwhile.
        A missing, corrupt or checksum-invalid root manifest halts collection before
        anything is deleted (`CollectionHaltedError`), so an altered manifest can never
        redirect reachability.
        """
        with self._lock:
            return self._collect_locked(dict(roots))

    def _collect_locked(self, roots: dict[str, str]) -> CollectionReport:
        report = CollectionReport()
        expected = dict(roots)
        for revision, digest in self._pinned_locked().items():
            if expected.setdefault(revision, digest) != digest:
                raise CollectionHaltedError(f"Stage revision {revision} has two digests")
        report.roots = set(expected)
        reachable: set[str] = set()
        for revision, digest in sorted(expected.items()):
            try:
                reachable |= self.read_revision(revision, sha256=digest).digests()
            except HauteValidationError as exc:
                logger.error("stage_collection_halted", revision=revision, error=str(exc))
                raise CollectionHaltedError(f"Stage revision {revision} cannot be read") from exc
        _fault_point("collection_marked")
        for path in self._entries(self.revisions):
            match = _REVISION_FILE.fullmatch(path.name)
            if match is not None and match.group(1) not in report.roots:
                report.removed_revisions += self._remove(path, report)
        for path in self._entries(self.objects):
            if _HEX64.fullmatch(path.name) and path.name not in reachable:
                report.removed_objects += self._remove(path, report)
        for path in self._entries(self.staging):
            match = _STAGING.fullmatch(path.name)
            if match is None or match.group(1) == self._tokens.current:
                continue
            if not self._tokens.alive(match.group(1)):
                report.removed_staging += self._remove(path, report)
        self._tokens.sweep_dead()
        return report

    def _remove(self, path: Path, report: CollectionReport) -> bool:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_reparse_point(info):
            report.skipped.append(path.name)  # only plain files are ever deleted
            return False
        try:
            path.unlink(missing_ok=True)
            return True
        except PermissionError:
            # Windows refuses to delete an open file; a later collection retries.
            report.skipped.append(path.name)
            return False

    # -- legacy stores ----------------------------------------------------------------------

    def legacy_entries(self) -> list[Path]:
        """Files of the pre-version-2 store layout in this scope."""
        return sorted(p for p in self._entries(self.directory) if _LEGACY.fullmatch(p.name))

    def reclaim_legacy(self) -> int:
        """Delete pre-version-2 files, holding each revision's legacy lease while deleting.

        A revision whose lease another process holds keeps its files.
        """
        removed = 0
        report = CollectionReport()
        for revision in sorted({path.name.split(".", 1)[0] for path in self.legacy_entries()}):
            lease_path = self.directory / f"{revision}.lease"
            lease = file_lock_for(lease_path)
            if not lease.acquire(blocking=False):
                continue
            try:
                for path in self.legacy_entries():
                    if path.name.split(".", 1)[0] == revision and path != lease_path:
                        removed += self._remove(path, report)
            finally:
                lease.release()
            removed += remove_unheld_lock_file(lease_path)
        return removed


# -- portable and delta archives ------------------------------------------------------------


@dataclass(frozen=True)
class StageArchive:
    """A validated portable or delta archive: its manifest and the objects it carries."""

    path: Path
    manifest: RevisionManifest
    inherited: frozenset[str]
    carried: frozenset[str]

    @classmethod
    def read(cls, path: Path) -> StageArchive:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)):
                    raise HauteValidationError("Stage archive repeats a member")
                if sum(entry.file_size for entry in entries) > EXPANDED_LIMIT + 2 * REVISION_LIMIT:
                    raise HauteValidationError("Stage archive exceeds its expanded size budget")
                if {"archive.json", "revision.json"} - set(names):
                    raise HauteValidationError("Stage archive is missing its manifest")
                if archive.getinfo("archive.json").file_size > _HEADER_LIMIT:
                    raise HauteValidationError("Stage archive header exceeds its size limit")
                if archive.getinfo("revision.json").file_size > REVISION_LIMIT:
                    raise HauteValidationError("Stage revision manifest exceeds its size limit")
                carried = set()
                for name in names:
                    if name in {"archive.json", "revision.json"}:
                        continue
                    digest = name.removeprefix("objects/")
                    if not name.startswith("objects/") or not _HEX64.fullmatch(digest):
                        raise HauteValidationError(f"Unknown stage archive member {name!r}")
                    carried.add(digest)
                header = strict_json_loads(archive.read("archive.json"))
                if (
                    not isinstance(header, dict)
                    or set(header) != {"format", "version", "delta"}
                    or header["format"] != ARCHIVE_FORMAT
                    or header["version"] != VERSION
                    or type(header["delta"]) is not bool
                ):
                    raise HauteValidationError(
                        "Unsupported stage archive; build the GBM again with this version"
                    )
                manifest = RevisionManifest.decode(archive.read("revision.json"))
                if carried - manifest.digests():
                    raise HauteValidationError("Stage archive carries an object it does not list")
                inherited = frozenset(
                    name for name, ref in manifest.objects.items() if ref.sha256 not in carried
                )
                if bool(inherited) != header["delta"]:
                    raise HauteValidationError("Stage archive objects disagree with its manifest")
                for digest in carried:
                    sizes = {ref.size for ref in manifest.objects.values() if ref.sha256 == digest}
                    if {archive.getinfo(f"objects/{digest}").file_size} != sizes:
                        raise HauteValidationError("Stage archive object size mismatch")
        except HauteValidationError:
            raise
        except (zipfile.BadZipFile, ValueError, UnicodeError, KeyError) as exc:
            raise HauteValidationError("Invalid stage archive") from exc
        return cls(path, manifest, inherited, frozenset(carried))

    def read_bytes(self, ref: ObjectRef) -> bytes:
        return read_archive_object(self.path, ref)

    def stage_into(
        self, store: StageObjects, *, parent: RevisionManifest | None
    ) -> list[StagedObject]:
        """Stream-copy carried objects into *store* staging, verifying every digest.

        Inherited members must appear in *parent*'s closure with identical references.
        """
        for name in self.inherited:
            if parent is None or parent.objects.get(name) != self.manifest.objects[name]:
                raise HauteValidationError(
                    f"Stage archive inherits {name!r}, which its parent does not hold"
                )
        staged: list[StagedObject] = []
        try:
            with zipfile.ZipFile(self.path) as archive:
                for digest in sorted(self.carried):
                    size = next(
                        ref.size for ref in self.manifest.objects.values() if ref.sha256 == digest
                    )
                    with archive.open(f"objects/{digest}") as reader:
                        item = store.stage_stream(reader, limit=size)
                    staged.append(item)
                    if (item.sha256, item.size) != (digest, size):
                        raise HauteValidationError("Stage archive object checksum mismatch")
        except BaseException:
            for item in staged:
                item.path.unlink(missing_ok=True)
            raise
        return staged


Opener = Callable[[], AbstractContextManager[IO[bytes]]]


def write_bundle(
    destination: Path,
    members: Mapping[str, Path | bytes | Opener | ObjectRef],
    *,
    revision: str,
    result: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> RevisionManifest:
    """A portable archive of member files, bytes or verified openers, without a store.

    Members with identical content share one object. An opener (a store revision's
    verified member reader) is read only through the handle it yields. An `ObjectRef`
    member is *inherited*: it is listed but not carried, so the archive is a delta that
    only its parent's store can complete. Returns the archive's manifest.
    """
    refs: dict[str, ObjectRef] = {}
    sources: dict[str, Path | bytes | Opener] = {}

    def reader(value: Path | Opener) -> AbstractContextManager[IO[bytes]]:
        return value.open("rb") if isinstance(value, Path) else value()

    for name, value in members.items():
        if isinstance(value, ObjectRef):
            refs[name] = value
            continue
        if isinstance(value, bytes):
            digest, size = hashlib.sha256(value).hexdigest(), len(value)
        else:
            hasher, size = hashlib.sha256(), 0
            with reader(value) as handle:
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    hasher.update(chunk)
                    size += len(chunk)
            digest = hasher.hexdigest()
        refs[name] = ObjectRef(digest, size, object_kind(name))
        sources.setdefault(digest, value)
    manifest = RevisionManifest(revision, refs, result, metadata)
    with atomic_path(destination) as temporary, temporary.open("wb") as handle:
        with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                zipfile.ZipInfo("archive.json", (1980, 1, 1, 0, 0, 0)),
                canonical_json(
                    {
                        "format": ARCHIVE_FORMAT,
                        "version": VERSION,
                        "delta": manifest.digests() != set(sources),
                    }
                ),
            )
            archive.writestr(
                zipfile.ZipInfo("revision.json", (1980, 1, 1, 0, 0, 0)), manifest.encode()
            )
            for digest, value in sorted(sources.items()):
                member = zipfile.ZipInfo(f"objects/{digest}", (1980, 1, 1, 0, 0, 0))
                with archive.open(member, "w", force_zip64=True) as target:
                    if isinstance(value, bytes):
                        target.write(value)
                        continue
                    with reader(value) as source:
                        hasher = hashlib.sha256()
                        for chunk in iter(lambda: source.read(_CHUNK), b""):
                            hasher.update(chunk)
                            target.write(chunk)
                    if hasher.hexdigest() != digest:
                        raise HauteValidationError("Stage member changed while packing")
    return manifest


def extract_archive(archive: StageArchive, directory: Path) -> dict[str, Path]:
    """Copy a full archive's objects into *directory*, verifying each; map names to files."""
    if archive.inherited:
        raise HauteValidationError("A delta stage archive needs its parent's store")
    directory.mkdir(parents=True, exist_ok=True)
    sizes = {ref.sha256: ref.size for ref in archive.manifest.objects.values()}
    with zipfile.ZipFile(archive.path) as source:
        for digest in sorted(archive.carried):
            hasher, size = hashlib.sha256(), 0
            with source.open(f"objects/{digest}") as reader, (directory / digest).open("xb") as out:
                for chunk in iter(lambda: reader.read(_CHUNK), b""):
                    size += len(chunk)
                    if size > sizes[digest]:
                        raise HauteValidationError("Stage archive object exceeds its size")
                    hasher.update(chunk)
                    out.write(chunk)
            if (hasher.hexdigest(), size) != (digest, sizes[digest]):
                raise HauteValidationError("Stage archive object checksum mismatch")
    return {name: directory / ref.sha256 for name, ref in archive.manifest.objects.items()}


def read_archive_object(path: Path, ref: ObjectRef) -> bytes:
    """One verified object from a validated archive, without extracting the rest."""
    if ref.kind == "json" and ref.size > JSON_OBJECT_LIMIT:
        raise HauteValidationError("Stage JSON object exceeds its size limit")
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(f"objects/{ref.sha256}")
        if info.file_size != ref.size:
            raise HauteValidationError("Stage archive object size mismatch")
        payload = archive.read(info)
    if hashlib.sha256(payload).hexdigest() != ref.sha256:
        raise HauteValidationError("Stage archive object checksum mismatch")
    return payload


class RevisionSource(Protocol):
    """A revision's manifest and verified reads of its objects: an archive or the store."""

    @property
    def manifest(self) -> RevisionManifest: ...

    def read_bytes(self, ref: ObjectRef) -> bytes: ...


@dataclass(frozen=True)
class StoredRevision:
    """A pinned revision read in place from its store; the holder keeps the pin."""

    objects: StageObjects
    manifest: RevisionManifest

    def read_bytes(self, ref: ObjectRef) -> bytes:
        return self.objects.read_bytes(ref)

    @contextmanager
    def open(self, name: str) -> Iterator[IO[bytes]]:
        """A member's verified handle; its object is never read through a reopened path."""
        ref = self.manifest.objects.get(name)
        if ref is None:
            raise HauteValidationError(f"Stage revision lacks member {name!r}")
        with self.objects.open(ref.sha256, size=ref.size) as handle:
            yield handle


@dataclass(frozen=True)
class DeltaRevision:
    """A delta archive read in place: carried objects from the archive, inherited ones from
    its parent revision, matched by member name and identical reference.

    *parent* is the pinned revision the delta was built on (``None`` for a full archive).
    """

    archive: StageArchive
    parent: StoredRevision | None

    @property
    def manifest(self) -> RevisionManifest:
        return self.archive.manifest

    def read_bytes(self, ref: ObjectRef) -> bytes:
        if ref.sha256 in self.archive.carried:
            return read_archive_object(self.archive.path, ref)
        name = next((n for n, r in self.manifest.objects.items() if r == ref), None)
        if name is None:
            raise HauteValidationError("Stage archive does not list this object")
        with self.open(name) as handle:
            return handle.read()

    @contextmanager
    def open(self, name: str) -> Iterator[IO[bytes]]:
        ref = self.manifest.objects.get(name)
        if ref is None:
            raise HauteValidationError(f"Stage revision lacks member {name!r}")
        if ref.sha256 in self.archive.carried:
            yield io.BytesIO(read_archive_object(self.archive.path, ref))
            return
        if self.parent is None or self.parent.manifest.objects.get(name) != ref:
            raise HauteValidationError(
                f"Stage archive inherits {name!r}, which its parent does not hold"
            )
        with self.parent.open(name) as handle:
            yield handle
