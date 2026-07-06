/**
 * Pure layout for the Version Control panel's graph rail (no DOM, no fetch).
 *
 * Turns the graph endpoint payload plus the panel's already-computed visual
 * row list into per-row rail cells: dots on the viewed spine (each in the
 * lane of the branch that OWNS the commit), curved lane transitions at
 * fork-point rows, departure curves rising to top chips for branches
 * forked off the visible spine, and magnifier placements for collapsed
 * folded-save runs.
 */

import type { GitGraphBranch, GitGraphEntry, GitGraphResponse } from "../../api/types"

/** One visual row of the panel's history list, in render order: pending saves
 *  first, then milestones newest-first, each immediately followed by its
 *  expanded save rows (or one placeholder row while loading / when no
 *  individual saves were recorded). */
export interface RowDescriptor {
  kind: "pending-save" | "milestone" | "save" | "placeholder"
  sha: string
  /** For milestone rows: its save rows are open below (including the loading
   *  placeholder). Collapsed milestones are magnifier candidates. */
  expanded?: boolean
  /** For save/placeholder rows: the milestone they are expanded under. */
  milestoneSha?: string
}

export interface GitGraphView {
  /** The peeked branch, or null to view the working branch. */
  viewBranch: string | null
  rows: RowDescriptor[]
  /** Total lane budget. Ownership-chain lanes always draw (the spine must stay
   *  continuous); departures compete for the remainder by `order` priority. */
  laneCap?: number
}

export const DEFAULT_LANE_CAP = 5
/** Size of the CSS lane palette (--git-lane-0..7). */
export const LANE_COLOR_COUNT = 8

// Lane geometry: 12px per lane over a shared 8px gutter. Departures are
// already budget-capped, so laneCount only exceeds the cap via a deep
// ownership chain — there the rail widens (squeezing row text) rather than
// overdrawing it with an overflowing SVG.
export const LANE_WIDTH = 12
export const RAIL_GUTTER = 8

export function railWidth(laneCount: number): number {
  return laneCount * LANE_WIDTH + RAIL_GUTTER
}

interface RailCellBase {
  branch: string
  colorIndex: number
}

/** Milestone dot on its owner's lane; the lane's line runs through the row. */
export interface RailDotCell extends RailCellBase {
  kind: "dot"
  lane: number
  sha: string
  /** True on the root milestone — the line terminates here, nothing below. */
  terminal: boolean
}

/** Pending save: hollow dot on the viewed lane. */
export interface RailHollowDotCell extends RailCellBase {
  kind: "hollow-dot"
  lane: number
  sha: string
}

/** Expanded ledger save: small dot on its milestone's owner lane. */
export interface RailSaveDotCell extends RailCellBase {
  kind: "save-dot"
  lane: number
  sha: string
}

/** A lane running vertically through this row with no node on it. */
export interface RailPassCell extends RailCellBase {
  kind: "pass"
  lane: number
}

/** The viewed line changes lanes at a fork-point row: it enters from
 *  `fromLane` above and lands on `toLane`, where the row's dot sits.
 *  `branch`/`colorIndex` describe the new owner (the landing lane). */
export interface RailTransitionCell extends RailCellBase {
  kind: "transition"
  fromLane: number
  toLane: number
  fromColorIndex: number
}

/** A departing child branch curves off the spine at its fork row, rising from
 *  `fromLane` (the spine lane at this row) into its own lane above.
 *  `branch`/`colorIndex` describe the departing child. */
export interface RailCurveOutCell extends RailCellBase {
  kind: "curve-out"
  fromLane: number
  toLane: number
}

export type RailCell =
  | RailDotCell
  | RailHollowDotCell
  | RailSaveDotCell
  | RailPassCell
  | RailTransitionCell
  | RailCurveOutCell

export interface RailDeparture {
  branch: string
  lane: number
  colorIndex: number
}

export interface RailMagnifier {
  /** The collapsed UPPER milestone whose folded saves the edge hides. */
  expandsSha: string
  lane: number
  colorIndex: number
}

export interface RailRow {
  cells: RailCell[]
  /** Rendered at this row's bottom edge (the edge to the next milestone). */
  magnifier?: RailMagnifier
}

export interface RailTopChip {
  branch: string
  lane: number
  colorIndex: number
}

export interface RailModel {
  /** 1:1 with the input rows; empty (with laneCount 0) for degraded inputs. */
  rows: RailRow[]
  laneCount: number
  /** Branches not drawable: fork point outside the visible spine, or dropped
   *  by the lane cap. Archived branches are excluded silently, not counted. */
  overflowCount: number
  topChips: RailTopChip[]
  /** The resolved branch the rail is drawn for (viewBranch ?? working_branch);
   *  null when the rail is empty. */
  viewBranch: string | null
  /** True when that branch is archived — the renderer greys the rail. */
  viewedIsArchived: boolean
}

function emptyRail(): RailModel {
  return {
    rows: [],
    laneCount: 0,
    overflowCount: 0,
    topChips: [],
    viewBranch: null,
    viewedIsArchived: false,
  }
}

export function computeGitGraphLayout(graph: GitGraphResponse, view: GitGraphView): RailModel {
  const viewedName = view.viewBranch ?? graph.working_branch
  if (viewedName === null) return emptyRail()
  const byName = new Map<string, GitGraphBranch>(graph.branches.map((b) => [b.name, b]))
  const viewed = byName.get(viewedName)
  if (!viewed || viewed.entries.length === 0) return emptyRail()

  const colorIndexOf = (branch: string): number => {
    const idx = graph.order.indexOf(branch)
    return (idx >= 0 ? idx : 0) % LANE_COLOR_COUNT
  }

  // --- Ownership segmentation of the viewed spine (A-8, client side) --------
  // Walk newest→oldest with the viewed branch as owner; the fork-point commit
  // itself belongs to the parent, so the switch happens BEFORE assigning it.
  // The loop follows chained fork points landing on the same commit; the
  // visited set is a pure cycle guard against malformed fork_of chains.
  const ownerBySha = new Map<string, string>()
  const entryBySha = new Map<string, GitGraphEntry>()
  let walkOwner = viewedName
  const visited = new Set<string>([walkOwner])
  for (const entry of viewed.entries) {
    let rec = byName.get(walkOwner)
    while (
      rec !== undefined &&
      rec.fork_point_sha === entry.sha &&
      rec.fork_of !== null &&
      byName.has(rec.fork_of) &&
      !visited.has(rec.fork_of)
    ) {
      walkOwner = rec.fork_of
      visited.add(walkOwner)
      rec = byName.get(walkOwner)
    }
    ownerBySha.set(entry.sha, walkOwner)
    entryBySha.set(entry.sha, entry)
  }
  // Panel rows and graph entries are fetched independently; a sha the graph
  // doesn't know stays on the viewed lane rather than breaking the rail.
  const ownerOf = (sha: string): string => ownerBySha.get(sha) ?? viewedName

  // --- Visible spine + lane assignment ---------------------------------------
  const milestoneRowIndexBySha = new Map<string, number>()
  view.rows.forEach((row, i) => {
    if (row.kind === "milestone" && !milestoneRowIndexBySha.has(row.sha)) {
      milestoneRowIndexBySha.set(row.sha, i)
    }
  })

  const chainBranches = new Set<string>()
  for (const sha of milestoneRowIndexBySha.keys()) {
    const owner = ownerOf(sha)
    if (owner !== viewedName) chainBranches.add(owner)
  }

  const departureCandidates: { branch: GitGraphBranch; forkRowIndex: number }[] = []
  let overflowCount = 0
  for (const b of graph.branches) {
    if (b.name === viewedName || chainBranches.has(b.name)) continue
    if (b.is_archived) continue
    const forkRowIndex =
      b.fork_point_sha === null ? undefined : milestoneRowIndexBySha.get(b.fork_point_sha)
    if (forkRowIndex !== undefined) {
      departureCandidates.push({ branch: b, forkRowIndex })
    } else {
      overflowCount += 1
    }
  }

  const orderPos = (name: string): number => {
    const idx = graph.order.indexOf(name)
    return idx >= 0 ? idx : Number.MAX_SAFE_INTEGER
  }
  const byOrder = (a: string, b: string): number =>
    orderPos(a) - orderPos(b) || a.localeCompare(b)

  const laneCap = view.laneCap ?? DEFAULT_LANE_CAP
  const departureBudget = Math.max(0, laneCap - 1 - chainBranches.size)
  const drawnDepartures = [...departureCandidates]
    .sort((a, b) => byOrder(a.branch.name, b.branch.name))
    .slice(0, departureBudget)
  overflowCount += departureCandidates.length - drawnDepartures.length

  const laneByBranch = new Map<string, number>([[viewedName, 0]])
  const lanedBranches = [...chainBranches, ...drawnDepartures.map((d) => d.branch.name)].sort(
    byOrder,
  )
  lanedBranches.forEach((name, i) => laneByBranch.set(name, i + 1))
  const laneOf = (branch: string): number => laneByBranch.get(branch) ?? 0

  const departuresByRowIndex = new Map<number, RailDeparture[]>()
  for (const { branch, forkRowIndex } of drawnDepartures) {
    const list = departuresByRowIndex.get(forkRowIndex) ?? []
    list.push({
      branch: branch.name,
      lane: laneOf(branch.name),
      colorIndex: colorIndexOf(branch.name),
    })
    departuresByRowIndex.set(forkRowIndex, list)
  }
  for (const list of departuresByRowIndex.values()) list.sort((a, b) => a.lane - b.lane)

  const topChips: RailTopChip[] = drawnDepartures
    .map((d) => ({
      branch: d.branch.name,
      lane: laneOf(d.branch.name),
      colorIndex: colorIndexOf(d.branch.name),
    }))
    .sort((a, b) => a.lane - b.lane)

  // Departure lanes run vertically from their fork row up to the top chip.
  const departurePassesAt = (rowIndex: number): RailPassCell[] =>
    drawnDepartures
      .filter((d) => d.forkRowIndex > rowIndex)
      .map((d): RailPassCell => ({
        kind: "pass",
        lane: laneOf(d.branch.name),
        branch: d.branch.name,
        colorIndex: colorIndexOf(d.branch.name),
      }))
      .sort((a, b) => a.lane - b.lane)

  // --- Magnifiers (A-4) -------------------------------------------------------
  // On the UPPER milestone row of every collapsed edge whose upper milestone
  // folds at least one save (a milestone folding saves is a merge of the
  // ledger, so >= 2 parents). Transition edges qualify too; the window-final
  // row has no lower edge, so never qualifies.
  const magnifierByRowIndex = new Map<number, RailMagnifier>()
  const milestoneRowIndexes = [...milestoneRowIndexBySha.values()].sort((a, b) => a - b)
  for (let k = 0; k + 1 < milestoneRowIndexes.length; k++) {
    const upper = milestoneRowIndexes[k]
    if (view.rows[upper].expanded) continue
    const sha = view.rows[upper].sha
    if ((entryBySha.get(sha)?.parents.length ?? 0) < 2) continue
    const owner = ownerOf(sha)
    magnifierByRowIndex.set(upper, {
      expandsSha: sha,
      lane: laneOf(owner),
      colorIndex: colorIndexOf(owner),
    })
  }

  // --- Per-row cells ----------------------------------------------------------
  // Running spine state: the owner lane of the most recent milestone row (save
  // and placeholder rows continue that lane), and the previous milestone's
  // owner for transition detection. Before the first milestone row the spine
  // is the viewed branch itself (pending rows draw on lane 0).
  let spineBranch = viewedName
  let prevMilestoneOwner = viewedName
  // Once the terminal (root) dot is emitted the spine lane ends: save and
  // placeholder rows below it draw no spine cell (departure passes still do).
  let spineEnded = false

  const rows: RailRow[] = view.rows.map((row, i) => {
    const cells: RailCell[] = []
    let magnifier: RailMagnifier | undefined

    switch (row.kind) {
      case "pending-save":
        cells.push({
          kind: "hollow-dot",
          lane: 0,
          branch: viewedName,
          colorIndex: colorIndexOf(viewedName),
          sha: row.sha,
        })
        break
      case "milestone": {
        const owner = ownerOf(row.sha)
        if (owner !== prevMilestoneOwner) {
          cells.push({
            kind: "transition",
            fromLane: laneOf(prevMilestoneOwner),
            toLane: laneOf(owner),
            branch: owner,
            colorIndex: colorIndexOf(owner),
            fromColorIndex: colorIndexOf(prevMilestoneOwner),
          })
        }
        const terminal = entryBySha.get(row.sha)?.is_root ?? false
        cells.push({
          kind: "dot",
          lane: laneOf(owner),
          branch: owner,
          colorIndex: colorIndexOf(owner),
          sha: row.sha,
          terminal,
        })
        for (const d of departuresByRowIndex.get(i) ?? []) {
          cells.push({
            kind: "curve-out",
            fromLane: laneOf(owner),
            toLane: d.lane,
            branch: d.branch,
            colorIndex: d.colorIndex,
          })
        }
        magnifier = magnifierByRowIndex.get(i)
        prevMilestoneOwner = owner
        spineBranch = owner
        if (terminal) spineEnded = true
        break
      }
      case "save":
        if (!spineEnded) {
          cells.push({
            kind: "save-dot",
            lane: laneOf(spineBranch),
            branch: spineBranch,
            colorIndex: colorIndexOf(spineBranch),
            sha: row.sha,
          })
        }
        break
      case "placeholder":
        if (!spineEnded) {
          cells.push({
            kind: "pass",
            lane: laneOf(spineBranch),
            branch: spineBranch,
            colorIndex: colorIndexOf(spineBranch),
          })
        }
        break
    }

    cells.push(...departurePassesAt(i))
    return { cells, ...(magnifier && { magnifier }) }
  })

  return {
    rows,
    laneCount: 1 + lanedBranches.length,
    overflowCount,
    topChips,
    viewBranch: viewedName,
    viewedIsArchived: viewed.is_archived,
  }
}
