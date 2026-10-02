/**
 * The canvas pill shown while an assistant turn runs
 * (src/components/AssistantWorkingPill.tsx).
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — "The canvas is read-only
 * while a turn runs".
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import AssistantWorkingPill from "../AssistantWorkingPill"
import useUIStore from "../../stores/useUIStore"

afterEach(() => {
  cleanup()
  useUIStore.setState({ assistantTurn: null })
})

describe("AssistantWorkingPill", () => {
  it("renders nothing while no turn runs", () => {
    render(<AssistantWorkingPill />)
    expect(screen.queryByTestId("assistant-working-pill")).not.toBeInTheDocument()
  })

  it("says the assistant is working and stops the running turn", () => {
    const stop = vi.fn()
    useUIStore.setState({ assistantTurn: { stop } })
    render(<AssistantWorkingPill />)

    expect(screen.getByTestId("assistant-working-pill")).toHaveTextContent("Assistant is working")
    fireEvent.click(screen.getByTestId("assistant-working-stop"))
    expect(stop).toHaveBeenCalledTimes(1)
  })
})
