"""A Constant node's sidecar is a map from name to value (READ-28).

Spec: specs/pipeline-config/high-level.md, Constant sidecars.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._config_io import load_node_config, node_config_json
from haute._node_apply import constant_frame
from haute._types import NodeType
from haute.errors import NodeConfigError


def _round_trip(tmp_path: Path, rows: list[dict]) -> tuple[str, dict]:
    text = node_config_json(NodeType.CONSTANT, {"values": rows}, node_label="rates")
    sidecar = tmp_path / "config" / "constant" / "rates.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(text.encode("utf-8"))
    return text, load_node_config("config/constant/rates.json", base_dir=tmp_path)


def test_a_constant_node_round_trips_as_a_map_of_numbers_and_text(tmp_path: Path) -> None:
    rows = [
        {"name": "load", "value": "1.45"},
        {"name": "cap", "value": "2"},
        {"name": "region", "value": "north"},
        {"name": "written", "value": "1e3"},
    ]

    text, loaded = _round_trip(tmp_path, rows)

    assert text == (
        "{\n"
        '  "values": {\n'
        '    "load": 1.45,\n'
        '    "cap": 2,\n'
        '    "region": "north",\n'
        '    "written": "1e3"\n'
        "  }\n"
        "}\n"
    )
    assert loaded["values"] == rows
    frame = constant_frame(loaded["values"]).collect()
    assert frame.schema["load"] == pl.Float64 and frame.schema["region"] == pl.String
    # Text the settings file holds as text runs as text (EXE-02).
    assert frame.schema["written"] == pl.String and frame["written"].to_list() == ["1e3"]


def test_a_row_without_a_name_is_not_written_and_a_name_twice_is_refused(
    tmp_path: Path,
) -> None:
    text, loaded = _round_trip(tmp_path, [{"name": "", "value": "1"}, {"name": "a", "value": "2"}])
    assert loaded["values"] == [{"name": "a", "value": "2"}]
    with pytest.raises(NodeConfigError, match="more than one value"):
        node_config_json(
            NodeType.CONSTANT,
            {"values": [{"name": "a", "value": "1"}, {"name": "a", "value": "2"}]},
            node_label="rates",
        )


def test_a_sidecar_holding_rows_is_refused_naming_the_map_form(tmp_path: Path) -> None:
    sidecar = tmp_path / "config" / "constant" / "rates.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b'{"values": [{"name": "a", "value": "1"}]}')
    with pytest.raises(NodeConfigError, match="Write them as a map from each constant's name"):
        load_node_config("config/constant/rates.json", base_dir=tmp_path)


#: The case file both readers of a typed number are held to (specs/frontend-shared
#: high-level, "Typed numbers"): each case's value and how a Constant holds it.
_CASES: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1] / "frontend/src/utils/__tests__/fixtures/typedText.json"
    ).read_text(encoding="utf-8")
)
_HELD = [
    *(
        {"name": case["name"], "value": case["text"], "constant": case["constant"]}
        for case in _CASES["numbers"]
    ),
    *_CASES["constants"],
]
_DTYPES = {
    "Int64": pl.Int64,
    "Float64": pl.Float64,
    "Boolean": pl.Boolean,
    "String": pl.String,
    "Null": pl.Null,
}


@pytest.mark.parametrize("case", _HELD, ids=[case["name"] for case in _HELD])
def test_a_constant_runs_as_the_type_its_sidecar_holds(
    tmp_path: Path, case: dict[str, Any]
) -> None:
    # EXE-02: the codec and the run read a value one way, so what the sidecar holds is what runs.
    rows = [{"name": "c", "value": case["value"]}]
    if case["constant"] == "refused":
        with pytest.raises(NodeConfigError, match="Constant 'c' has the value"):
            node_config_json(NodeType.CONSTANT, {"values": rows}, node_label="rates")
        with pytest.raises(NodeConfigError, match="Constant 'c' has the value"):
            constant_frame(rows)
        return
    text, loaded = _round_trip(tmp_path, rows)
    held = json.loads(text)["values"]["c"]
    frame = constant_frame(loaded["values"]).collect()
    expected = case["constant"]

    assert frame.schema["c"] == _DTYPES[expected["type"]]
    if expected["type"] == "String":
        assert held == case["value"] and frame["c"].to_list() == [case["value"]]
    else:
        value = int(expected["value"]) if expected["type"] == "Int64" else expected["value"]
        assert held == value and type(held) is type(frame["c"].item())
        assert frame["c"].to_list() == [value]
    # The editor holds the value again as the text it was typed as.
    typed = case["value"]
    assert loaded["values"] == [
        {
            "name": "c",
            "value": typed if typed is None or isinstance(typed, str | bool) else str(typed),
        }
    ]


def test_a_constant_that_is_not_finite_is_refused_by_the_settings_check() -> None:
    from haute._config_validation import validate_node_config

    with pytest.raises(
        NodeConfigError, match="Constant 'rate' has the value 'nan', which is not a finite number"
    ):
        validate_node_config(NodeType.CONSTANT, {"values": [{"name": "rate", "value": "nan"}]})
