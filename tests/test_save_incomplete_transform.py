"""Saving a graph whose transform is not written yet.

A half-built graph is an ordinary state to leave the editor in, so Save must
accept a transform that has no code — whether it has no upstream at all, or
several with no code saying how to combine them. Both used to be a hard
``ConfigError`` from codegen, which meant the user's other work could not be
saved either.

The contract Save now keeps:

* the save succeeds and reports a warning naming the node;
* the generated ``.py`` is valid Python that FAILS LOUDLY if run, rather than
  silently passing one input through and dropping the rest;
* reopening the pipeline shows the node still empty — the generated placeholder
  is scaffold, never adopted as the user's own code.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._code_extraction import INCOMPLETE_TRANSFORM_MESSAGE
from haute._types import PipelineGraph, SubmodelDefinition
from haute.executor import execute_graph
from haute.routes._save_pipeline import SavePipelineService


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Save writes real files, so each test gets its own working directory."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def client(isolated_cwd: Path) -> TestClient:
    from haute.server import app

    return TestClient(app)


def _transform_node(node_id: str, label: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "pipelineNode",
        "position": {"x": 0, "y": 0},
        "data": {"label": label, "nodeType": "polars", "config": {}},
    }


def _source_node(node_id: str, label: str, path: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "pipelineNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": label,
            "nodeType": "dataInput",
            "config": {
                "inputType": "file",
                "format": "csv",
                "mode": "scan",
                "path": path,
                "arguments": {},
            },
        },
    }


def _frame_source_node(node_id: str, label: str) -> dict[str, Any]:
    """Self-contained polars source for executor tests (no snapshot dependency)."""
    return {
        "id": node_id,
        "type": "pipelineNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "label": label,
            "nodeType": "polars",
            "config": {"code": f"df = pl.DataFrame({{'source': ['{label}']}})"},
        },
    }


def _save(client: TestClient, graph: dict[str, Any], name: str) -> Any:
    return client.post(
        "/api/pipeline/save",
        json={
            "name": name,
            "description": "",
            "graph": graph,
            "source_file": f"{name}.py",
        },
    )


@pytest.fixture
def two_sources(isolated_cwd: Path) -> list[dict[str, Any]]:
    for name in ("left.csv", "right.csv"):
        (isolated_cwd / name).write_text("id\n1\n", encoding="utf-8")
    return [
        _source_node("src_a", "left", "left.csv"),
        _source_node("src_b", "right", "right.csv"),
    ]


def test_save_succeeds_for_a_transform_with_several_inputs_and_no_code(
    client: TestClient, isolated_cwd: Path, two_sources: list[dict[str, Any]]
) -> None:
    """The reported case: two frames into one transform that has no code yet."""
    graph = {
        "nodes": [*two_sources, _transform_node("polars_3", "claims")],
        "edges": [
            {"id": "e1", "source": "src_a", "target": "polars_3"},
            {"id": "e2", "source": "src_b", "target": "polars_3"},
        ],
    }

    response = _save(client, graph, "claims_pipe")

    assert response.status_code == 200, response.text
    warnings = response.json().get("warnings") or []
    assert any("claims" in w for w in warnings), warnings

    source = (isolated_cwd / "claims_pipe.py").read_text(encoding="utf-8")
    # Fails loudly if run, and never silently drops one of the two inputs.
    assert "raise NotImplementedError" in source
    assert "def claims(left" in source and "right" in source


def test_save_succeeds_for_a_transform_with_no_inputs_and_no_code(
    client: TestClient, isolated_cwd: Path
) -> None:
    """An orphan transform is equally unfinished, not a reason to block a save."""
    graph = {"nodes": [_transform_node("polars_1", "todo")], "edges": []}

    response = _save(client, graph, "orphan_pipe")

    assert response.status_code == 200, response.text
    warnings = response.json().get("warnings") or []
    assert any("todo" in w for w in warnings), warnings

    source = (isolated_cwd / "orphan_pipe.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" in source
    # `df` is never bound here, so returning it would be a NameError at run time.
    assert "return df" not in source


def test_reopening_shows_the_unfinished_transform_still_empty(
    client: TestClient, isolated_cwd: Path, two_sources: list[dict[str, Any]]
) -> None:
    """Round-trip: the placeholder must not come back as the user's own code,
    or every save would silently write a `raise` into their empty node."""
    graph = {
        "nodes": [*two_sources, _transform_node("polars_3", "claims")],
        "edges": [
            {"id": "e1", "source": "src_a", "target": "polars_3"},
            {"id": "e2", "source": "src_b", "target": "polars_3"},
        ],
    }
    assert _save(client, graph, "roundtrip_pipe").status_code == 200

    from haute.routes import _helpers

    saved = isolated_cwd / "roundtrip_pipe.py"
    reloaded = _helpers.parse_pipeline_to_graph(saved, project_root=isolated_cwd)
    transform = next(n for n in reloaded.nodes if n.data.label == "claims")
    assert str(transform.data.config.get("code") or "").strip() == ""

    # Again after a formatter has been near the file. The generated .py is a
    # real source file that editors, pre-commit hooks and `ruff format` touch,
    # and normalising the placeholder's quote style must not turn it into the
    # user's code. Checking only the freshly-emitted text missed exactly this.
    formatted = saved.read_text(encoding="utf-8").replace(
        "'This transform has no code yet. Add code that defines what it returns.',",
        '"This transform has no code yet. Add code that defines what it returns.",',
    )
    assert '"This transform has no code yet' in formatted, "quote normalisation not applied"
    saved.write_text(formatted, encoding="utf-8")

    reloaded_after_format = _helpers.parse_pipeline_to_graph(saved, project_root=isolated_cwd)
    transform = next(n for n in reloaded_after_format.nodes if n.data.label == "claims")
    assert str(transform.data.config.get("code") or "").strip() == ""


def test_a_single_input_transform_with_no_code_saves_a_raising_placeholder(
    client: TestClient, isolated_cwd: Path, two_sources: list[dict[str, Any]]
) -> None:
    """A polars node has no implicit passthrough: exactly one upstream and no
    code still saves, but the generated body raises and the save warns."""
    graph = {
        "nodes": [two_sources[0], _transform_node("polars_3", "passthrough")],
        "edges": [{"id": "e1", "source": "src_a", "target": "polars_3"}],
    }

    response = _save(client, graph, "passthrough_pipe")

    assert response.status_code == 200, response.text
    source = (isolated_cwd / "passthrough_pipe.py").read_text(encoding="utf-8")
    assert "return left" not in source
    assert "raise NotImplementedError" in source
    warnings = response.json().get("warnings") or []
    assert any("passthrough" in w for w in warnings), warnings


def test_live_executor_rejects_a_transform_with_several_inputs_and_no_code() -> None:
    """Canvas execution must keep the same fail-loud contract as generated code.

    The generic passthrough helper returns the first frame, so wiring an empty
    multi-input transform to it silently discarded every other input.
    """
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                _frame_source_node("src_a", "left"),
                _frame_source_node("src_b", "right"),
                _transform_node("polars_3", "claims"),
            ],
            "edges": [
                {"id": "e1", "source": "src_a", "target": "polars_3"},
                {"id": "e2", "source": "src_b", "target": "polars_3"},
            ],
        }
    )

    result = execute_graph(graph, target_node_id="polars_3")

    assert result["polars_3"].error is not None
    assert INCOMPLETE_TRANSFORM_MESSAGE in result["polars_3"].error


def test_live_executor_rejects_a_transform_with_no_inputs_and_no_code() -> None:
    """An orphan empty transform raises the same actionable placeholder error."""
    graph = PipelineGraph.model_validate(
        {"nodes": [_transform_node("polars_1", "todo")], "edges": []}
    )

    result = execute_graph(graph, target_node_id="polars_1")

    assert result["polars_1"].error is not None
    assert INCOMPLETE_TRANSFORM_MESSAGE in result["polars_1"].error


def test_save_warning_includes_an_incomplete_transform_inside_a_submodel(
    isolated_cwd: Path,
) -> None:
    """Every generated placeholder must be named, not only root-graph nodes."""
    child = PipelineGraph.model_validate(
        {"nodes": [_transform_node("child_todo", "child todo")], "edges": []}
    )
    definition = SubmodelDefinition(
        definitionId="pricing",
        file="modules/pricing.py",
        graph=child,
        inputPorts=[],
        outputPorts=[],
    )
    graph = PipelineGraph(submodels={"pricing": definition})
    warnings: list[str] = []

    SavePipelineService(isolated_cwd)._validate_transforms_are_runnable(graph, warnings)

    assert any("child todo" in warning for warning in warnings), warnings
