# Trace Panel Experience Design

## Problem

Trace output had grown feature-rich but uneven. Different node types used visibly different
layouts, source nodes could take over the panel with long field lists, and optimiser apply traces
could include branches that were connected in the pipeline but not actually part of the selected
calculation. The result was technically complete but harder for users to scan than it needed to be.

The panel needs to answer one question first: "why does this cell have this value?" Supporting
details should remain available without forcing every upstream pass-through node into the primary
reading path.

## Approach

The Trace panel now renders a focused trace story by default. It finds the step that created or
modified the clicked column, preserves that target step, and preserves only upstream steps that
create structured dependencies used by that target. Generic pass-through and unrelated branches are
collapsed into a compact "show full trace" control. The full trace remains one click away for audit
and debugging.

Node-specific explanations share the same detail primitives:

- a compact node-coloured calculation frame for normal calculations;
- detail panels with small summary chips;
- callouts for the selected optimiser scenario or ratebook;
- dense, aligned tables for ladders, banding outputs, candidates, and model contributions.

Structured trace details own their dependency selection. For example, ratebook optimiser apply uses
the ratebook factors, online optimiser apply uses objective and constraint columns, model scoring
uses model feature columns, and banding uses source input columns. This avoids following incidental
input values that happen to be present because of extra graph edges.

Large source-origin nodes default to collapsed when they simply add many fields. A source node still
opens normally when it is the traced target, preserving the source-of-truth explanation for raw
columns.

## Alternatives Considered

Keeping Calculation and Nodes as separate tabs was rejected because it split the same story across
two navigation surfaces. Users had to move between tabs to reconcile value, source, and pipeline
context.

Showing every connected node by default was rejected because connected data can be relevant to the
pipeline but irrelevant to the clicked calculation. The focused story keeps the default view about
the value being inspected, while the full trace keeps the wider graph available.

Hard-coding per-node hiding rules was rejected. Instead, each rich detail type exposes its actual
dependency columns and the shared trace grouping logic handles presentation consistently.

## Open Questions

The current panel uses frontend tests for the focused story and shared trace detail rendering. If the
backend trace contract grows more structured dependency metadata, the dependency selection logic
could move closer to the trace enrichment layer so the frontend becomes a pure renderer.

The online optimiser chart is intentionally compact. If users need deeper optimiser diagnostics, the
next extension should reuse the same candidate data and add optional drill-in controls rather than
expanding the default table.
