"""XML preview support for Quote Input nodes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
from haute._json_shred._cache import build_per_port_cache, load_per_port_cache
from haute._json_shred._inference import infer_v2_schema_from_data
from haute._json_shred._records import _iter_xml_records, _xml_element_value, _xml_local_name


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


def test_xml_repeated_records_stream_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_path = tmp_path / "quotes.xml"
    data_path.write_text(
        "<quotes><quote><id>1</id></quote><quote><id>2</id></quote></quotes>",
        encoding="utf-8",
    )
    original = Path.read_bytes

    def _no_source_read_bytes(path: Path) -> bytes:
        if path == data_path:
            raise AssertionError("XML records must not materialise through Path.read_bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", _no_source_read_bytes)

    assert list(_iter_xml_records(data_path)) == [{"id": "1"}, {"id": "2"}]


def test_repeated_xml_document_can_exceed_limit_when_each_record_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_path = tmp_path / "quotes.xml"
    data_path.write_text(
        "<quotes>" + "".join(f"<quote><id>{i}</id></quote>" for i in range(20)) + "</quotes>",
        encoding="utf-8",
    )
    assert data_path.stat().st_size > 96
    monkeypatch.setenv("HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES", "96")

    records = list(_iter_xml_records(data_path))

    assert records == [{"id": str(i)} for i in range(20)]


@pytest.mark.parametrize(
    "source",
    [
        "<quote><value>" + "x" * 128 + "</value></quote>",
        "<quotes><quote><value>" + "x" * 128 + "</value></quote></quotes>",
    ],
)
def test_xml_logical_record_fails_at_hard_record_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    data_path = tmp_path / "oversized.xml"
    data_path.write_text(source, encoding="utf-8")
    monkeypatch.setenv("HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES", "64")

    with pytest.raises(
        ApiInputSchemaError,
        match="exceeds HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES=64",
    ):
        list(_iter_xml_records(data_path))


def test_repeated_xml_child_cannot_exceed_encoded_record_limit_via_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = "<quote><id>1</id>" + (" " * 40) + "</quote>"
    assert len(record.encode("utf-8")) == 65
    data_path = tmp_path / "oversized-whitespace.xml"
    data_path.write_text(f"<quotes>{record}</quotes>", encoding="utf-8")
    monkeypatch.setenv("HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES", "64")

    with pytest.raises(
        ApiInputSchemaError,
        match="exceeds HAUTE_STRUCTURED_INPUT_MAX_RECORD_BYTES=64",
    ):
        list(_iter_xml_records(data_path))


@pytest.mark.parametrize(
    "declaration",
    [
        "<!doctype quote>",
        '<!ENTITY x "expanded">',
    ],
)
def test_xml_rejects_dtd_or_entity_declarations(tmp_path, declaration: str) -> None:
    data_path = tmp_path / "unsafe.xml"
    data_path.write_text(
        f"{declaration}<quote><id>1</id></quote>",
        encoding="utf-8",
    )

    with pytest.raises(ApiInputSchemaError, match="DTD and entity"):
        infer_v2_schema_from_data(data_path)


def test_xml_namespaces_attributes_and_text_use_local_names(tmp_path) -> None:
    data_path = tmp_path / "namespaced.xml"
    data_path.write_text(
        """\
<q:quote xmlns:q="urn:quote" q:id="7">
  <q:premium currency="GBP"> 12.50 </q:premium>
</q:quote>
""",
        encoding="utf-8",
    )

    assert _xml_local_name("quote") == "quote"
    assert _xml_local_name("prefix:quote") == "quote"
    assert list(_iter_xml_records(data_path)) == [
        {
            "id": "7",
            "premium": {"currency": "GBP", "value": "12.50"},
        }
    ]


def test_xml_repeated_children_are_lists_and_singletons_are_scalars(tmp_path) -> None:
    data_path = tmp_path / "repeated.xml"
    data_path.write_text(
        "<quote><id>1</id><tag>a</tag><tag>b</tag></quote>",
        encoding="utf-8",
    )

    assert list(_iter_xml_records(data_path)) == [{"id": "1", "tag": ["a", "b"]}]


def test_xml_homogeneous_scalar_children_stay_in_one_root_record(tmp_path) -> None:
    data_path = tmp_path / "scalars.xml"
    data_path.write_text(
        "<values><value>1</value><value>2</value></values>",
        encoding="utf-8",
    )

    assert list(_iter_xml_records(data_path)) == [{"value": ["1", "2"]}]


def test_xml_mixed_object_children_stay_in_one_root_record(tmp_path) -> None:
    data_path = tmp_path / "mixed-objects.xml"
    data_path.write_text(
        "<quote><holder><id>1</id></holder><vehicle><id>2</id></vehicle></quote>",
        encoding="utf-8",
    )

    assert list(_iter_xml_records(data_path)) == [
        {
            "holder": {"id": "1"},
            "vehicle": {"id": "2"},
        }
    ]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            '<quote xmlns:a="urn:a" xmlns:b="urn:b" a:id="1" b:id="2"/>',
            "duplicate XML attribute name",
        ),
        ('<quote id="1"><id>2</id></quote>', "attribute and child element"),
        ('<quote value="1">text</quote>', "'value' attribute and text"),
    ],
)
def test_xml_rejects_local_name_collisions(
    tmp_path,
    source: str,
    message: str,
) -> None:
    data_path = tmp_path / "collision.xml"
    data_path.write_text(source, encoding="utf-8")

    with pytest.raises(ApiInputSchemaError, match=message):
        list(_iter_xml_records(data_path))


@pytest.mark.parametrize(
    "source",
    [
        "<quote>prefix<id>1</id></quote>",
        "<quote><id>1</id>tail</quote>",
    ],
)
def test_xml_rejects_mixed_text_and_children(tmp_path, source: str) -> None:
    data_path = tmp_path / "mixed.xml"
    data_path.write_text(source, encoding="utf-8")

    with pytest.raises(ApiInputSchemaError, match="mixed text and child elements"):
        list(_iter_xml_records(data_path))


def test_xml_invalid_document_has_typed_decode_error(tmp_path) -> None:
    data_path = tmp_path / "invalid.xml"
    data_path.write_text("<quote>", encoding="utf-8")

    with pytest.raises(ApiInputSchemaError, match="Invalid XML in data file"):
        list(_iter_xml_records(data_path))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("<quote> 7 </quote>", {"quote": "7"}),
        ('<quote status="new"/>', {"status": "new"}),
    ],
)
def test_xml_scalar_and_attribute_only_roots_are_records(
    tmp_path,
    source: str,
    expected: dict[str, str],
) -> None:
    data_path = tmp_path / "root.xml"
    data_path.write_text(source, encoding="utf-8")

    assert list(_iter_xml_records(data_path)) == [expected]


def test_nested_element_child_tail_text_is_mixed_content(tmp_path) -> None:
    """A child's trailing tail text is mixed content even when the element's
    own text is blank; the nested-value converter must fail closed on it."""
    element = ET.fromstring("<nested>\n  <child>1</child>trailing</nested>")

    with pytest.raises(ApiInputSchemaError, match="mixed text and child elements"):
        _xml_element_value(element)
