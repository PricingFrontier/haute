import { useEffect, useMemo, useRef } from "react"
import type { Edge, Node } from "@xyflow/react"

import { resolveEditorNodeIdentities } from "../api/client"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import type { EditorIdentityBatchRequest } from "../api/types"

/**
 * Revalidate a document loaded with name violations after each edit.
 *
 * While the document is fenced by its names, every change to the nodes,
 * edges or submodels that could change a name sends the whole graph, as
 * save receives it, to the editor identity request; the server answers with
 * the violations that remain, which the banner and the save/run fence follow.
 * A newer edit supersedes an answer still in flight. Positions are left out
 * of the change key, so dragging a node asks nothing.
 */
export function useNameViolationRevalidation({
  nodes,
  edges,
  submodels,
  buildContextGraph,
}: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  submodels: unknown
  /** A stable builder of the graph save would receive. */
  buildContextGraph: () => NonNullable<EditorIdentityBatchRequest["graph"]>
}): void {
  const fenced = useDocumentStatusStore((state) => state.nameFence !== null)
  const requestSerial = useRef(0)

  const changeKey = useMemo(() => {
    if (!fenced) return null
    return JSON.stringify([
      nodes.map((node) => [node.id, node.data?.label, node.data?.config]),
      edges.map((edge) => [edge.source, edge.target, edge.sourceHandle, edge.targetHandle]),
      submodels,
    ])
  }, [fenced, nodes, edges, submodels])

  useEffect(() => {
    if (changeKey === null) return
    const request = ++requestSerial.current
    resolveEditorNodeIdentities({ nodes: [], graph: buildContextGraph() })
      .then((response) => {
        if (request !== requestSerial.current) return
        useDocumentStatusStore.getState().setNameViolations(response.violations ?? [])
      })
      .catch((error: unknown) => {
        if (request !== requestSerial.current) return
        // The banner keeps the last answer, so the fence stays closed.
        useToastStore.getState().addToast(
          "error",
          `Could not recheck the pipeline's names: ${apiErrorMessage(error, "unknown error")}`,
        )
      })
  }, [changeKey, buildContextGraph])
}
