"""OUTPUT assembler: mapping validation, assembly, pruning and rendering.

The algorithm is specified in specs/json-shredding ("OUTPUT assembly").
"""

from __future__ import annotations

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._jsonpath import _Seg
from haute._node_apply import assemble_output_from_config
from haute._output_assembler import (
    OutputMappingSchemaError,
    OutputNestingKeyError,
    _assemble_document,
    _index_rows,
    _limit_level_plan,
    _OutputAssemblyProgress,
    _parse_output_path,
    _prune,
    assemble_output_from_mapping,
    is_active_mapping_entry,
    render_output_document,
    validate_v2_output_mapping,
)
from haute.errors import HauteError
from tests._execution_faults import FaultInjectingExecutionContext

# ─── OutputMappingSchemaError ──────────────────────────────────────


def test_output_mapping_error_is_haute_error() -> None:
    err = OutputMappingSchemaError("bad mapping", output_path="$.a")
    assert isinstance(err, HauteError)
    assert "bad mapping" in str(err)
    assert "output_path=$.a" in str(err)


def test_output_nesting_key_error_requires_keyword_context() -> None:
    with pytest.raises(TypeError):
        OutputNestingKeyError(  # type: ignore[misc]
            "bad nesting key",
            "frame",
            "$[:].id",
            "$[:].id",
        )


# ─── Output-path parser — the [:]-only conventional-JSONPath subset ───


def test_parse_output_path_segments() -> None:
    p = _parse_output_path("$[:].drivers[:].name")
    assert p.segments == (_Seg("drivers", True), _Seg("name", False))

    q = _parse_output_path("$[:].obj[:].attrs.X")
    assert q.segments == (_Seg("obj", True), _Seg("attrs", False), _Seg("X", False))


@pytest.mark.parametrize(
    "bad",
    [
        "$[:].drivers[0]",  # index selector
        "$[:].drivers[0:2]",  # range slice
        "$[:].drivers[?(@.age>21)]",  # filter
        "$[:]..name",  # descendant
        "$[:].*",  # wildcard not on array
        "$[:].drivers[*]",  # array wildcard (only [:] accepted)
        "$[:].drivers.:.name",  # the dropped dot form
        "drivers.name",  # no root
        "$[:]",  # names no leaf
    ],
)
def test_parse_output_path_rejects_unsupported_selectors(bad: str) -> None:
    with pytest.raises(OutputMappingSchemaError):
        _parse_output_path(bad)


def test_parse_output_path_rejects_non_array_root() -> None:
    with pytest.raises(OutputMappingSchemaError, match="must start with"):
        _parse_output_path("$.policy_id")


def test_validate_v2_output_mapping_requires_canonical_root() -> None:
    mapping = [
        {
            "source_port": "p",
            "source_column": "a",
            "output_path": "$.values[:].a",
            "enabled": True,
        }
    ]
    with pytest.raises(OutputMappingSchemaError, match="must start with"):
        validate_v2_output_mapping(mapping)


# ─── Assembler — descend the prefix tree, nest by ancestor key ───
#
# Each frame's columns are its output paths; the assembler nests children under
# parents by the ancestor keys the child carries (the inverse of the W1 shred).
# Sibling branches are assembled independently — never cross-joined.


def test_assemble_parent_and_child_array() -> None:
    # The canonical shred → assemble shape: drivers carry the ancestor key id and
    # nest under their policy; the parent de-dups, the drivers cascade into the
    # array. (The commit-9 round-trip invariant in miniature.)
    field_frames = {
        "policies": pl.LazyFrame({"$[:].id": [1], "$[:].policy": ["P"]}),
        "drivers": pl.LazyFrame({"$[:].id": [1, 1], "$[:].drivers[:].name": ["a", "b"]}),
    }
    assert _assemble_document(field_frames) == [
        {"id": 1, "policy": "P", "drivers": [{"name": "a"}, {"name": "b"}]}
    ]


def test_assemble_empty_child_array_is_omitted() -> None:
    # A policy with no matching driver rows emits an empty array, which S21 omits:
    # the drivers key is absent entirely.
    field_frames = {
        "policies": pl.LazyFrame({"$[:].id": [1, 2], "$[:].policy": ["P", "Q"]}),
        "drivers": pl.LazyFrame(
            {"$[:].id": [1], "$[:].drivers[:].name": ["a"]}
        ),  # only policy 1 has a driver
    }
    assert _assemble_document(field_frames) == [
        {"id": 1, "policy": "P", "drivers": [{"name": "a"}]},
        {"id": 2, "policy": "Q"},  # no drivers key
    ]


def test_assemble_empty_object_is_omitted() -> None:
    # Empty collections carry no data (Nick's ruling): an all-null nested object
    # is omitted, not emitted as {}.
    field_frames = {
        "p": pl.LazyFrame({"$[:].id": [1], "$[:].meta.note": [None]}),
    }
    assert _assemble_document(field_frames) == [{"id": 1}]  # meta omitted, not {}


def test_assemble_sibling_arrays_do_not_cross_join() -> None:
    # THE structural obstacle: drivers (→ licenses) and vehicles are sibling
    # branches sharing only the ancestor key policy_id. The tree recursion nests
    # each branch independently — so the policy keeps its 2 drivers and 2 vehicles
    # rather than the 2×2 (or 2×3) denormalised cross-product the data model calls
    # arithmetically meaningless. Licenses nest correctly per driver.
    field_frames = {
        "policy": pl.LazyFrame({"$[:].policy_id": [1001]}),
        "drivers": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001],
                "$[:].drivers[:].driver_id": [1, 2],
                "$[:].drivers[:].main": [True, False],
            }
        ),
        "licenses": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001, 1001],
                "$[:].drivers[:].driver_id": [1, 1, 2],
                "$[:].drivers[:].licenses[:].license_type": ["UK", "EU", "EU"],
            }
        ),
        "vehicles": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001],
                "$[:].vehicles[:].vehicle_id": [1, 2],
                "$[:].vehicles[:].engine_size": ["small", "medium"],
            }
        ),
    }
    assert _assemble_document(field_frames) == [
        {
            "policy_id": 1001,
            "drivers": [
                {
                    "driver_id": 1,
                    "main": True,
                    "licenses": [{"license_type": "UK"}, {"license_type": "EU"}],
                },
                {"driver_id": 2, "main": False, "licenses": [{"license_type": "EU"}]},
            ],
            "vehicles": [
                {"vehicle_id": 1, "engine_size": "small"},
                {"vehicle_id": 2, "engine_size": "medium"},
            ],
        }
    ]


def test_assemble_data_model_example_round_trip() -> None:
    # The canonical _DATA_MODEL.md fixture (Policy 1001: 2 drivers × {2,2}
    # licenses + 3 vehicles — the 2×2×3=12 anti-pattern; Policy 1002: simple).
    # Shredding it would produce exactly these four per-table frames; assembling
    # with the mirrored mapping must reproduce the document — the round-trip
    # invariant on the structure that forces the obstacle.
    field_frames = {
        "policies": pl.LazyFrame({"$[:].policy_id": [1001, 1002]}),
        "drivers": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001, 1002],
                "$[:].drivers[:].driver_id": [1, 2, 1],
                "$[:].drivers[:].main": [True, False, True],
                "$[:].drivers[:].age_band": ["60+", "30-59", "30-59"],
            }
        ),
        "licenses": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001, 1001, 1001, 1002],
                "$[:].drivers[:].driver_id": [1, 1, 2, 2, 1],
                "$[:].drivers[:].licenses[:].license_id": [1, 2, 1, 2, 1],
                "$[:].drivers[:].licenses[:].issuing_authority": [
                    "GB",
                    "IE",
                    "PL",
                    "PL",
                    "GB",
                ],
                "$[:].drivers[:].licenses[:].license_type": [
                    "UK",
                    "EU",
                    "EU",
                    "worldwide",
                    "UK",
                ],
            }
        ),
        "vehicles": pl.LazyFrame(
            {
                "$[:].policy_id": [1001, 1001, 1001, 1002],
                "$[:].vehicles[:].vehicle_id": [1, 2, 3, 1],
                "$[:].vehicles[:].engine_size": [
                    "small",
                    "medium",
                    "large",
                    "medium",
                ],
                "$[:].vehicles[:].class_of_use": [
                    "domestic-only",
                    "includes business",
                    "domestic-only",
                    "domestic-only",
                ],
            }
        ),
    }

    def _driver(did: int, main: bool, age: str, lics: list[dict[str, object]]) -> dict:
        return {
            "driver_id": did,
            "main": main,
            "age_band": age,
            "licenses": lics,
        }

    def _lic(lid: int, auth: str, ltype: str) -> dict[str, object]:
        return {"license_id": lid, "issuing_authority": auth, "license_type": ltype}

    def _veh(vid: int, eng: str, use: str) -> dict[str, object]:
        return {"vehicle_id": vid, "engine_size": eng, "class_of_use": use}

    assert _assemble_document(field_frames) == [
        {
            "policy_id": 1001,
            "drivers": [
                _driver(1, True, "60+", [_lic(1, "GB", "UK"), _lic(2, "IE", "EU")]),
                _driver(2, False, "30-59", [_lic(1, "PL", "EU"), _lic(2, "PL", "worldwide")]),
            ],
            "vehicles": [
                _veh(1, "small", "domestic-only"),
                _veh(2, "medium", "includes business"),
                _veh(3, "large", "domestic-only"),
            ],
        },
        {
            "policy_id": 1002,
            "drivers": [_driver(1, True, "30-59", [_lic(1, "GB", "UK")])],
            "vehicles": [_veh(1, "medium", "domestic-only")],
        },
    ]


# ─── Public boundary — {frames + outputMapping} → document, and validation ───


def test_assemble_from_mapping_renames_duplicates_and_skips_disabled() -> None:
    # The public entry: each entry renames a source column to its output path; a
    # column mapped to two paths (premium) is duplicated; a disabled entry
    # (alias) is skipped; drivers nest under the policy by the ancestor key.
    frames = {
        "policy": pl.LazyFrame({"policy_id": [1001], "premium": [120.75]}),
        "drivers": pl.LazyFrame(
            {"policy_id": [1001, 1001], "driver_id": [1, 2], "nm": ["Ann", "Ben"]}
        ),
    }
    mapping = [
        {
            "source_port": "policy",
            "source_column": "policy_id",
            "output_path": "$[:].policy_id",
            "enabled": True,
        },
        {
            "source_port": "policy",
            "source_column": "premium",
            "output_path": "$[:].premium",
            "enabled": True,
        },
        {
            "source_port": "policy",
            "source_column": "premium",
            "output_path": "$[:].record.premium",
            "enabled": True,
        },
        {
            "source_port": "drivers",
            "source_column": "policy_id",
            "output_path": "$[:].policy_id",
            "enabled": True,
        },
        {
            "source_port": "drivers",
            "source_column": "driver_id",
            "output_path": "$[:].drivers[:].id",
            "enabled": True,
        },
        {
            "source_port": "drivers",
            "source_column": "nm",
            "output_path": "$[:].drivers[:].name",
            "enabled": True,
        },
        {
            "source_port": "drivers",
            "source_column": "nm",
            "output_path": "$[:].drivers[:].alias",
            "enabled": False,
        },
    ]
    assert assemble_output_from_mapping(frames, mapping) == [
        {
            "policy_id": 1001,
            "premium": 120.75,
            "record": {"premium": 120.75},  # the duplicated column
            "drivers": [{"id": 1, "name": "Ann"}, {"id": 2, "name": "Ben"}],
            # no "alias" — that entry was disabled
        }
    ]


def _entry(port: str, col: str, path: str, enabled: bool = True) -> dict[str, object]:
    return {
        "source_port": port,
        "source_column": col,
        "output_path": path,
        "enabled": enabled,
    }


def test_validate_accepts_a_well_formed_mapping() -> None:
    validate_v2_output_mapping(
        [
            _entry("policy", "policy_id", "$[:].policy_id"),
            _entry(
                "drivers", "policy_id", "$[:].policy_id"
            ),  # shared across ports — the join, allowed
            _entry("drivers", "driver_id", "$[:].drivers[:].driver_id"),
            _entry("drivers", "nm", "$[:].drivers[:].name"),
        ]
    )


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param(
            [
                _entry("priced", "quote_id", "$[:].quote_id"),
                _entry("priced", "premium", "$[:].premium"),
                _entry("extras", "quote_id", "$[:].quote_id"),
                _entry("extras", "discount", "$[:].discount"),
            ],
            id="two-frames-at-the-root",
        ),
        pytest.param(
            [
                _entry("policies", "policy_id", "$[:].policy_id"),
                _entry("drivers", "policy_id", "$[:].policy_id"),
                _entry("drivers", "name", "$[:].drivers[:].name"),
                _entry("licenses", "policy_id", "$[:].policy_id"),
                _entry("licenses", "country", "$[:].drivers[:].licence_country"),
            ],
            id="two-frames-at-a-nested-level",
        ),
    ],
)
def test_validate_rejects_two_frames_emitting_at_one_array_level(
    mapping: list[dict[str, object]],
) -> None:
    # One frame per array level: frames describing the same objects are joined
    # upstream, where the join is an explicit node, never inside OUTPUT.
    with pytest.raises(OutputMappingSchemaError, match="same array level") as exc_info:
        validate_v2_output_mapping(mapping)

    assert len(exc_info.value.context["source_ports"]) == 2


def test_assembly_rejects_two_frames_at_one_level_before_collecting_them() -> None:
    collected: list[str] = []

    def frame(port: str, data: dict[str, list[object]]) -> pl.LazyFrame:
        def mark(batch: pl.DataFrame) -> pl.DataFrame:
            collected.append(port)
            return batch

        return pl.LazyFrame(data).map_batches(mark)

    field_frames = {
        "priced": frame("priced", {"$[:].quote_id": ["q1"], "$[:].premium": [1.0]}),
        "extras": frame("extras", {"$[:].quote_id": ["q1"], "$[:].discount": [0.1]}),
    }

    with pytest.raises(OutputMappingSchemaError, match="same array level"):
        _assemble_document(field_frames)
    assert collected == []


def test_frames_at_different_levels_and_sibling_branches_are_valid() -> None:
    validate_v2_output_mapping(
        [
            _entry("policies", "policy_id", "$[:].policy_id"),
            _entry("drivers", "policy_id", "$[:].policy_id"),
            _entry("drivers", "name", "$[:].drivers[:].name"),
            _entry("vehicles", "policy_id", "$[:].policy_id"),
            _entry("vehicles", "make", "$[:].vehicles[:].make"),
        ]
    )


def test_validate_parses_each_distinct_active_path_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._output_assembler as assembler

    original = assembler._parse_output_path
    calls: list[str] = []

    def spy(path: str):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(assembler, "_parse_output_path", spy)
    # A child frame carries its parent's key by repeating the parent's path.
    mapping = [_entry("p", f"value_{index}", f"$[:].value_{index}") for index in range(200)]
    mapping.extend([_entry("q", "same", "$[:].value_0") for _ in range(50)])
    mapping.append(_entry("q", "leaf", "$[:].items[:].leaf"))

    assembler.validate_v2_output_mapping(mapping)

    assert len(calls) == 201


def test_validate_rejects_prefix_comparable_paths_within_a_port() -> None:
    # $[:].a is a strict prefix of $[:].a.b — a would be both a leaf and the
    # container of b. Rejected within one port (B1).
    with pytest.raises(OutputMappingSchemaError):
        validate_v2_output_mapping([_entry("p", "x", "$[:].a"), _entry("p", "y", "$[:].a.b")])


def test_validate_rejects_two_columns_on_one_path() -> None:
    # Injectivity: one port cannot map two different columns to the same path.
    with pytest.raises(OutputMappingSchemaError):
        validate_v2_output_mapping([_entry("p", "x", "$[:].v"), _entry("p", "y", "$[:].v")])


def test_validate_allows_equal_nonidentical_column_names_on_one_path() -> None:
    first = b"same_column".decode()
    second = b"same_column".decode()
    assert first == second
    assert first is not second

    validate_v2_output_mapping([_entry("p", first, "$[:].v"), _entry("p", second, "$[:].v")])


def test_validate_rejects_unsupported_selector() -> None:
    with pytest.raises(OutputMappingSchemaError):
        validate_v2_output_mapping([_entry("p", "x", "$[:].drivers[0].name")])


def test_validate_skips_disabled_entries() -> None:
    # A disabled entry is off, so its (here prefix-conflicting) path is not checked.
    validate_v2_output_mapping(
        [_entry("p", "x", "$[:].a"), _entry("p", "y", "$[:].a.b", enabled=False)]
    )


def test_validate_rejects_divergent_array_branches_before_frame_collection() -> None:
    class CollectSpy:
        def collect(self) -> None:
            pytest.fail("structural validation must run before collection")

    mapping = [_entry("p", "id", "$[:].left[:].id"), _entry("p", "id", "$[:].right[:].id")]
    with pytest.raises(OutputMappingSchemaError, match="divergent"):
        assemble_output_from_mapping({"p": CollectSpy()}, mapping)  # type: ignore[arg-type]


def test_output_materialisation_uses_active_execution_context() -> None:
    """Terminal assembly remains observable and cancellable after Polars collection."""
    fault_points: list[str] = []
    context = FaultInjectingExecutionContext(
        operation="test_output_assembly",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_sampler=lambda: 1,
        fault_injector=lambda point: fault_points.append(point.name),
    )

    with context.stage("output_assembly"):
        result = _assemble_document({"port": pl.LazyFrame({"$[:].value": [1, 2]})})

    summary = context.metrics.summary(
        operation=context.operation,
        profile=context.profile,
    )
    assert result == [{"value": 1}, {"value": 2}]
    assert summary.n_collects == 1
    assert "output_assembly_rows" in fault_points
    assert "output_assembly_build" in fault_points


def test_output_rendering_checkpoints_python_materialisation() -> None:
    fault_points: list[str] = []
    context = FaultInjectingExecutionContext(
        operation="test_output_render",
        profile=ExecutionProfile.DEPLOY_BATCH,
        memory_sampler=lambda: 1,
        fault_injector=lambda point: fault_points.append(point.name),
    )

    with context.stage("output_render"):
        result = render_output_document(pl.DataFrame({"value": [1, 2]}))

    assert result == [{"value": 1}, {"value": 2}]
    assert "output_render_rows" in fault_points
    assert "output_render_prune" in fault_points


def test_validate_rejects_divergent_array_branches_in_descending_order() -> None:
    mapping = [
        _entry("p", "right_id", "$[:].right[:].id"),
        _entry("p", "left_id", "$[:].left[:].id"),
    ]

    with pytest.raises(OutputMappingSchemaError, match="divergent"):
        validate_v2_output_mapping(mapping)


def test_validate_checks_divergent_branches_separated_by_a_root_leaf() -> None:
    mapping = [
        _entry("p", "left", "$[:].aaa[:].value"),
        _entry("p", "middle", "$[:].middle"),
        _entry("p", "right", "$[:].zzz[:].value"),
    ]

    with pytest.raises(OutputMappingSchemaError, match="divergent"):
        validate_v2_output_mapping(mapping)


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [
        ("$[:].items[:].id", "$[:].items[:].subitems[:].value"),
        ("$[:].items[:].subitems[:].value", "$[:].items[:].id"),
    ],
)
def test_validate_allows_nested_array_prefix_in_either_order(
    first_path: str,
    second_path: str,
) -> None:
    validate_v2_output_mapping(
        [
            _entry("p", "first", first_path),
            _entry("p", "second", second_path),
        ]
    )


def test_validate_allows_multiple_columns_at_one_array_prefix() -> None:
    validate_v2_output_mapping(
        [_entry("p", "id", "$[:].items[:].id"), _entry("p", "name", "$[:].items[:].name")]
    )


@pytest.mark.parametrize(
    ("parent_id", "child_id", "expected_frame"),
    [(None, 1, "parent"), (1, None, "child")],
)
def test_assemble_rejects_null_nesting_key_on_either_side(
    parent_id: int | None, child_id: int | None, expected_frame: str
) -> None:
    frames = {
        "parent": pl.LazyFrame({"$[:].id": [parent_id]}),
        "child": pl.LazyFrame({"$[:].id": [child_id], "$[:].items[:].value": ["v"]}),
    }
    with pytest.raises(OutputNestingKeyError) as exc_info:
        _assemble_document(frames)
    assert exc_info.value.code == "output_nesting_key_null"
    assert exc_info.value.context == {
        "frame": expected_frame,
        "output_path": "$[:].id",
        "key": "$[:].id",
    }


def test_assemble_rejects_one_null_component_of_composite_nesting_key() -> None:
    frames = {
        "parent": pl.LazyFrame({"$[:].a": [1], "$[:].b": [None]}),
        "child": pl.LazyFrame({"$[:].a": [1], "$[:].b": [2], "$[:].items[:].value": ["v"]}),
    }
    with pytest.raises(OutputNestingKeyError) as exc_info:
        _assemble_document(frames)
    assert exc_info.value.context["output_path"] == "$[:].b"


def test_assemble_allows_null_scalar_payload_that_is_not_a_nesting_key() -> None:
    frames = {
        "parent": pl.LazyFrame({"$[:].id": [1], "$[:].optional": [None]}),
        "child": pl.LazyFrame({"$[:].id": [1], "$[:].items[:].value": ["v"]}),
    }
    assert _assemble_document(frames) == [{"id": 1, "items": [{"value": "v"}]}]


def test_assemble_allows_null_scalar_payload_at_a_leaf_array_level() -> None:
    frames = {
        "item": pl.LazyFrame(
            {
                "$[:].items[:].id": [1],
                "$[:].items[:].optional": [None],
            }
        )
    }

    assert _assemble_document(frames) == [{"items": [{"id": 1}]}]


@pytest.mark.parametrize(
    ("parent_item_id", "child_item_id", "expected_frame"),
    [(None, 7, "item"), (7, None, "detail")],
)
def test_assemble_rejects_null_key_across_a_synthesised_child_level(
    parent_item_id: int | None,
    child_item_id: int | None,
    expected_frame: str,
) -> None:
    frames = {
        "detail": pl.LazyFrame(
            {
                "$[:].root_id": [1],
                "$[:].items[:].item_id": [child_item_id],
                "$[:].items[:].subitems[:].details[:].value": ["v"],
            }
        ),
        "item": pl.LazyFrame(
            {
                "$[:].root_id": [1],
                "$[:].items[:].item_id": [parent_item_id],
            }
        ),
    }

    with pytest.raises(OutputNestingKeyError) as exc_info:
        _assemble_document(frames)

    assert exc_info.value.context == {
        "frame": expected_frame,
        "output_path": "$[:].items[:].item_id",
        "key": "$[:].items[:].item_id",
    }


def test_assemble_ignores_a_nesting_key_the_child_frame_does_not_carry() -> None:
    # The child frame has no quote_id column: that is absence, not a null key,
    # so it raises nothing and its rows nest under every parent object.
    frames = {
        "quotes": pl.LazyFrame({"$[:].quote_id": [1, None]}),
        "drivers": pl.LazyFrame({"$[:].drivers[:].name": ["Ann"]}),
    }

    assert _assemble_document(frames) == [
        {"quote_id": 1, "drivers": [{"name": "Ann"}]},
        {"drivers": [{"name": "Ann"}]},
    ]


_LICENCES_WITHOUT_QUOTE_ID = [
    _entry("quotes", "quote_id", "$[:].quote_id"),
    _entry("drivers", "quote_id", "$[:].quote_id"),
    _entry("drivers", "driver_id", "$[:].drivers[:].driver_id"),
    _entry("licences", "driver_id", "$[:].drivers[:].driver_id"),
    _entry("licences", "country", "$[:].drivers[:].licences[:].country"),
]


def test_validate_rejects_a_subtree_frame_that_lacks_an_ancestor_nesting_key() -> None:
    # quote_id nests drivers under quotes, and the drivers subtree carries it
    # (the drivers frame does), so every frame emitting in that subtree must:
    # licences rows without it could match no quote.
    with pytest.raises(OutputMappingSchemaError, match="nesting key") as exc_info:
        validate_v2_output_mapping(_LICENCES_WITHOUT_QUOTE_ID)

    assert exc_info.value.context == {
        "source_port": "licences",
        "output_path": "$[:].drivers[:].licences[:]",
        "key": "$[:].quote_id",
    }


def test_assemble_rejects_a_frame_lacking_a_nesting_key_before_collecting() -> None:
    collected: list[str] = []

    def frame(port: str, data: dict[str, list[object]]) -> pl.LazyFrame:
        def mark(batch: pl.DataFrame) -> pl.DataFrame:
            collected.append(port)
            return batch

        return pl.LazyFrame(data).map_batches(mark)

    frames = {
        "quotes": frame("quotes", {"$[:].quote_id": [1]}),
        "drivers": frame("drivers", {"$[:].quote_id": [1], "$[:].drivers[:].driver_id": [7]}),
        "licences": frame(
            "licences",
            {
                "$[:].drivers[:].driver_id": [7],
                "$[:].drivers[:].licences[:].country": ["GB"],
            },
        ),
    }

    with pytest.raises(OutputMappingSchemaError, match="nesting key") as exc_info:
        _assemble_document(frames)

    assert exc_info.value.context["source_port"] == "licences"
    assert collected == []


def test_validate_rejects_a_parent_frame_that_lacks_a_nesting_key_of_its_level() -> None:
    # region is a root-level field only the drivers frame carries, so drivers
    # nest under quotes by (quote_id, region); a quotes object without region
    # could match no driver.
    mapping = [
        _entry("quotes", "quote_id", "$[:].quote_id"),
        _entry("drivers", "quote_id", "$[:].quote_id"),
        _entry("drivers", "region", "$[:].region"),
        _entry("drivers", "driver_id", "$[:].drivers[:].driver_id"),
    ]

    with pytest.raises(OutputMappingSchemaError, match="nesting key") as exc_info:
        validate_v2_output_mapping(mapping)

    assert exc_info.value.context == {
        "source_port": "quotes",
        "output_path": "$[:]",
        "key": "$[:].region",
    }


def test_assemble_nests_a_subtree_frame_that_carries_every_nesting_key() -> None:
    frames = {
        "quotes": pl.LazyFrame({"$[:].quote_id": [1, 2]}),
        "drivers": pl.LazyFrame({"$[:].quote_id": [1, 2], "$[:].drivers[:].driver_id": [7, 7]}),
        "licences": pl.LazyFrame(
            {
                "$[:].quote_id": [1, 2],
                "$[:].drivers[:].driver_id": [7, 7],
                "$[:].drivers[:].licences[:].country": ["GB", "FR"],
            }
        ),
    }

    assert _assemble_document(frames) == [
        {"quote_id": 1, "drivers": [{"driver_id": 7, "licences": [{"country": "GB"}]}]},
        {"quote_id": 2, "drivers": [{"driver_id": 7, "licences": [{"country": "FR"}]}]},
    ]


def test_config_assembly_ignores_incomplete_enabled_mapping_port() -> None:
    result = assemble_output_from_config(
        pl.DataFrame({"value": [1]}),
        pl.DataFrame({"other": [2]}),
        config={
            "outputMapping": [
                {
                    "source_port": "phantom",
                    "source_column": "",
                    "output_path": "",
                    "enabled": True,
                }
            ]
        },
        source_names=["real", "other"],
    )

    assert result.collect().to_dicts() == []


def test_assemble_indexes_child_rows_once_per_relation_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._output_assembler as assembler

    seen: list[tuple[tuple[str, ...], int]] = []
    original = _index_rows

    def spy(
        rows: list[dict[str, object]], keys: tuple[str, ...]
    ) -> dict[tuple[object, ...], list[dict[str, object]]]:
        seen.append((keys, len(rows)))
        return original(rows, keys)  # type: ignore[arg-type]

    monkeypatch.setattr(assembler, "_index_rows", spy)
    child = pl.LazyFrame({"$[:].id": [1, 2], "$[:].items[:].value": ["a", "b"]})
    _assemble_document({"parent": pl.LazyFrame({"$[:].id": [1, 2]}), "child": child})
    _assemble_document({"parent": pl.LazyFrame({"$[:].id": [1, 2, 3, 4]}), "child": child})
    assert seen == [(("$[:].id",), 2), (("$[:].id",), 2)]


def test_index_rows_calls_callback_for_every_row() -> None:
    calls: list[None] = []

    indexed = _index_rows(
        [{"id": 1, "value": "a"}, {"id": 1, "value": "b"}],
        ("id",),
        on_row=lambda: calls.append(None),
    )

    assert indexed == {(1,): [{"id": 1, "value": "a"}, {"id": 1, "value": "b"}]}
    assert len(calls) == 2


def test_output_assembly_progress_checkpoints_at_threshold() -> None:
    class Context:
        def __init__(self) -> None:
            self.labels: list[str] = []

        def checkpoint(self, *, label: str) -> None:
            self.labels.append(label)

    context = Context()
    progress = _OutputAssemblyProgress(context)  # type: ignore[arg-type]
    progress.rows_since_checkpoint = 1_023

    progress.advance("output_assembly_build")

    assert context.labels == ["output_assembly_build"]
    assert progress.rows_since_checkpoint == 0


def test_assemble_document_with_context_indexes_scoped_rows() -> None:
    context = ExecutionContext(
        operation="output_assembly",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    frames = {
        "parent": pl.LazyFrame({"$[:].id": [1]}),
        "child": pl.LazyFrame({"$[:].id": [1], "$[:].items[:].value": ["x"]}),
    }

    with context.stage("assemble"):
        result = _assemble_document(frames)

    assert result == [{"id": 1, "items": [{"value": "x"}]}]


# ─── Incomplete (half-built) mapping rows ─────────────────────────
#
# A row whose source_column or output_path is still blank (e.g. a manually
# added editor row before its source column is picked) must be skipped
# everywhere — it must never demand a "" column (the confusing missing=['']
# contract failure) or crash pl.col("").


def test_is_active_mapping_entry() -> None:
    assert is_active_mapping_entry(_entry("p", "x", "$[:].x")) is True
    assert is_active_mapping_entry(_entry("p", "", "$[:].x")) is False  # blank source_column
    assert is_active_mapping_entry(_entry("p", "x", "")) is False  # blank output_path
    assert is_active_mapping_entry(_entry("p", "x", "$[:].x", enabled=False)) is False
    assert is_active_mapping_entry({"enabled": True}) is False
    assert (
        is_active_mapping_entry(
            {"source_port": "p", "source_column": "  ", "output_path": "$[:].x", "enabled": True}
        )
        is False
    )


def test_assemble_skips_blank_source_column_row() -> None:
    frames = {"p": pl.DataFrame({"a": [1], "b": [2]}).lazy()}
    mapping = [
        _entry("p", "a", "$[:].a"),
        _entry("p", "", "$[:].ghost"),  # half-built row — no source column yet
    ]
    # Does not crash on pl.col(""), and the ghost path never appears.
    assert assemble_output_from_mapping(frames, mapping) == [{"a": 1}]


def test_validate_skips_blank_source_column_row() -> None:
    # An incomplete row is not validated (its path isn't even parsed).
    validate_v2_output_mapping([_entry("p", "a", "$[:].a"), _entry("p", "", "not$valid")])


def test_output_contract_excludes_blank_source_column() -> None:
    """_output_columns must not demand a "" column from a half-built row — that
    was the confusing missing=[''] contract failure when an editor row was added
    before its source column was picked."""
    from haute._builders import _output_columns

    config = {
        "outputMapping": [
            _entry("p", "policy_id", "$[:].id"),
            _entry("p", "", "$[:].ghost"),  # half-built row — no source column yet
        ]
    }
    produced, referenced = _output_columns(config)
    assert produced == set()
    assert referenced == {"policy_id"}  # the blank row is skipped, not "" demanded


# ─── Mutation witnesses ────────────────────────────────────────────
#
# Targeted witnesses pinning branch decisions that a bounded Cosmic Ray run left
# under-tested (survivors). Each test names the construct it defends; together
# they drive the OUTPUT-assembler module's survival rate down to its equivalent-
# mutant floor. Constructed to DISCRIMINATE — the assertion changes value under
# the mutation, not merely "it still runs".


# _merge_groups union-find — find() must reach the true root regardless of the
# alphabetical relation between a node and its parent pointer (the '!=' loop
# bound must not become '<' or '>'). Two single-field merges with the carriers
# listed in opposite orders force a parent pointer in each direction.


# _execute_plan — the greedy fold must pick the next table by the INTERSECTION of
# its residual fields with the accumulated fields (`& acc_fields`), skipping a
# table that shares nothing yet (it joins later through a bridge). T1={A} shares
# nothing with the first pending T2={B}; only T3={A,B} bridges them. An unmatched
# T1 row (A=p2) makes the difference observable: the correct fold leaves {A:p2}
# standing alone, whereas a '|' (union) mutation makes the filter always-true,
# picks the non-overlapping T2 first, and cross-joins p2 onto B=q.


# Prefix-tree serialisation — a synthesised intermediate level (no frame emits
# there) that carries its OWN key must gather ONLY the descendants it is a STRICT
# prefix of: `pref[:len(prefix)] == prefix AND len(pref) > len(prefix)`. Three
# branches straddling the node alphabetically (aaa < items < other), each with a
# depth-1 key plus a depth-2 leaf, force the predicate to exclude both the
# lexically-smaller and lexically-larger sibling — a '==' → '>='/'<=' or
# 'and' → 'or' mutation would pull a sibling's rows into this level and spawn a
# spurious key=None object.


def test_assemble_synthesised_level_with_own_key_excludes_siblings() -> None:
    field_frames = {
        "Fa": pl.LazyFrame(
            {"$[:].rk": [1], "$[:].aaa[:].ka": ["a1"], "$[:].aaa[:].sub[:].va": ["av"]}
        ),
        "Fi": pl.LazyFrame(
            {"$[:].rk": [1], "$[:].items[:].ki": ["i1"], "$[:].items[:].sub[:].vi": ["iv"]}
        ),
        "Fo": pl.LazyFrame(
            {"$[:].rk": [1], "$[:].other[:].ko": ["o1"], "$[:].other[:].sub[:].vo": ["ov"]}
        ),
    }
    assert _assemble_document(field_frames) == [
        {
            "rk": 1,
            "aaa": [{"ka": "a1", "sub": [{"va": "av"}]}],
            "items": [{"ki": "i1", "sub": [{"vi": "iv"}]}],
            "other": [{"ko": "o1", "sub": [{"vo": "ov"}]}],
        }
    ]


def test_assemble_synthesised_siblings_scope_each_root_independently() -> None:
    field_frames = {
        "parent": pl.LazyFrame(
            {
                "$[:].a_key": [101, 102],
                "$[:].z_key": [201, 202],
                "$[:].name": ["first", "second"],
            }
        ),
        "aaa": pl.LazyFrame(
            {
                "$[:].a_key": [101, 102],
                "$[:].aaa[:].sub[:].value": ["a1", "a2"],
            }
        ),
        "zzz": pl.LazyFrame(
            {
                "$[:].z_key": [201, 202],
                "$[:].zzz[:].sub[:].value": ["z1", "z2"],
            }
        ),
    }

    assert _assemble_document(field_frames) == [
        {
            "a_key": 101,
            "z_key": 201,
            "name": "first",
            "aaa": [{"sub": [{"value": "a1"}]}],
            "zzz": [{"sub": [{"value": "z1"}]}],
        },
        {
            "a_key": 102,
            "z_key": 202,
            "name": "second",
            "aaa": [{"sub": [{"value": "a2"}]}],
            "zzz": [{"sub": [{"value": "z2"}]}],
        },
    ]


def test_assemble_allows_null_payload_owned_only_by_a_child() -> None:
    field_frames = {
        "parent": pl.LazyFrame({"$[:].id": [1]}),
        "child": pl.LazyFrame(
            {
                "$[:].id": [1],
                "$[:].items[:].name": ["item"],
                "$[:].items[:].optional": [None],
            }
        ),
    }

    assert _assemble_document(field_frames) == [{"id": 1, "items": [{"name": "item"}]}]


# _prune loop control — the empty-collection skips are `continue`, not `break`,
# so a later non-empty sibling still survives; and the empty-object test keeps
# non-empty objects (the `and not pv` guard is not negated).


def test_prune_drops_empty_collection_key_but_keeps_later_keys() -> None:
    # dict branch: the empty 'e' is skipped (continue), 'b' still kept. A
    # continue→break would abandon 'b'.
    assert _prune({"e": [], "b": 1}) == {"b": 1}


def test_prune_drops_empty_object_element_but_keeps_later_elements() -> None:
    # list branch: the empty {} element is skipped (continue), {"b": 1} kept. A
    # continue→break would drop {"b": 1}.
    assert _prune([{}, {"b": 1}]) == [{"b": 1}]


def test_prune_keeps_non_empty_object_elements() -> None:
    # The list-branch guard drops ONLY empty objects; negating it (`and pv`) would
    # invert this and drop the populated object.
    assert _prune([{"k": 1}]) == [{"k": 1}]


# Skip-loops in validate / assemble use `continue`, not `break`: an inactive
# entry must not curtail the entries AFTER it.


def test_validate_still_checks_entries_after_an_inactive_one() -> None:
    # The inactive (blank-column) entry is first; the colliding pair after it must
    # still be reached and rejected. A continue→break would skip the collision.
    mapping = [
        _entry("p", "", "$[:].skip"),
        _entry("p", "z", "$[:].v"),
        _entry("p", "a", "$[:].v"),
    ]
    with pytest.raises(OutputMappingSchemaError):
        validate_v2_output_mapping(mapping)


def test_assemble_still_processes_entries_after_an_inactive_one() -> None:
    frames = {"p": pl.DataFrame({"a": [1], "b": [2]}).lazy()}
    mapping = [
        _entry("p", "", "$[:].skip"),
        _entry("p", "a", "$[:].a"),
        _entry("p", "b", "$[:].b"),
    ]
    assert assemble_output_from_mapping(frames, mapping) == [{"a": 1, "b": 2}]


def test_validate_collision_is_detected_regardless_of_column_order() -> None:
    # Same path, columns in DESCENDING order ("z" before "a"). The collision test
    # is `stored != col`, not an ordered comparison — an inequality→'<' mutation
    # would miss this because "z" < "a" is False.
    with pytest.raises(OutputMappingSchemaError):
        validate_v2_output_mapping([_entry("p", "z", "$[:].v"), _entry("p", "a", "$[:].v")])


# Fast-path COUNTS — the single-item shortcuts (`len(port_list) == 1`,
# `len(group_frames) == 1`) must fire only for one, never for two: a count
# mutation (== 1 → == 2) would route a TWO-item level through the one-item branch
# and silently drop the second. No prior test had exactly two frames at a single
# node, nor exactly two honoured-merge groups.


# ---------------------------------------------------------------------------
# Limited assembly: the first documents without assembling every document
# ---------------------------------------------------------------------------


def _counted_frame(frame: pl.DataFrame, seen: list[int]) -> pl.LazyFrame:
    """A lazy frame that records how many rows the assembler actually reads."""
    from haute._polars_utils import row_local_python_scan

    def record(batch: pl.DataFrame) -> pl.DataFrame:
        seen.append(batch.height)
        return batch

    return row_local_python_scan(
        frame.lazy(),
        record,
        schema=frame.schema,
        generated_columns=(),
        required_input_columns=None,
        input_predicates_allowed=True,
        elide_transform_when_unused=False,
    )


def test_limited_assembly_reads_only_the_selected_policies_children() -> None:
    policies = pl.DataFrame(
        {"$[:].id": list(range(100)), "$[:].policy": [f"P{i}" for i in range(100)]}
    )
    covers = pl.DataFrame(
        {
            "$[:].id": [policy for policy in range(100) for _ in range(2)],
            "$[:].covers[:].name": [f"C{policy}-{n}" for policy in range(100) for n in range(2)],
        }
    )
    unlimited = _assemble_document({"policies": policies.lazy(), "covers": covers.lazy()})

    seen: list[int] = []
    limited = _assemble_document(
        {"policies": policies.lazy(), "covers": _counted_frame(covers, seen)},
        row_limit=3,
    )

    assert limited == unlimited[:3]
    assert sum(seen) == 6


def test_limited_assembly_of_a_synthesised_root_is_complete() -> None:
    field_frames = {
        "T1": pl.LazyFrame({"$[:].K": ["K0", "K1"], "$[:].obj[:].A": ["P", "Q"]}),
        "T2": pl.LazyFrame({"$[:].K": ["K0", "K1"], "$[:].other[:].B": ["R", "S"]}),
    }

    assert _assemble_document(field_frames, row_limit=1) == _assemble_document(field_frames)


def test_limited_assembly_collapses_duplicate_root_rows() -> None:
    field_frames = {"root": pl.LazyFrame({"$[:].id": ["A", "A", "B"]})}

    assert _assemble_document(field_frames, row_limit=2) == [{"id": "A"}]


def _limited_level(
    plan: pl.LazyFrame,
    *,
    level_paths: set[str],
    collected_by_prefix: dict[tuple[str, ...], pl.DataFrame],
) -> pl.LazyFrame:
    paths = set(level_paths).union(*(frame.columns for frame in collected_by_prefix.values()))
    return _limit_level_plan(
        plan,
        prefix=("a", "b"),
        row_limit=1,
        level_paths=level_paths,
        all_paths={path: _parse_output_path(path) for path in paths},
        collected_by_prefix=collected_by_prefix,
    )


def test_limited_level_filters_on_its_nearest_collected_ancestors_own_key() -> None:
    # The level carries the root key too, but only the nearest collected
    # ancestor's own key filters it, as one pushed-down ``is_in`` predicate.
    level = pl.LazyFrame(
        {
            "$[:].id": [1, 1, 2],
            "$[:].a[:].k": ["k1", "k2", "k1"],
            "$[:].a[:].b[:].v": [10, 20, 30],
        }
    )

    limited = _limited_level(
        level,
        level_paths=set(level.collect_schema().names()),
        collected_by_prefix={
            (): pl.DataFrame({"$[:].id": [1]}),
            ("a",): pl.DataFrame({"$[:].id": [1], "$[:].a[:].k": ["k1"]}),
        },
    )

    assert limited.collect()["$[:].a[:].b[:].v"].to_list() == [10, 30]
    plan = limited.explain()
    assert "is_in" in plan
    assert "SEMI" not in plan.upper()


def test_limited_level_semi_joins_on_every_own_key_of_its_ancestor() -> None:
    level = pl.LazyFrame(
        {
            "$[:].a[:].k1": [1, 1, 2, 3],
            "$[:].a[:].k2": ["x", "y", "y", "x"],
            "$[:].a[:].b[:].v": [10, 20, 30, 40],
        }
    )

    limited = _limited_level(
        level,
        level_paths=set(level.collect_schema().names()),
        collected_by_prefix={
            (): pl.DataFrame({"$[:].id": [7]}),
            ("a",): pl.DataFrame({"$[:].a[:].k1": [1, 2], "$[:].a[:].k2": ["x", "y"]}),
        },
    )

    assert sorted(limited.collect()["$[:].a[:].b[:].v"].to_list()) == [10, 30]
    assert "SEMI" in limited.explain().upper()


def test_limited_level_without_ancestor_keys_reads_every_row() -> None:
    level = pl.LazyFrame({"$[:].a[:].b[:].v": [10, 20]})

    limited = _limited_level(
        level,
        level_paths={"$[:].a[:].b[:].v"},
        collected_by_prefix={
            (): pl.DataFrame({"$[:].id": [1]}),
            ("a",): pl.DataFrame({"$[:].a[:].k": ["k1"]}),
        },
    )

    assert limited is level
