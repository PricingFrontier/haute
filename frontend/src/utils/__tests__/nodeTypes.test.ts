import { describe, expect, it } from "vitest"
import { NODE_TYPES, NODE_TYPE_META, PALETTE_TYPES, SINK_ONLY_TYPES, SOURCE_ONLY_TYPES, isSingletonType } from "../nodeTypes"
import { ALGORITHM_CAPABILITIES } from "../../panels/modelling/algorithmCapabilities"

describe("canonical data IO node types", () => {
  it("exposes dataInput and dataOutput", () => {
    expect(NODE_TYPES.DATA_INPUT).toBe("dataInput")
    expect(NODE_TYPES.DATA_OUTPUT).toBe("dataOutput")
  })

  it("uses complete canonical defaults", () => {
    // A new Data Input starts in step mode: an empty step list, no code key.
    expect(NODE_TYPE_META[NODE_TYPES.DATA_INPUT].defaultConfig).toEqual({ inputType: "file", format: "parquet", mode: "scan", path: "", arguments: {}, steps: [] })
    expect(NODE_TYPE_META[NODE_TYPES.DATA_OUTPUT].defaultConfig).toEqual({ outputType: "file", format: "parquet", mode: "sink", path: "", arguments: {} })
  })

  it("registers data input as a source and data output as a sink", () => {
    expect(SOURCE_ONLY_TYPES.has(NODE_TYPES.DATA_INPUT)).toBe(true)
    expect(SINK_ONLY_TYPES.has(NODE_TYPES.DATA_OUTPUT)).toBe(true)
    expect(PALETTE_TYPES).toContain(NODE_TYPES.DATA_INPUT)
    expect(PALETTE_TYPES).toContain(NODE_TYPES.DATA_OUTPUT)
  })

  it("keeps only the canonical singleton types", () => {
    expect(isSingletonType("apiInput")).toBe(true)
    expect(isSingletonType("output")).toBe(true)
    expect(isSingletonType("liveSwitch")).toBe(true)
    expect(isSingletonType("dataInput")).toBe(false)
  })
})

describe("Model Training palette entry", () => {
  // Every family in the backend algorithm registry, and the word the palette names it by.
  const PALETTE_FAMILY_WORDS: Record<string, string> = {
    catboost: "gradient boosting",
    lightgbm: "gradient boosting",
    xgboost: "gradient boosting",
    ebm: "EBM",
    glm: "GLM",
  }

  it("names every model family in the algorithm registry", () => {
    // A new registry family fails here until it is mapped and the description names it.
    expect(Object.keys(PALETTE_FAMILY_WORDS).sort()).toEqual(Object.keys(ALGORITHM_CAPABILITIES).sort())
    const description = NODE_TYPE_META[NODE_TYPES.MODELLING].description
    for (const word of new Set(Object.values(PALETTE_FAMILY_WORDS))) expect(description).toContain(word)
  })
})
