# Cache and pipeline: focused implementation plan

Status: narrowed at the user's request, 22 September 2026.
Based on PR #227 head `97f3e9909998460bae5dbdfedf821eb9189581a6`.

**Improve the existing Polars pipeline and Parquet cache through targeted
fixes, fewer repeated scans and copies, and measured memory control.** Keep
the current store, seed plans, recipes, estimators and worker infrastructure.

This replaces the earlier architecture proposal. Alternative execution
engines, a metadata database or journal, a new capability framework, a generic
dataset abstraction and a new host-wide resource manager are removed from
this work. They are not deferred implementation steps.

The [original review](pr-227-review.md) records the findings and reproducers.
The [Fable report](pr-227-fable-5.1-review.md) is historical review evidence;
the [reconciliation](pr-227-fable-reconciliation.md) records its qualifications.
This document defines the current implementation scope.

## Required outcome

Pipeline execution should be fast and use small, controlled amounts of RAM.
Full resident representations belong only at modelling or optimisation
boundaries where the algorithm requires them, and should contain only the
necessary columns and state. Writing a full result to disk is compatible with
this requirement.

Preserve results, schema, ordering where specified, cache identity and
cancellation behavior. Update the relevant existing component specification
before changing functionality. Use the smallest existing abstraction that
can express each fix.

## 1. Fix the reproduced correctness issues

| Finding | Targeted change | Verification |
|---|---|---|
| R2: publication can leave data that Clear cannot find | Correct index/pointer write ordering and failure handling in the existing store. Ensure abandoned and current generations remain discoverable | Inject failures around the changed transitions; verify Clear, replacement, pin state and the previous usable generation |
| R3: replacing a pinned slot can be rejected at quota | Include genuinely reclaimable older signatures of the same slot in admission credit; preserve the old data until replacement commits | Replacement at exact byte/count limits, with and without an old-generation reader |
| R4: statistics can answer an old question | Associate responses with the current analysis request identity and abort superseded work immediately | Move the three review probes into the ordinary hook tests and cover out-of-order replies |
| R5: internal join-column collision | Choose an unused temporary name using the existing collision-handling pattern | Valid user columns with the internal name, including the relevant validation path |
| R6: part indices exceed the naming contract | Make naming, parsing, metadata validation and numeric ordering agree | Boundary cases around indices 99,999 and 100,000; no large fixture required |

For R2, the existing indexed-but-unpointed state is useful. Make the ordering
recoverable within the current filesystem/index layout; do not add another
metadata system. Specify the retention outcome if a pointer commits but a
later pin/index update fails.

R1 is an inherited input-lease weakness between independent processes sharing
a cache root. Supervised workers already benefit from parent-held leases.
Address the supported independent-process cases by extending the existing
node-generation marker/lock mechanism to input generations. Keep local
reference counts for nested readers and preserve the parent/child handoff.
Verify lease versus Clear, replacement and owner death with focused
cross-process tests.

Resolve the actual failing CI checks listed in the review. Inspect the browser
failure artifacts before changing expected results. Existing CI remains the
full compatibility gate.

## 2. Measure the two main execution questions

The saved small join benchmark shows repeated lookup queries and a substantial
runtime penalty. It does not establish production-scale RAM behavior. Run a
small set of controlled measurements to select changes within Polars:

| Question | Measurement | Decision it supports |
|---|---|---|
| How does native Polars join memory scale? | Vary driving and lookup sizes independently, keeping schema and threads fixed | Where one native execution avoids unnecessary repeated lookup work while fitting the budget |
| Does the sliced writer stay within its budget? | Scale input at fixed chunk settings, including wide and variable-width columns | Whether the current slicing and reader behavior need adjustment |
| How much do repeated scans and staging cost? | Compare native and current chunked paths on ordinary many-to-one and skewed joins; count temporary writes | Which specific writer branch to improve |
| Where do consumer copies determine peak RAM? | Record training Pool construction and optimiser setup boundaries | Which allocation ordering or redundant write should change first |

Use fresh workers, keep fixture generation outside the measured interval, and
measure the actual worker's native memory. Report verification separately from
write-only measurements. Start at safe sizes and include an input exceeding
the selected memory budget before claiming larger-than-memory behavior.

Record elapsed time, peak RAM, scan passes and temporary bytes together.
Choose budgets from the supported workload and available resources; do not
invent a universal number or use compressed file size as a RAM estimate.

Replace the perf assertion that requires native execution to consume more
memory by a ratio. Gate the implementation being shipped against a stated
memory envelope and meaningful runtime tolerance. A perfectly flat RSS ratio
is not required, and a small ratio alone is not proof of bounded memory.

## 3. Improve the existing Polars writer where measurements justify it

Keep the current row-local recipes and writer entry points. For a supported
join that fits the admitted memory budget, prefer executing it once when that
is faster than repeatedly querying the lookup. Account for join working state
and output expansion before choosing this path.

For larger joins, change the current writer's repeated work only where the
measurements identify a problem. Evaluate the installed Polars APIs needed
for that specific case. Preserve null behavior, duplicate multiplicity,
suffix/coalesce rules, validation and requested ordering. Use small native
Polars comparisons to verify affected join semantics.

Cross joins need bounded slices from both inputs; the current full lookup
collection does not meet the goal. Keep the change local to that writer path
and bound the produced batch as well as the input slices.

Do not treat a final small batch or `engine="streaming"` as evidence that the
upstream computation fits memory. The resident-input batch path also needs
attention when a small input can expand into a large derived result.
Use the existing execution limits and typed errors when an operation cannot
complete within its budget; retain a clear explanation of the limiting step.

Add focused contract tests for the Polars plan shapes the current sliceability
check recognises. A supported-version upgrade should reveal a changed fast
path in CI. Keep these checks in the existing recipe/classifier implementation.

## 4. Use the memory controls and ownership already present

Extend `_ram_estimate.py`, `_execution_admission.py`, execution contexts and
the existing worker limits. The parent already reserves in-flight memory;
do not duplicate that machinery.

Use estimated decoded bytes, output expansion and conversion overhead when
choosing chunk rows. Keep a row ceiling as an additional control. Include
headroom and observe actual batches because estimates are imperfect.

Parquet `total_uncompressed_size` describes encoded page data, not decoded
resident values. The reconciliation's small dictionary-encoded example reports
832 metadata bytes for 5,120,000 decoded bytes. Metadata can inform an estimate,
but cannot be the sole admission rule. Reuse the existing bounded sampling and
dtype-aware estimates.

Keep temporary artifacts under their current job/lease owners. Add missing
disk-headroom checks at the relevant write sites, account for old and new data
during replacement, and clean up on cancellation or failure. This is targeted
ownership and quota work within the current store.

Retain active leases until their readers finish. Where an intermediate is
provably no longer used, release it through the existing owner. Do not replace
the ownership model merely to shorten an individual lifetime.

## 5. Remove unnecessary consumer copies

### Training

The code already projects model columns, uses Float32 numeric CatBoost
features, and releases conversion temporaries. Preserve those optimisations.

Load validation data after constructing the training Pool and releasing its
raw frame/arrays. Measure the resulting peak; the benefit depends on which
stage currently dominates. Keep the GLM adapter's distinct requirements.

Avoid overlapping full diagnostic frames, predictions and conversion arrays
when the current diagnostic calculation can consume them in batches. Preserve
the accuracy and feature/target/weight/offset semantics of each algorithm.

### Model scoring and optimiser setup

Where an existing leased generation already has the needed columns and
semantics, pass it through the current consumer interfaces instead of writing
another full temporary copy. Extend an existing parameter or adapter only
where an actual caller requires it.

Keep feature names and dtypes required by the model. A multipart generation
may still require one projected adapter file for a library that accepts only
a single file; document and measure that unavoidable write.

The optimiser's chunked input still produces a resident numeric grid. Keep
only the required columns, estimate the grid and conversion peak before setup,
and reuse prepared input where the current library safely permits it.
Parallel frontier work must respect the existing memory allowance; do not
increase concurrency by duplicating grids.

These changes fit the existing optimiser artifact/input work in `OPT-P11`
and `OPT-P13`. Preserve its measured `OPT-P06` decision rather than adding
another optimisation framework.

### Analysis and preview

Measure exact distinct/quantile/frequency work where it causes memory pressure.
A small response does not prove small operator state. Fix the affected
calculation using the current Polars and execution-limit paths.

Preserve exact-result semantics and current job/preview behavior. Accuracy-mode
redesign, new background scheduling and broad cache-policy redesign are not
part of these fixes. Existing deferred component packages remain separate.

## 6. Delivery order and completion checks

| Order | Work | Completion evidence |
|---|---|---|
| 1 | Correctness fixes and current CI failures | Reproduced defects have ordinary passing regressions; the changed publication transitions recover correctly |
| 2 | Writer/join and allocation measurements | Recorded time, native peak RAM, scan/staging cost and workload limitations |
| 3 | Small Polars writer and memory-estimation changes selected by those measurements | Equivalent results; improved measured cost; stated memory budget respected |
| 4 | Training allocation ordering and redundant consumer writes | Lower measured overlap or I/O without changed results or broken leases |
| 5 | Remaining input-lease and scratch checks within current owners | Supported concurrent-reader, cancellation, replacement and cleanup cases pass |

Run the smallest relevant tests while implementing each change, then affected
modules and required CI checks. Do not turn this plan into a new engine,
storage layer or generic framework project. Complete it by demonstrating that
the existing pipeline is correct, faster where changed and more economical
with memory.
