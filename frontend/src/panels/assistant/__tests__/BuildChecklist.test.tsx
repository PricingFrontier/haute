/**
 * The build checklist (src/panels/assistant/BuildChecklist.tsx): the backend's
 * build plan as each stage's completion and the changes saved for it.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — "A multi-stage build shows a
 * checklist"; specs/assistant/high-level.md — "Build plans for multi-stage requests".
 */

import { useRef } from "react"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"

import type { AssistantBuildPlan, AssistantChangeRecord } from "../../../api/assistant"
import type { TranscriptEntry } from "../../../stores/useAssistantStore"
import BuildChecklist from "../BuildChecklist"

function change(id: string, summary: string): AssistantChangeRecord {
  return {
    id,
    summary,
    assumptions: [],
    changes: {
      nodes: [],
      edges_added: [],
      edges_removed: [],
      preamble_changed: false,
      truncated: false,
    },
    warnings: [],
    git_sha: null,
    parent_sha: null,
    revision: "e".repeat(64),
    data_check: null,
  }
}

const SOURCE = change("1".repeat(64), "Read the batch and add its features.")
const BANDING = change("2".repeat(64), "Band region into region groups.")
const PRUNED = "3".repeat(64)

const PLAN: AssistantBuildPlan = {
  items: [
    { id: "source", title: "Data input and features", complete: true, changes: [{ id: SOURCE.id, undone: false }] },
    {
      id: "banding",
      title: "Region banding",
      complete: false,
      changes: [{ id: PRUNED, undone: true }, { id: BANDING.id, undone: false }],
    },
    { id: "rating", title: "Region rating", complete: false, changes: [] },
  ],
}

const ENTRIES: TranscriptEntry[] = [
  { kind: "user", text: "Build it in stages." },
  { kind: "change", change: SOURCE },
  { kind: "change", change: BANDING },
]

/** The checklist beside a transcript holding a card for each `change` entry. */
function Harness({ plan, entries }: { plan: AssistantBuildPlan; entries: TranscriptEntry[] }) {
  const transcriptRef = useRef<HTMLDivElement>(null)
  return (
    <>
      <div ref={transcriptRef}>
        {entries.map((entry, index) =>
          entry.kind === "change" ? (
            <section key={index} data-testid="card" data-change-id={entry.change.id} />
          ) : null,
        )}
      </div>
      <BuildChecklist plan={plan} entries={entries} transcriptRef={transcriptRef} />
    </>
  )
}

const scrolled: Element[] = []
const originalScrollIntoView = Element.prototype.scrollIntoView

beforeEach(() => {
  scrolled.length = 0
  // jsdom implements no layout, so the scroll is recorded instead.
  Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
    scrolled.push(this)
  }
})

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView
  cleanup()
})

describe("BuildChecklist", () => {
  it("counts the done stages, states each in order and collapses to its header", () => {
    render(<Harness plan={PLAN} entries={ENTRIES} />)

    expect(screen.getByTestId("assistant-build-checklist-count").textContent).toBe("1 of 3 done")
    const items = screen.getAllByTestId("assistant-build-item")
    expect(items.map((item) => item.dataset.state)).toEqual(["Done", "In progress", "Not started"])
    expect(items.map((item) => within(item).getByText(/Data input|Region/).textContent)).toEqual([
      "Data input and features",
      "Region banding",
      "Region rating",
    ])

    fireEvent.click(screen.getByTestId("assistant-build-checklist-toggle"))

    expect(screen.queryAllByTestId("assistant-build-item")).toHaveLength(0)
    expect(screen.getByTestId("assistant-build-checklist-toggle").getAttribute("aria-expanded")).toBe(
      "false",
    )
  })

  it("names a change by its card's summary and scrolls that card into view", () => {
    render(<Harness plan={PLAN} entries={ENTRIES} />)

    const [source] = screen.getAllByTestId("assistant-build-item")
    fireEvent.click(within(source).getByRole("button", { name: SOURCE.summary }))

    const cards = screen.getAllByTestId("card")
    expect(scrolled).toEqual([cards[0]])
  })

  it("names a change whose card is not in the transcript without a link, and marks an undone change", () => {
    render(<Harness plan={PLAN} entries={ENTRIES} />)

    const banding = screen.getAllByTestId("assistant-build-item")[1]
    const [pruned, live] = within(banding).getAllByTestId("assistant-build-change")
    expect(pruned.textContent).toBe(`An earlier change ${PRUNED.slice(0, 7)}undone`)
    expect(pruned.dataset.undone).toBe("true")
    expect(within(pruned).queryByRole("button")).toBeNull()
    expect(live.dataset.undone).toBe("false")
    expect(within(live).getByRole("button").textContent).toBe(BANDING.summary)
  })

  it("strikes through an undone change that still has its card", () => {
    const undone: AssistantBuildPlan = {
      items: [{ id: "banding", title: "Region banding", complete: false, changes: [{ id: BANDING.id, undone: true }] }],
    }
    render(<Harness plan={undone} entries={ENTRIES} />)

    const link = screen.getByRole("button", { name: BANDING.summary })
    expect(link.style.textDecoration).toBe("line-through")
    expect(screen.getByTestId("assistant-build-item").dataset.state).toBe("Not started")
    expect(screen.getByTestId("assistant-build-change").textContent).toBe(`${BANDING.summary}undone`)
  })
})
