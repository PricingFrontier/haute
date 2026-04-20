/**
 * Canonical serialization + dirty-derivation helpers for graph snapshots.
 *
 * Consolidated out of `stores/useUIStore.ts` (Wave 7E): both the UI
 * store's legacy dirty-tracking API *and* `useGraphStore.isDirty()` now
 * share a single serializer.  This avoids the split where the UI store
 * stripped presentation fields (`selected`, `dragging`, …) before
 * comparing but the graph store's internal serializer did not — the
 * latter would have flagged the workspace dirty every time a node was
 * selected.
 *
 * These helpers are **pure** — they operate on the raw inputs supplied
 * by the caller (nodes/edges/preamble or just the lastSaved string) and
 * do not read from any store.  Consumers compose them with whatever
 * source of graph state they already hold.
 */
import type { Node, Edge } from "@xyflow/react"

// ---------------------------------------------------------------------------
// Field stripping
//
// Fields that React Flow manages for presentation only — not part of
// the on-disk pipeline.  Stripped before serialization so that
// selecting a node or mid-drag flagging doesn't flip the unsaved-changes
// indicator.
// ---------------------------------------------------------------------------

const REACT_FLOW_NODE_UI_FIELDS = [
  "selected",
  "dragging",
  "positionAbsolute",
  "measured",
  "resizing",
  "computed",
] as const

const REACT_FLOW_EDGE_UI_FIELDS = ["selected"] as const

function stripNodeUiFields(n: Node): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = v
  }
  return out
}

function stripEdgeUiFields(e: Edge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = v
  }
  return out
}

// ---------------------------------------------------------------------------
// Canonicalisation
//
// Deterministic stringifier — same object shape and contents always
// produce the same string regardless of `JSON.stringify` key-order
// quirks.  Arrays retain their original order (intentional: the order
// of nodes/edges in the graph is user-meaningful).
//
// Safe for JSON-like tree structures with no cycles (the graph shapes
// we serialize).  A cyclic object would throw in `JSON.stringify` —
// correct loud-failure behaviour.
// ---------------------------------------------------------------------------

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map(canonicalize)
  const entries = Object.entries(value as Record<string, unknown>)
  entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const out: Record<string, unknown> = {}
  for (const [k, v] of entries) {
    out[k] = canonicalize(v)
  }
  return out
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Canonical serialization of a graph snapshot used for dirty-derivation.
 *
 * Scope: `{nodes, edges, preamble}` — the user-editable surface of a
 * pipeline.  Extra fields carried by the backend (preserved_blocks,
 * submodels, etc.) are out of scope because they round-trip verbatim
 * and are not user-editable via the GUI; changing them should NOT flag
 * the graph as dirty.
 *
 * Determinism: equal inputs produce equal strings even if the caller
 * constructed the object with keys in a different order.
 */
export function serializeSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  preamble: string
}): string {
  return JSON.stringify(
    canonicalize({
      nodes: input.nodes.map(stripNodeUiFields),
      edges: input.edges.map(stripEdgeUiFields),
      preamble: input.preamble,
    }),
  )
}

/** Pre-computed empty-workspace sentinel (fast path for fresh sessions). */
export const EMPTY_SNAPSHOT = serializeSnapshot({ nodes: [], edges: [], preamble: "" })

/**
 * Pure selector: is the current graph different from the last saved snapshot?
 *
 * Semantics:
 *   - `lastSavedSnapshot === null` (never saved) + empty current graph
 *     => NOT dirty.  Fresh workspace is clean.
 *   - `lastSavedSnapshot === null` + non-empty current graph => DIRTY.
 *     The user built something without saving.
 *   - `lastSavedSnapshot !== null`: string-compare against current.
 */
export function selectIsDirty(
  state: { lastSavedSnapshot: string | null },
  currentSnapshot: string,
): boolean {
  if (state.lastSavedSnapshot === null) {
    return currentSnapshot !== EMPTY_SNAPSHOT
  }
  return currentSnapshot !== state.lastSavedSnapshot
}
