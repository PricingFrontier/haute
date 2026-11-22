The shared immutable object design is the right direction, but I would not implement the draft unchanged. The main problems are in publication ordering, lease semantics, response hydration, and identity definitions—not the choice of storage architecture.

I inspected the supplied code, specifications, and run7 archive read-only. No files were changed and no tests, builds, servers, or training were run. Run7 supports the underlying diagnosis: its archive is 476.2 MB, with 24 point results containing 64 snapshot occurrences but only 22 distinct snapshots.

My recommendations are ranked below.

1. **High — Specify one transaction boundary between object installation, publication, and garbage collection.**

   Draft §4.6’s ordering is necessary but insufficient. Consider: ingest installs object X; cleanup runs before the revision manifest exists; X is unreachable and is deleted; publication then creates a revision referring to X. Naming “pending publications” as roots does not explain how cleanup discovers their object sets before their manifests exist.

   The simplest solution is to validate and hash files in private staging outside the manifest lock, then install objects, publish the complete revision manifest, and replace the pointer while holding that lock. Cleanup must coordinate its deletion decisions with the same lock. If installation spans unlocked intervals, a pending publication needs a durable, discoverable object inventory protected before the first object becomes visible.

   Failed publication must leave shared objects for reachability cleanup; it must never delete an object merely because this transaction installed or deduplicated it. Another publication may already use it.

   Require every revision manifest to enumerate its **complete object closure**, including histories and compact views. Treat `parent_revision` as provenance, not an implicit retention edge that keeps all history forever. Missing or corrupt root manifests must stop deletion.

   Preserve the existing rule that response preparation can fail **before** commit, but cannot trigger rollback after commit. See [publication](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:534) and the [existing hydration-failure regression](/home/ralph/suite/haute/tests/test_stage_store.py:630).

   Finally, distinguish process-crash safety from power-loss durability. The existing [atomic helper](/home/ralph/suite/haute/src/haute/_file_ops.py:241) renames files but does not sync parent directories. The draft’s unconditional crash claim needs either directory durability ordering or a narrower stated guarantee.

2. **High — “Leases, as today” is unsuitable for job-long concurrent readers.**

   Today a revision lease uses a cross-process **exclusive**, thread-owned `FileLock`. Holding it throughout a path job would make inspection or export of that same revision wait for the job. Passing ownership from the preparation thread to supervisor cleanup is also incompatible with the lock’s release contract. See [revision leasing](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:774) and [lock ownership](/home/ralph/suite/haute/src/haute/_file_lock.py:359).

   Use independently owned reader pins—such as one uniquely named locked lease file per reader—with registration coordinated under the manifest lock. Cleanup probes pins without waiting. This can reuse the existing lock primitive without introducing platform-specific shared locks.

   Define ownership through startup failure, cancellation, timeout, worker exit, completion validation, and abandonment. Release worker-access protection only after termination is confirmed. A parent-only pin also needs an explicit parent-death solution: Haute’s [parent watcher](/home/ralph/suite/haute/src/haute/_parent_watch.py:73) exits the child asynchronously, leaving a possible interval between parent death and child termination. A worker-owned pin acquired before reading closes that interval.

3. **High — The proposed “own snapshot” hydration rule does not preserve today’s responses.**

   Draft §4.3 says the selected stage’s snapshot comes from top-level diagnostics. That is not universally equivalent:

   - Run7’s durable response contains **200 top-level history rows**, while each nested stage snapshot contains **1,151**. Compact views contain 200 in both places.
   - Finalisation generates new final-fit diagnostics but deliberately retains the exploration `staged` snapshots.
   - Stored `TrainResult.metrics` and public `TrainResponse.diagnostic_metrics` are different representations; final-test selection adds another distinction.

   These behaviours come from [response projection](/home/ralph/suite/haute/src/haute/routes/_training_worker.py:555), [snapshot construction](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:629), and [finalisation](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:722).

   A cleaner representation is an explicit diagnostic-object reference for every stage, including the selected stage. Top-level diagnostics may reference that same object **when their provenance matches**, with response-specific projection applied afterward. Final results retain separate final diagnostics and exploration snapshot references. This avoids an exceptional “own snapshot” rule and circular references through result bodies.

   Also resolve the history ownership contradiction: §3.1 embeds history in t-boost evidence, while §4.3 creates another per-fit history object. Prefer t-boost evidence as the authoritative raw history, with cheap library-owned history access and Haute-owned presentation conversion. Explicitly distinguish compact derived projections from another full-history copy. Preserve `validation_loss_history`, reference-fit history, and final-fit history separately.

4. **High — Define data identity, fit-evidence identity, stage identity, and byte identity separately.**

   The draft defines the data digest informally and leaves the evidence digest undefined. These need exact, versioned encodings:

   - Array ordering, dtype/byte order, dimensions, absence markers and length framing.
   - Category normalization, dictionary order and UTF-8 encoding.
   - Treatment of valid feature NaNs and signed zero.
   - Whether evidence identity includes native bag membership, categorical fit bins, full working state and inference metadata.

   On load, checking member checksums and comparing a recorded `data_digest` with `expected_data_digest` is insufficient unless the recorded digest is recomputed from, or cryptographically derived from, the verified logical data. The current implementation explicitly binds its aggregate digest to verified member records: [checkpoint validation](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:170).

   Bind candidate envelopes to immutable **fit evidence**, too. Today candidates check data digest and parent stage revision, but not a separate evidence identity: [candidate validation](/home/ralph/suite/t-boost/python/t_boost/_paths.py:150). Once timing no longer distinguishes refits, equal rows and equal deployed parent content must not imply interchangeable native training evidence. Bump the candidate format as part of this change.

   Excluding `fit_time` is reasonable, but the resulting revision still includes working bytes, parent, settings and keep-set. It identifies stage content and policy—not prediction equivalence. Keep the existing [outcome fingerprint](/home/ralph/suite/t-boost/python/t_boost/_paths.py:205) for evaluation reuse.

5. **High — A member resolver needs explicit input ownership and format, not just resolved paths.**

   The current `StageBundle.root` serves as both extracted input storage and writable workspace. Checkpoint replacement, candidate preparation, OOF files and partition caching all write beneath it. The new abstraction must separate immutable member access from job-owned output and scratch. See [checkpoint replacement](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:233) and [partition retention](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:433).

   Two concrete traps:

   - `objects/<sha256>` has no extension. [TrainingJob’s existing-source path](/home/ralph/suite/haute/src/haute/modelling/_training_job.py:2193) requires `.parquet`; otherwise it uses the generic source loader, which rejects an extensionless source. Pass a typed, explicitly owned Parquet source through the canonical evaluator.
   - [Parent cleanup](/home/ralph/suite/haute/src/haute/routes/_training_lifecycle.py:1371) unlinks the old copied input. Substituting a store path without changing ownership would delete shared data.

   For integrity, a lease prevents collection, not modification. `_hash_artifact` checks the file during hashing; a raw path reopened later is a separate access. Define the threat model and resolver contract: validated descriptors, safe opens, file identity checks, and verification of the bytes actually consumed. Reject linked/reparse-point ancestors throughout the new subdirectories.

   In-place reads are appropriate for trusted cooperating workers. A path mapping alone is not an OS-enforced read-only boundary.

6. **High — Persisted point views must retain dynamic family resolution, and pointer Apply needs a defined metadata envelope.**

   A compact point view cannot freeze its complete `staged.paths`. Existing readers derive families from those retained and valid in the containing revision. Rebuilding pruning can change the family shown by an older banding point; applying `unchanged` can remove downstream families. This was explicitly fixed and tested in [point hydration](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:212) and [the family-rebuild regression](/home/ralph/suite/haute/tests/test_staged_runtime.py:1056).

   Persist compact diagnostic bodies, then join current family metadata on **every** read, including compact reads.

   Apply also changes more than a selected candidate reference: it merges current drafts, enabled phases, applied recipe/path, saved configuration and response metadata. See [Apply’s configuration merge](/home/ralph/suite/haute/src/haute/routes/_training_paths.py:98). If result/view objects are immutable, specify where these changes live. A small typed result envelope in the revision manifest, referencing existing diagnostic objects, satisfies the one-manifest Apply requirement. An unspecified overlay or rewritten result object does not.

   The proposed manifest-only response comparison also needs a defined normalization. Reference-bearing `metadata.staged` cannot be directly compared with hydrated public diagnostics as [today’s validator](/home/ralph/suite/haute/src/haute/routes/_training_artifacts.py:392) does.

7. **Medium — Resource and worker-transport limits need a coherent contract.**

   The proposed universal 256 MiB object cap is a new restriction on source Parquet and checkpoint evidence. Today the outer bundle permits 4 GiB; 256 MiB is a result-file limit. Use type-specific bounds justified against supported workloads.

   Other constraints the implementation will encounter:

   - The worker protocol allows **64 artifacts** and **4 MiB metadata**. Per-object descriptors will exceed 64 with several fits. Use a bounded proposal file with its own validated member inventory; do not silently raise a global protocol limit. See [protocol limits](/home/ralph/suite/haute/src/haute/_worker_protocol.py:80).
   - Worker outputs currently live under system temp, so rename into a project-local store is not necessarily same-filesystem: [artifact root](/home/ralph/suite/haute/src/haute/routes/_training_artifacts.py:410). Either place new stage output on the target filesystem or explicitly stream-copy new objects into local staging before publication.
   - Specify both compressed and nested expanded-size limits, total stored bytes, file counts and inode budgets.
   - Preserve bounded fit processing. The existing [session cache](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:250) has a 512 MiB retention estimate, but that does not bound active sessions, native allocations, hydrated responses or all CV fits together.
   - Hash C-order logical data incrementally; do not create a second row-sized buffer merely to fingerprint existing Fortran-order arrays.

   The one-fit run7 estimate should not become the capacity assumption for CV or ten retained checkpoints.

8. **Medium — Refusing old stores needs a working rebuild path.**

   The message says “build the GBM again,” but full builds currently call store recovery/read and `begin`, which themselves reject unsupported manifests. See [full-build validation](/home/ralph/suite/haute/src/haute/routes/_training_stages.py:105) and [begin](/home/ralph/suite/haute/src/haute/modelling/_stage_store.py:437).

   Specify how an explicit new full build creates a fresh generation without interpreting old diagnostic formats. Define cleanup of the incompatible generation and behaviour with another live server using it. This needs no compatibility reader, but refusal alone leaves the prescribed recovery action unusable.

9. **Medium — Preserve the exact native validation contract while removing duplicate JSON.**

   Removing `full.json` is clean. However, “native schema agreement through accessors” must cover everything currently checked: `schema`, `provenance`, `grids`, `link` and `mode`, not merely objective and feature names. See [current comparison](/home/ralph/suite/t-boost/python/t_boost/_checkpoint.py:276).

   A focused native compatibility check is preferable to exposing large structures back to Python. Keep native envelope validation for membership, bag spans and categorical fit bins in [training_checkpoint.rs](/home/ralph/suite/t-boost/crates/t-boost-py/src/training_checkpoint.rs:52).

The library boundaries are otherwise right. t-boost should own evidence/chain formats, identities, categorical representations and native validation. Haute should own evaluation provenance, diagnostic projections, object retention, jobs and publication. Put reusable bundle resolution and response hydration in a Haute modelling module usable by both scripts and routes; keep locking and reachability in `StageStore`. Avoid making the runtime depend on HTTP schemas or teaching Haute to interpret private native checkpoint members.

My answers to the five open questions are:

| Question | Recommendation and reasoning |
|---|---|
| **1. Exclude `fit_time`?** | **Yes**, while preserving it as information. Version the stage hash definition and bind candidates to fit evidence. Do not describe stage revisions as pure prediction identity; policy and working state intentionally participate. |
| **2. Read store objects in place?** | **Yes**, with concurrent reader pins, explicit ownership, validated member access and a stated trust model. Copying every parent would preserve the main cost. Copies remain appropriate at genuinely portable boundaries. |
| **3. Separate small objects or group them?** | **Separate objects initially.** They give simple validation, deduplication and reachability. Count candidates too: 24 points across 11 fits means 264 candidate objects before results, views and snapshots. Add count/byte budgets and measure directory operations. Packing introduces another index, partial-retention issues and compaction; it has not earned that complexity. |
| **4. Coded categoricals in the same release?** | **Yes, but as a separately verified dependency within that release.** Avoid shipping an intermediate decode-to-strings representation. The existing helper is not yet canonical: it uses unordered `unique()`, per-row labels for small/numeric columns, and a trailing missing level. Fix those rules explicitly rather than merely reusing its name. See [_polars_cat_codes](/home/ralph/suite/t-boost/python/t_boost/_ingest.py:134). Preserve existing category-label semantics across all supported adapters and native entry points. |
| **5. Flatten for finalisation/export?** | **No unnecessary flattening.** Finalisation already refits using frozen source, winning parameters and applied recipe; export copies the resulting ordinary artifacts. It does not deploy the exploration checkpoint. Preserve parent-plus-candidate restoration for subsequent exploration, flattening only the chain when needed. Finalise immediately after Apply must work without an intervening path job. |

For restore-final specifically, the cleaner option is to resolve the ordinary export members under a lease. Today’s [whole-archive copy](/home/ralph/suite/haute/src/haute/routes/_training_lifecycle.py:422) also satisfies the old `stage_state` artifact contract. Do not retain that expensive transport solely to avoid updating the validator. Assemble a portable archive when a consumer actually needs one.

The implementation checklist needs to cover these current consumers explicitly:

| Consumer | Required change |
|---|---|
| [Bundle load/save and scripted resume](/home/ralph/suite/haute/src/haute/modelling/_staged_runtime.py:269) | One member-resolution/validation path for objects and portable archives; preserve `TrainResult.stage_checkpoint_path` for scripted output. |
| [Path preparation, reads and Apply](/home/ralph/suite/haute/src/haute/modelling/_stage_paths.py:240) | Writable scratch separated from inputs; compact/full hydration; dynamic family resolution; exact candidate selection. |
| [Stage job input preparation](/home/ralph/suite/haute/src/haute/routes/_training_stages.py:153) | Replace copied-parent ownership with a revision-view lifetime. |
| [Worker request and output](/home/ralph/suite/haute/src/haute/routes/_training_worker.py:733) | Versioned revision-view/proposal protocol, containment checks, bounded inventory and response transport. |
| [Completion and artifact handles](/home/ralph/suite/haute/src/haute/routes/_training_lifecycle.py:2259) | Preserve the cancellation/completion claim; distinguish consumed stage output from retained ordinary artifacts. Handles borrowing store objects must be roots or acquire pins when used. |
| [Final restore/export](/home/ralph/suite/haute/src/haute/routes/_training_lifecycle.py:392) | Retain final-native artifacts, final evaluation and exploration snapshots with their distinct identities; survive expired job directories. |
| [Public schemas](/home/ralph/suite/haute/src/haute/schemas.py:4219) and [frontend parsing](/home/ralph/suite/haute/frontend/src/types/trainGuards.ts:627) | Public revision records currently require `sha256` **and `size`**. Either preserve their documented meaning for manifest bytes or deliberately update generated contracts. Storage schema version 3 must not leak into public stage-state version 1. |
| Tests and audit scripts | Update direct ZIP consumers, fixtures and [serialization tooling](/home/ralph/suite/haute/scripts/modelling_audit/serialization_bench.py:17); remove legacy-reader expectations deliberately. |

Also specify successful-job cleanup. Path preparation creates per-point output directories, while successful artifact publication retains the job directory. Moving selected stage objects into the store does not automatically remove those other exports or scratch files. Define which files remain owned by the job after ingestion, including `training-response.json`.

Windows/WSL behaviour needs an explicit publication rule. The existing [no-clobber helper](/home/ralph/suite/haute/src/haute/_file_ops.py:282) uses link-then-unlink on POSIX, temporarily producing `nlink == 2`; a crash between those operations conflicts with the proposed permanent single-link invariant. Choose and test a coordinated publication/recovery mechanism. Preserve Windows sharing-violation handling and reject unsupported filesystem behaviour clearly.

I would adjust the implementation order:

1. **Finish the specifications first:** identities, complete reference closure, transaction/pin lifecycle, hydration schemas, limits and rebuild recovery.
2. **Land and verify canonical categorical input and t-boost v2**, in small coherent changes. Include candidate binding and exact native validation.
3. **Build the Haute storage slice with the final normalized diagnostic format from the outset:** immutable resolver, transaction, GC, portable conversion and pointer Apply. Avoid an interim v2 store that still embeds diagnostics.
4. **Integrate the supervised lifecycle and all read/export consumers**, including named checkpoints, cancellation, automatic continuation and final restore.
5. **Verify contracts before measuring speed**, then measure storage, peak memory, file counts and latency for both holdout and several retained fits.

The most useful tests are behavioural and adversarial, rather than round trips alone:

| Risk | Test that would catch it |
|---|---|
| Publication versus GC | Use process barriers after each installation/publication boundary. Run cleanup concurrently. After interruption, every committed root must remain readable and only unreachable data may be removed. Include two publications sharing an object when one fails. |
| Reader lifetime | Keep two readers open on one revision; both must proceed. Replace/remove its pointers and run cleanup; bytes survive until the last pin ends. Cover startup failure, cancellation, parent death and confirmed worker termination. |
| Commit failure | Fail result projection before commit: old pointers remain unchanged. Fail notification/cleanup after commit: the new revision remains usable and is never rolled back. |
| Identity and integrity | Alter rows while retaining the advertised logical digest; pair chains/candidates with different evidence; swap same-shaped native state; replace a file between verification and use. Reject each without publishing. Include checksummed but semantically invalid JSON and mismatched compact/full objects. |
| Response equivalence | Compare frozen expected responses from the same captured results—not two outputs produced by the new hydrator. Cover run7’s 200/1,151 history distinction, full/compact views, aliases, `unchanged`, rejected points, family rebuilds and draft merges. |
| CV and finalisation | Use folds with different banks, histories and mandatory-table floors. Compare pooled OOF predictions and diagnostics against independent per-fit evaluation. Verify no reserved-test access before finalisation, then finalise immediately after pointer Apply. |
| Retention and restore | Save a named checkpoint, replace current, discard preview, collect, restart and restore. Expire job artifacts and restore final; ordinary model/contract/evaluation bytes must match. Model removal releases data only after readers finish. |
| Resource and platform boundaries | Exercise more than 64 new objects through the actual protocol, type-specific size boundaries, oversized nested archives, extensionless source resolution, bounded CV cache eviction, cross-filesystem staging, Windows open-file contention and interrupted link publication. |
| t-boost determinism | Canonical dictionaries across processes, input representations and thread counts; null/sentinel/numeric-category cases; unchanged-session save stability; reload parity for predictions, recipes and path evidence. |
| Recovery UX | Open an old store, observe the actionable refusal, then complete a new full build successfully without invoking a compatibility reader. |

These changes preserve the user’s decisions while making the implementation sufficiently precise to build and review independently.

DESIGN: SOUND WITH CHANGES