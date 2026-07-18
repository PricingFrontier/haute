# Phase 1 plan — Deep-dive + adversarial verification

Goal: turn the 107 Phase 0 **leads** into a **verified findings catalog** where every entry is
backed by a reproducing failing test (or a definitive code-trace proof), prioritised for remediation
*after* the in-flight feature branches land.

## Method (per subsystem / per lead)

1. **Deepen the dossier.** Beyond the Phase 0 map: exact contracts and invariants, and each candidate
   lead refined into a precise claim with `file:line` + a concrete failure scenario (inputs → wrong
   output).
2. **Adversarially verify.** Each claim is handed to an independent skeptic agent prompted to
   **refute** it. A claim survives only if it is backed by either:
   - a **reproducing failing test** — written as a throwaway repro under `review/` or `.cache/`,
     **never touching `src/` or `tests/`** (the frozen code stays untouched); or
   - a **definitive code-trace proof** for design/architecture issues that can't be unit-reproduced.
   Claims that can't be substantiated are downgraded to "smell" (logged, not actioned). This is the
   gate that keeps the catalog free of plausible-but-wrong noise.
3. **Catalogue survivors.** Each verified finding gets: severity (silent-mispricing > security >
   crash > perf > elegance) × confidence × blast-radius × effort, plus the attached failing test —
   ready to drop into `tests/` when the fix is implemented later (matches the CLAUDE.md TDD-bug flow:
   failing test first, then fix).

## Sequencing — spearhead the defining obligations first

**Spearhead A — Semantic-equivalence differential harness** *(risk 10).*
Build a differential test: codegen a graph → run the standalone `.py` → diff outputs against
`execute_graph`, for every node type, **especially the ones the round-trip property test skips**
(modelScore / external / liveSwitch / optimiser / optimiserApply / scenarioExpander / submodel).
First target: the **confirmed** optimiserApply divergence (`codegen.py:304-345` vs
`_builders.py:1375-1415`) and liveSwitch (`_codegen_builders.py:521-539` vs `_builders.py:633-666`).
This converts the single highest-value risk into reproducing failing tests.

**Spearhead B — Cache integrity** *(risk 9, the named `wave-2` concern).*
Two dossiers: (a) `DataFrameExecutionCache` parquet-artifact lifecycle under concurrent replacement +
Windows file-sharing (single-unlink rule, weakref.finalize on GC threads); (b) fingerprint
**completeness** — enumerate every output-affecting input class and prove each appears in the relevant
cache key; reconcile the preview/trace vs dataframe-cache key surfaces and the frontend `nodeId`-only
preview cache vs `(source,rowLimit)` freshness.

**Then sweep the remaining subsystems by risk:** execution-engine concurrency (9) → optimiser god-file
(8) → parser silent node-loss (8) → modelling (7) → projection/trace (7) → routes/job-store (6) →
platform/sandbox (6) → frontend (5).

## Leverage existing executable signal (ground truth, not opinion)

- **Mutation suite** (`scripts/run_mutation_suite.py`) scoped to each high-risk file → surviving
  mutants pinpoint exactly where tests are too weak to catch the bug class we're hunting.
- **Hypothesis** for the differential/property harnesses (round-trip, rating-key twin agreement,
  cache idempotence).
- **Coverage baseline** (`review/00-map/coverage-baseline.md`) to target genuinely-unexercised
  branches on high-risk files (`_worker_isolation.py` 69.5%, `_banding_config.py` 78.8%, etc.).
- **mypy --strict / ruff** discovery scans (extra rules, not committed) to surface latent issues.

## Guardrails
- Read-only on `src/` and `tests/`; repro scripts isolated to `review/`/`.cache/`.
- `git status` verified clean after every phase.
- Findings that touch files the in-flight feature branches are editing get tagged **coordinate** so
  remediation doesn't collide (computed in Phase 4 from `git diff origin/main...origin/<branch>`).
- Leads ≠ findings until verified.
