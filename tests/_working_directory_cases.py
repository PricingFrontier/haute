"""Two tests run in this order by ``tests/test_project_context.py``, never collected alone.

The first is strict, so its sandbox changes into its ``tmp_path``, and it undoes its
own monkeypatch, which takes that change back early. The second must still start in
the directory the process started in.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_STARTED_IN = os.getcwd()


@pytest.mark.sandbox_strict
def test_a_strict_test_undoes_its_monkeypatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Path.cwd() == tmp_path
    monkeypatch.undo()


def test_the_next_test_starts_where_the_process_started() -> None:
    assert os.getcwd() == _STARTED_IN
