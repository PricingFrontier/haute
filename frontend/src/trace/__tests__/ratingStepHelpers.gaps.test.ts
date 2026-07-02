import { describe, it, expect } from "vitest"
import {
  asRatingStepTables,
  asRatingStepCombinedOutputs,
  ratingTableStatus,
  formatRatingStatus,
  hasRichRatingStepDetail,
} from "../ratingStepHelpers"
import type {
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceStep,
} from "../../types/trace"

describe("asRatingStepTables", () => {
  it("returns the tables array when present", () => {
    const tables: RatingStepTableDetail[] = [{ name: "t1" }, { name: "t2" }]
    const detail = { detail_type: "rating_step", tables } as TraceNodeDetail
    expect(asRatingStepTables(detail)).toBe(tables)
  })

  it("returns an empty array when tables is missing", () => {
    const detail = { detail_type: "rating_step" } as TraceNodeDetail
    expect(asRatingStepTables(detail)).toEqual([])
  })

  it("returns an empty array when tables is not an array", () => {
    const detail = { detail_type: "rating_step", tables: "nope" } as unknown as TraceNodeDetail
    expect(asRatingStepTables(detail)).toEqual([])
  })
})

describe("asRatingStepCombinedOutputs", () => {
  it("returns the combined_outputs array when present", () => {
    const combined = [{ column: "c", operation: "add", base_value: 1, input_values: {}, value: 2 }]
    const detail = {
      detail_type: "rating_step",
      combined_outputs: combined,
    } as TraceNodeDetail
    expect(asRatingStepCombinedOutputs(detail)).toBe(combined)
  })

  it("returns an empty array when combined_outputs is missing", () => {
    const detail = { detail_type: "rating_step" } as TraceNodeDetail
    expect(asRatingStepCombinedOutputs(detail)).toEqual([])
  })

  it("returns an empty array when combined_outputs is not an array", () => {
    const detail = {
      detail_type: "rating_step",
      combined_outputs: 42,
    } as unknown as TraceNodeDetail
    expect(asRatingStepCombinedOutputs(detail)).toEqual([])
  })
})

describe("ratingTableStatus", () => {
  it("returns the explicit status string when set", () => {
    expect(ratingTableStatus({ status: "unmatched_value" })).toBe("unmatched_value")
  })

  it("ignores an empty status string and falls through", () => {
    expect(ratingTableStatus({ status: "" as RatingStepTableDetail["status"] })).toBeUndefined()
  })

  it("returns 'default' when default_used is true", () => {
    expect(ratingTableStatus({ default_used: true })).toBe("default")
  })

  it("returns 'no_match' when matched is false", () => {
    expect(ratingTableStatus({ matched: false })).toBe("no_match")
  })

  it("returns 'matched' when matched is true", () => {
    expect(ratingTableStatus({ matched: true })).toBe("matched")
  })

  it("returns undefined when nothing is set", () => {
    expect(ratingTableStatus({})).toBeUndefined()
  })

  it("prefers default_used over matched flags", () => {
    expect(ratingTableStatus({ default_used: true, matched: true })).toBe("default")
  })
})

describe("formatRatingStatus", () => {
  it("replaces underscores with spaces", () => {
    expect(formatRatingStatus("no_match")).toBe("no match")
  })

  it("replaces all underscores", () => {
    expect(formatRatingStatus("a_b_c")).toBe("a b c")
  })

  it("leaves strings without underscores unchanged", () => {
    expect(formatRatingStatus("matched")).toBe("matched")
  })
})

describe("hasRichRatingStepDetail", () => {
  it("returns true when detail has tables array", () => {
    const step = {
      node_detail: { detail_type: "rating_step", tables: [] },
    } as unknown as TraceStep
    expect(hasRichRatingStepDetail(step)).toBe(true)
  })

  it("returns true when detail has combined_outputs array", () => {
    const step = {
      node_detail: { detail_type: "rating_step", combined_outputs: [] },
    } as unknown as TraceStep
    expect(hasRichRatingStepDetail(step)).toBe(true)
  })

  it("returns false when detail_type is not rating_step", () => {
    const step = {
      node_detail: { detail_type: "banding", tables: [] },
    } as unknown as TraceStep
    expect(hasRichRatingStepDetail(step)).toBe(false)
  })

  it("returns false when rating_step has neither array", () => {
    const step = {
      node_detail: { detail_type: "rating_step" },
    } as unknown as TraceStep
    expect(hasRichRatingStepDetail(step)).toBe(false)
  })

  it("returns false when node_detail is missing", () => {
    expect(hasRichRatingStepDetail({} as TraceStep)).toBe(false)
  })

  it("returns false for null step", () => {
    expect(hasRichRatingStepDetail(null)).toBe(false)
  })

  it("returns false for undefined step", () => {
    expect(hasRichRatingStepDetail(undefined)).toBe(false)
  })
})
