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

import polars as pl
import pytest

from haute._output_assembler import (
    OutputMappingSchemaError,
    _CutPlan,
    _execute_plan,
    _gyo_residue,
    _merge_groups,
    _nest_document,
    _parse_output_path,
    _plan_cut,
    _Seg,
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


def test_parse_output_path_segments_and_root_array() -> None:
    p = _parse_output_path("$[:].drivers[:].name")
    assert p.root_array is True
    assert p.segments == (_Seg("drivers", True), _Seg("name", False))

    q = _parse_output_path("$[:].obj[:].attrs.X")
    assert q.segments == (_Seg("obj", True), _Seg("attrs", False), _Seg("X", False))

    # Bracketed name selectors are accepted and normalised to the bare name.
    r = _parse_output_path("$[:]['drivers'][:][\"name\"]")
    assert r.segments == (_Seg("drivers", True), _Seg("name", False))


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


# ─── Serialiser — prefix-nest the flat frame into the JSON document (§4.5) ───


def test_nest_round_trip_parent_and_child_array() -> None:
    # The canonical shred → assemble shape: a parent key repeated across child
    # rows nests into one object whose children collect into an array. The
    # parent fields de-dup; the drivers fan out.
    rows = [
        {"$[:].id": 1, "$[:].policy": "P", "$[:].drivers[:].name": "a"},
        {"$[:].id": 1, "$[:].policy": "P", "$[:].drivers[:].name": "b"},
    ]
    doc = _nest_document(rows, ["$[:].id", "$[:].policy", "$[:].drivers[:].name"])
    assert doc == [{"id": 1, "policy": "P", "drivers": [{"name": "a"}, {"name": "b"}]}]


def test_nest_triangle_three_partials_under_one_parent() -> None:
    # The cut's three partial objects co-locate in the obj array under the one
    # K0 parent; the parent key K nests (it does not merge), and each partial
    # keeps only the fields it carries (nulls pruned). attrs.* nests as an object.
    rows = [
        {"$[:].K": "K0", "$[:].obj[:].A": "P", "$[:].obj[:].B": "Q", "$[:].obj[:].attrs.X": 1},
        {"$[:].K": "K0", "$[:].obj[:].B": "Q", "$[:].obj[:].C": "R", "$[:].obj[:].attrs.Y": 2},
        {"$[:].K": "K0", "$[:].obj[:].A": "P", "$[:].obj[:].C": "R", "$[:].obj[:].attrs.Z": 3},
    ]
    paths = [
        "$[:].K",
        "$[:].obj[:].A",
        "$[:].obj[:].B",
        "$[:].obj[:].C",
        "$[:].obj[:].attrs.X",
        "$[:].obj[:].attrs.Y",
        "$[:].obj[:].attrs.Z",
    ]
    assert _nest_document(rows, paths) == [
        {
            "K": "K0",
            "obj": [
                {"A": "P", "B": "Q", "attrs": {"X": 1}},
                {"B": "Q", "C": "R", "attrs": {"Y": 2}},
                {"A": "P", "C": "R", "attrs": {"Z": 3}},
            ],
        }
    ]


def test_nest_empty_child_array_is_omitted() -> None:
    # A parent with no children (the child leaf is null — an outer-join leftover)
    # emits an empty array, which S21 omits: the drivers key is absent entirely.
    rows = [{"$[:].id": 1, "$[:].policy": "P", "$[:].drivers[:].name": None}]
    doc = _nest_document(rows, ["$[:].id", "$[:].policy", "$[:].drivers[:].name"])
    assert doc == [{"id": 1, "policy": "P"}]


def test_nest_empty_object_is_omitted() -> None:
    # Empty collections carry no data (Nick's ruling, 2026-06-16): an all-null
    # nested object is omitted, not emitted as {}. The round-trip therefore holds
    # only up to empty collections.
    rows = [{"$[:].id": 1, "$[:].meta.note": None}]
    doc = _nest_document(rows, ["$[:].id", "$[:].meta.note"])
    assert doc == [{"id": 1}]  # meta omitted entirely, not {"meta": {}}


def test_assemble_round_trip_execute_then_nest() -> None:
    # End-to-end over the two slices: a policies frame and a drivers frame keyed
    # by the shared ancestor id (the W1 distribution). The executor joins on the
    # id, the serialiser nests — reproducing the input document shape (the
    # commit-9 round-trip invariant in miniature).
    field_frames = {
        "policies": pl.LazyFrame({"$[:].id": [1], "$[:].policy": ["P"]}),
        "drivers": pl.LazyFrame({"$[:].id": [1, 1], "$[:].drivers[:].name": ["a", "b"]}),
    }
    incidence = {
        "policies": frozenset({"$[:].id", "$[:].policy"}),
        "drivers": frozenset({"$[:].id", "$[:].drivers[:].name"}),
    }
    plan = _plan_cut(incidence)
    rows = _execute_plan(field_frames, plan).collect().to_dicts()
    doc = _nest_document(rows, ["$[:].id", "$[:].policy", "$[:].drivers[:].name"])
    assert doc == [{"id": 1, "policy": "P", "drivers": [{"name": "a"}, {"name": "b"}]}]
