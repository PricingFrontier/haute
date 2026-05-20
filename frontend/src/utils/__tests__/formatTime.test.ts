import { describe, expect, it } from "vitest"
import { formatRelativeTime, formatTime } from "../formatTime"

describe("formatTime", () => {
  it("formats a unix-seconds timestamp as a short time string", () => {
    expect(formatTime(Date.UTC(2021, 0, 1, 13, 37) / 1000)).toMatch(/\d{1,2}:\d{2}/)
  })

  it("returns an empty string for NaN", () => {
    expect(formatTime(Number.NaN)).toBe("")
  })
})

describe("formatRelativeTime", () => {
  const now = new Date("2026-05-20T12:00:00Z")
  const secondsAgo = (seconds: number) => now.getTime() / 1000 - seconds

  it("renders 'just now' below the one-minute threshold", () => {
    expect(formatRelativeTime(secondsAgo(0), now)).toBe("just now")
    expect(formatRelativeTime(secondsAgo(59), now)).toBe("just now")
  })

  it("renders whole minutes from 60s up to (but not including) an hour", () => {
    expect(formatRelativeTime(secondsAgo(60), now)).toBe("1 min ago")
    expect(formatRelativeTime(secondsAgo(59 * 60), now)).toBe("59 min ago")
  })

  it("renders whole hours from 60min up to (but not including) a day", () => {
    expect(formatRelativeTime(secondsAgo(60 * 60), now)).toBe("1 h ago")
    expect(formatRelativeTime(secondsAgo(23 * 60 * 60), now)).toBe("23 h ago")
  })

  it("falls back to an absolute timestamp at or beyond 24 hours", () => {
    const ts = secondsAgo(24 * 60 * 60)
    expect(formatRelativeTime(ts, now)).toBe(new Date(ts * 1000).toLocaleString())
  })
})
