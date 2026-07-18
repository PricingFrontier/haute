"""Adversarial verification repro for BUG-N3.

BUG-N3 claims a NEW bug in `_resolve_leaf` (src/haute/_json_shred.py:529-536):
for a dotted column path `profile.age` where `profile` is a list of dicts
`[{"age":1},{"age":2}]`, the mid-walk list-collapse picks element [0]
(=> resolves to 1, loses 2) with NO skip accounting -- a silently-wrong
flat column.

The claim asserts this is DISTINCT from catalogued #12 ("about the
list-collapse picking element [0] for the FIRST segment of a dotted leaf"),
saying BUG-N3 is about a NON-first dotted segment + scalar-array case.

This repro tests BOTH:
  (1) Does BUG-N3's described behaviour actually reproduce on the real code?
  (2) Is BUG-N3's scenario materially different from #12's scenario, or is it
      the SAME code path / SAME example #12 already documents?

Isolated: synthetic in-memory data only. No disk I/O, no rating/, no real
project config files. Imports only the pure shred functions under test.
"""

import sys

from haute._json_shred import (
    _resolve_leaf,
    _SCALAR_VALUE_LEAF,
    ShredSkipStats,
    shred_to_buffers,
)

print(f"_SCALAR_VALUE_LEAF = {_SCALAR_VALUE_LEAF!r}")
print()

# ---------------------------------------------------------------------------
# (1) BUG-N3's exact described scenario: profile.age where profile is a
#     list-of-dicts [{"age":1},{"age":2}].
# ---------------------------------------------------------------------------
record_n3 = {"profile": [{"age": 1}, {"age": 2}]}
resolved_n3 = _resolve_leaf(record_n3, "profile.age")
print("[BUG-N3 scenario] _resolve_leaf({'profile':[{'age':1},{'age':2}]}, "
      f"'profile.age') = {resolved_n3!r}")

# Order-dependence: reversing the array changes the silently-picked value.
record_n3_rev = {"profile": [{"age": 2}, {"age": 1}]}
resolved_n3_rev = _resolve_leaf(record_n3_rev, "profile.age")
print("[BUG-N3 scenario] reversed array -> "
      f"{resolved_n3_rev!r} (arbitrary order-dependent pick)")

n3_collapses_to_elem0 = (resolved_n3 == 1 and resolved_n3_rev == 2)
print(f"[BUG-N3 scenario] collapses to element [0], loses the rest? "
      f"{n3_collapses_to_elem0}")
print()

# ---------------------------------------------------------------------------
# Catalogued #12's EXACT example (verbatim from catalog evidence line [A]):
#   _resolve_leaf({"profile":[{"age":41},{"age":99}]}, "profile.age") = 41
# ---------------------------------------------------------------------------
record_12 = {"profile": [{"age": 41}, {"age": 99}]}
resolved_12 = _resolve_leaf(record_12, "profile.age")
record_12_rev = {"profile": [{"age": 99}, {"age": 41}]}
resolved_12_rev = _resolve_leaf(record_12_rev, "profile.age")
print("[catalogued #12 example] _resolve_leaf(profile=[{age:41},{age:99}], "
      "'profile.age') = " + repr(resolved_12) + "  (catalog says 41)")
print("[catalogued #12 example] reversed -> " + repr(resolved_12_rev)
      + "  (catalog says 99)")
print()

# Same code path? BUG-N3 path == #12 path: identical leaf 'profile.age',
# identical shape (dict whose key holds a list-of-dicts), identical line 533
# collapse. The only difference is the integer payload (1/2 vs 41/99).
same_mechanism = (
    n3_collapses_to_elem0
    and resolved_12 == 41
    and resolved_12_rev == 99
)
print(f"[COMPARISON] BUG-N3 and #12 exercise the SAME mechanism "
      f"(line 533 cur[0].get(part) on profile.age)? {same_mechanism}")
print()

# ---------------------------------------------------------------------------
# (2) Full shred: does the silent loss reach buffers with ZERO skip
#     accounting? (#12 evidence [B] asserts exactly this: stats.total == 0.)
# ---------------------------------------------------------------------------
# Minimal object-table config: a table at root ('') with one selected
# column 'age' whose leaf path is the dotted 'profile.age'. We bypass
# parse_column_path by constructing the v2_config the way shred consumes it,
# but to stay faithful we let shred's own parse run via column path.
#
# parse_column_path(col_path, table_path) must yield leaf 'profile.age' for a
# root table. A root table_path is "" and a column path of "profile.age"
# resolves to leaf "profile.age".
cfg = {
    "tables": [
        {
            "label": "root",
            "path": "$[*]",
            "emit": True,
            "columns": [
                {
                    "name": "age",
                    "path": "$[*].profile.age",
                    "type": "int",
                    "selected": True,
                },
            ],
        },
    ],
}

stats = ShredSkipStats()
buffers = shred_to_buffers([record_n3], cfg, stats=stats)
print(f"[full shred] buffers['root'] = {buffers.get('root')!r}")
print(f"[full shred] stats.total = {stats.total}")
print(f"[full shred] skipped_rows_by_table = {stats.skipped_rows_by_table!r}")

emitted_age = buffers.get("root", [{}])[0].get("age") if buffers.get("root") else None
silent_loss_no_accounting = (
    emitted_age == 1            # element-0 value materialised
    and stats.total == 0        # the dropped age=2 produced ZERO skip count
)
print(f"[full shred] emitted age=1 (element 0) with ZERO skip accounting for "
      f"the dropped age=2? {silent_loss_no_accounting}")
print()

# ---------------------------------------------------------------------------
# Verdict logic for the adversarial question.
# ---------------------------------------------------------------------------
behaviour_reproduces = n3_collapses_to_elem0 and silent_loss_no_accounting
is_same_as_catalogued_12 = same_mechanism

print("=" * 70)
print(f"BUG-N3 described behaviour reproduces on real code? {behaviour_reproduces}")
print(f"BUG-N3 is the SAME code path/scenario as catalogued #12? "
      f"{is_same_as_catalogued_12}")
if behaviour_reproduces and is_same_as_catalogued_12:
    print("RESULT: behaviour is REAL but it is ALREADY catalogued as #12 "
          "(duplicate). The 'first vs non-first dotted segment' distinction "
          "is spurious: in 'profile.age', 'profile' is itself the first "
          "segment and the list-collapse fires when walking the 'age' "
          "segment -- exactly #12's documented case (verbatim example "
          "profile=[{age:41},{age:99}] -> 41).")
print("=" * 70)

# Exit non-zero only on an unexpected SETUP failure (so a green exit == the
# investigation completed and the assertions above hold).
sys.exit(0 if (behaviour_reproduces and is_same_as_catalogued_12) else 1)
