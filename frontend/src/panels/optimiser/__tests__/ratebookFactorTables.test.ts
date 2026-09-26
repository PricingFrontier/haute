import { describe, expect, it } from "vitest"
import {
  factorRateSpread,
  factorTablesCsv,
  formatVsNeutral,
  levelQuoteShares,
} from "../ratebookFactorTables"

describe("factorTablesCsv", () => {
  it("writes one row per level, prefixed by its factor, with its rate, quote count and the collar", () => {
    const csv = factorTablesCsv(
      {
        age_band: [
          { __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 },
          { __factor_group__: "26+", optimal_scenario_value: 0.95, quote_count: 0 },
        ],
        region: [{ __factor_group__: "North, East", optimal_scenario_value: 1, quote_count: 7 }],
      },
      { min: 0.9, max: 1.1 },
    )

    expect(csv.split("\n")).toEqual([
      "factor,__factor_group__,optimal_scenario_value,quote_count,combined_factor_min,combined_factor_max",
      "age_band,17-25,1.1,40,0.9,1.1",
      "age_band,26+,0.95,0,0.9,1.1",
      'region,"North, East",1,7,0.9,1.1',
    ])
  })

  it("writes the collar exactly as the solve recorded it, Float32 widening included", () => {
    const csv = factorTablesCsv(
      { region: [{ __factor_group__: "North", optimal_scenario_value: 1.2, quote_count: 5 }] },
      { min: 0.8999999761581421, max: 1.100000023841858 },
    )

    expect(csv.split("\n")[1]).toBe("region,North,1.2,5,0.8999999761581421,1.100000023841858")
  })

  it("keeps a single-value collar", () => {
    const csv = factorTablesCsv(
      { region: [{ __factor_group__: "North", optimal_scenario_value: 1, quote_count: 5 }] },
      { min: 1, max: 1 },
    )

    expect(csv.split("\n")[1]).toBe("region,North,1,5,1,1")
  })
})

describe("factorRateSpread", () => {
  it("is the quote-weighted mean absolute log rate", () => {
    const spread = factorRateSpread("age", [
      { __factor_group__: "a", optimal_scenario_value: 2, quote_count: 1 },
      { __factor_group__: "b", optimal_scenario_value: 0.5, quote_count: 3 },
    ])
    expect(spread).toBeCloseTo(Math.log(2), 12)
  })

  it("weights rare extreme levels below common moderate ones", () => {
    const sparseExtreme = factorRateSpread("sparse", [
      { __factor_group__: "Rare", optimal_scenario_value: 2.5, quote_count: 1 },
      { __factor_group__: "Common", optimal_scenario_value: 1, quote_count: 999 },
    ])
    const commonModerate = factorRateSpread("common", [
      { __factor_group__: "Low", optimal_scenario_value: 0.9, quote_count: 500 },
      { __factor_group__: "High", optimal_scenario_value: 1.1, quote_count: 500 },
    ])
    expect(commonModerate).toBeGreaterThan(sparseExtreme)
  })

  it("is zero for a factor left at the base price", () => {
    expect(factorRateSpread("flat", [
      { __factor_group__: "a", optimal_scenario_value: 1, quote_count: 5 },
    ])).toBe(0)
  })

  it("throws on a non-positive rate", () => {
    expect(() => factorRateSpread("age", [
      { __factor_group__: "17-24", optimal_scenario_value: 0, quote_count: 5 },
    ])).toThrow("Factor age level 17-24 has a non-positive rate 0")
  })

  it("throws when the factor has no quotes", () => {
    expect(() => factorRateSpread("age", [
      { __factor_group__: "17-24", optimal_scenario_value: 1.1, quote_count: 0 },
    ])).toThrow("Factor age has no quotes")
  })
})

describe("levelQuoteShares", () => {
  it("gives each level's share of the factor's quotes, in row order", () => {
    expect(levelQuoteShares("region", [
      { __factor_group__: "North", optimal_scenario_value: 1.1, quote_count: 30 },
      { __factor_group__: "South", optimal_scenario_value: 0.9, quote_count: 10 },
    ])).toEqual([75, 25])
  })

  it("rounds to one decimal by largest remainder so the shares sum to exactly 100", () => {
    const shares = levelQuoteShares("thirds", [
      { __factor_group__: "a", optimal_scenario_value: 1, quote_count: 1 },
      { __factor_group__: "b", optimal_scenario_value: 1, quote_count: 1 },
      { __factor_group__: "c", optimal_scenario_value: 1, quote_count: 1 },
    ])
    expect(shares).toEqual([33.4, 33.3, 33.3])
    expect(Math.round(shares.reduce((a, b) => a + b, 0) * 10)).toBe(1000)
  })

  it("sums to 100 for many uneven levels", () => {
    const rows = Array.from({ length: 37 }, (_, i) => ({
      __factor_group__: `L${i}`,
      optimal_scenario_value: 1,
      quote_count: (i * 7919) % 97 + 1,
    }))
    const shares = levelQuoteShares("many", rows)
    expect(Math.round(shares.reduce((a, b) => a + b, 0) * 10)).toBe(1000)
    for (const share of shares) expect(Math.round(share * 10)).toBeCloseTo(share * 10, 9)
  })

  it("keeps a level with no quotes at 0.0", () => {
    expect(levelQuoteShares("f", [
      { __factor_group__: "a", optimal_scenario_value: 1, quote_count: 4 },
      { __factor_group__: "b", optimal_scenario_value: 1, quote_count: 0 },
    ])).toEqual([100, 0])
  })

  it("throws when the factor has no quotes", () => {
    expect(() => levelQuoteShares("f", [
      { __factor_group__: "a", optimal_scenario_value: 1, quote_count: 0 },
    ])).toThrow("Factor f has no quotes")
  })
})

describe("formatVsNeutral", () => {
  it("signs the change against the neutral rate 1.0", () => {
    expect(formatVsNeutral(1.4)).toBe("+40.0%")
    expect(formatVsNeutral(0.75)).toBe("-25.0%")
    expect(formatVsNeutral(1)).toBe("0.0%")
  })
})
