import { describe, expect, it } from "vitest"
import type { FrontierPoint } from "../../../api/types"
import {
  assessFrontierPoint,
  asSolvedOnSlice,
  discreteTradeOff,
  displayedSlicePosition,
  frontierConstraintKinds,
  sliceFrontier,
  sliceNeighbours,
  type ConstraintKinds,
} from "../frontierSlices"
import { makeOnlineFrontier, makeOnlineFrontierPoint, makeOnlineSolveResult } from "./fixtures"

/** A 2×3 grid: `volume` (min) at 5, 5.5 and 6 against `margin` (max) at 400
 *  and 450, margin varying fastest, so global indices interleave the slices. */
const GRID_KINDS: ConstraintKinds = { volume: "min", margin: "max" }
const GRID_NAMES = ["volume", "margin"]
const GRID_SPEC = [
  // [volume bound, margin bound, objective, volume total, margin total]
  [5, 400, 130, 5.2, 390],
  [5, 450, 140, 5.1, 440],
  [5.5, 400, 120, 5.6, 395],
  [5.5, 450, 128, 5.7, 445],
  [6, 400, 105, 6.1, 398],
  [6, 450, 110, 6.2, 449],
] as const

function gridPoint(
  [volume, margin, objective, volumeTotal, marginTotal]: readonly number[],
  overrides: Partial<FrontierPoint> = {},
): FrontierPoint {
  return makeOnlineFrontierPoint(0, {
    total_objective: objective,
    thresholds: { volume, margin },
    bounds: { volume, margin },
    totals: { volume: volumeTotal, margin: marginTotal },
    lambdas: { volume: 0.5, margin: 0.01 },
    ...overrides,
  } as Partial<ReturnType<typeof makeOnlineFrontierPoint>>)
}

function grid(overrides: Record<number, Partial<FrontierPoint>> = {}): FrontierPoint[] {
  return GRID_SPEC.map((spec, index) => gridPoint(spec, overrides[index]))
}

function assessAll(points: FrontierPoint[], names = GRID_NAMES, kinds = GRID_KINDS) {
  return points.map((point) => assessFrontierPoint(point, names, kinds))
}

describe("frontierConstraintKinds", () => {
  it("reads each constraint's kind from the solve's effective bounds", () => {
    const solved = makeOnlineSolveResult({
      constraints: { volume: 5.2, margin: 390 },
      effective_bounds: { volume: { kind: "min", bound: 5 }, margin: { kind: "max", bound: 400 } },
      lambdas: { volume: 0.5, margin: 0.01 },
    })
    expect(frontierConstraintKinds(["volume", "margin"], solved)).toEqual({ volume: "min", margin: "max" })
  })

  it("throws when the solve has no bound for a frontier constraint", () => {
    expect(() => frontierConstraintKinds(["loss_ratio", "volume"], makeOnlineSolveResult()))
      .toThrow(/volume/)
  })
})

describe("assessFrontierPoint", () => {
  it("calls a converged point meeting every bound feasible", () => {
    const [point] = grid()
    expect(assessFrontierPoint(point, GRID_NAMES, GRID_KINDS)).toMatchObject({
      status: "feasible",
      converged: true,
      breached: [],
    })
  })

  it("marks a non-converged point as not converged whatever its bounds", () => {
    const [point] = grid({ 0: { converged: false } })
    expect(assessFrontierPoint(point, GRID_NAMES, GRID_KINDS)).toMatchObject({
      status: "not_converged",
      converged: false,
      breached: [],
    })
  })

  it("marks a converged point with a breached swept bound as breached", () => {
    // The ratebook case: converged means the factors stopped moving, not that
    // the bounds hold (volume 4.968 against a minimum of 5).
    const [point] = grid({ 0: { totals: { volume: 4.968, margin: 390 } } })
    const assessment = assessFrontierPoint(point, GRID_NAMES, GRID_KINDS)
    expect(assessment.status).toBe("breached")
    expect(assessment.breached).toEqual(["volume"])
    expect(assessment.attainment.find((row) => row.name === "volume")).toMatchObject({
      kind: "min",
      bound: 5,
      achieved: 4.968,
      status: "breached",
    })
  })

  it("marks a converged point breached when only an unswept constraint is breached", () => {
    const point = makeOnlineFrontierPoint(0, {
      thresholds: { loss_ratio: 0.6, volume: 0.9 },
      bounds: { loss_ratio: 0.6, volume: 0.9 },
      totals: { loss_ratio: 0.55, volume: 0.85 },
      lambdas: { loss_ratio: 0.001, volume: 0 },
    })
    const assessment = assessFrontierPoint(point, ["loss_ratio", "volume"], { loss_ratio: "max", volume: "min" })
    expect(assessment.status).toBe("breached")
    expect(assessment.breached).toEqual(["volume"])
  })

  it("judges a pct constraint against its absolute bound, never the threshold fraction", () => {
    // min_pct at 1.05 of a 1,000 baseline: the bound is 1,050. Against the
    // fraction, 1,000 would pass.
    const point = makeOnlineFrontierPoint(0, {
      thresholds: { volume: 1.05 },
      bounds: { volume: 1050 },
      totals: { volume: 1000 },
      lambdas: { volume: 0.2 },
    })
    expect(assessFrontierPoint(point, ["volume"], { volume: "min" }).status).toBe("breached")
  })

  it("throws when a point has no bound or total for a constraint", () => {
    const point = makeOnlineFrontierPoint(0)
    expect(() => assessFrontierPoint(point, ["loss_ratio", "volume"], { loss_ratio: "max", volume: "min" }))
      .toThrow(/volume/)
  })

  it("throws when a constraint has no kind", () => {
    const point = makeOnlineFrontierPoint(0)
    expect(() => assessFrontierPoint(point, ["loss_ratio"], {})).toThrow(/loss_ratio/)
  })
})

describe("sliceFrontier", () => {
  it("groups a 2×3 grid by the other constraint's threshold, keeping global indices in bound order", () => {
    const slicing = sliceFrontier(grid(), GRID_NAMES, GRID_NAMES, "volume")
    expect(slicing.heldNames).toEqual(["margin"])
    expect(slicing.slices.map((slice) => slice.held)).toEqual([{ margin: 400 }, { margin: 450 }])
    expect(slicing.slices.map((slice) => slice.indices)).toEqual([[0, 2, 4], [1, 3, 5]])
    expect(slicing.sliceOfPoint).toEqual([0, 1, 0, 1, 0, 1])
  })

  it("slices the same grid the other way when margin is on the x axis", () => {
    const slicing = sliceFrontier(grid(), GRID_NAMES, GRID_NAMES, "margin")
    expect(slicing.heldNames).toEqual(["volume"])
    expect(slicing.slices.map((slice) => slice.indices)).toEqual([[0, 1], [2, 3], [4, 5]])
  })

  it("orders a slice by the x bound, not by global index", () => {
    const points = grid()
    // Reverse the volume sweep: global order no longer follows the bound.
    const reversed = [points[4], points[5], points[2], points[3], points[0], points[1]]
    const slicing = sliceFrontier(reversed, GRID_NAMES, GRID_NAMES, "volume")
    expect(slicing.slices[0].indices).toEqual([4, 2, 0])
  })

  it("keeps a single-constraint frontier as one slice of every point in global order", () => {
    const frontier = makeOnlineFrontier(5)
    const slicing = sliceFrontier(frontier.points, ["loss_ratio"], ["loss_ratio"], "loss_ratio")
    expect(slicing.heldNames).toEqual([])
    expect(slicing.slices).toHaveLength(1)
    expect(slicing.slices[0].indices).toEqual([0, 1, 2, 3, 4])
    expect(slicing.sliceOfPoint).toEqual([0, 0, 0, 0, 0])
  })

  it("groups by an unswept constraint without offering it as a held axis", () => {
    const points = [0, 1, 2].map((i) => makeOnlineFrontierPoint(i, {
      thresholds: { loss_ratio: 0.58 + i * 0.01, volume: 0.9 },
      bounds: { loss_ratio: 0.58 + i * 0.01, volume: 0.9 },
      totals: { loss_ratio: 0.55 + i * 0.01, volume: 0.95 },
      lambdas: { loss_ratio: 0.001, volume: 0 },
    }))
    const slicing = sliceFrontier(points, ["loss_ratio", "volume"], ["loss_ratio"], "loss_ratio")
    expect(slicing.heldNames).toEqual([])
    expect(slicing.slices.map((slice) => slice.indices)).toEqual([[0, 1, 2]])
  })

  it("throws for an x constraint the frontier did not sweep", () => {
    expect(() => sliceFrontier(grid(), GRID_NAMES, ["volume"], "margin")).toThrow(/margin/)
  })
})

describe("sliceNeighbours", () => {
  it("steps within the point's slice in bound order", () => {
    const slicing = sliceFrontier(grid(), GRID_NAMES, GRID_NAMES, "volume")
    expect(sliceNeighbours(slicing, 2)).toEqual({ previous: 0, next: 4 })
    expect(sliceNeighbours(slicing, 4)).toEqual({ previous: 2, next: null })
    expect(sliceNeighbours(slicing, 1)).toEqual({ previous: null, next: 3 })
  })
})

describe("displayedSlicePosition", () => {
  const slicing = sliceFrontier(grid(), GRID_NAMES, GRID_NAMES, "volume")
  const choice = (key: string, selection: number | null) => ({ xName: "volume", generation: 0, key, selection })

  it("shows point 1's slice with no selection and no choice", () => {
    expect(displayedSlicePosition(slicing, null, null, 0)).toBe(0)
  })

  it("shows the selected point's slice", () => {
    expect(displayedSlicePosition(slicing, 3, null, 0)).toBe(1)
  })

  it("keeps the user's slice while the selection is the one it was chosen under", () => {
    expect(displayedSlicePosition(slicing, 3, choice(slicing.slices[0].key, 3), 0)).toBe(0)
  })

  it("switches to the selected point's slice once the selection changes", () => {
    expect(displayedSlicePosition(slicing, 3, choice(slicing.slices[0].key, 2), 0)).toBe(1)
  })

  it("ignores a choice made under another x constraint or frontier generation", () => {
    const key = slicing.slices[1].key
    expect(displayedSlicePosition(slicing, null, { ...choice(key, null), xName: "margin" }, 0)).toBe(0)
    expect(displayedSlicePosition(slicing, null, choice(key, null), 1)).toBe(0)
  })

  it("throws for a chosen slice this frontier does not have", () => {
    expect(() => displayedSlicePosition(slicing, null, choice("[\"nope\"]", null), 0)).toThrow(/slice/)
  })
})

describe("asSolvedOnSlice", () => {
  const points = grid()
  const slicing = sliceFrontier(points, GRID_NAMES, GRID_NAMES, "volume")

  it("places the solve on the slice whose held bounds it was solved at", () => {
    expect(asSolvedOnSlice(points, slicing, slicing.slices[0], { margin: 400 })).toBe(true)
    expect(asSolvedOnSlice(points, slicing, slicing.slices[1], { margin: 400 })).toBe(false)
  })

  it("is always on the slice of a single-constraint frontier", () => {
    const frontier = makeOnlineFrontier(3)
    const single = sliceFrontier(frontier.points, ["loss_ratio"], ["loss_ratio"], "loss_ratio")
    expect(asSolvedOnSlice(frontier.points, single, single.slices[0], {})).toBe(true)
  })
})

describe("discreteTradeOff", () => {
  function tradeOff(points: FrontierPoint[], xName: string, index: number) {
    const slicing = sliceFrontier(points, GRID_NAMES, GRID_NAMES, xName)
    return discreteTradeOff({
      points,
      slicing,
      assessments: assessAll(points),
      kinds: GRID_KINDS,
      index,
    })
  }

  it("relaxes a min constraint by lowering its bound (hand-calculated)", () => {
    // Point 3 (volume ≥ 5.5, objective 120) relaxes to point 1 (volume ≥ 5,
    // objective 130): (130 − 120) / (5.5 − 5) = +20 per unit of volume.
    expect(tradeOff(grid(), "volume", 2)).toEqual({ kind: "value", value: 20, nextIndex: 0 })
    // Point 5 (≥ 6, 105) to point 3 (≥ 5.5, 120): 15 / 0.5 = +30.
    expect(tradeOff(grid(), "volume", 4)).toEqual({ kind: "value", value: 30, nextIndex: 2 })
  })

  it("relaxes a max constraint by raising its bound (hand-calculated)", () => {
    // Point 1 (margin ≤ 400, objective 130) relaxes to point 2 (margin ≤ 450,
    // objective 140): (140 − 130) / (450 − 400) = +0.2 per unit of margin.
    const result = tradeOff(grid(), "margin", 0)
    expect(result.kind).toBe("value")
    if (result.kind !== "value") return
    expect(result.value).toBeCloseTo(0.2, 12)
    expect(result.nextIndex).toBe(1)
  })

  it("has no value at the slice's least-constrained end", () => {
    expect(tradeOff(grid(), "volume", 0)).toMatchObject({ kind: "unavailable" })
    expect(tradeOff(grid(), "margin", 1)).toMatchObject({ kind: "unavailable" })
  })

  it("has no value between identical bounds", () => {
    const points = grid({ 0: { bounds: { volume: 5.5, margin: 400 }, totals: { volume: 5.6, margin: 390 } } })
    expect(tradeOff(points, "volume", 2)).toMatchObject({
      kind: "unavailable",
      reason: expect.stringMatching(/same volume bound/),
    })
  })

  it("has no value when either neighbour did not converge", () => {
    expect(tradeOff(grid({ 0: { converged: false } }), "volume", 2)).toMatchObject({
      kind: "unavailable",
      reason: expect.stringMatching(/next point .* not feasible/),
    })
    expect(tradeOff(grid({ 2: { converged: false } }), "volume", 2)).toMatchObject({
      kind: "unavailable",
      reason: expect.stringMatching(/This point is not feasible/),
    })
  })

  it("has no value when a converged neighbour breaches a swept bound", () => {
    const points = grid({ 0: { totals: { volume: 4.968, margin: 390 } } })
    expect(tradeOff(points, "volume", 2)).toMatchObject({ kind: "unavailable" })
  })

  it("has no value when a converged neighbour breaches only an unswept bound", () => {
    // loss_ratio (max) is swept; volume (min, 0.9) is not. Point 2 converged
    // and meets its loss_ratio bound but its volume is 0.85.
    const names = ["loss_ratio", "volume"]
    const kinds: ConstraintKinds = { loss_ratio: "max", volume: "min" }
    const points = [0, 1, 2].map((i) => makeOnlineFrontierPoint(i, {
      thresholds: { loss_ratio: 0.6 + i * 0.1, volume: 0.9 },
      bounds: { loss_ratio: 0.6 + i * 0.1, volume: 0.9 },
      totals: { loss_ratio: 0.55 + i * 0.1, volume: i === 1 ? 0.85 : 0.95 },
      lambdas: { loss_ratio: 0.001, volume: 0 },
    }))
    const assessments = points.map((point) => assessFrontierPoint(point, names, kinds))
    expect(assessments.map((a) => a.status)).toEqual(["feasible", "breached", "feasible"])
    const slicing = sliceFrontier(points, names, ["loss_ratio"], "loss_ratio")
    expect(discreteTradeOff({ points, slicing, assessments, kinds, index: 0 })).toMatchObject({
      kind: "unavailable",
      reason: expect.stringMatching(/next point .* not feasible/),
    })
    expect(discreteTradeOff({ points, slicing, assessments, kinds, index: 1 })).toMatchObject({
      kind: "unavailable",
      reason: expect.stringMatching(/This point is not feasible/),
    })
  })
})
