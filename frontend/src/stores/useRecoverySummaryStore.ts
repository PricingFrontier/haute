import { create } from "zustand"
import type { PipelineNodeCompleteness } from "../types/pipelineDocument"
import type { PipelineRepairChange, PipelineRepairFieldChange } from "../types/pipelineRepair"

/** Transient session record of one applied recovery, keyed by recovery id. */
export interface RecoverySummary {
  recoveryId: string
  fieldChanges: PipelineRepairFieldChange[]
  completeness: PipelineNodeCompleteness[]
  previousConfig: Record<string, unknown> | null
  changes: PipelineRepairChange[]
}

interface RecoverySummaryState {
  summaries: Record<string, RecoverySummary>
  recordSummary: (summary: RecoverySummary) => void
  dismissSummary: (recoveryId: string) => void
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
    set((state) => ({ summaries: { ...state.summaries, [summary.recoveryId]: summary } })),
  dismissSummary: (recoveryId) =>
    set((state) => {
      const { [recoveryId]: _dismissed, ...rest } = state.summaries
      return { summaries: rest }
    }),
  reset: () => set({ summaries: {} }),
}))
