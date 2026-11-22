# Staged-workflow performance plan — v2, after the Codex and Fable reviews (22 November 2026)

**Evidence:** [performance-audit-2026-11-22.md](performance-audit-2026-11-22.md), including
its review corrections.

**Reviews:** both returned `PLAN: SOUND WITH CHANGES`. They are stored outside the repository:
- Codex: thread `01a202dd-7337-7b12-9069-472ad7fc673a`; text in
  `…/scratchpad/codex-review/review.md`.
- Fable: condensed record in `…/scratchpad/fable-review/review.md`.

**Baseline:** the whole workflow takes ~291 s. The three automatic path jobs take 196 s of
that, at 1.4–2.0 of 32 cores. Most of that time is not modelling. It goes to:
- restoring t-boost models through full JSON round trips;
- writing and reading checkpoints again and again;
- copying duplicated diagnostics around a bundle of 0.2–0.5 GB.

**The user's brief:** take the cleanest approach. Put each change in the library that owns
the concern, keep it consistent with existing code, and use no band-aids.

## Decisions

- **Checkpoint compatibility (user):** retraining is fine. Bump the t-boost checkpoint
  format and the Haute `.hstage` format. Reject old files with a clear "rebuild the GBM"
  error, and keep no v1 reader.
  - Amend `t-boost/specs/roadmap/staged-training.md:201-202`, which currently says
    "checkpoint and inference JSON formats remain backward compatible".
  - The public inference JSON format does not change.
  - Codex recommended keeping a narrow v1 reader because of that spec promise. The user's
    decision overrides it, and the spec is amended to match.
- **Stage storage (user, 22 Nov):** use the shared-files design. Saved stages become
  immutable, content-addressed shared files plus a small manifest per revision. Apply is a
  pointer publication, and portable `.hstage` archives are assembled only for export.
  This needs reachability-aware cleanup. The single storage spec (steps 5 and 7) is
  written for this design.
- **GBM loss history (user):** store it once. Per the reviews, it belongs to each
  retained *fit*, not to a stage snapshot and not to one global copy. Haute holds it once
  per fit and hydrates it at the read boundary, so `stageResults.ts`'s
  `snapshot.loss_history` contract is unchanged.
- **Dropped:** "defer per-point model export" (old D1). Its cost is t-boost
  `pruning_selection` and `data_digest` called from `StageRuntime.remember`, which items
  1–2 remove. The real export is about 0.4–1.8 s.
- **Withdrawn:** the additive 60–80 s savings forecast, because it double-counted
  overlapping costs. Each step is measured instead.

## Ordered plan

### 0. Measure before changing (no product code)

- Instrument the phase boundaries of the ~4–7 s worker-exit → next-state gap: result
  commit, view, bundle publish, spawn and poll. Phase 4's design depends on where that
  time goes.
- Fix the audit harness default: `--table-budget 44` does not exist for this
  configuration.

**Step 0 result (22 Nov; `audit_server --phase-timing`, unprofiled).** In each automatic
handoff, the next worker job starts 3.96 s (GBM → pruning) or ~4 s (later stages) after the
previous worker job ends:
- ~0.5–0.7 s for the worker result handover;
- two `_assert_json_finite` walks, 0.8–1.3 s;
- `_validate_training_artifacts` re-hashing, 0.3–1.1 s, growing with the bundle;
- store prepare, 0.7–1.0 s;
- publish and write, 0.3–0.8 s;
- cleanup, ≤0.4 s;
- spawn plus imports before the next job runs, 0.75–1.2 s.

The final smoothing publish spent **3.8 s in one `StageStore._write`**; look at this in
step 5. Apply took 3.9–4.7 s: store prepare 0.7–0.9 s, write 0.3–0.4 s, `apply_path`
0.4 s, follow-up preparation 0.55 s, and the request-side bundle copy, which was profiled
earlier. Everything except spawn scales with result and bundle size, which is what steps
4–5 shrink.

### 1. t-boost: cheap model restore — native accessors (highest leverage; no format change)

`_attach_model` (`sklearn.py:5152`), `_restore_metadata` (`:1138`) and
`_native_json_kind` (`:1324`) each serialise the whole native bank to JSON and parse it.
They do this just to read `schema.objective`, `provenance` and `kind`. In the path jobs
that costs **18.0 / 8.8 / 6.4 s**.

- Add small native accessors in `crates/t-boost-py/src/lib.rs`, with the stub in
  `_t_boost.pyi`.
- Use them in those three helpers, and pass `kind` through instead of sniffing it twice.
- Every `from_bytes` / `from_json` caller benefits, including `_checkpoint.py:268-269`.
- This makes old items A1 and A3 mostly unnecessary.

**Tests:** restored attributes equal today's for every envelope-version fixture,
including legacy bare blobs and multiclass; stubtest; the existing serialisation suite.

### 2. t-boost: immutable training state owns its data digest

`data_digest` re-serialises every array plus the 21 MB categorical JSON. It is called four
times per point: `_paths.py:81`, `_paths.py:152`, Haute `_staged_runtime.py:518` and Haute
`_tboost.py:303`. That costs **5.9 / 3.8 / 3.3 s**.

- Compute the digest once in `_FullFitState.capture()` and store it as a field. Do not
  mutate the frozen dataclass afterwards.
- `load_session` passes in the digest it has already verified from the manifest.
- Mark the owned arrays read-only, so the immutability the cache relies on is enforced and
  not merely assumed.
- **Then re-measure** the remaining `full_model` uses. If any still matter, use one private
  template estimator and `copy.copy` it, the existing session idiom (`_staged.py:144`,
  `:349`). Do not use a parameter-only estimator: `objective` and `tweedie_rho` come from
  the native schema.
- Derive `n_features_in_` from the validated state, not from a model rebuild.

**Tests:**
- the fresh digest equals the loaded digest;
- writes to owned arrays raise;
- mismatched-data refusal still fires;
- mutating a returned model leaves the session unchanged;
- `configure_path` evidence is byte-equal for Poisson and Tweedie.

**Steps 1–2 result (22 Nov, implemented in t-boost; not committed).**
- **Native accessors:** `objective()` and `provenance()` on the model classes, a
  module-level `_native_json_kind` read once per load, and a `factored` count in
  `deployed_census`, which `_complexity` now uses.
- **Training state:** `_FullFitState` owns its digest (computed at capture, or the verified
  manifest digest), has read-only arrays, caches its unit weights, and provides
  `n_features`. `candidate_bytes` serialises only the requested point.
- **Gates:** the full t-boost Python suite passed (873 tests); `mypy --strict` and stubtest
  are clean; new tests are in `test_estimator_serialization.py` and
  `test_training_checkpoint.py`.

Same harness, same points, `--phase-timing`, against the step-0 baseline:

| Job | Baseline | Now |
| --- | ---: | ---: |
| GBM | 55.7 s | 55.6 s |
| Pruning path | 86.3 s | **55.6 s** |
| Banding path | 61.9 s | **44.9 s** |
| Smoothing path | 35.5 s | **26.3 s** |
| Path jobs total | 183.7 s | **126.8 s (−31%)** |
| Workflow to smoothing applied | 292.3 s | **237.3 s** |

Equivalence:
- final Gini and deviance are identical;
- every checkpoint member is byte-identical apart from `fit_time`;
- all 24 path points are identical, including exact losses, statuses and complexity
  diagnostics.

Per call, `full_model` now takes 2.3 ms (was ~195 ms), `pruning_selection` 3.1 ms and
`data_digest` ~0 ms, so no shared template is needed. A checkpoint load still takes 1.5 s;
that is step 7's job.

**Finding for step 5.** t-boost stage revisions hash the inference JSON, which includes
`fit_time` (`duration_seconds`, `fitted_at`). Identical fits therefore get different
revisions across runs. Content-addressed snapshot keys must not assume revision stability
across refits, and must decide whether wall-clock metadata belongs in an identity hash.

### 3. GBM thread policy — measured; no Haute change

Haute passes `n_jobs = cpu_count` (32) to t-boost, which overrides t-boost's default pool
of 12 threads for this shape.

- **Measured:** with 12 threads the GBM worker took 106.7 s, against 59.0 s at 32; the
  pruning path was unchanged.
- Keep Haute's allotment as it is.
- The finding for t-boost is that the `_default_fit_pool` docstring's evidence (fewer
  threads faster for 8 bags) does not hold on this 32-core, 678k-row fit. Revisit that
  heuristic separately in t-boost. Low priority, since Haute does not use it.

### 4. Haute: path-job orchestration without churn

Scope note: this step removes only *redundant* saves and reloads. In banding and smoothing
the `:250` save is genuine: it flattens the applied candidate. Rows stop being rewritten
and recompressed in every job only when step 7 splits the fit from the stage chain.
Step 4 does not replace that; it is not a stopgap for step 7.

- **Write the parent checkpoint once per job.**
  - `_stage_paths.py:190-192` saves each parent, and `:250` saves it again; in pruning the
    second save follows a no-op.
  - The rewrites defeat the stat-keyed session cache (`_staged_runtime.py:203-212`),
    causing 2–3 reloads per job (6–8 s) and 1–2 saves (4–8 s).
  - Save only when the session changed. Keep the bounded-memory and atomic-failure
    behaviour.
- **Remove the second result pass** (`_stage_paths.py:367-380`, ~4 s in pruning). It
  decodes and re-encodes every point result only to embed `prepared_path` and `paths`,
  which the bundle metadata already holds. Write each result once.
- **Keep the reference fit's family in memory** within the job instead of round-tripping
  candidates through bytes (`:255-258` → `_staged_runtime.py:233-237`). Check memory first
  with several validation fits.
- **Use strict decoding everywhere.** `read_path_result` and `read_path_artifact` use bare
  `json.loads` (`:434`, `:468`). Share the stage store's strict finite-float decode hooks
  (`_stage_store.py:40-48`). Then drop the recursive `_assert_json_finite` walk on reads.
  Checksums prove byte integrity, not semantic validity, so validation stays at the read
  boundary, done once and properly.

**Step 4 result (22 Nov; implemented; not committed; patch in
`patches/step-4.patch`).**
- **Checkpoints:** `StageBundle.replace_checkpoint` writes once and refreshes the cache.
  Pruning writes each parent once; banding and smoothing write only to flatten. Tests show
  exactly one load and one save per fit, for single-holdout and 3-fold CV.
- **Point results:** written once, with no family information. Each read derives
  `staged.paths` as the retained families valid for the point, plus its own family. This
  replaced a first design that stored family references: Codex showed that normal rebuilds
  and invalidations could leave those dangling.
- **Decoding:** one strict JSON decoder for the store and point reads.
- **Budgets:** t-boost `pruning_budgets` owns the default schedule.
- **Step 4 review:** Codex approved after three rounds. It found dangling references twice
  and non-strict manifest decoding. Strict manifests were added to both readers. A final
  browser run was clean.
- **Steps 1–2 review:** Codex approved after two rounds. The `_native_json_kind` probe now
  matches `json.loads` exactly, including errors and parser limits, and the compute-once
  digest guard was added.

| Job | After steps 1–2 | After step 4 |
| --- | ---: | ---: |
| Pruning path | 55.6 s | **45.1 s** |
| Banding path | 44.9 s | **40.5 s** |
| Smoothing path | 26.3 s | **21.5 s** |
| Path jobs total | 126.8 s | **107.2 s** (183.7 s at baseline) |
| Workflow to smoothing applied | 237.3 s | **212.3 s** (292.3 s at baseline) |

Equivalence: checkpoint members, all path points and the final metrics are identical. The
bundle is still 476 MB, because the duplicated ancestor snapshots are step 5's target.

The point-read route still builds and walks the full response before compacting it. That
is deliberately left for step 5's persisted compact point views, not patched here.

### One storage spec for steps 5 and 7

Write one storage spec covering t-boost checkpoint v2 (step 7) and the Haute bundle and
store layout (step 5), even though they are implemented in sequence. Each depends on the
other's split of fit evidence from stage chain, and on the pointer-publish decision. One
spec also means a single "rebuild the GBM" break for the user, not two.

### 5. Haute: diagnostics stored once — extend the existing stage-store idiom

**Problem:** every point result embeds all ancestor snapshots, and its own diagnostics
twice. Snapshots are 338 MB of JSON in the 476.6 MB bundle, and only 85 MB of that is
unique (22 distinct snapshots).

**Design:**
- **Use the existing mechanism.** The store already writes immutable files with
  `{sha256, size}` descriptors and hydrates them at read (`_stage_store.py:262-335`). Use
  that idiom for bundles and point results too, not a second mechanism.
- **Key snapshots by content.** Each snapshot is keyed by its own SHA-256, with the t-boost
  stage revision, evaluation identity and diagnostics schema as checked fields. A t-boost
  revision identifies the model, not Haute's diagnostics.
- **Own snapshot.** It is hydrated from the result's top-level diagnostics, not stored
  twice.
- **Loss history** is held once per fit (see Decisions).
- **Compact reads** load compact representations directly. Each point gets a persisted
  compact view, as the store's `.view.json` already does, so reads never hydrate dense
  ancestors only to discard them.
- **Size caps.** Enforce the member and manifest caps in `_pack` at write time, as the
  store does at `_stage_store.py:296-300` and `:326`.
- **Retention.** Snapshots shared across revisions need reachability-aware cleanup, since
  leases currently cover whole revisions. Specify this before implementing.
- **Design comparison during the spec (Codex).** Immutable shared bundle members with
  small revision manifests would make Apply a pointer publication. Portable `.hstage`
  archives would then be assembled only for export. Choose deliberately between that and
  the incremental version; do not let a cache grow into it.

**Tests:**
- the hydrated response deep-equals today's for a full workflow;
- compact/full parity;
- missing, corrupt or checksum-mismatched members fail loudly;
- oversize members fail at write;
- differing evaluation contexts for the same model;
- restart, failed publication, checkpoint restore and concurrent cleanup;
- golden route-shape tests.

### 6. Haute: staged-job status carries the compact projection

- Final status responses are 15.7–17.6 MB today.
- Return the existing compact projection (`_stage_views.py:54`, the `diagnostics:
  "compact"` contract), computed once at completion in `_train_status_response`
  (`modelling.py:218`). Keep the full result on the job record for server use
  (`_training_lifecycle.py:415`).
- Do not null the result: `useBackgroundJobs.ts:187` and `ModellingConfig.tsx:850` need
  it, and the job would stay tracked forever.
- Codex preferred a typed completion receipt instead. The compact projection reuses an
  existing contract with less change.

**Tests:**
- a staged job completes through polling with a compact result;
- ordinary training is unchanged;
- automatic continuation, reopening, a stale completion after a document switch, and
  cancellation;
- the final-restore checks still validate.

### 7. t-boost: checkpoint format v2

Make all of these changes in one format change:
- **Separate the fit from the stage chain.** Keep the immutable fit evidence (rows, native
  state, full stage) apart from the small stage chain, so later jobs never rewrite or
  recompress rows.
- **Store the full stage's inference once.** Drop `full.json` / `_full_json`; it equals
  `stages/full.json`.
- **Store categorical columns as codes plus levels**, extending the existing
  `_polars_cat_codes` representation, not a new encoder. This also replaces the per-value
  validation loop at `_checkpoint.py:250-253`.
- **Define the data digest over logical content**, not physical `.npy` / JSON encoding, so
  identity stays stable across encodings. Haute persists that digest.
- **Compression last:** re-measure DEFLATE level 1, 3 and 6 on the slimmer checkpoint.
  Today level 1 is 3× faster for a 23% larger file, but the decision may no longer matter.

**Tests:** saving twice is byte-identical; v1 files are refused with a clear error;
resumed pruning, banding and smoothing are equivalent; categorical fixtures with nulls,
unseen levels, Unicode and numeric categories pass; dictionary determinism holds across
processes.

### 8. Native and parallel work (separate investigations)

- **Pruning family operation.** Each `apply_keepset_budgeted` call copies vectors, bins rows
  and builds a thread pool for each of the 10 budgets. Prepare once per family, as banding
  already does (Codex), before considering parallel budgets.
- **`band_family` scaling:** 16.9 s, partly parallel. Profile per thread first.
- **Categorical input — one coded representation from fit to serve** (resolved after
  reading the code).
  - **Today:**
    - Native fit and every post-fit native call take `cat_x: Vec<Vec<String>>`, one
      string per row (`lib.rs:4257` and others).
    - Python builds those strings value by value (`_ingest.py:216-230`; 3.19 million
      `_cat_level` calls).
    - The staged state keeps them as 21 MB of per-row strings, which every
      `apply_keepset_budgeted`, banding or graduation call converts into Rust strings again.
    - Prediction already uses codes plus labels (`_polars_cat_codes`, `coded=True`).
    - The comment at `_ingest.py:218-224` describes a `fill_null` optimisation that the
      code no longer performs.
  - **Change:**
    - The native fit and post-fit entry points accept the same codes-plus-labels input
      that serving uses, so per-row work stays in polars or Rust.
    - Python formats labels once per *distinct level*: missing markers and `str()`
      semantics stay in the adapter. This reconciles the two reviewers.
    - `_FullFitState` holds the codes, and checkpoint v2 stores them (step 7).

- **Parallel per-point evaluation in Haute:** last, once the remaining per-point cost is
  known. It requires repeated and reordered equivalence, byte-identical results across
  thread counts, bounded memory and cancellation.

## t-boost tidy-up (do each item in the step that already touches that code)

Do these with the related step rather than as separate churn, so each file changes once.

**Policy that lives in two libraries**
- **Pruning budget schedule (step 4).** Haute's `_pruning_budgets`
  (`_stage_paths.py:183-188`) re-implements t-boost's schedule (`_paths.py:243-244`: ten
  evenly spaced counts plus the selection-rule count). t-boost should own the schedule and
  expose it, with Haute calling it, so the two cannot drift apart. The one Haute-specific
  rule stays in Haute: a common floor that accommodates every retained fit's mandatory
  mains.

**Redundant serialisation (step 1, native accessors)**
- `_complexity` (`_paths.py:200-209`) parses the whole bank JSON to count tables and
  cells. A native accessor should return those counts.
- `_native_json_kind` runs twice per `from_json` (in `_unpack_json` and `from_json`).
  Determine the kind once and pass it through.
- `_load_stages` (`_checkpoint.py:350`) and `from_candidate_bytes` (`_paths.py:175`)
  rebuild `session.full_model` inside loops just to read `n_features_in_`. Take it from the
  validated state instead.

**Defensive copies that grow with point count (step 2)**
- `TBoostPathFamily.points` deep-copies every point's diagnostics on each access.
  `candidate_bytes` builds the whole list to emit one point (`_paths.py:109`), which is
  quadratic in the number of points. Give it a single-point accessor.
- `_FullFitState.weights` allocates a fresh `np.ones` array on each access when there is no
  weight. Store it once on the immutable state.
- `_FullFitState` is `frozen` but its arrays are mutable. Make the owned arrays read-only,
  which step 2's cached digest needs anyway.

**Format redundancy (step 7, checkpoint v2)**
- `full.json` / `session._full_json` duplicate `stages/full.json`. Keep one.
- Categorical columns: move to the single codes-plus-labels representation (step 8).

**Stale documentation and evidence**
- `_ingest.py:218-224`: the comment describes a `fill_null` optimisation the code no longer
  performs. It goes with the categorical change (step 8).
- `_default_fit_pool` docstring (`sklearn.py:3306-3318`): its "fewer threads are faster"
  evidence is contradicted on this machine (12 threads: 106.7 s; 32 threads: 59.0 s).
  Re-benchmark across shapes and correct either the heuristic or the claim.
- `specs/roadmap/staged-training.md:201-202`: amend the backward-compatibility promise,
  per the user's decision.

**Repository hygiene (needs the user's OK; untracked files)**
- `specs/roadmap/t-boost-improvements.md:Zone.Identifier` and `…:mshield` are Windows
  download-metadata files copied into the tree. Delete them; don't commit them.

## Process for every step

- **Rebuild the t-boost binding after Rust changes.** Haute's venv loads the in-tree
  `t-boost/python/t_boost/_t_boost.abi3.so`, so rebuild it as CONTRIBUTING.md says, or
  the harness measures a stale binary.
- **Specs first.** Update the owning repository's spec before code: t-boost
  `specs/roadmap/staged-training.md`; Haute `specs/modelling/low-level.md` and
  `specs/frontend-modelling-optimiser-ui/*`.
- **Tests.** Red–green with focused tests in the owning repository, then the affected
  modules once. In t-boost, run the gates in CONTRIBUTING.md for each area touched
  (binding: stubtest and reinstall; Python: pytest and `mypy --strict`). In Haute, follow
  the targeted verification rules in AGENTS.md.
- **Re-measure** with the audit harness after each step.
- **Equivalence oracle (stronger than final Gini).** For the same chosen points, compare
  selected predictions, reports, recipes, candidate statuses and stage revisions, plus
  restart and frontend contract behaviour. Also cover a multi-fit evaluation
  configuration, not only the single holdout.
- **Review.** One Codex review of the whole diff per repository before any PR, per
  AGENTS.md.

## Storage v2 implementation tracker (steps 5 and 7)

**Design:** [storage-design-2026-11-22.md](storage-design-2026-11-22.md) (v2, after the Codex
and Fable reviews). The specs have been written:
- Haute: `specs/modelling/low-level.md`, "Stage storage (version 2)", with five reconciled
  paragraphs;
- t-boost: `specs/roadmap/staged-training.md`, "Training checkpoints (format version 2)",
  with the compatibility line amended.

**Done (22 Nov):**
- **Goldens frozen** from pre-v2 code: `tests/_stage_goldens.py`, `tests/test_stage_goldens.py`
  and `tests/fixtures/stage_goldens/{holdout,cv3}.json.gz` (70 and 68 public responses).
  - They are deterministic across runs and pin `HAUTE_TRAINING_THREADS=1`.
  - `HAUTE_TRAIN_LOSS_HISTORY_LIMIT=8` forces thinned top-level histories against the full
    snapshot histories.
  - They cover aliases, rejected, `unchanged` and identity points, the family rebuild with
    banding retained, finalise and restore-final.
- **Baselines:** `baselines/` holds the modified and untracked files of both repositories,
  the HEAD commits and `SHA256SUMS`, including the goldens' SHA-256.

**Demo note:** the demo runs Haute from its installed wheel and t-boost editable from
source. The step 4 Haute changes are not in the demo; steps 1–2 (t-boost) are.

**Slices.** Each ends green against the goldens and gets one Codex review.
- [x] **A. t-boost format and identities.** Done (22 Nov). Codex approved after two rounds (combined
  size budget, categorical spec wording, evidence-binding test, duplicate parts);
  patch in `patches/storage-slice-A.patch`.
  - Gates: t-boost (877 passed) and Haute's staged modules plus goldens (356 passed) are
    green.
  - At 678k rows: capture encode 0.20 s; data digest 0.01 s; evidence save 0.87 s
    (10.5 MB, DEFLATE level 1); chain save 0.10 s; portable load 0.89 s.
  - Original scope:
  - the evidence/chain split;
  - canonical data members and the categorical coding at the save/load boundary, with
    per-row strings kept in memory;
  - the data and evidence digests;
  - path candidate v2;
  - one full inference document;
  - the native compatibility check.

  Haute stays on portable `save`/`load`; the t-boost gates, Haute's staged tests and the
  goldens pass without Haute changes. Measure capture-encode and load-decode time.
- [x] **B. Haute store v2 as an unwired library.** Done (22 Nov). Codex approved after three rounds.
  - Round 1:
    - full ancestry checks against links, plus an anchor;
    - one store lock for pins, installs and collection;
    - validation at construction;
    - serialised installs;
    - legacy reclaim holding leases;
    - a bounded archive header;
    - a Windows no-clobber retry in `_file_ops`.
  - Round 2: `..` traversal refused; the object map frozen; inherited members derived, not
    listed.
  - 30 stage-object tests, with mutation checks on the ordering and locking.
  patch in `patches/storage-slice-B.patch`.
  - `haute/_process_tokens.py` is extracted from `SourceCacheStore`, which delegates to it.
  - `haute/modelling/_stage_objects.py` holds the objects, manifests, pins, `publishing`,
    `collect`, legacy reclaim and archives. `strict_json_loads` moved there.
  - Tests: 21 new, plus 440 across the affected modules, passing. A mutation check
    confirmed that the interleaving test catches wrong ordering.
  - The old-store *state* in `StageStore._read` moves to C, with pointer schema 3.
  - Original scope: objects, revision manifests, one
  validator for store and archive, multi-holder pins, the publication transaction,
  cleanup, the old-store state. Tested with fault points; the lifecycle is untouched.
- **Before C (done, 22 Nov):**
  - **Goldens order-insensitive:** `normalise` walks keys sorted. A pure key reorder had
    renumbered ids. The goldens were regenerated from post-B code, which equals the
    pre-v2 responses.
  - **Baseline:** `baselines/post-slice-B-*`, with the new golden digests in `SHA256SUMS`.
  - **CV warnings:** "training_temp_parquet_removed_after_abort" in CV captures is a
    mislabelled success path. `run_evaluation_fit`'s `finally`
    (`_training_job.py:1558`) removes a split it never consumed and logs it as an abort.
    Pre-existing, outside storage; fix separately.
- **C sub-slices.** Each ends with the goldens and staged modules green, plus one Codex
  review.
  - [x] **C1. Diagnostics codec (22 Nov), approved by Codex after three rounds.** `haute/modelling/_stage_diagnostics.py`
    (`split`, `hydrate`, `closure`, `same_staged`, `without_nulls`, which moved from
    `routes/_training_artifacts`). References sit only at positions the codec controls;
    table references carry a validated `summary`. Tests: `tests/test_stage_diagnostics.py`
    (14). Patch:
    `patches/storage-slice-C1.patch`.
  - [x] **C2 (22 Nov), approved by Codex after two rounds** (round 1: refuse non-object result envelopes; validate members before touching the source). Patch: `patches/storage-slice-C2.patch`.
    - The audit workflow (ev8) completed with no errors, against step 4 (ev7):
      - workflow complete: 212.6 s (224.8 s);
      - pruned path ready: 116.7 s (115.7 s);
      - banding job: 54.2 s (56.6 s);
      - smoothing job: 32.6 s (37.6 s).
    - The differences are within noise. C2 changes the format; the copy removal that should
      save time is C3–C5.
    - Two `test_modelling_golden` failures predate this work: uncommitted fixture and
      `EvaluationReportPayload.purpose` edits, which are not ours.
  - [x] **C3 (22 Nov), approved by Codex after three rounds.** Patch: `patches/storage-slice-C3.patch`.
    - **What it does:**
      - pointer schema 3 over `StageObjects`, with a revision manifest per publication;
      - reads pin and hydrate in place (`StoredRevision`), and mutations prepare their response before the commit;
      - collection runs after pointer changes, with digest-bound roots and pins;
      - store revisions are read only through verified handles, and a persisted revision `view` serves compact reads;
      - one shared token directory for every scope;
      - an old store reads as empty and is reclaimed by the first full build.
    - **Review rounds:**
      - round 1: root integrity, verified consumption, post-commit housekeeping, stale cancellation tests;
      - round 2: link-safe pin markers, non-raising pin release.
    - **Interim archive copies (removed by C4/C5):** the worker handoff and restore-final `stage_state`, both via `StoredRevision.write_archive`; Apply still packs an applied bundle.
    - **Measured:**
      - ev9 (before the view) ran 226.0 s and completed;
      - compact status reads took 106 ms before the view, against 26 ms pre-C3. The view fixes this; ev10 re-measures.
    - **ev10 (with the view):**
      - workflow complete: 222.6 s;
      - pruned path ready: 117.2 s;
      - compact status reads: 42 ms (26 ms pre-C3);
      - full status reads: 0.27–0.35 s (unchanged);
      - one model's store after the workflow: 107 MB (1 revision, 103 objects).
    - **Remaining compact cost:** decoding the ~200 KB revision manifest that holds the view's reference. After approval, `read_revision(size=...)` replaced the size check by re-encoding; flagged for C4 review.
    - **Full suite:** 4,721 passed, 4 failed. The failures predate this work (the `purpose` schema field, `stages` config round-trip examples, Vite).
  - [x] **C4 (22 Nov): workers read the parent in place and return deltas.** Approved by Codex after three rounds. Round 1: a genuinely spawned worker with a name-and-content store check, and orphaned output at validation. Round 2: Windows lock-file reads in that check. Patch: `patches/storage-slice-C4.patch`.
    - **Worker:** given a parent reference (`StageStore.parent_reference` / `open_parent`), it has read-only access. It takes no pin or token and writes nothing under the store.
    - **Rows:** copied lazily into job scratch through their verified handle. The "typed Parquet source" was dropped as unnecessary.
    - **Archives:** delta archives inherit by name and reference (`ObjectRef` members, `DeltaRevision`).
    - **Validation and ingest:** the validator reads the delta in place, and `prepare(parent_revision=...)` binds a delta to its parent.
    - **Handoff:** there is no handoff copy and no `tmp_parquet` for stage resumes.
    - **Measured, ev11 → ev12:**
      - `copy_parent` 1.41 s → 0; extraction 0.34 s → 0;
      - `stage_into` 2.04 → 0.65 s; `prepare` 6.57 → 5.38 s;
      - validation 2.68 → 2.07 s; `_pack` 3.17 → 2.42 s;
      - worker jobs flat (164.2 s); workflow 213.9 s.
    - **Not storage:** `_assert_json_finite` takes 4.2 s over 8 calls.
  - **Phase timing after C3 (ev11, 22 Nov).**
    - **Totals:** workflow 222.1 s. Worker jobs: full GBM 53.2 s; pruning, banding and smoothing 49.9 / 40.3 / 21.2 s (111 s of path work).
    - **Per path job, copies C4 removes:** handoff `write_archive` ~0.65 s; worker `extract_archive` ~0.15 s; part of the worker `_pack` (~0.5 s total); the validator's full bundle open (~0.8 s total); and parent `stage_into` ~0.35 s. That is about 2 s per job, about 6 s per workflow.
    - **C5 (pointer Apply):** about 1–2 s more.
    - **Other parent spans per job:** `_assert_json_finite` ~1 s; `StageStore.prepare` ~1 s, mostly result split and view.
    - **Conclusion:** C4 and C5 are cleanliness and disk work, worth only single-digit seconds. The time is in path-job compute. Raised with the user before starting C4.
  - **C2 design (written down before implementing):**
    - **Archive helpers in `_stage_objects`:** `write_bundle(dest, members{name: Path|bytes},
      result, metadata, revision)` (hashes, dedupes, writes a v2 full archive) and
      extraction of a `StageArchive` into a temp dir by digest, returning `{name: path}`.
    - **`StageBundle`:**
      - `members` (read-only parent files), `scratch` (job-owned), and `member(name)`,
        which checks scratch writes first, then members;
      - `write_member(name, bytes|path)`;
      - `source` is `members['source.parquet']`;
      - `session(key)` loads `evidence=fit/<key>/evidence` and `chain=fit/<key>/chain`,
        with the cache keyed by both file identities;
      - `replace_checkpoint` writes only the chain to scratch;
      - metadata is hydrated on open (`metadata.staged` via the codec), so existing code is
        unchanged.
    - **Member names:**
      - `source.parquet`, `plan.json`, `fit/<key>/evidence|chain`;
      - `path/<id>/<point>/<fit>.candidate`, `path/<id>/<point>.result` (split envelope);
      - `snapshot|table|history/<sha>`;
      - `export/<model.native|feature-contract.json|plan.json|evaluation-results.json|evaluation-report.json>`.
    - **`StageRuntime`:**
      - `remember` saves `<key>.evidence` and `<key>.chain` into its root;
      - tuning retention moves those, where it used to move `*.checkpoint`;
      - `finalize` and finalise packing build v2 members.
    - **`_stage_paths`:** candidates and results go through `bundle.write_member`; readers
      hydrate envelopes; `read_path_artifact` reads objects straight from the zip.
    - **Store:** unchanged. `.hstage` is opaque to it, and `_validate_training_artifacts` and
      restore-final keep using `open_stage_bundle` and `export_artifacts`.
  - [ ] **C1 (original scope). Diagnostics codec, unwired.** Split a result into an envelope plus snapshot,
    table and history objects, and hydrate it back. Define the `metadata.staged` reference
    normalisation. Test that hydrate(split(x)) equals x on real holdout, CV and final
    results, full and compact.
  - [ ] **C2. Bundle format under the unchanged store.**
    - `_pack` and `open_stage_bundle` move to v2 archives with `fit/<key>/evidence|chain`
      members: `save_evidence` once, `save_chain` in `replace_checkpoint`.
    - `StageBundle` gets read-only members plus a scratch root.
    - The session cache is keyed by evidence and chain digests.
    - Measure.
  - [ ] **C3. Pointer schema 3 over `StageObjects`** (still full-archive ingest): reader pins,
    collection after pointer changes (not on every lease release), old-store state and
    reclaim. Rewrite `test_stage_store`'s helper. Update the audit harness, which reads
    `manifest.json` and `<rev>.view.json`, and the bench scripts.
  - [ ] **C4. Transport:** the delta archive, in-place worker reads via a read-only
    `StageObjects` construction path (no writes or tokens), a typed source and cleanup
    ownership. Proven with the spawned-worker test and the audit harness.
  - [x] **C5 (22 Nov), approved by Codex in round 1 with no findings.** Patch: `patches/storage-slice-C5.patch`.
    - **What it does:**
      - pointer Apply (`path_point_revision` + `StageStore.prepare_members`), with no archive;
      - a finalised revision keeps no checkpoint parts;
      - restore-final makes no archive copy and publishes no `stage_state`;
      - the store-to-archive writers are removed, so `write_bundle` is the only writer;
      - pointer records carry the compact view's digest, so status reads parse only the view.
  - [x] **C6. Measured (ev13 against the pre-storage ev7).**
    - **Workflow:** 215.7 s against 224.8 s, inside the ±7 s single-run noise.
    - **Parent-side storage spans (ev11 → ev13):**
      - handoff copy 1.41 s → 0; extraction 0.34 s → 0;
      - `StageStore.prepare` 6.57 → 3.16 s; `stage_into` 2.04 → 0.53 s;
      - `_hydrate` 3.49–5.85 → 2.61 s.
    - **Compact status reads:** 27–34 ms (26 ms pre-C3).
    - **Store:** one copy of each object, shared across revisions (107 MB for a model after the workflow), with verified reads.
    - **Remaining cost:** worker compute (162 s of 216 s) and `_assert_json_finite` (~4 s).
- **Hazards to handle:**
  - Process tokens per scope: move them to the store root, closed in test teardown.
  - Collection cost at about 1,000 objects: measure it.
- [ ] **C. Wire it in (original scope):** diagnostics normalization and hydration; delta ingest and publish;
  pointer Apply; finalise and restore-final; worker in-place reads with a typed source.
  Goldens after each step, then the audit harness.
- [~] **D. Coded native entry points: measured (22 Nov) and not worth doing now.**
    - The string conversion costs 0.09–0.13 s per call (542k rows × 5 categorical columns), and decoding at session load 0.046 s.
    - A real pruning job (49 s) makes only 11 `cat_x`-capable native calls (`_TableModel.tables`, 0.18 s in total). The heavy native call, `apply_keepset_budgeted`, takes no categories.
    - The spec bullets stay, as a design for a later release.
  - **Pruning-job profile (in-process, demo parent, 49.1 s):**
    - native `apply_keepset_budgeted` 17.6 s (10 calls);
    - `_write_point_result` 10.0 s (11 points). JSON `dumps` takes 6.8 s and `loads` 6.4 s, because C2's write is encode → decode → split → canonical-encode per object;
    - `_run_evaluation` 13.8 s, including `_train_model` 6.0 s and `table_report` 3.2 s;
    - `deepcopy` 3.5 s; session loads 3.7 s (13 calls).
  - [x] **Point-result writes (22 Nov), approved by Codex.** Patch: `patches/point-result-writes.patch`.
    - **Change:** `_write_point_result` encodes each object once, with no strict JSON round trip. That is byte-identical for today's producers, and a test compares the live results of a real path job against the old form.
    - **In-process pruning job:** `_write_point_result` 10.0 → 3.5 s.
    - **Path-job workers (ev13 → ev14):** 50.6 / 41.1 / 16.9 → 41.6 / 35.5 / 13.4 s, 18.1 s in total.
    - **Workflow:** 215.7 → 199.9 s.
    - **Codex caveat (minor):** the equivalence relies on string keys and list score rows; the comment says so.
  - **Native pruning profile (22 Nov; demo session, 10 budgets 10…142 tables; TBOOST_PROFILE spans, t-boost `fa45a80`).**
    - **Per call:** 0.6 s at 10 tables up to 4.3 s at 142; 17.6 s in total.
    - **The rebalance is about 80% of that,** and nearly all of the rebalance is `cell_refit::fit_cell_correction`. Its Gauss–Seidel ridge backfit makes a flat pass (up to 30 sweeps) and then an adaptive pass (up to 100). At 142 tables that is 54 supports, 11.4k cells and 462k fit rows in 3.3 s, about 1.7 ns per row per support per sweep, which is already memory-bound and sequential.
    - **Purify and score:** under 40 ms. The bank build is about 80 ms per call.
    - **Wasted work:** from 30 tables up, the held-out slice rejects the rebalance (deviance after > before). Those 1–3 s per call are computed and then discarded, by design of the guard.
    - **The 10 budget calls are independent** (`_paths.py:257`), but run one after another.
    - **Proposal:** a native pruning family call, like `band_family`, that runs the budgets in parallel under rayon, with concurrency bounded by memory. Each candidate stays bit-identical. Expected ~17.6 s → ~4–5 s per pruning job, and the categorical conversion done once rather than 10 times.
  - [x] **Native pruning family (22 Nov), t-boost commit after `fa45a80`, approved by Codex after three rounds.**
    - **What changed:** `_Model.apply_keepset_family` prunes all budgets in memory-bounded parallel waves. Each candidate is identical to a single call.
    - **Review rounds:** account for every row-proportional allocation; the direct single-binding oracle and failure tests; working buffers raised to 128 bytes per row.
    - **Measured:**
      - demo session, 10 budgets: 18.7 → 7.9 s, with identical revisions;
      - pruning job: 41.6 → 32.8 s;
      - workflow (ev15): 199.9 → 190.0 s, with the same final model and metrics.
    - **Remaining limit:** memory bandwidth. Concurrent backfits each run 1.6–2.2× slower.
  - **Next candidates:**
    1. ~~Point-result serialization~~ (done above).
    2. `apply_keepset_budgeted` (t-boost native).
    3. `deepcopy` in the runtime.
  - Original note: measure one post-fit call's
  string-conversion cost first. Two spec bullets already assume this: t-boost "Native
  coded categoricals", and the design's 3.4.
