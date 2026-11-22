# Review of storage-design-2026-11-22.md (reviewer: fable)

Read-only review. Nothing was run except read-only inspection of the code, specs and the run7 store. run7 is single-holdout (`fits = validation-0`), so every CV statement below is derived from code, not measured.

Paths: `H:` = /home/ralph/suite/haute/src/haute, `T:` = /home/ralph/suite/t-boost/python/t_boost.

## Verdict in brief

The shape is right: content-addressed objects, small revision manifests, pointer Apply, evidence/chain split. Nine points need changing in the text before it becomes spec. Three of them (leases, ingest ordering, old-format refusal) would produce defects if implemented as written.

## A. Correctness and safety

**A1. Job-long lease "as today" cannot work. HIGH.**
- The lease is `FileLock`: an exclusive `flock` (`H:_file_lock.py:42`), one instance per path per process (`:392-409`), and only the acquiring thread may release it (`:360-361`).
- Today the parent holds it only for the copy (`H:routes/_training_stages.py:153-167`). Held for a whole job (design 4.4.1) it would:
  - block `inspect_path` on the same revision for the job's duration (`H:routes/_training_paths.py:59`); in the automatic flow the running job's parent *is* current, whose path points the user is inspecting;
  - make `checkpoint` and `apply_path` hang instead of returning Conflict, because both take the lease before the running-job check (`H:modelling/_stage_store.py:387-394`, `:711-729`);
  - fail on release, since the launch thread and the completion thread differ.
- **Simpler fix: no job lease at all.** A running job already pins current: `begin`, `apply`, `apply_path`, `checkpoint` and `remove` all refuse while a job runs (`_stage_store.py:450`, `:680`, `:727`, `:392`, `:805`), and publish requires `expected_revision == current` (`:557-563`). Post-fit jobs always read current. State that invariant and test it.
- If a lease is still wanted (orphan workers after a server death), use the marker-file plus process-token lease that the snapshot store already has (`H:_node_snapshots.py:20-24`). It supports many holders and lets the worker hold its own.

**A2. Ingest ordering races cleanup. HIGH.**
- Design 4.4.3/4.6: objects enter `objects/`, then the revision manifest, then the pointer. Preparation runs outside the manifest lock, and `cleanup` runs on every lease release (`_stage_store.py:797-799`), so on every inspection.
- Between ingest and publication the new objects are unreachable and would be deleted. A dedup hit on an existing but unreachable object has the same race.
- "Revisions with a pending publication" is listed as a root, but no mechanism is given.
- Fix, using today's idiom (`.pending` plus its lease, `_stage_store.py:483-498`, `:815-825`): write the revision manifest first, under a publication lease, and make it a root while that lease is held. Then ingest objects. Then swap the pointer. A crash releases the lease and the manifest and objects become garbage.
- Cleanup must compute reachability and delete inside one manifest-lock hold, as today (`:812-846`).

**A3. Refusing old stores as written strands the model. HIGH.**
- `_read` raises on an unknown version (`_stage_store.py:156-159`), and `begin`, `recover`, `snapshot` and `cleanup` all call it. A refused store could then never start the full rebuild the message asks for.
- The new cleanup globs would also never reclaim the old `*.hstage` / `*.result.json` files (0.47 GB in run7).
- The spec needs: an old directory reads as "no saved stages, rebuild" (a state, not an exception), a full build may replace it, and legacy file names are recognised and deleted.

**A4. "Own snapshot from top-level diagnostics" is wrong for store results. HIGH for equivalence.**
- Measured on run7. In all 24 point results (`TrainResult` form) the own snapshot equals the top-level fields. In the store's `<rev>.result.json` (response form) it does not: `metrics`, `train_rows` and `validation_rows` are absent at top level, `fit_evidence` has different keys, and `loss_history` is thinned to 200 rows against 1,151.
- Cause: the snapshot copies `TrainResult` fields (`H:modelling/_staged_runtime.py:629-643`), while the response is a projection (`H:routes/_training_worker.py:563-605`).
- Fix: one rule for both forms. Every stage's diagnostics, including the selected one, is a snapshot object. In point results, hydrate the top-level fields *from* the own snapshot, the opposite direction to the draft.

**A5. 256 MiB cap on every object is a regression. MEDIUM-HIGH.**
- 256 MiB is the JSON result cap (`_stage_store.py:31`). Source Parquet and checkpoints are bounded today only by the 4 GiB bundle budget (`_staged_runtime.py:74`).
- Evidence is already 100 MB expanded at 678k rows. Larger data would be refused at write.
- Make the cap per kind: JSON objects 256 MiB, binary members under the revision budget only.

**A6. Digest and identity definitions. MEDIUM.**
- **Categorical levels.** `_polars_cat_codes` is not a canonical form: `unique()` has no order guarantee (`T:_ingest.py:152`), a `_CAT_MISSING` label is always appended (`:153`), and numeric or small columns use one label per row (`:148-151`). Design 3.3 says it "extends" this helper and also that level order is first appearance. Those conflict. The state needs its own canonical coding (distinct levels, first appearance, defined missing code), and the digest must be over that.
- **Evidence digest.** 3.2 does not define it. It cannot be `data_digest`: two different GBMs on the same rows share that. Define it as the SHA-256 of the evidence manifest (which lists every member digest).
- **Logical digest on load.** Today the data digest falls out of the member checksums (`T:_checkpoint.py:87-89`, `:196`). A logical digest does not. Say whether load recomputes it or trusts the recorded value under the member checksums; I would trust it and recompute in tests.
- **Byte form.** Specify little-endian, length-prefixed fields and an explicit marker for absent arrays.

**A7. Hydration equivalence, other points. MEDIUM.**
- Path responses embed the member name in `evaluation.*_path` (`H:routes/_training_paths.py:49-51`). The renamed members in 4.2 change those strings, so "deep-equal" fails unless the old string form is kept deliberately.
- A stored point view must stay family-free and derive `staged.paths` on read (`H:modelling/_stage_paths.py:212-237`), or it reintroduces the dangling families fixed in step 4.
- Snapshot closure is undefined. Apply drops invalid families (`_stage_paths.py:529-537`); the manifest must list exactly the snapshots its remaining results reference, and ingest must check that.
- The design does not say what form the worker's response takes. If it stays fully hydrated (17 MB), the parent's comparison with `metadata.staged` needs hydration or re-hashing, and the two finiteness walks stay.

**A8. Finalise, restore-final, checkpoints, CV, cancellation.**
- **Finalise (MEDIUM-LOW).** It refits and never opens a session (`_staged_runtime.py:722-770`). It drops `path`/`paths` but keeps the unflattened checkpoints (`:733-737`), so a final bundle's checkpoints do not match its `staged` metadata. Nothing reads them. The final revision should list only source, final plan (it replaces the parent's, `:762`), export members, result and snapshots.
- **Restore-final (MEDIUM, simplification).** It copies the whole bundle into job artifacts and re-extracts it (`H:routes/_training_lifecycle.py:422-427`, `:450`). I found no consumer of `stage_state` beyond validation and transfer, and the handle reader tolerates its absence (`H:routes/_training_artifacts.py:501`). Restore-final needs the five export members only, so no archive.
- **Named checkpoints.** Sound: records share objects and restore keeps the revision id.
- **CV (code-derived).** Up to 11 fits gives about 187 candidate objects per family. Fine for manifests (about 100 KB), relevant to B3.
- **Cancellation.** Sound once A1 and A2 are fixed. On Windows, deleting a file an orphan worker still has open raises `PermissionError`; cleanup must skip and retry, not fail the request.

**A9. Integrity of in-place reads. LOW-MEDIUM.**
- `publish_staged_file(replace=False)` links then unlinks on POSIX (`H:_file_ops.py:296-297`). A crash between the two leaves an object with `nlink == 2`, which `_digest` refuses (`_stage_store.py:61`) until the staging file is removed. Cleanup must remove dead staging before objects are read.
- `_hash_artifact` is `lru_cache(128)` per process (`:67`). A model has 100+ objects, so it will thrash. Verify on first read, prime at ingest, and size the cache to the store.
- The worker should hash the file handle it then reads from; `zipfile` accepts a file object, so t-boost can take the same handle.

## B. Cleanest design

1. **Library split is right.** Evidence/chain, digest and coded categoricals belong in t-boost; objects, manifests and hydration in Haute.
2. **Reuse, not a third store idiom (MEDIUM).** Haute already has a generation store with multi-holder leases, retire-by-rename and `_fault_point` hooks (`H:_node_snapshots.py:20-24`, `:388`). Where the stage store needs more than its own idioms, take from there.
3. **Snapshot views are avoidable (MEDIUM).** Persisting `snapshot-view/<sha>` beside each snapshot stores every projection twice and bakes in the loss-history limit of the moment (`H:modelling/_stage_views.py:40-51`, an environment variable). Simpler: split the two dense fields out of the snapshot as their own objects (`tboost_tables`, 5–6 MB; `loss_history`, 1.16 MB). A compact read does not dereference tables and thins the history on the fly, through the existing `compact_response`.
   - This also removes the `fit/<key>/loss_history` special case: content addressing stores the history once by itself. In run7 all 64 snapshots and 24 top levels carry the identical history.
   - Point bodies are then about 170 KB each (measured, 4.1 MB for 24), so separate point view objects are unnecessary.
4. **One transport, not two (MEDIUM).** The worker protocol is one file per artifact kind (`H:routes/_training_worker.py:779-787`, `_training_artifacts.py:501`), and job output is under the system temp directory, usually a different filesystem from the project, so "rename" is a copy. Make the worker's `stage_state` a delta `.hstage` v2: proposal manifest plus new members only, inherited members by reference. Ingest of a delta and of a full archive is then one code path, the protocol is unchanged, and the worker never writes to the store.
5. **`StageBundle` over a resolver.** Right, but `bundle.root` is also the writable workspace: `_staged_runtime.py:385`, `:440`, `:502`, `:544`; `_stage_paths.py:300-320`, `:393`. The abstraction needs read-only members and a separate job scratch root.
6. **t-boost API.** `save` / `save_evidence` / `save_chain` and two `load` forms are acceptable. The schema check "through native accessors" needs accessors for `grids`, `link` and `mode` as well (`T:_checkpoint.py:277-280`).

## C. Gaps

- **`.parquet` suffix.** `TrainingJob` treats a string ending `.parquet` as an on-disk source (`H:modelling/_training_job.py:2193`); `objects/<sha256>` has no suffix. `_split_key` also compares against `bundle.source` (`_staged_runtime.py:404`). Give the job an explicit typed input.
- **Worker protocol.** `stage_resume` has an exact key set (`_training_worker.py:736-741`); define its replacement. Pass store key and revision and let the worker resolve through the same validated reader, not a path map built by the parent.
- **Job records.** Completed staged jobs keep a 17 MB response in memory and a full bundle in the job directory. Define both for v2 (ties to plan step 6).
- **Session cache estimate.** It is four times the zip's expanded size (`_staged_runtime.py:250-257`); redefine it for split parts.
- **Lease files.** They are never removed: run7 has seven for one live revision. `remove_unheld_lock_file` exists.
- **Durability.** "A crash leaves only unreachable objects" holds for a process crash. For power loss it needs directory fsyncs, which the existing helpers do not do. State the claim accurately.
- **Windows and WSL.** Rename of a new file can hit transient sharing violations; `publish_staged_file(replace=False)` has no retry on Windows. Per-file create and fsync cost matters for 200 objects.
- **Path candidates.** The `t-boost-path-candidate` version stays 1 but embeds the redefined data digest; say so.
- **Remaining `.hstage` consumers:** `scripts/modelling_audit/serialization_bench.py`, `tests/test_staged_runtime.py`, `tests/test_stage_store.py`, `H:routes/_training_stages.py:180-182` (finalise reads `final_job_kwargs` by opening the bundle).
- **Hydration under the lock.** `snapshot()` hydrates while holding the manifest lock (`_stage_store.py:349-351`). Joining several objects there lengthens the hold; read the pointer under the lock and hydrate outside it, since objects are immutable.

## D. The five open questions

1. **Exclude `fit_time` from revisions? No, not in this change.** Nothing in the design needs revisions to be stable across refits: objects are keyed by bytes and snapshots by content. `_revision` hashes raw text (`T:_staged.py:255-262`) and is recomputed on every load and candidate restore (`T:_checkpoint.py:370`, `_paths.py` `from_candidate_bytes`), so stripping a field means parsing and re-encoding 5–20 MB documents on the hot path that step 1 just cleared. For tests, normalise or freeze the clock.
2. **In-place reads? Yes.** Same user, same trust domain as today. Conditions: no job lease, pinned by the running job (A1); verify each object on first open; the worker never writes under the store (B4).
3. **Granularity? Keep one object per result and snapshot; do not group.** Grouping defeats dedup and lazy reads. Scale is about 100 files single-holdout and under 2,000 with 10-fold CV, well inside directory and inode limits. Use B3 to cut object kinds. Measure Windows create cost before considering grouping candidates per fit.
4. **Coded categoricals in the same release? Same release, separate change.** Fix the on-disk format (codes plus levels) and the digest in the format change, decoding to strings on load. Then change the 26 native entry points (`cat_x: Vec<Vec<String>>`, `crates/t-boost-py/src/lib.rs:3810-6110`) under the bit-repro gate with no format change. One break for the user, and the Haute work does not wait on Rust. The decode shim must be gone before release.
5. **Pointer Apply without flattening? Yes.** Finalise refits and never reads the envelope (A8), and every path job flattens before preparing (`_stage_paths.py:270-296`, `:189`), so depth never exceeds one. Add tests for both facts.

## E. Order and tests

**Order.** Mostly right. Changes:
- **Freeze the equivalence goldens first**, from current code, for single holdout and CV, compact and full. After v1 readers go they cannot be regenerated. Normalise job ids, revisions, path ids, digests and `fit_time`.
- Split step 1 as in D4.
- Split step 2: store, manifests, ingest from archives, cleanup and old-store handling first; in-place worker reads second.
- Apply as pointer does not depend on step 3 and is the largest visible gain; it can follow step 2 directly.

**Tests that catch the risky failures.**
- Interleavings with `_fault_point`-style hooks: cleanup between each ingest step and the pointer swap; dedup hit against an object being collected; crash between link and unlink.
- A running job pins current: every mutating call refuses, and inspection of current returns promptly during a job.
- An old-format directory reads as empty, a full build succeeds over it, and its files are reclaimed.
- Store-result hydration equals the golden for the selected stage's `metrics`, row counts, `fit_evidence` and full `loss_history`.
- Closure: after Apply of `unchanged` drops a family, no listed snapshot is unreferenced and none referenced is missing.
- Per-kind caps: a source or evidence object above 256 MiB publishes; a JSON object above it fails at write.
- t-boost: equal digests across processes for the same frame, across polars, pandas and numpy inputs; a chain refused against evidence from a different fit on the same rows.
- Windows: cleanup while a reader holds an object open.

## Ranked recommendations

1. Drop the job-long lease; rely on, state and test "a running job pins current" (A1).
2. Write-ahead revision manifest under a publication lease as the GC root (A2).
3. Specify old-store handling as a recoverable state with reclaim (A3).
4. One snapshot rule, top level hydrated from the own snapshot (A4); freeze goldens before any code.
5. Per-kind size caps (A5).
6. Canonical categorical coding and a defined evidence digest (A6).
7. Delta `.hstage` as the single worker transport; no archive for restore-final (B4, A8).
8. Split dense fields instead of persisting views; drop the per-fit loss-history member (B3).
9. Define snapshot closure, the worker response form and the read/scratch split (A7, B5, C).
10. Answer the open questions as in D.

DESIGN: SOUND WITH CHANGES
