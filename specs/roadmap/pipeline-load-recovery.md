# Pipeline loading and recovery roadmap

## Scope

Pipeline discovery, parsing, sidecar loading, editor ingestion, and live sync
must let users inspect and recover authored pipelines without weakening the
strict contracts used by save, execution, lint, CI, or deploy. Current shipped
behaviour remains specified by [pipeline config](../pipeline-config/high-level.md),
[server API](../server-api/high-level.md), and
[frontend graph canvas](../frontend-graph-canvas/high-level.md).

This roadmap owns editor-only recovery documents, node/edge load diagnostics,
degraded-canvas behaviour, ready-subgraph preview eligibility, source-only and
last-known-good presentation, and revision-safe repair operations. It also
owns the strict/recovery entry-point boundary itself: the existing silent
syntax fallback moves out of the strict `parse_pipeline_file()` /
`parse_pipeline_source()` entry points and behind the recovery entry point,
so execution, lint, CI, and deploy consumers regain a genuinely strict parse. It does not make an invalid pipeline executable,
invent missing configuration, silently migrate source, or turn recovery
output into the canonical `PipelineGraph` consumed by runtime and deployment
code.

## Product contract

If Haute can safely discover and read a pipeline document, the editor opens it
as one of three explicit states:

| Document state | Meaning | Editor result |
|---|---|---|
| `ready` | The strict graph is valid. | Normal editable canvas and existing capabilities. |
| `degraded` | Authored graph structure is recoverable, but one or more nodes, edges, submodels, or document artifacts (such as a corrupt position sidecar) are invalid. | Recovered canvas with unavailable/blocked elements and visible diagnostics. |
| `source_only` | The current source is readable, but a trustworthy graph skeleton cannot be reconstructed. | Source-recovery surface; never a successful empty canvas. |

Authentication, transport, project-discovery, permission, and unreadable-file
failures remain real system failures. They do not become fabricated pipeline
documents, but the frontend shell must render a dedicated failure/recovery
surface rather than leaving an empty editor. "Always opens" therefore means
the current document or an honest recovery surface is always presented; it
does not mean arbitrary bytes are represented as a valid graph.

Selecting the active document never depends on parse success. The unnamed
editor load returns the first discovered pipeline that presents authored
content — ready, degraded, or source-only alike — so a broken first pipeline
opens as its own degraded document instead of being silently replaced by a
later healthy pipeline. A successful empty canvas remains valid only for a
project whose discovered pipelines genuinely contain no authored content.

Recovered graph elements have an editor-only availability state:

| Availability | Meaning | Behaviour |
|---|---|---|
| `ready` | This element resolved and validated. | Normal rendering; capabilities depend on its complete dependency closure. |
| `unavailable` | This element's own source, config, type, or contract is invalid. | Visible but non-executable, with its own diagnostics and repair actions. |
| `blocked` | This element is valid but depends on an unavailable element. | Visible with the blocking path; no execution action may start. |

Load availability is distinct from the frontend's transient execution
`_status`; a node can never appear to have a runtime failure when it has not
been executable in the first place.

### Diagnostics

Every recovery failure is explicit, bounded, and attributable wherever the
source permits. The editor load contract carries stable diagnostic ids and
codes, severity, scope (`pipeline`, `node`, `edge`, or `submodel`), a safe
message, optional node/edge identity, source file and line/column span,
remediation, and an incident id for unexpected internal failures. Server logs
retain the exception and stack for an incident id; clients never receive raw
internal traces.

Authored structures are conserved: each discoverable node and connection is
either represented in the recovered document or named by a diagnostic that
explains why it could not be represented. Recovery must never omit a broken
node, drop a dangling connection, replace invalid config with `{}`, or return
an empty graph that looks like user content disappeared.

### Strict and recovery boundaries

`haute.parser.parse_pipeline_file()` and the exported
`parse_pipeline_source()` back execution, lint, CI, code generation, deploy,
and ordinary save validation, and both are genuinely strict: every parse
failure raises, syntax errors included. Before `PLR-P01`, a `SyntaxError`
silently substituted regex-recovered canonical output for all of those
consumers. The neutral fragment-recovery machinery now lives only behind the
editor's separate recovery entry point. The recovery entry point uses separate
response models. Those models may share extraction primitives with the strict
parser, but they do not subclass, relax, or get accepted as the canonical
`PipelineGraph`.

Separate classes alone do not enforce that boundary at the wire: the
canonical models tolerate unknown fields, and several canonical operations
deliberately accept a client-posted graph. Two mechanisms close it. First, the recovery
document uses an element shape that is structurally incompatible with
`GraphNode`/`PipelineGraph` deserialization, so a recovery payload fails
request validation on every graph-consuming endpoint, with a per-endpoint
rejection test. Second, endpoints that write project artifacts for a named
document — ordinary Save, submodel create/dissolve, and the later `PLR-P05`
repair commands — enforce on-disk document state server-side by re-resolving
the named document's current state from disk (a Save request carries
`source_file` but no base revision today; the `PLR-P05` commands additionally
carry the recovery `source_revision` for compare-and-swap): a degraded or
source-only on-disk document rejects them no matter what graph the client
posts. Execution
endpoints that deliberately accept a client-posted graph (preview, trace,
output publication, training) keep that contract for ready documents —
previewing unsaved edits is authored behaviour, not a leak — and their
degraded-document counterparts exist only through the server-derived
`PLR-P03` planning boundary. The `PLR-P04` status fence keeps the client's
notion of ready authoritative for those actions even while a dirty graph
rejects an incoming document update.

The recovery path isolates each resolution boundary. Expected authored-input
errors become diagnostics. An unexpected exception caught around one
editor-only node resolution is logged and becomes an unavailable node with an
incident id; the same exception still fails loudly through the strict parser.
A source-wide recovery failure produces `source_only`, not a guessed graph.

The standard graph Save, deploy, train, trace, output publication, and full
execution actions remain unavailable for a degraded or source-only document.
No recovery state is trusted merely because a client sent it back. Later
packages may preview a server-validated ready ancestor closure and may apply
explicit source repair operations, but neither path treats the complete
pipeline as valid.

### Failure localisation

| Failure | Recovery outcome |
|---|---|
| Invalid node decorator arguments, contract, Explore pivot, or sidecar content | Preserve the node identity/type when known and mark it `unavailable`. |
| Missing, unreadable, or malformed node config JSON | Preserve the source reference, protect the config from overwrite, and mark only the owning node unavailable. |
| Corrupt or unreadable `.haute.json` sidecar | Load succeeds as a degraded document with default positions and a document-scope diagnostic distinguishing corrupt from absent; the sidecar bytes stay untouched. Its `sources`/`active_source` state is untrusted: preview admission is blocked rather than silently defaulting the active source. |
| Syntax error inside one recoverable function block | Reuse the existing syntax-recovery extraction and mark that node unavailable. |
| Unknown or removed node decorator | Strict parsing rejects it loudly. Recovery renders a dedicated unavailable-node card carrying the authored decorator name; never coerce it to Polars or another known type. |
| Duplicate node identity | Give recovery artifacts stable line-qualified ids, diagnose the collision, and never pass them to canonical graph consumers. |
| Dangling or ambiguous connection | Retain its authored endpoint identity in an edge diagnostic; render an overlay only when both recovery endpoints are unambiguous. |
| Missing or invalid submodel definition | Keep the parent graph and mark affected occurrences unavailable; diagnose blocked boundary connections. |
| Whole-file structure cannot be recovered | Return `source_only`; show the current source and, when available, a clearly stale read-only last-known-good canvas. |
| Unexpected parser defect | Isolate it to a node when possible, log it with an incident id, and otherwise return the source-only recovery surface. |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| `PLR-P01` | CI pending | P1 | Serve every readable pipeline as an explicit editor document, isolate node-local config failures, and retire the strict parser's silent syntax fallback. |
| `PLR-P02` | CI pending | P1 | Conserve and diagnose topology, unknown-node, duplicate-id, and submodel failures. |
| `PLR-P03` | CI pending | P2 | Preview only server-validated ready dependency closures. |
| `PLR-P04` | CI pending | P2 | Add degraded live sync, source-only recovery, and last-known-good presentation. |
| `PLR-P05` | CI pending | P2 | Add revision-safe unavailable-node removal only; automated migrations are deferred. |

## Planned improvements

`PLR-P01` through `PLR-P05` were implemented in the required dependency order
and remain listed until their CI exit gate is green. `PLR-P05` was approved and
implemented at minimal scope on 2026-08-19: it provides revision-safe removal
of an unavailable node and explicitly defers the migration registry. Each
implemented package also updates the owning component specifications with the
delivered contract.

### PLR-P01 — Editor recovery document and node-local degraded loading

**Why:** Before `PLR-P01`, the AST parser resolved and validated every node while constructing
the graph. A single `ConfigError`, such as an older Explore pivot missing a
newly required field, unwinds the parse; `GET /api/pipeline` then returns HTTP
422 and the initial frontend load leaves users without a canvas. The
surrounding load surface makes the failure worse: the unnamed route returns
the first pipeline that parses, so in a multi-pipeline project a broken first
pipeline silently opens a different pipeline, and the named route swallows
the parse failure and reports not-found. The strict/recovery boundary this
roadmap depends on also did not exist: on syntax errors
`parse_pipeline_file()` silently substituted the regex fallback for every
consumer, so `haute run` executes recovered graphs and `haute lint` passes
syntax-broken files via a dead `parseError` check. The existing syntax
fallback and `_load_error` save protection prove partial recovery is
possible, but neither AST-valid config failures nor the fallback's own
results use a first-class editor recovery state.

**Plan:** Specify an editor-only pipeline document DTO with the familiar
top-level graph metadata plus `load_status`, structured diagnostics,
element-availability data, raw-artifact-based `source_revision`, and explicit
capabilities. Keep its node and graph types distinct from `_types.PipelineGraph`.
Refactor AST extraction so the recovery entry point builds node skeletons
before config resolution, then resolves known nodes independently. Typed
config/contract failures produce unavailable nodes without synthesized
defaults. A syntax error delegates to the existing regex extraction and is
wrapped as a `degraded` document whose broken nodes are unavailable; a
whole-file extraction failure returns `source_only`. Merge safe sidecar
positions and source state even when a node is unavailable, while preserving
corrupt/missing config references for save protection. A corrupt `.haute.json`
sidecar no longer collapses silently to `{}`: it yields a `degraded`
document with a document-scope diagnostic that distinguishes corrupt from
absent, leaves the sidecar bytes untouched, treats its unreadable
`sources`/`active_source` state as untrusted, and withholds ordinary save
like any other degraded document.

Make the strict entry points actually strict in the same package: remove the
silent fallback from `parse_pipeline_file()` and `parse_pipeline_source()`,
so execution, lint, deploy, and
post-save verification raise on syntax errors, `haute lint` reports the parse
error and exits non-zero as its contract already claims, and the existing
fallback regression coverage moves to the recovery entry point. With the
fallback gone, an external edit that introduces a syntax error stops
broadcasting a silently degraded `graph_update`; it flows through the
existing retained-canvas `parse_error` frame until `PLR-P04` delivers
first-class degraded sync.

Change live editor load routes to return the editor document with HTTP 200 for
`ready`, `degraded`, and `source_only`; named-pipeline parse failures must not
become not-found, and a non-ready document resolves its name from recovered
metadata or the file stem. The unnamed route selects by discovery order and
authored-content presence only, never by load status, and `GET /api/pipelines`
summaries carry the same `load_status` vocabulary instead of a bare error
string. Keep the strict route/parser seam available to non-editor consumers
that require a canonical graph. Update frontend ingestion guards, the canvas
card, node panel, and initial-load handling. A degraded document shows one
summary banner, renders unavailable nodes with an accessible error indicator,
exposes their diagnostic and source/config location, and disables ordinary
mutation and execution actions — including layout dragging, since positions
cannot persist without codegen and no unsaveable edits may accumulate. A
`source_only` document opens a minimal read-only source-recovery surface
presenting the current readable source and document diagnostics;
last-known-good presentation stays in `PLR-P04`. Neither degraded nor
source-only loading emits the current `Failed to load pipeline` toast for an
authored error.

The recovery revision hashes the raw bytes of every artifact the document
depends on: the parent source, each registered submodel source, every
referenced node-config JSON (a malformed file hashes as its raw bytes), and
each `.haute.json` position sidecar, with explicit missing-file sentinels, so
an edit to any dependent artifact invalidates compare-and-swap. It must not
depend on constructing a valid canonical graph and must remain suitable for
the later revision-keyed operations (`PLR-P03` preview planning, `PLR-P05`
repairs).

**Acceptance:** A regression fixture containing otherwise-valid quote,
aggregation, feature, and modelling nodes plus an Explore node whose pivot
lacks required `value_order` loads with HTTP 200 and `load_status=degraded`.
Every authored node and resolvable edge is present; only the Explore node is
unavailable; its diagnostic identifies the node, config rule, and source
location; unaffected nodes are ready; and no project byte changes during
load. The strict parser still raises the original error. A two-pipeline
fixture whose first pipeline is broken returns the first pipeline's degraded
document from the unnamed route — never the second pipeline — while the
second stays reachable by name. A syntax-broken fixture loads as a degraded
document through the recovery entry point, and both strict entry points now
raise for it: `haute lint` exits non-zero naming the error, run/deploy
parsing fails loudly instead of consuming fallback output, and watcher tests
prove an external syntax-breaking edit takes the retained-canvas
`parse_error` path rather than replacing the canvas. A whole-file-unrecoverable
fixture returns `source_only` and renders the read-only source-recovery
surface, never an empty canvas. A corrupt `.haute.json` fixture loads
`degraded` with a document-scope diagnostic, default positions, and a
byte-identical sidecar on disk. A parametrised test posts a recovery document
and its elements to every graph-consuming endpoint and proves request
validation rejects them; ordinary Save and submodel create/dissolve against
an on-disk degraded document are rejected server-side regardless of the
posted graph; and the recovery revision changes when any hashed artifact
changes or goes missing. Frontend tests prove
the canvas renders, the degraded banner and node diagnostic are accessible,
invalid-node actions and layout dragging are disabled, and the generic
load-failure toast is absent. Ready pipelines remain wire- and
behaviour-equivalent at the strict graph boundary.

**Dependencies:** Existing syntax fallback, sidecar load protection, source
revision concurrency contract, graph-store atomic load, and frontend response
guards.

**Evidence:** `src/haute/parser.py`; `src/haute/_parser_regex.py`;
`src/haute/_graph_builders.py`; `src/haute/_config_builder.py`;
`src/haute/_types.py`; `src/haute/_pipeline_revision.py`;
`src/haute/routes/pipeline.py`; `src/haute/routes/_helpers.py`;
`src/haute/routes/_save_pipeline.py`; `src/haute/routes/submodel.py`;
`src/haute/server.py`;
`src/haute/discovery.py`; `src/haute/cli/_lint.py`; `src/haute/cli/_run.py`;
`src/haute/deploy/_config.py`; `src/haute/schemas.py`;
`frontend/src/api/client.ts`; `frontend/src/api/types.ts`;
`frontend/src/hooks/usePipelineAPI.ts`;
`frontend/src/types/guards.ts`; `frontend/src/types/node.ts`;
`frontend/src/nodes/PipelineNode.tsx`; `frontend/src/panels/NodePanel.tsx`;
`tests/test_parser_fail_loudly.py`; `tests/test_parser_regex.py`;
`tests/test_server.py`; `tests/test_cli_lint.py`; `tests/test_discovery.py`;
`tests/test_partial_failure.py`;
`frontend/src/hooks/__tests__/usePipelineAPI.test.ts`;
`frontend/src/__tests__/App.integration.test.tsx`.

### PLR-P02 — Structural conservation and broader fault isolation

**Why:** Before `PLR-P02`, node-local config isolation alone could not represent unknown decorators,
duplicate ids, unresolved connections, shape-contract violations, or a broken
submodel. These faults split two ways: an unknown or removed
`@pipeline.<type>` decorator was silently ignored, hiding an authored node
even from a ready canvas, while duplicate ids, dangling connections, and
missing submodels unwind the whole parse loudly — the same lockout `PLR-P01`
removes for config failures. Silently filtering those structures in recovery
would make a degraded canvas look healthy and could cause destructive
recovery edits.

**Plan:** Extend the recovery extractor into explicit phases: metadata and
source spans; node skeletons; declared connection skeletons; independent node
resolution; submodel resolution; topology diagnostics; and downstream
availability propagation. Preserve known authored node types for rendering
and add a dedicated editor-only unavailable renderer for unknown/removed
types; tighten the strict parser symmetrically so an unrecognised
`@pipeline.<name>` decorator raises loudly instead of silently dropping the
function. Use stable line-qualified recovery identities for duplicate names
while retaining the authored label in diagnostics. Only emit an ordinary
recovered edge when both endpoints resolve uniquely; keep every other
declaration in a typed unresolved-edge collection rather than inventing a
canonical edge.

Validate graph-shape contracts per attributable node and continue collecting
independent failures. A broken shared submodel definition marks each affected
occurrence unavailable without suppressing unrelated root nodes or healthy
definitions. Cap diagnostics and report an explicit omitted count so
adversarial source cannot create an unbounded response.

**Acceptance:** Backend conservation tests cover invalid/missing JSON, broken
function bodies, unknown decorators, duplicate function names, dangling and
ambiguous connections, Explore topology violations, missing submodels, and
multiple independent failures. Every discoverable structure is represented or
diagnosed exactly once; no recovery-only identity validates as a canonical
graph; the strict parser rejects an unrecognised `@pipeline.<name>` decorator
loudly; diagnostics remain deterministic and bounded; and strict parse
results for healthy pipelines are unchanged. Frontend tests cover unavailable
unknown types, blocked nodes, unresolved-edge diagnostics, multiple-error
navigation, and unaffected canvas selection/layout.

**Dependencies:** `PLR-P01`; existing parser conservation assertions and
submodel identity/interface contracts.

**Evidence:** `src/haute/_parser_conservation.py`;
`src/haute/_parser_submodels.py`; `src/haute/_graph_shape.py`;
`src/haute/_submodel_instances.py`; `src/haute/_graph_builders.py`;
`src/haute/_ast_helpers.py`;
`tests/test_parser_conservation.py`; `tests/test_graph_shape_contracts.py`;
`tests/test_parser.py`; `tests/test_submodel.py`;
`frontend/src/utils/nodeTypeRegistry.ts`;
`frontend/src/nodes/SubmodelNode.tsx`.

### PLR-P03 — Ready-subgraph preview and capability enforcement

**Why:** Opening the canvas solves the primary lockout, but users should still
be able to inspect an unaffected branch. Sending a recovery graph through the
ordinary preview endpoint would be unsafe because unavailable placeholders are
not executable and client availability flags are not authoritative.

**Plan:** Add a server-owned preview planning boundary for editor documents,
keyed by the document's raw-artifact `source_revision` and the target node
identity rather than a client-posted graph. For a requested node, resolve its
complete transitive ancestor closure on the server, including required
submodel definitions and port bindings. Preview is eligible only when every
participating node and edge is ready. Construct a canonical
temporary `PipelineGraph` from those already-resolved elements, run the normal
strict shape/config validators, then delegate to the existing preview
execution path. Unavailable targets and blocked closures fail before admission
with stable `node_unavailable` or `node_blocked_by_load_error` diagnostics that
identify the blockers. A document whose `.haute.json` sidecar is corrupt has
no trustworthy source-selection state, so preview admission is refused with
the sidecar diagnostic rather than silently defaulting `active_source`. Full
trace, training, publication, deploy, and graph
Save remain disabled until the entire document is ready.

Expose server-derived capabilities in the editor document so the frontend can
present actions accurately, but recheck every capability on the server when
invoked. Ready nodes outside an invalid branch preview normally; a downstream
blocked node explains its shortest deterministic blocking path.

**Acceptance:** Cross-stack tests prove a healthy upstream branch previews in
a degraded document, an unrelated broken branch does not participate, and a
target whose closure reaches an unavailable node is rejected before execution
or resource admission. A request carrying a stale document revision is
rejected with a structured conflict before planning, and a corrupt-sidecar
document cannot preview at all. Tampered client
availability/capability fields cannot enable preview. Canonical preview
caching and source keys remain unchanged for equivalent ready closures, and
no other execution endpoint accepts a recovery document.

**Dependencies:** `PLR-P01`, `PLR-P02`, canonical preview planning, graph
fingerprinting, and execution admission.

**Evidence:** `src/haute/routes/pipeline.py`; `src/haute/execution.py`;
`src/haute/executor.py`; `src/haute/_cache.py`;
`frontend/src/hooks/usePipelineAPI.ts`; `tests/test_server.py`;
`tests/test_preview_json_serialization.py`;
`frontend/src/hooks/__tests__/usePipelineAPI.test.ts`.

### PLR-P04 — Live-sync and source-only recovery continuity

**Why:** Initial recovery is insufficient if a later external edit broadcasts
only `parse_error`, or if the current source is too damaged for a canvas. Live
continuity must not confuse a stale last-good graph with the current file or
overwrite unsaved frontend edits.

**Plan:** Extend the sync protocol with a versioned editor-document update that
can carry ready or degraded recovery results and diagnostics, replacing the
`PLR-P01` interim in which any non-ready current source surfaces only the
legacy retained-canvas `parse_error` frame. Retain the existing dirty-graph
guard and atomic store transition for graph payloads, but make the guard
graph-scoped: a dirty rejection still applies the update's authoritative load
status, capabilities, and diagnostics as a status fence, so unsaved local
edits survive while execution actions reflect the true on-disk document and
cannot keep running against a document that silently degraded underneath
them. If the current source
is `source_only`, keep an available last-known-good canvas solely as a clearly
labelled, read-only reference with its previous revision; never publish it as
the current graph, enable Save from it, or merge current diagnostics into its
nodes. With no last-known-good graph, open a source-recovery panel containing
the current readable text and document diagnostics.

Unexpected top-level recovery defects and unreadable-file/system failures use
the same dedicated recovery shell with an incident id or safe filesystem
message. They never produce a successful empty graph. Route `.haute.json`
sidecar events in the file watcher to the owning document — previously JSON
outside `config/` was ignored — so external sidecar corruption and repair
produce the same versioned document updates as source edits. Reconnect and
resync compare both current document revision and load status so a transition
from degraded back to ready is delivered even if recovered topology is
unchanged.

**Acceptance:** WebSocket tests cover ready-to-degraded-to-ready transitions,
clean atomic application, graph-scoped dirty-state rejection with the status
fence applied (a cross-stack regression proves an unsaved-edit session loses
preview/trace/train/publication capability the moment the on-disk document
degrades externally, while the unsaved graph survives), stale update ordering,
source-only with and without last-good state, distinct current and stale
revisions, reconnect/resync, bounded diagnostics, unexpected recovery
exceptions, and an external sidecar corruption-then-repair round trip that
degrades and then restores the document. No failure path clears the current canvas to an apparently empty
pipeline, and no last-good snapshot can be saved as current source.

**Dependencies:** `PLR-P01`, `PLR-P02`, existing file-watcher batching,
fingerprinted WebSocket sync, graph dirty-state guards, and atomic graph-store
loading.

**Evidence:** `src/haute/server.py`; `src/haute/routes/_helpers.py`;
`frontend/src/hooks/useWebSocketSync.ts`;
`frontend/src/hooks/usePipelineAPI.ts`; `tests/test_partial_failure.py`;
`tests/test_server.py`;
`frontend/src/__tests__/hooks/useWebSocketSync.test.ts`.

### PLR-P05 — Transactional unavailable-node removal

**Why:** A degraded canvas is useful for diagnosis, but users should not need
to edit Python manually for common recovery operations such as removing an
obsolete node. Ordinary graph codegen is not safe while invalid raw fragments
exist because it could discard or normalize source the recovery parser could
not understand.

This package carries the roadmap's largest machinery-to-benefit ratio. The
recorded decision is **minimal**: add `Remove unavailable node` alongside the
existing raw source/config opening and defer the migration registry. `PLR-P01`
diagnostics already name the exact file and span, so problems without a safe
remove-only plan continue to use manual repair. The owning component
specifications record this boundary before implementation.

**Plan:** Add narrow recovery commands rather than enabling normal Save. Each
command takes the current raw-artifact revision, identifies exact conserved
source spans, computes and returns a dry-run patch, and applies through the
existing atomic multi-file transaction only after explicit confirmation and a
compare-and-swap revision check. `Remove unavailable node` removes only the
selected decorator/function block, explicit connections that reference it,
and its position metadata. A referenced config sidecar is retained by default
unless the user explicitly chooses its separately enumerated deletion.
Implicit connections that exist only because a downstream function parameter
names the removed node are enumerated during dry-run planning, and the
command rejects the removal with a diagnostic naming each dependent function
and parameter: consumer bodies reference those parameters, so mechanically
rewriting healthy signatures is unsafe, and an implicit edge must never
vanish silently while the file still parses.

The migration registry and `Upgrade node` action are explicitly out of scope.
Loading never migrates or writes a file. Raw-file editing remains the repair
path for problems that do not have a safe remove-only plan.

After each repair, re-run recovery parsing and strict parsing. A successful
strict parse transitions the document to ready; remaining independent errors
leave it degraded without rolling back a correctly targeted repair. Any stale
revision, span mismatch, write failure, or post-write conservation failure
rolls back every touched artifact and returns a structured error.

**Acceptance:** The legacy Explore fixture can remove its unavailable node
without changing unrelated functions, imports, comments, preserved blocks,
healthy connections, configs, or layout. The removed node's connections and
position disappear, its config remains unless separately approved, and the
result strictly parses. An unavailable node with implicit downstream
consumers is never removed: the command rejects with a diagnostic naming each
dependent function and parameter, and the same removal succeeds once the
consumer no longer references the node.
Tests also cover multiple remaining bad nodes, stale revision conflict,
shifted spans, duplicate identities, dry-run/apply agreement, atomic
rollback, and Windows write contention. No migration or upgrade action is
advertised. Normal graph Save stays unavailable until the document reaches
ready.

**Dependencies:** `PLR-P01`, `PLR-P02`, raw-artifact revisions, source-span
conservation, atomic writes, save locking, and transactional rollback.

**Evidence:** `src/haute/routes/_save_pipeline.py`;
`src/haute/routes/_helpers.py`; `src/haute/_pipeline_repair.py`;
`src/haute/routes/pipeline.py`; `frontend/src/types/pipelineRepair.ts`;
`frontend/src/components/PipelineRepairDialog.tsx`;
`tests/test_pipeline_recovery.py`; `tests/test_save_pipeline_integrity.py`;
`tests/test_route_save_pipeline.py`; `tests/test_file_ops.py`.

## Implementation plan

The package `Plan` fields above define architectural direction and observable
outcomes. This section defines the executable delivery sequence. Each numbered
slice is a red-green-refactor boundary: add the smallest failing contract or
regression first, make that slice pass without starting a later slice, inspect
the diff, and run the package's targeted verification before proceeding.

### Delivery rules

1. Add an `Approved change contract` to every affected owning specification
   before changing runtime behaviour. `PLR-P01` touches expression parsing,
   pipeline config, server API, frontend shared contracts, graph canvas, and
   node editors. Later packages update only the additional owners they affect.
2. Keep `parse_pipeline_file()`, `parse_pipeline_source()`,
   `parse_pipeline_to_graph()`, canonical graph request models, codegen, and
   execution strict. Recovery code may reuse extraction primitives but may
   not return, inherit from, or be implicitly coercible to `PipelineGraph`.
3. Give recovery its own module boundary. `src/haute/_pipeline_recovery.py`
   owns source/config/sidecar recovery orchestration and internal skeletons;
   `src/haute/schemas.py` owns public recovery wire DTOs; existing parser and
   revision modules retain strict parsing and revision primitives respectively.
4. Keep recovery reads side-effect free. Tests snapshot every participating
   source, config, and sidecar before a load and compare the bytes afterwards.
5. Use deterministic diagnostic ids, ordering, blocking paths, and recovery
   element ids. Only the separately reported incident id for an unexpected
   internal exception may be nondeterministic.
6. Centralise frontend status and capability state rather than adding ad hoc
   checks to individual buttons. A dedicated document-status store owns load
   status, diagnostics, capabilities, current revision, and source-only text;
   the existing request-facing `sourceRevisionRef` is an atomic mirror of that
   revision, and the graph store continues to own only renderable graph state
   and history.
7. Recheck every write or execution capability on the server. Frontend gates
   are product affordances, never an authority boundary.
8. `PLR-P05` is constrained to the recorded minimal scope. Do not add a
   migration registry, automatic upgrade, or recovery-graph Save path without
   a later approved specification change.

### Package 1 execution sequence (`PLR-P01`)

1. **Pin the strict/recovery contract.** Extend parser, CLI, deploy, route,
   sidecar, save, submodel, frontend guard, and initial-load tests with the
   exact old-Explore fixture and a syntax-broken fixture. The initial red
   evidence demonstrated the original leak: syntax fallback reached the
   canonical parser, an AST-valid node error prevents editor load, corrupt
   sidecars collapsed to defaults, and the unnamed route could skip a broken
   first pipeline. These failures were reproduced independently before the
   implementation changed.
2. **Introduce incompatible recovery DTOs.** Add enums and models for document
   status, element availability, source spans, diagnostics, capabilities,
   recovery nodes/edges, unresolved structures, and the editor document. A
   recovery element uses editor-specific identity/render fields instead of the
   canonical `GraphNode` `id/type/position/data` shape; the frontend maps it to
   React Flow only after response validation. Add backend model tests and
   frontend guard tests before any route emits the new contract. Add the
   parametrised rejection test that submits a recovery document and its raw
   elements to every canonical graph-consuming request model.
3. **Split AST discovery from node resolution.** Refactor
   `src/haute/_graph_builders.py` so one extraction pass records a known node's
   function identity, decorator token/kwargs, parameters, body, and exact
   source span without loading config. The existing strict builder resolves
   each skeleton and propagates every error unchanged. The new recovery
   orchestrator resolves skeletons independently, maps expected authored
   failures to diagnostics, and catches unexpected exceptions only around one
   named recovery boundary, logging the stack with an incident id. Prove a
   healthy source produces equivalent strict nodes and that one invalid
   Explore node does not alter healthy siblings.
4. **Relocate syntax recovery, then tighten the public parser.** Refactor
   `src/haute/_parser_regex.py` to expose neutral recovered fragments rather
   than a canonical graph. Consume those fragments only from
   `_pipeline_recovery.py`. Once the recovery syntax tests are green, remove
   `_fallback_parse` from `parse_pipeline_source()`, convert a whole-file
   `SyntaxError` into a contextual `ParseError`, and let both public strict
   entry points raise. Remove the dead `config["parseError"]` lint branch and
   prove lint, run, deploy parsing, post-save verification, and the interim
   watcher path all fail loudly on syntax-broken source.
5. **Make artifact state explicit.** Replace the editor's use of the
   ambiguous `load_sidecar() -> {}` result with a typed read result that
   distinguishes absent, valid, corrupt, and unreadable. Valid sidecar state
   is normalised through existing helpers; corrupt/unreadable state supplies
   default positions only, marks source selection untrusted, and emits a
   document diagnostic without changing bytes. Add a raw-artifact revision
   function in `src/haute/_pipeline_revision.py` that hashes a sorted,
   path-contained manifest of parent/submodel source, referenced configs, and
   position sidecars, including role-qualified missing sentinels and raw bytes
   for malformed artifacts. Prove order independence, alias deduplication,
   path safety, and a revision change for every dependency mutation.
6. **Add the editor document load service and route selection.** Keep
   `parse_pipeline_to_graph()` strict and add a separate recovery loader in
   `src/haute/routes/_helpers.py`. Change list, named, and unnamed editor load
   routes to return recovery documents. Select the unnamed document solely by
   discovery order and explicit `has_authored_content`, never by parse status;
   resolve a non-ready name from safely recovered metadata or the file stem.
   Preserve a genuinely empty document only when no discovered document has
   authored nodes, connections, submodel registrations, or preserved content.
   Route tests cover ready/degraded/source-only results, multiple pipelines,
   duplicate recovered names, configured paths, and no-pipeline projects.
7. **Fence persisted mutations from disk state.** Under the existing save
   lock, re-read the targeted existing document through recovery before
   ordinary Save and submodel create/dissolve. Reject non-ready disk state
   before staging any write regardless of the client-posted graph. Preserve
   the existing new-document path when no target artifact exists. Add
   byte-for-byte no-write assertions and race tests proving the readiness check
   and staging occur under one lock acquisition.
8. **Ingest and render recovery state.** Add strict TypeScript wire types and
   guards, plus one adapter from recovery elements to React Flow presentation
   nodes. Add the document-status store and update it, together with the
   `sourceRevisionRef` mirror, before publishing a graph snapshot.
   `PipelineNode` renders unavailable/blocked state accessibly;
   `NodePanel` selects a diagnostic view instead of a normal config editor for
   an unavailable node; a source-only component replaces the canvas when no
   graph is trustworthy. Derive one `documentReadOnly` gate in `App` and thread
   it through drag/connect/drop/context-menu, palette, keyboard, preamble,
   submodel, assistant graph-edit, Git/save, preview/trace/train/publication,
   undo, and redo entry points. Selection, panning, zooming, diagnostic
   navigation, and raw source/config opening remain available.
9. **Close the end-to-end regression.** Run the exact legacy Explore case
   through discovery, HTTP serialization, frontend guards, graph adaptation,
   canvas rendering, and node-panel diagnostics. Assert HTTP 200, degraded
   state, conservation of every healthy node/edge, one unavailable Explore
   node, disabled mutation/execution, stable revision, and unchanged project
   bytes. Repeat with healthy, syntax-broken, source-only, corrupt-sidecar,
   first-broken-of-two, and unexpected-node-resolver fixtures before marking
   `PLR-P01` complete.

### Package 2 execution sequence (`PLR-P02`)

1. **Broaden skeleton discovery.** Extend AST and regex discovery to record
   every top-level `@pipeline.<name>` function before checking whether the
   decorator name is supported. Add strict-parser rejection for unknown
   decorators and recovery diagnostics/render metadata for them. Detect
   duplicate authored function identities before dictionary construction and
   assign line-qualified editor ids without changing the authored label.
2. **Conserve connection declarations.** Extract explicit connection spans and
   implicit parameter bindings independently of resolved nodes. Emit an
   ordinary recovery edge only for unique resolved endpoints; put dangling,
   duplicate, or ambiguous declarations in the unresolved collection with
   endpoint/handle identity and source span. Extend the conservation assertion
   to cover represented plus diagnosed declarations.
3. **Localise topology failures.** Run shape checks per recovered node or
   definition context, map attributable failures to unavailable state, and
   continue collecting independent diagnostics. Calculate blocked state after
   local validation with a deterministic shortest blocker path and stable
   tie-breaking by authored order/id.
4. **Recover submodels independently.** Parse registration skeletons before
   resolving child files. Resolve each unique definition once, retain healthy
   definitions, mark every occurrence of a failed definition unavailable, and
   diagnose boundary edges that cannot be resolved. Missing, escaping,
   duplicate-definition, invalid-interface, and syntax-broken-child cases each
   receive distinct stable codes.
5. **Complete the recovery UI.** Register a dedicated unknown/unavailable
   renderer that cannot appear in the palette or canonical graph editor. Add
   an issues navigator for document/node/edge/submodel diagnostics and render
   unresolved connections only as non-interactive overlays when both visual
   endpoints are unambiguous.
6. **Prove conservation and bounds.** Add table-driven and property tests over
   combinations of invalid nodes, duplicate names, connections, topology, and
   submodels. The invariant is: every discoverable authored structure appears
   exactly once as a recovery element or diagnostic. Cap diagnostics and
   expose the omitted count; test deterministic results across repeated loads.

### Package 3 execution sequence (`PLR-P03`)

1. **Specify a server-owned request.** Add a recovery-preview request carrying
   `source_file`, raw-artifact `source_revision`, target recovery identity,
   source selection, row/column selectors, and streaming limits — never a
   client-posted recovery graph. Add stale-revision and invalid-target tests.
2. **Extract reusable preview execution.** Refactor the existing preview route
   so canonical graph preparation/execution/response assembly remains one
   shared internal service. The ordinary ready-graph endpoint and the new
   recovery planner both delegate to it; neither calls the other HTTP route.
3. **Plan and validate the ready closure.** Re-read the editor document,
   compare the raw revision, derive the complete target ancestor closure with
   submodel/port dependencies, reject unavailable or blocked participants
   before admission, and build a fresh canonical temporary graph only from
   individually resolved ready elements. Run canonical shape/config/path
   validation again before calling preview execution. A corrupt sidecar or
   untrusted active source rejects planning.
4. **Preserve caching and cancellation.** Derive cache/supersession identity
   from the canonical closure fingerprint plus source/selector inputs while
   retaining the raw revision as a staleness precondition. Prove the planner
   introduces no duplicate execution slot, cancellation path, or cache family.
5. **Enable only eligible UI actions.** Expose server-derived per-node preview
   capability and blockers in the document status store. Route degraded-node
   preview through the recovery-preview API; keep every other execution and
   persistence action fenced until document state is ready. Tampering tests
   prove frontend flags cannot bypass server planning.

### Package 4 execution sequence (`PLR-P04`)

1. **Version the sync frame.** Define and test a `pipeline_document_update`
   frame carrying version, source identity, raw revision, status,
   capabilities, diagnostics, and optional recovered graph/source-only text.
   Keep the legacy `graph_update` reader during the same release only where a
   ready strict producer still exists; do not infer degraded state from a
   `parse_error` string.
2. **Publish recovery results from resync and watcher paths.** Replace editor
   sync parsing with the recovery loader, publish ready/degraded/source-only
   document frames, and fingerprint the whole document contract. Extend the
   watcher ownership index so parent and submodel `.haute.json` changes map to
   their owning documents; preserve current debounce, retry, self-write, and
   dependency-trigger semantics.
3. **Apply the status fence independently of graph replacement.** Validate the
   complete frame at the frontend trust boundary. Apply revision, status,
   capabilities, and diagnostics first. If the graph is clean, atomically load
   the new recovered snapshot; if dirty, preserve local graph/history while
   retaining the new authoritative status fence. In-flight preview/trace/job
   responses whose captured status/revision is stale are discarded or
   cancelled through their existing lifecycle seams.
4. **Present source-only and last-good state honestly.** Retain the current
   client-side last renderable snapshot as an optional stale, read-only
   reference, preserving whether it was ready or degraded; label it with its
   old revision and never copy current diagnostics into it. A fresh session
   with no last renderable state shows only the current source-recovery view.
   No server-persisted snapshot cache is added unless a separate product
   decision requires recovery across process restarts.
5. **Exercise transition and race matrices.** Test ready -> degraded -> ready,
   ready -> source-only, clean versus dirty, sidecar corruption/repair,
   reconnect/resync, stale frame order, updates during layout, session expiry,
   and unexpected recovery failure. Assert no transition produces an empty
   successful graph or enables a stale execution/save action.

### Package 5 execution sequence (`PLR-P05`)

1. **Apply the recorded decision.** The owning specifications record
   `minimal`: remove an unavailable node only. The package is `CI pending`;
   the migration registry remains outside this package.
2. **Build a read-only repair planner.** Add
   `src/haute/_pipeline_repair.py` with a pure planner that takes source bytes,
   a recovery document, target recovery id, and raw revision. It enumerates
   exact function/decorator, explicit-connect, and position-sidecar edits;
   identifies implicit consumers; retains config sidecars by default; applies
   edits in descending byte-offset order; and returns a deterministic patch,
   touched-artifact manifest, warnings, and predicted recovery state. Reject
   ambiguous spans and implicit consumers rather than rewriting healthy
   signatures.
3. **Expose dry-run before apply.** Add typed dry-run/apply endpoints and
   frontend diff/confirmation UI. The client sends identity and revision, not
   replacement bytes or trusted spans. The apply path reacquires the save
   lock, rereads all artifacts, recomputes the plan, compares it with the
   confirmed plan hash, and returns conflict on any revision or plan drift.
4. **Commit transactionally and verify.** Reuse save-service staging,
   self-write suppression, atomic replacement, and rollback rather than
   creating a second filesystem transaction implementation. After writes,
   run recovery and strict parse plus conservation checks before commit; roll
   back every artifact on write, parse, or verification failure. A valid
   targeted removal may remain degraded only because an independent diagnosed
   error still exists.
5. **Keep migrations out of scope.** Do not advertise or implement an
   `Upgrade node` action, migration registry, automatic load-time rewrite, or
   guessed legacy version.
6. **Verify destructive boundaries.** Test exact unrelated-byte preservation,
   explicit connections, implicit-consumer rejection, retained config,
   separately approved config deletion, duplicate identities, stale revision,
   shifted spans, dry-run/apply agreement, rollback, self-write watcher
   suppression, and Windows contention. The UI must identify every file to be
   changed or deleted before confirmation.

### Verification and package exit gates

Use the smallest relevant test while iterating, then the package-level set
below. Run Ruff on touched Python, MyPy for affected backend surfaces, and
frontend typecheck/lint for touched TypeScript. Full compatibility, browser,
coverage, mutation, and performance suites remain CI-owned.

| Package | Required targeted verification before completion |
|---|---|
| `PLR-P01` | Recovery/parser/regex/config/revision tests; CLI lint/run parsing; deploy parse; pipeline list/named/unnamed routes; save/submodel disk-state rejection; frontend guards, initial load, canvas node, node panel, and App integration. |
| `PLR-P02` | Parser conservation, graph-shape, submodel parser/instance/route tests; recovery property/bounds tests; unknown renderer and diagnostic navigator frontend tests. |
| `PLR-P03` | Preview route/service, supersession, caching, cancellation, admission, submodel-port closure, stale revision, and frontend preview lifecycle/propagation tests. |
| `PLR-P04` | Server watcher/resync/partial-failure tests plus frontend WebSocket fail-loud, undo-history, panel-state, dirty-race, reconnect, and App transition tests. |
| `PLR-P05` | Save-lock, transactional-save, file-operation, source-preservation, sidecar, submodel persistence, recovery repair route, and frontend confirmation/diff tests. |

A package exits the roadmap only when its owning specifications describe the
delivered present-tense behaviour, every acceptance item has executable
evidence, recovery and strict paths have negative cross-boundary tests, the
targeted verification above is green, and CI is green. Remove the delivered
package entry rather than retaining a historical checklist here.

## Design decisions

- Recovery is a separate editor model, not a permissive mode on the canonical
  graph. This prevents a tolerant parse from leaking into runtime or deploy.
- Tolerance lives only behind the recovery entry point. The legacy silent
  syntax fallback inside the strict `parse_pipeline_file()` /
  `parse_pipeline_source()` entry points is relocated there, not duplicated:
  once the editor consumes recovery documents, strict consumers fail loudly
  on syntax errors instead of receiving regex-recovered graphs.
- Active-document selection ignores load status. A broken first pipeline
  opens as its own degraded document; it is never silently replaced by a
  later ready pipeline, and a parse failure is never reported as not-found.
- HTTP 422 remains appropriate for strict mutation/execution requests, but not
  for a readable authored document whose errors can be represented.
- Last-known-good state supplements current recovery; it never replaces or
  disguises the current source.
- Load never writes, auto-removes, auto-upgrades, or supplies speculative
  defaults. Repair and migration are explicit revision-checked commands.
- A broad catch is allowed only at a named editor recovery isolation boundary
  and must produce a visible diagnostic plus server evidence. It is never a
  silent fallback.
- Returning an empty graph, catching and using `{}`, dropping invalid nodes,
  using only the last-good graph, or reusing recovery output for execution are
  rejected because each can hide loss or weaken correctness.

## Implementation foundations

- Syntax-invalid pipeline source uses neutral fragment recovery from
  `src/haute/_parser_regex.py` only through the editor recovery service. It can
  conserve valid sibling nodes and diagnose a broken function body without
  producing a canonical graph for strict consumers.
- `src/haute/_config_io.py` and `src/haute/routes/_save_pipeline.py` already
  protect `_load_error` configs from ordinary persistence and stale cleanup.
- `src/haute/_types.py` carries a graph-level warning, and the frontend already
  displays it after an otherwise successful load.
- `frontend/src/hooks/useWebSocketSync.ts` retains the current graph when a
  `parse_error` frame arrives, providing the base for explicit degraded and
  last-known-good states.
- Strict fail-loud config behaviour is pinned by
  `tests/test_parser_fail_loudly.py`; recovery must complement rather than
  weaken that contract.
