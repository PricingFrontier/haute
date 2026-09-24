"""Ownership-marked optimiser artifacts and setup-owned solver-input files.

Two artifact families live under dedicated, marker-owned temp roots: the
online apply result (``optimiser_apply_result``) and the ratebook factor table
(``optimiser_ratebook_factors``). Their handle is the canonical persisted wire
schema. This module persists, validates, loads and removes them, registers the
job store's cleaners for evicted jobs, and reaps stale directories at startup.
Solve setup's solver-input parquet is created and removed here too, so the
solve service owns no filesystem deletion.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException

from haute._artifact_housekeeping import (
    create_owned_artifact_directory,
    reap_stale_artifact_directories,
)
from haute._logging import get_logger
from haute._polars_utils import bounded_sink, read_parquet_metadata
from haute._types import SolveResultLike
from haute.routes._job_store import register_artifact_cleaner

logger = get_logger(component="server.optimiser.solve")

_APPLY_RESULT_HANDLE_KEY = "apply_result"
_APPLY_RESULT_HANDLE_KIND = "optimiser_apply_result"
_RATEBOOK_FACTORS_HANDLE_KEY = "ratebook_factors"
_RATEBOOK_FACTORS_HANDLE_KIND = "optimiser_ratebook_factors"
_ARTIFACT_HANDLE_VERSION = 1
_APPLY_ARTIFACT_ROOT_NAME = "haute/artifacts/v1/optimiser_apply"
_APPLY_ARTIFACT_DIR_PREFIX = "apply_"
_APPLY_RESULT_FILENAME = "result.parquet"
_RATEBOOK_FACTORS_ARTIFACT_ROOT_NAME = "haute/artifacts/v1/optimiser_ratebook_factors"
_RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX = "factors_"
_RATEBOOK_FACTORS_FILENAME = "factors.parquet"
_APPLY_ARTIFACT_OWNER = "optimiser_apply"
_RATEBOOK_FACTORS_ARTIFACT_OWNER = "optimiser_ratebook_factors"
_ARTIFACT_STALE_SECONDS_ENV = "HAUTE_ARTIFACT_STALE_SECONDS"
_DEFAULT_ARTIFACT_STALE_SECONDS = 86_400


def _apply_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _APPLY_ARTIFACT_ROOT_NAME).resolve()


def _ratebook_factors_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _RATEBOOK_FACTORS_ARTIFACT_ROOT_NAME).resolve()


def _prepare_apply_artifact_root() -> Path:
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prepare_ratebook_factors_artifact_root() -> Path:
    root = _ratebook_factors_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_stale_seconds() -> int:
    raw = os.environ.get(_ARTIFACT_STALE_SECONDS_ENV)
    if raw is None:
        return _DEFAULT_ARTIFACT_STALE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{_ARTIFACT_STALE_SECONDS_ENV} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{_ARTIFACT_STALE_SECONDS_ENV} must be a non-negative integer")
    return value


def reap_stale_optimiser_artifacts(
    stale_after_seconds: int,
) -> dict[str, dict[str, int]]:
    """Reap stale marked artifacts from the optimiser's dedicated roots only."""
    reports: dict[str, dict[str, int]] = {}
    for name, root, owner in (
        ("apply", _apply_artifact_root(), _APPLY_ARTIFACT_OWNER),
        ("ratebook_factors", _ratebook_factors_artifact_root(), _RATEBOOK_FACTORS_ARTIFACT_OWNER),
    ):
        if root.is_dir():
            reports[name] = reap_stale_artifact_directories(root, owner, stale_after_seconds)
    logger.info("optimiser_artifact_reap_completed", reports=reports)
    return reports


def _validate_server_owned_parquet_handle(
    handle: dict[str, Any],
    *,
    kind: str,
    root: Path,
    directory_prefix: str,
    filename: str,
    description: str,
) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned parquet artifact."""
    if handle.get("kind") != kind:
        raise ValueError(f"Invalid {description} artifact handle.")
    if handle.get("version") != _ARTIFACT_HANDLE_VERSION:
        raise ValueError(f"Unsupported {description} artifact handle.")
    if handle.get("format") != "parquet":
        raise ValueError(f"Unsupported {description} artifact format.")

    raw_directory = handle.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        raise ValueError(f"{description} artifact handle has no directory.")
    raw_path = handle.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{description} artifact handle has no path.")
    if "\x00" in raw_directory or "\x00" in raw_path:
        raise ValueError(f"{description} artifact handle contains an invalid path.")

    directory_input = Path(raw_directory)
    path_input = Path(raw_path)
    if not directory_input.is_absolute() or not path_input.is_absolute():
        raise ValueError(f"{description} artifact handle must use absolute paths.")

    directory = directory_input.resolve(strict=directory_input.exists())
    artifact_path = path_input.resolve(strict=path_input.exists())

    if not directory.is_relative_to(root):
        raise ValueError(f"{description} artifact directory is outside the artifact root.")
    if directory.parent != root or not directory.name.startswith(directory_prefix):
        raise ValueError(f"{description} artifact directory is invalid.")
    if artifact_path.parent != directory:
        raise ValueError(f"{description} artifact path is outside its directory.")
    if artifact_path.name != filename:
        raise ValueError(f"{description} artifact path is invalid.")
    return artifact_path, directory


def _validate_apply_result_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned apply artifact."""
    return _validate_server_owned_parquet_handle(
        handle,
        kind=_APPLY_RESULT_HANDLE_KIND,
        root=_apply_artifact_root(),
        directory_prefix=_APPLY_ARTIFACT_DIR_PREFIX,
        filename=_APPLY_RESULT_FILENAME,
        description="Optimiser apply",
    )


def _validate_ratebook_factors_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned ratebook factor artifact."""
    return _validate_server_owned_parquet_handle(
        handle,
        kind=_RATEBOOK_FACTORS_HANDLE_KIND,
        root=_ratebook_factors_artifact_root(),
        directory_prefix=_RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        filename=_RATEBOOK_FACTORS_FILENAME,
        description="Optimiser ratebook factors",
    )


def _persist_apply_result_artifact(solve_result: SolveResultLike) -> dict[str, Any] | None:
    """Persist the large apply/detail dataframe behind an explicit handle."""
    if not hasattr(solve_result, "dataframe"):
        return None

    import polars as pl

    df = solve_result.dataframe
    if not isinstance(df, pl.DataFrame):
        return None

    artifact_dir = create_owned_artifact_directory(
        _prepare_apply_artifact_root(), _APPLY_ARTIFACT_DIR_PREFIX, _APPLY_ARTIFACT_OWNER
    )
    artifact_path = artifact_dir / _APPLY_RESULT_FILENAME
    try:
        df.write_parquet(artifact_path)
        row_count = len(df)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise
    try:
        cast(Any, solve_result).dataframe = None
    except Exception:
        logger.debug(
            "optimiser_apply_dataframe_reference_not_clearable",
            solve_result_type=type(solve_result).__name__,
        )

    return {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
    }


def _persist_ratebook_factors_artifact(factors_df: Any) -> dict[str, Any] | None:
    """Persist ratebook factors behind an explicit handle instead of the job dict."""
    if factors_df is None:
        return None

    import polars as pl

    if not isinstance(factors_df, pl.DataFrame):
        return None

    artifact_dir = create_owned_artifact_directory(
        _prepare_ratebook_factors_artifact_root(),
        _RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        _RATEBOOK_FACTORS_ARTIFACT_OWNER,
    )
    artifact_path = artifact_dir / _RATEBOOK_FACTORS_FILENAME
    try:
        factors_df.write_parquet(artifact_path)
        metadata = read_parquet_metadata(artifact_path)
        row_count = int(metadata["row_count"])
        size_bytes = int(metadata["size_bytes"])
        columns = list(factors_df.columns)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise

    return {
        "kind": _RATEBOOK_FACTORS_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
        "size_bytes": size_bytes,
        "columns": columns,
    }


def _new_ratebook_factors_directory() -> Path:
    """Create the marked artifact directory a setup worker persists ratebook factors into."""
    return create_owned_artifact_directory(
        _prepare_ratebook_factors_artifact_root(),
        _RATEBOOK_FACTORS_ARTIFACT_DIR_PREFIX,
        _RATEBOOK_FACTORS_ARTIFACT_OWNER,
    )


def _remove_ratebook_factors_directory(directory: Path) -> None:
    """Remove a parent-owned factors directory no job adopted; a failure is logged."""
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return
    except OSError as cleanup_exc:
        logger.warning(
            "setup_orphan_ratebook_factors_cleanup_failed",
            path=str(directory),
            error=str(cleanup_exc),
        )


def _persist_ratebook_factors_lazy_artifact(
    factors_lf: Any,
    *,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist projected ratebook factors without collecting them into memory.

    A setup worker persists into the *artifact_dir* its parent created and
    owns: on failure it removes only its partial file and leaves the
    directory to the parent.
    """
    owns_directory = artifact_dir is None
    if artifact_dir is None:
        artifact_dir = _new_ratebook_factors_directory()
    artifact_path = artifact_dir / _RATEBOOK_FACTORS_FILENAME
    try:
        bounded_sink(
            factors_lf,
            artifact_path,
        )
        metadata = read_parquet_metadata(artifact_path)
        row_count = int(metadata["row_count"])
        size_bytes = int(metadata["size_bytes"])
        columns = list(cast(Mapping[str, Any], metadata["columns"]).keys())
    except BaseException:
        if owns_directory:
            shutil.rmtree(artifact_dir, ignore_errors=True)
        else:
            artifact_path.unlink(missing_ok=True)
        raise

    return {
        "kind": _RATEBOOK_FACTORS_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
        "size_bytes": size_bytes,
        "columns": columns,
    }


def _cleanup_apply_result_artifact(handle: dict[str, Any]) -> None:
    """Remove a newly-created apply artifact that no job owns."""
    _artifact_path, artifact_dir = _validate_apply_result_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _cleanup_ratebook_factors_artifact(handle: dict[str, Any]) -> None:
    """Remove a persisted ratebook factors artifact owned by an expired job."""
    _artifact_path, artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _log_artifact_load_failure(
    event: str,
    handle: Mapping[str, Any],
    exc: BaseException,
) -> None:
    logger.error(
        event,
        path=str(handle.get("path") or "<unknown>"),
        error=str(exc),
        exc_info=True,
    )


def _load_apply_result_artifact(handle: dict[str, Any]) -> Any:
    """Load a persisted optimiser apply dataframe from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_apply_result_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure("optimiser_apply_artifact_validation_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail=(
                "Optimiser apply artifact reference is invalid. Re-run the solve to regenerate it."
            ),
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_apply_artifact_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail=(
                "Optimiser apply artifact is no longer available. "
                "Re-run the solve to regenerate it."
            ),
        )

    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_apply_artifact_read_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is corrupt. Re-run the solve to regenerate it.",
        ) from exc


def _load_ratebook_factors_artifact(handle: dict[str, Any]) -> Any:
    """Load persisted ratebook factors from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_validation_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact reference is invalid. Re-run the solve.",
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_ratebook_artifact_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail="Optimiser ratebook factor artifact is no longer available. Re-run the solve.",
        )
    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_read_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact is corrupt. Re-run the solve.",
        ) from exc


def _scan_ratebook_factors_artifact(handle: dict[str, Any]) -> Any:
    """Return a lazy scan for a validated ratebook factor artifact."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_ratebook_factors_artifact_handle(handle)
    except ValueError as exc:
        _log_artifact_load_failure(
            "optimiser_ratebook_artifact_scan_validation_failed",
            handle,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact reference is invalid. Re-run the solve.",
        ) from exc
    if not artifact_path.is_file():
        logger.warning("optimiser_ratebook_artifact_scan_missing", path=str(artifact_path))
        raise HTTPException(
            status_code=410,
            detail="Optimiser ratebook factor artifact is no longer available. Re-run the solve.",
        )
    try:
        return pl.scan_parquet(artifact_path)
    except Exception as exc:
        _log_artifact_load_failure("optimiser_ratebook_artifact_scan_failed", handle, exc)
        raise HTTPException(
            status_code=500,
            detail="Optimiser ratebook factor artifact is corrupt. Re-run the solve.",
        ) from exc


register_artifact_cleaner(_APPLY_RESULT_HANDLE_KIND, _cleanup_apply_result_artifact)
register_artifact_cleaner(_RATEBOOK_FACTORS_HANDLE_KIND, _cleanup_ratebook_factors_artifact)


def _cleanup_orphan_apply_result_artifact(
    handle: dict[str, Any],
    *,
    job_id: str,
    event: str,
) -> None:
    """Best-effort cleanup for apply artifacts that were never attached to a job."""
    try:
        if handle.get("kind") == _RATEBOOK_FACTORS_HANDLE_KIND:
            _cleanup_ratebook_factors_artifact(handle)
        else:
            _cleanup_apply_result_artifact(handle)
    except Exception as cleanup_exc:
        raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
        logger.warning(
            event,
            job_id=job_id,
            path=str(raw_path),
            error=str(cleanup_exc),
            exc_info=True,
        )


def _new_solver_input_path() -> str:
    """Create the empty, setup-owned parquet path the solver input is written to."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
    os.close(tmp_fd)
    return tmp_path


def _remove_solver_input(path: str) -> None:
    """Remove a setup-owned solver-input parquet; a failed removal is logged, not raised."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as cleanup_exc:
        logger.warning(
            "optimiser_grid_temp_cleanup_failed",
            path=path,
            error=str(cleanup_exc),
            exc_info=True,
        )


@contextlib.contextmanager
def _range_parts_directory() -> Iterator[Path]:
    """A private directory the auto-range reducer spills bucket parts into, removed on exit."""
    with tempfile.TemporaryDirectory(prefix="haute_frontier_range_parts_") as raw_dir:
        yield Path(raw_dir)
