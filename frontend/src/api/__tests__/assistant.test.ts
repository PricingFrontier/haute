/**
 * Tests for the assistant split endpoint module (src/api/assistant.ts).
 *
 * Spec: docs/specs/frontend-assistant-ui/low-level.md — api/assistant.ts row,
 * Key types, and Edge cases (SSE framing).  Authored test-first: the module
 * is implemented to make these pass.
 *
 * API pinned here:
 *   getAssistantStatus(): Promise<AssistantStatus>
 *   createAssistantSession(pipeline: string | null): Promise<string>
 *   streamAssistantMessage(sessionId, message, opts: {
 *     signal: AbortSignal
 *     onEvent: (event: AssistantStreamEvent) => void
 *   }): Promise<void>   — resolves when the stream ends (terminal-event
 *   accounting is the store's job); throws on unknown event types.
 *
 * Conventions: fetch stubbed on globalThis (as api/__tests__/client.test.ts
 * does); streams built from native ReadableStream + TextEncoder.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "../client"
import {
  createAssistantSession,
  getAssistantStatus,
  streamAssistantMessage,
  type AssistantStreamEvent,
} from "../assistant"

let mockFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

function sseResponse(chunks: string[], status = 200) {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    headers: { get: () => "text/event-stream" },
    body,
    json: () => Promise.reject(new Error("SSE responses are not JSON")),
  })
}

async function collectEvents(chunks: string[]): Promise<AssistantStreamEvent[]> {
  mockFetch.mockReturnValueOnce(sseResponse(chunks))
  const events: AssistantStreamEvent[] = []
  await streamAssistantMessage("session-1", "hello", {
    signal: new AbortController().signal,
    onEvent: (event) => events.push(event),
  })
  return events
}

describe("getAssistantStatus", () => {
  it("fetches and returns the status payload", async () => {
    const status = {
      configured: true,
      reason: null,
      provider: "anthropic",
      model: "m",
      mutations_enabled: false,
      mutations_reason: "Not a git repository. Run 'git init' first.",
    }
    mockFetch.mockReturnValueOnce(jsonResponse(status))

    await expect(getAssistantStatus()).resolves.toEqual(status)
    const [url] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/status")
  })
})

describe("createAssistantSession", () => {
  it("posts the pipeline name and returns the session result", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ session_id: "abc123", history: [] }))

    await expect(createAssistantSession("main")).resolves.toEqual({
      sessionId: "abc123",
      history: [],
    })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/session")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body as string)).toEqual({ pipeline: "main", session_id: null })
  })

  it("posts null for the default pipeline", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ session_id: "abc123", history: [] }))
    await createAssistantSession(null)
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body as string)).toEqual({ pipeline: null, session_id: null })
  })

  it("offers a remembered session id and surfaces returned history", async () => {
    const history = [
      { kind: "user", text: "hi", name: "", summary: "", is_error: false },
      { kind: "tool", text: "", name: "get_pipeline", summary: "{}", is_error: false },
    ]
    mockFetch.mockReturnValueOnce(jsonResponse({ session_id: "abc123", history }))

    await expect(createAssistantSession(null, "abc123")).resolves.toEqual({
      sessionId: "abc123",
      history,
    })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body as string)).toEqual({ pipeline: null, session_id: "abc123" })
  })

  it("propagates an optional abort signal to the in-flight request", async () => {
    const controller = new AbortController()
    let requestSignal: AbortSignal | undefined
    mockFetch.mockImplementationOnce((_url, options) => {
      requestSignal = options?.signal as AbortSignal
      return new Promise((_resolve, reject) => {
        requestSignal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        )
      })
    })

    const request = createAssistantSession(null, null, controller.signal)
    controller.abort()

    await expect(request).rejects.toMatchObject({ name: "AbortError" })
    expect(requestSignal?.aborted).toBe(true)
  })
})

describe("streamAssistantMessage", () => {
  it("posts the message with the abort signal attached", async () => {
    const signal = new AbortController().signal
    mockFetch.mockReturnValueOnce(sseResponse(['data: {"type":"cancelled"}\n\n']))
    await streamAssistantMessage("session-1", "add a node", { signal, onEvent: () => {} })

    const [url, opts] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/message")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body as string)).toEqual({
      session_id: "session-1",
      message: "add a node",
    })
    expect(opts.signal).toBe(signal)
  })

  it("parses events split across chunk boundaries", async () => {
    const events = await collectEvents([
      'data: {"type":"text_del',
      'ta","text":"Hi"}\n\ndata: {"type":"comp',
      'leted","usage":{"input_tokens":1,"output_tokens":2}}\n\n',
    ])
    expect(events).toEqual([
      { type: "text_delta", text: "Hi" },
      { type: "completed", usage: { input_tokens: 1, output_tokens: 2 } },
    ])
  })

  it("applies multiple frames arriving in one chunk in order", async () => {
    const events = await collectEvents([
      'data: {"type":"text_delta","text":"a"}\n\n' +
        'data: {"type":"tool_started","id":"t1","name":"get_pipeline","summary":"{}"}\n\n' +
        'data: {"type":"tool_finished","id":"t1","name":"get_pipeline","is_error":false,"summary":"ok"}\n\n' +
        'data: {"type":"completed","usage":{"input_tokens":1,"output_tokens":1}}\n\n',
    ])
    expect(events.map((event) => event.type)).toEqual([
      "text_delta",
      "tool_started",
      "tool_finished",
      "completed",
    ])
  })

  it("ignores keep-alive and empty frames", async () => {
    const events = await collectEvents([
      ": ping\n\n",
      "\n\n",
      'data: {"type":"text_delta","text":"x"}\n\n',
      ": ping\n\n",
      'data: {"type":"completed","usage":{"input_tokens":0,"output_tokens":0}}\n\n',
    ])
    expect(events.map((event) => event.type)).toEqual(["text_delta", "completed"])
  })

  it("throws loudly on an unrecognised event type", async () => {
    mockFetch.mockReturnValueOnce(sseResponse(['data: {"type":"mystery_event"}\n\n']))
    await expect(
      streamAssistantMessage("session-1", "hi", {
        signal: new AbortController().signal,
        onEvent: () => {},
      }),
    ).rejects.toThrow(/mystery_event/)
  })

  it("cancels the reader when parsing fails", async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"mystery_event"}\n\n'))
      },
      cancel,
    })
    mockFetch.mockReturnValueOnce(Promise.resolve({ ok: true, body }))

    await expect(streamAssistantMessage("session-1", "hi", {
      signal: new AbortController().signal,
      onEvent: () => {},
    })).rejects.toThrow(/mystery_event/)
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it("cancels the reader when the event callback fails", async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"cancelled"}\n\n'))
      },
      cancel,
    })
    mockFetch.mockReturnValueOnce(Promise.resolve({ ok: true, body }))

    await expect(streamAssistantMessage("session-1", "hi", {
      signal: new AbortController().signal,
      onEvent: () => { throw new Error("callback failure") },
    })).rejects.toThrow("callback failure")
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it("maps a non-OK response to ApiError before any streaming", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ detail: "Assistant is not configured" }, 400))
    await expect(
      streamAssistantMessage("session-1", "hi", {
        signal: new AbortController().signal,
        onEvent: () => {},
      }),
    ).rejects.toBeInstanceOf(ApiError)
  })

  it("resolves without a terminal event (the store decides interrupted)", async () => {
    const events = await collectEvents(['data: {"type":"text_delta","text":"partial"}\n\n'])
    expect(events).toEqual([{ type: "text_delta", text: "partial" }])
  })
})
