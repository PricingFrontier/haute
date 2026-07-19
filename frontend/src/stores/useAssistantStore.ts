/** Store-owned state machine for the assistant transcript and active turn. */

import { create } from "zustand"

import { ApiError } from "../api/client"
import {
  createAssistantSession,
  getAssistantStatus,
  streamAssistantMessage,
  type AssistantHistoryEntry,
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
  refreshStatus: () => Promise<void>
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

/**
 * Remembered session ids, keyed per pipeline source, so a reload or server
 * restart resumes the conversation the backend persisted in `.haute/`.
 * localStorage can be unavailable (disabled storage); resume is best-effort
 * convenience, so those failures degrade to a fresh session, never a crash.
 */
const SESSION_KEY_PREFIX = "haute.assistant.session:"

function rememberedSession(sourceFile: string): string | null {
  try {
    return localStorage.getItem(SESSION_KEY_PREFIX + sourceFile)
  } catch {
    return null
  }
}

function rememberSession(sourceFile: string, sessionId: string): void {
  try {
    localStorage.setItem(SESSION_KEY_PREFIX + sourceFile, sessionId)
  } catch {
    // Resume is best-effort; the conversation still works without it.
  }
}

function forgetSession(sourceFile: string | null): void {
  if (sourceFile === null) return
  try {
    localStorage.removeItem(SESSION_KEY_PREFIX + sourceFile)
  } catch {
    // Nothing to forget if storage is unavailable.
  }
}

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
      turnStatus: "idle",
    }))
    return
  }

  if (error instanceof ApiError && error.status === 400) {
    // The backend names exactly what is missing (spec: render its detail
    // verbatim) and readiness is re-fetched so the composer gate shows the
    // current reason rather than a stale one.
    set((state) => ({
      entries: closeAssistant(state.entries),
      turnStatus: "idle",
      notice: error.detail ?? "Assistant is not configured.",
    }))
    void useAssistantStore.getState().refreshStatus()
    return
  }

  if (error instanceof ApiError && error.status === 404) {
    set((state) => ({
      entries: closeAssistant(state.entries),
      turnStatus: "idle",
      notice: "The assistant session expired after a server restart. Start a new chat.",
    }))
    return
  }

  if (error instanceof ApiError && error.status === 409) {
    set((state) => ({
      entries: closeAssistant(state.entries),
      turnStatus: "idle",
      notice: "The assistant is still finishing its last edit; try again in a moment.",
    }))
    return
  }

  set((state) => ({
    entries: appendMarker(state.entries, "interrupted"),
    turnStatus: "idle",
  }))
  useToastStore.getState().addToast("error", "The assistant turn was interrupted.")
}

const useAssistantStore = create<AssistantStoreState>()((set, get) => ({
  sessionId: null,
  pipelineSource: null,
  entries: [],
  turnStatus: "idle",
  status: "unknown",
  notice: null,

  refreshStatus: async () => {
    try {
      const status = await getAssistantStatus()
      set({ status })
    } catch {
      set({ status: "error" })
    }
  },

  sendMessage: async (text, options) => {
    const current = get()
    if (current.turnStatus !== "idle") return

    if (current.status === "unknown") {
      set({ notice: "Assistant status is unavailable. Refresh its status before sending." })
      return
    }
    if (current.status === "error") {
      set({ notice: "Assistant status could not be loaded. Try again." })
      return
    }
    if (!current.status.configured) {
      set({ notice: current.status.reason ?? "Assistant is not configured." })
      return
    }
    if (!current.status.mutations_enabled) {
      set({ notice: current.status.mutations_reason ?? "Assistant mutations are disabled." })
      return
    }
    if (options.isInsideSubmodel) {
      set({ notice: "Assistant edits are available from the top-level pipeline only." })
      return
    }
    if (!text.trim()) return

    if (
      (current.pipelineSource !== null || current.sessionId !== null) &&
      current.pipelineSource !== options.currentSourceFile
    ) {
      set({ sessionId: null, pipelineSource: null, entries: [] })
    }

    if (useGraphStore.getState().dirty) {
      set({ notice: "Save or discard the current canvas changes before using Assistant." })
      return
    }

    let sessionId = get().sessionId
    try {
      if (sessionId === null) {
        const result = await createAssistantSession(
          null,
          rememberedSession(options.currentSourceFile),
        )
        sessionId = result.sessionId
        rememberSession(options.currentSourceFile, sessionId)
        set((state) => ({
          sessionId,
          pipelineSource: options.currentSourceFile,
          // A non-empty history means the backend resumed the remembered
          // session: rehydrate the persisted transcript before this turn.
          entries: result.history.length > 0 ? hydrateEntries(result.history) : state.entries,
        }))
      }
    } catch (error) {
      rejectTurn(set, error)
      return
    }

    const controller = new AbortController()
    activeController = controller
    set((state) => ({
      entries: [
        ...state.entries,
        { kind: "user", text },
        { kind: "assistant", text: "", streaming: true },
      ],
      turnStatus: "streaming",
      notice: null,
    }))

    let terminalSeen = false
    try {
      await streamAssistantMessage(sessionId, text, {
        signal: controller.signal,
        onEvent: (event) => {
          if (terminalSeen) return

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
              terminalSeen = true
              set((state) => ({
                entries: appendMarker(state.entries, "completed"),
                turnStatus: "idle",
              }))
              break
            case "failed":
              terminalSeen = true
              set((state) => ({
                entries: appendMarker(state.entries, "failed", event.message),
                turnStatus: "idle",
              }))
              useToastStore.getState().addToast("error", event.message)
              break
            case "cancelled":
              terminalSeen = true
              set((state) => ({
                entries: appendMarker(state.entries, "stopped"),
                turnStatus: "idle",
              }))
              break
          }
        },
      })

      if (!terminalSeen) {
        set((state) => ({
          entries: appendMarker(state.entries, "interrupted"),
          turnStatus: "idle",
        }))
      }
    } catch (error) {
      if (!terminalSeen) rejectTurn(set, error)
    } finally {
      if (activeController === controller) activeController = null
      set({ turnStatus: "idle" })
    }
  },

  stopTurn: () => {
    if (get().turnStatus !== "streaming") return
    activeController?.abort()
  },

  newChat: () => {
    if (get().turnStatus !== "idle") return
    forgetSession(get().pipelineSource)
    set({ sessionId: null, pipelineSource: null, entries: [], notice: null })
  },
}))

export default useAssistantStore
