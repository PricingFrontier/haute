"""End-to-end assistant integration: fake provider, real everything else.

Spec: specs/assistant/low-level.md § Testing (integration).  A scripted
provider drives ``run_turn`` against a real tmp project; the mutation flows
through the real ops engine, the real transactional save service (codegen on
disk), the real re-parse, and the real event bus.  Only two seams are faked:
the provider (scripted events) and — for mutation-enabled scenarios — the
git working-branch readiness (returning ``ready`` requires a full git
fixture; the state→reason mapping has its own unit tests, and the REAL
unset-state precondition is exercised here without patching).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from haute._event_bus import default_bus
from haute.assistant._providers import ProviderUsage, TextDelta, ToolCallRequest, TurnStop
from haute.assistant._session import SessionStore
from haute.assistant._tools import build_tool_executor, get_pipeline
from haute.routes._helpers import save_lock

PIPELINE_SOURCE = '''\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="integration fixture")

# haute:preserve-start
CUSTOM_CONSTANT = 42
# haute:preserve-end


@pipeline.polars
def quotes() -> pl.LazyFrame:
    """Source rows."""

    return pl.LazyFrame({"x": [1, 2]})
'''

PRESERVED_BLOCK = "# haute:preserve-start\nCUSTOM_CONSTANT = 42\n# haute:preserve-end"

ADD_NODE_OPS = [
    {
        "op": "add_node",
        "node_type": "polars",
        "name": "derived step",
        "config": {"code": "df"},
        "ref": "d",
    },
    {"op": "add_edge", "source": "quotes", "target": "$d"},
]


class ScriptedProvider:
    def __init__(self, rounds: list[list[object]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[list[dict]] = []

    async def stream_turn(self, *, system, messages, tools):
        self.calls.append([dict(message) for message in messages])
        for event in self._rounds.pop(0):
            yield event


def _usage() -> ProviderUsage:
    return ProviderUsage(input_tokens=1, output_tokens=1)


def _mutation_rounds() -> list[list[object]]:
    return [
        [
            ToolCallRequest("t1", "apply_graph_edits", {"ops": ADD_NODE_OPS}),
            TurnStop("tool_use", _usage()),
        ],
        [TextDelta("added"), TurnStop("end", _usage())],
    ]


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def mutations_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.assistant._tools as tools_module

    monkeypatch.setattr(tools_module, "mutations_readiness", lambda _root: (True, None))


async def _run_turn(provider: ScriptedProvider, store: SessionStore, session_id: str):
    from haute.assistant._loop import run_turn

    events = []
    async for event in run_turn(
        store,
        session_id,
        "add a derived step after quotes",
        provider=provider,
        tools=[],
        execute_tool=build_tool_executor("main.py"),
        system_prompt="s",
        turn_timeout=30.0,
        max_tool_calls=8,
    ):
        events.append(event)
    return events


class TestMutationEndToEnd:
    async def test_instruction_to_disk_broadcast_and_readback(
        self, project_root: Path, mutations_ready
    ):
        import threading

        loop_thread = threading.current_thread()
        published: list[dict] = []
        publish_threads: list[threading.Thread] = []

        def subscriber(payload) -> None:
            published.append(dict(payload))
            publish_threads.append(threading.current_thread())

        unsubscribe = default_bus.subscribe("graph.update", subscriber)
        try:
            store = SessionStore()
            session_id = store.create("main.py").id
            events = await _run_turn(ScriptedProvider(_mutation_rounds()), store, session_id)
        finally:
            unsubscribe()

        types = [event.type for event in events]
        assert types[-1] == "completed", [getattr(e, "message", e.type) for e in events]
        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is False, "mutation tool must succeed"

        # On-disk codegen: the new node exists as a function in the source.
        saved = (project_root / "main.py").read_text(encoding="utf-8")
        assert "def derived_step(" in saved
        assert "quotes" in saved

        # The preserve block survives byte-identically.
        assert PRESERVED_BLOCK in saved

        # The bus got exactly the watcher-shaped payload with the post-save
        # fingerprint, matching the loop's graph_updated event.
        assert len(published) == 1
        # Regression: publish must run on the event-loop thread — the
        # /ws/sync broadcast subscriber schedules onto the running loop and
        # silently skips when publish happens on a worker thread.
        assert publish_threads == [loop_thread]
        graph_updated = next(event for event in events if event.type == "graph_updated")
        assert published[0]["graph_fingerprint"] == graph_updated.fingerprint
        node_ids = {node["id"] for node in published[0]["graph"]["nodes"]}
        assert "derived_step" in node_ids

        # A subsequent read tool sees the assistant's own edit.
        rendered = get_pipeline("main.py")
        assert "derived_step" in {node["id"] for node in rendered["nodes"]}
        edge_pairs = {(edge["source"], edge["target"]) for edge in rendered["edges"]}
        assert ("quotes", "derived_step") in edge_pairs

    async def test_precondition_blocks_mutations_without_ready_branch(self, project_root: Path):
        """REAL working-branch state: a bare tmp project is not 'ready', so
        the tool must refuse with the git reason and write nothing."""

        original = (project_root / "main.py").read_text(encoding="utf-8")
        store = SessionStore()
        session_id = store.create("main.py").id
        events = await _run_turn(ScriptedProvider(_mutation_rounds()), store, session_id)

        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is True
        assert (project_root / "main.py").read_text(encoding="utf-8") == original
        assert not [event for event in events if event.type == "graph_updated"]

    async def test_save_lock_excludes_concurrent_writers_through_publish(
        self, project_root: Path, mutations_ready, monkeypatch: pytest.MonkeyPatch
    ):
        """While the assistant's critical section runs, save_lock cannot be
        acquired — even when the consuming client cancels mid-save (the
        shielded flow completes before the lock is released)."""

        import haute.assistant._tools as tools_module

        in_save = asyncio.Event()
        release_save = asyncio.Event()
        original_blocking = tools_module._apply_graph_edits_blocking
        loop = asyncio.get_running_loop()

        def slow_blocking(source_file: str, ops_payload: object) -> dict[str, object]:
            loop.call_soon_threadsafe(in_save.set)
            while not release_save.is_set():
                pass
            return original_blocking(source_file, ops_payload)

        monkeypatch.setattr(tools_module, "_apply_graph_edits_blocking", slow_blocking)

        store = SessionStore()
        session_id = store.create("main.py").id
        consumer = asyncio.create_task(
            _run_turn(ScriptedProvider(_mutation_rounds()), store, session_id)
        )
        await in_save.wait()

        # A GUI-style writer cannot take the lock inside the critical section.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(save_lock.acquire(), timeout=0.05)

        # Cancel the consumer mid-save; the shielded save must still finish.
        consumer.cancel()
        await asyncio.sleep(0.01)
        assert save_lock.locked(), "lock must be held while the shielded save runs"
        release_save.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        # After the shielded flow completes, the write landed and the lock is free.
        for _ in range(100):
            if not save_lock.locked():
                break
            await asyncio.sleep(0.01)
        assert not save_lock.locked()
        saved = (project_root / "main.py").read_text(encoding="utf-8")
        assert "def derived_step(" in saved

    async def test_degraded_ledger_capture_warning_reaches_tool_result(
        self, project_root: Path, mutations_ready, monkeypatch: pytest.MonkeyPatch
    ):
        """The save service's degrade-to-warning path must propagate into the
        mutation payload (asserted via the provider's second-round messages).
        The degradation itself is the service's own tested behaviour; here it
        is simulated at its seam."""

        from haute.routes._save_pipeline import SavePipelineService

        def degraded_capture(self, touched, removed, warnings):
            warnings.append("Changes saved; version capture failed: simulated")
            return None

        monkeypatch.setattr(SavePipelineService, "_capture_save_in_ledger", degraded_capture)

        provider = ScriptedProvider(_mutation_rounds())
        store = SessionStore()
        session_id = store.create("main.py").id
        events = await _run_turn(provider, store, session_id)

        assert events[-1].type == "completed"
        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is False
        # The warning must be visible to the model in the tool result fed back.
        second_round = provider.calls[1]
        tool_messages = [message for message in second_round if message.get("role") == "tool"]
        assert tool_messages, second_round
        assert "version capture failed" in str(tool_messages[-1].get("content"))
