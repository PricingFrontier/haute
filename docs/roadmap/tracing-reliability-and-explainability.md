# Tracing reliability and explainability roadmap

**Status:** Implemented

**Current as of:** 2026-07-23

## Outcome

Tracing remains a fast, trustworthy explanation of the selected value: an open result can never
survive a semantic change to the graph or request context, and every on-screen or exported
derivation is built from values the engine actually observed rather than a second, divergent
interpretation of the calculation. Normal traces should feel immediate; unobtrusive progress and
recovery UI exists only for exceptional latency or failure.

## Verified baseline

The July 6 review under `docs/fable-Review/tracing/` is valuable evidence, but it is not a current
implementation plan. The present tree has already delivered several of its highest-risk items:

| Review package | Current status |
| --- | --- |
| T02 row-relocation ambiguity | Delivered. Target relocation uses the shared typed/vectorised matcher and fails loudly on ambiguity; the route maps the failure and has route-level coverage. |
| T03/P03 correlation remediation | Superseded by the Polars remediation. Target, parent, edge-join, and multi-frame matching share the typed vectorised primitive; relaxed matches are diagnosed; JSON-safe values match preview semantics; enrichment memoisation is request-local. |
| T05 self-referential calculations | Delivered. A modified column referenced on its own right-hand side uses its pre-assignment input value, with focused chain coverage. |
| T06 multi-frame tracing | Delivered. Correlation resolves source frames per edge and a bare multi-frame target fails with a named client error rather than an opaque 500. |
| T07.1/T07.2 route fixed costs | Delivered. The route computes its supersession key off the event loop, shares one `GraphFingerprintMemo`, serialises in the worker, and returns the already-safe payload directly. |
| T07.5 supersession | Delivered. The coordinator runs the active request and latest pending request while rejecting obsolete pending work; worker-limit behaviour is covered. |
| T08.1/T08.2 cache-key drift | Substantially superseded. Preview and trace now use the shared lineage cache-key factory, including contract mode, and a full-lineage preview is demonstrably reusable by trace. A target-only GUI preview intentionally lacks the ancestor frames needed for a full trace. |

Focused verification on 2026-07-23 ran the current correlation, multi-frame, calculation-hero,
lineage-cache, and route-supersession suites: **158 tests passed**.

The delivered work below was limited to defects still observable in the current source. It did not
reopen the delivered matching, multi-frame, self-reference, route-offload, or shared-key work
without a new failing regression.

Implementation evidence, including separated backend stages and real-browser rendering, is
recorded in [the 2026-07-23 tracing performance baseline](tracing-performance-baseline-2026-07-23.md).

## Delivered milestones

### 1. Bind every trace result to its semantic request context

**Scope:** Replace the current `traceResult | null` request model with an explicit
`idle | loading | ready | error` state and bind every request to the same semantic context already
used by preview freshness: graph-store `structuralVersion`, active source, row limit, target, row,
column, and clicked row values. A change to that context must abort in-flight work and clear a
ready/error trace synchronously. Position-only canvas changes must not invalidate it because they
do not advance `structuralVersion`.

This should reuse `useGraphStore.structuralVersion`; do not introduce a second frontend graph
fingerprint or scatter `clearTrace()` calls across individual editors and WebSocket handlers. The
existing request sequence and `AbortController` remain the transport-race guards.

The request state is an internal correctness model, not a reason to add ceremony to the common
path. A trace that resolves within a short, measured threshold should transition directly to the
result without flashing a loading panel or skeleton. Only a request still pending beyond that
threshold should show a compact progress/cancel treatment. Failure remains persistent and
actionable, with a sanitised summary and raw server detail behind disclosure.

A 409 row-mismatch/ambiguity must **not** automatically re-run the same request as proposed by
T09.2. For a genuinely ambiguous row, refreshing and resubmitting identical visible values cannot
create identity and risks selecting a different policy at the same index. The safe recovery is to
refresh the preview, clear the old selection, explain why identity could not be proven, and require
the user to select the intended row again.

**Tests first:**

- A trace resolving before the progress threshold shows no loading-panel flicker.
- A request still pending after the threshold shows compact labelled progress, keeps the clicked
  cell highlighted, and can be cancelled back to idle.
- A rejected request renders a persistent error; retry reuses the captured request only while its
  semantic context remains current.
- Advancing `structuralVersion`, changing source/row limit, or applying a WebSocket graph refresh
  aborts and clears loading/ready/error state; a late response cannot repopulate it.
- A position-only node move preserves a ready trace.
- A 409 refreshes/invalidates the preview and asks for a new row selection; it does not silently
  trace the row now occupying the old index.

**Acceptance criteria:**

- No semantic graph or request-context change can leave an old panel, glow, value chip, or late
  response visible.
- Exceptional latency has proportionate progress/cancellation feedback, and every failure has a
  persistent recovery path.
- Ordinary node dragging causes neither a trace request nor trace invalidation.

### 2. Eliminate the remaining explanation/engine divergences

**Scope:** Resolve the current T04 fidelity gaps with shared runtime semantics and observed values:

1. Rating detail must distinguish the value selected by the table (matched entry or default) from
   the node's value after optional user code. It must never label a post-code output as the table's
   selected value.
2. Banding runtime and enrichment must consume one public, immutable comparison-operator contract.
   Unknown operators should be handled consistently; the trace must not credit a rule the engine
   skipped.
3. Model-score detail must use explicit configured/contract features or authoritative model
   explanation metadata. When none exists, omit the feature list with a visible unavailable reason;
   do not present every input column, including identifiers, as fact.
4. Row-lineage detection must stop substring-matching comments, strings, `.list.join`, and
   `.str.join`. Preserve real join/sort/expand semantics while using observed row counts to avoid
   claiming rows were filtered when none were removed.
5. Multi-parent input collisions must be namespaced symmetrically for every parent and schema-diff
   logic must understand those names as provenance variants rather than removed columns.
6. `_fix_upstream_values` must no longer rewrite one observed cell in isolation. First prove whether
   any valid scenario still needs it after the shared matcher remediation. If none does, delete it;
   if one does, move the correction into correlation so it selects one whole row atomically and
   emits a diagnostic. Enrichment must remain an observer, not a row-repair layer.

Update the tracing and rating specifications before changing these semantics.

**Tests first:**

- Rating lookup `1.1` followed by node code producing `2.2` reports both values under distinct,
  accurately named fields.
- The engine/enricher operator matrix is generated from one source and includes unknown-operator
  failure behaviour.
- A model input containing `quote_id` with no feature contract never calls it a model feature.
- `.list.join` and operator text in comments/strings remain `rows unchanged`; real joins and sorts
  retain their structural labels.
- Two parents sharing a column expose both qualified values, with stable schema-diff semantics
  independent of parent order.
- Historic upstream-fixup cases either correlate a complete row or emit a typed unresolved-row
  diagnostic; no step can contain a synthetic mixture of two rows.

**Acceptance criteria:**

- Specialised detail, schema diff, calculation, and observed output tell one consistent story for
  every golden trace.
- No enrichment path invents a feature, credits a skipped rule, or mutates a partial observed row.
- Parent order and display-name collisions cannot change provenance.

### 3. Preserve gaps and failures as first-class trace evidence

**Scope:** A correlation failure on an otherwise relevant path must remain visible in topological
position. Do not weaken `TraceStep` by fabricating empty input/output rows. Add a separate typed
`TraceOmission` collection carrying node identity, topological position, reason, and a reference to
the corresponding correlation diagnostic; the panel can interleave omissions with successful
steps. Benign column-relevance pruning is not an omission.

Hoist waterfall errors out of `CalculationHero` so specialised rating, banding, model, optimiser,
scenario, and live-switch cards cannot suppress them. Map stable error types to plain-language
copy and keep technical detail behind disclosure.

**Tests first:**

- A duplicate/unsupported parent correlation emits one omission linked to one diagnostic and the
  panel renders it between the surrounding successful steps.
- Column-relevance pruning emits no omission.
- Rich and generic target cards both render reconciliation/unavailable waterfall errors.
- Required omission/error fields are enforced by backend schemas, frontend guards, and the
  frontend/backend contract suite.

**Acceptance criteria:**

- `nodes_in_trace`, rendered steps, omissions, and diagnostics reconcile without relying on users
  to infer a missing node from counts.
- No backend-reported trace or waterfall failure is visible only in logs or a transient toast.

### 4. Make the visible story truthful and internally consistent

**Scope:** Remove or define every misleading field and presentation inconsistency before adding
new surface area.

- Remove per-step `execution_ms` end-to-end unless it can be populated for every execution origin
  with one documented meaning. The current always-zero value is false precision; the whole-request
  timing may remain.
- Consolidate trace number/null formatting under one helper and locale policy, retain exact values
  in accessible titles when rounded, and render missing values as “—” rather than the string
  `null`.
- Carry rating/default use into waterfall entries and collapsed cards.
- Present `passthrough` as “rows unchanged” and suppress that badge when a step adds or modifies
  the traced value.
- Delete the never-emitted top-level step fields and guessed branch-highlight fallbacks; use typed
  calculation fields only. Replace repeated `steps.indexOf(...)` work with an index map while the
  contract is being cleaned up.

**Tests first:**

- Contract tests fail if the removed timing/dead fields reappear.
- Golden detail/waterfall/card fixtures share formatting, exact-value titles, null copy, and default
  flags.
- Branch highlighting occurs only from a typed backend branch selection.

**Acceptance criteria:**

- No fake timing or dead optional field crosses the wire.
- The same numeric value has the same visible formatting and recoverable precision everywhere in
  the panel.
- Default use is visible without expanding a card.

### 5. Export the exact trace the user reviewed

**Scope:** Provide Markdown, CSV, copy, and print derivations from the already validated
`TraceResult` displayed in the panel. Do not re-run the graph during export: runtime input may have
changed, and an artifact produced from a second execution could disagree with the open panel.

Add explicit provenance to the trace response before building export: a server-generated
`generated_at`, pipeline/source identity where available, and a documented `execution_origin`
(`fresh_execution`, `preview_cache`, or `trace_cache`). Do not label this as data freshness. Store
the execution origin accurately through preview reuse and trace-cache hits rather than exposing the
current ambiguous boolean.

Build one deterministic export projection from the validated frontend trace type and use it for
download, clipboard, and print. It must include row identity, target/output, ordered successful and
omitted steps, calculations, selected/default rating evidence, waterfall data or its failure,
correlation diagnostics, and provenance. Once that path exists, delete the unreachable backend
`_trace_export.py` helper and its direct-only tests instead of maintaining two report interpretations.

**Tests first:**

- Golden trace exports byte-match every numeric/default/omission value in the displayed payload.
- Export after graph invalidation is unavailable because milestone 1 has cleared the trace.
- Markdown/CSV escaping, clipboard failure, download naming, and print layout have focused tests.
- A trace assembled from each execution origin reports the correct origin and a valid UTC timestamp.

**Acceptance criteria:**

- A user can leave the application with an accessible derivation that cannot differ from the panel
  they reviewed.
- There is one export projection and no dead production export module.
- Provenance states how the trace was assembled without implying that cached data is current.

### 6. Re-baseline performance before changing payload or cache architecture

**Scope:** Re-run the current trace benchmark shapes after milestones 1–5 and record results in the
existing performance evidence. The July 6 numbers predate the shared matcher, shared lineage key,
stat-gated fingerprints, and route serialization changes and must not justify new architecture on
their own.

Treat T07.3/T07.4/T07.6 and T08.3 as hypotheses, not committed fixes:

- Do not project away full step rows until profiling shows payload cost is material and the product
  contract for the expanded “all columns” view and export has been decided.
- Do not add a short-TTL runtime-input fingerprint that weakens immediate invalidation without a
  measured need and an explicit consistency contract.
- Do not add a client trace cache while the server cache already makes repeat clicks cheap; it
  duplicates the semantic invalidation problem solved in milestone 1.
- Do not combine preview/trace byte budgets without process-RSS evidence. Independent budgets are
  conservative and operator-configurable; shared-reference double accounting alone is not a leak.
- Measurement confirmed that a target-only preview cannot satisfy the full-ancestor reuse
  invariant, so trace no longer attempts that guaranteed-miss lookup; the tested full-preview
  reuse path remains.

**Acceptance criteria:**

- Benchmarks separate cold execution, preview reuse, trace-cache hit, correlation, serialization,
  and browser rendering costs on linear and join/multi-frame graphs.
- Any further optimisation has a failing structural/performance test, preserves exact trace/export
  semantics, and updates the performance documentation.
- Stale point-in-time performance claims are retired rather than copied into active planning.

## Non-goals

- Row-ID injection through user pipelines or heuristic resolution of ambiguous rows.
- Reopening the shared typed Polars matcher, multi-frame per-edge routing, or shared lineage key
  without a new demonstrated defect.
- Automatically retrying a row whose identity the backend could not prove.
- Hiding trace failures by dropping steps, coercing unknown values, or guessing model/rating facts.
- A general redesign of graph editing, preview execution, or cache admission policy.

## Dependencies and sequencing

Milestone 1 is first because every later persistent/error/export surface relies on correct
invalidation. Milestone 2 follows because exports and polished UI must not preserve explanations
known to diverge from runtime semantics. Milestone 3 establishes the complete evidence model;
milestone 4 then cleans the wire and presentation contract. Milestone 5 exports that settled
contract. Performance is re-baselined last so optimisation measures the delivered behaviour rather
than the retired July implementation.

Each functional milestone begins by updating `docs/specs/tracing/`,
`docs/specs/frontend-trace-ui/`, and any owned rating/server API specification. Wire changes update
`src/haute/schemas.py`, `frontend/src/types/trace.ts`, `frontend/src/types/guards.ts`, and both
contract suites in the same change. Browser workflow coverage should use the fixture/harness policy
owned by the Frontend UI quality roadmap; feature-specific trace assertions remain owned here.

## Completion evidence

The retirement criteria are met: semantic changes invalidate trace evidence, normal traces do not
show progress chrome before 500 ms, exceptional latency and failures remain actionable, omissions
and waterfall failures are typed evidence, runtime and enrichment semantics agree, dead/fake wire
fields are removed, and every export format derives from the open validated trace.

Final regression evidence on 2026-07-23:

- Backend tracing and rating lane: **1,157 passed, 3 skipped, 3 deselected**.
- Frontend coverage lane: **5,479 passed across 282 files**; critical coverage gates passed.
- TypeScript typecheck, ESLint, production build, and bundle audit passed. Export-only code is a
  lazy 1.7 KiB gzip chunk; initial JavaScript is 244.2 KiB gzip against the 245 KiB budget.
- Backend performance lane: **7 passed**. Real-browser trace rendering remained below the 500 ms
  progress threshold for both recorded shapes; exact stage and p95 results are in the linked
  performance baseline.

Durable behaviour now belongs to the tracing, frontend trace UI, rating, server API, and
engineering-quality specifications and ordinary regression suites. The July review remains
historical evidence only.
