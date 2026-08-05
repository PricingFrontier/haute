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
        "node_type": "banding",
        "name": "Age band",
        "ref": "d",
    },
    {"op": "add_edge", "source": "quotes", "target": "$d"},
]


class ExactPlanProvider:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def stream_turn(self, *, system, messages, tools):
        self.calls.append([dict(message) for message in messages])
        round_number = len(self.calls)
        if round_number == 1:
            yield ToolCallRequest("t1", "dry_run_graph_edits", {"ops": ADD_NODE_OPS})
            yield TurnStop("tool_use", _usage())
            return
        if round_number == 2:
            dry_run_result = next(
                message["content"]
                for message in reversed(messages)
                if message.get("role") == "tool" and message.get("name") == "dry_run_graph_edits"
            )
            assert isinstance(dry_run_result, dict)
            plan_hash = dry_run_result["plan_hash"]
            assert isinstance(plan_hash, str)
            yield ToolCallRequest(
                "t2",
                "apply_graph_plan",
                {"plan_hash": plan_hash},
            )
            yield TurnStop("tool_use", _usage())
            return
        yield TextDelta("added")
        yield TurnStop("end", _usage())


def _usage() -> ProviderUsage:
    return ProviderUsage(input_tokens=1, output_tokens=1)


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore

    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    (tmp_path / "haute.toml").write_text(
        '[assistant]\nprovider = "openai"\nmodel = "test"\n'
        'base_url = "https://api.openai.com/v1"\n'
        '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "internal"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def mutations_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.assistant._tools as tools_module

    monkeypatch.setattr(tools_module, "mutations_readiness", lambda _root: (True, None))


async def _run_turn(provider: ExactPlanProvider, store: SessionStore, session_id: str):
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
            events = await _run_turn(ExactPlanProvider(), store, session_id)
        finally:
            unsubscribe()

        types = [event.type for event in events]
        assert types[-1] == "completed", [getattr(e, "message", e.type) for e in events]
        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is False, "mutation tool must succeed"

        # On-disk codegen: the new node exists as a function in the source.
        saved = (project_root / "main.py").read_text(encoding="utf-8")
        assert "def Age_band(" in saved
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
        assert "Age_band" in node_ids

        # A subsequent read tool sees the assistant's own edit.
        rendered = get_pipeline("main.py")
        assert "Age_band" in {node["id"] for node in rendered["nodes"]}
        edge_pairs = {(edge["source"], edge["target"]) for edge in rendered["edges"]}
        assert ("quotes", "Age_band") in edge_pairs

    async def test_precondition_blocks_mutations_without_ready_branch(self, project_root: Path):
        """REAL working-branch state: a bare tmp project is not 'ready', so
        the tool must refuse with the git reason and write nothing."""

        original = (project_root / "main.py").read_text(encoding="utf-8")
        store = SessionStore()
        session_id = store.create("main.py").id
        events = await _run_turn(ExactPlanProvider(), store, session_id)

        finished = next(
            event
            for event in events
            if event.type == "tool_finished" and event.name == "apply_graph_plan"
        )
        assert finished.is_error is True
        assert (project_root / "main.py").read_text(encoding="utf-8") == original
        assert not [event for event in events if event.type == "graph_updated"]

    async def test_save_lock_excludes_concurrent_writers_through_publish(
        self, project_root: Path, mutations_ready, monkeypatch: pytest.MonkeyPatch
    ):
        """While the assistant's critical section runs, save_lock cannot be
        acquired — even when the consuming client cancels mid-save (the
        shielded flow completes before the lock is released)."""

        in_save = asyncio.Event()
        release_save = asyncio.Event()
        from haute.assistant._application import PipelineApplicationService

        original_blocking = PipelineApplicationService._commit
        loop = asyncio.get_running_loop()

        def slow_blocking(self, source_file, after):
            loop.call_soon_threadsafe(in_save.set)
            while not release_save.is_set():
                pass
            return original_blocking(self, source_file, after)

        monkeypatch.setattr(PipelineApplicationService, "_commit", slow_blocking)

        store = SessionStore()
        session_id = store.create("main.py").id
        consumer = asyncio.create_task(_run_turn(ExactPlanProvider(), store, session_id))
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
        assert "def Age_band(" in saved

    async def test_degraded_ledger_capture_warning_reaches_tool_result(
        self, project_root: Path, mutations_ready, monkeypatch: pytest.MonkeyPatch
    ):
        """The save service's degrade-to-warning path must propagate into the
        committed neutral history. A successful apply is terminal, so no third
        provider round receives its result. The degradation itself is the
        service's own tested behaviour; here it is simulated at its seam."""

        from haute.routes._save_pipeline import SavePipelineService

        def degraded_capture(self, touched, removed, warnings):
            warnings.append("Changes saved; version capture failed: simulated")
            return None

        monkeypatch.setattr(SavePipelineService, "_capture_save_in_ledger", degraded_capture)

        provider = ExactPlanProvider()
        store = SessionStore()
        session_id = store.create("main.py").id
        events = await _run_turn(provider, store, session_id)

        applied = next(
            event
            for event in events
            if event.type == "tool_finished" and event.name == "apply_graph_plan"
        )
        assert applied.is_error is False, applied.summary
        assert events[-1].type == "completed", [repr(event) for event in events]
        finished = next(event for event in events if event.type == "tool_finished")
        assert finished.is_error is False
        assert len(provider.calls) == 2
        session = store.lookup(session_id)
        assert session is not None
        tool_messages = [
            message
            for message in session.history[-1].messages
            if message.role == "tool" and message.name == "apply_graph_plan"
        ]
        assert tool_messages
        assert "version capture failed" in str(tool_messages[-1].content)
