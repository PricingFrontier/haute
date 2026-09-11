"""Startup wiring for the process high-QoS policy."""

from __future__ import annotations

import asyncio
import pickle
from pathlib import Path

import click
import pytest
from click.testing import CliRunner


class _Queue:
    def __init__(self, items: list[object] | None = None) -> None:
        self.items = list(items or [])
        self.put_items: list[object] = []

    def get(self) -> object:
        return self.items.pop(0)

    def put(self, item: object) -> None:
        self.put_items.append(item)

    def close(self) -> None:
        pass

    def join_thread(self) -> None:
        pass


def test_cli_applies_unavailable_qos_before_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute.cli as cli_module

    events: list[str] = []
    monkeypatch.setattr(
        cli_module, "configure_process_high_qos", lambda: events.append("qos") or "unavailable"
    )

    @click.command("qos-startup-test")
    def command() -> None:
        events.append("command")

    cli_module.cli.add_command(command)
    try:
        result = CliRunner().invoke(cli_module.cli, ["qos-startup-test"])
    finally:
        cli_module.cli.commands.pop("qos-startup-test")

    assert result.exit_code == 0, result.output
    assert events == ["qos", "command"]


@pytest.mark.parametrize("entrypoint", ["isolated", "protocol", "interactive"])
def test_child_entrypoints_apply_unavailable_qos_before_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entrypoint: str
) -> None:
    events: list[str] = []
    results = _Queue()

    if entrypoint == "isolated":
        import haute._worker_isolation as module

        monkeypatch.setattr(
            module, "configure_process_high_qos", lambda: events.append("qos") or "unavailable"
        )
        module._isolated_worker_entrypoint(
            results, lambda: events.append("work") or "result", (), {}, None
        )
        assert pickle.loads(results.put_items[0]) == ("ok", "result")
    elif entrypoint == "protocol":
        import haute._worker_protocol as module

        monkeypatch.setattr(
            module, "configure_process_high_qos", lambda: events.append("qos") or "unavailable"
        )

        def work(_runtime: object, _request: object) -> object:
            events.append("work")
            return module.WorkerResultManifest(metadata={})

        module._protocol_entrypoint(
            results,
            _Queue(),
            work,
            module.WorkerRequest("id", "kind", {}),
            str(tmp_path),
            None,
        )
        assert results.put_items[0][0] == "ok"  # type: ignore[index]
    else:
        import haute._interactive_workers as module

        monkeypatch.setattr(
            module, "configure_process_high_qos", lambda: events.append("qos") or "unavailable"
        )
        monkeypatch.setattr(module.importlib, "import_module", lambda _name: events.append("work"))
        module._interactive_worker_entrypoint(
            _Queue([pickle.dumps(("shutdown",))]), results, ("example",)
        )
        assert pickle.loads(results.put_items[0])[0] == "ready"

    assert events == ["qos", "work"]


def test_server_lifespan_applies_unavailable_qos_before_worker_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import haute.deploy._config as deploy_config
    import haute.server as server

    events: list[str] = []

    class FakeTask:
        def __init__(self, coroutine: object) -> None:
            self.coroutine = coroutine

        def cancel(self) -> None:
            self.coroutine.close()  # type: ignore[union-attr]

        def __await__(self):
            self.coroutine.close()  # type: ignore[union-attr]
            if False:
                yield None
            return None

    monkeypatch.setattr(server, "_clear_bytecache", lambda: None)
    monkeypatch.setattr(server, "configure_logging", lambda: events.append("logging"))
    monkeypatch.setattr(
        server, "configure_process_high_qos", lambda: events.append("qos") or "unavailable"
    )
    monkeypatch.setattr(deploy_config, "_load_env", lambda _path: None)
    monkeypatch.setattr(server, "configure_execution_telemetry", lambda: None)
    monkeypatch.setattr(server, "recover_json_runtime_storage", lambda: None)
    monkeypatch.setattr(server, "_artifact_stale_seconds", lambda: 1)
    monkeypatch.setattr(server, "_ensure_pipeline_index", lambda: None)
    monkeypatch.setattr(server, "start_interactive_worker_pool", lambda: events.append("pool"))
    monkeypatch.setattr(server, "shutdown_interactive_worker_pool", lambda: None)
    monkeypatch.setattr(server.asyncio, "create_task", lambda coroutine: FakeTask(coroutine))
    monkeypatch.setattr(server, "_watcher_task", None)
    monkeypatch.setattr(server, "_optimiser_reaper_task", None)

    async def exercise() -> None:
        async with server._lifespan(server.app):
            assert events == ["logging", "qos", "pool"]

    asyncio.run(exercise())
