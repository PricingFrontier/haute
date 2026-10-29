/**
 * The one relative-time rule (specs/frontend-shared): "just now", then whole minutes,
 * hours and days ago below a week, then the day and month, with the year only for another
 * year.
 */
import { describe, expect, it } from "vitest"
import { relativeTime } from "../relativeTime"

const now = new Date("2026-10-29T12:00:00Z")
const secondsAgo = (seconds: number) => new Date(now.getTime() - seconds * 1000)
const DAY = 86_400

describe("relativeTime", () => {
  it.each([
    [0, "just now"],
    [59, "just now"],
    [60, "1m ago"],
    [59 * 60 + 59, "59m ago"],
    [60 * 60, "1h ago"],
    [DAY - 1, "23h ago"],
    [DAY, "1d ago"],
    [7 * DAY - 1, "6d ago"],
  ])("says %ss ago as %s", (seconds, said) => {
    expect(relativeTime(secondsAgo(seconds), now)).toBe(said)
  })

  it("says just now for a time ahead of the clock", () => {
    expect(relativeTime(secondsAgo(-10 * 60), now)).toBe("just now")
  })

  it("says the day and month from a week on, without this year's year", () => {
    const weekAgo = secondsAgo(7 * DAY)
    const said = relativeTime(weekAgo, now)
    expect(said).toBe(weekAgo.toLocaleDateString(undefined, { day: "numeric", month: "short" }))
    expect(said).not.toMatch(/ago|2026/)
  })

  it("adds the year to a date from another year", () => {
    const lastYear = new Date("2025-12-30T12:00:00Z")
    const said = relativeTime(lastYear, now)
    expect(said).toBe(lastYear.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))
    expect(said).toMatch(/2025/)
  })

  it("says nothing for an invalid date", () => {
    expect(relativeTime(new Date("not a date"), now)).toBe("")
  })

  it("reads the clock when no now is given", () => {
    expect(relativeTime(new Date(Date.now() - 3 * 3600 * 1000))).toBe("3h ago")
  })
})
