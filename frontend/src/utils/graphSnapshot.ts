/**
 * Canonical serialization + dirty-derivation helpers for graph snapshots.
 *
 * Dirty tracking now shares a single serializer.  This avoids split behaviour where one
 * code path stripped presentation fields (`selected`, `dragging`, …)
 * before comparing but another did not — the latter would have flagged
 * the workspace dirty every time a node was selected.
 *
 * These helpers are **pure** — they operate on the raw inputs supplied
 * by the caller (nodes/edges/preamble/submodels or just the lastSaved string) and
 * do not read from any store.  Consumers compose them with whatever
 * source of graph state they already hold.
 */
import type { Node } from "@xyflow/react"
import type { PipelineEdge } from "../types/node"

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

const LIVE_HISTORY_NODE_METADATA_FIELDS = new Set([
  "_functionName",
  "_defaultInputName",
  "_sourceHandleInputNames",
  "_configReference",
  "_parentBindingScope",
  "_parentEdgeOrder",
])

const LIVE_HISTORY_EDGE_IDENTITY_FIELDS = new Set(["_inputName"])
const NO_RETAINED_METADATA = new Set<string>()

function stripNodeUiFields(n: Node): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = k === "data" ? stripNodeDataMetadataFields(v) : v
  }
  return out
}

function stripEdgeUiFields(e: PipelineEdge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(k)) continue
    if (k === "data") {
      const stripped = stripNodeDataMetadataFields(v)
      if (
        typeof stripped === "object"
        && stripped !== null
        && !Array.isArray(stripped)
        && Object.keys(stripped).length === 0
      ) continue
      out[k] = stripped
      continue
    }
    out[k] = v
  }
  return out
}

function stripNodeHistoryFields(n: Node): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(key)) continue
    out[key] = key === "data"
      ? stripNodeDataMetadataFields(value, LIVE_HISTORY_NODE_METADATA_FIELDS)
      : value
  }
  return out
}

function stripEdgeHistoryFields(e: PipelineEdge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(key)) continue
    if (key === "data") {
      const stripped = stripNodeDataMetadataFields(
        value,
        LIVE_HISTORY_EDGE_IDENTITY_FIELDS,
      )
      if (
        typeof stripped === "object"
        && stripped !== null
        && !Array.isArray(stripped)
        && Object.keys(stripped).length === 0
      ) continue
      out[key] = stripped
      continue
    }
    out[key] = value
  }
  return out
}

function stripNodeDataMetadataFields(
  value: unknown,
  retainedMetadata: ReadonlySet<string> = NO_RETAINED_METADATA,
): unknown {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return value

  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (k.startsWith("_") && !retainedMetadata.has(k)) continue
    out[k] = v
  }
  return out
}

function stripGraphMetadataTransientFields(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map(stripGraphMetadataTransientFields)

  const record = value as Record<string, unknown>
  const isSubmodelDefinition =
    typeof record.definitionId === "string"
    && typeof record.file === "string"
    && typeof record.graph === "object"
    && record.graph !== null
    && Array.isArray(record.inputPorts)
    && Array.isArray(record.outputPorts)
  if (isSubmodelDefinition) {
    return Object.fromEntries(
      Object.entries(record)
        .filter(([key]) => !key.startsWith("_"))
        .map(([key, child]) => [key, stripGraphMetadataTransientFields(child)]),
    )
  }
  if (typeof record.source === "string" && typeof record.target === "string") {
    return stripEdgeUiFields(record as unknown as PipelineEdge)
  }
  if (typeof record.id === "string" && "data" in record) {
    return stripNodeUiFields(record as unknown as Node)
  }

  const stripped: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(record)) {
    stripped[key] = stripGraphMetadataTransientFields(child)
  }
  return stripped
}

function stripGraphHistoryTransientFields(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map(stripGraphHistoryTransientFields)

  const record = value as Record<string, unknown>
  if (typeof record.source === "string" && typeof record.target === "string") {
    return stripEdgeHistoryFields(record as unknown as PipelineEdge)
  }
  if (typeof record.id === "string" && "data" in record) {
    return stripNodeHistoryFields(record as unknown as Node)
  }

  return Object.fromEntries(
    Object.entries(record).map(([key, child]) => [
      key,
      stripGraphHistoryTransientFields(child),
    ]),
  )
}

function cloneGraphValue<T>(value: T, seen = new WeakMap<object, unknown>()): T {
  if (value === null || typeof value !== "object") return value
  const objectValue = value as object
  const existing = seen.get(objectValue)
  if (existing !== undefined) return existing as T

  if (Array.isArray(value)) {
    const clone: unknown[] = []
    seen.set(objectValue, clone)
    for (const item of value) clone.push(cloneGraphValue(item, seen))
    return clone as T
  }

  const clone: Record<string, unknown> = {}
  seen.set(objectValue, clone)
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    clone[key] = cloneGraphValue(child, seen)
  }
  return clone as T
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

export function canonicalize(value: unknown): unknown {
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
 * Clone a live editor graph into the strict canonical payload accepted by
 * backend graph schemas. Presentation fields and server-owned editor identity
 * metadata are removed recursively from root and embedded definition graphs.
 */
export function toCanonicalGraphPayload(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}): {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown> | undefined
  preamble: string | undefined
} {
  return {
    nodes: input.nodes.map(
      (node) => cloneGraphValue(stripNodeUiFields(node)) as Node,
    ),
    edges: input.edges.map(
      (edge) => cloneGraphValue(stripEdgeUiFields(edge)) as PipelineEdge,
    ),
    submodels: input.submodels === undefined
      ? undefined
      : cloneGraphValue(
        stripGraphMetadataTransientFields(input.submodels),
      ) as Record<string, unknown>,
    preamble: input.preamble,
  }
}

/**
 * Canonical serialization of a graph snapshot used for dirty-derivation.
 *
 * Scope: `{nodes, edges, preamble, submodels}` — the complete persisted,
 * user-editable graph surface. Preserved blocks remain out of scope because
 * they round-trip outside the graph store, while submodels are editable in
 * the GUI and must participate in dirty detection.
 *
 * Determinism: equal inputs produce equal strings even if the caller
 * constructed the object with keys in a different order.
 */
export function serializeSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
}): string {
  const graph = toCanonicalGraphPayload(input)
  return JSON.stringify(
    canonicalize({
      nodes: graph.nodes,
      edges: graph.edges,
      preamble: graph.preamble,
      submodels: graph.submodels,
    }),
  )
}

/**
 * Clone a live history snapshot. React Flow presentation and volatile runtime
 * fields are omitted, but server-owned editor identities are retained:
 * undo/redo restores a live executable graph, not a persisted document.
 * `serializeSnapshot` remains the separate boundary that strips identity
 * metadata too.
 */
export function cloneGraphSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
}): {
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
} {
  return {
    nodes: input.nodes.map(
      (node) => cloneGraphValue(stripNodeHistoryFields(node)) as Node,
    ),
    edges: input.edges.map(
      (edge) => cloneGraphValue(stripEdgeHistoryFields(edge)) as PipelineEdge,
    ),
    preamble: input.preamble,
    submodels: cloneGraphValue(
      stripGraphHistoryTransientFields(input.submodels),
    ) as Record<string, unknown>,
  }
}

/** Pre-computed empty-workspace sentinel (fast path for fresh sessions). */
export const EMPTY_SNAPSHOT = serializeSnapshot({
  nodes: [],
  edges: [],
  preamble: "",
  submodels: {},
})

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
