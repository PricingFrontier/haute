import { describe, expect, it } from "vitest"
import type { GitGraphBranch, GitGraphEntry, GitGraphResponse } from "../../../api/types"
import { computeGitGraphLayout, DEFAULT_LANE_CAP } from "../layout"
import type { GitGraphView, RowDescriptor } from "../layout"

// ---------------------------------------------------------------------------
// Fixture builders — TS literals typed against GitGraphResponse (A-9: fixture
// drift from the locked schema is a compile error, not a runtime surprise).
// ---------------------------------------------------------------------------

// A milestone that folds saves is a merge of the ledger (parents: prior
// milestone + save tip), so `folded > 0` means two parents; zero-fold
// milestones are ordinary non-merge commits — matching engine reality.
const entry = (sha: string, folded = 0, over: Partial<GitGraphEntry> = {}): GitGraphEntry => ({
  sha,
  short_sha: sha.slice(0, 7),
  message: `commit ${sha}`,
  timestamp: "2026-07-01T00:00:00Z",
  version_label: null,
  is_root: false,
  parents: folded > 0 ? [`${sha}-prior`, `${sha}-save-tip`] : [],
  ...over,
})

const root = (sha: string): GitGraphEntry => entry(sha, 0, { is_root: true })

const branch = (
  name: string,
  entries: GitGraphEntry[],
  over: Partial<GitGraphBranch> = {},
): GitGraphBranch => ({
  name,
  is_archived: false,
  is_current: false,
  tip_sha: entries[0]?.sha ?? "",
  fork_point_sha: null,
  fork_of: null,
  forked_from: null,
  truncated: false,
  entries,
  ...over,
})

const milestone = (sha: string, expanded = false): RowDescriptor => ({
  kind: "milestone",
  sha,
  expanded,
})
const pendingRow = (sha: string): RowDescriptor => ({ kind: "pending-save", sha })
const saveRow = (sha: string, milestoneSha: string): RowDescriptor => ({
  kind: "save",
  sha,
  milestoneSha,
})
const placeholderRow = (milestoneSha: string): RowDescriptor => ({
  kind: "placeholder",
  sha: milestoneSha,
  milestoneSha,
})

// Canonical forest: trunk (7-entry spine, root R0) with feature/a forked at T3
// and feature/b forked at T4. Spine lengths keep the server's claim order
// (length DESC, name ASC) consistent with the recorded fork_of values.
const trunkEntries = [
  entry("T6", 2),
  entry("T5", 0),
  entry("T4", 1),
  entry("T3", 1),
  entry("T2", 2),
  entry("T1", 1),
  root("R0"),
]
const forestGraph: GitGraphResponse = {
  working_branch: "trunk",
  order: ["trunk", "feature/a", "feature/b"],
  branches: [
    branch("trunk", trunkEntries, { is_current: true }),
    branch(
      "feature/a",
      [entry("A2", 1), entry("A1", 1), entry("T3", 1), entry("T2", 2), entry("T1", 1), root("R0")],
      { fork_point_sha: "T3", fork_of: "trunk" },
    ),
    branch(
      "feature/b",
      [entry("B1", 0), entry("T4", 1), entry("T3", 1), entry("T2", 2), entry("T1", 1), root("R0")],
      { fork_point_sha: "T4", fork_of: "trunk" },
    ),
  ],
}

const trunkMilestoneRows = ["T6", "T5", "T4", "T3", "T2", "T1", "R0"].map((s) => milestone(s))

describe("computeGitGraphLayout — linear spine with departures (trunk view)", () => {
  const view: GitGraphView = {
    viewBranch: null,
    rows: [pendingRow("P2"), pendingRow("P1"), ...trunkMilestoneRows],
  }
  const rail = computeGitGraphLayout(forestGraph, view)

  it("resolves the working branch and aligns rows 1:1", () => {
    expect(rail.viewBranch).toBe("trunk")
    expect(rail.viewedIsArchived).toBe(false)
    expect(rail.rows).toHaveLength(view.rows.length)
    expect(rail.laneCount).toBe(3)
    expect(rail.overflowCount).toBe(0)
    expect(rail.topChips).toEqual([
      { branch: "feature/a", lane: 1, colorIndex: 1 },
      { branch: "feature/b", lane: 2, colorIndex: 2 },
    ])
  })

  it("draws pending saves as hollow dots on the viewed lane with departure passes", () => {
    expect(rail.rows[0].cells).toEqual([
      { kind: "hollow-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "P2" },
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "feature/b", colorIndex: 2 },
    ])
    expect(rail.rows[0].magnifier).toBeUndefined()
  })

  it("puts milestone dots on lane 0 and magnifiers on collapsed folded edges only", () => {
    expect(rail.rows[2]).toEqual({
      cells: [
        { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T6", terminal: false },
        { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
        { kind: "pass", lane: 2, branch: "feature/b", colorIndex: 2 },
      ],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0 },
    })
    // Zero-fold milestone: collapsed edge but nothing hidden — no magnifier.
    expect(rail.rows[3].magnifier).toBeUndefined()
    expect(rail.rows[7].magnifier).toEqual({ expandsSha: "T1", lane: 0, colorIndex: 0 })
  })

  it("curves departures out at their fork rows (magnifier still allowed there)", () => {
    expect(rail.rows[4]).toEqual({
      cells: [
        { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T4", terminal: false },
        { kind: "curve-out", fromLane: 0, toLane: 2, branch: "feature/b", colorIndex: 2 },
        { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      ],
      magnifier: { expandsSha: "T4", lane: 0, colorIndex: 0 },
    })
    expect(rail.rows[5]).toEqual({
      cells: [
        { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T3", terminal: false },
        { kind: "curve-out", fromLane: 0, toLane: 1, branch: "feature/a", colorIndex: 1 },
      ],
      magnifier: { expandsSha: "T3", lane: 0, colorIndex: 0 },
    })
    // Below both fork rows no departure lane is active any more.
    expect(rail.rows[6].cells).toEqual([
      { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T2", terminal: false },
    ])
  })

  it("terminates the line at the root milestone and never magnifies the window-final row", () => {
    expect(rail.rows[8]).toEqual({
      cells: [{ kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "R0", terminal: true }],
    })
  })
})

describe("computeGitGraphLayout — expansion lifecycle", () => {
  it("expanded saves draw save-dots on the milestone's lane and drop its magnifier", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [
        milestone("T6", true),
        saveRow("S1", "T6"),
        saveRow("S2", "T6"),
        ...trunkMilestoneRows.slice(1),
      ],
    })
    expect(rail.rows[0].magnifier).toBeUndefined()
    expect(rail.rows[1].cells).toEqual([
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S1" },
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "feature/b", colorIndex: 2 },
    ])
    // Other collapsed folded edges keep their magnifiers.
    expect(rail.rows[4].magnifier).toEqual({ expandsSha: "T4", lane: 0, colorIndex: 0 })
  })

  it("a placeholder row keeps the lane continuous and suppresses the magnifier", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), placeholderRow("T6"), ...trunkMilestoneRows.slice(1)],
    })
    expect(rail.rows[0].magnifier).toBeUndefined()
    expect(rail.rows[1].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "feature/b", colorIndex: 2 },
    ])
  })

  it("emits no spine cell on rows below the terminal (root) milestone", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [...trunkMilestoneRows.slice(0, 6), milestone("R0", true), placeholderRow("R0")],
    })
    expect(rail.rows[6].cells).toEqual([
      { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "R0", terminal: true },
    ])
    // The line terminated at the root dot — its placeholder row draws nothing.
    expect(rail.rows[7].cells).toEqual([])
  })
})

describe("computeGitGraphLayout — peeking a fork (ownership transition)", () => {
  const rail = computeGitGraphLayout(forestGraph, {
    viewBranch: "feature/a",
    rows: ["A2", "A1", "T3", "T2", "T1", "R0"].map((s) => milestone(s)),
  })

  it("owns the pre-fork segment via the parent and transitions at the fork-point row", () => {
    expect(rail.laneCount).toBe(2)
    expect(rail.rows[0].cells).toEqual([
      { kind: "dot", lane: 0, branch: "feature/a", colorIndex: 1, sha: "A2", terminal: false },
    ])
    // The fork-point commit itself belongs to the parent: the dot lands on the
    // trunk lane and the viewed line curves over on this row.
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "T3", terminal: false },
    ])
    expect(rail.rows[5].cells).toEqual([
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "R0", terminal: true },
    ])
  })

  it("magnifies the transition edge and keeps segment colours on their owners", () => {
    // A1 is collapsed with folds and its lower edge IS the lane transition.
    expect(rail.rows[1].magnifier).toEqual({ expandsSha: "A1", lane: 0, colorIndex: 1 })
    expect(rail.rows[2].magnifier).toEqual({ expandsSha: "T3", lane: 1, colorIndex: 0 })
  })

  it("counts branches whose fork point is outside the visible spine as overflow", () => {
    // feature/b forked at T4, which is not on feature/a's spine.
    expect(rail.overflowCount).toBe(1)
    expect(rail.topChips).toEqual([])
  })

  it("keeps colour indices stable across peeks (order-derived, not lane-derived)", () => {
    const trunkView = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: trunkMilestoneRows,
    })
    const trunkDot = trunkView.rows[0].cells[0]
    const peekedTrunkDot = rail.rows[2].cells[1]
    expect(trunkDot.kind).toBe("dot")
    expect(trunkDot.colorIndex).toBe(0)
    expect(peekedTrunkDot.colorIndex).toBe(0)
    const chip = trunkView.topChips.find((c) => c.branch === "feature/a")
    expect(chip?.colorIndex).toBe(1)
    expect(rail.rows[0].cells[0].colorIndex).toBe(1)
  })
})

describe("computeGitGraphLayout — crystallized fork", () => {
  // hotfix was forked at a pending save of feature/a while its tip was A1: its
  // anchor milestone X folds the pre-fork pending saves and its first parent
  // is A1, so hotfix attaches at feature/a's (now advanced past A1) spine.
  const crystalGraph: GitGraphResponse = {
    working_branch: "trunk",
    order: ["trunk", "feature/a", "hotfix"],
    branches: [
      branch(
        "trunk",
        [entry("T4", 1), entry("T3", 0), entry("T2", 1), entry("T1", 1), root("R0")],
        { is_current: true },
      ),
      branch("feature/a", [entry("A2", 0), entry("A1", 1), entry("T1", 1), root("R0")], {
        fork_point_sha: "T1",
        fork_of: "trunk",
      }),
      branch(
        "hotfix",
        [entry("X", 2, { parents: ["A1", "S9"] }), entry("A1", 1), entry("T1", 1), root("R0")],
        { fork_point_sha: "A1", fork_of: "feature/a" },
      ),
    ],
  }
  const rail = computeGitGraphLayout(crystalGraph, {
    viewBranch: "hotfix",
    rows: ["X", "A1", "T1", "R0"].map((s) => milestone(s)),
  })

  it("chains transitions down the fork-of-fork ancestry", () => {
    expect(rail.laneCount).toBe(3)
    // Lanes follow graph.order among the chain: trunk (order 0) → lane 1,
    // feature/a (order 1) → lane 2.
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 2,
        branch: "feature/a",
        colorIndex: 1,
        fromColorIndex: 2,
      },
      { kind: "dot", lane: 2, branch: "feature/a", colorIndex: 1, sha: "A1", terminal: false },
    ])
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "transition",
        fromLane: 2,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "T1", terminal: false },
    ])
    expect(rail.rows[3].cells).toEqual([
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "R0", terminal: true },
    ])
  })

  it("magnifies the crystallized anchor's transition edge (A-4)", () => {
    // X (collapsed, folds the pre-fork pending saves) sits exactly on the
    // hotfix → feature/a transition edge and MUST carry the magnifier.
    expect(rail.rows[0]).toEqual({
      cells: [
        { kind: "dot", lane: 0, branch: "hotfix", colorIndex: 2, sha: "X", terminal: false },
      ],
      magnifier: { expandsSha: "X", lane: 0, colorIndex: 2 },
    })
    expect(rail.rows[1].magnifier).toEqual({ expandsSha: "A1", lane: 2, colorIndex: 1 })
  })
})

describe("computeGitGraphLayout — departures", () => {
  it("draws two children off one commit in distinct lanes", () => {
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk", "kid-a", "kid-b"],
      branches: [
        branch("trunk", [entry("T2", 1), entry("T1", 1), root("R0")], { is_current: true }),
        branch("kid-a", [entry("T1", 1), root("R0")], { fork_point_sha: "T1", fork_of: "trunk" }),
        branch("kid-b", [entry("T1", 1), root("R0")], { fork_point_sha: "T1", fork_of: "trunk" }),
      ],
    }
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["T2", "T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.laneCount).toBe(3)
    expect(rail.rows[1]).toEqual({
      cells: [
        { kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T1", terminal: false },
        { kind: "curve-out", fromLane: 0, toLane: 1, branch: "kid-a", colorIndex: 1 },
        { kind: "curve-out", fromLane: 0, toLane: 2, branch: "kid-b", colorIndex: 2 },
      ],
      magnifier: { expandsSha: "T1", lane: 0, colorIndex: 0 },
    })
    expect(rail.topChips).toHaveLength(2)
  })

  it("curves a departure out of a parent-owned (chain-lane) row", () => {
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk", "feature/a", "gadget"],
      branches: [
        branch("trunk", [entry("T3", 1), entry("T2", 1), entry("T1", 1), root("R0")], {
          is_current: true,
        }),
        branch("feature/a", [entry("A1", 1), entry("T1", 1), root("R0")], {
          fork_point_sha: "T1",
          fork_of: "trunk",
        }),
        branch("gadget", [entry("T1", 1), root("R0")], {
          fork_point_sha: "T1",
          fork_of: "trunk",
        }),
      ],
    }
    const rail = computeGitGraphLayout(graph, {
      viewBranch: "feature/a",
      rows: ["A1", "T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.laneCount).toBe(3)
    // Above the transition only the departure lane passes — the trunk lane is
    // not active yet.
    expect(rail.rows[0].cells).toEqual([
      { kind: "dot", lane: 0, branch: "feature/a", colorIndex: 1, sha: "A1", terminal: false },
      { kind: "pass", lane: 2, branch: "gadget", colorIndex: 2 },
    ])
    // At the fork row the departure leaves from the spine's CURRENT lane (the
    // trunk chain lane the transition just landed on).
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "T1", terminal: false },
      { kind: "curve-out", fromLane: 1, toLane: 2, branch: "gadget", colorIndex: 2 },
    ])
    expect(rail.topChips).toEqual([{ branch: "gadget", lane: 2, colorIndex: 2 }])
  })
})

describe("computeGitGraphLayout — archived branches", () => {
  const graph: GitGraphResponse = {
    working_branch: "trunk",
    order: ["trunk", "old"],
    branches: [
      branch("trunk", [entry("T2", 1), entry("T1", 1), root("R0")], { is_current: true }),
      branch("old", [entry("T1", 1), root("R0")], {
        fork_point_sha: "T1",
        fork_of: "trunk",
        is_archived: true,
      }),
    ],
  }

  it("hides archived departures entirely (no chip, no overflow)", () => {
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["T2", "T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.laneCount).toBe(1)
    expect(rail.topChips).toEqual([])
    expect(rail.overflowCount).toBe(0)
    expect(rail.rows[1].cells.some((c) => c.kind === "curve-out")).toBe(false)
  })

  it("still draws the rail when the archived branch itself is viewed, flagged greyed", () => {
    const rail = computeGitGraphLayout(graph, {
      viewBranch: "old",
      rows: ["T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.viewedIsArchived).toBe(true)
    expect(rail.laneCount).toBe(2)
    // The viewed branch has no own milestones: the whole spine belongs to the
    // parent and the viewed lane contributes only the transition stub.
    expect(rail.rows[0].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      { kind: "dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "T1", terminal: false },
    ])
  })
})

describe("computeGitGraphLayout — forest, lane cap, truncation", () => {
  it("treats an independent root as overflow (fork forest)", () => {
    const graph: GitGraphResponse = {
      working_branch: "alpha",
      order: ["alpha", "beta"],
      branches: [
        branch("alpha", [entry("A1", 1), root("RA")], { is_current: true }),
        branch("beta", [entry("B1", 1), root("RB")]),
      ],
    }
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["A1", "RA"].map((s) => milestone(s)),
    })
    expect(rail.laneCount).toBe(1)
    expect(rail.overflowCount).toBe(1)
    expect(rail.topChips).toEqual([])
  })

  it("caps departures by order priority and counts the dropped ones", () => {
    const forkAt = ["T2", "T3", "T4", "T5", "T6", "T2", "T3", "T4"]
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
      branches: [
        branch("trunk", trunkEntries, { is_current: true }),
        ...forkAt.map((sha, i) =>
          branch(`c${i + 1}`, [entry(sha, 1), root("R0")], {
            fork_point_sha: sha,
            fork_of: "trunk",
          }),
        ),
      ],
    }
    const view: GitGraphView = { viewBranch: null, rows: trunkMilestoneRows }

    expect(DEFAULT_LANE_CAP).toBe(5)
    const capped = computeGitGraphLayout(graph, view)
    expect(capped.laneCount).toBe(5)
    expect(capped.overflowCount).toBe(4)
    expect(capped.topChips.map((c) => c.branch)).toEqual(["c1", "c2", "c3", "c4"])

    const tight = computeGitGraphLayout(graph, { ...view, laneCap: 2 })
    expect(tight.laneCount).toBe(2)
    expect(tight.overflowCount).toBe(7)
    expect(tight.topChips.map((c) => c.branch)).toEqual(["c1"])

    const roomy = computeGitGraphLayout(graph, { ...view, laneCap: 42 })
    expect(roomy.laneCount).toBe(9)
    expect(roomy.overflowCount).toBe(0)
    // Colour indices wrap modulo the 8-colour palette: c8 is order index 8.
    expect(roomy.topChips.map((c) => [c.branch, c.lane, c.colorIndex])).toEqual([
      ["c1", 1, 1],
      ["c2", 2, 2],
      ["c3", 3, 3],
      ["c4", 4, 4],
      ["c5", 5, 5],
      ["c6", 6, 6],
      ["c7", 7, 7],
      ["c8", 8, 0],
    ])
  })

  it("handles a truncated window: no terminal dot, no window-final magnifier", () => {
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk", "deep"],
      branches: [
        branch("trunk", [entry("T9", 1), entry("T8", 2), entry("T7", 3)], {
          is_current: true,
          truncated: true,
        }),
        // Fork point reported even though it lies beyond the window.
        branch("deep", [entry("D1", 1), entry("T2", 1)], {
          fork_point_sha: "T2",
          fork_of: "trunk",
          truncated: true,
        }),
      ],
    }
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["T9", "T8", "T7"].map((s) => milestone(s)),
    })
    expect(rail.overflowCount).toBe(1)
    expect(rail.rows[0].magnifier).toEqual({ expandsSha: "T9", lane: 0, colorIndex: 0 })
    expect(rail.rows[1].magnifier).toEqual({ expandsSha: "T8", lane: 0, colorIndex: 0 })
    // Window-final row: folded saves exist but there is no lower row in view.
    expect(rail.rows[2]).toEqual({
      cells: [{ kind: "dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "T7", terminal: false }],
    })
  })
})

describe("computeGitGraphLayout — degraded inputs", () => {
  const empty = {
    rows: [],
    laneCount: 0,
    overflowCount: 0,
    topChips: [],
    viewBranch: null,
    viewedIsArchived: false,
  }

  it("returns an empty rail when no branch can be resolved", () => {
    const graph: GitGraphResponse = { working_branch: null, order: [], branches: [] }
    expect(computeGitGraphLayout(graph, { viewBranch: null, rows: [] })).toEqual(empty)
  })

  it("returns an empty rail for an unknown view branch", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: "no-such-branch",
      rows: trunkMilestoneRows,
    })
    expect(rail).toEqual(empty)
  })

  it("returns an empty rail when the viewed branch has no entries", () => {
    const graph: GitGraphResponse = {
      working_branch: "bare",
      order: ["bare"],
      branches: [branch("bare", [], { is_current: true })],
    }
    expect(computeGitGraphLayout(graph, { viewBranch: null, rows: [] })).toEqual(empty)
  })
})
