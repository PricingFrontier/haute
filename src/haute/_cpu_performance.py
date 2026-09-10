"""Keep user-requested Haute work out of Windows' automatic background QoS.

This is a process-local power policy, not a scheduling priority or CPU allocation.
Call at application/worker startup, never as a package-import side effect.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Literal

from haute._logging import get_logger

logger = get_logger(component="cpu_performance")

_PROCESS_POWER_THROTTLING = 4
_EXECUTION_SPEED = 1


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    ]


def _unavailable(
    operation: str,
    reason: str,
    *,
    winerror: int | None = None,
) -> Literal["unavailable"]:
    logger.warning(
        "process_high_qos_unavailable",
        pid=os.getpid(),
        status="unavailable",
        operation=operation,
        reason=reason,
        winerror=winerror,
        message="HighQoS could not be enabled; Haute will continue under the host CPU policy.",
    )
    return "unavailable"


def configure_process_high_qos() -> Literal["high", "not_applicable", "unavailable"]:
    """Request and verify HighQoS for this process, preserving other policy bits.

    Windows documents this setting for performance-critical user work. An OS/API
    limitation must remain observable without preventing that work from running.
    The policy lasts until process exit; a child configures itself independently.
    """
    if sys.platform != "win32":
        return "not_applicable"

    try:
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        current_process = kernel32.GetCurrentProcess
        current_process.argtypes = []
        current_process.restype = ctypes.c_void_p
        read = kernel32.GetProcessInformation
        write = kernel32.SetProcessInformation
        for function in (read, write):
            function.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(_PowerThrottlingState),
                ctypes.c_uint32,
            ]
            function.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        return _unavailable("bind", str(exc), winerror=getattr(exc, "winerror", None))

    operation = "read"
    try:
        # GetCurrentProcess returns a pseudo-handle: it must not be closed.
        handle = current_process()
        state = _PowerThrottlingState(1, 0, 0)
        size = ctypes.sizeof(state)
        if not read(handle, _PROCESS_POWER_THROTTLING, ctypes.byref(state), size):
            return _unavailable(operation, "native_call_failed", winerror=ctypes.get_last_error())

        if not (state.ControlMask & _EXECUTION_SPEED) or state.StateMask & _EXECUTION_SPEED:
            state.ControlMask |= _EXECUTION_SPEED
            state.StateMask &= ~_EXECUTION_SPEED
            operation = "write"
            if not write(handle, _PROCESS_POWER_THROTTLING, ctypes.byref(state), size):
                return _unavailable(
                    operation, "native_call_failed", winerror=ctypes.get_last_error()
                )
            operation = "verify"
            observed = _PowerThrottlingState(1, 0, 0)
            if not read(handle, _PROCESS_POWER_THROTTLING, ctypes.byref(observed), size):
                return _unavailable(
                    operation, "native_call_failed", winerror=ctypes.get_last_error()
                )
            if (observed.ControlMask, observed.StateMask) != (state.ControlMask, state.StateMask):
                return _unavailable(operation, "policy_not_applied")
    except OSError as exc:
        return _unavailable(operation, str(exc), winerror=getattr(exc, "winerror", None))

    logger.info("process_high_qos_configured", pid=os.getpid(), status="high")
    return "high"
