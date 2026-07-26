/**
 * Pure logic tests for rating table utility functions.
 *
 * Tests: normaliseRatingTables, relativityColor, relativityTextColor,
 * tableStats, buildCartesianEntries
 */
import { describe, it, expect } from "vitest"
import {
  normaliseRatingTables,
  relativityColor,
  relativityTextColor,
  tableStats,
  buildCartesianEntries,
  resolveDefault,
  extractPreviewCategoricalLevels,
  extractTableEntryFactorLevels,
  mergeFactorLevels,
  ratingTableStatus,
} from "../../panels/editors/rating/ratingTableUtils"

// ─── normaliseRatingTables ───────────────────────────────────────

describe("normaliseRatingTables", () => {
  it("returns normalised copies of existing tables when present", () => {
    const tables = [{ factors: ["age"], outputColumn: "af", defaultValue: "1.0", entries: [] }]
    const result = normaliseRatingTables({ tables })
    expect(result).toEqual([{ factors: ["age"], outputColumn: "af", defaultValue: "1.0", entries: [] }])
    expect(result).not.toBe(tables)
    expect(result[0]).not.toBe(tables[0])
  })

  it("preserves outputColumn and normalises invalid output values to blank", () => {
    const result = normaliseRatingTables({
      tables: [
        { factors: [], outputColumn: "age_factor", defaultValue: "1.0", entries: [] },
        { factors: [], outputColumn: "   ", defaultValue: "1.0", entries: [] },
        { factors: [], defaultValue: "1.0", entries: [] },
        { factors: [], outputColumn: 42, defaultValue: "1.0", entries: [] },
      ],
    })

    expect(result.map(table => table.outputColumn)).toEqual(["age_factor", "   ", "", ""])
  })

  it("returns default table when tables is undefined", () => {
    const result = normaliseRatingTables({})
    expect(result).toHaveLength(1)
    expect(result[0].factors).toEqual([])
    expect(result[0].defaultValue).toBe("1.0")
  })

  it("returns default table when tables is empty array", () => {
    const result = normaliseRatingTables({ tables: [] })
    expect(result).toHaveLength(1)
  })

  it("returns default table when tables is non-array", () => {
    const result = normaliseRatingTables({ tables: "not an array" })
    expect(result).toHaveLength(1)
  })

  it("normalises malformed table fields without preserving invalid entries", () => {
    const result = normaliseRatingTables({
      tables: [
        null,
        [],
        {
          factors: ["age", 42, "region"],
          outputColumn: "risk_factor",
          defaultValue: 1.25,
          entries: [
            { age: "young", value: 1.1 },
            ["not", "an", "entry"],
            null,
          ],
        },
        {
          factors: "age",
          outputColumn: "fallback_factor",
          defaultValue: { bad: true },
          entries: "not entries",
        },
      ],
    })

    expect(result).toEqual([
      { factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
      { factors: [], outputColumn: "", defaultValue: "1.0", entries: [] },
      {
        factors: ["age", "region"],
        outputColumn: "risk_factor",
        defaultValue: "1.25",
        entries: [{ age: "young", value: 1.1 }],
      },
      {
        factors: [],
        outputColumn: "fallback_factor",
        defaultValue: "1.0",
        entries: [],
      },
    ])
  })

  it("preserves explicit null default values", () => {
    expect(normaliseRatingTables({
      tables: [
        {
          factors: ["channel"],
          outputColumn: "channel_factor",
          defaultValue: null,
          entries: [],
        },
      ],
    })).toEqual([
      {
        factors: ["channel"],
        outputColumn: "channel_factor",
        defaultValue: null,
        entries: [],
      },
    ])
  })

  it("preserves valid selected factor dtype descriptors without mutating the source", () => {
    const tables = [{
      factors: ["age", "region"],
      outputColumn: "rating",
      defaultValue: "1.0",
      entries: [{ age: "young", region: "north", value: 1.1 }],
      factorDtypes: {
        age: { kind: "Enum", categories: ["young", "old"] },
        region: { kind: "String" },
        removed_factor: { kind: "Int64" },
        blank_kind: { kind: "  " },
        invalid_value: "string",
      },
    }]
    const original = structuredClone(tables)

    const result = normaliseRatingTables({ tables })

    expect(result[0]?.factorDtypes).toEqual({
      age: { kind: "Enum", categories: ["young", "old"] },
      region: { kind: "String" },
    })
    expect(result[0]?.factorDtypes?.age).not.toBe(tables[0]?.factorDtypes.age)
    expect(result[0]?.entries).toEqual(tables[0]?.entries)
    expect(tables).toEqual(original)
  })

  it("preserves every structured dtype descriptor as an independent value", () => {
    const factorDtypes = {
      timestamp: { kind: "Datetime", timeUnit: "us", timeZone: "Europe/London" },
      elapsed: { kind: "Duration", timeUnit: "ns" },
      amount: { kind: "Decimal", precision: 12, scale: 2 },
      unbounded_amount: { kind: "Decimal", precision: null, scale: 4 },
      channel: { kind: "Enum", categories: ["web", "branch"] },
    }
    const factors = Object.keys(factorDtypes)

    const [result] = normaliseRatingTables({
      tables: [{ factors, factorDtypes, outputColumn: "rating", entries: [] }],
    })

    expect(result.factorDtypes).toEqual(factorDtypes)
    for (const factor of factors) {
      expect(result.factorDtypes?.[factor]).not.toBe(factorDtypes[factor as keyof typeof factorDtypes])
    }
    expect(result.factorDtypes?.channel).toEqual({ kind: "Enum", categories: ["web", "branch"] })
    expect((result.factorDtypes?.channel as { categories: string[] }).categories)
      .not.toBe(factorDtypes.channel.categories)
  })

  it.each([
    ["non-object metadata", "invalid"],
    ["array metadata", []],
    ["unknown kind", { kind: "Binary" }],
    ["primitive descriptor with extra key", { kind: "String", extra: true }],
    ["datetime missing timezone", { kind: "Datetime", timeUnit: "us" }],
    ["datetime with invalid unit", { kind: "Datetime", timeUnit: "s", timeZone: null }],
    ["datetime with invalid timezone", { kind: "Datetime", timeUnit: "us", timeZone: 42 }],
    ["duration missing unit", { kind: "Duration" }],
    ["duration with invalid unit", { kind: "Duration", timeUnit: "s" }],
    ["decimal missing scale", { kind: "Decimal", precision: 12 }],
    ["decimal with boolean precision", { kind: "Decimal", precision: true, scale: 2 }],
    ["decimal with fractional precision", { kind: "Decimal", precision: 12.5, scale: 2 }],
    ["decimal with non-number scale", { kind: "Decimal", precision: 12, scale: "2" }],
    ["decimal with fractional scale", { kind: "Decimal", precision: 12, scale: 2.5 }],
    ["enum with non-array categories", { kind: "Enum", categories: "web" }],
    ["enum with non-string category", { kind: "Enum", categories: ["web", 2] }],
    ["enum with duplicate categories", { kind: "Enum", categories: ["web", "web"] }],
  ])("drops %s", (_description, descriptor) => {
    const [result] = normaliseRatingTables({
      tables: [{
        factors: ["risk"],
        factorDtypes: { risk: descriptor },
        outputColumn: "rating",
        entries: [],
      }],
    })

    expect(result.factorDtypes).toBeUndefined()
  })

  it.each([null, [], "invalid"])("drops a malformed factorDtypes container: %j", factorDtypes => {
    const [result] = normaliseRatingTables({
      tables: [{ factors: ["risk"], factorDtypes, outputColumn: "rating", entries: [] }],
    })

    expect(result.factorDtypes).toBeUndefined()
  })
})

describe("ratingTableStatus", () => {
  it("marks a complete table as healthy", () => {
    const tables = [{
      factors: ["age_band"],
      outputColumn: "age_factor",
      defaultValue: "1.0",
      entries: [{ age_band: "young", value: 1.1 }],
    }]

    expect(ratingTableStatus(tables[0], 0, tables)).toEqual({
      state: "healthy",
      issues: [],
    })
  })

  it("reports blank output columns, missing factors, and missing entries", () => {
    const tables = [{
      factors: [],
      outputColumn: " ",
      defaultValue: "1.0",
      entries: [],
    }]

    expect(ratingTableStatus(tables[0], 0, tables)).toEqual({
      state: "problem",
      issues: [
        "Output column is required",
        "Add at least one factor",
        "Add at least one rating entry",
      ],
    })
  })

  it("reports duplicate output column names across tables", () => {
    const tables = [
      {
        factors: ["region"],
        outputColumn: "region_factor",
        defaultValue: "1.0",
        entries: [{ region: "north", value: 1.0 }],
      },
      {
        factors: ["region"],
        outputColumn: "region_factor",
        defaultValue: "1.0",
        entries: [{ region: "south", value: 1.0 }],
      },
    ]

    expect(ratingTableStatus(tables[0], 0, tables)).toEqual({
      state: "problem",
      issues: ["Output column name must be unique"],
    })
    expect(ratingTableStatus(tables[1], 1, tables)).toEqual({
      state: "problem",
      issues: ["Output column name must be unique"],
    })
  })
})

// ─── relativityColor ─────────────────────────────────────────────

describe("relativityColor", () => {
  it("returns transparent for NaN", () => {
    expect(relativityColor(NaN)).toBe("transparent")
  })

  it("returns transparent for value of exactly 1.0", () => {
    expect(relativityColor(1.0)).toBe("transparent")
  })

  it("returns transparent for values within ±0.005 of 1.0", () => {
    expect(relativityColor(1.004)).toBe("transparent")
    expect(relativityColor(0.996)).toBe("transparent")
    expect(relativityColor(1.005)).toBe("transparent")
  })

  it("returns colored for values just beyond the ±0.005 boundary", () => {
    const above = relativityColor(1.006)
    expect(above).toContain("rgba(var(--danger-rgb)")
    const below = relativityColor(0.994)
    expect(below).toContain("rgba(var(--chart-below-rgb)")
  })

  it("returns red-tinted color for values above 1.005", () => {
    const color = relativityColor(1.1)
    expect(color).toContain("rgba(var(--danger-rgb)")
  })

  it("returns blue-tinted color for values below 0.995", () => {
    const color = relativityColor(0.8)
    expect(color).toContain("rgba(var(--chart-below-rgb)")
  })

  it("increases alpha with larger deviation", () => {
    const low = relativityColor(1.05)
    const high = relativityColor(1.4)
    // Extract alpha values
    const alphaLow = parseFloat(low.match(/[\d.]+\)$/)?.[0] ?? "0")
    const alphaHigh = parseFloat(high.match(/[\d.]+\)$/)?.[0] ?? "0")
    expect(alphaHigh).toBeGreaterThan(alphaLow)
  })

  it("caps alpha at max deviation (0.5)", () => {
    const color1 = relativityColor(1.5)
    const color2 = relativityColor(2.0)
    // Both should have same max alpha since t is capped at 1
    expect(color1).toBe(color2)
  })
})

// ─── relativityTextColor ──────────────────────────────────────────

describe("relativityTextColor", () => {
  it("returns secondary text color for NaN", () => {
    expect(relativityTextColor(NaN)).toBe("var(--text-secondary)")
  })

  it("returns red for values above 1.005", () => {
    expect(relativityTextColor(1.1)).toBe("var(--danger-solid)")
  })

  it("returns blue for values below 0.995", () => {
    expect(relativityTextColor(0.8)).toBe("var(--chart-below)")
  })

  it("returns green for values at 1.0", () => {
    expect(relativityTextColor(1.0)).toBe("var(--success)")
  })

  it("returns green for values within ±0.005 of 1.0", () => {
    expect(relativityTextColor(1.003)).toBe("var(--success)")
    expect(relativityTextColor(0.997)).toBe("var(--success)")
  })
})

// ─── tableStats ──────────────────────────────────────────────────

describe("tableStats", () => {
  it("returns null for empty entries", () => {
    expect(tableStats([])).toBeNull()
  })

  it("returns null when no entries have numeric values", () => {
    expect(tableStats([{ age: "young" }])).toBeNull()
  })

  it("computes correct stats for numeric values", () => {
    const entries = [
      { age: "young", value: 1.1 },
      { age: "mid", value: 1.0 },
      { age: "old", value: 0.9 },
    ]
    const stats = tableStats(entries)
    expect(stats).not.toBeNull()
    expect(stats!.min).toBe(0.9)
    expect(stats!.max).toBe(1.1)
    expect(stats!.avg).toBeCloseTo(1.0, 5)
    expect(stats!.count).toBe(3)
  })

  it("parses string values", () => {
    const entries = [{ value: "1.5" }, { value: "2.5" }]
    const stats = tableStats(entries)
    expect(stats).not.toBeNull()
    expect(stats!.min).toBe(1.5)
    expect(stats!.max).toBe(2.5)
  })

  it("ignores non-numeric string values", () => {
    const entries = [{ value: "abc" }, { value: 2.0 }]
    const stats = tableStats(entries)
    expect(stats).not.toBeNull()
    expect(stats!.count).toBe(1)
    expect(stats!.min).toBe(2.0)
  })

  it("handles single entry", () => {
    const stats = tableStats([{ value: 3.14 }])
    expect(stats).not.toBeNull()
    expect(stats!.min).toBe(3.14)
    expect(stats!.max).toBe(3.14)
    expect(stats!.avg).toBe(3.14)
    expect(stats!.count).toBe(1)
  })

  it("handles entries with missing value key", () => {
    const entries: Record<string, string | number>[] = [{ age: "young" }, { value: 1.5 }]
    const stats = tableStats(entries)
    expect(stats!.count).toBe(1)
  })
})

// ─── buildCartesianEntries ────────────────────────────────────────

describe("buildCartesianEntries", () => {
  const bandingLevels = {
    age_band: ["young", "mid", "old"],
    region: ["north", "south"],
  }

  it("returns empty array for zero factors", () => {
    expect(buildCartesianEntries([], bandingLevels, [], null)).toEqual([])
  })

  it("returns existing entries when a factor has no levels", () => {
    const existing = [{ unknown: "x", value: 1.0 }]
    const result = buildCartesianEntries(["unknown_factor"], bandingLevels, existing, null)
    expect(result).toBe(existing)
  })

  it("builds 1-way cartesian product", () => {
    const result = buildCartesianEntries(["age_band"], bandingLevels, [], "1.0")
    expect(result).toHaveLength(3)
    expect(result.map(e => e.age_band)).toEqual(["young", "mid", "old"])
    expect(result.every(e => e.value === 1.0)).toBe(true)
  })

  it("builds 2-way cartesian product", () => {
    const result = buildCartesianEntries(["age_band", "region"], bandingLevels, [], "1.0")
    expect(result).toHaveLength(6) // 3 * 2
    // Check all combinations exist
    const combos = result.map(e => `${e.age_band}|${e.region}`)
    expect(combos).toContain("young|north")
    expect(combos).toContain("old|south")
  })

  it("preserves existing values", () => {
    const existing = [{ age_band: "young", value: 1.5 }]
    const result = buildCartesianEntries(["age_band"], bandingLevels, existing, "1.0")
    const youngEntry = result.find(e => e.age_band === "young")
    expect(youngEntry?.value).toBe(1.5)
    // Other entries get default
    const midEntry = result.find(e => e.age_band === "mid")
    expect(midEntry?.value).toBe(1.0)
  })

  it("uses 1.0 as default when defaultValue is null", () => {
    const result = buildCartesianEntries(["age_band"], bandingLevels, [], null)
    expect(result.every(e => e.value === 1.0)).toBe(true)
  })

  it("uses 1.0 as default when defaultValue is empty string", () => {
    const result = buildCartesianEntries(["age_band"], bandingLevels, [], "")
    expect(result.every(e => e.value === 1.0)).toBe(true)
  })

  it("parses defaultValue as number", () => {
    const result = buildCartesianEntries(["age_band"], bandingLevels, [], "2.5")
    expect(result.every(e => e.value === 2.5)).toBe(true)
  })

  it("preserves string existing values as numbers", () => {
    const existing = [{ age_band: "young", value: "1.3" }]
    const result = buildCartesianEntries(["age_band"], bandingLevels, existing, "1.0")
    const youngEntry = result.find(e => e.age_band === "young")
    expect(youngEntry?.value).toBe(1.3)
  })

  it("ignores blank, null, and invalid existing values and invalid defaults", () => {
    const existing = [
      { age_band: "young", value: "" },
      { age_band: "mid", value: null as unknown as string },
      { age_band: "old", value: "not numeric" },
    ]

    const result = buildCartesianEntries(["age_band"], bandingLevels, existing, "not numeric")

    expect(result).toEqual([
      { age_band: "young", value: 1.0 },
      { age_band: "mid", value: 1.0 },
      { age_band: "old", value: 1.0 },
    ])
  })

  it("handles 3-way product", () => {
    const levels3 = { ...bandingLevels, size: ["small", "large"] }
    const result = buildCartesianEntries(["age_band", "region", "size"], levels3, [], "1.0")
    expect(result).toHaveLength(12) // 3 * 2 * 2
  })

  it("preserves existing values for matching 2-way keys", () => {
    const existing = [
      { age_band: "young", region: "north", value: 2.0 },
      { age_band: "old", region: "south", value: 3.0 },
    ]
    const result = buildCartesianEntries(["age_band", "region"], bandingLevels, existing, "1.0")
    expect(result).toHaveLength(6)
    const youngNorth = result.find(e => e.age_band === "young" && e.region === "north")
    expect(youngNorth?.value).toBe(2.0)
    const oldSouth = result.find(e => e.age_band === "old" && e.region === "south")
    expect(oldSouth?.value).toBe(3.0)
    const youngSouth = result.find(e => e.age_band === "young" && e.region === "south")
    expect(youngSouth?.value).toBe(1.0)
  })
})

// ─── preview-derived factor levels ──────────────────────────────

describe("extractPreviewCategoricalLevels", () => {
  const previewRows = [
    { region: "north", channel: "direct", premium: 120.5, empty: "", mixed: "A" },
    { region: "south", channel: "broker", premium: 99.9, empty: null, mixed: 2 },
    { region: "north", channel: "direct", premium: 110.0, empty: undefined, mixed: "B" },
  ]

  it("extracts distinct string levels from preview rows in first-seen order", () => {
    expect(extractPreviewCategoricalLevels(previewRows)).toEqual({
      region: ["north", "south"],
      channel: ["direct", "broker"],
    })
  })

  it("does not expose numeric, empty-only, or mixed-type preview columns", () => {
    const levels = extractPreviewCategoricalLevels(previewRows)
    expect(levels.premium).toBeUndefined()
    expect(levels.empty).toBeUndefined()
    expect(levels.mixed).toBeUndefined()
  })

  it("uses upstream string/categorical dtypes when available and excludes numeric upstream columns", () => {
    expect(extractPreviewCategoricalLevels(previewRows, [
      { name: "scheme", dtype: "str" },
      { name: "region", dtype: "String" },
      { name: "channel", dtype: "Categorical" },
      { name: "tier", dtype: "Enum" },
      { name: "premium", dtype: "Float64" },
    ])).toEqual({
      region: ["north", "south"],
      channel: ["direct", "broker"],
    })
  })

  it("returns no preview levels when rows are unavailable", () => {
    expect(extractPreviewCategoricalLevels(undefined)).toEqual({})
    expect(extractPreviewCategoricalLevels([])).toEqual({})
  })
})

describe("mergeFactorLevels", () => {
  it("preserves banding levels first and appends preview-derived raw levels", () => {
    expect(mergeFactorLevels(
      { age_band: ["young", "old"], region: ["north"] },
      { region: ["north", "south"], channel: ["direct", "broker"] },
    )).toEqual({
      age_band: ["young", "old"],
      region: ["north", "south"],
      channel: ["direct", "broker"],
    })
  })
})

// ─── resolveDefault ─────────────────────────────────────────────

describe("extractTableEntryFactorLevels", () => {
  it("extracts saved factor levels from existing table entries", () => {
    expect(extractTableEntryFactorLevels([
      {
        factors: ["channel"],
        outputColumn: "channel_factor",
        defaultValue: "1.0",
        entries: [
          { channel: "direct", value: 1.0 },
          { channel: "broker", value: 1.1 },
          { channel: "direct", value: 1.0 },
        ],
      },
    ])).toEqual({
      channel: ["direct", "broker"],
    })
  })

  it("extracts multi-factor levels and skips empty values", () => {
    expect(extractTableEntryFactorLevels([
      {
        factors: ["channel", "segment"],
        outputColumn: "factor",
        defaultValue: "1.0",
        entries: [
          { channel: "direct", segment: "retail", value: 1.0 },
          { channel: "broker", segment: "fleet", value: 1.1 },
          { channel: "", segment: "", value: 1.2 },
        ],
      },
    ])).toEqual({
      channel: ["direct", "broker"],
      segment: ["retail", "fleet"],
    })
  })

  it("skips null and undefined saved factor values", () => {
    expect(extractTableEntryFactorLevels([
      {
        factors: ["channel"],
        outputColumn: "channel_factor",
        defaultValue: "1.0",
        entries: [
          { channel: null as unknown as string, value: 1.0 },
          { channel: undefined as unknown as string, value: 1.1 },
          { channel: "direct", value: 1.2 },
        ],
      },
    ])).toEqual({
      channel: ["direct"],
    })
  })

  it("returns no saved levels when configured factors have no entries", () => {
    expect(extractTableEntryFactorLevels([
      {
        factors: ["channel"],
        outputColumn: "channel_factor",
        defaultValue: "1.0",
        entries: [],
      },
    ])).toEqual({})
  })
})

describe("resolveDefault", () => {
  it("returns 1 for null", () => {
    expect(resolveDefault(null)).toBe(1)
  })

  it("returns 1 for undefined", () => {
    expect(resolveDefault(undefined)).toBe(1)
  })

  it("returns 1 for empty string", () => {
    expect(resolveDefault("")).toBe(1)
  })

  it("returns 1 for whitespace-only string", () => {
    expect(resolveDefault("   ")).toBe(1)
  })

  it("parses numeric string correctly", () => {
    expect(resolveDefault("2.5")).toBe(2.5)
  })

  it("parses integer string correctly", () => {
    expect(resolveDefault("3")).toBe(3)
  })

  it("returns number value as-is", () => {
    expect(resolveDefault(0.75)).toBe(0.75)
  })

  it("returns 1 for non-numeric string", () => {
    expect(resolveDefault("abc")).toBe(1)
  })

  it("returns 0 for numeric zero", () => {
    expect(resolveDefault(0)).toBe(0)
  })

  it("parses negative numeric string", () => {
    expect(resolveDefault("-0.5")).toBe(-0.5)
  })
})
