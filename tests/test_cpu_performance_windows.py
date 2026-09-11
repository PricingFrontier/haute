"""Exercise the real Windows policy in independently spawned Haute workers."""

from __future__ import annotations

import ctypes
import multiprocessing
import pickle
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process power policy")


class _Policy(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    ]


def _policy(*, reset: bool = False) -> tuple[int, int]:
    # Independent native oracle: do not query through the production helper.
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    for name in ("GetProcessInformation", "SetProcessInformation"):
        function = getattr(kernel, name)
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(_Policy),
            ctypes.c_uint32,
        ]
        function.restype = ctypes.c_int
    handle = kernel.GetCurrentProcess()
    state = _Policy(1, 0, 0)
    assert kernel.GetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state))
    if reset:
        state.ControlMask &= ~1
        state.StateMask &= ~1
        assert kernel.SetProcessInformation(handle, 4, ctypes.byref(state), ctypes.sizeof(state))
        assert _policy() == (state.ControlMask, state.StateMask)
    return state.ControlMask, state.StateMask


def _protocol_probe(_runtime: Any, _request: Any) -> Any:
    from haute._worker_protocol import WorkerResultManifest

    return WorkerResultManifest(metadata={"policy": _policy()})


def _start_with_automatic_policy(
    kind: str, results: Any, progress: Any, requests: Any, artifact_root: str
) -> None:
    _policy(reset=True)
    if kind == "isolated":
        from haute._worker_isolation import _isolated_worker_entrypoint

        _isolated_worker_entrypoint(results, _policy, (), {}, None)
    elif kind == "protocol":
        from haute._worker_protocol import WorkerRequest, _protocol_entrypoint

        _protocol_entrypoint(
            results,
            progress,
            _protocol_probe,
            WorkerRequest("probe", "probe", {}),
            artifact_root,
            None,
        )
    else:
        from haute._interactive_workers import _interactive_worker_entrypoint

        _interactive_worker_entrypoint(requests, results, ())


@pytest.mark.parametrize("kind", ["isolated", "protocol", "interactive"])
def test_spawned_worker_enables_high_qos_before_user_work(kind: str, tmp_path: Path) -> None:
    parent_policy = _policy()
    context = multiprocessing.get_context("spawn")
    results, progress, requests = (context.Queue() for _ in range(3))
    process = context.Process(
        target=_start_with_automatic_policy,
        args=(kind, results, progress, requests, str(tmp_path)),
    )
    process.start()
    try:
        if kind == "interactive":
            assert pickle.loads(results.get(timeout=30)) == ("ready", process.pid)
            # Two jobs also prove the warm worker keeps its policy between requests.
            for job_id in ("first", "second"):
                requests.put(pickle.dumps(("run", job_id, _policy, (), {}, None, False)))
                envelope = pickle.loads(results.get(timeout=30))
                assert envelope[:3] == ("result", job_id, "ok"), envelope
                control, state = envelope[3]
                assert control & 1 and not state & 1
                requests.put(pickle.dumps(("ack", job_id)))
                assert pickle.loads(results.get(timeout=30)) == ("released", job_id, "ok", None)
            requests.put(pickle.dumps(("shutdown",)))
        else:
            envelope = results.get(timeout=30)
            if kind == "isolated":
                envelope = pickle.loads(envelope)
                assert envelope[0] == "ok", envelope
                control, state = envelope[1]
            else:
                assert envelope[0] == "ok", envelope
                control, state = envelope[1].metadata["policy"]
            assert control & 1 and not state & 1
        process.join(timeout=30)
        assert process.exitcode == 0
        assert _policy() == parent_policy
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
        process.close()
        for queue in (results, progress, requests):
            queue.close()
            queue.join_thread()
