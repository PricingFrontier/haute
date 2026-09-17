# Polars step builder roadmap

## Scope

Polars transform nodes can be authored as an ordered list of low-code steps
rendered to the same generated function body that code-only nodes use. Current
behaviour is specified in [pipeline config](../pipeline-config/high-level.md),
[codegen](../codegen/high-level.md),
[execution engine](../execution-engine/high-level.md),
[server API](../server-api/high-level.md), and
[frontend node editors](../frontend-node-editors/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PST-S02 | Deferred | P3 | Extend step authoring to the other Polars code surfaces after Transform use. |

## Planned improvements

### PST-S02 — Steps on the other Polars surfaces

**Why:** Explore, Data Input, External File, Scenario Expander, Rating Step,
and Model Score also expose a Polars code box that an analyst who does not
know Polars cannot author.

**Plan:** After the Transform step builder has been used on real pipelines,
reuse the renderer and editor for the implicit-`df` code surfaces, whose
extraction matchers differ per node type.

**Acceptance:** Each surface renders, round-trips, and discards steps on hand
edits with the same tests the Transform builder has in
`tests/test_polars_steps.py`.

**Dependencies:** The Transform step builder, the per-type user-code
extraction matchers, and the shared node panel Polars tab.

**Evidence:** `src/haute/_polars_steps.py`; `src/haute/_code_extraction.py`;
`frontend/src/panels/NodePanel.tsx`.
