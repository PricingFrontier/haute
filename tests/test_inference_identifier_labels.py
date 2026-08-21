"""RED contracts for identifier-safe labels minted by schema inference."""

from __future__ import annotations

import json
import keyword
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import haute._api_input_schema as api_input_schema
from haute._api_input_schema import validate_v2_schema
from haute._json_shred._inference import infer_v2_schema_from_data


def _write(tmp_path: Path, record: dict[str, Any], name: str = "data.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps([record]), encoding="utf-8")
    return path


def _tables_by_path(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {table["path"]: table for table in schema["tables"]}


def _assert_inferred_identity(
    table: dict[str, Any],
    *,
    path: str,
    label: str,
) -> None:
    assert table["label"] == label
    assert table["path"] == path
    assert table["displayPath"] == path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("  quotes  ", "quotes", id="strips-outer-whitespace"),
        pytest.param("quote-id frame", "quote_id_frame", id="spaces-and-hyphens"),
        pytest.param("Plan_42.!/@#", "Plan_42", id="ascii-kept-or-dropped"),
        pytest.param("café/β", "caf_xe9__x3b2_", id="unicode-hex-encoding"),
        pytest.param("", "table", id="empty"),
        pytest.param("  !@#  ", "table", id="empty-after-character-filter"),
        pytest.param("123_frame", "_123_frame", id="digit-leading"),
        pytest.param("class", "class_", id="hard-keyword"),
        pytest.param("match", "match", id="soft-keyword-match"),
        pytest.param("case", "case", id="soft-keyword-case"),
        pytest.param("type", "type", id="soft-keyword-type"),
        pytest.param("_", "_", id="soft-keyword-underscore"),
    ],
)
def test_derive_identifier_label_uses_the_specified_pipeline_and_repairs(
    raw: str,
    expected: str,
) -> None:
    assert api_input_schema.derive_identifier_label(raw) == expected


def test_inference_labels_root_and_children_without_replacing_paths(tmp_path: Path) -> None:
    schema = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "policy_id": 1,
                "proposer": {"claims": [{"amount": 100}]},
                "class": [{"code": "A"}],
            },
        )
    )
    tables = _tables_by_path(schema)

    _assert_inferred_identity(tables["$[:]"], path="$[:]", label="quote_info")
    _assert_inferred_identity(
        tables["$[:].proposer.claims[:]"],
        path="$[:].proposer.claims[:]",
        label="claims",
    )
    _assert_inferred_identity(
        tables["$[:].class[:]"],
        path="$[:].class[:]",
        label="class_",
    )


def test_inference_qualifies_every_shared_innermost_label_symmetrically(
    tmp_path: Path,
) -> None:
    schema = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "id": 1,
                "a": {"items": [{"value": "A"}]},
                "b": {"items": [{"value": "B"}]},
            },
        )
    )
    tables = _tables_by_path(schema)

    _assert_inferred_identity(
        tables["$[:].a.items[:]"],
        path="$[:].a.items[:]",
        label="a_items",
    )
    _assert_inferred_identity(
        tables["$[:].b.items[:]"],
        path="$[:].b.items[:]",
        label="b_items",
    )
    assert "items" not in {table["label"] for table in schema["tables"]}


def test_inference_uses_deterministic_numeric_suffixes_as_the_final_backstop(
    tmp_path: Path,
) -> None:
    schema = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "id": 1,
                "a_b_items": [{"value": "top"}],
                "a_b": {"items": [{"value": "joined"}]},
                "a": {"b": {"items": [{"value": "split"}]}},
            },
        )
    )

    assert [(table["path"], table["label"]) for table in schema["tables"]] == [
        ("$[:]", "quote_info"),
        ("$[:].a_b_items[:]", "a_b_items"),
        ("$[:].a_b.items[:]", "a_b_items_2"),
        ("$[:].a.b.items[:]", "a_b_items_3"),
    ]


def test_inferred_schema_passes_validation_unchanged(tmp_path: Path) -> None:
    schema = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "id": 1,
                "class": [{"code": "A"}],
                "a_b_items": [{"value": "top"}],
                "a_b": {"items": [{"value": "joined"}]},
                "a": {"b": {"items": [{"value": "split"}]}},
            },
        )
    )
    inferred = deepcopy(schema)

    validate_v2_schema(schema)

    assert schema == inferred
    for table in schema["tables"]:
        label = table["label"]
        assert label.isascii()
        assert label.isidentifier()
        assert not keyword.iskeyword(label)


def test_inference_closure_resolves_case_only_filename_collisions(
    tmp_path: Path,
) -> None:
    first = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "Foo": [{"value": "same"}],
                "foo": [{"value": "same"}],
            },
            "first.json",
        )
    )
    reordered = infer_v2_schema_from_data(
        _write(
            tmp_path,
            {
                "foo": [{"value": "same"}],
                "Foo": [{"value": "same"}],
            },
            "reordered.json",
        )
    )

    assert reordered == first
    validate_v2_schema(first)
    labels = [table["label"] for table in first["tables"]]
    assert len({label.casefold() for label in labels}) == len(labels)
