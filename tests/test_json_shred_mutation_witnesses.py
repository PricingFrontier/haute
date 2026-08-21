"""Mutation witnesses for the v2 JSON-shred LOGIC core (notes-haute W1/W2).

Targeted unit witnesses for the pure, data-only functions of
:mod:`haute._json_shred` — scalar coercion, type inference/widening, column
naming, skip accounting, the emitting predicate, and leaf resolution. These are
the load-bearing half of the round-trip invariant; each test discriminates a
branch decision so a Cosmic Ray mutation flips the asserted value, not merely
"it still runs". The cache/build/load lifecycle (filesystem-bound) is covered by
the route/integration suites, not here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import haute._json_shred as shred_module
from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _SCALAR_VALUE_LEAF,
    ShredSkipStats,
    _assign_column_names,
    _coerce_scalar,
    _infer_type,
    _resolve_leaf,
    _scalar_to_str,
    _widen_type,
    table_is_emitting,
)


def test_json_shred_resource_defaults_are_explicit_contracts() -> None:
    assert shred_module._SHRED_EXECUTION_CHECKPOINT_ROWS == 1_024
    assert shred_module._DIRECT_SPILL_MAX_ROWS_DEFAULT == 10_000
    assert shred_module._DIRECT_SPILL_MAX_BYTES_DEFAULT == 16 * 1024 * 1024
    assert shred_module._STRUCTURED_INPUT_MAX_RECORD_BYTES_DEFAULT == 64 * 1024 * 1024
    assert shred_module._STRUCTURED_INPUT_PARSE_CHUNK_BYTES == 64 * 1024
    assert shred_module._DATA_FILE_SIGNATURE_MEMO_MAX_ENTRIES == 256
    assert shred_module._NATIVE_REVISION_SCHEMA_VERSION == 1
    assert shred_module._WINDOWS_EPOCH_OFFSET_100NS == 116_444_736_000_000_000
    assert shred_module._FSCTL_READ_FILE_USN_DATA == 0x000900EB
    assert shred_module._WINDOWS_USN_OUTPUT_BUFFER_SIZE == 4_096
    assert shred_module._RUNTIME_SNAPSHOT_DIGEST_PREFIX_HEX == 32
    assert shred_module._RUNTIME_OWNER_FORMAT_VERSION == 1
    assert shred_module._RUNTIME_STORAGE_BUDGET_DEFAULT_BYTES == 4 * 1024 * 1024 * 1024
    assert shred_module._RUNTIME_STORAGE_ORPHAN_GRACE_DEFAULT_SECONDS == 60 * 60
    assert shred_module.RUNTIME_SNAPSHOT_CACHE_MAX_ENTRIES == 64
    assert shred_module.RUNTIME_SNAPSHOT_CACHE_MAX_BYTES == 512 * 1024 * 1024


def test_json_shred_internal_value_objects_preserve_mutability_contracts() -> None:
    progress = shred_module._ShredExecutionProgress(None)
    assert not hasattr(progress, "__dict__")
    progress.work_since_checkpoint = 1
    assert progress.work_since_checkpoint == 1

    revision = shred_module._StrongFileRevision((1, 2), 3, 4, 5)
    signature_record = shred_module._DataFileSignatureRecord(1, 2, "digest", None)
    prepared = shred_module.PreparedPerPortCacheBuild(
        data_path="data.json",
        cache_dir="cache",
        staging_dir=None,
        schema_fingerprint="schema",
        data_file_signature={},
        summary={},
    )
    snapshot = shred_module._VerifiedRuntimeSnapshot(revision, Path("snapshot.parquet"), 7)
    xml_shape = shred_module._XmlRecordShape(repeated_object_children=True)
    probe_failure = shred_module._CacheProbeFailure(reason="invalid")

    for value, field, replacement in (
        (revision, "size", 99),
        (signature_record, "sha256", "changed"),
        (prepared, "no_op", True),
        (snapshot, "size", 99),
        (xml_shape, "repeated_object_children", False),
        (probe_failure, "reason", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)

    for value in (revision, signature_record, prepared, snapshot, xml_shape):
        assert not hasattr(value, "__dict__")

    assert prepared.no_op is False


# ─── _scalar_to_str — JSON-style booleans ──────────────────────────


def test_scalar_to_str_renders_json_booleans() -> None:
    # JSON spells booleans lower-case, NOT Python's "True"/"False".
    assert _scalar_to_str(True) == "true"
    assert _scalar_to_str(False) == "false"
    assert _scalar_to_str(5) == "5"
    assert _scalar_to_str(1.5) == "1.5"


# ─── _coerce_scalar — scalar-array element coercion to the declared type ──


def test_coerce_scalar_none_passes_through() -> None:
    assert _coerce_scalar(None, "str") is None
    assert _coerce_scalar(None, "float") is None


def test_coerce_scalar_str_token_stringifies_non_strings() -> None:
    # A str column keeps strings as-is and renders everything else (the
    # `type_token == "str"` branch, and the isinstance-str guard within it).
    assert _coerce_scalar("hi", "str") == "hi"
    assert _coerce_scalar(7, "str") == "7"
    assert _coerce_scalar(True, "str") == "true"


def test_coerce_scalar_float_token_widens_int_but_not_bool() -> None:
    # A float column promotes ints to float, leaves real floats, and explicitly
    # leaves bools alone (so `_buffer_to_frame` can reject them, not silently 0/1).
    assert _coerce_scalar(3, "float") == 3.0
    assert isinstance(_coerce_scalar(3, "float"), float)
    assert _coerce_scalar(2.5, "float") == 2.5
    assert _coerce_scalar(True, "float") is True


def test_coerce_scalar_other_tokens_return_value_unchanged() -> None:
    # int/bool/unknown tokens fall through untouched (no coercion).
    assert _coerce_scalar(9, "int") == 9
    assert _coerce_scalar("x", "bool") == "x"


def test_coerce_scalar_str_branch_discriminates_exact_token() -> None:
    # The ``type_token == "str"`` dispatch (L105) must be an EXACT match.
    # Kills '==' -> '>=': a token lexically ABOVE "str" ("zzz" > "str") must NOT
    # enter the str branch (a '>=' mutant would, stringifying the int).
    assert _coerce_scalar(7, "zzz") == 7
    assert not isinstance(_coerce_scalar(7, "zzz"), str)
    # Kills '==' -> 'is': at the real call site the token is an orjson-loaded
    # (non-interned) string, so an identity check would wrongly miss it. A
    # runtime-built "str" is a distinct object from the source literal.
    non_interned = "".join(list("str"))
    assert _coerce_scalar(7, non_interned) == "7"


def test_coerce_scalar_float_branch_discriminates_exact_token() -> None:
    # The ``type_token == "float"`` dispatch (L107) must be an EXACT match, and
    # the int-guard (L112) / bool-guard (L108) must fire only for the right shape.
    # Kills '==' -> '>=': "int" > "float" lexically, so a '>=' mutant would widen
    # an int under the "int" token; the real code leaves it an int.
    assert not isinstance(_coerce_scalar(3, "int"), float)
    assert _coerce_scalar(3, "int") == 3
    # Kills '==' -> '<=': "aaa" < "float", so a '<=' mutant would widen here too.
    assert not isinstance(_coerce_scalar(3, "aaa"), float)
    # The real (equal) token DOES widen the int — kills '!=', '>', '<', 'is not',
    # AddNot on L107 and the AddNot on L112 (all of which skip the widening).
    assert isinstance(_coerce_scalar(3, "float"), float)
    # A non-interned "float" still widens — kills '==' -> 'is' on L107.
    non_interned = "".join(list("float"))
    assert isinstance(_coerce_scalar(3, non_interned), float)


# ─── _infer_type — single-scalar type token (bool BEFORE int) ───────


def test_infer_type_distinguishes_bool_from_int() -> None:
    # bool must be checked first — True is an int in Python, so order matters.
    assert _infer_type(True) == "bool"
    assert _infer_type(5) == "int"
    assert _infer_type(1.5) == "float"
    assert _infer_type("x") == "str"


# ─── _widen_type — narrowest type fitting both observations ─────────


def test_widen_type_none_existing_takes_new() -> None:
    assert _widen_type(None, "int") == "int"


def test_widen_type_same_is_idempotent() -> None:
    assert _widen_type("int", "int") == "int"


def test_widen_type_same_token_match_is_value_equality_not_identity() -> None:
    # ``existing == new`` (L1162) is value equality. At the real call site
    # ``existing`` is a prior widen result and ``new`` an _infer_type literal, so
    # a non-interned existing must still match — kills '==' -> 'is' (which would
    # fall through to the {int,float} check, miss, and wrongly widen to "str").
    non_interned_int = "".join(list("int"))
    assert _widen_type(non_interned_int, "int") == "int"


def test_widen_type_int_and_float_widen_to_float_either_order() -> None:
    assert _widen_type("int", "float") == "float"
    assert _widen_type("float", "int") == "float"


def test_widen_type_any_other_disagreement_is_str() -> None:
    assert _widen_type("int", "str") == "str"
    assert _widen_type("bool", "int") == "str"  # NOT the int/float pair


# ─── _assign_column_names — bare-where-unique, qualified-on-collision ──


def test_assign_column_names_bare_when_unique() -> None:
    assert _assign_column_names([("a",), ("b",)]) == {("a",): "a", ("b",): "b"}


def test_assign_column_names_qualifies_colliding_leaves() -> None:
    # Two object paths whose bare leaf collides ("selected") take their full
    # underscore-joined path; a unique leaf stays bare.
    names = _assign_column_names([("x", "selected"), ("y", "selected"), ("z",)])
    assert names == {
        ("x", "selected"): "x_selected",
        ("y", "selected"): "y_selected",
        ("z",): "z",
    }


def test_assign_column_names_dedups_residual_clash_with_numeric_suffix() -> None:
    # A genuine residual clash: ("a","b") and ("c","b") share bare leaf "b" so
    # both qualify; ("a","b") qualifies to "a_b", which collides with the bare
    # unique leaf of ("a_b",). The dedup loop (L1190) appends a numeric suffix
    # starting at i=2 (L1193), so ("a_b",) becomes "a_b_2".
    #
    # Exact-name assertions (not just "all unique") pin the suffix value, killing
    # the NumberReplacer on the i=2 seed (i=1 would give "a_b_1"); the
    # ZeroIterationForLoop on the dedup loop (no dedup -> "a_b" appears twice).
    names = _assign_column_names([("a", "b"), ("c", "b"), ("a_b",)])
    assert names[("a", "b")] == "a_b"
    assert names[("c", "b")] == "c_b"
    assert names[("a_b",)] == "a_b_2"
    assert len(set(names.values())) == 3  # all unique


def test_assign_column_names_double_clash_increments_suffix() -> None:
    # Force the dedup ``while`` (L1194) to actually iterate: ("a_b_2",) is seen
    # FIRST (claiming "a_b_2"), so when ("a_b",) later clashes on "a_b" the loop
    # finds "a_b_2" already taken and must advance i 2 -> 3, landing on "a_b_3".
    # The exact "a_b_3" pins ``i += 1`` (L1195): i += 2 would give "a_b_4"; i += 0
    # would spin forever on "a_b_2" and be timeout-killed.
    names = _assign_column_names([("a", "b"), ("c", "b"), ("a_b_2",), ("a_b",)])
    assert names[("a_b_2",)] == "a_b_2"
    assert names[("a_b",)] == "a_b_3"
    assert len(set(names.values())) == 4


# ─── ShredSkipStats — two skip units, never conflated ──────────────


def test_skip_stats_counts_records_and_rows_separately() -> None:
    s = ShredSkipStats()
    s.count_record_skip()
    s.count_record_skip()
    s.count_row_skip("drivers")
    s.count_row_skip("drivers")
    s.count_row_skip("vehicles")
    assert s.skipped_records == 2
    assert s.skipped_rows_by_table == {"drivers": 2, "vehicles": 1}


def test_skip_stats_total_sums_records_plus_all_rows() -> None:
    # total = records + sum(rows). A '+' -> '*'/'**' mutation (2 vs 2+3=5) or a
    # dropped term is caught by the exact sum.
    s = ShredSkipStats()
    s.count_record_skip()
    s.count_record_skip()
    s.count_row_skip("a")
    s.count_row_skip("a")
    s.count_row_skip("b")
    assert s.total == 5  # 2 records + 3 rows


def test_skip_stats_as_meta_shape() -> None:
    s = ShredSkipStats()
    s.count_record_skip()
    s.count_row_skip("t")
    assert s.as_meta() == {"records": 1, "rows_by_table": {"t": 1}}


# ─── table_is_emitting — emit AND at least one selected column ───────


def test_table_is_emitting_requires_emit_and_a_selected_column() -> None:
    assert table_is_emitting({"emit": True, "columns": [{"selected": True}]}) is True
    # emit on but no column selected → not emitting.
    assert table_is_emitting({"emit": True, "columns": [{"selected": False}]}) is False
    # selected column but emit off → not emitting.
    assert table_is_emitting({"emit": False, "columns": [{"selected": True}]}) is False
    # ANY selected column suffices.
    assert (
        table_is_emitting({"emit": True, "columns": [{"selected": False}, {"selected": True}]})
        is True
    )


def test_table_is_emitting_tolerates_non_dict_shapes() -> None:
    # Runs against arbitrary on-disk configs — must not raise.
    assert table_is_emitting("not a dict") is False
    assert table_is_emitting({"emit": True, "columns": "not a list"}) is False
    assert table_is_emitting({"emit": True}) is False  # no columns key


# ─── _resolve_leaf — dotted walk, scalar sentinel, shape guards ─────


def test_resolve_leaf_plain_and_dotted() -> None:
    assert _resolve_leaf({"policy_id": 7}, "policy_id") == 7
    assert _resolve_leaf({"profile": {"age": 40}}, "profile.age") == 40
    assert _resolve_leaf({"a": 1}, "missing") is None


def test_resolve_leaf_scalar_value_sentinel_returns_the_element() -> None:
    # The reserved $value leaf means "the scalar element itself".
    assert _resolve_leaf(42, _SCALAR_VALUE_LEAF) == 42
    assert _resolve_leaf("s", _SCALAR_VALUE_LEAF) == "s"
    # A dict/list under $value is a shape mismatch → None.
    assert _resolve_leaf({"k": 1}, _SCALAR_VALUE_LEAF) is None
    assert _resolve_leaf([1, 2], _SCALAR_VALUE_LEAF) is None


def test_resolve_leaf_non_dict_is_none() -> None:
    assert _resolve_leaf(5, "a") is None
    assert _resolve_leaf(None, "a") is None


def test_resolve_leaf_raises_when_dotted_leaf_crosses_a_list() -> None:
    # W1 fix: a dotted leaf addresses 1-1 object nesting only. A list mid-walk
    # is a shape mismatch; silently taking the first element dropped the rest,
    # so it now fails LOUD (naming the offending leaf) rather than collapsing.
    with pytest.raises(ApiInputSchemaError, match="claims.amount"):
        _resolve_leaf({"claims": [{"amount": 3}, {"amount": 99}]}, "claims.amount")
    # Even a single-element list is a shape mismatch and raises (the schema
    # should model the array as a child table) — one element still discards
    # nothing visible but is the same mis-modelled shape as the multi case.
    with pytest.raises(ApiInputSchemaError):
        _resolve_leaf({"claims": [{"amount": 3}]}, "claims.amount")
    # An EMPTY list discards nothing (no element to drop) — not a conservation
    # violation. It resolves to None rather than raising, so data that mixes an
    # object with an occasional empty array at this key doesn't hard-fail (W1).
    assert _resolve_leaf({"claims": []}, "claims.amount") is None
