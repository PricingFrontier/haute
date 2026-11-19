import useGraphStore, { canvasGraph } from "../stores/useGraphStore"
import { useCallback, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from "react"
import { commitOpenDrafts } from "../components/form/useCommittedDraft"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import { answer, type Part } from "../utils/answers"
import { EditorNameCollisionError } from "../utils/editorIdentities"
import type { Edge, Node } from "@xyflow/react"

import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import { isSubmodelInstanceConfig, type SubmodelInstanceConfig } from "../types/node"
import { NODE_TYPES, isRequestInputType } from "../utils/nodeTypes"
import { structuralFingerprint } from "../utils/structuralFingerprint"
import {
  prepareNodeUpdate,
  type PreparedNodeUpdate,
} from "../utils/nodeUpdatePlan"

type ToastType = "success" | "error" | "warning" | "info"

/**
 * A preview response or a sync frame may replace the graph while an identity
 * request is in flight. Retry authored-field changes within the same editable
 * document, but never carry an edit across a document or capability change.
 */
const MAX_IDENTITY_ATTEMPTS = 3

type GraphCommitRequest = {
  nodeId: string
  generation: number
  /** The node's own authored fields when the request began. */
  nodeKey: string
  /** The definition interfaces it is resolved against. */
  definitionsKey: string
  documentIdentity: string
  contextGeneration: number
}

/**
 * A request input's update accepted ahead of its identities; the object is its identity.
 * `basis` is the node's own authored fields when its first pending draft began: the
 * committed node every later draft of it builds on, until one commits.
 */
type PendingDraft = { data: Record<string, unknown>; basis: string }

/** The definition interfaces a node's identities are resolved against. */
const definitionsKey = (): string => structuralFingerprint({ submodels: useGraphStore.getState().submodels })

const NO_PENDING_DRAFTS: ReadonlyMap<string, Record<string, unknown>> = new Map()

const SUPERSEDED_UPDATE =
  "Node update was not applied because the document, editing capability, or a newer node edit superseded it."

const NODE_MOVED_UPDATE =
  "Node update was not applied because the node changed while it was resolving (an Undo, a Redo or a change made elsewhere)."

export type UseGraphCommitControllerOptions = {
  readDocumentIdentity: () => string
  readOnly: boolean
  reservedApiInputFrameLabels: ReadonlySet<string>
  resolveNodeIdentities: (candidateNodes: readonly Node[]) => Promise<Node[]>
  /** Resolves a renamed node against the whole document, throwing
   *  `EditorNameCollisionError` for a name the naming rule refuses. */
  resolveRenameIdentities: (candidateNodes: readonly Node[]) => Promise<Node[]>
  /** A key of everything a rename's verdict depends on (every name, the support
   *  code); a rename resolved against an older key is resolved again. */
  readNamingContextKey: () => string
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
  /** Commit every open panel draft, then wait for every pending commit: what every save waits on. */
  settleEdits: () => Promise<OnUpdateConfigResult>
  /** Each request input's pending draft, by node id, while its identities
   *  resolve: the data its editor builds the next update on. */
  pendingNodeData: ReadonlyMap<string, Record<string, unknown>>
}

/**
 * Owns request generations and the one selected-node graph commit boundary.
 * The pure planner computes candidates; this hook alone decides whether an
 * asynchronous candidate still owns the graph/document fence and may commit.
 */
/** What a commit request's fence reads: the graph and the document's status. */
function graphOrDocument(listener: () => void): () => void {
  const graph = useGraphStore.subscribe(listener)
  const document = useDocumentStatusStore.subscribe(listener)
  return () => {
    graph()
    document()
  }
}

export default function useGraphCommitController({
  
  
  readDocumentIdentity,
  readOnly,
  reservedApiInputFrameLabels,
  resolveNodeIdentities,
  resolveRenameIdentities,
  readNamingContextKey,
  commitGraph,
  setSelectedNode,
  addToast,
}: UseGraphCommitControllerOptions): GraphCommitController {
  const requestGenerationsRef = useRef(new Map<string, number>())
  const pendingCommitsRef = useRef(new Set<Promise<OnUpdateConfigResult>>())
  // The newest update of each request input, held until its identities resolve.
  const draftsRef = useRef(new Map<string, PendingDraft>())
  // Drafts the node moved under (an Undo, a Redo, a sync frame): their replies commit nothing.
  const movedRef = useRef(new WeakSet<PendingDraft>())
  const [pendingNodeData, setPendingNodeData] = useState(NO_PENDING_DRAFTS)
  const documentIdentity = readDocumentIdentity()
  const contextRef = useRef({ readOnly, readDocumentIdentity, documentIdentity, generation: 0 })
  useLayoutEffect(() => {
    const identity = readDocumentIdentity()
    const context = contextRef.current
    if (context.readOnly !== readOnly || context.documentIdentity !== identity) {
      context.generation += 1
      // A draft belongs to the document and capability it was made in: no
      // edit in the next one may build on it, so it goes before paint.
      if (draftsRef.current.size > 0) {
        draftsRef.current.clear()
        setPendingNodeData(NO_PENDING_DRAFTS)
      }
    }
    context.readOnly = readOnly
    context.readDocumentIdentity = readDocumentIdentity
    context.documentIdentity = identity
  }, [readOnly, readDocumentIdentity, documentIdentity])
  useLayoutEffect(() => () => { contextRef.current.generation += 1 }, [])

  const publishDrafts = useCallback((): void => {
    setPendingNodeData(draftsRef.current.size === 0
      ? NO_PENDING_DRAFTS
      : new Map([...draftsRef.current].map(([nodeId, draft]) => [nodeId, draft.data])))
  }, [])

  const retireDraft = useCallback((nodeId: string, draft: PendingDraft): void => {
    if (draftsRef.current.get(nodeId) !== draft) return
    draftsRef.current.delete(nodeId)
    publishDrafts()
  }, [publishDrafts])

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

  // The one barrier every save waits on, of the pipeline or of one node: each value still
  // being typed is committed, as leaving its field would, then every commit still resolving
  // lands or fails, so the save reads what the panels show.
  const settleEdits = useCallback((): Promise<OnUpdateConfigResult> => {
    commitOpenDrafts()
    return waitForPendingCommits()
  }, [waitForPendingCommits])

  const prepare = useCallback((
    nodeId: string,
    data: Record<string, unknown>,
    refreshSourceIdentity: boolean,
  ) => prepareNodeUpdate({
    nodeId,
    data,
    refreshSourceIdentity,
    readOnly: contextRef.current.readOnly,
    graph: canvasGraph(),
    submodels: useGraphStore.getState().submodels,
    reservedApiInputFrameLabels,
  }), [reservedApiInputFrameLabels])

  // What an identity resolution depends on: the node's own authored fields and
  // the definition interfaces. Positions, selection and preview metadata
  // (underscore-prefixed data) change constantly and must not invalidate it.
  const nodeKey = useCallback((nodeId: string): string => {
    const node = canvasGraph().nodes.find((candidate) => candidate.id === nodeId)
    const data = (node?.data ?? {}) as Record<string, unknown>
    return JSON.stringify({
      label: data.label ?? null,
      nodeType: data.nodeType ?? null,
      config: data.config ?? null,
    })
  }, [])

  const beginRequest = useCallback((nodeId: string): GraphCommitRequest => {
    const generation = (requestGenerationsRef.current.get(nodeId) ?? 0) + 1
    requestGenerationsRef.current.set(nodeId, generation)
    return {
      nodeId,
      generation,
      nodeKey: nodeKey(nodeId),
      definitionsKey: definitionsKey(),
      documentIdentity: contextRef.current.readDocumentIdentity(),
      contextGeneration: contextRef.current.generation,
    }
  }, [nodeKey])

  const contextInvalidated = useCallback((request: GraphCommitRequest): boolean => (
    contextRef.current.readOnly
    || contextRef.current.generation !== request.contextGeneration
    || contextRef.current.readDocumentIdentity() !== request.documentIdentity
  ), [])

  const requestInvalidated = useCallback((request: GraphCommitRequest): boolean => (
    requestGenerationsRef.current.get(request.nodeId) !== request.generation
    || contextInvalidated(request)
  ), [contextInvalidated])

  const requestIsStale = useCallback((request: GraphCommitRequest): boolean => (
    requestInvalidated(request)
    || nodeKey(request.nodeId) !== request.nodeKey
    || definitionsKey() !== request.definitionsKey
  ), [nodeKey, requestInvalidated])

  const commit = useCallback((prepared: PreparedNodeUpdate): void => {
    // Keep request-facing refs coherent immediately; the store subscription
    // effects will observe the same identities after React commits.
    commitGraph(prepared.nodes, prepared.edges, prepared.submodels)
    setSelectedNode((previous) => (
      previous?.id === prepared.nodeId
        ? { ...previous, data: prepared.data }
        : previous
    ))
    if (prepared.removed.length === 0) return
    const label = String(prepared.data.label ?? prepared.nodeId)
    const one = prepared.removed.length === 1
    addToast(
      "warning",
      `Disconnected ${prepared.removed.length} edge${one ? "" : "s"} from ${label}: the source frame${one ? " no longer exists" : "s no longer exist"} after your edit.`,
    )
  }, [addToast, commitGraph, setSelectedNode])

  /**
   * Resolves a request input's draft and commits it while it is the node's
   * newest. A reply for a draft a newer update replaced, success or failure,
   * changes nothing: the newer draft carries its edits.
   */
  const resolveDraft = useCallback(async (
    nodeId: string,
    draft: PendingDraft,
    firstRequest: GraphCommitRequest,
  ): Promise<OnUpdateConfigResult> => {
    /** Why a reply for this draft is wanted no more, or null while it is. */
    const supersededBy = (request: GraphCommitRequest): "moved" | "replaced" | "superseded" | null => {
      if (movedRef.current.has(draft)) return "moved"
      if (contextInvalidated(request)) return "superseded"
      if (draftsRef.current.get(nodeId) !== draft) return "replaced"
      // A rename of the node, issued after this request.
      if (requestInvalidated(request)) return "superseded"
      return null
    }
    /** A reply the draft's request was superseded under, settled: a newer draft carries its edits. */
    const settleSuperseded = (request: GraphCommitRequest): OnUpdateConfigResult => {
      const why = supersededBy(request)
      if (why === "moved") return { ok: false, error: NODE_MOVED_UPDATE }
      if (why === "replaced") return { ok: true }
      retireDraft(nodeId, draft)
      return { ok: false, error: SUPERSEDED_UPDATE }
    }
    const failedUpdate = (error: unknown): OnUpdateConfigResult => {
      retireDraft(nodeId, draft)
      return { ok: false, error: `Update node failed: ${error instanceof Error ? error.message : String(error)}` }
    }
    const attempt = async (n: number, request: GraphCommitRequest): Promise<OnUpdateConfigResult> => {
      const liveNode = canvasGraph().nodes.find((node) => node.id === nodeId)
      if (!liveNode) {
        retireDraft(nodeId, draft)
        return { ok: false, error: `Cannot update missing node "${nodeId}".` }
      }
      let result: OnUpdateConfigResult = { ok: true }
      // The reply belongs to the draft as the node's newest, unmoved, in the context it was asked in.
      const wanted: Part = { holds: () => supersededBy(request) === null, subscribe: graphOrDocument }
      await answer([wanted], resolveNodeIdentities([{ ...liveNode, data: draft.data }]), {
        // What using the answer throws is the update's failure (`failed`).
        ok: async (resolved) => {
          if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
            throw new Error("identity resolver returned an invalid node")
          }
          // New definitions it is resolved against resolve it again; a move of the node
          // itself has retired the draft already.
          if (requestIsStale(request)) {
            if (n < MAX_IDENTITY_ATTEMPTS) {
              result = await attempt(n + 1, beginRequest(nodeId))
              return
            }
            retireDraft(nodeId, draft)
            result = {
              ok: false,
              error: "Node update was not applied because the graph kept changing while identity resolution was running.",
            }
            return
          }
          const finalPlan = prepare(nodeId, resolved[0].data, true)
          // Retired first, so its own commit is not taken for the node moving under it.
          retireDraft(nodeId, draft)
          if (!finalPlan.ok) {
            result = finalPlan
            return
          }
          commit(finalPlan)
        },
        failed: (error) => {
          result = failedUpdate(error)
        },
        stale: () => {
          result = settleSuperseded(request)
        },
      })
      return result
    }
    return attempt(1, firstRequest)
  }, [beginRequest, commit, contextInvalidated, prepare, requestInvalidated, requestIsStale, resolveNodeIdentities, retireDraft])

  // A draft is a whole node built on the node as it was: the moment an Undo, a Redo or a
  // sync frame moves the node's own fields under it, it lets go, as every panel draft lets
  // go when its value moves, so no later edit builds on it and its reply commits nothing.
  useLayoutEffect(() => useGraphStore.subscribe(() => {
    let moved = false
    for (const [nodeId, draft] of draftsRef.current) {
      if (nodeKey(nodeId) === draft.basis) continue
      movedRef.current.add(draft)
      draftsRef.current.delete(nodeId)
      moved = true
    }
    if (moved) publishDrafts()
  }), [nodeKey, publishDrafts])

  const onUpdateNode = useCallback((
    nodeId: string,
    data: Record<string, unknown>,
  ): OnUpdateConfigResult => {
    const currentNode = canvasGraph().nodes.find((node) => node.id === nodeId)
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
    if (!isRequestInputType(data.nodeType)) {
      commit(preflight)
      return { ok: true }
    }

    // The editor builds its next update on this draft, so an edit made before
    // the reply arrives carries this one.
    const draft: PendingDraft = { data, basis: draftsRef.current.get(nodeId)?.basis ?? nodeKey(nodeId) }
    draftsRef.current.set(nodeId, draft)
    publishDrafts()
    const pending = resolveDraft(nodeId, draft, request)
    registerPendingCommit(pending)
    void pending.then((result) => {
      if (!result.ok) addToast("error", result.error)
    })
    return { ok: true }
  }, [addToast, beginRequest, commit, nodeKey, prepare, publishDrafts, registerPendingCommit, resolveDraft])

  const onRenameNode = useCallback((
    nodeId: string,
    label: string,
  ): Promise<OnUpdateConfigResult> => {
    if (contextRef.current.readOnly) return Promise.resolve({ ok: false, error: "This pipeline document is read-only." })
    const initialNode = canvasGraph().nodes.find((node) => node.id === nodeId)
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
    const superseded: OnUpdateConfigResult = {
      ok: false,
      error: "Rename was not applied because the document, editing capability, or a newer node edit superseded it.",
    }
    // The server's own wording names the other node, helper or reserved name.
    const failedRename = (error: unknown): OnUpdateConfigResult => error instanceof EditorNameCollisionError
      ? { ok: false, error: error.message }
      : { ok: false, error: `Rename failed: ${error instanceof Error ? error.message : String(error)}` }
    const attempt = async (n: number): Promise<OnUpdateConfigResult> => {
      if (n > MAX_IDENTITY_ATTEMPTS) {
        return {
          ok: false,
          error: "Rename was not applied because the graph kept changing while identity resolution was running.",
        }
      }
      const currentNode = canvasGraph().nodes.find((node) => node.id === nodeId)
      if (!currentNode) return { ok: false, error: `Cannot rename missing node "${nodeId}".` }
      const isSubmodel = isOccurrence(currentNode)
      const request = beginRequest(nodeId)
      const namingKey = readNamingContextKey()
      let result: OnUpdateConfigResult = superseded
      // A newer edit or another document supersedes a refusal as it would an acceptance.
      const wanted: Part = { holds: () => !requestInvalidated(request), subscribe: graphOrDocument }
      await answer([wanted], resolveRenameIdentities([candidateFor(currentNode)]), {
        ok: async (resolved) => {
          try {
            if (resolved.length !== 1 || resolved[0]?.id !== nodeId) {
              throw new Error("identity resolver returned an invalid node")
            }
            // Another node's name or the support code changed while the server judged this
            // one: its verdict no longer holds.
            if (requestIsStale(request) || readNamingContextKey() !== namingKey) {
              result = await attempt(n + 1)
              return
            }
            if (isSubmodel) {
              const resolvedFn = resolved[0].data?._functionName
              if (resolvedFn !== label) {
                result = { ok: false, error: `Occurrence names must be identifiers; use "${resolvedFn ?? ""}".` }
                return
              }
            }
            const prepared = prepare(nodeId, resolved[0].data, true)
            if (!prepared.ok) {
              result = prepared
              return
            }
            commit(prepared)
            result = { ok: true }
          } catch (error: unknown) {
            result = failedRename(error)
          }
        },
        failed: async (error) => {
          // A refusal judged against names that have since changed may no longer hold: the
          // other node may have been renamed away.
          if (error instanceof EditorNameCollisionError && readNamingContextKey() !== namingKey) {
            result = await attempt(n + 1)
            return
          }
          result = failedRename(error)
        },
        stale: () => {
          result = superseded
        },
      })
      return result
    }
    const pending = attempt(1)
    registerPendingCommit(pending)
    return pending
  }, [beginRequest, commit, prepare, readNamingContextKey, registerPendingCommit, requestIsStale, requestInvalidated, resolveRenameIdentities])

  return { onUpdateNode, onRenameNode, waitForPendingCommits, settleEdits, pendingNodeData }
}
