import { describe, expect, it } from "vitest"
import { createElement } from "react"
import { cleanup, render } from "@testing-library/react"

import { canonicalStep, stepProblem } from "../catalogue"
import { columnsAtEachStep, columnsReadBy, type ColumnSource } from "../derivedColumns"
import { StepForm, type StepFormContext } from "../forms"
import { summarizeStep } from "../summary"
import type { Step } from "../types"
import stepShapes from "./fixtures/stepShapes.json"

/**
 * Every step the backend hands the editor when it reads a node back from its
 * code (`scripts/regen_step_shapes_fixture.py` renders each case and reads it
 * back as `_config_builder` does). The editor describes a step's shape by hand,
 * so this is where a shape it refuses, or reads a missing setting of, shows.
 */
const cases = (stepShapes as { cases: Array<{ id: string; steps: Step[] }> }).cases

const source: ColumnSource = {
  inputs: { quotes: [{ name: "premium", dtype: "Float64" }, { name: "region", dtype: "String" }] },
  frame: [],
  freeCode: new Map(),
}

const ctx: StepFormContext = { columns: ["premium", "region"], variables: ["rate"], inputNames: ["quotes", "rates", "policies"], firstFieldId: "first" }

describe("the editor takes every step the backend reads back from code", () => {
  it("has cases to check", () => {
    expect(cases.length).toBeGreaterThan(200)
  })

  it.each(cases.map((c) => [c.id, c.steps] as const))("%s", (_id, steps) => {
    for (const step of steps) {
      expect(stepProblem(step), JSON.stringify(step)).toBeNull()
      const canonical = canonicalStep(step)
      expect(stepProblem(canonical), JSON.stringify(canonical)).toBeNull()
      expect(summarizeStep(canonical)).not.toMatch(/undefined|\[object Object\]/)
      expect(columnsReadBy(canonical).every((name) => typeof name === "string")).toBe(true)
      render(createElement(StepForm, { step, onChange: () => {}, ctx }))
      cleanup()
    }
    expect(columnsAtEachStep(source, steps)).toHaveLength(steps.length + 1)
  })
})
