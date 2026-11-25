import { describe, expect, it } from "vitest"
import {
  attemptScenarioRead,
  readScenarioRows,
  ScenarioDataError,
  scenarioGapNotices,
} from "../scenarioRows"

const COLUMNS = {
  quoteIdCol: "quote_id",
  scenarioIndexCol: "scenario_index",
  scenarioValueCol: "scenario_value",
  series: ["expected_income", "volume"],
}

const NAN = { __haute_type__: "non_finite_float", value: "nan" }
const INF = { __haute_type__: "non_finite_float", value: "-inf" }

function row(overrides: Record<string, unknown> = {}) {
  return { quote_id: "Q1", scenario_index: 0, scenario_value: 1, expected_income: 10, volume: 1, ...overrides }
}

describe("readScenarioRows", () => {
  it("leaves a missing series value out of its series alone and counts it", () => {
    const read = readScenarioRows([row(), row({ scenario_index: 1, expected_income: null })], COLUMNS)

    expect(read.rows).toEqual([
      { quoteId: "Q1", scenarioIndex: 0, scenarioValue: 1, values: { expected_income: 10, volume: 1 } },
      { quoteId: "Q1", scenarioIndex: 1, scenarioValue: 1, values: { volume: 1 } },
    ])
    expect(read.gaps).toEqual([{ column: "expected_income", absent: 0, nulls: 1, nans: 0, infinities: 0 }])
    expect(scenarioGapNotices(read)).toEqual([
      "Null values found in optimiser input: 'expected_income' (1 null row).",
    ])
  })

  it("leaves out a row that cannot be placed on the scenario grid", () => {
    const read = readScenarioRows(
      [
        row(),
        row({ scenario_index: null }),
        row({ scenario_value: NAN }),
        row({ quote_id: null }),
      ],
      COLUMNS,
    )

    expect(read.rows).toHaveLength(1)
    expect(read.quoteIdNulls).toBe(1)
    // The solve's order: objective, scenario value, scenario index, then each constraint.
    expect(scenarioGapNotices(read)).toEqual([
      "Null quote_id values found in optimiser input (1 rows).",
      "Non-finite values found in optimiser input: 'scenario_value' (1 NaN row).",
      "Null values found in optimiser input: 'scenario_index' (1 null row).",
    ])
  })

  it("counts NaN and infinite cells as the solve's non-finite refusal does", () => {
    const read = readScenarioRows(
      [row({ volume: NAN }), row({ scenario_index: 1, volume: INF }), row({ scenario_index: 2, volume: INF })],
      COLUMNS,
    )

    expect(read.rows.map((r) => r.values)).toEqual([
      { expected_income: 10 },
      { expected_income: 10 },
      { expected_income: 10 },
    ])
    expect(scenarioGapNotices(read)).toEqual([
      "Non-finite values found in optimiser input: 'volume' (1 NaN row, 2 infinite rows).",
    ])
  })

  it("names a configured column the input does not have", () => {
    const { volume: _volume, ...withoutVolume } = row()
    const read = readScenarioRows([withoutVolume], COLUMNS)

    expect(read.rows[0].values).toEqual({ expected_income: 10 })
    expect(scenarioGapNotices(read)).toEqual(["Missing columns in scored data: ['volume']."])
  })

  it("reads a numeric string, as the server writes a wide integer", () => {
    const read = readScenarioRows([row({ expected_income: "9007199254740993" })], COLUMNS)
    expect(read.rows[0].values.expected_income).toBe(9007199254740992)
    expect(read.gaps).toEqual([])
  })

  it.each([
    ["a text cell", { volume: "bad" }, /Expected a number in "volume", received "bad"/],
    ["a blank text cell", { expected_income: " " }, /Expected a number in "expected_income"/],
    ["an object that is not a non-finite float", { volume: { value: 1 } }, /Expected a number in "volume"/],
    ["a fractional scenario index", { scenario_index: 1.5 }, /integer scenario index in "scenario_index"/],
  ])("throws ScenarioDataError for %s", (_case, overrides, message) => {
    const read = () => readScenarioRows([row(overrides)], COLUMNS)
    expect(read).toThrow(ScenarioDataError)
    expect(read).toThrow(message)
  })
})

describe("attemptScenarioRead", () => {
  it("turns a ScenarioDataError into its message", () => {
    expect(attemptScenarioRead(() => readScenarioRows([row({ volume: "bad" })], COLUMNS))).toEqual({
      value: null,
      error: 'Expected a number in "volume", received "bad"',
    })
  })

  it("lets any other error through", () => {
    expect(() => attemptScenarioRead(() => {
      throw new TypeError("a bug")
    })).toThrow(TypeError)
  })
})
