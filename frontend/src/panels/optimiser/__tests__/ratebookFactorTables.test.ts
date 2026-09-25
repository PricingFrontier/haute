import { describe, expect, it } from "vitest"
import { factorTablesCsv } from "../ratebookFactorTables"

describe("factorTablesCsv", () => {
  it("writes one row per level, prefixed by its factor, over the union of columns, with the collar", () => {
    const csv = factorTablesCsv(
      {
        age_band: [
          { __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 },
          { __factor_group__: "26+", optimal_scenario_value: 0.95 },
        ],
        region: [{ __factor_group__: "North, East", optimal_scenario_value: 1 }],
      },
      { min: 0.9, max: 1.1 },
    )

    expect(csv.split("\n")).toEqual([
      "factor,__factor_group__,optimal_scenario_value,quote_count,combined_factor_min,combined_factor_max",
      "age_band,17-25,1.1,40,0.9,1.1",
      "age_band,26+,0.95,,0.9,1.1",
      'region,"North, East",1,,0.9,1.1',
    ])
  })

  it("writes the collar exactly as the solve recorded it, Float32 widening included", () => {
    const csv = factorTablesCsv(
      { region: [{ __factor_group__: "North", optimal_scenario_value: 1.2 }] },
      { min: 0.8999999761581421, max: 1.100000023841858 },
    )

    expect(csv.split("\n")[1]).toBe("region,North,1.2,0.8999999761581421,1.100000023841858")
  })

  it("keeps a single-value collar", () => {
    const csv = factorTablesCsv(
      { region: [{ __factor_group__: "North", optimal_scenario_value: 1 }] },
      { min: 1, max: 1 },
    )

    expect(csv.split("\n")[1]).toBe("region,North,1,1,1")
  })
})
