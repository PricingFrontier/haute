/**
 * Rendering for the Version Control panel's graph rail (D-B): a fixed-width
 * SVG cell drawn as the first flex child of every history row, plus the slim
 * header strip of peekable branch chips above the list. Pure presentation
 * over the RailModel from ./layout — no fetching, no store access.
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
import type { RailCell, RailMagnifier, RailRow, RailTopChip } from "./layout"
import { SAVE_RAIL_DX, laneX, slotX } from "./layout"

const laneColor = (colorIndex: number): string => `var(--git-lane-${colorIndex})`

/** Cubic curve entering at (x0, y0) and landing at (x1, y1), bending on y. */
const curveD = (x0: number, x1: number, y0: number, y1: number): string => {
  const my = (y0 + y1) / 2
  return `M ${x0} ${y0} C ${x0} ${my}, ${x1} ${my}, ${x1} ${y1}`
}

const STROKE = 1.5
/** Dash pattern for ledger material (sub-rail, spawn stubs) — sparse, so the
 *  dotted lines read as secondary to the solid spine. */
const DOTTED = "1.5 3.5"
/** Spawn stubs are chrome around the viewed line — drawn dimmed. */
const STUB_OPACITY = 0.75
const ARCHIVED_OPACITY = 0.4
/** The stub's curve leaves the node and climbs this far before the dotted
 *  tail takes over towards the row top. */
const STUB_RISE = 10
/** Vertical run the fold curves take between a milestone dot and the
 *  sub-rail (small, so the merge reads as part of the dot). */
const FOLD_RISE = 12

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

  // Edges first, nodes second, so dots paint over the lines meeting them.
  const edges = (cell: RailCell, key: number): ReactNode => {
    switch (cell.kind) {
      case "dot": {
        const x = laneX(cell.lane)
        return (
          <g key={key} onContextMenu={laneMenu(cell.branch)}>
            <line
              data-testid="git-graph-edge"
              data-edge-kind="spine"
              data-branch={cell.branch}
              x1={x}
              y1={0}
              x2={x}
              y2={dotY}
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
            />
            {!cell.terminal && (
              <line
                data-testid="git-graph-edge"
                data-edge-kind="spine"
                data-branch={cell.branch}
                x1={x}
                y1={dotY}
                x2={x}
                y2="100%"
                stroke={laneColor(cell.colorIndex)}
                strokeWidth={STROKE}
              />
            )}
          </g>
        )
      }
      case "hollow-dot":
      case "pass": {
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
            onContextMenu={laneMenu(cell.branch)}
          />
        )
      case "save-dot": {
        const sx = laneX(cell.lane) + SAVE_RAIL_DX
        return (
          <g key={key} onContextMenu={laneMenu(cell.branch)}>
            <line
              data-testid="git-graph-edge"
              data-edge-kind="sub-rail"
              data-branch={cell.branch}
              x1={sx}
              y1={0}
              x2={sx}
              y2={dotY}
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
            {!cell.last && (
              <line
                data-testid="git-graph-edge"
                data-edge-kind="sub-rail"
                data-branch={cell.branch}
                x1={sx}
                y1={dotY}
                x2={sx}
                y2="100%"
                stroke={laneColor(cell.colorIndex)}
                strokeWidth={STROKE}
                strokeDasharray={DOTTED}
              />
            )}
          </g>
        )
      }
      case "fold-in": {
        // Out of the dot, down onto the sub-rail, then dotted to the row
        // bottom where the first save row's line picks it up.
        const x = laneX(cell.lane)
        const sx = x + SAVE_RAIL_DX
        return (
          <g key={key} onContextMenu={laneMenu(cell.branch)}>
            <path
              data-testid="git-graph-edge"
              data-edge-kind="sub-rail"
              data-branch={cell.branch}
              d={curveD(x, sx, dotY, dotY + FOLD_RISE)}
              fill="none"
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
            <line
              data-testid="git-graph-edge"
              data-edge-kind="sub-rail"
              data-branch={cell.branch}
              x1={sx}
              y1={dotY + FOLD_RISE}
              x2={sx}
              y2="100%"
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
          </g>
        )
      }
      case "fold-out": {
        // The sub-rail arrives from the save rows above and merges into this
        // milestone's dot.
        const x = laneX(cell.lane)
        const sx = x + SAVE_RAIL_DX
        const kneeY = Math.max(0, dotY - FOLD_RISE)
        return (
          <g key={key} onContextMenu={laneMenu(cell.branch)}>
            <line
              data-testid="git-graph-edge"
              data-edge-kind="sub-rail"
              data-branch={cell.branch}
              x1={sx}
              y1={0}
              x2={sx}
              y2={kneeY}
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
            <path
              data-testid="git-graph-edge"
              data-edge-kind="sub-rail"
              data-branch={cell.branch}
              d={curveD(sx, x, kneeY, dotY)}
              fill="none"
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
          </g>
        )
      }
      case "spawn-stub": {
        const fromX = laneX(cell.fromLane) + (cell.fromSub ? SAVE_RAIL_DX : 0)
        const sx = slotX(cell.slot, laneCount)
        const kneeY = Math.max(2, dotY - STUB_RISE)
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
              d={curveD(fromX, sx, dotY, kneeY)}
              fill="none"
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
            />
            <line
              data-testid="git-graph-edge"
              data-edge-kind="spawn"
              data-branch={cell.branch}
              x1={sx}
              y1={kneeY}
              x2={sx}
              y2={2}
              stroke={laneColor(cell.colorIndex)}
              strokeWidth={STROKE}
              strokeDasharray={DOTTED}
            />
            {cell.count > 1 && (
              <text x={sx + 3} y={kneeY - 2} fontSize={8} fill={laneColor(cell.colorIndex)}>
                ×{cell.count}
              </text>
            )}
          </g>
        )
      }
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
      case "spawn-stub":
        return null
    }
  }

  return (
    <div
      data-testid="git-graph-rail"
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
      {row?.magnifier && <Magnifier magnifier={row.magnifier} onToggle={onToggleExpand} />}
    </div>
  )
})

// Expand/collapse toggle on a fold-carrying milestone (A-4): sits at the
// bottom edge of the milestone row's cell, on its lane. Deliberately NEUTRAL
// chrome — the coloured rails stay calm and the button reads as a control,
// not another graph element.
function Magnifier({
  magnifier,
  onToggle,
}: {
  magnifier: RailMagnifier
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
        left: laneX(magnifier.lane) - 10,
        bottom: -10,
        width: 20,
        height: 20,
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
        color: "var(--text-muted)",
      }}
    >
      {magnifier.expanded ? <ZoomOut size={12} /> : <ZoomIn size={12} />}
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
