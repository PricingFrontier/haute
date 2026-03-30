import { describe, it, expect } from "vitest"
import { NODE_TYPES, NODE_TYPE_META, PALETTE_TYPES, SINGLETON_TYPES, isSingletonType, SOURCE_ONLY_TYPES, SINK_ONLY_TYPES, nodeTypeIcons, nodeTypeColors, nodeTypeLabels } from "../nodeTypes"

describe("NODE_TYPES", () => {
  it("contains all expected node types", () => {
    expect(NODE_TYPES.API_INPUT).toBe("apiInput")
    expect(NODE_TYPES.DATA_SOURCE).toBe("dataSource")
    expect(NODE_TYPES.POLARS).toBe("polars")
    expect(NODE_TYPES.MODEL_SCORE).toBe("modelScore")
    expect(NODE_TYPES.BANDING).toBe("banding")
    expect(NODE_TYPES.RATING_STEP).toBe("ratingStep")
    expect(NODE_TYPES.OUTPUT).toBe("output")
    expect(NODE_TYPES.DATA_SINK).toBe("dataSink")
    expect(NODE_TYPES.EXTERNAL_FILE).toBe("externalFile")
    expect(NODE_TYPES.LIVE_SWITCH).toBe("liveSwitch")
    expect(NODE_TYPES.MODELLING).toBe("modelling")
    expect(NODE_TYPES.SCENARIO_EXPANDER).toBe("scenarioExpander")
    expect(NODE_TYPES.CONSTANT).toBe("constant")
    expect(NODE_TYPES.SUBMODEL).toBe("submodel")
    expect(NODE_TYPES.SUBMODEL_PORT).toBe("submodelPort")
  })

  it("has exactly 17 node types", () => {
    expect(Object.keys(NODE_TYPES)).toHaveLength(17)
  })
})

describe("NODE_TYPE_META", () => {
  it("has metadata for every node type", () => {
    for (const value of Object.values(NODE_TYPES)) {
      const meta = NODE_TYPE_META[value]
      expect(meta).toBeDefined()
      expect(meta.icon).toBeDefined()
      expect(meta.color).toMatch(/^#[0-9a-f]{6}$/i)
      expect(meta.label.length).toBeGreaterThan(0)
      expect(meta.name.length).toBeGreaterThan(0)
      expect(meta.description.length).toBeGreaterThan(0)
      expect(meta.defaultConfig).toBeDefined()
    }
  })

  it("has exactly one entry per NODE_TYPES value", () => {
    expect(Object.keys(NODE_TYPE_META)).toHaveLength(Object.keys(NODE_TYPES).length)
  })

  it("label is UPPER CASE, name is Title Case", () => {
    for (const value of Object.values(NODE_TYPES)) {
      const meta = NODE_TYPE_META[value]
      expect(meta.label).toBe(meta.label.toUpperCase())
      expect(meta.name).not.toBe(meta.name.toUpperCase())
    }
  })
})

describe("SINGLETON_TYPES", () => {
  it("contains apiInput and output", () => {
    expect(SINGLETON_TYPES.has(NODE_TYPES.API_INPUT)).toBe(true)
    expect(SINGLETON_TYPES.has(NODE_TYPES.OUTPUT)).toBe(true)
  })

  it("does not contain non-singleton types", () => {
    expect(SINGLETON_TYPES.has(NODE_TYPES.POLARS)).toBe(false)
    expect(SINGLETON_TYPES.has(NODE_TYPES.DATA_SOURCE)).toBe(false)
    expect(SINGLETON_TYPES.has(NODE_TYPES.LIVE_SWITCH)).toBe(false)
  })

  it("has exactly 2 entries", () => {
    expect(SINGLETON_TYPES.size).toBe(2)
  })

  it("isSingletonType returns true for singleton types", () => {
    expect(isSingletonType("apiInput")).toBe(true)
    expect(isSingletonType("output")).toBe(true)
  })

  it("isSingletonType returns false for non-singleton types", () => {
    expect(isSingletonType("polars")).toBe(false)
    expect(isSingletonType("dataSource")).toBe(false)
  })

  it("isSingletonType returns false for undefined", () => {
    expect(isSingletonType(undefined)).toBe(false)
  })
})

describe("SOURCE_ONLY_TYPES", () => {
  it("contains dataSource, apiInput, and constant", () => {
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.DATA_SOURCE)).toBe(true)
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.API_INPUT)).toBe(true)
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.CONSTANT)).toBe(true)
  })

  it("does not contain non-source types", () => {
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.POLARS)).toBe(false)
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.OUTPUT)).toBe(false)
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.DATA_SINK)).toBe(false)
  })

  it("has exactly 3 entries", () => {
    expect(SOURCE_ONLY_TYPES.size).toBe(3)
  })
})

describe("SINK_ONLY_TYPES", () => {
  it("contains output, dataSink, modelling, and optimiser", () => {
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.OUTPUT)).toBe(true)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.DATA_SINK)).toBe(true)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.MODELLING)).toBe(true)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.OPTIMISER)).toBe(true)
  })

  it("does not contain non-sink types", () => {
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.POLARS)).toBe(false)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.DATA_SOURCE)).toBe(false)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.API_INPUT)).toBe(false)
  })

  it("has exactly 4 entries", () => {
    expect(SINK_ONLY_TYPES.size).toBe(4)
  })
})

describe("PALETTE_TYPES", () => {
  it("contains only valid node types", () => {
    const allTypes = new Set(Object.values(NODE_TYPES))
    for (const t of PALETTE_TYPES) {
      expect(allTypes.has(t)).toBe(true)
    }
  })

  it("excludes submodel and submodelPort", () => {
    expect(PALETTE_TYPES).not.toContain(NODE_TYPES.SUBMODEL)
    expect(PALETTE_TYPES).not.toContain(NODE_TYPES.SUBMODEL_PORT)
  })

  it("has no duplicates", () => {
    expect(new Set(PALETTE_TYPES).size).toBe(PALETTE_TYPES.length)
  })
})

describe("derived lookups (backward compat)", () => {
  it("nodeTypeIcons has an icon for every node type", () => {
    for (const value of Object.values(NODE_TYPES)) {
      expect(nodeTypeIcons[value]).toBeDefined()
    }
  })

  it("nodeTypeColors has a valid hex color for every node type", () => {
    for (const value of Object.values(NODE_TYPES)) {
      expect(nodeTypeColors[value]).toBeDefined()
      expect(nodeTypeColors[value]).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it("nodeTypeLabels has a non-empty label for every node type", () => {
    for (const value of Object.values(NODE_TYPES)) {
      expect(nodeTypeLabels[value]).toBeDefined()
      expect(nodeTypeLabels[value].length).toBeGreaterThan(0)
    }
  })
})
