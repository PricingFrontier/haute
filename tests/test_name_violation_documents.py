"""A loaded file with colliding names says so and stays renameable (NAME-02).

Semantic name violations load into a ready, editable document that lists them
and cannot be saved, executed or previewed; the editor identity request reports
what remains after each rename. Structural collisions, which would collapse two
nodes into one graph id, still go to recovery.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._flatten import flatten_executable_graph
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._types import PipelineGraph
from haute.errors import ParseError
from haute.parser import parse_pipeline_source_with_name_violations
from haute.routes._save_pipeline import SavePipelineService

_CHILD = """
import haute
submodel = haute.Submodel(
    "rates",
    definition_id="rates",
    input_ports=[],
    output_ports=[],
    pipeline_dir="..",
)

@submodel.polars
def transform():
    return None
"""

_PARENT = """
import haute
import polars as pl
pipeline = haute.Pipeline("violations")

@pipeline.polars
def transform():
    return None

@pipeline.polars
def pl():
    return None

pipeline.submodel("modules/rates.py", "rates_1")
"""


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "modules" / "rates.py", _CHILD)
    return _write(tmp_path / "main.py", _PARENT)


def _parsed(path: Path) -> PipelineGraph:
    graph, _violations = parse_pipeline_source_with_name_violations(
        path.read_text(encoding="utf-8"),
        source_file=str(path),
        _base_dir=path.parent,
    )
    return graph


def _renamed(graph: PipelineGraph, node_id: str, label: str) -> PipelineGraph:
    nodes = [
        node.model_copy(update={"data": node.data.model_copy(update={"label": label})})
        if node.id == node_id
        else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": nodes})


def test_a_file_with_name_violations_loads_editable_listing_each(
    project: Path, tmp_path: Path
) -> None:
    document = load_pipeline_editor_document(project, project_root=tmp_path)

    assert document.load_status == "ready"
    assert [(v.kind, v.name) for v in document.name_violations] == [
        ("reserved", "pl"),
        ("duplicate", "transform"),
    ]
    duplicate = document.name_violations[1]
    assert [(party.node_id, party.submodel) for party in duplicate.parties] == [
        ("transform", None),
        ("transform", "rates"),
    ]
    capabilities = document.capabilities
    assert capabilities.can_mutate is True
    assert (capabilities.can_save, capabilities.can_execute, capabilities.can_preview) == (
        False,
        False,
        False,
    )


def test_a_clean_file_has_no_violations_and_every_capability(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("clean")

        @pipeline.polars
        def source():
            return None
        """,
    )

    document = load_pipeline_editor_document(path, project_root=tmp_path)

    assert document.name_violations == []
    assert document.capabilities.can_save and document.capabilities.can_preview


def test_save_and_execution_refuse_the_file_until_the_renames_clear_it(project: Path) -> None:
    graph = _parsed(project)

    with pytest.raises(HTTPException, match="takes the name `pl`"):
        SavePipelineService(project.parent).validate_graph(graph, source_file="main.py")
    with pytest.raises(ParseError, match="take one name, `transform`"):
        flatten_executable_graph(graph)

    fixed = _renamed(_renamed(graph, "pl", "polars_rows"), "transform", "root transform")

    SavePipelineService(project.parent).validate_graph(fixed, source_file="main.py")
    flatten_executable_graph(fixed)


def test_the_identity_request_reports_what_remains_after_each_rename(
    project: Path, client: TestClient
) -> None:
    graph = _parsed(project)
    after_first = _renamed(graph, "transform", "root transform")

    def violations(candidate: PipelineGraph) -> list[str]:
        response = client.post(
            "/api/pipeline/editor-identities",
            json={"nodes": [], "graph": candidate.model_dump(mode="json", by_alias=True)},
        )
        assert response.status_code == 200, response.text
        return [violation["name"] for violation in response.json()["violations"]]

    assert violations(graph) == ["pl", "transform"]
    assert violations(after_first) == ["pl"]
    assert violations(_renamed(after_first, "pl", "polars_rows")) == []


def test_a_preview_of_a_graph_with_a_violation_reports_it_at_the_node(
    project: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project.parent)
    graph = _parsed(project)

    response = client.post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(mode="json", by_alias=True), "node_id": "pl"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "error"
    assert "takes the name `pl`" in response.json()["error"]


def test_without_a_naming_context_the_response_reports_no_violations(client: TestClient) -> None:
    response = client.post("/api/pipeline/editor-identities", json={"nodes": []})

    assert response.json() == {"identities": [], "violations": None}


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            import haute
            pipeline = haute.Pipeline("twice")

            @pipeline.polars
            def rows():
                return None

            @pipeline.polars
            def rows():
                return None
            """,
            id="two-functions-one-name",
        ),
        pytest.param(
            """
            import haute
            pipeline = haute.Pipeline("occurrences")

            pipeline.submodel("modules/rates.py", "rates_1")
            pipeline.submodel("modules/rates.py", "rates_1")
            """,
            id="two-occurrences-one-name",
        ),
        pytest.param(
            """
            import haute
            pipeline = haute.Pipeline("occurrence-like-node")

            @pipeline.polars
            def rates_1():
                return None

            pipeline.submodel("modules/rates.py", "rates_1")
            """,
            id="occurrence-named-like-a-root-node",
        ),
    ],
)
def test_a_structural_collision_still_loads_into_recovery(tmp_path: Path, source: str) -> None:
    _write(tmp_path / "modules" / "rates.py", _CHILD)
    path = _write(tmp_path / "main.py", source)

    document = load_pipeline_editor_document(path, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.name_violations == []
    # Nothing collapsed: both colliding entries are listed, each under its own recovery id.
    colliding = [node for node in document.nodes if node.availability == "unavailable"]
    assert len(colliding) == 2
    assert len({node.authored_id for node in colliding}) == 1
    assert len({node.recovery_id for node in colliding}) == 2


def test_a_flattened_parse_checks_names_before_copying_occurrences(tmp_path: Path) -> None:
    _write(tmp_path / "modules" / "rates.py", _CHILD)
    source = textwrap.dedent(
        """
        import haute
        pipeline = haute.Pipeline("twice")

        pipeline.submodel("modules/rates.py", "rates_1")
        pipeline.submodel("modules/rates.py", "rates_2", instance_of="rates_1")
        """
    )

    graph, violations = parse_pipeline_source_with_name_violations(
        source, source_file=str(tmp_path / "main.py"), flatten=True, _base_dir=tmp_path
    )

    assert violations == []
    assert not graph.submodels


def test_a_node_named_global_constants_loads_renameable(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("constants")

        @pipeline.polars
        def global_constants():
            return None
        """,
    )

    document = load_pipeline_editor_document(path, project_root=tmp_path)

    assert document.load_status == "ready"
    assert [(v.kind, v.name) for v in document.name_violations] == [
        ("reserved", "global_constants")
    ]
