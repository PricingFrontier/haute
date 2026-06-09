import { describe, expect, it } from "vitest"
import type { Connection } from "@xyflow/react"
import { isPipelineConnectionValid } from "../connectionValidation"

function connection(source: string, target: string): Connection {
  return {
    source,
    target,
    sourceHandle: null,
    targetHandle: null,
  }
}

describe("connection validation", () => {
  it("does not block edgeJoin output-to-default-input connections globally", () => {
    expect(isPipelineConnectionValid(connection("join1", "polars1"))).toBe(true)
  })

  it("rejects self loops globally", () => {
    expect(isPipelineConnectionValid(connection("polars1", "polars1"))).toBe(false)
  })

  it("rejects incomplete connections globally", () => {
    expect(isPipelineConnectionValid({ ...connection("polars1", "output1"), target: null })).toBe(false)
  })
})
