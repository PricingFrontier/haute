# Stage storage v2: shared immutable files — design v2, after review (22 November 2026)

This is the single storage spec for plan steps 5 and 7 ([performance-plan-2026-11-22.md](performance-plan-2026-11-22.md)).
It covers the t-boost training-checkpoint format and the Haute stage bundle and store.

**Decided by the user:**
- shared immutable files, with Apply as a pointer publication;
- retraining is acceptable: no old-format readers;
- the GBM loss history is stored once.

**Reviews:** Codex and Fable both returned `DESIGN: SOUND WITH CHANGES` on draft v1, and
this version incorporates their findings. Section 9 records where they disagreed and what
was chosen. Raw reviews are in `reviews/`.

Nothing is implemented. Once accepted, the text moves into
`haute/specs/modelling/low-level.md` and `t-boost/specs/roadmap/staged-training.md` before
code.

## 1. Problem (measured)

**Per path job:**
- the parent `.hstage` (0.2–0.5 GB) is copied into job scratch and fully extracted with
  hashing;
- a complete new `.hstage` is packed;
- the parent extracts it again just to compare metadata (`_validate_training_artifacts`);
- the store copies it to `<rev>.pending` (copy, fsync, hash) and renames it.

Apply repeats the extract, pack and copy for one pointer change, taking 3.9–8 s.

**Content:** the final bundle is 476.6 MB.
- 412 MB is point-result JSON. 338 MB of that is stage snapshots, of which 85 MB is
  unique: 64 snapshot occurrences across 24 points, but 22 distinct.
- The t-boost checkpoint (100.6 MB expanded) is recompressed on every stage change,
  although its rows never change.
- Inside it, `full.json` duplicates `stages/full.json`, and categorical columns are 21 MB of
  per-row JSON.

**Caps:** the manifest is at 16.5 MB of its 32 MiB cap, and the largest point result at
26.5 MB of its 32 MiB cap. Neither is enforced at write.

## 2. Design summary

Each model scope holds **content-addressed immutable objects** and small **revision
manifests**, each listing its complete object closure.
- A job reads its parent's objects in place. Its revision is pinned by the running-job
  record, which already forbids every mutation.
- The job returns a **delta archive** containing only new objects plus a manifest
  proposal.
- The parent ingests it through the same validated path that reads a full portable archive.
- **Apply writes one revision manifest and swaps the pointer; nothing is copied.**
- Stage diagnostics become content-addressed objects, with their dense fields (tables,
  histories) as separate objects. Every snapshot, table bank and history is therefore stored
  once, and compact reads never touch dense data.
- t-boost's checkpoint splits into immutable **fit evidence**, written once per GBM fit,
  and a small **stage chain**, written per stage revision.

## 3. t-boost training checkpoint v2

t-boost owns this part. Public inference JSON and binary formats are unchanged.

### 3.1 Parts

| Part | Contents |
| --- | --- |
| **Fit evidence** (immutable; once per fit) | the **canonical data members** (3.4); the native training envelope; supports and share; monotone; the raw GBM loss history; the full stage's working bank and inference document, **stored once** |
| **Stage chain** (per stage revision) | path settings and their structural OOB evidence; the post-fit stages (stage metadata plus each stage's working bank and inference document); the **evidence digest** it extends |

- `full.json` / `_full_json` are removed. Validation of the full stage is structural: no
  parent, settings or keep-set.
- The native schema agreement check between the full stage and the training envelope
  covers everything it covers today: `schema`, `provenance`, `grids`, `link` and `mode`.
  It becomes **one focused native compatibility check**, not JSON comparison.
- Native envelope validation is kept: membership, bag spans and categorical fit bins.

### 3.2 API

- `session.save(path)`: one portable file holding both parts.
- `session.save_evidence(path)` and `session.save_chain(path)`: the parts separately.
- `TBoostTrainingSession.load(path)` or `load(evidence=…, chain=…)`, with
  `expected_data_digest` as today. A chain whose evidence digest differs is refused.
- Path-candidate envelopes go to **version 2** and are bound to the **evidence digest** as
  well as the parent stage revision. A candidate cannot be restored onto a different fit on
  the same rows.

### 3.3 Identities, all versioned

- **Data digest:** SHA-256 over the canonical data members' digests (3.4), as today but
  over canonical members. It is therefore derived from bytes that load verifies by checksum.
  Load needs no recomputation, and replacing the data changes the digest. Capture computes
  it by hashing the same canonical bytes incrementally, without a second row-sized buffer.
- **Evidence digest:** SHA-256 of the evidence part's manifest. That manifest lists every
  evidence member's digest, so two GBMs on the same rows differ.
- **Stage revision:** unchanged, including `fit_time`. Nothing in this design needs stable
  revisions across refits, because objects are keyed by bytes and snapshots by content.
  Stripping `fit_time` would mean parsing and re-encoding multi-megabyte documents on every
  load and restore. Tests normalise `fit_time`.
- **Outcome fingerprint:** the existing bank fingerprint, unchanged, used for evaluation
  reuse.

### 3.4 Canonical data members and categorical coding

**Numeric members:** each array is stored as one member of raw C-order little-endian bytes,
with a small strict header (name, dtype, shape). An absent array has an explicit absence
entry. A valid feature NaN keeps its bit pattern.

**Categorical coding** is defined explicitly. It is not `_polars_cat_codes` as it stands,
whose `unique()` order is unspecified and which uses per-row labels for small and numeric
columns.
- `uint32` codes, plus levels ordered by first appearance in row order;
- missing values use a reserved code, recorded in the header, not a trailing pseudo-level;
- `_cat_level` semantics (sentinel escaping, missing markers, `str()` of numeric categories)
  are applied once per distinct level;
- levels are encoded as UTF-8 with length framing;
- the coding is identical for polars, pandas and numpy inputs.

**Native entry points:** the native fit and post-fit entry points accept codes plus levels,
as serving does. There are 56 `cat_x: Option<Vec<Vec<String>>>` parameters in the binding.
This lands in the same release as a separately verified change under the bit-repro gate.
A temporary decode-to-strings shim may exist between the two changes but must not ship.

### 3.5 Compression

The evidence part is written once per fit; the chain part is small. Keep DEFLATE, with the
level chosen by measuring the new layout.

### 3.6 Refusal

A v1 checkpoint or candidate is refused: "written by an older t-boost; rebuild the GBM".
Amend `staged-training.md:201-202`.

## 4. Haute stage store v2

Haute owns this part. Locking and reachability stay in `StageStore`. Bundle resolution and
response hydration live in one modelling module, shared by scripts and routes. The runtime
never depends on HTTP schemas, and Haute never interprets private t-boost checkpoint
members.

### 4.1 Layout per model key

```text
.haute/staged-training/<key>/
  manifest.json              # pointer: current / preview / final / job / checkpoints / next_preview
  revisions/<revision>.json  # immutable revision manifests
  objects/<sha256>           # immutable content-addressed files
  staging/                   # parent-owned ingest temporaries
  leases/                    # reader pins (4.5)
```

**Objects:**
- regular files, never rewritten;
- every path component is checked for symlinks and reparse points;
- verified by SHA-256 on ingest and on a reader's first open;
- the bytes actually consumed are the bytes verified: the reader hashes and reads through
  the same handle. `zipfile` and t-boost accept file objects;
- the process checksum cache is sized for a store's object count, not
  `lru_cache(128)`.

**Publication:** install a file without clobbering, on POSIX and Windows. Crash recovery
removes or repairs links left with `nlink == 2` by an interrupted link-then-unlink
publication. Windows sharing violations are retried.

**Durability:** the guarantee is against **process** crashes. Directory fsyncs are not
performed, and the spec says so rather than claiming power-loss durability.

### 4.2 Revision manifest

```text
{ format: "haute-stage-revision", version: 2,
  objects: { "<logical name>": {sha256, size, kind} },   # COMPLETE closure of this revision
  result:  {...},          # typed result envelope: small fields + diagnostic references
  metadata: {...} }        # job_kwargs, fit_config_digest, fits, staged, paths, path — references, no diagnostics
```

- **Closure:** every object a revision needs is listed, including inherited ones. The
  parent revision is provenance only, never a retention edge.
- **Names:** logical names are validated against an allowed-name grammar, extended from
  today's bundle rules.
- **Size caps, by kind and enforced at write:**
  - JSON objects: 256 MiB each;
  - binary members (source, checkpoint parts, candidates): no per-object cap, only the
    revision's total expanded budget, which keeps today's 4 GiB bundle limit;
  - revision manifests: 4 MiB;
  - the pointer manifest: 32 MiB.
- **Pointer records** keep `revision`, `build_id`, `parent_revision` and `config_digest`.
  `sha256` / `size` now describe the revision-manifest bytes, so public schemas keep their
  meaning. Storage schema version 3 never leaks into public stage-state schema version 1.

### 4.3 Diagnostics stored once

- **Stage snapshot object:** one stage's diagnostics, keyed by content. Its dense fields
  are themselves objects, referenced by digest: `tboost_tables` (5–6 MB) and each loss
  history. Content addressing stores each bank and history once; in this run the same GBM
  history appears in all 64 snapshots and 24 results. There is no special per-fit history
  member. `validation_loss_history`, reference-fit and final-fit histories remain distinct
  objects.
- **Every stage, including the selected one, is a snapshot reference.** There is no
  "own snapshot from top-level" rule, because it is wrong: a store result is a *response
  projection* (thinned 200-row history, `diagnostic_metrics`, different `fit_evidence`
  keys), while snapshots hold `TrainResult` fields (1,151-row history, `metrics`).
  - In **point results** (`TrainResult` form), the top-level diagnostics are hydrated from
    the own snapshot.
  - **Store results** reference the same objects where their provenance matches. Any
    response-specific projection is applied after hydration.
  - **Final results** keep their separate final-fit diagnostics plus the exploration
    snapshot references.
- **Hydration:** one function at every read boundary (store snapshot, path read,
  restore-final). Compact reads skip table objects and thin histories on the fly through the
  existing `compact_response`. No per-snapshot or per-point view objects are stored, and the
  existing revision-level view member is kept. Hydration runs **outside** the manifest lock;
  only the pointer read is locked.
- **Point families:** a point stores no family information. `staged.paths` is derived on
  every read, full or compact, from the families its revision retains and that are valid
  for it (step 4).
- **Evaluation member names in responses:** `evaluation.*_path` strings currently embed
  `path-<id>-<point>.result.json`. Their present form is kept, so responses deep-equal.

### 4.4 Job data flow and transport

1. **Start.** The worker request carries the store key and the parent revision.
   `stage_resume` gains a versioned replacement for its exact key set. The worker resolves
   members itself, through the same validated reader the parent uses, not a path map built
   by the parent.
   - **The source is a typed input:** an explicitly owned Parquet source passed to the
     canonical evaluator. An extensionless `objects/<sha>` must not fall through
     `TrainingJob`'s string-path heuristics (`_training_job.py:2193`) or `_split_key`
     comparisons.
   - The parent no longer copies or deletes an input file, so parent cleanup must never
     unlink a store object (`_training_lifecycle.py:1371`).
2. **Worker.** `StageBundle` separates **read-only members**, resolved from the store or a
   portable archive, from a **job-owned scratch root**. Scratch holds new checkpoint
   chains, candidates, OOF and partition files and point outputs; today these are written
   beneath `bundle.root`. Members are read lazily.
3. **Transport.** The worker returns one `stage_state` artifact: a **delta `.hstage` v2**
   holding the revision-manifest proposal plus only the new objects. The worker protocol
   is otherwise unchanged: no per-object artifacts, so the 64-artifact limit and 4 MiB
   metadata limit stand. The worker never writes under the store.
4. **Ingest and publish.** See 4.6 for the transaction.
   - The parent stream-copies new objects from the delta into `staging/`. A copy, not a
     rename, because job output lives under the system temp directory, which may be on
     another filesystem.
   - It verifies them, checks that every inherited reference belongs to the pinned parent's
     closure, and compares `metadata.staged` with the public response under a defined
     **normalization of reference-bearing metadata**.
   - This replaces full extraction, `_prepare`'s copy and the large rename.
5. **Job directory after ingest.** It keeps only the ordinary artifacts (model, contract,
   evaluation, `training-response.json`). The delta and the per-point exports are deleted.
   The job record's in-memory result is plan step 6, the compact projection.

### 4.5 Pinning and leases

**A running job's parent is pinned by the running-job record.** That is today's invariant
and is kept, stated and tested:
- `begin`, `apply`, `apply_path`, `checkpoint` and `remove` refuse while a job runs;
- publication requires `expected_revision == current`;
- post-fit jobs always read current.

So no lease is held for a job's lifetime. An orphaned worker after a parent crash may lose
its inputs to a later cleanup, which is harmless: an interrupted job's output is never
ingested, and cleanup skips and retries files that are open on Windows.

**Readers** (inspection, Apply, export, restore-final) take **multi-holder pins**:
- this reuses the marker-file plus process-token lease of the source and snapshot store
  (`_node_snapshots.py`), not the exclusive per-path `FileLock`;
- pins are registered under the manifest lock;
- cleanup probes them without waiting;
- concurrent readers of one revision proceed together;
- pin files are removed when released (`remove_unheld_lock_file`).

### 4.6 Publication transaction and cleanup

**Order** (a write-ahead manifest, extending today's `.pending` idiom):
1. Validate and hash new objects in `staging/`, outside the lock.
2. Write the **revision manifest** to `revisions/`. While its publisher is live it is a
   root: the running-job record for jobs, the `preparing` pin for synchronous Apply.
3. Install the objects into `objects/`. An existing digest is verified and the duplicate
   dropped.
4. Under the manifest lock, check compare-and-swap and replace the pointer.

A failed publication never deletes an object it installed or deduplicated, because another
revision may share it. Reachability cleanup decides.

**Response preparation** (hydration for the return value) happens before commit. A failure
after commit, in notification or cleanup, never rolls back a committed revision. The
existing rule and regression are kept.

**Cleanup:**
- computes reachability and deletes inside **one manifest-lock hold**;
- roots are current, preview, final, named checkpoints, pinned revisions, and live
  write-ahead manifests;
- a missing or corrupt root manifest **stops deletion**;
- it removes unreachable objects, unrooted revision manifests, dead staging files, stale
  pin files and recognised temporaries.

### 4.7 Old stores

An old store (schema 1–2, `*.hstage`, `*.result.json`) reads as **"no saved stages; build
the GBM"**. It is a state, not an exception, so the full build that the message asks for
can start. That build replaces it, and cleanup recognises and deletes legacy file names.
No legacy content is interpreted. Another server still using the old generation sees the
same state. There is no compatibility reader.

### 4.8 Apply, finalise, restore-final

- **Apply:**
  - `materialize_path_point` writes a revision manifest. It references the parent's closure
    plus the selected point's candidate objects, with the typed result envelope carrying
    Apply's merged configuration (drafts, enabled phases, applied recipe and path, saved
    config) and response metadata. No result object is rewritten.
  - There is no flattening: path jobs flatten before preparing (`_stage_paths.py`), so
    envelope depth never exceeds one.
  - Invalid families are dropped, and the closure lists exactly the snapshots the remaining
    results reference. Ingest checks this.
- **Finalise:** refits from source, plan, parameters and recipe, and never opens a session.
  The final revision's closure is defined explicitly: source, the final plan, export
  members, the final result and diagnostics, and the exploration snapshot references.
  Finalising immediately after a pointer Apply is tested.
- **Restore-final:** resolves the five export members under a reader pin into job-owned
  artifacts. There is no whole-archive copy and no `stage_state` artifact; the handle
  reader already tolerates its absence. It survives expired job directories.

### 4.9 Portable `.hstage` v2

A zip containing one revision manifest plus its objects (`ZIP_STORED`), validated by the
same reader as the store. A **delta** variant lists inherited objects by reference only.
It is used by scripted `run_stage_job` and `TrainResult.stage_checkpoint_path`, by worker
transport (delta), and by tests. `scripts/modelling_audit/serialization_bench.py` and the
tests that open bundles as zips are updated.

## 5. Memory and resources

**Memory:** in-place reads do not change worker memory, because arrays are loaded, not
memory-mapped. The session-cache estimate is redefined for split parts within the 512 MiB
budget. CV with several fits stays bounded: one active session per fit, with cache eviction.

**File counts:**
- single holdout: about 100–150 objects;
- 10-fold CV: under about 1,000, mostly candidates (about 17 per family per fit).

That is acceptable. Count and byte budgets are specified and measured, including Windows
create cost. Objects are not grouped.

**Capacity tests** cover CV and ten named checkpoints, not only this run.

## 6. Expected effect (estimate; measured after)

| | Today | After (estimate) |
| --- | --- | --- |
| Storage | 476 MB per retained revision | roughly 200 MB for the whole workflow |
| Write per path job | copies of 0.2–0.5 GB | tens of MB of new objects |
| Apply | 3.9–8 s | well under 1 s |
| Inspection | 1.2–2.4 s | reading one result of about 170 KB plus small snapshots |
| Handoff | 2.6–4 s | most of it removed |
| Checkpoint load | 1.5 s | lower: no duplicate full JSON, no per-row categorical JSON |

## 7. Implementation order

1. **Freeze equivalence goldens from today's code, before any change.** They cover:
   - store and path responses, full and compact;
   - single holdout and 3-fold CV;
   - selected, non-selected, alias, `unchanged` and rejected points;
   - family rebuilds and draft merges;
   - the 200-row against 1,151-row history distinction.

   Job ids, revisions, path ids, digests and `fit_time` are normalised. The goldens cannot
   be regenerated once the v1 readers go.
2. **Specs:** this design moves into both repositories' specs, with exact identity byte
   formats, the complete closure, the transaction, the pin lifecycle, hydration schemas,
   limits and old-store handling.
3. **t-boost checkpoint v2:** format, identities, candidate v2, one full document, the
   native compatibility check, canonical data members and categorical coding. Then the
   coded native entry points, as a separate verified change in the same release. Gates per
   CONTRIBUTING, including bit-repro and the core suites if the core changes.
4. **Haute store v2, built with normalized diagnostics from the start, so there is no
   interim format:** objects, revision manifests, pins, the transaction, cleanup, old-store
   handling, portable and delta archives, the `StageBundle` resolver with its scratch
   split, and hydration.
5. **Pointer Apply, finalise, restore-final.**
6. **In-place worker reads with the typed source; lifecycle integration** (cancellation,
   automatic continuation, named checkpoints).
7. **Verify contracts against the goldens, then measure:** storage, peak memory, file
   counts and latency for holdout and CV.

## 8. Tests that catch the risky failures

| Risk | Test |
| --- | --- |
| Publication versus cleanup | `_fault_point`-style barriers at each step of 4.6, with cleanup run concurrently. Two publications share an object and one fails; a dedup hit lands against an object being collected; a crash falls between link and unlink. Every committed root stays readable, and only unreachable data is removed. |
| Commit | A failure before commit leaves the pointer unchanged. A failure after commit never rolls back. |
| Pinning | While a job runs, every mutation refuses and inspection of current returns promptly. Two concurrent readers both proceed. Bytes survive until the last pin releases, across pointer replacement and removal. |
| Identity and integrity | Rows are altered while the advertised digest is kept. A chain or candidate is paired with other evidence. Same-shaped native state is swapped. A file is replaced between verification and use. JSON is checksummed but semantically invalid. Each is refused without publishing. |
| Equivalence | Hydrated responses deep-equal the frozen goldens. |
| CV and finalise | Folds with different banks, histories and floors: pooled OOF equals independent evaluation. No reserved-test access occurs. Finalise works immediately after pointer Apply. |
| Retention and restore | Save a checkpoint, replace current, discard, collect, restart, restore. Expire job artifacts, then restore final: the export bytes match. Removal waits for readers. |
| Resources and platform | More than 64 new objects go through the real protocol. Per-kind caps: a large binary publishes, an oversized JSON object fails. An oversized nested archive. An extensionless source. CV cache eviction. Cross-filesystem staging. Windows open-file contention. |
| t-boost | Digests are equal across processes, thread counts and polars, pandas and numpy inputs. Categorical cases: nulls, sentinels, numeric categories, Unicode. Saving is stable. Predictions, recipes and path evidence match after reload. |
| Old stores | An old directory reads as empty; a full build succeeds; legacy files are reclaimed. |

## 9. Review record: where the reviewers differed, and the choice

| Topic | Codex | Fable | Chosen |
| --- | --- | --- | --- |
| Job-long pinning | Per-reader pins, plus a worker-owned pin for the interval after parent death | No job lease; the running-job record pins current | **Running-job record** (an existing, testable invariant). Multi-holder pins for readers. An orphaned worker's lost inputs are harmless because its output is never ingested. |
| `fit_time` in revisions | Exclude, versioned | Keep; not needed, and stripping costs re-encoding on hot paths | **Keep.** Codex's real concern, candidates bound to fit evidence, is adopted (candidate v2). |
| Digest on load | Must be derived from verified data, or recomputed | Trust the recorded value under member checksums | **Canonical data members:** the digest is derived from verified member checksums. That satisfies both, with no recomputation. |
| Compact views | Persist compact diagnostic bodies; join families on read | Split dense fields into objects; thin and skip on the fly; no view objects | **Fable's** (fewer object kinds, no write-time projection baked in). Families are still joined on every read. |
| Loss history | t-boost evidence is authoritative | Content-addressed history objects; no special member | **Both:** t-boost keeps the raw history in its evidence; Haute's diagnostics reference history objects, stored once by content. |
| Coded categoricals | Same release; no shipped shim | Format first with a decode shim, then native signatures | **Same release, two verified changes; the shim never ships.** |
| Order | Build the store with normalized diagnostics from the outset | Store slice first; pointer Apply early | **Codex's:** one store format, no interim. **Fable's:** goldens first. |
| Worker transport | Bounded proposal file; copy across filesystems | Delta `.hstage` as the single `stage_state` artifact | **Delta `.hstage`.** It is the bounded proposal, and one validation path serves deltas and full archives. |

The reviewers agreed on the rest: the library split, in-place worker reads with verified
opens, separate objects, no flattening for finalise, per-kind caps, old stores as a
recoverable state, no archive for restore-final, the transaction ordering with live
write-ahead roots, the complete-closure manifests, and the read/scratch split.
