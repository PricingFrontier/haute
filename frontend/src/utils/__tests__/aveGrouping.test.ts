import { describe, expect, it } from "vitest"
import type { TrainAvePerFeatureRow } from "../../api/types"
import { aveGroupingCaption } from "../aveGrouping"

const bins = (...labels: string[]) => labels.map((label) => ({ label, exposure: 1, avg_actual: 1, avg_predicted: 1 }))
const row = (grouping: TrainAvePerFeatureRow["grouping"], ...labels: string[]) => ({ type: "numeric", grouping, bins: bins(...labels) })

describe("aveGroupingCaption", () => {
  it("names the grouping drawn, counting neither Missing nor Other", () => {
    expect(aveGroupingCaption(row("each value", "0", "1", "2", "Missing"))).toBe("Each value (3)")
    expect(aveGroupingCaption(row("equal exposure", "17–21", "22–30", "31–99"))).toBe("3 groups · equal exposure")
    expect(aveGroupingCaption(row("in order", "low", "mid", "high"))).toBe("3 levels · in order")
    expect(aveGroupingCaption(row("A–Z", "A", "B", "Missing"))).toBe("2 levels · A–Z")
    expect(aveGroupingCaption(row("by exposure", "a", "b", "Other (6 levels)", "Missing"))).toBe("Top 2 levels · by exposure")
  })

  it("falls back to the factor's type for a run saved before groupings were reported", () => {
    expect(aveGroupingCaption(row(undefined, "1–3", "4–6"))).toBe("numeric")
  })
})
