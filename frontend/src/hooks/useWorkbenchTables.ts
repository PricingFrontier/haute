import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react"
import type { Edge, Node } from "@xyflow/react"

import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useWorkbenchStore from "../stores/useWorkbenchStore"
import { WORKBENCH_COPY_PATCHES, type WorkbenchTables } from "../utils/workbenchTables"
import type { GraphCommitController } from "./useGraphCommitController"

export type UseWorkbenchTablesOptions = {
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  onUpdateNode: GraphCommitController["onUpdateNode"]
  /** The document can change and its top level, where the workbench nodes are, shows. */
  editable: boolean
}

/**
 * Keeps each Workbench Input up to date with the tables and sample the project's
 * workbench supplies, and each Workbench Output with the response's tables
 * (specs/workbench). The tables are fetched when the workbench is enabled and whenever a
 * document is adopted (`executionGeneration`), and applied only to the document they were
 * fetched for: once per node for each fetch, while the document is editable and its top
 * level shows. A graph edit, an undo or a redo never runs it; a node created from the
 * palette is reported through `nodeCreated`, so one made while its tables were being
 * fetched still gets them. `bringUpToDate` fetches afresh, waits for that fetch and
 * applies it at once, for a save that must carry the form's tables rather than wait for
 * a render; it resolves false when the fetch failed, and while the document cannot
 * change, when nothing could be applied, so the save is refused.
 */
export default function useWorkbenchTables({
  graphRef,
  onUpdateNode,
  editable,
}: UseWorkbenchTablesOptions): { nodeCreated: (nodeId: string) => void; bringUpToDate: () => Promise<boolean> } {
  const enabled = useWorkbenchStore((state) => state.enabled)
  const workbench = useWorkbenchStore((state) => state.tables)
  const generation = useDocumentStatusStore((state) => state.executionGeneration)
  // The fetch started when this generation's document was adopted: tables from an
  // earlier fetch were read for an earlier document.
  const adoptionFetch = useRef<{ generation: number; fetch: number } | null>(null)
  // The fetch whose tables each workbench node has been given in this generation.
  const given = useRef({ generation, fetches: new Map<string, number>() })
  const created = useRef<string[]>([])
  const [creations, setCreations] = useState(0)

  useEffect(() => {
    if (!enabled) return
    const fetch = useWorkbenchStore.getState().refreshTables()
    if (fetch !== null) adoptionFetch.current = { generation, fetch }
  }, [enabled, generation])

  const apply = useCallback((workbench: WorkbenchTables | null, only: ReadonlySet<string> | null) => {
    const adoption = adoptionFetch.current
    if (!editable || workbench === null || adoption === null) return
    if (adoption.generation !== generation || workbench.fetch < adoption.fetch) return
    if (given.current.generation !== generation) given.current = { generation, fetches: new Map() }
    const { fetch } = workbench
    const fetches = given.current.fetches
    for (const node of graphRef.current.nodes) {
      const patchFor = WORKBENCH_COPY_PATCHES[String(node.data.nodeType)]
      if (patchFor === undefined || (only !== null && !only.has(node.id))) continue
      const config = (node.data.config ?? {}) as Record<string, unknown>
      if (fetches.get(node.id) === fetch) continue
      fetches.set(node.id, fetch)
      const patch = patchFor(config, workbench)
      if (patch === null) continue
      // Current while the document is, and while no newer fetch has reached the node: an
      // update still resolving its identity when a newer fetch finds the node up to date
      // would otherwise commit the older tables over the newer.
      const isCurrent = () =>
        useDocumentStatusStore.getState().executionGeneration === generation && fetches.get(node.id) === fetch
      // An update that does not commit, such as one a submodel opened while its identity
      // was resolving, is forgotten, so the next pass (the top level showing again, or the
      // next fetch) tries it again.
      const onSettled = (result: OnUpdateConfigResult) => {
        if (!result.ok && fetches.get(node.id) === fetch) fetches.delete(node.id)
      }
      onUpdateNode(node.id, { ...node.data, config: { ...config, ...patch } }, { isCurrent, onSettled })
    }
  }, [editable, generation, graphRef, onUpdateNode])

  useEffect(() => {
    apply(workbench, null)
  }, [apply, workbench])

  useEffect(() => {
    if (created.current.length === 0) return
    apply(workbench, new Set(created.current.splice(0)))
  }, [apply, workbench, creations])

  const nodeCreated = useCallback((nodeId: string) => {
    created.current.push(nodeId)
    setCreations((count) => count + 1)
  }, [])
  // A fetch of its own, waited for and applied here rather than on a render: when this
  // resolves true the nodes carry the workbench's tables as the form now has them, so a
  // pipeline save that follows carries them too. False when the fetch failed, and while
  // the document cannot change, when nothing can be applied: either way the nodes may be
  // behind the form, and the save must not go on. The project save refuses such a
  // document before asking, so the second is a guard, never a success.
  const bringUpToDate = useCallback(async (): Promise<boolean> => {
    if (!editable) return false
    const store = useWorkbenchStore.getState()
    if (store.refreshTables() === null) return true
    if (!(await store.awaitTables())) return false
    apply(useWorkbenchStore.getState().tables, null)
    return true
  }, [apply, editable])
  return { nodeCreated, bringUpToDate }
}
