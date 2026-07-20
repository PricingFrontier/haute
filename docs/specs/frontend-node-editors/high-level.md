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

## Interactions

The panel consumes selected-node/edge state from
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md), API capabilities from
[server-api](../server-api/high-level.md), and modelling/optimiser configuration panels from
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md). Preview
columns and rows are supplied by the execution/result stores, not computed by these editors.

## Failure model

Client-side parse and shape checks show inline invalid state where implemented. Server failures
such as format, file, Databricks, or MLflow lookup errors are rendered by the invoking editor.
Malformed config that cannot be interpreted is surfaced as a visible diagnostic or an explicit
editor error; the component does not silently replace it with invented configuration.
