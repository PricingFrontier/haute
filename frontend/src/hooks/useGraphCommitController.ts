import { useCallback, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from "react"
import type { Edge, Node } from "@xyflow/react"

import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import { isSubmodelInstanceConfig, type SubmodelInstanceConfig } from "../types/node"
import { NODE_TYPES } from "../utils/nodeTypes"
import { structuralFingerprint } from "../utils/structuralFingerprint"
import {
  prepareNodeUpdate,
  type PreparedNodeUpdate,
} from "../utils/nodeUpdatePlan"

type GraphSnapshot = { nodes: Node[]; edges: Edge[] }
type ToastType = "success" | "error" | "warning" | "info"

/**
 * A preview response or a sync frame may replace the graph while an identity
 * request is in flight. That is not a reason to drop the user's edit: the
 * controller resolves again against the live graph, up to this many times,
 * and refuses only a request superseded by a newer edit of the same node.
 */
const MAX_IDENTITY_ATTEMPTS = 3

type GraphCommitRequest = {
  nodeId: string
  generation: number
  editKey: string
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

  // What an identity resolution depends on: the node's own authored fields and
  // the definition interfaces. Positions, selection and preview metadata
  // (underscore-prefixed data) change constantly and must not invalidate it.
  const editKey = useCallback((nodeId: string): string => {
    const node = graphRef.current.nodes.find((candidate) => candidate.id === nodeId)
    const data = (node?.data ?? {}) as Record<string, unknown>
    return JSON.stringify({
      label: data.label ?? null,
      nodeType: data.nodeType ?? null,
      config: data.config ?? null,
      submodels: structuralFingerprint({ submodels: submodelsRef.current }),
    })
  }, [graphRef, submodelsRef])

  const beginRequest = useCallback((nodeId: string): GraphCommitRequest => {
    const generation = (requestGenerationsRef.current.get(nodeId) ?? 0) + 1
    requestGenerationsRef.current.set(nodeId, generation)
    return {
      nodeId,
      generation,
      editKey: editKey(nodeId),
      documentIdentity: readDocumentIdentity(),
    }
  }, [editKey, readDocumentIdentity])

  const requestSuperseded = useCallback((request: GraphCommitRequest): boolean => (
    requestGenerationsRef.current.get(request.nodeId) !== request.generation
  ), [])

  const requestIsStale = useCallback((request: GraphCommitRequest): boolean => (
    requestSuperseded(request)
    || editKey(request.nodeId) !== request.editKey
    || readDocumentIdentity() !== request.documentIdentity
  ), [editKey, readDocumentIdentity, requestSuperseded])

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

    const pending = (async (): Promise<OnUpdateConfigResult> => {
      try {
        for (let attempt = 1; attempt <= MAX_IDENTITY_ATTEMPTS; attempt += 1) {
          const liveNode = graphRef.current.nodes.find((node) => node.id === nodeId)
          if (!liveNode) return { ok: false, error: `Cannot update missing node "${nodeId}".` }
          const attemptRequest = attempt === 1 ? request : beginRequest(nodeId)
          const resolved = await resolveNodeIdentities([{ ...liveNode, data }])
          if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
            throw new Error("identity resolver returned an invalid node")
          }
          if (requestSuperseded(attemptRequest)) {
            return {
              ok: false,
              error: "Node update was not applied because a newer edit of this node superseded it.",
            }
          }
          if (requestIsStale(attemptRequest)) continue
          const finalPlan = prepare(nodeId, resolved[0].data, true)
          if (!finalPlan.ok) return finalPlan
          commit(finalPlan)
          return { ok: true }
        }
        return {
          ok: false,
          error: "Node update was not applied because the graph kept changing while identity resolution was running.",
        }
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
  }, [addToast, beginRequest, commit, graphRef, prepare, registerPendingCommit, requestIsStale, requestSuperseded, resolveNodeIdentities])

  const onRenameNode = useCallback((
    nodeId: string,
    label: string,
  ): Promise<OnUpdateConfigResult> => {
    if (readOnly) return Promise.resolve({ ok: false, error: "This pipeline document is read-only." })
    const initialNode = graphRef.current.nodes.find((node) => node.id === nodeId)
    if (!initialNode) return Promise.resolve({ ok: false, error: `Cannot rename missing node "${nodeId}".` })
    if (initialNode.data.label === label) return Promise.resolve({ ok: true })
    const isOccurrence = (node: Node): boolean => (
      node.data?.nodeType === NODE_TYPES.SUBMODEL && isSubmodelInstanceConfig(node.data?.config)
    )
    // An occurrence's name is its alias, so the candidate carries the new alias into
    // the identity request and the returned handle identities carry the new names.
    const candidateFor = (currentNode: Node): Node => {
      const candidateData: Record<string, unknown> = isOccurrence(currentNode)
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
      return { ...currentNode, data: candidateData }
    }
    const pending = (async (): Promise<OnUpdateConfigResult> => {
      try {
        for (let attempt = 1; attempt <= MAX_IDENTITY_ATTEMPTS; attempt += 1) {
          const currentNode = graphRef.current.nodes.find((node) => node.id === nodeId)
          if (!currentNode) return { ok: false, error: `Cannot rename missing node "${nodeId}".` }
          const isSubmodel = isOccurrence(currentNode)
          const request = beginRequest(nodeId)
          const resolved = await resolveNodeIdentities([candidateFor(currentNode)])
          if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
            throw new Error("identity resolver returned an invalid node")
          }
          if (requestSuperseded(request)) {
            return {
              ok: false,
              error: "Rename was not applied because a newer edit of this node superseded it.",
            }
          }
          if (requestIsStale(request)) continue
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
        }
        return {
          ok: false,
          error: "Rename was not applied because the graph kept changing while identity resolution was running.",
        }
      } catch (error: unknown) {
        return {
          ok: false,
          error: `Rename failed: ${error instanceof Error ? error.message : String(error)}`,
        }
      }
    })()
    registerPendingCommit(pending)
    return pending
  }, [beginRequest, commit, graphRef, prepare, readOnly, registerPendingCommit, requestIsStale, requestSuperseded, resolveNodeIdentities])

  return { onUpdateNode, onRenameNode, waitForPendingCommits }
}
