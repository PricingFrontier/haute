import { useEffect, useMemo, useRef } from "react"
import type { Edge, Node } from "@xyflow/react"

import { resolveEditorNodeIdentities } from "../api/client"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import useToastStore from "../stores/useToastStore"
import { apiErrorMessage } from "../api/errors"
import type { EditorIdentityBatchRequest } from "../api/types"
import { isRequestInputType } from "../utils/nodeTypes"

/**
 * The names each input binding's owner sets: a request input's frame labels
 * (its config's tables) by node, a submodel definition's public input ports by
 * definition.
 */
function inputBindingOwners(nodes: readonly Node[], submodels: unknown): Record<string, unknown> {
  const owners: Record<string, unknown> = {}
  for (const node of nodes) {
    if (!isRequestInputType(node.data?.nodeType)) continue
    const tables = (node.data.config as Record<string, unknown> | undefined)?.tables
    owners[`node:${node.id}`] = Array.isArray(tables)
      ? tables.map((table: unknown) => (table as Record<string, unknown> | null)?.label)
      : null
  }
  if (typeof submodels === "object" && submodels !== null) {
    for (const [definitionId, definition] of Object.entries(submodels)) {
      const ports = (definition as Record<string, unknown> | null)?.inputPorts
      owners[`definition:${definitionId}`] = Array.isArray(ports)
        ? ports.map((port: unknown) => (port as Record<string, unknown> | null)?.name)
        : null
    }
  }
  return owners
}

/** Whether an owner present before and after changed the names it sets. */
function bindingEdited(previousKey: string, nextKey: string): boolean {
  const previous = JSON.parse(previousKey) as Record<string, unknown>
  const next = JSON.parse(nextKey) as Record<string, unknown>
  return Object.keys(next).some(
    (owner) => Object.hasOwn(previous, owner)
      && JSON.stringify(previous[owner]) !== JSON.stringify(next[owner]),
  )
}

/**
 * Revalidate the document's names after edits that could change them.
 *
 * While a document loaded with name violations is fenced by them, every change
 * to the nodes, edges, submodels or preamble that could change a name sends the
 * whole graph, as save receives it, to the editor identity request; the server
 * answers with the violations that remain, which the banner and the save/run
 * fence follow. In any other document, an edit to an existing API Input's frame
 * labels or a definition's public input ports asks the same, and so does any
 * such change while one of their violations is showing; the violations with a
 * binding go to the editor that set it. A newer edit supersedes an answer
 * still in flight. Positions are left out of the change key, so dragging a node
 * asks nothing.
 */
export function useNameViolationRevalidation({
  nodes,
  edges,
  submodels,
  preamble,
  buildContextGraph,
}: {
  nodes: readonly Node[]
  edges: readonly Edge[]
  submodels: unknown
  /** The root preamble: removing a colliding helper or import clears a violation. */
  preamble: string
  /** A stable builder of the graph save would receive. */
  buildContextGraph: () => NonNullable<EditorIdentityBatchRequest["graph"]>
}): void {
  const fenced = useDocumentStatusStore((state) => state.nameFence !== null)
  const requestSerial = useRef(0)
  const previousBindings = useRef<string | null>(null)

  const bindingsKey = useMemo(
    () => JSON.stringify(inputBindingOwners(nodes, submodels)),
    [nodes, submodels],
  )
  const changeKey = useMemo(() => {
    if (!fenced) return JSON.stringify([bindingsKey, preamble])
    return JSON.stringify([
      nodes.map((node) => [node.id, node.data?.label, node.data?.config]),
      edges.map((edge) => [edge.source, edge.target, edge.sourceHandle, edge.targetHandle]),
      submodels,
      preamble,
    ])
  }, [fenced, bindingsKey, nodes, edges, submodels, preamble])

  useEffect(() => {
    const previous = previousBindings.current
    previousBindings.current = bindingsKey
    if (!fenced) {
      const showing = useDocumentStatusStore.getState().inputBindingViolations.length > 0
      if (!showing && (previous === null || !bindingEdited(previous, bindingsKey))) return
    }
    const request = ++requestSerial.current
    resolveEditorNodeIdentities({ nodes: [], graph: buildContextGraph() })
      .then((response) => {
        if (request !== requestSerial.current) return
        const violations = response.violations ?? []
        const store = useDocumentStatusStore.getState()
        store.setNameViolations(violations)
        store.setInputBindingViolations(violations)
      })
      .catch((error: unknown) => {
        if (request !== requestSerial.current) return
        // The banner and the editors keep the last answer, so the fence stays closed.
        useToastStore.getState().addToast(
          "error",
          `Could not recheck the pipeline's names: ${apiErrorMessage(error, "unknown error")}`,
        )
      })
  }, [changeKey, bindingsKey, fenced, buildContextGraph])
}
