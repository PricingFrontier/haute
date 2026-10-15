import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react"
import type { Edge, Node } from "@xyflow/react"

import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useGraphStore from "../stores/useGraphStore"
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
 * fetched for, while the document is editable and its top level shows: to each node whose
 * copy differs from them, committed without an undo step, since the copies follow the
 * form and not the graph's history. They are checked again whenever the graph's nodes
 * change, so an undo or a redo that puts back an older copy is followed at once by the
 * copy the form gives; a node created from the palette is reported through
 * `nodeCreated`, so one made while its tables were being fetched still gets them.
 * `bringUpToDate` fetches afresh, waits for that fetch and applies it at once, for a save
 * that must carry the form's tables rather than wait for a render; it resolves false when
 * the fetch failed, when the document cannot change, read as it is now even for a save
 * that began before a fence closed, and when an update was refused outright, so the save
 * is refused. `copiesCurrent` checks the copies against the tables already fetched, for a
 * save once its pending commits have settled.
 */
export default function useWorkbenchTables({
  graphRef,
  onUpdateNode,
  editable,
}: UseWorkbenchTablesOptions): {
  nodeCreated: (nodeId: string) => void
  bringUpToDate: () => Promise<boolean>
  copiesCurrent: () => CopiesState
} {
  const enabled = useWorkbenchStore((state) => state.enabled)
  const workbench = useWorkbenchStore((state) => state.tables)
  const generation = useDocumentStatusStore((state) => state.executionGeneration)
  // Every change of the graph's nodes, an undo or a redo among them, checks the copies.
  const nodes = useGraphStore((state) => state.nodes)
  // The fetch started when this generation's document was adopted: tables from an
  // earlier fetch were read for an earlier document.
  const adoptionFetch = useRef<{ generation: number; fetch: number } | null>(null)
  // The update on its way to each workbench node in this generation, by the fetch it
  // carries: not sent again while it resolves, and no longer current once a newer
  // fetch's update reaches the node.
  const sent = useRef({ generation, fetches: new Map<string, number>() })
  // Each node's config last found holding a fetch's tables, so a change elsewhere in the
  // graph (a drag) compares nothing.
  const checked = useRef(new Map<string, { config: unknown; fetch: number }>())
  const created = useRef<string[]>([])
  const [creations, setCreations] = useState(0)
  // `editable` as it is now, for a save that began before a fence closed (a submodel
  // opened, the assistant's turn started) and still holds the earlier closure.
  const editableRef = useRef(editable)
  useEffect(() => { editableRef.current = editable }, [editable])

  useEffect(() => {
    if (!enabled) return
    const fetch = useWorkbenchStore.getState().refreshTables()
    if (fetch !== null) adoptionFetch.current = { generation, fetch }
  }, [enabled, generation])

  /**
   * Give the nodes *workbench*'s tables, and answer whether every update that was needed
   * was accepted: false while nothing can be applied (the document cannot change, as
   * rendered or as it is now; no tables; a fetch older than the document's own), and
   * false when the commit controller refuses an update outright (the document read-only,
   * the node gone, the update superseded), since nothing was applied and nothing will be.
   * An update still resolving its identity is accepted here; its outcome reaches the
   * pending commits a save waits for.
   */
  const apply = useCallback((workbench: WorkbenchTables | null, only: ReadonlySet<string> | null): boolean => {
    const adoption = adoptionFetch.current
    if (!editable || !editableRef.current || workbench === null || adoption === null) return false
    if (adoption.generation !== generation || workbench.fetch < adoption.fetch) return false
    if (sent.current.generation !== generation) {
      sent.current = { generation, fetches: new Map() }
      checked.current = new Map()
    }
    const { fetch } = workbench
    const fetches = sent.current.fetches
    let accepted = true
    for (const node of graphRef.current.nodes) {
      const patchFor = WORKBENCH_COPY_PATCHES[String(node.data.nodeType)]
      if (patchFor === undefined || (only !== null && !only.has(node.id))) continue
      if (fetches.get(node.id) === fetch) continue
      const config = (node.data.config ?? {}) as Record<string, unknown>
      const last = checked.current.get(node.id)
      if (last !== undefined && last.config === node.data.config && last.fetch === fetch) continue
      const patch = patchFor(config, workbench)
      if (patch === null) {
        // An older fetch's update still resolving would leave the node behind this one.
        fetches.delete(node.id)
        checked.current.set(node.id, { config: node.data.config, fetch })
        continue
      }
      fetches.set(node.id, fetch)
      // Current while the document is, and while no newer fetch has reached the node: an
      // update still resolving its identity when a newer fetch finds the node up to date
      // would otherwise commit the older tables over the newer.
      const isCurrent = () =>
        useDocumentStatusStore.getState().executionGeneration === generation && fetches.get(node.id) === fetch
      // Settled, committed or not, it is on its way no more: a copy still behind (one a
      // submodel opened over while its identity resolved) is sent again by the next pass.
      const onSettled = () => {
        if (fetches.get(node.id) === fetch) fetches.delete(node.id)
      }
      const result = onUpdateNode(
        node.id,
        { ...node.data, config: { ...config, ...patch } },
        { isCurrent, onSettled, history: false },
      )
      if (!result.ok) accepted = false
    }
    return accepted
  }, [editable, generation, graphRef, onUpdateNode])

  useEffect(() => {
    apply(workbench, null)
  }, [apply, workbench, nodes])

  useEffect(() => {
    if (created.current.length === 0) return
    apply(workbench, new Set(created.current.splice(0)))
  }, [apply, workbench, creations])

  const nodeCreated = useCallback((nodeId: string) => {
    created.current.push(nodeId)
    setCreations((count) => count + 1)
  }, [])
  // A fetch of its own, waited for and applied here rather than on a render: when this
  // resolves true the nodes carry the workbench's tables as the form now has them, or an
  // update on its way to them, so a pipeline save that follows carries them too. False
  // when the fetch failed, when the document cannot change, before the fetch or once it
  // has settled, and when an update was refused outright: either way the nodes may be
  // behind the form, and the save must not go on.
  const bringUpToDate = useCallback(async (): Promise<boolean> => {
    if (!editableRef.current) return false
    const store = useWorkbenchStore.getState()
    if (store.refreshTables() === null) return true
    if (!(await store.awaitTables())) return false
    return apply(useWorkbenchStore.getState().tables, null)
  }, [apply])
  const copiesCurrent = useCallback((): CopiesState => {
    if (!apply(useWorkbenchStore.getState().tables, null)) return "refused"
    return sent.current.fetches.size > 0 ? "sent" : "current"
  }, [apply])
  return { nodeCreated, bringUpToDate, copiesCurrent }
}

/**
 * The copies against the tables already fetched: each holding them (`current`), an update
 * on its way to one, just sent or still resolving, to be waited for (`sent`), or an update
 * refused outright or none possible, the document unable to change (`refused`).
 */
export type CopiesState = "current" | "sent" | "refused"
