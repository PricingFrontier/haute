/** Assistant endpoints kept in the lazy panel's split API chunk. */

import { post, postRawStream, request } from "./client"
import type {
  AssistantStreamEventPayload,
  AssistantTranscriptEntry,
  AssistantTurnOutcome as GeneratedAssistantTurnOutcome,
} from "../generated/api-contracts.generated"
import {
  validateAssistantSessionListResponse,
  validateAssistantSessionResponse,
  validateAssistantStatusResponse,
  validateAssistantStreamEventPayload,
  validateAssistantUndoResponse,
} from "../generated/api-contracts.assistant.validators.mjs"
import { expectGeneratedContract } from "../types/generatedContractValidation"

// The generated validators own every assistant response's structure (the server's
// models check the turn outcome, the transcript entry and the build plan); the panel
// reads them in the shapes below.
export type {
  AssistantBuildPlan,
  AssistantBuildPlanChange,
  AssistantBuildPlanItem,
  AssistantChangeDataCheck,
  AssistantChangeEdge,
  AssistantChangeNode,
  AssistantChangeRecord,
  AssistantDataFinding,
  AssistantStatusResponse as AssistantStatus,
  AssistantUsage,
} from "../generated/api-contracts.generated"
import type {
  AssistantBuildPlan,
  AssistantChangeRecord,
  AssistantStatusResponse,
} from "../generated/api-contracts.generated"

/**
 * How a completed turn ended, and what it saved. The detail is the model's
 * question, the blocker, the verification error of a save that committed, or
 * the controller's reason the model stopped before finishing. `changes` are the
 * ids of the change cards the turn saved, in order: at least one for `applied`,
 * none for `answered`, and any number before a question, blocker or stop.
 */
export type AssistantTurnOutcome =
  | { kind: "applied" | "answered"; detail: null; changes: string[] }
  | {
      kind: "needs_input" | "blocked" | "committed_unverified" | "incomplete"
      detail: string
      changes: string[]
    }

export type AssistantStreamEvent =
  | Exclude<AssistantStreamEventPayload, { type: "completed" }>
  | (Omit<Extract<AssistantStreamEventPayload, { type: "completed" }>, "outcome"> & {
      outcome: AssistantTurnOutcome
    })

/** The outcome as a union on its kind: the server pairs a detail with exactly the kinds that carry one. */
function turnOutcome(outcome: GeneratedAssistantTurnOutcome): AssistantTurnOutcome {
  const { kind, detail, changes } = outcome
  if (kind === "applied" || kind === "answered") {
    if (detail !== null) throw new Error(`Invalid assistant turn outcome: ${kind} carries a detail`)
    return { kind, detail, changes }
  }
  if (detail === null) throw new Error(`Invalid assistant turn outcome: ${kind} has no detail`)
  return { kind, detail, changes }
}

export type AssistantHistoryEntry =
  | {
      kind: "user" | "assistant" | "tool"
      text: string
      name: string
      /** A tool row's plain-words title; empty on text rows. */
      title: string
      summary: string
      is_error: boolean
    }
  /** Closes a completed turn with the outcome its live `completed` event carried. */
  | { kind: "outcome"; outcome: AssistantTurnOutcome }
  /** An apply's change card, after its tool row, as the live `change_applied` event showed. */
  | { kind: "change"; change: AssistantChangeRecord }
  /** The analyst undid this change, after the turn it followed. */
  | { kind: "undo"; change: AssistantChangeRecord }

/** A transcript entry as a union on its kind: the server gives exactly its own kind's payload. */
function historyEntry(entry: AssistantTranscriptEntry): AssistantHistoryEntry {
  const { kind, outcome, change, text, name, title, summary, is_error } = entry
  if (kind === "outcome") {
    if (outcome === null) throw new Error("Invalid assistant history: an outcome entry has no outcome")
    return { kind, outcome: turnOutcome(outcome) }
  }
  if (kind === "change" || kind === "undo") {
    if (change === null) throw new Error(`Invalid assistant history: a ${kind} entry has no change`)
    return { kind, change }
  }
  return { kind, text, name, title, summary, is_error }
}

function parseEvent(payload: string): AssistantStreamEvent {
  const event = expectGeneratedContract(
    "AssistantStreamEventPayload",
    validateAssistantStreamEventPayload,
    JSON.parse(payload),
  )
  return event.type === "completed" ? { ...event, outcome: turnOutcome(event.outcome) } : event
}

function parseFrame(frame: string): AssistantStreamEvent | null {
  const dataLines = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
  if (dataLines.length === 0) return null

  const payload = dataLines.map((line) => line.slice("data:".length).trimStart()).join("\n")
  if (!payload.trim()) return null
  return parseEvent(payload)
}

export function getAssistantStatus(): Promise<AssistantStatusResponse> {
  return request<unknown>("/api/assistant/status").then((value) =>
    expectGeneratedContract("AssistantStatusResponse", validateAssistantStatusResponse, value))
}

export interface AssistantSessionResult {
  sessionId: string
  /** The canonical source file the server bound the session to. */
  sourceFile: string
  history: AssistantHistoryEntry[]
  /** The session's build plan for the checklist; null until the model sets one. */
  buildPlan: AssistantBuildPlan | null
}

/** Create (or resume) a chat bound to the canvas document's source file. */
export function createAssistantSession(
  sourceFile: string,
  sessionId: string | null = null,
  signal?: AbortSignal,
): Promise<AssistantSessionResult> {
  return post<unknown>(
    "/api/assistant/session",
    { source_file: sourceFile, session_id: sessionId },
    { signal },
  ).then((value) => {
    const session = expectGeneratedContract(
      "AssistantSessionResponse",
      validateAssistantSessionResponse,
      value,
    )
    return {
      sessionId: session.session_id,
      sourceFile: session.source_file,
      history: session.history.map(historyEntry),
      buildPlan: session.build_plan,
    }
  })
}

export interface AssistantSessionSummary {
  sessionId: string
  title: string
  createdAt: number
  lastUsed: number
  messageCount: number
}

export interface AssistantSessionList {
  /** The canonical source file the listed chats are bound to. */
  sourceFile: string
  sessions: AssistantSessionSummary[]
}

/** List the chats bound to the canvas document's source file. */
export function listAssistantSessions(
  sourceFile: string,
  signal?: AbortSignal,
): Promise<AssistantSessionList> {
  const query = `?source_file=${encodeURIComponent(sourceFile)}`
  return request<unknown>(`/api/assistant/sessions${query}`, { signal }).then((value) => {
    const list = expectGeneratedContract(
      "AssistantSessionListResponse",
      validateAssistantSessionListResponse,
      value,
    )
    return {
      sourceFile: list.source_file,
      sessions: list.sessions.map((entry) => ({
        sessionId: entry.session_id,
        title: entry.title,
        createdAt: entry.created_at,
        lastUsed: entry.last_used,
        messageCount: entry.message_count,
      })),
    }
  })
}

export interface AssistantUndoResult {
  changeId: string
  /** The commit the undo's save made, or null when it was not captured in Git. */
  gitSha: string | null
  /** The session's build plan after the undo marked the change undone; null without one. */
  buildPlan: AssistantBuildPlan | null
}

/** Undo one change card: the backend saves the version before that change. */
export function undoAssistantChange(
  sessionId: string,
  changeId: string,
  sourceFile: string,
): Promise<AssistantUndoResult> {
  return post<unknown>("/api/assistant/changes/undo", {
    session_id: sessionId,
    change_id: changeId,
    source_file: sourceFile,
  }).then((value) => {
    const undo = expectGeneratedContract("AssistantUndoResponse", validateAssistantUndoResponse, value)
    return { changeId: undo.change_id, gitSha: undo.git_sha, buildPlan: undo.build_plan }
  })
}

/** What the canvas adds to a message: the backend's closed message `context`. */
export interface AssistantMessageContext {
  /** Top-level node ids selected on the canvas, at most `MAX_CONTEXT_SELECTION`. */
  selectedNodeIds: string[]
  /** The node whose schema-resolution error the turn context reports. */
  previewErrorNodeId: string | null
}

/** The most selected nodes one message carries; the backend refuses more. */
export const MAX_CONTEXT_SELECTION = 20

export interface StreamAssistantMessageOptions {
  signal: AbortSignal
  onEvent: (event: AssistantStreamEvent) => void
  context: AssistantMessageContext
}

export async function streamAssistantMessage(
  sessionId: string,
  message: string,
  sourceFile: string,
  options: StreamAssistantMessageOptions,
): Promise<void> {
  const response = await postRawStream(
    "/api/assistant/message",
    {
      session_id: sessionId,
      message,
      source_file: sourceFile,
      context: {
        selected_node_ids: options.context.selectedNodeIds,
        preview_error_node_id: options.context.previewErrorNodeId,
      },
    },
    { signal: options.signal },
  )
  if (response.body === null) {
    throw new Error("Assistant stream response has no body")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const emitFrames = () => {
    const frames = buffer.split("\n\n")
    buffer = frames.pop() ?? ""
    for (const frame of frames) {
      const event = parseFrame(frame)
      if (event !== null) options.onEvent(event)
    }
  }

  try {
    // eslint-disable-next-line no-restricted-syntax -- reads one SSE stream to its end; not a job poll
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")
      emitFrames()
    }
    buffer += decoder.decode().replace(/\r\n/g, "\n")
    if (buffer.trim()) {
      const event = parseFrame(buffer)
      if (event !== null) options.onEvent(event)
    }
  } catch (error) {
    try {
      await reader.cancel()
    } catch {
      // Cancelling is best-effort; preserve the parser, callback, or read error.
    }
    throw error
  } finally {
    reader.releaseLock()
  }
}
