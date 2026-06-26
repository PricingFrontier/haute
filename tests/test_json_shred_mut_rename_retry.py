"""Mutation witnesses for the cache-dir rename retry loop (W2 item 2.6).

:func:`_rename_dir_with_retry` exists for Windows, where a transient handle lock
makes ``Path.rename`` raise ``PermissionError`` mid-publish; it retries with a
short backoff and only re-raises once the delays are exhausted. On POSIX CI the
except clause is never reached, so these branches need a monkeypatched
``Path.rename`` to be exercised at all — without it the whole retry path is a
dark, untested corner of the atomic cache swap.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from haute._json_shred import _rename_dir_with_retry


def test_rename_retries_on_permission_error_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The except clause must catch PermissionError specifically (L317) and the
    # loop must RETRY rather than give up on the first failure (the L318
    # ``if delay is None`` gate, un-negated). Two transient locks then success:
    # a mutant that catches a different exception lets the PermissionError
    # propagate; an AddNot on the delay gate re-raises on the first attempt. Both
    # make this expected-success call raise instead.
    src = tmp_path / "src"
    src.mkdir()
    (src / "marker.txt").write_text("payload", encoding="utf-8")
    dst = tmp_path / "dst"

    real_rename = Path.rename
    calls = {"n": 0}

    def flaky_rename(self: Path, target: Any) -> Any:  # noqa: ANN401
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("transient Windows handle lock")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    _rename_dir_with_retry(src, dst)

    assert calls["n"] == 3  # failed twice, succeeded on the third
    assert dst.exists()
    assert not src.exists()
    assert (dst / "marker.txt").read_text(encoding="utf-8") == "payload"


def test_rename_reraises_after_exhausting_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When every attempt fails, the final ``delay is None`` iteration must
    # re-raise (L318). An ``is`` -> ``is not`` mutant inverts that gate so the
    # loop falls through and returns None silently — a swallowed failure that
    # would leave the cache half-published. pytest.raises pins the re-raise.
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"

    def always_locked(self: Path, target: Any) -> Any:  # noqa: ANN401
        raise PermissionError("permanently locked")

    monkeypatch.setattr(Path, "rename", always_locked)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(PermissionError):
        _rename_dir_with_retry(src, dst)
