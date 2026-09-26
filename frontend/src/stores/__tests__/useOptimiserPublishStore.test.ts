import { beforeEach, describe, expect, it, vi } from "vitest"

const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()
vi.mock("../../api/client", () => ({
  saveOptimiser: (...args: unknown[]) => mockSaveOptimiser(...args),
  logOptimiserToMlflow: (...args: unknown[]) => mockLogOptimiserToMlflow(...args),
}))

import { renderHook } from "@testing-library/react"
import useOptimiserPublishStore, { usePublishState } from "../useOptimiserPublishStore"

const SAVE = { nodeId: "opt_1", pointIndex: null, outputPath: "output/a.json", version: "", stale: false }
const LOG = { nodeId: "opt_1", jobId: "job_1", pointIndex: null, destination: "", experimentName: "e", stale: false }

/** A server refusal carrying a structured `{error_code, message}` detail. */
function structuredError(errorCode: string, message: string): Error {
  return Object.assign(new Error(message), { status: 409, rawDetail: { error_code: errorCode, message } })
}

beforeEach(() => {
  vi.clearAllMocks()
  useOptimiserPublishStore.setState({ byNode: {} })
})

describe("useOptimiserPublishStore", () => {
  it("keeps receipts per solve job, so a new job starts with none", async () => {
    mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "/p/output/a.json", apply_path: "output/a.json", message: "" })
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1" })
    expect(useOptimiserPublishStore.getState().byNode.opt_1?.saveReceipt?.apply_path).toBe("output/a.json")

    mockSaveOptimiser.mockReturnValue(new Promise(() => {}))
    void useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_2" })
    const state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.jobId).toBe("job_2")
    expect(state?.saveReceipt).toBeNull()
    expect(state?.saving).toBe(true)
  })

  it("drops a reply for a job the node has moved past", async () => {
    let resolveOld: (value: unknown) => void = () => {}
    mockSaveOptimiser.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    const oldSave = useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1" })
    mockSaveOptimiser.mockReturnValueOnce(new Promise(() => {}))
    void useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_2" })

    resolveOld({ status: "ok", path: "/old", apply_path: "old.json", message: "" })
    await oldSave
    const state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.jobId).toBe("job_2")
    expect(state?.saveReceipt).toBeNull()
  })

  it("sends the target explicitly and records a stale publish", async () => {
    mockLogOptimiserToMlflow.mockResolvedValue({ status: "ok", backend: "local", experiment_name: "e", run_id: "r", run_url: null, tracking_uri: "" })
    await useOptimiserPublishStore.getState().log({
      nodeId: "opt_1",
      jobId: "job_1",
      pointIndex: 3,
      destination: "",
      experimentName: "",
      stale: true,
    })
    expect(mockLogOptimiserToMlflow).toHaveBeenCalledWith({
      job_id: "job_1",
      point_index: 3,
      destination: "",
      experiment_name: null,
      stale: true,
    })
    expect(useOptimiserPublishStore.getState().byNode.opt_1?.logReceipt).toMatchObject({ run_id: "r", pointIndex: 3 })
  })

  it("turns an existing-file refusal into a Replace prompt that keeps the exact request", async () => {
    mockSaveOptimiser.mockRejectedValue(
      structuredError("optimiser_result_exists", "Optimiser result already exists: output/a.json"),
    )
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1", pointIndex: 2 })

    const state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.saving).toBe(false)
    expect(state?.saveError).toBeNull()
    expect(state?.overwritePrompt).toEqual({
      message: "Optimiser result already exists: output/a.json",
      request: { ...SAVE, jobId: "job_1", pointIndex: 2 },
    })
  })

  it("reports a refusal on a Replace retry as an error, not another prompt", async () => {
    mockSaveOptimiser.mockRejectedValue(
      structuredError("optimiser_result_exists", "Optimiser result already exists: output/a.json"),
    )
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1", overwrite: true })

    const state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.overwritePrompt).toBeNull()
    expect(state?.saveError).toBe("Optimiser result already exists: output/a.json")
  })

  it("records any other save failure as the save error", async () => {
    mockSaveOptimiser.mockRejectedValue(new Error("disk full"))
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1" })

    const state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.saving).toBe(false)
    expect(state?.saveError).toBe("disk full")
    expect(state?.overwritePrompt).toBeNull()
  })

  it("records a log failure with its error code, and clears both on the next log", async () => {
    mockLogOptimiserToMlflow.mockRejectedValueOnce(structuredError("mlflow_unreachable", "MLflow is unreachable"))
    await useOptimiserPublishStore.getState().log(LOG)
    let state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.logging).toBe(false)
    expect(state?.logError).toBe("MLflow is unreachable")
    expect(state?.logErrorCode).toBe("mlflow_unreachable")

    mockLogOptimiserToMlflow.mockReturnValueOnce(new Promise(() => {}))
    void useOptimiserPublishStore.getState().log(LOG)
    state = useOptimiserPublishStore.getState().byNode.opt_1
    expect(state?.logging).toBe(true)
    expect(state?.logError).toBeNull()
    expect(state?.logErrorCode).toBeNull()
  })

  it("dismisses the Replace prompt for the current job only", async () => {
    mockSaveOptimiser.mockRejectedValue(
      structuredError("optimiser_result_exists", "Optimiser result already exists: output/a.json"),
    )
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1" })

    useOptimiserPublishStore.getState().dismissOverwrite("opt_1", "job_0")
    expect(useOptimiserPublishStore.getState().byNode.opt_1?.overwritePrompt).not.toBeNull()

    useOptimiserPublishStore.getState().dismissOverwrite("opt_1", "job_1")
    expect(useOptimiserPublishStore.getState().byNode.opt_1?.overwritePrompt).toBeNull()
  })

  it("exposes a node's state only for the job it belongs to", async () => {
    mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "/p/a.json", apply_path: "a.json", message: "" })
    await useOptimiserPublishStore.getState().save({ ...SAVE, jobId: "job_1" })

    expect(renderHook(() => usePublishState("opt_1", "job_1")).result.current?.saveReceipt?.apply_path).toBe("a.json")
    expect(renderHook(() => usePublishState("opt_1", "job_2")).result.current).toBeNull()
    expect(renderHook(() => usePublishState("opt_1", null)).result.current).toBeNull()
    expect(renderHook(() => usePublishState("opt_2", "job_1")).result.current).toBeNull()
  })
})
