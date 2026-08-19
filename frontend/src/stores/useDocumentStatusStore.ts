import { create } from "zustand"

import type {
  PipelineDiagnostic,
  PipelineDocumentCapabilities,
  PipelineEditorDocument,
  PipelineLoadStatus,
} from "../types/pipelineDocument"

interface DocumentStatusState {
  loadStatus: PipelineLoadStatus | null
  capabilities: PipelineDocumentCapabilities | null
  diagnostics: PipelineDiagnostic[]
  diagnosticsOmitted: number
  sourceRevision: string | null
  sourceText: string
  sourceFile: string
  sources: string[]
  activeSource: string | null
  sourceSelectionTrusted: boolean
  hasAuthoredContent: boolean
  retainedCanvas: RetainedPipelineCanvas | null
  graphSynchronized: boolean
  systemFailure: string | null
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
  sourceRevision: string | null
  loadStatus: PipelineLoadStatus | null
  canExecute: boolean
}

export interface DocumentStatusStore extends DocumentStatusState {
  loadDocumentStatus: (
    document: PipelineEditorDocument,
    graphSynchronized?: boolean,
  ) => void
  loadLiveDocumentStatus: (
    document: PipelineEditorDocument,
    retainedCanvas: RetainedPipelineCanvas | null,
    graphSynchronized: boolean,
  ) => void
  setGraphSynchronized: (graphSynchronized: boolean) => void
  setSystemFailure: (systemFailure: string) => void
  setSourceRevision: (sourceRevision: string | null) => void
  reset: () => void
}

function initialState(): DocumentStatusState {
  return {
    loadStatus: null,
    capabilities: null,
    diagnostics: [],
    diagnosticsOmitted: 0,
    sourceRevision: null,
    sourceText: "",
    sourceFile: "",
    sources: [],
    activeSource: null,
    sourceSelectionTrusted: false,
    hasAuthoredContent: false,
    retainedCanvas: null,
    graphSynchronized: false,
    systemFailure: null,
  }
}

function documentState(
  document: PipelineEditorDocument,
  retainedCanvas: RetainedPipelineCanvas | null,
  graphSynchronized: boolean,
): DocumentStatusState {
  return {
    loadStatus: document.load_status,
    capabilities: { ...document.capabilities },
    diagnostics: document.diagnostics.map((diagnostic) => ({
      ...diagnostic,
      source_span: diagnostic.source_span ? { ...diagnostic.source_span } : null,
    })),
    diagnosticsOmitted: document.diagnostics_omitted,
    sourceRevision: document.source_revision,
    sourceText: document.source_text,
    sourceFile: document.source_file,
    sources: [...document.sources],
    activeSource: document.active_source,
    sourceSelectionTrusted: document.source_selection_trusted,
    hasAuthoredContent: document.has_authored_content,
    retainedCanvas,
    graphSynchronized,
    systemFailure: null,
  }
}

const useDocumentStatusStore = create<DocumentStatusStore>()((set) => ({
  ...initialState(),
  loadDocumentStatus: (document, graphSynchronized = true) =>
    set(documentState(document, null, graphSynchronized)),
  loadLiveDocumentStatus: (document, retainedCanvas, graphSynchronized) =>
    set(documentState(document, retainedCanvas, graphSynchronized)),
  setGraphSynchronized: (graphSynchronized) => set({ graphSynchronized }),
  setSystemFailure: (systemFailure) => set({ systemFailure, graphSynchronized: false }),
  setSourceRevision: (sourceRevision) => set({ sourceRevision }),
  reset: () => set(initialState()),
}))

function executionFence(state: DocumentStatusState): DocumentExecutionFence {
  return {
    sourceFile: state.sourceFile,
    sourceRevision: state.sourceRevision,
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
    current.sourceRevision === captured.sourceRevision &&
    current.loadStatus === captured.loadStatus &&
    current.canExecute === captured.canExecute &&
    // A null status is the standalone-component/test state. Once a real
    // document is loaded, responses cannot publish against a retained graph
    // that the live document fence has marked unsynchronised.
    (current.loadStatus === null || currentState.graphSynchronized)
}

export default useDocumentStatusStore
