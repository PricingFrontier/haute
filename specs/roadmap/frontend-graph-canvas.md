# Frontend graph canvas roadmap

## Scope

The graph store behind the canvas, as [the frontend graph canvas
specification](../frontend-graph-canvas/high-level.md) describes it: what one
edit costs and what the undo history holds. The package below comes from the
9 November 2026 codebase review and was measured against `HEAD` on that day
by bundling the store and driving it under Node.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEG-01 | Planned | P2 | An edit's cost and the undo history's size follow what changed, not the size of every table in the graph. |

## Planned improvements

### FEG-01 — An edit no longer serialises and clones the whole graph
**Why:** Measured. Every history-aware change to the graph serialises the
whole graph for the new fingerprint, serialises the state before it a second
time to decide whether history moves, computes the structural and
panel-context fingerprints, and deep-clones every node into the undo stack,
which keeps one hundred such clones. The node editors commit through this on
each edit. A Rating Step's tables live in its node's settings with no cap on
their rows, so their size is in every one of these passes, for an edit to any
node. One-character edits to one code node, one hundred times:

| Graph | Per edit | Undo stack after 100 edits |
|---|---:|---:|
| 100 code nodes | 2.4 ms | 3 MiB |
| 300 code nodes | 6.1 ms | 9 MiB |
| 100 code nodes and one 20,000-row table | 47.8 ms | 158 MiB |
| 100 code nodes and one 100,000-row table | 276.1 ms | 776 MiB |

With a large rating table in the pipeline, typing into any node's editor
stalls for a quarter of a second per keystroke and the tab's memory grows
towards a gigabyte.

**Plan:** Nodes are replaced, never mutated, on a commit, so history can hold
references: an undo entry shares every node the change did not touch.
Fingerprint per node, cached by the node object's identity, and combine the
cached parts; compare the changed nodes only to decide whether history
moves. Keep the saved-baseline and dirty semantics exactly as they are.

**Acceptance:** On the last graph above, a one-character edit to a code node
takes under 5 ms and one hundred edits grow the heap by under 10 MiB, held by
a store-level test with a generous bound. Undo and redo restore the same
graph as today across the existing history tests, and `dirty` returns to
false when an undo reaches the saved state.

**Dependencies:** A check that no caller mutates a node object in place
after a commit; the store's tests are where that is asserted.

**Evidence:** `frontend/src/stores/useGraphStore.ts::commitGraphChange`;
`frontend/src/stores/useGraphStore.ts::derivedPatch`;
`frontend/src/stores/useGraphStore.ts::MAX_HISTORY`;
`frontend/src/utils/graphSnapshot.ts::cloneGraphSnapshot`;
`frontend/src/utils/graphSnapshot.ts::serializeSnapshot`;
`frontend/src/hooks/useGraphCommitController.ts::onUpdateNode`;
`src/haute/_node_config_models.py:490` holds the uncapped `entries` list.
