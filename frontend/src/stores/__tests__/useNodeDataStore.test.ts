import { describe, it, expect, beforeEach, vi } from "vitest"

import type { NodeDataPointResponse, NodeDataProfile } from "../../api/types"
import useNodeDataStore, { columnsCoverDemand, ANNOUNCED_CAPTURES_CAP } from "../useNodeDataStore"

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

function profile(overrides: Partial<NodeDataProfile> = {}): NodeDataProfile {
  return {
    row_count: 1000,
    column_count: 4,
    columns: [],
    overview_summary: {
      data_quality: {
        issue_count: 0,
        issues: [],
        duplicate_row_count: null,
        duplicate_ratio: null,
      },
      categorical_summary: [],
    },
    data_version: "gen-1",
    generated_at: 1,
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

describe("useNodeDataStore epoch for a preview's own capture", () => {
  it("raises the epoch for a snapshot no slot reported", () => {
    const before = useNodeDataStore.getState().epoch
    useNodeDataStore.getState().bumpEpoch()
    expect(useNodeDataStore.getState().epoch).toBe(before + 1)
  })
})

describe("useNodeDataStore noteAnnouncedCaptures", () => {
  beforeEach(() => {
    useNodeDataStore.getState().reset()
  })

  it("records ids and answers unseen only the first time, and an empty list answers false", () => {
    const store = useNodeDataStore.getState()
    const beforeEpoch = store.epoch

    expect(store.noteAnnouncedCaptures([])).toBe(false)
    expect(useNodeDataStore.getState().announcedCaptures).toEqual([])
    expect(useNodeDataStore.getState().epoch).toBe(beforeEpoch)

    expect(store.noteAnnouncedCaptures(["gen-a", "gen-b"])).toBe(true)
    expect(useNodeDataStore.getState().announcedCaptures).toEqual(["gen-a", "gen-b"])
    expect(useNodeDataStore.getState().epoch).toBe(beforeEpoch)

    // Repeated announcements of the same ids return false and leave the record unchanged
    expect(store.noteAnnouncedCaptures(["gen-a"])).toBe(false)
    expect(store.noteAnnouncedCaptures(["gen-b"])).toBe(false)
    expect(store.noteAnnouncedCaptures(["gen-a", "gen-b"])).toBe(false)
    expect(useNodeDataStore.getState().announcedCaptures).toEqual(["gen-a", "gen-b"])

    // A batch containing at least one unseen id answers true and records the new id
    expect(store.noteAnnouncedCaptures(["gen-a", "gen-c"])).toBe(true)
    expect(useNodeDataStore.getState().announcedCaptures).toEqual(["gen-a", "gen-b", "gen-c"])
    expect(useNodeDataStore.getState().epoch).toBe(beforeEpoch)

    expect(store.noteAnnouncedCaptures(["gen-c"])).toBe(false)
  })

  it("drops the oldest ids past the cap so announcing an evicted id again answers true", () => {
    const store = useNodeDataStore.getState()
    const ids = Array.from({ length: ANNOUNCED_CAPTURES_CAP }, (_, i) => `gen-${i}`)

    expect(store.noteAnnouncedCaptures(ids)).toBe(true)
    expect(useNodeDataStore.getState().announcedCaptures.length).toBe(ANNOUNCED_CAPTURES_CAP)
    expect(useNodeDataStore.getState().announcedCaptures[0]).toBe("gen-0")

    // Pushing one more id evicts the oldest (gen-0)
    expect(store.noteAnnouncedCaptures(["gen-cap"])).toBe(true)
    const state = useNodeDataStore.getState()
    expect(state.announcedCaptures.length).toBe(ANNOUNCED_CAPTURES_CAP)
    expect(state.announcedCaptures[0]).toBe("gen-1")
    expect(state.announcedCaptures[ANNOUNCED_CAPTURES_CAP - 1]).toBe("gen-cap")
    expect(state.announcedCaptures.includes("gen-0")).toBe(false)

    // Announcing the evicted gen-0 answers true again
    expect(store.noteAnnouncedCaptures(["gen-0"])).toBe(true)
    // gen-cap was not evicted, so announcing it answers false
    expect(store.noteAnnouncedCaptures(["gen-cap"])).toBe(false)
  })

  it("clears the record on reset while the epoch still rises", () => {
    const store = useNodeDataStore.getState()
    expect(store.noteAnnouncedCaptures(["gen-a"])).toBe(true)
    expect(useNodeDataStore.getState().announcedCaptures).toEqual(["gen-a"])
    const beforeEpoch = useNodeDataStore.getState().epoch

    useNodeDataStore.getState().reset()

    expect(useNodeDataStore.getState().announcedCaptures).toEqual([])
    expect(useNodeDataStore.getState().epoch).toBe(beforeEpoch + 1)

    // Announcing gen-a after reset answers true again
    expect(useNodeDataStore.getState().noteAnnouncedCaptures(["gen-a"])).toBe(true)
  })

  // Every action below can arrive for a slot the store no longer holds: a poll
  // or a callback that outlived the consumer it was started for. None of them
  // may resurrect a slot from a status message, and none may raise the epoch —
  // that would send every consumer back to the server to learn nothing.
  it("ignores every job action naming a slot it does not hold", () => {
    const store = useNodeDataStore.getState()
    const beforeEpoch = useNodeDataStore.getState().epoch
    const cancel = vi.fn()

    store.startJob("gone||live", {
      jobId: "job-1",
      message: "Caching data",
      startedByLabel: "Explore",
    })
    store.updateJobProgress("gone||live", { status: "running", progress: 0.5, message: "Half way" })
    store.finishJob("gone||live", { status: "completed", progress: 1, message: "Data is cached" })
    store.startDelegatedBuild("gone||live", {
      token: "token-1",
      message: "Building",
      startedByLabel: "Explore",
      cancel,
    })
    store.forgetSlot("gone||live")

    const after = useNodeDataStore.getState()
    expect(after.slots).toEqual({})
    expect(after.jobs).toEqual({})
    expect(after.epoch).toBe(beforeEpoch)
    expect(cancel).not.toHaveBeenCalled()
  })

  it("ignores progress for a slot that holds no running job", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    const before = useNodeDataStore.getState().slots["join||live"]

    store.updateJobProgress("join||live", { status: "running", progress: 0.5, message: "Half way" })

    // The very same slot object: nothing was rewritten, so no consumer re-renders.
    expect(useNodeDataStore.getState().slots["join||live"]).toBe(before)
    expect(useNodeDataStore.getState().jobs).toEqual({})
  })

  it("keeps the running message when progress arrives without one", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ state: "missing", generation: null, data_version: null }),
      "live",
      IDENTITY,
    )
    store.startJob("join||live", {
      jobId: "job-1",
      message: "Caching data",
      startedByLabel: "Explore",
    })

    store.updateJobProgress("join||live", { status: "running", progress: 0.4, message: "" })

    const job = useNodeDataStore.getState().jobs["join||live"]
    expect(job.progress).toBe(0.4)
    // An empty message means "no news", not "stop saying what you were saying".
    expect(job.message).toBe("Caching data")
  })

  it("keeps the running job when the point reports the build without naming it", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(
      point({ state: "missing", generation: null, data_version: null }),
      "live",
      IDENTITY,
    )
    store.startJob("join||live", {
      jobId: "job-1",
      message: "Caching data",
      startedByLabel: "Explore",
    })

    // The point agrees a build is running but carries no job of its own, as a
    // poll that raced the job's registration does.
    store.observePoint(
      point({ state: "building", generation: null, data_version: null, job: null }),
      "live",
      IDENTITY,
    )

    expect(useNodeDataStore.getState().slots["join||live"].job).toEqual({
      jobId: "job-1",
      progress: 0.03,
      message: "Caching data",
      startedByLabel: "Explore",
    })
  })

  it("tracks a profile job's progress, keeping the running message when none arrives", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    store.startProfileJob("join||live", {
      jobId: "profile-1",
      message: "Profiling data",
      startedByLabel: "Explore",
      dataVersion: "gen-1",
    })

    store.updateProfileProgress("join||live", {
      status: "running",
      progress: 0.6,
      message: "Scanning columns",
    })
    expect(useNodeDataStore.getState().profileJobs["join||live"]).toMatchObject({
      progress: 0.6,
      message: "Scanning columns",
    })

    store.updateProfileProgress("join||live", { status: "running", progress: 0.8, message: "" })
    expect(useNodeDataStore.getState().profileJobs["join||live"]).toMatchObject({
      progress: 0.8,
      message: "Scanning columns",
    })
  })

  it("ignores profile progress and completion for a slot running no profile", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    const beforeEpoch = useNodeDataStore.getState().epoch

    store.updateProfileProgress("join||live", {
      status: "running",
      progress: 0.5,
      message: "Scanning columns",
    })
    store.finishProfileJob("join||live", {
      status: "completed",
      progress: 1,
      message: "Profiled",
      profile: profile(),
    })

    const after = useNodeDataStore.getState()
    expect(after.profileJobs).toEqual({})
    // Nothing was asked for, so nothing was learned and nothing failed.
    expect(after.profiles).toEqual({})
    expect(after.profileFailures).toEqual({})
    expect(after.epoch).toBe(beforeEpoch)
  })

  it("says why a profile is absent when its job ends with neither profile nor reason", () => {
    const store = useNodeDataStore.getState()
    store.observePoint(point(), "live", IDENTITY)
    store.startProfileJob("join||live", {
      jobId: "profile-1",
      message: "Profiling data",
      startedByLabel: "Explore",
      dataVersion: "gen-1",
    })

    // A terminal status carrying no profile, no error and no message — the
    // shape a cancelled or evicted job reports.
    store.finishProfileJob("join||live", { status: "failed", progress: 1, message: "" })

    expect(useNodeDataStore.getState().profileJobs).toEqual({})
    expect(useNodeDataStore.getState().profileFailures["join||live"]).toEqual({
      dataVersion: "gen-1",
      message: "Profiling this data did not finish.",
    })
  })
})

