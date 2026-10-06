/**
 * Export receipts for a completed training result, read from the job status.
 *
 * The server records every successful MLflow log and model-file save on the
 * training job, so what a result was exported to survives pane switches and
 * reloads: the Export pane re-reads it whenever it mounts for a job and after
 * each export.
 */
import { useCallback, useEffect, useState } from "react"
import { ApiError, getTrainStatus } from "../../api/client"
import type { TrainExportReceipts } from "../../api/types"

const NO_RECEIPTS: TrainExportReceipts = { mlflow: [], model_files: [] }

export interface ExportReceiptsState {
  receipts: TrainExportReceipts
  /** Re-read the job's receipts (after an export or a failed attempt). */
  refresh: () => void
}

export function useExportReceipts(jobId: string | null): ExportReceiptsState {
  const [state, setState] = useState<{ jobId: string | null; receipts: TrainExportReceipts }>({
    jobId: null,
    receipts: NO_RECEIPTS,
  })
  const [generation, setGeneration] = useState(0)

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    getTrainStatus(jobId, { signal: controller.signal }).then(
      (status) => {
        if (controller.signal.aborted) return
        setState({ jobId, receipts: status.export_receipts ?? NO_RECEIPTS })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        // A job that is gone has no receipts to show; anything else keeps the
        // last known receipts rather than claiming nothing was exported.
        if (caught instanceof ApiError && caught.status === 404) {
          setState({ jobId, receipts: NO_RECEIPTS })
        }
      },
    )
    return () => controller.abort()
  }, [jobId, generation])

  const refresh = useCallback(() => setGeneration((value) => value + 1), [])
  return {
    receipts: state.jobId === jobId && jobId ? state.receipts : NO_RECEIPTS,
    refresh,
  }
}

/** A new operation ID for one user action; a retry of that action reuses it. */
export function newOperationId(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID().replace(/-/g, "")
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
}
