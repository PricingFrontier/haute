# CLEARED — behaviours checked and found correct, and findings killed in verification

Do **not** "fix" anything in this file. Each entry was explicitly investigated (code + empirical
probe where noted). If an implementation wave appears to require changing one of these, stop and
re-read the linked evidence first.

## Findings refuted by adversarial verification

### CORE-03 (was HIGH): "column-relevance pruning silently drops genuine contributors" — REFUTED
The claimed mechanism required the targeted `ref_cols` walk to skip an opaque origin's ancestors.
It cannot: `_prune_to_column_relevance` seeds the ancestor walk from **all** origins
(`trace.py:899` `contributing_ids = set(origin_ids)`, queue at `:909`), so an origin whose
expression failed to parse still drags its full ancestor chain into the trace. The targeted walk is
**strictly additive** over the keep-all fallback (`keep_ids = ancestor_ids | origin_ids |
contributing_ids`). Verified empirically with the finding's own "most reachable" scenario — a
transparent origin plus an opaque (`if True:`-wrapped) origin whose side-branch input survived
pruning (`repros/verify_core03.py`). Residual corner (an `.alias('col')`-form origin whose
recompute is value-identity on the traced row): three coincidences deep with identity impact —
recorded here as not actionable. Optional cheap pin: turn `verify_core03.py`'s Case B into a
regression test asserting opaque origins retain their ancestors.
*(Related, kept: the carrier-keep check at `trace.py:907` retains pass-through carriers — that is
display noise, not omission, and the frontend renders carriers un-glowed by design; see
STRENGTHS.md "three-tier highlight".)*

## Suspicions raised in review briefs that did not survive scrutiny

1. **Trace/preview cache double-memory blow-up** — cleared. Shared `DataFrame` references, RLock'd
   `FingerprintCache`; no corruption, no 2× resident copy for the reuse path. Only the *byte
   accounting* double-counts (T08.3).
2. **`trace.py:513-524` partial-rows branch shows unverified rows** — not reachable through the
   API: cold path runs `swallow_errors=False` (first failure raises), preview-reuse path requires
   the full ancestor chain. Kept only as an elegance deletion (T02/CORE-10).
3. **`_detect_rename` value-equality false renames** — refuted; detection is code-based
   (`.rename({...})` / pure `pl.col('old')`), equal-valued distinct columns produce no chain
   (`repros/probe_rename_waterfall.py`).
4. **FR-11 `all_steps.index(current_step)` mis-identification** — refuted as a correctness risk;
   node_ids are unique per step list, so dataclass value-equality cannot tie two steps. Style/O(n)
   only (T10.7).
5. **DataPreview sends display-rounded values as `row_values`** — refuted; it sends the raw parsed
   backend row (`DataPreview.tsx:274-276`); formatting is render-only.
6. **A→B rapid-click race shows the wrong trace** — refuted; request-sequence token +
   AbortController, pinned by `useTracing.test.ts:178-210`.
7. **`correlation_diagnostics` silently dropped by the UI** — refuted; rendered as an amber
   warning banner (`TracePanel.tsx:124-153`) with contract-test coverage.
8. **First trace click re-executes a pipeline the preview already ran (2× waste)** — reduced: the
   reuse machinery is inert for the GUI's target-only preview flow (keys never match), but the
   preview never materialised the ancestors, so nothing is duplicated except the target node. The
   dead-optimization complexity is the actual finding (T08.2).
9. **Waterfall arithmetic doesn't reconcile (pre-C8 behaviour)** — refuted for the current tree;
   contributions derive from consecutive observed values and reconcile exactly; failures produce
   the structured error payload (verified end-to-end; see STRENGTHS.md).
10. **Supersession/timeout machinery unsound** — refuted; deferred limiter release and
    permit-ownership logic are correct. The one real nuance is slot-holding by superseded active
    work (T07.5). Superseded requests reaching the client are silently ignored **by design**
    (client sequence guard) — do not add a toast for them.
11. **Rating key matching / banding boundaries / optimiser-apply reconciliation drift from the
    engine** — refuted for the key/boundary/selection decisions (shared `normalise_rating_key`,
    Float32-faithful coercion verified by `repros/probe_f32.py`, reconcile-or-raise). The four
    *presentation* drifts that are real are precisely scoped in T04 — nothing beyond them.
12. **W4/W3a/C8 commits claim more than they deliver** — refuted; their claims verified
    (fail-loud matcher, real banding bodies, observed-value waterfall). Their gap is what they
    *didn't* touch (T02, T03).
