"""Tests for cross-platform memory helpers in _algorithms and _ram_estimate."""

from __future__ import annotations

from unittest.mock import patch

import polars as pl
import pytest

from haute.modelling._algorithms import _get_available_mb, _get_rss_mb, _mem_checkpoint
from tests._project import use_project

# ---------------------------------------------------------------------------
# _get_rss_mb
# ---------------------------------------------------------------------------


class TestGetRssMb:
    """Verify _get_rss_mb reads through the psutil-backed process probe."""

    def test_returns_positive_float(self):
        result = _get_rss_mb()
        assert isinstance(result, float)
        assert result > 0.0

    def test_returns_zero_when_probe_is_unavailable(self, monkeypatch):
        monkeypatch.setattr("haute.modelling._algorithms.current_process_rss_bytes", lambda: None)
        assert _get_rss_mb() == 0.0


# ---------------------------------------------------------------------------
# _get_available_mb
# ---------------------------------------------------------------------------


class TestGetAvailableMb:
    """Verify _get_available_mb delegates to available_ram_bytes."""

    def test_returns_positive_float(self):
        result = _get_available_mb()
        assert isinstance(result, float)
        assert result > 0.0

    def test_delegates_to_available_ram_bytes(self):
        """Must return available_ram_bytes() / (1024 * 1024)."""
        fake_bytes = 8 * 1024 * 1024 * 1024  # 8 GiB
        with patch("haute.modelling._algorithms.available_ram_bytes", return_value=fake_bytes):
            result = _get_available_mb()
        assert result == pytest.approx(8192.0)


# ---------------------------------------------------------------------------
# _mem_checkpoint
# ---------------------------------------------------------------------------


class TestMemCheckpoint:
    def test_a_fit_without_haute_mem_log_writes_no_file(self, tmp_path, monkeypatch):
        """The log is opt-in: a real fit leaves the working directory as it was."""
        from haute.modelling._algorithms import CatBoostAlgorithm

        work = tmp_path / "work"
        work.mkdir()
        use_project(work)
        monkeypatch.delenv("HAUTE_MEM_LOG", raising=False)
        frame = pl.DataFrame({"x": [0.1, 0.5, 0.9, 0.3] * 10, "y": [1.0, 2.0, 3.0, 4.0] * 10})
        CatBoostAlgorithm().fit(
            frame, ["x"], [], "y", None, {"iterations": 3, "depth": 2}, "regression"
        )
        assert list(work.iterdir()) == []

    def test_lines_append_to_the_named_file_across_runs(self, tmp_path, monkeypatch):
        """HAUTE_MEM_LOG is read at write time, and an earlier run's lines are kept."""
        log_path = tmp_path / "logs" / "mem.log"
        monkeypatch.setenv("HAUTE_MEM_LOG", str(log_path))
        with (
            patch("haute.modelling._algorithms._get_rss_mb", return_value=123.4),
            patch("haute.modelling._algorithms._get_available_mb", return_value=567.8),
        ):
            _mem_checkpoint("first run")
            _mem_checkpoint("second run")

        lines = log_path.read_text().splitlines()
        assert ["first run" in lines[0], "second run" in lines[1]] == [True, True]
        assert "123.4" in lines[0] and "567.8" in lines[0]


def test_get_rss_mb_converts_bytes_to_mebibytes(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.modelling._algorithms as algorithms_mod

    monkeypatch.setattr(algorithms_mod, "current_process_rss_bytes", lambda: 3 * 1024 * 1024)
    assert algorithms_mod._get_rss_mb() == 3.0
