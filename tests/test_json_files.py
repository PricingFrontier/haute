"""One printer writes every committed JSON file, a shape per kind of record (READ-10).

Spec: specs/pipeline-config/high-level.md, "JSON files Haute writes". Each golden is
written from the specification: the records its kind names on one line, the rest one item
a line, keys in their kind's order, defaults the loader fills left out.
"""

from __future__ import annotations

from pathlib import Path

from haute._config_io import global_constants_json, node_config_json
from haute._json_files import ANY, at, render_json
from haute._types import (
    GlobalConstant,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute._workbench_test_quotes import test_quote_bytes as quote_bytes
from haute.routes._helpers import save_sidecar


def test_the_printer_writes_named_records_and_numbers_on_one_line() -> None:
    value = {"b": [1, 2.5], "a": {"rows": [{"k": "é", "v": 1}, {"k": "x", "v": 2}]}, "e": []}
    assert render_json(value, one_line=at(("a", "rows", ANY))) == (
        "{\n"
        '  "b": [1, 2.5],\n'
        '  "a": {\n'
        '    "rows": [\n'
        '      {"k": "é", "v": 1},\n'
        '      {"k": "x", "v": 2}\n'
        "    ]\n"
        "  },\n"
        '  "e": []\n'
        "}\n"
    )


def test_a_rating_step_sidecar_orders_its_tables_and_writes_an_entry_a_line() -> None:
    config = {
        "tables": [
            {
                "entries": [{"value": 1.1, "region": "north"}, {"region": "south", "value": 0.9}],
                "outputColumn": "region_factor",
                "defaultValue": None,
                "factors": ["region"],
                "name": "Region",
            }
        ],
        "combinedOutputs": [],
    }
    assert node_config_json(NodeType.RATING_STEP, config, node_label="rate") == (
        "{\n"
        '  "tables": [\n'
        "    {\n"
        '      "name": "Region",\n'
        '      "factors": [\n'
        '        "region"\n'
        "      ],\n"
        '      "outputColumn": "region_factor",\n'
        '      "defaultValue": null,\n'
        '      "entries": [\n'
        '        {"region": "north", "value": 1.1},\n'
        '        {"region": "south", "value": 0.9}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )


def test_a_data_input_sidecar_leaves_out_empty_arguments() -> None:
    config = {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": "q.csv",
        "arguments": {},
    }
    text = node_config_json(NodeType.DATA_INPUT, config, node_label="quotes")
    assert '"arguments"' not in text
    assert (
        node_config_json(
            NodeType.DATA_INPUT, {**config, "arguments": {"separator": ";"}}, node_label="quotes"
        ).count('"arguments"')
        == 1
    )


def test_a_pipeline_sidecar_writes_a_position_a_line(tmp_path: Path) -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(id=nid, position={"x": x, "y": 0}, data=NodeData(label=nid))
            for nid, x in (("b", 299.5), ("a", 0.4))
        ]
    )
    save_sidecar(tmp_path / "main.py", graph)
    assert (tmp_path / "main.haute.json").read_bytes() == (
        b'{\n  "positions": {\n    "a": {"x": 0, "y": 0},\n    "b": {"x": 300, "y": 0}\n  }\n}\n'
    )


def test_global_constants_and_a_test_quote_use_the_printer() -> None:
    constants = global_constants_json([GlobalConstant(name="tax", type="float", value=0.12)])
    assert constants == render_json(
        {"constants": [{"name": "tax", "type": "float", "value": 0.12}]}
    )
    assert quote_bytes({"policy": {"age": 30}}, {"premium": 100}) == (
        b'[\n  {\n    "input": {\n      "policy": {"age": 30}\n    },\n'
        b'    "expected": {"premium": 100}\n  }\n]\n'
    )


def test_saving_twice_gives_identical_bytes() -> None:
    config = {"tables": [{"name": "T", "factors": ["k"], "outputColumn": "f", "entries": []}]}
    once = node_config_json(NodeType.RATING_STEP, config, node_label="rate")
    import json

    assert node_config_json(NodeType.RATING_STEP, json.loads(once), node_label="rate") == once


def test_a_workbench_schema_column_and_a_show_when_condition_sit_on_one_line() -> None:
    from haute._workbench_folder import _SCHEMA_ONE_LINE, _a_condition

    schema = {"name": "policy", "columns": [{"name": "age", "type": "int"}]}
    assert render_json(schema, one_line=_SCHEMA_ONE_LINE) == (
        '{\n  "name": "policy",\n  "columns": [\n    {"name": "age", "type": "int"}\n  ]\n}\n'
    )
    sheet = {"show_when": {"match": "any", "conditions": [{"column": "p.x", "op": "ticked"}]}}
    assert render_json(sheet, one_line=_a_condition) == (
        '{\n  "show_when": {\n    "match": "any",\n    "conditions": [\n'
        '      {"column": "p.x", "op": "ticked"}\n    ]\n  }\n}\n'
    )


def test_an_unchanged_pipeline_saves_its_sidecar_byte_for_byte(tmp_path: Path) -> None:
    from haute.routes._helpers import parse_pipeline_to_graph

    main = tmp_path / "main.py"
    main.write_text(
        'import haute\nimport polars as pl\n\npipeline = haute.Pipeline("main")\n\n\n'
        "@pipeline.polars\ndef data() -> pl.LazyFrame:\n"
        '    df = pl.LazyFrame({"x": [1]})\n    return df\n',
        encoding="utf-8",
    )
    (tmp_path / "main.haute.json").write_bytes(
        b'{\n  "positions": {\n    "data": {"x": 120, "y": -356}\n  }\n}\n'
    )
    before = (tmp_path / "main.haute.json").read_bytes()
    save_sidecar(main, parse_pipeline_to_graph(main))
    assert (tmp_path / "main.haute.json").read_bytes() == before
