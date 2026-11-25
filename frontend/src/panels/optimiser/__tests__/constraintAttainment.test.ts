import { describe, expect, it } from "vitest"
import { breachReport, constraintAttainment, constraintAttainmentRows } from "../constraintAttainment"

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

describe("breachReport", () => {
  const rows = (bounds: Parameters<typeof constraintAttainmentRows>[0]["bounds"], achieved: Record<string, number>) =>
    constraintAttainmentRows({
      bounds,
      achieved,
      lambdas: Object.fromEntries(Object.keys(achieved).map((name) => [name, 1])),
    })

  it("is null for a result that meets every bound", () => {
    expect(breachReport(rows({ volume: { kind: "min", bound: 100 } }, { volume: 100 }))).toBeNull()
    expect(breachReport([])).toBeNull()
  })

  it("words a minimum the result ends short of", () => {
    expect(breachReport(rows({ volume: { kind: "min", bound: 1868.27 } }, { volume: 1835.1 }))).toEqual({
      status: "1 bound breached",
      sentences: ["volume 1,835.1 is 1.78% under its minimum of 1,868.27."],
    })
  })

  it("words each breach, a maximum's too, and leaves out the bounds that are met", () => {
    const report = breachReport(rows(
      {
        volume: { kind: "min", bound: 100 },
        claims: { kind: "max", bound: 50 },
        income: { kind: "min", bound: 10 },
      },
      { volume: 90, claims: 55, income: 12 },
    ))

    expect(report).toEqual({
      status: "2 bounds breached",
      sentences: [
        "volume 90 is 10.00% under its minimum of 100.",
        "claims 55 is 10.00% over its maximum of 50.",
      ],
    })
  })

  it("gives no percentage against a zero bound", () => {
    expect(breachReport(rows({ cost: { kind: "max", bound: 0 } }, { cost: 3 }))?.sentences).toEqual([
      "cost 3 is over its maximum of 0.",
    ])
  })
})
