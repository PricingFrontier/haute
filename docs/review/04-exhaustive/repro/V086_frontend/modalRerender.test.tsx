/**
 * V086 reproduction (frontend, executed via vitest/jsdom).
 *
 * Claim: ModalShell's mount/focus effect depends on [onClose, extraCloseKeys].
 * When the PARENT re-renders (in the real app App re-renders on every node
 * hover because it subscribes to hoveredNodeId) and passes a FRESH onClose
 * arrow and/or a FRESH extraCloseKeys array literal each render, the effect's
 * cleanup+setup re-fire. The cleanup calls previousFocusRef.current.focus()
 * (pulls focus back to the pre-modal element) and the setup overwrites
 * previousFocusRef.current = document.activeElement, then focuses the modal
 * container — yanking focus OUT of whatever inner control the user had focused.
 *
 * This test imports the REAL ModalShell from src and runs it unmodified. It
 * asserts the SPECIFIC wrong behaviour (focus moved off the inner input onto
 * the modal container), not merely that "something happened".
 *
 * VERIFIED RESULT (vitest v4, jsdom): the bug test PASSES (focus is yanked off
 * the inner input onto the dialog container after one parent re-render) and a
 * control with STABLE deps keeps focus on the input — isolating the cause to
 * the unstable [onClose, extraCloseKeys] effect deps.
 *
 * HOW IT WAS RUN (node-resolution requires the frontend's node_modules + the
 * @vitejs/plugin-react JSX runtime, so it was executed from a throwaway copy at
 * the frontend root with a temp config, both since deleted):
 *   1. copy this file to frontend/repro-V086.test.tsx and change the ModalShell
 *      import to "./src/components/ModalShell"
 *   2. config: defineConfig({ plugins: [react()],
 *        test: { environment: "jsdom", include: ["repro-V086.test.tsx"] } })
 *   3. cd frontend && npx vitest run --config <that-config>
 */
import "@testing-library/jest-dom/vitest"
import { describe, it, expect, afterEach } from "vitest"
import { useState, useEffect } from "react"
import { render, screen, cleanup, act } from "@testing-library/react"
import ModalShell from "../../../../frontend/src/components/ModalShell"

afterEach(cleanup)

/**
 * Parent that mimics App.tsx: it owns a counter that an external trigger can
 * bump (standing in for hoveredNodeId changing on every node hover). On every
 * render it passes ModalShell a brand-new onClose arrow and a brand-new
 * extraCloseKeys array literal — exactly like App.tsx:733 +
 * KeyboardShortcuts.tsx:21.
 */
let bump: () => void = () => {}

function Harness() {
  const [, setTick] = useState(0)
  useEffect(() => {
    bump = () => setTick((t) => t + 1)
  }, [])
  return (
    <ModalShell
      ariaLabel="Repro dialog"
      onClose={() => {/* fresh arrow every render, like App.tsx */}}
      extraCloseKeys={["?"]} // fresh array literal every render, like KeyboardShortcuts.tsx
    >
      <input data-testid="inner-input" defaultValue="user typing here" />
    </ModalShell>
  )
}

describe("V086 — ModalShell focus effect re-runs on unstable parent re-render", () => {
  it("yanks focus off the inner control back to the modal container on a parent re-render", () => {
    // A background element that held focus BEFORE the modal opened.
    document.body.innerHTML = '<button id="bg">background</button>'
    const bg = document.getElementById("bg") as HTMLButtonElement
    bg.focus()
    expect(document.activeElement).toBe(bg)

    render(<Harness />)

    const dialog = screen.getByRole("dialog") as HTMLDivElement
    const input = screen.getByTestId("inner-input") as HTMLInputElement

    // On mount the effect focuses the dialog container. The user then clicks
    // into / tabs to the inner input and starts interacting with it.
    input.focus()
    expect(document.activeElement).toBe(input)

    // --- Simulate one background node-hover -> one parent re-render. ---
    // New onClose + new extraCloseKeys identity => effect deps changed =>
    // cleanup (restores previousFocusRef) then setup (re-focus container).
    act(() => {
      bump()
    })

    // BUG: focus has been stolen away from the inner input. With a correct
    // (stable-deps / mount-only) effect this would still be the input.
    const active = document.activeElement as HTMLElement
    expect(active).not.toBe(input) // focus was yanked off the user's control
    expect(active).toBe(dialog) // ...and dumped onto the modal container

    // Sanity: a single hover is enough; continuous hovering repeats this,
    // making it impossible to keep focus on the inner control.
  })
})
