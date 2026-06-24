"""Coverage for the optimiser apply-artifact security guard and cleanup paths.

Focuses on ``_validate_apply_result_artifact_handle`` rejection arms (forged or
path-traversing handles must be refused *before* any ``read_parquet``),
``_persist_apply_result_artifact`` cleanup-on-write-failure, the corrupt-parquet
load arm, the best-effort orphan-cleanup logger, and the ratebook side-input
helper's empty-source branch.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes._optimiser_service import (
    _APPLY_ARTIFACT_DIR_PREFIX,
    _APPLY_RESULT_FILENAME,
    _APPLY_RESULT_HANDLE_KIND,
    _ARTIFACT_HANDLE_VERSION,
    _apply_artifact_root,
    _cleanup_apply_result_artifact,
    _cleanup_orphan_apply_result_artifact,
    _load_apply_result_artifact,
    _optimiser_side_input_ids,
    _persist_apply_result_artifact,
    _validate_apply_result_artifact_handle,
)


def _handle(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _validate_apply_result_artifact_handle — rejection arms (forged handles)
# ---------------------------------------------------------------------------


def test_validate_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="Invalid optimiser apply artifact handle"):
        _validate_apply_result_artifact_handle(_handle(kind="something_else"))


def test_validate_rejects_unsupported_version() -> None:
    with pytest.raises(ValueError, match="Unsupported optimiser apply artifact handle"):
        _validate_apply_result_artifact_handle(_handle(version=_ARTIFACT_HANDLE_VERSION + 1))


def test_validate_rejects_unsupported_format() -> None:
    with pytest.raises(ValueError, match="Unsupported optimiser apply artifact format"):
        _validate_apply_result_artifact_handle(_handle(format="csv"))


def test_validate_rejects_missing_directory() -> None:
    with pytest.raises(ValueError, match="has no directory"):
        _validate_apply_result_artifact_handle(_handle(directory="", path="/abs/result.parquet"))


def test_validate_rejects_missing_path() -> None:
    with pytest.raises(ValueError, match="has no path"):
        _validate_apply_result_artifact_handle(_handle(directory="/abs/apply_x", path=None))


def test_validate_rejects_null_byte_in_path() -> None:
    with pytest.raises(ValueError, match="invalid path"):
        _validate_apply_result_artifact_handle(
            _handle(directory="/abs/apply_x", path="/abs/apply_x/res\x00ult.parquet")
        )


def test_validate_rejects_directory_not_under_root_prefix(tmp_path: Path) -> None:
    """A directory directly under the root but lacking the apply_ prefix is rejected."""
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    bogus_dir = root / "not_an_apply_dir"
    bogus_dir.mkdir(exist_ok=True)
    artifact_path = bogus_dir / _APPLY_RESULT_FILENAME
    try:
        with pytest.raises(ValueError, match="directory is invalid"):
            _validate_apply_result_artifact_handle(
                _handle(directory=str(bogus_dir), path=str(artifact_path))
            )
    finally:
        bogus_dir.rmdir()


def test_validate_rejects_wrong_filename(tmp_path: Path) -> None:
    """A path inside a valid apply_ dir but with the wrong filename is rejected."""
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    good_dir = root / f"{_APPLY_ARTIFACT_DIR_PREFIX}fixture"
    good_dir.mkdir(exist_ok=True)
    wrong_path = good_dir / "not_result.parquet"
    try:
        with pytest.raises(ValueError, match="path is invalid"):
            _validate_apply_result_artifact_handle(
                _handle(directory=str(good_dir), path=str(wrong_path))
            )
    finally:
        good_dir.rmdir()


def test_validate_accepts_well_formed_handle() -> None:
    """The happy path returns the resolved (path, directory) pair."""
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
    assert handle is not None
    try:
        artifact_path, directory = _validate_apply_result_artifact_handle(handle)
        assert artifact_path.name == _APPLY_RESULT_FILENAME
        assert artifact_path.parent == directory
    finally:
        _cleanup_apply_result_artifact(handle)


# ---------------------------------------------------------------------------
# _persist_apply_result_artifact — short-circuits + cleanup-on-failure
# ---------------------------------------------------------------------------


def test_persist_returns_none_without_dataframe_attr() -> None:
    assert _persist_apply_result_artifact(SimpleNamespace()) is None


def test_persist_returns_none_for_non_dataframe() -> None:
    assert _persist_apply_result_artifact(SimpleNamespace(dataframe=[1, 2, 3])) is None


def test_persist_cleans_up_dir_when_write_fails() -> None:
    """A write_parquet failure must rmtree the temp dir and re-raise."""
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})

    created_dirs: list[Path] = []
    real_write = pl.DataFrame.write_parquet

    def _capture_then_fail(self: pl.DataFrame, path: Any, *args: Any, **kwargs: Any) -> None:
        created_dirs.append(Path(path).parent)
        raise OSError("disk full")

    with patch.object(pl.DataFrame, "write_parquet", _capture_then_fail):
        with pytest.raises(OSError, match="disk full"):
            _persist_apply_result_artifact(SimpleNamespace(dataframe=df))

    assert created_dirs, "write_parquet should have been called"
    assert not created_dirs[0].exists(), "temp artifact dir must be removed on failure"
    # sanity: the real writer still works after the patch is lifted
    assert callable(real_write)


# ---------------------------------------------------------------------------
# _load_apply_result_artifact — corrupt-parquet arm
# ---------------------------------------------------------------------------


def test_load_rejects_corrupt_parquet() -> None:
    """A valid-shaped handle pointing at a non-parquet file raises a 500."""
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
    assert handle is not None
    try:
        # Corrupt the artifact in place — it remains under the owned root so
        # validation passes and the read_parquet failure arm is exercised.
        Path(str(handle["path"])).write_bytes(b"not a parquet file")
        with pytest.raises(HTTPException) as exc_info:
            _load_apply_result_artifact(handle)
        assert exc_info.value.status_code == 500
        assert "corrupt" in str(exc_info.value.detail)
    finally:
        _cleanup_apply_result_artifact(handle)


def test_load_reports_missing_artifact() -> None:
    """A validated handle whose file was deleted reports a missing-artifact 500."""
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
    assert handle is not None
    try:
        Path(str(handle["path"])).unlink()
        with pytest.raises(HTTPException) as exc_info:
            _load_apply_result_artifact(handle)
        assert exc_info.value.status_code == 500
        assert "missing" in str(exc_info.value.detail)
    finally:
        _cleanup_apply_result_artifact(handle)


# ---------------------------------------------------------------------------
# _cleanup_orphan_apply_result_artifact — best-effort logging arm
# ---------------------------------------------------------------------------


def test_orphan_cleanup_swallows_and_logs_failure() -> None:
    """An invalid handle makes the inner cleanup raise; the helper logs, no raise."""
    bad_handle = _handle(directory="relative", path="relative/result.parquet")
    with patch("haute.routes._optimiser_service.logger.warning") as warn:
        _cleanup_orphan_apply_result_artifact(
            bad_handle,
            job_id="job-123",
            event="orphan_cleanup_failed",
        )
    warn.assert_called_once()
    args, kwargs = warn.call_args
    assert args[0] == "orphan_cleanup_failed"
    assert kwargs["job_id"] == "job-123"
    assert kwargs["path"] == "relative"


def test_orphan_cleanup_silent_on_success() -> None:
    """A valid handle is cleaned without touching the warning logger."""
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
    assert handle is not None
    artifact_dir = Path(str(handle["directory"]))
    with patch("haute.routes._optimiser_service.logger.warning") as warn:
        _cleanup_orphan_apply_result_artifact(
            handle,
            job_id="job-xyz",
            event="orphan_cleanup_failed",
        )
    warn.assert_not_called()
    assert not artifact_dir.exists()


# ---------------------------------------------------------------------------
# _optimiser_side_input_ids — empty-source branch
# ---------------------------------------------------------------------------


def _optimiser_graph(config: dict[str, Any]) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="opt_1",
                data=NodeData(
                    label="Optimiser",
                    nodeType=NodeType.OPTIMISER,
                    config=config,
                ),
            ),
        ],
        edges=[],
    )


def test_side_input_ids_empty_for_online_mode() -> None:
    graph = _optimiser_graph({"mode": "online"})
    assert _optimiser_side_input_ids(graph, "opt_1") == frozenset()


def test_side_input_ids_returns_banding_source_for_ratebook() -> None:
    graph = _optimiser_graph({"mode": "ratebook", "banding_source": "band_1"})
    assert _optimiser_side_input_ids(graph, "opt_1") == frozenset({"band_1"})


def test_side_input_ids_empty_ratebook_without_banding_source() -> None:
    """Ratebook mode with an empty banding_source yields no side inputs."""
    graph = _optimiser_graph({"mode": "ratebook", "banding_source": ""})
    assert _optimiser_side_input_ids(graph, "opt_1") == frozenset()
