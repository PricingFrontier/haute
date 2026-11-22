/**
 * What a node's editor reads and writes of the graph around it, shared by the node panel
 * and the Modelling view: the columns arriving at a node, and a node's data once an edit
 * has made its cached preview shape stale.
 */
import { useMemo } from "react"
import type { HauteNodeData } from "../types/node"
import type { SimpleEdge, SimpleNode } from "./editors/_shared"

export type ColumnInfo = { name: string; dtype: string }

/**
 * The columns an edge carries: the source's columns for the output handle
 * the edge leaves from (a submodel output), else the source's columns.
 */
export function edgeSourceColumns(edge: SimpleEdge, nodeMap: Record<string, SimpleNode>): ColumnInfo[] | undefined {
  const data = nodeMap[edge.source]?.data as Record<string, unknown> | undefined
  const frames = data?._frameColumns as Record<string, ColumnInfo[]> | undefined
  const handle = edge.sourceHandle
  if (handle && frames?.[handle]) return frames[handle]
  return data?._columns as ColumnInfo[] | undefined
}

/** Collect upstream columns from already-filtered incoming edges. */
function collectColumnsFromEdges(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): ColumnInfo[] {
  const cols: ColumnInfo[] = []
  const seen = new Set<string>()
  edges.forEach(e => {
    const srcCols = edgeSourceColumns(e, nodeMap)
    if (srcCols) srcCols.forEach(c => { if (!seen.has(c.name)) { seen.add(c.name); cols.push(c) } })
  })
  return cols
}

export function columnsSignature(columns: ColumnInfo[] | undefined): string {
  return columns?.map((column) => `${column.name}\u0002${column.dtype}`).join("\u0001") ?? ""
}

function upstreamColumnsSignature(edges: SimpleEdge[], nodeMap: Record<string, SimpleNode>): string {
  return edges
    .map((edge) => `${edge.source}\u0003${edge.sourceHandle ?? ""}\u0003${columnsSignature(edgeSourceColumns(edge, nodeMap))}`)
    .join("\u0004")
}

/**
 * The columns arriving at a node over its incoming edges, as one array per upstream schema:
 * an edit to the node's own config or label rebuilds the node map and leaves the upstream
 * schema, and so this array's identity, as it was.
 */
export function useUpstreamColumns(
  nodeId: string,
  upstreamEdges: SimpleEdge[],
  nodeMap: Record<string, SimpleNode>,
): ColumnInfo[] {
  const signature = upstreamColumnsSignature(upstreamEdges, nodeMap)
  return useMemo(
    () => collectColumnsFromEdges(upstreamEdges, nodeMap),
    // Keyed by the node and the upstream schema's content, not by the map's identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodeId, signature],
  )
}

// Cached preview-result fields cleared on user config edits.  Listing the
// keys explicitly (rather than stripping every leading-underscore field)
// lets selected_columns-only edits preserve `_availableColumns`: that
// pre-filter schema remains valid while the post-filter preview is stale.
// preserves selection/trace state — `_status`, `_traceActive`, etc. — which
// are intentionally NOT invalidated by a config change.  Adding a new cached
// field to ``HauteNodeData`` will not surface here automatically — the choice
// to clear or preserve a new field must be made deliberately by extending
// this list.
const CACHED_PREVIEW_KEYS: readonly (keyof HauteNodeData)[] = [
  "_columns",
  "_availableColumns",
  "_frameColumns",
  "_schemaWarnings",
  "_notes",
  "_columnsSource",
  "_columnsStructuralVersion",
]

export function clearCachedResultShape<T extends Record<string, unknown>>(
  data: T,
  { preserveAvailableColumns = false }: { preserveAvailableColumns?: boolean } = {},
): T {
  const next = { ...data }
  for (const key of CACHED_PREVIEW_KEYS) {
    if (preserveAvailableColumns && key === "_availableColumns") continue
    delete next[key as keyof T]
  }
  return next
}
