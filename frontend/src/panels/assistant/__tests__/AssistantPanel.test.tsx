/**
 * The assistant panel (src/panels/assistant/AssistantPanel.tsx): transcript
 * order, the one-click reply on the latest question, and readiness on the
 * opening chat-list screen.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — Readiness, "A turn streams
 * into the transcript live", "Every completed turn ends with its outcome", "A new
 * chat says what the assistant can do", "The composer shows what a message
 * carries", "Ask the assistant to fix a run error".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"

import AssistantPanel from "../AssistantPanel"
import useAssistantStore, {
  CHOOSE_FOR_ME_REPLY,
  FIX_ERROR_PROMPT,
  type AssistantStoreState,
} from "../../../stores/useAssistantStore"
import useDocumentStatusStore from "../../../stores/useDocumentStatusStore"
import useGraphStore from "../../../stores/useGraphStore"
import useUIStore from "../../../stores/useUIStore"

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
    thinking: false,
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
  useGraphStore.setState({ dirty: false, nodes: [] })
  useUIStore.setState({ assistantPreviewErrorNodeId: null })
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
        { kind: "outcome", outcome: { kind: "answered", detail: null, changes: [] } },
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

  it("shows the thinking status only while the model thinks", () => {
    seed({ entries: [{ kind: "user", text: "go" }], turnStatus: "streaming", thinking: true })
    renderPanel()

    expect(screen.getByTestId("assistant-thinking")).toHaveTextContent("Thinking…")

    act(() => useAssistantStore.setState({ thinking: false }))

    expect(screen.queryByTestId("assistant-thinking")).toBeNull()
  })

  it("sends the one-click reply from the latest question", () => {
    const sendMessage = vi.fn(async () => {})
    useAssistantStore.setState({ sendMessage })
    seed({
      entries: [
        { kind: "user", text: "keep active policies" },
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?", changes: [] } },
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
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?", changes: [] } },
        { kind: "user", text: "Y and N" },
        { kind: "outcome", outcome: { kind: "applied", detail: null, changes: ["plan-1"] } },
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
        { kind: "outcome", outcome: { kind: "needs_input", detail: "Which values mean active?", changes: [] } },
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

function canvasNode(id: string, label: string, selected: boolean) {
  return { id, position: { x: 0, y: 0 }, data: { label }, selected }
}

describe("header and new chat", () => {
  it("names the configured model and provider", () => {
    seed({ view: "list" })
    renderPanel()
    expect(screen.getByTestId("assistant-model")).toHaveTextContent("m · openai")
  })

  it("says what the assistant can and cannot do, and a starter prompt only fills the composer", () => {
    seed({ sessionId: null, pipelineSource: null })
    renderPanel()

    expect(screen.getByTestId("assistant-empty")).toHaveTextContent("Ask the assistant to change main.py.")
    expect(screen.getByTestId("assistant-can")).toHaveTextContent("banding")
    expect(screen.getByTestId("assistant-cannot")).toHaveTextContent("Run the pipeline")
    expect(screen.getByTestId("assistant-cannot")).toHaveTextContent("Deploy")
    const [prompt] = screen.getAllByTestId("assistant-starter-prompt")
    fireEvent.click(prompt)

    expect(screen.getByTestId("assistant-composer")).toHaveValue(prompt.textContent)
    expect(useAssistantStore.getState().sendMessage).not.toHaveBeenCalled()
  })
})

describe("context chips", () => {
  it("names the selected nodes the message carries, and nothing with none selected", () => {
    seed({})
    useGraphStore.setState({
      nodes: [
        canvasNode("a", "Age bands", true),
        canvasNode("b", "Base rate", false),
        canvasNode("c", "Claims", true),
        canvasNode("d", "Driver", true),
        canvasNode("e", "Excess", true),
      ],
    })
    renderPanel()
    expect(screen.getByTestId("assistant-context-selection")).toHaveTextContent(
      "Selected: Age bands, Claims, Driver +1 more",
    )

    act(() => {
      useGraphStore.setState({ nodes: [canvasNode("a", "Age bands", false)] })
    })
    expect(screen.queryByTestId("assistant-context-selection")).not.toBeInTheDocument()
  })

  it("names the fix request's node until it is dismissed", () => {
    seed({})
    useGraphStore.setState({ nodes: [canvasNode("rating", "Rating", true)] })
    useUIStore.setState({ assistantPreviewErrorNodeId: "rating" })
    renderPanel()

    expect(screen.getByTestId("assistant-context-error")).toHaveTextContent("Run error: Rating")
    fireEvent.click(screen.getByTestId("assistant-context-error-dismiss"))
    expect(useUIStore.getState().assistantPreviewErrorNodeId).toBeNull()
    expect(screen.queryByTestId("assistant-context-error")).not.toBeInTheDocument()
  })

  it("opens a new chat with the fix drafted when asked to fix from the list", () => {
    seed({ view: "list", sessionId: null, pipelineSource: null })
    renderPanel()
    expect(screen.queryByTestId("assistant-composer")).not.toBeInTheDocument()

    act(() => {
      useUIStore.setState({ assistantPreviewErrorNodeId: "rating" })
    })
    expect(useAssistantStore.getState().view).toBe("chat")
    expect(screen.getByTestId("assistant-composer")).toHaveValue(FIX_ERROR_PROMPT)
  })
})
