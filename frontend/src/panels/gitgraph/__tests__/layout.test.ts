import { describe, expect, it } from "vitest"
import type { GitGraphBranch, GitGraphEntry, GitGraphResponse } from "../../../api/types"
import {
  MAGNIFIER_GUTTER,
  SLOT_FLARE_WIDTH,
  SLOT_TIGHT_WIDTH,
  computeGitGraphLayout,
  laneX,
  railWidth,
  slotFlareX,
  slotTightX,
} from "../layout"
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
  fork_source_sha: null,
  fork_credit_sha: null,
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
// and feature/b forked at T4. Neither fork records a source save, so both
// anchor at their fork-point milestone rows.
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

  it("resolves the working branch, aligns rows 1:1 and lanes only the viewed spine", () => {
    expect(rail.viewBranch).toBe("trunk")
    expect(rail.viewedIsArchived).toBe(false)
    expect(rail.rows).toHaveLength(view.rows.length)
    // Departing branches draw NO lanes any more — only the viewed branch and
    // its ancestors get one. Trunk has no ancestors.
    expect(rail.laneCount).toBe(1)
    expect(rail.lanes).toEqual([{ branch: "trunk", lane: 0, colorIndex: 0 }])
    // Each departure group (one branch per fork point) needs one slot.
    expect(rail.slotCount).toBe(1)
    expect(rail.overflowCount).toBe(0)
    expect(rail.topChips).toEqual([
      { branch: "feature/a", colorIndex: 1, archived: false },
      { branch: "feature/b", colorIndex: 2, archived: false },
    ])
  })

  it("draws pending saves as hollow dots on the viewed lane", () => {
    expect(rail.rows[0]).toEqual({
      cells: [{ kind: "hollow-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "P2" }],
    })
    expect(rail.rows[1]).toEqual({
      cells: [{ kind: "hollow-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "P1" }],
    })
  })

  it("puts milestone dots on lane 0 with magnifiers on collapsed fold-carrying rows only", () => {
    expect(rail.rows[2]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T6",
          terminal: false,
          upperDotted: false,
          lowerDotted: true,
        },
      ],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: false },
    })
    // Zero-fold milestone (single parent): nothing hidden — no magnifier and
    // no dotted lower segment; its upper segment inherits T6's hidden fold.
    expect(rail.rows[3]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T5",
          terminal: false,
          upperDotted: true,
          lowerDotted: false,
        },
      ],
    })
    expect(rail.rows[7].magnifier).toEqual({
      expandsSha: "T1",
      lane: 0,
      colorIndex: 0,
      expanded: false,
    })
  })

  it("emits spawn stubs (not lanes) at the fork-point rows of departing branches", () => {
    expect(rail.rows[4].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "T4",
        terminal: false,
        upperDotted: false, // T5 above is zero-fold — nothing hidden there
        lowerDotted: true, // T4 folds saves and is collapsed
      },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: false,
        slot: 0,
        branch: "feature/b",
        colorIndex: 2,
        archived: false,
        count: 1,
      },
    ])
    expect(rail.rows[5].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "T3",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: false,
        slot: 0,
        branch: "feature/a",
        colorIndex: 1,
        archived: false,
        count: 1,
      },
    ])
    // Below both fork rows nothing extra is drawn.
    expect(rail.rows[6].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "T2",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
    ])
  })

  it("terminates the line at the root: no window-final magnifier, never lowerDotted", () => {
    expect(rail.rows[8]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "R0",
          terminal: true,
          upperDotted: true, // T1 above still hides its fold
          lowerDotted: false, // nothing exists below a terminal dot
        },
      ],
    })
  })

  it("sizes the rail from the magnifier gutter + lanes + the widest slot group", () => {
    expect(railWidth(rail.laneCount, rail.slotCount)).toBe(MAGNIFIER_GUTTER + 1 * 12 + 1 * 13 + 8)
    expect(railWidth(1, 1)).toBe(47)
    expect(railWidth(1, 2)).toBe(60)
    expect(railWidth(1, 0)).toBe(34)
    // Stub knees flare wide; their dotted tails converge to a tight pitch.
    expect(SLOT_FLARE_WIDTH).toBe(13)
    expect(SLOT_TIGHT_WIDTH).toBe(5)
    expect(laneX(0)).toBe(MAGNIFIER_GUTTER + 4 + 6)
    expect(slotFlareX(0, 1)).toBe(MAGNIFIER_GUTTER + 4 + 12 + 13)
    expect(slotTightX(0, 1)).toBe(MAGNIFIER_GUTTER + 4 + 12 + 4)
    expect(slotTightX(1, 1)).toBe(slotTightX(0, 1) + SLOT_TIGHT_WIDTH)
  })
})

describe("computeGitGraphLayout — dotted spine segments (folded-away material)", () => {
  it("a collapsed fold-carrying milestone dots its lower segment and the next row's upper", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6"), milestone("T5")],
    })
    expect(rail.rows[0].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T6",
      upperDotted: false, // nothing precedes the first milestone row
      lowerDotted: true,
    })
    expect(rail.rows[1].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T5",
      upperDotted: true, // the same hidden fold, seen from below
      lowerDotted: false,
    })
  })

  it("expanding the milestone flips both segments solid (the material is now visible)", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), saveRow("S1", "T6"), milestone("T5")],
    })
    expect(rail.rows[0].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T6",
      upperDotted: false,
      lowerDotted: false,
    })
    expect(rail.rows[2].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T5",
      upperDotted: false,
      lowerDotted: false,
    })
  })

  it("a transition below a collapsed fold-carrying milestone is dotted", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: "feature/a",
      rows: ["A2", "A1", "T3", "T2", "T1", "R0"].map((s) => milestone(s)),
    })
    // A1 (collapsed fold) sits directly above the fork-point transition.
    expect(rail.rows[2].cells[0]).toMatchObject({ kind: "transition", dotted: true })
  })

  it("a transition below an EXPANDED fold-carrying milestone is solid", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: "feature/a",
      rows: [
        milestone("A2"),
        milestone("A1", true),
        saveRow("Ax", "A1"),
        milestone("T3"),
        milestone("T2"),
        milestone("T1"),
        milestone("R0"),
      ],
    })
    expect(rail.rows[3].cells[0]).toMatchObject({ kind: "transition", dotted: false })
  })
})

describe("computeGitGraphLayout — sub-rail (expanded saves)", () => {
  it("draws pass + save-dot on the sub-rail and fold-in/fold-out around the range", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [
        milestone("T6", true),
        saveRow("S1", "T6"),
        saveRow("S2", "T6"),
        ...trunkMilestoneRows.slice(1),
      ],
    })
    // The expanded milestone folds the range out of its dot…
    expect(rail.rows[0]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T6",
          terminal: false,
          upperDotted: false,
          lowerDotted: false, // open range: the material is on screen
        },
        { kind: "fold-in", lane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: true },
    })
    // …save rows ride the sub-rail beside a spine pass…
    expect(rail.rows[1].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S1", last: false },
    ])
    // …the final save is NOT `last` when a milestone row follows (the line
    // continues down into that milestone's fold-out)…
    expect(rail.rows[2].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S2", last: false },
    ])
    // …and the next milestone merges the sub-rail back into its dot (same
    // owner both sides here: fromLane === lane).
    expect(rail.rows[3]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T5",
          terminal: false,
          upperDotted: false,
          lowerDotted: false,
        },
        { kind: "fold-out", lane: 0, fromLane: 0, branch: "trunk", colorIndex: 0 },
      ],
    })
  })

  it("marks the range-final save `last` when no save or milestone row follows", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), saveRow("S1", "T6"), saveRow("S2", "T6")],
    })
    expect(rail.rows[2].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S2", last: true },
    ])
    // The expanded milestone keeps its magnifier even as the window-final
    // range (the collapsed-only lower-edge rule does not apply).
    expect(rail.rows[0].magnifier).toEqual({
      expandsSha: "T6",
      lane: 0,
      colorIndex: 0,
      expanded: true,
    })
  })

  it("chains the sub-rail straight through a doubly-expanded milestone (fold-in + fold-out)", () => {
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk"],
      branches: [
        branch("trunk", [entry("M3", 2), entry("M2", 2), entry("M1", 1), root("R0")], {
          is_current: true,
        }),
      ],
    }
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: [
        milestone("M3", true),
        saveRow("Sa", "M3"),
        saveRow("Sb", "M3"),
        milestone("M2", true),
        saveRow("Sc", "M2"),
        milestone("M1"),
        milestone("R0"),
      ],
    })
    // M2 has saves directly above AND below: both curves on one row.
    expect(rail.rows[3]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "M2",
          terminal: false,
          upperDotted: false,
          lowerDotted: false,
        },
        { kind: "fold-in", lane: 0, branch: "trunk", colorIndex: 0 },
        { kind: "fold-out", lane: 0, fromLane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "M2", lane: 0, colorIndex: 0, expanded: true },
    })
    // M1 only closes the range above it — and, still collapsed with a fold of
    // its own, dots its lower segment.
    expect(rail.rows[5]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "M1",
          terminal: false,
          upperDotted: false,
          lowerDotted: true,
        },
        { kind: "fold-out", lane: 0, fromLane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "M1", lane: 0, colorIndex: 0, expanded: false },
    })
  })

  it("a placeholder row keeps the spine continuous without a save dot or fold-in", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), placeholderRow("T6"), ...trunkMilestoneRows.slice(1)],
    })
    // Placeholder is not a save row: no fold-in above it, magnifier expanded.
    expect(rail.rows[0]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T6",
          terminal: false,
          upperDotted: false,
          lowerDotted: false,
        },
      ],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: true },
    })
    expect(rail.rows[1].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
    ])
  })

  it("emits no spine cell on rows below the terminal (root) milestone", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [...trunkMilestoneRows.slice(0, 6), milestone("R0", true), placeholderRow("R0")],
    })
    expect(rail.rows[6].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "R0",
        terminal: true,
        upperDotted: true, // T1 above is a collapsed fold
        lowerDotted: false, // terminal: nothing below, never dotted
      },
    ])
    // The line terminated at the root dot — its placeholder row draws nothing.
    expect(rail.rows[7].cells).toEqual([])
  })
})

describe("computeGitGraphLayout — ancestor lanes (peeking a fork)", () => {
  const rail = computeGitGraphLayout(forestGraph, {
    viewBranch: "feature/a",
    rows: ["A2", "A1", "T3", "T2", "T1", "R0"].map((s) => milestone(s)),
  })

  it("assigns lane 0 to the viewed branch and lane 1 to its ancestor", () => {
    expect(rail.laneCount).toBe(2)
    expect(rail.lanes).toEqual([
      { branch: "feature/a", lane: 0, colorIndex: 1 },
      { branch: "trunk", lane: 1, colorIndex: 0 },
    ])
  })

  it("runs the ancestor lane to the very top as pass cells above its first owned row", () => {
    expect(rail.rows[0].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "feature/a",
        colorIndex: 1,
        sha: "A2",
        terminal: false,
        upperDotted: false,
        lowerDotted: true,
      },
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0 },
    ])
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "feature/a",
        colorIndex: 1,
        sha: "A1",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0 },
    ])
  })

  it("owns the pre-fork segment via the parent and transitions at the fork-point row", () => {
    // The fork-point commit itself belongs to the parent: the dot lands on the
    // trunk lane and the viewed line curves over on this row — no upper trunk
    // pass here (the lane IS the spine from this row down).
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
        dotted: true, // A1 above is a collapsed fold — the curve hides it
      },
      {
        kind: "dot",
        lane: 1,
        branch: "trunk",
        colorIndex: 0,
        sha: "T3",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
    ])
    expect(rail.rows[5].cells).toEqual([
      {
        kind: "dot",
        lane: 1,
        branch: "trunk",
        colorIndex: 0,
        sha: "R0",
        terminal: true,
        upperDotted: true,
        lowerDotted: false,
      },
    ])
  })

  it("magnifies the transition row on the owner's lane and colour", () => {
    expect(rail.rows[1].magnifier).toEqual({
      expandsSha: "A1",
      lane: 0,
      colorIndex: 1,
      expanded: false,
    })
    expect(rail.rows[2].magnifier).toEqual({
      expandsSha: "T3",
      lane: 1,
      colorIndex: 0,
      expanded: false,
    })
  })

  it("counts departures whose anchor is outside the visible spine as overflow", () => {
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

  it("save rows under an ancestor-owned milestone ride the ancestor's lane", () => {
    const expanded = computeGitGraphLayout(forestGraph, {
      viewBranch: "feature/a",
      rows: [
        milestone("A2"),
        milestone("A1"),
        milestone("T3", true),
        saveRow("Tx", "T3"),
        milestone("T2"),
        milestone("T1"),
        milestone("R0"),
      ],
    })
    expect(expanded.rows[3].cells).toEqual([
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0 },
      { kind: "save-dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "Tx", last: false },
    ])
    expect(expanded.rows[4].cells).toEqual([
      {
        kind: "dot",
        lane: 1,
        branch: "trunk",
        colorIndex: 0,
        sha: "T2",
        terminal: false,
        upperDotted: false, // T3 above is expanded — its material is visible
        lowerDotted: true,
      },
      { kind: "fold-out", lane: 1, fromLane: 1, branch: "trunk", colorIndex: 0 },
    ])
  })
})

describe("computeGitGraphLayout — nearest-ancestor-first lanes (fork of a fork)", () => {
  // hotfix forked off feature/a (at A1), which itself forked off trunk (at T1):
  // lanes are discovered walking DOWN the viewed spine, so the nearest
  // ancestor (feature/a) takes lane 1 and trunk lane 2 — the spine migrates
  // monotonically outward, never crossing back over a lane.
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

  it("orders lanes by spine discovery (nearest ancestor first), not graph order", () => {
    expect(rail.laneCount).toBe(3)
    expect(rail.lanes).toEqual([
      { branch: "hotfix", lane: 0, colorIndex: 2 },
      { branch: "feature/a", lane: 1, colorIndex: 1 },
      { branch: "trunk", lane: 2, colorIndex: 0 },
    ])
  })

  it("migrates the spine monotonically outward through chained transitions", () => {
    expect(rail.rows[0].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "hotfix",
        colorIndex: 2,
        sha: "X",
        terminal: false,
        upperDotted: false,
        lowerDotted: true,
      },
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0 },
    ])
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "feature/a",
        colorIndex: 1,
        fromColorIndex: 2,
        dotted: true, // X above hides its folded pre-fork saves
      },
      {
        kind: "dot",
        lane: 1,
        branch: "feature/a",
        colorIndex: 1,
        sha: "A1",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0 },
    ])
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "transition",
        fromLane: 1,
        toLane: 2,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
        dotted: true,
      },
      {
        kind: "dot",
        lane: 2,
        branch: "trunk",
        colorIndex: 0,
        sha: "T1",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
    ])
    expect(rail.rows[3].cells).toEqual([
      {
        kind: "dot",
        lane: 2,
        branch: "trunk",
        colorIndex: 0,
        sha: "R0",
        terminal: true,
        upperDotted: true,
        lowerDotted: false,
      },
    ])
  })

  it("magnifies the crystallized anchor's transition edge (A-4)", () => {
    expect(rail.rows[0].magnifier).toEqual({
      expandsSha: "X",
      lane: 0,
      colorIndex: 2,
      expanded: false,
    })
    expect(rail.rows[1].magnifier).toEqual({
      expandsSha: "A1",
      lane: 1,
      colorIndex: 1,
      expanded: false,
    })
  })

  it("a fold-out across an ownership transition departs from the RANGE owner's lane", () => {
    // A1 (owned by feature/a, lane 1) expanded directly above T1 (owned by
    // trunk, lane 2): the sub-rail ran on feature/a's lane, so the fold-out
    // into T1's dot must depart from lane 1 in feature/a's colour, landing on
    // lane 2 — otherwise the line dies at the ownership boundary.
    const expanded = computeGitGraphLayout(crystalGraph, {
      viewBranch: "hotfix",
      rows: [
        milestone("X"),
        milestone("A1", true),
        saveRow("As", "A1"),
        milestone("T1"),
        milestone("R0"),
      ],
    })
    // The expanded ancestor milestone opens the range on its own lane.
    expect(expanded.rows[1].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "feature/a",
        colorIndex: 1,
        fromColorIndex: 2,
        dotted: true,
      },
      {
        kind: "dot",
        lane: 1,
        branch: "feature/a",
        colorIndex: 1,
        sha: "A1",
        terminal: false,
        upperDotted: true,
        lowerDotted: false, // open — its material is on screen
      },
      { kind: "fold-in", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0 },
    ])
    expect(expanded.rows[2].cells).toEqual([
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "save-dot", lane: 1, branch: "feature/a", colorIndex: 1, sha: "As", last: false },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0 },
    ])
    // The next milestone belongs to trunk: fold-out departs the RANGE's lane
    // (feature/a, lane 1) and wears the range owner's colour.
    expect(expanded.rows[3].cells).toEqual([
      {
        kind: "transition",
        fromLane: 1,
        toLane: 2,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
        dotted: false, // A1 above is expanded
      },
      {
        kind: "dot",
        lane: 2,
        branch: "trunk",
        colorIndex: 0,
        sha: "T1",
        terminal: false,
        upperDotted: false,
        lowerDotted: true,
      },
      { kind: "fold-out", lane: 2, fromLane: 1, branch: "feature/a", colorIndex: 1 },
    ])
  })
})

describe("computeGitGraphLayout — spawn-stub anchor cascade", () => {
  // trunk spine M2 (folds S1+S2), M1, R0. Departures exercising each anchor
  // rule: a save-sourced fork (live-src), a pending-save fork (pend-kid), a
  // plain fork-point fork (point-only), an off-window fork (elsewhere) and an
  // off-window ARCHIVED fork (ghost — drops silently).
  const cascadeGraph: GitGraphResponse = {
    working_branch: "trunk",
    order: ["trunk", "live-src", "pend-kid", "point-only", "elsewhere", "ghost"],
    branches: [
      branch("trunk", [entry("M2", 2), entry("M1", 1), root("R0")], { is_current: true }),
      branch("live-src", [entry("L1", 1)], {
        fork_point_sha: "M1",
        fork_of: "trunk",
        fork_source_sha: "S1",
        fork_credit_sha: "M2",
      }),
      branch("pend-kid", [entry("K1", 1)], {
        fork_point_sha: "M2",
        fork_of: "trunk",
        fork_source_sha: "P1",
        fork_credit_sha: null,
      }),
      branch("point-only", [entry("Q1", 1)], { fork_point_sha: "M1", fork_of: "trunk" }),
      branch("elsewhere", [entry("E1", 1)], { fork_point_sha: "ZZ", fork_of: "trunk" }),
      branch("ghost", [entry("G1", 1)], {
        fork_point_sha: "ZZ",
        fork_of: "trunk",
        is_archived: true,
      }),
    ],
  }
  const collapsedRows = [pendingRow("P1"), milestone("M2"), milestone("M1"), milestone("R0")]

  it("anchors at the credit milestone while the source save is folded away", () => {
    const rail = computeGitGraphLayout(cascadeGraph, { viewBranch: null, rows: collapsedRows })
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "M2",
        terminal: false,
        upperDotted: false,
        lowerDotted: true,
      },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: false,
        slot: 0,
        branch: "live-src",
        colorIndex: 1,
        archived: false,
        count: 1,
      },
    ])
  })

  it("anchors on the visible pending-save row for a pending-save fork", () => {
    const rail = computeGitGraphLayout(cascadeGraph, { viewBranch: null, rows: collapsedRows })
    expect(rail.rows[0].cells).toEqual([
      { kind: "hollow-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "P1" },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: false,
        slot: 0,
        branch: "pend-kid",
        colorIndex: 2,
        archived: false,
        count: 1,
      },
    ])
  })

  it("falls back to the fork-point row when no save or credit is available", () => {
    const rail = computeGitGraphLayout(cascadeGraph, { viewBranch: null, rows: collapsedRows })
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "dot",
        lane: 0,
        branch: "trunk",
        colorIndex: 0,
        sha: "M1",
        terminal: false,
        upperDotted: true,
        lowerDotted: true,
      },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: false,
        slot: 0,
        branch: "point-only",
        colorIndex: 3,
        archived: false,
        count: 1,
      },
    ])
  })

  it("counts unresolvable live departures as overflow; unresolvable archived drop silently", () => {
    const rail = computeGitGraphLayout(cascadeGraph, { viewBranch: null, rows: collapsedRows })
    expect(rail.overflowCount).toBe(1) // elsewhere only — ghost never counts
    expect(rail.topChips).toEqual([
      { branch: "live-src", colorIndex: 1, archived: false },
      { branch: "pend-kid", colorIndex: 2, archived: false },
      { branch: "point-only", colorIndex: 3, archived: false },
    ])
    expect(rail.slotCount).toBe(1) // one departure per anchor group
  })

  it("moves a save-sourced stub onto its save row (sub-rail) once expanded", () => {
    const rail = computeGitGraphLayout(cascadeGraph, {
      viewBranch: null,
      rows: [
        pendingRow("P1"),
        milestone("M2", true),
        saveRow("S1", "M2"),
        saveRow("S2", "M2"),
        milestone("M1"),
        milestone("R0"),
      ],
    })
    // The milestone row no longer carries the stub…
    expect(rail.rows[1].cells.some((c) => c.kind === "spawn-stub")).toBe(false)
    // …the source save row does, curving off the SUB-rail.
    expect(rail.rows[2].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0 },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S1", last: false },
      {
        kind: "spawn-stub",
        fromLane: 0,
        fromSub: true,
        slot: 0,
        branch: "live-src",
        colorIndex: 1,
        archived: false,
        count: 1,
      },
    ])
    expect(rail.slotCount).toBe(1)
  })
})

describe("computeGitGraphLayout — slot reservation per anchor group", () => {
  // Two live branches spawned from two saves folded into the SAME milestone:
  // one group of two, whether collapsed (both on the milestone row) or open
  // (spread over the save rows) — the rail width must not change on expand.
  const groupGraph: GitGraphResponse = {
    working_branch: "trunk",
    order: ["trunk", "kid-a", "kid-b"],
    branches: [
      branch("trunk", [entry("M2", 2), entry("M1", 1), root("R0")], { is_current: true }),
      branch("kid-a", [entry("KA", 1)], {
        fork_point_sha: "M1",
        fork_of: "trunk",
        fork_source_sha: "S1",
        fork_credit_sha: "M2",
      }),
      branch("kid-b", [entry("KB", 1)], {
        fork_point_sha: "M1",
        fork_of: "trunk",
        fork_source_sha: "S2",
        fork_credit_sha: "M2",
      }),
    ],
  }

  const collapsed = computeGitGraphLayout(groupGraph, {
    viewBranch: null,
    rows: [milestone("M2"), milestone("M1"), milestone("R0")],
  })
  const expanded = computeGitGraphLayout(groupGraph, {
    viewBranch: null,
    rows: [
      milestone("M2", true),
      saveRow("S1", "M2"),
      saveRow("S2", "M2"),
      milestone("M1"),
      milestone("R0"),
    ],
  })

  it("stacks both stubs on the credit row while collapsed, in payload order", () => {
    const stubs = collapsed.rows[0].cells.filter((c) => c.kind === "spawn-stub")
    expect(stubs).toEqual([
      expect.objectContaining({ branch: "kid-a", slot: 0, fromSub: false }),
      expect.objectContaining({ branch: "kid-b", slot: 1, fromSub: false }),
    ])
    expect(collapsed.slotCount).toBe(2)
  })

  it("keeps each branch's slot and the total reservation stable across expansion", () => {
    expect(expanded.slotCount).toBe(collapsed.slotCount)
    const rowStub = (i: number) => expanded.rows[i].cells.find((c) => c.kind === "spawn-stub")
    expect(rowStub(1)).toEqual(
      expect.objectContaining({ branch: "kid-a", slot: 0, fromSub: true }),
    )
    expect(rowStub(2)).toEqual(
      expect.objectContaining({ branch: "kid-b", slot: 1, fromSub: true }),
    )
    // The milestone row itself carries neither stub once open.
    expect(expanded.rows[0].cells.some((c) => c.kind === "spawn-stub")).toBe(false)
  })
})

describe("computeGitGraphLayout — archived branches", () => {
  const graph: GitGraphResponse = {
    working_branch: "trunk",
    // Archived branches sit BETWEEN live ones in the payload order to prove
    // they never burn a palette slot.
    order: ["trunk", "old1", "old2", "kid"],
    branches: [
      branch("trunk", [entry("T2", 2), entry("T1", 1), root("R0")], { is_current: true }),
      branch("old1", [entry("O1", 1)], {
        fork_point_sha: "T1",
        fork_of: "trunk",
        is_archived: true,
      }),
      branch("old2", [entry("O2", 1)], {
        fork_point_sha: "T1",
        fork_of: "trunk",
        is_archived: true,
      }),
      branch("kid", [entry("K1", 1)], { fork_point_sha: "T1", fork_of: "trunk" }),
    ],
  }

  it("groups archived departures into ONE muted stub in the parent's colour", () => {
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["T2", "T1", "R0"].map((s) => milestone(s)),
    })
    const stubs = rail.rows[1].cells.filter((c) => c.kind === "spawn-stub")
    expect(stubs).toHaveLength(2) // one live + ONE shared archived stub
    expect(stubs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          archived: true,
          count: 2,
          slot: 1, // after the live departures of the group
          branch: "trunk", // labelled by the parent (right-click target)
          colorIndex: 0, // borrows the parent's colour
        }),
        expect.objectContaining({ branch: "kid", archived: false, slot: 0, count: 1 }),
      ]),
    )
    expect(rail.slotCount).toBe(2)
    expect(rail.overflowCount).toBe(0)
  })

  it("lists archived chips but assigns palette indexes over live branches only", () => {
    const rail = computeGitGraphLayout(graph, {
      viewBranch: null,
      rows: ["T2", "T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.topChips).toEqual([
      { branch: "old1", colorIndex: 0, archived: true },
      { branch: "old2", colorIndex: 0, archived: true },
      // kid is 4th in the payload order but 2nd among NON-archived branches.
      { branch: "kid", colorIndex: 1, archived: false },
    ])
  })

  it("still draws the rail when the archived branch itself is viewed, flagged greyed", () => {
    const viewedGraph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk", "old1"],
      branches: [
        branch("trunk", [entry("T2", 2), entry("T1", 1), root("R0")], { is_current: true }),
        // The viewed branch's own spine (fork point + shared history included).
        branch("old1", [entry("T1", 1), root("R0")], {
          fork_point_sha: "T1",
          fork_of: "trunk",
          is_archived: true,
        }),
      ],
    }
    const rail = computeGitGraphLayout(viewedGraph, {
      viewBranch: "old1",
      rows: ["T1", "R0"].map((s) => milestone(s)),
    })
    expect(rail.viewedIsArchived).toBe(true)
    expect(rail.laneCount).toBe(2)
    // The archived viewed branch borrows its parent's colour throughout.
    expect(rail.lanes[0]).toEqual({ branch: "old1", lane: 0, colorIndex: 0 })
    expect(rail.rows[0].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 0,
        dotted: false, // nothing precedes the first milestone row
      },
      {
        kind: "dot",
        lane: 1,
        branch: "trunk",
        colorIndex: 0,
        sha: "T1",
        terminal: false,
        upperDotted: false,
        lowerDotted: true,
      },
    ])
  })
})

describe("computeGitGraphLayout — magnifier rules", () => {
  it("requires >= 2 parents, and a following milestone row while collapsed", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6"), milestone("T5")],
    })
    // T6 folds saves and has a lower milestone row in-window.
    expect(rail.rows[0].magnifier).toEqual({
      expandsSha: "T6",
      lane: 0,
      colorIndex: 0,
      expanded: false,
    })
    // T5 is single-parent: never a magnifier.
    expect(rail.rows[1].magnifier).toBeUndefined()
  })

  it("suppresses the magnifier on a collapsed window-final row even when it folds saves", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6")],
    })
    expect(rail.rows[0].magnifier).toBeUndefined()
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
    expect(rail.rows[0].magnifier).toEqual({
      expandsSha: "T9",
      lane: 0,
      colorIndex: 0,
      expanded: false,
    })
    expect(rail.rows[1].magnifier).toEqual({
      expandsSha: "T8",
      lane: 0,
      colorIndex: 0,
      expanded: false,
    })
    // Window-final row: folded saves exist but there is no lower row in view.
    // Its lower segment still reads dotted — the fold is hidden either way —
    // but no magnifier offers to open it.
    expect(rail.rows[2]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "T7",
          terminal: false,
          upperDotted: true,
          lowerDotted: true,
        },
      ],
    })
  })
})

describe("computeGitGraphLayout — degraded inputs", () => {
  const empty = {
    rows: [],
    laneCount: 0,
    slotCount: 0,
    overflowCount: 0,
    topChips: [],
    lanes: [],
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
})
