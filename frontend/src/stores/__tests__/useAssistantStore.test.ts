/**
 * Tests for the assistant chat store (src/stores/useAssistantStore.ts).
 *
 * Spec: specs/frontend-assistant-ui/low-level.md — Key types, Control
 * flow (Send/Stop/New chat), Edge cases, Error handling, Testing.
 * Authored test-first: the store is implemented to make these pass.
 *
 * API pinned here (state): sessionId, pipelineSource, entries,
 * turnStatus ("idle" | "streaming"), status (AssistantStatus | "unknown" |
 * "error"), notice (string | null — inline send-failure messaging).
 * Actions: refreshStatus(), sendMessage(text, { isInsideSubmodel,
 * currentSourceFile, readOnly }), stopTurn(), newChat(), loadSessions(sourceFile),
 * openSession(sessionId, sourceFile), showSessionList(sourceFile), plus the
 * list-screen state view/sessions/sessionsStatus.
 *
 * The api module is mocked (the SSE parser has its own suite); graph and
 * toast stores are the real ones (the toast store is reset per test by the
 * global setupTests hook).
 */

import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "../../api/client"
import useGraphStore from "../useGraphStore"
import useToastStore from "../useToastStore"

vi.mock("../../api/assistant", () => ({
  getAssistantStatus: vi.fn(),
  createAssistantSession: vi.fn(),
  listAssistantSessions: vi.fn(),
  streamAssistantMessage: vi.fn(),
  MAX_CONTEXT_SELECTION: 20,
}))

import {
  createAssistantSession,
  getAssistantStatus,
  listAssistantSessions,
  streamAssistantMessage,
  type AssistantChangeRecord,
  type AssistantSessionList,
  type AssistantSessionResult,
  type AssistantStreamEvent,
  type AssistantTurnOutcome,
} from "../../api/assistant"
import useAssistantStore, { type TranscriptEntry } from "../useAssistantStore"

const READY_STATUS = {
  configured: true,
  reason: null,
  provider: "anthropic",
  model: "m",
  endpoint_host: "api.anthropic.com",
  trust: "external" as const,
  max_sensitivity: "public" as const,
  mutations_enabled: true,
  mutations_reason: null,
}

const SEND_OPTS = { isInsideSubmodel: false, currentSourceFile: "main.py", readOnly: false }

function resetStores() {
  useAssistantStore.setState({
    sessionId: null,
    pipelineSource: null,
    entries: [],
    turnStatus: "idle",
    status: READY_STATUS,
    statusErrorDetail: null,
    notice: null,
    view: "list",
    sessions: [],
    sessionsStatus: "unknown",
    // The mounted panel has listed the canvas pipeline's chats.
    sessionsSource: "main.py",
  })
  useGraphStore.setState({ dirty: false, nodes: [] })
  vi.mocked(createAssistantSession).mockResolvedValue({ sessionId: "session-1", sourceFile: "main.py", history: [] })
  vi.mocked(getAssistantStatus).mockResolvedValue(READY_STATUS)
  // Every completed turn refreshes the list; without a default the shared
  // mock resolves undefined and every unrelated test records a list error.
  vi.mocked(listAssistantSessions).mockResolvedValue({ sourceFile: "main.py", sessions: [] })
}

function scriptStream(events: AssistantStreamEvent[]) {
  vi.mocked(streamAssistantMessage).mockImplementation(async (_id, _text, _source, opts) => {
    for (const event of events) opts.onEvent(event)
  })
}

function completed(outcome: AssistantTurnOutcome = { kind: "answered", detail: null, changes: [] }): AssistantStreamEvent {
  return { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStores()
})

describe("refreshStatus", () => {
  it("stores the fetched status", async () => {
    useAssistantStore.setState({ status: "unknown" })
    await useAssistantStore.getState().refreshStatus()
    expect(useAssistantStore.getState().status).toEqual(READY_STATUS)
  })

  it("marks status error on fetch failure", async () => {
    vi.mocked(getAssistantStatus).mockRejectedValue(new Error("boom"))
    await useAssistantStore.getState().refreshStatus()
    expect(useAssistantStore.getState().status).toBe("error")
    expect(useAssistantStore.getState().statusErrorDetail).toBeNull()
  })

  it("keeps a 400's detail, which names what to fix in haute.toml", async () => {
    vi.mocked(getAssistantStatus).mockRejectedValue(
      new ApiError("bad", 400, "haute.toml is malformed and could not be parsed"),
    )
    await useAssistantStore.getState().refreshStatus()
    expect(useAssistantStore.getState().status).toBe("error")
    expect(useAssistantStore.getState().statusErrorDetail).toBe(
      "haute.toml is malformed and could not be parsed",
    )

    vi.mocked(getAssistantStatus).mockResolvedValue(READY_STATUS)
    await useAssistantStore.getState().refreshStatus()
    expect(useAssistantStore.getState().statusErrorDetail).toBeNull()
  })
})

describe("sendMessage transcript flow", () => {
  it("appends deltas into one streaming assistant entry then closes it", async () => {
    scriptStream([
      { type: "text_delta", text: "Hel" },
      { type: "text_delta", text: "lo" },
      completed(),
    ])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    const { entries, turnStatus, sessionId, pipelineSource } = useAssistantStore.getState()
    expect(sessionId).toBe("session-1")
    expect(pipelineSource).toBe("main.py")
    expect(turnStatus).toBe("idle")
    expect(entries[0]).toEqual({ kind: "user", text: "hi" })
    const assistant = entries.find((entry) => entry.kind === "assistant")
    expect(assistant).toMatchObject({ text: "Hello", streaming: false })
    expect(entries[entries.length - 1]).toEqual({
      kind: "outcome",
      outcome: { kind: "answered", detail: null, changes: [] },
    })
  })

  it("settles activity rows from started to ok with the summary", async () => {
    scriptStream([
      { type: "tool_started", id: "t1", name: "get_pipeline", title: "Reading the pipeline", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "get_pipeline", title: "Reading the pipeline", is_error: false, summary: "3 nodes" },
      completed(),
    ])
    await useAssistantStore.getState().sendMessage("read", SEND_OPTS)

    const activity = useAssistantStore
      .getState()
      .entries.find((entry) => entry.kind === "activity")
    expect(activity).toMatchObject({
      id: "t1",
      name: "get_pipeline",
      state: "ok",
      summary: "3 nodes",
    })
  })

  it("marks failed tool activity as error state", async () => {
    scriptStream([
      { type: "tool_started", id: "t1", name: "apply_graph_plan", title: "Applying the plan", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "apply_graph_plan", title: "Applying the plan", is_error: true, summary: "no" },
      completed(),
    ])
    await useAssistantStore.getState().sendMessage("edit", SEND_OPTS)
    const activity = useAssistantStore
      .getState()
      .entries.find((entry) => entry.kind === "activity")
    expect(activity).toMatchObject({ state: "error" })
  })

  it("renders a failed terminal event as marker plus error toast", async () => {
    scriptStream([{ type: "failed", message: "provider rate_limit failure" }])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    const { entries, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(entries[entries.length - 1]).toMatchObject({
      kind: "marker",
      outcome: "failed",
      detail: "provider rate_limit failure",
    })
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(true)
  })

  it("appends the change card a change_applied event carries, after the apply row", async () => {
    scriptStream([
      { type: "text_delta", text: "Adding the band." },
      { type: "tool_started", id: "t1", name: "apply_graph_plan", title: "Applying the plan", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "apply_graph_plan", title: "Applying 2 changes", is_error: false, summary: "ok" },
      { type: "change_applied", change: CHANGE },
      completed({ kind: "applied", detail: null, changes: ["plan-1"] }),
    ])
    await useAssistantStore.getState().sendMessage("edit", SEND_OPTS)
    const { entries } = useAssistantStore.getState()
    expect(entries.map((entry) => entry.kind)).toEqual([
      "user",
      "assistant",
      "activity",
      "change",
      "outcome",
    ])
    expect(entries[2]).toMatchObject({ title: "Applying 2 changes", state: "ok" })
    expect(entries[3]).toEqual({ kind: "change", change: CHANGE })
  })

  it("renders a backend cancelled event as a stopped marker without a toast", async () => {
    scriptStream([{ type: "text_delta", text: "part" }, { type: "cancelled" }])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    const { entries, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "stopped" })
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("marks post-terminal events as interrupted and shows an error toast", async () => {
    scriptStream([
      completed(),
      { type: "text_delta", text: "late" },
      { type: "failed", message: "late failure" },
    ])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    const { entries } = useAssistantStore.getState()
    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "interrupted" })
    expect(entries.filter((entry) => entry.kind === "marker")).toHaveLength(1)
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(true)
  })

  it("leaves the transcript unchanged for a tool_finished with no matching row", async () => {
    scriptStream([
      { type: "tool_finished", id: "ghost", name: "get_pipeline", title: "Reading the pipeline", is_error: false, summary: "" },
      completed(),
    ])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(
      useAssistantStore.getState().entries.filter((entry) => entry.kind === "activity"),
    ).toEqual([])
  })

  it("marks a stream that ends without a terminal event as interrupted", async () => {
    scriptStream([{ type: "text_delta", text: "partial" }])
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    const { entries } = useAssistantStore.getState()
    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "interrupted" })
  })

  it("marks a parser throw as interrupted with an error toast", async () => {
    vi.mocked(streamAssistantMessage).mockRejectedValue(new Error("Unknown assistant stream event"))
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    const { entries, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "interrupted" })
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(true)
  })
})

const HISTORY_TEXT = { name: "", title: "", summary: "", is_error: false }

const CHANGE: AssistantChangeRecord = {
  id: "a".repeat(64),
  summary: "Add an age band after quotes.",
  assumptions: [],
  changes: {
    nodes: [
      {
        id: "age_band",
        type: "Banding",
        change: "added",
        renamed_from: null,
        fields: [],
        steps: null,
        steps_changed: 0,
      },
    ],
    edges_added: [{ source: "quotes", target: "age_band" }],
    edges_removed: [],
    preamble_changed: false,
    truncated: false,
  },
  warnings: [],
  git_sha: "c".repeat(40),
  parent_sha: "d".repeat(40),
}

async function liveEntries(events: AssistantStreamEvent[]): Promise<TranscriptEntry[]> {
  scriptStream(events)
  await useAssistantStore.getState().sendMessage("go", SEND_OPTS)
  return useAssistantStore.getState().entries
}

describe("transcript order and turn outcomes", () => {
  it("keeps text and tool rows in stream order", async () => {
    const entries = await liveEntries([
      { type: "text_delta", text: "Reading the pipeline." },
      { type: "tool_started", id: "t1", name: "get_pipeline", title: "Reading the pipeline", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "get_pipeline", title: "Reading the pipeline", is_error: false, summary: "3 nodes" },
      { type: "text_delta", text: "It has " },
      { type: "text_delta", text: "three nodes." },
      completed(),
    ])

    expect(entries.map((entry) => entry.kind)).toEqual([
      "user", "assistant", "activity", "assistant", "outcome",
    ])
    expect(entries[1]).toEqual({ kind: "assistant", text: "Reading the pipeline.", streaming: false })
    expect(entries[3]).toEqual({ kind: "assistant", text: "It has three nodes.", streaming: false })
  })

  it("drops the empty placeholder when a tool row comes first", async () => {
    const entries = await liveEntries([
      { type: "tool_started", id: "t1", name: "get_pipeline", title: "Reading the pipeline", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "get_pipeline", title: "Reading the pipeline", is_error: false, summary: "ok" },
      completed(),
    ])

    expect(entries.map((entry) => entry.kind)).toEqual(["user", "activity", "outcome"])
  })

  it("replaces the model's NEEDS_INPUT: text with the question outcome", async () => {
    const entries = await liveEntries([
      { type: "text_delta", text: "I checked the columns. " },
      { type: "text_delta", text: "NEEDS_INPUT: Which values of status mean active?" },
      completed({ kind: "needs_input", detail: "Which values of status mean active?", changes: [] }),
    ])

    expect(entries.slice(1)).toEqual([
      { kind: "assistant", text: "I checked the columns.", streaming: false },
      {
        kind: "outcome",
        outcome: { kind: "needs_input", detail: "Which values of status mean active?", changes: [] },
      },
    ])
  })

  it("replaces a blocker that follows a tool row with the blocked outcome", async () => {
    const detail = "graph validation failed after one corrected retry (x); no graph changes were applied."
    const entries = await liveEntries([
      { type: "tool_started", id: "t1", name: "dry_run_graph_edits", title: "Checking the plan", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "dry_run_graph_edits", title: "Checking the plan", is_error: true, summary: "bad" },
      { type: "text_delta", text: `BLOCKED: ${detail}` },
      completed({ kind: "blocked", detail, changes: [] }),
    ])

    expect(entries.map((entry) => entry.kind)).toEqual(["user", "activity", "outcome"])
    expect(entries[2]).toEqual({ kind: "outcome", outcome: { kind: "blocked", detail, changes: [] } })
  })

  it("keeps the saved-but-unverified statement beside its outcome", async () => {
    const outcome: AssistantTurnOutcome = {
      kind: "committed_unverified",
      detail: "The plan was committed, but structural verification failed.",
      changes: [],
    }
    const entries = await liveEntries([
      { type: "text_delta", text: "Graph changes were saved, but post-save verification failed." },
      completed(outcome),
    ])

    expect(entries.slice(1)).toEqual([
      {
        kind: "assistant",
        text: "Graph changes were saved, but post-save verification failed.",
        streaming: false,
      },
      { kind: "outcome", outcome },
    ])
  })

  it("keeps the model's text beside a stopped-before-finishing outcome", async () => {
    const outcome: AssistantTurnOutcome = {
      kind: "incomplete",
      detail: "The last dry-run failed and no later dry-run succeeded.",
      changes: [],
    }
    const entries = await liveEntries([
      { type: "text_delta", text: "I will look again." },
      completed(outcome),
    ])

    expect(entries.slice(1)).toEqual([
      { kind: "assistant", text: "I will look again.", streaming: false },
      { kind: "outcome", outcome },
    ])
  })

  it("interrupts a turn whose outcome does not match its reply", async () => {
    const entries = await liveEntries([
      { type: "text_delta", text: "Here is the answer." },
      completed({ kind: "needs_input", detail: "Which column?", changes: [] }),
    ])

    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "interrupted" })
    expect(useToastStore.getState().toasts.some((toast) => toast.type === "error")).toBe(true)
  })

  it("renders a resumed turn exactly as the live turn rendered", async () => {
    const detail = "Which values of status mean active?"
    const live = await liveEntries([
      { type: "text_delta", text: "Reading." },
      { type: "tool_started", id: "t1", name: "get_pipeline", title: "Reading the pipeline", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "get_pipeline", title: "Reading the pipeline", is_error: false, summary: "3 nodes" },
      { type: "text_delta", text: "The plan is ready." },
      { type: "text_delta", text: `NEEDS_INPUT: ${detail}` },
      completed({ kind: "needs_input", detail, changes: [] }),
    ])
    vi.mocked(createAssistantSession).mockResolvedValue({
      sessionId: "session-1",
      sourceFile: "main.py",
      history: [
        { kind: "user", text: "go", ...HISTORY_TEXT },
        { kind: "assistant", text: "Reading.", ...HISTORY_TEXT },
        { kind: "tool", text: "", name: "get_pipeline", title: "Reading the pipeline", summary: "3 nodes", is_error: false },
        // One stored row per provider round: a controller continuation split these.
        { kind: "assistant", text: "The plan is ready.", ...HISTORY_TEXT },
        { kind: "assistant", text: `NEEDS_INPUT: ${detail}`, ...HISTORY_TEXT },
        { kind: "outcome", outcome: { kind: "needs_input", detail, changes: [] } },
      ],
    })

    await useAssistantStore.getState().openSession("session-1", "main.py")

    const strip = (entries: TranscriptEntry[]) =>
      entries.map((entry) => (entry.kind === "activity" ? { ...entry, id: "" } : entry))
    expect(strip(useAssistantStore.getState().entries)).toEqual(strip(live))
  })

  it("renders a resumed apply with its titled row and change card as the live turn did", async () => {
    const live = await liveEntries([
      { type: "tool_started", id: "t1", name: "apply_graph_plan", title: "Applying the plan", summary: "{}" },
      { type: "tool_finished", id: "t1", name: "apply_graph_plan", title: "Applying 2 changes", is_error: false, summary: "ok" },
      { type: "change_applied", change: CHANGE },
      completed({ kind: "applied", detail: null, changes: ["plan-1"] }),
    ])
    vi.mocked(createAssistantSession).mockResolvedValue({
      sessionId: "session-1",
      sourceFile: "main.py",
      history: [
        { kind: "user", text: "go", ...HISTORY_TEXT },
        {
          kind: "tool",
          text: "",
          name: "apply_graph_plan",
          title: "Applying 2 changes",
          summary: "ok",
          is_error: false,
        },
        { kind: "change", change: CHANGE },
        { kind: "outcome", outcome: { kind: "applied", detail: null, changes: ["plan-1"] } },
      ],
    })

    await useAssistantStore.getState().openSession("session-1", "main.py")

    const strip = (entries: TranscriptEntry[]) =>
      entries.map((entry) => (entry.kind === "activity" ? { ...entry, id: "" } : entry))
    expect(strip(useAssistantStore.getState().entries)).toEqual(strip(live))
  })
})

describe("send gates", () => {
  it.each([
    ["dirty canvas", () => useGraphStore.setState({ dirty: true }), SEND_OPTS],
    [
      "unconfigured",
      () => useAssistantStore.setState({ status: { ...READY_STATUS, configured: false, reason: "r" } }),
      SEND_OPTS,
    ],
    [
      "mutations disabled",
      () =>
        useAssistantStore.setState({
          status: { ...READY_STATUS, mutations_enabled: false, mutations_reason: "git" },
        }),
      SEND_OPTS,
    ],
    ["inside a submodel", () => {}, { ...SEND_OPTS, isInsideSubmodel: true }],
    ["read-only document", () => {}, { ...SEND_OPTS, readOnly: true }],
    ["unknown status", () => useAssistantStore.setState({ status: "unknown" }), SEND_OPTS],
    ["a canvas without a source file", () => {}, { ...SEND_OPTS, currentSourceFile: null }],
  ])("refuses to send with %s", async (_label, prepare, opts) => {
    prepare()
    await useAssistantStore.getState().sendMessage("hi", opts)
    expect(createAssistantSession).not.toHaveBeenCalled()
    expect(streamAssistantMessage).not.toHaveBeenCalled()
    expect(useAssistantStore.getState().turnStatus).toBe("idle")
  })

  it("treats whitespace-only input as a no-op", async () => {
    await useAssistantStore.getState().sendMessage("   \n", SEND_OPTS)
    expect(streamAssistantMessage).not.toHaveBeenCalled()
    expect(useAssistantStore.getState().entries).toEqual([])
  })

  it("locks the composer while a turn is streaming", async () => {
    useAssistantStore.setState({ turnStatus: "streaming" })
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(streamAssistantMessage).not.toHaveBeenCalled()
  })

  it("refuses to send a chat from another pipeline's canvas and names its pipeline", async () => {
    scriptStream([completed()])
    useAssistantStore.setState({
      view: "chat",
      sessionId: "old-session",
      pipelineSource: "other.py",
      entries: [{ kind: "user", text: "old" }],
    })
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    expect(createAssistantSession).not.toHaveBeenCalled()
    expect(streamAssistantMessage).not.toHaveBeenCalled()
    const { sessionId, pipelineSource, entries, notice, turnStatus } = useAssistantStore.getState()
    expect(notice).toContain("other.py")
    expect(sessionId).toBe("old-session")
    expect(pipelineSource).toBe("other.py")
    expect(entries).toEqual([{ kind: "user", text: "old" }])
    expect(turnStatus).toBe("idle")
  })

  it("reuses the existing session for the same pipeline", async () => {
    scriptStream([completed()])
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(createAssistantSession).not.toHaveBeenCalled()
    expect(streamAssistantMessage).toHaveBeenCalledWith("session-1", "hi", "main.py", expect.anything())
  })

  it("carries the canvas selection, at most twenty nodes in canvas order", async () => {
    scriptStream([completed()])
    const nodes = Array.from({ length: 23 }, (_, index) => ({
      id: `n${index}`,
      position: { x: 0, y: 0 },
      data: {},
      selected: index !== 1,
    }))
    useGraphStore.setState({ nodes, dirty: false })
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    const options = vi.mocked(streamAssistantMessage).mock.calls[0][3]
    const expected = nodes.filter((node) => node.selected).slice(0, 20).map((node) => node.id)
    expect(options.context).toEqual({ selectedNodeIds: expected, previewErrorNodeId: null })
    expect(options.context.selectedNodeIds).not.toContain("n1")
  })

  it("binds a new chat to the source file the server echoes", async () => {
    scriptStream([completed()])
    vi.mocked(createAssistantSession).mockResolvedValue({
      sessionId: "nested",
      sourceFile: "pipelines/motor.py",
      history: [],
    })
    useAssistantStore.setState({ sessionsSource: "pipelines/motor.py" })
    await useAssistantStore.getState().sendMessage("hi", {
      ...SEND_OPTS,
      currentSourceFile: "pipelines/motor.py",
    })

    expect(createAssistantSession).toHaveBeenCalledWith(
      "pipelines/motor.py", null, expect.any(AbortSignal),
    )
    expect(useAssistantStore.getState().pipelineSource).toBe("pipelines/motor.py")
  })
})

describe("chat list navigation", () => {
  it("opens on the list and never resumes a conversation on send", async () => {
    // The panel used to look empty until a message was sent, then produced an
    // earlier transcript above it, because resume happened inside sendMessage.
    vi.mocked(createAssistantSession).mockResolvedValue({ sessionId: "fresh-9", sourceFile: "main.py", history: [] })
    scriptStream([completed()])

    expect(useAssistantStore.getState().view).toBe("list")
    useAssistantStore.getState().newChat()
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    expect(createAssistantSession).toHaveBeenCalledWith("main.py", null, expect.any(AbortSignal))
    expect(streamAssistantMessage).toHaveBeenCalledWith("fresh-9", "hi", "main.py", expect.anything())
    const { entries, sessionId, view } = useAssistantStore.getState()
    expect(sessionId).toBe("fresh-9")
    expect(view).toBe("chat")
    expect(entries[0]).toEqual({ kind: "user", text: "hi" })
  })

  it("hydrates a chosen conversation when it is opened, not when it is used", async () => {
    vi.mocked(createAssistantSession).mockResolvedValue({
      sessionId: "old-session",
      sourceFile: "main.py",
      history: [
        { kind: "user", text: "add nb_batch", name: "", title: "", summary: "", is_error: false },
        { kind: "assistant", text: "Adding it now.", name: "", title: "", summary: "", is_error: false },
        {
          kind: "tool",
          text: "",
          name: "get_node_schema",
          title: "Reading a node's columns",
          summary: "No node x",
          is_error: true,
        },
      ],
    })

    await useAssistantStore.getState().openSession("old-session", "main.py")

    expect(createAssistantSession).toHaveBeenCalledWith("main.py", "old-session")
    const { entries, sessionId, view } = useAssistantStore.getState()
    expect(sessionId).toBe("old-session")
    expect(view).toBe("chat")
    expect(entries[0]).toEqual({ kind: "user", text: "add nb_batch" })
    expect(entries[1]).toEqual({ kind: "assistant", text: "Adding it now.", streaming: false })
    expect(entries[2]).toMatchObject({
      kind: "activity",
      name: "get_node_schema",
      title: "Reading a node's columns",
      state: "error",
      summary: "No node x",
    })
  })

  it("loads the pipeline's conversations for the list", async () => {
    vi.mocked(listAssistantSessions).mockResolvedValue({
      sourceFile: "main.py",
      sessions: [{ sessionId: "a", title: "First", createdAt: 1, lastUsed: 2, messageCount: 4 }],
    })

    await useAssistantStore.getState().loadSessions("main.py")

    expect(listAssistantSessions).toHaveBeenCalledWith("main.py")
    const { sessions, sessionsStatus, sessionsSource } = useAssistantStore.getState()
    expect(sessionsStatus).toBe("ready")
    expect(sessionsSource).toBe("main.py")
    expect(sessions).toHaveLength(1)
    expect(sessions[0].title).toBe("First")
  })

  it("returns to the list and clears the active chat when the pipeline changes", async () => {
    useAssistantStore.setState({
      view: "chat",
      sessionId: "old-session",
      pipelineSource: "old.py",
      entries: [{ kind: "user", text: "old question" }],
    })

    await useAssistantStore.getState().loadSessions("new.py")

    const { view, sessionId, pipelineSource, entries } = useAssistantStore.getState()
    expect(view).toBe("list")
    expect(sessionId).toBeNull()
    expect(pipelineSource).toBeNull()
    expect(entries).toEqual([])
  })

  it("refreshes the canvas pipeline's list when a turn ends after a pipeline change", async () => {
    let finishTurn: () => void = () => {}
    vi.mocked(streamAssistantMessage).mockImplementation(async (_id, _text, _source, opts) => {
      await new Promise<void>((resolve) => { finishTurn = resolve })
      opts.onEvent(completed())
    })
    await useAssistantStore.getState().loadSessions("main.py")
    useAssistantStore.getState().newChat()

    const sending = useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    await vi.waitFor(() => expect(streamAssistantMessage).toHaveBeenCalled())
    // The canvas switches pipeline mid-turn; the panel lists the new one.
    await useAssistantStore.getState().loadSessions("other.py")
    expect(useAssistantStore.getState().sessionId).toBe("session-1")
    finishTurn()
    await sending

    await vi.waitFor(() => expect(useAssistantStore.getState().view).toBe("list"))
    expect(listAssistantSessions).toHaveBeenLastCalledWith("other.py")
    const { sessionId, pipelineSource, sessionsSource } = useAssistantStore.getState()
    expect(sessionId).toBeNull()
    expect(pipelineSource).toBeNull()
    expect(sessionsSource).toBe("other.py")
  })

  it("reports a list failure without discarding the current chat", async () => {
    useAssistantStore.setState({ view: "chat", sessionId: "live", pipelineSource: "main.py" })
    vi.mocked(listAssistantSessions).mockRejectedValue(new ApiError("HTTP 500", 500, "boom"))

    await useAssistantStore.getState().loadSessions("main.py")

    expect(useAssistantStore.getState().sessionsStatus).toBe("error")
    expect(useAssistantStore.getState().sessionId).toBe("live")
  })

  it("returns to the list and refreshes it", async () => {
    useAssistantStore.setState({ view: "chat" })

    useAssistantStore.getState().showSessionList("main.py")

    expect(useAssistantStore.getState().view).toBe("list")
    expect(listAssistantSessions).toHaveBeenCalled()
  })

  it("refuses to leave a chat or start a new one mid-turn", () => {
    useAssistantStore.setState({ view: "chat", turnStatus: "streaming", sessionId: "live" })

    useAssistantStore.getState().showSessionList("main.py")
    useAssistantStore.getState().newChat()

    expect(useAssistantStore.getState().view).toBe("chat")
    expect(useAssistantStore.getState().sessionId).toBe("live")
  })

  it("never leaves the previous conversation addressable while another opens", async () => {
    // The composer mounts as soon as the chat screen does. Holding the old id
    // across the await would post the next message into the conversation the
    // user just navigated away from, while the panel shows the new one.
    let resolveSecond: (value: AssistantSessionResult) => void = () => {}
    vi.mocked(createAssistantSession)
      .mockResolvedValueOnce({ sessionId: "first", sourceFile: "main.py", history: [] })
      .mockImplementationOnce(
        () => new Promise((resolve) => { resolveSecond = resolve }),
      )

    await useAssistantStore.getState().openSession("first", "main.py")
    expect(useAssistantStore.getState().sessionId).toBe("first")

    const opening = useAssistantStore.getState().openSession("second", "main.py")
    expect(useAssistantStore.getState().sessionId).toBeNull()

    resolveSecond({ sessionId: "second", sourceFile: "main.py", history: [] })
    await opening
    expect(useAssistantStore.getState().sessionId).toBe("second")
  })

  it("ignores a superseded open, so the chat shown is the one last chosen", async () => {
    let resolveSlow: (value: AssistantSessionResult) => void = () => {}
    vi.mocked(createAssistantSession)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSlow = resolve }))
      .mockResolvedValueOnce({
        sessionId: "quick",
        sourceFile: "main.py",
        history: [{ kind: "user", text: "quick chat", name: "", title: "", summary: "", is_error: false }],
      })

    const slow = useAssistantStore.getState().openSession("slow", "main.py")
    await useAssistantStore.getState().openSession("quick", "main.py")

    resolveSlow({ sessionId: "slow", sourceFile: "main.py", history: [] })
    await slow

    const { sessionId, entries } = useAssistantStore.getState()
    expect(sessionId).toBe("quick")
    expect(entries[0]).toEqual({ kind: "user", text: "quick chat" })
  })

  it("does not let an in-flight open overwrite a new message", async () => {
    let resolveOpen: (value: AssistantSessionResult) => void = () => {}
    vi.mocked(createAssistantSession)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOpen = resolve }))
      .mockResolvedValueOnce({ sessionId: "fresh", sourceFile: "main.py", history: [] })
    scriptStream([completed()])

    const opening = useAssistantStore.getState().openSession("old", "main.py")
    await useAssistantStore.getState().sendMessage("new question", SEND_OPTS)
    resolveOpen({ sessionId: "old", sourceFile: "main.py", history: [] })
    await opening

    const { sessionId, entries } = useAssistantStore.getState()
    expect(sessionId).toBe("fresh")
    expect(entries[0]).toEqual({ kind: "user", text: "new question" })
  })

  it.each([
    ["New chat", () => useAssistantStore.getState().newChat()],
    ["going back to the list", () => useAssistantStore.getState().showSessionList("main.py")],
  ])("discards an in-flight open once %s supersedes it", async (_label, navigate) => {
    let resolveOpen: (value: AssistantSessionResult) => void = () => {}
    vi.mocked(createAssistantSession).mockImplementationOnce(
      () => new Promise((resolve) => { resolveOpen = resolve }),
    )

    const opening = useAssistantStore.getState().openSession("chosen", "main.py")
    navigate()
    resolveOpen({ sessionId: "chosen", sourceFile: "main.py", history: [] })
    await opening

    // Landing here would silently re-attach a conversation the user left.
    expect(useAssistantStore.getState().sessionId).toBeNull()
  })

  it("ignores a superseded list load", async () => {
    let resolveSlow: (value: AssistantSessionList) => void = () => {}
    vi.mocked(listAssistantSessions)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSlow = resolve }))
      .mockResolvedValueOnce({
        sourceFile: "main.py",
        sessions: [{ sessionId: "b", title: "Newer", createdAt: 1, lastUsed: 9, messageCount: 2 }],
      })

    const slow = useAssistantStore.getState().loadSessions("main.py")
    await useAssistantStore.getState().loadSessions("main.py")

    resolveSlow({ sourceFile: "main.py", sessions: [] })
    await slow

    const { sessions, sessionsStatus } = useAssistantStore.getState()
    expect(sessionsStatus).toBe("ready")
    expect(sessions.map((session) => session.sessionId)).toEqual(["b"])
  })
})

describe("send failures", () => {
  it("blocks with a notice when the status fetch previously failed", async () => {
    useAssistantStore.setState({ status: "error" })
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(streamAssistantMessage).not.toHaveBeenCalled()
    expect(useAssistantStore.getState().notice ?? "").toMatch(/status/i)
  })

  it("surfaces a session-create failure without starting a stream", async () => {
    vi.mocked(createAssistantSession).mockRejectedValue(
      new ApiError("HTTP 404", 404, "No pipeline was found"),
    )
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(streamAssistantMessage).not.toHaveBeenCalled()
    const { turnStatus, notice } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(notice ?? "").not.toBe("")
  })

  it("refreshes readiness after a session-create 400", async () => {
    vi.mocked(createAssistantSession).mockRejectedValue(
      new ApiError("HTTP 400", 400, "Missing API key"),
    )

    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    expect(streamAssistantMessage).not.toHaveBeenCalled()
    expect(getAssistantStatus).toHaveBeenCalledTimes(1)
    expect(useAssistantStore.getState().entries).toEqual([])
    expect(useAssistantStore.getState().notice).toBe("Missing API key")
  })

  it("renders a stale-session notice on 404", async () => {
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    vi.mocked(streamAssistantMessage).mockRejectedValue(
      new ApiError("Unknown assistant session", 404, "Unknown assistant session"),
    )
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    const { notice, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(notice ?? "").toMatch(/session|expired|restart/i)
  })

  it.each([
    [400, "Missing API key"],
    [404, "Unknown assistant session"],
    [409, "An assistant turn is already running"],
  ])("keeps the user entry and records one failed marker for send-time %i", async (status, detail) => {
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    vi.mocked(streamAssistantMessage).mockRejectedValue(new ApiError(`HTTP ${status}`, status, detail))

    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    const { entries } = useAssistantStore.getState()
    expect(entries).toContainEqual({ kind: "user", text: "hi" })
    expect(entries.some((entry) => entry.kind === "assistant" && entry.text === "")).toBe(false)
    expect(entries.filter((entry) => entry.kind === "marker")).toEqual([
      expect.objectContaining({ outcome: "failed" }),
    ])
  })

  it("renders the still-finishing notice on 409 without auto-retry", async () => {
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    vi.mocked(streamAssistantMessage).mockRejectedValue(
      new ApiError(
        "An assistant turn is already running",
        409,
        "An assistant turn is already running",
      ),
    )
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(streamAssistantMessage).toHaveBeenCalledTimes(1)
    expect(useAssistantStore.getState().notice ?? "").toMatch(/finishing|moment|running/i)
  })
})

describe("stop and new chat", () => {
  it("locks same-tick sends while session creation is pending", async () => {
    let resolveSession: ((result: AssistantSessionResult) => void) | undefined
    vi.mocked(createAssistantSession).mockImplementation(() => new Promise((resolve) => {
      resolveSession = resolve
    }))
    scriptStream([completed()])

    const first = useAssistantStore.getState().sendMessage("first", SEND_OPTS)
    const second = useAssistantStore.getState().sendMessage("second", SEND_OPTS)
    expect(createAssistantSession).toHaveBeenCalledTimes(1)
    expect(useAssistantStore.getState().turnStatus).toBe("streaming")

    resolveSession?.({ sessionId: "session-1", sourceFile: "main.py", history: [] })
    await Promise.all([first, second])
    expect(useAssistantStore.getState().entries).toContainEqual({ kind: "user", text: "first" })
    expect(useAssistantStore.getState().entries).not.toContainEqual({ kind: "user", text: "second" })
  })

  it("stop aborts pending session creation without speculative transcript entries", async () => {
    vi.mocked(createAssistantSession).mockImplementation((_sourceFile, _sessionId, signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))
      }),
    )

    const sending = useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    expect(useAssistantStore.getState().turnStatus).toBe("streaming")
    useAssistantStore.getState().stopTurn()
    await sending

    expect(useAssistantStore.getState().turnStatus).toBe("idle")
    expect(useAssistantStore.getState().entries).toEqual([])
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("stop aborts the in-flight turn and marks it stopped without a toast", async () => {
    vi.mocked(streamAssistantMessage).mockImplementation(
      (_id, _text, _source, opts) =>
        new Promise((_resolve, reject) => {
          opts.signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          )
        }),
    )
    const sending = useAssistantStore.getState().sendMessage("hi", SEND_OPTS)
    await vi.waitFor(() => {
      expect(useAssistantStore.getState().turnStatus).toBe("streaming")
    })
    useAssistantStore.getState().stopTurn()
    await sending

    const { entries, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(entries[entries.length - 1]).toMatchObject({ kind: "marker", outcome: "stopped" })
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("stopTurn while idle is a no-op", () => {
    useAssistantStore.getState().stopTurn()
    expect(useAssistantStore.getState().turnStatus).toBe("idle")
    expect(useAssistantStore.getState().entries).toEqual([])
  })

  it("newChat clears the conversation while idle", async () => {
    useAssistantStore.setState({
      sessionId: "session-1",
      pipelineSource: "main.py",
      entries: [{ kind: "user", text: "old" }],
    })
    useAssistantStore.getState().newChat()
    const { sessionId, pipelineSource, entries } = useAssistantStore.getState()
    expect(sessionId).toBeNull()
    expect(pipelineSource).toBeNull()
    expect(entries).toEqual([])
  })

  it("newChat is refused while streaming", () => {
    useAssistantStore.setState({
      turnStatus: "streaming",
      sessionId: "session-1",
      entries: [{ kind: "user", text: "old" }],
    })
    useAssistantStore.getState().newChat()
    expect(useAssistantStore.getState().sessionId).toBe("session-1")
    expect(useAssistantStore.getState().entries).toHaveLength(1)
  })
})

describe("send-time 400 handling", () => {
  it("renders the backend detail and refreshes readiness", async () => {
    useAssistantStore.setState({ sessionId: "session-1", pipelineSource: "main.py" })
    vi.mocked(streamAssistantMessage).mockRejectedValue(
      new ApiError(
        "HTTP 400",
        400,
        "Missing API key environment variable: ANTHROPIC_API_KEY.",
      ),
    )
    await useAssistantStore.getState().sendMessage("hi", SEND_OPTS)

    const { notice, turnStatus } = useAssistantStore.getState()
    expect(turnStatus).toBe("idle")
    expect(notice).toContain("ANTHROPIC_API_KEY")
    expect(getAssistantStatus).toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toEqual([])
  })
})
