/**
 * The one reader of the optimiser preview's scenario cells. The curves and the
 * statistics both read the rows through it, so a cell means the same in both:
 * a null, NaN or infinite cell is a value the solve would refuse, left out and
 * counted, and a present value that is not a number is a `ScenarioDataError`.
 */

import type { PreviewData } from "../DataPreview"
import { isHauteNonFiniteFloat } from "../../utils/formatValue"

type PreviewRow = PreviewData["preview"][number]

/** One preview row placed on the scenario grid. */
export type ScenarioRow = {
  quoteId: string
  scenarioIndex: number
  scenarioValue: number
  /** The finite objective and constraint values; a series without one has no key. */
  values: Record<string, number>
}

/** The cells of one column the solve would refuse. */
export type ScenarioCellGaps = {
  column: string
  /** Rows without the column at all: the input does not have it. */
  absent: number
  nulls: number
  nans: number
  infinities: number
}

export type ScenarioRead = {
  rows: ScenarioRow[]
  /** The quote id column's null cells. */
  quoteIdNulls: number
  /** Per value column, in the solve's order, the columns with a gap only. */
  gaps: ScenarioCellGaps[]
}

export type ScenarioColumns = {
  quoteIdCol: string
  scenarioIndexCol: string
  scenarioValueCol: string
  /** Objective first, then each constraint. */
  series: string[]
}

/** A present scenario cell that cannot be read: shown in the pane, not thrown past it. */
export class ScenarioDataError extends Error {
  override name = "ScenarioDataError"
}

/** The value of *fn*, or the message of the `ScenarioDataError` it threw; any other error propagates. */
export function attemptScenarioRead<T>(fn: () => T): { value: T; error: null } | { value: null; error: string } {
  try {
    return { value: fn(), error: null }
  } catch (error) {
    if (error instanceof ScenarioDataError) return { value: null, error: error.message }
    throw error
  }
}

type Cell = { kind: "value"; value: number } | { kind: "absent" | "null" | "nan" | "infinite" }

function readCell(row: PreviewRow, column: string): Cell {
  if (!Object.hasOwn(row, column)) return { kind: "absent" }
  const raw = row[column]
  if (raw == null) return { kind: "null" }
  if (isHauteNonFiniteFloat(raw)) return { kind: raw.value === "nan" ? "nan" : "infinite" }
  const value = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() !== "" ? Number(raw) : NaN
  if (!Number.isFinite(value)) {
    throw new ScenarioDataError(`Expected a number in "${column}", received ${JSON.stringify(raw)}`)
  }
  return { kind: "value", value }
}

/**
 * The scenario rows of *rows* and the cells the solve would refuse. A row
 * without a quote id, or without a finite scenario index or value, is left
 * out; a series cell without a finite value is left out of that series.
 */
export function readScenarioRows(rows: Iterable<PreviewRow>, columns: ScenarioColumns): ScenarioRead {
  const { quoteIdCol, scenarioIndexCol, scenarioValueCol, series } = columns
  // The solve checks the objective, the scenario value, the scenario index, then each constraint.
  const [objective, ...constraints] = series
  const checked = [...new Set([objective, scenarioValueCol, scenarioIndexCol, ...constraints].filter(Boolean))]
  const tallies = new Map(checked.map((column) => [column, { column, absent: 0, nulls: 0, nans: 0, infinities: 0 }]))
  const tally = (column: string, cell: Cell): number | null => {
    if (cell.kind === "value") return cell.value
    const gaps = tallies.get(column)!
    if (cell.kind === "absent") gaps.absent += 1
    else if (cell.kind === "null") gaps.nulls += 1
    else if (cell.kind === "nan") gaps.nans += 1
    else gaps.infinities += 1
    return null
  }

  const read: ScenarioRow[] = []
  let quoteIdNulls = 0
  for (const row of rows) {
    const quoteId = row[quoteIdCol]
    // An absent quote id column is the no-data notice's to report, not a null count.
    if (quoteId == null && Object.hasOwn(row, quoteIdCol)) quoteIdNulls += 1
    const scenarioIndex = tally(scenarioIndexCol, readCell(row, scenarioIndexCol))
    if (scenarioIndex !== null && !Number.isInteger(scenarioIndex)) {
      throw new ScenarioDataError(`Expected an integer scenario index in "${scenarioIndexCol}", received ${scenarioIndex}`)
    }
    const scenarioValue = tally(scenarioValueCol, readCell(row, scenarioValueCol))
    const values: Record<string, number> = {}
    for (const column of series) {
      const value = tally(column, readCell(row, column))
      if (value !== null) values[column] = value
    }
    if (quoteId == null || scenarioIndex === null || scenarioValue === null) continue
    read.push({ quoteId: String(quoteId), scenarioIndex, scenarioValue, values })
  }
  return {
    rows: read,
    quoteIdNulls,
    gaps: [...tallies.values()].filter((gaps) => gaps.absent + gaps.nulls + gaps.nans + gaps.infinities > 0),
  }
}

const rowCount = (count: number, kind: string) => `${count} ${kind}row${count === 1 ? "" : "s"}`

/**
 * One line per kind of refused cell, in the words of the solve's own refusals
 * (`routes/_optimiser_input.py`), without their advice.
 */
export function scenarioGapNotices(read: Pick<ScenarioRead, "quoteIdNulls" | "gaps">): string[] {
  const notices: string[] = []
  const absent = read.gaps.filter((gaps) => gaps.absent > 0).map((gaps) => `'${gaps.column}'`)
  if (absent.length > 0) notices.push(`Missing columns in scored data: [${absent.join(", ")}].`)
  if (read.quoteIdNulls > 0) {
    notices.push(`Null quote_id values found in optimiser input (${read.quoteIdNulls} rows).`)
  }
  const nonFinite = read.gaps
    .filter((gaps) => gaps.nans + gaps.infinities > 0)
    .map((gaps) => {
      const kinds = [
        gaps.nans > 0 ? rowCount(gaps.nans, "NaN ") : null,
        gaps.infinities > 0 ? rowCount(gaps.infinities, "infinite ") : null,
      ].filter(Boolean)
      return `'${gaps.column}' (${kinds.join(", ")})`
    })
  if (nonFinite.length > 0) notices.push(`Non-finite values found in optimiser input: ${nonFinite.join(", ")}.`)
  const nulls = read.gaps
    .filter((gaps) => gaps.nulls > 0)
    .map((gaps) => `'${gaps.column}' (${rowCount(gaps.nulls, "null ")})`)
  if (nulls.length > 0) notices.push(`Null values found in optimiser input: ${nulls.join(", ")}.`)
  return notices
}
