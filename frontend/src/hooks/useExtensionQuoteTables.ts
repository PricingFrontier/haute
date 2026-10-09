import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react"
import type { Edge, Node } from "@xyflow/react"

import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useExtensionsStore from "../stores/useExtensionsStore"
import { WORKBENCH_COPY_PATCHES, quoteTablesSupplier } from "../utils/extensionQuoteTables"
import type { GraphCommitController } from "./useGraphCommitController"

export type UseExtensionQuoteTablesOptions = {
  graphRef: MutableRefObject<{ nodes: Node[]; edges: Edge[] }>
  onUpdateNode: GraphCommitController["onUpdateNode"]
  /** The document can change and its top level, where the Workbench Input is, shows. */
  editable: boolean
}

/**
 * Keeps each Workbench Input up to date with the tables the installed extension
 * supplies, and each Workbench Output with the response's tables (specs/extensions).
 * The tables are fetched when the supplier is listed and whenever a document is adopted
 * (`executionGeneration`), and applied only to the document they were fetched for: once
 * per node for each fetch, while the document is editable and its top level shows. A
 * graph edit, an undo or a redo never runs it; a node created from the palette is
 * reported through `nodeCreated`, so one made while its tables were being fetched still
 * gets them.
 */
export default function useExtensionQuoteTables({
  graphRef,
  onUpdateNode,
  editable,
}: UseExtensionQuoteTablesOptions): { nodeCreated: (nodeId: string) => void } {
  const supplier = useExtensionsStore((state) => quoteTablesSupplier(state.extensions)?.name ?? null)
  const quoteTables = useExtensionsStore((state) => state.quoteTables)
  const generation = useDocumentStatusStore((state) => state.executionGeneration)
  // The fetch started when this generation's document was adopted: tables from an
  // earlier fetch were read for an earlier document.
  const adoptionFetch = useRef<{ generation: number; fetch: number } | null>(null)
  // The fetch whose tables each Workbench Input has been given in this generation.
  const given = useRef({ generation, fetches: new Map<string, number>() })
  const created = useRef<string[]>([])
  const [creations, setCreations] = useState(0)

  useEffect(() => {
    if (supplier === null) return
    const fetch = useExtensionsStore.getState().refreshQuoteTables()
    if (fetch !== null) adoptionFetch.current = { generation, fetch }
  }, [supplier, generation])

  const apply = useCallback((only: ReadonlySet<string> | null) => {
    const adoption = adoptionFetch.current
    if (!editable || quoteTables === null || adoption === null) return
    if (adoption.generation !== generation || quoteTables.fetch < adoption.fetch) return
    if (given.current.generation !== generation) given.current = { generation, fetches: new Map() }
    const isCurrent = () => useDocumentStatusStore.getState().executionGeneration === generation
    const { fetch } = quoteTables
    const fetches = given.current.fetches
    for (const node of graphRef.current.nodes) {
      const patchFor = WORKBENCH_COPY_PATCHES[String(node.data.nodeType)]
      if (patchFor === undefined || (only !== null && !only.has(node.id))) continue
      const config = (node.data.config ?? {}) as Record<string, unknown>
      if (fetches.get(node.id) === fetch) continue
      fetches.set(node.id, fetch)
      const patch = patchFor(config, quoteTables)
      if (patch === null) continue
      // An update that does not commit, such as one a submodel opened while its identity
      // was resolving, is forgotten, so the next pass (the top level showing again, or the
      // next fetch) tries it again.
      const onSettled = (result: OnUpdateConfigResult) => {
        if (!result.ok && fetches.get(node.id) === fetch) fetches.delete(node.id)
      }
      onUpdateNode(node.id, { ...node.data, config: { ...config, ...patch } }, { isCurrent, onSettled })
    }
  }, [editable, generation, graphRef, onUpdateNode, quoteTables])

  useEffect(() => {
    apply(null)
  }, [apply])

  useEffect(() => {
    if (created.current.length === 0) return
    apply(new Set(created.current.splice(0)))
  }, [apply, creations])

  const nodeCreated = useCallback((nodeId: string) => {
    created.current.push(nodeId)
    setCreations((count) => count + 1)
  }, [])
  return { nodeCreated }
}
