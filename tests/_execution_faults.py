"""Test-only fault injection at execution checkpoints.

Production code carries no fault points. A test that must fail or observe an
execution at a named boundary builds a :class:`FaultInjectingExecutionContext`
instead of a plain context: each checkpoint first reports an
:class:`ExecutionFaultPoint` to the injector, which may raise to fail the
execution there, before the checkpoint does any work of its own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from haute._execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class ExecutionFaultPoint:
    """One checkpoint an execution reached, numbered in the order reached."""

    name: str
    operation: str
    node_id: str | None
    sequence: int


class FaultInjectingExecutionContext(ExecutionContext):
    """An execution context that reports every checkpoint to ``fault_injector`` first."""

    def __init__(
        self,
        *args: Any,
        fault_injector: Callable[[ExecutionFaultPoint], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fault_injector = fault_injector
        self._fault_lock = threading.Lock()
        self._fault_sequence = 0

    def checkpoint(self, *, label: str, node_id: str | None = None) -> None:
        with self._fault_lock:
            self._fault_sequence += 1
            point = ExecutionFaultPoint(
                name=label,
                operation=self.operation,
                node_id=node_id,
                sequence=self._fault_sequence,
            )
        self.fault_injector(point)
        super().checkpoint(label=label, node_id=node_id)
