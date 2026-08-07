"""Runtime file path resolution shared by executor and server routes."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path, PureWindowsPath
from typing import Any, Literal, TypeVar, cast

from haute._sandbox import _get_project_root
from haute._types import PipelineGraph
from haute._validation_error import HauteValidationError

PathPreference = Literal["project", "pipeline"]
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])
_RUNTIME_PROJECT_ROOT: ContextVar[Path | None] = ContextVar(  # pragma: no mutate
    "haute_runtime_project_root",
    default=None,
)


class RuntimePathError(HauteValidationError):
    """Base class for user-facing runtime path validation failures."""


class MalformedRuntimePathError(RuntimePathError):
    """Raised when a path cannot be parsed safely."""


class RuntimePathOutsideProjectError(RuntimePathError):
    """Raised when a runtime path resolves outside its execution root."""


def _normalise_path_text(path: str | Path) -> str:  # pragma: no mutate
    """Normalise path separators before constructing a ``Path``.

    Config files may contain Windows backslashes.  Forward slashes are
    accepted on Windows, Linux, and macOS, so normalising early keeps cache
    keys and existence checks consistent across operating systems.
    """
    text = str(path)
    if "\x00" in text:
        raise MalformedRuntimePathError("Path contains an embedded null byte")
    return text.replace("\\", "/")


def _reject_reserved_device_components(raw_path: str | Path, path: Path) -> None:
    """Reject DOS device names in a runtime path, on every platform.

    Windows resolves a component such as ``NUL`` or ``CON.csv`` to the device
    (``\\\\.\\NUL``) rather than to a file inside its directory, so the path
    escapes its root and the failure surfaces as a misleading "outside the
    project root". On Linux and macOS the same path is an ordinary file, so
    without this check one configured path resolves differently depending on
    who runs it.

    Rejecting everywhere mirrors the save-time guards in
    ``routes/_save_pipeline.py`` and ``routes/submodel.py``, which share this
    predicate for the same portability reason: a pipeline authored on one
    platform must stay loadable on another.
    """

    from haute._config_io import is_windows_reserved_filename

    for component in path.parts:
        if is_windows_reserved_filename(component):
            raise MalformedRuntimePathError(
                f"Path {raw_path!r} contains the reserved device name {component!r}. "
                "CON, PRN, AUX, NUL, COM1-COM9 and LPT1-LPT9 (any casing, any "
                "extension) cannot name a file on Windows, so they are rejected on "
                "every platform to keep a project portable."
            )


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

    current_project = _get_project_root().resolve()
    if not source_file:
        return current_project

    source = Path(_normalise_path_text(source_file))
    if not source.is_absolute():
        return current_project

    lexical_source = source.absolute()
    resolved_source = source.resolve()
    if lexical_source.is_relative_to(current_project) and not resolved_source.is_relative_to(
        current_project
    ):
        raise RuntimePathOutsideProjectError("Pipeline source resolves outside the project root")
    if resolved_source.is_relative_to(current_project):
        return current_project
    return resolved_source.parent


def current_runtime_project_root() -> Path:
    """Return the execution-scoped root, falling back to the selected project."""
    return _RUNTIME_PROJECT_ROOT.get() or _get_project_root().resolve()


@contextmanager
def runtime_project_root_scope(
    source_file: str | Path | None,  # pragma: no mutate
) -> Iterator[Path]:
    """Scope all builder path reads to the selected pipeline's project root."""
    root = _infer_project_root(project_root=None, source_file=source_file)
    token = _RUNTIME_PROJECT_ROOT.set(root)
    try:
        yield root
    finally:
        _RUNTIME_PROJECT_ROOT.reset(token)


def runtime_project_root_scoped(function: _CallableT) -> _CallableT:
    """Decorate an execution entry point whose first argument is a graph."""

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        graph = args[0] if args else kwargs.get("graph")
        if not isinstance(graph, PipelineGraph):
            raise TypeError(f"{function.__name__} requires a PipelineGraph as its graph argument")
        with runtime_project_root_scope(graph.source_file):
            return function(*args, **kwargs)

    return cast(_CallableT, wrapper)


def _candidate_if_allowed(
    candidate: Path,
    project_root: Path,
    *,  # pragma: no mutate
    enforce_project_root: bool,  # pragma: no mutate
) -> Path | None:  # pragma: no mutate
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
    enforce_project_root: bool = True,
) -> Path:
    """Resolve a user-facing path for runtime execution.

    Relative paths come from the GUI file browser, which reports paths from
    the Haute project root.  Pipelines may live in a subdirectory, so we also
    support pipeline-relative paths as a fallback.  Existing files win over
    missing candidates; if both candidates exist, ``prefer`` decides.
    """
    root = _infer_project_root(project_root=project_root, source_file=source_file)
    normalised_path = _normalise_path_text(raw_path)
    raw = Path(normalised_path)
    _reject_reserved_device_components(raw_path, raw)
    windows_path = PureWindowsPath(normalised_path)
    if windows_path.drive and not raw.is_absolute():
        raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")

    if raw.is_absolute():
        spelling_preserved = Path(os.path.abspath(raw))
        resolved = spelling_preserved.resolve()
        if enforce_project_root and not resolved.is_relative_to(root):
            raise RuntimePathOutsideProjectError(
                f"Path {raw_path!r} resolves outside the project root"
            )
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

    raise RuntimePathOutsideProjectError(f"Path {raw_path!r} resolves outside the project root")
