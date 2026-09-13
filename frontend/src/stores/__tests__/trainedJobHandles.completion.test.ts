/**
 * A completed training job is remembered for reload at the results store's
 * completion boundary, so it is restorable even when the node's editor was
 * never open while the job finished (background completion).
 */
import { beforeEach, describe, expect, it } from "vitest"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useNodeResultsStore from "../useNodeResultsStore"
import { makeTrainResult } from "../../test-utils/factories"
import { readTrainedJobHandle } from "../../utils/trainedJobHandles"

const DOCUMENT = "rating/main.py"

describe("remembering training jobs at completion", () => {
  beforeEach(() => {
    localStorage.clear()
    useDocumentStatusStore.setState({ ...useDocumentStatusStore.getInitialState(), sourceFile: DOCUMENT })
    useNodeResultsStore.setState({ trainJobs: {}, trainResults: {} })
  })

  it("remembers a job that completes with no editor mounted, with its submitted lineage", () => {
    useNodeResultsStore.getState().startTrainJob("model", "job_bg", "model", "hash", "live", 3, "lineage-submitted")

    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult())

    expect(readTrainedJobHandle(DOCUMENT, "model")).toEqual({
      jobId: "job_bg",
      configHash: "hash",
      source: "live",
      lineage: "lineage-submitted",
    })
  })

  it("never remembers a job started without a lineage", () => {
    useNodeResultsStore.getState().startTrainJob("model", "job_bare", "model", "hash", "live", 0)

    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult())

    expect(readTrainedJobHandle(DOCUMENT, "model")).toBeNull()
  })

  it("forgets the node when a later training attempt fails", () => {
    useNodeResultsStore.getState().startTrainJob("model", "job_ok", "model", "hash", "live", 0, "l1")
    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult())
    expect(readTrainedJobHandle(DOCUMENT, "model")?.jobId).toBe("job_ok")

    useNodeResultsStore.getState().startTrainJob("model", "job_oom", "model", "hash", "live", 0, "l2")
    useNodeResultsStore.getState().failTrainJob("model", "Out of memory")

    expect(readTrainedJobHandle(DOCUMENT, "model")).toBeNull()
  })

  it("forgets the node when a job completes with an error result", () => {
    useNodeResultsStore.getState().startTrainJob("model", "job_ok", "model", "hash", "live", 0, "l1")
    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult())
    expect(readTrainedJobHandle(DOCUMENT, "model")?.jobId).toBe("job_ok")
    useNodeResultsStore.getState().startTrainJob("model", "job_err", "model", "hash", "live", 0, "l2")

    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult({ status: "error", error: "bad" }))

    expect(readTrainedJobHandle(DOCUMENT, "model")).toBeNull()
  })

  it("touches no handle when the document changed before the job finished", () => {
    useNodeResultsStore.getState().startTrainJob("model", "job_old_doc", "model", "hash", "live", 0, "l1")
    useDocumentStatusStore.setState({ sourceFile: "rating/other.py" })

    useNodeResultsStore.getState().completeTrainJob("model", makeTrainResult())

    expect(readTrainedJobHandle(DOCUMENT, "model")).toBeNull()
    expect(readTrainedJobHandle("rating/other.py", "model")).toBeNull()
  })
})
