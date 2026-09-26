/**
 * The efficient frontier's slice and feasibility model.
 *
 * A multi-constraint sweep is an n^k grid. Plotting every point against one
 * constraint would draw a projected cloud, so the chart shows one **slice** at
 * a time: the points whose other constraints were held at the same thresholds,
 * in ascending order of the x constraint's bound. Thresholds come from
 * linspace, so exact equality groups them safely.
 *
 * **Global identity.** Every point keeps its index in `frontier.points`
 * through slicing, overlap grouping, stepping and publishing; a slice lists
 * global indices and nothing here hands out a slice-local one.
 *
 * **Feasibility** is haute's own judgement in both modes: a point is feasible
 * when it converged and every constraint's total meets its absolute bound
 * (`bounds`, never the pct `thresholds` fraction). Online `converged` already
 * includes the library's tolerance check, but ratebook `converged` only says
 * the factor values stopped moving, so a converged ratebook point can breach.
 */

import type { FrontierPoint, OptimiserSolveResult } from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import { constraintAttainment, type ConstraintAttainment, type ConstraintKind } from "./constraintAttainment"

export type ConstraintKinds = Record<string, ConstraintKind>

export type FrontierPointStatus = "feasible" | "not_converged" | "breached"

export interface FrontierPointAssessment {
  /** `not_converged` wins over `breached`: a point that did not converge is never joined either way. */
  status: FrontierPointStatus
  converged: boolean
  /** Every constraint's attainment against the point's absolute bound, in constraint order. */
  attainment: (ConstraintAttainment & { name: string })[]
  /** The constraints whose bound the point breaches, in constraint order. */
  breached: string[]
}

export interface FrontierSlice {
  /** Identifies the slice among this frontier's slices for one x constraint. */
  key: string
  /** The thresholds the other swept constraints are held at. */
  held: Record<string, number>
  /** Global point indices in ascending x bound (ties by index). */
  indices: number[]
}

export interface FrontierSlicing {
  xName: string
  /** The other swept constraints, which name the slices. */
  heldNames: string[]
  /** Ordered by the held thresholds, in constraint order. */
  slices: FrontierSlice[]
  /** Each global point index's position in `slices`. */
  sliceOfPoint: number[]
}

/** The user's slice choice, which holds only under the state it was made in. */
export interface FrontierSliceChoice {
  xName: string
  generation: number | null
  key: string
  /** The selected point when the choice was made. */
  selection: number | null
}

export type DiscreteTradeOff =
  | { kind: "value"; value: number; nextIndex: number }
  | { kind: "unavailable"; reason: string }

function requireNumber(map: Record<string, number>, name: string, field: string, index: number | null): number {
  const value = map[name]
  if (typeof value !== "number" || !Number.isFinite(value)) {
    const where = index == null ? "A frontier point" : `Frontier point ${index + 1}`
    throw new Error(`${where} has no finite ${field} for constraint ${name}`)
  }
  return value
}

/** Each frontier constraint's kind, from the bounds the solve reports. */
export function frontierConstraintKinds(constraintNames: string[], solved: OptimiserSolveResult): ConstraintKinds {
  const bounds = effectiveConstraintBounds(solved)
  const missing = constraintNames.filter((name) => !(name in bounds))
  if (missing.length > 0) {
    throw new Error(`The solve reports no bound for frontier constraint(s) ${missing.join(", ")}`)
  }
  return Object.fromEntries(constraintNames.map((name) => [name, bounds[name].kind]))
}

export function assessFrontierPoint(
  point: FrontierPoint,
  constraintNames: string[],
  kinds: ConstraintKinds,
  index: number | null = null,
): FrontierPointAssessment {
  const attainment = constraintNames.map((name) => {
    const kind = kinds[name]
    if (kind === undefined) throw new Error(`No constraint kind for frontier constraint ${name}`)
    return {
      name,
      ...constraintAttainment({
        kind,
        bound: requireNumber(point.bounds, name, "bound", index),
        achieved: requireNumber(point.totals, name, "total", index),
      }),
    }
  })
  const breached = attainment.filter((row) => row.status === "breached").map((row) => row.name)
  const status: FrontierPointStatus = !point.converged
    ? "not_converged"
    : breached.length > 0 ? "breached" : "feasible"
  return { status, converged: point.converged, attainment, breached }
}

/**
 * Group the points into slices along `xName`: points whose every other
 * constraint has the same threshold share a slice. `heldNames` are the other
 * swept constraints; an unswept one is held everywhere and names no slice.
 */
export function sliceFrontier(
  points: FrontierPoint[],
  constraintNames: string[],
  sweptAxes: string[],
  xName: string,
): FrontierSlicing {
  if (!sweptAxes.includes(xName)) {
    throw new Error(`The frontier did not sweep ${xName}; its swept constraints are ${sweptAxes.join(", ")}`)
  }
  const otherNames = constraintNames.filter((name) => name !== xName)
  const heldNames = sweptAxes.filter((name) => name !== xName)
  const byKey = new Map<string, { thresholds: number[]; indices: number[] }>()
  points.forEach((point, index) => {
    const thresholds = otherNames.map((name) => requireNumber(point.thresholds, name, "threshold", index))
    requireNumber(point.bounds, xName, "bound", index)
    const key = JSON.stringify(thresholds)
    const group = byKey.get(key)
    if (group) group.indices.push(index)
    else byKey.set(key, { thresholds, indices: [index] })
  })
  const compareThresholds = (a: number[], b: number[]) => {
    for (let i = 0; i < a.length; i += 1) {
      if (a[i] !== b[i]) return a[i] - b[i]
    }
    return 0
  }
  if (heldNames.length === 0 && byKey.size > 1) {
    throw new Error(`The frontier's unswept constraints vary in threshold across its points (x: ${xName})`)
  }
  const groups = Array.from(byKey.entries()).sort(([, a], [, b]) => compareThresholds(a.thresholds, b.thresholds))
  const sliceOfPoint = new Array<number>(points.length)
  const slices = groups.map(([key, { thresholds, indices }], position) => {
    const ordered = [...indices].sort((a, b) => points[a].bounds[xName] - points[b].bounds[xName] || a - b)
    for (const index of ordered) sliceOfPoint[index] = position
    return {
      key,
      held: Object.fromEntries(heldNames.map((name) => [name, thresholds[otherNames.indexOf(name)]])),
      indices: ordered,
    }
  })
  return { xName, heldNames, slices, sliceOfPoint }
}

function slicePosition(slicing: FrontierSlicing, index: number): { slice: FrontierSlice; at: number } {
  const position = slicing.sliceOfPoint[index]
  if (position === undefined) throw new Error(`Frontier point ${index + 1} is not in the frontier`)
  const slice = slicing.slices[position]
  return { slice, at: slice.indices.indexOf(index) }
}

/** The point's neighbours in bound order within its own slice. */
export function sliceNeighbours(slicing: FrontierSlicing, index: number): { previous: number | null; next: number | null } {
  const { slice, at } = slicePosition(slicing, index)
  return {
    previous: at > 0 ? slice.indices[at - 1] : null,
    next: at < slice.indices.length - 1 ? slice.indices[at + 1] : null,
  }
}

/**
 * The slice on display: the user's choice while it still holds (same x
 * constraint, frontier generation and selection), else the selected point's
 * slice, else point 1's.
 */
export function displayedSlicePosition(
  slicing: FrontierSlicing,
  selectedIdx: number | null,
  choice: FrontierSliceChoice | null,
  generation: number | null,
): number {
  if (
    choice
    && choice.xName === slicing.xName
    && choice.generation === generation
    && choice.selection === selectedIdx
  ) {
    const position = slicing.slices.findIndex((slice) => slice.key === choice.key)
    if (position < 0) throw new Error(`The chosen frontier slice ${choice.key} is not in this frontier`)
    return position
  }
  return slicing.sliceOfPoint[selectedIdx ?? 0]
}

/**
 * Whether the solve lies on the slice: it was solved at the slice's bound for
 * every other swept constraint. Exact equality, as the conservative claim: a
 * solve at any other bound is not a point of this slice.
 */
export function asSolvedOnSlice(
  points: FrontierPoint[],
  slicing: FrontierSlicing,
  slice: FrontierSlice,
  solvedBounds: Record<string, number>,
): boolean {
  const representative = points[slice.indices[0]]
  return slicing.heldNames.every((name) => {
    const solved = solvedBounds[name]
    if (typeof solved !== "number") throw new Error(`The solve reports no bound for ${name}`)
    return solved === representative.bounds[name]
  })
}

/**
 * The objective change per unit of the x bound relaxed, to the next point in
 * the relaxing direction of the same slice: raising a max bound, lowering a
 * min one. The denominator is the bound, which the reviewer controls, not the
 * achieved total. It exists only between two feasible points with different
 * bounds; it is a discrete step in which other totals may also move, never a
 * check of λ.
 */
export function discreteTradeOff({
  points,
  slicing,
  assessments,
  kinds,
  index,
}: {
  points: FrontierPoint[]
  slicing: FrontierSlicing
  assessments: FrontierPointAssessment[]
  kinds: ConstraintKinds
  index: number
}): DiscreteTradeOff {
  const xName = slicing.xName
  const kind = kinds[xName]
  if (kind === undefined) throw new Error(`No constraint kind for frontier constraint ${xName}`)
  if (assessments[index].status !== "feasible") {
    return { kind: "unavailable", reason: "This point is not feasible." }
  }
  const { previous, next } = sliceNeighbours(slicing, index)
  const nextIndex = kind === "max" ? next : previous
  if (nextIndex === null) {
    return { kind: "unavailable", reason: `No point in this slice relaxes the ${xName} bound further.` }
  }
  if (assessments[nextIndex].status !== "feasible") {
    return { kind: "unavailable", reason: `The next point in this slice (point ${nextIndex + 1}) is not feasible.` }
  }
  const bound = points[index].bounds[xName]
  const nextBound = points[nextIndex].bounds[xName]
  const relaxation = kind === "max" ? nextBound - bound : bound - nextBound
  if (relaxation === 0) {
    return { kind: "unavailable", reason: `The next point in this slice has the same ${xName} bound.` }
  }
  return {
    kind: "value",
    value: (points[nextIndex].total_objective - points[index].total_objective) / relaxation,
    nextIndex,
  }
}
