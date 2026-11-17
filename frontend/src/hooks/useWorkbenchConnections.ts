import { useCallback, useEffect, useRef } from "react"
import type { Edge } from "@xyflow/react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore, { canvasGraph } from "../stores/useGraphStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import type { PipelineEdge } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import { workbenchDisconnections } from "../utils/workbenchTables"

export type UseWorkbenchConnectionsOptions = {
  /** The graph the editor shows: the pipeline's top level, or the open submodel's. */
  /** Replace the shown graph's edges without an undo step. */
  commitEdges: (edges: Edge[]) => void
  /** The document can change. */
  editable: boolean
  addToast: (type: "warning", text: string) => void
}

/**
 * Fetches the workbench's tables (specs/workbench) when the workbench is found enabled and
 * whenever a document is adopted (`executionGeneration`), and holds each Workbench Input's
 * and Workbench Output's connections to the tables the newest fetch gave, whose names are
 * their ports: in the graph the editor shows, while the document can change, a connection
 * to a table whose name has gone is removed and reported in a warning toast, committed
 * without an undo step, since the tables are the form's and not the graph's
 * history. The connections are held again whenever the edges change, so one that an undo,
 * a redo or a paste brings back is removed again at once. A document is held only to tables
 * fetched since it was adopted, an earlier document's form having had tables of its own; a
 * fetch that failed leaves the tables of the one before, and removes nothing more.
 * `holdConnections` holds them at once, for a save that has just fetched the tables, and
 * answers whether it could: false while no fetch made since the document was adopted has
 * landed.
 */
/**
 * The warning that *count* connections of the workbench node *label* were removed because
 * the workbench no longer has the tables they used: one a Workbench Input's connection read,
 * or a Workbench Output's filled.
 */
function tablesGoneWarning(label: string, nodeType: unknown, count: number): string {
  const one = count === 1
  const used = nodeType === NODE_TYPES.WORKBENCH_OUTPUT ? "filled" : "read"
  return `Disconnected ${count} edge${one ? "" : "s"} from ${label}: the workbench no longer has the table${one ? " it" : "s they"} ${used}.`
}

export default function useWorkbenchConnections({
  
  commitEdges,
  editable,
  addToast,
}: UseWorkbenchConnectionsOptions): { holdConnections: () => boolean } {
  const enabled = useWorkbenchStore((state) => state.enabled)
  const workbench = useWorkbenchStore((state) => state.tables)
  const generation = useDocumentStatusStore((state) => state.executionGeneration)
  // Every change of the shown graph's edges, an undo, a redo or a paste among them.
  const edges = useGraphStore((state) => state.edges)
  // The fetch started when this generation's document was adopted: tables from an earlier
  // fetch were read for an earlier document.
  const adoptionFetch = useRef<{ generation: number; fetch: number } | null>(null)

  useEffect(() => {
    if (!enabled) return
    const fetch = useWorkbenchStore.getState().refreshTables()
    if (fetch !== null) adoptionFetch.current = { generation, fetch }
  }, [enabled, generation])

  const holdConnections = useCallback((): boolean => {
    const tables = useWorkbenchStore.getState().tables
    const adoption = adoptionFetch.current
    if (
      tables === null
      || adoption === null
      || adoption.generation !== useDocumentStatusStore.getState().executionGeneration
      || tables.fetch < adoption.fetch
    ) {
      return false
    }
    const held = workbenchDisconnections(canvasGraph(), tables)
    if (held !== null) {
      commitEdges(held.edges)
      for (const { node, edges: removed } of held.removed) {
        addToast("warning", tablesGoneWarning(String(node.data.label ?? node.id), node.data.nodeType, removed.length))
      }
    }
    // Behind an open submodel the pipeline's own workbench nodes are held too: a save
    // there sends the whole pipeline.
    const focus = useGraphStore.getState().focus
    const behind = focus === null ? null : workbenchDisconnections({ nodes: focus.rootNodes, edges: focus.rootEdges }, tables)
    if (behind !== null) {
      useGraphStore.getState().setRootEdgesRaw(behind.edges as PipelineEdge[])
      for (const { node, edges: removed } of behind.removed) {
        addToast("warning", tablesGoneWarning(String(node.data.label ?? node.id), node.data.nodeType, removed.length))
      }
    }
    return true
  }, [addToast, commitEdges])

  useEffect(() => {
    if (editable) holdConnections()
  }, [editable, holdConnections, workbench, edges])

  return { holdConnections }
}
