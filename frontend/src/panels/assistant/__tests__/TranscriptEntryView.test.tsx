/**
 * One transcript entry (src/panels/assistant/TranscriptEntryView.tsx), and in
 * particular the card each typed turn outcome renders as.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — "Every completed turn
 * ends with its outcome".
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import TranscriptEntryView from "../TranscriptEntryView"
import { CHOOSE_FOR_ME_REPLY } from "../../../stores/useAssistantStore"

afterEach(cleanup)

describe("turn outcomes", () => {
  it("closes an applied turn with the ordinary completed marker", () => {
    render(<TranscriptEntryView entry={{ kind: "outcome", outcome: { kind: "applied", detail: null } }} />)
    const marker = screen.getByTestId("assistant-entry-marker")
    expect(marker).toHaveAttribute("data-outcome", "applied")
    expect(marker).toHaveTextContent("Changes applied")
  })

  it("closes an answered turn with the ordinary completed marker", () => {
    render(<TranscriptEntryView entry={{ kind: "outcome", outcome: { kind: "answered", detail: null } }} />)
    expect(screen.getByTestId("assistant-entry-marker")).toHaveTextContent("Turn completed")
  })

  it("renders a question card whose one-click reply lets the assistant choose", () => {
    const onSend = vi.fn()
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean **active**?" } }}
        reply={{ onSend, disabledReason: null }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-needs-input")
    expect(card).toHaveTextContent("Assistant needs your input")
    expect(card).toHaveTextContent("Which values mean active?")
    expect(card).not.toHaveTextContent("NEEDS_INPUT:")
    const button = screen.getByTestId("assistant-choose-for-me")
    expect(button).toHaveTextContent(CHOOSE_FOR_ME_REPLY)
    fireEvent.click(button)
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it("disables the reply with the send gate's reason", () => {
    const onSend = vi.fn()
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which column?" } }}
        reply={{ onSend, disabledReason: "Save or discard the current canvas changes before using Assistant." }}
      />,
    )

    const button = screen.getByTestId("assistant-choose-for-me")
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute(
      "title",
      "Save or discard the current canvas changes before using Assistant.",
    )
  })

  it("offers no reply on a question the panel did not make answerable", () => {
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which column?" } }}
      />,
    )
    expect(screen.queryByTestId("assistant-choose-for-me")).not.toBeInTheDocument()
  })

  it("renders a blocked card that says nothing was saved", () => {
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "blocked", detail: "The claims file is missing." } }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-blocked")
    expect(card).toHaveTextContent("Assistant is blocked")
    expect(card).toHaveTextContent("The claims file is missing.")
    expect(card).toHaveTextContent("Nothing was saved.")
  })

  it("says a committed but unverified save was saved, never that nothing changed", () => {
    render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: {
            kind: "committed_unverified",
            detail: "The plan was committed, but structural verification failed.",
          },
        }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-committed-unverified")
    expect(card).toHaveTextContent("Your changes were saved")
    expect(card).toHaveTextContent("The plan was committed, but structural verification failed.")
    expect(card.textContent).not.toMatch(/nothing (was saved|changed)/i)
  })
})
