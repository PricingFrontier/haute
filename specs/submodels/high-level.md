# Submodels — High-Level Specification

## Purpose

A pipeline graph can grow large enough that a flat canvas of nodes stops being
navigable. Submodels let a user select a group of related nodes and collapse
them into a single named navigation unit: the selected nodes are extracted
into their own `modules/<name>.py` file, and the parent graph shows one occurrence node
in their place. The user can drill into the occurrence to see and edit the
group's internals, or ungroup it back into the parent at any time.

Submodels exist purely as a **code-organisation and GUI-navigation** concept.
Execution, tracing, and deployment never reason about submodel boundaries —
they operate on a single flat graph with the boundaries dissolved. This
component owns both sides of that representation boundary: constructing the hierarchical
(collapsed) form and flattening one or all occurrences back into executable graph nodes.

## Scope

In scope:
- Building a submodel occurrence node, classifying its input/output ports
  from cross-boundary edges, and rewiring those edges to/from the occurrence
  (`src/haute/_submodel_instances.py`).
- Resolving a submodel's `.py` file relative to the active pipeline directory,
  using the same convention as the parser (`src/haute/_submodel_paths.py`).
- The pure, I/O-free graph transform that extracts selected nodes out of a
  `PipelineGraph` into a new submodel (`src/haute/routes/_submodel_ops.py`).
- Flattening one named submodel or every submodel into a flat execution graph, including boundary
  handle consumption, edge-join target-role restoration, and edge deduplication
  (`src/haute/_flatten.py`).
- The three HTTP endpoints that expose creation, drill-down, and dissolution
  to the GUI: `POST /api/submodel/create`, `GET /api/submodel/{definition_id}`,
  `POST /api/submodel/dissolve` (`src/haute/routes/submodel.py`).

Out of scope (owned elsewhere, linked where relevant):
- Parsing `pipeline.submodel("path")` calls out of a pipeline file's AST,
  parsing an individual submodel `.py` file's `@submodel.<type>` decorators,
  and merging parsed submodel graphs into the parent's hierarchical form
  (`_parser_submodels.py`, `parser.py`) — owned by
  [expression-parsing](../expression-parsing/high-level.md). This component consumes that parser's canonical
  definition/occurrence output but does not parse code itself.
- Generating the `.py` source for the parent file and each submodel file from
  a graph (`graph_to_code`, `graph_to_code_multi`) — owned by the codegen
  component. This component calls it but does not implement it.
- Transactional multi-file writes, path allowlisting, sidecar persistence,
  and the shared `save_lock` serialisation primitive — owned by
  [server-api](../server-api/high-level.md). Both submodel-mutating endpoints
  route through that component's `SavePipelineService` rather than writing
  files themselves.

## Behaviour

### Reusable definitions and instances (normative)

This is the only supported submodel representation. A submodel is represented
by one shared **definition** and any number of parent-graph **instances**:

- `PipelineGraph.submodels` is a definition registry keyed by immutable,
  opaque `definitionId`. A definition owns its file, internal graph, metadata,
  and public port contract. Loading the same resolved file more than once must
  resolve it once and reuse the same registry entry. The canonical graph is a
  typed model only; it exposes no dictionary-style compatibility access or
  mutation API.
- Every occurrence is represented solely by a `NodeType.SUBMODEL` node in the
  parent graph. Its node id is the occurrence's canonical name (`node.id == label == alias == name`);
  its typed config contains `definitionId`, the canonical identifier `alias`, and optional `instanceOf`.
  Exactly one occurrence for each definition omits `instanceOf` and is the
  editable definition owner. Every created instance points `instanceOf` at
  that owner; chains, self-references, missing owners, cross-definition owners,
  and multiple owners are invalid. The node owns its occurrence name
  and position, while its incident parent edges own its bindings.
  `node.id == node.data.label == config.alias` is an invariant enforced on validation (raising `ParseError` if violated).
  The registration signature `pipeline.submodel(file, name, *, instance_of=None)` accepts `name` as positional argument and does not accept `definition_id=`, `instance_id=`, `alias=`, or `label=`.
  Recovery uses the alias only.
- Renaming an occurrence changes its identity, exactly as renaming an ordinary
  node does: in the editor the node id stays until Save (the alias and every
  consumer binding change at once), codegen emits the name, and the reparse
  re-keys the node id and the `submodel_runtime/<name>/...` runtime ids.
  Accepted consequence: preview caches, trace snapshots and drilled targets
  keyed on the old runtime id are invalidated by the rename, and a drilled
  view whose occurrence no longer exists after a reload returns to the root
  view.
  There is no second top-level instances map. Internal definition positions are
  occurrence-local: grouping subtracts the first occurrence's origin and each
  expansion adds the selected occurrence's position, so copies never inherit
  another occurrence's absolute canvas coordinates.
- Definition ids and public port names are structural identity, not
  executable frame names or presentation. Renaming a definition, an occurrence, an
  internal node, or a file must not change them. Authored and generated source persists these ids
  explicitly. Missing definition, alias, or port identity is an
  invalid document and fails during parsing; identity is never inferred from a
  file name, occurrence name (the alias), registry key, or internal child id.
- A public input port has an immutable `name` and zero or more ordered
  internal targets `{nodeId, handleId}`. Each occurrence may bind that public
  input from at most one parent edge; the single binding fans out to every
  declared target. A public output port has an immutable
  `name` and exactly one internal source `{nodeId, handleId}`.
  Parent edges may address only `in__<name>` and `out__<name>` handles;
  internal child ids are never a parent-graph interface. After backend
  sanitisation, the public input port name is the child-side frame name.
  Occurrence names (or `<alias>__<name>`) name submodel outputs downstream;
  a public port has exactly one name, and it is a canonical identifier.
- Referential integrity is checked on load, mutation, flatten, and save:
  instance definitions must exist, public port names must be unique, endpoints
  must exist with matching directions, and parent bindings must use a declared
  port of the correct direction. Stale or malformed declared references fail
  with the affected definition, instance, and port identified.

Creating another instance is a pure parent-graph mutation performed by the
canvas: it adds a new occurrence with a fresh immutable id and stable alias
whose `instanceOf` points at the definition owner, and does not create or copy
a file. A definition containing a document-wide singleton node (`apiInput`,
`output`, or `liveSwitch`) cannot be instantiated because flattening would
create another executable occurrence of that singleton. Removing an instance
copy removes only that occurrence and its parent bindings, and is permitted
from every canvas delete surface. The definition owner is never raw-deleted
from any surface — it anchors the shared definition, so retiring it means
dissolving the submodel (after its copies are
removed or dissolved); blocked attempts say so visibly. The owner cannot be
dissolved while instances still reference it. Renaming an occurrence changes
presentation only. Drilling into the owner opens the shared definition editor and states
that edits affect every instance; drilling into an instance opens the same
definition as an explicitly read-only view. All mutation surfaces are disabled
there, including config fields, labels, node/edge edits, boundary edits,
paste/delete/undo/redo, palette drops, imports, and auto-layout. Preview, trace,
selection, copy, pan, and zoom remain available. In v1, an ordinary owner edit
that removes or changes a public port in use is blocked atomically and lists
every affected instance/port; per-instance internal overrides and nested
submodels remain unsupported. The deliberate exception is the Input inspector's
explicit remove action: it retires the selected public input together with all
of its internal routes and every parent binding across occurrences as one
undoable definition edit. Every collapsed
occurrence presents one generic `inputs` socket rather than one socket per
public frame. Multiple parent edges may share that socket. Each drop uses the
upstream frame's authoritative executable name to select a declared public port;
an owner mints a port named after that identity (suffixing `_2`, `_3` on
collision across both directions) when it is new, while a copy may bind only
names already present in the shared definition. The stored edge targets the canonical named `in__<name>` handle,
but every such handle is visually co-located at the one generic socket. The
named frames are exposed only after drill-in, where the Input boundary renders
them as a multi-frame output and each may be routed to one or more child targets.
The collapsed card keeps the structural `SUBMODEL` marker in its standard
accent header treatment. The occurrence's mutable display name remains in the
right-hand header pill but uses a 13px semibold primary-foreground label so it
is easy to identify.
The Input boundary itself has no `new input` affordance. A public
input with no child targets is a valid authored-but-unrouted interface state.
An unbound declaration can be serialised, but a parent edge bound to that port
blocks Save and execution clearly until the input is routed. Existing input
ports can be fanned out or relabelled subject to the in-use guard. Selecting the
owner's drilled Input boundary opens the standard Inputs chip list; removing a
chip performs the explicit cascading removal above, including for an unrouted
port. Because that reaches past the one connection an Inputs chip ordinarily
drops, its control names the public input and the internal routes and
occurrence connections it retires rather than reusing the shared
remove-connection wording. A read-only occurrence shows the same list without
removal controls. New
public output ports are added in the drilled view by wiring a child output to
the Output boundary. Instance copies retain the generic parent socket and
read-only drill-down.

Flattening expands each occurrence independently. Runtime node ids are
qualified from immutable `(instanceId, localNodeId)` values and retain a
reversible `(instanceId, localNodeId)` identity. Runtime-only identity metadata
must never be inserted into a node's domain config: strict node config schemas
receive exactly the authored config after schema-declared node-id references
are rewritten. A central, schema-led reference rewriter updates every declared
config field that carries a node id (including instance, data-input, banding,
Edge Join role, trace, and projection references). Unknown or stale declared
references fail loudly; opaque config fields are never guessed. Cross-instance
data flow is legal only from a declared public output to a declared public
input.
Extraction preserves the existing executable frame name by making it the new
public port's name at the same time as it rewires the edge; remaining parent
consumers therefore require no generated alias/port rename. Expansion resolves
public input and output names to the concrete upstream or qualified child
source names inside cloned and remaining configs. Ordinary Polars code keeps
its public logical name through an explicit `inputMapping` whenever the physical
expanded source name differs. Every schema-owned incoming-frame reference is
migrated with that edge identity, including selector fields, live-switch maps,
instance mappings, and each OUTPUT `outputMapping[].source_port`. Edge and
config rewrites are one atomic transform, so no consumer retains a stale
identity.

When the editor is drilled into one occurrence, preview and trace requests use
the parent hierarchical graph and qualify the clicked child from the explicit
occurrence identity before crossing the execution API boundary. Local editor
ids remain the identity used for selection, result caching, and rendering;
qualified runtime ids do not leak back into the visible definition graph.
The occurrence identity is authoritative navigation state, not inferred from
rendered boundary-port cards: definitions with no public ports (including a
group made entirely from disconnected source nodes) must use the same qualified
preview and trace targets.

Code generation emits each definition file once and one
`pipeline.submodel(...)` registration per occurrence. Parent connections use
public ports and never name internal children. Parse -> codegen -> parse must
preserve definition identity, occurrence names, labels, positions, ports,
and independent bindings. Every generated config-backed node resolves its
sidecar from the owning pipeline directory. A definition emitted under
`modules/` must not reinterpret `config/...` relative to the module directory.

Acceptance requires a single file-backed definition instantiated twice with
different aliases, positions, and bindings to survive save -> reload ->
flatten/execute -> save. The definition artifact is emitted once, both
registrations remain distinct, runtime ids and origins cannot collide, and an
internal definition edit that preserves its interface is visible through both
instances.

Acceptance also requires grouping existing config-backed source nodes, saving,
reloading, drilling into the occurrence, and previewing each child. The request
must target that occurrence's qualified runtime node, strict source config must
remain unpolluted by composition metadata, and generated standalone execution
must resolve the original pipeline-owned sidecars.

- **Creation** (`POST /api/submodel/create`): given an ordered list of unique,
  existing `node_ids`, a non-blank name, the current graph, and the document
  revision on which that graph is based, the selected nodes are removed from
  the parent graph and replaced with one `SUBMODEL` occurrence. The GUI reads
  `nodes`, `edges`, and `submodels` together from the canonical graph store at
  submission time; an effect-mirrored ref is never the source of a create
  request.  Creation sets the occurrence node id to the sanitised name (`node.id = sm_name`);
  the initial definition id and alias are the sanitised name, and the occurrence config is exactly
  `{definitionId, alias}`. Cross-boundary edges are grouped into stable public
  ports: each logical input created by extraction records one or more ordered
  internal targets and each output records one internal source. Parent handles are
  `in__<name>`/`out__<name>`, never internal node ids, and each logical
  input produces exactly one parent binding even when it fans out internally.
  Each public port name preserves the executable name that crossed that boundary
  before grouping, so schema-owned selectors and Polars input mappings remain
  unchanged; a duplicate executable name is disambiguated with sequential numeric
  suffixes (`_2`, `_3`, ...) across both input and output ports. The unified port
  name is validated as a canonical identifier. Codegen still rejects a duplicate
  derived input name at save. The
  parent registry gains one typed definition keyed by its exact definition id
  and containing the file, structured ports, and internal graph. The
  occurrence is placed at the selected bounding-box centre; child positions
  are stored relative to that origin and definition graph order is retained.
  Creation is an editor transform only: it atomically replaces the browser's
  graph, creates one undo entry, and marks the document dirty. It does not
  write the parent, module, config, or sidecar. The existing explicit **Save**
  action later persists the whole graph through the normal transaction,
  derives the new managed module from the persisted-versus-submitted
  definition diff, and repeats the no-clobber check before writing. Closing
  the browser before Save discards the grouping. The browser serialises the
  complete persisted graph snapshot used for each create or dissolve request
  together with its pipeline identity, source revision, preserved blocks, and
  request order. It commits the returned transform only while that complete
  context is still current; a stale response is discarded with a visible error
  instead of replacing intervening edits or a newer transform.
- **Drill-down**
  uses the canonical definition graph already embedded in the current
  in-memory registry, so unsaved definitions open without a disk re-fetch.
  `GET /api/submodel/{definition_id}?source_file=<parent>` remains a
  read-only persisted-document endpoint that resolves the
  exact definition registry key from the named parent, parses the recorded
  file fresh, applies sidecar positions, and verifies that the child's
  declared `definition_id` matches the route identity. The parent source is
  mandatory: lookup never scans unrelated pipelines, derives a filename from
  the route, or infers identity from a display name. Every occurrence of the
  same definition opens this one shared definition graph.
- **Dissolution** (`POST /api/submodel/dissolve`): the inverse of creation —
  the selected occurrence is expanded from the submitted canonical definition
  graph via targeted `flatten_graph`. The definition remains while any sibling
  occurrence survives. Dissolve is one undoable dirty editor transform and
  does not rewrite the parent or delete a module/sidecar. On explicit Save,
  the normal transaction derives removed definitions. The recorded child
  source and sidecar are deleted only when the child carries a matching Haute
  ownership marker and no other parseable pipeline references the same resolved
  file. Hand-authored, shared, ambiguously owned, or potentially referenced
  files are retained. The transform response reports only the transformed
  graph, unchanged source revision, and selected instance/definition identity;
  it exposes no file-deletion compatibility state.
- **Selection validity.** A submodel must contain at least 2 unique node ids,
  every requested id must exist in the submitted graph, and duplicates are an
  invalid client request rather than an instruction to coalesce entries. A
  stale id rejects the entire operation; creation never silently groups a
  subset of the user's selection. Selections may be disconnected and may have
  no cross-boundary edges.
- **No nesting.** A node that is itself a `SUBMODEL` occurrence cannot be
  selected as part of a new group — grouping is capped at one level. A hand-authored submodel
  file that contains `pipeline.submodel(...)` is rejected by the parser with every nested path
  named; it is never accepted with the nested graph omitted.
- **GUI creation is selection-based.** There is no submodel entry in the node palette or GUI
  library picker: the create endpoint only extracts an existing selection from the current graph.
  Existing occurrences expose **Create instance**, which creates an unbound
  occurrence of the same definition without copying a file. Source files can
  also hand-author explicit reusable `pipeline.submodel(...)` registrations.
- **Windows-reserved names are rejected up front.** A submodel name that would
  produce a module filename matching a Windows reserved device name (`CON`,
  `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, any casing, any extension) is rejected
  before any graph transformation runs, on every platform — so a pipeline
  authored on Linux/macOS stays loadable on a Windows checkout.
- **Names are validated at the API boundary.** Whitespace-only names are
  rejected rather than silently becoming `unnamed_node`; the trimmed name is
  the input to sanitisation. A canonical name, occurrence alias, or occurrence
  identity already present in the parent under any casing is a conflict,
  matching the case-insensitive module no-clobber rule.
- **Canonical boundary identity and executable naming are separate.** Parent edges
  address only `in__<name>` and `out__<name>` handles. Public input port names
  and occurrence names provide the executable frame names, internal endpoint ids stay
  definition-private, and public ports have a single name field. Drill-down projects the
  definition contract as one composite Input and one composite Output card.
  Declared outputs remain visible and round-trip without consumers. Draft or
  stale boundary handles are rejected before save or execution.
- **Per-file module code survives grouping and dissolve.** Parsed submodel
  descriptions, preambles, and column-zero preserved blocks stay in the
  child metadata and are re-emitted in that child file. When a submodel is
  flattened for execution or dissolve, its preamble and preserved blocks are
  merged into the parent graph so the inlined nodes retain their support code.
  Support code already present in the parent is never appended a second time:
  preamble presence is detected by whole-line block containment (not exact
  blob identity), so a staged dissolve of one occurrence followed by a later
  expansion of the owner still merges each definition preamble exactly once,
  and a partial line overlap (`import a` vs `import ab`) never suppresses a
  genuinely new line. Create then dissolve reaches a stable representation.
- **Writes are serialised and freshness-checked.** Both create and dissolve acquire the same shared
  lock used by the manual pipeline save endpoint while reading the persisted
  revision and computing a transform. Before transforming anything,
  each route compares `base_revision` with the editor document's deterministic
  raw-artifact revision of the current parent and referenced child state. A mismatch returns
  `409`. A successful transform leaves that revision unchanged. The lock is process-local
  and is not a multi-worker filesystem lock.

## Design rationale

- **Parser and GUI creation converge on the same typed representation.** Both
  construct `SubmodelDefinition`, `SubmodelInstanceConfig`, and structured
  public ports directly, then pass through the same referential-integrity
  validation. Neither path constructs an intermediate child-id representation.
- **The GUI-operation graph transform is pure.** `create_submodel_graph` takes
  and returns `PipelineGraph` values with no file I/O, so its extensive
  behavioural coverage (port inference, nesting rejection, node-count
  validation, name sanitisation) can run as fast in-memory unit tests, and the
  route layer's only job is turning its `ValueError`s into HTTP responses and
  handing its output to the save transaction.
- **There is one persistence boundary.** Submodel endpoints only validate and
  transform graphs. `POST /api/pipeline/save` is the sole route that writes
  parent code, modules, configs, or sidecars and the sole route that deletes
  detached managed modules.
- **File ownership is explicit and conservative.** A GUI-created child gets a
  `managed_parent` marker only during explicit Save. Save compares submitted
  definitions with the persisted parent; a new definition becomes a
  transaction-local claim only after source and sidecar pass no-clobber. A
  removed definition is deleted only when its marker and a complete reference
  audit authorise it. Uncertainty always retains the file.
- **Submodel path resolution deliberately mirrors the parser's own module
  lookup**, rather than inventing a second convention: drill-down parses the
  parent named by `source_file` and resolves the exact project-relative path
  stored in its submodel metadata. There is no global name-only fallback. The
  GUI and the actually executed pipeline therefore open the same file.
- **Nesting is disallowed by construction, not by convention.** Rejecting any
  selection that includes a submodel occurrence keeps the occurrence model,
  the `in__`/`out__` handle scheme, and the flatten pass single-level;
  recursive nesting (a submodel containing another submodel) was considered
  as a possible future phase and never implemented. This closes the
  GUI-authored path; the expression parser closes the hand-authored path by
  raising `ParseError` with the containing file and every nested reference.
  A cycle therefore cannot be hidden by truncating the authored hierarchy.
- **Name-collision detection lives at save time, not in `haute lint`.**
  Extending `haute lint` to warn about (and block on) node-name collisions
  across submodels was proposed and never built — the CLI's lint command has
  no submodel-specific rules today. Collision checking is instead part of
  `SavePipelineService.save`, after create or dissolve has produced the graph
  that will actually be persisted. The routes do not duplicate that private
  validation step, and there is no independent `haute lint` check for it.

## Interactions

- Depends on [expression-parsing](../expression-parsing/high-level.md) for parsing
  submodel `.py` files (`parse_submodel_file`) and for the hierarchical-merge
  logic (`_parser_submodels.py::merge_submodels`) that this component's
  occurrence/port/rewire helpers are shared with.
- Provides `src/haute/_flatten.py::flatten_graph` to
  [execution-engine](../execution-engine/high-level.md), tracing, deployment, expression-parsing,
  and the dissolve route; those consumers request the flat form and never execute a
  canonical occurrence node directly.
- Depends on [codegen](../codegen/high-level.md) through the explicit Save transaction:
  codegen is invoked only by the explicit Save transaction (`SavePipelineService`,
  server-api); the submodel routes neither generate nor write code.
- Depends on [server-api](../server-api/high-level.md) for
  `SavePipelineService`, the shared `save_lock`, `pipeline_dir()` resolution,
  sidecar position loading, and the codebase-wide sanitised-error-detail
  convention for internal failures.
- Depended on by the GUI graph editor, the sole caller of `/api/submodel/*`. The frontend
  submodel navigation UI itself — the `useSubmodelNavigation` hook (drill-in/out, create,
  dissolve) and the `SubmodelDialog` create/rename component — lives in the
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) component, not here.
- Depends on server-api's document revision and sidecar ownership fields. The
  frontend retains the latest `source_revision` from load, save, WebSocket
  refresh and supplies it as `base_revision` on the next submodel transform.
  Create and dissolve return that revision unchanged; only explicit Save
  replaces it with a newly committed revision.
- A downstream node fed across a canonical submodel boundary resolves the
  occurrence's `out__<name>` handle through the referenced definition to
  that public output's internal `{nodeId, handleId}` data source. Its parent
  input name is the occurrence's own sanitised name (`_sanitize_func_name(alias)`),
  or `<name>__<port_name>` when the referenced definition declares more than one
  output port (aliases are unique among a parent's nodes by construction;
  public ports have one name). See
  [frontend-node-editors](../frontend-node-editors/high-level.md)
  for chip derivation and [codegen](../codegen/high-level.md) for the backend
  rule.
- A downstream `edgeJoin`'s base/join role is governed solely by each boundary edge's target
  handle. Resolve `out__<name>` for the edge's executable input identity (the occurrence name or `<name>__<port_name>`) before duplicate-name
  validation, so two public outputs of one occurrence remain distinct even though their parent
  edges share the same occurrence `source`.

## Failure model

- A blank name, fewer than 2 unique ids, duplicate ids, or selecting a node
  that is itself a submodel occurrence returns `400` with a stable, safe,
  actionable explanation. An id absent from the submitted graph, an existing
  canonical submodel name, or a changed `base_revision` returns `409`; no graph
  transform or write runs.
- Creation performs a read-only module no-clobber preflight (`validate_new_module_files`);
  any configuration collision raises `ConfigError` mapped to `400`. Any other unexpected
  `ValueError` during graph transformation is logged server-side and returned as a sanitised
  `400` error, never leaking internal graph walk details or escaping as `500`.
- If the new module path already exists under any casing, creation returns
  `409` before any file is touched.
- A submodel name that would collide with a Windows reserved device name
  returns a `400` with a specific, user-facing explanation (unlike the
  generic case above, this message is safe to show as-is since it only names
  the offending filename).
- A create or dissolve request without `source_file` is rejected with an
  explicit `400` — the frontend is expected to always track and send the
  originating pipeline file path.
- Dissolving an `instance_id` that does not identify a canonical occurrence
  returns `404`.
- A canonical occurrence whose definition is missing from the submitted
  registry returns `400`.
- Drilling into a submodel whose `.py` file does not exist on disk returns
  `404`.
- A missing or invalid drill-down `source_file`, a parent that does not record
  the requested definition id, or a missing recorded child returns `400`/`404` as
  appropriate. Unrelated pipelines cannot influence drill-down lookup.
- A malformed definition id or recorded reference (empty, NUL-containing, or
  containing `/` or `\`) returns `400`; a reference resolving
  outside the project returns `403`. These typed path failures are mapped
  before filesystem access rather than escaping as an uncaught `ValueError`.
- A null inbound handle on an occurrence raises a contextual `ParseError`
  (matching `_port_name`) during `flatten_graph` through
  `resolve_submodel_instances`. A missing outbound handle, a wrong-prefixed
  mapped handle, or a stale child reference likewise raises `ParseError`.
  Because dissolve is transform-only, these failures leave the submitted
  graph unchanged and cannot touch the parent or child files.
- Any failure partway through the later explicit Save transaction (config
  write, sidecar write, or managed child deletion) triggers a best-effort
  rollback of every touched file and surfaces the original
  failure as `500`. A compensating filesystem operation can itself fail; that
  rollback failure is logged and may leave partial state. See
  [server-api](../server-api/high-level.md) for the transaction's full
  contract.
- Dissolve responses expose no child-file lifecycle state; the transform route
  never deletes the child source or sidecar.
- On explicit Save, a child without a matching ownership marker, a child
  referenced by another healthy pipeline, or any unparseable sibling that
  makes a complete reference audit impossible is retained. Uncertainty never
  authorises deletion.
- Sanitised node-name collisions discovered by `SavePipelineService` return a
  specific `400` before writes begin.
