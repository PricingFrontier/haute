The ownership split is broadly right, but the plan needs changes to storage identity, validation, export attribution and sequencing. I inspected the source, saved profiles and ZIP members; I ran no training, builds or tests and changed no files.

The decisive evidence checks are:

| Claim | Assessment |
|---|---|
| Duplicated ancestor snapshots | **Confirmed.** The bundle is 476.59 MB; 24 result members account for 411.96 MB. Repeated snapshots with the same revision were identical in this sample. Removing every nonselected snapshot from each result gives approximately **245 MB**, rather than the reported 265 MB; document the exact transformation used for that estimate. |
| `full_model` rebuilds | **Confirmed, savings overstated slightly.** Pruning has 55 calls taking 10.70 profiled seconds. Ten calls, taking 1.88 seconds, construct actual pruning results; A1 targets the other **45 calls / 8.82 seconds**. |
| Repeated `data_digest` | **Confirmed:** 45 / 31 / 27 calls, taking 5.88 / 3.83 / 3.28 seconds. These repeatedly serialize and hash prepared data. |
| Duplicated `full.json` | **Confirmed byte-for-byte:** both members contain 21,657,967 bytes. Each occupies about **1.79 MB compressed**, so distinguish processing savings from checkpoint disk savings. |
| Manifest cap | **Confirmed, with another missing risk.** The manifest is 16.55 MB against 32 MiB. Individual path results also have a **32 MiB read limit**; the largest saved result is already 26.51 MB. [_stage_paths.py:463](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:463) |
| Per-point model export | **Misattributed.** Of `_save_artifacts`’ 5.35 / 3.47 / 2.64 seconds, **3.56 / 2.48 / 1.87 seconds are `StageRuntime.remember`**, including digest and selection work already targeted by A1/A2. Actual model saves take 1.77 / 0.98 / 0.77 seconds, and themselves include digest work. D1 cannot claim another 11.4 seconds. [_training_job.py:3425](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:3425) |

Two further evidence corrections matter. The saved estimator has **`n_jobs=32`**; explicit `n_jobs` overrides `_default_fit_pool`, so this run’s utilization cannot be explained by a 12-thread cap. Also, the handoff gaps are already inside the measured phase windows: they are not another 21 seconds to add to the workflow. [_sklearn.py:3319](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:3319)

My recommendations, ranked:

1. **High — C: normalize diagnostics, but define their identity correctly.**

   Haute owns this. However, a t-boost stage revision hashes model state and policy, **not Haute’s evaluation context or diagnostic payload**. Haute’s durable revisions are UUIDs. Neither automatically makes diagnostics “content-addressed by stage revision.” Use a content digest for the stored snapshot and retain explicit model revision, evaluation identity and diagnostic-schema provenance. [_staged.py:235](/home/ralph/suite/t-boost/python/t_boost/_staged.py:235), [_staged_runtime.py:570](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:570)

   Extend the existing checked result/view descriptors and hydration mechanism. Remove duplication between the top-level selected diagnostics and its own snapshot too. Compact reads should load compact representations directly; hydrating dense ancestors and then discarding their tables preserves much of the read cost. [_stage_store.py:262](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:262)

   Specify shared-object retention before implementation: existing cleanup leases whole revisions, so snapshots shared across revisions need reachability-aware cleanup. Test differing evaluation contexts for the same model, compact/full parity, missing/corrupt references, restart, failed publication, checkpoint restore and concurrent cleanup.

2. **High — B1/B2 and compatibility: separate training identity from archive encoding.**

   Deduplication and categorical encoding belong in t-boost. But `data_digest` currently hashes physical `.npy`/`categories.json` member records. Changing categories to codes changes that digest, which Haute persists and candidate loading checks. Caching the old digest while saving a differently encoded checkpoint would create inconsistent identities. [_checkpoint.py:81](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:81), [_staged_runtime.py:210](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:210)

   Recommend **v2 writing plus a narrow v1 reader**, both normalized into one internal representation. The current specs explicitly promise compatible checkpoint reads; untracked files do not negate that contract. Preserve logical data identity across encoding changes, or explicitly version and migrate every dependent identity. Ordinary inference compatibility remains separate. [_staged-training.md:197](/home/ralph/suite/t-boost/specs/roadmap/staged-training.md:197)

   Test real v1 fixtures, v1→v2 save/reload, retained candidate application, expected-digest checks, reordered rows, invalid category codes, deterministic dictionaries and atomic failures. Keep working banks and repaired inference banks distinct.

3. **High — D3: replace repeated validation, not read-boundary validation.**

   “Validate once at write time” is insufficient for persisted input. Checksums establish byte integrity, not semantic validity. Preserve strict parsing on load, including overflow such as `1e999`, and schema/link validation.

   Haute already has finite-float parsing in the stage store. Reuse that approach and avoid the subsequent recursive walk of an already validated immutable representation; preferably serve a persisted compact point view. [_stage_store.py:40](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:40), [_training_worker.py:639](/home/ralph/suite/haute/src/haute/routes/_training_worker.py:639)

   Test NaN, infinity, overflow, malformed references and semantically invalid payloads whose checksums have been recomputed.

4. **Medium — A1–A3: retain independent public models and make A3 concrete.**

   A1 is appropriately placed. A detached parameter-only estimator reuses existing selection logic; extracting shared parameterized helpers is also reasonable if it simplifies both ordinary and staged fitting.

   A2 is sound **provided immutability is enforced**. `frozen=True` does not freeze NumPy buffers, lists or dictionaries. Own and protect the cached state, share it across forks, and seed the digest only after complete load validation. Test caller mutation, fork reconfiguration and post-fit operations against predictions and digest stability. [_staged.py:21](/home/ralph/suite/t-boost/python/t_boost/_staged.py:21)

   For A3, investigate a reusable immutable inference snapshot with independent estimator metadata. The binding already stores table models behind `Arc`; binary templates are another existing mechanism. Parsing a document once is only a partial improvement when native JSON is parsed again. `fork()` itself already avoids deserialization. [_lib.rs:5820](/home/ralph/suite/t-boost/crates/t-boost-py/src/lib.rs:5820), [_sklearn.py:1296](/home/ralph/suite/t-boost/python/t_boost/sklearn.py:1296)

5. **Medium — Missing before B3: eliminate redundant checkpoint round trips.**

   `_pruning_budgets` saves each configured parent, then `run_path_job` saves it again after unpublished family preparation. Pruning’s profile contains **two checkpoint saves totaling 7.84 seconds**. Replacements also invalidate the session cache and cause reloads. Consolidate parent preparation and persistence so each required state is saved once, preserving bounded memory and failure atomicity. [_stage_paths.py:190](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:190), [_stage_paths.py:250](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:250)

   Then benchmark compression. B3 is correctly in t-boost, but the reported 22 seconds also includes candidate ZIP compression, not just checkpoints. Compression-level changes do not inherently require v2. Test resumed predictions, stable repeated saves and failures between retained fits.

6. **Medium — D1: separate evaluation from publication; lower its priority.**

   Haute owns this separation. Preserve the canonical evaluator and move stage capture out of `_save_artifacts`; do not introduce a second scoring path or merely skip a method with required side effects. Equivalent outcomes may share diagnostics, but each alias must retain its own policy, recipe and revision. [_stage_paths.py:134](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:134)

   Consider the larger option: exploration paths publish diagnostics and resumable state, with inference exports created only during finalisation. That fits the existing finalisation boundary, although worker artifact contracts would need an explicit change. [_staged_runtime.py:691](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:691)

   Test nondefault point selection, aliases, identity/rejected points, feature contracts and exported predictions. Re-estimate savings after A1/A2.

7. **High — D2: use an explicit staged completion receipt.**

   Recommend a typed completion payload containing job/operation and committed revision identity, while keeping progress, terminal diagnostics and continuation information. Fetch the compact durable stage state through the existing endpoint.

   Simply omitting `result` breaks current completion handling: `trainOnComplete` returns immediately, and `getResult` also requires it. Update polling, job removal, document fences, notifications and generated contracts together. Completion must follow durable publication. [_useBackgroundJobs.ts:185](/home/ralph/suite/haute/frontend/src/hooks/useBackgroundJobs.ts:185)

   Test ordinary training unchanged, automatic continuation, reopen, stale completion after document switching, cancellation, publication failure and final export receipts.

8. **Medium — B2/E: reuse categorical abstractions and shared native preparation before adding parallelism.**

   Moving `_cat_level` wholesale into the pure Rust core is the wrong boundary: pandas/NumPy missing markers and Python scalar stringification belong to the ingest adapter. t-boost already has `_polars_cat_codes`. Extend that representation consistently through training and checkpoints instead of adding an unrelated encoder. Test missing values, literal sentinel strings, Unicode, numeric categoricals, unknown levels and cross-process dictionary determinism. [_ingest.py:134](/home/ralph/suite/t-boost/python/t_boost/_ingest.py:134)

   For pruning, consider a native family operation before parallel budgets: every call currently copies vectors, bins rows and creates a thread pool. Banding already demonstrates shared family preparation. These costs are included in the profiled native-method time. [_lib.rs:5450](/home/ralph/suite/t-boost/crates/t-boost-py/src/lib.rs:5450)

   Keep Haute evaluation parallelism last. Require repeated/reordered-budget equivalence, byte-identical results across thread counts, bounded memory and cancellation.

9. **Medium — D4 and verification: measure handoff first and withdraw additive savings estimates.**

   Put phase-boundary instrumentation before optimization. Saved request profiles already show Apply’s unpack-plus-pack taking roughly **1.0–2.1 seconds**, with substantial additional copying, validation and response work. They do not establish that bundle copying dominates parent-side handoff.

   The 60–80-second forecast double-counts overlapping costs, especially D1 against A1/A2. Treat it as an unverified target. Final Gini/deviance equality is too weak: compare selected predictions, reports, recipes, candidate statuses, inference bytes where required, restart behavior and frontend contracts. Include multi-fit evaluation, not only this single-holdout fixture.

For the remaining open question: **store GBM `loss_history` once on the full-fit snapshot**, with explicit references or compatibility projection for existing consumers. The learning-curve UI already selects Full GBM. Preserve separate histories for distinct retained fits and final refits; do not collapse them into one global history. Test the curve through selection, Apply, restore and finalisation. [_ModellingView.tsx:733](/home/ralph/suite/haute/frontend/src/modelling/ModellingView.tsx:733)

A larger Haute option deserves an explicit design comparison: immutable shared bundle members with small revision manifests, making Apply a pointer publication and assembling portable `.hstage` archives only when needed. This removes repeated whole-archive copying, but requires proper shared-object leases and cleanup; it should be a deliberate storage design, not another cache.

PLAN: SOUND WITH CHANGES