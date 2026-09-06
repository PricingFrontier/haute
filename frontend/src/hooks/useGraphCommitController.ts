import { useCallback, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from "react"
import type { Edge, Node } from "@xyflow/react"

import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import { isSubmodelInstanceConfig, type SubmodelInstanceConfig } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import {
  prepareNodeUpdate,
  type PreparedNodeUpdate,
} from "../utils/nodeUpdatePlan"

type GraphSnapshot = { nodes: Node[]; edges: Edge[] }
type ToastType = "success" | "error" | "warning" | "info"

type GraphCommitRequest = {
  nodeId: string
  generation: number
  graph: GraphSnapshot
  submodels: Record<string, unknown>
  documentIdentity: string
}

export type UseGraphCommitControllerOptions = {
  graphRef: MutableRefObject<GraphSnapshot>
  submodelsRef: MutableRefObject<Record<string, unknown>>
  readDocumentIdentity: () => string
  readOnly: boolean
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveNodeIdentities: (candidateNodes: readonly Node[]) => Promise<Node[]>
  commitGraph: (
    nodes: Node[],
    edges: Edge[],
    submodels: Record<string, unknown>,
  ) => void
  setSelectedNode: Dispatch<SetStateAction<Node | null>>
  addToast: (type: ToastType, text: string) => void
}

export type GraphCommitController = {
  onUpdateNode: (nodeId: string, data: Record<string, unknown>) => OnUpdateConfigResult
  onRenameNode: (nodeId: string, label: string) => Promise<OnUpdateConfigResult>
  waitForPendingCommits: () => Promise<OnUpdateConfigResult>
}

/**
 * Owns request generations and the one selected-node graph commit boundary.
 * The pure planner computes candidates; this hook alone decides whether an
 * asynchronous candidate still owns the graph/document fence and may commit.
 */
export default function useGraphCommitController({
  graphRef,
  submodelsRef,
  readDocumentIdentity,
  readOnly,
  reservedApiInputFrameLabels,
  resolveNodeIdentities,
  commitGraph,
  setSelectedNode,
  addToast,
}: UseGraphCommitControllerOptions): GraphCommitController {
  const requestGenerationsRef = useRef(new Map<string, number>())
  const pendingCommitsRef = useRef(new Set<Promise<OnUpdateConfigResult>>())

  const registerPendingCommit = useCallback((pending: Promise<OnUpdateConfigResult>): void => {
    pendingCommitsRef.current.add(pending)
    void pending.finally(() => {
      pendingCommitsRef.current.delete(pending)
    })
  }, [])

  const waitForPendingCommits = useCallback(async (): Promise<OnUpdateConfigResult> => {
    while (pendingCommitsRef.current.size > 0) {
      const results = await Promise.all([...pendingCommitsRef.current])
      const failure = results.find((result) => !result.ok)
      if (failure) return failure
    }
    return { ok: true }
  }, [])

  const prepare = useCallback((
    nodeId: string,
    data: Record<string, unknown>,
    refreshSourceIdentity: boolean,
  ) => prepareNodeUpdate({
    nodeId,
    data,
    refreshSourceIdentity,
    readOnly,
    graph: graphRef.current,
    submodels: submodelsRef.current,
    reservedApiInputFrameLabels,
  }), [graphRef, readOnly, reservedApiInputFrameLabels, submodelsRef])

  const beginRequest = useCallback((nodeId: string): GraphCommitRequest => {
    const generation = (requestGenerationsRef.current.get(nodeId) ?? 0) + 1
    requestGenerationsRef.current.set(nodeId, generation)
    return {
      nodeId,
      generation,
      graph: graphRef.current,
      submodels: submodelsRef.current,
      documentIdentity: readDocumentIdentity(),
    }
  }, [graphRef, readDocumentIdentity, submodelsRef])

  const requestIsStale = useCallback((request: GraphCommitRequest): boolean => (
    requestGenerationsRef.current.get(request.nodeId) !== request.generation
    || graphRef.current !== request.graph
    || submodelsRef.current !== request.submodels
    || readDocumentIdentity() !== request.documentIdentity
  ), [graphRef, readDocumentIdentity, submodelsRef])

  const commit = useCallback((prepared: PreparedNodeUpdate): void => {
    // Keep request-facing refs coherent immediately; the store subscription
    // effects will observe the same identities after React commits.
    graphRef.current = { nodes: prepared.nodes, edges: prepared.edges }
    submodelsRef.current = prepared.submodels
    commitGraph(prepared.nodes, prepared.edges, prepared.submodels)
    setSelectedNode((previous) => (
      previous?.id === prepared.nodeId
        ? { ...previous, data: prepared.data }
        : previous
    ))
    if (prepared.removed.length === 0) return
    const label = String(prepared.data.label ?? prepared.nodeId)
    addToast(
      "warning",
      `Disconnected ${prepared.removed.length} edge${prepared.removed.length === 1 ? "" : "s"} from ${label}: the source ${prepared.removed.length === 1 ? "frame no longer exists" : "frames no longer exist"} after your edit.`,
    )
  }, [addToast, commitGraph, graphRef, setSelectedNode, submodelsRef])

  const onUpdateNode = useCallback((
    nodeId: string,
    data: Record<string, unknown>,
  ): OnUpdateConfigResult => {
    const currentNode = graphRef.current.nodes.find((node) => node.id === nodeId)
    if (!currentNode) return { ok: false, error: `Cannot update missing node "${nodeId}".` }
    if (currentNode.data.label !== data.label) {
      return {
        ok: false,
        error: "Use the rename action so the server can resolve the node identity before commit.",
      }
    }

    const preflight = prepare(nodeId, data, false)
    if (!preflight.ok) return preflight
    const request = beginRequest(nodeId)
    if (data.nodeType !== NODE_TYPES.API_INPUT) {
      commit(preflight)
      return { ok: true }
    }

    const candidate = { ...currentNode, data }
    const pending = (async (): Promise<OnUpdateConfigResult> => {
      try {
        const resolved = await resolveNodeIdentities([candidate])
        if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
          throw new Error("identity resolver returned an invalid node")
        }
        if (requestIsStale(request)) {
          return {
            ok: false,
            error: "Node update was not applied because the graph changed while identity resolution was running.",
          }
        }
        const finalPlan = prepare(nodeId, resolved[0].data, true)
        if (!finalPlan.ok) return finalPlan
        commit(finalPlan)
        return { ok: true }
      } catch (error: unknown) {
        return {
          ok: false,
          error: `Update node failed: ${error instanceof Error ? error.message : String(error)}`,
        }
      }
    })()
    registerPendingCommit(pending)
    void pending.then((result) => {
      if (!result.ok) addToast("error", result.error)
    })
    return { ok: true }
  }, [addToast, beginRequest, commit, graphRef, prepare, registerPendingCommit, requestIsStale, resolveNodeIdentities])

  const onRenameNode = useCallback((
    nodeId: string,
    label: string,
  ): Promise<OnUpdateConfigResult> => {
    if (readOnly) return Promise.resolve({ ok: false, error: "This pipeline document is read-only." })
    const currentNode = graphRef.current.nodes.find((node) => node.id === nodeId)
    if (!currentNode) return Promise.resolve({ ok: false, error: `Cannot rename missing node "${nodeId}".` })
    if (currentNode.data.label === label) return Promise.resolve({ ok: true })
    const isSubmodel = currentNode.data?.nodeType === NODE_TYPES.SUBMODEL
      && isSubmodelInstanceConfig(currentNode.data?.config)
    const candidateData: Record<string, unknown> = isSubmodel
      ? {
          ...currentNode.data,
          label,
          config: {
            ...(currentNode.data.config as SubmodelInstanceConfig),
            alias: label,
          },
        }
      : {
          ...currentNode.data,
          label,
        }
    const candidate = { ...currentNode, data: candidateData }
    const request = beginRequest(nodeId)
    const pending = (async (): Promise<OnUpdateConfigResult> => {
      try {
        const resolved = await resolveNodeIdentities([candidate])
        if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
          throw new Error("identity resolver returned an invalid node")
        }
        if (requestIsStale(request)) {
          return {
            ok: false,
            error: "Rename was not applied because the graph changed while identity resolution was running.",
          }
        }
        if (isSubmodel) {
          const resolvedFn = resolved[0].data?._functionName
          if (resolvedFn !== label) {
            return {
              ok: false,
              error: `Occurrence names must be identifiers; use "${resolvedFn ?? ""}".`,
            }
          }
          const isUsed = graphRef.current.nodes.some((other) => {
            if (other.id === nodeId) return false
            if (other.id === label) return true
            if (other.data?.label === label) return true
            const otherConfig = other.data?.config
            if (
              other.data?.nodeType === NODE_TYPES.SUBMODEL
              && isSubmodelInstanceConfig(otherConfig)
              && otherConfig.alias === label
            ) {
              return true
            }
            return false
          })
          if (isUsed) {
            return {
              ok: false,
              error: `"${label}" is already used by another node.`,
            }
          }
        }
        const prepared = prepare(nodeId, resolved[0].data, true)
        if (!prepared.ok) return prepared
        commit(prepared)
        return { ok: true }
      } catch (error: unknown) {
        return {
          ok: false,
          error: `Rename failed: ${error instanceof Error ? error.message : String(error)}`,
        }
      }
    })()
    registerPendingCommit(pending)
    return pending
  }, [beginRequest, commit, graphRef, prepare, readOnly, registerPendingCommit, requestIsStale, resolveNodeIdentities])

  return { onUpdateNode, onRenameNode, waitForPendingCommits }
}
