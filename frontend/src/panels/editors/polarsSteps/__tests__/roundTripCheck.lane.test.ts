/**
 * Step round-trip lane, second half (`scripts/run_step_round_trip.py` runs it).
 *
 * Reads, from `HAUTE_STEP_ROUND_TRIP_DIR`, the backend's random read-backs
 * (`backend_read_backs.json`) and each form edit saved as code and read back
 * (`round_trips.json`), and writes what the editor makes of them to
 * `editor_report.json`: a read-back it refuses, cannot summarise, derive
 * columns from or open, and a round trip that comes back as another kind of
 * step, with any setting other than it was, or reading differently on its card.
 * The lane script fails on any of them.
 */
import { expect, it } from "vitest"
import { createElement } from "react"
import { cleanup, render } from "@testing-library/react"
import { readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"

import { canonicalValue } from "../../../../utils/canonicalJson"
import { canonicalStep, stepProblem } from "../catalogue"
import { columnsAtEachStep, columnsReadBy, type ColumnSource } from "../derivedColumns"
import { StepForm, type StepFormContext } from "../forms"
import { summarizeStep } from "../summary"
import type { Step } from "../types"

type Finding = { check: string; detail: string; example: unknown }

const source: ColumnSource = { inputs: { quotes: [{ name: "premium", dtype: "Float64" }] }, frame: [], freeCode: new Map() }
const ctx: StepFormContext = { columns: ["premium", "region"], variables: ["rate", "label", "flag", "level"], inputNames: ["quotes", "rates", "policies"], firstFieldId: "first" }

/** Why the editor cannot take *step* as it takes any other, or null. */
function editorProblem(step: Step): string | null {
  const problem = stepProblem(step)
  if (problem) return `refused: ${problem}`
  try {
    const canonical = canonicalStep(step)
    const again = stepProblem(canonical)
    if (again) return `refused once canonical: ${again}`
    if (/undefined|\[object Object\]/.test(summarizeStep(canonical))) return "summary shows a missing setting"
    columnsReadBy(canonical)
    render(createElement(StepForm, { step, onChange: () => {}, ctx }))
    return null
  } catch (error) {
    return `throws: ${String(error)}`
  } finally {
    cleanup()
  }
}

/**
 * *step*'s settings as the editor holds them, canonical, its id aside: what a save must keep.
 * Three differences the code has no way to hold are set aside, as they change no result:
 * an expression's `text` (the formula as typed, which the renderer ignores), an operand
 * wrapping an expression that is a plain operand (it renders as that operand), and the
 * match of a group of one condition or none (any and all agree on it).
 */
function settings(step: Step): unknown {
  const { id: _id, ...rest } = canonicalStep(step)
  return normalised(canonicalValue(rest))
}

function normalised(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalised)
  if (value === null || typeof value !== "object") return value
  const { text: _text, ...entry } = value as Record<string, unknown>
  const inner = entry.expr as Record<string, unknown> | undefined
  if (entry.kind === "expr" && inner?.type === "operand") return normalised(inner.operand)
  const out = Object.fromEntries(Object.entries(entry).map(([key, field]) => [key, normalised(field)]))
  if ("match" in out && Array.isArray(out.conditions) && out.conditions.length <= 1) out.match = "all"
  return out
}

/** The first setting, as a path, where *before* and *after* differ, or null when they hold the same. */
function difference(before: unknown, after: unknown, path = "step"): string | null {
  if (before === after) return null
  if (typeof before !== "object" || typeof after !== "object" || before === null || after === null || Array.isArray(before) !== Array.isArray(after)) {
    return path
  }
  const a = before as Record<string, unknown>
  const b = after as Record<string, unknown>
  for (const key of [...new Set([...Object.keys(a), ...Object.keys(b)])]) {
    const found = difference(a[key], b[key], Array.isArray(before) ? `${path}[${key}]` : `${path}.${key}`)
    if (found) return found
  }
  return null
}

it("takes every step the backend reads back, as the step it was", { timeout: 3_600_000 }, () => {
  const dir = process.env.HAUTE_STEP_ROUND_TRIP_DIR
  if (!dir) throw new Error("Set HAUTE_STEP_ROUND_TRIP_DIR; scripts/run_step_round_trip.py runs this lane.")
  const findings: Finding[] = []
  const readBacks = JSON.parse(readFileSync(join(dir, "backend_read_backs.json"), "utf-8")) as Array<{ steps: Step[] }>
  for (const program of readBacks) {
    for (const step of program.steps) {
      const problem = editorProblem(step)
      if (problem) findings.push({ check: "backend read-back", detail: problem, example: step })
    }
    try {
      columnsAtEachStep(source, program.steps)
    } catch (error) {
      findings.push({ check: "backend read-back", detail: `columns after each step throw: ${String(error)}`, example: program.steps })
    }
  }
  const trips = JSON.parse(readFileSync(join(dir, "round_trips.json"), "utf-8")) as Array<{ seed: string; action: string; before: Step; after: Step }>
  for (const trip of trips) {
    const problem = editorProblem(trip.after)
    if (problem) {
      findings.push({ check: "round trip", detail: problem, example: trip })
      continue
    }
    if (trip.after.kind !== trip.before.kind) {
      findings.push({ check: "round trip", detail: `a ${trip.before.kind} step comes back as a ${trip.after.kind} step`, example: trip })
      continue
    }
    const changed = difference(settings(trip.before), settings(trip.after))
    if (changed) {
      findings.push({ check: "round trip", detail: `a ${trip.before.kind} step comes back with ${changed} changed`, example: trip })
    }
    if (summarizeStep(canonicalStep(trip.before)) !== summarizeStep(canonicalStep(trip.after))) {
      findings.push({ check: "round trip", detail: `a ${trip.before.kind} step reads differently on its card`, example: trip })
    }
  }
  expect(readBacks.length + trips.length).toBeGreaterThan(0)
  writeFileSync(join(dir, "editor_report.json"), JSON.stringify({ checked: { readBacks: readBacks.length, trips: trips.length }, findings }))
})
