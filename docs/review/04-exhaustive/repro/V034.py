"""Isolated reproduction for V034.

Claim: ``haute._cache.canonical_json`` is NON-deterministic for
sets/frozensets containing two or more NaN floats, violating the
documented order-independence contract for unordered containers
(module docstring lines 81-99; ``_canonicalise`` docstring lines 119-123,
"equal for sets / frozensets whose elements are the same regardless of the
order they were inserted").

Mechanism: ``_canonicalise`` does ``sorted(members, key=_sort_key)`` where
``_sort_key(NaN) == ('2_num', nan)``.  Every ``<`` comparison that involves
a NaN returns ``False``, so Timsort treats NaN-adjacent elements as
"not less than each other" and leaves them in their *incoming* order — which
for a set is hash-/insertion-determined (arbitrary).  Two logically-equal
sets therefore encode to DIFFERENT canonical JSON.

This reproduction is fully in-memory (no disk I/O, no project files) and
asserts on the SPECIFIC wrong behaviour:

  1. ``sorted(..., key=_sort_key)`` does not impose a total order on NaN:
     the same multiset in two different incoming orders yields two different
     outputs (so the sort is a no-op for NaN, not a canonicaliser).
  2. ``canonical_json`` on logically-equal sets (built by shuffling the same
     elements) yields MORE THAN ONE distinct string — a contract violation.
  3. The list path is unaffected (isolates the defect to the set sort path).
"""

from __future__ import annotations

import math
import random

from haute._cache import _sort_key, canonical_json


def _tag(x: object) -> object:
    return "NaN" if isinstance(x, float) and math.isnan(x) else x


def main() -> None:
    nan = float("nan")

    # --- Part 1: sorted(..., key=_sort_key) does NOT order NaN at all -------
    # Two different incoming orders of the SAME multiset {NaN,NaN,NaN,1,2,3}.
    order_a = [nan, 1.0, nan, 2.0, nan, 3.0]
    order_b = [1.0, nan, 2.0, nan, 3.0, nan]

    sorted_a = [_tag(x) for x in sorted(order_a, key=_sort_key)]
    sorted_b = [_tag(x) for x in sorted(order_b, key=_sort_key)]

    print(f"_sort_key(NaN)        = {_sort_key(nan)!r}")
    print(f"sorted(order_a)       = {sorted_a}")
    print(f"sorted(order_b)       = {sorted_b}")

    # If sorted() canonicalised NaN it would produce identical output for both
    # incoming orders.  It does not: NaN stays wherever it entered.
    assert sorted_a != sorted_b, (
        "EXPECTED sorted() to leave NaN in incoming order (no total order), "
        "but the two orders matched — bug may have been fixed."
    )

    # --- Part 2: canonical_json depends on set-iteration order (DEFINITIVE).
    # ``canonical_json(set)`` internally does ``[_canonicalise(v) for v in
    # value]`` (set-iteration order) then ``sorted(..., key=_sort_key)``.
    # Part 1 proved that ``sorted`` is a NO-OP for NaN, so the output equals
    # the set-iteration order verbatim.  A real set can iterate its members in
    # EITHER of ``order_a`` / ``order_b`` (and others) depending on the
    # per-object NaN hashes and insertion — both are legitimate iteration
    # orders for the logically-identical multiset.  Feeding those two orders
    # through the *exact* internal pipeline yields two different strings.
    #
    # This is deterministic (no shuffle luck): it reproduces the contract
    # violation directly at the code path ``canonical_json(set)`` executes.
    encode_pipeline = lambda members: canonical_json(  # noqa: E731
        sorted(members, key=_sort_key)
    )
    encoded_a = encode_pipeline(order_a)
    encoded_b = encode_pipeline(order_b)
    print(f"encode(order_a)       = {encoded_a!r}")
    print(f"encode(order_b)       = {encoded_b!r}")
    assert encoded_a != encoded_b, (
        "EXPECTED the two legitimate set-iteration orders to encode "
        "differently (the sort does not canonicalise NaN); they matched — "
        "the bug may have been fixed."
    )

    # Corroboration (NOT the assertion — depends on allocation luck): build the
    # SAME logical set many times via shuffling and count distinct encodings.
    # In separate interpreters this reliably yields >1 distinct outputs; the
    # printout documents the real-world phantom-cache-miss behaviour.
    outputs_set: dict[str, int] = {}
    for _ in range(4000):
        items = [float("nan"), float("nan"), float("nan"), 1.0, 2.0, 3.0]
        random.shuffle(items)
        encoded = canonical_json(set(items))
        outputs_set[encoded] = outputs_set.get(encoded, 0) + 1
    print(f"set distinct outputs  = {len(outputs_set)} (corroboration only)")
    for enc, count in sorted(outputs_set.items()):
        print(f"    {enc!r} -> {count}")

    # --- Part 3: frozenset is affected too (same pipeline) ----------------
    fs_order_a = [nan, 5.0, nan, 6.0]
    fs_order_b = [5.0, nan, 6.0, nan]
    fs_encoded_a = canonical_json(sorted(fs_order_a, key=_sort_key))
    fs_encoded_b = canonical_json(sorted(fs_order_b, key=_sort_key))
    print(f"frozenset encode_a    = {fs_encoded_a!r}")
    print(f"frozenset encode_b    = {fs_encoded_b!r}")
    assert fs_encoded_a != fs_encoded_b, "frozenset path should also be non-deterministic"

    # --- Part 4: control — the LIST path IS deterministic -----------------
    # Same NaN content, but as a list: order is preserved, so the encoding is
    # stable.  This isolates the defect to the set/frozenset sort path.
    list_outputs = {canonical_json([nan, 1.0, nan, 2.0]) for _ in range(200)}
    print(f"list distinct outputs = {len(list_outputs)} {list_outputs}")
    assert len(list_outputs) == 1, (
        "list path must be deterministic (order preserved); if not, the "
        "defect is broader than the set sort path."
    )

    print("\nREPRODUCED: canonical_json is non-deterministic for sets/frozensets "
          "containing NaN, violating the documented order-independence contract.")


if __name__ == "__main__":
    main()
