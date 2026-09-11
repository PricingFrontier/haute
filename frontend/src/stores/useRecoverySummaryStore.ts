import { create } from "zustand"
import type { PipelineNodeCompleteness } from "../types/pipelineDocument"
import type { PipelineRepairChange, PipelineRepairFieldChange } from "../types/pipelineRepair"

/** Transient session record of one applied recovery. */
export interface RecoverySummary {
  sourceFile: string
  recoveryId: string
  fieldChanges: PipelineRepairFieldChange[]
  completeness: PipelineNodeCompleteness[]
  previousConfig: Record<string, unknown> | null
  changes: PipelineRepairChange[]
}

/** Recovery ids repeat across documents; qualify by owning source file. */
export function recoverySummaryKey(sourceFile: string, recoveryId: string): string {
  return JSON.stringify([sourceFile, recoveryId])
}

interface RecoverySummaryState {
  summaries: Record<string, RecoverySummary>
  recordSummary: (summary: RecoverySummary) => void
  dismissSummary: (sourceFile: string, recoveryId: string) => void
  reset: () => void
}

/**
 * Session-only recovery summaries: never persisted, never sent to the server,
 * and deliberately survive document adoption and panel reselection so the
 * user can review what a recover retained after the node returns to its
 * normal editor.
 */
export const useRecoverySummaryStore = create<RecoverySummaryState>((set) => ({
  summaries: {},
  recordSummary: (summary) =>
    set((state) => ({
      summaries: {
        ...state.summaries,
        [recoverySummaryKey(summary.sourceFile, summary.recoveryId)]: summary,
      },
    })),
  dismissSummary: (sourceFile, recoveryId) =>
    set((state) => {
      const { [recoverySummaryKey(sourceFile, recoveryId)]: _dismissed, ...rest } = state.summaries
      return { summaries: rest }
    }),
  reset: () => set({ summaries: {} }),
}))
