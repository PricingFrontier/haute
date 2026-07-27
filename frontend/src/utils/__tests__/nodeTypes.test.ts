import { describe, expect, it } from "vitest"
import { NODE_TYPES, NODE_TYPE_META, PALETTE_TYPES, SINK_ONLY_TYPES, SOURCE_ONLY_TYPES, isSingletonType } from "../nodeTypes"

describe("canonical data IO node types", () => {
  it("exposes dataInput and dataOutput", () => {
    expect(NODE_TYPES.DATA_INPUT).toBe("dataInput")
    expect(NODE_TYPES.DATA_OUTPUT).toBe("dataOutput")
  })

  it("uses complete canonical defaults", () => {
    expect(NODE_TYPE_META[NODE_TYPES.DATA_INPUT].defaultConfig).toEqual({ inputType: "file", cacheMode: "direct", format: "parquet", mode: "scan", path: "", arguments: {}, code: "" })
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
