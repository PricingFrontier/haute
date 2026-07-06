# Tracing review — "click any price, see exactly how it was calculated"

**Date:** 2026-07-06 · **Base:** branch `code-fixes`, commit `220bcccd` (working tree; trace-file
"M" flags are CRLF noise — `git diff HEAD` empty for them)
**Question asked:** is the tracing implementation as efficient, performant, elegant and robust as
it could be — and as informative and easy to follow as possible?
**Method:** five specialist review passes (backend core correctness, per-node enrichment fidelity,
full-stack performance, frontend state/contract, informativeness/followability) over the full
stack (`trace.py`, `_trace_correlation.py`, `_trace_enrichment.py`, `_trace_waterfall.py`,
`_trace_export.py`, `routes/pipeline.py` trace endpoint, `useTracing.ts`, `TracePanel` +
`frontend/src/trace/*`), followed by an adversarial verification pass on every unreproduced
CRITICAL/HIGH claim. Every headline finding carries a runnable repro in `repros/` and was
**re-executed independently by the lead session** before entering this document. One reported HIGH
was killed in verification (see `CLEARED.md`); one reported MEDIUM was escalated to HIGH.

---

## Verdict

The architecture is genuinely right: a pure observation layer over preview frames, a correlation
matcher that (since W4) fails loud on ambiguity, a waterfall that derives every number from
observed values and refuses to render if it can't reconcile, purpose-built per-node-type detail
views, and a frontend with correctly-handled request races and calibrated runtime guards. The
June-wave hardening commits (W3a/W4/C8) do what they claim. But measured against "regulator-grade
explainability":

1. **The trust chain has two CRITICAL holes, both reproduced.** Relocate a clicked row after
   preview-cache eviction and the backend silently anchors the whole trace to the first of several
   identical-looking rows — every upstream value correct-for-the-wrong-policy, zero diagnostics
   (T02). Edit the pipeline (or let the file-watcher sync a colleague's edit) while a trace is
   open and the frontend keeps narrating the old pipeline over the new graph — no invalidation
   exists (T01). Both are "confidently explaining the wrong thing", the worst class for this
   feature.
2. **The headline arithmetic is wrong for the most common ratebook pattern.** For
   `premium = premium * factor` chains, the hero/collapsed-chip substitution shows
   `120.0 × 1.2 = 144.0` under a header that says `premium = 120` — reproduced end-to-end; the
   correct handling already exists 400 lines away and was never applied to the primary
   calculation (T05).
3. **Multi-frame pipelines cannot be traced at all.** Any pipeline fed by a ≥2-table apiInput
   500s on every trace click — while previewing the same node works, so users walk straight into
   it (T06, confirmed and escalated by verification).
4. **The "<10 ms warm click" holds only on toy linear graphs.** Any join/sort pipeline is 3–6×
   over budget (62.6 ms at 5000×50), 68–73 % of it the same full-frame Python scan P03 flagged —
   every P03 item (FR-03…FR-11) is still open — plus new fixed costs: an un-memoised
   utility-preamble hash blocking the event loop 12–20 ms per click, triple payload serialization,
   and a 66 %-redundant payload (T03, T07, T08).
5. **Four reproduced fidelity drifts make the explanation disagree with the engine** in specific,
   realistic cases: rating `selected_value` reports the post-user-code value as the table's output;
   banding enrichment credits rules the engine skipped (`!=` operator drift); model-score feature
   lists are guessed from all input columns; string `.join`s are labelled row-joins (T04).
6. **The regulator pack is a promise without an artifact**: the export module is dead code, there
   is no download/copy/print, no loading state (a 120 s cold trace looks hung), and failures
   vanish in a 3-second toast (T09, T10).

Nothing here is structural. Fix Wave 1 (T01, T02, T03-tolerance, T05, T06) and the feature is
trustworthy; Waves 2–4 make it fast and regulator-ready. `STRENGTHS.md` lists the verified-good
behaviours the fixes must not regress.

## Scorecard

| Dimension | Grade | One-liner |
|---|---|---|
| Row-identity integrity | **C+** | Fail-loud matcher (W4) undercut by a permissive relocation entry point + a float-tolerance split between the two matching paths (T02, T03) |
| Explanation fidelity | **B** | Shared-SSOT key matching, Float32-faithful banding, reconcile-or-raise waterfall/optimiser; four reproduced presentation drifts (T04, T05) |
| Performance vs promise | **C** | <10 ms only on linear toys; joins 3–6× over; all P03 items open; new fixed costs eat the budget even post-P03 (T03, T07, T08) |
| Frontend robustness | **B+** | Race handling, guards, contract alignment excellent; one CRITICAL invalidation gap (T01) |
| Informativeness / followability | **B−** | Glow/fade, per-node details, progressive disclosure are best-in-class; headline arithmetic wrongness, leaky error story, no export (T05, T09, T10) |
| Elegance / architecture | **B+** | Observation-layer design, injectable preview reader, canonical fingerprints; dead export module + dead payload fields + inert reuse machinery (T08, T10) |

## Read in this order

| Doc | Contents | Top severity |
|---|---|---|
| `T01-stale-trace-invalidation.md` | Trace + glow survive pipeline edits/file-watcher sync | **CRITICAL** |
| `T02-row-anchor-ambiguity.md` | Relocation silently anchors to the wrong duplicate row (+ dead branch deletion) | **CRITICAL** |
| `T03-correlation-p03-status.md` | P03 re-verified/re-measured: float split (escalated), warm-click scans, jsonify fidelity | HIGH ×3 |
| `T05-self-referential-calculation.md` | `premium = premium × f` chains display wrong substitutions/results | HIGH |
| `T06-multiframe-trace-support.md` | Any trace through a multi-frame apiInput → opaque 500 (preview works) | HIGH |
| `T04-enrichment-fidelity.md` | Rating post-code value, banding operator drift, guessed model features, lineage mislabels, join-input provenance | MEDIUM ×5 |
| `T07-warm-click-performance.md` | Event-loop preamble hash, triple serialization, payload projection, stat churn, slot-holding, client memo | HIGH + MEDIUM |
| `T08-cache-architecture.md` | Contracts flag in the key, inert preview-reuse (decide), double-counted budgets | MEDIUM ×3 |
| `T09-error-story-and-loading.md` | Loading/error/empty states, 409 auto-recovery, waterfall-error surfacing, dropped-step placeholders | HIGH + MEDIUM |
| `T10-regulator-pack-and-polish.md` | Export/copy/print, dead `0.0ms` + dead fields, formatter unification, default flags, jargon | MEDIUM + LOW |
| `STRENGTHS.md` | Verified-good behaviours — the regression-protection list | — |
| `CLEARED.md` | Refuted findings + suspicions that died (incl. the killed pruning HIGH) — do not "fix" | — |
| `IMPLEMENTATION-PLAN.md` | Wave order, pairing rules, gates for the implementing agent | — |
| `REPORTS/` | The five raw specialist reports (full evidence trail) | — |
| `repros/` | Runnable repro + benchmark scripts for every reproduced finding | — |

Totals: **2 CRITICAL · 7 HIGH · ~20 MEDIUM · ~10 LOW** (after verification killed one HIGH and
escalated one MEDIUM).

## Relationship to prior reviews

- **`fable-Review/polars-backend/P03-trace-correlation.md` remains the canonical fix spec for
  FR-03…FR-11** (fixes not started; all items re-verified still open). `T03` augments it with
  fresh larger-scale measurements, one escalation (FR-05 → HIGH), one supersession (FR-06 → T02,
  now CRITICAL), one downgrade (FR-11 correctness risk refuted), and design confirmations against
  the current tree. Implement P03 under T03's sequencing notes; do not re-derive.
- **June 2026 audit (`review/`):** orthogonal; the audit's trace items were remediated in W3a/W4/C8
  and this review verified those remediations hold (see `CLEARED.md` §12). Everything in the
  T-docs is new relative to the audit.
- The **README product claims** (lines 63–75) were audited sentence-by-sentence — see
  `REPORTS/report-ux.md` for the TRUE/PARTIAL/FALSE table; T05/T09/T10 close the FALSE rows.

## Verification notes for the implementer

Every reproduced claim has a script in `repros/` (run from the repo root; they import `haute`
directly and need no server): `repro_e2e.py` (T02), `gen_trace.py`+`analyze.py` (T05),
`verify_core08_real.py` (T06), `probe_rating_postcode.py`/`probe_banding_ops.py`/
`probe_modelscore.py`/`probe_sniff.py` (T04), `repro_trace.py` (T03 FR-05/FR-07 + T04.5),
`bench_*.py` (T07/T08 cost model; regenerate their parquet fixtures on first run),
`verify_core03.py` (the refuted pruning claim — usable as a regression pin). Re-run the relevant
script before starting a package and after landing it; the expected before/after outputs are
stated in each T-doc. Frontend claims cite the existing vitest suites (165 targeted tests green at
review time) — `useTracing.test.ts`, `guards.contract.test.ts`, `client.contract.test.ts`.
