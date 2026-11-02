import { describe, expect, it } from "vitest"

import { isDateTime, isDay, isTime } from "../isoDate"

describe("isoDate", () => {
  it("accepts a real calendar day from year 1, years before 100 included", () => {
    expect(isDay("2024-02-29")).toBe(true)
    expect(isDay("0099-03-01")).toBe(true)
    expect(isDay("0001-01-01")).toBe(true)
  })

  it.each(["2023-02-29", "1900-02-29", "0000-01-01", "2024-13-01", "2024-04-31", "24-01-01", "2024-1-01", " 2024-01-01"])(
    "refuses %s",
    (text) => expect(isDay(text)).toBe(false),
  )

  it("checks a time of day and a day joined to one", () => {
    expect(isTime("23:59:59.123456+05:30")).toBe(true)
    expect(isTime("24:00:00")).toBe(false)
    expect(isTime("12:00:00+24:00")).toBe(false)
    expect(isDateTime("0050-06-15T08:30:00Z")).toBe(true)
    expect(isDateTime("2023-02-29T08:30:00")).toBe(false)
    expect(isDateTime("T08:30:00")).toBe(false)
  })
})
