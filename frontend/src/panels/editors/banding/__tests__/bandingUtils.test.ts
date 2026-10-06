/**
 * Tests for banding utilities:
 * inferBandingType and friends,
 * detectDuplicateCategorical, categoricalRuleCounts, suggestOutputColumn, even breakpoints,
 * date boundaries, day numbers and calendar-step breakpoints
 */
import { describe, it, expect } from "vitest"
import {
  detectDuplicateCategorical,
  categoricalRuleCounts,
  generateEvenBreakpoints,
  generateSettingsFromBreakpoints,
  suggestOutputColumn,
  boundaryKind,
  breakpointKinds,
  boundaryDayNumber,
  boundarySplitDayNumber,
  previewValueDayNumber,
  dayNumberToDate,
  generateCalendarBreakpoints,
  calendarSettingsFromBreakpoints,
  type CalendarGenerateSettings,
} from "../bandingUtils"
import type { BreakpointRule, CategoricalRule } from "../../../../types/banding"

describe("detectDuplicateCategorical", () => {
  it("returns empty when no duplicates", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "Group1" },
      { value: "B", assignment: "Group2" },
    ]
    expect(detectDuplicateCategorical(rules)).toEqual([])
  })

  it("detects single duplicate", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "Group1" },
      { value: "B", assignment: "Group2" },
      { value: "A", assignment: "Group3" },
    ]
    const dupes = detectDuplicateCategorical(rules)
    expect(dupes).toHaveLength(1)
    expect(dupes[0].value).toBe("A")
    expect(dupes[0].indices).toEqual([0, 2])
  })

  it("detects multiple duplicates", () => {
    const rules: CategoricalRule[] = [
      { value: "A", assignment: "G1" },
      { value: "B", assignment: "G2" },
      { value: "A", assignment: "G3" },
      { value: "B", assignment: "G4" },
    ]
    const dupes = detectDuplicateCategorical(rules)
    expect(dupes).toHaveLength(2)
  })

  it("ignores empty values", () => {
    const rules: CategoricalRule[] = [
      { value: "", assignment: "G1" },
      { value: "", assignment: "G2" },
    ]
    expect(detectDuplicateCategorical(rules)).toEqual([])
  })

  it("returns empty for empty rules", () => {
    expect(detectDuplicateCategorical([])).toEqual([])
  })
})

// ─── suggestOutputColumn ─────────────────────────────────────────

describe("suggestOutputColumn", () => {
  it("appends _band to column name", () => {
    expect(suggestOutputColumn("age")).toBe("age_band")
  })

  it("handles empty string", () => {
    expect(suggestOutputColumn("")).toBe("_band")
  })

  it("returns unchanged if already ends with _band", () => {
    expect(suggestOutputColumn("age_band")).toBe("age_band")
  })

  it("handles column with spaces", () => {
    expect(suggestOutputColumn("age group")).toBe("age group_band")
  })

  it("handles column with underscores", () => {
    expect(suggestOutputColumn("my_column")).toBe("my_column_band")
  })
})

describe("categoricalRuleCounts", () => {
  const counts = new Map([
    ["north", 900],
    ["south", 100],
  ])

  it("gives a value named twice to the last rule, and nothing to a rule without an assignment", () => {
    const rules = [
      { value: "north", assignment: "N" },
      { value: "south", assignment: "" },
      { value: "north", assignment: "Top" },
    ]
    expect(categoricalRuleCounts(rules, counts, true)).toEqual([0, 0, 900])
  })

  it("counts a value the data never holds as 0 when every value is known", () => {
    expect(categoricalRuleCounts([{ value: "east", assignment: "E" }], counts, true)).toEqual([0])
  })

  it("leaves a value outside a truncated list unknown", () => {
    const rules = [
      { value: "north", assignment: "N" },
      { value: "east", assignment: "E" },
    ]
    expect(categoricalRuleCounts(rules, counts, false)).toEqual([900, null])
  })
})

describe("even breakpoints", () => {
  it.each([
    { start: 4000, end: 13600, step: 1200 },
    { start: 0, end: 100, step: 10 },
    { start: 0, end: 95, step: 10 }, // a shorter last band
    { start: -5, end: 5, step: 2.5 },
    { start: 0.1, end: 1.1, step: 0.2 },
  ])("infers back the settings that generated them: %o", (settings) => {
    const breakpoints = generateEvenBreakpoints(settings.start, settings.end, settings.step)
    expect(generateSettingsFromBreakpoints(breakpoints)).toEqual(settings)
  })

  it("labels each band by its range", () => {
    expect(generateEvenBreakpoints(0, 30, 10)).toEqual([
      { boundary: "10", label: "0–10" },
      { boundary: "20", label: "11–20" },
      { boundary: "30", label: "21–30" },
    ])
  })

  it("spreads uneven boundaries' lowest to highest over their bands", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    // Typed by hand: 4 bands whose "Up to" values run from 10 to 70, so the
    // step is (70 − 10) / 3 and the start one step below the lowest.
    expect(generateSettingsFromBreakpoints([bp("10"), bp("25"), bp("35"), bp("70")])).toEqual({
      start: -10,
      end: 70,
      step: 20,
    })
  })

  it("infers nothing from fewer than two boundaries", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    expect(generateSettingsFromBreakpoints([bp("10")])).toBeNull()
    expect(generateSettingsFromBreakpoints([bp(""), bp("10")])).toBeNull()
    expect(generateSettingsFromBreakpoints([])).toBeNull()
  })

  it("reads the settings whatever order the breakpoints are in, ignoring an open-ended one", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    expect(generateSettingsFromBreakpoints([bp("30"), bp(""), bp("10"), bp("20")])).toEqual({
      start: 0,
      end: 30,
      step: 10,
    })
  })
})

// ─── Date boundaries and day numbers ─────────────────────────────

// 2024-01-01 is day 19723 counted from 1970-01-01.
const JAN_1_2024 = 19723

describe("boundaryKind", () => {
  it.each([
    ["10", "number"],
    ["-2.5", "number"],
    ["1e3", "number"],
    [" 7 ", "number"],
    ["2024-02-29", "date"],
    ["2024-01-01 10:00", "datetime"],
    ["2024-01-01T10:00:30", "datetime"],
    ["2024-01-01 10:00:30.25", "datetime"],
  ])("reads %s as a %s", (boundary, kind) => {
    expect(boundaryKind(boundary)).toBe(kind)
  })

  it.each([
    "",
    "abc",
    "Infinity",
    "2023-02-29",
    "2024-13-01",
    "0000-01-01",
    "2024-1-1",
    "01/02/2024",
    "2024-01-01 10",
    "2024-01-01 24:00",
    "2024-01-01 10:60",
    "2024-01-01 10:00+01:00",
    "2024-01-01T10:00Z",
  ])("cannot read %j", (boundary) => {
    expect(boundaryKind(boundary)).toBeNull()
  })

  it("collects the kinds a factor's bounded breakpoints hold, leaving out blank and unreadable ones", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    expect(breakpointKinds([bp("2024-01-01"), bp(""), bp("2024-02-01 10:00"), bp("abc")])).toEqual(
      new Set(["date", "datetime"]),
    )
    expect(breakpointKinds([bp("10"), bp("20")])).toEqual(new Set(["number"]))
  })
})

describe("day numbers", () => {
  it("counts a date's days from 1970-01-01", () => {
    expect(boundaryDayNumber("1970-01-01")).toBe(0)
    expect(boundaryDayNumber("1969-12-31")).toBe(-1)
    expect(boundaryDayNumber("2024-01-01")).toBe(JAN_1_2024)
    expect(boundaryDayNumber("2024-02-29")).toBe(JAN_1_2024 + 59)
  })

  it("adds a date and time's wall-clock time as a fraction of its day", () => {
    expect(boundaryDayNumber("2024-01-01 06:00")).toBe(JAN_1_2024 + 0.25)
    expect(boundaryDayNumber("2024-01-01T18:00:00")).toBe(JAN_1_2024 + 0.75)
    expect(boundaryDayNumber("2024-01-01 12:00:00.5")).toBeCloseTo(JAN_1_2024 + 43200.5 / 86400, 9)
  })

  it("gives no day number for a number or an unreadable boundary", () => {
    expect(boundaryDayNumber("19723")).toBeNull()
    expect(boundaryDayNumber("")).toBeNull()
    expect(boundaryDayNumber("2023-02-29")).toBeNull()
  })

  it("reads a preview value's wall clock, before any time zone offset", () => {
    expect(previewValueDayNumber("2024-01-01")).toBe(JAN_1_2024)
    expect(previewValueDayNumber("2024-01-01T10:00:00")).toBe(boundaryDayNumber("2024-01-01 10:00"))
    // 10:00 in the column's own zone, not the 09:00 UTC it is.
    expect(previewValueDayNumber("2024-01-01T10:00:00+01:00")).toBe(boundaryDayNumber("2024-01-01 10:00"))
    // Late on New Year's Day in New York is still that day, though UTC has moved on.
    expect(Math.floor(previewValueDayNumber("2024-01-01T23:30:00-05:00")!)).toBe(JAN_1_2024)
    expect(previewValueDayNumber("2024-01-01T10:00:00.123456")).toBeCloseTo(
      JAN_1_2024 + (36000 + 0.123456) / 86400,
      9,
    )
  })

  it("gives no day number for a preview value that is not a date", () => {
    expect(previewValueDayNumber(JAN_1_2024)).toBeNull()
    expect(previewValueDayNumber(null)).toBeNull()
    expect(previewValueDayNumber(undefined)).toBeNull()
    expect(previewValueDayNumber("not a date")).toBeNull()
    expect(previewValueDayNumber("2024-02-30")).toBeNull()
  })

  it("shows a day number as the calendar date it falls on", () => {
    expect(dayNumberToDate(0)).toBe("1970-01-01")
    expect(dayNumberToDate(-0.5)).toBe("1969-12-31")
    expect(dayNumberToDate(JAN_1_2024)).toBe("2024-01-01")
    expect(dayNumberToDate(JAN_1_2024 + 0.99)).toBe("2024-01-01")
  })

  it("divides the axis after a date's whole day when right-closed, and at a time itself", () => {
    const feb29 = JAN_1_2024 + 59
    expect(boundarySplitDayNumber("2024-02-29", true)).toBe(feb29 + 1)
    expect(boundarySplitDayNumber("2024-02-29", false)).toBe(feb29)
    expect(boundarySplitDayNumber("2024-01-01 06:00", true)).toBe(JAN_1_2024 + 0.25)
    expect(boundarySplitDayNumber("10", true)).toBeNull()
  })
})

describe("calendar breakpoints", () => {
  it("ends each band the day before the next starts, capping the last at End", () => {
    expect(generateCalendarBreakpoints({ start: "2024-01-01", end: "2024-03-15", step: 1, unit: "months" }, true)).toEqual([
      { boundary: "2024-01-31", label: "2024-01-01–2024-01-31" },
      { boundary: "2024-02-29", label: "2024-02-01–2024-02-29" },
      { boundary: "2024-03-15", label: "2024-03-01–2024-03-15" },
    ])
  })

  it("steps whole months from Start, clamping a band that starts past a shorter month's end", () => {
    // Jan 31 + 1 month is Feb 29; + 2 months is Mar 31 again, not Mar 29.
    expect(generateCalendarBreakpoints({ start: "2024-01-31", end: "2024-04-29", step: 1, unit: "months" }, true)).toEqual([
      { boundary: "2024-02-28", label: "2024-01-31–2024-02-28" },
      { boundary: "2024-03-30", label: "2024-02-29–2024-03-30" },
      { boundary: "2024-04-29", label: "2024-03-31–2024-04-29" },
    ])
  })

  it.each<CalendarGenerateSettings>([
    { start: "2024-01-01", end: "2024-01-31", step: 10, unit: "days" }, // a shorter last band
    { start: "2024-01-01", end: "2024-03-24", step: 2, unit: "weeks" },
    { start: "2024-01-01", end: "2024-12-31", step: 3, unit: "months" },
    { start: "2024-01-01", end: "2024-12-15", step: 1, unit: "months" }, // a shorter last band
    { start: "2024-01-01", end: "2024-02-29", step: 1, unit: "months" }, // two bands
    { start: "2024-01-31", end: "2024-04-29", step: 1, unit: "months" }, // month-end clamping
    { start: "2020-01-01", end: "2024-12-31", step: 1, unit: "years" },
  ])("reads back the settings that generated them, either closure: %o", (settings) => {
    for (const rightClosed of [true, false]) {
      expect(calendarSettingsFromBreakpoints(generateCalendarBreakpoints(settings, rightClosed), rightClosed)).toEqual(
        settings,
      )
    }
  })

  it("puts each band's Up to on the day after its last when bands stop before their Up to", () => {
    expect(generateCalendarBreakpoints({ start: "2024-01-01", end: "2024-01-02", step: 1, unit: "days" }, false)).toEqual([
      { boundary: "2024-01-02", label: "2024-01-01–2024-01-01" },
      { boundary: "2024-01-03", label: "2024-01-02–2024-01-02" },
    ])
  })

  // The engine's rule for a date breakpoint on a Date column (pinned by
  // tests/test_banding.py TestDateBreakpoints): a day takes the first band
  // whose Up to it is on or before (right-closed), or before (left-closed).
  const bandOf = (day: number, rules: BreakpointRule[], rightClosed: boolean) =>
    rules.find((rule) => {
      const upTo = boundaryDayNumber(rule.boundary)!
      return rightClosed ? day <= upTo : day < upTo
    })?.label

  it.each<[CalendarGenerateSettings, boolean]>([
    [{ start: "2024-01-01", end: "2024-01-02", step: 1, unit: "days" }, false],
    [{ start: "2024-01-01", end: "2024-01-02", step: 1, unit: "days" }, true],
    [{ start: "2024-01-31", end: "2024-04-29", step: 1, unit: "months" }, false],
    [{ start: "2024-01-31", end: "2024-04-29", step: 1, unit: "months" }, true],
    [{ start: "2024-01-01", end: "2024-03-24", step: 2, unit: "weeks" }, false],
  ])("bands every day from Start to End under the label naming it: %o, right-closed %s", (settings, rightClosed) => {
    const rules = generateCalendarBreakpoints(settings, rightClosed)
    const start = boundaryDayNumber(settings.start)!
    const end = boundaryDayNumber(settings.end)!
    for (let day = start; day <= end; day += 1) {
      const date = dayNumberToDate(day)
      const label = bandOf(day, rules, rightClosed)
      expect(label, date).toBeDefined()
      const [first, last] = label!.split("–")
      expect(first <= date && date <= last, `${date} in ${label}`).toBe(true)
    }
  })

  it("reads a whole number of years before months, and weeks before days", () => {
    const twelveMonths = generateCalendarBreakpoints({ start: "2022-01-01", end: "2024-12-31", step: 12, unit: "months" }, true)
    expect(calendarSettingsFromBreakpoints(twelveMonths, true)).toEqual({
      start: "2022-01-01",
      end: "2024-12-31",
      step: 1,
      unit: "years",
    })
    const fourteenDays = generateCalendarBreakpoints({ start: "2024-01-01", end: "2024-03-24", step: 14, unit: "days" }, true)
    expect(calendarSettingsFromBreakpoints(fourteenDays, true)).toMatchObject({ step: 2, unit: "weeks" })
  })

  it("starts uneven date breakpoints from their lowest and highest dates and their band count in days", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    // 52 days from Jan 10 to Mar 1 inclusive, over 3 bands.
    expect(calendarSettingsFromBreakpoints([bp("2024-03-01"), bp(""), bp("2024-01-10"), bp("2024-01-15")], true)).toEqual({
      start: "2024-01-10",
      end: "2024-03-01",
      step: 17,
      unit: "days",
    })
  })

  it("reads date-and-time breakpoints by the last day their bands reach", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    const monthly = { start: "2024-01-01", end: "2024-02-29", step: 1, unit: "months" }
    expect(calendarSettingsFromBreakpoints([bp("2024-01-31 23:00"), bp("2024-02-29 12:00")], true)).toEqual(monthly)
    // A band up to midnight reaches no further into that day.
    expect(calendarSettingsFromBreakpoints([bp("2024-02-01 00:00"), bp("2024-03-01 00:00")], true)).toEqual(monthly)
    expect(calendarSettingsFromBreakpoints([bp("2024-02-01 00:00"), bp("2024-03-01 00:00")], false)).toEqual(monthly)
  })

  it("reads a left-closed date as its band ending the day before", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    expect(calendarSettingsFromBreakpoints([bp("2024-02-01"), bp("2024-03-01")], false)).toEqual({
      start: "2024-01-01",
      end: "2024-02-29",
      step: 1,
      unit: "months",
    })
  })

  it("reads nothing from fewer than two date breakpoints", () => {
    const bp = (boundary: string) => ({ boundary, label: boundary })
    expect(calendarSettingsFromBreakpoints([bp("2024-01-31"), bp("")], true)).toBeNull()
    expect(calendarSettingsFromBreakpoints([bp("10"), bp("20")], true)).toBeNull()
    expect(calendarSettingsFromBreakpoints([], true)).toBeNull()
  })

  it.each([
    [{ start: "2024-01-01", end: "2024-12-31", step: 0, unit: "months" }, "Step must be a whole number of at least 1"],
    [{ start: "2024-01-01", end: "2024-12-31", step: 1.5, unit: "months" }, "Step must be a whole number of at least 1"],
    [{ start: "2024-01-01", end: "2023-12-31", step: 1, unit: "months" }, "End must not be before Start"],
    [{ start: "", end: "2024-12-31", step: 1, unit: "months" }, "Start and End must be dates"],
    [{ start: "2000-01-01", end: "2040-01-01", step: 1, unit: "days" }, "more than 10000 bands"],
  ] as [CalendarGenerateSettings, string][])("refuses %o", (settings, message) => {
    expect(() => generateCalendarBreakpoints(settings, true)).toThrow(message)
  })
})
