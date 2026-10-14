"""The Files panels' routes: list, write, download and delete project files.

Two places (``haute._project_layout``). The pipeline folder's ``files/`` is
versioned: a write or delete there is captured on the save ledger at once, a
file larger than version history takes is refused, and so is one ``.gitignore``
would ignore. The project root's ``data/`` takes and deletes files and
``outputs/`` only downloads; neither is versioned, and a hosted app refuses
files there because its disk does not survive a restart. See specs/server-api.
"""

from __future__ import annotations

import errno
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from haute import _git
from haute._artifact_paths import PathProblem, ordinary_path_problem
from haute._file_ops import ensure_disk_headroom, publish_staged_file
from haute._git_lock import repository_mutation
from haute._git_read_models import FileState
from haute._git_state import read_working_branch
from haute._logging import get_logger
from haute._project_layout import (
    LOCAL_FOLDERS,
    PIPELINE_FILES_SUBFOLDERS,
    VERSIONED_FILE_LIMIT_BYTES,
    pipeline_files_folder,
)
from haute._sandbox import _get_project_root
from haute.hosted import databricks_app_environment
from haute.routes._helpers import save_lock
from haute.routes._save_pipeline import capture_save_in_ledger
from haute.schemas import (
    ProjectFileDeleteResponse,
    ProjectFileEntry,
    ProjectFilePlace,
    ProjectFileWriteResponse,
    ProjectFolderResponse,
)

logger = get_logger(component="server.project_files")

router = APIRouter(prefix="/api/project-files", tags=["project-files"])

_WRITABLE_LOCAL_FOLDER = "data"
_STAGING_SUFFIX = ".upload"
_WRITE_BUFFER_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400

_PROBLEMS: dict[PathProblem, str] = {
    "not_ordinary": "isn't a name a project file can have: avoid ':' and the names Windows "
    "reserves, such as CON",
    "link": "is reached through a link or junction, which the Files panels don't follow",
    "multiply_linked": "is a hard link to another file, which the Files panels don't change",
    "not_a_file": "isn't an ordinary file or folder",
    "outside": "is outside the project",
}


def _refusal(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message})


@dataclass(frozen=True, slots=True)
class _Place:
    name: ProjectFilePlace
    root: str
    """The place's folder relative to the project root, POSIX; ``""`` for local."""
    known: tuple[str, ...]
    """The folders its root always lists, even before they exist."""


@dataclass(frozen=True, slots=True)
class _Target:
    place: _Place
    relative: str
    """Relative to the place."""
    project_relative: str
    """Relative to the project root, POSIX: the path Git and messages name."""
    path: Path

    @property
    def writable(self) -> bool:
        if self.place.name == "pipeline":
            return True
        return PurePosixPath(self.relative).parts[:1] == (_WRITABLE_LOCAL_FOLDER,)


def _place(project_root: Path, name: ProjectFilePlace) -> _Place:
    if name == "pipeline":
        folder = pipeline_files_folder(project_root)
        if folder is None:
            raise _refusal(
                409,
                "no_pipeline_folder",
                "This project's pipeline sits at the project root, so it has no pipeline "
                "folder to keep versioned files in.",
            )
        return _Place("pipeline", folder, PIPELINE_FILES_SUBFOLDERS)
    return _Place("local", "", LOCAL_FOLDERS)


def _target(project_root: Path, place_name: ProjectFilePlace, path: str) -> _Target:
    """Resolve a place-relative *path*, refusing anything but an ordinary visible name."""
    place = _place(project_root, place_name)
    text = path.replace("\\", "/")
    parts = text.split("/") if text else []
    if (
        text.startswith("/")
        or PureWindowsPath(path).drive
        or any(
            part in ("", ".", "..") or part.startswith(".") or part.casefold() == "__pycache__"
            for part in parts
        )
    ):
        raise _refusal(400, "invalid_path", f"{path!r} isn't a path the Files panels can use.")
    relative = "/".join(parts)
    if place.name == "local" and parts and parts[0] not in LOCAL_FOLDERS:
        raise _refusal(
            404, "folder_missing", f"Local files holds only {' and '.join(LOCAL_FOLDERS)}."
        )
    project_relative = str(PurePosixPath(place.root, relative)) if place.root else relative
    resolved, problem = (
        ordinary_path_problem(project_root, project_relative)
        if project_relative
        else (project_root, None)
    )
    if problem is not None:
        raise _refusal(400, "invalid_path", f"{project_relative} {_PROBLEMS[problem]}.")
    return _Target(place, relative, project_relative, resolved)


def _revision(info: os.stat_result) -> str:
    return f"{info.st_size}-{info.st_mtime_ns}"


def _hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith(".") or entry.name.casefold() == "__pycache__":
        return True
    if entry.is_symlink():
        return True
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def _state(states: dict[str, FileState], project_relative: str) -> FileState | None:
    """A path's state, an ignored parent folder (listed with a trailing ``/``) included."""
    if project_relative in states:
        return states[project_relative]
    parts = PurePosixPath(project_relative).parts
    for depth in range(len(parts) - 1, 0, -1):
        if states.get("/".join(parts[:depth]) + "/") == "ignored":
            return "ignored"
    return None


def _list_folder(place_name: ProjectFilePlace, path: str) -> ProjectFolderResponse:
    project_root = _get_project_root().resolve()
    folder = _target(project_root, place_name, path)
    place = folder.place
    at_root = folder.relative == ""
    if place.name == "local" and at_root:
        return ProjectFolderResponse(
            place="local",
            path="",
            root="",
            writable=False,
            entries=[_folder_entry(project_root, place, name) for name in place.known],
        )
    # The place's root and its known folders are listed even before they exist.
    known = at_root or folder.relative in place.known
    states = (
        _git.pipeline_file_states(folder.project_relative, cwd=project_root)
        if place.name == "pipeline"
        else {}
    )
    entries: list[ProjectFileEntry] = []
    if folder.path.is_dir():
        with os.scandir(folder.path) as scan:
            for entry in scan:
                if _hidden(entry):
                    continue
                relative = f"{folder.relative}/{entry.name}" if folder.relative else entry.name
                info = entry.stat(follow_symlinks=False)
                project_relative = f"{folder.project_relative}/{entry.name}"
                state = _state(states, project_relative + ("/" if entry.is_dir() else ""))
                if entry.is_dir(follow_symlinks=False):
                    entries.append(
                        ProjectFileEntry(
                            name=entry.name,
                            path=relative,
                            kind="folder",
                            modified=info.st_mtime,
                            status="ignored" if state == "ignored" else None,
                        )
                    )
                elif stat.S_ISREG(info.st_mode):
                    entries.append(
                        ProjectFileEntry(
                            name=entry.name,
                            path=relative,
                            kind="file",
                            size=info.st_size,
                            modified=info.st_mtime,
                            revision=_revision(info),
                            status=state,
                        )
                    )
    elif not known:
        raise _refusal(404, "folder_missing", f"{folder.project_relative} doesn't exist.")
    if place.name == "pipeline" and at_root:
        present = {entry.name.casefold() for entry in entries}
        entries.extend(
            ProjectFileEntry(name=name, path=name, kind="folder")
            for name in place.known
            if name.casefold() not in present
        )
    entries.sort(key=lambda entry: (entry.kind != "folder", entry.name.casefold()))
    return ProjectFolderResponse(
        place=place.name,
        path=folder.relative,
        root=place.root,
        writable=folder.writable,
        entries=entries,
    )


def _folder_entry(project_root: Path, place: _Place, name: str) -> ProjectFileEntry:
    path = project_root / place.root / name if place.root else project_root / name
    modified = path.stat().st_mtime if path.is_dir() else None
    return ProjectFileEntry(name=name, path=name, kind="folder", modified=modified)


@router.get("", response_model=ProjectFolderResponse)
async def list_project_files(place: ProjectFilePlace, path: str = "") -> ProjectFolderResponse:
    """One folder of a place, for the Pipeline files and Local files panels."""
    return await run_in_threadpool(_list_folder, place, path)


def _file_target(project_root: Path, place: ProjectFilePlace, path: str) -> _Target:
    target = _target(project_root, place, path)
    if not target.relative or (
        target.place.name == "local" and len(PurePosixPath(target.relative).parts) < 2
    ):
        raise _refusal(400, "invalid_path", f"{path!r} names a folder, not a file.")
    return target


def _writable_file_target(project_root: Path, place: ProjectFilePlace, path: str) -> _Target:
    target = _file_target(project_root, place, path)
    if not target.writable:
        raise _refusal(
            403,
            "read_only",
            f"{target.project_relative} is in a folder the Files panels only download from.",
        )
    if target.path.is_dir():
        raise _refusal(400, "invalid_path", f"{target.project_relative} is a folder.")
    return target


@dataclass(frozen=True, slots=True)
class _WritePlan:
    target: _Target
    length: int
    branch: str | None


def _plan_write(place: ProjectFilePlace, path: str, length_header: str | None) -> _WritePlan:
    """Everything a write can check before its body is read."""
    project_root = _get_project_root().resolve()
    target = _writable_file_target(project_root, place, path)
    display = target.project_relative
    if target.place.name == "local" and databricks_app_environment() is not None:
        raise _refusal(
            403,
            "hosted_local_files",
            "This hosted app keeps only what version history keeps, so a file in Local "
            "files would be lost when it restarts. Add it to Pipeline files instead.",
        )
    if length_header is None or not length_header.isdigit():
        raise _refusal(411, "length_required", "The upload didn't say how large the file is.")
    length = int(length_header)
    if target.place.name == "pipeline":
        if length > VERSIONED_FILE_LIMIT_BYTES:
            raise _refusal(
                413,
                "too_large_for_history",
                f"{display} is {length / (1024 * 1024):.1f} MiB. Pipeline files are "
                "versioned, and version history takes files up to "
                f"{VERSIONED_FILE_LIMIT_BYTES // (1024 * 1024)} MiB. Add it to Local files' "
                "data/ instead.",
            )
        rule = _git.ignore_rule(display, cwd=project_root)
        if rule is not None:
            raise _refusal(
                409,
                "ignored",
                f"{display} would be ignored by .gitignore ({rule}), so it couldn't be "
                "versioned. Change that rule to apply only at the project root, for "
                "example '/data/' instead of 'data/'.",
            )
    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        raise _refusal(400, "invalid_path", f"{display} is inside a file, not a folder.") from None
    try:
        ensure_disk_headroom(target.path.parent, length)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise _refusal(507, "disk_full", f"There isn't room on disk for {display}.") from None
        raise
    branch = read_working_branch(project_root) if target.place.name == "pipeline" else None
    return _WritePlan(target, length, branch)


def _publish_write(
    plan: _WritePlan, staging: Path, replace_revision: str | None
) -> ProjectFileWriteResponse:
    project_root = _get_project_root().resolve()
    target = plan.target
    display = target.project_relative
    with repository_mutation(project_root):
        if target.place.name == "pipeline" and read_working_branch(project_root) != plan.branch:
            raise _refusal(
                409,
                "branch_changed",
                "The branch changed while the file was uploading, so nothing was written. "
                "Add it again on this branch.",
            )
        _target(project_root, target.place.name, target.relative)
        current = target.path.stat() if target.path.is_file() else None
        if replace_revision is None and (current is not None or target.path.exists()):
            raise _refusal(409, "file_exists", f"{display} already exists.")
        if replace_revision is not None and (
            current is None or _revision(current) != replace_revision
        ):
            raise _refusal(
                412,
                "file_changed",
                f"{display} changed since you chose to replace it. Look again, then retry.",
            )
        try:
            publish_staged_file(staging, target.path, replace=replace_revision is not None)
        except FileExistsError:
            raise _refusal(409, "file_exists", f"{display} already exists.") from None
        except PermissionError:
            raise _refusal(
                409,
                "file_in_use",
                f"{display} is open in Haute or another program. Try again once it's closed.",
            ) from None
        written = target.path.stat()
        warnings: list[str] = []
        git_sha: str | None = None
        identity_required = False
        status: FileState | None = None
        if target.place.name == "pipeline":
            verb = "Replaced" if replace_revision is not None else "Added"
            git_sha, identity_required = capture_save_in_ledger(
                project_root, [display], warnings, message=f"{verb} {display}"
            )
            status = _state(_git.pipeline_file_states(display, cwd=project_root), display)
    logger.info("project_file_written", path=display, size=written.st_size, sha=git_sha)
    return ProjectFileWriteResponse(
        path=target.relative,
        size=written.st_size,
        revision=_revision(written),
        replaced=replace_revision is not None,
        status=status,
        git_sha=git_sha,
        identity_required=identity_required,
        warnings=warnings,
    )


@router.put("/content", response_model=ProjectFileWriteResponse)
async def write_project_file(
    request: Request,
    place: ProjectFilePlace,
    path: str,
    replace_revision: str | None = None,
) -> ProjectFileWriteResponse:
    """Write one file whole: stream it to a hidden staging file, then publish it."""
    plan = await run_in_threadpool(_plan_write, place, path, request.headers.get("content-length"))
    staging = plan.target.path.with_name(
        f".{plan.target.path.name}.{uuid.uuid4().hex[:8]}{_STAGING_SUFFIX}"
    )
    display = plan.target.project_relative
    try:
        handle = await run_in_threadpool(staging.open, "xb")
        try:
            received = 0
            buffer = bytearray()
            async for chunk in request.stream():
                received += len(chunk)
                if received > plan.length:
                    raise _refusal(
                        400, "length_mismatch", f"The upload of {display} was longer than declared."
                    )
                buffer.extend(chunk)
                if len(buffer) >= _WRITE_BUFFER_BYTES:
                    await run_in_threadpool(handle.write, bytes(buffer))
                    buffer.clear()
            if buffer:
                await run_in_threadpool(handle.write, bytes(buffer))
        finally:
            await run_in_threadpool(handle.close)
        if received != plan.length:
            raise _refusal(
                400, "length_mismatch", f"The upload of {display} ended before it was complete."
            )
        async with save_lock:
            return await run_in_threadpool(_publish_write, plan, staging, replace_revision)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise _refusal(507, "disk_full", f"There isn't room on disk for {display}.") from None
        raise
    finally:
        await run_in_threadpool(staging.unlink, missing_ok=True)


def _download_target(place: ProjectFilePlace, path: str) -> Path:
    project_root = _get_project_root().resolve()
    target = _file_target(project_root, place, path)
    if not target.path.is_file():
        raise _refusal(404, "file_missing", f"{target.project_relative} doesn't exist.")
    return target.path


@router.get("/content")
async def download_project_file(place: ProjectFilePlace, path: str) -> FileResponse:
    """The file as an attachment, never rendered inline."""
    target = await run_in_threadpool(_download_target, place, path)
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


def _delete(place: ProjectFilePlace, path: str, revision: str) -> ProjectFileDeleteResponse:
    project_root = _get_project_root().resolve()
    target = _writable_file_target(project_root, place, path)
    display = target.project_relative
    with repository_mutation(project_root):
        _target(project_root, target.place.name, target.relative)
        if not target.path.is_file():
            raise _refusal(404, "file_missing", f"{display} doesn't exist.")
        if _revision(target.path.stat()) != revision:
            raise _refusal(
                412,
                "file_changed",
                f"{display} changed since you chose to delete it. Look again, then retry.",
            )
        try:
            target.path.unlink()
        except PermissionError:
            raise _refusal(
                409,
                "file_in_use",
                f"{display} is open in Haute or another program. Try again once it's closed.",
            ) from None
        warnings: list[str] = []
        git_sha: str | None = None
        identity_required = False
        if target.place.name == "pipeline":
            git_sha, identity_required = capture_save_in_ledger(
                project_root, [display], warnings, message=f"Deleted {display}"
            )
    logger.info("project_file_deleted", path=display, sha=git_sha)
    return ProjectFileDeleteResponse(
        path=target.relative,
        git_sha=git_sha,
        identity_required=identity_required,
        warnings=warnings,
    )


@router.delete("/content", response_model=ProjectFileDeleteResponse)
async def delete_project_file(
    place: ProjectFilePlace, path: str, revision: str
) -> ProjectFileDeleteResponse:
    """Delete one file, if it is still the revision the analyst saw."""
    async with save_lock:
        return await run_in_threadpool(_delete, place, path, revision)
