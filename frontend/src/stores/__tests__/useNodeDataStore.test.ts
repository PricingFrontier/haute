import { describe, it, expect, beforeEach, vi } from "vitest"

import type { NodeDataPointResponse } from "../../api/types"
import useNodeDataStore, { columnsCoverDemand } from "../useNodeDataStore"

function point(overrides: Partial<NodeDataPointResponse> = {}): NodeDataPointResponse {
  return {
    consumer_node_id: "explore",
    point: { producer_node_id: "join", port_label: null },
    slot_key: "join||live",
    kind: "node_output",
    state: "current",
    demand: "all",
    data_version: "gen-1",
    row_count: 1000,
    size_bytes: 2048,
    retention: "pinned",
    generation: {
      generation_id: "gen-1",
      columns: "all",
      row_count: 1000,
      column_count: 4,
      size_bytes: 2048,
      retention: "pinned",
      fresh: true,
      created_at: 1,
    },
    job: null,
    reads_directly: false,
    build_endpoint: null,
    clear_endpoint: null,
    ...overrides,
  }
}

/** The asking consumer's data identity; these tests never vary it. */
const IDENTITY = "identity-1"

describe("useNodeDataStore", () => {
  beforeEach(() => {
    useNodeDataStore.getState().reset()
  })

  it("keys one entry by slot so every consumer of the point shares it", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    store.observePoint(
      point({ consumer_node_id: "banding", demand: ["premium"], state: "current" }),
      "live",
      IDENTITY,
    )

    const slots = useNodeDataStore.getState().slots
    expect(Object.keys(slots)).toEqual(["join||live"])
    expect(slots["join||live"].generation?.generation_id).toBe("gen-1")
    expect(slots["join||live"].reportedDemand).toEqual(["premium"])
  })

  it("raises the epoch only when the data the point holds changes", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    const afterFirst = useNodeDataStore.getState().epoch

    store.observePoint(point({ consumer_node_id: "banding", demand: ["premium"] }), "live", IDENTITY)
    expect(useNodeDataStore.getState().epoch).toBe(afterFirst)

    store.observePoint(
      point({
        data_version: "gen-2",
        generation: { ...point().generation!, generation_id: "gen-2" },
      }),
      "live",
      IDENTITY,
    )
    expect(useNodeDataStore.getState().epoch).toBe(afterFirst + 1)
  })

  it("raises the epoch when a generation widens its columns", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ generation: { ...point().generation!, columns: ["premium"] } }),
      "live",
      IDENTITY,
    )
    const narrow = useNodeDataStore.getState().epoch

    store.observePoint(
      point({ generation: { ...point().generation!, columns: ["premium", "region"] } }),
      "live",
      IDENTITY,
    )

    expect(useNodeDataStore.getState().epoch).toBe(narrow + 1)
  })

  it("tracks a running build once, for the one poller, until it finishes", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point({ state: "missing", generation: null, data_version: null }), "live", IDENTITY)

    store.startJob("join||live", { jobId: "job-1", message: "Caching data", startedByLabel: "Explore" })
    expect(useNodeDataStore.getState().jobs).toEqual({
      "join||live": { jobId: "job-1", progress: 0.03, message: "Caching data", startedByLabel: "Explore" },
    })
    expect(useNodeDataStore.getState().slots["join||live"].reportedState).toBe("building")

    store.updateJobProgress("join||live", { status: "running", progress: 0.5, message: "Half way" })
    expect(useNodeDataStore.getState().jobs["join||live"].progress).toBe(0.5)
    expect(useNodeDataStore.getState().slots["join||live"].job?.message).toBe("Half way")

    const beforeFinish = useNodeDataStore.getState().epoch
    store.finishJob("join||live", { status: "completed", progress: 1, message: "Data is cached" })

    expect(useNodeDataStore.getState().jobs).toEqual({})
    expect(useNodeDataStore.getState().slots["join||live"].job).toBeNull()
    expect(useNodeDataStore.getState().epoch).toBe(beforeFinish + 1)
  })

  it("adopts a build another client started, as the point reports it", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({
        state: "building",
        generation: null,
        data_version: null,
        job: { job_id: "job-elsewhere", progress: 0.2, message: "Caching data" },
      }),
      "live",
      IDENTITY,
    )

    expect(useNodeDataStore.getState().jobs["join||live"]).toEqual({
      jobId: "job-elsewhere",
      progress: 0.2,
      message: "Caching data",
      startedByLabel: "join",
    })
  })

  it("ends a failed build so its consumers ask what is there now", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point({ state: "missing", generation: null, data_version: null }), "live", IDENTITY)
    store.startJob("join||live", { jobId: "job-1", message: "Caching", startedByLabel: "Explore" })
    const before = useNodeDataStore.getState().epoch

    store.finishJob("join||live", { status: "memory_limited", progress: 1, message: "Out of memory" })

    // A failed build leaves the point as it was, which consumers must see: a
    // job that stays behind would show a Cancel button with nothing to cancel.
    expect(useNodeDataStore.getState().epoch).toBe(before + 1)
    expect(useNodeDataStore.getState().jobs).toEqual({})
    expect(useNodeDataStore.getState().slots["join||live"].job).toBeNull()
  })

  it("tracks a delegated build with the canceller every consumer uses", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ kind: "data_input", state: "missing", generation: null, data_version: null }),
      "live",
      IDENTITY,
    )
    const cancel = vi.fn()

    store.startDelegatedBuild("join||live", {
      token: "op-1",
      message: "Preparing this input",
      startedByLabel: "Banding",
      cancel,
    })
    expect(useNodeDataStore.getState().slots["join||live"].reportedState).toBe("building")
    expect(useNodeDataStore.getState().jobs).toEqual({})

    store.reportDelegatedProgress("join||live", "op-1", "Caching Quote Input as Parquet…")
    expect(useNodeDataStore.getState().slots["join||live"].delegatedBuild?.message).toBe(
      "Caching Quote Input as Parquet…",
    )

    useNodeDataStore.getState().slots["join||live"].delegatedBuild?.cancel()
    expect(cancel).toHaveBeenCalledTimes(1)

    const before = useNodeDataStore.getState().epoch
    store.finishDelegatedBuild("join||live", "op-1")
    expect(useNodeDataStore.getState().slots["join||live"].delegatedBuild).toBeNull()
    expect(useNodeDataStore.getState().epoch).toBe(before + 1)
  })

  it("ignores an abandoned delegated pass reporting on the build that replaced it", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ kind: "data_input", state: "missing", generation: null, data_version: null }),
      "live",
      IDENTITY,
    )
    store.startDelegatedBuild("join||live", {
      token: "op-old",
      message: "Preparing this input",
      startedByLabel: "Banding",
      cancel: vi.fn(),
    })
    const replacement = vi.fn()
    store.startDelegatedBuild("join||live", {
      token: "op-new",
      message: "Preparing this input again",
      startedByLabel: "Explore",
      cancel: replacement,
    })

    store.reportDelegatedProgress("join||live", "op-old", "stale progress")
    store.finishDelegatedBuild("join||live", "op-old")

    const current = useNodeDataStore.getState().slots["join||live"].delegatedBuild
    expect(current?.token).toBe("op-new")
    expect(current?.message).toBe("Preparing this input again")
    expect(current?.cancel).toBe(replacement)
  })

  it("ignores a retained message from a pass that no longer owns the slot", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ kind: "data_input", state: "missing", generation: null, data_version: null }),
      "live",
      IDENTITY,
    )
    const replacement = vi.fn()
    store.startDelegatedBuild("join||live", {
      token: "op-new",
      message: "Preparing this input",
      startedByLabel: "Explore",
      cancel: replacement,
    })

    store.retainDelegatedBuild("join||live", "op-old", {
      message: "This build has not stopped yet; cancel it again",
      cancel: vi.fn(),
    })

    const current = useNodeDataStore.getState().slots["join||live"].delegatedBuild
    expect(current?.message).toBe("Preparing this input")
    expect(current?.cancel).toBe(replacement)
  })

  it("keeps counting the epoch through a reset, so an unchanged consumer still asks again", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    store.observePoint(point({ data_version: "gen-2" }), "live", IDENTITY)
    const before = useNodeDataStore.getState().epoch

    useNodeDataStore.getState().reset()

    expect(useNodeDataStore.getState().slots).toEqual({})
    expect(useNodeDataStore.getState().epoch).toBe(before + 1)
  })

  it("forgets a slot and its job together", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    store.startJob("join||live", { jobId: "job-1", message: "Caching", startedByLabel: "Explore" })
    const before = useNodeDataStore.getState().epoch

    store.forgetSlot("join||live")

    expect(useNodeDataStore.getState().slots).toEqual({})
    expect(useNodeDataStore.getState().jobs).toEqual({})
    expect(useNodeDataStore.getState().epoch).toBe(before + 1)
  })
})

describe("columnsCoverDemand", () => {
  it("treats every column as covering any demand", () => {
    expect(columnsCoverDemand("all", "all")).toBe(true)
    expect(columnsCoverDemand("all", ["premium"])).toBe(true)
  })

  it("never lets a named column set cover a demand for every column", () => {
    expect(columnsCoverDemand(["premium"], "all")).toBe(false)
  })

  it("covers a demand exactly when it holds every demanded column", () => {
    expect(columnsCoverDemand(["premium", "region"], ["premium"])).toBe(true)
    expect(columnsCoverDemand(["premium"], ["premium", "region"])).toBe(false)
    expect(columnsCoverDemand(["premium"], [])).toBe(true)
  })
})
