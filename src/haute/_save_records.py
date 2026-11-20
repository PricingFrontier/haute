"""A pipeline save's progress and outcome, on disk under the clone's ``.haute/saves/``.

A save writes its record (``records/``, one per pipeline file) before it replaces its first
file: the operation it names and its request's digest, the revision it replaces, and every
file it writes or removes. It marks the record committed once its files are written and
parsed back, with its answer as it stands, and removes the record once its removals are made
and captured. A committed save that named an operation leaves a receipt (``receipts/``) of its
answer, the newest :data:`MAX_SAVE_RECEIPTS` kept, so a repeat of it is answered with what it
did after a restart too.

A record no save of this process is running (:func:`saving`) is one a server died in, or one
whose rollback failed, or whose removals did not all succeed: the save route and the server's
start settle it (``haute.routes._save_pipeline.settle_saves``), and one that never committed is
reported on its pipeline's document (:func:`interruption_words`) until a save of the pipeline
lands. Every read and write happens under the repository's lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from haute._file_ops import atomic_write_text
from haute._git_state import name_files
from haute._logging import get_logger
from haute.errors import ConflictError
from haute.schemas import SavePipelineRequest, SavePipelineResponse

logger = get_logger(component="save_records")

#: How many committed saves a repeat can still be answered for.
MAX_SAVE_RECEIPTS = 256

_SAVES = (".haute", "saves")
_RECORDS = "records"
_RECEIPTS = "receipts"


class SaveOperationReusedError(Exception):
    """An operation names a committed save whose request was different."""


class SaveRecordsUnavailableError(ConflictError):
    """The project's ``.haute/saves/`` folder is reached through a link: a save's record
    and its receipts are kept only in the project's own folder, which a save would otherwise
    write and a settle remove files by."""


def save_request_digest(body: SavePipelineRequest) -> str:
    """The request as a repeat must send it again: without the operation, and without
    the connection, which a tab may have reconnected before repeating. The graph is the
    one the request reads (CGEN-13), so the parts it read from the request are named
    beside it: two requests that send the same values, one with a part's saved form and
    one without, are two saves."""
    from haute._cache import canonical_json

    payload = body.model_dump_json(exclude={"operation", "connection_id"})

    def refs(parts: frozenset[tuple[str | None, str]]) -> list[list[str | None]]:
        return [list(ref) for ref in sorted(parts, key=lambda ref: (ref[0] or "", ref[1]))]

    read = canonical_json([refs(body.edited_steps), refs(body.edited_settings)])
    return hashlib.sha256(f"{payload}\n{read}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Removal:
    """A file a save removes once it has committed, with the digest of the bytes it held when
    the save planned the removal: a file that holds other bytes since is left alone."""

    file: str
    digest: str | None


@dataclass(frozen=True, slots=True)
class SaveRecord:
    """A save of the pipeline file *source_file* (project-relative, ``/``-separated).

    *writes* are the files it writes or deletes in its transaction, and for a save made over
    the record of one that never committed, that one's files too: each may hold either save
    until this one commits, and its capture takes each that is on disk or known to git (a
    deletion that save made is still to record; a file it never came to create is not).
    *leftover* are files whose staging could not yet be removed: a settle removes the staging
    beside them and the files the record writes, and they are never captured. *removes* are
    the stale files it removes once committed. *answer* is the response as it stood when the
    save committed, a ``SavePipelineResponse`` in JSON. A record that cannot be read is still
    a save that stopped: *readable* is False and it names no files."""

    source_file: str
    operation: str | None = None
    digest: str | None = None
    base_revision: str | None = None
    writes: tuple[str, ...] = ()
    leftover: tuple[str, ...] = ()
    removes: tuple[Removal, ...] = ()
    committed: bool = False
    answer: dict[str, object] | None = field(default=None, compare=False)
    readable: bool = True

    def to_json(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "operation": self.operation,
            "digest": self.digest,
            "base_revision": self.base_revision,
            "writes": list(self.writes),
            "leftover": list(self.leftover),
            "removes": [{"file": r.file, "digest": r.digest} for r in self.removes],
            "committed": self.committed,
            "answer": self.answer,
        }


def _optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _project_file(value: object) -> bool:
    """Whether *value* is a project-relative, ``/``-separated file path as a record names one:
    never absolute, never climbing, never another drive."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in value.split("/"))


def _parse_record(raw: object, source_file: str) -> SaveRecord | None:
    if not isinstance(raw, dict):
        return None
    writes, leftover = raw.get("writes"), raw.get("leftover")
    removes, answer = raw.get("removes"), raw.get("answer")
    recorded = raw.get("source_file")
    if not (
        _project_file(recorded)
        and isinstance(recorded, str)
        # One file, however a case-insensitive file system spells it (the record's key).
        and recorded.casefold() == source_file.casefold()
        and _optional_str(raw.get("operation"))
        and _optional_str(raw.get("digest"))
        and _optional_str(raw.get("base_revision"))
        and isinstance(writes, list)
        and all(_project_file(path) for path in writes)
        and isinstance(leftover, list)
        and all(_project_file(path) for path in leftover)
        and isinstance(removes, list)
        and all(
            isinstance(r, dict) and _project_file(r.get("file")) and _optional_str(r.get("digest"))
            for r in removes
        )
        and isinstance(raw.get("committed"), bool)
        and (answer is None or isinstance(answer, dict))
    ):
        return None
    return SaveRecord(
        source_file=recorded,
        operation=raw["operation"],
        digest=raw["digest"],
        base_revision=raw["base_revision"],
        writes=tuple(writes),
        leftover=tuple(leftover),
        removes=tuple(Removal(r["file"], r["digest"]) for r in removes),
        committed=raw["committed"],
        answer=answer,
    )


def _saves_folder(project_root: Path, name: str) -> Path:
    """``.haute/saves/<name>`` in the project, refused when it is reached through a link:
    what a save writes there, and what a settle removes files by, stays in the project."""
    root = project_root.resolve()
    folder = root.joinpath(*_SAVES, name)
    if os.path.normcase(folder.resolve()) != os.path.normcase(folder):
        raise SaveRecordsUnavailableError(
            "Saves are recorded in the project's .haute/saves/ folder, which is reached "
            "through a link. Remove the link and save again."
        )
    return folder


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _record_path(project_root: Path, source_file: str) -> Path:
    # One case, so the spellings a case-insensitive file system gives one file share it.
    return _saves_folder(project_root, _RECORDS) / f"{_key(source_file.casefold())}.json"


def write_record(project_root: Path, record: SaveRecord) -> None:
    """Write *record*, replacing the one its pipeline file had."""
    path = _record_path(project_root, record.source_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(record.to_json(), indent=2) + "\n")


def read_record(project_root: Path, source_file: str) -> SaveRecord | None:
    """The record of a save of *source_file*, or None when there is none."""
    path = _record_path(project_root, source_file)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        record = _parse_record(json.loads(text), source_file)
    except json.JSONDecodeError:
        record = None
    if record is None:
        logger.warning("save_record_unreadable", path=str(path))
        return SaveRecord(source_file=source_file, readable=False)
    return record


def clear_record(project_root: Path, source_file: str) -> None:
    """Forget the record of a save of *source_file*: it finished, or was put back."""
    _record_path(project_root, source_file).unlink(missing_ok=True)


def saved_records(project_root: Path) -> list[SaveRecord]:
    """Every readable record in the project. One that cannot be read names no pipeline file,
    so it is left for its pipeline's document to report."""
    folder = _saves_folder(project_root, _RECORDS)
    records: list[SaveRecord] = []
    try:
        entries = sorted(folder.glob("*.json"))
    except OSError:
        return records
    for path in entries:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("save_record_unreadable", path=str(path))
            continue
        source_file = raw.get("source_file") if isinstance(raw, dict) else None
        record = (
            _parse_record(raw, source_file)
            if isinstance(source_file, str) and _project_file(source_file)
            else None
        )
        if record is None or _record_path(project_root, record.source_file) != path:
            logger.warning("save_record_unreadable", path=str(path))
            continue
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# The saves this process is running: their records are not interruptions.
# ---------------------------------------------------------------------------

_running: set[str] = set()
_running_lock = threading.Lock()


def _running_key(project_root: Path, source_file: str) -> str:
    return f"{project_root.resolve()}\0{source_file.casefold()}"


@contextmanager
def saving(project_root: Path, source_file: str) -> Iterator[None]:
    """Run a save of *source_file*: while it runs, its record is not one that stopped."""
    key = _running_key(project_root, source_file)
    with _running_lock:
        _running.add(key)
    try:
        yield
    finally:
        with _running_lock:
            _running.discard(key)


def is_running(project_root: Path, source_file: str) -> bool:
    with _running_lock:
        return _running_key(project_root, source_file) in _running


def interrupted_save(project_root: Path, source_file: str) -> SaveRecord | None:
    """The record of a save of *source_file* that stopped before it committed, or None."""
    if is_running(project_root, source_file):
        return None
    try:
        record = read_record(project_root, source_file)
    except SaveRecordsUnavailableError:
        # A folder reached through a link holds no record a save of Haute's wrote: the load
        # reports none, and the next Save refuses with the link named.
        return None
    if record is None or record.committed:
        return None
    return record


def interruption_words(record: SaveRecord) -> tuple[str, str]:
    """What the pipeline's document says of *record*, a save that stopped part-way: the
    message, naming its files, and the remedy."""
    if not record.readable:
        message = "A save stopped part-way, and its record cannot be read."
    elif record.writes or record.leftover:
        files = list(dict.fromkeys([*record.writes, *record.leftover]))
        message = (
            "A save stopped part-way, so these files may each hold a different save: "
            f"{name_files(files)}."
        )
    else:
        message = "A save stopped part-way."
    return message, "Saving the pipeline writes them all again."


# ---------------------------------------------------------------------------
# Receipts: the answers of committed saves, by operation.
# ---------------------------------------------------------------------------


def _receipt_path(project_root: Path, operation: str) -> Path:
    return _saves_folder(project_root, _RECEIPTS) / f"{_key(operation)}.json"


def replay_receipt(project_root: Path, operation: str, digest: str) -> SavePipelineResponse | None:
    """The response of the committed save *operation* names, or None when no receipt is
    kept; ``SaveOperationReusedError`` when that save's request was different."""
    path = _receipt_path(project_root, operation)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        recorded_operation, recorded_digest = raw["operation"], raw["digest"]
        response = SavePipelineResponse.model_validate(raw["response"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        # Unreadable, it answers nothing: the repeat is saved against its base revision.
        logger.warning("save_receipt_unreadable", path=str(path))
        return None
    if recorded_operation != operation or recorded_digest != digest:
        raise SaveOperationReusedError(
            "The operation names a different save. Send a new save with a new operation."
        )
    return response


def keep_receipt(
    project_root: Path, operation: str, digest: str, response: SavePipelineResponse
) -> None:
    """Keep *response* as the answer to a repeat of *operation*, and forget the oldest
    receipts beyond :data:`MAX_SAVE_RECEIPTS`. A receipt already kept for *operation* stays:
    a settle that finishes a save again, after its receipt was written, captures nothing new
    and would answer with less."""
    path = _receipt_path(project_root, operation)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "operation": operation,
        "digest": digest,
        "response": response.model_dump(mode="json"),
    }
    atomic_write_text(path, json.dumps(payload) + "\n")
    _forget_oldest_receipts(path.parent)


def _forget_oldest_receipts(folder: Path) -> None:
    with os.scandir(folder) as entries:
        receipts = [
            (entry.stat(follow_symlinks=False).st_mtime_ns, entry.path)
            for entry in entries
            if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)
        ]
    if len(receipts) <= MAX_SAVE_RECEIPTS:
        return
    receipts.sort()
    for _mtime, path in receipts[: len(receipts) - MAX_SAVE_RECEIPTS]:
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
