"""API-input schema recognition, validation, and per-port shredding.

Layered:

1. ``validate_v2_schema`` / path helpers — pure functions, exhaustive
   edge cases.
2. ``shred_to_buffers`` — algorithm correctness on rating-shaped
   nested-array data (including ancestor-column distribution).
3. ``build_test_api_input_snapshots`` + ``load_v2_api_source`` — the input
   snapshot store round-trip (build every emitting table, lease it back).
"""

from __future__ import annotations

import json
import keyword
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from haute._api_input_schema import (
    ApiInputSchemaError,
    parse_column_path,
    parse_table_path,
    validate_v2_schema,
)
from haute._json_shred._cache import load_v2_api_source
from haute._json_shred._shred import shred_to_buffers
from haute._sandbox import set_project_root
from tests.conftest import build_test_api_input_snapshots

# ─── Path helpers ─────────────────────────────────────────────────


def test_parse_table_path_root() -> None:
    assert parse_table_path("$[:]") == ()


def test_parse_table_path_one_level() -> None:
    # [:] is the only accepted array selector; segments carry is_array.
    assert parse_table_path("$[:].drivers[:]") == (("drivers", True),)


def test_parse_table_path_two_levels() -> None:
    assert parse_table_path("$[:].drivers[:].licenses[:]") == (
        ("drivers", True),
        ("licenses", True),
    )


def test_parse_table_path_rejects_malformed() -> None:
    with pytest.raises(ApiInputSchemaError):
        parse_table_path("drivers[:]")  # no $[:] prefix
    with pytest.raises(ApiInputSchemaError):
        parse_table_path("$[:].drivers")  # missing [:] at iteration depth
    with pytest.raises(ApiInputSchemaError):
        parse_table_path("$[:]..drivers[:]")  # empty segment


def test_parse_column_path_simple() -> None:
    assert parse_column_path("$[:].drivers[:].driver_id", "$[:].drivers[:]") == "driver_id"


def test_parse_column_path_nested_dotted() -> None:
    assert (
        parse_column_path(
            "$[:].drivers[:].profile.age",
            "$[:].drivers[:]",
        )
        == "profile.age"
    )


def test_parse_column_path_rejects_unrelated() -> None:
    with pytest.raises(ApiInputSchemaError):
        parse_column_path("$[:].vehicles[:].id", "$[:].drivers[:]")


def test_validate_rejects_non_identifier_dot_key() -> None:
    # Charset tightening: a non-identifier dot key (digit-leading) is rejected,
    # where the old loose INPUT parser accepted any non-`[`/`]` key.
    cfg = {
        "path": "data.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "columns": [
                    {"name": "y2024", "path": "$[:].2024", "type": "int", "selected": True},
                ],
            },
        ],
    }
    with pytest.raises(ApiInputSchemaError):
        validate_v2_schema(cfg)


# ─── validate_v2_schema ────────────────────────────────────────────


def _minimal_v2() -> dict[str, Any]:
    return {
        "path": "data.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def test_validate_v2_schema_accepts_minimal() -> None:
    validate_v2_schema(_minimal_v2())  # raises on failure


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        pytest.param("emit", 1, "v2 tables[0].emit must be a bool", id="emit-int"),
        pytest.param("emit", "true", "v2 tables[0].emit must be a bool", id="emit-string"),
        pytest.param(
            "selected",
            0,
            "v2 tables[0].columns[0].selected must be a bool",
            id="selected-int",
        ),
        pytest.param(
            "selected",
            "false",
            "v2 tables[0].columns[0].selected must be a bool",
            id="selected-string",
        ),
        pytest.param(
            "status",
            None,
            "v2 tables[0].columns[0].status must be Confirmed or Inferred",
            id="status-null",
        ),
        pytest.param(
            "status",
            True,
            "v2 tables[0].columns[0].status must be Confirmed or Inferred",
            id="status-bool",
        ),
        pytest.param(
            "status",
            "Pending",
            "v2 tables[0].columns[0].status must be Confirmed or Inferred",
            id="status-unknown",
        ),
        pytest.param(
            "status",
            ["Confirmed"],
            "v2 tables[0].columns[0].status must be Confirmed or Inferred",
            id="status-list",
        ),
    ],
)
def test_validate_v2_schema_rejects_invalid_declared_scalars(
    field: str, value: Any, message: str
) -> None:
    cfg = _minimal_v2()
    target = cfg["tables"][0] if field == "emit" else cfg["tables"][0]["columns"][0]
    target[field] = value

    with pytest.raises(ApiInputSchemaError, match=f"^{re.escape(message)}$"):
        validate_v2_schema(cfg)


@pytest.mark.parametrize("emit", [False, True])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("status", ["Confirmed", "Inferred"])
def test_validate_v2_schema_accepts_declared_scalars(
    emit: bool, selected: bool, status: str
) -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["emit"] = emit
    cfg["tables"][0]["columns"][0]["selected"] = selected
    cfg["tables"][0]["columns"][0]["status"] = status

    validate_v2_schema(cfg)


def test_validate_v2_schema_accepts_missing_declared_scalars() -> None:
    cfg = _minimal_v2()
    del cfg["tables"][0]["emit"]
    del cfg["tables"][0]["columns"][0]["selected"]
    del cfg["tables"][0]["columns"][0]["status"]

    validate_v2_schema(cfg)


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("quote id", id="embedded-space"),
        pytest.param("1st_frame", id="leading-digit"),
        pytest.param("with-hyphen", id="hyphen"),
        pytest.param("driver\tclaims", id="embedded-tab"),
    ],
)
def test_validate_v2_schema_rejects_labels_that_are_not_python_parameter_names(
    label: str,
) -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["label"] = label

    with pytest.raises(ApiInputSchemaError) as exc_info:
        validate_v2_schema(cfg)

    detail = str(exc_info.value)
    assert label in detail or repr(label) in detail


@pytest.mark.parametrize(
    "label",
    keyword.kwlist,
)
def test_validate_v2_schema_rejects_every_hard_python_keyword(label: str) -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["label"] = label

    with pytest.raises(ApiInputSchemaError) as exc_info:
        validate_v2_schema(cfg)

    detail = str(exc_info.value)
    assert repr(label) in detail or exc_info.value.context.get("label") == label


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("caf\u00e9", id="latin-accent"),
        pytest.param("\u53d8\u91cf", id="cjk"),
    ],
)
def test_validate_v2_schema_rejects_valid_unicode_identifiers_with_ascii_rule(
    label: str,
) -> None:
    assert label.isidentifier()
    assert not label.isascii()
    cfg = _minimal_v2()
    cfg["tables"][0]["label"] = label

    with pytest.raises(ApiInputSchemaError) as exc_info:
        validate_v2_schema(cfg)

    detail = str(exc_info.value)
    assert label in detail
    assert "ascii" in detail.casefold()


def test_validate_v2_schema_rejects_non_nfkc_unicode_identifier_with_ascii_rule() -> None:
    label = "\N{KELVIN SIGN}elvin"
    assert label.isidentifier()
    assert unicodedata.normalize("NFKC", label) == "Kelvin"
    cfg = _minimal_v2()
    cfg["tables"][0]["label"] = label

    with pytest.raises(ApiInputSchemaError) as exc_info:
        validate_v2_schema(cfg)

    detail = str(exc_info.value)
    assert label in detail
    assert "ascii" in detail.casefold()


@pytest.mark.parametrize(
    "label",
    ["quotes", "driver_claims", "_private", "MixedCase", "match", "case", "type", "_"],
)
def test_validate_v2_schema_accepts_ascii_identifiers_and_soft_keywords(label: str) -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["label"] = label

    validate_v2_schema(cfg)


def test_validate_rejects_missing_label() -> None:
    cfg = _minimal_v2()
    del cfg["tables"][0]["label"]
    with pytest.raises(ApiInputSchemaError, match="label"):
        validate_v2_schema(cfg)


# ─── Blank name/path: the backend twin of the frontend readV2 fix ───
#
# The frontend `readV2` codec (frontend/src/panels/editors/apiInputSchema.ts)
# used to SILENTLY DROP tables with a blank `path` and columns with a
# blank `name`/`path` — silent data loss. The fix makes it KEEP them and
# surface them for repair. The backend is the mirror: it must never
# silently drop these either — it rejects them LOUDLY here, so a config
# the user couldn't repair can never reach cache-build / execute. These
# pin that loud rejection (contract parity with the editor's validation).


def test_validate_rejects_blank_table_path() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["path"] = ""
    with pytest.raises(ApiInputSchemaError, match=r"tables\[0\]\.path is missing"):
        validate_v2_schema(cfg)


def test_validate_rejects_blank_column_name() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["name"] = ""
    with pytest.raises(ApiInputSchemaError, match=r"columns\[0\]\.name is missing"):
        validate_v2_schema(cfg)


def test_validate_rejects_blank_column_path() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["path"] = ""
    with pytest.raises(ApiInputSchemaError, match=r"columns\[0\]\.path is missing"):
        validate_v2_schema(cfg)


def test_validate_rejects_duplicate_table_labels() -> None:
    cfg = _minimal_v2()
    cfg["tables"].append(dict(cfg["tables"][0]))
    cfg["tables"][1]["path"] = "$[:].drivers[:]"
    cfg["tables"][1]["columns"] = [
        {"name": "id", "path": "$[:].drivers[:].id", "type": "int", "selected": True},
    ]
    # Both tables still labelled "policies"
    with pytest.raises(ApiInputSchemaError, match="appears more than once"):
        validate_v2_schema(cfg)


def test_validate_rejects_duplicate_column_names_within_table() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"].append(
        {"name": "policy_id", "path": "$[:].policy_number", "type": "str", "selected": True},
    )
    with pytest.raises(ApiInputSchemaError, match="duplicate column name"):
        validate_v2_schema(cfg)


def test_validate_accepts_same_column_name_across_tables() -> None:
    """Per §4d, column.name uniqueness is per-table; cross-table reuse OK."""
    cfg = _minimal_v2()
    cfg["tables"].append(
        {
            "path": "$[:].drivers[:]",
            "label": "drivers",
            "emit": True,
            "columns": [
                {
                    "name": "policy_id",
                    "path": "$[:].drivers[:].policy_id",
                    "type": "int",
                    "selected": True,
                },
            ],
        },
    )
    validate_v2_schema(cfg)


def test_validate_rejects_row_id_column_pointing_at_nothing() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["row_id_column"] = "nonexistent"
    with pytest.raises(ApiInputSchemaError, match="row_id_column"):
        validate_v2_schema(cfg)


def test_validate_accepts_valid_row_id_column() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["row_id_column"] = "policy_id"
    validate_v2_schema(cfg)


def test_validate_rejects_empty_levels_list() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["levels"] = []
    with pytest.raises(ApiInputSchemaError, match="levels is empty"):
        validate_v2_schema(cfg)


def test_validate_accepts_null_levels() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["levels"] = None
    validate_v2_schema(cfg)


def test_validate_accepts_non_empty_levels() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["type"] = "str"
    cfg["tables"][0]["columns"][0]["levels"] = ["A", "B", None]
    validate_v2_schema(cfg)


# ─── shred_to_buffers algorithm ───────────────────────────────────


def _rating_v2() -> dict[str, Any]:
    """Three-level nested schema modelled on the rating example."""
    return {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[:].drivers[:].driver_id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "age_band",
                        "path": "$[:].drivers[:].age_band",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[:].drivers[:].licenses[:]",
                "label": "licenses",
                "emit": True,
                "columns": [
                    {
                        "name": "license_id",
                        "path": "$[:].drivers[:].licenses[:].license_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def _rating_records() -> list[dict[str, Any]]:
    """Two policies; one has two drivers (with 1 + 2 licenses respectively),
    the other has one driver with no licenses. Total counts:
    - policies: 2 rows
    - drivers: 3 rows
    - licenses: 3 rows
    """
    return [
        {
            "policy_id": 1001,
            "drivers": [
                {
                    "driver_id": 1,
                    "age_band": "30-59",
                    "licenses": [{"license_id": 100}],
                },
                {
                    "driver_id": 2,
                    "age_band": "60+",
                    "licenses": [{"license_id": 101}, {"license_id": 102}],
                },
            ],
        },
        {
            "policy_id": 1002,
            "drivers": [
                {"driver_id": 3, "age_band": "60+", "licenses": []},
            ],
        },
    ]


def test_shred_to_buffers_row_counts_match_iteration_depths() -> None:
    cfg = _rating_v2()
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert len(buffers["policies"]) == 2
    assert len(buffers["drivers"]) == 3
    assert len(buffers["licenses"]) == 3


def test_shred_to_buffers_extracts_per_depth_columns() -> None:
    cfg = _rating_v2()
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert buffers["policies"] == [{"policy_id": 1001}, {"policy_id": 1002}]
    assert buffers["drivers"][0] == {"driver_id": 1, "age_band": "30-59"}
    assert buffers["licenses"][2] == {"license_id": 102}


def test_shred_to_buffers_skips_non_emitting_tables() -> None:
    cfg = _rating_v2()
    cfg["tables"][1]["emit"] = False  # drivers off
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert "drivers" not in buffers
    # But licenses still gets shredded because it's emit-true and we
    # transparently walk through the drivers depth to reach it.
    assert len(buffers["licenses"]) == 3


def test_shred_to_buffers_handles_missing_leaves_as_null() -> None:
    """A column whose JSONPath doesn't resolve produces ``None`` (per the
    plan §4d edge case — "a path that doesn't exist needs to produce a
    null value")."""
    cfg = _rating_v2()
    records = [
        {"policy_id": 999, "drivers": [{"driver_id": 1}]},  # age_band missing
    ]
    buffers = shred_to_buffers(records, cfg)
    assert buffers["drivers"] == [{"driver_id": 1, "age_band": None}]


def test_shred_to_buffers_two_columns_from_same_source_get_same_values() -> None:
    """Per §4d: two columns with the same `path` but different `name`
    emit identical row values to both."""
    cfg = {
        "path": "x.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "id_copy",
                        "path": "$[:].id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }
    buffers = shred_to_buffers([{"id": 7}], cfg)
    assert buffers["policies"] == [{"policy_id": 7, "id_copy": 7}]


# ─── Disk round-trip: build + load + validity ─────────────────────


def _write_rating_json(path: Path) -> None:
    path.write_text(json.dumps(_rating_records()), encoding="utf-8")


def test_build_writes_one_input_snapshot_per_emit_table(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    set_project_root(tmp_path)

    generations = build_test_api_input_snapshots(data_path, _rating_v2())

    assert len(generations) == 3
    for generation in generations.values():
        assert generation.data_paths
        for part in generation.data_paths:
            assert Path(part).suffix == ".parquet"


def test_load_v2_api_source_returns_one_lazyframe_per_emit_table(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    set_project_root(tmp_path)
    config = _rating_v2()
    build_test_api_input_snapshots(data_path, config)

    bundle = load_v2_api_source(str(data_path), config, read_snapshots=True)
    assert set(bundle.keys()) == {"policies", "drivers", "licenses"}

    drivers_df = bundle["drivers"].collect()
    assert drivers_df.height == 3
    assert sorted(drivers_df.columns) == ["age_band", "driver_id"]
    assert drivers_df["driver_id"].to_list() == [1, 2, 3]


# ─── W1 — ancestor-leaf duplicate mapping ─────────────────────────
#
# A column whose path sits under a *proper ancestor* of its table's path
# is an "ancestor column": its value lives higher in the JSON tree than
# the table iterates, and distributes (broadcasts) over every descendant
# row the table emits. Distribution is walk-time — the ancestor value is
# carried down the shred's recursion and filled at row emission — NOT a
# post-shred join (STATE_OF_PLAY §7 (b), ruled 2026-06-16).


def test_parse_column_path_accepts_ancestor_one_level() -> None:
    # root-level column on a one-deep table: ancestor one level up.
    assert parse_column_path("$[:].policy_id", "$[:].drivers[:]") == "policy_id"


def test_parse_column_path_accepts_ancestor_two_levels() -> None:
    # root-level column on a two-deep table: ancestor two levels up.
    assert parse_column_path("$[:].policy_id", "$[:].drivers[:].licenses[:]") == "policy_id"
    # drivers-level column on the two-deep licenses table: ancestor one up.
    assert (
        parse_column_path("$[:].drivers[:].driver_id", "$[:].drivers[:].licenses[:]") == "driver_id"
    )


def test_parse_column_path_accepts_ancestor_nested_dotted_leaf() -> None:
    # ancestor column whose leaf walks into a nested object at the
    # ancestor's depth (no array crossing).
    assert parse_column_path("$[:].meta.broker", "$[:].drivers[:]") == "meta.broker"


def test_parse_column_path_rejects_column_deeper_than_table() -> None:
    """A column whose own array-iteration depth is *deeper* than its table
    crosses an array the table doesn't iterate — that is not an ancestor
    (nor a descendant leaf), so it is rejected."""
    with pytest.raises(ApiInputSchemaError):
        parse_column_path("$[:].drivers[:].licenses[:].license_id", "$[:].drivers[:]")


def test_parse_column_path_rejects_divergent_ancestor_sibling() -> None:
    """A column rooted in a sibling branch (not a prefix of the table path)
    stays rejected even though it is shallower — it is not an ancestor."""
    with pytest.raises(ApiInputSchemaError):
        parse_column_path("$[:].vehicles[:].vin", "$[:].drivers[:].licenses[:]")


def test_validate_v2_schema_accepts_ancestor_column() -> None:
    cfg = _rating_v2()
    # policy_id lives at root ($[:]); attach it to the drivers table
    # ($[:].drivers[:]) as an ancestor column.
    cfg["tables"][1]["columns"].append(
        {
            "name": "policy_id",
            "path": "$[:].policy_id",
            "type": "int",
            "selected": True,
        }
    )
    validate_v2_schema(cfg)  # must not raise


def test_shred_distributes_ancestor_value_over_descendant_rows() -> None:
    """An ancestor column fills every descendant row with the ancestor
    instance's value — across one *and* two levels of nesting."""
    cfg = _rating_v2()
    # drivers gets policy_id (one level up).
    cfg["tables"][1]["columns"].append(
        {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
    )
    # licenses gets BOTH policy_id (two up) and driver_id (one up).
    cfg["tables"][2]["columns"].extend(
        [
            {
                "name": "policy_id",
                "path": "$[:].policy_id",
                "type": "int",
                "selected": True,
            },
            {
                "name": "driver_id",
                "path": "$[:].drivers[:].driver_id",
                "type": "int",
                "selected": True,
            },
        ]
    )
    buffers = shred_to_buffers(_rating_records(), cfg)

    # drivers: 3 rows, each carrying its policy's id and its own id.
    assert [r["policy_id"] for r in buffers["drivers"]] == [1001, 1001, 1002]
    assert [r["driver_id"] for r in buffers["drivers"]] == [1, 2, 3]

    # licenses: 3 rows; each carries its policy (2 up) and driver (1 up).
    assert buffers["licenses"] == [
        {"license_id": 100, "policy_id": 1001, "driver_id": 1},
        {"license_id": 101, "policy_id": 1001, "driver_id": 2},
        {"license_id": 102, "policy_id": 1001, "driver_id": 2},
    ]


def test_shred_ancestor_column_emits_only_for_existing_descendant_rows() -> None:
    """Distribution lands on every descendant row that *exists* — a parent
    with no descendant instances contributes no rows (so policy 1002, whose
    only driver has no licenses, never appears in the licenses buffer)."""
    cfg = _rating_v2()
    cfg["tables"][2]["columns"].append(
        {"name": "policy_id", "path": "$[:].policy_id", "type": "int", "selected": True}
    )
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert [r["policy_id"] for r in buffers["licenses"]] == [1001, 1001, 1001]
    assert 1002 not in [r["policy_id"] for r in buffers["licenses"]]


def test_shred_scalar_array_table_distributes_ancestor_column() -> None:
    """A scalar-array child table (a ``$value`` column) also receives
    ancestor columns, distributed over its elements alongside the scalar."""
    cfg = {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:].coverages[:]",
                "label": "coverages",
                "emit": True,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].coverages[:].$value",
                        "type": "str",
                        "selected": True,
                    },
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }
    records = [
        {"policy_id": 1, "coverages": ["TPFT", "comprehensive"]},
        {"policy_id": 2, "coverages": ["TPO"]},
    ]
    buffers = shred_to_buffers(records, cfg)
    assert buffers["coverages"] == [
        {"value": "TPFT", "policy_id": 1},
        {"value": "comprehensive", "policy_id": 1},
        {"value": "TPO", "policy_id": 2},
    ]


def test_shred_object_table_ignores_ancestor_scalar_value_column() -> None:
    cfg = {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:].items[:]",
                "label": "items",
                "emit": True,
                "columns": [
                    {"name": "name", "path": "$[:].items[:].name", "type": "str", "selected": True},
                    {"name": "root_value", "path": "$[:].$value", "type": "str", "selected": True},
                ],
            },
        ],
    }

    assert shred_to_buffers([{"items": [{"name": "kept"}]}], cfg) == {
        "items": [{"name": "kept", "root_value": None}],
    }
