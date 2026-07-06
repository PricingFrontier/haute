/**
 * Pure layout for the Version Control panel's graph rail (no DOM, no fetch).
 *
 * Turns the graph endpoint payload plus the panel's already-computed visual
 * row list into per-row rail cells. Two distinct horizontal regions:
 *
 * - LANES (left): full-height vertical lines. Lane 0 is the viewed branch's
 *   spine; lanes 1..k are its ANCESTORS (the ownership chain of the visible
 *   history). Ancestor lanes continue to the very top of the rail — their
 *   branches have history of their own alongside the viewed one — and are
 *   right-clickable (switch / view).
 * - SPAWN SLOTS (right): branches forked OFF the visible history draw no
 *   full-height lane. Each renders at its anchor row as a single solid curve
 *   flaring off the spine into a slot column. Slot space is reserved per
 *   anchor GROUP (a milestone plus its expanded save rows) so expanding a
 *   milestone never changes the rail width.
 *
 * Expanded ledger saves sit on a SIDING: a solid line hugging the spine lane
 * (SAVE_RAIL_DX to its right, same colour) that curves out of its milestone's
 * dot at the top of the range — the fold merge — and back into the next
 * milestone's dot below. Between two ADJACENT EXPANDED milestones on the same
 * save/milestone lane the ledger is continuous: the siding runs STRAIGHT
 * THROUGH the middle milestone's row as one solid line, with a SINGLE
 * branch-off curve folding into that dot (the fold merge) — no pinch. Across
 * an ownership transition (the incoming siding is on a different lane) the
 * siding instead folds out and back in as two curves. Across the expanded
 * range, the
 * milestone RAIL itself (the straight milestone-to-milestone line) renders
 * DOTTED — the inactive of the two parallel lines while the siding shows the
 * detail. Collapsed stretches are plain solid.
 *
 * VERTICAL RUNS: per-row cells only place dots and curves. Every straight
 * vertical line is consolidated by computeRailRuns() into one line per
 * contiguous (x, style) stretch, drawn by a measured overlay spanning the
 * whole list — a dotted run keeps its dash phase across every row and box
 * border it crosses, and each run costs one SVG element.
 */

import type { GitGraphBranch, GitGraphResponse } from "../../api/types"

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
}

/** Size of the CSS lane palette (--git-lane-0..7). */
export const LANE_COLOR_COUNT = 8

// Geometry. Lanes are 12px columns over a shared 8px gutter; the save
// sub-rail hugs its spine lane at a much smaller offset than a lane so it
// reads as the same line's shadow. Spawn stubs launch off the spine at their
// node and their bezier tails converge to a tight pitch at the row top; the
// round-4 bezier draws only into the tight envelope, so SLOT_TIGHT_WIDTH sets
// both the stub pitch AND the rail's right edge (no separate flare envelope).
export const LANE_WIDTH = 12
export const SLOT_TIGHT_WIDTH = 5
export const RAIL_GUTTER = 8
/** Dedicated strip LEFT of the lanes where the magnifier toggles live (an
 *  editor fold-gutter): keeps the buttons off the coloured rails without
 *  the enclosing box's overflow clipping them. */
export const MAGNIFIER_GUTTER = 14
export const SAVE_RAIL_DX = 5
/** Vertical run of the little fold curves between a milestone dot and the
 *  siding (small, so the merge reads as part of the dot). Shared between the
 *  per-row curve rendering and the run consolidation. */
export const FOLD_RISE = 12

export const laneX = (lane: number): number =>
  MAGNIFIER_GUTTER + RAIL_GUTTER / 2 + lane * LANE_WIDTH + LANE_WIDTH / 2

/** x where a stub's bezier tail terminates at the row top. */
export const slotTightX = (slot: number, laneCount: number): number =>
  MAGNIFIER_GUTTER + RAIL_GUTTER / 2 + laneCount * LANE_WIDTH + 4 + slot * SLOT_TIGHT_WIDTH

/** Rail width trimmed to the drawn geometry (no flare envelope). With stubs
 *  the right edge sits one stub-pitch (SLOT_TIGHT_WIDTH) beyond the outermost
 *  stub tail. Without stubs it sits far enough beyond the rightmost lane to
 *  house the siding (laneX + SAVE_RAIL_DX + a save-dot radius ≈ +9) so the
 *  width never changes when a milestone expands. */
export function railWidth(laneCount: number, slotCount: number): number {
  if (slotCount > 0) return slotTightX(slotCount - 1, laneCount) + SLOT_TIGHT_WIDTH
  return laneX(laneCount - 1) + 9
}

interface RailCellBase {
  branch: string
  colorIndex: number
}

/** Milestone dot on its owner's lane; the lane's line runs through the row.
 *  Edge dash semantics: the milestone RAIL is dotted exactly where the saves
 *  SIDING runs beside it — across an expanded range, from the expanded
 *  milestone's dot down to the next milestone's dot on the same lane (the
 *  dotted stretch "feeds into" that lower dot even though its own row is
 *  collapsed). Everywhere else the rail is solid. */
export interface RailDotCell extends RailCellBase {
  kind: "dot"
  lane: number
  sha: string
  /** True on the root milestone — the line terminates here, nothing below. */
  terminal: boolean
  /** The segment above the dot closes a dotted stretch fed from an expanded
   *  same-lane milestone above. */
  upperDotted: boolean
  /** The segment below the dot opens a dotted stretch: THIS milestone is
   *  expanded with real save rows following. */
  lowerDotted: boolean
}

/** Pending save: hollow dot on the viewed lane. */
export interface RailHollowDotCell extends RailCellBase {
  kind: "hollow-dot"
  lane: number
  sha: string
}

/** Expanded ledger save: dot on the sub-rail beside `lane`'s spine line. The
 *  dotted sub-rail line runs the row's full height unless `last` (the range's
 *  final save with no milestone row following to fold into). */
export interface RailSaveDotCell extends RailCellBase {
  kind: "save-dot"
  lane: number
  sha: string
  last: boolean
}

/** A lane running vertically through this row with no node on it. Dotted
 *  when it is the milestone rail crossing an expanded range's save rows
 *  (the siding carries the detail there). */
export interface RailPassCell extends RailCellBase {
  kind: "pass"
  lane: number
  dotted: boolean
}

/** The viewed line changes lanes at a fork-point row: it enters from
 *  `fromLane` above and lands on `toLane`, where the row's dot sits.
 *  `branch`/`colorIndex` describe the new owner (the landing lane). */
/** Branch-off edges are always SOLID (dotted is reserved for the rail
 *  beside a visible siding, which never spans a lane change). */
export interface RailTransitionCell extends RailCellBase {
  kind: "transition"
  fromLane: number
  toLane: number
  fromColorIndex: number
}

/** The FOLD MERGE: time flows up the list, so the saves displayed beneath a
 *  milestone committed INTO it — this curve joins the milestone's dot to the
 *  siding BELOW it. */
export interface RailFoldInCell extends RailCellBase {
  kind: "fold-in"
  lane: number
}

/** The BRANCH-OFF: the siding of an expanded range ABOVE this milestone is
 *  the ledger continuing AFTER it (later, so upward) — this curve leaves the
 *  dot up onto the siding. `fromLane` is the lane the siding ran on (the
 *  RANGE's owner) — across an ownership transition it differs from `lane`
 *  (this milestone's owner), and the curve must depart from the siding's
 *  true x or the line dies at the boundary. */
export interface RailFoldOutCell extends RailCellBase {
  kind: "fold-out"
  lane: number
  fromLane: number
}

/** A milestone row whose siding runs STRAIGHT THROUGH it (both neighbouring
 *  same-lane milestones expanded): the fold-in merge from below still joins
 *  the dot, but the siding does NOT pinch — it passes the full row height at
 *  the sub-rail, so the upward side reads as the clean continuing branch-off.
 *  Draws nothing itself; computeRailRuns turns it into a full-height siding
 *  segment so the overlay consolidates one continuous run across both
 *  adjacent ranges. */
export interface RailSidingPassCell extends RailCellBase {
  kind: "siding-pass"
  lane: number
}

/** A branch departing the visible history at this row: a curve off the row's
 *  node into slot `slot`, then a short dotted stub rising to the row top.
 *  `fromSub` anchors the curve on the save sub-rail instead of the spine
 *  lane (the branch was spawned from that ledger save). Archived departures
 *  are grouped: one muted stub in the PARENT's colour covering `count`
 *  branches. */
export interface RailSpawnStubCell extends RailCellBase {
  kind: "spawn-stub"
  fromLane: number
  fromSub: boolean
  slot: number
  archived: boolean
  count: number
}

export type RailCell =
  | RailDotCell
  | RailHollowDotCell
  | RailSaveDotCell
  | RailPassCell
  | RailTransitionCell
  | RailFoldInCell
  | RailFoldOutCell
  | RailSidingPassCell
  | RailSpawnStubCell

export interface RailMagnifier {
  /** The milestone whose folded saves the button toggles. */
  expandsSha: string
  lane: number
  colorIndex: number
  /** Drawn as zoom-out (collapse) when the milestone is already open. */
  expanded: boolean
}

export interface RailRow {
  cells: RailCell[]
  /** Rendered at this row's bottom edge (the edge to the next milestone). */
  magnifier?: RailMagnifier
}

export interface RailTopChip {
  branch: string
  colorIndex: number
  archived: boolean
}

/** A full-height lane line (viewed branch or ancestor) — the right-click
 *  switch/view targets. */
export interface RailLane {
  branch: string
  lane: number
  colorIndex: number
}

export interface RailModel {
  /** 1:1 with the input rows; empty (with laneCount 0) for degraded inputs. */
  rows: RailRow[]
  laneCount: number
  slotCount: number
  /** Non-archived departures whose anchor row is outside the visible spine. */
  overflowCount: number
  topChips: RailTopChip[]
  lanes: RailLane[]
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
    slotCount: 0,
    overflowCount: 0,
    topChips: [],
    lanes: [],
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

  // Palette indexes are assigned over the NON-archived branches only, in the
  // payload's deterministic order — archived branches borrow their parent's
  // colour (muted by the renderer), so they never burn a palette slot.
  const paletteOrder = graph.order.filter((name) => !(byName.get(name)?.is_archived ?? false))
  const colorIndexOf = (branch: string): number => {
    const rec = byName.get(branch)
    if (rec?.is_archived) {
      const parent = rec.fork_of
      if (parent !== null && parent !== branch) return colorIndexOf(parent)
    }
    const idx = paletteOrder.indexOf(branch)
    return (idx >= 0 ? idx : 0) % LANE_COLOR_COUNT
  }

  // --- Ownership segmentation of the viewed spine (A-8, client side) --------
  // Walk newest→oldest with the viewed branch as owner; the fork-point commit
  // itself belongs to the parent, so the switch happens BEFORE assigning it.
  // The loop follows chained fork points landing on the same commit; the
  // visited set is a pure cycle guard against malformed fork_of chains.
  const ownerBySha = new Map<string, string>()
  const entryBySha = new Map<string, GitGraphBranch["entries"][number]>()
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

  // --- Row indexes ------------------------------------------------------------
  const milestoneRowIndexBySha = new Map<string, number>()
  const rowIndexByKey = new Map<string, number>()
  view.rows.forEach((row, i) => {
    rowIndexByKey.set(`${row.kind}:${row.sha}`, i)
    if (row.kind === "milestone" && !milestoneRowIndexBySha.has(row.sha)) {
      milestoneRowIndexBySha.set(row.sha, i)
    }
  })

  const chainBranches = new Set<string>()
  for (const sha of milestoneRowIndexBySha.keys()) {
    const owner = ownerOf(sha)
    if (owner !== viewedName) chainBranches.add(owner)
  }

  const orderPos = (name: string): number => {
    const idx = graph.order.indexOf(name)
    return idx >= 0 ? idx : Number.MAX_SAFE_INTEGER
  }
  const byOrder = (a: string, b: string): number =>
    orderPos(a) - orderPos(b) || a.localeCompare(b)

  // Each ancestor lane extends to the TOP of the rail (its branch's history
  // continues alongside the viewed one): pass cells on every row above the
  // lane's first owned (fork-point) row.
  const firstOwnedRowIndex = new Map<string, number>()
  for (const [sha, i] of milestoneRowIndexBySha) {
    const owner = ownerOf(sha)
    if (owner === viewedName) continue
    const prev = firstOwnedRowIndex.get(owner)
    if (prev === undefined || i < prev) firstOwnedRowIndex.set(owner, i)
  }

  // Lanes: the viewed branch, then its ancestors NEAREST FIRST (the order the
  // ownership walk meets them down the spine) — the spine then migrates
  // monotonically outward, never crossing back over a lane.
  const laneByBranch = new Map<string, number>([[viewedName, 0]])
  const chainOrdered = [...chainBranches].sort(
    (a, b) =>
      (firstOwnedRowIndex.get(a) ?? Number.MAX_SAFE_INTEGER) -
      (firstOwnedRowIndex.get(b) ?? Number.MAX_SAFE_INTEGER),
  )
  chainOrdered.forEach((name, i) => laneByBranch.set(name, i + 1))
  const laneOf = (branch: string): number => laneByBranch.get(branch) ?? 0
  const laneCount = 1 + chainOrdered.length

  // --- Departures: anchor rows, groups, slots ---------------------------------
  // A departing branch anchors, in preference order, at: the visible row of
  // the SAVE it was spawned from (expanded save or pending save); the parent
  // milestone that folded that save (visible credit while collapsed); its
  // fork-point milestone row. Unresolvable non-archived departures count as
  // overflow; unresolvable archived ones drop silently (they never did draw).
  interface Departure {
    branch: GitGraphBranch
    rowIndex: number
    groupKey: string
    fromSub: boolean
  }
  const resolveAnchor = (b: GitGraphBranch): Omit<Departure, "branch"> | null => {
    const src = b.fork_source_sha ?? null
    if (src !== null) {
      const saveRow = rowIndexByKey.get(`save:${src}`)
      if (saveRow !== undefined && !b.is_archived) {
        const milestoneSha = view.rows[saveRow].milestoneSha ?? ""
        return { rowIndex: saveRow, groupKey: milestoneSha, fromSub: true }
      }
      const pendingRow = rowIndexByKey.get(`pending-save:${src}`)
      if (pendingRow !== undefined && !b.is_archived) {
        return { rowIndex: pendingRow, groupKey: "pending", fromSub: false }
      }
      const credit = b.fork_credit_sha ?? null
      if (credit !== null) {
        const creditRow = milestoneRowIndexBySha.get(credit)
        if (creditRow !== undefined) {
          return { rowIndex: creditRow, groupKey: credit, fromSub: false }
        }
      }
    }
    if (b.fork_point_sha !== null) {
      const pointRow = milestoneRowIndexBySha.get(b.fork_point_sha)
      if (pointRow !== undefined) {
        return { rowIndex: pointRow, groupKey: b.fork_point_sha, fromSub: false }
      }
    }
    return null
  }

  const departures: Departure[] = []
  let overflowCount = 0
  const chips: RailTopChip[] = []
  for (const b of [...graph.branches].sort((a, z) => byOrder(a.name, z.name))) {
    if (b.name === viewedName || chainBranches.has(b.name)) continue
    const anchor = resolveAnchor(b)
    if (anchor === null) {
      if (!b.is_archived) overflowCount += 1
      continue
    }
    departures.push({ branch: b, ...anchor })
    chips.push({
      branch: b.name,
      colorIndex: colorIndexOf(b.name),
      archived: b.is_archived,
    })
  }

  // Slot demand is per anchor GROUP so the reservation is stable whether the
  // group's milestone is collapsed (everything anchors on its row) or open
  // (spawns spread over the save rows): live departures take one slot each,
  // all archived departures of a group share a single muted stub. Rail width
  // reserves the WIDEST group.
  const groupLive = new Map<string, GitGraphBranch[]>()
  const groupArchivedCount = new Map<string, number>()
  for (const d of departures) {
    if (d.branch.is_archived) {
      groupArchivedCount.set(d.groupKey, (groupArchivedCount.get(d.groupKey) ?? 0) + 1)
    } else {
      const list = groupLive.get(d.groupKey) ?? []
      list.push(d.branch)
      groupLive.set(d.groupKey, list)
    }
  }
  let slotCount = 0
  const groupKeys = new Set<string>([...groupLive.keys(), ...groupArchivedCount.keys()])
  for (const key of groupKeys) {
    const demand = (groupLive.get(key)?.length ?? 0) + ((groupArchivedCount.get(key) ?? 0) > 0 ? 1 : 0)
    slotCount = Math.max(slotCount, demand)
  }
  const slotOf = (groupKey: string, branch: string): number =>
    (groupLive.get(groupKey) ?? []).findIndex((b) => b.name === branch)

  // Stub cells per row. Archived departures collapse to one stub per group,
  // anchored at the group's milestone row (never a save row), in the last
  // slot of the group, drawn muted in the parent's colour.
  const stubsByRowIndex = new Map<number, RailSpawnStubCell[]>()
  const pushStub = (rowIndex: number, cell: RailSpawnStubCell) => {
    const list = stubsByRowIndex.get(rowIndex) ?? []
    list.push(cell)
    stubsByRowIndex.set(rowIndex, list)
  }
  const archivedEmitted = new Set<string>()
  for (const d of departures) {
    if (d.branch.is_archived) {
      if (archivedEmitted.has(d.groupKey)) continue
      archivedEmitted.add(d.groupKey)
      const milestoneRow = milestoneRowIndexBySha.get(d.groupKey) ?? d.rowIndex
      const ownerName = view.rows[milestoneRow]?.kind === "milestone"
        ? ownerOf(view.rows[milestoneRow].sha)
        : viewedName
      pushStub(milestoneRow, {
        kind: "spawn-stub",
        fromLane: laneOf(ownerName),
        fromSub: false,
        slot: groupLive.get(d.groupKey)?.length ?? 0,
        branch: d.branch.fork_of ?? viewedName,
        colorIndex: colorIndexOf(d.branch.name),
        archived: true,
        count: groupArchivedCount.get(d.groupKey) ?? 1,
      })
      continue
    }
    const row = view.rows[d.rowIndex]
    // Save rows inherit the lane of the milestone they are expanded under;
    // pending saves live on the viewed lane.
    const ownerName =
      row.kind === "milestone"
        ? ownerOf(row.sha)
        : row.milestoneSha !== undefined
          ? ownerOf(row.milestoneSha)
          : viewedName
    pushStub(d.rowIndex, {
      kind: "spawn-stub",
      fromLane: laneOf(ownerName),
      fromSub: d.fromSub,
      slot: slotOf(d.groupKey, d.branch.name),
      branch: d.branch.name,
      colorIndex: colorIndexOf(d.branch.name),
      archived: false,
      count: 1,
    })
  }

  const lanes: RailLane[] = [
    { branch: viewedName, lane: 0, colorIndex: colorIndexOf(viewedName) },
    ...chainOrdered.map((name, i) => ({
      branch: name,
      lane: i + 1,
      colorIndex: colorIndexOf(name),
    })),
  ]

  // --- Magnifiers (A-4) -------------------------------------------------------
  // Toggle affordance on every milestone that folds saves (>= 2 parents — the
  // engine never commits an empty fold). Collapsed → zoom-in on the lower
  // edge, provided a lower edge exists (the window-final row has none);
  // expanded → zoom-out at the same anchor, always.
  const magnifierByRowIndex = new Map<number, RailMagnifier>()
  const milestoneRowIndexes = [...milestoneRowIndexBySha.values()].sort((a, b) => a - b)
  milestoneRowIndexes.forEach((rowIndex, k) => {
    const row = view.rows[rowIndex]
    const expanded = row.expanded === true
    if (!expanded && k + 1 >= milestoneRowIndexes.length) return
    if ((entryBySha.get(row.sha)?.parents.length ?? 0) < 2) return
    const owner = ownerOf(row.sha)
    magnifierByRowIndex.set(rowIndex, {
      expandsSha: row.sha,
      lane: laneOf(owner),
      colorIndex: colorIndexOf(owner),
      expanded,
    })
  })

  // --- Per-row cells ----------------------------------------------------------
  // Running spine state: the owner lane of the most recent milestone row (save
  // and placeholder rows continue that lane), and the previous milestone's
  // owner for transition detection. Before the first milestone row the spine
  // is the viewed branch itself (pending rows draw on lane 0).
  let spineBranch = viewedName
  let prevMilestoneOwner = viewedName
  // Whether a dotted rail stretch is open: the PREVIOUS milestone was
  // expanded with real save rows (its siding is showing), so the rail beside
  // it — down to and into the next same-lane milestone dot — is dotted.
  let prevEdgeDotted = false
  // Once the terminal (root) dot is emitted the spine lane ends: save and
  // placeholder rows below it draw no spine cell.
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
        const crossLane = owner !== prevMilestoneOwner
        if (crossLane) {
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
        // The rail is dotted exactly where the siding shows: below an
        // expanded milestone with real save rows following.
        const lowerDotted = row.expanded === true && view.rows[i + 1]?.kind === "save"
        cells.push({
          kind: "dot",
          lane: laneOf(owner),
          branch: owner,
          colorIndex: colorIndexOf(owner),
          sha: row.sha,
          terminal,
          // A dotted stretch feeds in from above only on the SAME lane — a
          // lane change is carried by the (always solid) transition curve.
          upperDotted: prevEdgeDotted && !crossLane,
          lowerDotted,
        })
        // Siding curves — TIME FLOWS UP the list, so a milestone's fold
        // merge joins its dot FROM BELOW (its own saves, displayed beneath
        // it, committed INTO it): the fold-in. The fold-out — the siding
        // arriving from an expanded range ABOVE — marks the ledger BRANCHING
        // OFF this milestone (after it, so upward), and departs from the
        // RANGE's lane, which differs from this milestone's own lane across
        // an ownership transition.
        //
        // DOUBLY-EXPANDED SAME LANE: when this milestone has BOTH a save row
        // directly above AND below and the incoming siding runs on this
        // milestone's OWN lane (same-lane continuation, not an ownership
        // transition), the save ledger runs straight through: the siding
        // passes the full row height as one solid line, joined to the dot by
        // the single fold-in merge from below — the upward side stays a
        // clean continuing line (the branch-off), never obscuring the
        // milestone commit. We suppress the fold-out pinch and emit a
        // siding-pass so computeRailRuns consolidates one continuous siding
        // run across both adjacent ranges. Across an ownership transition
        // (or when only one side is expanded) we keep the original fold-in +
        // fold-out behaviour.
        const belowIsSave = view.rows[i + 1]?.kind === "save"
        const above = view.rows[i - 1]
        const aboveIsSave = above?.kind === "save"
        const rangeOwner =
          aboveIsSave && above.milestoneSha !== undefined ? ownerOf(above.milestoneSha) : owner
        const sameLaneThrough =
          belowIsSave && aboveIsSave && laneOf(rangeOwner) === laneOf(owner)
        if (belowIsSave) {
          cells.push({ kind: "fold-in", lane: laneOf(owner), branch: owner, colorIndex: colorIndexOf(owner) })
        }
        if (aboveIsSave && !sameLaneThrough) {
          cells.push({
            kind: "fold-out",
            lane: laneOf(owner),
            fromLane: laneOf(rangeOwner),
            branch: rangeOwner,
            colorIndex: colorIndexOf(rangeOwner),
          })
        }
        if (sameLaneThrough) {
          cells.push({
            kind: "siding-pass",
            lane: laneOf(owner),
            branch: owner,
            colorIndex: colorIndexOf(owner),
          })
        }
        magnifier = magnifierByRowIndex.get(i)
        prevMilestoneOwner = owner
        prevEdgeDotted = lowerDotted
        spineBranch = owner
        if (terminal) spineEnded = true
        break
      }
      case "save":
        if (!spineEnded) {
          // The rail crossing a save row runs beside the siding — dotted.
          cells.push({
            kind: "pass",
            lane: laneOf(spineBranch),
            branch: spineBranch,
            colorIndex: colorIndexOf(spineBranch),
            dotted: true,
          })
          cells.push({
            kind: "save-dot",
            lane: laneOf(spineBranch),
            branch: spineBranch,
            colorIndex: colorIndexOf(spineBranch),
            sha: row.sha,
            last: view.rows[i + 1]?.kind !== "save" && view.rows[i + 1]?.kind !== "milestone",
          })
        }
        break
      case "placeholder":
        if (!spineEnded) {
          // No siding on a loading/empty placeholder — the rail stays solid.
          cells.push({
            kind: "pass",
            lane: laneOf(spineBranch),
            branch: spineBranch,
            colorIndex: colorIndexOf(spineBranch),
            dotted: false,
          })
        }
        break
    }

    // Ancestor lanes run from their fork-point row to the very top; below
    // that row they ARE the spine (ownership), so only upper passes are
    // synthesised here.
    for (const name of chainOrdered) {
      const first = firstOwnedRowIndex.get(name)
      if (first !== undefined && i < first) {
        cells.push({
          kind: "pass",
          lane: laneOf(name),
          branch: name,
          colorIndex: colorIndexOf(name),
          dotted: false,
        })
      }
    }

    cells.push(...(stubsByRowIndex.get(i) ?? []))
    return { cells, ...(magnifier && { magnifier }) }
  })

  return {
    rows,
    laneCount,
    slotCount,
    overflowCount,
    topChips: chips,
    lanes,
    viewBranch: viewedName,
    viewedIsArchived: viewed.is_archived,
  }
}

// ─── Vertical run consolidation ─────────────────────────────────────────────

/** Measured geometry of one visual row, relative to the box the overlay
 *  spans: `top` of the rail cell, its `height`, and the row's node-centre
 *  `dotY` (the same value the per-row cell renders with). Rows outside the
 *  overlay's box (e.g. pending rows measured as part of another box) pass
 *  null and contribute no runs. */
export interface RailRowGeom {
  top: number
  height: number
  dotY: number
}

/** One consolidated vertical line: a contiguous same-style stretch of a lane
 *  (kind "spine") or of the saves siding (kind "siding"), drawn ONCE so dash
 *  phase holds across every row and box border it crosses. */
export interface RailRun {
  kind: "spine" | "siding"
  x: number
  y1: number
  y2: number
  dotted: boolean
  branch: string
  colorIndex: number
}

/** Adjacent segments whose gap is at most this many px merge into one run —
 *  bridges the 1px dividers between commit boxes. */
const RUN_MERGE_TOLERANCE = 2

/**
 * Consolidate every straight vertical segment implied by the rail cells into
 * whole-length runs, using the measured row geometry. Curves, dots and stubs
 * stay per-row; this produces only the long lines.
 */
export function computeRailRuns(model: RailModel, geom: (RailRowGeom | null)[]): RailRun[] {
  interface Segment {
    kind: RailRun["kind"]
    x: number
    y1: number
    y2: number
    dotted: boolean
    branch: string
    colorIndex: number
  }
  const segments: Segment[] = []

  model.rows.forEach((row, i) => {
    const g = geom[i]
    if (!g) return
    const rowTop = g.top
    const rowBottom = g.top + g.height
    const dotAbs = g.top + g.dotY
    for (const cell of row.cells) {
      switch (cell.kind) {
        case "dot": {
          const x = laneX(cell.lane)
          segments.push({
            kind: "spine",
            x,
            y1: rowTop,
            y2: dotAbs,
            dotted: cell.upperDotted,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          if (!cell.terminal) {
            segments.push({
              kind: "spine",
              x,
              y1: dotAbs,
              y2: rowBottom,
              dotted: cell.lowerDotted,
              branch: cell.branch,
              colorIndex: cell.colorIndex,
            })
          }
          break
        }
        case "hollow-dot":
        case "pass": {
          segments.push({
            kind: "spine",
            x: laneX(cell.lane),
            y1: rowTop,
            y2: rowBottom,
            dotted: cell.kind === "pass" ? cell.dotted : false,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          break
        }
        case "save-dot": {
          segments.push({
            kind: "siding",
            x: laneX(cell.lane) + SAVE_RAIL_DX,
            y1: rowTop,
            y2: cell.last ? dotAbs : rowBottom,
            dotted: false,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          break
        }
        case "fold-in": {
          // The straight tail below the fold curve, down to the row bottom
          // where the first save row's siding picks it up.
          segments.push({
            kind: "siding",
            x: laneX(cell.lane) + SAVE_RAIL_DX,
            y1: dotAbs + FOLD_RISE,
            y2: rowBottom,
            dotted: false,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          break
        }
        case "fold-out": {
          // The straight lead-in above the fold curve, from the row top.
          segments.push({
            kind: "siding",
            x: laneX(cell.fromLane) + SAVE_RAIL_DX,
            y1: rowTop,
            y2: Math.max(rowTop, dotAbs - FOLD_RISE),
            dotted: false,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          break
        }
        case "siding-pass": {
          // The siding runs straight through a doubly-expanded milestone's
          // row: a full-height segment so it consolidates with the ranges
          // above and below into one continuous run.
          segments.push({
            kind: "siding",
            x: laneX(cell.lane) + SAVE_RAIL_DX,
            y1: rowTop,
            y2: rowBottom,
            dotted: false,
            branch: cell.branch,
            colorIndex: cell.colorIndex,
          })
          break
        }
        case "transition":
        case "spawn-stub":
          break
      }
    }
  })

  // Merge touching same-style segments on the same x into single runs.
  segments.sort((a, b) => a.x - b.x || a.y1 - b.y1)
  const runs: RailRun[] = []
  for (const s of segments) {
    if (s.y2 - s.y1 <= 0) continue
    const prev = runs[runs.length - 1]
    if (
      prev !== undefined &&
      prev.x === s.x &&
      prev.kind === s.kind &&
      prev.dotted === s.dotted &&
      prev.branch === s.branch &&
      s.y1 - prev.y2 <= RUN_MERGE_TOLERANCE
    ) {
      prev.y2 = Math.max(prev.y2, s.y2)
    } else {
      runs.push({ ...s })
    }
  }
  return runs
}
