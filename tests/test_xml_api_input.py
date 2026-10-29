"""XML preview support for Quote Input nodes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from haute._api_input_schema import ApiInputSchemaError, is_json_api_input_path
from haute._input_providers import InputSnapshotMissingError
from haute._json_shred._cache import load_v2_api_source
from haute._json_shred._inference import infer_v2_schema_from_data
from haute._json_shred._records import _iter_xml_records, _xml_element_value, _xml_local_name
from haute._sandbox import _get_project_root, set_project_root
from tests.conftest import build_test_api_input_snapshots


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

    config = {"path": str(data_path), "contract": "opaque", **inferred}
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        build_test_api_input_snapshots(data_path, config)
        frame = load_v2_api_source(str(data_path), config, read_snapshots=True)[
            root["label"]
        ].collect()
    finally:
        set_project_root(original_root)
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


_SINGLETON_DRIVERS = """<quotes>
  <quote><id>1</id><driver><age>30</age></driver><driver><age>40</age></driver></quote>
  <quote><id>2</id><driver><age>50</age></driver></quote>
  <quote><id>3</id></quote>
</quotes>
"""


def _xml_tables(tmp_path: Path, source: str, config: dict | None = None) -> dict[str, list]:
    data_path = tmp_path / "quotes.xml"
    data_path.write_text(source, encoding="utf-8")
    if config is None:
        config = infer_v2_schema_from_data(data_path)
        for table in config["tables"]:
            table["emit"] = True
    config = {"path": str(data_path), "contract": "opaque", **config}
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        build_test_api_input_snapshots(data_path, config)
        frames = load_v2_api_source(str(data_path), config, read_snapshots=True)
        return {label: frame.collect().to_dicts() for label, frame in frames.items()}
    finally:
        set_project_root(original_root)


def test_a_single_xml_element_where_another_quote_repeats_it_is_a_one_row_list(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "quotes.xml"
    data_path.write_text(_SINGLETON_DRIVERS, encoding="utf-8")

    inferred = infer_v2_schema_from_data(data_path)

    assert [table["path"] for table in inferred["tables"]] == ["$[:]", "$[:].driver[:]"]
    assert [column["path"] for column in inferred["tables"][0]["columns"]] == ["$[:].id"]
    tables = _xml_tables(tmp_path, _SINGLETON_DRIVERS)
    assert tables["driver"] == [{"age": "30"}, {"age": "40"}, {"age": "50"}]


def test_an_empty_xml_element_holds_nothing(tmp_path: Path) -> None:
    """An empty element (``<driver/>``) arrives as an empty string, alone or among
    repeated elements: it is no row and no skipped row, so a feed that writes one
    for "none" builds cleanly."""
    from haute._json_shred._records import ShredSkipStats
    from haute._json_shred._shred import file_records, shred_request, shred_to_buffers

    data_path = tmp_path / "quotes.xml"
    data_path.write_text(_SINGLETON_DRIVERS, encoding="utf-8")
    schema = infer_v2_schema_from_data(data_path)
    for table in schema["tables"]:
        table["emit"] = True
    data_path.write_text(
        "<quotes><quote><id>1</id><driver/></quote>"
        "<quote><id>2</id><driver><age>50</age></driver><driver/></quote></quotes>",
        encoding="utf-8",
    )
    stats = ShredSkipStats()

    buffers = shred_to_buffers(file_records(data_path, schema), schema, stats=stats)

    assert buffers["driver"] == [{"age": "50"}]
    assert stats.skipped_rows_by_table == {}
    assert shred_request(list(file_records(data_path, schema)), schema)["driver"].height == 1


def test_a_schema_keeps_singleton_elements_as_lists_in_a_later_file(tmp_path: Path) -> None:
    learned = tmp_path / "learned"
    learned.mkdir()
    (learned / "quotes.xml").write_text(_SINGLETON_DRIVERS, encoding="utf-8")
    schema = infer_v2_schema_from_data(learned / "quotes.xml")
    for table in schema["tables"]:
        table["emit"] = True

    tables = _xml_tables(
        tmp_path,
        "<quotes><quote><id>7</id><driver><age>61</age></driver>"
        "<owner><driver><age>9</age></driver></owner></quote>"
        "<quote><id>8</id><driver><age>62</age></driver></quote></quotes>",
        schema,
    )

    assert tables["driver"] == [{"age": "61"}, {"age": "62"}]


def test_single_scalar_xml_elements_join_their_repeated_list(tmp_path: Path) -> None:
    tables = _xml_tables(
        tmp_path,
        "<quotes><quote><id>1</id><tag>a</tag><tag>b</tag></quote>"
        "<quote><id>2</id><tag>c</tag></quote></quotes>",
    )

    assert tables["tag"] == [{"value": "a"}, {"value": "b"}, {"value": "c"}]


def test_a_single_element_inside_a_wrapper_element_is_a_one_row_list(tmp_path: Path) -> None:
    tables = _xml_tables(
        tmp_path,
        "<quotes><quote><id>1</id><drivers><driver><age>30</age></driver>"
        "<driver><age>40</age></driver></drivers></quote>"
        "<quote><id>2</id><drivers><driver><age>50</age></driver></drivers></quote></quotes>",
    )

    assert tables["driver"] == [{"age": "30"}, {"age": "40"}, {"age": "50"}]


def test_a_table_built_before_singleton_lists_is_not_read_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot built under the previous shred semantics never answers for the source.

    Before single elements became lists, quote 2's one driver was dropped, so a table
    built then disagrees with a build today (``SHRED_SEMANTICS_VERSION``).
    """
    from haute._json_shred import _records, _shred, _snapshots

    with monkeypatch.context() as previous:
        previous.setattr(_snapshots, "SHRED_SEMANTICS_VERSION", 1)
        previous.setattr(
            _shred,
            "file_records",
            lambda path, config, *, stats=None: _records._iter_records(path, stats=stats),
        )
        assert _xml_tables(tmp_path, _SINGLETON_DRIVERS)["driver"] == [
            {"age": "30"},
            {"age": "40"},
        ]

    data_path = tmp_path / "quotes.xml"
    config = infer_v2_schema_from_data(data_path)
    for table in config["tables"]:
        table["emit"] = True
    config = {"path": str(data_path), "contract": "opaque", **config}
    original_root = _get_project_root()
    set_project_root(tmp_path)
    try:
        with pytest.raises(InputSnapshotMissingError):
            load_v2_api_source(str(data_path), config, read_snapshots=True)
    finally:
        set_project_root(original_root)
