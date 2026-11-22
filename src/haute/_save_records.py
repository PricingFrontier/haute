"""A pipeline save's progress and outcome, on disk under the clone's ``.haute/saves/``.

A save writes its record (``records/``, one file per pipeline file, holding each of its saves
still to settle) before it replaces its first file: the operation it names and its request's
digest, the revision it replaces, and every file it writes or removes. It marks the record
committed once its files are written and parsed back, with its answer as it stands, and
removes the record once its removals are made and captured. A committed save that named an
operation leaves a receipt (``receipts/``) of its answer, the newest :data:`MAX_SAVE_RECEIPTS`
kept, so a repeat of it is answered with what it did after a restart too.

A record names the bytes the pipeline's code files hold while it is the checkout's
(*holds*): what each held before the save or was being written, for a save that stopped
part-way; what the save left in each, once it committed. Only a save or a start on a checkout
whose code still holds them settles it (:func:`holds_here`), since its removals and capture
are that code's: a save on another branch, whatever history the two share, neither finishes
its work, which could remove a file that branch needs, nor replaces it, and it waits for its
files to be checked out again. Nothing of git is asked, so a folder git refuses to work in
changes nothing. A record naming none (one that cannot be read, or one written before
records named theirs) is the checkout's wherever the project is. Every change to a pipeline
file's records is one write of its file, so a save that rolls back leaves the records it
found.

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
import uuid
from collections.abc import Collection, Iterator, Sequence
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

#: How many records one pipeline file keeps: beyond it, the oldest committed ones, whose work
#: is only removals and a capture, are forgotten (each a stale file left behind, harmless).
MAX_RECORDS_PER_FILE = 16

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
class Held:
    """A file a save's record names with the bytes it holds while the record is the
    checkout's: each a digest, None for no file there."""

    file: str
    digests: tuple[str | None, ...]


def _new_id() -> str:
    return uuid.uuid4().hex


#: The name a record kept before a pipeline file kept several is read under.
_LEGACY_ID = "legacy"


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
    a save that stopped: *readable* is False and it names no files. *id* names it among its
    pipeline file's records, from the save's start to its finish; *holds* are the bytes its
    pipeline's code files hold while it is the checkout's (:func:`holds_here`)."""

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
    id: str = field(default_factory=_new_id)
    holds: tuple[Held, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_file": self.source_file,
            "holds": [{"file": h.file, "digests": list(h.digests)} for h in self.holds],
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
    # A record kept before a pipeline file kept several names neither: it holds nothing.
    record_id, holds = raw.get("id", _LEGACY_ID), raw.get("holds", [])
    if not (
        _project_file(recorded)
        and isinstance(recorded, str)
        # One file, however a case-insensitive file system spells it (the record's key).
        and recorded.casefold() == source_file.casefold()
        and isinstance(record_id, str)
        and record_id
        and isinstance(holds, list)
        and all(
            isinstance(h, dict)
            and _project_file(h.get("file"))
            and isinstance(h.get("digests"), list)
            and all(_optional_str(d) for d in h["digests"])
            for h in holds
        )
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
        id=record_id,
        holds=tuple(Held(h["file"], tuple(h["digests"])) for h in holds),
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


def _read_records(path: Path, source_file: str) -> list[SaveRecord]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = None
    # A record kept before a pipeline file kept several is the file itself.
    entries = raw.get("saves") if isinstance(raw, dict) and "saves" in raw else [raw]
    if not isinstance(entries, list):
        entries = [None]
    records: list[SaveRecord] = []
    for number, entry in enumerate(entries):
        record = _parse_record(entry, source_file)
        if record is None:
            logger.warning("save_record_unreadable", path=str(path))
            record = SaveRecord(source_file=source_file, readable=False, id=f"unreadable-{number}")
        records.append(record)
    return records


def records_of(project_root: Path, source_file: str) -> list[SaveRecord]:
    """The records of the saves of *source_file* still to settle, oldest first; one that
    cannot be read is a record naming no files, *readable* False."""
    return _read_records(_record_path(project_root, source_file), source_file)


def read_record(project_root: Path, source_file: str, record_id: str) -> SaveRecord | None:
    """The record *record_id* of a save of *source_file*, or None when there is none."""
    return next((r for r in records_of(project_root, source_file) if r.id == record_id), None)


def put_records(
    project_root: Path,
    source_file: str,
    records: Sequence[SaveRecord] = (),
    *,
    dropping: Collection[str] = (),
) -> None:
    """In one write of *source_file*'s records: forget those *dropping* names and the ones
    *records* replace, and put *records* after the others. Beyond
    :data:`MAX_RECORDS_PER_FILE`, the oldest committed records go."""
    path = _record_path(project_root, source_file)
    replaced = {*dropping, *(record.id for record in records)}
    kept = [record for record in _read_records(path, source_file) if record.id not in replaced]
    excess = len(kept) + len(records) - MAX_RECORDS_PER_FILE
    for record in [record for record in kept if record.committed][: max(excess, 0)]:
        kept.remove(record)
        logger.warning("save_record_forgotten", source_file=record.source_file, id=record.id)
    every = [*kept, *records]
    if not every:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source_file": every[-1].source_file, "saves": [r.to_json() for r in every]}
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def write_record(
    project_root: Path, record: SaveRecord, *, replacing: Collection[str] = ()
) -> None:
    """Write *record*, in the one write replacing the records *replacing* names."""
    put_records(project_root, record.source_file, [record], dropping=replacing)


def clear_record(project_root: Path, source_file: str, record_id: str) -> None:
    """Forget the record *record_id* of a save of *source_file*: it finished, or it was put
    back."""
    put_records(project_root, source_file, dropping=[record_id])


def file_digest(path: Path) -> str | None:
    """sha256 of *path*'s bytes, or None when it is no file."""
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def holds_here(project_root: Path, record: SaveRecord) -> bool:
    """Whether *record* is the checkout's: each file it holds holds one of the bytes it names,
    so its removals and capture are made against the code they were planned for. One that
    names none is the checkout's wherever the project is; one naming a file that cannot be
    read, or that leaves the project, is not."""
    from haute._sandbox import is_contained

    root = project_root.resolve()
    for held in record.holds:
        path = root / held.file
        try:
            if not is_contained(root, path) or file_digest(path) not in held.digests:
                return False
        except OSError:
            return False
    return True


def held_records(project_root: Path, source_file: str) -> list[SaveRecord]:
    """The records of saves of *source_file* the checkout holds (:func:`holds_here`)."""
    return [r for r in records_of(project_root, source_file) if holds_here(project_root, r)]


def saved_records(project_root: Path) -> list[SaveRecord]:
    """Every readable record in the project. A file of records that names no pipeline file is
    left for its pipeline's document to report."""
    folder = _saves_folder(project_root, _RECORDS)
    records: list[SaveRecord] = []
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return records
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("save_record_unreadable", path=str(path))
            continue
        source_file = raw.get("source_file") if isinstance(raw, dict) else None
        if not (
            isinstance(source_file, str)
            and _project_file(source_file)
            and _record_path(project_root, source_file) == path
        ):
            logger.warning("save_record_unreadable", path=str(path))
            continue
        records.extend(r for r in _read_records(path, source_file) if r.readable)
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
    """The record of a save of *source_file* the checkout holds that stopped before it
    committed, or None."""
    if is_running(project_root, source_file):
        return None
    try:
        held = held_records(project_root, source_file)
    except SaveRecordsUnavailableError:
        # A folder reached through a link holds no record a save of Haute's wrote: the load
        # reports none, and the next Save refuses with the link named.
        return None
    return next((record for record in held if not record.committed), None)


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
