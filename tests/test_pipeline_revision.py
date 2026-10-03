"""Document-revision coverage for parent pipelines and referenced children."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import make_graph


def _document(tmp_path: Path):
    parent = tmp_path / "main.py"
    parent.write_text("# parent source\n", encoding="utf-8")
    parent_sidecar = tmp_path / "main.haute.json"
    parent_sidecar.write_text('{"positions":{"root":{"x":1,"y":2}}}\n', encoding="utf-8")

    modules = tmp_path / "modules"
    modules.mkdir()
    child = modules / "child.py"
    child.write_text("# child source\n", encoding="utf-8")
    child_sidecar = modules / "child.haute.json"
    child_sidecar.write_text('{"positions":{"child":{"x":3,"y":4}}}\n', encoding="utf-8")

    graph = make_graph(
        {
            "pipeline_name": "main",
            "source_file": str(parent),
            "nodes": [
                {
                    "id": "root",
                    "data": {
                        "label": "root",
                        "nodeType": "polars",
                        "config": {"code": "return df"},
                    },
                },
                {
                    "id": "child-instance",
                    "type": "submodel",
                    "data": {
                        "label": "child",
                        "nodeType": "submodel",
                        "config": {"definitionId": "child", "alias": "child"},
                    },
                },
            ],
            "edges": [],
            "submodels": {
                "child": {
                    "definitionId": "child",
                    "file": "modules/child.py",
                    "graph": {
                        "nodes": [
                            {
                                "id": "child",
                                "data": {
                                    "label": "child",
                                    "nodeType": "polars",
                                    "config": {"code": "return df"},
                                },
                            }
                        ],
                        "edges": [],
                        "pipeline_name": "child",
                        "source_file": str(child),
                    },
                    "inputPorts": [],
                    "outputPorts": [],
                }
            },
        }
    )
    files = {
        "parent_source": parent,
        "parent_sidecar": parent_sidecar,
        "child_source": child,
        "child_sidecar": child_sidecar,
    }
    return graph, parent, files


def _revision(graph, parent: Path, root: Path) -> str:
    from haute._pipeline_revision import pipeline_document_revision

    return pipeline_document_revision(graph, pipeline_path=parent, project_root=root)


def test_revision_is_deterministic_and_excludes_itself(tmp_path: Path) -> None:
    graph, parent, _files = _document(tmp_path)
    first = _revision(graph.model_copy(update={"source_revision": "old"}), parent, tmp_path)
    second = _revision(graph.model_copy(update={"source_revision": "new"}), parent, tmp_path)

    assert first == second
    assert first == _revision(graph, parent, tmp_path)


@pytest.mark.parametrize(
    "relative_path",
    ["main.py", "main.haute.json", "modules/child.py", "modules/child.haute.json"],
)
def test_revision_changes_with_every_owned_document_file(
    haute_scratch: Path,
    relative_path: str,
) -> None:
    graph, parent, _files = _document(haute_scratch)
    before = _revision(graph, parent, haute_scratch)
    target = haute_scratch / relative_path
    (haute_scratch / relative_path).write_bytes(target.read_bytes() + b"# changed\n")

    assert _revision(graph, parent, haute_scratch) != before


def test_revision_changes_with_canonical_graph_config(tmp_path: Path) -> None:
    graph, parent, _files = _document(tmp_path)
    before = _revision(graph, parent, tmp_path)
    node = graph.nodes[0]
    changed_data = node.data.model_copy(update={"config": {"code": "return df.select('x')"}})
    changed = graph.model_copy(update={"nodes": [node.model_copy(update={"data": changed_data})]})

    assert _revision(changed, parent, tmp_path) != before


def test_recovery_revision_is_order_independent_and_deduplicates_aliases(
    tmp_path: Path,
) -> None:
    from haute._pipeline_revision import pipeline_recovery_revision

    source = tmp_path / "main.py"
    source.write_bytes(b"source")
    sidecar = tmp_path / "main.haute.json"
    sidecar.write_bytes(b'{"positions":')
    first = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=[
            ("parent_source", source),
            ("parent_sidecar", sidecar),
            ("parent_source", tmp_path / "." / "main.py"),
        ],
    )
    second = pipeline_recovery_revision(
        project_root=tmp_path,
        artifacts=[
            ("parent_sidecar", sidecar),
            ("parent_source", source),
        ],
    )

    assert first == second


def test_recovery_revision_tracks_raw_bytes_and_missing_transitions(tmp_path: Path) -> None:
    from haute._pipeline_revision import pipeline_recovery_revision

    source = tmp_path / "main.py"
    source.write_bytes(b"source")
    config = tmp_path / "config.json"
    artifacts = [("parent_source", source), ("node_config", config)]
    missing = pipeline_recovery_revision(project_root=tmp_path, artifacts=artifacts)
    config.write_bytes(b'{"invalid":')
    malformed = pipeline_recovery_revision(project_root=tmp_path, artifacts=artifacts)
    config.write_bytes(b'{"still_invalid":')
    changed = pipeline_recovery_revision(project_root=tmp_path, artifacts=artifacts)
    config.unlink()
    missing_again = pipeline_recovery_revision(project_root=tmp_path, artifacts=artifacts)

    assert len({missing, malformed, changed}) == 3
    assert missing_again == missing


def test_recovery_revision_rejects_artifacts_outside_project(tmp_path: Path) -> None:
    from haute._pipeline_revision import pipeline_recovery_revision

    outside = tmp_path.parent / "outside.py"
    with pytest.raises(ValueError, match="escapes the project root"):
        pipeline_recovery_revision(
            project_root=tmp_path,
            artifacts=[("parent_source", outside)],
        )


_CONSTANTS_PIPELINE = (
    '"""Pipeline: main"""\n\n'
    "import haute\n"
    "import polars as pl\n\n"
    'pipeline = haute.Pipeline("main", global_constants="config/global_constants.json")\n'
    "global_constants = pipeline.global_constants\n\n\n"
    "@pipeline.polars\n"
    "def quotes() -> pl.LazyFrame:\n"
    '    df = pl.LazyFrame({"rate": [global_constants.rate]})\n'
    "    return df\n"
)


def _constants_document(tmp_path: Path, rate: float) -> Path:
    main = tmp_path / "main.py"
    main.write_text(_CONSTANTS_PIPELINE, encoding="utf-8")
    constants = tmp_path / "config" / "global_constants.json"
    constants.parent.mkdir(parents=True, exist_ok=True)
    constants.write_text(
        json.dumps({"constants": [{"name": "rate", "type": "float", "value": rate}]}) + "\n",
        encoding="utf-8",
    )
    return main


def _save_document(tmp_path: Path, document) -> None:
    from haute._types import PipelineGraph
    from haute.parser import parse_pipeline_file
    from haute.routes._save_pipeline import SavePipelineService
    from haute.schemas import SavePipelineRequest

    graph = parse_pipeline_file(tmp_path / "main.py")
    graph = PipelineGraph.model_validate(
        {
            **graph.model_dump(),
            "global_constants": [constant.model_dump() for constant in document.global_constants],
        }
    )
    SavePipelineService(project_root=tmp_path).save(
        SavePipelineRequest(
            graph=graph,
            name="main",
            source_file="main.py",
            base_revision=document.source_revision,
        )
    )


def test_an_edit_of_the_constants_file_after_load_makes_the_document_stale(
    tmp_path: Path,
) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.routes._save_pipeline import StaleDocumentRevisionError

    main = _constants_document(tmp_path, 1.05)
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    assert [constant.value for constant in document.global_constants] == [1.05]
    constants = tmp_path / "config" / "global_constants.json"
    constants.write_text(
        '{"constants": [{"name": "rate", "type": "float", "value": 2.5}]}\n',
        encoding="utf-8",
    )
    newer = constants.read_bytes()

    with pytest.raises(StaleDocumentRevisionError):
        _save_document(tmp_path, document)

    assert constants.read_bytes() == newer


def test_a_write_between_the_loads_read_and_its_revision_leaves_the_document_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constants a document carries are the bytes its revision authenticates."""
    from haute import _pipeline_recovery
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute.routes._save_pipeline import StaleDocumentRevisionError

    main = _constants_document(tmp_path, 1.05)
    constants = tmp_path / "config" / "global_constants.json"
    newer = b'{"constants": [{"name": "rate", "type": "float", "value": 2.5}]}\n'
    original_revision = _pipeline_recovery.pipeline_recovery_revision

    def replace_then_hash(**kwargs):
        # Another writer lands after the load read the file, before it hashes.
        constants.write_bytes(newer)
        return original_revision(**kwargs)

    monkeypatch.setattr(_pipeline_recovery, "pipeline_recovery_revision", replace_then_hash)
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    monkeypatch.undo()

    assert [constant.value for constant in document.global_constants] == [1.05]
    with pytest.raises(StaleDocumentRevisionError):
        _save_document(tmp_path, document)
    assert constants.read_bytes() == newer
