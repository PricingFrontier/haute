import { beforeEach, describe, expect, it, vi } from "vitest"

const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()
vi.mock("../../api/client", () => ({
  saveOptimiser: (...args: unknown[]) => mockSaveOptimiser(...args),
  logOptimiserToMlflow: (...args: unknown[]) => mockLogOptimiserToMlflow(...args),
}))

import useOptimiserPublishStore from "../useOptimiserPublishStore"

const SAVE = { nodeId: "opt_1", pointIndex: null, outputPath: "output/a.json", version: "", stale: false }

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
})
