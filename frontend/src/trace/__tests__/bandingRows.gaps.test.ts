import { describe, it, expect } from "vitest"
import {
  asBandingDetail,
  bandingRowFromFactor,
  bandingRowFromDetail,
  bandingRows,
  bandingRowsForDisplay,
  hasRenderableBandingRows,
  formatBandingTransform,
  formatBandingRange,
  hasBandingSecondaryDetail,
} from "../bandingRows"
import type {
  BandingFactorDetail,
  BandingNodeDetail,
  TraceNodeDetail,
} from "../../types/trace"

describe("asBandingDetail", () => {
  it("passes the detail through unchanged", () => {
    const detail = { detail_type: "banding" } as TraceNodeDetail
    expect(asBandingDetail(detail)).toBe(detail)
  })
})

describe("bandingRowFromFactor", () => {
  it("prefers input_column/matched_band over fallbacks and builds a keyed row", () => {
    const factor: BandingFactorDetail = {
      input_column: "age",
      column: "ignored",
      output_column: "age_band",
      matched_band: "30-40",
      selected_band: "ignored",
      input_value: 35,
      lower_bound: 30,
      upper_bound: 40,
      lower_inclusive: true,
      upper_inclusive: false,
      is_default: false,
      status: "matched",
    }
    expect(bandingRowFromFactor(factor, 2)).toEqual({
      key: "age_band-age-2",
      inputColumn: "age",
      outputColumn: "age_band",
      inputValue: 35,
      matchedBand: "30-40",
      lowerBound: 30,
      upperBound: 40,
      lowerInclusive: true,
      upperInclusive: false,
      isDefault: false,
      status: "matched",
    })
  })

  it("falls back to column and selected_band, and uses placeholders in the key", () => {
    const factor: BandingFactorDetail = {
      column: "tenure",
      selected_band: "long",
    }
    const row = bandingRowFromFactor(factor, 0)
    expect(row.inputColumn).toBe("tenure")
    expect(row.matchedBand).toBe("long")
    expect(row.outputColumn).toBeUndefined()
    // output placeholder used, input column present
    expect(row.key).toBe("output-tenure-0")
  })
})

describe("bandingRowFromDetail", () => {
  it("returns null when there is no identifying content", () => {
    const detail = { detail_type: "banding" } as BandingNodeDetail
    expect(bandingRowFromDetail(detail)).toBeNull()
  })

  it("builds a summary row when columns are present", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      input_column: "age",
      output_column: "age_band",
      matched_band: "30-40",
      input_value: 35,
    }
    const row = bandingRowFromDetail(detail)
    expect(row).not.toBeNull()
    expect(row?.key).toBe("age_band-age-summary")
    expect(row?.inputColumn).toBe("age")
  })

  it("is not null when only input_value is present (column placeholders used)", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      input_value: 7,
    }
    const row = bandingRowFromDetail(detail)
    expect(row).not.toBeNull()
    expect(row?.key).toBe("output-input-summary")
    expect(row?.inputValue).toBe(7)
  })

  it("falls back to column and selected_band", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      column: "tenure",
      selected_band: "long",
    }
    const row = bandingRowFromDetail(detail)
    expect(row?.inputColumn).toBe("tenure")
    expect(row?.matchedBand).toBe("long")
  })
})

describe("bandingRows", () => {
  it("returns rows from factors when factors is an array", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      factors: [
        { input_column: "age", output_column: "age_band", matched_band: "30-40" },
      ],
    }
    const rows = bandingRows(detail)
    expect(rows).toHaveLength(1)
    expect(rows[0].outputColumn).toBe("age_band")
  })

  it("returns empty array when there are no factors and no summary content", () => {
    const detail = { detail_type: "banding" } as BandingNodeDetail
    expect(bandingRows(detail)).toEqual([])
  })

  it("prepends a summary row when it does not duplicate an existing factor row", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      factors: [
        { input_column: "age", output_column: "age_band", matched_band: "30-40" },
      ],
      input_column: "income",
      output_column: "income_band",
      matched_band: "low",
    }
    const rows = bandingRows(detail)
    expect(rows).toHaveLength(2)
    expect(rows[0].outputColumn).toBe("income_band")
    expect(rows[1].outputColumn).toBe("age_band")
  })

  it("does not prepend a summary row that duplicates an existing factor row", () => {
    const detail: BandingNodeDetail = {
      detail_type: "banding",
      factors: [
        { input_column: "age", output_column: "age_band", matched_band: "30-40" },
      ],
      input_column: "age",
      output_column: "age_band",
      matched_band: "30-40",
    }
    const rows = bandingRows(detail)
    expect(rows).toHaveLength(1)
    expect(rows[0].outputColumn).toBe("age_band")
  })
})

describe("bandingRowsForDisplay", () => {
  const detail: BandingNodeDetail = {
    detail_type: "banding",
    factors: [
      { input_column: "age", output_column: "age_band", matched_band: "30-40" },
      { input_column: "income", output_column: "income_band", matched_band: "low" },
    ],
  }

  it("returns all rows when no tracedColumn is supplied", () => {
    expect(bandingRowsForDisplay(detail)).toHaveLength(2)
  })

  it("returns only the matching row when tracedColumn matches", () => {
    const rows = bandingRowsForDisplay(detail, "income_band")
    expect(rows).toHaveLength(1)
    expect(rows[0].outputColumn).toBe("income_band")
  })

  it("returns all rows when tracedColumn matches nothing", () => {
    expect(bandingRowsForDisplay(detail, "missing")).toHaveLength(2)
  })
})

describe("hasRenderableBandingRows", () => {
  it("is false for null/undefined", () => {
    expect(hasRenderableBandingRows(null)).toBe(false)
    expect(hasRenderableBandingRows(undefined)).toBe(false)
  })

  it("is false when detail_type is not banding", () => {
    const detail = { detail_type: "rating_step" } as TraceNodeDetail
    expect(hasRenderableBandingRows(detail)).toBe(false)
  })

  it("is false when there are no renderable rows", () => {
    const detail = { detail_type: "banding" } as TraceNodeDetail
    expect(hasRenderableBandingRows(detail)).toBe(false)
  })

  it("is true when there are renderable rows", () => {
    const detail = {
      detail_type: "banding",
      factors: [{ input_column: "age", output_column: "age_band", matched_band: "30-40" }],
    } as TraceNodeDetail
    expect(hasRenderableBandingRows(detail)).toBe(true)
  })
})

describe("formatBandingTransform", () => {
  it("includes the input column when present", () => {
    expect(
      formatBandingTransform({ key: "k", inputColumn: "age", inputValue: 35, matchedBand: "30-40" }),
    ).toBe("age=35 -> 30-40")
  })

  it("omits the column label when inputColumn is absent", () => {
    expect(formatBandingTransform({ key: "k", inputValue: 35, matchedBand: "30-40" })).toBe(
      "35 -> 30-40",
    )
  })

  it("renders the trace missing-value marker", () => {
    expect(formatBandingTransform({ key: "k" })).toBe("\u2014 -> \u2014")
  })
})

describe("formatBandingRange", () => {
  it("returns null when both bounds are absent", () => {
    expect(formatBandingRange({ key: "k" })).toBeNull()
  })

  it("uses inclusive brackets by default", () => {
    expect(formatBandingRange({ key: "k", lowerBound: 30, upperBound: 40 })).toBe("[30, 40]")
  })

  it("uses exclusive brackets when inclusive flags are false", () => {
    expect(
      formatBandingRange({
        key: "k",
        lowerBound: 30,
        upperBound: 40,
        lowerInclusive: false,
        upperInclusive: false,
      }),
    ).toBe("(30, 40)")
  })

  it("renders an empty side when only one bound is present", () => {
    expect(formatBandingRange({ key: "k", lowerBound: 30 })).toBe("[30, ]")
    expect(formatBandingRange({ key: "k", upperBound: 40 })).toBe("[, 40]")
  })
})

describe("hasBandingSecondaryDetail", () => {
  it("is false for null/undefined", () => {
    expect(hasBandingSecondaryDetail(null)).toBe(false)
    expect(hasBandingSecondaryDetail(undefined)).toBe(false)
  })

  it("is false when detail_type is not banding", () => {
    expect(hasBandingSecondaryDetail({ detail_type: "rating_step" } as TraceNodeDetail)).toBe(false)
  })

  it("is false when no bounds and not default", () => {
    expect(hasBandingSecondaryDetail({ detail_type: "banding" } as TraceNodeDetail)).toBe(false)
  })

  it("is true when a lower_bound is present", () => {
    expect(
      hasBandingSecondaryDetail({ detail_type: "banding", lower_bound: 30 } as TraceNodeDetail),
    ).toBe(true)
  })

  it("is true when an upper_bound is present", () => {
    expect(
      hasBandingSecondaryDetail({ detail_type: "banding", upper_bound: 40 } as TraceNodeDetail),
    ).toBe(true)
  })

  it("is true when is_default is true", () => {
    expect(
      hasBandingSecondaryDetail({ detail_type: "banding", is_default: true } as TraceNodeDetail),
    ).toBe(true)
  })
})
