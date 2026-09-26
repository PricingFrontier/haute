import { describe, expect, it } from "vitest"
import { constraintAttainment, constraintAttainmentRows } from "../constraintAttainment"

describe("constraintAttainment", () => {
  it.each([
    { kind: "min", achieved: 1_000_000, slack: 0, status: "met" },
    { kind: "min", achieved: 1_012_400, slack: 12_400, status: "met" },
    { kind: "min", achieved: 987_600, slack: -12_400, status: "breached" },
    { kind: "max", achieved: 1_000_000, slack: 0, status: "met" },
    { kind: "max", achieved: 987_600, slack: 12_400, status: "met" },
    { kind: "max", achieved: 1_012_400, slack: -12_400, status: "breached" },
  ] as const)("judges a $kind bound with achieved $achieved as $status", ({ kind, achieved, slack, status }) => {
    const attainment = constraintAttainment({ kind, bound: 1_000_000, achieved })

    expect(attainment).toEqual({
      kind,
      bound: 1_000_000,
      achieved,
      slack,
      slackPct: (slack / 1_000_000) * 100,
      status,
    })
  })

  it("signs the slack percentage so a hair's breach reads as a breach", () => {
    const attainment = constraintAttainment({ kind: "max", bound: 100, achieved: 100.01 })

    expect(attainment.status).toBe("breached")
    expect(attainment.slack).toBeCloseTo(-0.01, 12)
    expect(attainment.slackPct).toBeCloseTo(-0.01, 12)
  })

  it("is strict: no tolerance turns the smallest breach into Met", () => {
    const bound = 0.1 + 0.2
    expect(constraintAttainment({ kind: "min", bound, achieved: 0.3 }).status).toBe("breached")
    expect(constraintAttainment({ kind: "max", bound: 0.3, achieved: bound }).status).toBe("breached")
  })

  it("scales the percentage by the bound's magnitude for a negative bound", () => {
    const attainment = constraintAttainment({ kind: "min", bound: -200, achieved: -100 })

    expect(attainment.slack).toBe(100)
    expect(attainment.slackPct).toBe(50)
    expect(attainment.status).toBe("met")
  })

  it("has no percentage against a zero bound rather than an infinite one", () => {
    const attainment = constraintAttainment({ kind: "max", bound: 0, achieved: -3 })

    expect(attainment.slack).toBe(3)
    expect(attainment.slackPct).toBeNull()
    expect(attainment.status).toBe("met")
  })

  it.each([
    { bound: Number.NaN, achieved: 1 },
    { bound: 1, achieved: Number.NaN },
    { bound: Number.POSITIVE_INFINITY, achieved: 1 },
    { bound: 1, achieved: Number.NEGATIVE_INFINITY },
  ])("throws on non-finite input (bound $bound, achieved $achieved)", ({ bound, achieved }) => {
    expect(() => constraintAttainment({ kind: "min", bound, achieved })).toThrow(/finite/)
  })

  it("throws on a kind the contract does not define", () => {
    expect(() => constraintAttainment({ kind: "min_pct" as "min", bound: 1, achieved: 1 })).toThrow(/kind/)
  })
})

describe("constraintAttainmentRows", () => {
  const bounds = {
    volume: { kind: "min" as const, bound: 5 },
    margin: { kind: "max" as const, bound: 400 },
  }

  it("builds one row per bound, in the backend's constraint order, with its λ", () => {
    const rows = constraintAttainmentRows({
      bounds,
      achieved: { margin: 389.7, volume: 5.2 },
      lambdas: { margin: 0, volume: 0.4 },
    })

    expect(rows.map((row) => row.name)).toEqual(["volume", "margin"])
    expect(rows[0]).toMatchObject({ name: "volume", kind: "min", bound: 5, achieved: 5.2, status: "met", lambda: 0.4 })
    expect(rows[1]).toMatchObject({ name: "margin", kind: "max", bound: 400, achieved: 389.7, status: "met", lambda: 0 })
  })

  it("throws when a constraint has no achieved total", () => {
    expect(() => constraintAttainmentRows({ bounds, achieved: { volume: 5.2 }, lambdas: { volume: 0.4, margin: 0 } }))
      .toThrow(/margin/)
  })

  it("throws when a constraint has no λ", () => {
    expect(() => constraintAttainmentRows({ bounds, achieved: { volume: 5.2, margin: 1 }, lambdas: { volume: 0.4 } }))
      .toThrow(/margin/)
  })

  it("throws when an achieved total has no bound", () => {
    expect(() => constraintAttainmentRows({
      bounds: { volume: bounds.volume },
      achieved: { volume: 5.2, margin: 1 },
      lambdas: { volume: 0.4, margin: 0 },
    })).toThrow(/margin/)
  })
})
