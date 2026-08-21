"""Mutation witness for output-assembly cancellation checkpoints."""

from __future__ import annotations

import haute._output_assembler as assembler


def test_output_assembly_checkpoint_occurs_exactly_at_1024_rows() -> None:
    class Context:
        def __init__(self) -> None:
            self.labels: list[str] = []

        def checkpoint(self, *, label: str) -> None:
            self.labels.append(label)

    assert assembler._OUTPUT_ASSEMBLY_CHECKPOINT_ROWS == 1_024
    context = Context()
    progress = assembler._OutputAssemblyProgress(context)
    for _ in range(1_023):
        progress.advance("assemble")
    assert context.labels == []
    assert progress.rows_since_checkpoint == 1_023
    progress.advance("assemble")
    assert context.labels == ["assemble"]
    assert progress.rows_since_checkpoint == 0
