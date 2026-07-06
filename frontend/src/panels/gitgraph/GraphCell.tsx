/**
 * Rendering for the Version Control panel's graph rail (D-B): a fixed-width
 * SVG cell drawn as the first flex child of every history row, plus the slim
 * header strip of peekable branch chips above the list. Pure presentation
 * over the RailModel from ./layout — no fetching, no store access.
 *
 * Interactive pieces (milestone dots, the magnifier) live INSIDE the
 * milestone row's <button>, so they are role="button" elements with
 * stopPropagation — never nested <button>s (ViewVersionButton idiom).
 */

import { ZoomIn } from "lucide-react"
import { memo } from "react"
import type { ReactNode } from "react"
import type { RailCell, RailMagnifier, RailRow, RailTopChip } from "./layout"
import { LANE_WIDTH, RAIL_GUTTER } from "./layout"

const laneX = (lane: number): number => RAIL_GUTTER / 2 + lane * LANE_WIDTH + LANE_WIDTH / 2

const laneColor = (colorIndex: number): string => `var(--git-lane-${colorIndex})`

/** Cubic curve entering at (fromLane, y0) and landing at (toLane, y1). */
const curveD = (fromLane: number, toLane: number, y0: number, y1: number): string => {
  const fx = laneX(fromLane)
  const tx = laneX(toLane)
  const my = (y0 + y1) / 2
  return `M ${fx} ${y0} C ${fx} ${my}, ${tx} ${my}, ${tx} ${y1}`
}

const STROKE = 1.5
/** Departure lanes are chrome around the viewed line — drawn dimmed. */
const DEPARTURE_OPACITY = 0.6

export interface GraphRailCellProps {
  /** This row's slice of the rail; undefined draws an empty spacer so the
   *  content column stays aligned. */
  row: RailRow | undefined
  width: number
  /** y of this row's node centre (the first text line of the row content). */
  dotY: number
  /** Lane-0 branch (RailModel.viewBranch): its milestone dots select rows. */
  viewedBranch: string | null
  /** Drawn departures (RailModel.topChips): their lanes render as fork edges. */
  departureBranches: ReadonlySet<string>
  /** RailModel.viewedIsArchived — grey the whole cell. */
  dimmed: boolean
  /** Row-scoped by the caller: non-null only when it names one of THIS row's
   *  cells, so a selection click re-renders O(1) memoized cells. */
  selectedSha: string | null
  onSelectSha: (sha: string) => void
  onExpand: (sha: string) => void
  /** Departure-curve click peeks the departing branch (A-15). */
  onPeekBranch: (branch: string) => void
}

export const GraphRailCell = memo(function GraphRailCell({
  row,
  width,
  dotY,
  viewedBranch,
  departureBranches,
  dimmed,
  selectedSha,
  onSelectSha,
  onExpand,
  onPeekBranch,
}: GraphRailCellProps) {
  // A transition entering this row supplies the upper segment of its landing
  // lane, so the dot there must not draw its own (they would double-stroke).
  const transition = row?.cells.find((c) => c.kind === "transition")
  const transitionToLane = transition?.kind === "transition" ? transition.toLane : null

  const edgeKindOf = (branch: string): "spine" | "fork" =>
    departureBranches.has(branch) ? "fork" : "spine"

  // Edges first, nodes second, so dots paint over the lines meeting them.
  const edges = (cell: RailCell, key: number): ReactNode => {
    switch (cell.kind) {
      case "dot": {
        const x = laneX(cell.lane)
        return (
          <g key={key}>
            {transitionToLane !== cell.lane && (
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
            )}
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
      case "save-dot": {
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
          />
        )
      }
      case "pass": {
        const kind = edgeKindOf(cell.branch)
        const x = laneX(cell.lane)
        return (
          <line
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind={kind}
            data-branch={cell.branch}
            x1={x}
            y1={0}
            x2={x}
            y2="100%"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={STROKE}
            opacity={kind === "fork" ? DEPARTURE_OPACITY : undefined}
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
            d={curveD(cell.fromLane, cell.toLane, 0, dotY)}
            fill="none"
            stroke={laneColor(cell.fromColorIndex)}
            strokeWidth={STROKE}
          />
        )
      case "curve-out":
        return (
          <path
            key={key}
            data-testid="git-graph-edge"
            data-edge-kind="fork"
            data-branch={cell.branch}
            data-from-lane={cell.fromLane}
            data-to-lane={cell.toLane}
            d={curveD(cell.fromLane, cell.toLane, dotY, 0)}
            fill="none"
            stroke={laneColor(cell.colorIndex)}
            strokeWidth={STROKE}
            opacity={DEPARTURE_OPACITY}
            // Clicking the departure curve peeks its branch (A-15); the top
            // chip is the primary (and keyboard-reachable) affordance.
            className="cursor-pointer"
            onClick={(e) => {
              e.stopPropagation()
              onPeekBranch(cell.branch)
            }}
          />
        )
    }
  }

  const node = (cell: RailCell, key: number): ReactNode => {
    switch (cell.kind) {
      case "dot": {
        const x = laneX(cell.lane)
        const selected = selectedSha === cell.sha
        const selectable = cell.branch === viewedBranch
        return (
          <g key={key}>
            {selected && (
              <circle
                cx={x}
                cy={dotY}
                r={6}
                fill="none"
                stroke={laneColor(cell.colorIndex)}
                strokeOpacity={0.4}
                strokeWidth={STROKE}
              />
            )}
            <circle
              data-testid="git-graph-dot"
              data-sha={cell.sha}
              data-lane={cell.lane}
              data-branch={cell.branch}
              data-color-index={cell.colorIndex}
              data-kind="milestone"
              data-selected={selected || undefined}
              role={selectable ? "button" : undefined}
              tabIndex={selectable ? 0 : undefined}
              aria-label={selectable ? "Select this milestone" : undefined}
              onClick={
                selectable
                  ? (e) => {
                      e.stopPropagation()
                      onSelectSha(cell.sha)
                    }
                  : undefined
              }
              onKeyDown={
                selectable
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        e.stopPropagation()
                        onSelectSha(cell.sha)
                      }
                    }
                  : undefined
              }
              className={selectable ? "cursor-pointer" : undefined}
              cx={x}
              cy={dotY}
              r={3.5}
              fill={laneColor(cell.colorIndex)}
            />
          </g>
        )
      }
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
            data-selected={selectedSha === cell.sha || undefined}
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
            data-selected={selectedSha === cell.sha || undefined}
            cx={laneX(cell.lane)}
            cy={dotY}
            r={2.5}
            fill={laneColor(cell.colorIndex)}
          />
        )
      case "pass":
      case "transition":
      case "curve-out":
        return null
    }
  }

  return (
    <div
      data-testid="git-graph-rail"
      className="relative shrink-0 self-stretch"
      style={{ width, opacity: dimmed ? 0.55 : undefined }}
    >
      {row && (
        <svg className="absolute inset-0 w-full h-full overflow-visible">
          {row.cells.map(edges)}
          {row.cells.map(node)}
        </svg>
      )}
      {row?.magnifier && <Magnifier magnifier={row.magnifier} onExpand={onExpand} />}
    </div>
  )
})

// Expand affordance on a collapsed folded-save edge (A-4): sits at the bottom
// edge of the UPPER row's cell, on the edge's lane; clicking is the same
// toggleExpand the row click performs, so stopPropagation prevents the two
// from cancelling each other out.
function Magnifier({
  magnifier,
  onExpand,
}: {
  magnifier: RailMagnifier
  onExpand: (sha: string) => void
}) {
  const color = laneColor(magnifier.colorIndex)
  return (
    <span
      role="button"
      tabIndex={0}
      data-testid="git-graph-magnifier"
      data-expands={magnifier.expandsSha}
      title="Show the saves folded into this milestone"
      onClick={(e) => {
        e.stopPropagation()
        onExpand(magnifier.expandsSha)
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          e.stopPropagation()
          onExpand(magnifier.expandsSha)
        }
      }}
      className="absolute z-10 inline-flex items-center justify-center rounded-full cursor-pointer"
      style={{
        left: laneX(magnifier.lane) - 8,
        bottom: -8,
        width: 16,
        height: 16,
        background: "var(--bg-elevated)",
        border: `1px solid ${color}`,
        color,
      }}
    >
      <ZoomIn size={10} />
    </span>
  )
}

/** Slim strip above the history list: one peekable chip per departing branch
 *  (click = peek, mirroring ForkLinks) plus the "+N elsewhere" overflow. */
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
          title={`View ${c.branch}`}
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
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
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
