/** Assistant endpoints kept in the lazy panel's split API chunk. */

import { post, postRawStream, request } from "./client"

export interface AssistantStatus {
  configured: boolean
  reason: string | null
  provider: string | null
  model: string | null
  mutations_enabled: boolean
  mutations_reason: string | null
}

export interface AssistantUsage {
  input_tokens: number
  output_tokens: number
}

export type AssistantStreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_started"; id: string; name: string; summary: string }
  | { type: "tool_finished"; id: string; name: string; is_error: boolean; summary: string }
  | { type: "graph_updated"; fingerprint: string }
  | { type: "completed"; usage: AssistantUsage }
  | { type: "failed"; message: string }
  | { type: "cancelled" }

const ASSISTANT_EVENT_TYPES = new Set<AssistantStreamEvent["type"]>([
  "text_delta",
  "tool_started",
  "tool_finished",
  "graph_updated",
  "completed",
  "failed",
  "cancelled",
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function parseEvent(payload: string): AssistantStreamEvent {
  const parsed: unknown = JSON.parse(payload)
  const type = isRecord(parsed) && typeof parsed.type === "string" ? parsed.type : undefined
  if (type === undefined || !ASSISTANT_EVENT_TYPES.has(type as AssistantStreamEvent["type"])) {
    throw new Error(`Unknown assistant stream event type: ${String(type)}`)
  }
  return parsed as AssistantStreamEvent
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
  return request<AssistantStatus>("/api/assistant/status")
}

export interface AssistantHistoryEntry {
  kind: "user" | "assistant" | "tool"
  text: string
  name: string
  summary: string
  is_error: boolean
}

export interface AssistantSessionResult {
  sessionId: string
  history: AssistantHistoryEntry[]
}

export function createAssistantSession(
  pipeline: string | null,
  sessionId: string | null = null,
): Promise<AssistantSessionResult> {
  return post<{ session_id: string; history: AssistantHistoryEntry[] }>(
    "/api/assistant/session",
    { pipeline, session_id: sessionId },
  ).then(({ session_id, history }) => ({ sessionId: session_id, history }))
}

export interface StreamAssistantMessageOptions {
  signal: AbortSignal
  onEvent: (event: AssistantStreamEvent) => void
}

export async function streamAssistantMessage(
  sessionId: string,
  message: string,
  options: StreamAssistantMessageOptions,
): Promise<void> {
  const response = await postRawStream(
    "/api/assistant/message",
    { session_id: sessionId, message },
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
  } finally {
    reader.releaseLock()
  }
}

