"""OUTPUT assembler (notes-haute OUTPUT_ASSEMBLY_PROPERTIES.md).

The worked examples in OUTPUT_ASSEMBLY_WORKED_EXAMPLES.md are the oracles. We
encode each as a table → field-set incidence and assert the algorithm's
schema-determined decisions (A4) — here, the GYO core detection of §3.3: which
constraint systems are α-acyclic (no cut) vs which expose a cyclic core.

Fields are single capital letters as in the doc; ``K`` is a common parent key,
``X/Y/Z`` etc. are private attributes (must strip out as private, Rule 1).
"""

from __future__ import annotations

import pytest

from haute._output_assembler import OutputMappingSchemaError, _gyo_residue
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
