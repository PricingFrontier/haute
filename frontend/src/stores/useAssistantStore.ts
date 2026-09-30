/** Store-owned state machine for the assistant transcript and active turn. */

import { create } from "zustand"

import { ApiError } from "../api/client"
import {
  createAssistantSession,
  getAssistantStatus,
  listAssistantSessions,
  MAX_CONTEXT_SELECTION,
  streamAssistantMessage,
  type AssistantHistoryEntry,
  type AssistantMessageContext,
  type AssistantSessionSummary,
  type AssistantStatus,
  type AssistantStreamEvent,
  type AssistantTurnOutcome,
} from "../api/assistant"
import useGraphStore from "./useGraphStore"
import useToastStore from "./useToastStore"

export type TranscriptEntry =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming: boolean }
  | {
      kind: "activity"
      id: string
      name: string
      state: "running" | "ok" | "error"
      summary: string
    }
  | {
      kind: "marker"
      outcome: "failed" | "stopped" | "interrupted"
      detail?: string
    }
  /** How a completed turn ended; `needs_input` and `blocked` render as cards. */
  | { kind: "outcome"; outcome: AssistantTurnOutcome }

/**
 * What the canvas adds to a message: the ids of its selected nodes, the first
 * `MAX_CONTEXT_SELECTION` in canvas order. No preview error is shared yet.
 */
export function canvasMessageContext(): AssistantMessageContext {
  const selectedNodeIds = useGraphStore
    .getState()
    .nodes.filter((node) => node.selected)
    .map((node) => node.id)
    .slice(0, MAX_CONTEXT_SELECTION)
  return { selectedNodeIds, previewErrorNodeId: null }
}

/** The question card's one-click reply: hand the choice back to the assistant. */
export const CHOOSE_FOR_ME_REPLY = "You choose, and tell me what you picked."

export interface SendMessageOptions {
  isInsideSubmodel: boolean
  /** The canvas document's source file; `null` while the canvas has none. */
  currentSourceFile: string | null
  readOnly: boolean
}

export interface AssistantStoreState {
  sessionId: string | null
  /** The source file the server bound the open chat to. */
  pipelineSource: string | null
  entries: TranscriptEntry[]
  turnStatus: "idle" | "streaming"
  status: AssistantStatus | "unknown" | "error"
  /**
   * The detail of a status fetch refused with 400: a malformed `haute.toml` or
   * invalid assistant table, named by the backend. `null` for any other failure.
   */
  statusErrorDetail: string | null
  notice: string | null
  /** Which screen the panel shows: the conversation list, or one chat. */
  view: "list" | "chat"
  sessions: AssistantSessionSummary[]
  sessionsStatus: "unknown" | "loading" | "ready" | "error"
  /** The source file the panel last listed; a finished turn refreshes this list. */
  sessionsSource: string | null
  refreshStatus: () => Promise<void>
  loadSessions: (sourceFile: string | null) => Promise<void>
  openSession: (sessionId: string, sourceFile: string) => Promise<void>
  showSessionList: (sourceFile: string | null) => void
  sendMessage: (text: string, options: SendMessageOptions) => Promise<void>
  stopTurn: () => void
  newChat: () => void
}

type SetAssistantState = (
  update:
    | Partial<AssistantStoreState>
    | ((state: AssistantStoreState) => Partial<AssistantStoreState>),
) => void

let activeController: AbortController | null = null

/*
 * Monotonic tickets for the two navigation fetches. Both are re-issued faster
 * than they resolve — a second row click, a pipeline change, a turn finishing —
 * and neither response identifies which request it answers, so only the latest
 * ticket is allowed to write. Module scope, like `activeController`: the panel
 * can unmount and remount while a request is in flight.
 */
let openGeneration = 0
let listGeneration = 0

export interface AssistantSendGate {
  status: AssistantStatus | "unknown" | "error"
  isInsideSubmodel: boolean
  dirty: boolean
  readOnly: boolean
  /** The canvas document's source file; `null` while the canvas has none. */
  sourceFile: string | null
  /** The source file the open chat is bound to; `null` before its first send. */
  chatSource: string | null
}

export function assistantSendDisabledReason({
  status,
  isInsideSubmodel,
  dirty,
  readOnly,
  sourceFile,
  chatSource,
}: AssistantSendGate): string | null {
  if (status === "unknown") return "Assistant status is unavailable. Refresh its status before sending."
  if (status === "error") return "Assistant status could not be loaded. Try again."
  if (!status.configured) return status.reason ?? "Assistant is not configured."
  if (!status.mutations_enabled) return status.mutations_reason ?? "Assistant mutations are disabled."
  if (sourceFile === null) return "Save this pipeline to a file before using Assistant."
  // A chat edits only the pipeline it was started on; sending it from another
  // pipeline's canvas would change a file the analyst is not looking at.
  if (chatSource !== null && chatSource !== sourceFile) {
    return `This chat belongs to ${chatSource}. Open that pipeline to continue it, or start a new chat.`
  }
  if (isInsideSubmodel) return "Assistant edits are available from the top-level pipeline only."
  if (dirty) return "Save or discard the current canvas changes before using Assistant."
  if (readOnly) return "Resolve the current pipeline recovery issues before using Assistant."
  return null
}

/*
 * There is deliberately no client-remembered "last session" here. The backend
 * list is the single record of which conversations exist, and a localStorage id
 * that silently resumed on the next send is exactly what made the panel open
 * blank and then produce an earlier transcript mid-conversation.
 */

/**
 * Map a resumed session's backend history to the entries the live turn showed.
 *
 * The backend stores one assistant row per provider round, while the live
 * stream joins deltas until a tool row interrupts them, so adjacent assistant
 * rows are joined the same way. Each outcome row settles its turn through the
 * same `settleOutcome` the live `completed` event uses.
 */
function hydrateEntries(history: AssistantHistoryEntry[]): TranscriptEntry[] {
  let entries: TranscriptEntry[] = []
  history.forEach((entry, index) => {
    if (entry.kind === "outcome") {
      entries = settleOutcome(entries, entry.outcome)
      return
    }
    if (entry.kind === "user") {
      entries = [...entries, { kind: "user", text: entry.text }]
      return
    }
    if (entry.kind === "assistant") {
      const last = entries[entries.length - 1]
      entries = last?.kind === "assistant"
        ? [...entries.slice(0, -1), { ...last, text: last.text + entry.text }]
        : [...entries, { kind: "assistant", text: entry.text, streaming: false }]
      return
    }
    entries = [
      ...entries,
      {
        kind: "activity",
        id: `history-${index}`,
        name: entry.name,
        state: entry.is_error ? "error" : "ok",
        summary: entry.summary,
      },
    ]
  })
  return entries
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null &&
    (error as { name?: unknown }).name === "AbortError"
}

function lastIndexMatching(
  entries: TranscriptEntry[],
  predicate: (entry: TranscriptEntry) => boolean,
): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    if (predicate(entries[index])) return index
  }
  return -1
}

/**
 * Settle the streaming assistant segment: it stops streaming, and a segment no
 * text reached (the placeholder opened at send time) is removed.
 */
function closeAssistant(entries: TranscriptEntry[]): TranscriptEntry[] {
  const assistantIndex = lastIndexMatching(entries,
    (entry) => entry.kind === "assistant" && entry.streaming,
  )
  if (assistantIndex < 0) return entries
  const segment = entries[assistantIndex]
  if (segment.kind === "assistant" && segment.text === "") {
    return entries.filter((_, index) => index !== assistantIndex)
  }

  return entries.map((entry, index) =>
    index === assistantIndex && entry.kind === "assistant"
      ? { ...entry, streaming: false }
      : entry,
  )
}

function removeStreamingAssistant(entries: TranscriptEntry[]): TranscriptEntry[] {
  const assistantIndex = lastIndexMatching(entries,
    (entry) => entry.kind === "assistant" && entry.streaming,
  )
  return assistantIndex < 0 ? entries : entries.filter((_, index) => index !== assistantIndex)
}

function appendMarker(
  entries: TranscriptEntry[],
  outcome: "failed" | "stopped" | "interrupted",
  detail?: string,
): TranscriptEntry[] {
  const marker: TranscriptEntry = detail === undefined
    ? { kind: "marker", outcome }
    : { kind: "marker", outcome, detail }
  return [...closeAssistant(entries), marker]
}

function toolStartedEntry(event: Extract<AssistantStreamEvent, { type: "tool_started" }>): TranscriptEntry {
  return {
    kind: "activity",
    id: event.id,
    name: event.name,
    state: "running",
    summary: event.summary,
  }
}

/**
 * Append streamed text in stream order: it extends the last entry when that is
 * the streaming segment, and otherwise opens a new segment below whatever the
 * turn showed last, so prose that follows a tool row renders after it.
 */
function appendAssistantText(entries: TranscriptEntry[], text: string): TranscriptEntry[] {
  const last = entries[entries.length - 1]
  if (last?.kind === "assistant" && last.streaming) {
    return [...entries.slice(0, -1), { ...last, text: last.text + text }]
  }
  return [...entries, { kind: "assistant", text, streaming: true }]
}

/** Append a tool or canvas row after the text streamed before it. */
function appendActivity(entries: TranscriptEntry[], entry: TranscriptEntry): TranscriptEntry[] {
  return [...closeAssistant(entries), entry]
}

const OUTCOME_MARKERS = { needs_input: "NEEDS_INPUT:", blocked: "BLOCKED:" } as const

/**
 * Remove the model's `NEEDS_INPUT:`/`BLOCKED:` text, which the outcome card
 * shows instead. By the backend contract the last assistant segment ends with
 * the marker followed by the outcome's detail; anything else is contract drift.
 */
function withoutOutcomeText(
  entries: TranscriptEntry[],
  marker: string,
  detail: string,
): TranscriptEntry[] {
  const index = lastIndexMatching(entries, (entry) => entry.kind === "assistant")
  const segment = index < 0 ? undefined : entries[index]
  const text = segment?.kind === "assistant" ? segment.text.trimEnd() : ""
  const body = text.endsWith(detail)
    ? text.slice(0, text.length - detail.length).trimEnd()
    : null
  if (segment?.kind !== "assistant" || body === null || !body.endsWith(marker)) {
    throw new Error(
      `Assistant contract violation: the turn's reply does not end with its ${marker} outcome.`,
    )
  }
  const rest = body.slice(0, body.length - marker.length).trimEnd()
  return rest
    ? entries.map((entry, entryIndex) => (entryIndex === index ? { ...segment, text: rest } : entry))
    : entries.filter((_, entryIndex) => entryIndex !== index)
}

/**
 * Close a completed turn with its outcome. Shared by the live `completed` event
 * and a resumed chat's history so both render the same transcript.
 */
export function settleOutcome(
  entries: TranscriptEntry[],
  outcome: AssistantTurnOutcome,
): TranscriptEntry[] {
  let settled = closeAssistant(entries)
  if (outcome.kind === "needs_input" || outcome.kind === "blocked") {
    settled = withoutOutcomeText(settled, OUTCOME_MARKERS[outcome.kind], outcome.detail)
  }
  return [...settled, { kind: "outcome", outcome }]
}

function settleTool(
  entries: TranscriptEntry[],
  event: Extract<AssistantStreamEvent, { type: "tool_finished" }>,
): TranscriptEntry[] {
  const activityIndex = lastIndexMatching(entries,
    (entry) => entry.kind === "activity" && entry.id === event.id,
  )
  if (activityIndex < 0) return entries

  return entries.map((entry, index) =>
    index === activityIndex && entry.kind === "activity"
      ? {
          ...entry,
          name: event.name,
          state: event.is_error ? "error" : "ok",
          summary: event.summary,
        }
      : entry,
  )
}

function rejectTurn(set: SetAssistantState, error: unknown): void {
  if (isAbortError(error)) {
    set((state) => ({
      entries: appendMarker(state.entries, "stopped"),
    }))
    return
  }

  if (error instanceof ApiError && error.status === 400) {
    // The backend names exactly what is missing (spec: render its detail
    // verbatim) and readiness is re-fetched so the composer gate shows the
    // current reason rather than a stale one.
    set((state) => ({
      entries: appendMarker(removeStreamingAssistant(state.entries), "failed", error.detail),
      notice: error.detail ?? "Assistant is not configured.",
    }))
    void useAssistantStore.getState().refreshStatus()
    return
  }

  if (error instanceof ApiError && error.status === 404) {
    set((state) => ({
      entries: appendMarker(removeStreamingAssistant(state.entries), "failed", error.detail),
      notice: "The assistant session expired after a server restart. Start a new chat.",
    }))
    return
  }

  if (error instanceof ApiError && error.status === 409) {
    let notice = error.detail ?? "The assistant request conflicted with current server state."
    if (error.detail === "An assistant turn is already running") {
      notice = "The assistant is still finishing its last edit; try again in a moment."
    }
    set((state) => ({
      entries: appendMarker(removeStreamingAssistant(state.entries), "failed", error.detail),
      notice,
    }))
    return
  }

  set((state) => ({
    entries: appendMarker(state.entries, "interrupted"),
  }))
  useToastStore.getState().addToast("error", "The assistant turn was interrupted.")
}

function rejectSessionCreation(set: SetAssistantState, error: unknown): void {
  if (isAbortError(error)) return
  if (error instanceof ApiError && error.status === 400) {
    set({ notice: error.detail ?? "Assistant is not configured." })
    void useAssistantStore.getState().refreshStatus()
    return
  }
  const detail = error instanceof ApiError ? error.detail : null
  set({ notice: detail ?? "The assistant session could not be started." })
}

const useAssistantStore = create<AssistantStoreState>()((set, get) => ({
  sessionId: null,
  pipelineSource: null,
  entries: [],
  turnStatus: "idle",
  status: "unknown",
  statusErrorDetail: null,
  notice: null,
  view: "list",
  sessions: [],
  sessionsStatus: "unknown",
  sessionsSource: null,

  refreshStatus: async () => {
    try {
      const status = await getAssistantStatus()
      set({ status, statusErrorDetail: null })
    } catch (error) {
      // A 400 is a configuration the backend could not read; its detail names
      // what to fix, and asking again cannot succeed until the file changes.
      const detail = error instanceof ApiError && error.status === 400
        ? error.detail ?? null
        : null
      set({ status: "error", statusErrorDetail: detail })
    }
  },

  loadSessions: async (sourceFile) => {
    // The list shows only the canvas pipeline's chats; with no source file
    // there is nothing to list.
    const current = get()
    if (
      current.turnStatus === "idle" &&
      (current.pipelineSource !== null || current.sessionId !== null) &&
      current.pipelineSource !== sourceFile
    ) {
      openGeneration += 1
      set({
        view: "list",
        sessionId: null,
        pipelineSource: null,
        entries: [],
        notice: null,
      })
    }
    if (sourceFile === null) {
      listGeneration += 1
      set({ sessions: [], sessionsStatus: "ready", sessionsSource: null })
      return
    }
    const generation = (listGeneration += 1)
    set({ sessionsStatus: "loading", sessionsSource: sourceFile })
    try {
      const { sessions } = await listAssistantSessions(sourceFile)
      if (generation !== listGeneration) return
      set({ sessions, sessionsStatus: "ready" })
    } catch {
      if (generation !== listGeneration) return
      set({ sessionsStatus: "error" })
    }
  },

  openSession: async (sessionId, sourceFile) => {
    if (get().turnStatus !== "idle") return
    // Resolve the transcript on open, not on send. Resuming lazily inside
    // `sendMessage` is what made the panel look empty until a message was
    // sent, and then made an earlier conversation appear above it.
    //
    // The chat screen mounts the composer immediately, so the conversation
    // being left has to stop being addressable now rather than when its
    // replacement arrives — otherwise a message sent during the fetch lands in
    // the chat the user just navigated away from.
    const generation = (openGeneration += 1)
    set({ view: "chat", entries: [], notice: null, sessionId: null, pipelineSource: null })
    try {
      const result = await createAssistantSession(sourceFile, sessionId)
      // A second choice while this one was in flight owns the screen; letting
      // a slower earlier response land would show a chat nobody picked.
      if (generation !== openGeneration) return
      set({
        sessionId: result.sessionId,
        pipelineSource: result.sourceFile,
        entries: hydrateEntries(result.history),
      })
    } catch (error) {
      if (generation !== openGeneration) return
      rejectSessionCreation(set, error)
      set({ view: "list" })
    }
  },

  showSessionList: (sourceFile) => {
    if (get().turnStatus !== "idle") return
    // Any navigation supersedes an open still in flight, which would otherwise
    // land afterwards and re-attach the conversation just left.
    openGeneration += 1
    set({ view: "list", notice: null })
    void get().loadSessions(sourceFile)
  },

  sendMessage: async (text, options) => {
    const current = get()
    if (current.turnStatus !== "idle") return

    const disabledReason = assistantSendDisabledReason({
      status: current.status,
      isInsideSubmodel: options.isInsideSubmodel,
      dirty: useGraphStore.getState().dirty,
      readOnly: options.readOnly,
      sourceFile: options.currentSourceFile,
      chatSource: current.pipelineSource,
    })
    const sourceFile = options.currentSourceFile
    if (disabledReason !== null || sourceFile === null) {
      set({ notice: disabledReason })
      return
    }
    if (!text.trim()) return
    // The selection as the analyst sends, before any await can change it.
    const context = canvasMessageContext()

    // A message sent from the immediately mounted composer becomes the active
    // chat. A slower transcript-open response must not replace it afterwards.
    openGeneration += 1

    const controller = new AbortController()
    activeController = controller
    set({ turnStatus: "streaming", notice: null })
    let sessionId = get().sessionId
    try {
      if (sessionId === null) {
        // Always a fresh session. The panel resolves an existing conversation
        // through `openSession`, so resuming a remembered id here would drop
        // someone else's transcript into a chat the user opened as new.
        const result = await createAssistantSession(sourceFile, null, controller.signal)
        sessionId = result.sessionId
        set({ sessionId, pipelineSource: result.sourceFile })
      }
    } catch (error) {
      rejectSessionCreation(set, error)
      if (activeController === controller) {
        activeController = null
        set({ turnStatus: "idle" })
      }
      return
    }

    set((state) => ({
      entries: [
        ...state.entries,
        { kind: "user", text },
        { kind: "assistant", text: "", streaming: true },
      ],
      turnStatus: "streaming",
      notice: null,
    }))

    type TerminalEvent = Extract<AssistantStreamEvent, {
      type: "completed" | "failed" | "cancelled"
    }>
    const terminal = { current: null as TerminalEvent | null }
    try {
      await streamAssistantMessage(sessionId, text, sourceFile, {
        signal: controller.signal,
        context,
        onEvent: (event) => {
          if (terminal.current !== null) {
            throw new Error("Assistant stream contract violation: received an event after a terminal event.")
          }

          switch (event.type) {
            case "text_delta":
              set((state) => ({ entries: appendAssistantText(state.entries, event.text) }))
              break
            case "tool_started":
              set((state) => ({ entries: appendActivity(state.entries, toolStartedEntry(event)) }))
              break
            case "tool_finished":
              set((state) => ({ entries: settleTool(state.entries, event) }))
              break
            case "graph_updated":
              set((state) => ({
                entries: appendActivity(state.entries, {
                  kind: "activity",
                  id: `graph-${event.fingerprint}`,
                  name: "graph_updated",
                  state: "ok",
                  summary: "Canvas updated",
                }),
              }))
              break
            case "completed":
              terminal.current = event
              break
            case "failed":
              terminal.current = event
              break
            case "cancelled":
              terminal.current = event
              break
          }
        },
      })

      const terminalEvent = terminal.current
      if (terminalEvent === null) {
        set((state) => ({ entries: appendMarker(state.entries, "interrupted") }))
      } else if (terminalEvent.type === "completed") {
        set((state) => ({ entries: settleOutcome(state.entries, terminalEvent.outcome) }))
      } else if (terminalEvent.type === "failed") {
        set((state) => ({ entries: appendMarker(state.entries, "failed", terminalEvent.message) }))
        useToastStore.getState().addToast("error", terminalEvent.message)
      } else {
        set((state) => ({ entries: appendMarker(state.entries, "stopped") }))
      }
    } catch (error) {
      rejectTurn(set, error)
    } finally {
      if (activeController === controller) {
        activeController = null
        set({ turnStatus: "idle" })
        // The turn gave this conversation its first message, and therefore its
        // title and its place in the list. Refresh so going back shows it —
        // for the pipeline the canvas shows now, which may have changed mid-turn.
        // With no listed pipeline there is no list to refresh.
        const listedSource = get().sessionsSource
        if (listedSource !== null) void get().loadSessions(listedSource)
      }
    }
  },

  stopTurn: () => {
    if (get().turnStatus !== "streaming") return
    activeController?.abort()
  },

  newChat: () => {
    if (get().turnStatus !== "idle") return
    // No session is created here. `create` persists immediately, so an
    // abandoned new chat would sit in the list as an empty untitled row; the
    // backend session is minted by the first send instead. Superseding any
    // open still in flight is what keeps this chat empty: its response would
    // otherwise arrive and fill the new chat with an old transcript.
    openGeneration += 1
    set({ sessionId: null, pipelineSource: null, entries: [], notice: null, view: "chat" })
  },
}))

export default useAssistantStore
