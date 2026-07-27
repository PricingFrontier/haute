"""Safety coverage for persisted optimiser apply artifacts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._optimiser_service import (
    _APPLY_RESULT_HANDLE_KIND,
    _ARTIFACT_HANDLE_VERSION,
    _cleanup_apply_result_artifact,
    _load_apply_result_artifact,
    _persist_apply_result_artifact,
)


def _handle(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
    }
    base.update(overrides)
    return base


def test_persisted_apply_artifact_round_trips_through_validated_handle() -> None:
    df = pl.DataFrame({"quote_id": ["q1"], "optimal_scenario_value": [1.05]})
    handle = _persist_apply_result_artifact(SimpleNamespace(dataframe=df))
    assert handle is not None

    try:
        loaded = _load_apply_result_artifact(handle)
        assert loaded.to_dicts() == df.to_dicts()
        assert Path(handle["path"]).name == "result.parquet"
        assert Path(handle["path"]).parent == Path(handle["directory"])
    finally:
        _cleanup_apply_result_artifact(handle)


def test_apply_artifact_load_rejects_paths_outside_owned_root(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_path = outside_dir / "result.parquet"
    pl.DataFrame({"quote_id": ["q1"]}).write_parquet(outside_path)

    with pytest.raises(HTTPException) as exc_info:
        _load_apply_result_artifact(
            _handle(path=str(outside_path), directory=str(outside_dir), row_count=1)
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == (
        "Optimiser apply artifact reference is invalid. Re-run the solve to regenerate it."
    )
    assert outside_path.exists()


def test_apply_artifact_cleanup_rejects_path_directory_mismatch(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    sentinel = outside_dir / "result.parquet"
    sentinel.write_text("do not delete")
    handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"quote_id": ["q1"]}))
    )
    assert handle is not None

    invalid = dict(handle)
    invalid["path"] = str(sentinel)
    try:
        with pytest.raises(ValueError, match="outside its directory"):
            _cleanup_apply_result_artifact(invalid)
        assert sentinel.exists()
        assert Path(handle["path"]).exists()
    finally:
        _cleanup_apply_result_artifact(handle)


def test_apply_artifact_load_rejects_relative_paths() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _load_apply_result_artifact(
            _handle(path="relative/result.parquet", directory="relative", row_count=1)
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == (
        "Optimiser apply artifact reference is invalid. Re-run the solve to regenerate it."
    )


def test_apply_artifact_read_failure_logs_underlying_cause_before_wrapping() -> None:
    handle = _persist_apply_result_artifact(
        SimpleNamespace(dataframe=pl.DataFrame({"quote_id": ["q1"]}))
    )
    assert handle is not None

    try:
        with (
            patch("polars.read_parquet", side_effect=OSError("corrupt parquet")),
            patch("haute.routes._optimiser_service.logger.error") as log_error,
            pytest.raises(HTTPException) as exc_info,
        ):
            _load_apply_result_artifact(handle)
        assert exc_info.value.status_code == 500
        assert "corrupt" in str(exc_info.value.detail).lower()
        log_error.assert_called_once()
        assert log_error.call_args.kwargs["exc_info"] is True
        assert log_error.call_args.kwargs["path"] == handle["path"]
    finally:
        _cleanup_apply_result_artifact(handle)
