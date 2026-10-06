import { create } from "zustand"

import type {
  PipelineDiagnostic,
  PipelineDocumentCapabilities,
  PipelineEditorDocument,
  PipelineLoadStatus,
  PipelineNameViolation,
  PipelineNodeCompleteness,
} from "../types/pipelineDocument"

/**
 * A ready document loaded with name violations: editable, but fenced from
 * save, execution and preview until renames clear them. `lifted` is the fence
 * once they are cleared: the ready document's own capabilities.
 */
interface NameFence {
  fenced: PipelineDocumentCapabilities
  lifted: PipelineDocumentCapabilities
}

interface DocumentStatusState {
  loadStatus: PipelineLoadStatus | null
  capabilities: PipelineDocumentCapabilities | null
  diagnostics: PipelineDiagnostic[]
  diagnosticsOmitted: number
  completeness: PipelineNodeCompleteness[]
  completenessOmitted: number
  /** The document's remaining name violations, revalidated after each edit. */
  nameViolations: PipelineNameViolation[]
  nameFence: NameFence | null
  sourceRevision: string | null
  executionGeneration: number
  sourceText: string
  sourceFile: string
  sources: string[]
  activeSource: string | null
  sourceSelectionTrusted: boolean
  hasAuthoredContent: boolean
  graphSynchronized: boolean
  systemFailure: string | null
  /** Server fingerprint of the accepted document, or null when the accepting response named none. */
  documentFingerprint: string | null
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
    graphSynchronized: boolean,
    documentFingerprint: string,
  ) => void
  /** The remaining name violations the server reported for the current graph. */
  setNameViolations: (nameViolations: PipelineNameViolation[]) => void
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
    nameViolations: [],
    nameFence: null,
    sourceRevision: null,
    executionGeneration: 0,
    sourceText: "",
    sourceFile: "",
    sources: [],
    activeSource: null,
    sourceSelectionTrusted: false,
    hasAuthoredContent: false,
    graphSynchronized: false,
    systemFailure: null,
    documentFingerprint: null,
  }
}

function nameFence(document: PipelineEditorDocument): NameFence | null {
  if (document.load_status !== "ready" || document.name_violations.length === 0) return null
  return {
    fenced: { ...document.capabilities },
    lifted: {
      ...document.capabilities,
      can_save: true,
      can_execute: true,
      can_preview: document.source_selection_trusted,
    },
  }
}

function documentState(
  document: PipelineEditorDocument,
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
    nameViolations: document.name_violations,
    nameFence: nameFence(document),
    sourceRevision: document.source_revision,
    executionGeneration,
    sourceText: document.source_text,
    sourceFile: document.source_file,
    sources: [...document.sources],
    activeSource: document.active_source,
    sourceSelectionTrusted: document.source_selection_trusted,
    hasAuthoredContent: document.has_authored_content,
    graphSynchronized,
    systemFailure: null,
    documentFingerprint,
  }
}

const useDocumentStatusStore = create<DocumentStatusStore>()((set) => ({
  ...initialState(),
  loadDocumentStatus: (document, graphSynchronized = true, documentFingerprint = null) =>
    set((state) => documentState(
      document, graphSynchronized, state.executionGeneration + 1, documentFingerprint,
    )),
  loadLiveDocumentStatus: (document, graphSynchronized, documentFingerprint) =>
    set((state) => documentState(
      document, graphSynchronized, state.executionGeneration + 1, documentFingerprint,
    )),
  setNameViolations: (nameViolations) => set((state) => {
    // Only a document loaded with violations is fenced by them; any other
    // document's names are checked as they are edited.
    if (state.nameFence === null) return {}
    const capabilities = nameViolations.length === 0 ? state.nameFence.lifted : state.nameFence.fenced
    return { nameViolations, capabilities: { ...capabilities } }
  }),
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
  if (state.nameViolations.length > 0) {
    return "Rename the nodes the name banner lists before saving or running the pipeline."
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
