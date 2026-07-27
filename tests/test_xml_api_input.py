"""XML preview support for Quote Input nodes."""

from __future__ import annotations

import pytest

from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
from haute._json_shred import (
    build_per_port_cache,
    infer_v2_schema_from_data,
    load_per_port_cache,
)


def test_xml_routes_through_structured_api_input_codec(tmp_path) -> None:
    data_path = tmp_path / "quotes.xml"
    data_path.write_text(
        """\
<quotes>
  <quote><id>1</id><premium>12.50</premium></quote>
  <quote><id>2</id><premium>18.75</premium></quote>
</quotes>
""",
        encoding="utf-8",
    )

    assert is_json_api_input_path(str(data_path))
    inferred = infer_v2_schema_from_data(data_path)
    root = inferred["tables"][0]
    assert root["path"] == "$[:]"
    assert {column["path"] for column in root["columns"]} == {
        "$[:].id",
        "$[:].premium",
    }

    build_per_port_cache(data_path, inferred, tmp_path / "cache")
    frame = load_per_port_cache(tmp_path / "cache", inferred)[root["label"]].collect()
    assert frame.to_dicts() == [
        {"id": "1", "premium": "12.50"},
        {"id": "2", "premium": "18.75"},
    ]


def test_xml_rejects_entity_declarations(tmp_path) -> None:
    data_path = tmp_path / "unsafe.xml"
    data_path.write_text(
        '<!DOCTYPE quote [<!ENTITY x "expanded">]><quote><id>&x;</id></quote>',
        encoding="utf-8",
    )

    with pytest.raises(ApiInputSchemaError, match="DTD and entity"):
        infer_v2_schema_from_data(data_path)
