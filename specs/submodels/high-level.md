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
they operate on a single flat graph with the boundaries dissolved.

A definition is also the unit of reuse. An **instance** applies a definition's
logic to another set of inputs, through an explicit input mapping: an instance
of a submodel reuses the group, and an instance of a single node reuses that
node once it is wrapped in a one-node submodel. A submodel works one way
however many nodes it holds, and an instance one way whatever it reuses.
There is no other reuse mechanism: node-level instances are refused. This
component owns both sides of that representation boundary: constructing the hierarchical
(collapsed) form and flattening one or all occurrences back into executable graph nodes.

## Scope

In scope:
- Building a submodel occurrence node, classifying its input/output ports
  from cross-boundary edges, and rewiring those edges to/from the occurrence
  (`src/haute/_submodel_instances.py`).
- Resolving a submodel's `.py` file relative to the active pipeline directory,
  and the `modules/<name>.py` convention by which a pipeline imports a definition
  module, shared with the parser and codegen (`src/haute/_submodel_paths.py`).
- The pure, I/O-free graph transforms that extract selected nodes out of a
  `PipelineGraph` into a new submodel and wrap one node in a one-node
  definition for Create Instance (`src/haute/routes/_submodel_ops.py`).
- Flattening one named submodel or every submodel into a flat execution graph, including boundary
  handle consumption, edge-join target-role restoration, and edge deduplication
  (`src/haute/_flatten.py`).
- The four HTTP endpoints that expose creation, a node's Create Instance,
  drill-down, and dissolution to the GUI: `POST /api/submodel/create`,
  `POST /api/submodel/instance`, `GET /api/submodel/{definition_id}`,
  `POST /api/submodel/dissolve` (`src/haute/routes/submodel.py`).

Out of scope (owned elsewhere, linked where relevant):
- Reading a pipeline file's submodel imports (`from modules import <name>`) and its
  `pipeline.submodel(<name>.submodel, "<occurrence>")` registrations out of its AST,
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
  The registration signature `pipeline.submodel(definition, /, name, *, instance_of=None)`
  takes the definition imported from its module (`<module>.submodel`) positionally, then the
  occurrence `name`, and does not accept `definition_id=`, `instance_id=`, `alias=`, or `label=`.
  Recovery uses the alias only.
- A definition's source is a Python module directly in the `modules/` folder beside the
  pipeline, `modules/<name>.py`, named as a node is: a canonical identifier of ASCII letters,
  digits and underscores that does not start with a digit or `__`, and neither a keyword nor
  a Python built-in. The pipeline file imports it,
  `from modules import <name>`, and registers each occurrence of the definition it defines:

  ```python
  from modules import factors

  pipeline.submodel(factors.submodel, "factors")
  pipeline.submodel(factors.submodel, "again", instance_of="factors")
  ```

  It is an ordinary Python import: running or importing the pipeline file imports the
  module, `Pipeline.run()` and `score()` execute each occurrence from the definition it
  registered, and an editor's go-to-definition and a type checker follow it, while the parser
  reads the same statements without importing anything. The import names the file
  (`from modules import factors` is `modules/factors.py`), so a definition has exactly one
  spelling. A registration by file path, `pipeline.submodel("modules/factors.py", ...)`, is a
  removed form: the parser refuses it with the import form that replaces it, and nothing
  migrates it (the [canonical-only policy](../README.md#canonical-only-format-policy)).
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
`workbenchInput`, `output` or `workbenchOutput`) cannot be instantiated because flattening would
create another executable occurrence of that singleton. Removing an instance
copy removes only that occurrence and its parent bindings, and is permitted
from every canvas delete surface. The definition owner is never raw-deleted
from any surface — it anchors the shared definition, so retiring it means
dissolving the submodel (after its copies are
removed or dissolved); blocked attempts say so visibly. The owner cannot be
dissolved while instances still reference it. Renaming the owner changes
presentation only; an instance's outputs are named after it, so renaming an
instance renames those frames for its consumers (see Reuse). Drilling into the owner opens the shared definition editor and states
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
public frame. Multiple parent edges may share that socket. Each drop binds a declared
public port by the binding rule under Reuse: an owner binds the port named like
the upstream frame's authoritative executable name, or mints one named after it
(suffixing `_2`, `_3` on collision across both directions) when none is; an
instance binds the port named like the frame, else the one port it has not
bound, else the port the user picks, and never mints one. The stored edge targets the canonical named `in__<name>` handle,
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
source names inside cloned and remaining configs. Code that names its inputs,
a Transform's or a Load File's (the stepped surfaces whose steps may name their
inputs), keeps its public logical name through an explicit `inputMapping`
whenever the physical expanded source name differs; steps on those surfaces
have their input references rewritten instead. Every schema-owned incoming-frame reference is
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

Code generation emits each definition module once, one `from modules import ...`
statement in the pipeline file naming every definition's module, and one
`pipeline.submodel(<module>.submodel, "<name>")` registration per occurrence. A
definition whose file is not a `modules/<name>.py` module cannot be imported by name, so
codegen refuses it rather than emit a registration nothing could resolve. Parent
connections use public ports and never name internal children. Parse -> codegen -> parse
must preserve definition identity, occurrence names, labels, positions, ports,
and independent bindings. Every generated config-backed node resolves its
sidecar from the owning pipeline directory. A definition emitted under
`modules/` must not reinterpret `config/...` relative to the module directory:
its constructor ends with `pipeline_dir=".."`, the way from `modules/` back to the
pipeline, which codegen derives from the definition's file and the parser rejects when it
disagrees with where the file sits, so a node of the definition run on its own reads the
pipeline's sidecars. When Save writes a definition module into a `modules/` folder that has
no `__init__` file, it writes an empty `<pipeline_dir>/modules/__init__.py`, and never
overwrites one that exists: `modules` is then a regular package, so the pipeline's import
resolves to the folder beside it and never to another `modules` folder on `sys.path`.

Acceptance requires a single definition module instantiated twice with
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
  `{definitionId, alias}`. A grouping whose result breaks the codegen naming rule where the
  submitted graph did not (a reserved or built-in name, a name a selected child or another node
  takes) is refused with HTTP 400 and the rule's messages, and nothing changes. Violations are
  compared by kind, name and the labels involved, so one the graph already had does not block
  grouping when its node moves into the new definition; the Create
  Submodel dialog shows the refusal with the typed name. Cross-boundary edges are grouped into stable public
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
  graph via targeted `flatten_graph`, an instance's nodes named
  `<instance>__<child>` so they do not repeat the definition's names. The definition remains while any sibling
  occurrence survives. Dissolve is one undoable dirty editor transform and
  does not rewrite the parent or delete a module/sidecar. On explicit Save,
  the normal transaction derives removed definitions. The recorded child
  source and sidecar are deleted only when the child carries a matching Haute
  ownership marker and no other parseable pipeline references the same resolved
  file. Hand-authored, shared, ambiguously owned, or potentially referenced
  files are retained. The transform response reports only the transformed
  graph, unchanged source revision, and selected instance/definition identity;
  it exposes no file-deletion compatibility state.
- **Selection validity.** A grouped submodel must contain at least 2 unique node ids
  (Create Instance wraps a single node in a one-node submodel),
  every requested id must exist in the submitted graph, and duplicates are an
  invalid client request rather than an instruction to coalesce entries. A
  stale id rejects the entire operation; creation never silently groups a
  subset of the user's selection. Selections may be disconnected and may have
  no cross-boundary edges.
- **No nesting.** A node that is itself a `SUBMODEL` occurrence cannot be
  selected as part of a new group — grouping is capped at one level. A hand-authored submodel
  file that contains a `pipeline.submodel(...)` registration, whatever its arguments, is
  rejected by the parser with the line of every nested registration (`nested_lines`); it is
  never accepted with the nested graph omitted. An import from `modules` in a definition file
  is ordinary preamble.
- **GUI creation is selection-based.** There is no submodel entry in the node palette or GUI
  library picker: the create endpoint only extracts an existing selection from the current graph.
  Existing occurrences expose **Create instance**, which creates an unbound
  occurrence of the same definition without copying a file; a plain node's
  Create Instance wraps it in a one-node definition (see Reuse). Source files can
  also hand-author explicit reusable registrations: `from modules import <module>`, then one
  `pipeline.submodel(<module>.submodel, "<name>")` per occurrence.
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
  and public output port names provide the executable frame names (an instance's
  outputs are named after it; see Reuse), internal endpoint ids stay
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
- **Writes are serialised and freshness-checked.** Create, a node's Create Instance and dissolve
  acquire the same shared lock used by the manual pipeline save endpoint while reading the persisted
  revision and computing a transform. Before transforming anything,
  each route compares `base_revision` with the editor document's deterministic
  raw-artifact revision of the current parent and referenced child state. A mismatch returns
  `409`. A successful transform leaves that revision unchanged. The lock is process-local
  and is not a multi-worker filesystem lock.

### Reuse: instances of nodes and submodels (normative)

A submodel groups nodes that form one process. An **instance** reuses logic on
another set of inputs: an instance of a submodel is another occurrence of its
definition, and an instance of a single node is another occurrence of a
one-node definition. Nothing below depends on how many nodes a definition
holds. There is no other reuse mechanism: `@pipeline.instance`,
an `of=` keyword on a node decorator and an `instanceOf` on an ordinary node are
refused (see Failure model), and nothing migrates them. `inputMapping` on a
plain Transform or Load File is not reuse: it keeps the names its code reads
across a rewire.

- **An instance's input mapping is its incoming edges.** Each parent edge into
  an occurrence names the public input it feeds in its `in__<name>` handle, so
  an instance may bind any upstream frame to any of its definition's public
  inputs, at most one frame each. The edge set is the whole mapping; there is
  no separate mapping field. Flattening binds each public input's targets to
  its edge's frame and keeps the names the definition's code reads
  (`inputMapping` on Transform and Load File code, renamed step references),
  as it does for every occurrence.
- **Binding a dropped connection.** A connection dropped on an instance binds
  the public input named like the incoming frame's executable name; failing
  that, the one public input the instance has not bound; failing that, the
  editor asks which of its unbound inputs the connection feeds. A drop on an
  instance whose every public input is bound is refused, naming the instance.
  An instance never mints a public input. The owner binds a public input named
  like the frame, or mints one named after it. No path pairs inputs by
  position or by substring.
- **The input mapping in the panel.** Every occurrence's panel lists its
  definition's public inputs, each with the connection that feeds it on this
  occurrence, or "not connected". Choosing another of the occurrence's
  connections re-targets that edge to the input as one undoable edit, leaving
  the input it fed unbound. An unbound input is an ordinary editing state: the
  nodes it targets run without it and report what their code misses, as an
  unconnected node does.
- **Output names.** An instance has its own name, and the frames it outputs
  are named after it, so one consumer can read the owner and its instances
  side by side whenever their names differ (an owner output named like an
  instance is refused at the consumer, as any duplicate input name is). The owner names each
  output by its public output port name. An instance names its output by its
  own name when the definition declares one public output, and each output
  `<instance>_<port>` when it declares several. An occurrence card labels
  each output with its name. Renaming an instance renames its frames for its
  consumers, coded consumers keeping their code through `inputMapping` as for
  any rename, and so does a definition edit that takes it from one public
  output to several or back; renaming the owner renames nothing downstream.
  An edit that would give a consumer two inputs of one name is refused before
  it applies.
- **Missing columns.** For each public input that an instance and its owner
  both bind, the instance reports, as `missing` schema warnings naming the
  input, the columns the owner's frame carries that the instance's does not,
  read from schemas alone (the owner's frame is resolved without rows when the
  preview did not reach it); an owner frame whose schema cannot be resolved is
  reported as unavailable, never as no difference. An instance's panel lists
  them by input.
- **Preview.** Clicking any occurrence, owner or instance, previews its
  outputs without drilling in, one at a time: the first declared output, and
  any other the user picks, each shown under the name its consumers read. An
  output previews its source node, and that node's frame, run on this
  occurrence's inputs as a qualified runtime target. An occurrence with no
  public output has nothing to preview.
- **Create Instance.** On a submodel occurrence it places another occurrence
  of the definition, as described above. On a plain node it wraps the node in
  place in a one-node definition: the definition and its module take the
  node's name (`modules/<name>.py`, suffixed `_2`, `_3` while a module of that
  name exists under any casing); the owner occurrence keeps the node's name,
  position and connections, its output becoming the one public output, named
  as its frame was, so its consumers read what they read before, and each
  incoming frame a public input named as the node read it (suffixed `_2` when
  that is the output's name, the node still reading its old name); and an unbound
  instance, named as a submodel copy is (`<name>_2`), is placed beside it.
  From then on both are ordinary occurrences: the node is edited by drilling
  into the owner, and Dissolve, once no instance references the owner, turns
  it back into the plain node. It is one undoable editor transform through
  `POST /api/submodel/instance`, written only by Save, as grouping is. It is
  offered on every node type a definition can hold, apart from the
  document-wide singletons (`apiInput`, `workbenchInput`, `output`,
  `workbenchOutput`) and the Source Switch, and only in the parent view.

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
- **A definition is imported, not named by a path.** An ordinary
  `from modules import <name>` makes the pipeline file plain Python that runs, and that
  editors and type checkers follow, without Haute's help, and it leaves one spelling per
  definition, since the import name is the file name. The parser reads the same import
  statically and derives the file from it, so the parsed graph and the imported module are
  the same file. The path-string registration this replaced is refused rather than migrated,
  because Haute keeps only its current canonical source form.
- **Submodel path resolution deliberately mirrors the parser's own module
  lookup**, rather than inventing a second convention: drill-down parses the
  parent named by `source_file` and resolves the exact project-relative path
  stored in its submodel metadata, the file its import names. There is no global
  name-only fallback. The GUI and the actually executed pipeline therefore open the
  same file.
- **One reuse mechanism.** Node-level instances paired inputs by name, then by
  substring, then by position; a pipeline holding one did not run as plain
  Python; and every engine and editor layer had to carry them on a second
  path. Submodel instances bind named public inputs explicitly, run standalone
  and expand through the one flatten path. Making a node's instance an
  occurrence of a one-node submodel keeps what node instances did (the same
  logic on other inputs, edits followed everywhere) on that machinery, and
  gives every instance an explicit input mapping. A one-node submodel is drawn
  and edited as any other, so a definition gaining or losing nodes changes
  nothing about how it, its instances or their output names behave; the cost
  is that a reused node shows on the canvas as a submodel rather than as
  itself.
- **Nesting is disallowed by construction, not by convention.** Rejecting any
  selection that includes a submodel occurrence keeps the occurrence model,
  the `in__`/`out__` handle scheme, and the flatten pass single-level;
  recursive nesting (a submodel containing another submodel) was considered
  as a possible future phase and never implemented. This closes the
  GUI-authored path; the expression parser closes the hand-authored path by
  raising `ParseError` with the containing file and the line of every nested
  registration.
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
  input name is the output's name (see Reuse): the sanitised public output
  port name from an owner and, from an instance, the instance's name (one
  output) or `<instance>_<port>` (several). See
  [frontend-node-editors](../frontend-node-editors/high-level.md)
  for chip derivation and [codegen](../codegen/high-level.md) for the backend
  rule.
- A downstream `edgeJoin`'s base/join role is governed solely by each boundary edge's target
  handle. Resolve `out__<name>` for the edge's executable input identity (the output's name) before duplicate-name
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
- A pipeline file whose submodel imports or registrations are not the canonical form fails
  to parse with a `ParseError` (on the statement's line, apart from an unused import, which
  names its modules), so the editor document is degraded and Save is refused until the
  source is corrected: a registration by file path (positional or `file=`), the removed
  form, whose remediation is the exact import form; an import from `modules` in any other
  spelling (relative, dotted, `import modules...`, an alias, `*`) or a module imported
  twice; a module name that is not a canonical identifier, starts with `__` or would hide a
  Python built-in; a registration naming a module
  the file does not import, whose remediation adds the import; a registration before the
  import it names, which Python could not run; an import no registration uses; and an import
  sharing its lines with other code (after a `;`) or inside a preserved block, which a save,
  rewriting the import, would lose or write twice. The editor reports a malformed import as
  `submodel_import_invalid` and a refused registration as `submodel_registration_invalid`; a
  removed path-form registration, and one naming a module the file does not import, keep
  their occurrence as an unavailable card carrying the parser's message (the card names the
  file the import would read). An unused import has no element of its own and is listed as
  the pipeline-level `pipeline_parse_invalid` diagnostic at the import's line, with the
  parser's remediation. Removing an unavailable occurrence also takes its module's name out
  of the import when no other registration uses it. No recovery action rewrites any of them.
- Save refuses with `400` a graph whose definition file is not a `modules/<name>.py` module,
  before writing anything.
- Create Instance on a document-wide singleton, a Source Switch, a node in a
  drilled definition or a node that does not exist is refused before any
  transform: `400` from `POST /api/submodel/instance` with the reason, or the
  editor's visible refusal before a request. The route shares create's
  `source_file` and `base_revision` checks (`400`, `409`), and the module it
  derives passes the same read-only no-clobber preflight.
- A connection dropped on an instance whose every public input is bound is
  refused in the editor, naming the instance; no edge is added.
- A pipeline file that registers a node with `@pipeline.instance`, gives a node
  decorator `of=`, or whose node config carries `instanceOf` on anything but a
  submodel occurrence fails to parse with a `ParseError` naming the removed form
  and its replacement: the node in its own module `modules/<name>.py`,
  registered as `pipeline.submodel(<name>.submodel, "<original>")` and
  `pipeline.submodel(<name>.submodel, "<instance>", instance_of="<original>")`,
  with each input bound by `pipeline.connect(..., target_port="<input>")`. The
  editor shows that node unavailable with the message; nothing migrates it. The
  live `Pipeline` has no `instance` decorator.
- The live `Pipeline.submodel()` raises `TypeError` when given a file path, naming the
  import form, or anything else that is not a `haute.Submodel`, such as the imported module
  itself. A definition module that defines no `submodel` fails with Python's own
  `AttributeError` when the pipeline file is imported.
