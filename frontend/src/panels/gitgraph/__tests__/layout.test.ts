import { describe, expect, it } from "vitest"
import type { GitGraphBranch, GitGraphEntry, GitGraphResponse } from "../../../api/types"
import {
  FOLD_RISE,
  MAGNIFIER_GUTTER,
  SLOT_TIGHT_WIDTH,
  computeGitGraphLayout,
  computeRailRuns,
  laneX,
  railWidth,
  slotTightX,
} from "../layout"
import type { GitGraphView, RailRowGeom, RowDescriptor } from "../layout"

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

/** Solid dot shorthand: collapsed stretches render a plain solid rail. */
const solidDot = (
  sha: string,
  lane: number,
  branch: string,
  colorIndex: number,
  terminal = false,
) => ({
  kind: "dot" as const,
  lane,
  branch,
  colorIndex,
  sha,
  terminal,
  upperDotted: false,
  lowerDotted: false,
})

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
    // Collapsed stretches render a plain SOLID rail — dotted marks only where
    // the saves siding runs beside it (an expanded range).
    expect(rail.rows[2]).toEqual({
      cells: [solidDot("T6", 0, "trunk", 0)],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: false },
    })
    // Zero-fold milestone (single parent): no magnifier.
    expect(rail.rows[3]).toEqual({
      cells: [solidDot("T5", 0, "trunk", 0)],
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
      solidDot("T4", 0, "trunk", 0),
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
      solidDot("T3", 0, "trunk", 0),
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
    expect(rail.rows[6].cells).toEqual([solidDot("T2", 0, "trunk", 0)])
  })

  it("terminates the line at the root milestone with no window-final magnifier", () => {
    expect(rail.rows[8]).toEqual({
      cells: [solidDot("R0", 0, "trunk", 0, true)],
    })
  })

  it("sizes the rail to the drawn geometry: gutter + lanes + widest slot group tail", () => {
    // With stubs the right edge sits one stub-pitch (SLOT_TIGHT_WIDTH) beyond
    // the outermost stub's tail — no separate flare envelope.
    expect(railWidth(rail.laneCount, rail.slotCount)).toBe(slotTightX(0, 1) + SLOT_TIGHT_WIDTH)
    // (1 lane, 1 slot): tail of slot 0 + one pitch = 34 + 5.
    expect(railWidth(1, 1)).toBe(39)
    // (1 lane, 2 slots): tail of the outermost slot (1) + one pitch = 39 + 5.
    expect(railWidth(1, 2)).toBe(44)
    // (3 lanes, 2 slots): slots start past three lanes → 63 + 5.
    expect(railWidth(3, 2)).toBe(68)
    // No stubs: far enough past the rightmost lane to house the siding (+9).
    expect(railWidth(1, 0)).toBe(33)
    expect(SLOT_TIGHT_WIDTH).toBe(5)
    expect(FOLD_RISE).toBe(12)
    expect(laneX(0)).toBe(MAGNIFIER_GUTTER + 4 + 6)
    expect(slotTightX(0, 1)).toBe(MAGNIFIER_GUTTER + 4 + 12 + 4)
    expect(slotTightX(1, 1)).toBe(slotTightX(0, 1) + SLOT_TIGHT_WIDTH)
    // The width never changes when a milestone expands: with-stub width is
    // slot-driven, so it holds regardless of lane geometry beyond the slots.
    expect(railWidth(1, 2)).toBe(slotTightX(1, 1) + SLOT_TIGHT_WIDTH)
  })
})

describe("computeGitGraphLayout — dotted rail beside the saves siding", () => {
  it("dots the rail across an expanded range: lower at the expanded dot, upper at the next", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), saveRow("S1", "T6"), saveRow("S2", "T6"), milestone("T5")],
    })
    // The expanded milestone opens the dotted stretch below its dot…
    expect(rail.rows[0].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T6",
      upperDotted: false,
      lowerDotted: true,
    })
    // …the rail crossing the save rows is the dotted line beside the siding…
    expect(rail.rows[1].cells[0]).toMatchObject({ kind: "pass", dotted: true })
    expect(rail.rows[2].cells[0]).toMatchObject({ kind: "pass", dotted: true })
    // …and it feeds into the NEXT same-lane dot from above.
    expect(rail.rows[3].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T5",
      upperDotted: true,
      lowerDotted: false,
    })
  })

  it("keeps collapsed milestones entirely solid (fold-carrying or not)", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6"), milestone("T5")],
    })
    expect(rail.rows[0].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T6",
      upperDotted: false,
      lowerDotted: false,
    })
    expect(rail.rows[1].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T5",
      upperDotted: false,
      lowerDotted: false,
    })
  })

  it("stays solid across a placeholder expansion (no real saves — no siding)", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), placeholderRow("T6"), milestone("T5")],
    })
    expect(rail.rows[0].cells[0]).toMatchObject({
      kind: "dot",
      sha: "T6",
      lowerDotted: false,
    })
    expect(rail.rows[1].cells[0]).toMatchObject({ kind: "pass", dotted: false })
    expect(rail.rows[2].cells[0]).toMatchObject({ kind: "dot", sha: "T5", upperDotted: false })
  })

  it("never carries a dotted stretch across a lane change (transitions are always solid)", () => {
    // hotfix → feature/a → trunk chain; A1 (feature/a) expanded directly
    // above the trunk-owned T1.
    const rail = computeGitGraphLayout(crystalGraph, {
      viewBranch: "hotfix",
      rows: [
        milestone("X"),
        milestone("A1", true),
        saveRow("As", "A1"),
        milestone("T1"),
        milestone("R0"),
      ],
    })
    expect(rail.rows[1].cells[1]).toMatchObject({ kind: "dot", sha: "A1", lowerDotted: true })
    const transition = rail.rows[3].cells[0]
    expect(transition).toMatchObject({ kind: "transition", fromLane: 1, toLane: 2 })
    // The transition curve has NO dash variant at all any more…
    expect(transition).not.toHaveProperty("dotted")
    // …and the landing dot does not inherit the dotted stretch across lanes.
    expect(rail.rows[3].cells[1]).toMatchObject({ kind: "dot", sha: "T1", upperDotted: false })
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
    // The expanded milestone folds the range out of its dot and opens the
    // dotted rail stretch beside the siding…
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
          lowerDotted: true,
        },
        { kind: "fold-in", lane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: true },
    })
    // …save rows ride the (solid) siding beside the dotted rail pass…
    expect(rail.rows[1].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0, dotted: true },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S1", last: false },
    ])
    // …the final save is NOT `last` when a milestone row follows (the line
    // continues down into that milestone's fold-out)…
    expect(rail.rows[2].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0, dotted: true },
      { kind: "save-dot", lane: 0, branch: "trunk", colorIndex: 0, sha: "S2", last: false },
    ])
    // …and the next milestone merges the siding back into its dot, closing
    // the dotted stretch from above (same owner both sides: fromLane === lane).
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
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0, dotted: true },
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

  it("passes the siding straight through a doubly-expanded same-lane milestone (single fold-out, no pinch)", () => {
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
    // M2 has saves directly above AND below on its OWN lane: the ledger runs
    // straight through. Time flows up, so the single kept curve is the
    // FOLD-IN — the merge joining M2's dot to its own saves BELOW it; the
    // fold-out pinch from above is SUPPRESSED (the upward side is the clean
    // continuing branch-off) and a siding-pass is emitted so the siding
    // passes the full row height. The dot still carries the dotted rail both
    // sides (upper closes M3's range, lower opens M2's own).
    expect(rail.rows[3]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "M2",
          terminal: false,
          upperDotted: true,
          lowerDotted: true,
        },
        { kind: "fold-in", lane: 0, branch: "trunk", colorIndex: 0 },
        { kind: "siding-pass", lane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "M2", lane: 0, colorIndex: 0, expanded: true },
    })
    // No fold-out on the through row: the pinch from above is gone.
    expect(rail.rows[3].cells.some((c) => c.kind === "fold-out")).toBe(false)
    // M1 only closes the range above it; collapsed, its own lower edge is
    // solid again. Only one side is expanded, so the normal fold-out (no
    // siding-pass) stands.
    expect(rail.rows[5]).toEqual({
      cells: [
        {
          kind: "dot",
          lane: 0,
          branch: "trunk",
          colorIndex: 0,
          sha: "M1",
          terminal: false,
          upperDotted: true,
          lowerDotted: false,
        },
        { kind: "fold-out", lane: 0, fromLane: 0, branch: "trunk", colorIndex: 0 },
      ],
      magnifier: { expandsSha: "M1", lane: 0, colorIndex: 0, expanded: false },
    })
    expect(rail.rows[5].cells.some((c) => c.kind === "siding-pass")).toBe(false)
  })

  it("consolidates ONE continuous siding run across both ranges through a doubly-expanded milestone's row", () => {
    const graph: GitGraphResponse = {
      working_branch: "trunk",
      order: ["trunk"],
      branches: [
        branch("trunk", [entry("M3", 2), entry("M2", 2), entry("M1", 1), root("R0")], {
          is_current: true,
        }),
      ],
    }
    const model = computeGitGraphLayout(graph, {
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
    // 7 contiguous 40px rows, node centres 16px in.
    const geom: RailRowGeom[] = Array.from({ length: 7 }, (_, i) => ({
      top: i * 40,
      height: 40,
      dotY: 16,
    }))
    const runs = computeRailRuns(model, geom)
    const SX = laneX(0) + 5 // the siding x
    const siding = runs.filter((r) => r.kind === "siding")
    // ONE siding run spanning M3's fold-in tail (row 0: dot 16 + FOLD_RISE 12 =
    // 28) all the way down through M2's through-row to M1's fold-out lead-in
    // (row 5: dot 5*40+16 = 216, minus FOLD_RISE = 204). The siding-pass gives
    // M2's row a full-height segment, so nothing breaks the run at its dot.
    expect(siding).toEqual([
      {
        kind: "siding",
        x: SX,
        y1: 28,
        y2: 204,
        dotted: false,
        branch: "trunk",
        colorIndex: 0,
      },
    ])
  })

  it("keeps the two-curve fold across an ownership transition (no siding-pass, no continuation)", () => {
    // hotfix → feature/a → trunk. A1 (feature/a, lane 1) is expanded with a
    // save both above (feature/a's own range) and below, but the incoming
    // siding above belongs to a DIFFERENT lane's range, so this is NOT a
    // same-lane continuation: the milestone keeps the fold-in + fold-out pair
    // and emits no siding-pass. Here A1 sits between two of its own saves so
    // the range owner IS feature/a on both sides — the guard is the lane of the
    // ABOVE range vs the milestone owner; a cross-lane above would differ.
    const rail = computeGitGraphLayout(crystalGraph, {
      viewBranch: "hotfix",
      rows: [
        milestone("X", true),
        saveRow("Xs", "X"),
        milestone("A1", true),
        saveRow("As", "A1"),
        milestone("T1"),
        milestone("R0"),
      ],
    })
    // X is owned by hotfix (lane 0); A1 by feature/a (lane 1). The save above
    // A1 (Xs) belongs to X's range on lane 0, so laneOf(rangeOwner) !==
    // laneOf(A1's owner) — the same-lane-through guard fails. A1 keeps BOTH
    // curves and emits no siding-pass.
    const a1 = rail.rows[2]
    expect(a1.cells.some((c) => c.kind === "siding-pass")).toBe(false)
    expect(a1.cells.some((c) => c.kind === "fold-in")).toBe(true)
    expect(a1.cells.some((c) => c.kind === "fold-out")).toBe(true)
    // The fold-out departs the RANGE owner's lane (lane 0, hotfix's siding
    // above) and lands on A1's own lane (1); the fold-in opens A1's own range.
    expect(a1.cells.find((c) => c.kind === "fold-out")).toMatchObject({
      kind: "fold-out",
      lane: 1,
      fromLane: 0,
    })
    expect(a1.cells.find((c) => c.kind === "fold-in")).toMatchObject({
      kind: "fold-in",
      lane: 1,
    })
  })

  it("a placeholder row keeps the spine continuous without a save dot or fold-in", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [milestone("T6", true), placeholderRow("T6"), ...trunkMilestoneRows.slice(1)],
    })
    // Placeholder is not a save row: no fold-in above it, no dotted rail,
    // magnifier expanded.
    expect(rail.rows[0]).toEqual({
      cells: [solidDot("T6", 0, "trunk", 0)],
      magnifier: { expandsSha: "T6", lane: 0, colorIndex: 0, expanded: true },
    })
    expect(rail.rows[1].cells).toEqual([
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0, dotted: false },
    ])
  })

  it("emits no spine cell on rows below the terminal (root) milestone", () => {
    const rail = computeGitGraphLayout(forestGraph, {
      viewBranch: null,
      rows: [...trunkMilestoneRows.slice(0, 6), milestone("R0", true), placeholderRow("R0")],
    })
    expect(rail.rows[6].cells).toEqual([solidDot("R0", 0, "trunk", 0, true)])
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

  it("runs the ancestor lane to the very top as (solid) pass cells above its first owned row", () => {
    expect(rail.rows[0].cells).toEqual([
      solidDot("A2", 0, "feature/a", 1),
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0, dotted: false },
    ])
    expect(rail.rows[1].cells).toEqual([
      solidDot("A1", 0, "feature/a", 1),
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0, dotted: false },
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
      },
      solidDot("T3", 1, "trunk", 0),
    ])
    expect(rail.rows[5].cells).toEqual([solidDot("R0", 1, "trunk", 0, true)])
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
      { kind: "pass", lane: 1, branch: "trunk", colorIndex: 0, dotted: true },
      { kind: "save-dot", lane: 1, branch: "trunk", colorIndex: 0, sha: "Tx", last: false },
    ])
    // Same lane below the range: the dotted stretch feeds into T2's dot.
    expect(expanded.rows[4].cells).toEqual([
      {
        kind: "dot",
        lane: 1,
        branch: "trunk",
        colorIndex: 0,
        sha: "T2",
        terminal: false,
        upperDotted: true,
        lowerDotted: false,
      },
      { kind: "fold-out", lane: 1, fromLane: 1, branch: "trunk", colorIndex: 0 },
    ])
  })
})

// hotfix forked off feature/a (at A1), which itself forked off trunk (at T1):
// lanes are discovered walking DOWN the viewed spine, so the nearest ancestor
// (feature/a) takes lane 1 and trunk lane 2 — the spine migrates monotonically
// outward, never crossing back over a lane. (Module scope: the dotted-rules
// describe reuses it.)
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

describe("computeGitGraphLayout — nearest-ancestor-first lanes (fork of a fork)", () => {
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
      solidDot("X", 0, "hotfix", 2),
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1, dotted: false },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0, dotted: false },
    ])
    expect(rail.rows[1].cells).toEqual([
      {
        kind: "transition",
        fromLane: 0,
        toLane: 1,
        branch: "feature/a",
        colorIndex: 1,
        fromColorIndex: 2,
      },
      solidDot("A1", 1, "feature/a", 1),
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0, dotted: false },
    ])
    expect(rail.rows[2].cells).toEqual([
      {
        kind: "transition",
        fromLane: 1,
        toLane: 2,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      solidDot("T1", 2, "trunk", 0),
    ])
    expect(rail.rows[3].cells).toEqual([solidDot("R0", 2, "trunk", 0, true)])
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
    // trunk, lane 2): the siding ran on feature/a's lane, so the fold-out
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
      },
      {
        kind: "dot",
        lane: 1,
        branch: "feature/a",
        colorIndex: 1,
        sha: "A1",
        terminal: false,
        upperDotted: false,
        lowerDotted: true, // open range with real saves below
      },
      { kind: "fold-in", lane: 1, branch: "feature/a", colorIndex: 1 },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0, dotted: false },
    ])
    expect(expanded.rows[2].cells).toEqual([
      { kind: "pass", lane: 1, branch: "feature/a", colorIndex: 1, dotted: true },
      { kind: "save-dot", lane: 1, branch: "feature/a", colorIndex: 1, sha: "As", last: false },
      { kind: "pass", lane: 2, branch: "trunk", colorIndex: 0, dotted: false },
    ])
    // The next milestone belongs to trunk: fold-out departs the RANGE's lane
    // (feature/a, lane 1) and wears the range owner's colour. The dotted
    // stretch does NOT cross the lane change (solid transition, solid dot).
    expect(expanded.rows[3].cells).toEqual([
      {
        kind: "transition",
        fromLane: 1,
        toLane: 2,
        branch: "trunk",
        colorIndex: 0,
        fromColorIndex: 1,
      },
      solidDot("T1", 2, "trunk", 0),
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
      solidDot("M2", 0, "trunk", 0),
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
      solidDot("M1", 0, "trunk", 0),
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
      { kind: "pass", lane: 0, branch: "trunk", colorIndex: 0, dotted: true },
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
      },
      solidDot("T1", 1, "trunk", 0),
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
    expect(rail.rows[2]).toEqual({
      cells: [solidDot("T7", 0, "trunk", 0)],
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

// ---------------------------------------------------------------------------
// computeRailRuns — consolidated vertical runs from measured row geometry.
// ---------------------------------------------------------------------------

describe("computeRailRuns", () => {
  // Single-lane trunk: M2 (folds S1+S2), M1, R0 (root).
  const runsGraph: GitGraphResponse = {
    working_branch: "trunk",
    order: ["trunk"],
    branches: [
      branch("trunk", [entry("M2", 2), entry("M1", 1), root("R0")], { is_current: true }),
    ],
  }
  const collapsedModel = computeGitGraphLayout(runsGraph, {
    viewBranch: null,
    rows: [milestone("M2"), milestone("M1"), milestone("R0")],
  })
  const expandedModel = computeGitGraphLayout(runsGraph, {
    viewBranch: null,
    rows: [
      milestone("M2", true),
      saveRow("S1", "M2"),
      saveRow("S2", "M2"),
      milestone("M1"),
      milestone("R0"),
    ],
  })

  /** Contiguous 40px rows starting at y=0, node centres 16px in. */
  const geomRows = (count: number, rowHeight = 40, dotY = 16): RailRowGeom[] =>
    Array.from({ length: count }, (_, i) => ({ top: i * rowHeight, height: rowHeight, dotY }))

  const LX = laneX(0) // the trunk lane
  const SX = laneX(0) + 5 // its siding (SAVE_RAIL_DX to the right)

  it("collapses a fully-collapsed single-lane spine to ONE solid run ending at the terminal dot", () => {
    const runs = computeRailRuns(collapsedModel, geomRows(3))
    expect(runs).toEqual([
      {
        kind: "spine",
        x: LX,
        y1: 0,
        y2: 80 + 16, // stops AT the root dot, not the row bottom
        dotted: false,
        branch: "trunk",
        colorIndex: 0,
      },
    ])
  })

  it("splits the spine solid/dotted/solid at the expanded dot and the next dot, plus one siding run", () => {
    const runs = computeRailRuns(expandedModel, geomRows(5))
    expect(runs).toEqual([
      // Above the expanded dot: solid.
      { kind: "spine", x: LX, y1: 0, y2: 16, dotted: false, branch: "trunk", colorIndex: 0 },
      // The rail beside the siding: ONE dotted run from M2's dot (y 16) down
      // across both save rows into M1's dot (y 120+16). The runs touch at
      // exactly y=16 and y=136 — a STYLE change, not a gap, is what breaks
      // them.
      { kind: "spine", x: LX, y1: 16, y2: 136, dotted: true, branch: "trunk", colorIndex: 0 },
      // Below the range: solid again, through M1 down to the root dot.
      { kind: "spine", x: LX, y1: 136, y2: 176, dotted: false, branch: "trunk", colorIndex: 0 },
      // The siding: one SOLID run from the fold-in tail (dot + FOLD_RISE)
      // through both save rows to the fold-out lead-in (next dot - FOLD_RISE).
      {
        kind: "siding",
        x: SX,
        y1: 16 + FOLD_RISE,
        y2: 136 - FOLD_RISE,
        dotted: false,
        branch: "trunk",
        colorIndex: 0,
      },
    ])
  })

  it("bridges gaps up to 2px (box borders) but not larger ones", () => {
    // Row 1 sits 1px below row 0 (a border); row 2 sits 3px below row 1.
    const geom: RailRowGeom[] = [
      { top: 0, height: 40, dotY: 16 },
      { top: 41, height: 40, dotY: 16 },
      { top: 84, height: 40, dotY: 16 },
    ]
    const runs = computeRailRuns(collapsedModel, geom)
    expect(runs).toEqual([
      // Rows 0+1 merged across the 1px border…
      { kind: "spine", x: LX, y1: 0, y2: 81, dotted: false, branch: "trunk", colorIndex: 0 },
      // …row 2 starts its own run (3px > tolerance), ending at its (root) dot.
      { kind: "spine", x: LX, y1: 84, y2: 100, dotted: false, branch: "trunk", colorIndex: 0 },
    ])
  })

  it("null geometry rows contribute nothing", () => {
    const geom: (RailRowGeom | null)[] = [
      { top: 0, height: 40, dotY: 16 },
      null, // e.g. a row measured as part of another box
      { top: 80, height: 40, dotY: 16 },
    ]
    const runs = computeRailRuns(collapsedModel, geom)
    expect(runs).toEqual([
      { kind: "spine", x: LX, y1: 0, y2: 40, dotted: false, branch: "trunk", colorIndex: 0 },
      { kind: "spine", x: LX, y1: 80, y2: 96, dotted: false, branch: "trunk", colorIndex: 0 },
    ])
  })

  it("stops the siding at a `last` save-dot (window ends inside the range)", () => {
    const model = computeGitGraphLayout(runsGraph, {
      viewBranch: null,
      rows: [milestone("M2", true), saveRow("S1", "M2")],
    })
    const runs = computeRailRuns(model, geomRows(2))
    const siding = runs.filter((r) => r.kind === "siding")
    expect(siding).toEqual([
      // Fold-in tail (16 + 12 → 40) merged with the save row's line, which
      // stops AT the last save's dot (40 + 16).
      { kind: "siding", x: SX, y1: 28, y2: 56, dotted: false, branch: "trunk", colorIndex: 0 },
    ])
  })

  it("drops zero-length segments (fold-out lead-in clamped to the row top)", () => {
    // M1's dot sits so close to its row top that dotAbs - FOLD_RISE clamps:
    // the fold-out contributes no straight lead-in, only its (per-row) curve.
    const geom: RailRowGeom[] = [
      { top: 0, height: 40, dotY: 16 },
      { top: 40, height: 40, dotY: 16 },
      { top: 80, height: 40, dotY: 16 },
      { top: 120, height: 40, dotY: 10 }, // M1: 130 - FOLD_RISE < 120 → clamped
      { top: 160, height: 40, dotY: 16 },
    ]
    const runs = computeRailRuns(expandedModel, geom)
    for (const r of runs) expect(r.y2).toBeGreaterThan(r.y1)
    const siding = runs.filter((r) => r.kind === "siding")
    // The siding ends at the last save row's bottom — no lead-in segment.
    expect(siding).toEqual([
      { kind: "siding", x: SX, y1: 28, y2: 120, dotted: false, branch: "trunk", colorIndex: 0 },
    ])
  })
})
