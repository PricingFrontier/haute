import { describe, expect, it } from "vitest"
import { readOverview } from "../overviewConfig"

describe("readOverview", () => {
  it("returns {} when overview key is missing", () => {
    expect(readOverview({})).toEqual({})
  })

  it("returns {} when overview is null", () => {
    expect(readOverview({ overview: null })).toEqual({})
  })

  it("returns {} when overview is a string", () => {
    expect(readOverview({ overview: "yes" })).toEqual({})
  })

  it("returns {} when overview is an array", () => {
    expect(readOverview({ overview: [] })).toEqual({})
  })

  it("returns {} when overview is a number", () => {
    expect(readOverview({ overview: 42 })).toEqual({})
  })

  it("drops non-boolean values for known keys", () => {
    const result = readOverview({ overview: { dataset_header: "yes", schema: 1 } })
    expect(result).toEqual({})
  })

  it("returns valid boolean values faithfully", () => {
    expect(readOverview({ overview: { dataset_header: true, schema: false } }))
      .toEqual({ dataset_header: true, schema: false })
  })

  it("returns only the known keys present (dropping undefined)", () => {
    expect(readOverview({ overview: { dataset_header: true } }))
      .toEqual({ dataset_header: true })
  })

  it("ignores unknown extra keys", () => {
    const result = readOverview({
      overview: { dataset_header: true, future_key: true },
    })
    expect(result).toEqual({ dataset_header: true })
    expect("future_key" in result).toBe(false)
  })
})
