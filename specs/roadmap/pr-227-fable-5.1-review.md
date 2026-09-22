# Independent Fable 5.1 review: PR #227 cache/pipeline assessment and proposed architecture

Reviewed head `97f3e990`, PR base `a71c9470`. Read-only review of source, saved measurements and existing tests. No probes or benchmarks were re-executed; no repository state was changed.

## Verdict

**The prior review's defect findings are sound. Its architecture plan is right in direction but over-built in three places and under-specified in the one place that matters most.** As written, the plan would produce a good system, not one of the absolute highest standard, because it adds machinery (a catalog spike, a second-engine prototype, typed plan capabilities, a host-wide resource manager) where the highest-standard answer is to remove machinery and make two measurements.

- R1–R8 all reproduce in source. My severity differs on R1 (lower: the production worker topology already protects it; only independent processes are exposed), R4 (lower: brief mislabel window, small fix) and R5/R6 (trivial). R2, R3 and R8 are the substantive findings.
- R2 and R3 each have a small, local fix. The design's "single commit authority / recoverable journal / SQLite catalog" response to R2 is disproportionate: the store already defines the recoverable state it needs, and a write-order change reaches it.
- The central premise in the code (`src/haute/_chunked_writes.py:3-6`: the native streaming sink is unbounded and only a driver loop is bounded) is overstated by the repository's own perf artifact, which shows the native sink growing about 1.6–2x for a 4x row step and the "bounded" sliced path growing 1.3–1.44x on the same step. Neither is flat; neither is proportional. The plan needs a decoded-byte model, not more row-count loops.
- The join decision should be concrete and mostly native: admit the build side by a decoded-size estimate from Parquet metadata and let Polars build it once; fall back to one physical hash-partition pass when both sides exceed the budget. The per-chunk semi-join rescan should be a fallback at most. DuckDB is not needed to get there.
- Merge recommendation: **request changes**, but the changes required *for this PR* are small (R2–R6 plus the CI failures). The architecture work is follow-up and must not block the PR.

## R1–R8 adjudication

| ID | Prior | Mine | Class | Key evidence |
|---|---|---|---|---|
| R1 | P1 | **Valid, P2** | Inherited, *specified* weakness: `specs/io-layer/low-level.md:65-70` documents process-local input leases, the 1800 s grace, and that clear reclaims immediately | `src/haute/_source_cache.py:1025-1039` (process-local counter), `:1106-1122`, `:1187-1190`; `src/haute/_node_snapshots.py:1439-1443` and `:1466-1473` delegate inputs to the base. Production exposure is narrower than the probe: the parent keeps its plan and leases open while the isolated worker runs (`src/haute/routes/_training_lifecycle.py:1604-1606`, `src/haute/routes/_node_data_service.py:1368-1378`), and Clear runs in that parent (`src/haute/routes/input_cache.py:822-844`), where `_retire_unleased(force=True)` skips leased generations. The defect needs two *independent* processes on one project root (a CLI run beside `haute serve`, or two servers). |
| R2 | P2 | **Valid, P2, confirmed by reading** | Introduced | Pointer written at `_node_snapshots.py:1828`; slot index written at `:1844` in a second try block; the handler at `:1855-1859` releases the lease, but `_release_node_lease` retires only non-current generations (`:1421-1424`), so a pointed, un-indexed generation survives. `clear_slot` walks only the index (`:1604-1609`). The same window exists for a process killed between `:1807` and `:1844`. |
| R3 | P2 | **Valid, P2 (P3 in practice)** | Introduced | `_admit_node_output_locked` credits only the same identity (`:1895-1902`); the old signature's current pinned generation is skipped as an eviction candidate (`:2007-2014`); the retirement that would free it runs after admission (`:1843`). With 512 default generations, the practical trigger is the byte quota: a node output above half of the 40 GiB budget, or a nearly full store. |
| R4 | P2 | **Valid, P3** | Introduced | `frontend/src/panels/editors/banding/useBandingStats.ts:153-154` gates on `data_version` only, not on `askedFor`; cleanup at `:144` clears the timer but does not abort; the abort happens only when the next timer fires (`:106`). Same shape in `useRatingLevels.ts:135-136` and `:126`. Effect: for one debounce window plus request latency, old counts are shown with basis `all` for the edited rules. |
| R5 | P2 | **Valid, P3** | Introduced | `_MATCHES_COLUMN` (`_chunked_writes.py:61`) used as a `len(name=...)` beside the key columns at `:928`, `:937`, `:1046`; the index column already has the collision loop the fix needs (`:994-1003`). |
| R6 | P2 | **Valid, P3** | Introduced | `part_name` formats `:05d` (`:75`), `is_part_name` demands exactly five digits (`:83`), `part_paths` sorts lexically (`:90-93`). At 500,000 rows per part, part 100,000 is 50 billion rows; reachable only with tiny configured chunks or heavy-row windows. The fix is numeric parsing and numeric sort, not a manifest. |
| R7 | P1 gap | **Valid as a requirement gap; the framing needs correcting** | Mixed: cross-join collect and validation rescans are specified (`specs/roadmap/caching.md:472-512`, CACHE-S18); the fixed row chunk and native fallbacks are design limits; "native is unbounded" is overstated | `_chunked_writes.py:797-806` (whole lookup collected), `src/haute/_polars_utils.py:224-225` (whole in-memory plan collected before slicing), `_polars_utils.py:32` (500,000-row default), `src/haute/_frame_profile.py:539`, `:554-559`, `:599-602` (exact `n_unique`, quantiles, struct `n_unique`), `:423-430` (`value_counts` then `head(50)`). Perf artifact `tests/performance/test_write_strategy_memory.py:175-190` records native 1.97–2.35x and sliced 1.30–1.44x over a 4x row step; CI observed native 1.597x (361 → 577 MB). |
| R8 | P2 perf | **Valid, the most important performance finding** | Introduced | `_ChunkJoin._matches` (`:897-904`) runs a semi-join of the whole lookup per driving chunk; `write` issues it for the probe (`:921`) and again for counts on overflow (`:934-938`); `_collect` documents the full scan (`:892-895`). In addition, `_staged` (`:731-745`) writes any non-sliceable input to disk in full before the join starts (`:780-781`). Benchmark: 4–6x slower at 400,000 rows on the most favourable path (unique keys both sides, so the `:923` shortcut always fires). |

The probes are honest about being internal-API reproductions. The real production callers are `src/haute/_execute_lazy.py:2429-2438` (captures, `chunk_rows=current_streaming_chunk_size()`), `routes/_node_data_service.py:328-336` (explicit builds), `routes/_training_preparation.py:876-883` (training input via `write_file`), and `_polars_utils.py:228-235` (batch staging). All four reach the same writer with the same defaults, so the writer probes reflect real callers. The R1 probe's two-independent-process topology does *not* reflect the supervised-worker caller, which is why I lower it.

## Confirmed defects, with the smallest correct fix

**R2, publish order.** The store's own docstring (`_node_snapshots.py:1583-1589`) defines "indexed and unpointed" as a legal, recoverable state that the next publish or `clear_slot` retries. Reach it by construction. Under the lease lock: (1) add the identity to the slot index and write it; (2) rename staging to the generation directory; (3) write the pointer; (4) update the pin, retire superseded signatures, write the index again. A failure after (1) leaves an indexed identity with no generation, or an indexed unpointed generation, both of which `clear_slot` and `_retire_non_current_locked` already reclaim. A failure after (3) leaves a correct current generation whose pin transfer and superseded retirement are idempotent on the next publish. No journal, no catalog. Add a fault-point test at each step, in the pattern already used at `:1777`, `:1795`, `:1930`.

**R3, admission credit.** In `_admit_node_output_locked`, after the same-identity credit, also credit every other signature of the slot whose current generation has no live holders when this publication will retire them (explicit build, or a pin already exists), because `_retire_superseded_signatures_locked` does exactly that a few lines later. Keep the old generation visible until the new pointer is written, which the order above guarantees.

**R1, one lease protocol.** Agree with the direction, disagree with "design a protocol". The protocol exists: `_acquire_node_lease` / `_release_node_lease` with token files whose liveness is a held file lock (`_node_snapshots.py:1086-1128`, `:1131-1160`). Move marker acquire/release into the base `SourceCacheStore` for every provider and delete the process-local counter path and the grace timer (`_source_cache.py:548-550`). Acceptance is the two-process test the prior review lists, which `tests/test_node_snapshot_cross_process.py:176-341` already models for node outputs.

**R4, request key.** Store `{askedFor, dataVersion, response}` together, expose `stats` only when both match the current values, and abort the in-flight controller in the effect cleanup rather than at the next timer. The document fence does not need to join the key because it is already checked on receipt (`useBandingStats.ts:125`, `:130`).

**R5 and R6.** Reuse the `_resolve_index` collision loop for the matches column; parse the digit run as an integer and sort numerically in `part_paths`, accepting five or more digits and rejecting anything else. One unit test each.

## Assessment of the proposed approach

### Keep

Immutable projected Parquet generations; semantic identities and column demand; leases owned by the seed plan; request-owned staging tokens; write-time digests; the recipe/classifier approach to row-local work (CACHE-S22's 20-Sep measurement at `caching.md:360-367` showed every refusal is genuinely global, so the classifier is doing its job); the training allocation reordering (validation load at `src/haute/modelling/_training_job.py:1913-1920` currently overlaps the train Pool build at `:1963-1981`); the optimiser estimate (the installed `price_contour` documents the grid as `O(rows × columns × 4)` resident, confirmed at `.venv/Lib/site-packages/price_contour/__init__.py:120-127`); absolute-envelope perf gates instead of ratio-versus-native assertions.

### Change

**1. Replace the row chunk with a decoded-byte estimate, and make it the one mechanism.** Every generation and every staged input is Parquet. Parquet column-chunk metadata carries the uncompressed size per column per row group, so decoded bytes per row for any scan is a metadata read, not a guess. From it derive: chunk rows for a sliced write (budget divided by bytes per row), whether a join's build side fits its admitted budget, and whether a group-by's expected state fits. This replaces the fixed default at `_polars_utils.py:32` and answers the design's own objection that a 500,000-row batch says nothing reliable about decoded bytes. Non-Parquet inputs (API tables, Python scans) already fall back to a staged write (`_staged`); read the metadata of what was written.

**2. Make the join rule concrete, and mostly native.** With the estimate in hand:

- Build side (the smaller side, swapping where the join type permits) fits the budget: run the native streaming join once and sink to parts. The lookup is resident once, not rescanned per chunk. For a left join with the small table on the right this is already the ideal resident set, and it is the overwhelmingly common pricing shape (policies against rating and reference tables), where the current code spends 4–6x.
- Neither side fits: one physical hash-partition pass per side into K bucket files, then K native joins of matching buckets. CACHE-S18 already plans this for full joins (`caching.md:492-512`); it generalises to every join type and to uniqueness validation. Build it with Polars' own partitioned sink if 1.44.2 supports partition-by-key sinks (hypothesis, check the installed API), otherwise with K filtered passes, which is worse but still O(K × input) rather than O(chunks × lookup).
- Cross join: tile both sides in slices, never collect (`:797` today). S18 already says this.
- The per-chunk semi-join path becomes a fallback for a build side just over budget, or is deleted. It should not be the universal low-memory path.

**3. Fix publication order instead of adding a catalog.** See R2. Reject the SQLite spike and the recovery journal for now. Both add a second source of truth beside the filesystem that then needs its own reconciliation with renames and deletions, which is the very problem being solved.

**4. Reject the second execution engine.** DuckDB would add a runtime, an Arrow conversion boundary with its own dtype semantics (categoricals, decimals, nested types, null ordering), and packaging for the hosted container, to solve a problem one hash-partition pass in Polars solves. Revisit only if E4 below fails.

**5. Do not carry typed capabilities from graph construction.** The classifier already yields row-locality. The remaining fragility is the IR-version guard (`_SUPPORTED_IR_MAJOR = 14` at `_chunked_writes.py:54`, checked at `:115` and `:147`), which silently turns every sliced write into a native one after a Polars upgrade. The right control is a contract test asserting `sliceable()` is true for a canonical Parquet scan and false for a canonical sort, so an upgrade fails CI rather than degrading silently. Cheaper and more direct than a capability system.

**6. Scope down the resource manager.** Host-wide admission across preview, build, training and optimiser is real but later. The immediate need is per-job scratch reservation before a large write and a byte budget per writer, both of which fall out of item 1.

### Defer

CACHE-S19 and CACHE-S17's store summary are correctly evidence-gated already. The profile's exact-versus-approximate contract (design §5) is right but unmeasured; record which exact statistics actually dominate profile memory before designing modes.

## Issues the prior review missed or understated

1. **The "bounded" path is not flat either.** The perf artifact's own numbers (`test_write_strategy_memory.py:175-190`) show the sliced strategy growing 1.30–1.44x over a 4x row step at a fixed chunk size. If slicing bounded memory, that ratio would be about 1.0. Either the slice is not fully pushed into the scan and each slice decodes more than its rows, or read-ahead scales with file size. Nobody has separated these. This is the most important unknown, because the whole chunked writer rests on the claim that slicing bounds memory. E2 below is decisive and cheap.

2. **Write amplification in `_staged`.** Any edge join whose inputs are not a plain scan (a filter upstream, a preceding uncaptured join, an API table) writes both inputs to disk in full before the first driving chunk is read (`:780-781`), then rescans the lookup copy per chunk. A chain of N joins over filtered inputs pays N full intermediate writes beyond the captures the cache policy chose to keep. The native path pays none of this. This belongs in the join decision.

3. **Cross-process input build is unlocked.** `SourceCacheStore.build` serialises on a process-local lock (`_source_cache.py:917-918`); the route's 409 guard uses a process-local single-flight (`input_cache.py:831-835`). Two independent processes can build the same input concurrently and both publish. Harmless in effect, but the same family as R1 and fixed by the same protocol.

4. **Windows retirement deferral has no retry trigger.** `_retire_generation_locked` logs and leaves a generation in place when a handle blocks the rename (`_node_snapshots.py:1492-1499`), "retried later". Later means the next publication or clear on that identity; nothing sweeps it otherwise. Low priority; note it in the lifetime protocol.

5. **The CI perf assertion is inverted.** `assert ratio >= 1.6` on the native case (`test_write_strategy_memory.py:175-182`) requires native Polars to *stay bad*. The 1.597 failure is a Polars improvement or noise, not a regression. Replace it with an absolute envelope on the bounded case and record the native number without gating. The artifact's headline claim should be re-measured at 16N before it is cited again.

6. **Join semantics not yet differentially tested in the chunked path.** `tests/test_chunked_writes.py` covers equality, validate, order, hot keys and cross joins (44 tests), but I found no case with null keys on both sides, `coalesce=False` with same-named keys, or dtype-mismatched keys. Null handling is consistent by construction today (`src/haute/_edge_join.py` never passes `nulls_equal`, so semi-join and final join agree). If the join rule changes as proposed, a differential suite with nulls, duplicates, empty sides, skew and unmatched outer rows is a precondition. The design lists these; they are not in the repository yet.

## Benchmark validity and the decisive experiments

The saved benchmark is honest and small. Its limits beyond those stated: keys are unique on both sides, so it exercises only the `:923` shortcut, the chunked join's best case; both sizes fit in memory, so it measures overhead, not the memory behaviour the strategy exists for; the child peak includes verification; two repetitions. It supports "the chunked join is several times slower on ordinary small joins" and nothing else. It does not support the design's adaptive/spillable table one way or the other.

Smallest decisive set, in order:

| # | Question | Design | Decides |
|---|---|---|---|
| E1 | Does the native streaming sink's peak grow with the build side or with both sides? | Left join, driving 20M rows fixed, lookup 1M / 4M / 16M rows × 10 columns, fixed threads, fresh child, peak RSS | The join rule's admission model and the per-row constant for the byte estimate |
| E2 | Is the sliced path actually flat? | Existing `unnest` probe at N / 4N / 16N with fixed `chunk_rows`; record one slice's optimised `explain()` to confirm the slice sits inside the scan | Whether the chunked writer bounds memory at all, and if not, why |
| E3 | What does the chunked join cost on realistic keys? | m:1 join, driving 4M / 16M, lookup 1M, Zipf-skewed keys, versus native; time and peak | Whether the semi-join path survives as a fallback or is removed |
| E4 | Can Polars 1.44.2 hash-partition to files in one pass with bounded memory? | One `sink_parquet` with a partition-by-key sink on `hash(keys) % K` at 16M rows, K = 64; if absent, K filtered passes | Whether the Grace path needs anything beyond Polars |
| E5 | Store correctness | Two-process lease-vs-clear on an input identity; fault points at each publish step; replacement at an exact byte quota | R1, R2, R3 become ordinary regression tests |

Each runs in minutes locally. This host has about 10 GB of commit headroom; scale E1 and E4 down by 4x if needed rather than running them beside the suite.

The proposed acceptance gates (design §7–8) are the right kind. They prove the user's requirement only if the envelope is stated in bytes per admitted job and the largest point actually exceeds the budget so that spill or partitioning is forced. Gates at N and 4N that both fit in memory prove nothing about the bound.

## Prioritised recommendation

**Before merging PR #227 (small, do now):**

1. R2 publish-order fix with fault-point tests.
2. R3 admission credit for the slot's other signatures.
3. R4 request key and abort-on-cleanup in both hooks; the three probes become tests.
4. R5 and R6 with one test each.
5. The CI failures: sandbox-lint writes in `tests/test_cache_nodes_routes.py`, the `W10-S02` ledger title, the 0.8 KiB bundle overage, and the two browser failures, whose artifacts must be read before deciding whether the screenshot change is intended.
6. Replace the inverted perf assertion with an absolute envelope.
7. Correct the roadmap text claiming all captures are bounded and only full/cross joins rescan (`caching.md:58`, S18 title).

**Follow-up A (measurement, one or two days):** E1–E4. No join or writer design decision before E1 and E2 exist.

**Follow-up B (lifetime protocol):** R1 by moving marker leases into the base store; remove the grace timer; cross-process input build lock; extend the cross-process tests to input identities.

**Follow-up C (byte model and join rule):** Parquet-metadata decoded-size estimate; chunk rows from bytes; native join with admitted build side; cross-join tiling; Grace partition path if E4 passes; differential join suite. Retire or demote the per-chunk semi-join.

**Follow-up D (consumer copies):** validation-after-Pool ordering; a dataset handle so `_sink_to_temp` (`src/haute/_model_scorer.py:1116`) and the optimiser's temporary sink (`src/haute/routes/_optimiser_service.py:5197-5201`) can read an already-leased generation where the library's single-file contract allows.

**Defer:** SQLite catalog, DuckDB, typed capabilities, host-wide resource manager, CACHE-S19, profile accuracy modes.

## What stays unverified

- Polars 1.44.2's actual streaming-join memory behaviour and whether its streaming engine spills any operator. I did not run it; the design is right that upstream documentation does not certify this pinned runtime.
- Whether a partition-by-key sink exists and is bounded on the installed version (E4).
- Why the sliced path grows 1.3–1.44x (E2).
- The browser E2E failures' cause; artifacts were not inspected.
- Production-scale peaks, multi-process deployments, network filesystems.
- I did not re-run the probes or the benchmark; the adjudication rests on reading the cited code paths and the saved JSON and CI evidence.

Note for the parent: the Write tool is disabled in this session, so this report could not be saved to the plan file. Save this message verbatim under `specs/roadmap` as intended.