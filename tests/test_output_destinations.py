"""Deploy artifact keys and Data Output destinations cannot collide (NAME-08)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from haute._executable_names import executable_name_violations
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph, SubmodelDefinition
from haute.deploy._bundler import collect_artifacts
from haute.errors import DeployError, ParseError
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from tests.conftest import write_node_config


def _node(node_id: str, node_type: str, **config: Any) -> GraphNode:
    return GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))


def _file_output(node_id: str, path: str) -> GraphNode:
    return _node(
        node_id,
        "dataOutput",
        outputType="file",
        format="parquet",
        mode="sink",
        path=path,
        arguments={},
    )


def _table_output(node_id: str, table: str) -> GraphNode:
    return _node(
        node_id,
        "dataOutput",
        outputType="database",
        format="database",
        mode="write",
        connection="warehouse",
        table=table,
        arguments={},
    )


# ---------------------------------------------------------------------------
# Deploy artifact keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        pytest.param(
            ("a", "b__c.pkl"),
            ("a__b", "c.pkl"),
            "would share one bundle key.",
            id="non-injective-key",
        ),
        pytest.param(
            ("Model", "m.pkl"),
            ("model", "m.pkl"),
            "would share one bundle key on a case-insensitive file system",
            id="keys-equal-ignoring-case",
        ),
    ],
)
def test_colliding_artifact_keys_are_refused_before_upload(
    tmp_path: Path, first: tuple[str, str], second: tuple[str, str], message: str
) -> None:
    for _node_id, filename in (first, second):
        (tmp_path / filename).write_bytes(b"model")
    graph = PipelineGraph(
        nodes=[
            _node(node_id, "externalFile", path=filename, fileType="pickle")
            for node_id, filename in (first, second)
        ],
        edges=[],
    )

    with pytest.raises(DeployError, match=message) as excinfo:
        collect_artifacts(graph, [], tmp_path)

    assert f"node {first[0]!r}" in str(excinfo.value) and f"node {second[0]!r}" in str(
        excinfo.value
    )


def test_distinct_artifact_keys_are_bundled(tmp_path: Path) -> None:
    (tmp_path / "a.pkl").write_bytes(b"model")
    (tmp_path / "b.pkl").write_bytes(b"model")
    graph = PipelineGraph(
        nodes=[
            _node("first", "externalFile", path="a.pkl", fileType="pickle"),
            _node("second", "externalFile", path="b.pkl", fileType="pickle"),
        ],
        edges=[],
    )

    assert sorted(collect_artifacts(graph, [], tmp_path)) == ["first__a.pkl", "second__b.pkl"]


# ---------------------------------------------------------------------------
# Data Output destinations
# ---------------------------------------------------------------------------


def test_two_outputs_writing_one_file_are_refused_at_save(tmp_path: Path) -> None:
    graph = PipelineGraph(
        nodes=[
            _file_output("first", "out/result.parquet"),
            _file_output("second", "out/result.parquet"),
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as excinfo:
        SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")

    assert (
        "Data Output nodes 'first' (the pipeline) and 'second' (the pipeline) write one "
        "destination, out/result.parquet" in str(excinfo.value.detail)
    )


def test_a_bare_file_name_and_its_outputs_path_are_one_destination() -> None:
    graph = PipelineGraph(
        nodes=[_file_output("first", "result"), _file_output("second", "outputs/result.parquet")],
        edges=[],
    )

    assert [v.kind for v in executable_name_violations(graph)] == ["output_destination"]


def test_one_table_name_in_different_schemas_is_two_destinations() -> None:
    graph = PipelineGraph(
        nodes=[_table_output("first", "a.result"), _table_output("second", "b.result")],
        edges=[],
    )

    assert executable_name_violations(graph) == []


def test_a_submodel_output_written_by_two_occurrences_is_refused() -> None:
    definition = SubmodelDefinition(
        definitionId="rates",
        file="modules/rates.py",
        graph=PipelineGraph(nodes=[_file_output("written", "out/rates.parquet")], edges=[]),
        inputPorts=[],
        outputPorts=[],
    )
    occurrences = [
        _node(alias, "submodel", definitionId="rates", alias=alias)
        for alias in ("rates_a", "rates_b")
    ]
    graph = PipelineGraph(nodes=occurrences, edges=[], submodels={"rates": definition})

    (violation,) = executable_name_violations(graph)

    assert violation.kind == "output_destination"
    assert violation.name == "out/rates.parquet"


def test_unfinished_outputs_are_not_compared(tmp_path: Path) -> None:
    graph = PipelineGraph(nodes=[_file_output("first", ""), _file_output("second", "")], edges=[])

    SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")


_RUN_SOURCE = """\
import haute
import polars as pl

pipeline = haute.Pipeline("main")


@pipeline.polars
def rows() -> pl.LazyFrame:
    return pl.LazyFrame({{"x": [1]}})


@pipeline.data_output(config="{first}")
def first(rows): ...


@pipeline.data_output(config="{second}")
def second(rows): ...
"""


@pytest.mark.parametrize(
    ("paths", "refused"),
    [
        pytest.param(("out/result.parquet", "out/result.parquet"), True, id="one-destination"),
        pytest.param(("", ""), False, id="two-unfinished-drafts"),
    ],
)
def test_a_strict_parse_refuses_one_destination_and_keeps_drafts(
    tmp_path: Path, paths: tuple[str, str], refused: bool
) -> None:
    configs = [
        write_node_config(
            tmp_path,
            NodeType.DATA_OUTPUT,
            name,
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": path,
                "arguments": {},
            },
        )
        for name, path in zip(("first", "second"), paths, strict=True)
    ]
    main = tmp_path / "main.py"
    main.write_text(_RUN_SOURCE.format(first=configs[0], second=configs[1]), encoding="utf-8")

    if refused:
        with pytest.raises(ParseError, match="write one destination, out/result.parquet"):
            parse_pipeline_file(main)
        assert not (tmp_path / "out").exists()
    else:
        graph = parse_pipeline_file(main)
        assert [node.data.config["path"] for node in graph.nodes[1:]] == ["", ""]


def test_a_relative_and_an_absolute_path_to_one_file_are_one_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._sandbox import set_project_root

    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    graph = PipelineGraph(
        nodes=[
            _file_output("first", "out/result.parquet"),
            _file_output("second", (tmp_path / "out" / "result.parquet").as_posix()),
        ],
        edges=[],
    )

    assert [v.kind for v in executable_name_violations(graph)] == ["output_destination"]
