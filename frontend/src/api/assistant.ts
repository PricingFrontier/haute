/** Assistant endpoints kept in the lazy panel's split API chunk. */

import { post, postRawStream, request } from "./client"
import { isPlainObject } from "../types/guards"

export interface AssistantStatus {
  configured: boolean
  reason: string | null
  provider: string | null
  model: string | null
  endpoint_host: string | null
  trust: "local" | "organization" | "external" | null
  max_sensitivity: "public" | "internal" | "restricted" | null
  mutations_enabled: boolean
  mutations_reason: string | null
}

export interface AssistantUsage {
  input_tokens: number
  output_tokens: number
}

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

/** One edge a saved plan added or removed, by its endpoint node ids. */
export interface AssistantChangeEdge {
  source: string
  target: string
}

/** One node chip on a change card: which node, its palette type and what happened. */
export interface AssistantChangeNode {
  id: string
  type: string
  change: "added" | "changed" | "removed" | "renamed"
  renamed_from: string | null
  /** The configuration fields the plan changed, in plain words, never their values. */
  fields: string[]
  /** The node's step kinds after the change; null for a node without a step list. */
  steps: string[] | null
  steps_changed: number
}

/** The value-free change card of one saved plan, mirrored from `schemas.py`. */
export interface AssistantChangeRecord {
  /** The hash of the plan the change saved; the turn outcome lists it. */
  id: string
  summary: string
  assumptions: string[]
  changes: {
    nodes: AssistantChangeNode[]
    edges_added: AssistantChangeEdge[]
    edges_removed: AssistantChangeEdge[]
    preamble_changed: boolean
    truncated: boolean
  }
  warnings: string[]
  git_sha: string | null
  parent_sha: string | null
}

export type AssistantStreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_started"; id: string; name: string; title: string; summary: string }
  | {
      type: "tool_finished"
      id: string
      name: string
      title: string
      is_error: boolean
      summary: string
    }
  | { type: "change_applied"; change: AssistantChangeRecord }
  | { type: "completed"; usage: AssistantUsage; outcome: AssistantTurnOutcome }
  | { type: "failed"; message: string }
  | { type: "cancelled" }

function invalidAssistantPayload(path: string, expected: string): never {
  throw new Error(`Invalid assistant payload at ${path}: expected ${expected}`)
}

function requireRecord(value: unknown, path: string): Record<string, unknown> {
  if (!isPlainObject(value)) invalidAssistantPayload(path, "an object")
  return value
}

function requireString(value: unknown, path: string): string {
  if (typeof value !== "string") invalidAssistantPayload(path, "a string")
  return value
}

function requireBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") invalidAssistantPayload(path, "a boolean")
  return value
}

function requireNumber(value: unknown, path: string): number {
  if (typeof value !== "number") invalidAssistantPayload(path, "a number")
  return value
}

function requireNullableString(value: unknown, path: string): string | null {
  if (value !== null && typeof value !== "string") invalidAssistantPayload(path, "a string or null")
  return value
}

function requireNullableLiteral<T extends string>(
  value: unknown,
  path: string,
  allowed: readonly T[],
): T | null {
  if (value === null) return null
  const result = requireString(value, path)
  if (!allowed.includes(result as T)) {
    invalidAssistantPayload(path, allowed.map((item) => JSON.stringify(item)).join(" or "))
  }
  return result as T
}

function parseAssistantStatus(value: unknown): AssistantStatus {
  const payload = requireRecord(value, "status")
  const status: AssistantStatus = {
    configured: requireBoolean(payload.configured, "status.configured"),
    reason: requireNullableString(payload.reason, "status.reason"),
    provider: requireNullableString(payload.provider, "status.provider"),
    model: requireNullableString(payload.model, "status.model"),
    endpoint_host: requireNullableString(payload.endpoint_host, "status.endpoint_host"),
    trust: requireNullableLiteral(
      payload.trust,
      "status.trust",
      ["local", "organization", "external"] as const,
    ),
    max_sensitivity: requireNullableLiteral(
      payload.max_sensitivity,
      "status.max_sensitivity",
      ["public", "internal", "restricted"] as const,
    ),
    mutations_enabled: requireBoolean(payload.mutations_enabled, "status.mutations_enabled"),
    mutations_reason: requireNullableString(payload.mutations_reason, "status.mutations_reason"),
  }
  if (
    status.configured &&
    (status.endpoint_host === null || status.trust === null || status.max_sensitivity === null)
  ) {
    invalidAssistantPayload(
      "status",
      "endpoint_host, trust, and max_sensitivity when configured is true",
    )
  }
  return status
}

function parseTurnOutcome(value: unknown, path: string): AssistantTurnOutcome {
  const payload = requireRecord(value, path)
  const kind = requireString(payload.kind, `${path}.kind`)
  const changes = requireStringArray(payload.changes, `${path}.changes`)
  switch (kind) {
    case "applied":
    case "answered":
      if (payload.detail !== null) invalidAssistantPayload(`${path}.detail`, "null")
      if (kind === "applied" && changes.length === 0) {
        invalidAssistantPayload(`${path}.changes`, "at least one saved change")
      }
      if (kind === "answered" && changes.length > 0) {
        invalidAssistantPayload(`${path}.changes`, "no saved change")
      }
      return { kind, detail: null, changes }
    case "needs_input":
    case "blocked":
    case "committed_unverified":
    case "incomplete": {
      const detail = requireString(payload.detail, `${path}.detail`)
      if (!detail.trim()) invalidAssistantPayload(`${path}.detail`, "a non-empty string")
      return { kind, detail, changes }
    }
    default:
      throw new Error(`Unknown assistant turn outcome kind: ${kind}`)
  }
}

function requireStringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) invalidAssistantPayload(path, "an array")
  return value.map((item, index) => requireString(item, `${path}[${index}]`))
}

function parseChangeEdges(value: unknown, path: string): AssistantChangeEdge[] {
  if (!Array.isArray(value)) invalidAssistantPayload(path, "an array")
  return value.map((item, index) => {
    const edge = requireRecord(item, `${path}[${index}]`)
    return {
      source: requireString(edge.source, `${path}[${index}].source`),
      target: requireString(edge.target, `${path}[${index}].target`),
    }
  })
}

const CHANGE_KINDS = ["added", "changed", "removed", "renamed"] as const

function parseChangeNode(value: unknown, path: string): AssistantChangeNode {
  const node = requireRecord(value, path)
  const change = requireString(node.change, `${path}.change`)
  if (!(CHANGE_KINDS as readonly string[]).includes(change)) {
    invalidAssistantPayload(`${path}.change`, CHANGE_KINDS.join(" or "))
  }
  const stepsChanged = requireNumber(node.steps_changed, `${path}.steps_changed`)
  if (!Number.isInteger(stepsChanged) || stepsChanged < 0) {
    invalidAssistantPayload(`${path}.steps_changed`, "a non-negative integer")
  }
  return {
    id: requireString(node.id, `${path}.id`),
    type: requireString(node.type, `${path}.type`),
    change: change as AssistantChangeNode["change"],
    renamed_from: requireNullableString(node.renamed_from, `${path}.renamed_from`),
    fields: requireStringArray(node.fields, `${path}.fields`),
    steps: node.steps === null ? null : requireStringArray(node.steps, `${path}.steps`),
    steps_changed: stepsChanged,
  }
}

/** Parse a change record field by field; any missing or mistyped field throws. */
function parseChangeRecord(value: unknown, path: string): AssistantChangeRecord {
  const record = requireRecord(value, path)
  const changes = requireRecord(record.changes, `${path}.changes`)
  if (!Array.isArray(changes.nodes)) invalidAssistantPayload(`${path}.changes.nodes`, "an array")
  return {
    id: requireString(record.id, `${path}.id`),
    summary: requireString(record.summary, `${path}.summary`),
    assumptions: requireStringArray(record.assumptions, `${path}.assumptions`),
    changes: {
      nodes: changes.nodes.map((node, index) =>
        parseChangeNode(node, `${path}.changes.nodes[${index}]`),
      ),
      edges_added: parseChangeEdges(changes.edges_added, `${path}.changes.edges_added`),
      edges_removed: parseChangeEdges(changes.edges_removed, `${path}.changes.edges_removed`),
      preamble_changed: requireBoolean(
        changes.preamble_changed,
        `${path}.changes.preamble_changed`,
      ),
      truncated: requireBoolean(changes.truncated, `${path}.changes.truncated`),
    },
    warnings: requireStringArray(record.warnings, `${path}.warnings`),
    git_sha: requireNullableString(record.git_sha, `${path}.git_sha`),
    parent_sha: requireNullableString(record.parent_sha, `${path}.parent_sha`),
  }
}

function parseAssistantHistoryEntry(value: unknown, path: string): AssistantHistoryEntry {
  const payload = requireRecord(value, path)
  const kind = requireString(payload.kind, `${path}.kind`)
  if (kind === "outcome") {
    return { kind, outcome: parseTurnOutcome(payload.outcome, `${path}.outcome`) }
  }
  if (kind === "change") {
    return { kind, change: parseChangeRecord(payload.change, `${path}.change`) }
  }
  if (kind !== "user" && kind !== "assistant" && kind !== "tool") {
    throw new Error(`Unknown assistant history entry kind: ${kind}`)
  }
  return {
    kind,
    text: requireString(payload.text, `${path}.text`),
    name: requireString(payload.name, `${path}.name`),
    title: requireString(payload.title, `${path}.title`),
    summary: requireString(payload.summary, `${path}.summary`),
    is_error: requireBoolean(payload.is_error, `${path}.is_error`),
  }
}

function parseAssistantSession(value: unknown): AssistantSessionResult {
  const payload = requireRecord(value, "session")
  if (!Array.isArray(payload.history)) invalidAssistantPayload("session.history", "an array")
  return {
    sessionId: requireString(payload.session_id, "session.session_id"),
    sourceFile: requireString(payload.source_file, "session.source_file"),
    history: payload.history.map((entry, index) =>
      parseAssistantHistoryEntry(entry, `session.history[${index}]`),
    ),
  }
}

function parseEvent(payload: string): AssistantStreamEvent {
  const parsed = requireRecord(JSON.parse(payload), "stream event")
  const type = requireString(parsed.type, "stream event.type")
  switch (type) {
    case "text_delta":
      return { type, text: requireString(parsed.text, "stream event.text") }
    case "tool_started":
      return {
        type,
        id: requireString(parsed.id, "stream event.id"),
        name: requireString(parsed.name, "stream event.name"),
        title: requireString(parsed.title, "stream event.title"),
        summary: requireString(parsed.summary, "stream event.summary"),
      }
    case "tool_finished":
      return {
        type,
        id: requireString(parsed.id, "stream event.id"),
        name: requireString(parsed.name, "stream event.name"),
        title: requireString(parsed.title, "stream event.title"),
        is_error: requireBoolean(parsed.is_error, "stream event.is_error"),
        summary: requireString(parsed.summary, "stream event.summary"),
      }
    case "change_applied":
      return { type, change: parseChangeRecord(parsed.change, "stream event.change") }
    case "completed": {
      const usage = requireRecord(parsed.usage, "stream event.usage")
      return {
        type,
        usage: {
          input_tokens: requireNumber(usage.input_tokens, "stream event.usage.input_tokens"),
          output_tokens: requireNumber(usage.output_tokens, "stream event.usage.output_tokens"),
        },
        outcome: parseTurnOutcome(parsed.outcome, "stream event.outcome"),
      }
    }
    case "failed":
      return { type, message: requireString(parsed.message, "stream event.message") }
    case "cancelled":
      return { type }
    default:
      throw new Error(`Unknown assistant stream event type: ${type}`)
  }
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

export function getAssistantStatus(): Promise<AssistantStatus> {
  return request<unknown>("/api/assistant/status").then(parseAssistantStatus)
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

export interface AssistantSessionResult {
  sessionId: string
  /** The canonical source file the server bound the session to. */
  sourceFile: string
  history: AssistantHistoryEntry[]
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
  ).then(parseAssistantSession)
}

export interface AssistantSessionSummary {
  sessionId: string
  title: string
  createdAt: number
  lastUsed: number
  messageCount: number
}

function parseAssistantSessionSummary(value: unknown, path: string): AssistantSessionSummary {
  const entry = requireRecord(value, path)
  return {
    sessionId: requireString(entry.session_id, `${path}.session_id`),
    title: requireString(entry.title, `${path}.title`),
    createdAt: requireNumber(entry.created_at, `${path}.created_at`),
    lastUsed: requireNumber(entry.last_used, `${path}.last_used`),
    messageCount: requireNumber(entry.message_count, `${path}.message_count`),
  }
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
    const payload = requireRecord(value, "sessions")
    if (!Array.isArray(payload.sessions)) invalidAssistantPayload("sessions.sessions", "an array")
    return {
      sourceFile: requireString(payload.source_file, "sessions.source_file"),
      sessions: payload.sessions.map((entry, index) =>
        parseAssistantSessionSummary(entry, `sessions.sessions[${index}]`),
      ),
    }
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
