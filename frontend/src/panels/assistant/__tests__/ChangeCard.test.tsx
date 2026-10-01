/**
 * The change card (src/panels/assistant/ChangeCard.tsx) an apply streams, and
 * its place in the transcript.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — "A turn streams into the
 * transcript live"; specs/assistant/high-level.md — "Change cards".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

import type { AssistantChangeNode, AssistantChangeRecord } from "../../../api/assistant"
import useAssistantStore from "../../../stores/useAssistantStore"
import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
import useGitStore from "../../../stores/useGitStore"
import ChangeCard from "../ChangeCard"
import TranscriptEntryView from "../TranscriptEntryView"

afterEach(cleanup)

const REVISION = "e".repeat(64)
const { undoChange: storeUndoChange } = useAssistantStore.getState()

beforeEach(() => {
  useAssistantStore.setState({
    turnStatus: "idle",
    undoingChangeId: null,
    entries: [],
    undoChange: storeUndoChange,
  })
  useDocumentStatusStore.setState({ sourceRevision: REVISION })
  useGitStore.setState({ comparison: null })
})

function chip(node: Partial<AssistantChangeNode> & Pick<AssistantChangeNode, "id">): AssistantChangeNode {
  return {
    type: "Polars",
    change: "added",
    renamed_from: null,
    fields: [],
    steps: null,
    steps_changed: 0,
    ...node,
  }
}

function record(overrides: Partial<AssistantChangeRecord> = {}): AssistantChangeRecord {
  return {
    id: "b".repeat(64),
    summary: "Band driver age, rate it and price the quote.",
    assumptions: ["Drivers under 25 are the young band."],
    changes: {
      nodes: [
        chip({ id: "features", steps: ["source", "free_code"] }),
        chip({ id: "bands", type: "Banding", change: "changed", fields: ["factors", "selected columns"] }),
        chip({ id: "premium", change: "renamed", renamed_from: "priced" }),
        chip({
          id: "risk_features",
          change: "changed",
          steps: ["source", "free_code", "free_code"],
          steps_changed: 1,
        }),
        chip({ id: "old_rates", type: "Constant", change: "removed" }),
      ],
      edges_added: [{ source: "quotes", target: "features" }],
      edges_removed: [{ source: "quotes", target: "old_rates" }],
      preamble_changed: false,
      truncated: false,
    },
    warnings: ["Changes saved; version capture failed (git error — see server log)."],
    git_sha: "0123456789abcdef0123456789abcdef01234567",
    parent_sha: "fedcba9876543210fedcba9876543210fedcba98",
    revision: "e".repeat(64),
    ...overrides,
  }
}

describe("change card", () => {
  it("shows the summary, assumptions, chips, edges, warnings and short commit", () => {
    render(<ChangeCard change={record()} />)

    const card = screen.getByTestId("assistant-change-card")
    expect(card).toHaveTextContent("Band driver age, rate it and price the quote.")
    expect(card).toHaveTextContent("Drivers under 25 are the young band.")

    const chips = within(card).getAllByTestId("assistant-change-node")
    expect(chips.map((item) => item.getAttribute("data-change"))).toEqual([
      "added",
      "changed",
      "renamed",
      "changed",
      "removed",
    ])
    expect(chips[0]).toHaveTextContent("Added features Polars")
    expect(chips[0]).toHaveTextContent("Steps: source, free code")
    expect(chips[1]).toHaveTextContent("Changed bands Banding")
    expect(chips[1]).toHaveTextContent("Changed: factors, selected columns")
    expect(chips[2]).toHaveTextContent("Renamed premium Polars from priced")
    expect(chips[3]).toHaveTextContent("Steps: source, free code, free code (1 step changed)")
    expect(chips[4]).toHaveTextContent("Removed old_rates Constant")

    expect(card).toHaveTextContent("Connected quotes → features")
    expect(card).toHaveTextContent("Disconnected quotes → old_rates")
    expect(within(card).getByTestId("assistant-change-warning")).toHaveTextContent(
      "version capture failed",
    )
    expect(card).toHaveTextContent("Commit 0123456")
    expect(card).not.toHaveTextContent("0123456789")
  })

  it("says a save without a commit was not saved to Git", () => {
    render(<ChangeCard change={record({ git_sha: null, parent_sha: null, warnings: [], assumptions: [] })} />)

    const card = screen.getByTestId("assistant-change-card")
    expect(card).toHaveTextContent("Not saved to Git")
    expect(card).not.toHaveTextContent("Assumed")
    expect(within(card).queryByTestId("assistant-change-warning")).toBeNull()
  })

  it("says when the backend cut the lists, and when the preamble changed", () => {
    const base = record()
    render(
      <ChangeCard
        change={{ ...base, changes: { ...base.changes, truncated: true, preamble_changed: true } }}
      />,
    )

    const card = screen.getByTestId("assistant-change-card")
    expect(card).toHaveTextContent("More changes were saved than this card lists.")
    expect(card).toHaveTextContent("The pipeline preamble changed.")
  })

  it("renders as a transcript entry", () => {
    render(<TranscriptEntryView entry={{ kind: "change", change: record() }} />)
    expect(screen.getByTestId("assistant-change-card")).toHaveTextContent("Commit 0123456")
  })
})

describe("undo and compare", () => {
  it("offers neither on a change not saved to Git", () => {
    render(<ChangeCard change={record({ git_sha: null, parent_sha: null })} />)
    expect(screen.queryByTestId("assistant-change-undo")).toBeNull()
    expect(screen.queryByTestId("assistant-change-compare")).toBeNull()
  })

  it("enables Undo only while the canvas shows the revision the change produced", () => {
    render(<ChangeCard change={record()} />)
    const undo = screen.getByTestId("assistant-change-undo")
    expect(undo).toBeEnabled()
    expect(undo).toHaveAttribute("data-primary", "false")

    act(() => useDocumentStatusStore.setState({ sourceRevision: "f".repeat(64) }))
    expect(undo).toBeDisabled()
    expect(undo).toHaveAttribute("title", "The pipeline was saved again after this change.")

    act(() => useDocumentStatusStore.setState({ sourceRevision: REVISION }))
    act(() => useAssistantStore.setState({ turnStatus: "streaming" }))
    expect(undo).toBeDisabled()
  })

  it("undoes through the store, and Compare opens the version before the change", () => {
    const undoChange = vi.fn(async () => {})
    useAssistantStore.setState({ undoChange })
    const change = record()
    render(<ChangeCard change={change} />)

    fireEvent.click(screen.getByTestId("assistant-change-undo"))
    fireEvent.click(screen.getByTestId("assistant-change-compare"))

    expect(undoChange).toHaveBeenCalledWith(change)
    expect(useGitStore.getState().comparison).toEqual({
      sha: change.parent_sha,
      label: `Before: ${change.summary}`,
    })
  })

  it("makes Undo the saved-but-unverified card's primary action for its last change", () => {
    const change = record()
    useAssistantStore.setState({ entries: [{ kind: "change", change }] })
    render(
      <TranscriptEntryView
        entry={{
          kind: "outcome",
          outcome: { kind: "committed_unverified", detail: "verification_failed", changes: [change.id] },
        }}
      />,
    )

    const card = screen.getByTestId("assistant-outcome-committed-unverified")
    expect(card).toHaveTextContent("undo this change")
    const undo = within(card).getByTestId("assistant-change-undo")
    expect(undo).toHaveAttribute("data-primary", "true")
    expect(undo).toBeEnabled()
  })

  it("notes an undone change in the transcript", () => {
    render(<TranscriptEntryView entry={{ kind: "undo", change: record() }} />)
    expect(screen.getByTestId("assistant-entry-undo")).toHaveTextContent(
      "You undid: Band driver age, rate it and price the quote.",
    )
  })
})
