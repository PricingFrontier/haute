# Opus 5 review — remaining items and implementation plan

Source: `docs/opus-5-review.md` (461 findings) as remediated by the 14 workstream PRs
(#134–#147, branches `ws-01`–`ws-14`), all merged to `main` at `3af79b5b` on 26-Jul-2026.

**Audit method (26-Jul-2026 evening):** every one of the 461 findings was verified against
the merged tree by a two-stage agent workflow — a per-component verify sweep (33 batches),
then an independent adversarial re-check of every open/partial/low-confidence verdict and
every critical/high "fixed" claim (94 re-checks). One finding the sweep dropped
(`deploy-4`) was verified directly at root. Medium/low "fixed" verdicts carry one
evidence-cited pass; every verdict below survived the adversarial pass.

## Verdict summary

| Severity | Total | Fixed | Partial | Open | Deferred |
|---|---:|---:|---:|---:|---:|
| Critical | 3 | 3 | 0 | 0 | 0 |
| High | 67 | 64 | 2 | 0 | 1 |
| Medium | 200 | 193 | 3 | 2 | 2 |
| Low | 191 | 177 | 0 | 11 | 3 |
| **All** | **461** | **437** | **5** | **13** | **6** |

- **Partial** — part of the finding shipped; a concrete remainder is listed below.
- **Open** — no fix and no recorded deferral (several survive only as ratchet-baseline rows).
- **Deferred** — an explicit recorded decision exists (spec `> NOTE:`, retention sentence,
  or PR-declared cross-stream deferral); actioned only if the decision changes.

Every critical and every Wave-1 data-loss / Wave-2 security finding from the review is
**fixed**. Both partial highs have only documentation remainders. The single most
consequential code gap below is `json-shredding-4` (a planned cross-stream hunk that never
landed).

---

## Wave A — code correctness (one PR)

### A1. `json-shredding-4` (M, partial) — blank OUTPUT mapping rows still reach projection

The `_node_apply.py` consumer was fixed, but the coordinated WS-03/WS-04 hunk in
projection was never landed. `src/haute/projection.py:2828` still filters with
`e.get("enabled", True)`, so an enabled-but-incomplete editor row propagates `""` as an
upstream column demand (the `missing=['']` failure class).

- Replace the filter with `is_active_mapping_entry(e)` (local import from
  `haute._output_assembler`, matching `src/haute/_node_apply.py:359`).
- Regression test: terminal OUTPUT node with an enabled blank-`source_column` row yields a
  projection demand without `""` and no `missing=['']` contract failure.

### A2. `mlflow-model-registry-7` (L, open) — cascade-under-lock asymmetry

`_ModelCacheWithCascade.put` (`src/haute/_mlflow_io.py:160-175`) and `clear` (:177-182)
run the feature-validation invalidation cascade **inside** `self._lock`, while
`evict_matching` (:184-208) deliberately runs it outside to avoid deadlock.

- Collect evicted values under the lock; invoke the cascade after release, in all three
  methods. Update `docs/specs/mlflow-model-registry/low-level.md:31-39` to state one rule.

### A3. `mlflow-model-registry-8` (L, open) — cwd-derived cache root computed at 3 sites

`Path.cwd() / ".cache" / "models"` is derived independently at `_mlflow_io.py:732`, `:823`
and `:1019`.

- Introduce one accessor (`_disk_cache_root()`), use it at all three sites; add a spec
  sentence (high-level.md:95 area) that the root is cwd-resolved through that helper.

### A4. `mlflow-model-registry-10` (L, open) — dead ModelScorer delegates

`ModelScorer._score_eager` / `_score_batched` (`src/haute/_model_scorer.py:1424-1460`)
have no production caller (production path calls `_run_score_pipeline`); only
`tests/test_model_scorer.py:1342,:1363` exercise the bound methods.

- Delete both delegates and retarget those tests at the module-level helpers they wrap
  (`haute._mlflow_io._score_eager`, `_score_batched_standalone`).

### A5. `over-complication-9` (L, open) — two digest sites bypass `canonical_json`

`_cache.py:935` declares `canonical_json` "THE canonical-JSON encoding for digest
material", but `routes/pipeline.py:295-298` (`_trace_row_values_fingerprint`) and
`modelling/_feature_contract.py:89-91` (`_hash_payload`) hash raw
`json.dumps(sort_keys=True)`.

- **Recommended split:** route the trace-row fingerprint (in-process cache key only)
  through `canonical_json`; for the feature-contract hash, changing the encoder changes
  persisted hash values — either migrate deliberately or add an explicit documented
  exemption in `canonical_json`'s docstring and the caching spec. Add a regression test
  that no digest call site outside `_cache.py` uses raw `json.dumps` for digest material.

### A6. `modelling-3` (L, open) — dispersion estimate: spec says "never written
automatically", UI writes it

`GLMTargetConfig.tsx:88-95` auto-fills the estimate via `onUpdate` with no accept step;
the spec (`docs/specs/modelling/high-level.md:99-100`) promises the opposite.

- **Recommended:** rewrite the spec sentence to match the shipped auto-fill-into-editable-
  field behaviour (option a). If the explicit accept step is actually wanted, instead hold
  the estimate in local state behind an accept control plus a test (option b) — decide
  before implementation.

**Wave A verification:** smallest failing regression test first per fix (AGENTS.md);
`uv run pytest tests/test_v2_codec_and_shred.py tests/test_output_assembler.py
tests/test_mlflow_io.py tests/test_model_scorer.py tests/test_docs_accuracy.py -q`;
quick preflight; Codex code review before merge.

---

## Wave B — spec truth (one PR, docs + docstrings only)

### B1. `failure-model-1` (H, partial) — stale "data fetching" wording

Spec rewrite complete; two docstrings remain: `src/haute/server.py:6` and
`src/haute/routes/databricks.py:1` still say the browse-only Databricks routes do "data
fetching". Reword to browsing-only.

### B2. `seam-exec-1` (H, partial) — execution-engine spec still documents the superseded
preview key

Caching and tracing halves are folded; `docs/specs/execution-engine/` is untouched:

- Rewrite low-level.md:112-116 (preview control flow) around
  `execution.preview_lineage_cache_key()` → `_cache.lineage_cache_key()` with the shipped
  payload; delete the non-existent "preview-projection cache suffix".
- Add `preview_lineage_cache_key` + `PREVIEW_EXECUTION_SEMANTICS_VERSION` to the
  `execution.py` module-map row (low-level.md:8).
- Update high-level.md:259-266 ("graph-fingerprint helpers" → versioned lineage key).

### B3. `contracts-a-11` (M, partial) — overwrite-refusal mapping absent from failure models

- Add a `DataOutputDestinationExistsError` → `POST /api/pipeline/write-output` → 409 row
  to `docs/specs/server-api/low-level.md`'s Error handling table (:385-399).
- Add one sentence to `docs/specs/io-layer/high-level.md` Failure model (:101-111).

### B4. `deploy-10` (L, open) — container manifest path contract contradicts spec

Code emits `artifacts/<name>` resolved by `WORKDIR /app`; the spec claims the container
CWD is `/` and pipeline-relative paths were chosen to avoid exactly this.

- **Recommended:** document reality (option b): amend `docs/specs/deploy/high-level.md:175-179`,
  low-level.md near :133 and the Edge-cases bullet at :242-247 to state manifest artefact
  paths are container-relative, resolved against the image's `WORKDIR /app`, and that
  `_container.py:111` + the Dockerfile `WORKDIR`/`COPY` form one invariant.

### B5. `contracts-d-8` + `modelling-4` (L, open) — fold the shipped 0.8.0 contract

`## Approved change contract — 0.8.0 isolated fit and dispersion` survives at
`docs/specs/modelling/high-level.md:251-269` and `low-level.md:561-590` while describing
shipped code. Fold each bullet into the present-tense sections (training control flow,
publication/rollback invariants, progress budget, dispersion rules, failure model) and
delete both headings. Leave `— canonical-only modelling artifacts` (low-level.md:592)
alone — that migration is genuinely pending.

### B6. `mlflow-model-registry-5` (L, open) — retire the last `Polars backend contracts
(0.6.0)` sections

Six legacy sections remain corpus-wide (mlflow-model-registry, modelling, optimiser ×
high+low), all ratcheted at `tests/docs_accuracy_baseline.txt:195-200`. Fold the shipped
bullets (e.g. metadata-derived empty-batch dtype, `src/haute/_model_scorer.py:1565-1591`)
into ordinary sections, delete the headings, remove the six baseline rows.

### B7. `mlflow-model-registry-11` (L, open) — two uncited test files

Add Testing entries in `docs/specs/mlflow-model-registry/low-level.md` for
`tests/test_model_cache_observability.py` and `tests/test_feature_validation_cache.py`;
re-derive the "Known coverage gaps: none" closing sentence.

### B8. `testing-credibility-11` (L, open) — hard-coded test counts drifted again

`docs/specs/modelling/low-level.md` (:491-528) and `docs/specs/pipeline-config/low-level.md`
(:301,:306,:316) assert exact `(N tests)` counts that no longer match.

- **Recommended:** drop the exact-count parentheticals entirely, and add a
  `tests/test_docs_accuracy.py` guard that parses any surviving `` `test_*.py` (N tests) ``
  claim and fails on divergence — so counts can never silently drift again.

**Wave B verification:** `uv run pytest tests/test_docs_accuracy.py -q` (baseline rows
195-200 deleted in B6 must go green, not be re-added); `uv run mkdocs build --strict`;
quick preflight; Codex code review.

---

## Wave C — docs-accuracy ratchet burn-down (one or two PRs, mechanical)

The WS-01 ratchet (`tests/docs_accuracy_baseline.txt`) still holds **281 accepted
violations** (from 373 at commit). Two review findings are the finding-level view of this
debt; the rest is pre-existing corpus hygiene the review's S3 systemic finding predicted:

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

- **C1. `testing-credibility-9` (M, open):** rewrite the
  `docs/specs/frontend-shared/low-level.md` Testing section (~lines 370-425) so every
  reference is a full root-relative `frontend/src/...` path — this alone clears ~30 rows
  and disambiguates the `formatValue`/`formatBytes`/`ConfigCheckbox` basename class.
- **C2. `testing-credibility-8` (M, open):** cite each of the ~71 unreferenced
  `tests/test_*.py` files in its owning component's Testing section (or delete genuinely
  dead test files), burning the 65 `unreferenced-test` rows.
- **C3.** Sweep the remaining rows document-by-document (frontend-graph-canvas first),
  deleting each baseline row with its fix. Suitable for cheap-tier batch execution with a
  per-document diff review; the ratchet test itself enforces correctness.

**Wave C verification:** `uv run pytest tests/test_docs_accuracy.py -q` after each
document; baseline shrinks monotonically (no added rows); quick preflight at the end.

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

- The 14 `docs/opus-5-ws-NN-*.md` files still read "Owner: unassigned · Status: not
  started"; mark them delivered (with PR numbers) or delete them and let this file plus
  the review stand.
- This file is already excluded from the public site by the `opus-5-*.md` glob in
  `mkdocs.yml` `exclude_docs`.

## Suggested sequencing

1. **Wave A** (code, ~½ day) — one branch, regression-test-first per item; full preflight;
   Codex code review. A1 is the only behaviour-visible fix; A5/A6 carry small decisions to
   confirm at PR time (recommendations above).
2. **Wave B** (docs, ~½ day) — one branch; strict MkDocs + docs-accuracy green; Codex
   review of the folded contracts for fidelity to shipped behaviour.
3. **Wave C** (ratchet, ~1–2 days elapsed, mechanical) — chunk by document; cheap-tier
   batch execution is appropriate; the ratchet test is the gate. C1/C2 first (they close
   the two open M findings), then C3.
4. Deferred register: no work; keep the table in this file authoritative.

Waves A and B are independent and can proceed in parallel worktrees; Wave C touches only
specs/baseline and conflicts with nothing except B6's six baseline rows (land B first or
coordinate that one hunk).
