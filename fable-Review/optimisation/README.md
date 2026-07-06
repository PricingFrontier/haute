# Fable Review — Price optimisation engine

**Read-only deep review of the built-in price optimisation subsystem, performed 2026-07-06 at
HEAD `2caa4134` (branch `code-fixes`).**
Five reviewers in parallel — solve lifecycle, estimate/frontier auto-range, HTTP layer + job
infrastructure, apply/explainability/save, and the `price_contour` engine — with every finding
re-verified against source by the coordinating reviewer before inclusion.

**Scope.** Backend + engine: `routes/optimiser.py` (1,802 lines), `routes/_optimiser_service.py`
(5,045), `routes/_optimiser_limits.py`, `routes/_job_store.py` + lifecycle/background-jobs
support, `_optimiser_io.py`, `_optimiser_apply_explainability.py`, the OPTIMISER_APPLY node
path, and the `price_contour` 0.4.x Python orchestration (~4.5k lines). The numeric core is a
compiled Rust module (`_price_contour.pyd`) — reviewed at its interface, not its source. The
frontend optimiser panels were out of scope. **Nothing in the source tree was changed.**

---

## Verdict

This is a seriously engineered subsystem — memory-admitted execution, cooperative cancellation,
atomic job-store swaps, traversal-validated artifact handles, single-flight setup dedupe, and
loud contract errors are all real and mostly exemplary (see [CLEARED.md](CLEARED.md) — a long
list, deliberately). But it is **not yet as efficient, performant, or robust as it could be**,
and four of the gaps directly contradict the product's own promises:

1. **The frontier workflow breaks after one apply.** Applying frontier point A wipes the quote
   grid, so applying point B — the entire point of sweeping a frontier — 400s and forces a full
   re-solve. One branch uses the session-preserving cleanup helper, its sibling doesn't. → P01
2. **`/save` can destroy the production pricing artifact.** The JSON the deploy scorer loads on
   every quote is overwritten in place, non-atomically; a mid-write crash leaves a truncated
   file where the last good artifact was. No finiteness gate, no schema version either. → P02
3. **Null constraint values silently corrupt results** (the exact class CLAUDE.md forbids):
   validation checks NaN/inf but not null, per-quote extrema skip nulls, and an all-null
   constraint yields a plausible-looking `[0, 0]` frontier range. On the solve path, nulls
   reach the opaque Rust grid builder unvalidated. → P03
4. **The solve-time frontier cannot succeed at ≥4 constraints** (15⁴ points exceeds the
   library's own 10k cap → caught → "Frontier unavailable"), and at 2–3 constraints it runs
   225–3,375 **sequential** re-solves — Haute triples the library's deliberate ratebook default
   of 5 points/dim and never enables the Rust sweep's `parallel` flag. → P06

Around those sit an availability cluster (up to 10,000 solver evaluations inline in a FastAPI
request thread with no job, no cancel, no gate → P04; timeout/cancel unable to reach a running
Rust solve while it holds its memory-admission slot → P05), an IO cluster (the scenario-expanded
frame — the biggest artifact in the system — executed twice per solve setup; K parquet scans for
K factor groups; full-parquet loads for 100-row previews; 11 passes for one stats block → P07),
a lifecycle cluster (artifact parquets orphaned forever on restart; N solves in 15 minutes
pinning N quote grids invisible to admission → P08), a trace-latency defect (full-portfolio
re-apply per traced cell → P09), and cleanup (→ P10).

The `price_contour` engine itself is clean fail-loud code; its issues (a save→load key-corruption
edge, quadratic NN ordering, guard-after-materialise, exact-zero ratio guard) are catalogued
separately with Haute's exposure assessed per item →
[UPSTREAM-price-contour.md](UPSTREAM-price-contour.md).

Total: **5 HIGH, 14 MEDIUM, 8 LOW** verified Haute-side findings (FO-01…FO-27, in 10 fix
packages) plus **11 upstream** findings (U-01…U-11, 1 HIGH — not fixable in this repo).

---

## Fix packages, in recommended execution order

| # | Package | Severity | Effort | Files touched |
|---|---------|----------|--------|---------------|
| P01 | [Frontier multi-point apply wipe](P01-frontier-multipoint-apply.md) | HIGH | S | `routes/optimiser.py` |
| P02 | [/save artifact contract: atomicity, finiteness, schema version](P02-save-artifact-contract.md) | HIGH (deploy-critical) | M | `routes/optimiser.py`, `_optimiser_io.py`, `_builders.py`, `_optimiser_apply_explainability.py` |
| P03 | [Null-constraint validation gap](P03-null-constraint-validation.md) | HIGH + silent wrongness | S–M | `routes/_optimiser_service.py` |
| P06 | [Frontier compute scaling + parallelism](P06-frontier-compute-scaling.md) | HIGH | M | `routes/_optimiser_service.py`, `schemas.py` |
| P07 | [Redundant IO passes (setup, auto-range, counts, previews, stats)](P07-setup-io-passes.md) | MEDIUM | M | `routes/_optimiser_service.py`, `routes/optimiser.py` |
| P09 | [Trace re-applies whole portfolio per click](P09-trace-apply-recompute.md) | MEDIUM | M | `_optimiser_apply_explainability.py` |
| P08 | [Memory/disk lifecycle: orphan sweep + retention cap](P08-memory-lifecycle.md) | MEDIUM | M | `server.py`, `routes/_job_store.py`, `routes/_optimiser_service.py` |
| P05 | [Solve interruptibility, timeout honesty, admission release](P05-solve-interruptibility.md) | MEDIUM | M–L | `routes/_optimiser_service.py`, `routes/_background_jobs.py` |
| P04 | [Synchronous heavy endpoints → gates/jobs](P04-sync-endpoints-jobs.md) | HIGH (availability) | L | `routes/optimiser.py`, `routes/_optimiser_service.py` |
| P10 | [Elegance & dead code](P10-elegance-dead-code.md) | LOW | S–M | several |

Rationale for the order: P01/P02/P03 are the correctness trio — small-to-medium, fully
verified, user- and deploy-facing; do them first. P06 is a contained change that unbreaks a
feature and removes the single biggest latency multiplier. P07/P09/P08 are the measured
performance/lifecycle work. P05 and P04 are coupled architecture changes (P04's phase-1
concurrency gate is small and may be pulled forward; its phase-2 backgrounding should be
designed together with P05's interruptibility so the cancellation machinery is built once).
P10 is batchable cleanup. **UPSTREAM items are documentation for the `price_contour` repo — do
not patch the venv.**

**[CLEARED.md](CLEARED.md) lists behaviours adversarially checked and found correct — do not
"fix" anything on that list.**

---

## Implementation protocol (binding, per project CLAUDE.md)

1. **Failing test first, always.** Every package file has a TDD plan; write those tests, watch
   them fail, then implement. For performance findings use *structural* assertions (execution
   counters via monkeypatch/spies, scan-count guards, plan-shape checks) — never wall-clock
   thresholds, which flake in CI.
2. **Two agents per package: one developer, one reviewer.** Full dev/reviewer pairs are
   mandatory for P01–P09; P10 (mechanical cleanup) may use a single batch reviewer per the
   calibrated-review convention.
3. **Fail loud, no fallbacks.** P03 adds rejections, P02 adds gates — do not soften either into
   defaults. Where a fix removes a silent behaviour (e.g. P06's opaque `frontier_error`), the
   replacement must name the cause and the remedy.
4. **Numerical parity is part of done.** P07 (quantile interpolation) and P09 (sliced vs
   full-frame trace) carry explicit byte-parity assertions — implement them exactly; silent
   numeric drift is the failure mode these packages exist to prevent.
5. **Line numbers will drift.** All citations are valid at `2caa4134`. Locate code by the
   quoted symbols, not line numbers.
6. **Gates before every commit:** `ruff format --check`, `ruff check`, `mypy`, the package's
   focused test files, then the full suite before a package's final commit. Accumulate on the
   existing PR; **do not merge** — Ralph reviews independently.
7. **Cross-references:** FO-23 and FO-24 close items already tracked in
   `review/REMEDIATION-PROGRAM.md` / `review/MASTER/all-verified.json` — cite the audit IDs in
   commit messages so the ledger stays coherent. P01 inverts a pinned assertion in
   `test_optimiser_frontier_materialisation.py:269-270` — deliberate, documented in P01.

## Finding ID scheme

Haute-side findings are FO-01…FO-27 across the package files; upstream engine findings are
U-01…U-11 in [UPSTREAM-price-contour.md](UPSTREAM-price-contour.md). Each carries severity,
file:line (at `2caa4134`), verified evidence, the concrete failure/cost scenario, a fix design,
and a test spec. Severity scale: HIGH = broken workflow, deploy-critical risk, silent
wrongness, or guaranteed feature failure; MEDIUM = real but bounded cost or robustness gap;
LOW = hygiene, dead code, or micro-cost.
