# Opus 5 review — remaining items and implementation plan

Source: `docs/opus-5-review.md` (461 findings) as remediated by the 14 workstream PRs
(#134–#147, branches `ws-01`–`ws-14`), all merged to `main` at `3af79b5b` on 26-Jul-2026.

**Audit method (26-Jul-2026 evening):** every one of the 461 findings was verified against
the merged tree by a two-stage agent workflow — a per-component verify sweep (33 batches),
then an independent adversarial re-check of every open/partial/low-confidence verdict and
every critical/high "fixed" claim (94 re-checks). One finding the sweep dropped
(`deploy-4`) was verified directly at root. Medium/low "fixed" verdicts carry one
evidence-cited pass; every verdict below survived the adversarial pass.

## Completion record (27-Jul-2026)

Every non-deferred item in this plan is implemented. Each of the six deferred findings
has an explicit retained decision and revisit trigger rather than an unrecorded action.
The completed work includes all Wave A code changes and regressions, every Wave B
specification correction, the complete Wave C documentation-reference cleanup, and
delivery metadata for all 14 workstreams.

The finished tree passed the repository's full verification ladder:

- targeted regressions and affected suites: 563 tests passed, followed by 387 passed
  and 1 skipped across the affected server, Explore, trace, and pipeline-route suites;
- documentation gates: 31 accuracy tests passed and `mkdocs build --strict` succeeded;
- static gates: Ruff checked and format-checked 591 files, and mypy reported no issues
  across 175 source files;
- quick preflight: all checks passed;
- full preflight: 13,724 backend tests passed (46 skipped, 1 expected failure), aggregate
  backend coverage was 91.90%, and all 30 critical Python coverage gates passed;
- packaging and frontend gates: package build, TypeScript typecheck, ESLint (0 errors),
  production build, bundle budget, and all 35 performance benchmarks passed; and
- frontend coverage: 278 test files and 5,456 tests passed, with all 22 critical frontend
  coverage gates passing.

## Verdict summary

| Severity | Total | Fixed | Partial | Open | Deferred |
|---|---:|---:|---:|---:|---:|
| Critical | 3 | 3 | 0 | 0 | 0 |
| High | 67 | 66 | 0 | 0 | 1 |
| Medium | 200 | 198 | 0 | 0 | 2 |
| Low | 191 | 188 | 0 | 0 | 3 |
| **All** | **461** | **455** | **0** | **0** | **6** |

- **Partial** — none remain.
- **Open** — none remain.
- **Deferred** — an explicit recorded decision exists (spec `> NOTE:`, retention sentence,
  or PR-declared cross-stream deferral); actioned only if the decision changes.

Every non-deferred finding is now **fixed**. The six deferred findings retain explicit
decisions and revisit triggers in the authoritative register below.

---

## Wave A — code correctness (completed)

### A1. `json-shredding-4` (M, resolved) — incomplete OUTPUT mappings are inactive

`src/haute/projection.py::compute_prepared_plan` now uses the shared
`is_active_mapping_entry` predicate for terminal OUTPUT mappings. The projection-planner
regression proves an enabled incomplete editor row cannot introduce `""` into node or
edge demands.

### A2. `mlflow-model-registry-7` (L, resolved) — callbacks run outside the cache lock

`_ModelCacheWithCascade.put`, `clear`, and `evict_matching` now mutate or collect state
under the model-cache lock and invoke feature-validation callbacks only after releasing
it. A regression observes the lock boundary for capacity eviction, predicate eviction,
and clear.

### A3. `mlflow-model-registry-8` (L, resolved) — one cwd-derived cache-root accessor

`_disk_cache_root()` is now the sole cwd-derived model-artifact cache-root accessor and is
used by artifact resolution, clear, and the native-model fast path. Both MLflow registry
specs document that invariant and the helper has direct cwd-relative coverage.

### A4. `mlflow-model-registry-10` (L, resolved) — dead ModelScorer delegates removed

The unused `ModelScorer._score_eager` and `_score_batched` delegates are deleted.
Their tests now exercise `haute._mlflow_io._score_eager` and
`_score_batched_standalone` directly.

### A5. `over-complication-9` (L, resolved) — digest identity is canonicalised

Transient JSON-shaped digest identities now use `canonical_json`, including trace-row,
graph-payload, Explore-report, and trace-enrichment identities. The persisted modelling
feature-contract hash retains its historical byte encoding as an explicit compatibility
exception in code and the caching spec. An AST regression rejects new raw
`json.dumps`-to-digest call sites outside that exemption.

### A6. `modelling-3` (L, resolved) — dispersion auto-fill contract matches the UI

The modelling high-level spec now matches the shipped interaction: the explicit estimate
action auto-fills the visible editable config field, which remains inspectable and
adjustable before the normal save/publish action.

**Wave A verification:** smallest failing regression test first per fix (AGENTS.md);
`uv run pytest tests/test_v2_codec_and_shred.py tests/test_output_assembler.py
tests/test_mlflow_io.py tests/test_model_scorer.py tests/test_docs_accuracy.py -q`;
quick preflight; Codex code review before merge.

---

## Wave B — spec truth (completed)

### B1. `failure-model-1` (H, resolved) — Databricks routes are documented as browse-only

The server route map and Databricks route docstring now describe the endpoints as
read-only Unity Catalog browsing, without implying data fetching.

### B2. `seam-exec-1` (H, resolved) — execution specs document the versioned lineage key

Both execution-engine specs now describe
`execution.preview_lineage_cache_key()` → `_cache.lineage_cache_key()`, the complete
versioned selected-lineage payload, and
`PREVIEW_EXECUTION_SEMANTICS_VERSION`. The obsolete preview-projection suffix wording is
removed and the module map names the shipped key factory and version.

### B3. `contracts-a-11` (M, resolved) — overwrite refusal is in both failure models

The server error table now maps `DataOutputDestinationExistsError` from
`POST /api/pipeline/write-output` to HTTP 409, and the I/O failure model documents the
same fail-loud overwrite refusal.

### B4. `deploy-10` (L, resolved) — container manifest paths match the runtime invariant

The deploy specs now state that bundled source paths become
`artifacts/<name>` manifest paths, resolve against image `WORKDIR /app`, and must change
together with the generated Dockerfile's `COPY artifacts/ artifacts/` instruction.

### B5. `contracts-d-8` + `modelling-4` (L, resolved) — shipped 0.8.0 contract folded

The shipped isolated-fit/dispersion contract is folded into present-tense training
control flow, progress transport, publication/rollback, dispersion, failure, and testing
sections. Both 0.8.0 headings are removed; the genuinely pending canonical-only
modelling-artifacts contract remains.

### B6. `mlflow-model-registry-5` (L, resolved) — legacy Polars contract sections retired

All six shipped Polars backend contract sections across MLflow registry, modelling, and
optimiser high/low specs are folded into ordinary present-tense sections. The legacy
headings and their baseline debt are gone.

### B7. `mlflow-model-registry-11` (L, resolved) — model-cache tests cited

The MLflow registry Testing section cites
`tests/test_model_cache_observability.py` and
`tests/test_feature_validation_cache.py`, and its closing coverage statement reflects
the direct regression evidence.

### B8. `testing-credibility-11` (L, resolved) — drifting hard-coded counts removed

The stale exact-count parentheticals are removed. A documentation-accuracy test parses
any future `` `test_*.py` (N tests) `` claim and compares it with the source-defined test
functions so count drift cannot silently recur.

**Wave B verification:** `uv run pytest tests/test_docs_accuracy.py -q` (baseline rows
195-200 deleted in B6 must go green, not be re-added); `uv run mkdocs build --strict`;
quick preflight; Codex code review.

---

## Wave C — docs-accuracy ratchet burn-down (completed)

The WS-01 ratchet (`tests/docs_accuracy_baseline.txt`) started this pass with **281
accepted violations** (from 373 at its original commit) and now holds **zero**. The
historical starting distribution was:

| Rule | Rows | Note |
|---|---:|---|
| `non-root-relative-test-reference` | 122 | test cited by bare filename, not `frontend/...`/`tests/...` |
| `unreferenced-test` | 65 | = `testing-credibility-8` (M, open): ~71 test files cited by no spec |
| `missing-module-map-symbol` | 49 | symbols named in prose missing from module-map rows |
| `ambiguous-repo-reference` | 19 | basename matches multiple repo files |
| `missing-test-reference` / `missing-repo-reference` | 12 | named file does not exist |
| `roadmap-evidence-missing-repo-reference` | 6 | explore-eda (4), frontend-canvas, modelling |
| `legacy-contract-section` | 6 | cleared by Wave B6 |
| `broken-link-anchor` | 2 | |

Concentration by document: `frontend-graph-canvas/low-level.md` (56),
`frontend-shared/low-level.md` (54), `frontend-git-ui/low-level.md` (23),
`assistant/low-level.md` (21), remainder spread thin.

- **C1. `testing-credibility-9` (M, resolved):** the frontend-shared Testing section
  uses full root-relative paths, including disambiguated duplicate basenames.
- **C2. `testing-credibility-8` (M, resolved):** every previously unreferenced Python
  test file is cited in an owning low-level Testing section.
- **C3 (resolved):** all remaining ambiguous/missing references, module-map symbols,
  roadmap evidence, legacy headings, and broken anchors were corrected
  document-by-document. The accepted-debt baseline is intentionally empty.

**Wave C verification:** `uv run pytest tests/test_docs_accuracy.py -q` passes with 31
tests and the live violation inventory at zero; `uv run mkdocs build --strict` passes.

---

## Deferred register — recorded decisions, no action unless revisited

| Finding | Sev | Decision on record | Revisit trigger |
|---|---|---|---|
| `io-layer-9` | H | Source-cache locks/leases are process-local; destructive startup sweep fixed (age-gated, generations never deleted). Cross-process retirement explicitly out of scope (WS-14/PR #143). | Running multiple server processes / CLI-during-server against one project becomes a supported workflow → add cross-process advisory locking (`msvcrt.locking`/`fcntl.flock`). |
| `seam-exec-4` | M | Preview stores target-only, trace materialises full lineage; documented as a known coverage gap at `docs/specs/tracing/low-level.md:628` (WS-11/PR #144). | Aligning cache scopes with the preview route/key-factory owners. |
| `frontend-preview-explore-4` | M | Diagnostic-unavailable vs schema-mismatch parsing both return `null` (`frontend/src/types/guards.ts:826-829`); left with frontend-shared owners (WS-10/PR #146). | Adding a distinct "diagnostic unavailable" rendered state. |
| `execution-engine-11` | L | `IsolatedJobSupervisor.launch()` / `run_isolated_worker` retained as tested legacy primitive per `docs/specs/background-jobs/low-level.md:94-97,:305-312`. | If retention is not wanted long-term, delete both plus their tests and the retention sentences. |
| `contracts-d-11` | L | Trace provenance fields not in `TraceResult`; `> NOTE:` at `docs/specs/tracing/high-level.md:210-214` forbids inferring them. | Adding provenance fields to the trace payload. |
| `over-complication-10` | L | Python keyword list triplicated (`sanitizeName.ts:30`, `apiInputPorts.ts:86`, test copy); dedup left with frontend-shared owners (WS-10/PR #146). | Any edit to the keyword lists → consolidate to one exported constant first. |

Also recorded, not per-finding: `over-complication-5`'s god-module remainder — the
5,150-line `src/haute/routes/_optimiser_service.py` carve is roadmap-planned as
`OPT-P11–OPT-P14` (`docs/roadmap/optimiser.md:15`, P2) with an `OptimiserFrontierService`
extraction plan; execute it from the roadmap, not from this list.

## Housekeeping (no severity)

- All 14 `docs/opus-5-ws-NN-*.md` files are marked delivered with their actual merged
  PR numbers: WS-01 #137, WS-02 #134, WS-03 #138, WS-04 #136, WS-05 #139,
  WS-06 #135, WS-07 #140, WS-08 #142, WS-09 #145, WS-10 #146, WS-11 #144,
  WS-12 #141, WS-13 #147, and WS-14 #143.
- This file is already excluded from the public site by the `opus-5-*.md` glob in
  `mkdocs.yml` `exclude_docs`.

## Completed sequence

1. [x] **Wave A** — code fixes and focused regressions.
2. [x] **Wave B** — shipped behavior folded into present-tense specifications.
3. [x] **Wave C** — documentation debt ratchet reduced monotonically to zero.
4. [x] **Housekeeping** — all workstream records marked delivered.
5. [x] **Deferred register** — retained unchanged as the authoritative decision record.
