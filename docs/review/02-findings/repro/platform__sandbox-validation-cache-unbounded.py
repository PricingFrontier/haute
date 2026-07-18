"""Reproduction for claim: sandbox-validation-cache-unbounded.

Claim: ``haute._sandbox._validation_cache`` is an unbounded module-global
dict keyed by ``(code, allow_imports)``.  Every distinct safe code string
that passes AST validation is inserted and never evicted, so a long-lived
server accumulates one entry per distinct previewed/traced fragment.

This repro:
  * Snapshots the cache size,
  * Validates N=10000 DISTINCT safe code strings,
  * Asserts the cache grew monotonically by exactly N (no cap/eviction),
  * Retrieves the bounded-cache contract the rest of the codebase uses
    (``haute._lru_cache.LRUCache.max_size``) to show the contrast: a
    bounded cache would NOT have grown by N once N exceeds its cap.

Isolation: ``validate_user_code`` performs only ``ast.parse`` + an AST
walk.  It touches no disk, no project root, no real project files.  No
tempfile is therefore required; nothing is read or written outside this
process's memory.

A wrong-value assertion (expected bounded growth vs. actual unbounded
growth) is what makes this a genuine reproduction rather than "something
raised".
"""

from __future__ import annotations

import sys

import haute._sandbox as sandbox
from haute._lru_cache import LRUCache


def main() -> int:
    N = 10_000

    cache = sandbox._validation_cache

    # Start from a clean, known baseline so the assertion is exact even if
    # some earlier import incidentally validated code.
    cache.clear()
    start = len(cache)
    assert start == 0, f"expected empty baseline after clear(), got {start}"

    # Feed N distinct *safe* fragments.  Each is a different key:
    #   df = df.with_columns(pl.lit(<i>))
    # These all pass the AST validator (no dunder/import/getattr/class).
    for i in range(N):
        code = f"df = df.with_columns(pl.lit({i}))"
        sandbox.validate_user_code(code)

    end = len(cache)
    growth = end - start

    print(f"[repro] baseline cache size        : {start}")
    print(f"[repro] distinct safe fragments fed : {N}")
    print(f"[repro] cache size after           : {end}")
    print(f"[repro] net growth                 : {growth}")
    print(f"[repro] cache type                 : {type(cache).__name__}")
    print(f"[repro] has max_size attr          : {hasattr(cache, 'max_size')}")
    print(f"[repro] has maxsize/popitem evict  : "
          f"{any(hasattr(cache, a) for a in ('maxsize', 'cache_info'))}")

    # --- Contrast: what a BOUNDED cache (the codebase's own LRUCache,
    #     used by _feature_validation_cache and the preamble cache) would
    #     do under the same load.  This demonstrates the EXPECTED bounded
    #     behaviour the leaking cache violates.
    bound = 128  # stdlib lru_cache default; LRUCache instances here use small caps
    bounded: LRUCache[str, bool] = LRUCache(max_size=bound)
    for i in range(N):
        bounded.put(f"df = df.with_columns(pl.lit({i}))", True)
    bounded_size = len(bounded)
    print(f"[repro] bounded LRUCache(max_size={bound}) size after {N} puts: {bounded_size}")

    # ---- The load-bearing assertions ----

    # 1. The validation cache grew by EXACTLY N — i.e. it retained every
    #    single distinct fragment.  An unbounded dict does this; a bounded
    #    cache cannot once N exceeds its cap.
    assert growth == N, (
        f"EXPECTED unbounded growth of {N} (monotonic, no eviction); "
        f"got net growth {growth}. If this is < N, the cache gained a bound."
    )

    # 2. Sanity: the cache exposes none of the bounded-cache machinery
    #    (no max_size, no maxsize/cache_info). It is a bare dict.
    assert isinstance(cache, dict), f"expected bare dict, got {type(cache)!r}"
    assert not hasattr(cache, "max_size"), "bare dict unexpectedly has max_size"

    # 3. Contrast: the codebase's own bounded cache caps at its max_size
    #    under the identical N-distinct-key load. This proves a bound is
    #    both achievable and the established pattern here.
    assert bounded_size == bound, (
        f"bounded LRUCache should cap at {bound}; got {bounded_size}"
    )

    print()
    print("REPRODUCED: _validation_cache retained all "
          f"{growth} distinct fragments with no eviction, while the "
          f"codebase's bounded LRUCache capped at {bounded_size} under the "
          "same load. The validation cache is unbounded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
