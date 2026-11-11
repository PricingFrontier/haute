/**
 * Step round-trip lane, first half (`scripts/run_step_round_trip.py` runs it).
 *
 * Makes every single edit each step form offers, and one more below each
 * button, from every step of the step shapes fixture, and writes each step the
 * form emits to `form_edits.json` in `HAUTE_STEP_ROUND_TRIP_DIR`: the steps a
 * user can make in the editor, which the lane then saves as code and reads back.
 * It is not part of the ordinary suite (`vitest.config.ts` leaves out
 * `*.lane.test.*`); `npm run test:lanes` runs it.
 */
import { expect, it } from "vitest"
import { useState } from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { writeFileSync } from "node:fs"
import { join } from "node:path"

import { variablesBefore } from "../catalogue"
import { StepForm, type StepFormContext } from "../forms"
import type { Step } from "../types"
import stepShapes from "./fixtures/stepShapes.json"

type Edit = { seed: string; index: number; action: string; step: Step }
type Action = { kind: string; run: (control: HTMLElement) => void }

/** The form's context as the editor gives it: the variables the steps before this one define. */
const contextFor = (variables: string[]): StepFormContext => ({ columns: ["premium", "region"], variables, inputNames: ["quotes", "rates", "policies"], firstFieldId: "first" })

function Stateful({ initial, spy, ctx }: { initial: Step; spy: (next: Step) => void; ctx: StepFormContext }) {
  const [step, setStep] = useState(initial)
  return <StepForm step={step} onChange={(next) => { spy(next); setStep(next) }} ctx={ctx} />
}

const controlsOf = (root: HTMLElement): HTMLElement[] => Array.from(root.querySelectorAll<HTMLElement>("select, input, textarea, button"))

/** What a user can do to one control: pick each other option, toggle it, press it, or type into it. */
function actionsOf(control: HTMLElement): Action[] {
  if (control instanceof HTMLSelectElement) {
    return Array.from(control.options)
      .filter((option) => option.value !== control.value)
      .map((option) => ({ kind: "select", run: (el) => fireEvent.change(el, { target: { value: option.value } }) }))
  }
  if (control instanceof HTMLInputElement && control.type === "checkbox") return [{ kind: "toggle", run: (el) => fireEvent.click(el) }]
  if (control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement) {
    const values = control.type === "number" ? ["3", "0.5"] : ["premium", "2", "region * 2"]
    return values.flatMap((value) => [
      { kind: "type", run: (el: HTMLElement) => { fireEvent.change(el, { target: { value } }); fireEvent.blur(el) } },
      { kind: "type", run: (el: HTMLElement) => { fireEvent.change(el, { target: { value } }); fireEvent.keyDown(el, { key: "Enter" }) } },
    ])
  }
  return [{ kind: "button", run: (el) => fireEvent.click(el) }]
}

/** Press the buttons on *path* in turn, as the exploration did before this level. */
function replay(root: HTMLElement, path: number[]) {
  for (const index of path) {
    const control = controlsOf(root)[index]
    if (control) act(() => fireEvent.click(control))
  }
}

it("makes every edit each step form offers", { timeout: 7_200_000 }, () => {
  const dir = process.env.HAUTE_STEP_ROUND_TRIP_DIR
  if (!dir) throw new Error("Set HAUTE_STEP_ROUND_TRIP_DIR; scripts/run_step_round_trip.py runs this lane.")
  const edits = new Map<string, Edit>()
  const cases = (stepShapes as { cases: Array<{ id: string; steps: Step[] }> }).cases

  const explore = (seed: string, index: number, start: Step, ctx: StepFormContext, path: number[], lead: string) => {
    const mounted = render(<Stateful initial={start} spy={() => {}} ctx={ctx} />)
    replay(mounted.container, path)
    const plan = controlsOf(mounted.container).map((control) => ({ actions: actionsOf(control).length, button: control instanceof HTMLButtonElement }))
    cleanup()
    plan.forEach(({ actions, button }, control) => {
      for (let action = 0; action < actions; action += 1) {
        const emitted: Step[] = []
        const { container } = render(<Stateful initial={start} spy={(next) => emitted.push(next)} ctx={ctx} />)
        replay(container, path)
        const target = controlsOf(container)[control]
        const chosen = target ? actionsOf(target)[action] : undefined
        if (target && chosen) act(() => chosen.run(target))
        cleanup()
        for (const step of emitted) {
          const key = JSON.stringify(step)
          if (!edits.has(key)) edits.set(key, { seed, index, action: `${lead}${chosen?.kind ?? "none"}`, step })
        }
        // A button opens more controls: one more edit below it.
        if (button && path.length === 0) explore(seed, index, start, ctx, [control], "button>")
      }
    })
  }

  for (const shape of cases) {
    shape.steps.forEach((step, index) => {
      if (step.kind !== "source" && step.kind !== "free_code") explore(shape.id, index, step, contextFor(variablesBefore(shape.steps, index)), [], "")
    })
  }
  expect(edits.size).toBeGreaterThan(1000)
  writeFileSync(join(dir, "form_edits.json"), JSON.stringify([...edits.values()]))
})
