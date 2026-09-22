# Reconciliation of Fable 5.1's independent review

Scope note, 22 September 2026: the current focused implementation plan
supersedes the architectural options below. Those options are removed from the
implementation scope rather than deferred; the preserved Fable report is
historical evidence.

22 September 2026. This is the parent reviewer's assessment of the
[verbatim Fable 5.1 report](pr-227-fable-5.1-review.md), not text written by
Fable. It qualifies the earlier [review](pr-227-review.md) and
[implementation plan](pipeline-cache-memory-design.md). No production
changes have been made.

Fable was invoked through Claude Code with the exact canonical model
`claude-fable-5-1`, medium effort, standard speed and read-only tools. Runtime
metadata confirms successful completion, 53 turns, no permission denials and
no spawned agents. It read source and saved evidence; it did not rerun the
probes or benchmarks. The [request](pr-227-fable-review-request.md) and
[provenance](pr-227-fable-5.1-provenance.json) are saved separately. The report
is preserved verbatim, including claims qualified below.

## Main outcome

Fable supports the mechanisms behind R1–R8 and also recommends requesting
changes. It assigns lower severity to several findings and argues for a
smaller implementation plan: local store fixes, decisive measurements, then
the minimum execution changes justified by those measurements.

The current work fixes existing store, writer and consumer behavior without
introducing new architecture.

## Changes I accept

| Topic | Refined recommendation |
|---|---|
| Publication R2 | First try ordering the existing index/pointer writes so every visible or abandoned generation is discoverable. Prove recovery with fault injection using the current filesystem/index layout |
| Replacement R3 | Credit reclaimable superseded slot generations during admission, while preserving the previous usable generation until commit |
| Part naming R6 | Numeric parsing and numeric sorting may be sufficient. A new manifest format is not required merely to fix the five-digit boundary |
| Join strategy | Establish native build-side memory behavior and sliced-writer scaling before choosing a larger implementation. Prefer existing Polars APIs if they meet the semantic and byte-budget gates |
| IR maintenance | Add contract tests for recognised scan/transform/blocking shapes now. Keep the existing recipes and classifier. |
| Resource control | Extend current admission and worker controls rather than create a parallel resource manager. Prioritise missing scratch reservations and byte-aware execution |
| Performance evidence | Investigate why the sliced path's historical peak grows with input, and remove tests that require native execution to remain inefficient |
| Consumer copies | Preserve the recommendation to delay validation loading until after train Pool construction, and remove redundant staging through existing leased datasets where supported |

The current admission implementation already reserves in-flight memory in the
parent process: [`_execution_admission.py:553`](../../src/haute/_execution_admission.py#L553)
and [`:606`](../../src/haute/_execution_admission.py#L606). Its reservation
table is process-local. That is useful existing infrastructure, not a
host-wide coordination guarantee, and it should be acknowledged explicitly in
any implementation specification.

## Findings that need more precise scope

**R1: distinguish supervised children from independent processes.** The
training lifecycle holds the seed plan while its worker runs
([`_training_lifecycle.py:1604`](../../src/haute/routes/_training_lifecycle.py#L1604));
node builds do the same
([`_node_data_service.py:1368`](../../src/haute/routes/_node_data_service.py#L1368)).
Input Clear executes in the parent with local coordination
([`input_cache.py:822`](../../src/haute/routes/input_cache.py#L822)). Those
paths have protection that the two-independent-process probe deliberately
does not. The reproduced defect remains valid for independent processes
sharing a cache root, but the initial review described the common supervised
topology too broadly. Treat this as inherited multi-process hardening, with
priority determined by the supported deployment topology. It is not a new
PR regression.

**R4: a short stale-answer window is still a correctness issue.** I would
retain P2 for showing old pricing rule counts as the current full-data answer.
The window lasts through debounce and request completion, not necessarily
just 250 ms. The fix is small; severity should reflect misleading data, not
implementation size. Gate the complete analysis request identity and abort
immediately on supersession.

**R5/R6: narrow triggers justify lower urgency.** Both remain straightforward
bugs worth fixing before merge; neither justifies architectural expansion.
Their tests should exercise the relevant normal/validation paths and naming
order boundary, rather than be counted by an arbitrary one-test quota.

**“Bounded” requires an envelope, not exactly flat measured RSS.** The
historical sliced-path ratios of 1.30–1.44 do not prove its memory is unbounded;
allocator behavior, metadata and read-ahead can change measured peaks inside
a finite envelope. They also do not prove a bound. Fable's proposed scaling
experiment is valuable, but its binary “a bound means ratio 1.0” inference is
too strong. Separate operator state from reader/metadata/allocator overhead,
then test the stated byte envelope with inputs exceeding it.

## Recommendations I would not adopt unchanged

### Parquet uncompressed bytes are not decoded RAM

Fable proposes treating Parquet uncompressed column-chunk sizes as decoded
bytes and using that as the one admission mechanism. That is unsafe.
Dictionary and run-length encodings remain encodings even with the compression
codec disabled. Decoding them can expand the values substantially; hash-table
state and conversion overhead add further allocations. Group cardinality
also cannot generally be inferred from that size alone.
[Parquet encoding specification](https://parquet.apache.org/docs/file-format/data-pages/encodings/).

I ran a small verification in the current environment, separate from Fable's
read-only review: 20,000 identical strings, each 256 bytes, written with Arrow
dictionary encoding and `compression="NONE"`, then read with Polars.

| Measurement | Bytes |
|---|---:|
| Sum of Parquet `total_uncompressed_size` across column chunks | 832 |
| Polars decoded-frame `estimated_size()` | 5,120,000 |

That is over 6,000 times the metadata size. This is a representation-size
counterexample, not an RSS benchmark; temporary fixtures were removed.
The essential reproduction is:

```python
frame = pl.DataFrame({"s": ["x" * 256] * 20_000})
pq.write_table(frame.to_arrow(), path, use_dictionary=True, compression="NONE")
metadata = pq.read_metadata(path)
encoded = sum(
    metadata.row_group(r).column(c).total_uncompressed_size
    for r in range(metadata.num_row_groups)
    for c in range(metadata.num_columns)
)
decoded = pl.read_parquet(path).estimated_size()
```

Use metadata as one estimation input, alongside dtype widths, bounded decoded
samples, worst-case/skew allowances, observed allocations and operator-specific
state. Existing `_ram_estimate.py:543–590` already uses decoded frame/column
sizes and bounded sampling; extend that work. Estimates need conservative
headroom plus enforced limits and an admitted spill strategy. Staging an
unknown query to Parquet first is not automatically safe: that staging query
itself must satisfy the execution contract.

### The publication ordering proposal is a candidate, not a proven fix

The existing store explicitly supports indexed-but-unpointed generations
([`_node_snapshots.py:1583`](../../src/haute/_node_snapshots.py#L1583)), so
Fable's simpler ordering is promising. It still needs a complete transition
specification covering pin transfer, same-identity refresh, different-signature
replacement, failed metadata writes, process death, Clear and recovery.
In particular, specify the retention state when the new pointer commits but
the later pin/index update fails. Neither reviewer has implemented or tested
the proposed ordering. Do not equate a plausible rearrangement with a finished
durability proof.

### Keep local reference counts when unifying cross-process leases

Removing the legacy provider-specific lease path is reasonable. Removing all
process-local reference counting is not. The existing marker protocol counts
nested readers locally and removes its process marker only after the last
local release
([`_node_snapshots.py:1390`](../../src/haute/_node_snapshots.py#L1390),
[`:1404`](../../src/haute/_node_snapshots.py#L1404)). Preserve that property
while extending marker-based lifetime protection to input generations.

### Repeated filtered passes are not a free partitioning fallback

A one-pass physical partition writer is worth testing in the installed Polars
version. Fable's alternative of K full filtered passes could retain much of
the original scan-amplification problem. It needs explicit I/O/runtime gates;
do not make it an automatic fallback simply because K is usually smaller than
the driving chunk count. Oversized partitions and hot keys still require
special handling, and a native join's build-side allocation must be measured.

The implementation scope remains the existing Polars pipeline. Alternative-engine
evaluation has been removed at the user's request.

## Additional observations and limits

Fable usefully highlights staging before joins: `_staged` writes each input
that fails the sliceability check
([`_chunked_writes.py:731`](../../src/haute/_chunked_writes.py#L731)). Measure
those writes as part of end-to-end join cost. This does not mean every join
always stages both sides: plain scans and previously captured outputs can
avoid that branch.

Cross-process input publication, Windows deferred-retirement cleanup and
additional join semantic cases deserve targeted checks. They are not new
reproduced failures from this second review. In particular, the assertion
that concurrent input builds are harmless needs stronger lifecycle evidence.

Fable's proposed large experiments are designs, not execution evidence. Its
claim about approximately 10 GB of current host commit headroom was not
independently verified here. Read current resources before choosing sizes;
several estimates, skew and concurrent activity can invalidate a static host
assumption. No large-data experiments were run during this reconciliation.

## Refined next steps

1. Implement focused regressions and local fixes for R2–R6, address the actual
   CI failures, and correct overstated bounded-memory claims. Resolve R1's
   delivery priority from the supported process topology.
2. Specify byte budgets and exceptions, reusing the current estimator,
   admission and worker-limit infrastructure.
3. Measure native join build-state scaling, sliced-writer scaling and staging
   cost under a fixed budget. Verify whether the installed engine can perform
   one-pass physical partitioning within that budget.
4. Choose the smallest join/writer change that satisfies those measurements
   and differential semantic tests. Keep changes within the existing Polars and
   filesystem-cache implementation.
5. Remove proven consumer copies and stagger training allocations; then extend
   shared leases and scratch admission without duplicating existing owners.

This is a more focused plan than treating every architecture option in the
first proposal as a work item. The target remains the same: demonstrable
correctness and bounded resources, with the fastest execution strategy that
satisfies those constraints.
