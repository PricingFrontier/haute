/** Store-owned state machine for the assistant transcript and active turn. */

import { create } from "zustand"

import { ApiError } from "../api/client"
import {
  createAssistantSession,
  getAssistantStatus,
  listAssistantSessions,
  streamAssistantMessage,
  type AssistantHistoryEntry,
  type AssistantSessionSummary,
  type AssistantStatus,
  type AssistantStreamEvent,
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
      outcome: "completed" | "failed" | "stopped" | "interrupted"
      detail?: string
    }

export interface SendMessageOptions {
  isInsideSubmodel: boolean
  currentSourceFile: string
}

export interface AssistantStoreState {
  sessionId: string | null
  pipelineSource: string | null
  entries: TranscriptEntry[]
  turnStatus: "idle" | "streaming"
  status: AssistantStatus | "unknown" | "error"
  notice: string | null
  /** Which screen the panel shows: the conversation list, or one chat. */
  view: "list" | "chat"
  sessions: AssistantSessionSummary[]
  sessionsStatus: "unknown" | "loading" | "ready" | "error"
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

export function assistantSendDisabledReason(
  status: AssistantStatus | "unknown" | "error",
  isInsideSubmodel: boolean,
  dirty: boolean,
): string | null {
  if (status === "unknown") return "Assistant status is unavailable. Refresh its status before sending."
  if (status === "error") return "Assistant status could not be loaded. Try again."
  if (!status.configured) return status.reason ?? "Assistant is not configured."
  if (!status.mutations_enabled) return status.mutations_reason ?? "Assistant mutations are disabled."
  if (isInsideSubmodel) return "Assistant edits are available from the top-level pipeline only."
  if (dirty) return "Save or discard the current canvas changes before using Assistant."
  return null
}

/*
 * There is deliberately no client-remembered "last session" here. The backend
 * list is the single record of which conversations exist, and a localStorage id
 * that silently resumed on the next send is exactly what made the panel open
 * blank and then produce an earlier transcript mid-conversation.
 */

/** Map a resumed session's backend history to settled transcript entries. */
function hydrateEntries(history: AssistantHistoryEntry[]): TranscriptEntry[] {
  return history.map((entry, index): TranscriptEntry => {
    if (entry.kind === "user") return { kind: "user", text: entry.text }
    if (entry.kind === "assistant") {
      return { kind: "assistant", text: entry.text, streaming: false }
    }
    return {
      kind: "activity",
      id: `history-${index}`,
      name: entry.name,
      state: entry.is_error ? "error" : "ok",
      summary: entry.summary,
    }
  })
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

function closeAssistant(entries: TranscriptEntry[]): TranscriptEntry[] {
  const assistantIndex = lastIndexMatching(entries,
    (entry) => entry.kind === "assistant" && entry.streaming,
  )
  if (assistantIndex < 0) return entries

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
  outcome: "completed" | "failed" | "stopped" | "interrupted",
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

function appendAssistantText(entries: TranscriptEntry[], text: string): TranscriptEntry[] {
  const assistantIndex = lastIndexMatching(entries,
    (entry) => entry.kind === "assistant" && entry.streaming,
  )
  if (assistantIndex < 0) return entries

  return entries.map((entry, index) =>
    index === assistantIndex && entry.kind === "assistant"
      ? { ...entry, text: entry.text + text }
      : entry,
  )
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
  notice: null,
  view: "list",
  sessions: [],
  sessionsStatus: "unknown",

  refreshStatus: async () => {
    try {
      const status = await getAssistantStatus()
      set({ status })
    } catch {
      set({ status: "error" })
    }
  },

  loadSessions: async (sourceFile) => {
    if (sourceFile === null) {
      set({ sessions: [], sessionsStatus: "ready" })
      return
    }
    set({ sessionsStatus: "loading" })
    try {
      const sessions = await listAssistantSessions(null)
      set({ sessions, sessionsStatus: "ready" })
    } catch {
      set({ sessionsStatus: "error" })
    }
  },

  openSession: async (sessionId, sourceFile) => {
    if (get().turnStatus !== "idle") return
    // Resolve the transcript on open, not on send. Resuming lazily inside
    // `sendMessage` is what made the panel look empty until a message was
    // sent, and then made an earlier conversation appear above it.
    set({ view: "chat", entries: [], notice: null })
    try {
      const result = await createAssistantSession(null, sessionId)
      set({
        sessionId: result.sessionId,
        pipelineSource: sourceFile,
        entries: hydrateEntries(result.history),
      })
    } catch (error) {
      rejectSessionCreation(set, error)
      set({ view: "list" })
    }
  },

  showSessionList: (sourceFile) => {
    if (get().turnStatus !== "idle") return
    set({ view: "list", notice: null })
    void get().loadSessions(sourceFile)
  },

  sendMessage: async (text, options) => {
    const current = get()
    if (current.turnStatus !== "idle") return

    const disabledReason = assistantSendDisabledReason(
      current.status,
      options.isInsideSubmodel,
      useGraphStore.getState().dirty,
    )
    if (disabledReason !== null) {
      set({ notice: disabledReason })
      return
    }
    if (!text.trim()) return

    if (
      (current.pipelineSource !== null || current.sessionId !== null) &&
      current.pipelineSource !== options.currentSourceFile
    ) {
      set({ sessionId: null, pipelineSource: null, entries: [] })
    }

    const controller = new AbortController()
    activeController = controller
    set({ turnStatus: "streaming", notice: null })
    let sessionId = get().sessionId
    try {
      if (sessionId === null) {
        // Always a fresh session. The panel resolves an existing conversation
        // through `openSession`, so resuming a remembered id here would drop
        // someone else's transcript into a chat the user opened as new.
        const result = await createAssistantSession(null, null, controller.signal)
        sessionId = result.sessionId
        set({ sessionId, pipelineSource: options.currentSourceFile })
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
      await streamAssistantMessage(sessionId, text, {
        signal: controller.signal,
        onEvent: (event) => {
          if (terminal.current !== null) {
            throw new Error("Assistant stream contract violation: received an event after a terminal event.")
          }

          switch (event.type) {
            case "text_delta":
              set((state) => ({ entries: appendAssistantText(state.entries, event.text) }))
              break
            case "tool_started":
              set((state) => ({ entries: [...state.entries, toolStartedEntry(event)] }))
              break
            case "tool_finished":
              set((state) => ({ entries: settleTool(state.entries, event) }))
              break
            case "graph_updated":
              set((state) => ({
                entries: [
                  ...state.entries,
                  {
                    kind: "activity",
                    id: `graph-${event.fingerprint}`,
                    name: "graph_updated",
                    state: "ok",
                    summary: "Canvas updated",
                  },
                ],
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
        set((state) => ({
          entries: appendMarker(state.entries, "completed"),
        }))
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
        // title and its place in the list. Refresh so going back shows it.
        void get().loadSessions(options.currentSourceFile)
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
    // backend session is minted by the first send instead.
    set({ sessionId: null, pipelineSource: null, entries: [], notice: null, view: "chat" })
  },
}))

export default useAssistantStore
