/**
 * Step round-trip lane, second half (`scripts/run_step_round_trip.py` runs it).
 *
 * Reads, from `HAUTE_STEP_ROUND_TRIP_DIR`, the backend's random read-backs
 * (`backend_read_backs.json`) and each form edit saved as code and read back
 * (`round_trips.json`), and writes what the editor makes of them to
 * `editor_report.json`: a read-back it refuses, cannot summarise, derive
 * columns from or open, and a round trip that comes back as another kind of
 * step or reads differently on its card. The lane script fails on any of them.
 */
import { expect, it } from "vitest"
import { createElement } from "react"
import { cleanup, render } from "@testing-library/react"
import { readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"

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
    } else if (trip.after.kind !== trip.before.kind) {
      findings.push({ check: "round trip", detail: `a ${trip.before.kind} step comes back as a ${trip.after.kind} step`, example: trip })
    } else if (summarizeStep(canonicalStep(trip.before)) !== summarizeStep(canonicalStep(trip.after))) {
      findings.push({ check: "round trip", detail: `a ${trip.before.kind} step reads differently on its card`, example: trip })
    }
  }
  expect(readBacks.length + trips.length).toBeGreaterThan(0)
  writeFileSync(join(dir, "editor_report.json"), JSON.stringify({ checked: { readBacks: readBacks.length, trips: trips.length }, findings }))
})
