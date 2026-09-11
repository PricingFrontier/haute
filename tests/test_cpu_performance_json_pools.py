"""Process-pool startup wiring for JSON shredding."""

from __future__ import annotations

from pathlib import Path

import pytest


class _PoolConstructedError(Exception):
    pass


@pytest.mark.parametrize("pool_name", ["inference", "writer"])
def test_json_pool_uses_high_qos_initializer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pool_name: str
) -> None:
    captured: dict[str, object] = {}

    class CapturePool:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            raise _PoolConstructedError

    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", CapturePool)

    if pool_name == "inference":
        import haute._json_shred._inference as module

        monkeypatch.setattr(
            module,
            "_learn_jsonl_prefix",
            lambda *_args: (module._InferenceState(), None),
        )
        monkeypatch.setattr(module, "configure_process_high_qos", lambda: "unavailable")
        with pytest.raises(_PoolConstructedError):
            module._infer_jsonl_in_parallel(tmp_path / "source.jsonl", [(0, 1)])
    else:
        import haute._json_shred._writer as module

        monkeypatch.setattr(module, "configure_process_high_qos", lambda: "unavailable")
        with pytest.raises(_PoolConstructedError):
            module._write_tables_in_parallel(tmp_path / "source.jsonl", {}, (), tmp_path, [(0, 1)])

    initializer = captured["initializer"]
    assert initializer is module.configure_process_high_qos
    assert initializer() == "unavailable"  # type: ignore[operator]
