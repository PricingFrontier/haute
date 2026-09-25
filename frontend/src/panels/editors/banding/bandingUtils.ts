import type { BandingFactor, BandingMode, CategoricalRule, BreakpointRule } from "../../../types/banding"
import { isNumericDtype, isTemporalDtype } from "../../../utils/polarsDtypes"

export { isNumericDtype }

/** The node's factors, or one empty Numeric factor for a new node. */
export function normaliseBandingFactors(config: Record<string, unknown>): BandingFactor[] {
  const raw = config.factors as BandingFactor[] | undefined
  if (Array.isArray(raw) && raw.length > 0) return raw
  return [{ banding: "breakpoints", column: "", outputColumn: "", rules: [], default: null }]
}

/** Numeric for a number, Date or Datetime column; Categorical for any other. */
export function inferBandingType(colName: string, colMap: Record<string, string>): BandingMode | null {
  const dtype = colMap[colName]
  if (!dtype) return null
  return isNumericDtype(dtype) || isTemporalDtype(dtype) ? "breakpoints" : "categorical"
}

// ---------------------------------------------------------------------------
// Categorical duplicate detection
// ---------------------------------------------------------------------------

export function detectDuplicateCategorical(
  rules: CategoricalRule[],
): { indices: number[]; value: string }[] {
  const byValue = new Map<string, number[]>()
  rules.forEach((r, i) => {
    const v = r.value ?? ""
    if (!v) return
    const arr = byValue.get(v)
    if (arr) arr.push(i)
    else byValue.set(v, [i])
  })
  const result: { indices: number[]; value: string }[] = []
  for (const [value, indices] of byValue) {
    if (indices.length > 1) result.push({ indices, value })
  }
  return result
}

/**
 * Each categorical rule's row count, from how many rows hold each value.
 *
 * Execution builds one remap in rule order, so a value named by several rules
 * is claimed by the *last* of them, and a rule missing its value or its
 * assignment claims nothing. A claimed value absent from `valueCounts` counts
 * 0 when those counts cover every value, and is unknown (null) otherwise.
 */
export function categoricalRuleCounts(
  rules: CategoricalRule[],
  valueCounts: ReadonlyMap<string, number>,
  complete: boolean,
): (number | null)[] {
  const claimant = new Map<string, number>()
  rules.forEach(({ value, assignment }, index) => {
    if (value && assignment) claimant.set(value, index)
  })
  return rules.map(({ value }, index) => {
    if (!value || claimant.get(value) !== index) return 0
    return valueCounts.get(value) ?? (complete ? 0 : null)
  })
}

// ---------------------------------------------------------------------------
// Output column suggestion
// ---------------------------------------------------------------------------

export function suggestOutputColumn(inputColumn: string): string {
  if (!inputColumn) return "_band"
  if (inputColumn.endsWith("_band")) return inputColumn
  return `${inputColumn}_band`
}

// ---------------------------------------------------------------------------
// Even breakpoints (Generate)
// ---------------------------------------------------------------------------

/** What Generate is asked for. */
export interface GenerateSettings {
  start: number
  end: number
  step: number
}

/** Drop binary noise such as 0.30000000000000004, as the generator does. */
const tidy = (value: number) => +value.toFixed(10)

/**
 * Upper-bound boundaries from `start + step` up to `end`, always ending at
 * `end`, so the last band may be shorter. Each band is labelled by its range:
 * integer bands as "lo–hi" (right-closed, so a band starts one above the
 * boundary before it), others as ">prev–hi".
 */
export function generateEvenBreakpoints(
  start: number,
  end: number,
  step: number,
): BreakpointRule[] {
  const boundaries: number[] = []
  for (let v = tidy(start + step); v <= end; v = tidy(v + step)) {
    boundaries.push(v)
    if (boundaries.length > 10000) break
  }
  if (boundaries.length === 0 || boundaries[boundaries.length - 1] < end) {
    boundaries.push(end)
  }
  const allInteger = Number.isInteger(start) && Number.isInteger(step) && boundaries.every(Number.isInteger)
  return boundaries.map((high, i) => {
    if (i === 0) return { boundary: String(high), label: `${start}–${high}` }
    const prev = boundaries[i - 1]
    return { boundary: String(high), label: allInteger ? `${prev + 1}–${high}` : `>${prev}–${high}` }
  })
}

/**
 * The Generate settings to start from for `breakpoints`, read from their "Up
 * to" boundaries: End is the highest, Step spreads the lowest to the highest
 * over the bands, and Start is one step below the lowest, which gives back what
 * Generate was given for bands it made. When every gap but a shorter last one
 * is equal (Generate's output for a range that is not a whole number of
 * steps), that gap is the step. Null with fewer than two boundaries; an
 * open-ended breakpoint is ignored.
 */
export function generateSettingsFromBreakpoints(
  breakpoints: BreakpointRule[],
): GenerateSettings | null {
  const boundaries = breakpoints
    .map((bp) => (bp.boundary ?? "").trim())
    .filter((boundary) => boundary !== "")
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b)
  if (boundaries.length < 2) return null
  const lowest = boundaries[0]
  const end = boundaries[boundaries.length - 1]
  const gaps = boundaries.slice(1).map((boundary, i) => tidy(boundary - boundaries[i]))
  const firstGap = gaps[0]
  const lastGap = gaps[gaps.length - 1]
  const tolerance = 1e-9 * Math.max(1, Math.abs(firstGap))
  const evenButShorterLast =
    gaps.length > 1 &&
    gaps.slice(0, -1).every((gap) => Math.abs(gap - firstGap) <= tolerance) &&
    lastGap > 0 &&
    lastGap < firstGap - tolerance
  const step = evenButShorterLast ? firstGap : tidy((end - lowest) / (boundaries.length - 1))
  if (step <= 0) return null
  return { start: tidy(lowest - step), end, step }
}

// ---------------------------------------------------------------------------
// Date boundaries and day numbers
// ---------------------------------------------------------------------------

/**
 * What a breakpoint's "Up to" holds, read as the backend's
 * `parse_breakpoint_boundary` reads it: a finite number, a date
 * (`YYYY-MM-DD`), or a date and time (`YYYY-MM-DD HH:MM`, optionally `:SS` and
 * a fraction, `T` or a space between, and no UTC offset).
 */
export type BoundaryKind = "number" | "date" | "datetime"

const MS_PER_DAY = 86_400_000
const SECONDS_PER_DAY = 86_400
const NUMBER_PATTERN = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const DATETIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/
/**
 * A preview value: Python's `isoformat` of a date or a datetime, the latter
 * ending in its UTC offset when its column has a time zone. The groups are the
 * wall-clock value before that offset.
 */
const PREVIEW_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(?:Z|[+-]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?)?$/

type CalendarDate = { year: number; month: number; day: number }

/** Days since 1970-01-01 of a calendar date, counted in UTC so no browser zone shifts it. */
function utcDayNumber({ year, month, day }: CalendarDate): number {
  const date = new Date(0)
  // Unlike Date.UTC, setUTCFullYear does not read years 0–99 as 1900–1999.
  date.setUTCFullYear(year, month - 1, day)
  return date.getTime() / MS_PER_DAY
}

function calendarDate(dayNumber: number): CalendarDate {
  const date = new Date(Math.floor(dayNumber) * MS_PER_DAY)
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate() }
}

function daysInMonth(year: number, month: number): number {
  return calendarDate(utcDayNumber({ year, month: month + 1, day: 0 })).day
}

/**
 * The wall-clock day number a date pattern's match spells, fractional for a
 * time, or null when it is not a real date and time (2023-02-29, 24:00).
 */
function matchedDayNumber(match: RegExpMatchArray | null): number | null {
  if (!match) return null
  const [, year, month, day, hour, minute, second, fraction] = match
  const date = { year: Number(year), month: Number(month), day: Number(day) }
  if (date.year < 1 || date.month < 1 || date.month > 12 || date.day < 1) return null
  if (date.day > daysInMonth(date.year, date.month)) return null
  const dayNumber = utcDayNumber(date)
  if (hour === undefined) return dayNumber
  const seconds = second === undefined ? 0 : Number(second)
  if (Number(hour) > 23 || Number(minute) > 59 || seconds > 59) return null
  const time = Number(hour) * 3600 + Number(minute) * 60 + seconds + (fraction ? Number(`0.${fraction}`) : 0)
  return dayNumber + time / SECONDS_PER_DAY
}

/** A breakpoint boundary's kind; null when it is blank or unreadable. */
export function boundaryKind(boundary: string): BoundaryKind | null {
  const text = boundary.trim()
  if (NUMBER_PATTERN.test(text)) return Number.isFinite(Number(text)) ? "number" : null
  if (matchedDayNumber(text.match(DATE_PATTERN)) !== null) return "date"
  if (matchedDayNumber(text.match(DATETIME_PATTERN)) !== null) return "datetime"
  return null
}

/** The kinds a factor's bounded breakpoints hold, leaving out blank and unreadable ones. */
export function breakpointKinds(breakpoints: BreakpointRule[]): Set<BoundaryKind> {
  const kinds = new Set<BoundaryKind>()
  for (const bp of breakpoints) {
    const kind = boundaryKind(bp.boundary ?? "")
    if (kind) kinds.add(kind)
  }
  return kinds
}

/**
 * A date or date-and-time boundary as its wall-clock day number: days since
 * 1970-01-01, fractional for a time — the unit of the banding statistics. Null
 * for a number, a blank or an unreadable boundary.
 */
export function boundaryDayNumber(boundary: string): number | null {
  const text = boundary.trim()
  return matchedDayNumber(text.match(DATE_PATTERN)) ?? matchedDayNumber(text.match(DATETIME_PATTERN))
}

/**
 * A preview row's date or datetime as its wall-clock day number, fractional
 * for a time. A time-zoned column's value ends in its UTC offset; the wall
 * clock before it is the column's own, which is what banding compares. Null
 * for anything that is not such a string.
 */
export function previewValueDayNumber(value: unknown): number | null {
  return typeof value === "string" ? matchedDayNumber(value.match(PREVIEW_PATTERN)) : null
}

/** The calendar date (`YYYY-MM-DD`) a day number falls on. */
export function dayNumberToDate(dayNumber: number): string {
  return new Date(Math.floor(dayNumber) * MS_PER_DAY).toISOString().slice(0, 10)
}

/**
 * Where a date or date-and-time boundary divides the day-number axis. A date's
 * band holds the whole of its day, so right-closed it ends at the next
 * midnight; a time divides at itself. Null for any other boundary.
 */
export function boundarySplitDayNumber(boundary: string, rightClosed: boolean): number | null {
  const dayNumber = boundaryDayNumber(boundary)
  if (dayNumber === null) return null
  return rightClosed && boundaryKind(boundary) === "date" ? dayNumber + 1 : dayNumber
}

// ---------------------------------------------------------------------------
// Calendar-step breakpoints (Generate on a date column)
// ---------------------------------------------------------------------------

export type CalendarUnit = "days" | "weeks" | "months" | "years"

/** The units Generate steps a date column by, in the order its unit select lists them. */
export const CALENDAR_UNITS: readonly CalendarUnit[] = ["days", "weeks", "months", "years"]

/** What Generate is asked for on a date column. */
export interface CalendarGenerateSettings {
  /** The first band's first day, `YYYY-MM-DD`. */
  start: string
  /** The last band's last day, `YYYY-MM-DD`. */
  end: string
  /** How many `unit`s each band spans: a whole number. */
  step: number
  unit: CalendarUnit
}

/** The most bands Generate makes on a date column. */
export const MAX_CALENDAR_BANDS = 10000

/**
 * The day `count` units after `start`. Months and years count from `start`
 * itself rather than from the band before, so a start on the 31st returns to
 * the 31st after being clamped to a shorter month's end.
 */
function shiftDays(start: number, count: number, unit: CalendarUnit): number {
  if (unit === "days") return start + count
  if (unit === "weeks") return start + 7 * count
  const from = calendarDate(start)
  const months = from.year * 12 + from.month - 1 + count * (unit === "years" ? 12 : 1)
  const year = Math.floor(months / 12)
  const month = months - year * 12 + 1
  return utcDayNumber({ year, month, day: Math.min(from.day, daysInMonth(year, month)) })
}

/** Each band's first and last day number, stopping after `limit` bands. */
function calendarBandDays(start: number, end: number, step: number, unit: CalendarUnit, limit: number): [number, number][] {
  const bands: [number, number][] = []
  for (let band = 0; bands.length < limit; band += 1) {
    const first = shiftDays(start, band * step, unit)
    if (first > end) break
    bands.push([first, Math.min(shiftDays(start, (band + 1) * step, unit) - 1, end)])
  }
  return bands
}

/**
 * Breakpoints stepping `step` units from Start: band k starts at Start + k
 * steps, its "Up to" is the day before the next band starts (the last capped
 * at End, so it may be shorter), and it is labelled by its first and last day.
 * Throws a RangeError, whose message is for the user, for settings Generate
 * cannot use.
 */
export function generateCalendarBreakpoints(settings: CalendarGenerateSettings): BreakpointRule[] {
  const start = matchedDayNumber(settings.start.trim().match(DATE_PATTERN))
  const end = matchedDayNumber(settings.end.trim().match(DATE_PATTERN))
  if (start === null || end === null) throw new RangeError("Start and End must be dates")
  if (!Number.isInteger(settings.step) || settings.step < 1) {
    throw new RangeError("Step must be a whole number of at least 1")
  }
  if (end < start) throw new RangeError("End must not be before Start")
  const bands = calendarBandDays(start, end, settings.step, settings.unit, MAX_CALENDAR_BANDS + 1)
  if (bands.length > MAX_CALENDAR_BANDS) {
    throw new RangeError(`That makes more than ${MAX_CALENDAR_BANDS} bands; use a longer step`)
  }
  return bands.map(([first, last]) => ({
    boundary: dayNumberToDate(last),
    label: `${dayNumberToDate(first)}–${dayNumberToDate(last)}`,
  }))
}

/**
 * The Start and whole step in `unit` that band starts `anchors` imply, or null
 * when they are not a whole number of units apart. A month or year step clamps
 * a start past a shorter month's end, so Start's day of the month is the latest
 * any band start has.
 */
function wholeUnitStart(anchors: number[], unit: CalendarUnit): { start: number; step: number } | null {
  const [first, second] = anchors
  const from = calendarDate(first)
  const to = calendarDate(second)
  const step =
    unit === "years" ? to.year - from.year
      : unit === "months" ? (to.year - from.year) * 12 + to.month - from.month
        : unit === "weeks" ? (second - first) / 7
          : second - first
  if (!Number.isInteger(step) || step < 1) return null
  if (unit === "days" || unit === "weeks") return { start: shiftDays(first, -step, unit), step }
  const months = from.year * 12 + from.month - 1 - step * (unit === "years" ? 12 : 1)
  const year = Math.floor(months / 12)
  if (year < 1) return null
  const month = months - year * 12 + 1
  const dayOfMonth = Math.max(...anchors.map((anchor) => calendarDate(anchor).day))
  return { start: utcDayNumber({ year, month, day: Math.min(dayOfMonth, daysInMonth(year, month)) }), step }
}

/**
 * The Generate settings to start from for date `breakpoints`: the whole step
 * of years, months, weeks or days (checked in that order) that regenerates
 * exactly their "Up to" dates, the last band possibly shorter; failing that,
 * their lowest and highest dates with their band count spread over that range
 * in days. A date and time counts by its calendar date. Null with fewer than
 * two date boundaries; numbers and an open-ended breakpoint are ignored.
 */
export function calendarSettingsFromBreakpoints(breakpoints: BreakpointRule[]): CalendarGenerateSettings | null {
  const days = breakpoints
    .map((bp) => boundaryDayNumber(bp.boundary ?? ""))
    .filter((dayNumber): dayNumber is number => dayNumber !== null)
    .map(Math.floor)
    .sort((a, b) => a - b)
  if (days.length < 2) return null
  const lowest = days[0]
  const end = days[days.length - 1]
  // Every band after the first starts the day after the one before it ends.
  // With only two bands the day after the end stands in for a third start,
  // taking the last band as whole.
  const starts = days.slice(0, -1).map((dayNumber) => dayNumber + 1)
  const anchors = starts.length >= 2 ? starts : [...starts, end + 1]
  for (const unit of ["years", "months", "weeks", "days"] as const) {
    const whole = wholeUnitStart(anchors, unit)
    if (!whole) continue
    const bands = calendarBandDays(whole.start, end, whole.step, unit, days.length + 1)
    if (bands.length === days.length && bands.every(([, last], index) => last === days[index])) {
      return { start: dayNumberToDate(whole.start), end: dayNumberToDate(end), step: whole.step, unit }
    }
  }
  return {
    start: dayNumberToDate(lowest),
    end: dayNumberToDate(end),
    step: Math.max(1, Math.round((end - lowest + 1) / days.length)),
    unit: "days",
  }
}
