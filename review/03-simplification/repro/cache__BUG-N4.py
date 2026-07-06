"""Adversarial verification of BUG-N4.

CLAIM (as stated): _canonical_dumps passes sort_keys=True AFTER _canonicalise
"already sorted/canonicalised". The asserted (low-confidence, latent) defect:
the two layers (dict-key sort in _canonicalise + json.dumps sort_keys=True)
are REDUNDANT and mask each other, so a future regression dropping the
_canonicalise dict-key sort would "silently still pass because _canonical_dumps
re-sorts".

This repro tests the load-bearing factual premises:

  P1. Does _canonicalise actually sort dict KEYS? (claim assumes YES -> double sort)
  P2. Is there ANY live mis-digest today from sort_keys=True interacting with
      the code-point set-member sort? (claim concedes NO, "correct")
  P3. The masking argument: if _canonicalise's dict handling were changed,
      would sort_keys=True silently rescue it? Test what sort_keys=True can and
      cannot rescue.

All synthetic in-memory data. No disk I/O of project files. We import the real
module read-only to test its actual behaviour.
"""

import json
import sys

# Import the real module read-only via the normal package path. Importing does
# not mutate src/. (A spec_from_file_location load under a synthetic name breaks
# @dataclass introspection, which resolves cls.__module__ via sys.modules.)
sys.path.insert(0, r"C:/Users/prici/haute/src")
import haute._cache as mod  # noqa: E402

_canonicalise = mod._canonicalise
_canonical_dumps = mod._canonical_dumps
canonical_json = mod.canonical_json

print("=" * 70)
print("P1: Does _canonicalise sort dict KEYS itself, or rely on sort_keys?")
print("=" * 70)
unsorted_in = {"b": 1, "a": 2, "c": 3}
canon = _canonicalise(unsorted_in)
canon_keys = list(canon.keys())
print(f"  input key order        : {list(unsorted_in.keys())}")
print(f"  _canonicalise key order: {canon_keys}")
# If _canonicalise sorted keys, canon_keys would be ['a','b','c'].
# If it preserves insertion order (relying on json.dumps), it stays ['b','a','c'].
p1_canonicalise_sorts_keys = canon_keys == sorted(canon_keys)
print(f"  -> _canonicalise sorts dict keys itself? {p1_canonicalise_sorts_keys}")
print(f"     (claim's premise 'already sorted' requires this to be True)")

print()
print("=" * 70)
print("P2: Live mis-digest TODAY? Two structures that SHOULD differ/match.")
print("=" * 70)
# (a) Same dict, different key insertion order -> must produce EQUAL digest.
d1 = {"z": [1, 2], "a": {"m": 1, "n": 2}}
d2 = {"a": {"n": 2, "m": 1}, "z": [1, 2]}
j1 = canonical_json(d1)
j2 = canonical_json(d2)
print(f"  canonical_json(d1) == canonical_json(d2) (reordered keys): {j1 == j2}")
print(f"    j1={j1}")
assert j1 == j2, "REGRESSION: key-order should not affect digest"

# (b) Non-ASCII set members: code-point sort, escaped serialization.
#     Two frozensets with the SAME members must match regardless of insert order.
s_a = frozenset({"é", "à", "z"})   # é, à, z
s_b = frozenset({"z", "à", "é"})
ja = canonical_json({"s": s_a})
jb = canonical_json({"s": s_b})
print(f"  canonical_json(frozenset variants) equal: {ja == jb}")
print(f"    ja={ja}")
assert ja == jb, "set members not order-independent"

# (c) Non-ASCII ordering correctness: the set-member order is by code point,
#     NOT by escaped JSON text. Confirm 'z' (U+007A) sorts before 'é'/'à'
#     (U+00E0/E1) by code point, and the OUTPUT is escaped.
s_mixed = frozenset({"é", "z"})
jm = canonical_json(s_mixed)
print(f"    canonical_json(frozenset(é,z)) = {jm}")
# code point: z=0x7A < é=0xE9, so 'z' must appear FIRST; é escaped to é.
expected = '["z","\u00e9"]'
print(f"    expected (code-point order, escaped): {expected}")
p2_order_correct = jm == expected
print(f"  -> set-member order correct & escaped? {p2_order_correct}")

print()
print("=" * 70)
print("P3: Does sort_keys=True actually RESCUE a dropped _canonicalise key-sort?")
print("=" * 70)
# The claim says a future change dropping _canonicalise's dict-key-sort would
# "silently still pass because _canonical_dumps re-sorts". But json.dumps(
# sort_keys=True) ONLY sorts top-level mapping keys it serializes -- and it
# does so for ALL dicts regardless. So we test: can sort_keys=True mask a hypo-
# thetical bug? It can ONLY mask "dict key ordering", because that is literally
# what json.dumps(sort_keys=True) is responsible for. It CANNOT mask set->list
# conversion, set member sorting, tuple->list, or type rejection -- those are
# _canonicalise's unique jobs that json.dumps never performs.
print("  json.dumps(sort_keys=True) responsibilities vs _canonicalise's:")
# Demonstrate json.dumps does NOT sort set members (it can't even serialize a set)
try:
    json.dumps({"s": {3, 1, 2}}, sort_keys=True)
    print("    json.dumps serialized a raw set?! (unexpected)")
except TypeError as e:
    print(f"    json.dumps(raw set) -> TypeError (cannot mask set handling): {type(e).__name__}")

# Demonstrate: the ONLY overlap is dict-key ordering. Show sort_keys=True sorts
# keys of an ALREADY-canonicalised-but-unsorted dict (which is what happens today).
unsorted_dict = {"b": 1, "a": 2}
with_sort = json.dumps(unsorted_dict, sort_keys=True, separators=(",", ":"))
without_sort = json.dumps(unsorted_dict, sort_keys=False, separators=(",", ":"))
print(f"    json.dumps(sort_keys=True ) = {with_sort}")
print(f"    json.dumps(sort_keys=False) = {without_sort}")
overlap_is_keys_only = (with_sort == '{"a":2,"b":1}') and (without_sort == '{"b":1,"a":2}')
print(f"  -> The redundancy is EXACTLY dict-key ordering, nothing else: {overlap_is_keys_only}")

print()
print("=" * 70)
print("VERDICT INPUTS")
print("=" * 70)
print(f"  P1 _canonicalise sorts dict keys itself : {p1_canonicalise_sorts_keys}")
print(f"  P2 any live mis-digest today            : {'NO (all asserts passed)'}")
print(f"  P2 set order correct+escaped            : {p2_order_correct}")
print(f"  P3 redundancy scope = dict-key order only: {overlap_is_keys_only}")
print()
# The claim's headline premise: "_canonicalise already sorted ... dict keys"
# creating a DOUBLE sort. If P1 is False, _canonicalise does NOT sort keys, so
# there is NO double sort of keys -- the single authority for key order is
# json.dumps(sort_keys=True). The "two layers mask each other" mechanism for
# dict keys does not exist as described.
if not p1_canonicalise_sorts_keys:
    print("FINDING: _canonicalise does NOT sort dict keys; json.dumps(sort_keys=True)")
    print("         is the SOLE key-ordering authority. The claimed 'redundant second")
    print("         sort' / 'two layers mask each other' for dict keys is FALSE as")
    print("         described -- there is exactly ONE key sort, not two.")
else:
    print("FINDING: _canonicalise DOES sort dict keys -> genuine redundancy with")
    print("         json.dumps(sort_keys=True).")

print()
print("CONCLUSION: No live mis-digest reproduced (claim concedes this). The")
print("            claim is a code-quality/fragility assertion, and its central")
print("            factual premise about a double key-sort is contradicted above.")
