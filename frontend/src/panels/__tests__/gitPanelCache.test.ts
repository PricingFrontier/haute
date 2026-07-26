import { describe, it, expect, beforeEach } from "vitest"
import {
  BRANCH_HISTORY_CAP,
  MILESTONE_SAVES_CAP,
  clearGitPanelCaches,
  readBranchHistory,
  readGraphCache,
  readMilestoneSaves,
  serializePayload,
  writeBranchHistory,
  writeGraphCache,
  writeMilestoneSaves,
} from "../gitPanelCache"
import type { BranchHistoryEntry } from "../gitPanelCache"
import type { GitGraphResponse, GitLedgerSave } from "../../api/types"

const save = (sha: string): GitLedgerSave => ({
  sha,
  short_sha: sha.slice(0, 6),
  message: `save ${sha}`,
  timestamp: "2026-07-08T00:00:00Z",
  files: [],
})

const entryFor = (branch: string): BranchHistoryEntry => {
  const milestones = [
    { sha: `${branch}-m1`, short_sha: "m1", message: "m1", timestamp: "2026-07-08T00:00:00Z", version_label: null },
  ]
  const pending: GitLedgerSave[] = []
  return {
    milestones,
    milestonesJson: serializePayload(milestones),
    pending,
    pendingJson: serializePayload(pending),
  }
}

describe("gitPanelCache", () => {
  beforeEach(clearGitPanelCaches)

  it("serializePayload round-trips identical payloads to identical strings", () => {
    const a = { entries: [save("abc")], working_branch: "b" }
    const b = structuredClone(a)
    expect(a).not.toBe(b)
    expect(serializePayload(a)).toBe(serializePayload(b))
    expect(serializePayload(a)).not.toBe(serializePayload({ ...a, working_branch: "c" }))
  })

  it("stores and returns a branch history entry by branch name", () => {
    const entry = entryFor("pricing-dev")
    writeBranchHistory("pricing-dev", entry)
    expect(readBranchHistory("pricing-dev")).toBe(entry)
    expect(readBranchHistory("unknown")).toBeUndefined()
  })

  it("evicts the least-recently-used branch past the cap", () => {
    for (let i = 0; i < BRANCH_HISTORY_CAP; i++) {
      writeBranchHistory(`b${i}`, entryFor(`b${i}`))
    }
    // Touch b0 so it is no longer the oldest, then overflow by one.
    expect(readBranchHistory("b0")).toBeDefined()
    writeBranchHistory("overflow", entryFor("overflow"))
    // b1 (now the least-recently-used) was evicted; b0 and the newcomer stay.
    expect(readBranchHistory("b1")).toBeUndefined()
    expect(readBranchHistory("b0")).toBeDefined()
    expect(readBranchHistory("overflow")).toBeDefined()
  })

  it("rewriting an existing branch does not evict anything", () => {
    for (let i = 0; i < BRANCH_HISTORY_CAP; i++) {
      writeBranchHistory(`b${i}`, entryFor(`b${i}`))
    }
    writeBranchHistory("b3", entryFor("b3"))
    for (let i = 0; i < BRANCH_HISTORY_CAP; i++) {
      expect(readBranchHistory(`b${i}`)).toBeDefined()
    }
  })

  it("caches milestone saves by sha with an LRU bound", () => {
    for (let i = 0; i < MILESTONE_SAVES_CAP; i++) {
      writeMilestoneSaves(`sha${i}`, [save(`sha${i}`)])
    }
    expect(readMilestoneSaves("sha0")).toBeDefined() // touch → most recent
    writeMilestoneSaves("overflow", [save("overflow")])
    expect(readMilestoneSaves("sha1")).toBeUndefined() // evicted
    expect(readMilestoneSaves("sha0")).toBeDefined()
    expect(readMilestoneSaves("overflow")).toEqual([save("overflow")])
  })

  it("holds a single whole-forest graph payload", () => {
    expect(readGraphCache()).toBeNull()
    const graph: GitGraphResponse = { working_branch: null, order: [], branches: [] }
    const json = serializePayload(graph)
    writeGraphCache(graph, json)
    expect(readGraphCache()).toEqual({ graph, json })
  })

  it("clearGitPanelCaches empties everything", () => {
    writeBranchHistory("b", entryFor("b"))
    writeMilestoneSaves("sha", [save("sha")])
    writeGraphCache({ working_branch: null, order: [], branches: [] }, "{}")
    clearGitPanelCaches()
    expect(readBranchHistory("b")).toBeUndefined()
    expect(readMilestoneSaves("sha")).toBeUndefined()
    expect(readGraphCache()).toBeNull()
  })
})
