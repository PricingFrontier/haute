"""Frame targets the preview and preview-inputs requests name what they read by.

``PreviewNodeRequest.target`` and ``PreviewInputsRequest.target`` are a frame target
(specs/server-api/low-level.md, "Node addresses"): every field is required, so the suites
build one here rather than spelling the five keys of a node target at each call.
"""

from __future__ import annotations

from typing import Any


def node_target(
    node_id: str,
    *,
    occurrence: str | None = None,
    port_label: str | None = None,
    port_labels: list[str] | None = None,
) -> dict[str, Any]:
    """A node's output frame: a pipeline node's, or a child's of ``occurrence``."""
    return {
        "kind": "node",
        "occurrence": occurrence,
        "node_id": node_id,
        "port_label": port_label,
        "port_labels": port_labels,
    }


def occurrence_input_target(occurrence: str, port: str) -> dict[str, Any]:
    """The frame feeding a public input port of an occurrence."""
    return {"kind": "occurrence_input", "occurrence": occurrence, "port": port}


def occurrence_output_target(occurrence: str, port: str) -> dict[str, Any]:
    """The frame a public output port of an occurrence produces."""
    return {"kind": "occurrence_output", "occurrence": occurrence, "port": port}
