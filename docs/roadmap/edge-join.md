# Edge Join roadmap

## Scope

Owns remaining user-facing completion of the `edgeJoin` node: discoverable
insertion, deterministic browser behaviour, and accurate join interaction
documentation.

## Priorities

| Package | State | Priority | Outcome |
| --- | --- | --- | --- |
| ROAD-EDGE-01 | Active | P0 | Make compatible edge insertion discoverable and accessible. |
| ROAD-EDGE-02 | Active | P1 | Prove the complete Edge Join browser journey. |
| ROAD-EDGE-03 | Active | P1 | Align public and specification documentation with runtime behaviour. |

## Planned improvements

### ROAD-EDGE-01 — Discoverable accessible edge insertion

**Why:** Insertion is only detected at connection release, giving no pre-release indication that an edge is a valid target.

**Plan:** Track compatible candidate edges during a source gesture and render transient accessible feedback. Clear it on leave, cancellation, invalid endpoints, cycles, and ordinary drops; preserve the existing role-aware graph rewrite and one undo action.

**Acceptance:** A compatible target is visibly and programmatically distinguishable before release; invalid/cancelled gestures change no graph, selection, or undo state; successful insertion selects the new node and preserves role-bound edges and ordinary connection behaviour.

**Dependencies:** Shared canvas gesture and accessibility conventions.

**Evidence:** `frontend/src/hooks/useEdgeHandlers.ts`; `frontend/src/utils/edgeJoinGraph.ts`; `frontend/src/utils/edgeJoinValidation.ts`; `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts`; `frontend/src/utils/__tests__/edgeJoinGraph.test.ts`.

### ROAD-EDGE-02 — Deterministic browser workflow

**Why:** Focused unit coverage does not prove canvas insertion, configuration, preview, persistence, repeated joins, named handles, and tracing together.

**Plan:** Add a production-shaped Playwright fixture with compatible inputs and deterministic output; drive actual edge insertion, configure same-name keys, preview, save/reload, repeat insertion, use a named API-input handle, and trace a downstream output.

**Acceptance:** The normal browser suite fails on regressions in insertion, configuration, joined output, persistence, repeated joins, named source-handle preservation, or downstream trace; assertions observe user-visible results rather than component internals.

**Dependencies:** ROAD-EDGE-01; project-isolated browser fixtures and semantic locators.

**Evidence:** `frontend/e2e/core-flows.spec.ts`; `frontend/e2e/persistence/api-input-v2-native.spec.ts`; `frontend/src/panels/editors/EdgeJoinEditor.tsx`; `tests/test_edge_join.py`; `tests/test_trace_edge_join.py`.

### ROAD-EDGE-03 — Accurate interaction and join documentation

**Why:** Users need one precise explanation of creation gestures, dynamic role handles, role swapping, supported join modes, and key invariants.

**Plan:** Update maintained user and specification content after the interaction settles. Describe edge-drop and output-to-output creation, base/join/output handle geometry, `inner`, `left`, `right`, `full`, `semi`, `anti`, and `cross` modes, and cross/key constraints; add documentation accuracy checks.

**Acceptance:** Public and low-level descriptions match validation and canvas behaviour; all supported modes and key rules are discoverable; no checked documentation claims a fixed top-only handle or palette-only creation.

**Dependencies:** ROAD-EDGE-01 establishes the settled interaction.

**Evidence:** `frontend/src/utils/edgeJoinRoles.ts`; `frontend/src/panels/editors/EdgeJoinEditor.tsx`; `frontend/src/utils/edgeJoinValidation.ts`; `src/haute/_edge_join.py`; `tests/test_edge_join.py`.
