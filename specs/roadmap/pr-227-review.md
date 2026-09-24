# PR #227: cache and pipeline review

Review date: 22 September 2026. Recommendation: **request changes**.

PR: [Share one node-output cache across every consumer, and bound every write](https://github.com/PricingFrontier/haute/pull/227).
Reviewed head: `97f3e9909998460bae5dbdfedf821eb9189581a6`.
Actual PR base: `a71c947000e46f3b9331e96ec1a034eaa3739c97`.
The local `main` reference is older; it is not the comparison baseline.

The shared immutable Parquet generations, demand-aware seed plans, projection,
write-time digests, and explicit ownership of temporary artifacts are sound
foundations. Keep them. However, the current implementation does not yet meet
the requested contract of fast execution with small RAM usage outside minimal
modelling/optimisation state. There are reproducible lifecycle and UI defects,
and the custom join strategy exchanges memory pressure for repeated lookup
work even on ordinary left joins.

This review covers the PR and the surrounding cache/pipeline architecture.
It distinguishes new defects, inherited system weaknesses, and design proposals.
The companion [focused implementation plan](pipeline-cache-memory-design.md)
defines the current work and acceptance criteria. At the user's request,
recommendations have been narrowed to targeted improvements in the existing
Polars pipeline and filesystem cache. The findings remain review evidence;
the proposed fixes have not been implemented.

## Scope and evidence

The PR contains 327 changed paths: 63 backend paths, 108 frontend paths,
116 test/support paths, 39 spec/documentation paths, and one other path.
Review concentrated on cache identity, publication, retention, leases,
invalidation, seed planning, writer strategies, joins, profiling, UI consumers,
training preparation and allocations, model scoring, and optimiser input/grid
ownership. Adjacent changed surfaces were inventoried; this is not a claim of
exhaustive line-by-line review of all 58,085 added lines or all browser flows.

The root performed review judgments, architecture analysis and probe design.
Two Luna workers collected independent cache and materialisation inventories,
ran predefined verification batches, and collected benchmark/CI evidence.
Their inventories are evidence supplements, not independent approvals:

- [Cache inventory and CI evidence](pr-227-cache-evidence.md).
- [Materialisation inventory and join measurements](pr-227-materialisation-evidence.md).
- [Executable backend review probes](../../scripts/benchmarks/pr-227-review-probes.py).
- [Frontend regression probes](../../scripts/benchmarks/pr-227-frontend-probes.test.tsx).
- [Benchmark harness](../../scripts/benchmarks/pr-227-join-benchmark.py) and [raw results](../../scripts/benchmarks/pr-227-join-benchmark.json).

Production code has not been changed as part of this review.

## Findings

Priorities describe impact: P1 needs prompt resolution for the stated system
contract; P2 is a material correctness, robustness or performance issue.
Review IDs below are local to this assessment, not new component package IDs.

| ID | Priority | Finding | Origin / evidence |
|---|---|---|---|
| R1 | P1 | An input generation can be deleted while another process holds its lease | Inherited input-store weakness, still exposed through the new shared store; reproduced |
| R2 | P2 | A failed slot-index write leaves a published generation that slot Clear cannot remove | New publication path; fault-injection reproduction |
| R3 | P2 | Replacing a pinned slot with a changed signature can fail at a full quota even when the old generation is unleased | New admission/slot replacement interaction; reproduced |
| R4 | P2 | Banding counts and rating levels remain labelled current after their analysis question changes | New frontend hooks; three failing regression probes |
| R5 | P2 | A valid join key collides with the internal match-count column | New chunked join; reproduced against native semantics |
| R6 | P2 | Part 100,000 is writable but not discoverable/validatable | New multipart naming contract; boundary probe |
| R7 | P1 requirement gap | Cross joins, some batch paths, global analyses and native fallbacks have no strict memory bound | Code-confirmed, with cross-join/batch materialisation probes; some intentionally supported by existing specs |
| R8 | P2 performance | Ordinary chunked joins repeatedly scan the lookup, not just full joins | Code/plan evidence and small controlled measurements |

### R1 — apply cross-process lease protection to input snapshots too

At [`_node_snapshots.py:1439`](../../src/haute/_node_snapshots.py#L1439)
and `:1467`, non-node-output identities delegate to the parent lease methods.
[`_source_cache.py:1025`](../../src/haute/_source_cache.py#L1025) records those
leases only in process-local state. At `:1106`, retirement consults that local
counter; `clear` at `:1187` forces retirement immediately.

Reproduction: process A builds and leases a three-row `provider="file"`
generation; process B opens the same root and clears the same identity.
While A's context is still open, its generation directory no longer exists and
collecting its lazy scan raises `FileNotFoundError`. This does not require a
reader to outlive its lease or an already-open Windows file handle.

The node-output implementation's process tokens do not protect these input
generations. Training/build worker handoffs and source clearing therefore do
not share one reliable lifetime contract. A grace period is insufficient:
Clear and quota-pressure retirement can bypass it.

**Required change:** use one cross-process acquisition/retirement protocol for
input and node generations. Clear should make a generation unselectable
immediately, while physical deletion waits for every live holder. Preserve
exact-generation acquisition during worker handoff. This underlying defect
predates the PR; it remains a high-priority system finding, not a newly
introduced source-cache regression.

**Acceptance:** two-process tests for lease versus Clear, quota eviction,
replacement, worker death and parent/child handoff. An active reader completes;
the last release or dead-owner recovery eventually reclaims the files.

### R2 — commit the pointer and slot index as one recoverable transition

[`_node_snapshots.py:1828`](../../src/haute/_node_snapshots.py#L1828) writes
the current pointer before the slot-index update. The later exception handler
releases the publisher lease but leaves that pointer committed.
[`clear_slot:1600`](../../src/haute/_node_snapshots.py#L1600) discovers
identities only through the slot index. The node-data Clear service calls this
method at [`_node_data_service.py:1137`](../../src/haute/routes/_node_data_service.py#L1137).

Fault-injection reproduction: make `_write_slot_index_locked` raise `OSError`
on the first publication. Build reports failure. Restore normal I/O and call
`clear_slot`; leasing the identity still returns its one row. The generation
is readable but absent from the slot's clearing authority. Disk exhaustion,
interrupted metadata writes or process termination in the same interval need
a defined recovery rule.

**Required change:** correct write ordering and failure handling within the
existing index/pointer layout. Keep every published or abandoned generation
discoverable, using the existing indexed-but-unpointed recovery state. Define
the visibility point and the pin/retention outcome on failure. Clear and
recovery must also find generations left by an interrupted process.

**Acceptance:** inject failure/process death around each metadata transition;
after recovery, a generation is either committed and indexed, or not selectable
and reclaimable. Clear always finds every generation of the slot, and failed
replacement does not discard the previous usable generation.

### R3 — credit the unleased generation being replaced across signatures

[`_admit_node_output_locked:1880`](../../src/haute/_node_snapshots.py#L1880)
subtracts a superseded generation only under the same identity. A configuration
edit changes the identity digest. The old pinned signature is not an eviction
candidate, and retirement of that signature happens only after admission.

Reproduction: set `node_output_max_generations=1`; publish an explicit pinned
generation for a slot and close its lease; publish another signature for the
same slot. It raises `NodeSnapshotQuotaRejectedError`, although the documented
slot replacement would retire the old, unleased generation and end with one.
The analogous issue applies to tight byte quotas. A user must clear useful
data before attempting a replacement that should fit.

**Required change:** compute admission against the slot's post-commit retained
state, crediting only superseded generations that are genuinely reclaimable.
Keep the old generation visible until a replacement safely commits. Account
separately for temporary disk headroom; retention credit does not mean the
staging write requires no free space.

**Acceptance:** same/different signature replacement at exact count and byte
limits; pinned and automatic slots; live old-generation lease; publication
failure with the old generation still readable.

### R4 — analysis identity includes the question, not only the data version

[`useBandingStats.ts:153`](../../frontend/src/panels/editors/banding/useBandingStats.ts#L153)
checks availability, response status and `data_version`. It does not check
the factor/rules/bins that produced the answer. The equivalent check in
[`useRatingLevels.ts:135`](../../frontend/src/panels/editors/rating/useRatingLevels.ts#L135)
does not check the requested columns. Both effect cleanups clear the debounce
timer without immediately aborting an already-running request; abortion occurs
when the next timer fires.

Three regression probes fail at the reviewed head:

1. Change a band boundary from 50 to 90 while retaining the same upstream data
   generation: the old `[750, 250]` rule counts are still exposed as current.
2. Resolve the old request during the next 250 ms debounce window: its old
   counts are accepted for the edited factor.
3. Change rating columns from `region` to `occupation`: old region levels
   remain attached to an `all` basis.

Keeping a previous answer internally is reasonable, but attributing it to
the new question is not. A document execution fence cannot distinguish two
analysis questions against the same current input.

**Required change:** attach a complete request key to each answer: document
fence, consumer/point identity, source, data version, canonical question.
Expose the answer only when that key matches the current key. Supersede the
request immediately on key change, before debounce; gate errors and loading
state with the same request generation.

**Acceptance:** the three probes, plus out-of-order replies for unchanged data,
factor removal, source/node change and unmount. No old answer is presented as
the new full-data result.

### R5 — generate collision-free internal join columns

[`_chunked_writes.py:928`](../../src/haute/_chunked_writes.py#L928) and `:937`
name a grouped match-count column `__haute_chunk_matches`. That name is valid
for a user join key, so a group key and its count can have the same name.

Reproduction: two duplicate rows per side, join on `__haute_chunk_matches`,
chunk size two. Native Polars returns four rows; the chunked writer raises
`DuplicateError`. The uniqueness-validation path also uses the constant.

**Required change:** allocate temporary names against both input schemas and
all generated names, and pass the selected name through counting/validation.
Reuse the collision-avoidance approach already used for the row-index column.
Do not introduce an undocumented reserved user-column namespace.

### R6 — make part naming and discovery agree beyond five digits

[`part_name:71`](../../src/haute/_chunked_writes.py#L71) formats an index with
a minimum width of five; `is_part_name` at `:78` accepts exactly five digits.
`part-100000.parquet` can therefore be written but is ignored by discovery.
Metadata validation also rejects it.

The probe creates parts 99,999 and 100,000: discovery sees one of the two rows.
**Qualification:** normal cache writers record every digest, so
[`describe_parts:280`](../../src/haute/_source_cache.py#L280) rejects the
unknown recorded part instead of silently publishing truncated data. The
normal production failure is a late failed publication after the expensive
write. Direct discovery without recorded digests silently omits the file.

Small configured chunks, large outputs and join expansion make the boundary
reachable independently of the default 500,000-row chunk setting.

**Required change:** make naming, parsing, metadata validation and numeric
sorting agree for nonnegative part indices. Simply relaxing the digit count
while retaining lexical sorting gives the wrong order around this boundary.
Reject unexpected/malformed parts explicitly.

**Acceptance:** names/discovery/metadata/order at indices 0, 99,999, 100,000
and 100,001, including missing or unexpected parts. The naming boundary can
be tested without generating 100,001 real data files.

### R7 — a small returned batch does not prove bounded execution memory

These are code-confirmed limits of the current approach, rather than claims
that every native operation necessarily runs out of memory:

| Path | Where the bound is lost | Consequence |
|---|---|---|
| Cross join | [`_chunked_writes.py:797`](../../src/haute/_chunked_writes.py#L797) collects the entire lookup | Full resident data outside modelling/optimisation; a lookup larger than the row chunk also produces oversized output parts |
| Resident-source batch path | [`_polars_utils.py:224`](../../src/haute/_polars_utils.py#L224) collects the whole derived plan before yielding slices | Two small resident inputs can expand into a much larger join/explode output |
| Unsupported/non-sliceable writer | [`write_parts:472`](../../src/haute/_chunked_writes.py#L472), `:499`, `:523`, `:537`, plus `write_file` | Native streaming sink is used without proving that its operator state fits the budget |
| Profiling | [`_frame_profile.py:539`](../../src/haute/_frame_profile.py#L539), `:554`, `:559`, `:600`; categorical counts at `:423` | Exact distinct, quantiles and duplicate-row state can scale with data; a top-K response limit does not bound the upstream frequency table |
| Fixed row chunk size | Default 500,000 rows and per-write row limits | Wide strings, nested values, temporary arrays and fan-out invalidate a fixed byte/RAM interpretation |

The additional materialisation probe requests ten-row cross-join chunks with
an 80-row lookup: it observes an 80-row eager lookup collection and 80-row
output parts. The writer correctly reports `chunk_rows=None`, so it is not
claiming a ten-row output bound for this case. A separate 20-by-30 cross join
over resident inputs goes through `bounded_collect_batches(chunk_size=10)`:
the helper collects all 600 derived rows before returning its first ten-row
batch. These small probes demonstrate the mechanism without forcing an OOM.

The implementation already reports native reasons and documents some of these
boundaries. Existing specs allow more full-input work than the user's stronger
requirement. Update that contract explicitly; do not call all of these new
regressions or promise that changing `engine="streaming"` fixes them.

**Required direction:** classify and budget operator state, use byte-aware
backpressure, spill supported blocking operations, and reject unsupported
strict-budget execution with an actionable error. Modelling and optimisation
get explicit, preflighted resident-state exceptions. Exact large analyses need
spill or an explicitly chosen approximate mode, not silent approximation.

### R8 — ordinary left/inner joins also rescan their lookup

[`_ChunkJoin._matches:897`](../../src/haute/_chunked_writes.py#L897) builds
a new lookup-side semi-join for each driving chunk. `_collect` explicitly
describes the full lookup scan. Probing for overflow and then counting/splitting
can add further work. Full joins add the reverse-side pass; they are not the
only affected join type.

With a fixed lookup Parquet file, 20/40/80 driving rows at ten rows per chunk
produce 2/4/8 lookup scan plans. Each small result was compared to native
Polars with full frame equality. These are logical scan counts, not measured
physical disk reads: pruning and the OS page cache can reduce physical I/O.
For unclustered keys with little pruning, work tends toward
`ceil(driving_rows / chunk_rows) * lookup_rows`, plus output and skew handling.
Shrinking chunks to reduce RAM therefore increases repeated work.

The controlled benchmark uses shuffled unique Int64 keys, four Float64 payloads
per side, Parquet row groups/chunks of 25,000, two repetitions and fresh Python
children. Both paths use the same imported helpers and compression settings.

| Rows per side | Native write seconds | Chunked join write seconds | Native child peak MiB | Chunked child peak MiB |
|---|---:|---:|---:|---:|
| 100,000 | 0.039 / 0.028 | 0.105 / 0.082 | 140.6 / 140.7 | 141.4 / 139.0 |
| 400,000 | 0.061 / 0.076 | 0.364 / 0.330 | 228.0 / 227.9 | 221.1 / 207.9 |

At 400,000 rows the custom write is roughly 4–6 times slower, with only a
modest observed child-process peak reduction. Peak sampling is every 5 ms and
includes post-write verification, so these are not isolated write-only peaks.
The timed interval excludes imports and verification. Windows venv-launcher
RSS was avoided by measuring the real base-interpreter child. Counts and
column sums pass; those checks alone do not establish per-row equivalence.
These two small cases fit in memory and do not certify large-data performance,
asymptotic memory use or the best strategy for skewed/many-to-many joins.

**Required direction:** measure native Polars and the current writer under a
byte budget, including their staging cost. Use one native execution where it
meets the budget and improves runtime; target other repeated work within the
existing writer according to those measurements.
Extend `CACHE-S18` to ordinary joins, uniqueness validation and skew windows;
its current wording identifies only full/cross joins.

## Further system-level improvements

These are targeted improvement areas and known limitations, not additional
reproduced regressions. The companion plan defines the current delivery scope.

| Area | Current strength | Remaining issue / preferred next step |
|---|---|---|
| Identity and reuse | Semantic signatures, column coverage, immutable generations and source checks | Reuse existing leased generations through current consumer interfaces where this removes a redundant write |
| Admission | Node-output and input retention budgets, pinned/leased protection | Quota is checked after writing; reserve scratch headroom and account for simultaneous staging, final artifacts and failed writers |
| Cold concurrency | Publication locks choose a winner and preserve each writer's own result | They do not prevent duplicate computation; existing deferred `CACHE-S19` remains separate from this PR's fixes |
| Capture choice | Static operation-cost classes skip plain transforms | This is not measured recompute-vs-I/O cost; do not automatically serialize every small join or claim runtime cost optimality |
| Reader lifetime | SeedPlan ownership is explicit and cleanly scoped | Long plans retain generations/artifacts until plan close; release at last consumer use where handoff/reuse permits |
| Model scoring | Batches, feature projection, direct scored-output artifact and inline digests | `_sink_to_temp` still writes a full projected input at `_model_scorer.py:1116`; let supported consumers iterate already-leased parts |
| Training | Projected prepared Parquet, Float32 numeric CatBoost features, early temporary deletion | `_training_job.py:1901–1917` loads both train and validation before constructing the train Pool; delay validation materialisation to reduce overlap |
| Optimisation | Required-column projection, finite/range checks, Float32 values and chunked grid ingestion | The final QuoteGrid still holds all solver vectors; budget this explicitly and reuse one grid for compatible frontier work |
| Profiling and analysis | Shared data points and persisted answers avoid repeated graph work | Measure expensive exact statistics and answer sizes; fix demonstrated memory pressure while preserving existing accuracy semantics |
| Plan maintenance | Typed WriteRecipe/JoinRecipe improves on arbitrary collect calls | Add focused contract tests for the Polars plan shapes the existing recipe/classifier implementation recognises |

Do not propose “convert to Float32”, “remove pandas everywhere”, “stream the
training input”, or “use the chunked optimiser API” as if none exists. Much of
that work is already present. MLflow pyfunc feature names/dtypes must remain
correct; CatBoost numeric conversion policy cannot be applied indiscriminately.

## Verification and limits

Existing targeted tests passed:

- **253 backend tests** for node retention/cross-process ownership and seeding
  across preview, training and optimisation: 135.13 s.
- **275 backend tests** for chunked writes, bounded sinks, Polars helpers,
  output seeding, training memory/splitting and optimiser frontier
  materialisation: 84.99 s.
- **124 frontend tests in seven files** for shared node-data cache/store,
  invalidation, analysis editors and cache settings: 32.26 s.

Total: **528 existing backend and 124 existing frontend tests passed**.
Commands and warning counts are recorded in the evidence inventories. This
does not override the new probes or failing CI checks.

The backend review probe exits successfully after recording the defective
observations; its exit code is not a correctness pass. Run it with:

```text
uv run python scripts/benchmarks/pr-227-review-probes.py
```

The frontend review source lives outside the normal suite. To reproduce,
temporarily copy it to
`frontend/src/hooks/__tests__/pr-227-review-probes.test.tsx`, run the command
below, then remove only that temporary copy. Do not overwrite an existing file.
All three assertions currently fail; no production fix is included.

```text
npm --prefix frontend test -- src/hooks/__tests__/pr-227-review-probes.test.tsx
```

The benchmark can be rerun with:

```text
uv run python scripts/benchmarks/pr-227-join-benchmark.py
```

It replaces its review JSON artifact, uses a private temporary workspace, and
removes generated Parquet fixtures afterward. External child RSS includes
verification; the parent harness's own `process_peak_rss_bytes` is not the
strategy's memory measurement.

GitHub CI on the reviewed head is **not green**. The perf job includes a failing
native-passthrough growth-ratio assertion: observed 1.5974555 against a required
ratio greater than 1.6 (approximately 361.4 MB to 577.3 MB). That assertion
requires the comparison path to remain sufficiently bad and is not, by itself,
evidence that this PR increased production memory. The latest recorded snapshot
has six failing checks, with other jobs still running/queued:

| Check(s) | Observed failure |
|---|---|
| Backend compatibility 3.11 and 3.13 | Four write-sandbox lint violations in `tests/test_cache_nodes_routes.py` |
| Backend coverage shard 2 | The same lint violations, plus a missing `W10-S02` coverage-ledger test title |
| Frontend | Initial JS gzip 292.8 KiB exceeds the 292 KiB budget; its unit tests, build and static checks passed |
| Browser E2E | Rating-journey screenshot assertion and Explore pivot-table locator timeout |
| Perf | The native-passthrough ratio assertion described above |

The sandbox lint diagnostics describe what its analysis rejected; they do not
by themselves establish an actual write outside a scratch directory. The
browser failures require their artifacts to distinguish changed intended UI
from a regression; no cause is invented here. See the timestamped
[CI evidence](pr-227-cache-evidence.md) for exact tests, excerpts and job links.
No workflows were rerun and the full CI/browser suite was not recreated locally.

Not established by this review: production-scale peak RAM, multi-terabyte
throughput, network-filesystem lock guarantees, abrupt host/power-loss
durability, model-library internal peak allocations, or correctness of every
Polars join dtype/order combination. The focused plan preserves these evidence
limits and requires targeted verification for each implemented change.

## Recommended delivery order

1. Fix R1–R6 with focused regressions and make the failing CI checks understood
   and green. R1 is inherited but belongs in the shared-store reliability work.
2. Specify the strict RAM contract and exceptions; correct the current roadmap
   claims about universal bounded writes and the limited scope of join rescans.
3. Measure native Polars and the current writer under a fixed byte budget,
   then make the smallest change that improves the affected execution path.
4. Remove redundant model/optimiser staging through existing interfaces where
   semantics allow; stagger training materialisation.
5. Extend existing memory estimation, admission and artifact cleanup where the
   measurements or reproduced failures identify a gap.

The work stays within the current Polars pipeline, filesystem cache and worker
infrastructure. The companion plan is the authority for implementation scope.
