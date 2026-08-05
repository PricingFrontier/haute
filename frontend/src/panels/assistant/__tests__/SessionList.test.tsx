/**
 * The assistant chat list (src/panels/assistant/SessionList.tsx).
 *
 * Spec: specs/frontend-assistant-ui/low-level.md — Control flow (chat list).
 * The panel opens on this screen, so an empty, loading, or failed list has to
 * say which it is rather than rendering as a blank panel.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import SessionList from "../SessionList"
import { relativeTime } from "../relativeTime"
import useAssistantStore from "../../../stores/useAssistantStore"

function seed(partial: Partial<ReturnType<typeof useAssistantStore.getState>>) {
  useAssistantStore.setState({
    sessions: [],
    sessionsStatus: "ready",
    ...partial,
  })
}

describe("SessionList", () => {
  beforeEach(() => {
    useAssistantStore.setState({ openSession: vi.fn(), loadSessions: vi.fn() })
  })
  afterEach(cleanup)

  it("names an empty list rather than rendering blank", () => {
    seed({ sessions: [] })
    render(<SessionList currentSourceFile="main.py" />)
    expect(screen.getByTestId("assistant-sessions-empty")).toBeInTheDocument()
  })

  it("distinguishes loading from empty", () => {
    seed({ sessionsStatus: "loading" })
    render(<SessionList currentSourceFile="main.py" />)
    expect(screen.getByTestId("assistant-sessions-loading")).toBeInTheDocument()
  })

  it("offers a retry when the list could not be loaded", () => {
    const loadSessions = vi.fn()
    seed({ sessionsStatus: "error" })
    useAssistantStore.setState({ loadSessions })

    render(<SessionList currentSourceFile="main.py" />)
    fireEvent.click(screen.getByTestId("assistant-sessions-retry"))

    expect(loadSessions).toHaveBeenCalledWith("main.py")
  })

  it("renders each conversation and opens the one clicked", () => {
    const openSession = vi.fn()
    seed({
      sessions: [
        { sessionId: "s1", title: "aggregate claims", createdAt: 1, lastUsed: 2, messageCount: 6 },
        { sessionId: "s2", title: "", createdAt: 1, lastUsed: 1, messageCount: 2 },
      ],
    })
    useAssistantStore.setState({ openSession })

    render(<SessionList currentSourceFile="main.py" />)

    expect(screen.getByText("aggregate claims")).toBeInTheDocument()
    // A conversation with no opening user text still needs a row label.
    expect(screen.getByText("Untitled chat")).toBeInTheDocument()

    fireEvent.click(screen.getAllByTestId("assistant-session-item")[0])
    expect(openSession).toHaveBeenCalledWith("s1", "main.py")
  })

  it("cannot open a conversation with no pipeline resolved", () => {
    const openSession = vi.fn()
    seed({
      sessions: [
        { sessionId: "s1", title: "one", createdAt: 1, lastUsed: 2, messageCount: 2 },
      ],
    })
    useAssistantStore.setState({ openSession })

    render(<SessionList currentSourceFile={null} />)
    fireEvent.click(screen.getAllByTestId("assistant-session-item")[0])

    expect(openSession).not.toHaveBeenCalled()
  })
})

describe("relativeTime", () => {
  const now = Date.parse("2026-08-05T12:00:00Z")

  it.each([
    [now / 1000, "just now"],
    [now / 1000 - 120, "2m ago"],
    [now / 1000 - 7200, "2h ago"],
    [now / 1000 - 172_800, "2d ago"],
  ])("renders %s as %s", (seconds, expected) => {
    expect(relativeTime(seconds, now)).toBe(expected)
  })

  it("falls back to a date beyond a week", () => {
    expect(relativeTime(now / 1000 - 30 * 86_400, now)).not.toMatch(/ago|just now/)
  })

  it("renders nothing for a missing timestamp", () => {
    expect(relativeTime(0, now)).toBe("")
  })
})
