# Submodels — High-Level Specification

## Purpose

A pipeline graph can grow large enough that a flat canvas of nodes stops being
navigable. Submodels let a user select a group of related nodes and collapse
them into a single named navigation unit: the selected nodes are extracted
into their own `modules/<name>.py` file, and the parent graph shows one placeholder node
in their place. The user can drill into the placeholder to see and edit the
group's internals, or ungroup it back into the parent at any time.

Submodels exist purely as a **code-organisation and GUI-navigation** concept.
Execution, tracing, and deployment never reason about submodel boundaries —
they operate on a single flat graph with the boundaries dissolved. This
component owns both sides of that representation boundary: constructing the hierarchical
(collapsed) form and flattening one or all placeholders back into executable graph nodes.

## Scope

In scope:
- Building a submodel placeholder node, classifying its input/output ports
  from cross-boundary edges, and rewiring those edges to/from the placeholder
  (`src/haute/_submodel_graph.py`).
- Resolving a submodel's `.py` file relative to the active pipeline directory,
  using the same convention as the parser (`src/haute/_submodel_paths.py`).
- The pure, I/O-free graph transform that extracts selected nodes out of a
  `PipelineGraph` into a new submodel (`src/haute/routes/_submodel_ops.py`).
- Flattening one named submodel or every submodel into a flat execution graph, including boundary
  handle consumption, edge-join target-role restoration, and edge deduplication
  (`src/haute/_flatten.py`).
- The three HTTP endpoints that expose creation, drill-down, and dissolution
  to the GUI: `POST /api/submodel/create`, `GET /api/submodel/{name}`,
  `POST /api/submodel/dissolve` (`src/haute/routes/submodel.py`).

Out of scope (owned elsewhere, linked where relevant):
- Parsing `pipeline.submodel("path")` calls out of a pipeline file's AST,
  parsing an individual submodel `.py` file's `@submodel.<type>` decorators,
  and merging parsed submodel graphs into the parent's hierarchical form
  (`_parser_submodels.py`, `parser.py`) — owned by
  [expression-parsing](../expression-parsing/high-level.md). This component reuses
  that parser's output shape and its placeholder-building helpers, but does
  not parse code itself.
- Generating the `.py` source for the parent file and each submodel file from
  a graph (`graph_to_code`, `graph_to_code_multi`) — owned by the codegen
  component. This component calls it but does not implement it.
- Transactional multi-file writes, path allowlisting, sidecar persistence,
  and the shared `save_lock` serialisation primitive — owned by
  [server-api](../server-api/high-level.md). Both submodel-mutating endpoints
  route through that component's `SavePipelineService` rather than writing
  files themselves.

## Behaviour

- **Creation** (`POST /api/submodel/create`): given a set of `node_ids`, a
  name, and the current graph, the selected nodes are removed from the parent
  graph and replaced with one `SUBMODEL`-typed placeholder node
  (id `submodel__<sanitized-name>`). Edges that crossed the selection
  boundary are rewired to the placeholder using synthetic handles —
  `in__<child_id>` for edges now entering the placeholder,
  `out__<child_id>` for edges now leaving it — and the parent graph gains a
  `submodels["<name>"]` metadata entry holding the file path, the child node
  ids, the inferred ports, and the submodel's own internal graph (nodes +
  internal edges only). A new `modules/<name>.py` file is written and the
  parent file is rewritten to reference it, through the same save transaction
  as a manual pipeline save.
- **Drill-down** (`GET /api/submodel/{name}`): returns the named submodel's
  internal graph, parsed fresh from the path recorded by the active pipeline,
  with any sidecar node positions applied. The `modules/<name>.py` convention
  is used only when no parseable discovered pipeline records the requested
  submodel. A broken sibling pipeline is logged and skipped rather than
  preventing an unrelated healthy submodel from opening.
  This is how the GUI renders the inside of a placeholder when the user opens
  it, including hand-authored references such as `lib/pricing.py`.
- **Dissolution** (`POST /api/submodel/dissolve`): the inverse of creation —
  the recorded submodel file is parsed again under the write lock, its
  authoritative graph is merged with its sidecar node positions and replaces
  the client's possibly stale metadata, and only then is the placeholder
  replaced by child nodes/internal edges via targeted `flatten_graph`. The
  parent file is rewritten and the recorded child file is deleted through one
  save transaction.
- **Minimum size.** A submodel must contain at least 2 nodes after any
  nonexistent or duplicate ids in the request are resolved against the actual
  graph; fewer is rejected.
- **No nesting.** A node that is itself a `SUBMODEL` placeholder cannot be
  selected as part of a new group — grouping is capped at one level. A hand-authored submodel
  file that contains `pipeline.submodel(...)` is rejected by the parser with every nested path
  named; it is never accepted with the nested graph omitted.
- **GUI creation is selection-based.** There is no submodel entry in the node palette or GUI
  library picker: the create endpoint only extracts an existing selection from the current graph.
  Source files can still hand-author `pipeline.submodel("path")` references, and the static parser
  resolves those paths; reuse is therefore possible in authored source within the project even
  though it is not a first-class GUI workflow.
- **Windows-reserved names are rejected up front.** A submodel name that would
  produce a module filename matching a Windows reserved device name (`CON`,
  `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, any casing, any extension) is rejected
  before any graph transformation runs, on every platform — so a pipeline
  authored on Linux/macOS stays loadable on a Windows checkout.
- **Boundary handles are the single source of truth for "which side of the
  boundary this edge attaches to."** `in__<child>` / `out__<child>` is the
  spelling produced when a submodel is created (or reconstructed by the parser) and validated by
  both codegen and flattening. Flattening requires the correct prefix and a
  child id present in the authoritative child graph; malformed boundaries
  raise `ParseError` before any edge or file is removed. On valid graphs it
  strips the prefix, restores the child endpoint and any authored port,
  regenerates an id from all visible and still-hidden port metadata, and is
  the inverse of creation. A
  regular (non-submodel) edge whose handle *happens* to look like
  `out__<something>` is never treated as a boundary handle by the consuming
  side — only an edge whose source or target actually is a submodel
  placeholder node is.
- **Per-file module code survives both representations.** Parsed submodel
  descriptions, preambles, and column-zero preserved blocks stay in the
  child metadata and are re-emitted in that child file. When a submodel is
  flattened for execution or dissolve, its preamble and preserved blocks are
  merged into the parent graph so the inlined nodes retain their support code.
- **Writes are serialised.** Both create and dissolve acquire the same shared
  write lock used by the manual pipeline save endpoint, so a submodel
  operation can never interleave with a concurrent save or with another
  submodel operation.

## Design rationale

- **Placeholder construction, port classification, and edge rewiring are one
  shared implementation, not two.** `_submodel_graph.py`'s three functions are
  used both by the parser (building the hierarchical view when a pipeline
  file referencing `pipeline.submodel(...)` is loaded from disk) and by the
  GUI "group as submodel" operation (`_submodel_ops.py`). Diverging
  implementations here would mean a submodel built once in the GUI and a
  submodel rebuilt on the next parse could disagree about port inference or
  edge-id naming.
- **The GUI-operation graph transform is pure.** `create_submodel_graph` takes
  and returns `PipelineGraph` values with no file I/O, so its extensive
  behavioural coverage (port inference, nesting rejection, node-count
  validation, name sanitisation) can run as fast in-memory unit tests, and the
  route layer's only job is turning its `ValueError`s into HTTP responses and
  handing its output to the save transaction.
- **Both mutating endpoints reuse the pipeline save transaction rather than
  writing files directly.** `SavePipelineService.save_graph_transactionally`
  gives create/dissolve the same path allowlist, rollback-on-partial-failure,
  sidecar staging, and stale-config cleanup that a manual save gets — a
  bespoke writer for submodel operations would be a second place those
  invariants could drift out of sync.
- **Submodel path resolution deliberately mirrors the parser's own module
  lookup**, rather than inventing a second convention: drill-down parses the
  active pipeline and resolves the exact project-relative path stored in its
  submodel metadata. `modules/<name>.py` remains a compatibility fallback
  only when no discovered pipeline records the name. The GUI and the
  actually executed pipeline therefore open the same file.
- **Nesting is disallowed by construction, not by convention.** Rejecting any
  selection that includes a submodel placeholder keeps the placeholder model,
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
  placeholder/port/rewire helpers are shared with.
- Provides `src/haute/_flatten.py::flatten_graph` to
  [execution-engine](../execution-engine/high-level.md), tracing, deployment, expression-parsing,
  and the dissolve route; those consumers request the flat form and never execute a
  `submodel__*` placeholder node directly.
- Depends on the codegen component through `SavePipelineService`: create emits the rewritten
  parent plus submodel files; dissolve uses `graph_to_code` only when no submodels remain and
  `graph_to_code_multi` when other placeholders remain.
- Depends on [server-api](../server-api/high-level.md) for
  `SavePipelineService`, the shared `save_lock`, `pipeline_dir()` resolution,
  sidecar position loading, and the codebase-wide sanitised-error-detail
  convention for internal failures.
- Depended on by the GUI graph editor, the sole caller of `/api/submodel/*`. The frontend
  submodel navigation UI itself — the `useSubmodelNavigation` hook (drill-in/out, create,
  dissolve) and the `SubmodelDialog` create/rename component — lives in the
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) component, not here.
- A downstream node fed across a submodel boundary lists that input by the referenced
  child node's sanitised label — the name the flattened code actually binds as the
  argument (see [frontend-node-editors](../frontend-node-editors/high-level.md) for the
  chip derivation and [codegen](../codegen/high-level.md)/`edge_input_name` for the
  backend rule) — never by the submodel placeholder's own label, which names the
  container, not the frame delivered.

## Failure model

- Selecting fewer than 2 nodes, or any node that is itself a submodel
  placeholder, raises `ValueError` inside the pure graph transform. The route
  logs the full detail server-side (it may embed graph-walk internals) and
  returns a generic sanitised `400` — the client never sees the specific
  validation message, only that the request was rejected.
- A submodel name that would collide with a Windows reserved device name
  returns a `400` with a specific, user-facing explanation (unlike the
  generic case above, this message is safe to show as-is since it only names
  the offending filename).
- A create or dissolve request without `source_file` is rejected with an
  explicit `400` — the frontend is expected to always track and send the
  originating pipeline file path.
- Dissolving a submodel name that is not present in the graph's `submodels`
  metadata returns `404`.
- Drilling into a submodel whose `.py` file does not exist on disk returns
  `404`.
- A discovered sibling pipeline that cannot be parsed is logged and skipped
  during drill-down lookup. Other discovered pipelines and the conventional
  module fallback remain eligible.
- A malformed route name or recorded reference (empty, NUL-containing, or a
  `{name}` containing `/` or `\`) returns `400`; a reference resolving
  outside the project returns `403`. These typed path failures are mapped
  before filesystem access rather than escaping as an uncaught `ValueError`.
- A missing, wrong-prefixed, or stale-child boundary handle raises
  `ParseError` from `flatten_graph`. Dissolve stops before persisting the
  parent or deleting the authoritative child file.
- Any failure partway through the underlying save transaction (config write,
  sidecar write, or the submodel file's own deletion on dissolve) triggers a
  best-effort rollback of every touched file and surfaces the original
  failure as `500`. A compensating filesystem operation can itself fail; that
  rollback failure is logged and may leave partial state. See
  [server-api](../server-api/high-level.md) for the transaction's full
  contract.
