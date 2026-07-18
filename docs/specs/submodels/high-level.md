# Submodels — High-Level Specification

## Purpose

A pipeline graph can grow large enough that a flat canvas of nodes stops being
navigable. Submodels let a user select a group of related nodes and collapse
them into a single reusable unit: the selected nodes are extracted into their
own `modules/<name>.py` file, and the parent graph shows one placeholder node
in their place. The user can drill into the placeholder to see and edit the
group's internals, or ungroup it back into the parent at any time.

Submodels exist purely as a **code-organisation and GUI-navigation** concept.
Execution, tracing, and deployment never reason about submodel boundaries —
they always operate on a single flat graph with the boundaries dissolved. This
component's job is limited to producing and consuming the *hierarchical*
(collapsed) representation; the flattening itself is a neighbouring
component's responsibility (see [Interactions](#interactions)).

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
- The three HTTP endpoints that expose creation, drill-down, and dissolution
  to the GUI: `POST /api/submodel/create`, `GET /api/submodel/{name}`,
  `POST /api/submodel/dissolve` (`src/haute/routes/submodel.py`).

Out of scope (owned elsewhere, linked where relevant):
- Parsing `pipeline.submodel("path")` calls out of a pipeline file's AST,
  parsing an individual submodel `.py` file's `@submodel.<type>` decorators,
  and merging parsed submodel graphs into the parent's hierarchical form
  (`_parser_submodels.py`, `parser.py`) — owned by
  [pipeline-config](../pipeline-config/high-level.md). This component reuses
  that parser's output shape and its placeholder-building helpers, but does
  not parse code itself.
- Dissolving submodel placeholders into a fully flat graph
  (`src/haute/_flatten.py::flatten_graph`) — owned by
  [execution-engine](../execution-engine/high-level.md). This component calls
  it (for dissolve) but does not implement it.
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
  edges (via the execution engine's flattener, scoped to just that one
  submodel), the parent file is rewritten, and the `modules/<name>.py` file is
  deleted.
- **Minimum size.** A submodel must contain at least 2 nodes after any
  nonexistent or duplicate ids in the request are resolved against the actual
  graph; fewer is rejected.
- **No nesting.** A node that is itself a `SUBMODEL` placeholder cannot be
  selected as part of a new group — grouping is capped at one level.
- **A submodel belongs to one pipeline.** There is no import mechanism for
  reusing a `modules/<name>.py` file across different pipelines, and no
  submodel entry in the node palette — a submodel can only be created from an
  existing selection of nodes already in the current pipeline, never dragged
  in from a library of reusable submodels.
- **Windows-reserved names are rejected up front.** A submodel name that would
  produce a module filename matching a Windows reserved device name (`CON`,
  `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, any casing, any extension) is rejected
  before any graph transformation runs, on every platform — so a pipeline
  authored on Linux/macOS stays loadable on a Windows checkout.
- **Boundary handles are the single source of truth for "which side of the
  boundary this edge attaches to."** `in__<child>` / `out__<child>` is the one
  spelling produced when a submodel is created (or reconstructed by the
  parser) and the one spelling consumed when a submodel is dissolved back to
  a flat graph — the two operations are exact inverses of each other. A
  regular (non-submodel) edge whose handle *happens* to look like
  `out__<something>` is never treated as a boundary handle by the consuming
  side — only an edge whose source or target actually is a submodel
  placeholder node is.
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
  the `in__`/`out__` handle scheme, and the flatten pass single-level; the
  historical design notes in the retired SUBMODEL_DESIGN doc (git history)
  describe recursive nesting as a possible future phase that was never
  implemented. This closes
  the GUI-authored path, but a hand-authored submodel `.py` file could still
  contain its own `pipeline.submodel(...)` call — the parser (owned by
  pipeline-config/expression-parsing) handles that case by detecting the
  nested call and *ignoring* it with a `nested_submodel_ignored` warning log,
  not by raising. The retired SUBMODEL_DESIGN doc (git history) §8.2
  describes circular submodel references as something the parser "must
  detect... and raise a clear
  error" — that was never built; nesting one level deep is structurally
  impossible to trigger from the GUI, and nesting attempted from hand-written
  code is silently dropped rather than erroring, so a cycle cannot form
  either way.
- **Name-collision detection lives at save time, not in `haute lint`.**
  The retired SUBMODEL_DESIGN doc (git history) §5.9/§8.1 describes `haute
  lint` warning about (and blocking on) node-name collisions across
  submodels. The CLI's lint command
  has no submodel-specific rules today — collision checking is
  `SavePipelineService._validate_unique_sanitized_names`, run against the
  pre-transform graph on create and against the post-inline graph on dissolve
  (see the `create_submodel`/`dissolve_submodel` control flow in
  [Low-level](low-level.md) for both call sites), not a `haute lint` check a
  user can run independently of a submodel-mutating request.

## Interactions

- Depends on [pipeline-config](../pipeline-config/high-level.md) for parsing
  submodel `.py` files (`parse_submodel_file`) and for the hierarchical-merge
  logic (`_parser_submodels.py::merge_submodels`) that this component's
  placeholder/port/rewire helpers are shared with.
- Depends on [execution-engine](../execution-engine/high-level.md)'s
  `_flatten.py::flatten_graph` to dissolve a submodel back to a flat graph
  during dissolution; execution, tracing, and deployment consume that
  flattened form and never see a `submodel__*` placeholder node.
- Depends on the codegen component's `graph_to_code_multi` (create — emits
  both the rewritten parent file and the new submodel file) and
  `graph_to_code` (dissolve — emits just the rewritten parent file).
- Depends on [server-api](../server-api/high-level.md) for
  `SavePipelineService`, the shared `save_lock`, `pipeline_dir()` resolution,
  sidecar position loading, and the codebase-wide sanitised-error-detail
  convention for internal failures.
- Depended on by the GUI graph editor, the sole caller of `/api/submodel/*`. The frontend
  submodel navigation UI itself — the `useSubmodelNavigation` hook (drill-in/out, create,
  dissolve) and the `SubmodelDialog` create/rename component — lives in the
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md) component, not here.

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
