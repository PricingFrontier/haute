# Frontend Node Editors — High-Level Specification

## Purpose

The node-editor surface turns the selected pipeline node into a type-specific, editable
configuration form. It keeps authoring consistent across simple scalar nodes, code nodes,
multi-frame API inputs, IO sources/sinks, banding, rating, model scoring, submodels, and
optimiser application.

## Scope

This component owns node-panel dispatch, the node palette, editor implementations and their
form, banding, rating, path, clipboard, and format helpers. It also owns the generic Columns
and grouped-columns configuration tabs. The graph canvas owns selection and graph mutation;
backend API modules own validation and persistence.

## Behaviour

- Selecting a node opens its matching editor through a lazy editor registry. An unknown type,
  malformed instance reference, or unsupported read-only configuration is shown as diagnostics
  instead of being guessed.
- Editor updates flow through the supplied configuration callbacks. Text and numeric drafts are
  committed on blur/Enter where their controls use the shared committed-input primitives, so a
  normal edit does not create a graph mutation for each keystroke.
- Connected inputs are listed by their **input name — the exact argument name in the node's
  code**, 1:1 with the generated function signature: an API-input frame edge's chip shows the
  frame label carried on the edge (`quotes` is displayed as `quotes` and callable as `quotes`),
  an ordinary source's chip shows the sanitised node label, and a submodel-output edge's chip
  shows the child node's sanitised label (what the flattened code actually binds). The source
  node is named in the chip tooltip. Two frames connected from one API input render as two
  distinct, individually removable chips with two distinct names. Live-switch mapping rows and
  output frame blocks present the same names — there is no separate display identity anywhere.
- Editors retain incomplete persisted rows when they can be repaired (notably API schema and
  output mappings); fresh inference data may be normalised separately from persisted data.
- Banding exposes categorical/numeric rule editing, preview-derived suggestions and histogram
  context. Rating supports one- and two-way factor tables, value-level matching, statistics,
  paste/copy and downloadable table data.
- IO editors obtain supported formats and their arguments from the server. API/data input,
  output, source, sink, external-file, transform, explore, live-switch, scenario, submodel,
  model-score and optimiser-apply editors render only their own configuration contract.

## Design rationale

The UI uses specialised editors rather than one schema-driven form because graph node contracts
are structurally different. Shared helpers centralise the places where consistency matters:
commit timing, clipboard parsing, path handling, rendered input-source chips, and normalisation
of persisted banding/rating data. Lazy dispatch keeps editor code out of the initial canvas load.

Display identity and executable identity are one identity. `InputSource.name` is the input's
single name — the chip text, the code argument, and the key persisted contracts use (the
live-switch `input_scenario_map` and the instance `inputMapping`, both consumed by the
backend). It is derived per edge by the shared `edgeInputName` helper, mirroring the backend's
`edge_input_name` byte-for-byte, so the panel can never advertise a name the code does not
recognise. (An earlier interim design split `varName` from `displayLabel` to avoid touching
executable contracts in a presentation-only release; that split itself produced the
name-shown-but-not-callable confusion and was deliberately retired by the convergence release.)

## Interactions

The panel consumes selected-node/edge state from
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md), API capabilities from
[server-api](../server-api/high-level.md), and modelling/optimiser configuration panels from
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md). Preview
columns and rows are supplied by the execution/result stores, not computed by these editors.
Frame display labels for input chips and output frame naming are resolved through the
api-input frame-identity helpers owned by
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)
(`frontend/src/utils/apiInputPorts.ts`).

## Failure model

Client-side parse and shape checks show inline invalid state where implemented. Server failures
such as format, file, Databricks, or MLflow lookup errors are rendered by the invoking editor.
Malformed config that cannot be interpreted is surfaced as a visible diagnostic or an explicit
editor error; the component does not silently replace it with invented configuration. A dangling
`sourceHandle` (an edge bound to a frame that no longer exists) is displayed **verbatim** as the
edge's frame identity with an explicit unresolved warning state wherever the connection is
presented (input chips, live-switch mapping rows, output frame blocks) — never silently renamed
to the parent node and never a normal-looking entry. Null-handle API-input edges cannot be
created (the zero-frame handle is non-connectable) and are pruned by reconciliation when read
from a hand-edited file, so the verbatim-plus-warning rule is the complete unresolved story.
