import { useEffect, useRef, useState } from "react"
import {
  computeNextNodeId,
  filterIncomingEdges,
  normalizeEdges,
  type RejectedIncomingEdge,
} from "../utils/graphHelpers"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"
import useDocumentStatusStore, {
  type RetainedPipelineCanvas,
} from "../stores/useDocumentStatusStore"
import {
  adaptPipelineEditorDocument,
  parsePipelineEditorDocument,
  type PipelineEditorDocument,
} from "../types/pipelineDocument"
import {
  bootstrapHauteSession,
  isHauteSessionExpiredReason,
  notifyHauteSessionExpired,
} from "../api/client"

export type WsStatus = "connected" | "reconnecting" | "disconnected"

interface WebSocketSyncParams {
  preambleRef: React.MutableRefObject<string>
  submodelsRef: React.MutableRefObject<Record<string, unknown>>
  sourceFileRef?: React.MutableRefObject<string>
  sourceRevisionRef: React.MutableRefObject<string>
  preservedBlocksRef: React.MutableRefObject<string[]>
  graphRefreshingRef: React.MutableRefObject<number>
  nodeIdCounter: React.MutableRefObject<number>
  fitView: (options?: { padding?: number }) => void
  enabled?: boolean
}

const MAX_RETRIES = 50

// After replacing nodes, React Flow fires onSelectionChange before the new
// nodes are committed. This guard window lets that spurious event pass.
const SELECTION_CHANGE_GUARD_MS = 150
const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const ABNORMAL_CLOSE = 1006
const DOCUMENT_FINGERPRINT_FIELD = "document_fingerprint"
const DOCUMENT_SCHEMA_VERSION = 1
const MAX_REJECTED_EDGE_WARNING_DETAILS = 3
const MAX_REJECTED_EDGE_WARNING_DETAIL_LENGTH = 120

function formatSyncError(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function formatRejectedEdgeWarning(rejectedEdges: RejectedIncomingEdge[]): string {
  const details = rejectedEdges
    .slice(0, MAX_REJECTED_EDGE_WARNING_DETAILS)
    .map(({ edge, reason }) => {
      const detail = `${edge.id} (${reason})`
      if (detail.length <= MAX_REJECTED_EDGE_WARNING_DETAIL_LENGTH) return detail
      return `${detail.slice(0, MAX_REJECTED_EDGE_WARNING_DETAIL_LENGTH - 3)}...`
    })
    .join(", ")
  const omitted = rejectedEdges.length - MAX_REJECTED_EDGE_WARNING_DETAILS
  const omittedSummary = omitted > 0 ? `; ${omitted} more omitted` : ""
  const edgeLabel = rejectedEdges.length === 1 ? "edge" : "edges"
  return `Retained ${rejectedEdges.length} unresolved synced ${edgeLabel} to prevent data loss. ${details}${omittedSummary}. Saving may fail until they are repaired.`
}

function normalizeSourceFile(value: unknown): string | null {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed) return null
  return trimmed
    .replace(/\\/g, "/")
    .replace(/\/+/g, "/")
    .replace(/^\.\//, "")
}

function isAbsoluteSourceFile(value: string): boolean {
  return value.startsWith("/") || /^[a-z]:\//i.test(value)
}

function hasDirectorySegment(value: string): boolean {
  return value.includes("/")
}

function isAbsoluteRelativeMatch(absoluteSource: string, relativeSource: string): boolean {
  if (!hasDirectorySegment(relativeSource)) return false
  return absoluteSource.endsWith(`/${relativeSource}`)
}

function isCurrentSourceFile(incoming: unknown, current: string | undefined): boolean {
  const incomingSource = normalizeSourceFile(incoming)
  const currentSource = normalizeSourceFile(current)
  if (!incomingSource || !currentSource) return incomingSource === currentSource
  if (incomingSource === currentSource) return true
  const incomingIsAbsolute = isAbsoluteSourceFile(incomingSource)
  const currentIsAbsolute = isAbsoluteSourceFile(currentSource)
  if (incomingIsAbsolute && !currentIsAbsolute) {
    return isAbsoluteRelativeMatch(incomingSource, currentSource)
  }
  if (currentIsAbsolute && !incomingIsAbsolute) {
    return isAbsoluteRelativeMatch(currentSource, incomingSource)
  }
  return false
}

function sourceFileLabel(value: unknown, fallback = "the current pipeline"): string {
  if (typeof value !== "string" || value.trim() === "") return fallback
  return value.replace(/\\/g, "/")
}

interface PipelineDocumentUpdateFrame {
  document: PipelineEditorDocument
  documentFingerprint: string
  sourceFile: string
}

function parsePipelineDocumentUpdateFrame(
  message: Record<string, unknown>,
): PipelineDocumentUpdateFrame {
  const expectedKeys = [
    "type",
    "schema_version",
    "document",
    "document_fingerprint",
    "source_file",
  ]
  const actualKeys = Object.keys(message).sort()
  if (
    actualKeys.length !== expectedKeys.length ||
    !expectedKeys.slice().sort().every((key, index) => key === actualKeys[index])
  ) {
    throw new Error("pipeline_document_update: unexpected frame fields")
  }
  if (message.schema_version !== DOCUMENT_SCHEMA_VERSION) {
    throw new Error("pipeline_document_update: unsupported schema_version")
  }
  const documentFingerprint =
    typeof message.document_fingerprint === "string"
      ? message.document_fingerprint.trim()
      : ""
  if (!documentFingerprint) {
    throw new Error("pipeline_document_update: missing document_fingerprint")
  }
  if (typeof message.source_file !== "string" || !message.source_file.trim()) {
    throw new Error("pipeline_document_update: missing source_file")
  }
  const document = parsePipelineEditorDocument(message.document)
  if (!document.source_revision) {
    throw new Error("pipeline_document_update: document is missing source_revision")
  }
  if (!isCurrentSourceFile(message.source_file, document.source_file)) {
    throw new Error("pipeline_document_update: envelope and document source_file differ")
  }
  return {
    document,
    documentFingerprint,
    sourceFile: message.source_file,
  }
}

function retainedCanvasFor(
  document: PipelineEditorDocument,
  dirty: boolean,
): RetainedPipelineCanvas | null {
  if (document.load_status !== "source_only") return null
  const current = useDocumentStatusStore.getState()
  if (current.loadStatus === "source_only") return current.retainedCanvas
  if (current.loadStatus !== "ready" && current.loadStatus !== "degraded") return null
  return {
    kind: dirty ? "local_dirty" : "last_renderable",
    sourceRevision: current.sourceRevision,
    loadStatus: current.loadStatus,
  }
}

export default function useWebSocketSync({
  preambleRef, submodelsRef, sourceFileRef, sourceRevisionRef, preservedBlocksRef,
  graphRefreshingRef, nodeIdCounter, fitView, enabled = true,
}: WebSocketSyncParams): WsStatus {
  const { setSyncBanner } = useUIStore()
  const { addToast } = useToastStore()
  const [status, setStatus] = useState<WsStatus>(() => enabled ? "reconnecting" : "disconnected")
  const retriesRef = useRef(0)
  const appliedDocumentFingerprintRef = useRef<{
    sourceFile: string
    fingerprint: string
  } | null>(null)

  useEffect(() => {
    if (!enabled) {
      retriesRef.current = 0
      setStatus("disconnected")
      return
    }

    setStatus("reconnecting")
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${protocol}//${window.location.host}/ws/sync`
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let mounted = true
    let graphUpdateSeq = 0
    let activeSelectionGuardIncrements = 0
    const delayedTimers = new Set<ReturnType<typeof setTimeout>>()

    function scheduleDelayed(callback: () => void, delayMs: number) {
      const timer = setTimeout(() => {
        delayedTimers.delete(timer)
        callback()
      }, delayMs)
      delayedTimers.add(timer)
    }

    function releaseSelectionGuard() {
      if (activeSelectionGuardIncrements > 0) {
        activeSelectionGuardIncrements -= 1
      }
      graphRefreshingRef.current = Math.max(0, graphRefreshingRef.current - 1)
    }

    function blockDirtyGraphUpdate(incomingSource: unknown): boolean {
      if (!useGraphStore.getState().dirty) {
        return false
      }
      const label = sourceFileLabel(incomingSource)
      const message = `Pipeline changed on disk (${label}) while you have unsaved changes. Reload the file or discard local edits before applying external changes.`
      setSyncBanner(message)
      addToast("warning", "Pipeline changed on disk while you have unsaved changes.")
      return true
    }

    function appliedDocumentFingerprintFor(sourceFile: string): string | undefined {
      const applied = appliedDocumentFingerprintRef.current
      if (!applied || !isCurrentSourceFile(applied.sourceFile, sourceFile)) {
        return undefined
      }
      return applied.fingerprint
    }

    function rememberAppliedDocumentFingerprint(
      incomingSource: unknown,
      fingerprint: string,
    ) {
      const sourceFile = normalizeSourceFile(incomingSource)
        ?? normalizeSourceFile(sourceFileRef?.current)
      appliedDocumentFingerprintRef.current = sourceFile
        ? { sourceFile, fingerprint }
        : null
    }

    function markSessionExpired(reason: string, notify = true) {
      retriesRef.current = MAX_RETRIES + 1
      setStatus("disconnected")
      if (notify) notifyHauteSessionExpired(reason)
    }

    function scheduleReconnect() {
      retriesRef.current += 1

      if (retriesRef.current > MAX_RETRIES) {
        setStatus("disconnected")
        return
      }

      setStatus("reconnecting")
      const backoff = Math.min(INITIAL_BACKOFF_MS * 2 ** (retriesRef.current - 1), MAX_BACKOFF_MS)
      reconnectTimer = setTimeout(connect, backoff)
    }

    async function probeSessionThenReconnect() {
      try {
        await bootstrapHauteSession(true)
      } catch {
        // A pre-accept close is also how a temporarily unavailable backend
        // appears. Keep the bounded reconnect loop alive and retry bootstrap.
      }
      if (!mounted) return
      scheduleReconnect()
    }

    function connect() {
      if (!mounted) return
      let opened = false
      try {
        ws = new WebSocket(wsUrl)
      } catch (err) {
        if (!mounted) return
        setStatus("disconnected")
        addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
        return
      }

      ws.onopen = () => {
        if (!mounted) return
        opened = true
        retriesRef.current = 0
        setStatus("connected")
        const sourceFile = sourceFileRef?.current.trim()
        if (sourceFile) {
          try {
            const resyncPayload: Record<string, string | number> = {
              type: "resync",
              source_file: sourceFile,
              document_schema_version: DOCUMENT_SCHEMA_VERSION,
            }
            const documentFingerprint = appliedDocumentFingerprintFor(sourceFile)
            if (documentFingerprint) {
              resyncPayload[DOCUMENT_FINGERPRINT_FIELD] = documentFingerprint
            }
            ws?.send(JSON.stringify(resyncPayload))
          } catch (err) {
            addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
          }
        }
      }

      ws.onmessage = async (event) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(event.data)
        } catch (err) {
          addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
          return
        }

        if (msg.type === "pipeline_document_update") {
          let frame: PipelineDocumentUpdateFrame
          try {
            frame = parsePipelineDocumentUpdateFrame(msg)
          } catch (err) {
            addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
            return
          }
          if (!isCurrentSourceFile(frame.sourceFile, sourceFileRef?.current)) {
            return
          }
          const updateSeq = ++graphUpdateSeq
          const graphState = useGraphStore.getState()
          const dirty = graphState.dirty
          const retainedCanvas = retainedCanvasFor(frame.document, dirty)

          // The document fence is authoritative independently of whether the
          // renderable graph can be replaced. Mirror its revision first so
          // request admission can never race a stale ready state.
          useDocumentStatusStore.getState().loadLiveDocumentStatus(
            frame.document,
            retainedCanvas,
            false,
          )
          sourceRevisionRef.current = frame.document.source_revision ?? ""
          rememberAppliedDocumentFingerprint(frame.sourceFile, frame.documentFingerprint)

          if (frame.document.load_status === "source_only") {
            if (dirty) {
              blockDirtyGraphUpdate(frame.sourceFile)
            } else {
              setSyncBanner(null)
            }
            return
          }

          if (blockDirtyGraphUpdate(frame.sourceFile)) {
            return
          }

          try {
            const adapted = adaptPipelineEditorDocument(frame.document)
            const newNodes = adapted.nodes
            const newEdges = normalizeEdges(adapted.edges)
            // Every validated document node carries a finite display position,
            // so external sync never generates layout and applies synchronously.
            const { rejectedEdges } = filterIncomingEdges(
              newNodes,
              newEdges,
              adapted.submodels,
            )
            const nodesToApply = newNodes

            const previousPreservedBlocks = preservedBlocksRef.current
            const previousPreamble = preambleRef.current
            const previousSubmodels = submodelsRef.current
            const nextPreamble = frame.document.preamble ?? ""

            graphRefreshingRef.current += 1
            activeSelectionGuardIncrements += 1
            try {
              preservedBlocksRef.current = [...frame.document.preserved_blocks]
              submodelsRef.current = adapted.submodels
              preambleRef.current = nextPreamble
              useGraphStore.getState().loadGraphSnapshot({
                nodes: nodesToApply,
                edges: newEdges,
                preamble: nextPreamble,
                submodels: adapted.submodels,
              })
              useDocumentStatusStore.getState().setGraphSynchronized(true)
              nodeIdCounter.current = computeNextNodeId(newNodes)
              setSyncBanner(null)
            } catch (err) {
              preservedBlocksRef.current = previousPreservedBlocks
              submodelsRef.current = previousSubmodels
              preambleRef.current = previousPreamble
              throw err
            } finally {
              scheduleDelayed(releaseSelectionGuard, SELECTION_CHANGE_GUARD_MS)
            }

            const newNodeIds = new Set<string>(newNodes.map((node) => node.id))
            const ui = useUIStore.getState()
            if (ui.renameDialog && !newNodeIds.has(ui.renameDialog.nodeId)) {
              ui.setRenameDialog(null)
            }
            if (
              ui.submodelDialog &&
              ui.submodelDialog.nodeIds.some((id) => !newNodeIds.has(id))
            ) {
              ui.setSubmodelDialog(null)
            }

            addToast(
              "info",
              frame.document.load_status === "degraded"
                ? "Pipeline updated in recovery mode"
                : "Pipeline updated from file",
            )
            if (rejectedEdges.length > 0) {
              addToast("warning", formatRejectedEdgeWarning(rejectedEdges))
            }
            scheduleDelayed(() => {
              if (mounted && updateSeq === graphUpdateSeq) fitView({ padding: 0.8 })
            }, 100)
          } catch (err) {
            if (!mounted || updateSeq !== graphUpdateSeq) return
            addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
          }
          return
        }

        if (msg.type === "parse_error") {
          if (!isCurrentSourceFile(msg.source_file, sourceFileRef?.current)) {
            return
          }
          // A parse_error frame now means one thing: the current document
          // could not be loaded or resynced at all. Authored errors arrive
          // as degraded/source-only documents, never through this frame.
          ++graphUpdateSeq
          appliedDocumentFingerprintRef.current = null
          useDocumentStatusStore.getState().setSystemFailure(
            String(msg.error || "Pipeline document could not be loaded."),
          )
          setSyncBanner(null)
        }
      }

      ws.onclose = (event) => {
        if (!mounted) return
        if (event.code === 1008 && isHauteSessionExpiredReason(event.reason)) {
          void bootstrapHauteSession(true)
            .then(() => {
              if (mounted) scheduleReconnect()
            })
            .catch(() => {
              if (mounted) markSessionExpired(event.reason)
            })
          return
        }
        if (!opened && event.code === ABNORMAL_CLOSE) {
          void probeSessionThenReconnect()
          return
        }
        scheduleReconnect()
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      mounted = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      for (const timer of delayedTimers) {
        clearTimeout(timer)
      }
      delayedTimers.clear()
      if (activeSelectionGuardIncrements > 0) {
        graphRefreshingRef.current = Math.max(
          0,
          graphRefreshingRef.current - activeSelectionGuardIncrements,
        )
        activeSelectionGuardIncrements = 0
      }
      ws?.close()
    }
  }, [
    enabled, preambleRef, submodelsRef, sourceFileRef, sourceRevisionRef,
    preservedBlocksRef, nodeIdCounter, fitView, setSyncBanner, addToast,
    graphRefreshingRef,
  ])

  return status
}
