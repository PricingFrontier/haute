"""Loadable-incomplete Data Input/Output configurations (REC-R01).

A required locator that is absent or empty is a completeness gap, not a load
failure: tolerant validation accepts the config unchanged, the gaps are
reported separately, and strict callers keep today's first-failure errors.
Structural violations stay strict in both modes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from haute._polars_io_registry import (
    PolarsIoConfigError,
    data_input_completeness,
    data_output_completeness,
    validate_data_input_config,
    validate_data_output_config,
)

INCOMPLETE_INPUTS: list[tuple[dict[str, Any], list[str]]] = [
    (
        {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": "",
            "arguments": {},
            "code": "",
        },
        ["path"],
    ),
    (
        {"inputType": "lakehouse", "format": "delta", "mode": "scan", "path": ""},
        ["path"],
    ),
    (
        {"inputType": "databricks", "http_path": "", "table": ""},
        ["http_path", "table"],
    ),
    (
        {"inputType": "database", "format": "database", "query": ""},
        ["connection", "query"],
    ),
    (
        {"inputType": "inline", "format": "records"},
        ["records"],
    ),
]

INCOMPLETE_OUTPUTS: list[tuple[dict[str, Any], list[str]]] = [
    (
        {"outputType": "file", "format": "parquet", "mode": "sink", "path": "", "arguments": {}},
        ["path"],
    ),
    (
        {"outputType": "lakehouse", "format": "delta", "path": ""},
        ["path"],
    ),
    (
        {"outputType": "database", "format": "database", "table": ""},
        ["connection", "table"],
    ),
]


@pytest.mark.parametrize(("config", "gaps"), INCOMPLETE_INPUTS)
def test_incomplete_input_is_tolerated_and_reported(config: dict[str, Any], gaps: list[str]):
    with pytest.raises(PolarsIoConfigError):
        validate_data_input_config(config)
    assert validate_data_input_config(config, require_complete=False) == config
    reported = data_input_completeness(config)
    assert [gap.path for gap in reported] == gaps
    assert all(gap.code == "required" for gap in reported)
    assert all(gap.message for gap in reported)


@pytest.mark.parametrize(("config", "gaps"), INCOMPLETE_OUTPUTS)
def test_incomplete_output_is_tolerated_and_reported(config: dict[str, Any], gaps: list[str]):
    with pytest.raises(PolarsIoConfigError):
        validate_data_output_config(config)
    assert validate_data_output_config(config, require_complete=False) == config
    reported = data_output_completeness(config)
    assert [gap.path for gap in reported] == gaps
    assert all(gap.code == "required" for gap in reported)


def test_complete_configs_report_no_gaps():
    complete_input = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": "data/train.parquet",
        "arguments": {},
        "code": "",
    }
    complete_output = {
        "outputType": "file",
        "format": "parquet",
        "mode": "sink",
        "path": "out/scored.parquet",
        "arguments": {},
    }
    assert data_input_completeness(complete_input) == []
    assert data_output_completeness(complete_output) == []
    inline = {"inputType": "inline", "format": "records", "records": []}
    assert data_input_completeness(inline) == []


@pytest.mark.parametrize(
    "config",
    [
        {"inputType": "file", "format": "parquet", "mode": "teleport", "path": ""},
        {"inputType": "file", "format": "json", "mode": "scan", "path": ""},
        {"inputType": "file", "format": "parquet", "path": "", "wormhole": True},
        {
            "inputType": "database",
            "format": "database",
            "connection": "A",
            "uri": "b://c",
            "query": "Q",
        },
        {"inputType": "database", "format": "database", "connection": 7, "query": "Q"},
        {"inputType": "file", "format": "parquet", "path": 123},
        {"inputType": "portal", "format": "parquet", "path": ""},
        {"inputType": "inline", "format": "records", "records": "nope"},
    ],
    ids=[
        "invalid-mode",
        "json-scan-unsupported",
        "inactive-field",
        "both-locators",
        "non-string-locator",
        "non-string-path",
        "unknown-input-type",
        "records-not-list",
    ],
)
def test_structural_violations_stay_strict_in_tolerant_mode(config: dict[str, Any]):
    with pytest.raises(PolarsIoConfigError):
        validate_data_input_config(config, require_complete=False)


@pytest.mark.parametrize(
    "config",
    [
        {"outputType": "file", "format": "parquet", "mode": "beam", "path": ""},
        {
            "outputType": "database",
            "format": "database",
            "connection": "A",
            "uri": "b://c",
            "table": "t",
        },
        {"outputType": "file", "format": "parquet", "path": None},
        {"outputType": "elsewhere", "format": "parquet", "path": ""},
    ],
    ids=[
        "invalid-mode",
        "both-locators",
        "null-path-is-incomplete-not-structural",
        "unknown-output-type",
    ],
)
def test_output_structural_violations_stay_strict(config: dict[str, Any]):
    if config.get("path") is None and config.get("outputType") == "file":
        # Explicit null is absence, not a structural violation.
        assert validate_data_output_config(config, require_complete=False) == config
        assert [gap.path for gap in data_output_completeness(config)] == ["path"]
        return
    with pytest.raises(PolarsIoConfigError):
        validate_data_output_config(config, require_complete=False)


def test_strict_messages_are_unchanged():
    with pytest.raises(PolarsIoConfigError, match="requires a non-empty 'path'"):
        validate_data_input_config(
            {"inputType": "file", "format": "parquet", "mode": "scan", "path": ""}
        )
    with pytest.raises(PolarsIoConfigError, match="exactly one non-empty 'connection' or 'uri'"):
        validate_data_input_config({"inputType": "database", "format": "database", "query": "Q"})


def test_parse_accepts_incomplete_data_input_sidecar(tmp_path):
    from haute.parser import parse_pipeline_file

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        "import haute\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\n'
        "def source():\n"
        "    return None\n"
    )
    (tmp_path / "in.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "",
                "arguments": {},
                "code": "",
            }
        )
    )
    graph = parse_pipeline_file(tmp_path / "main.py")
    (node,) = [item for item in graph.nodes if item.data.label == "source"]
    assert node.data.config["path"] == ""


def test_document_reports_completeness_for_loadable_incomplete_nodes(tmp_path):
    from haute._pipeline_recovery import load_pipeline_editor_document

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        "import haute\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\n'
        "def source():\n"
        "    return None\n"
        '@pipeline.data_output(config="out.json")\n'
        "def sink(source):\n"
        "    return None\n"
    )
    (tmp_path / "in.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "",
                "arguments": {},
                "code": "",
            }
        )
    )
    (tmp_path / "out.json").write_text(
        json.dumps(
            {
                "outputType": "file",
                "format": "parquet",
                "mode": "sink",
                "path": "",
                "arguments": {},
            }
        )
    )
    document = load_pipeline_editor_document(tmp_path / "main.py", project_root=tmp_path)
    assert document.load_status == "ready"
    by_label = {node.label: node for node in document.nodes}
    assert by_label["source"].availability == "ready"
    assert by_label["sink"].availability == "ready"
    reported = [(entry.element_id, entry.path, entry.code) for entry in document.completeness]
    assert (by_label["source"].recovery_id, "path", "required") in reported
    assert (by_label["sink"].recovery_id, "path", "required") in reported
    assert len(reported) == 2
    assert document.completeness_omitted == 0
    assert all(entry.message for entry in document.completeness)
    # Completeness never degrades the document or its capabilities.
    assert document.capabilities.can_save
    assert document.capabilities.can_mutate


def test_parse_still_rejects_structural_violations(tmp_path):
    from haute.errors import ConfigError
    from haute.parser import parse_pipeline_file

    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "main.py").write_text(
        "import haute\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="in.json")\n'
        "def source():\n"
        "    return None\n"
    )
    (tmp_path / "in.json").write_text(
        json.dumps({"inputType": "file", "format": "json", "mode": "scan", "path": "x.json"})
    )
    with pytest.raises(ConfigError, match="no lazy scan"):
        parse_pipeline_file(tmp_path / "main.py")
