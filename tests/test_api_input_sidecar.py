"""An API Input column and a Quote Response mapping row are one line each (READ-42).

Spec: specs/json-shredding/high-level.md (the column's sidecar form) and
specs/pipeline-config/high-level.md, JSON files Haute writes. A column's sidecar leaves out
a setting at the value inference gives it, and the load gives it back, so a run reads the
same columns as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._config_io import load_node_config, node_config_json
from haute._types import NodeType
from haute.executor import _preview_cache, execute_graph
from haute.parser import parse_pipeline_file
from tests._project import use_project

_INFERRED = {"status": "Inferred", "selected": True, "levels": None, "origin": "inferred"}


def _column(name: str, kind: str = "str", **settings: Any) -> dict[str, Any]:
    return {
        "name": name,
        "path": f"$[:].{name}",
        "type": kind,
        **_INFERRED,
        "key": False,
        **settings,
    }


def _config(*columns: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": "data/quotes.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes",
                "display_path": None,
                "emit": True,
                "row_id_column": None,
                "columns": list(columns),
            }
        ],
    }


def test_each_column_is_one_line_leaving_out_what_inference_gives_it() -> None:
    config = _config(
        _column("quote_id", key=True, origin="manual", status="Confirmed"),
        _column("premium", "float"),
        _column("region", levels=["north", None], selected=False),
        _column("inception", "date", format="%d/%m/%Y"),
    )
    assert node_config_json(NodeType.API_INPUT, config, node_label="quotes") == (
        "{\n"
        '  "path": "data/quotes.json",\n'
        '  "tables": [\n'
        "    {\n"
        '      "path": "$[:]",\n'
        '      "label": "quotes",\n'
        '      "display_path": null,\n'
        '      "emit": true,\n'
        '      "row_id_column": null,\n'
        '      "columns": [\n'
        '        {"name": "quote_id", "path": "$[:].quote_id", "type": "str", '
        '"status": "Confirmed", "origin": "manual", "key": true},\n'
        '        {"name": "premium", "path": "$[:].premium", "type": "float"},\n'
        '        {"name": "region", "path": "$[:].region", "type": "str", "selected": false, '
        '"levels": ["north", null]},\n'
        '        {"name": "inception", "path": "$[:].inception", "type": "date", '
        '"format": "%d/%m/%Y"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )


def test_the_load_gives_each_setting_back_and_a_second_save_is_the_same(tmp_path: Path) -> None:
    config = _config(_column("quote_id", key=True), _column("premium", "float"))
    text = node_config_json(NodeType.API_INPUT, config, node_label="quotes")
    (tmp_path / "quotes.json").write_text(text, encoding="utf-8")

    loaded = load_node_config(tmp_path / "quotes.json", node_type=NodeType.API_INPUT)

    assert loaded == config
    assert node_config_json(NodeType.API_INPUT, loaded, node_label="quotes") == text


def test_a_column_given_no_selected_is_written_as_not_selected(tmp_path: Path) -> None:
    """A run takes a column without ``selected`` as not selected; a sidecar that left the
    key out would read back as selected, so the save writes it."""
    column = {"name": "premium", "path": "$[:].premium", "type": "float"}
    text = node_config_json(NodeType.API_INPUT, _config(column), node_label="quotes")
    assert '{"name": "premium", "path": "$[:].premium", "type": "float", "selected": false}' in text
    (tmp_path / "quotes.json").write_text(text, encoding="utf-8")

    loaded = load_node_config(tmp_path / "quotes.json", node_type=NodeType.API_INPUT)

    assert loaded["tables"][0]["columns"][0]["selected"] is False


def test_a_sidecar_that_writes_every_setting_loads_the_same_and_saves_short(
    tmp_path: Path,
) -> None:
    config = _config(_column("quote_id"), _column("premium", "float"))
    (tmp_path / "quotes.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    loaded = load_node_config(tmp_path / "quotes.json", node_type=NodeType.API_INPUT)

    assert loaded == config
    assert '{"name": "premium", "path": "$[:].premium", "type": "float"}' in node_config_json(
        NodeType.API_INPUT, loaded, node_label="quotes"
    )


def test_each_response_field_is_one_line() -> None:
    mapping = [
        {
            "source_port": "priced",
            "source_column": "quote_id",
            "output_path": "$[:].quote_id",
            "enabled": True,
        },
        {
            "source_port": "priced",
            "source_column": "premium",
            "output_path": "$[:].premium",
            "enabled": False,
        },
    ]
    assert node_config_json(NodeType.OUTPUT, {"output_mapping": mapping}, node_label="out") == (
        "{\n"
        '  "output_mapping": [\n'
        '    {"source_port": "priced", "source_column": "quote_id", '
        '"output_path": "$[:].quote_id", "enabled": true},\n'
        '    {"source_port": "priced", "source_column": "premium", '
        '"output_path": "$[:].premium", "enabled": false}\n'
        "  ]\n"
        "}\n"
    )


_PIPELINE = '''\
"""Pipeline: quotes"""

import haute
import polars as pl

pipeline = haute.Pipeline("quotes")


@pipeline.api_input(config="config/quote_input/quotes.json")
def quote_input(): ...


@pipeline.polars
def priced(quotes: pl.LazyFrame) -> pl.LazyFrame:
    df = quotes
    df = df.with_columns((pl.col("premium") * 2).alias("premium"))
    return df


@pipeline.output(config="config/quote_response/response.json")
def quote_response(priced): ...


pipeline.connect("quote_input", "priced", source_port="quotes")
'''


@pytest.fixture
def project(tmp_path: Path):
    original = Path.cwd()
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "quotes"\npipeline = "main.py"\n', encoding="utf-8"
    )
    (tmp_path / "main.py").write_text(_PIPELINE, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "quotes.json").write_text(
        json.dumps([{"quote_id": "q1", "premium": 10.0}, {"quote_id": "q2", "premium": 7.5}]),
        encoding="utf-8",
    )
    (tmp_path / "config" / "quote_response").mkdir(parents=True)
    mapping = [
        {"source_port": "priced", "source_column": column, "output_path": f"$[:].{column}"}
        for column in ("quote_id", "premium")
    ]
    (tmp_path / "config" / "quote_response" / "response.json").write_text(
        json.dumps({"output_mapping": [{**row, "enabled": True} for row in mapping]}),
        encoding="utf-8",
    )
    (tmp_path / "config" / "quote_input").mkdir(parents=True)
    use_project(tmp_path)
    _preview_cache.clear()
    yield tmp_path
    _preview_cache.clear()
    use_project(original)


def _response(project: Path, sidecar: str) -> Any:
    (project / "config" / "quote_input" / "quotes.json").write_text(sidecar, encoding="utf-8")
    _preview_cache.clear()
    graph = parse_pipeline_file(project / "main.py")
    results = execute_graph(graph, target_node_id="quote_response")
    assert results["quote_response"].status == "ok", results["quote_response"].error
    return results["quote_response"].preview


def test_a_project_whose_columns_leave_their_settings_out_answers_the_same(
    project: Path,
) -> None:
    """Built from the files: the short sidecar, which writes no "selected", gives the
    response the long one does."""
    config = _config(_column("quote_id"), _column("premium", "float"))
    config["tables"][0]["display_path"] = "quotes[]"
    short = node_config_json(NodeType.API_INPUT, config, node_label="quotes")
    assert '"selected"' not in short

    long = _response(project, json.dumps(config, indent=2))
    assert _response(project, short) == long
    assert long == [{"quote_id": "q1", "premium": 20.0}, {"quote_id": "q2", "premium": 15.0}]


@pytest.mark.parametrize(
    "config",
    [
        {"tables": "not a list"},
        {"tables": [{"label": "no columns"}, "not a table"]},
        {"tables": [{"columns": ["not a column"]}]},
    ],
    ids=["tables-not-a-list", "table-without-columns", "column-not-a-mapping"],
)
def test_a_shape_the_model_refuses_passes_through_for_validation_to_name(
    config: dict[str, Any],
) -> None:
    from haute._api_input_schema import columns_for_sidecar, columns_from_sidecar

    assert columns_for_sidecar(config) == config
    assert columns_from_sidecar(config) == config
