/**
 * The assistant panel (src/panels/assistant/AssistantPanel.tsx): transcript
 * order, the one-click reply on the latest question, and readiness on the
 * opening chat-list screen.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — Readiness, "A turn streams
 * into the transcript live", "Every completed turn ends with its outcome".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import AssistantPanel from "../AssistantPanel"
import useAssistantStore, {
  CHOOSE_FOR_ME_REPLY,
  type AssistantStoreState,
} from "../../../stores/useAssistantStore"
import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
import useGraphStore from "../../../stores/useGraphStore"

const READY_STATUS = {
  configured: true,
  reason: null,
  provider: "openai",
  model: "m",
  endpoint_host: "api.example.com",
  trust: "organization" as const,
  max_sensitivity: "restricted" as const,
  mutations_enabled: true,
  mutations_reason: null,
}

function seed(partial: Partial<AssistantStoreState>) {
  useAssistantStore.setState({
    sessionId: "s1",
    pipelineSource: "main.py",
    entries: [],
    turnStatus: "idle",
    status: READY_STATUS,
    statusErrorDetail: null,
    notice: null,
    view: "chat",
    sessions: [],
    sessionsStatus: "ready",
    sessionsSource: "main.py",
    ...partial,
  })
}

function renderPanel() {
  return render(<AssistantPanel isInsideSubmodel={false} readOnly={false} />)
}

beforeEach(() => {
  // The panel's mount effects fetch; these tests pin what it renders.
  useAssistantStore.setState({
    refreshStatus: vi.fn(async () => {}),
    loadSessions: vi.fn(async () => {}),
    sendMessage: vi.fn(async () => {}),
  })
  useDocumentStatusStore.setState({ sourceFile: "main.py" })
  useGraphStore.setState({ dirty: false })
})

afterEach(cleanup)

describe("transcript", () => {
  it("renders text, tool and text in the order they streamed", () => {
    seed({
      entries: [
        { kind: "user", text: "go" },
        { kind: "assistant", text: "Reading the pipeline.", streaming: false },
        {
          kind: "activity",
          id: "t1",
          name: "get_pipeline",
          title: "Reading the pipeline",
          state: "ok",
          summary: "3 nodes",
        },
        { kind: "assistant", text: "It has three nodes.", streaming: false },
        { kind: "outcome", outcome: { kind: "answered", detail: null } },
      ],
    })
    renderPanel()

    const rows = Array.from(screen.getByTestId("assistant-transcript").children)
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "assistant-entry-user",
      "assistant-entry-assistant",
      "assistant-entry-activity",
      "assistant-entry-assistant",
      "assistant-entry-marker",
    ])
    expect(rows[1]).toHaveTextContent("Reading the pipeline.")
    expect(rows[3]).toHaveTextContent("It has three nodes.")
  })

  it("sends the one-click reply from the latest question", () => {
    const sendMessage = vi.fn(async () => {})
    useAssistantStore.setState({ sendMessage })
    seed({
      entries: [
        { kind: "user", text: "keep active policies" },
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?" } },
      ],
    })
    renderPanel()

    fireEvent.click(screen.getByTestId("assistant-choose-for-me"))
    expect(sendMessage).toHaveBeenCalledWith(CHOOSE_FOR_ME_REPLY, {
      isInsideSubmodel: false,
      currentSourceFile: "main.py",
      readOnly: false,
    })
  })

  it("offers no one-click reply on an earlier question", () => {
    seed({
      entries: [
        { kind: "user", text: "keep active policies" },
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?" } },
        { kind: "user", text: "Y and N" },
        { kind: "outcome", outcome: { kind: "applied", detail: null } },
      ],
    })
    renderPanel()

    expect(screen.getByTestId("assistant-outcome-needs-input")).toBeInTheDocument()
    expect(screen.queryByTestId("assistant-choose-for-me")).not.toBeInTheDocument()
  })

  it("disables the one-click reply while the canvas has unsaved edits", () => {
    useGraphStore.setState({ dirty: true })
    seed({
      entries: [
        { kind: "user", text: "keep active policies" },
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?" } },
      ],
    })
    renderPanel()

    expect(screen.getByTestId("assistant-choose-for-me")).toBeDisabled()
  })
})

describe("readiness on the chat list", () => {
  it("states why an unconfigured assistant cannot be used, with a re-check", () => {
    const refreshStatus = vi.fn(async () => {})
    useAssistantStore.setState({ refreshStatus })
    seed({
      view: "list",
      status: {
        ...READY_STATUS,
        configured: false,
        reason: "Missing API key environment variable: OPENAI_API_KEY.",
        endpoint_host: null,
        trust: null,
        max_sensitivity: null,
      },
    })
    renderPanel()

    const card = screen.getByTestId("assistant-readiness")
    expect(card).toHaveTextContent("Assistant is not set up")
    expect(card).toHaveTextContent("Missing API key environment variable: OPENAI_API_KEY.")
    refreshStatus.mockClear()
    fireEvent.click(screen.getByTestId("assistant-status-retry"))
    expect(refreshStatus).toHaveBeenCalledTimes(1)
  })

  it("states why edits are disabled", () => {
    seed({
      view: "list",
      status: {
        ...READY_STATUS,
        mutations_enabled: false,
        mutations_reason: "Git is not available on this host; assistant edits need Git to record each change.",
      },
    })
    renderPanel()

    const card = screen.getByTestId("assistant-readiness")
    expect(card).toHaveTextContent("Assistant cannot edit this project")
    expect(card).toHaveTextContent("Git is not available on this host")
  })

  it("shows no readiness card when the assistant is ready", () => {
    seed({ view: "list" })
    renderPanel()
    expect(screen.queryByTestId("assistant-readiness")).not.toBeInTheDocument()
  })

  it("names an unreadable configuration and its fix instead of a bare retry", () => {
    seed({
      view: "list",
      status: "error",
      statusErrorDetail: "haute.toml is malformed and could not be parsed",
    })
    renderPanel()

    const card = screen.getByTestId("assistant-status-error")
    expect(card).toHaveTextContent("haute.toml is malformed and could not be parsed")
    expect(card).toHaveTextContent("Fix haute.toml, then check again.")
    expect(screen.getByTestId("assistant-status-retry")).toHaveTextContent("Check again")
  })

  it("offers a retry when the status request itself failed", () => {
    seed({ view: "list", status: "error", statusErrorDetail: null })
    renderPanel()

    expect(screen.getByTestId("assistant-status-error")).toHaveTextContent(
      "Assistant status could not be loaded.",
    )
    expect(screen.getByTestId("assistant-status-retry")).toHaveTextContent("Retry")
  })
})
