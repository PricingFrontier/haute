import { describe, it, expect } from "vitest"
import { extractBandingLevelsForNode, extractBandingLevels } from "../../utils/banding"
import { buildCartesianEntries } from "../../panels/editors/rating/ratingTableUtils"
import type { SimpleNode } from "../../panels/editors/_shared"

// ─── Helpers ─────────────────────────────────────────────────────

/** Shorthand to build a banding node with the given factors config. */
function makeBandingNode(
  id: string,
  factors: unknown[] | undefined,
  overrides?: Partial<SimpleNode["data"]>,
): SimpleNode {
  return {
    id,
    data: {
      label: `Banding ${id}`,
      description: "",
      nodeType: "banding",
      config: factors !== undefined ? { factors } : {},
      ...overrides,
    },
  }
}

/** Shorthand to build a non-banding node. */
function makeOtherNode(id: string, nodeType = "polars"): SimpleNode {
  return {
    id,
    data: {
      label: `Node ${id}`,
      description: "",
      nodeType,
    },
  }
}

// ─── Tests: extractBandingLevelsForNode ──────────────────────────

describe("extractBandingLevelsForNode", () => {
  it("returns empty object for a non-banding node", () => {
    const nodes: SimpleNode[] = [makeOtherNode("n1", "polars")]
    expect(extractBandingLevelsForNode(nodes, "n1")).toEqual({})
  })

  it("returns empty object for a node without factors", () => {
    const nodes: SimpleNode[] = [makeBandingNode("b1", undefined)]
    expect(extractBandingLevelsForNode(nodes, "b1")).toEqual({})
  })

  it("returns empty object for a node not found", () => {
    const nodes: SimpleNode[] = [makeOtherNode("n1")]
    expect(extractBandingLevelsForNode(nodes, "missing")).toEqual({})
  })

  it("extracts single factor levels correctly", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "age_band",
          rules: [
            { op1: ">=", val1: "18", op2: "<", val2: "30", assignment: "Young" },
            { op1: ">=", val1: "30", op2: "<", val2: "50", assignment: "Middle" },
            { op1: ">=", val1: "50", op2: "", val2: "", assignment: "Senior" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({ age_band: ["Young", "Middle", "Senior"] })
  })

  it("extracts breakpoint labels as levels", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "breakpoints",
          column: "proposer_age",
          outputColumn: "proposer_age_band",
          rules: [
            { boundary: "25", label: "Under 25" },
            { boundary: "50", label: "25 to 49" },
            { boundary: "", label: "50 plus" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({
      proposer_age_band: ["Under 25", "25 to 49", "50 plus"],
    })
  })

  it("extracts multiple factor levels", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "age_band",
          rules: [
            { op1: ">=", val1: "0", op2: "<", val2: "30", assignment: "Young" },
            { op1: ">=", val1: "30", op2: "", val2: "", assignment: "Old" },
          ],
        },
        {
          banding: "categorical",
          column: "region",
          outputColumn: "region_band",
          rules: [
            { value: "NSW", assignment: "East" },
            { value: "VIC", assignment: "East" },
            { value: "WA", assignment: "West" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({
      age_band: ["Young", "Old"],
      region_band: ["East", "West"],
    })
  })

  it("ignores factors without outputColumn", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "",
          rules: [
            { op1: ">=", val1: "18", op2: "<", val2: "30", assignment: "Young" },
          ],
        },
        {
          banding: "continuous",
          column: "score",
          outputColumn: "score_band",
          rules: [
            { op1: ">=", val1: "0", op2: "<", val2: "50", assignment: "Low" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({ score_band: ["Low"] })
    // The empty-outputColumn factor should not produce a key
    expect(Object.keys(result)).toEqual(["score_band"])
  })

  it("ignores rules without assignment", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "age_band",
          rules: [
            { op1: ">=", val1: "18", op2: "<", val2: "30", assignment: "Young" },
            { op1: ">=", val1: "30", op2: "<", val2: "50", assignment: "" },
            { op1: ">=", val1: "50", op2: "", val2: "" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({ age_band: ["Young"] })
  })

  it("deduplicates assignments within a factor", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "categorical",
          column: "region",
          outputColumn: "region_band",
          rules: [
            { value: "NSW", assignment: "East" },
            { value: "VIC", assignment: "East" },
            { value: "QLD", assignment: "East" },
            { value: "WA", assignment: "West" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevelsForNode(nodes, "b1")
    expect(result).toEqual({ region_band: ["East", "West"] })
  })
})

// ─── Tests: extractBandingLevels ─────────────────────────────────

describe("extractBandingLevels", () => {
  it("returns empty object when no banding nodes exist", () => {
    const nodes: SimpleNode[] = [
      makeOtherNode("n1", "polars"),
      makeOtherNode("n2", "output"),
    ]
    expect(extractBandingLevels(nodes)).toEqual({})
  })

  it("aggregates levels from multiple banding nodes", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "age_band",
          rules: [
            { op1: ">=", val1: "0", op2: "<", val2: "30", assignment: "Young" },
          ],
        },
      ]),
      makeBandingNode("b2", [
        {
          banding: "continuous",
          column: "age",
          outputColumn: "age_band",
          rules: [
            { op1: ">=", val1: "30", op2: "", val2: "", assignment: "Old" },
          ],
        },
        {
          banding: "categorical",
          column: "vehicle",
          outputColumn: "vehicle_band",
          rules: [
            { value: "sedan", assignment: "Car" },
            { value: "SUV", assignment: "Truck" },
          ],
        },
      ]),
    ]

    const result = extractBandingLevels(nodes)
    expect(result.age_band).toEqual(expect.arrayContaining(["Young", "Old"]))
    expect(result.age_band).toHaveLength(2)
    expect(result.vehicle_band).toEqual(expect.arrayContaining(["Car", "Truck"]))
    expect(result.vehicle_band).toHaveLength(2)
  })

  it("aggregates categorical assignments and breakpoint labels for rating factors", () => {
    const nodes: SimpleNode[] = [
      makeBandingNode("b1", [
        {
          banding: "categorical",
          column: "channel",
          outputColumn: "channel_band",
          rules: [
            { value: "direct", assignment: "Direct" },
            { value: "broker", assignment: "Broker" },
          ],
        },
        {
          banding: "breakpoints",
          column: "proposer_age",
          outputColumn: "proposer_age_band",
          rules: [
            { boundary: "25", label: "Under 25" },
            { boundary: "50", label: "25 to 49" },
            { boundary: "", label: "50 plus" },
          ],
        },
        {
          banding: "breakpoints",
          column: "vehicle_age",
          outputColumn: "vehicle_age_band",
          rules: [
            { boundary: "1", label: "New" },
            { boundary: "5", label: "Established" },
            { boundary: "", label: "Older" },
          ],
        },
      ]),
    ]

    expect(extractBandingLevels(nodes)).toEqual({
      channel_band: ["Direct", "Broker"],
      proposer_age_band: ["Under 25", "25 to 49", "50 plus"],
      vehicle_age_band: ["New", "Established", "Older"],
    })
  })

  it("handles nodes with no config", () => {
    const nodes: SimpleNode[] = [
      {
        id: "b1",
        data: {
          label: "Banding",
          description: "",
          nodeType: "banding",
          // no config at all
        },
      },
    ]
    expect(extractBandingLevels(nodes)).toEqual({})
  })

  it("handles nodes with empty factors array", () => {
    const nodes: SimpleNode[] = [makeBandingNode("b1", [])]
    expect(extractBandingLevels(nodes)).toEqual({})
  })
})

// ─── Tests: buildCartesianEntries ────────────────────────────────

describe("buildCartesianEntries", () => {
  it("returns empty array for zero factors", () => {
    const result = buildCartesianEntries([], {}, [], "1.0")
    expect(result).toEqual([])
  })

  it("returns existing entries when any factor has zero levels", () => {
    const existing = [{ age_band: "Young", value: 1.5 }]
    const result = buildCartesianEntries(
      ["age_band", "region_band"],
      { age_band: ["Young", "Old"] },  // region_band missing -> 0 levels
      existing,
      "1.0",
    )
    expect(result).toBe(existing)
  })

  it("returns existing entries when factor has empty levels array", () => {
    const existing = [{ age_band: "Young", value: 1.5 }]
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: [] },
      existing,
      "1.0",
    )
    expect(result).toBe(existing)
  })

  it("builds 1-way entries (single factor, 3 levels)", () => {
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young", "Middle", "Senior"] },
      [],
      "1.0",
    )

    expect(result).toHaveLength(3)
    expect(result[0]).toEqual({ age_band: "Young", value: 1.0 })
    expect(result[1]).toEqual({ age_band: "Middle", value: 1.0 })
    expect(result[2]).toEqual({ age_band: "Senior", value: 1.0 })
  })

  it("builds 2-way Cartesian product (2 factors with 2 levels each)", () => {
    const result = buildCartesianEntries(
      ["age_band", "region_band"],
      { age_band: ["Young", "Old"], region_band: ["East", "West"] },
      [],
      "1.0",
    )

    expect(result).toHaveLength(4)
    // Verify all combinations exist
    const keys = result.map(e => `${e.age_band}|${e.region_band}`)
    expect(keys).toEqual([
      "Young|East",
      "Young|West",
      "Old|East",
      "Old|West",
    ])
    // All values should be the default
    expect(result.every(e => e.value === 1.0)).toBe(true)
  })

  it("builds 3-way Cartesian product (2x2x2 = 8 entries)", () => {
    const result = buildCartesianEntries(
      ["age_band", "region_band", "vehicle_band"],
      {
        age_band: ["Young", "Old"],
        region_band: ["East", "West"],
        vehicle_band: ["Car", "Truck"],
      },
      [],
      "1.0",
    )

    expect(result).toHaveLength(8)
    // Spot-check first and last
    expect(result[0]).toEqual({
      age_band: "Young",
      region_band: "East",
      vehicle_band: "Car",
      value: 1.0,
    })
    expect(result[7]).toEqual({
      age_band: "Old",
      region_band: "West",
      vehicle_band: "Truck",
      value: 1.0,
    })
  })

  it("preserves existing values when rebuilding (key = factor values joined by |)", () => {
    const existing = [
      { age_band: "Young", region_band: "East", value: 1.25 },
      { age_band: "Old", region_band: "West", value: 0.85 },
    ]

    const result = buildCartesianEntries(
      ["age_band", "region_band"],
      { age_band: ["Young", "Old"], region_band: ["East", "West"] },
      existing,
      "1.0",
    )

    expect(result).toHaveLength(4)

    const youngEast = result.find(e => e.age_band === "Young" && e.region_band === "East")
    expect(youngEast?.value).toBe(1.25)

    const oldWest = result.find(e => e.age_band === "Old" && e.region_band === "West")
    expect(oldWest?.value).toBe(0.85)

    // New combinations should get the default
    const youngWest = result.find(e => e.age_band === "Young" && e.region_band === "West")
    expect(youngWest?.value).toBe(1.0)

    const oldEast = result.find(e => e.age_band === "Old" && e.region_band === "East")
    expect(oldEast?.value).toBe(1.0)
  })

  it("uses defaultValue when no existing entry matches", () => {
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young", "Old"] },
      [],
      "2.5",
    )

    expect(result).toHaveLength(2)
    expect(result[0].value).toBe(2.5)
    expect(result[1].value).toBe(2.5)
  })

  it("falls back to 1.0 when defaultValue is null", () => {
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young"] },
      [],
      null,
    )

    expect(result).toHaveLength(1)
    expect(result[0].value).toBe(1.0)
  })

  it("falls back to 1.0 when defaultValue is empty string", () => {
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young"] },
      [],
      "",
    )

    expect(result).toHaveLength(1)
    expect(result[0].value).toBe(1.0)
  })

  it("falls back to 1.0 when defaultValue is whitespace-only", () => {
    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young"] },
      [],
      "   ",
    )

    expect(result).toHaveLength(1)
    expect(result[0].value).toBe(1.0)
  })

  it("preserves existing numeric values parsed from strings", () => {
    const existing = [
      { age_band: "Young", value: "1.75" },
    ]

    const result = buildCartesianEntries(
      ["age_band"],
      { age_band: ["Young", "Old"] },
      existing as Record<string, string | number>[],
      "1.0",
    )

    expect(result).toHaveLength(2)
    const young = result.find(e => e.age_band === "Young")
    expect(young?.value).toBe(1.75)
  })
})
