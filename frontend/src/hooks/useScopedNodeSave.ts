import { useCallback, useEffect, useRef, useState } from "react"
import type { Edge, Node } from "@xyflow/react"
import { ApiError, saveNodeScoped } from "../api/client"
import useDocumentStatusStore from "../stores/useDocumentStatusStore"
import type { OnUpdateConfigResult } from "../panels/editors"
import type { HauteNodeData } from "../types/node"
import type { PipelineEditorDocument } from "../types/pipelineDocument"
import { otherScopedEditedNodes, scopedSaveResponseFenceError } from "../utils/scopedSaveGuards"

export interface ScopedSaveResult {
  ok: boolean
  error?: string
}

type UpdateNode = (id: string, data: Record<string, unknown>) => OnUpdateConfigResult

interface UseScopedNodeSaveArgs {
  scopedEditingActive: boolean
  selectedNodeId: string | null
  graphRef: React.RefObject<{ nodes: Node[]; edges: Edge[] }>
  onUpdateNode: UpdateNode
  /** Adopt the authoritative response; owns selection retention. */
  applyDocument: (document: PipelineEditorDocument, savedNodeId: string) => void
}

/**
 * App-lifetime coordination for the node-scoped save. The guarantees Codex
 * review demanded live here, outliving any panel mount:
 *
 * - scoped edits are accepted for one node at a time — starting to edit a
 *   second node is refused while the first holds unsaved scoped edits, so the
 *   two-edited-node deadlock state is unreachable;
 * - while a save is in flight, every scoped edit (any node, any panel
 *   instance) is refused, so a delayed response can never adopt over work
 *   entered after submission;
 * - a late response is discarded when the on-screen document identity moved
 *   after submission.
 */
export function useScopedNodeSave({
  scopedEditingActive,
  selectedNodeId,
  graphRef,
  onUpdateNode,
  applyDocument,
}: UseScopedNodeSaveArgs) {
  const editedNodeIdsRef = useRef<Set<string>>(new Set())
  const inFlightRef = useRef(false)
  const [scopedSaveInFlight, setScopedSaveInFlight] = useState(false)
  const scopedEditingActiveRef = useRef(scopedEditingActive)
  useEffect(() => {
    scopedEditingActiveRef.current = scopedEditingActive
  }, [scopedEditingActive])
  const documentSourceFile = useDocumentStatusStore((s) => s.sourceFile)
  const documentSourceRevision = useDocumentStatusStore((s) => s.sourceRevision)
  useEffect(() => {
    editedNodeIdsRef.current.clear()
  }, [documentSourceFile, documentSourceRevision])

  const nodeLabel = useCallback(
    (id: string): string => {
      const node = graphRef.current?.nodes.find((item) => item.id === id)
      return String((node?.data as HauteNodeData | undefined)?.label ?? id)
    },
    [graphRef],
  )

  const handlePanelUpdateNode = useCallback<UpdateNode>(
    (id, data) => {
      if (scopedEditingActiveRef.current) {
        if (inFlightRef.current) {
          return {
            ok: false,
            error: "A node save is in flight; wait for it to finish before editing.",
          }
        }
        const others = otherScopedEditedNodes(editedNodeIdsRef.current, id)
        if (others.length > 0) {
          return {
            ok: false,
            error:
              `Save ${nodeLabel(others[0])} or reload the pipeline before editing ` +
              "another node.",
          }
        }
        editedNodeIdsRef.current.add(id)
      }
      return onUpdateNode(id, data)
    },
    [nodeLabel, onUpdateNode],
  )

  const handleScopedSave = useCallback(async (): Promise<ScopedSaveResult> => {
    if (inFlightRef.current) {
      return { ok: false, error: "A node save is already in flight." }
    }
    const target = graphRef.current?.nodes.find((item) => item.id === selectedNodeId)
    const data = target?.data as HauteNodeData | undefined
    const state = useDocumentStatusStore.getState()
    if (
      !target ||
      typeof data?._sourceFile !== "string" ||
      typeof data?._recoveryId !== "string" ||
      !state.sourceRevision
    ) {
      return { ok: false, error: "This node cannot be saved in isolation right now." }
    }
    const others = otherScopedEditedNodes(editedNodeIdsRef.current, target.id)
    if (others.length > 0) {
      return { ok: false, error: `Save the other edited node first: ${nodeLabel(others[0])}.` }
    }
    const requestSourceFile = state.sourceFile
    const requestRevision = state.sourceRevision
    inFlightRef.current = true
    setScopedSaveInFlight(true)
    try {
      const document = await saveNodeScoped({
        sourceFile: requestSourceFile,
        sourceRevision: requestRevision,
        targetSourceFile: data._sourceFile,
        targetRecoveryId: data._recoveryId,
        config: (data.config ?? {}) as Record<string, unknown>,
      })
      const current = useDocumentStatusStore.getState()
      const fenceError = scopedSaveResponseFenceError({
        requestSourceFile,
        requestRevision,
        currentSourceFile: current.sourceFile,
        currentRevision: current.sourceRevision,
      })
      if (fenceError) return { ok: false, error: fenceError }
      editedNodeIdsRef.current.delete(target.id)
      applyDocument(document, target.id)
      return { ok: true }
    } catch (error) {
      const detail = error instanceof ApiError ? (error.detail ?? error.message) : String(error)
      return { ok: false, error: detail }
    } finally {
      inFlightRef.current = false
      setScopedSaveInFlight(false)
    }
  }, [applyDocument, graphRef, nodeLabel, selectedNodeId])

  return { handlePanelUpdateNode, handleScopedSave, scopedSaveInFlight }
}
