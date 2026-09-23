"""Tests for cross-platform memory helpers in _algorithms and _ram_estimate."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from haute.modelling._algorithms import _get_available_mb, _get_rss_mb, _mem_checkpoint

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
    def test_writes_to_log_file(self, tmp_path, monkeypatch):
        log_path = tmp_path / "mem.log"
        monkeypatch.setattr("haute.modelling._algorithms._MEM_LOG", log_path)

        _mem_checkpoint("test_label")

        content = log_path.read_text()
        assert "test_label" in content
        assert "RSS=" in content
        assert "Avail=" in content

    def test_appends_on_multiple_calls(self, tmp_path, monkeypatch):
        log_path = tmp_path / "mem.log"
        monkeypatch.setattr("haute.modelling._algorithms._MEM_LOG", log_path)

        _mem_checkpoint("first")
        _mem_checkpoint("second")

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_fsync_failure_does_not_raise(self, tmp_path, monkeypatch):
        """Defensive fsync: OSError from fsync must not crash the process."""
        log_path = tmp_path / "mem.log"
        monkeypatch.setattr("haute.modelling._algorithms._MEM_LOG", log_path)

        with patch("os.fsync", side_effect=OSError("not supported")):
            _mem_checkpoint("test")  # should not raise

        assert "test" in log_path.read_text()


def test_get_rss_mb_converts_bytes_to_mebibytes(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.modelling._algorithms as algorithms_mod

    monkeypatch.setattr(algorithms_mod, "current_process_rss_bytes", lambda: 3 * 1024 * 1024)
    assert algorithms_mod._get_rss_mb() == 3.0
