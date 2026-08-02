"""Document-revision coverage for parent pipelines and referenced children."""

from __future__ import annotations

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
                }
            ],
            "edges": [],
            "submodels": {
                "child": {
                    "file": "modules/child.py",
                    "childNodeIds": ["child"],
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
