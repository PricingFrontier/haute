import { describe, expect, it } from "vitest"
import {
  GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT,
  shouldUseLiteGraphEffects,
} from "../graphPerformance"

describe("graph performance thresholds", () => {
  it("keeps optional graph effects for graphs below the shared size limit", () => {
    expect(shouldUseLiteGraphEffects(GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT - 1, 0)).toBe(false)
    expect(shouldUseLiteGraphEffects(250, 249)).toBe(false)
  })

  it("uses lite graph effects when combined node and edge count reaches the threshold", () => {
    expect(shouldUseLiteGraphEffects(GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT, 0)).toBe(true)
    expect(shouldUseLiteGraphEffects(500, 500)).toBe(true)
    expect(shouldUseLiteGraphEffects(1, GRAPH_EFFECTS_LITE_GRAPH_SIZE_LIMIT - 1)).toBe(true)
  })
})
