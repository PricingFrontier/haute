import useGraphStore, { canvasGraph } from "../stores/useGraphStore"
import { savedFormsFor } from "../utils/documentRequest"
import { useCallback, useEffect, useRef, useState } from "react"
import { saveNodeScoped } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import useDocumentStatusStore, { documentPart } from "../stores/useDocumentStatusStore"
import { answer, watch } from "../utils/answers"
import type { OnUpdateConfigResult } from "../panels/editors/_shared"
import type { HauteNodeData } from "../types/node"
import type { PipelineEditorDocument } from "../types/pipelineDocument"
import { otherScopedEditedNodes, STALE_SCOPED_SAVE } from "../utils/scopedSaveGuards"

export interface ScopedSaveResult {
  ok: boolean
  error?: string
}

type UpdateNode = (id: string, data: Record<string, unknown>) => OnUpdateConfigResult

interface UseScopedNodeSaveArgs {
  scopedEditingActive: boolean
  selectedNodeId: string | null
  onUpdateNode: UpdateNode
  /** The barrier every save waits on: open drafts committed, pending commits landed. */
  settleEdits: () => Promise<OnUpdateConfigResult>
  /** Adopt the authoritative response and the fingerprint it names; owns selection retention. */
  applyDocument: (document: PipelineEditorDocument, documentFingerprint: string, savedNodeId: string) => void
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
 *   after submission;
 * - the save first settles the open edits through the barrier the pipeline's
 *   save uses, so it sends the node as the panel shows it, however long a
 *   change still resolving takes.
 */
export function useScopedNodeSave({
  scopedEditingActive,
  selectedNodeId,
  onUpdateNode,
  settleEdits,
  applyDocument,
}: UseScopedNodeSaveArgs) {
  const editedNodeIdsRef = useRef<Set<string>>(new Set())
  const inFlightRef = useRef(false)
  const [scopedSaveInFlight, setScopedSaveInFlight] = useState(false)
  const scopedEditingActiveRef = useRef(scopedEditingActive)
  useEffect(() => {
    scopedEditingActiveRef.current = scopedEditingActive
  }, [scopedEditingActive])
  // executionGeneration advances on EVERY authoritative document load —
  // including re-adopting an unchanged revision after discarding local edits
  // — so "reload the pipeline" always releases the tracking.
  const documentGeneration = useDocumentStatusStore((s) => s.executionGeneration)
  useEffect(() => {
    editedNodeIdsRef.current.clear()
  }, [documentGeneration])

  const nodeLabel = useCallback(
    (id: string): string => {
      const node = canvasGraph()?.nodes.find((item) => item.id === id)
      return String((node?.data as HauteNodeData | undefined)?.label ?? id)
    },
    [],
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
        const result = onUpdateNode(id, data)
        // A refused update edited nothing, so it locks no other node.
        if (result.ok) editedNodeIdsRef.current.add(id)
        return result
      }
      return onUpdateNode(id, data)
    },
    [nodeLabel, onUpdateNode],
  )

  const handleScopedSave = useCallback(async (): Promise<ScopedSaveResult> => {
    if (inFlightRef.current) {
      return { ok: false, error: "A node save is already in flight." }
    }
    let result: ScopedSaveResult = { ok: false, error: STALE_SCOPED_SAVE }
    const refused = (error: unknown) => {
      result = { ok: false, error: apiErrorMessage(error) }
    }
    // What the panel shows is saved: the barrier commits each value still being typed at
    // once, and the save is in flight from then on, so no later edit joins it; a change
    // still resolving lands (or fails, and nothing is saved) before the node is read.
    await answer([documentPart()], settleEdits(), {
      ok: async (settled) => {
        if (!settled.ok) {
          result = { ok: false, error: settled.error }
          return
        }
        const target = canvasGraph()?.nodes.find((item) => item.id === selectedNodeId)
        const data = target?.data as HauteNodeData | undefined
        const state = useDocumentStatusStore.getState()
        if (
          !target ||
          typeof data?._sourceFile !== "string" ||
          typeof data?._recoveryId !== "string" ||
          !state.sourceRevision
        ) {
          result = { ok: false, error: "This node cannot be saved in isolation right now." }
          return
        }
        const others = otherScopedEditedNodes(editedNodeIdsRef.current, target.id)
        if (others.length > 0) {
          result = { ok: false, error: `Save the other edited node first: ${nodeLabel(others[0])}.` }
          return
        }
        // A part the node holds as saved is sent with its saved form, and read from that
        // (CGEN-11, CGEN-13): steps as saved by the code saved for them, settings as saved by
        // their saved text.
        const config = (data.config ?? {}) as Record<string, unknown>
        const graph = useGraphStore.getState()
        const saved = savedFormsFor(graph.lastSavedSnapshot, graph.focus?.definitionId ?? null, target)
        // A response is adopted only while the document it was computed against, its file and
        // revision, is still the one on screen.
        const sentAgainst = watch(useDocumentStatusStore, (current) => JSON.stringify([current.sourceFile, current.sourceRevision]))
        await answer([sentAgainst], saveNodeScoped({
          sourceFile: state.sourceFile,
          sourceRevision: state.sourceRevision,
          targetSourceFile: data._sourceFile,
          targetRecoveryId: data._recoveryId,
          config: saved.saved_code === undefined ? config : { ...config, code: saved.saved_code },
          savedCode: saved.saved_code ?? null,
          savedSettings: saved.saved_settings ?? null,
        }), {
          ok: ({ value: document, documentFingerprint }) => {
            editedNodeIdsRef.current.delete(target.id)
            applyDocument(document, documentFingerprint, target.id)
            result = { ok: true }
          },
          failed: refused,
        })
      },
      failed: refused,
    }, {
      inFlight: (flying) => {
        inFlightRef.current = flying
        setScopedSaveInFlight(flying)
      },
    })
    return result
  }, [applyDocument, nodeLabel, selectedNodeId, settleEdits])

  return { handlePanelUpdateNode, handleScopedSave, scopedSaveInFlight }
}
