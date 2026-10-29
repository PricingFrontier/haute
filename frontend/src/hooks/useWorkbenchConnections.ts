import { useCallback, useEffect, useRef, type MutableRefObject } from "react"
import type { Edge, Node } from "@xyflow/react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { NODE_TYPES } from "../utils/nodeTypes"
import { workbenchDisconnections } from "../utils/workbenchTables"

export type UseWorkbenchConnectionsOptions = {
  /** The graph the editor shows: the pipeline's top level, or the open submodel's. */
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
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
  graphRef,
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
    const held = workbenchDisconnections(graphRef.current, tables)
    if (held === null) return true
    graphRef.current = { ...graphRef.current, edges: held.edges }
    commitEdges(held.edges)
    for (const { node, edges: removed } of held.removed) {
      addToast("warning", tablesGoneWarning(String(node.data.label ?? node.id), node.data.nodeType, removed.length))
    }
    return true
  }, [addToast, commitEdges, graphRef])

  useEffect(() => {
    if (editable) holdConnections()
  }, [editable, holdConnections, workbench, edges])

  return { holdConnections }
}
