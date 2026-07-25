"""Mutation-killing witness tests for the v2 shred core.

Targets specific Cosmic Ray SURVIVORS in ``haute._json_shred``:
the leaf resolver (``_resolve_leaf``), the shred walk
(``shred_to_buffers`` and its closures), and the typed frame builder
(``_buffer_to_frame``). Each test is engineered so the named mutation
flips an OBSERVABLE output; line numbers in comments are Cosmic Ray
start_pos_row in the current working tree.

These tests intentionally exercise private helpers (``_resolve_leaf``,
``_buffer_to_frame``, ``_SCALAR_VALUE_LEAF``) and shape-skip accounting
(``ShredSkipStats``) directly, because that is the only way to pin the
discriminating operator tightly.
"""

from __future__ import annotations

from typing import Any

import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _SCALAR_VALUE_LEAF,
    ShredSkipStats,
    _buffer_to_frame,
    _reject_reserved_leaf_collision,
    _reject_unexpressible_key,
    _resolve_leaf,
    shred_to_buffers,
)


def _noninterned(s: str) -> str:
    """A runtime-built, NON-interned copy of *s*.

    CPython constant-folds literal concatenation, so we rebuild the string
    character-by-character. ``== s`` is True but ``is s`` is False — the
    discriminator for an ``Eq -> Is`` mutation on a string compare.
    """
    copy = "".join(list(s))
    assert copy == s and copy is not s
    return copy


# ─── _resolve_leaf, line 535: `if leaf == _SCALAR_VALUE_LEAF:` ────────
#   Eq_Is and Eq_LtE survivors.


def test_resolve_leaf_scalar_value_uses_equality_not_identity() -> None:
    # Eq -> Is: a non-interned copy of "$value" must STILL take the scalar
    # branch (== True, is False). The element is a bare scalar (int 7), which
    # has no `.get`, so the mutant ('is' False) would fall through to
    # `value.get("$value")` and raise AttributeError instead of returning 7.
    leaf = _noninterned(_SCALAR_VALUE_LEAF)
    assert _resolve_leaf(7, leaf) == 7


def test_resolve_leaf_scalar_value_uses_equality_not_lte() -> None:
    # Eq -> LtE: pick a real dict leaf "#x" that is lexically LESS than
    # "$value" ('#' 0x23 < '$' 0x24) but not equal. Real code (==) does NOT
    # take the scalar branch and returns value.get("#x") == 42. The LtE mutant
    # ("#x" <= "$value" is True) would take the scalar branch and, because
    # `value` is a dict, return None. Assert the normal lookup happened.
    assert _resolve_leaf({"#x": 42}, "#x") == 42


# ─── _resolve_leaf: a dotted leaf crossing a list fails LOUD (W1) ──────
#   A dotted leaf addresses 1-1 object nesting only; a list mid-walk is a
#   shape mismatch. Silently taking `cur[0]` dropped every other element, so
#   it now raises ApiInputSchemaError naming the offending leaf.


def test_resolve_leaf_crossing_a_list_raises_not_silently_collapses() -> None:
    # A list of >1 objects would lose all but the first under the old collapse;
    # fail loud instead so the loss can never go unnoticed.
    value = {"claims": [{"amount": 3}, {"amount": 99}]}
    with pytest.raises(ApiInputSchemaError, match="claims.amount"):
        _resolve_leaf(value, "claims.amount")


def test_resolve_leaf_empty_list_mid_walk_returns_none_not_raises() -> None:
    # An EMPTY list mid-walk discards nothing (no element to drop), so it is
    # NOT a conservation violation — it resolves to None rather than raising,
    # so data that mixes an object with an occasional empty array at this key
    # doesn't hard-fail the whole build (W1). A NON-empty list still raises
    # (see test_resolve_leaf_crossing_a_list_raises_not_silently_collapses).
    assert _resolve_leaf({"claims": []}, "claims.amount") is None


# ─── shred_to_buffers, line 600: `continue` in column-select loop ─────


def test_unselected_column_continues_not_breaks() -> None:
    # ContinueWithBreak: an unselected column sits BETWEEN two selected ones.
    # `continue` skips just it; `break` would abandon the rest of the loop and
    # drop the trailing selected column "c". Assert "c" is present.
    cfg = {
        "path": "x.json",
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "columns": [
                    {"name": "a", "path": "$[:].a", "type": "int", "selected": True},
                    {"name": "b", "path": "$[:].b", "type": "int", "selected": False},
                    {"name": "c", "path": "$[:].c", "type": "int", "selected": True},
                ],
            }
        ],
    }
    buffers = shred_to_buffers([{"a": 1, "b": 2, "c": 3}], cfg)
    assert buffers["root"] == [{"a": 1, "c": 3}]


# ─── _emit_row, line 650: ancestor vs current-node source selection ───
#   `src = value if src_depth == depth else ancestors[src_depth]`


def test_ancestor_column_sourced_from_ancestor_not_current_node() -> None:
    # Eq -> LtE: an ancestor column (src_depth 0) on a depth-1 table. Real
    # code (== False at depth 1) reads the ANCESTOR (the root element) and
    # finds policy_id. The LtE mutant (0 <= 1 True) would read the CURRENT
    # node (the driver dict), which has no policy_id -> None. The driver dict
    # deliberately has NO policy_id key so the two sources differ sharply.
    cfg = {
        "path": "x.json",
        "tables": [
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
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            }
        ],
    }
    records = [{"policy_id": 1001, "drivers": [{"driver_id": 1}, {"driver_id": 2}]}]
    buffers = shred_to_buffers(records, cfg)
    assert buffers["drivers"] == [
        {"driver_id": 1, "policy_id": 1001},
        {"driver_id": 2, "policy_id": 1001},
    ]


def test_normal_column_sources_current_node_equality_not_neq() -> None:
    # Eq -> NotEq: a NORMAL column at depth 1 (src_depth == depth == 1). Real
    # code reads the current node. The NotEq mutant (1 != 1 False) would index
    # ancestors[1]; ancestors has length == depth == 1, so ancestors[1] raises
    # IndexError. Real code returns the driver's own id.
    cfg = {
        "path": "x.json",
        "tables": [
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
                ],
            }
        ],
    }
    buffers = shred_to_buffers([{"drivers": [{"driver_id": 9}]}], cfg)
    assert buffers["drivers"] == [{"driver_id": 9}]


# ─── _resolve_leaf via _emit_row: inferred scalar coercion ────────────
#   `if leaf == _SCALAR_VALUE_LEAF: resolved = _coerce_scalar(...)`


def test_declared_string_columns_coerce_json_scalars_at_any_depth() -> None:
    # Inference widens mixed scalar domains to ``str``. The shredder therefore
    # applies deterministic scalar stringification at every source depth.
    cfg = {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:].tags[:]",
                "label": "tags",
                "emit": True,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].tags[:].$value",
                        "type": "str",
                        "selected": True,
                    },
                    {"name": "gid", "path": "$[:].gid", "type": "str", "selected": True},
                ],
            }
        ],
    }
    records = [{"gid": 1, "tags": [7]}]
    buffers = shred_to_buffers(records, cfg)
    # String widening is consistent for both the scalar element and ancestor.
    assert buffers["tags"] == [{"value": "7", "gid": "1"}]
    assert isinstance(buffers["tags"][0]["value"], str)
    assert isinstance(buffers["tags"][0]["gid"], str)


# ─── _emit_at, lines 671-672 + 674: shape-mismatch skip accounting ────
#   671 is_scalar_table = any(leaf == _SCALAR_VALUE_LEAF ...)
#   672 if is_scalar_table != (not is_dict): _count_row_skip; 674 continue


def _mixed_two_tables_cfg() -> dict[str, Any]:
    """Two emitting tables at the SAME array position $[:].items[:]:
    a scalar ($value) table first, an object table second."""
    return {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:].items[:]",
                "label": "scalars",
                "emit": True,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].items[:].$value",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[:].items[:]",
                "label": "objects",
                "emit": True,
                "columns": [
                    {"name": "k", "path": "$[:].items[:].k", "type": "int", "selected": True},
                ],
            },
        ],
    }


def test_shape_mismatch_skips_and_counts_per_table() -> None:
    # NotEq guard (672): a mixed array [scalar 5, object {"k":9}].
    #   - scalar table: emits the scalar 5, SKIPS+counts the object.
    #   - object table: emits {"k":9}, SKIPS+counts the scalar.
    # The Eq mutant (== for !=) inverts the guard: each table would keep its
    # MISMATCHED element and drop its matching one.
    cfg = _mixed_two_tables_cfg()
    stats = ShredSkipStats()
    records = [{"items": [5, {"k": 9}]}]
    buffers = shred_to_buffers(records, cfg, stats=stats)
    assert buffers["scalars"] == [{"value": 5}]
    assert buffers["objects"] == [{"k": 9}]
    # Exactly one mismatched element dropped per table, and COUNTED.
    assert stats.skipped_rows_by_table == {"scalars": 1, "objects": 1}


def test_emit_at_table_loop_continue_not_break() -> None:
    # ContinueWithBreak (674): a single DICT element at a position holding the
    # scalar table (first, skipped) then the object table (second). `continue`
    # lets the object table still emit; `break` would abandon the table loop
    # after the skip, so "objects" would be empty.
    cfg = _mixed_two_tables_cfg()
    stats = ShredSkipStats()
    records = [{"items": [{"k": 42}]}]
    buffers = shred_to_buffers(records, cfg, stats=stats)
    assert buffers["objects"] == [{"k": 42}]
    assert buffers["scalars"] == []
    assert stats.skipped_rows_by_table == {"scalars": 1}


# ─── _walk_array, line 703: null element branch ───────────────────────
#   `if any(leaf == _SCALAR_VALUE_LEAF ...): emit None-row else: count skip`


def test_null_element_emits_for_scalar_table_counts_for_object_table() -> None:
    # Eq cluster: a null array element.
    #   - scalar ($value) table: emits a real row with value None.
    #   - object table: a null is a non-record -> counted skip, no row.
    # A mutant that fails to recognise $value would route the scalar table's
    # null to the skip branch (no None-row) instead.
    cfg = _mixed_two_tables_cfg()
    stats = ShredSkipStats()
    records = [{"items": [None]}]
    buffers = shred_to_buffers(records, cfg, stats=stats)
    assert buffers["scalars"] == [{"value": None}]
    assert buffers["objects"] == []
    assert stats.skipped_rows_by_table == {"objects": 1}


# ─── _buffer_to_frame, line 770: date column rejects raw int ──────────
#   `if col_type == "date" and any(isinstance(v, int) for v in values):`


def test_date_column_with_int_raises_equality_not_identity() -> None:
    # Eq_Is: declare a "date" column (non-interned token to defeat 'is') with
    # a raw int value. Real code (==) raises ApiInputSchemaError naming the
    # column. The 'is' mutant would not match, skip the guard, and let Polars
    # silently reinterpret the int as days-since-epoch (no raise).
    col_type = _noninterned("date")
    rows = [{"d": 2024}]
    with pytest.raises(ApiInputSchemaError) as exc:
        _buffer_to_frame(rows, [("d", "d", col_type)])
    # The date-specific message proves the date guard fired (not the generic
    # strict-build fallback), and names the offending column.
    assert exc.value.context["column"] == "d"
    assert exc.value.context["declared_type"] == "date"
    assert "days since 1970-01-01" in str(exc.value)


def test_non_date_int_column_builds_without_date_guard() -> None:
    # Eq -> NotEq discriminator: an honest "int" column with a plain int value
    # must NOT trip the date guard — it builds fine. A NotEq mutant
    # (col_type != "date" True for an int col) would raise the date error on a
    # perfectly valid int column.
    df = _buffer_to_frame([{"n": 5}], [("n", "n", "int")])
    assert df["n"].to_list() == [5]


def test_float_column_with_int_builds_kills_date_guard_gte() -> None:
    # Eq -> GtE: a "float" column with an int builds cleanly (5 -> 5.0). The
    # GtE mutant ("float" >= "date" is True) trips the date guard and raises.
    # "float" is lexically > "date", so only >= (not ==/<=) misfires here.
    df = _buffer_to_frame([{"x": 5}], [("x", "x", "float")])
    assert df["x"].to_list() == [5.0]


def test_bool_column_int_raises_generic_not_date_message_kills_lte() -> None:
    # Eq -> LtE: a "bool" column with an int. Real code raises the GENERIC
    # strict-build error (Polars can't put 5 in a Boolean). The LtE mutant
    # ("bool" <= "date" is True) trips the date guard FIRST and raises the
    # date-specific message instead. Both raise, so we discriminate on which
    # message: real must NOT mention the days-since-epoch reinterpretation.
    with pytest.raises(ApiInputSchemaError) as exc:
        _buffer_to_frame([{"b": 5}], [("b", "b", "bool")])
    assert "days since 1970-01-01" not in str(exc.value)


def test_int_column_with_bool_raises_sibling_guard() -> None:
    # Sibling guard, line 757: an int/float column containing a bool must
    # raise (Polars would silently coerce True->1). Confirms the bool guard
    # operator (`col_type in ("int","float")`) stays live.
    with pytest.raises(ApiInputSchemaError) as exc:
        _buffer_to_frame([{"flag": True}], [("flag", "flag", "int")])
    assert exc.value.context["column"] == "flag"
    assert "boolean values" in str(exc.value)


# ─── _reject_reserved_leaf_collision (W1 fail-loud validator) ──────────
#   `own_depth_cols = [(n, leaf) for n, leaf, _t, d in col_specs if d == own_depth]`
#   `if any(leaf == _SCALAR_VALUE_LEAF ...) and len(own_depth_cols) > 1:`
#   Direct witnesses because the collision only arises from a hand-edited
#   config; the mutation run had no test entering this branch at all.


def test_reject_reserved_collision_raises_on_own_depth_sibling() -> None:
    # A `$value` leaf sharing the table's OWN array depth with a real sibling is
    # malformed and must raise. Uses a NON-interned `$value` copy so the
    # `leaf == _SCALAR_VALUE_LEAF` Eq -> Is mutation (identity would be False for
    # the copy) is killed alongside the `d == own_depth` NotEq/Gt/Lt/IsNot and
    # the `len > 1` Gt_Lt / NumberReplacer(> 2) mutations, which each drop the
    # own-depth pair and stop the raise.
    col_specs = [
        ("v", _noninterned(_SCALAR_VALUE_LEAF), "str", 1),
        ("sibling", "sibling", "str", 1),
    ]
    with pytest.raises(ApiInputSchemaError, match="reserved"):
        _reject_reserved_leaf_collision("t", 1, col_specs)


def test_reject_reserved_collision_allows_scalar_value_with_ancestor() -> None:
    # A `$value` column MAY coexist with an ANCESTOR column at a SHALLOWER depth
    # (a legitimate W1 distribution). own_depth_cols is then just the `$value`
    # column (len 1), so no raise. Kills the `len > 1` NumberReplacer(> 0), which
    # would wrongly raise on this single own-depth column.
    col_specs = [
        ("v", _SCALAR_VALUE_LEAF, "str", 1),
        ("ancestor", "ancestor", "str", 0),
    ]
    _reject_reserved_leaf_collision("t", 1, col_specs)


def test_reject_reserved_collision_ignores_deeper_column() -> None:
    # A DEEPER column (depth 2 under an own_depth of 1) is not an own-depth
    # sibling, so `$value` alone at own_depth means no raise. Kills the
    # `d == own_depth` Eq -> GtE mutation, which would fold the deeper column
    # into own_depth_cols and raise.
    col_specs = [
        ("v", _SCALAR_VALUE_LEAF, "str", 1),
        ("deep", "deep", "str", 2),
    ]
    _reject_reserved_leaf_collision("t", 1, col_specs)


def test_reject_reserved_collision_no_sentinel_allows_multiple() -> None:
    # Two ordinary own-depth columns (no `$value`) never collide. Both leaves are
    # lexically BELOW "$value" ('#' 0x23 < '$' 0x24), so the `leaf == $value`
    # Eq -> LtE mutation (leaf <= "$value" is True) would wrongly raise; the real
    # equality does not. Kills that LtE.
    col_specs = [
        ("a", "#alpha", "str", 1),
        ("b", "#beta", "str", 1),
    ]
    _reject_reserved_leaf_collision("t", 1, col_specs)


# ─── _reject_unexpressible_key (W1 fail-loud validator) ────────────────
#   `if key == _SCALAR_VALUE_LEAF: raise` ; `if "." in key: raise`


def test_reject_unexpressible_key_rejects_scalar_sentinel() -> None:
    # A source key equal to the reserved "$value" sentinel must raise. A
    # NON-interned copy still equals it, killing the `key == _SCALAR_VALUE_LEAF`
    # Eq -> Is (identity False -> no raise) and Eq -> Lt ("$value" < "$value" is
    # False -> no raise) mutations.
    with pytest.raises(ApiInputSchemaError, match="reserved"):
        _reject_unexpressible_key(_noninterned(_SCALAR_VALUE_LEAF))


def test_reject_unexpressible_key_rejects_dotted() -> None:
    # A key containing "." can't be addressed as a single leaf and must raise.
    with pytest.raises(ApiInputSchemaError, match="rename this field"):
        _reject_unexpressible_key("profile.age")


def test_reject_unexpressible_key_rejects_non_identifier() -> None:
    with pytest.raises(ApiInputSchemaError, match="identifier"):
        _reject_unexpressible_key("#id")


def test_reject_unexpressible_key_allows_identifier() -> None:
    _reject_unexpressible_key("_id2")
