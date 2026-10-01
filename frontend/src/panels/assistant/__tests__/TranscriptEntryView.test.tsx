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
    render(<TranscriptEntryView entry={{ kind: "outcome", outcome: { kind: "applied", detail: null, changes: ["plan-1"] } }} />)
    const marker = screen.getByTestId("assistant-entry-marker")
    expect(marker).toHaveAttribute("data-outcome", "applied")
    expect(marker).toHaveTextContent("Changes applied")
  })

  it("closes an answered turn with the ordinary completed marker", () => {
    render(<TranscriptEntryView entry={{ kind: "outcome", outcome: { kind: "answered", detail: null, changes: [] } }} />)
    expect(screen.getByTestId("assistant-entry-marker")).toHaveTextContent("Turn completed")
  })

  it("renders a question card whose one-click reply lets the assistant choose", () => {
    const onSend = vi.fn()
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean **active**?", changes: [] } }}
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
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which column?", changes: [] } }}
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
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which column?", changes: [] } }}
      />,
    )
    expect(screen.queryByTestId("assistant-choose-for-me")).not.toBeInTheDocument()
  })

  it("renders a blocked card that says nothing was saved", () => {
    render(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "blocked", detail: "The claims file is missing.", changes: [] } }}
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
            changes: [],
          },
        }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-committed-unverified")
    expect(card).toHaveTextContent("Your changes were saved")
    expect(card).toHaveTextContent("The plan was committed, but structural verification failed.")
    expect(card.textContent).not.toMatch(/nothing (was saved|changed)/i)
  })

  it("says a turn that stopped before finishing saved nothing and can be continued", () => {
    render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: {
            kind: "incomplete",
            detail: "A dry-run validated a plan that was never applied.",
            changes: [],
          },
        }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-incomplete")
    expect(card).toHaveTextContent("Stopped before finishing")
    expect(card).toHaveTextContent("Ask it to continue. Nothing was saved.")
    expect(card).toHaveTextContent("A dry-run validated a plan that was never applied.")
  })

  it("counts the changes a turn saved before it was blocked", () => {
    render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: { kind: "blocked", detail: "The rating table is missing.", changes: ["plan-1", "plan-2"] },
        }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-blocked")
    expect(card).toHaveTextContent("2 changes were saved, shown above.")
    expect(card.textContent).not.toMatch(/nothing was saved/i)
  })

  it("counts the change a turn saved before its question, and none on a plain question", () => {
    const { rerender } = render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: { kind: "needs_input", detail: "Which band edges?", changes: ["plan-1"] },
        }}
      />,
    )
    expect(screen.getByTestId("assistant-outcome-needs-input")).toHaveTextContent(
      "1 change was saved, shown above.",
    )

    rerender(
      <TranscriptEntryView
        entry={{ kind: "outcome", outcome: { kind: "needs_input", detail: "Which band edges?", changes: [] } }}
      />,
    )
    expect(screen.getByTestId("assistant-outcome-needs-input").textContent).not.toMatch(/saved/i)
  })

  it("counts the changes a turn saved before it stopped", () => {
    render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: {
            kind: "incomplete",
            detail: "A dry-run validated a plan that was never applied.",
            changes: ["plan-1"],
          },
        }}
      />,
    )

    expect(screen.getByTestId("assistant-outcome-incomplete")).toHaveTextContent(
      "Ask it to continue. 1 change was saved, shown above.",
    )
  })
})
