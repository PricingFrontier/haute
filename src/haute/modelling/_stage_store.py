"""Durable per-model stage publication, separate from transient worker job records.

Each model scope keeps a pointer manifest, `manifest.json` (schema 3). It names the
current, preview and final revisions, the named checkpoints, the stage job record and any
queued preview. Revisions and their objects live in the scope's `StageObjects`; see
"Stage storage (version 2)" in `specs/modelling/low-level.md`.

- **Locks:** the pointer lock (`manifest.lock`) is always taken before the objects lock.
- **Readers:** they pin the revisions they read while holding the pointer lock, and read
  them after releasing it.
- **Collection:** it runs under the pointer lock after a pointer change, so its roots
  cannot change meanwhile.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from haute._file_lock import FileLock, file_lock_for
from haute._file_ops import atomic_path
from haute._logging import get_logger
from haute._process_memory import process_is_alive
from haute.errors import ConflictError, HauteValidationError
from haute.modelling._stage_diagnostics import hydrate, split
from haute.modelling._stage_objects import (
    REVISION_LIMIT,
    ObjectRef,
    RevisionManifest,
    StageArchive,
    StagedObject,
    StageObjects,
    StoredRevision,
    _discard,
    canonical_json,
    object_kind,
    strict_json_loads,
)
from haute.modelling._stage_views import compact_result

logger = get_logger(component="stage_store")

_SCHEMA = 3
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_MANIFEST_LIMIT = 32 * 1024 * 1024
_OPERATIONS = {"full", "pruned", "banded", "smoothed", "finalise"}
_POINTER = {"schema_version", "current", "preview", "final", "job", "checkpoints", "next_preview"}
# Pointer-record fields the public stage state never shows.
_PRIVATE = ("result_job_id", "view")
_RECORD = {
    "revision",
    "build_id",
    "parent_revision",
    "sha256",
    "size",
    "config_digest",
    "result_job_id",
    "view",
}


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _empty() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA,
        "current": None,
        "preview": None,
        "final": None,
        "job": None,
        "checkpoints": [],
        "next_preview": None,
    }


@dataclass(frozen=True)
class PreparedStage:
    """An unpublished revision: its manifest and staged new objects, owned by this process.

    `sha256` and `size` describe the encoded revision manifest. `result` is the store
    result in memory, so publication never reads it back.
    """

    key: str
    revision: str
    sha256: str
    size: int
    config_digest: str
    result: dict[str, Any]
    manifest: RevisionManifest
    staged: tuple[StagedObject, ...]


class StageStore:
    """One current build and one preview, with compare-and-swap publication."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = self.project_root / ".haute" / "staged-training"

    @staticmethod
    def key(source: str, node_id: str, model_id: str) -> str:
        return hashlib.sha256(_json([source, node_id, model_id])).hexdigest()

    def _directory(self, key: str) -> Path:
        if not _HEX64.fullmatch(key):
            raise HauteValidationError("Invalid stage store key")
        directory = self.root / key
        for path in (self.project_root / ".haute", self.root, directory):
            if path.is_symlink():
                raise HauteValidationError("Stage store directories cannot be symlinks")
            if path.exists() and not path.is_dir():
                raise HauteValidationError("Stage store paths must be directories")
        return directory

    def _lock(self, key: str) -> FileLock:
        return file_lock_for(self._directory(key) / "manifest.lock")

    def _objects(self, key: str) -> StageObjects:
        # One token directory per store, so a process holds one token for all its scopes.
        return StageObjects(
            self._directory(key), anchor=self.project_root, processes=self.root / "processes"
        )

    # -- the pointer ------------------------------------------------------------------------

    def _read(self, key: str) -> dict[str, Any]:
        path = self._directory(key) / "manifest.json"
        if not path.exists() and not path.is_symlink():
            return _empty()
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _MANIFEST_LIMIT:
            raise HauteValidationError("Invalid stage manifest file or size")
        try:
            value = strict_json_loads(path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise HauteValidationError("Invalid stage manifest JSON") from exc
        if isinstance(value, dict) and value.get("schema_version") != _SCHEMA:
            # An older store: nothing in it is interpreted. A full build replaces it.
            return _empty()
        if not isinstance(value, dict) or set(value) != _POINTER:
            raise HauteValidationError("Unsupported stage manifest format/version")
        queued = value["next_preview"]
        if queued is not None and (
            not isinstance(queued, dict)
            or queued.get("operation") not in {"pruned", "banded", "smoothed"}
            or not _HEX32.fullmatch(str(queued.get("parent_revision", "")))
            or type(queued.get("owner_pid")) is not int
            or queued["owner_pid"] < 1
            or queued.get("status") not in {"queued", "failed", "interrupted"}
            or not isinstance(queued.get("message"), str)
        ):
            raise HauteValidationError("Invalid next-stage preview record")
        checkpoints = value["checkpoints"]
        if not isinstance(checkpoints, list) or len(checkpoints) > 10:
            raise HauteValidationError("Invalid checkpoint collection")
        for checkpoint in checkpoints:
            if (
                not isinstance(checkpoint, dict)
                or not _HEX32.fullmatch(str(checkpoint.get("id", "")))
                or not isinstance(checkpoint.get("name"), str)
                or not 1 <= len(checkpoint["name"].strip()) <= 80
                or not isinstance(checkpoint.get("created_at"), str)
                or not isinstance(checkpoint.get("record"), dict)
            ):
                raise HauteValidationError("Invalid named checkpoint")
        for record in self._records(value):
            self._check_record(record)
        job = value["job"]
        if job is not None and (
            not isinstance(job, dict)
            or not isinstance(job.get("id"), str)
            or job.get("operation") not in _OPERATIONS
            or type(job.get("owner_pid")) is not int
            or job["owner_pid"] < 1
            or job.get("status")
            not in {"running", "completed", "cancelled", "failed", "interrupted"}
            or type(job.get("automatic")) is not bool
        ):
            raise HauteValidationError("Invalid stage job record")
        return value

    @classmethod
    def _check_record(cls, record: Any) -> None:
        if (
            not isinstance(record, dict)
            or set(record) != _RECORD
            or not _HEX32.fullmatch(str(record["revision"]))
            or not _HEX32.fullmatch(str(record["build_id"]))
            or not (
                record["parent_revision"] is None
                or _HEX32.fullmatch(str(record["parent_revision"]))
            )
            or not _HEX64.fullmatch(str(record["sha256"]))
            or not _HEX64.fullmatch(str(record["config_digest"]))
            or type(record["size"]) is not int
            or not 0 <= record["size"] <= REVISION_LIMIT
            or not isinstance(record["view"], dict)
            or set(record["view"]) != {"sha256", "size"}
            or not _HEX64.fullmatch(str(record["view"]["sha256"]))
            or type(record["view"]["size"]) is not int
            or record["view"]["size"] < 0
        ):
            raise HauteValidationError("Invalid stage revision record")
        cls._check_result_job_id(record["result_job_id"])

    def _write(self, key: str, value: Mapping[str, Any]) -> None:
        encoded = _json({**value, "schema_version": _SCHEMA})
        if len(encoded) > _MANIFEST_LIMIT:
            raise HauteValidationError("Stage metadata exceeds the manifest size limit")
        with atomic_path(self._directory(key) / "manifest.json") as temporary:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _records(value: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        for name in ("current", "preview", "final"):
            if value[name] is not None:
                yield value[name]
        for checkpoint in value["checkpoints"]:
            yield checkpoint["record"]

    @staticmethod
    def _check_result_job_id(value: Any) -> str | None:
        if value is not None and (not isinstance(value, str) or len(value) > 128):
            raise HauteValidationError("Invalid stage result job identity")
        return value

    @classmethod
    def _result_job_id(cls, result: Mapping[str, Any]) -> str | None:
        response = result.get("response")
        return cls._check_result_job_id(
            response.get("job_id") if isinstance(response, dict) else None
        )

    # -- revisions and results --------------------------------------------------------------

    @staticmethod
    def _manifest(objects: StageObjects, record: Mapping[str, Any]) -> RevisionManifest:
        return objects.read_revision(
            record["revision"], sha256=record["sha256"], size=record["size"]
        )

    def _load_result(
        self, key: str, record: Mapping[str, Any], *, compact: bool = False
    ) -> dict[str, Any]:
        """A record's store result, hydrated from its revision's objects.

        A compact read returns the revision's persisted compact view. The pointer record
        carries the view's digest, so status reads verify and parse only the view, never
        the revision manifest.
        """
        objects = self._objects(key)
        if compact:
            view = objects.read_json(
                ObjectRef(record["view"]["sha256"], record["view"]["size"], "json")
            )
            if not isinstance(view, dict):
                raise HauteValidationError("Stage revision has no valid compact view")
            return view
        manifest = self._manifest(objects, record)

        def get(name: str) -> Any:
            ref = manifest.objects.get(name)
            if ref is None:
                raise HauteValidationError(f"Stage revision lacks object {name!r}")
            return objects.read_json(ref)

        return hydrate(manifest.result, get, compact=compact)

    @contextmanager
    def _pinned(self, key: str, value: Mapping[str, Any]) -> Iterator[None]:
        """Pin every revision *value* names; enter it while holding the pointer lock."""
        with ExitStack() as stack:
            roots = self._roots(value)
            if roots:
                objects = self._objects(key)
                for revision, digest in sorted(roots.items()):
                    stack.enter_context(objects.pin(revision, digest))
            yield

    def _roots(self, value: Mapping[str, Any]) -> dict[str, str]:
        """Each revision *value* names and its manifest's digest."""
        return {record["revision"]: record["sha256"] for record in self._records(value)}

    @staticmethod
    def _copy_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
        # Copy record envelopes, never diagnostic arrays.
        result = dict(value)
        for name in ("current", "preview", "final"):
            if value[name] is not None:
                result[name] = dict(value[name])
        result["checkpoints"] = [
            {**item, "record": dict(item["record"])} for item in value["checkpoints"]
        ]
        return result

    def _hydrate(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        compact: bool = False,
        known: Mapping[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """The public stage state of *value*; call it with *value*'s revisions pinned.

        *known* maps a revision to its store result already in memory.
        """
        hydrated = self._copy_manifest(value)
        # Storage versioning is private; the public stage-state contract is unchanged.
        hydrated["schema_version"] = 1
        results = {
            revision: compact_result(result) if compact else result
            for revision, result in (known or {}).items()
        }
        for record in self._records(hydrated):
            revision = record["revision"]
            if revision not in results:
                results[revision] = self._load_result(key, record, compact=compact)
            record["result"] = results[revision]
            for private in _PRIVATE:
                record.pop(private)
        return hydrated

    def _collect(self, key: str, value: Mapping[str, Any], *, reclaim: bool = False) -> None:
        """Delete what *value*'s revisions and live pins no longer reach; hold the pointer lock.

        With *reclaim*, an older store's files go too. This housekeeping follows a committed
        pointer change, so any failure is logged and left for a later collection; it never
        turns a committed change into an error.
        """
        objects = self._objects(key)
        try:
            if reclaim:
                objects.reclaim_legacy()
            objects.collect(self._roots(value))
        except Exception as exc:
            logger.error("stage_collection_failed", key=key, error=str(exc))

    def _record(
        self, prepared: PreparedStage, *, build_id: str, parent_revision: str | None
    ) -> dict[str, Any]:
        return {
            "revision": prepared.revision,
            "build_id": build_id,
            "parent_revision": parent_revision,
            "sha256": prepared.sha256,
            "size": prepared.size,
            "config_digest": prepared.config_digest,
            "result_job_id": self._result_job_id(prepared.result),
            "view": {
                "sha256": prepared.manifest.objects["view"].sha256,
                "size": prepared.manifest.objects["view"].size,
            },
        }

    # -- reads ------------------------------------------------------------------------------

    def snapshot(self, key: str, *, compact: bool = False) -> dict[str, Any]:
        with ExitStack() as pins:
            with self._lock(key):
                value = self._read(key)
                pins.enter_context(self._pinned(key, value))
            return self._hydrate(key, value, compact=compact)

    def recover(self, key: str, *, compact: bool = False) -> dict[str, Any]:
        with ExitStack() as pins:
            with self._lock(key):
                value = self._read(key)
                changed = False
                queued = value["next_preview"]
                if (
                    queued
                    and queued["status"] == "queued"
                    and not process_is_alive(queued["owner_pid"])
                ):
                    value["next_preview"] = {
                        **queued,
                        "status": "interrupted",
                        "message": (
                            "Server stopped before preparing the next preview. "
                            "Use Preview to retry."
                        ),
                    }
                    changed = True
                job = value["job"]
                if (
                    job is not None
                    and job["status"] == "running"
                    and not process_is_alive(job["owner_pid"])
                ):
                    value["job"] = {
                        **job,
                        "status": "interrupted",
                        "message": "Server stopped before this stage completed",
                    }
                    changed = True
                if changed:
                    self._write(key, value)
                pins.enter_context(self._pinned(key, value))
            return self._hydrate(key, value, compact=compact)

    @contextmanager
    def lease(self, key: str, revision: str) -> Iterator[StoredRevision]:
        """A revision pinned for reading in place; it stays readable until released."""
        with ExitStack() as pins:
            with self._lock(key):
                value = self._read(key)
                record = next(
                    (record for record in self._records(value) if record["revision"] == revision),
                    None,
                )
                if record is None:
                    raise ConflictError("This stage revision is no longer available")
                objects = self._objects(key)
                pins.enter_context(objects.pin(revision, record["sha256"]))
            yield StoredRevision(objects, self._manifest(objects, record))

    # -- named checkpoints ------------------------------------------------------------------

    def checkpoint(
        self,
        key: str,
        *,
        action: str,
        expected_revision: str,
        name: str = "",
        checkpoint_id: str | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if action not in {"save", "restore", "delete"}:
            raise HauteValidationError("Unknown checkpoint action")
        with self._lock(key):
            value = self._read(key)
            if (
                value["current"] is None
                or value["current"]["revision"] != expected_revision
                or (value["job"] is not None and value["job"]["status"] == "running")
            ):
                raise ConflictError("The model changed or is running; refresh before continuing")
            selected = next(
                (item for item in value["checkpoints"] if item["id"] == checkpoint_id), None
            )
            if action == "save":
                name = name.strip()
                if not 1 <= len(name) <= 80:
                    raise HauteValidationError("Checkpoint names need 1–80 characters")
                if len(value["checkpoints"]) >= 10:
                    raise HauteValidationError(
                        "Remove a checkpoint before saving another (limit 10)"
                    )
                value["checkpoints"].append(
                    {
                        "id": uuid.uuid4().hex,
                        "name": name,
                        "created_at": datetime.now(UTC).isoformat(),
                        "record": copy.deepcopy(value["current"]),
                    }
                )
            elif selected is None:
                raise ConflictError("This checkpoint is no longer available")
            elif action == "delete":
                value["checkpoints"].remove(selected)
            else:
                self._manifest(self._objects(key), selected["record"])  # still intact
                if validate is not None:
                    record = {
                        **{
                            field: item
                            for field, item in selected["record"].items()
                            if field not in _PRIVATE
                        },
                        "result": self._load_result(key, selected["record"]),
                    }
                    validate(record)
                value.update(
                    current=copy.deepcopy(selected["record"]),
                    preview=None,
                    final=None,
                    job=None,
                    next_preview=None,
                )
            hydrated = self._hydrate(key, value)
            self._write(key, value)
            self._collect(key, value)
            return hydrated

    # -- jobs and publication ---------------------------------------------------------------

    def begin(
        self,
        key: str,
        *,
        job_id: str,
        operation: str,
        expected_revision: str | None,
        queued_preview: bool = False,
    ) -> None:
        if operation not in _OPERATIONS or not job_id:
            raise HauteValidationError("Invalid stage operation or job id")
        with self._lock(key):
            value = self._read(key)
            if value["job"] is not None and value["job"]["status"] == "running":
                raise ConflictError("A stage job is already running for this model")
            current = value["current"]
            revision = current["revision"] if current else None
            if operation != "full" and (revision is None or revision != expected_revision):
                raise ConflictError("The stage parent changed; refresh the model before continuing")
            queued = value["next_preview"]
            if queued_preview and (
                not queued
                or queued["status"] != "queued"
                or queued["parent_revision"] != revision
                or queued["operation"] != operation
            ):
                raise ConflictError("The automatic preview is no longer queued")
            value["next_preview"] = None
            value["job"] = {
                "id": job_id,
                "operation": operation,
                "automatic": queued_preview,
                "expected_revision": revision,
                "owner_pid": os.getpid(),
                "status": "running",
                "message": "Running",
            }
            self._write(key, value)

    def prepare(
        self,
        key: str,
        source: Path,
        *,
        result: Mapping[str, Any],
        config_digest: str,
        parent_revision: str | None = None,
    ) -> PreparedStage:
        """Stage a stage archive's new objects and the result's diagnostics as a revision.

        A delta archive inherits members from *parent_revision*, the revision it was built
        on. Each inherited member must be in that revision's verified closure with an
        identical reference. Nothing is published; `publish`, `apply_path` or
        `discard_prepared` follows.
        """
        if not _HEX64.fullmatch(config_digest):
            raise HauteValidationError("Stage fit configuration needs a SHA-256 digest")
        archive = StageArchive.read(source)
        if archive.manifest.result:
            raise HauteValidationError("A stage artifact cannot carry a result envelope")
        objects = self._objects(key)
        parent = None
        if archive.inherited:
            parent = self._parent_manifest(key, parent_revision)
        staged = archive.stage_into(objects, parent=parent)
        return self._revision(
            key,
            objects,
            dict(archive.manifest.objects),
            staged,
            metadata=archive.manifest.metadata,
            result=result,
            config_digest=config_digest,
        )

    def prepare_members(
        self,
        key: str,
        members: Mapping[str, ObjectRef | bytes],
        *,
        metadata: Mapping[str, Any],
        result: Mapping[str, Any],
        config_digest: str,
        parent_revision: str,
    ) -> PreparedStage:
        """A revision built directly from *parent_revision*'s objects plus new JSON objects.

        This is Apply: an `ObjectRef` member is inherited and must be in the parent's
        verified closure with an identical reference; a bytes member is staged as a new
        object. No archive is written or read.
        """
        if not _HEX64.fullmatch(config_digest):
            raise HauteValidationError("Stage fit configuration needs a SHA-256 digest")
        parent = self._parent_manifest(key, parent_revision)
        objects = self._objects(key)
        refs: dict[str, ObjectRef] = {}
        staged: list[StagedObject] = []
        try:
            for name, value in sorted(members.items()):
                if isinstance(value, ObjectRef):
                    if parent.objects.get(name) != value:
                        raise HauteValidationError(
                            f"Stage revision inherits {name!r}, which its parent does not hold"
                        )
                    refs[name] = value
                    continue
                item = objects.stage_bytes(value)
                staged.append(item)
                refs[name] = ObjectRef(item.sha256, item.size, object_kind(name))
        except BaseException:
            for item in staged:
                _discard(item.path)
            raise
        return self._revision(
            key,
            objects,
            refs,
            staged,
            metadata=metadata,
            result=result,
            config_digest=config_digest,
        )

    def _revision(
        self,
        key: str,
        objects: StageObjects,
        refs: dict[str, ObjectRef],
        staged: list[StagedObject],
        *,
        metadata: Mapping[str, Any],
        result: Mapping[str, Any],
        config_digest: str,
    ) -> PreparedStage:
        """Add the result's diagnostics and compact view to *refs*; build the revision."""
        try:
            payloads: dict[str, bytes] = {}
            envelope = split(result, payloads.__setitem__)
            # The revision's compact view: what status reads return, without decoding the
            # complete histories they would otherwise thin.
            payloads["view"] = canonical_json(compact_result(result))
            for name, payload in sorted(payloads.items()):
                item = objects.stage_bytes(payload)
                staged.append(item)
                refs[name] = ObjectRef(item.sha256, item.size, object_kind(name))
            revision = uuid.uuid4().hex
            manifest = RevisionManifest(revision, refs, envelope, metadata)
            encoded = manifest.encode()
            return PreparedStage(
                key,
                revision,
                hashlib.sha256(encoded).hexdigest(),
                len(encoded),
                config_digest,
                copy.deepcopy(dict(result)),
                manifest,
                tuple(staged),
            )
        except BaseException:
            for item in staged:
                _discard(item.path)
            raise

    def _parent_manifest(self, key: str, revision: str | None) -> RevisionManifest:
        """The verified manifest of a revision the pointer still names, for a delta's ingest."""
        with self._lock(key):
            record = next(
                (item for item in self._records(self._read(key)) if item["revision"] == revision),
                None,
            )
            if record is None:
                raise ConflictError("The stage parent changed; refresh before continuing")
            return self._manifest(self._objects(key), record)

    def parent_reference(self, key: str, revision: str) -> dict[str, Any]:
        """What a worker needs to read *revision* in place: its scope and manifest digest."""
        with self._lock(key):
            record = next(
                (item for item in self._records(self._read(key)) if item["revision"] == revision),
                None,
            )
            if record is None:
                raise ConflictError("The stage parent changed; refresh before continuing")
        return {
            "project_root": str(self.project_root),
            "key": key,
            "revision": revision,
            "sha256": record["sha256"],
            "size": record["size"],
        }

    @classmethod
    def open_parent(cls, reference: Mapping[str, Any]) -> StoredRevision:
        """A worker's read-only view of its parent revision; it writes nothing to the store.

        It takes no pin and no token: the running-job record keeps the parent rooted. If
        the parent was nevertheless collected (an orphaned worker), reads fail validation.
        """
        if not isinstance(reference, Mapping) or set(reference) != {
            "project_root",
            "key",
            "revision",
            "sha256",
            "size",
        }:
            raise HauteValidationError("Invalid stage parent reference")
        store = cls(Path(reference["project_root"]))
        objects = store._objects(reference["key"])
        if not _HEX64.fullmatch(str(reference["sha256"])) or type(reference["size"]) is not int:
            raise HauteValidationError("Invalid stage parent reference")
        manifest = objects.read_revision(
            reference["revision"], sha256=reference["sha256"], size=reference["size"]
        )
        return StoredRevision(objects, manifest)

    def discard_prepared(self, prepared: PreparedStage) -> None:
        """Remove an unpublished revision's staged objects; installed objects stay."""
        for item in prepared.staged:
            _discard(item.path)  # may follow a commit, so never raises

    def publish(
        self,
        key: str,
        *,
        job_id: str,
        prepared: PreparedStage,
        preview: bool = False,
        next_preview: str | None = None,
    ) -> dict[str, Any]:
        if prepared.key != key:
            raise HauteValidationError("Prepared stage belongs to another model")
        objects = self._objects(key)
        try:
            with objects.publishing(prepared.manifest, prepared.staged):
                with self._lock(key):
                    value = self._read(key)
                    job, current = value["job"], value["current"]
                    revision = current["revision"] if current else None
                    if (
                        job is None
                        or job["id"] != job_id
                        or job["status"] != "running"
                        or job["expected_revision"] != revision
                    ):
                        raise ConflictError("This stage job or its parent is no longer current")
                    if preview != (job["operation"] in {"pruned", "banded", "smoothed"}):
                        raise HauteValidationError(
                            "Post-fit stages must publish a preview before Apply"
                        )
                    if next_preview is not None and job["operation"] != "full":
                        raise HauteValidationError("Only an applied full fit can queue a preview")
                    record = self._record(
                        prepared,
                        build_id=uuid.uuid4().hex
                        if job["operation"] == "full"
                        else current["build_id"],
                        parent_revision=revision,
                    )
                    slot = (
                        "final"
                        if job["operation"] == "finalise"
                        else "preview"
                        if preview
                        else "current"
                    )
                    value[slot] = record
                    if slot == "current":
                        value["preview"] = None
                        value["final"] = None
                    if next_preview is not None:
                        value["next_preview"] = self._preview_queue(next_preview, prepared.revision)
                    value["job"] = {**job, "status": "completed", "message": "Completed"}
                    # The response is prepared before the commit, which is never rolled back.
                    hydrated = self._hydrate(key, value, known={prepared.revision: prepared.result})
                    self._write(key, value)
                    # A full build replaces any older store; reclaim its files now.
                    self._collect(key, value, reclaim=job["operation"] == "full")
                    return hydrated
        finally:
            self.discard_prepared(prepared)

    @staticmethod
    def _preview_queue(operation: str, revision: str) -> dict[str, Any]:
        if operation not in {"pruned", "banded", "smoothed"}:
            raise HauteValidationError("Invalid next-stage preview operation")
        return {
            "operation": operation,
            "parent_revision": revision,
            "owner_pid": os.getpid(),
            "status": "queued",
            "message": "Preparing the next stage preview",
        }

    def preview_start_failed(self, key: str, *, revision: str, message: str) -> None:
        with self._lock(key):
            value = self._read(key)
            queued = value["next_preview"]
            if queued and queued["status"] == "queued" and queued["parent_revision"] == revision:
                value["next_preview"] = {**queued, "status": "failed", "message": message}
                self._write(key, value)

    def finish(self, key: str, *, job_id: str, status: str, message: str) -> None:
        if status not in {"failed", "cancelled", "interrupted"}:
            raise HauteValidationError("Invalid terminal stage status")
        with self._lock(key):
            value = self._read(key)
            job = value["job"]
            if job is not None and job["id"] == job_id and job["status"] == "running":
                value["job"] = {**job, "status": status, "message": message}
                self._write(key, value)

    def cancel_followup(self, key: str, *, parent_job_id: str) -> str | None:
        """Stop a queued handoff, or identify only this parent's running continuation."""
        with self._lock(key):
            value = self._read(key)
            current = value["current"]
            if current is None or current["result_job_id"] != parent_job_id:
                return None
            revision = current["revision"]
            queued = value["next_preview"]
            if queued and queued["status"] == "queued" and queued["parent_revision"] == revision:
                value["next_preview"] = None
                self._write(key, value)
            job = value["job"]
            if (
                job
                and job["status"] == "running"
                and job["automatic"]
                and job["expected_revision"] == revision
            ):
                return str(job["id"])
            return None

    def apply(
        self,
        key: str,
        *,
        expected_revision: str,
        preview_revision: str,
        next_preview: str | None = None,
    ) -> dict[str, Any]:
        with self._lock(key):
            value = self._read(key)
            if (
                value["current"] is None
                or value["preview"] is None
                or value["current"]["revision"] != expected_revision
                or value["preview"]["revision"] != preview_revision
                or value["preview"]["parent_revision"] != expected_revision
                or (value["job"] is not None and value["job"]["status"] == "running")
            ):
                raise ConflictError("The stage preview or its parent changed; refresh before Apply")
            self._manifest(self._objects(key), value["preview"])  # still intact
            value["current"], value["preview"] = value["preview"], None
            value["next_preview"] = (
                self._preview_queue(next_preview, preview_revision) if next_preview else None
            )
            value["final"] = None
            hydrated = self._hydrate(key, value)
            self._write(key, value)
            self._collect(key, value)
            return hydrated

    def apply_path(
        self,
        key: str,
        *,
        expected_revision: str,
        artifact_revision: str,
        prepared: PreparedStage,
        next_preview: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace current with an evaluated point from current or preview."""
        if prepared.key != key:
            raise HauteValidationError("Prepared stage belongs to another model")
        if next_preview is not None and next_preview not in {"pruned", "banded", "smoothed"}:
            raise HauteValidationError("Invalid next path operation")
        objects = self._objects(key)
        try:
            with objects.publishing(prepared.manifest, prepared.staged):
                with self._lock(key):
                    value = self._read(key)
                    current, preview, job = value["current"], value["preview"], value["job"]
                    owner = next(
                        (
                            record
                            for record in (current, preview)
                            if record and record["revision"] == artifact_revision
                        ),
                        None,
                    )
                    if (
                        current is None
                        or current["revision"] != expected_revision
                        or owner is None
                        or (owner is preview and owner["parent_revision"] != expected_revision)
                        or (job is not None and job["status"] == "running")
                    ):
                        raise ConflictError("The path or its parent changed; refresh before Apply")
                    value.update(
                        current=self._record(
                            prepared,
                            build_id=current["build_id"],
                            parent_revision=expected_revision,
                        ),
                        preview=None,
                        final=None,
                        next_preview=self._preview_queue(next_preview, prepared.revision)
                        if next_preview
                        else None,
                    )
                    hydrated = self._hydrate(key, value, known={prepared.revision: prepared.result})
                    self._write(key, value)
                    self._collect(key, value)
                    return hydrated
        finally:
            self.discard_prepared(prepared)

    def discard(self, key: str, *, preview_revision: str) -> dict[str, Any]:
        with self._lock(key):
            value = self._read(key)
            if value["preview"] is None or value["preview"]["revision"] != preview_revision:
                raise ConflictError("The stage preview changed; refresh before discarding")
            value["preview"] = None
            hydrated = self._hydrate(key, value)
            self._write(key, value)
            self._collect(key, value)
            return hydrated

    # -- removal and collection -------------------------------------------------------------

    def remove(self, key: str) -> None:
        """Detach a removed model atomically; active readers keep their pinned revisions."""
        with self._lock(key):
            value = self._read(key)
            if value["job"] is not None and value["job"]["status"] == "running":
                raise ConflictError("Cancel the running stage before removing its model")
            value.update(current=None, preview=None, final=None, checkpoints=[], next_preview=None)
            self._write(key, value)
            self._collect(key, value)

    def cleanup(self, key: str) -> None:
        """Collect what the pointer and live pins no longer reach (dead staging included)."""
        directory = self._directory(key)
        if not any(
            (directory / name).exists() for name in ("revisions", "objects", "staging", "leases")
        ):
            return  # nothing was ever stored for this model
        with self._lock(key):
            self._collect(key, self._read(key))
