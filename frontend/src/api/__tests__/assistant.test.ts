/**
 * Tests for the assistant split endpoint module (src/api/assistant.ts).
 *
 * Spec: specs/frontend-assistant-ui/low-level.md — api/assistant.ts row,
 * Key types, and Edge cases (SSE framing).  Authored test-first: the module
 * is implemented to make these pass.
 *
 * API pinned here:
 *   getAssistantStatus(): Promise<AssistantStatus>
 *   createAssistantSession(sourceFile, sessionId?, signal?): Promise<AssistantSessionResult>
 *   listAssistantSessions(sourceFile, signal?): Promise<AssistantSessionList>
 *   streamAssistantMessage(sessionId, message, sourceFile, opts: {
 *     signal: AbortSignal
 *     onEvent: (event: AssistantStreamEvent) => void
 *     context: AssistantMessageContext
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
  listAssistantSessions,
  streamAssistantMessage,
  type AssistantStreamEvent,
} from "../assistant"

let mockFetch: ReturnType<typeof vi.fn>

const NO_CONTEXT = { selectedNodeIds: [], previewErrorNodeId: null }

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
  await streamAssistantMessage("session-1", "hello", "main.py", {
    signal: new AbortController().signal,
    context: NO_CONTEXT,
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
      endpoint_host: "api.anthropic.com",
      trust: "external",
      max_sensitivity: "public",
      mutations_enabled: false,
      mutations_reason: "Not a git repository. Run 'git init' first.",
    }
    mockFetch.mockReturnValueOnce(jsonResponse(status))

    await expect(getAssistantStatus()).resolves.toEqual(status)
    const [url] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/status")
  })

  it.each([
    ["an array payload", []],
    ["a missing required field", { configured: true, reason: null, provider: null, model: null, endpoint_host: null, trust: null, max_sensitivity: null, mutations_enabled: true }],
    ["a wrong primitive field", { configured: "yes", reason: null, provider: null, model: null, endpoint_host: null, trust: null, max_sensitivity: null, mutations_enabled: true, mutations_reason: null }],
    ["an invalid nullable field", { configured: true, reason: 1, provider: null, model: null, endpoint_host: null, trust: null, max_sensitivity: null, mutations_enabled: true, mutations_reason: null }],
    ["an invalid trust class", { configured: true, reason: null, provider: null, model: null, endpoint_host: "x", trust: "trusted", max_sensitivity: "public", mutations_enabled: true, mutations_reason: null }],
    ["a configured status without egress identity", { configured: true, reason: null, provider: "openai", model: "m", endpoint_host: null, trust: null, max_sensitivity: null, mutations_enabled: true, mutations_reason: null }],
  ])("rejects %s with an ordinary validation error", async (_label, payload) => {
    mockFetch.mockReturnValueOnce(jsonResponse(payload))
    const result = getAssistantStatus()
    await expect(result).rejects.toBeInstanceOf(Error)
    await expect(result).rejects.not.toBeInstanceOf(ApiError)
  })
})

describe("createAssistantSession", () => {
  it("posts the canvas source file and returns the bound session", async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse({ session_id: "abc123", source_file: "pipelines/main.py", history: [] }),
    )

    await expect(createAssistantSession("pipelines/main.py")).resolves.toEqual({
      sessionId: "abc123",
      sourceFile: "pipelines/main.py",
      history: [],
    })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/session")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body as string)).toEqual({
      source_file: "pipelines/main.py",
      session_id: null,
    })
  })

  it("offers a remembered session id and surfaces returned history", async () => {
    const history = [
      { kind: "user", text: "hi", name: "", summary: "", is_error: false },
      { kind: "tool", text: "", name: "get_pipeline", summary: "{}", is_error: false },
    ]
    mockFetch.mockReturnValueOnce(
      jsonResponse({ session_id: "abc123", source_file: "main.py", history }),
    )

    await expect(createAssistantSession("main.py", "abc123")).resolves.toEqual({
      sessionId: "abc123",
      sourceFile: "main.py",
      history,
    })
    const [, opts] = mockFetch.mock.calls[0]
    expect(JSON.parse(opts.body as string)).toEqual({
      source_file: "main.py",
      session_id: "abc123",
    })
  })

  it("parses a turn's outcome row", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      session_id: "abc123",
      source_file: "main.py",
      history: [
        { kind: "user", text: "go", name: "", summary: "", is_error: false, outcome: null },
        {
          kind: "outcome",
          text: "",
          name: "",
          summary: "",
          is_error: false,
          outcome: { kind: "needs_input", detail: "Which column?" },
        },
        {
          kind: "outcome",
          text: "",
          name: "",
          summary: "",
          is_error: false,
          outcome: { kind: "incomplete", detail: "A dry-run validated a plan that was never applied." },
        },
      ],
    }))

    const { history } = await createAssistantSession("main.py", "abc123")
    expect(history).toEqual([
      { kind: "user", text: "go", name: "", summary: "", is_error: false },
      { kind: "outcome", outcome: { kind: "needs_input", detail: "Which column?" } },
      {
        kind: "outcome",
        outcome: { kind: "incomplete", detail: "A dry-run validated a plan that was never applied." },
      },
    ])
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

    const request = createAssistantSession("main.py", null, controller.signal)
    controller.abort()

    await expect(request).rejects.toMatchObject({ name: "AbortError" })
    expect(requestSignal?.aborted).toBe(true)
  })

  it.each([
    ["a non-object envelope", []],
    ["a missing session id", { source_file: "main.py", history: [] }],
    ["a missing source file", { session_id: "abc", history: [] }],
    ["a non-array history", { session_id: "abc", source_file: "main.py", history: {} }],
    ["a non-object history entry", { session_id: "abc", source_file: "main.py", history: [null] }],
    ["an unknown history kind", { session_id: "abc", source_file: "main.py", history: [{ kind: "other", text: "", name: "", summary: "", is_error: false }] }],
    ["a missing history field", { session_id: "abc", source_file: "main.py", history: [{ kind: "user", text: "", name: "", summary: "" }] }],
    ["a wrong history field primitive", { session_id: "abc", source_file: "main.py", history: [{ kind: "tool", text: "", name: "", summary: "", is_error: "false" }] }],
    ["an outcome row without its outcome", { session_id: "abc", source_file: "main.py", history: [{ kind: "outcome", text: "", name: "", summary: "", is_error: false, outcome: null }] }],
    ["an outcome row with a malformed outcome", { session_id: "abc", source_file: "main.py", history: [{ kind: "outcome", outcome: { kind: "blocked", detail: null } }] }],
  ])("rejects %s", async (_label, payload) => {
    mockFetch.mockReturnValueOnce(jsonResponse(payload))
    await expect(createAssistantSession("main.py")).rejects.toThrow(/assistant|session|history/i)
  })
})

describe("listAssistantSessions", () => {
  it("queries by the canvas source file and returns the bound list", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      source_file: "pipelines/main.py",
      sessions: [
        { session_id: "s1", title: "t", created_at: 1, last_used: 2, message_count: 3 },
      ],
    }))

    await expect(listAssistantSessions("pipelines/main.py")).resolves.toEqual({
      sourceFile: "pipelines/main.py",
      sessions: [{ sessionId: "s1", title: "t", createdAt: 1, lastUsed: 2, messageCount: 3 }],
    })
    const [url] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/sessions?source_file=pipelines%2Fmain.py")
  })

  it("rejects a list without its source file", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ sessions: [] }))
    await expect(listAssistantSessions("main.py")).rejects.toThrow(/sessions\.source_file/)
  })
})

describe("streamAssistantMessage", () => {
  it("posts the message and its canvas context with the abort signal attached", async () => {
    const signal = new AbortController().signal
    mockFetch.mockReturnValueOnce(sseResponse(['data: {"type":"cancelled"}\n\n']))
    await streamAssistantMessage("session-1", "add a node", "main.py", {
      signal,
      onEvent: () => {},
      context: { selectedNodeIds: ["quotes", "premium"], previewErrorNodeId: "premium" },
    })

    const [url, opts] = mockFetch.mock.calls[0]
    expect(String(url)).toContain("/api/assistant/message")
    expect(opts.method).toBe("POST")
    expect(JSON.parse(opts.body as string)).toEqual({
      session_id: "session-1",
      message: "add a node",
      source_file: "main.py",
      context: { selected_node_ids: ["quotes", "premium"], preview_error_node_id: "premium" },
    })
    expect(opts.signal).toBe(signal)
  })

  it("parses events split across chunk boundaries", async () => {
    const events = await collectEvents([
      'data: {"type":"text_del',
      'ta","text":"Hi"}\n\ndata: {"type":"comp',
      'leted","usage":{"input_tokens":1,"output_tokens":2},"outcome":{"kind":"answered","detail":null}}\n\n',
    ])
    expect(events).toEqual([
      { type: "text_delta", text: "Hi" },
      {
        type: "completed",
        usage: { input_tokens: 1, output_tokens: 2 },
        outcome: { kind: "answered", detail: null },
      },
    ])
  })

  it("applies multiple frames arriving in one chunk in order", async () => {
    const events = await collectEvents([
      'data: {"type":"text_delta","text":"a"}\n\n' +
        'data: {"type":"tool_started","id":"t1","name":"get_pipeline","summary":"{}"}\n\n' +
        'data: {"type":"tool_finished","id":"t1","name":"get_pipeline","is_error":false,"summary":"ok"}\n\n' +
        'data: {"type":"completed","usage":{"input_tokens":1,"output_tokens":1},"outcome":{"kind":"answered","detail":null}}\n\n',
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
      'data: {"type":"completed","usage":{"input_tokens":0,"output_tokens":0},"outcome":{"kind":"answered","detail":null}}\n\n',
    ])
    expect(events.map((event) => event.type)).toEqual(["text_delta", "completed"])
  })

  it("throws loudly on an unrecognised event type", async () => {
    mockFetch.mockReturnValueOnce(sseResponse(['data: {"type":"mystery_event"}\n\n']))
    await expect(
      streamAssistantMessage("session-1", "hi", "main.py", {
        signal: new AbortController().signal,
        context: NO_CONTEXT,
        onEvent: () => {},
      }),
    ).rejects.toThrow(/mystery_event/)
  })

  it.each([
    ["text_delta", { type: "text_delta" }],
    ["tool_started", { type: "tool_started", id: "id", name: "tool", summary: false }],
    ["tool_finished", { type: "tool_finished", id: "id", name: "tool", is_error: "false", summary: "done" }],
    ["graph_updated", { type: "graph_updated", fingerprint: 1 }],
    ["completed usage object", { type: "completed", usage: [] }],
    ["completed nested input_tokens", { type: "completed", usage: { input_tokens: "1", output_tokens: 2 } }],
    ["completed nested output_tokens", { type: "completed", usage: { input_tokens: 1 }, outcome: { kind: "answered", detail: null } }],
    ["completed without an outcome", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 } }],
    ["completed outcome kind", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome: { kind: 1, detail: null } }],
    ["completed answered with a detail", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome: { kind: "answered", detail: "x" } }],
    ["completed question without a detail", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome: { kind: "needs_input", detail: null } }],
    ["completed blocker with a blank detail", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome: { kind: "blocked", detail: " " } }],
    ["completed incomplete without a detail", { type: "completed", usage: { input_tokens: 1, output_tokens: 2 }, outcome: { kind: "incomplete", detail: null } }],
    ["failed", { type: "failed", message: null }],
    ["cancelled discriminator", { type: 1 }],
  ])("rejects malformed %s before invoking the callback", async (_label, event) => {
    const callback = vi.fn()
    mockFetch.mockReturnValueOnce(sseResponse([`data: ${JSON.stringify(event)}\n\n`]))

    await expect(streamAssistantMessage("session-1", "hi", "main.py", {
      signal: new AbortController().signal,
      context: NO_CONTEXT,
      onEvent: callback,
    })).rejects.toThrow(/Invalid assistant payload/)
    expect(callback).not.toHaveBeenCalled()
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

    await expect(streamAssistantMessage("session-1", "hi", "main.py", {
      signal: new AbortController().signal,
      context: NO_CONTEXT,
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

    await expect(streamAssistantMessage("session-1", "hi", "main.py", {
      signal: new AbortController().signal,
      context: NO_CONTEXT,
      onEvent: () => { throw new Error("callback failure") },
    })).rejects.toThrow("callback failure")
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it("maps a non-OK response to ApiError before any streaming", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ detail: "Assistant is not configured" }, 400))
    await expect(
      streamAssistantMessage("session-1", "hi", "main.py", {
        signal: new AbortController().signal,
        context: NO_CONTEXT,
        onEvent: () => {},
      }),
    ).rejects.toBeInstanceOf(ApiError)
  })

  it("resolves without a terminal event (the store decides interrupted)", async () => {
    const events = await collectEvents(['data: {"type":"text_delta","text":"partial"}\n\n'])
    expect(events).toEqual([{ type: "text_delta", text: "partial" }])
  })
})
