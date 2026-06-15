import { describe, it, expect } from "vitest"
import {
  CONNECTION_RADIUS_BY_BUCKET,
  DEAD_BAND_BY_BUCKET,
  DEAD_BAND_MAX_WIDTH_FRACTION,
  ZOOM_BUCKET_FULL_MIN,
  ZOOM_BUCKET_MEDIUM_MIN,
  zoomSelector,
  zoomToBucket,
} from "../zoomBuckets"

describe("zoomToBucket", () => {
  it("buckets zoom levels at the canonical thresholds", () => {
    expect(zoomToBucket(1)).toBe("full")
    expect(zoomToBucket(0.56)).toBe("full")
    expect(zoomToBucket(0.55)).toBe("medium") // boundary is exclusive
    expect(zoomToBucket(0.31)).toBe("medium")
    expect(zoomToBucket(0.3)).toBe("compact") // boundary is exclusive
    expect(zoomToBucket(0.1)).toBe("compact")
  })

  it("pins the threshold constants", () => {
    expect(ZOOM_BUCKET_FULL_MIN).toBe(0.55)
    expect(ZOOM_BUCKET_MEDIUM_MIN).toBe(0.3)
  })
})

describe("zoomSelector", () => {
  it("reads the zoom from the React Flow transform tuple", () => {
    expect(zoomSelector({ transform: [10, 20, 1] })).toBe("full")
    expect(zoomSelector({ transform: [0, 0, 0.4] })).toBe("medium")
    expect(zoomSelector({ transform: [-5, 3, 0.2] })).toBe("compact")
  })
})

describe("behavioural constants (edge-targeting contract values)", () => {
  it("pins the modest connection radii (ruling 4 — NOT the withdrawn 56/160 table)", () => {
    expect(CONNECTION_RADIUS_BY_BUCKET).toEqual({ full: 20, medium: 28, compact: 36 })
  })

  it("pins the dead-band widths (ruling 3 gap geometry)", () => {
    expect(DEAD_BAND_BY_BUCKET).toEqual({ full: 28, medium: 32, compact: 36 })
  })

  it("pins the dead-band width clamp at 25% of node width", () => {
    expect(DEAD_BAND_MAX_WIDTH_FRACTION).toBe(0.25)
  })

  it("keeps compact snap reach no larger than the compact dead band", () => {
    // Ruling 3/4 interplay: a snap from the wrong side of the output
    // connector must never reach past the dead band into the connect zone.
    expect(CONNECTION_RADIUS_BY_BUCKET.compact).toBeLessThanOrEqual(
      DEAD_BAND_BY_BUCKET.compact,
    )
  })
})
