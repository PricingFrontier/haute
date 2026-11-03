"""A Constant node's sidecar is a map from name to value (READ-28).

Spec: specs/pipeline-config/high-level.md, Constant sidecars.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._config_io import load_node_config, node_config_json
from haute._constant_config import CONSTANTS_CONVERSION
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
    assert frame["written"].to_list() == [1000.0]


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


def test_a_sidecar_holding_rows_is_refused_naming_the_conversion(tmp_path: Path) -> None:
    sidecar = tmp_path / "config" / "constant" / "rates.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b'{"values": [{"name": "a", "value": "1"}]}')
    with pytest.raises(NodeConfigError, match=CONSTANTS_CONVERSION):
        load_node_config("config/constant/rates.json", base_dir=tmp_path)
