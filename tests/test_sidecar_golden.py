"""Golden snapshots for persisted sidecar payloads."""

from __future__ import annotations

import json
from pathlib import Path

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes._helpers import load_sidecar, save_sidecar

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "golden"


def _fixture_text(name: str) -> str:
    return (_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8")


def _fixture_json(name: str) -> dict[str, object]:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _make_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="alpha",
                position={"x": 10.0, "y": 20.0},
                data=NodeData(label="Alpha Node", nodeType=NodeType.POLARS),
            ),
            GraphNode(
                id="beta",
                position={"x": -5.5, "y": 12.25},
                data=NodeData(label="beta", nodeType=NodeType.OUTPUT),
            ),
        ],
        edges=[],
        sources=["live", "batch_a"],
        active_source="batch_a",
    )


def test_save_sidecar_emits_canonical_json_snapshot(tmp_path: Path) -> None:
    py_path = tmp_path / "pipeline.py"

    warnings = save_sidecar(py_path, _make_graph())

    assert warnings == []
    assert py_path.with_suffix(".haute.json").read_text(encoding="utf-8") == _fixture_text(
        "sidecar_with_source_state"
    )


def test_canonical_sidecar_fixture_round_trips_through_loader(tmp_path: Path) -> None:
    py_path = tmp_path / "pipeline.py"
    py_path.write_text("", encoding="utf-8")
    py_path.with_suffix(".haute.json").write_text(
        _fixture_text("sidecar_with_source_state"),
        encoding="utf-8",
    )

    assert load_sidecar(py_path) == _fixture_json("sidecar_with_source_state")
