/**
 * Rendering for the Version Control panel's graph rail (D-B): a fixed-width
 * SVG cell drawn as the first flex child of every history row, plus the slim
 * header strip of peekable branch chips above the list, plus the measured
 * OVERLAY that owns every straight vertical line. Pure presentation over the
 * RailModel / RailRun data from ./layout — no fetching, no store access.
 *
 * Division of labour: per-row cells draw only NODES (dots) and CURVES
 * (transitions, fold merges, spawn stubs); every straight vertical line is a
 * consolidated run drawn once by GraphRailOverlay so dash phase and stroke
 * continuity hold across row and box boundaries (and each run costs one SVG
 * element). Pending rows live in a box without an overlay, so the hollow-dot
 * cell still draws its own (solid, contiguous) line.
 *
 * Interaction contract (feedback round 2): the rail is INERT to left-click —
 * the only left-click affordance is the magnifier (expand/collapse toggle).
 * Right-click is context-driven: a milestone dot offers the commit actions
 * (view side-by-side / move), any lane line offers its branch's actions
 * (switch / view). Handlers stopPropagation so the enclosing row button
 * never sees rail clicks.
 */

import { ZoomIn, ZoomOut } from "lucide-react"
import { memo } from "react"
import type { ReactNode } from "react"
import type { RailCell, RailMagnifier, RailRow, RailRun, RailTopChip } from "./layout"
import { FOLD_RISE, SAVE_RAIL_DX, laneX, slotTightX } from "./layout"

const laneColor = (colorIndex: number): string => `var(--git-lane-${colorIndex})`

/** Cubic curve entering at (x0, y0) and landing at (x1, y1), bending on y. */
const curveD = (x0: number, x1: number, y0: number, y1: number): string => {
  const my = (y0 + y1) / 2
  return `M ${x0} ${y0} C ${x0} ${my}, ${x1} ${my}, ${x1} ${y1}`
}

const STROKE = 1.5
/** The saves siding is real material (solid) but secondary — thinner. */
const SUB_STROKE = 1
/** Dash pattern for the milestone rail where it runs BESIDE the siding (the
 *  inactive of the two parallel lines across an expanded range). Only ever
 *  applied to whole overlay runs, so the phase never breaks. */
const DOTTED = "1.5 3.5"
/** Spawn stubs are chrome around the viewed line — drawn dimmed. */
const STUB_OPACITY = 0.75
const ARCHIVED_OPACITY = 0.4

/** Spawn-stub curve shape — three hand-tunable knobs.
 *
 * The stub is a cubic bezier from the node to the row-top terminus:
 *   P0 = (fromX, dotY)   — the spawn node
 *   P3 = (tightX, 0)     — the row-top terminus at the tight pitch
 *   dx = tightX - fromX  — horizontal reach (signed)
 *
 * Handle length grows with reach:
 *   L = STUB_HANDLE_BASE + STUB_HANDLE_DX_FACTOR * |dx|
 *
 * The DEPARTURE handle launches out of the node at STUB_HANDLE_ANGLE_DEG above
 * horizontal, toward the terminus side:
 *   C1 = (fromX + cos(A°)·L·sign(dx), dotY - sin(A°)·L)
 * The ARRIVAL handle is vertical, the same length, pointing back down the line,
 * so the stub lands upright at the terminus:
 *   C2 = (tightX, L)
 * Path: M P0 C C1, C2, P3.
 */
export const STUB_HANDLE_ANGLE_DEG = 50 // A: launch angle of the departure handle, degrees above horizontal
export const STUB_HANDLE_BASE = 6 // B: handle length floor, px
export const STUB_HANDLE_DX_FACTOR = 0.35 // C: handle length gained per px of horizontal reach

export interface GraphRailCellProps {
  /** This row's slice of the rail; undefined draws an empty spacer so the
   *  content column stays aligned. */
  row: RailRow | undefined
  width: number
  /** y of this row's node centre (the first text line of the row content). */
  dotY: number
  /** RailModel.laneCount — fixes the x origin of the spawn-slot region. */
  laneCount: number
  /** RailModel.viewedIsArchived — grey the whole cell. */
  dimmed: boolean
  onToggleExpand: (sha: string) => void
  /** Right-click on a milestone dot: commit actions (view / move). */
  onDotContextMenu: (sha: string, x: number, y: number) => void
  /** Right-click on a lane line: branch actions (switch / view). */
  onLaneContextMenu: (branch: string, x: number, y: number) => void
}

export const GraphRailCell = memo(function GraphRailCell({
  row,
  width,
  dotY,
  laneCount,
  dimmed,
  onToggleExpand,
  onDotContextMenu,
  onLaneContextMenu,
}: GraphRailCellProps) {
  const laneMenu = (branch: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    onLaneContextMenu(branch, e.clientX, e.clientY)
  }

  // Curves first, nodes second, so dots paint over the lines meeting them.
  // Straight vertical lines belong to the overlay (see module docstring) —
  // the pending box's hollow-dot is the one exception.
  const edges = (cell: RailCell, key: number): ReactNode => {
    switch (cell.kind) {
      case "hollow-dot": {
        const x = laneX(cell.lane)
        return (
          <line
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind="spine"
            data-branch={cell.branch}
            x1={x}
            y1={0}
            x2={x}
            y2="100%"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={STROKE}
            onContextMenu={laneMenu(cell.branch)}
          />
        )
      }
      case "transition":
        // Path runs M row-top → dot. SVG dash phase starts at the path start,
        // so a dotted transition is phase-0 exactly at the row-top junction
        // where it meets the dotted overlay run coming down from above — the
        // pattern crosses the seam without a phase break.
        return (
          <path
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind="transition"
            data-branch={cell.branch}
            data-from-lane={cell.fromLane}
            data-to-lane={cell.toLane}
            d={curveD(laneX(cell.fromLane), laneX(cell.toLane), 0, dotY)}
            fill="none"
            stroke={laneColor(cell.fromColorIndex)}
            strokeWidth={STROKE}
            strokeDasharray={cell.dotted ? DOTTED : undefined}
            onContextMenu={laneMenu(cell.branch)}
          />
        )
      case "fold-in": {
        // Out of the dot, onto the siding; the straight tail below is an
        // overlay run.
        const x = laneX(cell.lane)
        return (
          <path
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind="sub-rail"
            data-branch={cell.branch}
            d={curveD(x, x + SAVE_RAIL_DX, dotY, dotY + FOLD_RISE)}
            fill="none"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={SUB_STROKE}
            onContextMenu={laneMenu(cell.branch)}
          />
        )
      }
      case "fold-out": {
        // The siding arrives from the save rows above (straight lead-in is
        // an overlay run) and merges into this milestone's dot — possibly
        // across lanes at an ownership transition.
        const sx = laneX(cell.fromLane) + SAVE_RAIL_DX
        return (
          <path
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind="sub-rail"
            data-branch={cell.branch}
            d={curveD(sx, laneX(cell.lane), Math.max(0, dotY - FOLD_RISE), dotY)}
            fill="none"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={SUB_STROKE}
            onContextMenu={laneMenu(cell.branch)}
          />
        )
      }
      case "spawn-stub": {
        // A single solid curve from the spawn node to the tight-pitch terminus
        // at the very top of the row. Shape is driven by the three STUB_HANDLE_*
        // knobs above: a departure handle launched at STUB_HANDLE_ANGLE_DEG and a
        // vertical arrival handle so the stub lands upright. The bezier draws only
        // into the tight envelope (slotTightX), which also sets the rail's right
        // edge — no separate flare envelope.
        const fromX = laneX(cell.fromLane) + (cell.fromSub ? SAVE_RAIL_DX : 0)
        const tightX = slotTightX(cell.slot, laneCount)
        const dx = tightX - fromX
        const sign = dx === 0 ? 0 : Math.sign(dx)
        const rad = (STUB_HANDLE_ANGLE_DEG * Math.PI) / 180
        const L = STUB_HANDLE_BASE + STUB_HANDLE_DX_FACTOR * Math.abs(dx)
        const c1x = fromX + Math.cos(rad) * L * sign
        const c1y = dotY - Math.sin(rad) * L
        const c2x = tightX
        const c2y = L
        return (
          <g
            key={key}
            data-testid="git-graph-spawn"
            data-branch={cell.branch}
            data-slot={cell.slot}
            data-archived={cell.archived || undefined}
            data-count={cell.count}
            opacity={cell.archived ? ARCHIVED_OPACITY : STUB_OPACITY}
          >
            <path
              data-testid="git-graph-edge"
              data-edge-kind="spawn"
              data-branch={cell.branch}
              d={`M ${fromX} ${dotY} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${tightX} 0`}
              fill="none"
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
            />
            {cell.count > 1 && (
              <text x={tightX + 3} y={dotY - 6} fontSize={8} fill={laneColor(cell.colorIndex)}>
                ×{cell.count}
              </text>
            )}
          </g>
        )
      }
      case "dot":
      case "pass":
      case "save-dot":
      case "siding-pass":
        return null
    }
  }

  const node = (cell: RailCell, key: number): ReactNode => {
    switch (cell.kind) {
      case "dot":
        return (
          <circle
            key={key}
            data-testid="git-graph-dot"
            data-sha={cell.sha}
            data-lane={cell.lane}
            data-branch={cell.branch}
            data-color-index={cell.colorIndex}
            data-kind="milestone"
            onContextMenu={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onDotContextMenu(cell.sha, e.clientX, e.clientY)
            }}
            cx={laneX(cell.lane)}
            cy={dotY}
            r={3.5}
            fill={laneColor(cell.colorIndex)}
          />
        )
      case "hollow-dot":
        return (
          <circle
            key={key}
            data-testid="git-graph-dot"
            data-sha={cell.sha}
            data-lane={cell.lane}
            data-branch={cell.branch}
            data-color-index={cell.colorIndex}
            data-kind="pending"
            cx={laneX(cell.lane)}
            cy={dotY}
            r={3.5}
            fill="var(--bg-panel)"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={STROKE}
          />
        )
      case "save-dot":
        return (
          <circle
            key={key}
            data-testid="git-graph-dot"
            data-sha={cell.sha}
            data-lane={cell.lane}
            data-branch={cell.branch}
            data-color-index={cell.colorIndex}
            data-kind="save"
            onContextMenu={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onLaneContextMenu(cell.branch, e.clientX, e.clientY)
            }}
            cx={laneX(cell.lane) + SAVE_RAIL_DX}
            cy={dotY}
            r={2.5}
            fill={laneColor(cell.colorIndex)}
          />
        )
      case "pass":
      case "transition":
      case "fold-in":
      case "fold-out":
      case "siding-pass":
      case "spawn-stub":
        return null
    }
  }

  return (
    <div
      data-testid="git-graph-rail"
      data-rail-row
      data-dot-y={dotY}
      className="relative shrink-0 self-stretch"
      style={{ width, opacity: dimmed ? 0.55 : undefined }}
      // The rail is inert to left-click: swallow it so the enclosing row
      // button doesn't treat a rail click as a row toggle. The magnifier is
      // the one left-click affordance and handles itself before this fires.
      onClick={(e) => e.stopPropagation()}
    >
      {row && (
        <svg className="absolute inset-0 w-full h-full overflow-visible">
          {row.cells.map(edges)}
          {row.cells.map(node)}
        </svg>
      )}
      {row?.magnifier && <Magnifier magnifier={row.magnifier} dotY={dotY} onToggle={onToggleExpand} />}
    </div>
  )
})

/** The measured overlay: one absolutely-positioned SVG spanning a whole
 *  history box, drawing every consolidated vertical run as a single line —
 *  dash phase and stroke continuity hold across all the rows and 1px box
 *  borders the run crosses. Lines stay right-clickable for the lane menu;
 *  the SVG itself swallows nothing. */
export function GraphRailOverlay({
  runs,
  dimmed,
  onLaneContextMenu,
}: {
  runs: RailRun[]
  dimmed: boolean
  onLaneContextMenu: (branch: string, x: number, y: number) => void
}) {
  return (
    <svg
      data-testid="git-graph-overlay"
      className="absolute inset-y-0 left-0 overflow-visible"
      style={{ width: 1, height: "100%", opacity: dimmed ? 0.55 : undefined, pointerEvents: "none" }}
    >
      {runs.map((run, i) => (
        // Dotted runs render BOTTOM-anchored: swap the endpoints (y1 = run.y2,
        // y2 = run.y1) so the dash phase is 0 at the run's BOTTOM end. A dotted
        // run either ends under a milestone dot (both ends hidden by the dot,
        // direction irrelevant) or ends at a cross-lane transition row's top,
        // where the dotted transition curve begins — with the run phase-0 at
        // that junction and the curve phase-0 there too, the pattern crosses
        // the seam without a phase break (both dotted elements radiate outward
        // from the point the curves touch). Solid runs keep top→bottom.
        <line
          key={i}
          data-testid="git-graph-edge"
          data-edge-kind={run.kind === "siding" ? "sub-rail" : "spine"}
          data-branch={run.branch}
          data-run
          x1={run.x}
          y1={run.dotted ? run.y2 : run.y1}
          x2={run.x}
          y2={run.dotted ? run.y1 : run.y2}
          stroke={laneColor(run.colorIndex)}
          strokeWidth={run.kind === "siding" ? SUB_STROKE : STROKE}
          strokeDasharray={run.dotted ? DOTTED : undefined}
          style={{ pointerEvents: "stroke" }}
          onContextMenu={(e) => {
            e.preventDefault()
            e.stopPropagation()
            onLaneContextMenu(run.branch, e.clientX, e.clientY)
          }}
        />
      ))}
    </svg>
  )
}

// Expand/collapse toggle on a fold-carrying milestone (A-4): sits in the
// left fold-gutter, anchored to the row TOP (never the bottom — the anchor
// must not depend on anything that changes when the row's surroundings
// expand, so the button is a stable toggle). Neutral chrome on a grey
// darker than the blue-cast panel backgrounds.
function Magnifier({
  magnifier,
  dotY,
  onToggle,
}: {
  magnifier: RailMagnifier
  dotY: number
  onToggle: (sha: string) => void
}) {
  return (
    <span
      role="button"
      tabIndex={0}
      data-testid="git-graph-magnifier"
      data-expands={magnifier.expandsSha}
      data-expanded={magnifier.expanded || undefined}
      title={
        magnifier.expanded
          ? "Hide the saves folded into this milestone"
          : "Show the saves folded into this milestone"
      }
      onClick={(e) => {
        e.stopPropagation()
        onToggle(magnifier.expandsSha)
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          e.stopPropagation()
          onToggle(magnifier.expandsSha)
        }
      }}
      className="absolute z-10 inline-flex items-center justify-center rounded-full cursor-pointer"
      style={{
        left: laneX(magnifier.lane) - 22,
        top: dotY + 10,
        width: 16,
        height: 16,
        background: "var(--git-magnifier-bg)",
        border: "1px solid var(--border)",
        color: "var(--text-muted)",
      }}
    >
      {magnifier.expanded ? <ZoomOut size={11} /> : <ZoomIn size={11} />}
    </span>
  )
}

/** Slim strip above the history list: one peekable chip per departing branch
 *  (click = peek, mirroring ForkLinks) plus the "+N elsewhere" overflow.
 *  Chips wear their branch's lane colour; archived chips are muted and carry
 *  the parent branch's colour (they never burn a palette slot). */
export function GraphRailHeader({
  topChips,
  overflowCount,
  onPeek,
}: {
  topChips: RailTopChip[]
  overflowCount: number
  onPeek: (branch: string) => void
}) {
  if (topChips.length === 0 && overflowCount === 0) return null
  return (
    <div data-testid="git-graph-header" className="flex flex-wrap items-center gap-1 px-1">
      {topChips.map((c) => (
        <span
          key={c.branch}
          role="button"
          tabIndex={0}
          data-testid="git-graph-branch-chip"
          data-branch={c.branch}
          data-archived={c.archived || undefined}
          title={c.archived ? `View ${c.branch} (archived)` : `View ${c.branch}`}
          onClick={() => onPeek(c.branch)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault()
              e.stopPropagation()
              onPeek(c.branch)
            }
          }}
          className="inline-flex items-center gap-1 px-1 py-0.5 rounded text-[10px] font-mono max-w-[140px] cursor-pointer hover:underline"
          style={{
            background: "var(--chip-rest)",
            border: `1px solid ${laneColor(c.colorIndex)}`,
            color: laneColor(c.colorIndex),
            opacity: c.archived ? 0.55 : 1,
          }}
        >
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ background: laneColor(c.colorIndex) }}
          />
          <span className="truncate">{c.branch.split("/").pop() ?? c.branch}</span>
        </span>
      ))}
      {overflowCount > 0 && (
        <span
          data-testid="git-graph-overflow"
          title="Branches whose fork point is outside the visible history"
          className="text-[10px] px-1 py-0.5 rounded font-mono"
          style={{ border: "1px dashed var(--border)", color: "var(--text-muted)" }}
        >
          +{overflowCount} elsewhere
        </span>
      )}
    </div>
  )
}
