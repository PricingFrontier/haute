# Implementation plan — tracing review

For the implementing agent (Opus). Read `README.md` first, then this file, then the T-docs of the
wave you are executing. `STRENGTHS.md` is the do-not-regress list; `CLEARED.md` is the
do-not-"fix" list.

## Protocol (binding)

- **TDD, failing test first, every item.** Each T-doc names its first failing test; write it, watch
  it fail for the stated reason, then fix. The `repros/` script for the package must flip from the
  documented "before" output to the documented "after".
- **Pairing (calibrated split):**
  - **Full dev + reviewer pairs** — silent-wrongness / crash classes: T01, T02, T03 (FR-05 +
    FR-07), T04 (all items), T05, T06, T09.1/T09.2.
  - **Batch review** (one reviewer over the whole wave's diff) — mechanical/measured classes:
    T03's vectorisation tail (FR-03/04/08/09/10/11, once the pinned behaviour tests from
    STRENGTHS are green first), T07, T08, T09.3/T09.4, T10.
  - No re-review of verbatim fix-ups.
- **Gates after every package:** full trace suites
  (`pytest tests/test_trace*.py tests/test_optimiser_apply_trace_enrichment.py -q`), frontend
  (`npx vitest run` on the trace-touching suites + `npx tsc -b --noEmit`), `ruff check`, mypy,
  `tests/test_frontend_backend_contract.py` whenever the wire shape moves. Coverage may not drop;
  never lower a gate.
- **Git:** accumulate waves on the single open review PR for Ralph's independent review; never
  merge. One commit per T-package (or per T-sub-item where a doc has independent items), message
  prefixed `trace-Wn:`.
- **After all waves:** run a cross-wave holistic review (fresh agent, whole-diff view) before
  declaring done — interactions between T01 (invalidation), T07.6 (client memo) and T09 (state
  machine) are the likeliest seam bugs.

## Waves and order

### Wave 1 — Trust (the trace never confidently explains the wrong thing)
Order within the wave matters:
1. **T03/FR-05** (float tolerance in `_build_value_match_expr`) — precondition for T02's
   vectorisation and T03/FR-04's deletion.
2. **T02** — ambiguity-raising relocation (the CRITICAL); its 409 lands on the frontend via the
   existing toast until T09 upgrades the surface. Include the CORE-10 branch deletion.
3. **T05** — self-referential substitution fix (small, isolated, huge user impact).
4. **T06** — multi-frame per-port routing (unblocks a whole pipeline class).
5. **T01** — frontend invalidation on graph mutation.
6. **T03/FR-07** — jsonify fidelity (update `test_non_primitives_stringified` in the same commit).

Wave-1 exit: `repros/repro_e2e.py` raises 409-shaped ValueError; `repros/analyze.py` shows
substituted == observed for every step; `repros/verify_core08_real.py` returns traces (or mapped
4xx) for all three cases; a graph edit clears the open trace in the App integration test.

### Wave 2 — Fidelity of the explanation
- **T04.1–T04.6** in doc order. T04.2 includes the shared operator-table pin test. Flag (do not
  fix) the engine's silent unknown-operator skip to Ralph — it is an engine fail-loud violation
  outside trace scope.

### Wave 3 — Speed (make the <10 ms promise true)
1. **T03 tail**: FR-03 vectorisation → FR-04 fast-path deletion (pins from STRENGTHS first) →
   FR-08 exact-first → FR-09 diagnostic → FR-10 shared memo → FR-11 comment/idiom.
2. **T07.1–T07.6** (T07.6 strictly after T01 — the client memo must share T01's invalidation).
3. **T08.1–T08.3** (+ T08.2 requires an explicit option-(a)/(b) decision; default to (a) unless
   Ralph objects on the PR).

Wave-3 exit: `repros/bench_e2e.py` diamond 5000×50 warm ≤ ~15 ms end-to-end; structural spy tests
(no `iter_rows` on row-location; single stat per warm click; utility files hashed once) green.

### Wave 4 — The story
- **T09.1 → T09.2 → T09.3 → T09.4**, then **T10.1–T10.7**. T10.1 (export) is the only item with
  product-surface discretion: implement per the doc unless Ralph deprioritises it on the PR;
  deleting `_trace_export.py` is the documented fallback, not silence.

## Sequencing constraints (summary)
- FR-05 before: T02 vectorisation, FR-04 deletion.
- T01 before T07.6 (client trace memo).
- STRENGTHS pins (no-shared-columns positional fallback, reorder gate, duplicate-key diagnostic)
  before FR-04's branch deletion.
- Any payload/wire change (T06 port label, T07.3 projection, T09.4 placeholder, T10.2/T10.6
  fields) updates `schemas.py` + `types/trace.ts` + `guards.ts` + both contract suites in the same
  commit.
- T07.3 (payload projection) is the only behaviour-visible perf item — it ships behind the
  documented `full_rows` escape hatch and its frontend-consumption contract test.

## Estimate
Wave 1 ≈ 2–3 sessions; Wave 2 ≈ 1; Wave 3 ≈ 1–2; Wave 4 ≈ 2. Each wave independently shippable;
do not start a wave until the previous wave's exit criteria are green.
