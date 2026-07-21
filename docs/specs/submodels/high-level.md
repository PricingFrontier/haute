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
- Resolving a submodel's `.py` file on disk, matching the same
  pipeline-local-first / project-root-fallback preference the parser itself
  uses (`src/haute/_submodel_paths.py`).
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
  internal graph, parsed fresh from its `.py` file, with any sidecar node
  positions applied. This is how the GUI renders the inside of a placeholder
  when the user opens it.
- **Dissolution** (`POST /api/submodel/dissolve`): the inverse of creation —
  the named submodel's placeholder is replaced by its child nodes and internal
  edges (via `flatten_graph`, scoped to just that one
  submodel), the parent file is rewritten, and the `modules/<name>.py` file is
  deleted.
- **Minimum size.** A submodel must contain at least 2 nodes after any
  nonexistent or duplicate ids in the request are resolved against the actual
  graph; fewer is rejected.
- **No nesting.** A node that is itself a `SUBMODEL` placeholder cannot be
  selected as part of a new group — grouping is capped at one level.
- **GUI creation is selection-based.** There is no submodel entry in the node palette or GUI
  library picker: the create endpoint only extracts an existing selection from the current graph.
  Source files can still hand-author `pipeline.submodel("path")` references, and the static parser
  resolves those paths; reuse is therefore possible in authored source within the project even
  though it is not a first-class GUI workflow.
- **Windows-reserved names are rejected up front.** A submodel name that would
  produce a module filename matching a Windows reserved device name (`CON`,
  `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, any casing, any extension) is rejected
  before any graph transformation runs, on every platform — so a pipeline
  authored on Linux/macOS stays loadable on a Windows checkout.
- **Boundary handles are the single source of truth for "which side of the
  boundary this edge attaches to."** `in__<child>` / `out__<child>` is the
  spelling produced when a submodel is created (or reconstructed by the parser) and validated by
  codegen. On well-formed graphs, flattening strips that prefix, restores the child endpoint,
  clears the synthetic handle, and is the inverse of creation. A
  regular (non-submodel) edge whose handle *happens* to look like
  `out__<something>` is never treated as a boundary handle by the consuming
  side — only an edge whose source or target actually is a submodel
  placeholder node is.
  > NOTE: `flatten_graph` itself checks only that the relevant handle is non-empty; it uses
  > `removeprefix`, so a malformed non-empty handle such as `wrong_child` is consumed as the child
  > id verbatim. A missing handle leaves the placeholder endpoint unchanged and the edge is then
  > silently dropped. Codegen validates prefixes and child membership before save, but the
  > standalone flattener is more permissive than the producer contract.
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
  lookup**, rather than inventing a second convention: `modules/<name>.py` is
  preferred relative to the active pipeline's directory (for configured
  nested projects), falling back to the project root for legacy single-file
  projects. If drill-down (`GET /api/submodel/{name}`) resolved a different
  file than the parser would load for the same reference, the GUI and the
  actually-executed pipeline would show different things.
- **Nesting is disallowed by construction, not by convention.** Rejecting any
  selection that includes a submodel placeholder keeps the placeholder model,
  the `in__`/`out__` handle scheme, and the flatten pass single-level;
  recursive nesting (a submodel containing another submodel) was considered
  as a possible future phase and never implemented. This closes the
  GUI-authored path, but a hand-authored submodel `.py` file could still
  contain its own `pipeline.submodel(...)` call — the parser (owned by
  expression-parsing component) handles that case by detecting the
  nested call and *ignoring* it with a `nested_submodel_ignored` log plus a graph-level warning,
  not by raising. Detecting a circular submodel reference and raising a clear
  error for it was proposed but never built; nesting one level deep is
  structurally impossible to trigger from the GUI, and nesting attempted from
  hand-written code is silently dropped rather than erroring, so a cycle
  cannot form either way.
- **Name-collision detection lives at save time, not in `haute lint`.**
  Extending `haute lint` to warn about (and block on) node-name collisions
  across submodels was proposed and never built — the CLI's lint command has
  no submodel-specific rules today. Collision checking is instead
  `SavePipelineService._validate_unique_sanitized_names`, run against the
  pre-transform graph on create and against the post-inline graph on dissolve
  (see the `create_submodel`/`dissolve_submodel` control flow in
  [Low-level](low-level.md) for both call sites), not a `haute lint` check a
  user can run independently of a submodel-mutating request.

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
- Any failure partway through the underlying save transaction (config write,
  sidecar write, or the submodel file's own deletion on dissolve) rolls back
  every file already touched in that request and surfaces as `500` — no
  partial multi-file write is ever left on disk. See
  [server-api](../server-api/high-level.md) for the transaction's full
  contract.
- `flatten_graph` has no dedicated malformed-boundary exception. Non-empty handles with the wrong
  prefix are consumed as ids, while boundary edges with no handle are dropped after they still
  reference a removed placeholder. This is silent standalone behaviour; codegen's stricter
  validation normally prevents such graphs from being persisted.
- > NOTE: `GET /api/submodel/{name}` contains a defensive
  > `sm_path.is_relative_to(project_root)` check that raises `403` if it
  > fails, but the path it checks was already produced by
  > `resolve_submodel_by_name`, which validates the same condition (and
  > raises `ValueError` instead) before ever returning a path. Because the
  > route's `{name}` parameter is a single path segment and cannot itself
  > contain `/`, the resulting `modules/{name}.py` reference cannot escape
  > the project root through this endpoint, so the `403` branch is
  > unreachable in practice — defensive redundancy rather than live
  > behaviour.
