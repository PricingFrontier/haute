/**
 * Where the SHAP beeswarm puts its rows and dots at a given width, and which
 * dot lies nearest a pointer. The component paints these positions on a canvas.
 */
import type { TrainShapBeeswarmFeature } from "../../api/types"
import { chartDomain, chartTicks, chartTickSpan } from "../../utils/chartHelpers"
import { beeswarmOffsets } from "./beeswarm"

/** The chart's fixed geometry, in pixels. */
export const SHAP_BEESWARM_GEOMETRY = {
  marginTop: 16,
  marginBottom: 46,
  rowGap: 34,
  /** How far a dot may sit above or below its row line. */
  rowHalfHeight: 14,
  labelGap: 12,
  /** The feature labels take this share of the width, within these bounds. */
  labelAreaShare: 0.22,
  minLabelArea: 88,
  maxLabelArea: 160,
  marginRight: 64,
  colorBarInset: 52,
  dotRadius: 2,
} as const

/** How far from a dot's centre the pointer still picks it. */
const PICK_RADIUS = 6
/** The pixel grid the pick index buckets dots into. */
const PICK_CELL = 8

export type ShapBeeswarmLayout = {
  width: number
  height: number
  marginLeft: number
  plotRight: number
  ticks: number[]
  xScale: (value: number) => number
  /** Dot `i` is row `row[i]` of feature `feature[i]`, drawn at (`x[i]`, `y[i]`). */
  feature: Uint16Array
  row: Uint32Array
  x: Float32Array
  y: Float32Array
  /** Dot indices by pick cell. */
  cells: Map<number, number[]>
}

/** The y of feature row `index`'s line. */
export function shapBeeswarmRowY(index: number): number {
  const { marginTop, rowGap } = SHAP_BEESWARM_GEOMETRY
  return marginTop + rowGap / 2 + index * rowGap
}

function cellKey(column: number, row: number): number {
  return column * 65_536 + row
}

/** Every dot's position at `width`, stacked within its feature's row. */
export function layoutShapBeeswarm(
  features: readonly TrainShapBeeswarmFeature[],
  width: number,
): ShapBeeswarmLayout {
  const g = SHAP_BEESWARM_GEOMETRY
  const marginLeft = Math.min(g.maxLabelArea, Math.max(g.minLabelArea, width * g.labelAreaShare))
  const plotRight = Math.max(marginLeft + 1, width - g.marginRight)
  const height = g.marginTop + features.length * g.rowGap + g.marginBottom
  const allShap = features.flatMap((feature) => feature.shap_values)
  const [low, high] = chartDomain(allShap, true)
  const xScale = (value: number) => marginLeft + ((value - low) / (high - low)) * (plotRight - marginLeft)
  const [tickLow, tickHigh] = chartTickSpan([...allShap, 0])

  const count = allShap.length
  const feature = new Uint16Array(count)
  const row = new Uint32Array(count)
  const x = new Float32Array(count)
  const y = new Float32Array(count)
  const cells = new Map<number, number[]>()
  const stacking = { bucketWidth: g.dotRadius * 2, laneStep: g.dotRadius * 1.5, halfHeight: g.rowHalfHeight }
  let index = 0
  features.forEach((entry, featureIndex) => {
    const xs = entry.shap_values.map(xScale)
    const offsets = beeswarmOffsets(xs, stacking)
    const lineY = shapBeeswarmRowY(featureIndex)
    xs.forEach((dotX, rowIndex) => {
      feature[index] = featureIndex
      row[index] = rowIndex
      x[index] = dotX
      y[index] = lineY + offsets[rowIndex]
      const key = cellKey(Math.floor(dotX / PICK_CELL), Math.floor(y[index] / PICK_CELL))
      const cell = cells.get(key)
      if (cell) cell.push(index)
      else cells.set(key, [index])
      index += 1
    })
  })
  return {
    width,
    height,
    marginLeft,
    plotRight,
    ticks: chartTicks(tickLow, tickHigh, 5),
    xScale,
    feature,
    row,
    x,
    y,
    cells,
  }
}

/** The dot nearest (`px`, `py`) within the pick radius, or null when none is that close. */
export function nearestDot(layout: ShapBeeswarmLayout, px: number, py: number): number | null {
  const column = Math.floor(px / PICK_CELL)
  const rowCell = Math.floor(py / PICK_CELL)
  let best: number | null = null
  let bestDistance = PICK_RADIUS * PICK_RADIUS
  for (let dc = -1; dc <= 1; dc += 1) {
    for (let dr = -1; dr <= 1; dr += 1) {
      for (const index of layout.cells.get(cellKey(column + dc, rowCell + dr)) ?? []) {
        const distance = (layout.x[index] - px) ** 2 + (layout.y[index] - py) ** 2
        if (distance <= bestDistance) {
          best = index
          bestDistance = distance
        }
      }
    }
  }
  return best
}
