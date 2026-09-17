import { create } from "zustand"

import type {
  PipelineDiagnostic,
  PipelineDocumentCapabilities,
  PipelineEditorDocument,
  PipelineLoadStatus,
  PipelineNodeCompleteness,
} from "../types/pipelineDocument"

interface DocumentStatusState {
  loadStatus: PipelineLoadStatus | null
  capabilities: PipelineDocumentCapabilities | null
  diagnostics: PipelineDiagnostic[]
  diagnosticsOmitted: number
  completeness: PipelineNodeCompleteness[]
  completenessOmitted: number
  sourceRevision: string | null
  executionGeneration: number
  sourceText: string
  sourceFile: string
  sources: string[]
  activeSource: string | null
  sourceSelectionTrusted: boolean
  hasAuthoredContent: boolean
  retainedCanvas: RetainedPipelineCanvas | null
  graphSynchronized: boolean
  systemFailure: string | null
  /** Server fingerprint of the accepted document, or null when the accepting response named none. */
  documentFingerprint: string | null
}

export interface RetainedPipelineCanvas {
  kind: "last_renderable" | "local_dirty"
  sourceRevision: string | null
  loadStatus: Exclude<PipelineLoadStatus, "source_only">
}

/**
 * Identity captured by an execution request. Responses are publishable only
 * while this authoritative document fence is still current.
 */
export interface DocumentExecutionFence {
  sourceFile: string
  executionGeneration: number
  loadStatus: PipelineLoadStatus | null
  canExecute: boolean
}

export interface DocumentStatusStore extends DocumentStatusState {
  loadDocumentStatus: (
    document: PipelineEditorDocument,
    graphSynchronized?: boolean,
    documentFingerprint?: string | null,
  ) => void
  loadLiveDocumentStatus: (
    document: PipelineEditorDocument,
    retainedCanvas: RetainedPipelineCanvas | null,
    graphSynchronized: boolean,
    documentFingerprint: string,
  ) => void
  setGraphSynchronized: (graphSynchronized: boolean) => void
  setSystemFailure: (systemFailure: string) => void
  setSourceRevision: (sourceRevision: string | null) => void
  acknowledgeSave: (sourceRevision: string) => void
  reset: () => void
}

function initialState(): DocumentStatusState {
  return {
    loadStatus: null,
    capabilities: null,
    diagnostics: [],
    diagnosticsOmitted: 0,
    completeness: [],
    completenessOmitted: 0,
    sourceRevision: null,
    executionGeneration: 0,
    sourceText: "",
    sourceFile: "",
    sources: [],
    activeSource: null,
    sourceSelectionTrusted: false,
    hasAuthoredContent: false,
    retainedCanvas: null,
    graphSynchronized: false,
    systemFailure: null,
    documentFingerprint: null,
  }
}

function documentState(
  document: PipelineEditorDocument,
  retainedCanvas: RetainedPipelineCanvas | null,
  graphSynchronized: boolean,
  executionGeneration: number,
  documentFingerprint: string | null,
): DocumentStatusState {
  return {
    loadStatus: document.load_status,
    capabilities: { ...document.capabilities },
    diagnostics: document.diagnostics.map((diagnostic) => ({
      ...diagnostic,
      source_span: diagnostic.source_span ? { ...diagnostic.source_span } : null,
    })),
    diagnosticsOmitted: document.diagnostics_omitted,
    completeness: document.completeness.map((entry) => ({ ...entry })),
    completenessOmitted: document.completeness_omitted,
    sourceRevision: document.source_revision,
    executionGeneration,
    sourceText: document.source_text,
    sourceFile: document.source_file,
    sources: [...document.sources],
    activeSource: document.active_source,
    sourceSelectionTrusted: document.source_selection_trusted,
    hasAuthoredContent: document.has_authored_content,
    retainedCanvas,
    graphSynchronized,
    systemFailure: null,
    documentFingerprint,
  }
}

const useDocumentStatusStore = create<DocumentStatusStore>()((set) => ({
  ...initialState(),
  loadDocumentStatus: (document, graphSynchronized = true, documentFingerprint = null) =>
    set((state) => documentState(
      document, null, graphSynchronized, state.executionGeneration + 1, documentFingerprint,
    )),
  loadLiveDocumentStatus: (document, retainedCanvas, graphSynchronized, documentFingerprint) =>
    set((state) => documentState(
      document, retainedCanvas, graphSynchronized, state.executionGeneration + 1, documentFingerprint,
    )),
  setGraphSynchronized: (graphSynchronized) => set({ graphSynchronized }),
  // No document was accepted, so the next resync must ask for the current one.
  setSystemFailure: (systemFailure) => set({ systemFailure, graphSynchronized: false, documentFingerprint: null }),
  setSourceRevision: (sourceRevision) => set((state) => ({
    sourceRevision,
    executionGeneration: state.executionGeneration + Number(sourceRevision !== state.sourceRevision),
  })),
  // Persisting this canvas doesn't replace the document that owns running jobs.
  // Keep their original config/version stamps so edited results still read stale.
  acknowledgeSave: (sourceRevision) => set({ sourceRevision }),
  reset: () => set((state) => ({ ...initialState(), executionGeneration: state.executionGeneration + 1 })),
}))

/**
 * User-facing reason the current document cannot be edited or saved right
 * now. Distinguishes unresolved load diagnostics from an out-of-sync canvas
 * so fences never blame "diagnostics" for a pending external change.
 */
export function documentReadOnlyReason(): string {
  const state = useDocumentStatusStore.getState()
  if (state.systemFailure !== null) {
    return "The pipeline document could not be loaded. Resolve the failure and reload before editing."
  }
  if (state.capabilities?.can_mutate === true && !state.graphSynchronized) {
    return "Pipeline changed on disk while you have unsaved changes. Reload the file or discard local edits first."
  }
  return "This pipeline is read-only until its load diagnostics are resolved."
}

function executionFence(state: DocumentStatusState): DocumentExecutionFence {
  return {
    sourceFile: state.sourceFile,
    executionGeneration: state.executionGeneration,
    loadStatus: state.loadStatus,
    canExecute: state.capabilities?.can_execute === true,
  }
}

export function captureDocumentExecutionFence(): DocumentExecutionFence {
  return executionFence(useDocumentStatusStore.getState())
}

export function isDocumentExecutionFenceCurrent(
  captured: DocumentExecutionFence,
): boolean {
  const currentState = useDocumentStatusStore.getState()
  const current = executionFence(currentState)
  return (captured.loadStatus === null || captured.canExecute) &&
    current.sourceFile === captured.sourceFile &&
    current.executionGeneration === captured.executionGeneration &&
    current.loadStatus === captured.loadStatus &&
    current.canExecute === captured.canExecute &&
    // A null status is the standalone-component/test state. Once a real
    // document is loaded, responses cannot publish against a retained graph
    // that the live document fence has marked unsynchronised.
    (current.loadStatus === null || currentState.graphSynchronized)
}

export default useDocumentStatusStore
