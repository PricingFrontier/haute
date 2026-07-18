# Upstream findings — `price_contour` library (NOT fixable in this repo)

Verified against the installed `price-contour` 0.4.x sources in
`.venv/Lib/site-packages/price_contour/`. The numeric core is compiled Rust
(`_price_contour.pyd`) and is **out of source reach** — findings below are in the Python
orchestration layer. File these against the library repo; none should be "fixed" by patching
the venv. Haute-side mitigations, where they exist, are already captured in P05/P06.

**Engine cost model (for context, loops cited):**
- Online solve: Rust dual ascent ≤ `max_iter`; DataFrame path adds one validation scan +
  (ratio) one linearisation pass (`solver.py:1044`, `:1124`).
- Ratebook solve: one Rust CD call — `max_cd_iterations × n_factors × grouped solves`
  (`ratebook.py:530`); `factor_columns=None` adds per-column screening solves (`:614`).
- Frontiers: `n_points_per_dim ** n_swept` full solves — Rust fast path (online, sum-only,
  all-swept; optional `parallel`, `solver.py:308`) or Python orchestrator
  (`_frontier_helpers.py:221`; always for ratebook, `ratebook.py:690`).

## U-01 — [HIGH] Ratebook save→load corrupts interaction factor keys containing `:`
`ratebook.py:177` (save: `k.replace("\x1f", ":")`), `:252` (load keeps `:`), `:206-209`
(`to_rating_entries` splits on `:` when no `\x1f` present). A level value like `"12:30"` in a
2-column interaction key splits into 3 parts after a round-trip; `zip(cols, parts)` silently
drops/mislabels. The `\x1f` unit separator was chosen for collision-freedom; save discards it.
**Fix:** persist raw `\x1f` (or store the separator in config.json and split on it).
**Haute exposure: none today** — Haute serialises factor tables itself
(`_serialise_ratebook_factor_tables`) and never uses `RatebookResult.save/load`.

## U-02 — [MEDIUM] Frontier cartesian product materialised before the max_total_points guard
`_frontier_helpers.py:252-258` (verified: `combos = _cartesian_product(per_axis)` precedes the
length check); mirrored `ratebook.py:829-839`. `n=1000, d=4` allocates 10¹² conceptually —
practically, large d×n allocates millions of lists before the rejection fires. **Fix:** check
`n ** len(swept_names)` arithmetically first.

## U-03 — [MEDIUM] Greedy NN ordering is O(points²·d) pure Python
`ratebook.py:1116-1129` — pairwise distance loop, recomputed per step, no index. At the 10,000
cap: ~10⁸ Python ops before any CD solve. The online Python sweep, by contrast, does **no**
ordering (`_frontier_helpers.py:265` raw cartesian order) — worse warm-start locality;
the two sweeps disagree. **Fix:** boustrophedon axis traversal (O(points)) shared by both.

## U-04 — [MEDIUM] Ratio frontier re-validates and re-linearises the full DataFrame per point
`solver.py:406-417` (fresh `OnlineOptimiser` + full `solve(df)` per combo — full validation +
linearisation each time); ratebook ratio path also rebuilds the QuoteGrid per point
(`ratebook.py:937`). Baseline totals and validation are point-invariant; only `L` changes.
**Fix:** hoist validation/baseline; update the single synthetic column per point.
**Haute exposure: none today** (pre-built grids, all-swept ranges).

## U-05 — [MEDIUM] `_discover_structure` screens the quote_id column as a candidate factor
`ratebook.py:646-673` — `[[col] for col in factors.columns]` includes `quote_id` when present;
a per-quote "factor" wins screening with near-perfect (maximally overfit) lift and is selected
silently. **Fix:** exclude the alignment column; warn on ≈n_quotes-cardinality candidates.
**Haute exposure: none** — Haute always passes explicit `factor_columns`
(`_optimiser_service.py:4106` rejects ratebook mode without it).

## U-06 — [MEDIUM] `_safe_ratio_from_columns` guards exact zero only
`_ratio_results.py:47-51` (verified) — `denom_total == 0.0` → NaN, but `1e-12` → a reported
ratio of `1e12` presented as a real achieved constraint. Docstring promises "near-zero …
handled gracefully". **Fix:** relative-epsilon threshold scaled to numerator magnitude.

## U-07 — [LOW] Ratebook frontier reports a missing constraint total as `0.0`; online uses NaN
`ratebook.py:986` (`.get(name, 0.0)`) vs `solver.py:434` (`.get(name, nan)`). `0.0` is
indistinguishable from a genuinely-zero total. **Fix:** NaN in both.

## U-08 — [LOW] `RatebookResult.save` factor filenames can collide
`ratebook.py:182` — `factor_name.replace(":", "_")`: spec `["a:b"]` vs interaction `["a","b"]`
both → `a_b.json`; second write silently clobbers. **Fix:** reversible encoding + collision
check.

## U-09 — [LOW] `RatebookResult` on-disk format has no schema version
`ratebook.py:157-172` / `:235-265` — unlike `ApplyOptimiser.save/load`, which writes
`"version": 1` and rejects unknown versions (`apply.py:420-471`). Combined with U-01 the
ratebook format has no forward-compat handle. **Fix:** mirror ApplyOptimiser's gate.

## U-10 — [LOW/PLAUSIBLE] Float32 decision precision for linearised ratio columns
`solver.py:1116-1119` (`(num - L·denom).cast(pl.Float32)`), `:1372-1379` (history replay in
f32). ~7 significant digits; near-cancellation at portfolio monetary scale can flip near-tie
argmax picks. Reporting is f64 (correct). **Fix:** document the f32 decision contract; optional
f64 grid mode if tie-stability matters.

## U-11 — [LOW/PLAUSIBLE] `_extrapolate_lambdas` clamps below but not above
`ratebook.py:1080-1088` — `max(0.0, lam + fraction·slope)` with unbounded `fraction`; a long
NN jump can inject an extreme warm-start λ, costing corrector iterations (never wrongness —
the corrector re-converges). **Fix:** clamp `fraction` to e.g. `[-1, 2]`.

## Feature requests worth filing alongside
- `should_stop: Callable[[], bool]` / deadline parameter on `solve()`/`frontier()` checked
  between dual-ascent iterations and frontier points — required for Haute's P05 to reach full
  interruptibility on the Rust fast path.
- `parallel=` support for the ratebook frontier (currently online-only).

## Cleared upstream (do not re-report)
- Fail-loud discipline throughout: no swallowed exceptions in the Python layer (sole `except`
  is the documented `PackageNotFoundError` version fallback, `__init__.py:13`).
- Null/NaN rejection on the DataFrame entry path is thorough and column-named
  (`solver.py:1639-1674`) — the grid-path gap is Haute's boundary to police (P03).
- Grid/context alignment enforced by 64-bit fingerprint + n_quotes match
  (`ratebook.py:1227-1261`).
- `_stitch_optimal_ratio_columns` inner-join + height assertion fails loudly on a missing step
  (`_ratio_results.py:96-107`).
- Ratio-label vs column collision rejected before the synthetic column could clobber data
  (`solver.py:1014-1022`).
- `ApplyOptimiser.save/load` version gate + unknown-key allowlist (`apply.py:420-471`).
- Predictor-corrector warm start: a bad predictor costs iterations, never a wrong answer
  (`ratebook.py:881-890`).
