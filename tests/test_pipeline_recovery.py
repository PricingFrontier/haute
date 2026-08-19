"""Editor-only pipeline recovery contracts."""

from __future__ import annotations

import textwrap
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from haute._types import NodeType
from haute.errors import ConfigError, ParseError
from haute.parser import parse_pipeline_file, parse_pipeline_source
from haute.schemas import (
    PipelineEditorDocument,
    RecoveryPipelineNode,
    RecoveryPreviewRequest,
    RecoverySourceSpan,
    RecoverySubmodelDefinition,
    RecoveryUnresolvedConnection,
)
from tests.conftest import write_data_input_config


def _write(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _write_bytes(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _healthy_source(name: str = "healthy") -> str:
    return f'''\
        import haute

        pipeline = haute.Pipeline("{name}")

        @pipeline.polars
        def source():
            return None

        @pipeline.polars
        def transform(source):
            return source
    '''


def _legacy_explore_source(name: str = "legacy") -> str:
    """Return a valid pipeline whose old Explore pivot lacks value_order."""
    return f'''\
        import haute

        pipeline = haute.Pipeline("{name}")

        @pipeline.polars
        def quote_source():
            return None

        @pipeline.polars
        def aggregate(quote_source):
            return quote_source

        @pipeline.explore(pivots=[{{
            "version": 1,
            "id": "pivot_1",
            "name": "Legacy pivot",
            "enabled": True,
            "filters": [],
            "columns": [],
            "rows": [],
            "values": [{{
                "id": "value_1",
                "field": "premium",
                "aggregation": "sum",
                "reference": "premium_sum",
                "display_name": "Premium",
                "sort_rows": "none",
                "color_scale": "none",
                "color_scale_split_by": None,
                "number_format": "general",
                "decimal_places": None,
                "use_grouping": True,
            }}],
            "formulas": [],
            "options": {{
                "row_grand_totals": True,
                "column_grand_totals": True,
                "sort_by": None,
            }},
        }}])
        def explore(aggregate):
            return aggregate

        @pipeline.polars
        def model_input(aggregate):
            return aggregate

        pipeline.connect("quote_source", "aggregate")
        pipeline.connect("aggregate", "explore")
        pipeline.connect("aggregate", "model_input")
    '''


def test_strict_parser_rejects_syntax_while_recovery_preserves_healthy_nodes(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("broken-syntax")

        @pipeline.polars
        def healthy():
            return None

        def broken(:
            pass
        """,
    )

    with pytest.raises(ParseError, match="syntax"):
        parse_pipeline_file(pipeline_file)
    with pytest.raises(ParseError, match="syntax"):
        parse_pipeline_source(pipeline_file.read_text(encoding="utf-8"), source_file="main.py")

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    assert document.load_status == "degraded"
    assert [node.authored_id for node in document.nodes] == ["healthy"]
    assert document.nodes[0].availability == "ready"
    assert any(diagnostic.code == "python_syntax_error" for diagnostic in document.diagnostics)


def test_legacy_explore_failure_is_localised_without_writing_project_bytes(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with pytest.raises(ConfigError, match="value_order"):
        parse_pipeline_file(pipeline_file)

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert [node.authored_id for node in document.nodes] == [
        "quote_source",
        "aggregate",
        "explore",
        "model_input",
    ]
    availability = {node.authored_id: node.availability for node in document.nodes}
    assert availability == {
        "quote_source": "ready",
        "aggregate": "ready",
        "explore": "unavailable",
        "model_input": "ready",
    }
    explore = next(node for node in document.nodes if node.authored_id == "explore")
    diagnostic = next(
        item for item in document.diagnostics if item.diagnostic_id in explore.diagnostic_ids
    )
    assert diagnostic.code == "node_config_invalid"
    assert "value_order" in diagnostic.message
    assert diagnostic.source_span is not None
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_corrupt_position_sidecar_degrades_and_does_not_trust_source_selection(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _healthy_source())
    sidecar = tmp_path / "main.haute.json"
    sidecar.write_bytes(b'{"positions":')
    before = sidecar.read_bytes()

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.source_selection_trusted is False
    assert document.active_source is None
    assert document.capabilities.can_preview is False
    assert all(
        node.display_position == {"x": index * 300.0, "y": 0.0}
        for index, node in enumerate(document.nodes)
    )
    assert [diagnostic.code for diagnostic in document.diagnostics].count("sidecar_corrupt") == 1
    assert sidecar.read_bytes() == before


def test_unrecoverable_readable_source_returns_source_only(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("source-only")
        def broken(:
            pass
        """,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "source_only"
    assert document.nodes == []
    assert document.source_text == pipeline_file.read_text(encoding="utf-8")
    assert document.has_authored_content is True
    assert document.capabilities.can_mutate is False


def test_recovery_nodes_are_not_canonical_graph_nodes(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.schemas import Graph, GraphNode

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    with pytest.raises(ValidationError):
        GraphNode.model_validate(document.nodes[0].model_dump(mode="json"))
    with pytest.raises(ValidationError):
        Graph.model_validate(document.model_dump(mode="json"))

    source_only = load_pipeline_editor_document(
        _write(
            tmp_path / "source_only.py",
            'import haute\npipeline = haute.Pipeline("source-only")\ndef invalid(:\n',
        ),
        project_root=tmp_path,
    )
    assert source_only.nodes == []
    with pytest.raises(ValidationError, match="not canonical"):
        Graph.model_validate(source_only.model_dump(mode="json"))


@pytest.mark.parametrize(
    "request_model_name",
    [
        "SavePipelineRequest",
        "PreviewNodeRequest",
        "TraceRequest",
        "OutputDestinationRequest",
        "WriteOutputRequest",
        "ExploreRunRequest",
        "ExplorePivotRunRequest",
        "ExplorePivotMembersRequest",
        "CreateSubmodelRequest",
        "DissolveSubmodelRequest",
        "TrainRequest",
        "TrainEstimateRequest",
        "DispersionEstimateRequest",
        "ExportScriptRequest",
        "OptimiserSolveRequest",
        "OptimiserEstimateRequest",
        "OptimiserFrontierAutoRangeRequest",
    ],
)
def test_every_canonical_graph_request_model_rejects_recovery_documents(
    tmp_path: Path,
    request_model_name: str,
) -> None:
    from haute import schemas
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    request_model = getattr(schemas, request_model_name)

    with pytest.raises(ValidationError) as exc_info:
        request_model.model_validate({"graph": document.model_dump(mode="json", by_alias=True)})

    graph_errors = [error for error in exc_info.value.errors() if tuple(error["loc"]) == ("graph",)]
    assert len(graph_errors) == 1
    assert "not canonical" in graph_errors[0]["msg"]


@pytest.mark.parametrize(
    ("model", "payload", "message"),
    [
        (
            RecoverySourceSpan,
            {"start_line": 2, "start_column": 0, "end_line": 1, "end_column": 0},
            "end at or after",
        ),
        (
            RecoveryPipelineNode,
            {
                "recovery_id": "node",
                "authored_id": "node",
                "label": "node",
                "decorator_name": "polars",
                "node_type": "polars",
                "availability": "ready",
                "display_position": {"x": 0, "y": 0, "z": 0},
            },
            "exactly x and y",
        ),
        (
            RecoveryPipelineNode,
            {
                "recovery_id": "node",
                "authored_id": "node",
                "label": "node",
                "decorator_name": "polars",
                "node_type": "polars",
                "availability": "ready",
                "display_position": {"x": float("nan"), "y": float("inf")},
            },
            "must be finite",
        ),
    ],
)
def test_recovery_schema_rejects_invalid_source_ranges_and_positions(
    model: type[RecoverySourceSpan] | type[RecoveryPipelineNode],
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        model.model_validate(payload)


def test_recovery_preview_planner_rejects_unavailable_sources_and_nodes() -> None:
    from haute._pipeline_recovery import empty_pipeline_editor_document
    from haute.routes.pipeline import _plan_recovery_preview, _RecoveryPreviewRequestError
    from haute.schemas import RecoveryPipelineEdge

    def node(node_id: str, availability: str = "ready") -> RecoveryPipelineNode:
        return RecoveryPipelineNode(
            recovery_id=node_id,
            authored_id=node_id,
            label=node_id,
            decorator_name="polars",
            node_type="polars",
            availability=availability,  # type: ignore[arg-type]
            display_position={"x": 0, "y": 0},
            config={},
        )

    def document(**updates: object) -> PipelineEditorDocument:
        return empty_pipeline_editor_document().model_copy(
            update={
                "source_file": "main.py",
                "source_revision": "revision",
                "has_authored_content": True,
                **updates,
            }
        )

    def request(target: str, source: str = "live") -> RecoveryPreviewRequest:
        return RecoveryPreviewRequest(
            source_file="main.py",
            source_revision="revision",
            target_recovery_id=target,
            source=source,
        )

    cases = [
        (document(), request("missing", "archived"), "source_not_available", 400),
        (document(), request("missing"), "recovery_target_not_found", 404),
        (
            document(nodes=[node("target", "unavailable")]),
            request("target"),
            "node_unavailable",
            409,
        ),
        (
            document(
                nodes=[node("ancestor", "unavailable"), node("target")],
                edges=[
                    RecoveryPipelineEdge(
                        recovery_id="ancestor->target",
                        source_recovery_id="ancestor",
                        target_recovery_id="target",
                        source_authored_id="ancestor",
                        target_authored_id="target",
                        availability="unavailable",
                    )
                ],
            ),
            request("target"),
            "node_blocked_by_load_error",
            409,
        ),
        (
            document(
                nodes=[node("target")],
                edges=[
                    RecoveryPipelineEdge(
                        recovery_id="missing->target",
                        source_recovery_id="missing",
                        target_recovery_id="target",
                        source_authored_id="missing",
                        target_authored_id="target",
                        availability="unavailable",
                    )
                ],
            ),
            request("target"),
            "node_blocked_by_load_error",
            409,
        ),
        (
            document(
                nodes=[node("target")],
                unresolved_connections=[
                    RecoveryUnresolvedConnection(
                        recovery_id="missing->target",
                        source_authored_id="missing",
                        target_authored_id="target",
                        target_recovery_id="target",
                        diagnostic_ids=["connection_endpoint_missing"],
                    )
                ],
            ),
            request("target"),
            "node_blocked_by_unresolved_connection",
            409,
        ),
    ]

    for preview_document, preview_request, code, status_code in cases:
        with pytest.raises(_RecoveryPreviewRequestError) as exc_info:
            _plan_recovery_preview(preview_document, preview_request)
        assert exc_info.value.status_code == status_code
        assert exc_info.value.detail["code"] == code


def test_canonical_snapshot_rejects_unavailable_nodes_and_submodels() -> None:
    from haute.routes.pipeline import _canonical_snapshot_graph, _RecoveryPreviewRequestError
    from haute.schemas import RecoveryGraphSnapshot

    def node(
        node_id: str,
        availability: str = "ready",
        node_type: str = "polars",
        config: dict[str, object] | None = None,
    ) -> RecoveryPipelineNode:
        return RecoveryPipelineNode(
            recovery_id=node_id,
            authored_id=node_id,
            label=node_id,
            decorator_name="polars",
            node_type=node_type,
            availability=availability,  # type: ignore[arg-type]
            display_position={"x": 0, "y": 0},
            config={} if config is None else config,
        )

    unavailable_definition = RecoverySubmodelDefinition(
        definition_id="pricing",
        file="models/pricing.py",
        availability="unavailable",
        graph=RecoveryGraphSnapshot(),
    )
    cases = [
        ([node("broken", "unavailable")], None, "node_unavailable"),
        (
            [node("pricing", node_type=NodeType.SUBMODEL, config={"definitionId": "pricing"})],
            None,
            "submodel_unavailable",
        ),
        (
            [node("pricing", node_type=NodeType.SUBMODEL, config={"definitionId": "pricing"})],
            {"pricing": unavailable_definition},
            "submodel_unavailable",
        ),
    ]

    for nodes, submodels, code in cases:
        with pytest.raises(_RecoveryPreviewRequestError) as exc_info:
            _canonical_snapshot_graph(nodes=nodes, edges=[], submodels=submodels)
        assert exc_info.value.detail["code"] == code


def test_recovery_revision_tracks_malformed_config_and_missing_sidecar(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    config = tmp_path / "config" / "data_input" / "source.json"
    config.parent.mkdir(parents=True)
    config.write_bytes(b'{"inputType":')
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("revision")

        @pipeline.data_input(config="config/data_input/source.json")
        def source():
            return None
        """,
    )

    first = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    config.write_bytes(b'{"inputType": "file"')
    second = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    (tmp_path / "main.haute.json").write_text("{}", encoding="utf-8")
    third = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert first.source_revision != second.source_revision
    assert second.source_revision != third.source_revision


def test_recovery_revision_authenticates_the_bytes_the_document_presents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent parent edit must not pair old content with a new revision."""
    from haute import _pipeline_recovery
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_revision import pipeline_recovery_revision

    pipeline_file = _write(tmp_path / "main.py", _healthy_source("revision-byte-parity"))
    original_text = pipeline_file.read_text(encoding="utf-8")
    original_bytes = pipeline_file.read_bytes()
    real_artifacts = _pipeline_recovery._recovery_artifacts

    def edit_after_discovery(
        target_file: Path,
        *args: object,
        **kwargs: object,
    ):
        artifacts = real_artifacts(*args, **kwargs)  # type: ignore[arg-type]
        target_file.write_text("import haute\n", encoding="utf-8")
        return artifacts

    monkeypatch.setattr(
        _pipeline_recovery,
        "_recovery_artifacts",
        partial(edit_after_discovery, pipeline_file),
    )
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.source_text == original_text
    expected = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=[
            ("parent_source", pipeline_file),
            ("parent_sidecar", pipeline_file.with_suffix(".haute.json")),
        ],
        known_bytes={pipeline_file: original_bytes},
    )
    assert document.source_revision == expected

    changed_on_disk = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=[
            ("parent_source", pipeline_file),
            ("parent_sidecar", pipeline_file.with_suffix(".haute.json")),
        ],
    )
    assert document.source_revision != changed_on_disk


def test_ready_document_revision_authenticates_strictly_parsed_child_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready load's strict child parse and revision must share one read."""
    from haute import _pipeline_recovery
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_revision import pipeline_recovery_revision

    child = tmp_path / "modules" / "child.py"
    child.parent.mkdir()
    _write(
        child,
        """
        import haute
        submodel = haute.Submodel(
            "child",
            definition_id="child-definition",
            input_ports=[],
            output_ports=[],
        )

        @submodel.polars
        def transform():
            return None
        """,
    )
    parent = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("ready-child-byte-parity")

        @pipeline.polars
        def source():
            return None

        pipeline.submodel(
            "modules/child.py",
            definition_id="child-definition",
            instance_id="child__one",
            alias="child_one",
        )
        """,
    )
    parent_bytes = parent.read_bytes()
    child_bytes = child.read_bytes()
    real_artifacts = _pipeline_recovery._recovery_artifacts
    saved_artifacts: list[tuple[str, Path]] = []

    def edit_child_before_discovery(*args: object, **kwargs: object):
        # Rewrite BEFORE delegating: the walk and revision must reuse the
        # construction-time capture, never the changed bytes now on disk.
        child.write_text("import haute" + chr(10), encoding="utf-8")
        artifacts = real_artifacts(*args, **kwargs)  # type: ignore[arg-type]
        saved_artifacts.extend(artifacts)
        return artifacts

    monkeypatch.setattr(_pipeline_recovery, "_recovery_artifacts", edit_child_before_discovery)
    document = load_pipeline_editor_document(parent, project_root=tmp_path)

    assert document.load_status == "ready"
    assert document.submodels is not None
    child_nodes = {node.authored_id for node in document.submodels["child-definition"].graph.nodes}
    assert "transform" in child_nodes

    expected = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=saved_artifacts,
        known_bytes={parent: parent_bytes, child: child_bytes},
    )
    assert document.source_revision == expected

    changed_on_disk = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=saved_artifacts,
        known_bytes={parent: parent_bytes},
    )
    assert document.source_revision != changed_on_disk


def test_recovery_revision_authenticates_child_bytes_the_document_presents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent child edit must not pair old submodel content with a new revision."""
    from haute import _pipeline_recovery
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_revision import pipeline_recovery_revision

    child = tmp_path / "modules" / "child.py"
    child.parent.mkdir()
    _write(
        child,
        """
        import haute
        submodel = haute.Submodel(
            "child",
            definition_id="child-definition",
            input_ports=[],
            output_ports=[],
        )

        @submodel.polars
        def transform():
            return None
        """,
    )
    parent = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("child-byte-parity")

        @pipeline.removed_node
        def unavailable():
            return None

        pipeline.submodel(
            "modules/child.py",
            definition_id="child-definition",
            instance_id="child__one",
            alias="child_one",
        )
        """,
    )
    parent_bytes = parent.read_bytes()
    child_bytes = child.read_bytes()
    real_artifacts = _pipeline_recovery._recovery_artifacts
    saved_artifacts: list[tuple[str, Path]] = []

    def edit_child_before_discovery(*args: object, **kwargs: object):
        # Rewrite BEFORE delegating: the walk and revision must reuse the
        # construction-time capture, never the changed bytes now on disk.
        child.write_text("import haute" + chr(10), encoding="utf-8")
        artifacts = real_artifacts(*args, **kwargs)  # type: ignore[arg-type]
        saved_artifacts.extend(artifacts)
        return artifacts

    monkeypatch.setattr(_pipeline_recovery, "_recovery_artifacts", edit_child_before_discovery)
    document = load_pipeline_editor_document(parent, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.submodels is not None
    child_nodes = {node.authored_id for node in document.submodels["child-definition"].graph.nodes}
    assert "transform" in child_nodes

    expected = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=saved_artifacts,
        known_bytes={parent: parent_bytes, child: child_bytes},
    )
    assert document.source_revision == expected

    changed_on_disk = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=saved_artifacts,
        known_bytes={parent: parent_bytes},
    )
    assert document.source_revision != changed_on_disk


def test_submodel_failure_codes_classify_by_exception_type_not_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a genuine child SyntaxError earns ``submodel_syntax_invalid``."""
    from haute import _pipeline_recovery
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.errors import ParseError

    child = tmp_path / "modules" / "child.py"
    child.parent.mkdir()
    _write(
        child,
        """
        import haute
        def broken(:
        """,
    )
    parent = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("submodel-codes")

        pipeline.submodel(
            "modules/child.py",
            definition_id="child-definition",
            instance_id="child__one",
            alias="child_one",
        )
        """,
    )

    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    codes = {diagnostic.code for diagnostic in document.diagnostics}
    assert "submodel_syntax_invalid" in codes
    assert "submodel_definition_invalid" not in codes

    def misleading_definition_error(*_args: object, **_kwargs: object) -> object:
        raise ParseError("This definition mentions syntax but is not a SyntaxError.")

    monkeypatch.setattr(
        _pipeline_recovery,
        "parse_submodel_source",
        misleading_definition_error,
    )
    document = load_pipeline_editor_document(parent, project_root=tmp_path)
    codes = {diagnostic.code for diagnostic in document.diagnostics}
    assert "submodel_definition_invalid" in codes
    assert "submodel_syntax_invalid" not in codes


def test_recovery_revision_tracks_child_config_from_parent_config_base(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    config_ref = write_data_input_config(
        tmp_path,
        "child_source",
        "data/input.parquet",
    )
    (tmp_path / "modules").mkdir()
    _write(
        tmp_path / "modules" / "child.py",
        f'''
        import haute
        submodel = haute.Submodel(
            "child",
            definition_id="child",
            input_ports=[],
            output_ports=[],
        )

        @submodel.data_input(config="{config_ref}")
        def child_source():
            return None
        ''',
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("child-config-revision")

        pipeline.submodel(
            "modules/child.py",
            definition_id="child",
            instance_id="child__one",
            alias="child_one",
        )
        """,
    )

    first = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    config_path = tmp_path / config_ref
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    second = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert first.load_status == "ready"
    assert second.load_status == "ready"
    assert first.source_revision != second.source_revision


@pytest.mark.parametrize(
    ("endpoint", "body"),
    [
        (
            "/api/pipeline/save",
            {
                "name": "replacement",
                "source_file": "main.py",
                "graph": {"nodes": [], "edges": []},
            },
        ),
        (
            "/api/submodel/create",
            {
                "name": "group",
                "node_ids": ["a", "b"],
                "source_file": "main.py",
                "base_revision": "posted-ready-revision",
                "graph": {"nodes": [], "edges": []},
            },
        ),
        (
            "/api/submodel/dissolve",
            {
                "instance_id": "submodel__group",
                "source_file": "main.py",
                "base_revision": "posted-ready-revision",
                "graph": {"nodes": [], "edges": []},
            },
        ),
    ],
)
def test_persisted_mutations_reject_degraded_disk_without_writes(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    body: dict[str, object],
) -> None:
    _write(tmp_path / "main.py", _legacy_explore_source())
    monkeypatch.chdir(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    response = client.post(endpoint, json=body)

    assert response.status_code == 409
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_editor_routes_keep_first_broken_pipeline_active(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _write(tmp_path / "a.py", _legacy_explore_source("broken-first"))
    second = _write(tmp_path / "b.py", _healthy_source("healthy-second"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "haute.routes.pipeline.discover_pipelines",
        lambda: [first, second],
    )
    monkeypatch.setattr(
        "haute.routes.pipeline.lookup_pipeline_by_name",
        lambda name: second if name == "healthy-second" else None,
    )

    first_response = client.get("/api/pipeline")
    named_response = client.get("/api/pipeline/healthy-second")

    assert first_response.status_code == 200
    assert first_response.json()["pipeline_name"] == "broken-first"
    assert first_response.json()["load_status"] == "degraded"
    assert named_response.status_code == 200
    assert named_response.json()["pipeline_name"] == "healthy-second"
    assert named_response.json()["load_status"] == "ready"


def test_unknown_decorator_is_rejected_strictly_and_preserved_for_recovery(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("unknown")

        @pipeline.polars
        def healthy():
            return None

        @pipeline.retired_explore(mode="legacy")
        def old_node(healthy):
            return healthy
        """,
    )

    with pytest.raises(ParseError, match="unknown.*retired_explore"):
        parse_pipeline_file(pipeline_file)

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert [
        (node.authored_id, node.decorator_name, node.availability) for node in document.nodes
    ] == [
        ("healthy", "polars", "ready"),
        ("old_node", "retired_explore", "unavailable"),
    ]
    assert any(diagnostic.code == "node_decorator_unknown" for diagnostic in document.diagnostics)


def test_duplicate_nodes_and_connections_are_conserved_with_stable_diagnostics(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("duplicates")

        @pipeline.polars
        def source():
            return "first"

        @pipeline.polars
        def source():
            return "second"

        @pipeline.polars
        def target(source):
            return source

        pipeline.connect("source", "target")
        pipeline.connect("source", "target")
        pipeline.connect("target", "missing", source_port="out")
        """,
    )

    first = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    second = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert first.load_status == "degraded"
    assert [node.recovery_id for node in first.nodes] == ["source@L5", "source@L9", "target"]
    assert all(node.availability == "unavailable" for node in first.nodes[:2])
    assert first.edges == []
    assert len(first.unresolved_connections) == 4
    assert {diagnostic.code for diagnostic in first.diagnostics} >= {
        "node_identity_duplicate",
        "connection_endpoint_ambiguous",
        "connection_endpoint_missing",
    }
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_explore_shape_failure_is_local_and_blocks_only_downstream(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("shape")

        @pipeline.polars
        def source():
            return None

        @pipeline.explore(pivots=[])
        def inspect(source):
            return source

        @pipeline.polars
        def downstream(inspect):
            return inspect
        """,
    )

    with pytest.raises(ParseError, match="Explore nodes cannot have outgoing"):
        parse_pipeline_file(pipeline_file)

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    by_id = {node.authored_id: node for node in document.nodes}

    assert by_id["source"].availability == "ready"
    assert by_id["inspect"].availability == "unavailable"
    assert by_id["downstream"].availability == "blocked"
    assert by_id["downstream"].blocking_path == ["inspect", "downstream"]
    assert any(diagnostic.code == "node_topology_invalid" for diagnostic in document.diagnostics)


def test_missing_submodel_preserves_occurrence_and_unrelated_root_nodes(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("submodel-recovery")

        @pipeline.polars
        def healthy():
            return None

        pipeline.submodel(
            "models/missing.py",
            definition_id="pricing",
            instance_id="pricing__one",
            alias="pricing_one",
        )
        pipeline.connect("healthy", "pricing_one", target_port="input")
        """,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    by_id = {node.authored_id: node for node in document.nodes}

    assert document.load_status == "degraded"
    assert by_id["healthy"].availability == "ready"
    assert by_id["pricing__one"].availability == "unavailable"
    assert document.submodels is not None
    assert document.submodels["pricing"].availability == "unavailable"
    assert any(diagnostic.code == "submodel_file_missing" for diagnostic in document.diagnostics)
    assert document.edges == []
    assert len(document.unresolved_connections) == 1
    assert any(
        diagnostic.code == "submodel_input_port_invalid" for diagnostic in document.diagnostics
    )


def test_unknown_submodel_decorator_is_rejected_strictly_and_conserved(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    (tmp_path / "modules").mkdir()
    _write(
        tmp_path / "modules" / "child.py",
        """
        import haute
        submodel = haute.Submodel(
            "child",
            definition_id="child",
            input_ports=[],
            output_ports=[],
        )

        @submodel.removed_node
        def old_child():
            return None
        """,
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("unknown-child-node")

        pipeline.submodel(
            "modules/child.py",
            definition_id="child",
            instance_id="child__one",
            alias="child_one",
        )
        """,
    )

    with pytest.raises(ParseError, match="unknown @submodel node decorator"):
        parse_pipeline_file(pipeline_file)

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.submodels is not None
    definition = document.submodels["child"]
    assert definition.availability == "unavailable"
    assert [(node.authored_id, node.availability) for node in definition.graph.nodes] == [
        ("old_child", "unavailable")
    ]
    assert any(diagnostic.code == "node_decorator_unknown" for diagnostic in document.diagnostics)


def test_duplicate_submodel_definition_paths_mark_every_occurrence_unavailable(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    child_source = """
        import haute
        submodel = haute.Submodel(
            "shared",
            definition_id="shared",
            input_ports=[],
            output_ports=[],
        )
    """
    (tmp_path / "models").mkdir()
    _write(tmp_path / "models" / "one.py", child_source)
    _write(tmp_path / "models" / "two.py", child_source)
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("duplicate-definition")

        pipeline.submodel(
            "models/one.py",
            definition_id="shared",
            instance_id="shared__one",
            alias="shared_one",
        )
        pipeline.submodel(
            "models/two.py",
            definition_id="shared",
            instance_id="shared__two",
            alias="shared_two",
        )
        """,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    by_authored_id = {node.authored_id: node for node in document.nodes}
    assert by_authored_id["shared__one"].availability == "unavailable"
    assert by_authored_id["shared__two"].availability == "unavailable"
    assert document.submodels is not None
    assert document.submodels["shared"].availability == "unavailable"
    duplicate_diagnostics = [
        diagnostic
        for diagnostic in document.diagnostics
        if diagnostic.code == "submodel_definition_duplicate"
    ]
    assert len(duplicate_diagnostics) == 1


def test_duplicate_submodel_alias_stays_degraded_while_revision_is_computed(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    (tmp_path / "models").mkdir()
    _write(
        tmp_path / "models" / "shared.py",
        """
        import haute
        submodel = haute.Submodel(
            "shared",
            definition_id="shared",
            input_ports=[],
            output_ports=[],
        )
        """,
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("duplicate-alias")

        pipeline.submodel(
            "models/shared.py",
            definition_id="shared",
            instance_id="shared__one",
            alias="same_alias",
        )
        pipeline.submodel(
            "models/shared.py",
            definition_id="shared",
            instance_id="shared__two",
            alias="same_alias",
        )
        """,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.source_revision is not None
    assert {node.availability for node in document.nodes} == {"unavailable"}
    assert (
        sum(diagnostic.code == "submodel_alias_duplicate" for diagnostic in document.diagnostics)
        == 2
    )


def test_recovery_diagnostics_are_deterministically_bounded(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    functions = "\n".join(
        f"@pipeline.removed_{index}\ndef node_{index}():\n    return None\n" for index in range(230)
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        f'import haute\npipeline = haute.Pipeline("bounded")\n\n{functions}',
    )

    first = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    second = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert len(first.diagnostics) == 200
    assert first.diagnostics_omitted == 30
    assert [item.diagnostic_id for item in first.diagnostics] == [
        item.diagnostic_id for item in second.diagnostics
    ]


def test_recovery_preview_plans_only_the_ready_ancestor_closure(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.schemas import PreviewNodeResponse

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("preview-ready-branch")

        @pipeline.polars
        def source():
            return None

        @pipeline.polars
        def clean(source):
            return source

        @pipeline.explore(pivots=[{"version": 1}])
        def broken(source):
            return source
        """,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.pipeline._get_project_root", lambda: tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    captured: dict[str, object] = {}

    async def execute(body: object) -> PreviewNodeResponse:
        captured["body"] = body
        return PreviewNodeResponse(node_id="clean", status="ok")

    monkeypatch.setattr("haute.routes.pipeline._preview_canonical_graph", execute)

    response = client.post(
        "/api/pipeline/recovery-preview",
        json={
            "source_file": "main.py",
            "source_revision": document.source_revision,
            "target_recovery_id": "clean",
            "source": "live",
        },
    )

    assert response.status_code == 200, response.text
    planned = captured["body"]
    assert [node.id for node in planned.graph.nodes] == ["source", "clean"]  # type: ignore[attr-defined]
    assert planned.node_id == "clean"  # type: ignore[attr-defined]


def test_recovery_preview_closure_shares_canonical_cache_identity(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The planned ready closure keys caches exactly like its strict twin.

    Preview cache and supersession identity derive from ``graph_fingerprint``
    of the request graph. The server-planned recovery closure must therefore
    fingerprint identically to the canonical graph a ready document would
    produce once the broken node is removed, so neither path spawns a second
    cache family for equivalent work.
    """
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.graph_utils import graph_fingerprint
    from haute.parser import parse_pipeline_file
    from haute.schemas import PreviewNodeResponse

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("closure-cache-parity")

        @pipeline.polars
        def source():
            return None

        @pipeline.polars
        def clean(source):
            return source

        @pipeline.explore(pivots=[{"version": 1}])
        def broken(source):
            return source
        """,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.pipeline._get_project_root", lambda: tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    captured: dict[str, object] = {}

    async def execute(body: object) -> PreviewNodeResponse:
        captured["body"] = body
        return PreviewNodeResponse(node_id="clean", status="ok")

    monkeypatch.setattr("haute.routes.pipeline._preview_canonical_graph", execute)

    response = client.post(
        "/api/pipeline/recovery-preview",
        json={
            "source_file": "main.py",
            "source_revision": document.source_revision,
            "target_recovery_id": "clean",
            "source": "live",
        },
    )
    assert response.status_code == 200, response.text
    planned_graph = captured["body"].graph  # type: ignore[attr-defined]

    _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("closure-cache-parity")

        @pipeline.polars
        def source():
            return None

        @pipeline.polars
        def clean(source):
            return source
        """,
    )
    # A ready client previews with the project-relative wire path, so align
    # the strict twin to the same canonical source identity before comparing.
    repaired_graph = parse_pipeline_file(pipeline_file).model_copy(
        update={"source_file": "main.py"}
    )

    assert graph_fingerprint(planned_graph) == graph_fingerprint(repaired_graph)


def test_recovery_preview_rejects_blocked_target_before_execution(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        _legacy_explore_source("preview-blocked")
        + """

        @pipeline.polars
        def after_explore(explore):
            return explore
        """,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.pipeline._get_project_root", lambda: tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    response = client.post(
        "/api/pipeline/recovery-preview",
        json={
            "source_file": "main.py",
            "source_revision": document.source_revision,
            "target_recovery_id": "after_explore",
            "source": "live",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "node_blocked_by_load_error"
    assert response.json()["detail"]["blocking_path"] == ["explore", "after_explore"]


def test_recovery_preview_rejects_stale_revision_and_untrusted_sidecar(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _healthy_source("preview-fences"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.pipeline._get_project_root", lambda: tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    _write(
        pipeline_file,
        pipeline_file.read_text(encoding="utf-8") + "\n# external edit\n",
    )

    stale = client.post(
        "/api/pipeline/recovery-preview",
        json={
            "source_file": "main.py",
            "source_revision": document.source_revision,
            "target_recovery_id": "source",
            "source": "live",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_document_revision"

    current = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    (tmp_path / "main.haute.json").write_bytes(b'{"positions":')
    untrusted = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    response = client.post(
        "/api/pipeline/recovery-preview",
        json={
            "source_file": "main.py",
            "source_revision": untrusted.source_revision,
            "target_recovery_id": "source",
            "source": "live",
        },
    )
    assert current.source_revision != untrusted.source_revision
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_selection_untrusted"


def test_unexpected_recovery_defect_returns_safe_source_only_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _healthy_source("incident"))
    expected_source = pipeline_file.read_text(encoding="utf-8")

    def fail_recovery(_path: Path) -> object:
        raise RuntimeError("private parser implementation detail")

    monkeypatch.setattr(
        "haute._pipeline_recovery.read_sidecar_state",
        fail_recovery,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "source_only"
    assert document.source_text == expected_source
    assert document.source_revision is not None
    assert document.nodes == []
    assert document.capabilities.can_save is False
    assert document.capabilities.can_execute is False
    assert document.capabilities.can_preview is False
    assert document.source_selection_trusted is False
    assert [diagnostic.code for diagnostic in document.diagnostics] == [
        "pipeline_recovery_internal_error"
    ]
    diagnostic = document.diagnostics[0]
    assert diagnostic.incident_id
    assert "private parser implementation detail" not in diagnostic.message


def test_unexpected_strict_parser_defect_is_not_laundered_as_authored_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _healthy_source("strict-incident"))

    def fail_strict_parse(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private strict parser implementation detail")

    monkeypatch.setattr(
        "haute._pipeline_recovery.parse_pipeline_source",
        fail_strict_parse,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "source_only"
    assert document.source_text == pipeline_file.read_text(encoding="utf-8")
    assert [diagnostic.code for diagnostic in document.diagnostics] == [
        "pipeline_recovery_internal_error"
    ]
    diagnostic = document.diagnostics[0]
    assert diagnostic.incident_id
    assert "private strict parser implementation detail" not in diagnostic.message


def test_unexpected_syntax_fragment_recovery_defect_has_visible_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import (
        load_pipeline_editor_document,
        recover_pipeline_fragments,
    )

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("fragment-incident")

        @pipeline.polars
        def broken(:
            return None
        """,
    )

    calls = 0

    def fail_fragment_recovery_once(source: str) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private fragment recovery implementation detail")
        return recover_pipeline_fragments(source)

    monkeypatch.setattr(
        "haute._pipeline_recovery.recover_pipeline_fragments",
        fail_fragment_recovery_once,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "source_only"
    assert [diagnostic.code for diagnostic in document.diagnostics] == [
        "python_syntax_error",
        "pipeline_recovery_internal_error",
    ]
    diagnostic = document.diagnostics[1]
    assert diagnostic.incident_id
    assert "private fragment recovery implementation detail" not in diagnostic.message


def test_unexpected_submodel_parser_defect_is_localised_with_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    (tmp_path / "models").mkdir()
    _write(
        tmp_path / "models" / "shared.py",
        """
        import haute
        submodel = haute.Submodel(
            "shared",
            definition_id="shared",
            input_ports=[],
            output_ports=[],
        )
        """,
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("submodel-incident")

        @pipeline.removed_node
        def obsolete():
            return None

        pipeline.submodel(
            "models/shared.py",
            definition_id="shared",
            instance_id="shared__one",
            alias="shared_one",
        )
        """,
    )

    def fail_submodel_parse(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private submodel parser implementation detail")

    monkeypatch.setattr(
        "haute._pipeline_recovery.parse_submodel_source",
        fail_submodel_parse,
    )

    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    assert document.load_status == "degraded"
    assert document.submodels is not None
    assert document.submodels["shared"].availability == "unavailable"
    internal = [
        diagnostic
        for diagnostic in document.diagnostics
        if diagnostic.code == "submodel_recovery_internal_error"
    ]
    assert len(internal) == 1
    assert internal[0].incident_id
    assert "private submodel parser implementation detail" not in internal[0].message


def test_remove_unavailable_node_dry_run_is_no_write_and_enumerates_exact_edits(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    _write(
        pipeline_file.with_suffix(".haute.json"),
        '{"positions":{"quote_source":{"x":1,"y":2},'
        '"explore":{"x":30,"y":40},"model_input":{"x":50,"y":60}},'
        '"sources":["live"],"active_source":"live"}\n',
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "explore")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json={
            "source_file": document.source_file,
            "source_revision": document.source_revision,
            "target_source_file": target.source_file,
            "target_recovery_id": target.recovery_id,
            "delete_config": False,
        },
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["repair_kind"] == "remove_unavailable_node"
    assert plan["source_revision"] == document.source_revision
    assert plan["target_recovery_id"] == target.recovery_id
    assert plan["target_authored_id"] == "explore"
    assert plan["delete_config"] is False
    assert len(plan["plan_hash"]) == 64
    assert plan["predicted_load_status"] == "ready"
    assert [change["path"] for change in plan["changes"]] == [
        "main.py",
        "main.haute.json",
    ]
    source_change = plan["changes"][0]
    assert source_change["operation"] == "update"
    assert "@pipeline.explore" in source_change["diff"]
    assert 'pipeline.connect("aggregate", "explore")' in source_change["diff"]
    assert source_change["diff_truncated"] is False
    assert plan["retained_artifacts"] == []
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_remove_unavailable_node_apply_commits_confirmed_plan_and_returns_document(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    sidecar = _write(
        pipeline_file.with_suffix(".haute.json"),
        '{"positions":{"quote_source":{"x":1,"y":2},'
        '"explore":{"x":30,"y":40},"model_input":{"x":50,"y":60}},'
        '"sources":["live"],"active_source":"live"}\n',
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "explore")
    request = {
        "source_file": document.source_file,
        "source_revision": document.source_revision,
        "target_source_file": target.source_file,
        "target_recovery_id": target.recovery_id,
        "delete_config": False,
    }
    plan_response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=request,
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()

    response = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": plan["plan_hash"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["repair_kind"] == "remove_unavailable_node"
    assert payload["plan_hash"] == plan["plan_hash"]
    assert payload["applied_artifacts"] == ["main.py", "main.haute.json"]
    assert payload["document"]["load_status"] == "ready"
    assert payload["document"]["source_revision"] != document.source_revision
    assert [node["authored_id"] for node in payload["document"]["nodes"]] == [
        "quote_source",
        "aggregate",
        "model_input",
    ]

    repaired_source = pipeline_file.read_text(encoding="utf-8")
    assert "@pipeline.explore" not in repaired_source
    assert 'pipeline.connect("aggregate", "explore")' not in repaired_source
    assert "def quote_source():\n    return None" in repaired_source
    assert "def model_input(aggregate):\n    return aggregate" in repaired_source
    assert sidecar.read_text(encoding="utf-8") == (
        '{"positions":{"quote_source":{"x":1,"y":2},'
        '"model_input":{"x":50,"y":60}},'
        '"sources":["live"],"active_source":"live"}\n'
    )
    assert parse_pipeline_file(pipeline_file).pipeline_name == "legacy"


def test_remove_unavailable_node_repairs_a_child_submodel_source(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    modules = tmp_path / "modules"
    modules.mkdir()
    child_file = _write(
        modules / "scoring.py",
        """
        import haute
        submodel = haute.Submodel(
            "scoring",
            definition_id="scoring",
            input_ports=[],
            output_ports=[],
        )

        @submodel.removed_node
        def obsolete():
            return None

        @submodel.polars
        def healthy():
            return None
        """,
    )
    child_sidecar = _write(
        child_file.with_suffix(".haute.json"),
        '{"positions":{"obsolete":{"x":1,"y":2},"healthy":{"x":3,"y":4}}}\n',
    )
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("child-repair")

        pipeline.submodel(
            "modules/scoring.py",
            definition_id="scoring",
            instance_id="scoring__one",
            alias="scoring",
        )
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    assert document.submodels is not None
    target = next(
        node for node in document.submodels["scoring"].graph.nodes if node.authored_id == "obsolete"
    )
    request = {
        "source_file": document.source_file,
        "source_revision": document.source_revision,
        "target_source_file": target.source_file,
        "target_recovery_id": target.recovery_id,
        "delete_config": False,
    }
    parent_before = pipeline_file.read_bytes()

    dry_run = client.post("/api/pipeline/repair/remove/dry-run", json=request)
    assert dry_run.status_code == 200, dry_run.text
    assert [change["path"] for change in dry_run.json()["changes"]] == [
        "modules/scoring.py",
        "modules/scoring.haute.json",
    ]

    applied = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": dry_run.json()["plan_hash"]},
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["document"]["load_status"] == "ready"
    assert pipeline_file.read_bytes() == parent_before
    assert "@submodel.removed_node" not in child_file.read_text(encoding="utf-8")
    assert child_sidecar.read_text(encoding="utf-8") == (
        '{"positions":{"healthy":{"x":3,"y":4}}}\n'
    )


def _remove_repair_request(
    document: PipelineEditorDocument,
    authored_id: str,
) -> dict[str, object]:
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    return {
        "source_file": document.source_file,
        "source_revision": document.source_revision,
        "target_source_file": target.source_file,
        "target_recovery_id": target.recovery_id,
        "delete_config": False,
    }


def test_remove_unavailable_node_rejects_implicit_consumers_without_writing(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("implicit-consumer")

        @pipeline.removed_node
        def obsolete():
            return None

        @pipeline.polars
        def downstream(obsolete):
            return obsolete
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    before = pipeline_file.read_bytes()

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=_remove_repair_request(document, "obsolete"),
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "repair_implicit_consumers"
    assert detail["consumers"] == [
        {"function": "downstream", "parameter": "obsolete"},
    ]
    assert pipeline_file.read_bytes() == before


def test_remove_unavailable_node_apply_rejects_revision_and_plan_drift(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    request = _remove_repair_request(document, "explore")
    plan_response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=request,
    )
    assert plan_response.status_code == 200, plan_response.text
    original = pipeline_file.read_bytes()

    wrong_plan = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": "0" * 64},
    )
    assert wrong_plan.status_code == 409
    assert wrong_plan.json()["detail"]["code"] == "repair_plan_conflict"
    assert pipeline_file.read_bytes() == original

    _write_bytes(pipeline_file, original + b"\n# concurrent external edit\n")
    externally_edited = pipeline_file.read_bytes()
    stale_revision = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": plan_response.json()["plan_hash"]},
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["detail"]["code"] == "repair_revision_conflict"
    assert pipeline_file.read_bytes() == externally_edited


@pytest.mark.parametrize("delete_config", [False, True])
def test_remove_unavailable_node_retains_config_unless_separately_approved(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    delete_config: bool,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    config = tmp_path / "config" / "obsolete.json"
    config.parent.mkdir()
    config.write_text('{"legacy":true}\n', encoding="utf-8")
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("config-retention")

        @pipeline.removed_node(config="config/obsolete.json")
        def obsolete():
            return None

        @pipeline.polars
        def healthy():
            return None
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    request = {
        **_remove_repair_request(document, "obsolete"),
        "delete_config": delete_config,
    }

    dry_run = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=request,
    )
    assert dry_run.status_code == 200, dry_run.text
    plan = dry_run.json()
    if delete_config:
        assert plan["retained_artifacts"] == []
        assert plan["changes"][-1]["path"] == "config/obsolete.json"
        assert plan["changes"][-1]["operation"] == "delete"
        assert plan["changes"][-1]["diff"] == ""
    else:
        assert plan["retained_artifacts"] == ["config/obsolete.json"]
        assert "will be retained" in plan["warnings"][0]

    applied = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": plan["plan_hash"]},
    )
    assert applied.status_code == 200, applied.text
    assert config.exists() is (not delete_config)
    assert applied.json()["document"]["load_status"] == "ready"


def test_remove_unavailable_node_rejects_deleting_a_shared_config(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    config = tmp_path / "config" / "shared.json"
    config.parent.mkdir()
    config.write_text("{}\n", encoding="utf-8")
    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("shared-config")

        @pipeline.removed_a(config="config/shared.json")
        def obsolete():
            return None

        @pipeline.removed_b(config="./config/shared.json")
        def other():
            return None
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json={
            **_remove_repair_request(document, "obsolete"),
            "delete_config": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repair_config_shared"
    assert config.is_file()


def test_remove_unavailable_node_rejects_config_deletion_of_a_document_artifact(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("managed-artifact-config")

        @pipeline.removed_node(config="main.haute.json")
        def obsolete():
            return None
        """,
    )
    sidecar = _write(
        pipeline_file.with_suffix(".haute.json"),
        '{"positions":{"obsolete":{"x":1,"y":2}}}\n',
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    before = {
        pipeline_file: pipeline_file.read_bytes(),
        sidecar: sidecar.read_bytes(),
    }

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json={
            **_remove_repair_request(document, "obsolete"),
            "delete_config": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repair_config_not_deletable"
    assert {path: path.read_bytes() for path in before} == before


def test_remove_unavailable_node_rejects_duplicate_identity_and_mixed_chain(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    duplicate_file = _write(
        tmp_path / "duplicate.py",
        """
        import haute
        pipeline = haute.Pipeline("duplicate")

        @pipeline.removed_node
        def obsolete():
            return None

        @pipeline.removed_node
        def obsolete():
            return None
        """,
    )
    monkeypatch.chdir(tmp_path)
    duplicate_document = load_pipeline_editor_document(duplicate_file, project_root=tmp_path)
    duplicate_target = duplicate_document.nodes[0]
    duplicate = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json={
            "source_file": duplicate_document.source_file,
            "source_revision": duplicate_document.source_revision,
            "target_source_file": duplicate_target.source_file,
            "target_recovery_id": duplicate_target.recovery_id,
            "delete_config": False,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == ("repair_target_authored_identity_ambiguous")

    mixed_file = _write(
        tmp_path / "mixed.py",
        """
        import haute
        pipeline = haute.Pipeline("mixed-chain")

        @pipeline.polars
        def source():
            return None

        @pipeline.removed_node
        def obsolete():
            return None

        @pipeline.polars
        def healthy():
            return None

        pipeline.connect("source", "obsolete").connect("source", "healthy")
        """,
    )
    mixed_document = load_pipeline_editor_document(mixed_file, project_root=tmp_path)
    mixed = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=_remove_repair_request(mixed_document, "obsolete"),
    )
    assert mixed.status_code == 409
    assert mixed.json()["detail"]["code"] == "repair_connection_chain_mixed"


def test_remove_unavailable_node_rejects_connection_sharing_a_source_line(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("shared-line")

        @pipeline.polars
        def source():
            return None

        @pipeline.removed_node
        def obsolete():
            return None

        pipeline.connect("source", "obsolete"); preserved = "keep me"
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    before = pipeline_file.read_bytes()

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=_remove_repair_request(document, "obsolete"),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repair_connection_span_ambiguous"
    assert pipeline_file.read_bytes() == before


def test_remove_unavailable_node_does_not_delete_a_trailing_connection_comment(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("connection-comment")

        @pipeline.polars
        def source():
            return None

        @pipeline.removed_node
        def obsolete():
            return None

        pipeline.connect("source", "obsolete")  # preserve this authored note
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    before = pipeline_file.read_bytes()

    response = client.post(
        "/api/pipeline/repair/remove/dry-run",
        json=_remove_repair_request(document, "obsolete"),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repair_connection_span_ambiguous"
    assert pipeline_file.read_bytes() == before


def test_remove_unavailable_node_can_leave_independent_degraded_error(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document

    pipeline_file = _write(
        tmp_path / "main.py",
        """
        import haute
        pipeline = haute.Pipeline("two-errors")

        @pipeline.removed_a
        def obsolete():
            return None

        @pipeline.removed_b
        def other():
            return None
        """,
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    request = _remove_repair_request(document, "obsolete")
    dry_run = client.post("/api/pipeline/repair/remove/dry-run", json=request)
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["predicted_load_status"] == "degraded"

    applied = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": dry_run.json()["plan_hash"]},
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["document"]["load_status"] == "degraded"
    assert [node["authored_id"] for node in applied.json()["document"]["nodes"]] == [
        "other",
    ]


def test_remove_unavailable_node_rolls_back_every_staged_artifact_on_write_failure(
    tmp_path: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.routes import _save_pipeline

    pipeline_file = _write(tmp_path / "main.py", _legacy_explore_source())
    sidecar = _write(
        pipeline_file.with_suffix(".haute.json"),
        '{"positions":{"explore":{"x":30,"y":40}}}\n',
    )
    monkeypatch.chdir(tmp_path)
    document = load_pipeline_editor_document(pipeline_file, project_root=tmp_path)
    request = _remove_repair_request(document, "explore")
    dry_run = client.post("/api/pipeline/repair/remove/dry-run", json=request)
    assert dry_run.status_code == 200, dry_run.text
    before = {pipeline_file: pipeline_file.read_bytes(), sidecar: sidecar.read_bytes()}
    real_stage = _save_pipeline._stage_artifact_write_bytes

    def fail_sidecar_write(path: Path, payload: bytes, touched: list[object]) -> None:
        if path == sidecar:
            raise PermissionError("sidecar locked")
        real_stage(path, payload, touched)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "haute.routes._save_pipeline._stage_artifact_write_bytes",
        fail_sidecar_write,
    )

    response = client.post(
        "/api/pipeline/repair/remove/apply",
        json={**request, "plan_hash": dry_run.json()["plan_hash"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "repair_artifact_unavailable"
    assert pipeline_file.read_bytes() == before[pipeline_file]
    assert sidecar.read_bytes() == before[sidecar]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            b'{"positions":{"explore":{"x":1,"y":2}},"other":1}\n',
            b'{"positions":{},"other":1}\n',
        ),
        (
            b'{"positions":{"explore":{},"other":{}},"other":1}\n',
            b'{"positions":{"other":{}},"other":1}\n',
        ),
        (
            b'{"positions":{"other":{},"explore":{}},"other":1}\n',
            b'{"positions":{"other":{}},"other":1}\n',
        ),
    ],
)
def test_remove_position_entry_preserves_unrelated_sidecar_bytes(
    before: bytes,
    after: bytes,
) -> None:
    from haute._pipeline_repair import _remove_position_entry

    assert _remove_position_entry(before, "explore") == after


@pytest.mark.parametrize(
    "sidecar",
    [
        b'{"positions":{"explore":{}},"positions":{"other":{}}}\n',
        b'{"positions":{"explore":{},"explore":{"x":1,"y":2}}}\n',
    ],
)
def test_remove_position_entry_rejects_duplicate_json_identity(sidecar: bytes) -> None:
    from haute._pipeline_repair import PipelineRepairError, _remove_position_entry

    with pytest.raises(PipelineRepairError) as raised:
        _remove_position_entry(sidecar, "explore")

    assert raised.value.code == "repair_sidecar_ambiguous"
