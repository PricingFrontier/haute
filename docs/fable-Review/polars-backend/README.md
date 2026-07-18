# Fable Review — Polars execution backend

**Read-only deep review of the Polars backend, performed 2026-07-06 at HEAD `4fcaa8f0` (branch `code-fixes`).**
Five reviewers: one core-engine pass (execution facade, executor, lazy engine, projection planner,
execution/LRU caches) plus four parallel cluster reviewers (node-ops, batch/shred, exec-infra, trace).
Every finding below was verified against the current code; the load-bearing ones were reproduced with
runnable Polars experiments (repro snippets are embedded in the package files).

**Nothing in the source tree was changed by this review.** This folder is the deliverable: the verdict,
the findings, and per-package implementation plans for a follow-up agent.

---

## Verdict

The backend is a genuinely strong engine — the projection planner (static AST demand inference with a
"prove it or stay full-width" policy), the bounded-memory collect/sink contracts, the parquet-backed
execution cache with scan-pinning, and the rating-key canonicalisation are all high-quality, verified
work. But it is **not yet as efficient, performant, or robust as it could be**. Three of the product's
own performance promises are not delivered by the current implementation:

1. **The preview cache does not deliver the README's caching story** ("change a rating factor and only
   the downstream nodes recalculate — everything upstream stays cached"). Every node click is keyed by a
   whole-graph fingerprint *plus the clicked node id*, so any edit invalidates everything and no entry is
   ever reused across targets. → P05
2. **The trace's documented "<10 ms warm click" is exceeded ~10×** by a Python full-frame scan on the
   row-location hot path (measured 8.5× slower than the vectorised equivalent). → P03
3. **The eager diamond optimisation is a no-op that adds overhead**: `.cache()` is applied per *child*,
   and each Polars `.cache()` call mints a distinct cache id (verified experimentally), so shared
   ancestors are recomputed once per branch and each copy is held in RAM. → P02

Alongside those, there is a cluster of **silent-wrongness edges** (wrong numbers with no error — the
exact class CLAUDE.md forbids): trace float-equality divergence, first-duplicate row anchoring,
null-key mis-nesting in the OUTPUT assembler, a min/max rating-combine null gap, and a RAM guard that
under-measures dictionary-encoded strings and silently disables itself on read errors. → P03/P04/P07/P08/P09

Total: **5 HIGH, ~15 MEDIUM, ~15 LOW** verified findings, organised into 11 fix packages below.

---

## Fix packages, in recommended execution order

| # | Package | Severity | Effort | Files touched |
|---|---------|----------|--------|---------------|
| P01 | [Windows RSS sampler memoisation](P01-windows-rss-sampler.md) | HIGH | S | `_execution_context.py` |
| P02 | [Diamond `.cache()` fix](P02-diamond-cache.md) | HIGH | S | `_execute_lazy.py` |
| P03 | [Trace correlation: hot path + float tolerance + row anchor](P03-trace-correlation.md) | HIGH + silent wrongness | M | `_trace_correlation.py`, `trace.py`, `_trace_enrichment.py` |
| P04 | [OUTPUT assembler: quadratic build + null semantics](P04-output-assembler.md) | HIGH + silent wrongness | M | `_output_assembler.py` |
| P06 | [Redundant full-file hashing](P06-redundant-file-hashing.md) | MEDIUM | S–M | `execution.py`, `_json_shred.py` |
| P07 | [Rating: null gaps and miss-guard cost](P07-rating-nulls-and-misses.md) | MEDIUM + silent wrongness | M | `_rating.py` |
| P08 | [RAM estimator robustness](P08-ram-estimate.md) | MEDIUM + silent wrongness | M | `_ram_estimate.py` |
| P09 | [Robustness misc (timing units, error classification, probes, pins)](P09-robustness-misc.md) | MEDIUM | M | `_execute_lazy.py`, `_polars_utils.py`, `_model_scorer.py`, `_lru_cache.py`, `_node_apply.py`, `_execution_admission.py` |
| P05 | [Preview cache: lineage-scoped keys](P05-preview-cache-lineage.md) | HIGH (architecture) | L | `executor.py`, `trace.py`, `_cache.py` |
| P10 | [Dead code and doc-rot](P10-dead-code-and-doc-rot.md) | LOW | S | several |
| P11 | [Low-priority perf nits](P11-perf-nits.md) | LOW | S | several |

Rationale for the order: P01/P02 are small, fully verified, and pay off on every interaction — do them
first. P03/P04 are the user-visible latency bugs plus their entangled silent-wrongness fixes. P06–P09
are the remaining robustness/correctness work. P05 is the highest-value change but is an architecture
change to cache identity — do it once warmed up on the codebase, with the design notes in its file.
P10/P11 are cleanup and can be batched.

**[CLEARED.md](CLEARED.md) lists behaviours that were adversarially checked and found correct — do not
"fix" anything on that list.**

---

## Implementation protocol (binding, per project CLAUDE.md)

1. **Failing test first, always.** Each package file has a "TDD plan" section listing the failing tests
   to write before touching the implementation. For performance findings, prefer *structural* assertions
   (operation counts via monkeypatch, plan-text assertions like counting distinct `CACHE[id:` entries in
   `explain()`, scaling-ratio bounds with generous margins) over wall-clock thresholds — wall-clock tests
   flake in CI.
2. **Two agents per package: one developer, one reviewer.** Full dev/reviewer pairs are mandatory for the
   silent-wrongness packages (P03, P04, P07, P08, and the P09 items marked as such); the mechanical
   packages (P01, P02, P10, P11) may use a single batch reviewer.
3. **Fail loud, no fallbacks.** Several fixes *remove* masking behaviour (dead `fill_nan`, broad
   `except Exception`, substring error classification). Do not replace one mask with another.
4. **Line numbers will drift.** All citations are valid at `4fcaa8f0`. Locate code by the quoted symbol
   names, not by line number.
5. **Gates before every commit:** `ruff format --check`, `ruff check`, `mypy`, the focused test files for
   the package, then the full suite before the final commit of a package. Accumulate work on the existing
   PR; **do not merge** — Ralph reviews independently.
6. **Cross-reference:** three items here (P10: FR-36, FR-37, FR-39) are deferred simplifications already
   tracked in `review/REMEDIATION-PROGRAM.md` (the no-op projection guard, the hand-rolled `json.dumps`
   at `execution.py:509`, the duplicated model-score predicate). Fixing them here closes those audit
   items — note that in the commit message so the audit ledger stays coherent.

## Finding ID scheme

Findings are numbered FR-01 … FR-47 across the package files. Each carries: severity, file:line (at
`4fcaa8f0`), evidence (what the code does today), impact (the concrete failure/cost scenario), fix
design, and test spec. Severity scale: HIGH = user-visible latency defect or architecture gap;
MEDIUM = real but bounded cost, or silent-wrongness edge behind an uncommon precondition;
LOW = hygiene, dead code, or micro-cost.
