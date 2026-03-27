import { describe, it, expect } from "vitest"
import { formatTime } from "../../utils/formatTime"

describe("formatTime", () => {
  it("formats epoch timestamp (0) as a valid time", () => {
    // 0 is a valid Unix timestamp (Jan 1 1970 00:00 UTC)
    const result = formatTime(0)
    expect(result).toBeTruthy()
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it("returns empty string for NaN", () => {
    expect(formatTime(NaN)).toBe("")
  })

  it("formats a unix timestamp into HH:MM", () => {
    // 2024-01-15 12:30:00 UTC = 1705319400
    const result = formatTime(1705319400)
    // We can't assert exact locale output, but we can check it's non-empty
    // and matches a time-like pattern (e.g. "12:30 PM" or "12:30")
    expect(result).toBeTruthy()
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it("formats midnight correctly", () => {
    // 2024-01-15 00:00:00 UTC = 1705276800
    const result = formatTime(1705276800)
    expect(result).toBeTruthy()
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })

  it("handles very large timestamps", () => {
    // 2040-01-01 00:00:00 UTC = 2208988800
    const result = formatTime(2208988800)
    expect(result).toBeTruthy()
    expect(result).toMatch(/\d{1,2}:\d{2}/)
  })
})
