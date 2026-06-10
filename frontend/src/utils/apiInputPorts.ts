/**
 * Emit-port helpers for apiInput nodes.
 *
 * An apiInput node shreds one JSON file into multiple "tables". Each
 * `emit: true` table renders as a labelled source `<Handle>` on the
 * node's right edge (see `PipelineNode._SourceHandles`). Downstream
 * edges bind to a port via `edge.sourceHandle` = the table's label.
 *
 * This module is the single source of truth for two derived facts:
 *
 *  1. `apiInputEmitPortLabels` — the ordered list of port labels the
 *     node exposes. Mirrors the Handle-rendering rules exactly (only
 *     emit:true tables; `port_<idx>` for blank/missing labels;
 *     `__<idx>` to disambiguate duplicates). `PipelineNode` consumes
 *     it for both the Handles and the body label column, so the editor
 *     and the canvas can never disagree about which ports exist.
 *
 *  2. `reconcileApiInputEdges` — given the node's *new* config, finds
 *     outgoing edges whose `sourceHandle` no longer maps to a rendered
 *     port and returns the pruned edge list plus a description of what
 *     was removed. This is the load-bearing fix for Defect 1: emit-off,
 *     table-rename, table-delete, and the single↔multi-port handle
 *     transition all silently orphaned edges before. Pruning at edit
 *     time (with a visible toast at the call site) replaces the
 *     previous failure mode — a backend `KeyError` at execution.
 */
import type { SimpleEdge } from "../panels/editors/_shared"

type ConfigLike = Record<string, unknown> | undefined | null

function hasSelectedColumn(table: Record<string, unknown>): boolean {
  const cols = (table as { columns?: unknown }).columns
  if (!Array.isArray(cols)) return false
  return cols.some(
    (c) => !!c && typeof c === "object" && (c as { selected?: unknown }).selected === true,
  )
}

function emitTables(config: ConfigLike): Array<Record<string, unknown>> {
  const tables = (config as { tables?: unknown } | null | undefined)?.tables
  if (!Array.isArray(tables)) return []
  return tables.filter(
    (t): t is Record<string, unknown> =>
      !!t &&
      typeof t === "object" &&
      (t as { emit?: unknown }).emit === true &&
      // Mirror the backend runtime (`_json_shred.load_v2_api_source`): a table
      // is a port only if it ALSO has at least one selected column. An
      // emit-true table with no selected columns is NOT emitted at runtime, so
      // rendering a bindable Handle for it would let an edge bind to a port the
      // executor then KeyErrors on — the very silent-orphan failure this module
      // exists to prevent.
      hasSelectedColumn(t),
  )
}

/**
 * Ordered list of port labels an apiInput exposes.
 *
 * Rules mirror `PipelineNode._SourceHandles` AND the backend runtime
 * (`_json_shred.load_v2_api_source`) so the rendered Handle ids, this
 * list, and the executor's emitted ports are always identical:
 *  - a table counts only if `emit: true` AND it has ≥1 selected column;
 *  - a missing / non-string / blank label falls back to `port_<idx>`;
 *  - a label that collides with an earlier one is suffixed `__<idx>`.
 *
 * Returns `[]` for configs with zero or one emit:true table — those
 * render the single default Handle (id `null`), which has no label.
 */
export function apiInputEmitPortLabels(config: ConfigLike): string[] {
  const emit = emitTables(config)
  if (emit.length < 2) return []
  const seen = new Set<string>()
  return emit.map((t, idx) => {
    const raw = (t as { label?: unknown }).label
    const candidate = typeof raw === "string" && raw.trim() ? raw : `port_${idx}`
    const label = seen.has(candidate) ? `${candidate}__${idx}` : candidate
    seen.add(label)
    return label
  })
}

/**
 * The set of `sourceHandle` values an apiInput's outgoing edges may
 * legitimately carry, given its config:
 *  - multi-port (≥2 emit tables): the derived label set;
 *  - single/zero-port: the single default Handle, whose id is `null`.
 *
 * `null` is encoded as the empty string in the returned set so it can
 * be compared against `edge.sourceHandle ?? ""`.
 */
function validSourceHandleKeys(config: ConfigLike): Set<string> {
  const labels = apiInputEmitPortLabels(config)
  // Multi-port: the labelled handles are the only valid ports. The
  // default null handle is NOT rendered in this mode, so a legacy
  // null-handle edge is orphaned (single→multi transition).
  if (labels.length > 0) return new Set(labels)
  // Single/zero-port: the default Handle (id null → "") is the only port.
  return new Set([""])
}

export type ReconciledApiInputEdge = {
  edge: SimpleEdge
  /** The stale `sourceHandle` the edge was bound to (null shown as null). */
  sourceHandle: string | null
}

export type ReconcileApiInputEdgesResult<E extends SimpleEdge> = {
  /** Edges with orphaned ones removed. Same reference as the input when nothing changed. */
  edges: E[]
  /** The removed edges + the stale port they pointed at. Empty when nothing changed. */
  removed: ReconciledApiInputEdge[]
}

/**
 * Prune outgoing edges of `nodeId` whose `sourceHandle` no longer maps
 * to a rendered port under `config`.
 *
 * Pure: it computes the result, never mutates. The caller is
 * responsible for committing the new edge list and surfacing the
 * removal to the user (a toast naming what was disconnected) — pruning
 * silently would just trade one invisible failure for another.
 *
 * Returns the original `edges` array reference untouched when nothing
 * is orphaned, so callers can cheaply skip a state update.
 */
export function reconcileApiInputEdges<E extends SimpleEdge>({
  nodeId,
  config,
  edges,
}: {
  nodeId: string
  config: ConfigLike
  edges: E[]
}): ReconcileApiInputEdgesResult<E> {
  const validKeys = validSourceHandleKeys(config)
  const removed: ReconciledApiInputEdge[] = []
  const kept: E[] = []
  for (const edge of edges) {
    if (edge.source !== nodeId) {
      kept.push(edge)
      continue
    }
    const handleKey = edge.sourceHandle ?? ""
    if (validKeys.has(handleKey)) {
      kept.push(edge)
      continue
    }
    removed.push({ edge, sourceHandle: edge.sourceHandle ?? null })
  }
  // Preserve referential identity when nothing was orphaned so callers
  // can short-circuit a re-render / snapshot.
  if (removed.length === 0) return { edges, removed }
  return { edges: kept, removed }
}
