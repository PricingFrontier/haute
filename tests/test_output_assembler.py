"""OUTPUT assembler (notes-haute OUTPUT_ASSEMBLY_PROPERTIES.md).

The worked examples in OUTPUT_ASSEMBLY_WORKED_EXAMPLES.md are the oracles. We
encode each as a table → field-set incidence and assert the algorithm's
schema-determined decisions (A4) — here, the GYO core detection of §3.3: which
constraint systems are α-acyclic (no cut) vs which expose a cyclic core.

Fields are single capital letters as in the doc; ``K`` is a common parent key,
``X/Y/Z`` etc. are private attributes (must strip out as private, Rule 1).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import FrozenInstanceError

import polars as pl
import pytest

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._jsonpath import _Seg
from haute._node_apply import assemble_output_from_config
from haute._output_assembler import (
    OutputMappingSchemaError,
    OutputNestingKeyError,
    _assemble_document,
    _Core,
    _CutPlan,
    _execute_plan,
    _gyo_residue,
    _index_rows,
    _merge_groups,
    _OutputAssemblyProgress,
    _parse_output_path,
    _plan_cut,
    _prune,
    assemble_output_from_mapping,
    is_active_mapping_entry,
    render_output_document,
    validate_v2_output_mapping,
)
from haute.errors import HauteError


def _fs(spec: dict[str, str]) -> dict[str, frozenset[str]]:
    """Build a {table: frozenset(fields)} incidence from compact strings.

    ``{"T1": "KABX"}`` → ``{"T1": frozenset({"K","A","B","X"})}``.
    """
    return {t: frozenset(fields) for t, fields in spec.items()}


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


# ─── GYO core detection — α-acyclic cases (no core, nothing cut) ───


def test_single_key_is_a_star_not_a_cycle() -> None:
    # Three tables on one common field A — a star. No second shared field to
    # close a loop, so it is α-acyclic and fully reduces.
    assert _gyo_residue(_fs({"S1": "AX", "S2": "AY", "S3": "AZ"})) == {}


def test_multiplicity_star_is_acyclic() -> None:
    # Two tables sharing only A (both non-unique on A — that is a data
    # property, irrelevant to the schema-determined cut). Still a star.
    assert _gyo_residue(_fs({"M1": "AX", "M2": "AY"})) == {}


def test_composite_key_is_one_join_not_a_loop() -> None:
    # Two tables sharing *two* fields A,B is a single composite join, not a
    # cycle (a loop needs the shared fields to chain through different tables).
    assert _gyo_residue(_fs({"K1": "ABX", "K2": "ABY"})) == {}


def test_nested_table_subsumption_is_acyclic() -> None:
    # N2's fields {A} ⊆ N1's {A,B}: a covered table, stripped by Rule 2. No new
    # field reaches out to a third table to close a loop.
    assert _gyo_residue(_fs({"N1": "ABX", "N2": "AY"})) == {}


def test_boxed_triangle_covered_cycle_is_acyclic() -> None:
    # The bare triangle plus a box B1 carrying all three cycle fields A,B,C.
    # B1 covers the whole cycle, so each triangle table is covered (Rule 2)
    # and the residue is empty: a covered cycle is not an obstruction.
    residue = _gyo_residue(_fs({"T1": "ABX", "T2": "BCY", "T3": "ACZ", "B1": "ABCS"}))
    assert residue == {}


# ─── GYO core detection — α-cyclic cases (a core survives, will be cut) ───


def test_triangle_exposes_a_three_table_core() -> None:
    # A,B,C each held by exactly two tables, closing a loop no single table
    # covers; K is a common parent key (rides above the loop); X,Y,Z private.
    # The privates strip out (Rule 1); K + the three carriers survive.
    residue = _gyo_residue(_fs({"T1": "KABX", "T2": "KBCY", "T3": "KACZ"}))
    assert set(residue) == {"T1", "T2", "T3"}
    surviving_fields = frozenset().union(*residue.values())
    assert surviving_fields == frozenset({"K", "A", "B", "C"})
    # K is in every core table (the benign parent key); A,B,C in exactly two.
    assert all("K" in fs for fs in residue.values())


def test_consistent_triangle_cut_is_data_independent() -> None:
    # Same shared-field structure as the bare triangle. The cut is decided by
    # the shared fields, not the values — so even with consistent data (a
    # detail invisible at this layer) the core is the same (A4).
    residue = _gyo_residue(_fs({"T1": "KAB", "T2": "KBC", "T3": "KAC"}))
    assert set(residue) == {"T1", "T2", "T3"}


def test_gyo_is_order_independent_confluent() -> None:
    # Same triangle, tables presented in a different dict order — GYO is
    # confluent, so the residue table-set is identical.
    a = _gyo_residue(_fs({"T1": "KABX", "T2": "KBCY", "T3": "KACZ"}))
    b = _gyo_residue(_fs({"T3": "KACZ", "T1": "KABX", "T2": "KBCY"}))
    assert set(a) == set(b)


@pytest.mark.parametrize(
    "spec",
    [
        {},  # no tables
        {"only": "ABC"},  # one table — every field private
        {"a": "X", "b": "Y"},  # disjoint fields — nothing shared
    ],
)
def test_trivially_acyclic_inputs_have_empty_residue(spec: dict[str, str]) -> None:
    assert _gyo_residue(_fs(spec)) == {}


# ─── Recursive surgical cut — the cut PLAN (§4.1–§4.2, schema-determined) ───
#
# These pin the worked-example *decisions*: which (table, field) incidences are
# severed, which cores are found (and in what recursion order), and the
# honoured-merge groups the cut leaves behind. All data-independent (A4).


def _groups(plan: _CutPlan) -> set[frozenset[str]]:
    """The honoured-merge structure as a set of table groups (executor input)."""
    return {frozenset(g) for g in _merge_groups(plan.merge_residue)}


def test_triangle_cut_plan_parent_key_nests_carriers_cut() -> None:
    # The bare triangle under a common parent key K. K is in every core table
    # (the all-vs-some split, §3.3) → a parent key: it LOCATES the objects under
    # one parent but never merges them. A,B,C are carriers (each in exactly two)
    # → cut at the core tables. Result: three standalone objects, not one.
    plan = _plan_cut(_fs({"T1": "KABX", "T2": "KBCY", "T3": "KACZ"}))

    assert len(plan.cores) == 1
    (core,) = plan.cores
    assert core.tables == frozenset({"T1", "T2", "T3"})
    assert core.parent_keys == frozenset({"K"})
    assert core.carriers == frozenset({"A", "B", "C"})

    # Each carrier severed at exactly the two core tables that carry it.
    assert plan.cuts == frozenset(
        {("T1", "A"), ("T3", "A"), ("T1", "B"), ("T2", "B"), ("T2", "C"), ("T3", "C")}
    )
    # The parent key is never cut.
    assert all(field != "K" for _table, field in plan.cuts)

    # No honoured merge among the core tables — each stands alone. And the cut
    # removes the JOIN role, not the value: the private X still rides along.
    assert _groups(plan) == {frozenset({"T1"}), frozenset({"T2"}), frozenset({"T3"})}
    assert plan.merge_residue["T1"] == frozenset({"X"})


def test_pendant_cut_is_surgical() -> None:
    # The triangle core plus two pendants P1,P2 sharing carrier A. The cut is
    # surgical (§4.2): A is severed at the CORE tables T1,T3 but stays live at
    # the pendants, so the pendants join among themselves while the core stands
    # apart — (A:P, W, V) coexists unmerged beside (A:P, B, X).
    plan = _plan_cut(_fs({"T1": "ABX", "T2": "BCY", "T3": "ACZ", "P1": "AW", "P2": "AV"}))

    (core,) = plan.cores
    assert core.tables == frozenset({"T1", "T2", "T3"})
    assert core.carriers == frozenset({"A", "B", "C"})
    assert core.parent_keys == frozenset()  # no K in this listing

    # Surgical: cut at the core tables, untouched at the pendants.
    assert {("T1", "A"), ("T3", "A")} <= plan.cuts
    assert ("P1", "A") not in plan.cuts
    assert ("P2", "A") not in plan.cuts

    assert _groups(plan) == {
        frozenset({"P1", "P2"}),  # pendants merge among themselves on A
        frozenset({"T1"}),
        frozenset({"T2"}),
        frozenset({"T3"}),
    }


def test_tri_pendant_symmetric_three_pendant_merges() -> None:
    # Every carrier A,B,C carries its own pendant pair. By symmetry no field is
    # distinguished; the core is cut and each carrier is restricted to its
    # pendants, giving three independent merge groups beside the standalone core.
    plan = _plan_cut(
        _fs(
            {
                "T1": "ABX",
                "T2": "BCY",
                "T3": "ACZ",
                "P1": "AW",
                "P2": "AV",
                "Q1": "BU",
                "Q2": "BG",
                "R1": "CS",
                "R2": "CO",
            }
        )
    )

    assert plan.cores[0].tables == frozenset({"T1", "T2", "T3"})
    groups = _groups(plan)
    assert frozenset({"P1", "P2"}) in groups
    assert frozenset({"Q1", "Q2"}) in groups
    assert frozenset({"R1", "R2"}) in groups
    assert frozenset({"T1"}) in groups  # the core never merges


def test_window_recursion_finds_curtain_core_then_window_core() -> None:
    # The recursion trap (§4.1 step 3). GYO finds the CURTAIN core first because
    # the windows are *covered* by the curtains and strip out as covered tables.
    # After cutting the curtain carriers {A,B,D} (with C the parent key), re-run
    # on the FULL set: the windows are now un-covered and surface as a SECOND
    # core {A,B,C,D} with no parent key. Everything lands standalone (8 objects).
    plan = _plan_cut(
        _fs(
            {
                "W1": "ABX",
                "W2": "BCY",
                "W3": "CDZ",
                "W4": "ADW",
                "C1": "ABCV",
                "C2": "ACDU",
                "C3": "BCDT",
            }
        )
    )

    assert len(plan.cores) == 2
    curtain, window = plan.cores  # recursion order: curtains first
    assert curtain.tables == frozenset({"C1", "C2", "C3"})
    assert curtain.parent_keys == frozenset({"C"})
    assert curtain.carriers == frozenset({"A", "B", "D"})
    assert window.tables == frozenset({"W1", "W2", "W3", "W4"})
    assert window.parent_keys == frozenset()
    assert window.carriers == frozenset({"A", "B", "C", "D"})

    # No honoured merge survives — eight standalone partial objects.
    assert len(_groups(plan)) == 7  # 7 tables, every one isolated


def test_boxed_cycle_is_not_cut_and_all_merge() -> None:
    # A box B1 covering the whole cycle dissolves it (§6.3): no core, nothing
    # cut, and every table lands in one honoured-merge group.
    plan = _plan_cut(_fs({"T1": "ABX", "T2": "BCY", "T3": "ACZ", "B1": "ABCS"}))
    assert plan.cores == ()
    assert plan.cuts == frozenset()
    assert _groups(plan) == {frozenset({"T1", "T2", "T3", "B1"})}


def test_nested_table_is_not_cut_and_joins_on_shared_field() -> None:
    # Subsumption: N2 ⊆ N1, no cycle. Nothing cut; the two merge on A.
    plan = _plan_cut(_fs({"N1": "ABX", "N2": "AY"}))
    assert plan.cores == ()
    assert plan.cuts == frozenset()
    assert _groups(plan) == {frozenset({"N1", "N2"})}


@pytest.mark.parametrize(
    "spec",
    [
        {"S1": "AX", "S2": "AY", "S3": "AZ"},  # single-key star
        {"K1": "ABX", "K2": "ABY"},  # composite key — one join, not a loop
        {},  # no tables
        {"only": "ABC"},  # one table — all private
    ],
)
def test_acyclic_inputs_have_empty_cut_plan(spec: dict[str, str]) -> None:
    plan = _plan_cut(_fs(spec))
    assert plan.cores == ()
    assert plan.cuts == frozenset()


# ─── Executor — run the plan over data (§4.3 bag join + §4.4 co-location) ───
#
# The frames here are already keyed by FIELD (column name = field id), isolating
# the relational assembly from the column→path normalisation. Each assembled row
# is read back as the worked examples write objects: the set of (field, value)
# pairs it actually carries (nulls = absent fields). Compared as a *multiset*,
# because the join is a bag (multiplicity is meaningful, not deduped).


def _objects(lf: pl.LazyFrame) -> Counter[frozenset[tuple[str, object]]]:
    df = lf.collect()
    cols = df.columns
    return Counter(
        frozenset((c, v) for c, v in zip(cols, row) if v is not None) for row in df.iter_rows()
    )


def _obj(**fields: object) -> frozenset[tuple[str, object]]:
    return frozenset(fields.items())


def test_execute_multiplicity_fans_out_as_a_bag() -> None:
    # Star on A, both sides non-unique → the bag natural join multiplies out to
    # every combination (§4.3). Four objects, not deduped.
    frames = {
        "M1": pl.LazyFrame({"A": ["m", "m"], "X": [1, 2]}),
        "M2": pl.LazyFrame({"A": ["m", "m"], "Y": [3, 4]}),
    }
    plan = _plan_cut(_fs({"M1": "AX", "M2": "AY"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [
            _obj(A="m", X=1, Y=3),
            _obj(A="m", X=1, Y=4),
            _obj(A="m", X=2, Y=3),
            _obj(A="m", X=2, Y=4),
        ]
    )


def test_execute_single_key_star_merges_to_one() -> None:
    frames = {
        "S1": pl.LazyFrame({"A": ["m"], "X": [1]}),
        "S2": pl.LazyFrame({"A": ["m"], "Y": [2]}),
        "S3": pl.LazyFrame({"A": ["m"], "Z": [3]}),
    }
    plan = _plan_cut(_fs({"S1": "AX", "S2": "AY", "S3": "AZ"}))
    assert _objects(_execute_plan(frames, plan)) == Counter([_obj(A="m", X=1, Y=2, Z=3)])


def test_execute_nested_joins_where_matched_else_stands_alone() -> None:
    # Full-outer bag join: N2's matching A=m row folds into N1; its A=n row has
    # nothing to join and survives as a co-located partial (§4.4).
    frames = {
        "N1": pl.LazyFrame({"A": ["m"], "B": [5], "X": [1]}),
        "N2": pl.LazyFrame({"A": ["m", "n"], "Y": [7, 8]}),
    }
    plan = _plan_cut(_fs({"N1": "ABX", "N2": "AY"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [_obj(A="m", B=5, X=1, Y=7), _obj(A="n", Y=8)]
    )


def test_execute_triangle_stays_three_partials_despite_consistent_data() -> None:
    # The cut is schema-determined (A4): even with consistent data round the
    # cycle (one K0, P, Q, R everywhere — which a join WOULD have merged), the
    # three core tables stay three separate partial objects. The parent key K0
    # rides on every row (it nests at serialise time; it does not merge here).
    frames = {
        "T1": pl.LazyFrame({"K": ["K0"], "A": ["P"], "B": ["Q"], "X": [1]}),
        "T2": pl.LazyFrame({"K": ["K0"], "B": ["Q"], "C": ["R"], "Y": [2]}),
        "T3": pl.LazyFrame({"K": ["K0"], "A": ["P"], "C": ["R"], "Z": [3]}),
    }
    plan = _plan_cut(_fs({"T1": "KABX", "T2": "KBCY", "T3": "KACZ"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [
            _obj(K="K0", A="P", B="Q", X=1),
            _obj(K="K0", B="Q", C="R", Y=2),
            _obj(K="K0", A="P", C="R", Z=3),
        ]
    )


def test_execute_pendant_pendants_join_core_stands_apart() -> None:
    # Surgical cut at execution: the pendants merge on A into one object, while
    # the three core tables stand apart — (A:P, W, V) coexists unmerged beside
    # (A:P, B, X), both carrying A=P (§4.2 transitivity).
    frames = {
        "T1": pl.LazyFrame({"A": ["P"], "B": ["Q"], "X": [1]}),
        "T2": pl.LazyFrame({"B": ["Q"], "C": ["R"], "Y": [2]}),
        "T3": pl.LazyFrame({"A": ["P"], "C": ["R"], "Z": [3]}),
        "P1": pl.LazyFrame({"A": ["P"], "W": [8]}),
        "P2": pl.LazyFrame({"A": ["P"], "V": [9]}),
    }
    plan = _plan_cut(_fs({"T1": "ABX", "T2": "BCY", "T3": "ACZ", "P1": "AW", "P2": "AV"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [
            _obj(A="P", B="Q", X=1),
            _obj(B="Q", C="R", Y=2),
            _obj(A="P", C="R", Z=3),
            _obj(A="P", W=8, V=9),  # the joined-up pendant
        ]
    )


def test_execute_standalone_table_keeps_every_row() -> None:
    # Co-location is a bag-union: an isolated table's rows each stand alone,
    # multiplicity preserved (nothing dropped, nothing invented — A1a).
    frames = {"L": pl.LazyFrame({"A": ["p", "q", "q"], "X": [1, 2, 3]})}
    plan = _plan_cut(_fs({"L": "AX"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [_obj(A="p", X=1), _obj(A="q", X=2), _obj(A="q", X=3)]
    )


# ─── Output-path parser — the [:]-only conventional-JSONPath subset (§2) ───


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


# ─── Assembler — descend the prefix tree, nest by ancestor key (§4.5) ───
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


def test_assemble_triangle_three_partials_under_one_parent() -> None:
    # The single-level cyclic case still works through the tree recursion: the
    # root level has no frame of its own, so it is synthesised from the parent key
    # K the obj-level frames carry; the three obj frames share a cyclic core at
    # one level → cut → three co-located partials, nested under the one K0 parent.
    field_frames = {
        "T1": pl.LazyFrame(
            {
                "$[:].K": ["K0"],
                "$[:].obj[:].A": ["P"],
                "$[:].obj[:].B": ["Q"],
                "$[:].obj[:].attrs.X": [1],
            }
        ),
        "T2": pl.LazyFrame(
            {
                "$[:].K": ["K0"],
                "$[:].obj[:].B": ["Q"],
                "$[:].obj[:].C": ["R"],
                "$[:].obj[:].attrs.Y": [2],
            }
        ),
        "T3": pl.LazyFrame(
            {
                "$[:].K": ["K0"],
                "$[:].obj[:].A": ["P"],
                "$[:].obj[:].C": ["R"],
                "$[:].obj[:].attrs.Z": [3],
            }
        ),
    }
    assert _assemble_document(field_frames) == [
        {
            "K": "K0",
            "obj": [
                {"A": "P", "B": "Q", "attrs": {"X": 1}},
                {"B": "Q", "C": "R", "attrs": {"Y": 2}},
                {"A": "P", "C": "R", "attrs": {"Z": 3}},
            ],
        }
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


def test_validate_parses_each_distinct_active_path_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import haute._output_assembler as assembler

    original = assembler._parse_output_path
    calls: list[str] = []

    def spy(path: str):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(assembler, "_parse_output_path", spy)
    mapping = [
        _entry("p", f"value_{index}", f"$[:].items[:].value_{index}") for index in range(200)
    ]
    mapping.extend([_entry("q", "same", "$[:].items[:].value_0") for _ in range(50)])

    assembler.validate_v2_output_mapping(mapping)

    assert len(calls) == 200


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


def test_same_level_output_frames_are_materialised_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same lazy sources must not be collected once raw and again for their join."""
    original_collect = pl.LazyFrame.collect
    collect_calls = 0

    def counted_collect(self: pl.LazyFrame, *args: object, **kwargs: object) -> pl.DataFrame:
        nonlocal collect_calls
        collect_calls += 1
        return original_collect(self, *args, **kwargs)

    monkeypatch.setattr(pl.LazyFrame, "collect", counted_collect)
    result = _assemble_document(
        {
            "left": pl.LazyFrame({"$[:].id": [1], "$[:].a": ["left"]}),
            "right": pl.LazyFrame({"$[:].id": [1], "$[:].b": ["right"]}),
        }
    )

    assert result == [{"id": 1, "a": "left", "b": "right"}]
    assert collect_calls == 1


def test_output_materialisation_uses_active_execution_context() -> None:
    """Terminal assembly remains observable and cancellable after Polars collection."""
    fault_points: list[str] = []
    context = ExecutionContext(
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
    context = ExecutionContext(
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


def test_assemble_ignores_absent_nesting_key_in_partial_frame() -> None:
    frames = {
        "a": pl.LazyFrame(
            {
                "$[:].quote_id": [1],
                "$[:].drivers[:].name": ["Ann"],
            }
        ),
        "b": pl.LazyFrame({"$[:].drivers[:].age": [42]}),
    }

    assert _assemble_document(frames) == [
        {"quote_id": 1, "drivers": [{"name": "Ann"}]},
        {"drivers": [{"age": 42}]},
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


@pytest.mark.parametrize(
    ("record", "attribute"),
    [
        (
            _Core(
                tables=frozenset({"T"}),
                parent_keys=frozenset(),
                carriers=frozenset(),
            ),
            "tables",
        ),
        (
            _CutPlan(
                cores=(),
                cuts=frozenset(),
                merge_residue={"T": frozenset()},
            ),
            "cuts",
        ),
    ],
)
def test_cut_plan_records_are_immutable(record: object, attribute: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(record, attribute, None)


# _merge_groups union-find — find() must reach the true root regardless of the
# alphabetical relation between a node and its parent pointer (the '!=' loop
# bound must not become '<' or '>'). Two single-field merges with the carriers
# listed in opposite orders force a parent pointer in each direction.


def test_merge_groups_unions_with_ascending_parent_pointer() -> None:
    # residue order A,B → union(A,B) sets parent[A]=B (ascending). A '<' mutant on
    # `while parent[root] != root` stops immediately at A, splitting the group.
    assert _merge_groups({"A": frozenset({"f"}), "B": frozenset({"f"})}) == [frozenset({"A", "B"})]


def test_merge_groups_unions_with_descending_parent_pointer() -> None:
    # residue order B,A → union(B,A) sets parent[B]=A (descending). A '>' mutant on
    # the same loop bound stops immediately at B, splitting the group.
    assert _merge_groups({"B": frozenset({"f"}), "A": frozenset({"f"})}) == [frozenset({"A", "B"})]


def test_merge_groups_transitive_chain_is_one_group() -> None:
    # A–B–C–D linked pairwise through three distinct fields: find() must walk the
    # multi-hop parent chain to a single root, so all four are one honoured group.
    chain = {
        "A": frozenset({"f1"}),
        "B": frozenset({"f1", "f2"}),
        "C": frozenset({"f2", "f3"}),
        "D": frozenset({"f3"}),
    }
    assert _merge_groups(chain) == [frozenset({"A", "B", "C", "D"})]


def test_merge_groups_field_shared_by_three_tables() -> None:
    # One field carried by THREE tables unions members[1:] (B and C) onto members[0]
    # (A). A `members[2:]` slice mutation would leave B ungrouped → two groups.
    groups = _merge_groups({"A": frozenset({"f"}), "B": frozenset({"f"}), "C": frozenset({"f"})})
    assert {frozenset(g) for g in groups} == {frozenset({"A", "B", "C"})}


def test_merge_groups_disconnected_pairs_stay_separate() -> None:
    # Two field-disjoint pairs never merge — a sanity bound on the union step.
    groups = {
        frozenset(g)
        for g in _merge_groups(
            {
                "A": frozenset({"f1"}),
                "B": frozenset({"f1"}),
                "C": frozenset({"f2"}),
                "D": frozenset({"f2"}),
            }
        )
    }
    assert groups == {frozenset({"A", "B"}), frozenset({"C", "D"})}


# _execute_plan — the greedy fold must pick the next table by the INTERSECTION of
# its residual fields with the accumulated fields (`& acc_fields`), skipping a
# table that shares nothing yet (it joins later through a bridge). T1={A} shares
# nothing with the first pending T2={B}; only T3={A,B} bridges them. An unmatched
# T1 row (A=p2) makes the difference observable: the correct fold leaves {A:p2}
# standing alone, whereas a '|' (union) mutation makes the filter always-true,
# picks the non-overlapping T2 first, and cross-joins p2 onto B=q.


def test_execute_plan_picks_fold_order_by_shared_field_intersection() -> None:
    frames = {
        "T1": pl.LazyFrame({"A": ["p", "p2"]}),  # p2 has no T3 match
        "T2": pl.LazyFrame({"B": ["q"], "mB": [9]}),
        "T3": pl.LazyFrame({"A": ["p"], "B": ["q"]}),
    }
    plan = _plan_cut(_fs({"T1": "A", "T2": "B", "T3": "AB"}))
    assert _objects(_execute_plan(frames, plan)) == Counter(
        [_obj(A="p", B="q", mB=9), _obj(A="p2")]
    )


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


def test_assemble_two_frames_at_one_level_keeps_both() -> None:
    # Two frames both emit at the root array and join on id — a 2-port level. If
    # the `len == 1` shortcut fired for a count of two, the second frame's column
    # ("b") would vanish.
    field_frames = {
        "F1": pl.LazyFrame({"$[:].id": [1], "$[:].a": ["av"]}),
        "F2": pl.LazyFrame({"$[:].id": [1], "$[:].b": ["bv"]}),
    }
    assert _assemble_document(field_frames) == [{"id": 1, "a": "av", "b": "bv"}]


def test_execute_plan_two_disjoint_groups_are_both_emitted() -> None:
    # Two field-disjoint tables form two honoured-merge groups, stacked by the
    # diagonal concat. A `len(group_frames) == 2` shortcut would return only the
    # first group and drop the second.
    frames = {
        "G1": pl.LazyFrame({"A": ["p"], "x": [1]}),
        "G2": pl.LazyFrame({"B": ["q"], "y": [2]}),
    }
    plan = _plan_cut(_fs({"G1": "Ax", "G2": "By"}))
    assert _objects(_execute_plan(frames, plan)) == Counter([_obj(A="p", x=1), _obj(B="q", y=2)])
