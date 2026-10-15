"""One way into the project for the editor's writes (specs/server-api): `project_write`
takes each in turn with every other save, under the repository's lock, a failure
releasing both, so a write's checks, the write and its capture are never split.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from haute._git_lock import repository_mutation
from haute.routes import _helpers
from haute.routes._helpers import project_write


@pytest.fixture()
def fresh_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    # The module's lock binds to the loop that first contends it; each test has its own.
    monkeypatch.setattr(_helpers, "save_lock", asyncio.Lock())


async def test_writes_take_turns_a_failure_passing_the_turn_on(
    tmp_path: Path, fresh_lock: None
) -> None:
    ran: list[str] = []
    first_in = threading.Event()
    let_go = threading.Event()

    def slow() -> str:
        ran.append("first starts")
        first_in.set()
        let_go.wait(timeout=5)
        ran.append("first ends")
        return "one"

    def refused() -> str:
        ran.append("second")
        raise ValueError("refused")

    def third() -> str:
        ran.append("third")
        return "three"

    first = asyncio.ensure_future(project_write(tmp_path, slow))
    await asyncio.get_running_loop().run_in_executor(None, first_in.wait, 5)
    second = asyncio.ensure_future(project_write(tmp_path, refused))
    last = asyncio.ensure_future(project_write(tmp_path, third))
    await asyncio.sleep(0.05)
    # The others wait their turn while the first runs.
    assert ran == ["first starts"]

    let_go.set()
    assert await first == "one"
    with pytest.raises(ValueError, match="refused"):
        await second
    assert await last == "three"
    assert ran == ["first starts", "first ends", "second", "third"]


async def test_a_write_holds_the_repository_lock_a_branch_switch_takes(
    tmp_path: Path, fresh_lock: None
) -> None:
    switched: list[bool] = []
    switch_done = threading.Event()

    def switch() -> None:
        with repository_mutation(tmp_path):
            switched.append(True)
        switch_done.set()

    def write() -> list[bool]:
        thread = threading.Thread(target=switch)
        thread.start()
        # The switch waits on the lock the write holds.
        switch_done.wait(timeout=0.2)
        return list(switched)

    assert await project_write(tmp_path, write) == []
    switch_done.wait(timeout=5)
    assert switched == [True]
