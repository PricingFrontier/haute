# Haute — Master Remediation Program

> **Historical synthesis, not the active queue.** Choose and update work through
> the [component improvement catalogue](../roadmap/index.md); use this document
> for evidence and original prioritisation only.

> **Single prioritised, deduplicated remediation program** compiled from the complete read-only audit (5 phases, every file and function). No source code has been changed. Each finding links back to its phase detail under `review/`.

## Audit at a glance

- **881 verified findings** = **788 bugs/quality findings** (5 critical, 110 high, 257 medium, 413 low) + **93 sound, behaviour-preserving simplifications**.
- Sources: per-subsystem deep-dive (P1), simplification pass (P2), exhaustive file-by-file sweep (P3, every function), and 10 quality dimensions (P4: numerical, tests, performance, types, security, CI-gaps, docs, API/DX, dependencies, frontend a11y).
- Every "real" finding was reproduced or code-traced by an independent skeptic; ~45 candidates refuted.
- **Read-only throughout** — this is a plan, not a change.

## Where remediation concentrates (cross-phase file hotspots)

Fixing the top ~15 files clears a large fraction of all findings:

| findings | file |
|---:|---|
| 34 | `src/haute/_expression_parser.py` |
| 21 | `src/haute/schemas.py` |
| 21 | `src/haute/_types.py` |
| 20 | `src/haute/routes/_optimiser_service.py` |
| 15 | `src/haute/_json_shred.py` |
| 15 | `src/haute/projection.py` |
| 13 | `src/haute/_sandbox.py` |
| 12 | `src/haute/pipeline.py` |
| 11 | `src/haute/codegen.py` |
| 11 | `src/haute/_rating.py` |
| 11 | `src/haute/executor.py` |
| 10 | `src/haute/chunking.py` |
| 10 | `src/haute/_execute_lazy.py` |
| 9 | `src/haute/routes/_train_service.py` |
| 9 | `src/haute/cli/_init_cmd.py` |

## How this program is organised

- **Executive summary, prevention investments, and execution sequence** — the few highest-leverage moves and the order to do them.
- **P0 — Must-fix** — the 5 critical + 110 high, consolidated to root-cause themes (security, served-price, data-loss, crash).
- **P1 — Should-fix** — the 257 medium bugs, the 93 sound simplifications (god-file decompositions + dedup), and the quality/hardening investments.
- **P2 — Long tail & accept-risk** — the 413 low-severity items, with a file-batched burn-down strategy and the reasonable WONTFIX classes.

---
## Executive Summary

`haute` is already a high-bar codebase, and the audit numbers prove it: across 881 verified findings only **5 are critical and 110 high** against **257 medium + 413 low**, and the dominant high-severity pattern is not "this is wrong" but "this is *silently* wrong under a configuration the test suite never exercises." The engine carries a genuine integrity spine — a `NODE_REGISTRY` validated complete at import (`_registry.py:166-188`), a single canonical-JSON digest encoder (`_cache.py`), a Python-mirror/Polars-twin rating-key pinned by a real differential test (`tests/test_rating_key_agreement.py:259-285`), a shred conservation law (`tests/test_json_shred_properties.py:51-169`), and a 91.75%/92% coverage floor with a mutation lane. The marvel work is therefore **not a rewrite**: it is a handful of *structural* fixes that make existing correct patterns total instead of half-applied (the codebase already routes banding/ratingStep/modelScore through one shared `apply_*_from_config` helper but abandoned the pattern for five node types), plus closing the **prevention gaps** that let these classes ship green. Roughly 80% of the high-severity catalog collapses into ~6 root causes; fix the roots and the long tail of mediums/lows largely evaporates or becomes mechanically detectable.

### The state in one line

> A strong engine with a strong test suite that asserts the **wrong invariant** at exactly its most dangerous seams — structural equivalence where it needed *value* equivalence, fingerprint *presence* where it needed fingerprint *completeness*, and graceful degradation where CLAUDE.md demands fail-loud. Close those seams structurally and the codebase is genuinely excellent.

---

## The 5-7 Highest-Leverage Moves

These are ordered by *leverage* (how much they dissolve), not execution order. Each is endorsed independently by the dimension lens (`ci-prevention.json`) and the architecture panel (`ARCHITECTURE-ROADMAP.md`). The pairing is deliberate: a **detection** guard (a test that turns the latent class red) plus a **construction** guard (a structural invariant that makes the class inexpressible).

### 1. Complete the shared `apply_*_from_config` helper + the parse→codegen→execute differential harness

**The single highest-leverage move; all five architecture panels converged on it.** Today `_make_passthrough_builder` (`_codegen_builders.py:784-858`) emits `return {first}` for `optimiser`, `optimiserApply`, `modelling`, `scenarioExpander`, and `_gen_live_switch:522` hard-wires the `live` branch — so a **saved standalone `.py` silently no-ops or mis-routes** while the canvas executor applies real logic. This is the README's "it's just Python, take it with you" promise, broken. The structural test (`test_codegen_roundtrip_property.py`) passes because it asserts *structure* and explicitly budgets out execution; `test_pipeline.py` runs the executor object, never the emitted-then-reparsed file. **Dissolves:** cluster C1 in full (3 confirmed divergences) — and, via a registry `is_behavioural` flag, makes any *future* stateful NodeType shipping an inert body a hard **import-time error**. The differential harness (`graph→code→write→pipeline.run()/score() under source∈{live,batch}→assert_frame_equal vs execute_graph`) also catches the *detection* class for the parser (a dropped node changes executed output even when structural diffing is fooled). Retires `_make_passthrough_builder` and the bespoke codegen string-surgery as dead code. **Effort: M.**

### 2. Schema → TS codegen + a single CI diff gate (the "make the backend the source of truth" move)

The OpenAPI contract fingerprint (`tests/test_api_contracts.py:8,21,512`) is **field-blind**: `_normalise_schema` collapses every response to `{$ref|type|title}` and never descends into the referenced schema, so a backend rename/retype/optional-flip/drop of any field the frontend consumes **ships GREEN**. The frontend types (`frontend/src/api/types.ts`, `node.ts`, `guards.ts`) are hand-authored with zero generation. **Dissolves:** the entire frontend/backend schema-drift class at *field* granularity, plus the NodeType-enum desync (`nodeTypes.ts:5-25` vs `_types.py NodeType`, never mechanically checked because backend/frontend are disjoint CI jobs). One committed `api.generated.ts` from `app.openapi()` + a `schema-sync` job running `git diff --exit-code` *replaces* the brittle hand-fingerprint, the `ALLOWED_TYPES` mirror, and ~45 hand-curated `ui_contracts` fixtures with a mechanical, exhaustive invariant. The structural underpinning of the `frontend/src/api/types.ts` (count 7) and `App.tsx` (count 7) hotspots. **Effort: S-M.**

### 3. Float32 rating-key widening + the twin property test extended to the save↔apply dtype boundary

`normalise_rating_key` (the Python mirror, `_rating.py:309-343`) always widens Float32→Float64 before the Utf8 cast, while `_rating_key_expr` (the Polars twin, `:346-370`) casts the Float32 column directly — so the **same nominal factor value yields a different lookup key**, producing a silent **neutral-1.0 / default-rate miss** (real price impact). The excellent existing twin test explicitly carves Float32 out (docstring `:23-27`). **Dissolves:** cluster C6 — carry the source dtype across the trace-JSON boundary so the mirror and twin agree, and add a **save-dtype ≠ apply-dtype** round-trip property (solve a ratebook against column dtype D1, apply against D2 over {Float32,Float64,Int32,Int64}, assert match or a LOUD mismatch — never silent neutral-1.0). This is the highest-severity *medium* with genuine mispricing; it is "contained, not actively firing" only because the agreement test is dtype-incomplete. **Effort: M.**

### 4. JSON-shred conservation + fingerprint-completeness as a *checked* invariant (the wave-2 cache spine)

The named wave-2 mandate, and its members are one defect in different cells of one walker. `_v2_fingerprint` (`_json_shred.py:108-147`) `continue`s past non-dict tables/columns → two structurally-different on-disk configs collapse to **one `schema_fingerprint`** → a stale per-port parquet is judged fresh. `_data_file_matches` (`:253-271`) returns fresh on (size, mtime_ns) **without hashing** → a byte-changing rewrite serves stale. `_resolve_leaf`/`_walk` collapse/flatten lists with **zero skip accounting** (the verified HIGH row-inflation bug). A literal key named `$value` (`:1199`) or containing `.` (`:1203`) silently drops every record. The machinery already exists (`ShredSkipStats`) but is applied unevenly. **Dissolves:** cluster C2 in full plus verified HIGHs #2/#16-family — replace the `continue`s with `raise`, route every collapse through skip-accounting, add a build-time **reconciliation assertion** (every DROP/COLLAPSE accounted — *not* rows==records), and always-verify the data-file hash. Batches naturally with C11 (mirror lock/ns-precision, same file) and C12. **Effort: S.** **This is the active branch's own surface — do it on `wave-2-cache-integrity`.**

### 5. Artifact-identity fingerprint threaded through deploy validate + every output-affecting key (the only class that mis-prices a *live* quote)

`score_test_quotes` (`deploy/_validators.py:375-380`) and `infer_output_schema` (`deploy/_schema.py:134-139`) both call `score_graph` **without `artifact_paths`**, even though the container *does* pass them — so the "test before live" gate loads a model live from MLflow (`latest`, mutable) that can **differ from the bytes the container serves**, and skips the contract check. `infer_output_schema` keys its cache on `graph_fingerprint` *alone* (`:95`), excluding artifact bytes → a retrained-in-place model bakes a **stale ModelSignature**. Compounded by verified HIGH #8: a `modelScore` node with nothing bundled deploys as a **silent passthrough** (`deploy/_scorer.py:605`). **Dissolves:** clusters C3+C4 — `resolved.artifacts` is *already available* at every call site, so this is wiring + a required-argument tightening, not new logic. One `artifact_identity_fingerprint(resolved.artifacts)` helper + a byte-identical regression test. **Effort: S — highest value-to-effort in the entire backlog.**

### 6. The `run()`/`score()` explicit-output fix (the public-API correctness floor)

`pipeline.run()` and `.score()` (`pipeline.py:360,409`) silently `return outputs[order[-1].name]` — the **last topological node, not a declared output** — so any fan-out returns the *wrong* DataFrame. Adjacent: `Node.__call__` (`:54-68`) silently drops extra wired inputs (a single-param node fed two edges uses only the first), and `@pipeline.api_input` (`:132-134`) does **not** mark the live API input despite its name. This is the entry point every standalone user hits first; it must be fail-loud (raise on ambiguous output / require an explicit output node) before #1's portability promise means anything. **Effort: S.** *Note: `pipeline.py` is the public decorator surface — land this with #1 so the helper extraction and the API contract change once, together.*

### 7. (Prevention capstone) The module-singleton isolation registry + warnings-as-errors

The largest *test-infra* root cause: `conftest.py` carries **11 hand-written autouse reset fixtures** — reactive scars, each added *after* a state-leak regression — while **~17 other mutable module-level globals** (`_feature_validation_cache`, `_contract_cache`, `_model_cache`, `_runtime_path_fingerprint_memo`, …) have **no** isolation and leak silently under xdist. This same state-bleed mechanism *is* catalog findings 14/15/34/36/40/54/60. **Dissolves:** the entire stale-cache/cross-test-bleed class via an AST-scan ratchet (reusing the proven `test_test_debt.py` fingerprint pattern) that fails CI when a new un-isolated global appears. Pair with escalating `filterwarnings` from RuntimeWarning-only to `error` + `error::DeprecationWarning:haute` + `error::FutureWarning` + `error::ResourceWarning` (`pyproject.toml:148-152`), which turns silent polars/pydantic API drift and unclosed-resource leaks into loud CI failures. **Effort: M.**

---

## Cross-Cutting Prevention Investments

The audit's deepest lesson: **coverage proved lines *ran*, not that a wrong result *fails a test*.** A 91.75% suite missed every one of these classes. The prevention program is therefore about installing *invariants* and *behavioural* gates, not raising line thresholds.

| Investment | Replaces / closes | Root-cause class dissolved | Effort |
|---|---|---|---|
| **Execution-differential harness** (codegen→emit→run→frame-diff) | the structure-only roundtrip property test's blind spot | C1 codegen/executor value divergence (heat-map #1) | M |
| **Registry `is_behavioural` invariant** | the "shared helper by convention" gap (`_registry.py` has no helper slot) | makes a passthrough body for a stateful type *unrepresentable* | S |
| **Schema→TS codegen + `git diff` CI gate** | field-blind OpenAPI fingerprint + `ALLOWED_TYPES` + 45 fixtures | all frontend/backend field-level drift + NodeType desync | S-M |
| **Fingerprint-completeness registry** (mutate-any-input-moves-the-key table test) | ad-hoc per-cache assertions | C3/C11/C12 + V034 (NaN-set non-determinism) + V035 (non-injective `\|`/`\n` join, `_cache.py:429`) | M |
| **Shred reconciliation assertion** | uneven `ShredSkipStats` application | C2 + `$value`/dotted-key silent drops | S |
| **Parser structure-conservation pass** | 7 independent per-shape silent drops; a test that *pins* the bug (`test_parser.py:862`) | C5 in full (heat-map #5) | M |
| **Module-singleton isolation ratchet** | 11 reactive conftest scars; ~17 unguarded globals | the entire stale-cache/state-bleed class | M |
| **`filterwarnings = 'error'`** (first-party) | RuntimeWarning-only escalation | silent polars/pydantic drift + ResourceWarning leaks | S |
| **Always-on mutation smoke** on parser/executor/`_model_scorer` + monotonic **coverage-threshold ratchet** | mutation gated only on PRs touching `src/**`, path-filtered, not required on main; thresholds silently loosenable (`pyproject.toml:271` TODO) | coverage-gaming; under-asserted critical modules | M |
| **Clean-subprocess deploy lane** (wheel-only venv runs the emitted `.py`, asserts golden premium) + **windows-latest** on package-smoke | in-process-only `test_e2e.py` roundtrip; Linux-3.12-only smoke | standalone-portability + OS-specific (rename/path/npm) regressions | M |

**The meta-principle worth installing as policy:** *every output-affecting input must be in the cache key; every loss boundary must fail loud or account; every codegen body must be the same function the executor calls.* Three of the nine fixes above are literally "fold the missing input class into the key" — that is one discipline (`fingerprint-completeness`), not three projects.

---

## Recommended Execution Sequence (Waves)

Sequenced by **severity × leverage × effort** and the file hotspots, **not** by branch — per the orchestrator note, the active branches (`wave-2-cache-integrity` et al.) are unpublished, so branch-overlap analysis is withdrawn. Where a fix sits on the active wave-2 surface it is flagged, but impact drives the order.

### Wave 0 — Critical & near-free fail-loud (days)
The 5 criticals and the quick wins that close *live* exposure or violate fail-loud at near-zero cost.

| Item | Why first | Files | Effort |
|---|---|---|---|
| **Sandbox pickle-allowlist RCE** (whole-package `numpy` entry admits gadgets via `safe_unpickle`/`safe_joblib_load`) | the only *critical security* finding; arbitrary code execution | `_sandbox.py:346-413,454-463` | S |
| **Thread `resolved.artifacts` into deploy validate** (Move #5, C4 half) | only live-served-price divergence; pure wiring | `deploy/_validators.py`, `deploy/_schema.py` | S |
| **`run()`/`score()` explicit-output + `Node.__call__` extra-input** (Move #6) | public-API correctness floor; prerequisite for #1's promise | `pipeline.py:54-68,360,409` | S |
| **Remove trace value-laundering catch-alls** (C7) | direct fail-loud violation; display masks evaluator bugs | `_expression_parser.py:988-1003`, `_trace_enrichment.py` | S |
| **Dedup the 4 drifted job-failure ladders → 1 mapper** (also fixes C15 terminal_reason) | already-drifted copies; ~200 LOC removed | `routes/_optimiser_service.py` (hotspot, count 20) | S |
| **Critical deps + missing dev deps** (mlflow / vitest / coverage-v8 absent) | CI cannot run as documented | `pyproject.toml`, `package.json` | S |
| **`init` pyproject quote-escaping** (verified HIGH #1 — invalid TOML crashes the command) | first-run breakage | `cli/_init_cmd.py:269` | S |

### Wave 1 — Cache-spine integrity (the active wave-2 surface)
Self-contained on the named branch; closes the cache silent-stale class. **Build the failing test first (CLAUDE.md TDD).**

- **JSON-shred conservation + fingerprint-completeness** (Move #4 / C2) — raise on non-dict fingerprint cells; skip-account every collapse; always-hash `_data_file_matches`; `$value`/dotted-key (verified HIGHs #2, #16). `_json_shred.py` (hotspot, count 15). **+ batch C11** (mirror lock/ns-precision/Windows-safe rename — `_json_flatten.py:239-317` uses bare rename) **and C12**.
- **Fingerprint-injectivity + completeness registry** — kills V034 (NaN-set non-determinism, `_cache.py:137-147`) and V035 (`|`/`\n` collision, `:429`); the meta-property every future cache extends.
- **Chunk-sizing OOM fix** (C13) — cost target row width from the *target's projected output schema*, not source-only (`chunking.py:607-687`, hotspot count 10); downstream wide-string columns currently fall back to 64 bytes → OOM.

### Wave 2 — Codegen/executor equivalence (highest structural leverage)
- **Complete `apply_*_from_config` for all 5 abandoned types + registry `is_behavioural` + differential harness** (Move #1 / C1). Touches the top hotspot `_expression_parser.py`-adjacent codegen path: `_codegen_builders.py` (count 8), `_builders.py` (count 7), `_registry.py`, `graph_utils.py`. **Land with Wave 0's `pipeline.py` fix so the public surface changes once.** Pin existing optimiserApply/liveSwitch executor tests green *before* switching templates (prove it's a pure refactor).

### Wave 3 — Parser structure-conservation (turns 7 silent drops into 1 invariant)
- **`assert_structure_conserved(source, graph)`** (Move's prevention / C5) — every authored node/edge/submodel survives or raises. Replaces the bug-pinning `test_parser.py:862` with `pytest.raises(ParseError)`. Fixes the verified HIGHs: duplicate-name collapse (`_ast_helpers.py:269`), async-def drop (`:270`), regex-fallback submodel wipe (`_parser_regex.py:658`), cross-boundary edge loss (verified HIGH #7, `_parser_submodels.py:173`), posonly/kwonly drop (`_graph_builders.py:73`). `_expression_parser.py` is the #1 hotspot (count 34) but is overwhelmingly *numerical-divergence mediums* (clip/log/pow/Kleene) — sweep those **with** C7 here, not as a separate wave.

### Wave 4 — Rating-key & trace-correlation fidelity (real but contained mispricing)
- **Float32 rating-key + save↔apply property** (Move #3 / C6) — `_rating.py` (hotspot, count 11).
- **Trace-correlation soundness** (C8) — uniqueness + scale-relative tolerance replacing the 1e-6 absolute collision (`_trace_enrichment.py:1003`) and the positional fast-path misroute (`_trace_correlation.py:632`); land with the Wave-0 C7 work. `_trace_enrichment.py` (count 8).

### Wave 5 — Frontend/backend contract + remaining verified HIGHs
- **Schema→TS codegen + diff gate** (Move #2) — and the NodeType-parity test. `App.tsx`, `api/types.ts` (count 7 each).
- **Remaining verified HIGHs**: Azure DevOps YAML indent (#3), `_gen_constant` empty-name divergence (#4), `_match_source` dropped statement (#5), rating-step compact↔expand asymmetry (#6), Explore non-UTF-8 crash (#10), supersession permit leak (#11), UtilityPanel lost edit (#9). Each ships with a repro in `review/04-exhaustive/repro/` — a ready-made TDD backlog.

### Wave 6 — Prevention hardening & enabler refactors (do *after* correctness lands)
- **Module-singleton isolation ratchet + `filterwarnings='error'`** (Move #7); **always-on mutation smoke + monotonic coverage ratchet**; **clean-subprocess deploy lane + windows-latest smoke**.
- **Decompose the 5046-LOC `routes/_optimiser_service.py`** (count 20 — *why* C10/C15 concentrate) into `routes/optimiser/`. **Explicitly last**: it is an enabler, fixes no finding by itself, and carries heavy churn — do it *after* C6/C10/C15 land so they're written once in their final modules.
- **413 lows + 244 unverified candidates**: triage as a continuous backlog, not a wave. Verify-before-acting (the exhaustive sweep notes these were code-traced, *not* through the reproduce-or-refute gate).

---

## Must-Fix vs Accept-Risk Decision Framework

When triaging the 257 mediums and 413 lows, apply this test in order. The first matching row decides.

| If the finding… | Decision | Rationale |
|---|---|---|
| Can change a **deployed/served price** OR enables **arbitrary code execution/read** | **MUST-FIX, this program** | C3/C4, the sandbox RCE — the only classes that corrupt a real production number or breach the boundary. Non-negotiable. |
| Is a **silent-wrongness** defect (wrong result with **no error**) on the pricing/cache/parse path | **MUST-FIX** | Directly violates CLAUDE.md fail-loud. The audit's entire thesis: these are the bugs the green suite *cannot* see. Includes C1, C2, C5, C6. |
| Is a **prevention gap** that would let a fixed class **silently regress** | **MUST-FIX (as a guard)** | A fix without a guard is temporary. Every structural fix above ships *with* its detection test — that pairing is the deliverable, not the code change alone. |
| Produces a **wrong value but fails loud** (raises, visible error), or is **display-only** (trace waterfall, never a stored price) | **FIX, scheduled** | Real but bounded — the user *sees* something is wrong. C7/C8 land in-wave because they're cheap and adjacent, not because they're urgent. |
| Is **latent** (guarded by a default the engine doesn't currently hit — e.g. non-strict projection profiles) | **FIX when touching the file** | C9. Genuine but not firing; fold into adjacent work to avoid a dedicated wave. Prefer "widen to a provably-safe superset" over "raise" where a correct wider bound exists. |
| Is **environment-specific** (cgroup-blind RAM, Windows file-sharing, OOM-under-load) | **FIX, batched tail** | C13/C20. Availability/UX, not wrong prices. Real for container/Windows deploys; batch after correctness. |
| Is a **type-safety / vocabulary** finding (stringly-typed enum, 4 names for one concept, untyped `dict[str,Any]` config) | **ACCEPT as tracked debt; fix opportunistically** | The 21 findings each on `schemas.py`/`_types.py` are overwhelmingly this. High *count*, low *individual* severity — they enable future bugs but cause none today. A single "introduce the `Task`/`TerminalReason`/`NodeType` literal everywhere" sweep clears dozens at once; do it when refactoring the file, never as an emergency. |
| Is a **docs/README overstatement** | **FIX in a documentation pass** | The 2 critical/high doc findings (standalone-portability and test-before-live claims) become *true* once #1 and #5 land — so fix the **code**, then correct the prose. Don't patch the docs to match a buggy implementation. |
| Is **accessibility / frontend-quality** (missing aria-label, no focus trap) | **ACCEPT for this program; separate a11y initiative** | ~138 controls lack programmatic labels. A coherent body of work, but orthogonal to engine correctness — a dedicated a11y pass with its own axe/lighthouse gate, not interleaved here. |

**The opinionated cut line:** ship Waves 0–4 and the Wave-5 schema gate as the **must-fix correctness program** (every item is live-price, silent-wrongness, or its guard). Treat type-vocabulary, a11y, perf-within-budget, and the optimiser god-file decomposition as **tracked debt with a burn-down**, not blockers — they are the cost of a codebase that grew fast, not evidence that it grew wrong. Accept a low/latent risk *only* when (a) it fails loud or is display-only, **and** (b) a guard exists or is cheap to add so it cannot silently worsen. Never accept a silent-wrongness finding on the pricing path; that is the one line this audit was written to defend.

---

## P0 — Must-Fix

> Consolidation of all **5 critical + 110 high** verified findings into **9 root-cause themes**, ordered by blast-radius × reachability. Each theme names the underlying defect, representative member findings, the single fix that dissolves the cluster, severity, effort, and — critically — whether it touches a **real-world served price / data-loss / crash / security** surface or is **standalone-only**. The five structural roadmap changes are folded in where one dissolves a theme.
>
> **Severity corrections applied (from `review/02-findings/orchestrator-notes.md`):**
> - The codegen passthrough cluster (P0-2) is **standalone-portability**, NOT deployed mispricing. The deploy path (`deploy/_model_code.py:49` → `score_graph(...)` on the pruned graph) and in-canvas preview/trace/batch both run the **executor**, never the generated body. Only `pipeline.run()`/`.score()` on an **exported `.py`** diverges.
> - The Phase-0 concurrency cluster (preview-cache RMW race, preamble-lock-on-miss, `DataFrameExecutionCache` finalize, JobStore by-ref reads) was **refuted** — those are NOT in P0. Do **not** re-open them.

### Ordering rationale (blast-radius × reachability)

| Rank | Theme | Worst real-world impact | Reachability | Sev | Effort |
|---:|---|---|---|:--:|:--:|
| 1 | RCE via unpickle allowlist | Arbitrary code execution | Any loaded model/artifact | **CRIT** | S |
| 2 | Deploy artifact identity | **Live served quote mispriced** | Every deploy w/ modelScore | HIGH | S |
| 3 | JSON-shred conservation + fingerprint completeness | Silent data loss / stale-served cache | Every apiInput cache build | HIGH | S–M |
| 4 | Parser structure-conservation | Silent node/submodel loss (wrong graph) | Any multi-submodel / syntax-error file | HIGH | M |
| 5 | Codegen shared-helper (passthrough class) | Standalone `.py` no-ops / wrong branch | Exported-file `pipeline.run()` only | HIGH | M |
| 6 | Chunk-sizing OOM under-bound | Process-killing OOM under load | Wide/string downstream columns | HIGH | M |
| 7 | Crash-class (Explore / trace / worker deadlock) | Whole-operation crash / hang | Specific dtypes / large payloads | HIGH | S–M |
| 8 | Docs portability/secret-name false claims | User self-inflicted prod breakage | Anyone following deploy guides | HIGH | S–M |
| 9 | Supply-chain dependency advisories | CVE exposure | Build/runtime | CRIT/HIGH | S |

Themes 1–6 are the correctness/security spine. Themes 7–9 are crash-class, doc-integrity, and supply-chain. The **test-only HIGHs** (~40 P4-tests entries — e.g. `tests/test_worker_isolation.py:47-51`, `tests/test_explore_routes.py:785-908`, `tests/test_codegen_roundtrip_property.py`) are NOT separate themes: each is the **missing red-build guard** for a code theme and is folded into that theme's fix as "pin with a failing test first" per the CLAUDE.md TDD mandate.

---

### P0-1 — RCE: unpickle allowlist admits real gadgets (CRITICAL, security, do first)

**Defect.** `RestrictedUnpickler`'s allowlist contains a **whole-package** `('numpy',)` single-segment entry, and `_pickle_global_is_allowed` does a **prefix match** — so the gate at `_sandbox.py:405-413` (`find_class`) admits arbitrary `numpy.*` callables, which include real RCE gadgets. This path is reached by **both** `safe_unpickle` AND `safe_joblib_load` (`_sandbox.py:454-463`) — i.e. every model/artifact load, not just one entry point.

**Member findings.**
- `src/haute/_sandbox.py:346-394` — `_ALLOWED_PICKLE_PREFIXES` whole-package `('numpy',)` + prefix-match logic (the CRITICAL).
- `src/haute/_sandbox.py:405-413, 454-463` — gate reached via both `safe_unpickle` and `safe_joblib_load`.

**Single fix.** Replace whole-package prefix matching with an **exact, fully-qualified symbol allowlist** (module + attribute pairs, e.g. `numpy.core.multiarray._reconstruct`, `numpy.ndarray`, the specific dtype constructors actually needed) and match on equality, not prefix. Pin with a failing test that asserts a known `numpy`-routed gadget is **rejected** by both `safe_unpickle` and `safe_joblib_load`, and that the legitimately-needed reconstruct symbols still load.

**Impact:** real-world **security (arbitrary code execution)**. **Effort: S.** Highest priority by reachability — it gates every model load.

---

### P0-2 — Deploy artifact identity: validate scores a *different* model than the container serves (HIGH, served-price)

> **Dissolved by Architecture Roadmap #2** (cluster pair **C3 + C4**) — "make artifact identity a first-class input via one `artifact_identity_fingerprint` helper." Roadmap rank **#1: do first** among structural changes.

**Defect.** Artifact bytes/version are **not** threaded into either the test-before-live validation **or** the output-schema freshness key. Two faces of one gap:
- **C4 (validate ≠ serve):** `score_test_quotes` (`deploy/_validators.py:375-380`) and `infer_output_schema` (`deploy/_schema.py:134-139`) call `score_graph(...)` **without** `artifact_paths`, while the container **does** pass `artifact_paths=_artifact_paths` (`deploy/_container.py:394,407`). So the "before anything goes live" gate loads a model from MLflow `latest` (mutable) that can differ from the bundled bytes the container serves — **and skips the contract check**.
- **C3 (stale baked signature):** `infer_output_schema` keys `.haute_cache/output_schema.json` on `graph_fingerprint(graph, output_node_id, *input_node_ids)` **alone** (`_schema.py:95`), excluding artifact bytes — so a retrained-in-place `latest` bakes a stale `ModelSignature`/manifest.

**Member findings.**
- `src/haute/deploy/_validators.py:375-380` — `score_graph` without `artifact_paths` (catalog #7).
- `src/haute/deploy/_schema.py:95-150, 134-139` — fingerprint excludes artifact bytes (catalog #8).
- `src/haute/deploy/_scorer.py:605-704` — misconfigured `modelScore` deploys as a **silent passthrough** (the fail-loud "no artifact" guard is bypassed when neither model nor contract present).
- Same-class C3 members: preamble utility resolved via `sys.path` never hashed (`_cache.py`), `id(scoring_model)` in feature-validation key (`_model_scorer.py`).

**Single fix.** Add `artifact_identity_fingerprint(resolved.artifacts)` (thin wrapper over the existing `execution._stat_gated_runtime_path_fingerprint`, already used at `deploy/_scorer.py:199`) and thread `resolved.artifacts` through **both** (a) `score_graph(..., artifact_paths=...)` at both validate sites and (b) the output-schema cache key — in one pass through `deploy/_schema.py`. `resolved.artifacts` is already available at every call site (it is what the container uses), so this is **wiring + a required-argument tightening**, not new logic. Regression test: assert validate and serve load **byte-identical** artifacts, and that retraining `latest` in place changes the output-schema key.

**Impact:** real-world **served-price** — the **only** theme that can mis-price a **live served quote** or bake a stale signature into a deployed manifest. **Effort: S.** This is the single most urgent real-world fix; land the C4 wiring half as a standalone quick win immediately.

---

### P0-3 — JSON-shred conservation + fingerprint completeness (HIGH, data-loss / stale-served cache)

> **Dissolved by Architecture Roadmap #3** (cluster **C2**, batches **C11 + C12**) — "encode conservation + fingerprint-completeness as a CHECKED invariant in the wave-2 cache spine." This is the **named mandate of the current `wave-2-cache-integrity` branch**.

**Defect.** One walker loses or mis-keys data at multiple cells, **without accounting**, so two structurally-different on-disk configs collapse to one fingerprint (stale cache served as fresh) and rows are silently inflated/dropped:
- `_v2_fingerprint` (`_json_shred.py:108-147`) `continue`s past non-dict tables (line 118) and columns (line 122) → distinct configs collide to one `schema_fingerprint`.
- `shred_to_buffers._walk` (`:597-618`, scalar emit `:628-639`) **flattens a nested list inside a scalar array**, emitting MORE rows than source elements with zero skip accounting (catalog #4, the in-depth HIGH).
- `_resolve_leaf` collapses a mid-walk list to element `[0]` (catalog #12).
- Status route reads the v2 config **without** `validate_v2_schema` (catalog #34); `$value` reserved-sentinel collision drops every row of an affected table (`_json_shred.py:1199-1221`); dotted-key `"a.b"` resolves to `None`.
- Freshness hole: `_data_file_matches` (`:253-271`) returns fresh on size+mtime_ns match **without hashing** — a byte-changing rewrite preserving both serves stale (catalog #14).

**Single fix.** The machinery already exists (`ShredSkipStats.count_record_skip`/`count_row_skip` in meta.json) but the fingerprint path bypasses it. Make conservation a **checked invariant**: (1) replace the two `continue`s with `raise ApiInputSchemaError` (callers validate first → existing 422 path); (2) route every mid-walk-list collapse / nested-list flatten through `count_*_skip` or raise; (3) add a **reconciliation assertion** in `build_per_port_cache` that every DROP and COLLAPSE is accounted (NOT `rows==records` — an N-object array correctly emits N rows), failing the build (atomic temp-dir already rolls back); (4) call `validate_v2_schema` inside `_v2_status_response`; (5) close the freshness hole — verify sha256 on stat-match. Batch **C11** (committed-mirror lock/ns-precision/Windows-safe-rename — reuse sibling `_swap_dir_into_place`) and **C12** (duplicate-key divergence + `store_artifact` over-eager revalidation-evict) in the same files. Pin each with a failing collision/conservation/freshness test first.

**Impact:** real-world **data-loss + stale-served cache** on the cache spine the branch is named for. **Effort: S–M.**

---

### P0-4 — Parser structure-conservation: silent node/submodel loss (HIGH, wrong-graph / data-loss)

> **Dissolved by Architecture Roadmap #4** (cluster **C5**, 7 members) — "a parse-time `assert_structure_conserved(source, graph)` pass; fail-loud by construction, not per-shape."

**Defect.** The parser drops/collapses authored graph structure at many independent boundaries **without a loud error** — a direct CLAUDE.md fail-loud violation. The worst member is itself a HIGH: a single syntax error in the main file makes the regex fallback parser **silently discard ALL submodels** and their nodes/edges.

**Member findings.**
- `src/haute/_parser_regex.py:658-767` — `fallback_parse` never calls `extract_submodel_calls`/`merge_submodels`; contrast healthy path `parser.py:182-211` (the HIGH).
- `tests/test_parser.py:836-865` — `test_duplicate_function_names_both_appear` actively **pins the silent node-loss bug** (duplicate `@pipeline` names → one collapsed GraphNode; catalog #19).
- `src/haute/_parser_submodels.py:173-198` — direct edge between children of two submodels loses its boundary handle on flatten (catalog #21).
- Async `def` nodes silently dropped (catalog #20); two submodel refs → same `pipeline_name` silently overwrite (catalog #22); aliased-import preamble over-capture (`_json_shred`-adjacent, catalog #48).

**Single fix.** One always-run post-parse `assert_structure_conserved(source, graph)`: AST-walk decorated `FunctionDef`+`AsyncFunctionDef`, regex-scan top-level `pipeline.submodel("...")` literals, collect `connect()`/param-name edges; assert each authored function name is unique (raise `ParseError` naming duplicates — fixes `_ast_helpers._extract_function_bodies`), maps to exactly one GraphNode, each submodel resolves to a distinct `pipeline_name`, each authored edge survives — against an **explicit authored→expected allowlist** (not naive count-equality) so legitimate transforms aren't false-positives. Under the same umbrella: make the regex fallback **replicate the healthy path's submodel recovery** (scoped to the submodel literals only, so it doesn't itself re-raise on the triggering syntax error); raise a clear unsupported-async `ParseError`; thread child `param_names` through `merge_submodels`. **Delete the test that pins the bug** and replace with a conservation test. Each gated by a failing test first.

**Impact:** real-world **wrong-graph / silent structure loss** (GUI shows a graph missing a node's pricing body). **Effort: M.**

---

### P0-5 — Codegen shared `apply_*_from_config` helper: the passthrough-body class (HIGH, **standalone-only**)

> **Dissolved by Architecture Roadmap #1** (cluster **C1**, 3 members) — "complete the shared runtime-helper pattern for EVERY stateful NodeType, enforce as a registry invariant, gate with an execution-differential harness." Highest **structural** leverage; all five panels converged on it.

> **Severity-corrected impact (orchestrator-notes §"codegen passthrough cluster"):** **standalone-portability only.** Deploy (`deploy/_model_code.py:49` runs the executor on the pruned graph) and in-canvas preview/trace/batch are **SAFE**. The defect surfaces **only** when an exported `.py` is run via `pipeline.run()` (batch) / `.score()` (live) — `Node.__call__` (`pipeline.py:54-68`) executes the literal generated body with no executor dispatch.

**Defect.** Five node types abandoned the shared-helper pattern that banding/ratingStep/modelScore use, so their **generated bodies do nothing** (or route the wrong branch) under standalone execution, while the canvas executor does the real work:
- `_codegen_builders.py:391-417, 784-858` — `_OPTIMISER`/`_OPTIMISER_APPLY`/`_MODELLING`/`_SCENARIO_EXPANDER` emit literal `return {first}` via `_make_passthrough_builder` (catalog #2, #3).
- `_codegen_builders.py:521-539` (`_gen_live_switch`) — generated liveSwitch hard-wires the `live` input; standalone `pipeline.run()` (batch) selects the wrong branch (catalog #1).
- `_codegen_builders.py:572-603` (`_gen_constant`) — emits columns for empty/missing-name constant entries, diverging from the executor which skips them.

**Single fix.** Finish the pattern: lift node-application logic into shared `apply_optimiser_apply_from_config` / `apply_optimiser_from_config` / `apply_modelling_from_config` / `expand_scenarios_from_config` / `select_live_switch_input` helpers in `graph_utils.py` (re-exported beside `apply_banding_from_config`); rewrite the five templates to the `_BANDING_SINGLE`/`_MODEL_SCORE` shape that imports+calls the helper; thread runtime source via the existing `_scenario_ctx` contextvar (so liveSwitch becomes branch-correct **without** editing `Node.__call__`/`pipeline.py`); have the executor builders delegate to the same helpers (genuinely one code path). Then make it **unrepresentable**: extend `_registry.validate_registry_complete` with an `is_behavioural` flag so any non-passthrough type whose template emits bare `return {first}` **raises at import**. Add the guard the structural round-trip test cannot provide by construction — `tests/test_codegen_execution_equivalence.py`: codegen → write sidecars → import → run `pipeline.run()`/`.score()` under source∈{live,batch} → frame-diff against `execute_graph`. Retires `_make_passthrough_builder` and the `_optimiser_apply_ratebook_return_source` string-surgery as dead code.

**Impact:** **standalone-only** (the README "it's just Python, take it with you" promise) — **not** deployed/in-canvas mispricing. **Effort: M.** Top structural leverage, but ranked below themes 1–4 because the real-world blast radius is the exported file only.

---

### P0-6 — Chunk-sizing OOM under-bound (HIGH, crash / data-loss-of-work)

> **Dissolved by Architecture Roadmap #5** (cluster **C13**, HIGH member) — "cost byte-budget chunk sizing from the TARGET node's projected output schema, not the source schema."

**Defect.** `_plan_chunk_sizes` (`chunking.py:607-626`) sizes the chunk from `_estimate_projected_row_bytes` (`:629-651`), which obtains real widths **only** via `_source_projected_column_widths` (`:654-692`) — and that returns `{}` for any non-`DATA_SOURCE` node. So every downstream-created **wide/string column** absent from the source falls back to `_DEFAULT_PROJECTED_COLUMN_BYTES` (~64 bytes), and the computed chunk can far exceed the memory budget → **OOM** (catalog #5).

**Single fix.** Cost the target row width from the **TARGET node's projected output schema**: `collect_schema()` on the target lazyframe restricted to `needed_by_node[target]`, sampling String/variable-width columns the way `_source_projected_column_widths` already samples the source (`:661-687`), instead of reading the source schema for target columns. Preserve the existing `ChunkPlanUnsupportedError` fail-loud (no silent fallback — a wrong width is the bug). Keep `collect_schema()` to schema resolution + a bounded width sample (reuse `_ROW_BYTE_SAMPLE_SIZE`). Test: a transform creating a wide string column absent from the source must produce a plan that respects the byte budget. The continuous-memory-enforcement member (`_execution_context.py`, catalog #42) is explicitly **deferred** (L-effort, lower priority).

**Impact:** real-world **process-killing OOM** under load (loses in-flight work). **Effort: M.**

---

### P0-7 — Crash & deadlock class: specific-input aborts (HIGH, crash / hang)

**Defect.** Three independent code paths abort or hang the **entire operation** on a specific input, with no recovery — distinct root causes but one user-facing class (whole-operation crash/hang) and all currently **untested**.

**Member findings.**
- `src/haute/routes/_explore_service.py:331` — a Binary column with **non-UTF-8 bytes** crashes the entire Explore materialisation via a strict `cast(pl.String)` in value-counts (catalog #99). Untested: `tests/test_explore_routes.py:785-908`.
- `src/haute/_worker_isolation` (run_isolated_worker) — a multi-MiB return payload **deadlocks** (`mp.Queue` feeder-pipe: parent joins child before draining; catalog #36). Untested: `tests/test_worker_isolation.py:47-51` (only tiny payloads).
- `src/haute/_trace_enrichment` / `_expression_parser` — dtype-asymmetric correlation (numeric value vs string parent column) **aborts the entire trace** (catalog #27). Untested across `tests/test_trace*.py`.

**Single fix (per member, batched).** Explore: replace the strict cast with a lossy/`strict=False` decode or a Binary-aware value-counts branch (decode-or-`�`, never crash the whole frame). Worker isolation: drain the queue **before** `join()` (or use a pipe/spawn-safe transport sized for large payloads). Trace correlation: guard the dtype-asymmetric compare (the fail-loud direction is an explicit "unresolved" sentinel, never a wrong attribution). Pin each with the named failing test first.

**Impact:** real-world **crash/hang** (no served-price corruption, but a dead Explore tab / hung worker / aborted trace). **Effort: S–M.**

---

### P0-8 — Documentation falsely promises portability & wrong deploy secret names (HIGH, user self-inflicted prod breakage)

**Defect.** The README and deploy guides assert behaviours that are false in ways that cause a user to **break their own production deploy** or trust a lineage that diverges. These are HIGH because they directly cause real-world misconfiguration, even though no `haute` code is wrong.

**Member findings.**
- `README.md:45` (CRITICAL) — "everything you've built still works - it's just Python" is **FALSE** for optimiser/optimiserApply/modelling/scenarioExpander standalone (the P0-5 passthrough class); the standalone file silently no-ops.
- `docs/deployment/targets/databricks.md:96-97` (+ github-actions/gitlab/azure-devops guides) — every guide names secrets `DATABRICKS_HOST`/`DATABRICKS_TOKEN`, but the generated workflow requires `DATABRICKS_RATING_HOST`/`DATABRICKS_RATING_TOKEN` → **deploy fails for anyone following the docs**.
- `docs/deployment/index.md:60-72, 84-86` — fabricated `haute init` before/after tree; claims root `main.py` persists and "nothing is overwritten", but init **deletes root `main.py`** and writes `rating/main.py`.
- `README.md:123` — "Before anything goes live, Haute runs your pipeline against test inputs" overstated — the gate scores a **different** model than the container ships (this is P0-2; the doc claim is the user-facing face of it).
- `README.md:148-151` — RAM estimator "prevents silent crash" overstated — it is cgroup-blind (over-estimates → OOM in containers; catalog #31).
- `_scaffold.py:890-891` — Azure DevOps scaffold emits **invalid YAML** (production-deploy `env:` secrets under-indented), breaking every generated `.azure-pipelines.yml` (catalog-class; untested at `tests/test_scaffold.py:882-887` which parses only the header).

**Single fix.** Correct the docs to match shipped behaviour (real secret names, real `haute init` tree, accurate portability scope once P0-5 lands), and fix the Azure DevOps scaffold indentation (with a full-YAML parse test, not header-only). The portability claims (`README:45/123`) are **resolved jointly with P0-5/P0-2** — fix the code, then the doc becomes true; until then the doc must state the standalone limitation.

**Impact:** real-world **user self-inflicted prod breakage** (failed deploys, lost `main.py`, broken Azure pipelines). **Effort: S–M.**

---

### P0-9 — Supply-chain dependency advisories (CRITICAL/HIGH, security)

**Defect.** Multiple direct/transitive dependencies carry security advisories flagged in the P4-dependencies sweep. These are environment/build security, independent of `haute` source.

**Member findings.** CRITICAL: `mlflow`, `vitest`, `@vitest/coverage-v8`. HIGH: `pyjwt`, `gitpython`, `pillow`, `starlette`, `python-multipart`, `vite`, `undici`.

**Single fix.** Bump each to the advisory-clearing version, run the suite, and (per `feedback_fix_all_ci.md`) confirm CI is green — do not dismiss any as pre-existing. Re-escalate the `pyproject.toml:148-152` `filterwarnings` gap (only `RuntimeWarning` → error; Deprecation/Future/Resource swallowed) so future dependency-drift fails loudly.

**Impact:** real-world **security (CVE exposure)** in build/runtime. **Effort: S** (version bumps + green CI), assuming no breaking-change cascade.

---

### Must-fix checklist (ordered)

1. **[P0-1] Exact-symbol unpickle allowlist** — `_sandbox.py:346-413,454-463`; kill whole-package `numpy` prefix match. RCE. **S. Do first.**
2. **[P0-2 / Roadmap #2] Thread `resolved.artifacts` into deploy validate + output-schema key** — `deploy/_validators.py:375-380`, `deploy/_schema.py:95-150`. The only **live-served-price** divergence. **S.** (Land the C4 wiring half immediately as a quick win.)
3. **[P0-3 / Roadmap #3] Shred conservation + fingerprint-completeness as a checked invariant (+C11/C12)** — `_json_shred.py:108-147,253-271,597-652`, `routes/json_cache.py`. Data-loss / stale-served cache; the wave-2 mandate. **S–M.**
4. **[P0-4 / Roadmap #4] Parse-time `assert_structure_conserved` + regex-fallback submodel recovery** — `_parser_regex.py:658-767`, `parser.py`, `_parser_submodels.py`; delete the bug-pinning test `test_parser.py:836-865`. Silent structure loss. **M.**
5. **[P0-6 / Roadmap #5] Chunk-size from TARGET projected schema** — `chunking.py:607-692`. Process-killing OOM. **M.**
6. **[P0-7] Batch the three crash/hang fixes** — Explore Binary cast (`_explore_service.py:331`), worker-isolation queue drain-before-join, trace dtype-asymmetric guard; each with its named failing test. **S–M.**
7. **[P0-5 / Roadmap #1] Complete `apply_*_from_config` helpers + `is_behavioural` registry invariant + execution-differential harness** — `graph_utils.py`, `_codegen_builders.py:391-539`, `_registry.py:166-209`, NEW `tests/test_codegen_execution_equivalence.py`. **Standalone-only** (top structural leverage; not deployed mispricing). **M.**
8. **[P0-8] Fix deploy-guide secret names, `haute init` tree, Azure DevOps scaffold YAML; scope the README portability claim** — `docs/deployment/*`, `README.md:45,123,148`, `_scaffold.py:890-891`. User self-inflicted prod breakage. **S–M.** (Portability claims close with #2/#7.)
9. **[P0-9] Bump advisory dependencies to green; tighten `filterwarnings`** — `pyproject.toml:148-152`. CVE exposure. **S.**

> **Sequencing note:** items 1–5 are the correctness/security spine and share the cross-cutting **fail-loud-or-account** discipline (themes 3, 4, and the trace de-laundering in 7 all enforce the same invariant); land them first. Items 7–9 are crash-class / doc / supply-chain and can proceed in parallel. Per `feedback_no_auto_merge.md`, accumulate on the single PR for Ralph's review — do not merge.

---

**Files read (absolute):** `C:\Users\prici\haute\review\MASTER\INDEX.md`, `C:\Users\prici\haute\review\ARCHITECTURE-ROADMAP.md`, `C:\Users\prici\haute\review\02-findings\orchestrator-notes.md`, `C:\Users\prici\haute\review\02-findings\catalog.md`.

---

# P1 — SHOULD-FIX

This program collects the **257 MEDIUM findings**, the **93 sound simplifications**, and the high-leverage items from the **10 quality dimensions** into one prioritised plan. P1 is everything that is *not* a P0 release-blocker but materially degrades correctness, maintainability, or the product's core promises. Items are grouped by **root cause**, not by file — fix the cause once and the cluster collapses.

The ordering principle within P1:
1. **A — Medium correctness bugs** (silent-wrongness first; these mis-price or mislead).
2. **B — Structural simplifications** (the god-file decompositions that *unblock* the correctness work, then genuine dedups).
3. **C — Type-design & hardening** (make the silent-wrongness classes unrepresentable, then docs/a11y).

A and C deliberately overlap on two root causes (the **codegen↔executor passthrough cluster** and the **Float32 rating-key**): the bug fix and the type-level prevention are listed once each, cross-referenced, and should ship as a single PR.

---

## A. Medium correctness bugs, by subsystem

These are the MEDIUM-severity silent-wrongness findings. They are grouped so that one fix (or one shared helper) closes several at once. Each group cites representative `file:line`.

### A1. Trace/explanation evaluator does not faithfully reproduce Polars (the regulator-facing lie)

This is the single largest medium cluster and the one most damaging to the "*See exactly how it was calculated*" promise. The expression evaluator (`_expression_parser.py`) is dtype-unaware and wraps everything in `except Exception → raw row value`, so it displays values Polars never computed and launders its own bugs as "self-consistent".

| Defect | Location | Symptom |
|---|---|---|
| Kleene 3-valued logic missing — `False & null` / `True | null` return `None` | `_expression_parser.py:1616-1635` (`_binop` null-guard 1619-1620) | mis-selects `when/then` branches in the dominant pricing pattern |
| Integer arithmetic in unbounded Python int, not Int64 | `_expression_parser.py:1616-1635` | shows bignum where Polars wrapped (overflow) |
| `**` of negative base, fractional exp → Python `complex` | `_expression_parser.py:1616-1635` | complex value where Polars yields NaN; breaks JSON serialisation |
| `clip()` applies lower-then-upper (upper wins on `lower>upper`) | `_expression_parser.py:1781-1802` | diverges from Polars on contradictory bounds |
| `log()/sqrt()` out-of-domain return `None` not NaN/-inf | `_expression_parser.py:1887-1899` | wrong downstream `is_nan/fill_nan` flow |
| `max_horizontal/min_horizontal` via Python `max/min` (NaN order-dependent) | `_expression_parser.py:1990-2017` | non-deterministic vs Polars |
| Div/floor-div/mod by zero raises → masked to observed value | `_expression_parser.py:1616-1635` + `_compute_result` 1528-1539 | should be `±inf` (float) / null (int floordiv) |
| `round()` via Python `round` differs at binary-non-representable midpoints | `_expression_parser.py:1765-1772` | monetary rounding off by one ULP-scaled unit |

**Priority within group:** Kleene logic and integer-wraparound first (they change *branch selection* and headline numbers), then the domain ops. **The enabling prerequisite is removing the catch-all `except Exception` fallback** (tracked as a P0/HIGH subsystem item at `_expression_parser.py:993-1003,1302-1325,1536-1539,2149-2156`) so these stop masking. Do that decomposition (see **B1**) before patching the arithmetic, or the regressions remain invisible.

### A2. Dtype-dependent rating/banding keys — the same nominal value yields a different lookup key

A focused, high-value correctness family: a factor's canonical lookup key depends on its *column dtype*, so an authored table entry silently misses.

- **Float32 factor key** (the flagship): `_rating_key_expr` casts the column at its native f32 precision while `normalise_rating_key` always widens to f64 — engine key ≠ Python-mirror key ≠ authored string for every non-dyadic decimal. `_rating.py:346-370` vs `309-343`; reached via `_apply_rating_table:583,596`. Fix = widen Float32→Float64 before the Utf8 cast in *both*. This also fixes the **continuous-banding trace re-match** that shows `status='no_match'` for a band the engine actually applied (`_trace_enrichment.py:285-322,540-546`). *(This item is the "numerical: Float32 rating-key" investment from the dimensions report — fix here, not twice.)*
- **Decimal factor key** authored-scale sensitivity (`_rating.py:346-370`, plain-cast branch) — `'25.5'` vs `'25.50'`; lower priority, document + normalise.
- **Open-ended-only breakpoints factor produces NO output column** (`_rating.py:136-220`, tail-append 213-218) — silent passthrough on a plausible misconfig; should fail loud.

### A3. codegen ↔ executor passthrough divergence (standalone file prices differently than the canvas)

Generated standalone bodies for `optimiser`, `optimiserApply`, `modelling`, `scenarioExpander` (and `liveSwitch`) emit `return {first}` — a no-op — while the canvas executor applies real logic. This **falsifies the README's central portability promise** ("*it's just Python*"). Representative: `_codegen_builders.py:391-417,784-858`; `codegen.py:304-345`. The money-precision gap belongs here too: **raw IEEE-754 Float64 premium is served to the customer with rounding applied exactly zero times** (`deploy/_container.py:305-315,337`).

**This is a correctness bug AND a type-design bug.** The durable fix is the registry invariant in **C1** (encode passthrough-vs-stateful, require one shared `apply_*_from_config` helper). Treat A3 + C1 as one workstream; until it lands, the README claim must be softened (see **C4**).

### A4. JSON shred / cache freshness — silent row loss and stale serves

`_json_shred.py` has a cluster of silent data-loss and stale-cache defects:

- **`_data_file_matches` returns fresh on size+mtime match without hashing** (`_json_shred.py:253-271`) — a byte-changing rewrite that preserves both serves stale cache. (Same class: preamble-utility fingerprint hashes only `pipeline_dir/cwd`, missing `sys.path`-resolved utilities — `_cache.py:265-289,302-317`.)
- **`$value` / dotted-key collisions** silently drop every row of a table or resolve a value to `None` (`_json_shred.py:1203,1213` interacting with `_resolve_leaf` 526-536; header comment at `:70-71` asserts the collision is impossible — it is not). The fix is a guard in `validate_v2_schema`.
- **Mid-walk list collapse to element `[0]`** with no skip accounting (`_resolve_leaf`, `_json_shred.py:529-536`).
- **`mirror_cache_to_committed` bypasses the per-dir build lock and uses bare `rename`** (no Windows retry), unlike `_swap_dir_into_place` (`_json_flatten.py:239-317`).

### A5. Optimiser frontier / ratebook — wrong swept ranges and re-solve drift

- **Auto-range emits ABSOLUTE constraint-sum ranges for `min_pct/max_pct` constraints**, which the solver reads as fractions-of-baseline → swept range off by 6+ orders of magnitude (`_optimiser_service.py:1585-1652,4537-4605` → `1366-1406`).
- **Ratebook frontier-point save canonicalises levels against the banding-source dtype but applies against the optimiserApply-input dtype** → dtype-mismatch miss (`_optimiser_service.py:1701-1761,4660-4671` vs `_builders.py:1571-1621`).
- **Non-integer Float `scenario_index` passes the finite check then is silently truncated on Int32 cast**, merging distinct scenario steps (`_optimiser_service.py:4458-4479`).
- **`scenario_value_stats` differ for the same optimum** depending on solve-summary (Polars sample-std) vs frontier-point (library population-std) path (`optimiser.py:480-499` vs `_optimiser_service.py:1300-1312`).
- **NaN constraint-total turns frontier select/apply/save into a hard 500** instead of a usable point (`optimiser.py:468-477,380-392`).
- **User-fixable optimiser config errors surface as HTTP 500** (raw `ValueError` indistinguishable from internal failure) — `_optimiser_service.py:1188-1234,1381-1404`.

### A6. Parser / submodel structure loss (entire subgraphs silently dropped)

- **Implicit param-name edge from a main-file node into a submodel child is silently dropped** (`parser.py:205-211`; `_parser_submodels.py:144-168`).
- **Two submodel references resolving to the same `pipeline_name` overwrite each other**, losing a subgraph (`parser.py:198-202`).
- Plus the structural-conservation gaps already catalogued as HIGH (`_ast_helpers.py`, `_graph_builders.py:73`) — the parser has **no structure-conservation invariant**, and a test actively *pins* the node-loss bug (see **C3**).

### A7. Modelling — train/predict semantic mismatches

- **Training metrics use `predict_proba[:,1]` but the scored `prediction` column is the hard class label** (`_algorithms.py:608-615`; `_mlflow_io.py:351-354`; `_model_scorer.py:585-593`) — and the SHAP explanation reports the hard label as the headline while contributions reconstruct log-odds (`_model_explainability.py:206-214,261-279`), producing a waterfall that cannot reconcile.
- **MLflow logging aborts for any Date/Datetime/Decimal/Time/Duration feature dtype** (`modelling/_signature.py:14-27,70-73`; `_training_job.py:124-140`).
- **Empty-input batch scoring hardcodes prediction column to Float64**, diverging from the Int64 the same model emits on non-empty input (`_model_scorer.py:1393-1403`).
- **Temporal/group split edge cases**: holdout swallows all post-cutoff rows when `validation_size==0` (`modelling/_split.py:300-314`); group-mask fallback leaves both validation and train empty (`:351-358`).

### A8. Concurrency & resource correctness

- **Optimiser background worker subscripts the unlocked `.jobs` dict in ~20 sites incl. exception handlers** — a concurrent TTL eviction races (`_optimiser_service.py:3478…3942`).
- **Train/solve status timeouts cannot fire while the job is still executing** (`start_time` stamped only after `_execute_and_sink`) — `_train_service.py:436,501,1042-1054`.
- **`available_ram_bytes` is cgroup-blind** (over-estimates → container OOM) and **silently falls back to a fixed 4 GiB** (under-estimates → silent downsample truncation) — `_ram_estimate.py:51-126,832-998`. This breaks the "*Knows your machine's limits … prevents silent crash*" claim.

### A9. Frontend correctness — source-blind caches and out-of-order responses

- **Node-level `_columns`/`_availableColumns` are source-blind and not invalidated on active-source change** (`usePipelineAPI.ts:116-156`; `useSettingsStore.ts:168`) — editors and edge-join validation operate on stale columns. Same family: **preview LRU slot keyed by `nodeId` alone** while freshness depends on `(structuralVersion, source, rowLimit)` (`useNodeResultsStore.ts:95-100,647-672`).
- **Out-of-order response races** with no abort/sequence guard: trace (`useTracing.ts:207-240`), MLflow browser (`useMlflowBrowser.ts:112-144`), post-unmount poll reschedules forever (`useJobPolling.ts:304-312`).
- **Editor desync bugs**: duplicating a node copies cached preview/result fields (`useNodeHandlers.ts:75-81`); EdgeJoin paired keys normalised independently → unequal-length arrays the backend rejects (`EdgeJoinEditor.tsx:113-133`); feature names round-tripped through `join(",")/split(",")` corrupt comma-containing names (`AveTab.tsx:51,58-65`); removing a GLM factor leaves orphaned interaction refs that flip `include_main` (`GLMFactorConfig.tsx:134-137`).

---

## B. Structural simplifications (highest value first)

The audit verified **93 sound simplifications** (`severity=="sim"`). The top tier are **god-file decompositions** that directly de-risk Section A; the rest are genuine dedups that remove drift-prone duplicate logic.

### B1. God-file decompositions (do these first — they unblock the bug work)

These four files are the cross-phase hotspots (`_expression_parser.py` 34 findings, `_optimiser_service.py` 20, `_execute_lazy.py` 10, plus `enrich_steps`). Decomposing them is the prerequisite for fixing A1 (you cannot safely patch the evaluator while a catch-all hides regressions) and A5/A8 (the two near-identical optimiser job runners must be unified before the range/locking fixes).

| Target | Locations | What to extract |
|---|---|---|
| **`_expression_parser.py`** | `572-635` + `1938-1978`; `553-570`; `1006-1168` (10 `opaque` literals) | Hoist one `_collect_when_then_chain` (kills duplicated converter/evaluator clause logic); collapse `_pl_when_chain/_chained_when/_when_continuation` → one `_format_when_entry`; add an `_opaque(...)` factory for the 10 repeated `ParsedExpression(...,"opaque",...)` literals. |
| **`_optimiser_service.py`** | `3562-3658` vs `3853-3949` | Extract the shared frontier-auto-range job body (the streaming and non-streaming runners are near-duplicates) — must thread `body.node_id`. This is also where the A5/A8 fixes land. |
| **`_execute_lazy.py`** | `746-882`; `1725-1762` + `1926-1933` | Extract the `cache_request` block (treat `cache_backed_node_ids` as a returned-but-mutable accumulator) and fold the duplicate model-score-schema guard into `_full_model_score_schema` returning `(full_columns, is_plain_model_score)`. |
| **`enrich_steps`** (`_trace_enrichment.py`) | `492-645` (`enrich_banding`); `1781-1796` (op-type sniffing) | Extract `enrich_banding` helper bodies *inside* the existing try/except (preserve degraded-dict contract); hoist `_sniff_operation_type` backed by an ordered table. |

Adjacent structural wins in the same hot files: **`executor.execute_graph` three cache arms** (`executor.py:918-1077`) → `_run_eager`/`_store_and_pin`/`_merge_cached_entry`, ideally returning an `EagerResult` instead of the 8-tuple; **`deploy/_scorer.score_graph_lazy._intercept`** (`_scorer.py:469-733`) → per-node-type helpers mirroring the `_register` convention; **`validate_v2_schema`** (`_api_input_schema.py:263-403`) → extract-function refactor (this is where the A4 `$value` guard belongs). Each of these is flagged "dev+reviewer pair, not mechanical" — they touch shared accumulators.

### B2. Genuine dedups (remove duplicated logic that *will* drift)

The valuable dedups are the ones where the duplicate is a correctness liability, not cosmetics:

- **`Contract`, `_freeze`, `_freeze_mapping` duplicated verbatim** in `_builders.py:160-309` vs `_contracts.py:35-148` — delete the local copies, import the canonical (sim #57). High value: two copies of the column-contract machinery is exactly the kind of drift that produced A3.
- **Terminal-reason / job-status sets duplicated 4×**: `_background_jobs.py:231-242` hardcodes a 4th copy of the terminal-reason set (sim #91); `routes/modelling.py:109-143` duplicates the `TrainStatusResponse` builder (sim #90). These feed directly into the type consolidation in **C2**.
- **`canonical_json` bypassed by hand-rolled `json.dumps`** in `execution.py:509` (sim #50) — re-route through the mandated single encoder (dev/reviewer pair per CLAUDE.md, fingerprint-affecting).
- **Shared utility-path enumeration duplicated** (`executor._utility_module_candidates` vs `_cache._utility_candidates_for_dir`, sim #43) — relevant to the A4 preamble-fingerprint fix.
- **Rank/truncate-contributions duplicated** across CatBoost and RustyStats explainers (`_model_explainability.py:231-259,412-443`, sim #52); **submodel cross-boundary handle validation hand-rolled twice** (`codegen.py:1013-1068`, sim #46) and **two consecutive edge loops recompute identical `_resolve_submodel_endpoint`** (`codegen.py:1156-1235`, sim #47) — these touch the A6 submodel paths.
- Frontend: **MLflow wire types re-declared in `useMlflowBrowser.ts:21-36`** and drift from `api/types.ts` (sim #82); **stores subscribed without selectors** re-render App on every unrelated change (`useWebSocketSync.ts:93-94`, `useKeyboardShortcuts.ts:33`, sims #66/#78).

The remaining ~60 sims (single-use locals, redundant Fragments, double-IIFEs, `set()`-rebuilt-in-comprehension, etc.) are **mechanical W7/W8a-class** — batch them under one "tidy" PR, no dev/reviewer pair, no re-review of verbatim fix-ups (per the calibrated-review-split memory).

---

## C. Type-design, test-quality, docs & a11y hardening (from the 10 dimensions)

These make the silent-wrongness classes in Section A *unrepresentable* rather than merely fixed once.

### C1. Encode passthrough-vs-stateful in the node registry (root cause of A3)

The registry (`_registry.py:47-65`) carries `exec` and `codegen` as two free function pointers with **no shared declaration of execution semantics**, so the two builders independently decide and silently disagree — this is the type-level root cause of the entire A3 cluster.

- Add `kind: NodeSemantics` (`PASSTHROUGH | STATEFUL | SOURCE`) to `NodeRegistryEntry` and require it at registration.
- For `STATEFUL` nodes require a single `apply: ApplyFn` helper that **both** the exec builder and the codegen-emitted body call (promotes the "one shared apply helper" rule from convention-kept-for-3-types-violated-for-4 to a constructor-enforced invariant).
- Freeze the registry after completion (fields are currently mutable-public; `column_contract` is type-Optional but runtime-required and excluded from `validate_registry_complete`).
- Replace the unnamed `(str, Callable, bool)` builder tuple (`_builders.py:141,1317,1367`) with a `BuiltNode(func_name, fn, role)` dataclass so `is_source` reorder bugs become type errors.

**Ship C1 with A3 as one PR.** This is the highest-leverage type investment in the report.

### C2. Stringly-typed → `Literal`/`StrEnum` (closing typo-routes-to-wrong-branch holes)

A large, mostly-mechanical family where a closed set is typed as open `str` and re-validated by hand against module-private frozensets in 2–4 places. Prioritise by blast radius:

1. **`sourceType`** — three incompatible per-node-type domains hand-compared against bare literals in 15+ files; a single typo (`'registerd'`, `'flatfile'`) routes to the wrong loader with no error (`_types.py:111,166,386`). **Highest value.**
2. **Model `flavour`** (duplicated across 4 sites, `_model_scorer.py` / `_mlflow_io.py`) and **`task`** (a `Task` Literal already exists in `_feature_contract.py:29` but the hot path uses bare `str`).
3. **Job-status vocabulary unification**: `JobStatus` and `TerminalReason` are two independently-authored literal sets that must agree by hand (`schemas.py:33-42` vs `_job_lifecycle.py:24-45`); `terminal_reason` is bare `str|None` on five response models though the canonical `TerminalReason` Literal exists. Express `JobStatus = Literal['running'] | TerminalReason` once. The GUI's `terminal_reason === 'memory_limited'` branch *silently fails to match* a typo today.
4. **Rating `operation`/`onMissing`**, **edgeJoin `how`** (+ the `on` XOR `(leftOn,rightOn)` invariant), **`BandingFactor.banding`** (whose comment-documented domain contradicts the real engine domain), **GLM `family`/`link`**, **CatBoost loss names**, **MLflow `source_type`/`version`**.
5. Constrained scalars: **`progress` as `Annotated[float, Field(ge=0,le=1,allow_inf_nan=False)]`** (NaN/inf currently reach progress bars); **`OptimiserFrontierRange`** response model permits `min>max`/non-finite though the request side rejects them.

### C3. Illegal-states-unrepresentable on the core models

- **`NodeData.config: dict[str, Any]`** with no discriminated union tying `nodeType` → config shape — the 18 per-type TypedDicts are documentation-only (`_types.py:593-599`). Model as a `Field(discriminator=...)` union or at minimum a `@model_validator`. **Largest single bug-surface class in the graph model.**
- **Job record is an untyped `dict[str, Any]`** with stringly-keyed required fields (`_job_store.py:80,342,…`) — introduce a `JobRecord` TypedDict with `Required/NotRequired`; type `expected_status: JobStatus`, artifact handles, and lift the heavy-object bookkeeping out of the shared namespace.
- **`NodeResult` makes contradictory ok/error states representable** (`schemas.py:229-244`) — model the sum type (`NodeOk | NodeError`), or at minimum `status: Literal['ok','error']`.
- **Graph models are mutable with no `validate_assignment`** and `PipelineGraph` permits **duplicate node ids** (`node_map` silently collapses) and **`active_source ∉ sources`** though the identical invariant is enforced on `SidecarModel` (`_types.py:638-705,656-657`). Add the validators + `frozen`/`validate_assignment`.
- Make **`ScoringModel`** and **`FeatureContract`** genuinely immutable (both are "frozen" in name but hold mutable list/dict fields, desyncing their content-hash). Use the already-exemplary `ScoreWriteProjection` as the reference pattern.

### C4. The 12 tests that codify bugs (delete-or-invert before fixing)

Twelve tests **actively pin verified-wrong behaviour as correct** — they will block the very fixes in Section A. Each must be converted to a failing TDD regression *as part of* its paired fix (never leave a test asserting a non-contract):

| Test | Pins (bug) | Paired fix |
|---|---|---|
| `test_parser.py:836-865` `test_duplicate_function_names_both_appear` | silent node-loss collapse | A6 |
| `test_rating_key_agreement.py:177-199` | Float32 divergence as "out of scope" | A2 |
| `test_rating_key_agreement.py` (string-label compact→expand) | read-side migration "always-correct" (V038) | A2 |
| `test_banding.py:1088-1093` `test_breakpoints_only_open_ended` | open-ended-only → zero rules | A2 |
| `test_rating.py` / `test_banding.py:926-950` (NaN/Inf) | source float column untouched not asserted (V025) | A7/A2 |
| `test_deploy.py:304-323,485-508` + serve-side gap | silent-skip of misconfigured modelScore (V044) | A3 |
| `test_deploy_internals.py:481-528` `test_cache_hit` | output-schema short-circuit excludes artifact identity | A3 |
| `test_expression_parser.py:1190-1207` (`== correct OR is None`) | evaluator value-laundering "acceptable" | A1 |
| `test_caching_correctness.py:546-556` + NaN-set | `canonical_json` set order-independence (V034) | (cache) |
| `test_optimiser_routes.py:5140-5186` | `frontier_min/max` broadcast-to-every-constraint (#44) | A5 |
| `test_algorithms_coverage.py:641-656` | classifier `predict()` returns probability | A7 |
| `test_optimiser_routes.py` ratebook frontier-point save | re-solve total "authoritative" (#16) | A5 |

Beyond these, the dimensions report flags broad **CI-prevention gaps** worth one structural investment: no parse→codegen→execute **differential VALUES harness** (structural roundtrip passes while standalone values diverge — the A3 detector), and no **cross-language NodeType / OpenAPI contract snapshot** shared between the disjoint backend/frontend CI lanes.

### C5. Documentation accuracy (the docs that send a by-the-book user into a wall)

The docs lens found claims that are not soft overstatement but **operationally false**. Highest priority are the ones that fail a first setup or mislead a regulator:

- **Deploy secret names**: every guide says `DATABRICKS_HOST/_TOKEN`; the generated workflow requires `DATABRICKS_RATING_HOST/_TOKEN` — *the by-the-book setup fails its first deploy* (`databricks.md:96-97` + 3 sibling CI guides).
- **`pipeline = "main.py"`** everywhere in the guides, but the scaffold deletes root `main.py` and writes `rating/main.py` (`deployment/index.md:84-91,60-72`); the before/after tree is fabricated.
- **README portability / trace claims** — gate behind the A3 + A1 fixes (cross-referenced above); until then soften "*it's just Python*", "*near-instant preview*", "*See exactly how it was calculated*", and "*prevents silent crash*".
- **ARCHITECTURE** says "17 node types" (real: 20; `edgeJoin`+`explore` omitted) and "rating tables all in Databricks" (real: in-repo JSON sidecars) — `ARCHITECTURE.md:49,794-795`.
- **Phantom APIs**: `haute.testing.PipelineTestCase` / `haute test` (`ARCHITECTURE.md:728-741`), `haute rollback` (`DEPLOY_DESIGN.md:464-469`), `haute export` referenced by `haute train --help` — all unimplemented.
- **GLM family list** diverges from the validator (lists rejected `negbinomial`/`quasipoisson`, omits supported `inverse_gaussian`) — `GLM_INTEGRATION_DESIGN.md:119-121`.

A cheap recurrence guard recommended by the report: a test asserting `documented node count == len(NodeType)`.

### C6. Frontend accessibility (the trace entry point is keyboard-dead)

The a11y lens found the **core "click a cell to trace" interaction is keyboard- and screen-reader-inaccessible** — plain `<td>` with `onClick`, no `role`/`tabIndex`/`onKeyDown`, no alternative affordance (`DataPreview.tsx:139-168,221-234,340`). That is the highest-priority a11y item. Grouped with it:

- **~138 of ~146 form controls have no programmatic label** (visual-only `<label>`, no `htmlFor`/`aria-label`) — fix once by making `EditorLabel` require `htmlFor` + `useId()` (`panels/editors/*`, `panels/modelling/*`).
- **NodeSearch (Ctrl+K) has no focus trap and never restores focus** + invalid listbox/option ARIA (`NodeSearch.tsx:148-271,188-257`) — route through the existing `ModalShell`.
- **WebSocket disconnect dies behind a 2px colour-only dot** with no banner/recovery and no `aria-live` (`Toolbar.tsx:90-94`; `App.tsx:533-539` syncBanner missing `role="alert"`).
- **Zero `role="progressbar"` in the whole app**; loading/preview/trace status never announced (no live regions) — `TrainingProgress.tsx:23-28`, `DataPreview.tsx:276-286`.
- **Bundle hygiene**: five heavy preview/trace panels are eagerly imported into the entry chunk despite conditional render (`App.tsx:23-28`); code-split them and add their prefixes to the bundle-budget lazy-only allowlist so re-eager-ing fails the build.

---

**Suggested P1 sequencing.** (1) Land the **B1 decompositions** for `_expression_parser`, `_optimiser_service`, `_execute_lazy` — they unblock A1/A5/A8 and must precede them. (2) Ship **A3+C1** as one workstream (the portability cluster + registry invariant + differential-values harness), then un-soften the README. (3) Ship **A2 + the Float32 test inversions** together. (4) Sweep the **C2 enum** family and **B2 dedups** (they reduce the surface for A6/A8). (5) Batch the mechanical sims, **C5 docs**, and **C6 a11y** as independent low-coupling PRs. All bug fixes follow the repo TDD mandate: failing test first (inverting the C4 codifying tests where they exist), then the fix.

Source indexes: `review/MASTER/INDEX.md` (MEDIUM §), `review/05-dimensions/DIMENSIONS-REPORT.md` (lenses), `review/MASTER/all-verified.json` (`severity=="sim"`).

---

## P2 — Long Tail & Accept-Risk

This section governs the **413 low-severity findings** (the residual after P0 critical/high and P1 medium remediation). These are real but individually minor: dead branches, orphaned helpers, renderer edge cases, micro-perf, stringly-typed enums, display-only drift, and dependency-advisory bumps. **None block a release.** The governing principle is *do not spend a dedicated dev/reviewer pair on any single low* — burn them down **by file, as a side-effect of higher-severity work**, and explicitly **defer the accept-risk classes indefinitely**.

### P2.0 The one number that drives the strategy

| Metric | Value | Implication |
|---:|---|---|
| Total lows | 413 | Do **not** enumerate or ticket individually |
| Lows inside the top-21 cross-phase hotspot files | **120 (29%)** | Fixed *for free* when P0/P1 opens those files |
| Pure dead/unreachable/orphan cleanup | ~66 | Mechanical, batch-deletable, no dev/reviewer pair |
| Display/UX/docstring/wording | ~48 | Mostly accept-risk or trivial-batch |
| Trace-evaluator divergences (`_expression_parser.py`) | 16 | Single largest **accept-as-risk** cluster (display path) |
| Dependency advisories (`P4-dependencies` lows) | 17 | Automate via Dependabot/renovate; not hand-work |

The headline: **~30% of the tail evaporates with zero incremental cost** if lows are addressed file-by-file alongside P0/P1, and a further large slice is legitimately WONTFIX. The dedicated effort required for the tail is far smaller than 413 suggests.

---

### P2.1 Batching strategy — fix by file-hotspot, never as a flat backlog

Per `CLAUDE.md`, every code-change item normally gets a developer + reviewer pair. Applying that to 413 individual lows would be absurd and is explicitly **out of scope** (consistent with the memory directive *"batch review for mechanical items; no re-review of verbatim fix-ups"*). Instead, lows are **co-located with the file** and ride along on the higher-severity remediation that already opens that file.

**Rule: a file is "opened" exactly once.** When any P0/P1/P2-medium item touches a hotspot file, the assigned dev clears *all* lows in that same file in the same branch, and the single reviewer for that file covers them in one batch pass.

The remediation order below is sorted by **lows-resolved-per-file-open**, citing `review/MASTER/INDEX.md` hotspot counts:

| Batch | File | Lows | Higher-sev work that already opens it | Dominant low theme |
|---:|---|---:|---|---|
| B1 | `src/haute/_expression_parser.py` | 16 | P0/P1 numerical + perf (re-parse, Kleene logic) | Trace-evaluator divergences — **mostly accept-risk, see P2.3** |
| B2 | `src/haute/routes/_optimiser_service.py` | 13 | P1 frontier/auto-range correctness | Dead branches + orphaned auto-range helpers |
| B3 | `src/haute/_sandbox.py` | 10 | **P0 RCE allowlist fix** (critical) | Unreachable allowlist entries, false docstrings, case-sensitivity |
| B4 | `src/haute/projection.py` | 9 | P1 demand-walk correctness | No-op guards, redundant set/parents rebuilds |
| B5 | `src/haute/schemas.py` | 8 | P1 wire-contract typing | Stringly-typed enums, unconstrained `int`, cross-field invariants |
| B6 | `src/haute/routes/_train_service.py` | 7 | P1 bool-coercion + admission bugs | `row_limit:true`→1-row, false `_check_gpu_fallback` docstring |
| B7 | `src/haute/chunking.py` | 6 | P1 byte-budget sizing | Per-chunk schema micro-perf |
| B8 | `src/haute/_types.py` | 6 | P1 graph-model invariants | camelCase/snake_case, naming drift |
| B9 | `src/haute/_execute_lazy.py` | 6 | P1 multi-port preview | Falsy-`source` handled 3 ways, contract caching |
| B10 | `src/haute/_databricks_io.py` | 5 | P0/P1 SELECT-clause exfiltration | Validator hardening tail |
| B11 | `src/haute/executor.py` / `_parser_regex.py` / `codegen.py` / `_trace_enrichment.py` | 4 each | P1 subsystem work in each | Mixed cleanup |

**Frontend mini-batches** (`BandingEditor.tsx` 5, `trace/CalculationHero.tsx` 4, `useNodeResultsStore.ts` 4, `GroupedColumnsTab.tsx`/`SchemaTableCard.tsx`/`useKeyboardShortcuts.ts` 3 each) follow the same rule against `frontend/src/App.tsx` and the panels hotspots in `INDEX.md`.

**The 26 lows in the `(other)` bucket** (singletons in cold files not on any hotspot list) are the *only* lows that justify a standalone sweep — and even then as **one batched "cold-file cleanup" PR**, not 26 tickets.

---

### P2.2 Categorisation with rough counts

The 413 lows fall into three behavioural categories plus four cross-cutting sub-classes that each warrant a *uniform* fix-strategy rather than per-item handling.

#### (a) Pure-cleanup / dead code — **~66 records**
Deletion-only, regression-risk near zero. Concentrated in `_optimiser_service.py`, `_sandbox.py`, `projection.py`, `_expression_parser.py`. Representative:
- `routes/_optimiser_service.py` — dead `reason == "cancelled"` branch in `_coerce_stopped_terminal_reason` (ternary always returns `"superseded"`); orphaned `_build_streaming_auto_range_chain_functions` / `_streaming_scenario_steps` helpers; `if c in available_cols` filter always-true given the preceding guard.
- `projection.py` — `_raise_if_unbounded_user_code_is_terminal` is a no-op whose name promises a guard; unreachable `if outputs is None` true-branch in SCENARIO_EXPANDER merge.
- `_sandbox.py` — allowlist entries `('builtins','True'/'False'/'None')` unreachable (pickle never emits them); byte-identical subclass override of `_eval_when_from_then_or_otherwise` in `_expression_parser.py:2131`.
- `_expression_parser.py:776-789` — `_build_symbol_table` / `_resolve_list_variable` dead (superseded code path).

**Strategy:** batch-delete per file under B1–B11; reviewer confirms no caller. These *raise* coverage (dead-code removal) and align with the memory directive *"raise coverage… or delete dead code; never lower the gate."*

#### (b) Minor-correctness — **~219 records (the bulk; 133 of them P3-low)**
Real behaviour, but bounded blast-radius (one editor, one display path, an edge input, or a 4xx-vs-500 status). These ride along with the file's higher-severity fix. Representative:
- `routes/_train_service.py` — `row_limit:true` (bool JSON) accepted as numeric → downsamples training to **1 row**; malformed list config returns HTTP 500 instead of 4xx.
- `cli/_init_cmd.py` — `parsed.get('project',{}).get('dependencies',[])` raises `AttributeError` when `[[project]]` is an array; `--force` re-init leaves stale CI files from a previous provider.
- `cli/_impact.py` — `GITHUB_STEP_SUMMARY` opened without `encoding='utf-8'` (UnicodeEncodeError); `--sample` accepts negatives and treats them as "all".
- `schemas.py` — `_normalise_frontier_range_pair` accepts booleans as range bounds (`[False,True]`→`(0.0,1.0)`).

**Strategy:** These are the lows that *most* benefit from co-location — fix them while the dev already has the file's invariants paged in. Where a true behaviour change exists (e.g. `row_limit:true`), follow the project TDD mandate: failing test first, then fix.

#### (c) Display / UX / a11y / wording — **~48 records (incl. 7 `P4-frontend-quality` a11y lows)**
The user-visible-but-non-numeric tail. Split sharply:
- **Trivial-batch:** `_constant` not escaping embedded double-quotes (malformed formula text); sliced subscripts rendering as raw `ast.dump` garbage; panel close-button `aria-label` gaps.
- **Accept-risk** (see P2.3): evaluator value-laundering that the real engine already reconciles.

#### Cross-cutting sub-classes (handled by *one* policy each, not per-item)
| Sub-class | Count | Uniform policy |
|---|---:|---|
| `P4-dependencies` advisories (requests, flask, pygments, pytest, @babel/core, …) | **17** | **Automate.** Enable Dependabot/renovate grouped PRs; do not hand-triage. One-time `pip-audit`/`npm audit` gate in CI. |
| `P4-tests` coverage-gaps at low sev | **18** | Fold into the **owning subsystem's** test work; never a standalone "add tests" sprint. |
| `P4-numerical` evaluator divergences | **17** | Almost entirely the B1 trace-evaluator cluster → **accept-risk** (P2.3). |
| `P4-performance` micro-perf | **16** | **WONTFIX unless on a hot path with a perf budget.** Most are flagged "minor — NOT worth fixing" by the audit itself (e.g. `_expression_parser.py:1621` dispatch-dict rebuild). |
| `P4-types` stringly-typed enums at low sev | **13** | Ride along with the file's typing work in B5/B8; cosmetic in isolation. |

---

### P2.3 Accept-as-risk / WONTFIX classes (defer indefinitely)

These are **defensible to never fix**. They are catalogued here so the deferral is a *decision of record*, not an oversight — and re-classified out only if a user actually reports them.

#### AR-1 — Trace-evaluator divergence reconciled by the real engine *(largest class; ~16–25 records, centred on `_expression_parser.py`)*
The expression evaluator in `src/haute/_expression_parser.py` is a **display/explanation path**, not the pricing path. The number a customer is *charged* is computed by Polars in the executor; the evaluator only renders "how it was calculated." Lows here:
- `:1647-1648` Unary `~` does Python bitwise-not on booleans vs Polars logical-not;
- `:1651-1670` `_compare` treats `is/is not/in/not in` as passing;
- `:1990-2017` `all_horizontal`/`any_horizontal` unimplemented;
- `:1765-1772` `round()` differs at binary-non-representable midpoints;
- `:725-750` f-string format-specs/conversions dropped.

**Why defer:** the waterfall/contract reconciles the displayed lineage against the engine result, and the higher-severity P1 finding *"evaluator wraps everything in `except Exception` and falls back to the raw row value"* (`_expression_parser.py:993-1003`) is the **root cause** that P0/P1 already addresses. Once the broad-except masking is fixed at the root, these individual operator gaps become *visible* (fail-loud) rather than silently wrong — at which point most are cheap follow-ups, not pre-release blockers. **Defer until the root masking fix lands; re-triage the residue then.**

#### AR-2 — Unreachable-precondition-only branches
Findings whose trigger condition is provably unreachable through the real guards, e.g. `_compute_scenario_value_stats` `if n else 0.0` fallbacks (`n` is always ≥1 by construction), the `('builtins','True')` pickle allowlist entries, `solver_cols` `if c in available_cols` (always true after the missing-columns guard). **WONTFIX as behaviour**; fold into AR/dead-code deletion *only* when the file is already open (P2.1) — deleting them is cleanup, not a fix.

#### AR-3 — Display-only casing / naming drift the contract already bridges
`outputColumn` vs `output_column`, `mult_col`/`optimal_scenario_value`/`optimised_factor`, `quote_count` vs `n_quotes`, `func_name`/`label`/`node_name`. These are *internal vocabulary* inconsistencies reconciled at the serialisation boundary; no user-facing miscalculation. **Defer**; the durable fix is the single `GLOSSARY.md` + enum-isation already tracked as **medium** P4-apidx/P4-types items — do not duplicate effort at low severity.

#### AR-4 — Cosmetic renderer output on malformed-but-rare AST
Sliced-subscript `ast.dump` garbage, unescaped quotes in rendered formulae, raw `float()` error text from `_normalise_frontier_range_pair`. **Trivial-batch if the file is open; otherwise WONTFIX** — they degrade a debug display on inputs users rarely author.

#### AR-5 — Micro-perf the audit itself rates not-worth-fixing
The 16 `P4-performance` lows that are O(1)-per-call dispatch-dict rebuilds and set re-materialisations off any measured hot path. **WONTFIX absent a failing perf budget** (note: the repo has no committed perf baseline per the high-severity CI findings, so there is currently no gate that would even detect these).

#### AR-6 — Dependency advisories with no reachable exploit
The 17 `P4-dependencies` lows are advisory-driven version bumps (requests, flask, pygments, pytest, @babel/core, etc.), not demonstrated exploits in `haute`'s usage. **Do not hand-fix** — delegate to automated grouped dependency PRs; accept the current pins until the next grouped bump.

---

### P2.4 How to burn down the tail efficiently — recommendation

1. **Never ticket a low individually.** Track the tail as *file-scoped checklists* attached to the B1–B11 hotspot branches. The unit of work is the *file*, not the finding.
2. **Sequence the tail to P0/P1, not separately.** Because 120/413 lows live in the top-21 hotspot files, the moment a critical/high opens `_sandbox.py`, `_expression_parser.py`, `routes/_optimiser_service.py`, `projection.py`, `schemas.py`, etc., the assigned dev clears that file's *entire* low list in the same branch, single batched reviewer pass (consistent with the *"batch review for mechanical items"* memory directive). This is the **only** efficient path and it costs ~zero incremental review budget.
3. **Delete-first.** Land the ~66 dead/unreachable/orphan lows as deletion-only commits early — they shrink the surface, *raise* coverage, and reduce reviewer load for everything after. Pure win, no behaviour risk.
4. **Automate the two mechanical sub-classes out of human hands:** dependency advisories (17 → Dependabot/renovate grouped PRs + a `pip-audit`/`npm audit` CI gate) and the low-sev type-enum hardening (13 → ride along with B5/B8 typing PRs).
5. **Formally accept AR-1…AR-6 now.** Record the WONTFIX decision in the program so these ~80–100 records stop appearing as "open work." Re-open *only* on a real user report or once the AR-1 root-cause (broad-except masking at `_expression_parser.py:993-1003`) is fixed, which converts the trace-evaluator cluster from silent-wrong to fail-loud and makes the residue cheap.
6. **Cold `(other)` singletons:** one final batched "cold-file cleanup" PR after B1–B11 — the only standalone tail effort, and small.

**Net effect:** of 413 lows, ~120 are absorbed free by hotspot work, ~66 are delete-on-sight, ~30 are automated away (deps + enums), and ~80–100 are formally deferred (AR-1…AR-6). The genuinely-standalone residual is the ~26-item cold-file batch — a single afternoon, not a program. **The tail should never gate a release and should never consume a dedicated dev/reviewer pair.**

---

## Appendix — where the detail lives

| Phase | Report | Findings |
|---|---|---|
| Map | `review/00-map/architecture.md` | system map + risk heat-map |
| P1 bugs | `review/02-findings/catalog.md` + `repro/` | 69 verified |
| P2 | `review/03-simplification/{simplifications,new-bugs}.md` | 38 sound sims + 24 bugs |
| Roadmap | `review/ARCHITECTURE-ROADMAP.md`, `REMEDIATION-PLAN.md` | 5 structural changes, 20 clusters |
| P3 exhaustive | `review/04-exhaustive/{FINAL-REPORT,VERIFIED-BUGS}.md` + `repro/` | 95 hi/med + 229 low + coverage ledger |
| P4 dimensions | `review/05-dimensions/DIMENSIONS-REPORT.md` | 371 across 10 lenses |
| Index | `review/MASTER/INDEX.md`, `all-verified.json` | all 881, normalised |
