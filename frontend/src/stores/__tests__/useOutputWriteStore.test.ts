import { beforeEach, describe, expect, it } from "vitest"
import useOutputWriteStore, {
  resetOutputWriteStoreForTests,
} from "../useOutputWriteStore"

describe("useOutputWriteStore", () => {
  beforeEach(resetOutputWriteStoreForTests)

  it("keeps one pending request per node and ignores stale completions", () => {
    const first = useOutputWriteStore.getState().begin("output-1", "config-a")
    expect(first).toBe(1)
    expect(useOutputWriteStore.getState().begin("output-1", "config-a")).toBeNull()

    useOutputWriteStore.getState().complete("output-1", first!, "config-a", {
      phase: "success",
      result: { status: "ok", path: "out.csv", row_count: 2 },
    })
    const second = useOutputWriteStore.getState().begin("output-1", "config-b")
    useOutputWriteStore.getState().complete("output-1", first!, "config-a", {
      phase: "error",
      message: "stale",
    })

    expect(useOutputWriteStore.getState().writes["output-1"]).toEqual({
      requestId: second,
      configIdentity: "config-b",
      phase: "writing",
    })
  })

  it("allows independent nodes and ignores absent or wrong-config completions", () => {
    const first = useOutputWriteStore.getState().begin("output-1", "config-a")
    const second = useOutputWriteStore.getState().begin("output-2", "config-b")
    expect([first, second]).toEqual([1, 2])

    useOutputWriteStore.getState().complete("missing", 99, "config-x", {
      phase: "error",
      message: "absent",
    })
    useOutputWriteStore.getState().complete("output-1", first!, "wrong-config", {
      phase: "error",
      message: "wrong",
    })

    expect(useOutputWriteStore.getState().writes["output-1"]?.phase).toBe("writing")
    expect(useOutputWriteStore.getState().writes["output-2"]?.phase).toBe("writing")
  })
})
