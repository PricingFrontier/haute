# Submodels roadmap

## Scope

Submodel definitions and occurrences, and the other mechanisms for reusing
logic in a pipeline. Current behaviour is specified in
[the submodels specification](../submodels/high-level.md). `SUB-R01`
comes from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-R01 | Planned | P3 | A submodel groups nodes; an instance reuses a node or a submodel on other inputs, through one mechanism with an explicit input mapping. |

## Planned improvements

`SUB-R01` makes an instance of a node an occurrence of a one-node definition,
using the existing import-based registration. `SUB-R01` is best taken before
`PCFG-R08` and `PCFG-R07` (pipeline config), so the editor-state move and the
typed config models never have to carry node-level instances.

### SUB-R01 — One reuse mechanism
**Why:** Two mechanisms reuse logic on other inputs, and each has the gaps
the other does not. A node instance (`@pipeline.instance`, or `instanceOf`
with `inputMapping` on an ordinary node) applies one node's logic to other
inputs:

- a pipeline that holds one does not run as plain Python: `Pipeline.run()`
  and `score()` refuse an `@pipeline.instance` node;
- its inputs are paired with the original's by exact name, then by substring,
  then by position (`build_instance_mapping`), so a rewire can silently
  re-pair them;
- every engine and editor path handles `instanceOf` on its own (258
  `instanceOf`/`instance_of` references across the backend and frontend). Two
  defects found on 29 October 2026, SUB-R06's crash and a refused Load File
  instance in codegen, were that second path.

An instance of a submodel (an occurrence with `instance_of`) has none of
those, but it binds only frames named like one of the definition's public
inputs (the editor refuses any other with "New public inputs can only be
added through the definition owner"), it reports no missing columns, its
outputs carry the owner's names (so no node can read both), and a collapsed
occurrence can be neither previewed nor edited in place. Files named
after submodels hold 6,300 source lines and 11,700 test lines, and 25 of the
48 submodel commits since May 2026 were fixes or hardening of identity and
aliasing.

**Decided (24 September 2026, refined 29 October 2026):**

- A submodel groups nodes that form one process: it makes the code modular
  and the canvas tidier.
- An instance reuses logic on another set of inputs, of a single node or of
  a submodel. Every instance has an explicit input mapping, since its inputs
  are usually named otherwise than the original's.
- An instance has its own name, and its outputs are named from it, never as
  the original's are.
- One machinery serves both: a definition with named public inputs and
  outputs, and its occurrences. An instance of a single node is an occurrence
  of a one-node definition (its own `modules/<name>.py`), shown on the canvas
  as the node itself, a linked node, so editing the original still updates
  every copy.
- `inputMapping` on a plain Transform or Load File stays: it names a node's
  inputs across rewires and reuses no logic. Project `utility` modules stay.
- Node-level instances go: `@pipeline.instance`, an `of=` keyword on a node
  decorator and `instanceOf` on an ordinary node are refused with a message
  naming the linked-node form ([the canonical-input rule](../README.md#canonical-only-format-policy)).

**Design** (specified in [the submodels specification](../submodels/high-level.md#reuse-instances-of-nodes-and-submodels-normative)):

- **Bindings.** An instance's mapping is its incoming edges: each one names
  the public input it feeds in its `in__<name>` handle. Dropping a connection
  on an instance binds the public input named like the frame; failing that,
  the one public input the instance has not bound; failing that, the editor
  asks which. Nothing pairs inputs by position or by substring. The panel of
  every occurrence lists its public inputs with the connection that feeds
  each, and choosing another connection re-targets the edge.
- **Output names.** An instance's outputs are named from its own name: a
  linked node's output by its name, as a node's is by its label, and a
  submodel instance's by `<instance>_<port>`. The original keeps its names (a
  submodel's port names, a linked node's own name), so one node can read the
  original and its instances side by side, and renaming an instance renames
  its frames for its consumers.
- **Linked nodes.** An occurrence of a one-node definition is drawn as its
  node's card with a link mark, and clicking it previews that node. The
  original's panel is the node's own editor, whose edits change the shared
  definition; an instance's shows the same editor read-only, with
  "Instance of <original>", its input mapping and its missing columns. It
  has no drill-in. Duplicate gives an unlinked plain copy, and Unlink, once no
  instance is left, turns the original back into a plain node.
- **Create Instance.** On a plain node it wraps the node, in place, in a
  one-node definition named after it (`modules/<name>.py`, suffixed `_2`,
  `_3` when that module exists), keeping the node's name, position and
  connections, and places an instance beside it: one undoable edit, written
  only by Save. On a submodel it places an instance as today. It is offered
  on every node type a definition can hold, apart from the document-wide
  singletons and the Source Switch.
- **Missing columns.** For each public input both an instance and its
  original bind, the instance reports the columns the original's frame
  carries that its own does not.

**Plan:** five phases, each a commit that changes the specifications it
needs first.

1. Bindings and output names for instances of submodels: the drop rule, the
   choice of input, the input mapping in the occurrence panel, and
   `<instance>_<port>` output names in the shared input-name rule
   (`edge_input_name` and its browser mirror) and on the card. It stands
   alone: today's submodel instances gain differently named inputs and
   outputs of their own.
2. Linked nodes: the server transform that wraps a node
   (`POST /api/submodel/instance`), the one-node output name in the shared
   input-name rule (`edge_input_name` and its browser mirror), and the card,
   preview target, in-place editing, read-only instances, Duplicate and
   Unlink.
3. Missing columns for instances, from the walk's recorded schemas as
   SUB-R06 reads them, by public input. Flattening carries each instance's
   original with the flattened graph, never in node config.
4. Proof: for every type Create Instance offers, a linked node with remapped
   inputs passes preview, trace, `haute run`, a standalone `pipeline.run()`,
   a codegen round trip and deploy, and the type's own job where it has one
   (training, a solve); it also passes the multi-frame regression in
   `tests/test_apiinput_multi_port_runtime.py`. A type that fails is
   withdrawn from Create Instance and brought back as a decision.
5. Removal: the parser and config validation refuse node-level instances
   with the linked-node remediation; the node-instance resolver,
   `build_instance_mapping`, instance codegen and their editor, assistant,
   trace, cache and recovery paths go, with their passages in the
   execution-engine, codegen, pipeline-config, assistant, caching, tracing,
   sandbox-security, server-api and frontend specifications;
   `docs/building-models/nodes/instances.md` becomes "Reusing a node" and
   `docs/building-models/nodes/submodel.md` describes an instance's input
   mapping.

**Acceptance:**

- Create Instance on a Transform reading `policies` keeps the original where
  it was, under its name, and places an instance. Connecting `claims_data` to
  the instance binds it to `policies`; the instance previews `claims_data`'s
  rows through the original's code, and editing the original changes that
  preview. Save writes `modules/<name>.py` and
  `pipeline.connect("claims_data", "<instance>", target_port="policies")`;
  reload restores both, the saved file runs standalone, and renaming the
  instance renames its output for its consumers.
- The same holds for a Model Score node, and for an instance of a multi-node
  submodel bound to frames named otherwise than its public inputs.
- One node reads an original and its instance side by side: a linked node's
  outputs as `<name>` and `<name>_2`, a submodel's output `premium` as
  `premium` and `<instance>_premium`.
- No path pairs inputs by position or by substring.
- `@pipeline.instance(...)`, `of=` on a node decorator and `instanceOf` on an
  ordinary node's config are refused with a message naming the linked-node
  form; nothing migrates them.
- `instanceOf` appears only on submodel occurrences, in every layer.
- An instance reports the columns its original's inputs carry that its own
  do not.
- The documentation describes only supported mechanisms.

**Dependencies:** None.

**Evidence:** `src/haute/pipeline.py::instance`;
`src/haute/_graph_utils.py::build_instance_mapping`;
`src/haute/_builders.py::resolve_instance_node`;
`src/haute/_submodel_instances.py`; `src/haute/_flatten.py`;
`frontend/src/utils/connectionValidation.ts`;
`frontend/src/hooks/usePipelineAPI.ts`;
`docs/building-models/nodes/instances.md`;
`docs/building-models/nodes/submodel.md`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`.
