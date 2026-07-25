"""Runtime file path resolution shared by executor and server routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

PathPreference = Literal["project", "pipeline"]


def _normalise_path_text(path: str | Path) -> str:  # pragma: no mutate
    """Normalise path separators before constructing a ``Path``.

    Config files may contain Windows backslashes.  Forward slashes are
    accepted on Windows, Linux, and macOS, so normalising early keeps cache
    keys and existence checks consistent across operating systems.
    """
    text = str(path)
    if "\x00" in text:
        raise ValueError("Path contains an embedded null byte")
    return text.replace("\\", "/")


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


def _infer_project_root(
    *,  # pragma: no mutate
    project_root: str | Path | None,  # pragma: no mutate
    source_file: str | Path | None,  # pragma: no mutate
) -> Path:
    if project_root is not None:
        return Path(project_root).resolve()

    cwd = Path.cwd().resolve()
    if not source_file:
        return cwd

    source = Path(_normalise_path_text(source_file))
    if not source.is_absolute():
        return cwd

    resolved_source = source.resolve()
    if resolved_source.is_relative_to(cwd):
        return cwd
    return resolved_source.parent


def _candidate_if_allowed(
    candidate: Path,
    project_root: Path,
    *,  # pragma: no mutate
    enforce_project_root: bool,  # pragma: no mutate
) -> Path | None:
    spelling_preserved = Path(os.path.abspath(candidate))
    resolved = spelling_preserved.resolve()
    if enforce_project_root and not resolved.is_relative_to(project_root):
        return None
    return spelling_preserved


def resolve_runtime_file_path(
    raw_path: str | Path,  # pragma: no mutate
    *,  # pragma: no mutate
    source_file: str | Path | None = None,  # pragma: no mutate
    pipeline_dir: str | Path | None = None,  # pragma: no mutate
    project_root: str | Path | None = None,  # pragma: no mutate
    prefer: PathPreference = "project",
    enforce_project_root: bool = False,
) -> Path:
    """Resolve a user-facing path for runtime execution.

    Relative paths come from the GUI file browser, which reports paths from
    the Haute project root.  Pipelines may live in a subdirectory, so we also
    support pipeline-relative paths as a fallback.  Existing files win over
    missing candidates; if both candidates exist, ``prefer`` decides.
    """
    root = _infer_project_root(project_root=project_root, source_file=source_file)
    raw = Path(_normalise_path_text(raw_path))

    if raw.is_absolute():
        spelling_preserved = Path(os.path.abspath(raw))
        resolved = spelling_preserved.resolve()
        if enforce_project_root and not resolved.is_relative_to(root):
            raise ValueError(f"Path {raw_path!r} resolves outside the project root")
        return spelling_preserved

    pdir: Path | None
    if pipeline_dir is not None:
        pdir = Path(pipeline_dir).resolve()
    else:
        pdir = _resolve_source_file_parent(source_file, root)

    project_candidate = _candidate_if_allowed(
        root / raw,
        root,
        enforce_project_root=enforce_project_root,
    )
    pipeline_candidate = (
        _candidate_if_allowed(
            pdir / raw,
            root,
            enforce_project_root=enforce_project_root,
        )
        if pdir is not None
        else None
    )

    if prefer == "pipeline":  # pragma: no mutate
        ordered = [pipeline_candidate, project_candidate]
    else:
        ordered = [project_candidate, pipeline_candidate]

    deduped: list[Path] = []
    for candidate in ordered:
        if candidate is not None and candidate not in deduped:
            deduped.append(candidate)

    for candidate in deduped:
        if candidate.exists():
            return candidate

    if deduped:
        return deduped[0]

    raise ValueError(f"Path {raw_path!r} resolves outside the project root")
