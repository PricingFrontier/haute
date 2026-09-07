/**
 * Tests for API client retry behavior (Phase 5 Wave 10B, Item #115).
 *
 * Contract being pinned:
 *   - Idempotent verbs (GET, DELETE) retry transient failures (network errors,
 *     5xx) with exponential backoff + jitter, capped at 3 retries by default.
 *   - Non-idempotent POST does NOT retry by default (callers opt in via an
 *     explicit idempotency key if the implementation chooses to support one).
 *   - 4xx responses are NOT retried — they indicate client-side bugs.
 *   - Total backoff budget stays under ~1s so user-perceived latency is bounded.
 *   - A user-supplied AbortSignal cancels the retry loop immediately, including
 *     while sleeping between attempts.
 *
 * These tests are written BEFORE the retry logic exists in client.ts; they
 * are expected to fail against the current implementation (no retries).
 *
 * Timer strategy:
 *   Instead of using fake timers (which also swallow the 30s timeout guard
 *   created by AbortController), we stub setTimeout directly. Short delays
 *   (< 10s — i.e. retry backoffs) are captured and fired as zero-delay tasks.
 *   Long delays (>= 10s — i.e. the timeout guard) pass through to the real
 *   setTimeout. This lets us assert on backoff durations without the timeout
 *   guard firing mid-test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import {
  ApiError,
  loadPipeline,
  checkMlflow,
  listUtilityFiles,
  savePipeline,
  commitMilestone,
  deleteJsonCache,
} from "../client"

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

function errorResponse(status: number, body?: unknown) {
  return Promise.resolve({
    ok: false,
    status,
    statusText: "Error",
    json: body !== undefined
      ? () => Promise.resolve(body)
      : () => Promise.reject(new Error("no body")),
  })
}

function mlflowCheckResponse(available: boolean) {
  return {
    mlflow_installed: available,
    mlflow_importable: available,
    tracking_configured: available,
    backend: "",
    databricks_host: "",
    detail: "",
  }
}

const BACKOFF_THRESHOLD_MS = 10_000 // anything >= this is treated as the timeout guard
const dummyGraph = { nodes: [], edges: [] }

/**
 * Install a setTimeout wrapper that:
 *   - captures backoff delays (< 10s) in `capturedDelays`
 *   - fires backoff callbacks on the microtask queue (zero real delay)
 *   - passes longer delays through to the real setTimeout (so the 30s
 *     AbortController-timeout guard behaves normally)
 *
 * Returns { capturedDelays, restore }.
 */
function stubBackoffTimers() {
  const capturedDelays: number[] = []
  const originalSetTimeout = globalThis.setTimeout
  const handle = (globalThis.setTimeout = function patchedSetTimeout(
    fn: (...args: unknown[]) => void,
    ms?: number,
    ...rest: unknown[]
  ) {
    const delay = ms ?? 0
    if (delay < BACKOFF_THRESHOLD_MS) {
      capturedDelays.push(delay)
      // Schedule on the microtask queue so awaiting code unblocks immediately,
      // without waiting the real wall-clock delay.
      return originalSetTimeout(fn as never, 0, ...(rest as never[]))
    }
    return originalSetTimeout(fn as never, delay, ...(rest as never[]))
  } as typeof globalThis.setTimeout)
  // Re-expose so that spies can still see calls if needed.
  return {
    capturedDelays,
    restore: () => {
      globalThis.setTimeout = originalSetTimeout
      void handle
    },
  }
}

/**
 * Install a setTimeout wrapper that captures backoff callbacks WITHOUT firing
 * them. The caller can inspect `pendingBackoffs` and manually invoke callbacks
 * to simulate the passage of time (or leave them un-invoked to test abort
 * during a sleep).
 *
 * Returns { pendingBackoffs, capturedDelays, restore }.
 */
function pauseBackoffTimers() {
  const capturedDelays: number[] = []
  const pendingBackoffs: Array<() => void> = []
  const originalSetTimeout = globalThis.setTimeout
  globalThis.setTimeout = function pausedSetTimeout(
    fn: (...args: unknown[]) => void,
    ms?: number,
    ...rest: unknown[]
  ) {
    const delay = ms ?? 0
    if (delay < BACKOFF_THRESHOLD_MS) {
      capturedDelays.push(delay)
      pendingBackoffs.push(() => (fn as () => void)())
      // Return a fake handle; the code under test only ever passes handles to
      // clearTimeout in the timeout guard path (which uses the real setTimeout).
      return 0 as unknown as ReturnType<typeof setTimeout>
    }
    return originalSetTimeout(fn as never, delay, ...(rest as never[]))
  } as typeof globalThis.setTimeout

  return {
    capturedDelays,
    pendingBackoffs,
    restore: () => {
      globalThis.setTimeout = originalSetTimeout
    },
  }
}

// ---------------------------------------------------------------------------
// Shared setup
// ---------------------------------------------------------------------------

let mockFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ═══════════════════════════════════════════════════════════════════════════
// Retry on transient network errors (idempotent verbs only)
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: idempotent GET on network error", () => {
  it("retries up to 2 times on TypeError and eventually succeeds", async () => {
    const stub = stubBackoffTimers()
    try {
      const payload = { nodes: [{ id: "n1" }], edges: [], preserved_blocks: [], source_revision: "revision-test" }
      mockFetch
        .mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockReturnValueOnce(jsonResponse(payload))

      const result = await loadPipeline()

      expect(result).toEqual(payload)
      expect(mockFetch).toHaveBeenCalledTimes(3)
    } finally {
      stub.restore()
    }
  })

  it("succeeds on the first attempt when fetch succeeds immediately", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(jsonResponse({ nodes: [], edges: [], preserved_blocks: [], source_revision: "revision-test" }))
      await loadPipeline()
      // No backoff sleeps should have been scheduled.
      expect(stub.capturedDelays).toHaveLength(0)
      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Retry on 5xx responses
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: idempotent GET on 5xx", () => {
  it("retries on 503 and succeeds on the next attempt", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockReturnValueOnce(errorResponse(503, { detail: "Service unavailable" }))
        .mockReturnValueOnce(jsonResponse(mlflowCheckResponse(true)))

      const result = await checkMlflow()

      expect(result).toEqual({
        mlflow_installed: true,
        mlflow_importable: true,
        tracking_configured: true,
        backend: "",
        databricks_host: "",
        detail: "",
      })
      expect(mockFetch).toHaveBeenCalledTimes(2)
    } finally {
      stub.restore()
    }
  })

  it("retries on 500 Internal Server Error", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockReturnValueOnce(errorResponse(500, { detail: "boom" }))
        .mockReturnValueOnce(jsonResponse(mlflowCheckResponse(false)))

      const result = await checkMlflow()

      expect(result).toEqual({
        mlflow_installed: false,
        mlflow_importable: false,
        tracking_configured: false,
        backend: "",
        databricks_host: "",
        detail: "",
      })
      expect(mockFetch).toHaveBeenCalledTimes(2)
    } finally {
      stub.restore()
    }
  })

  it("retries on 502 Bad Gateway and 504 Gateway Timeout (common proxy errors)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockReturnValueOnce(errorResponse(502))
        .mockReturnValueOnce(errorResponse(504))
        .mockReturnValueOnce(jsonResponse({ files: [] }))

      const result = await listUtilityFiles()

      expect(result).toEqual({ files: [] })
      expect(mockFetch).toHaveBeenCalledTimes(3)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Does NOT retry on 4xx responses
// ═══════════════════════════════════════════════════════════════════════════

describe("no retry: 4xx client errors", () => {
  it("fails immediately on 400 Bad Request without retry", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(errorResponse(400, { detail: "bad input" }))

      await expect(checkMlflow()).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })

  it("fails immediately on 401 Unauthorized without retry", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(errorResponse(401, { detail: "auth" }))

      await expect(checkMlflow()).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      stub.restore()
    }
  })

  it("fails immediately on 404 without retry", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(errorResponse(404, { detail: "not found" }))

      await expect(listUtilityFiles()).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      stub.restore()
    }
  })

  it("fails immediately on 422 Unprocessable Entity without retry", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(errorResponse(422, { detail: "validation" }))

      await expect(checkMlflow()).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Max retry cap
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: max attempts cap", () => {
  it("stops retrying after 3 retries (4 total attempts) when fetch keeps throwing", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockRejectedValue(new TypeError("Failed to fetch"))

      await expect(checkMlflow()).rejects.toBeInstanceOf(TypeError)

      // 1 initial attempt + 3 retries
      expect(mockFetch).toHaveBeenCalledTimes(4)
    } finally {
      stub.restore()
    }
  })

  it("stops retrying after 3 retries when server keeps returning 503", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValue(errorResponse(503, { detail: "down" }))

      await expect(checkMlflow()).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(4)
    } finally {
      stub.restore()
    }
  })

  it("surfaces the final error after exhausting retries (ApiError preserved)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValue(errorResponse(503, { detail: "still down" }))

      try {
        await checkMlflow()
        throw new Error("should have rejected")
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError)
        expect((err as ApiError).status).toBe(503)
        expect((err as ApiError).detail).toBe("still down")
      }
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Exponential backoff with jitter
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: caller supplied policy", () => {
  it("lets an idempotent caller opt into more retries than the default budget", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("cold start"))
        .mockRejectedValueOnce(new TypeError("cold start"))
        .mockRejectedValueOnce(new TypeError("cold start"))
        .mockRejectedValueOnce(new TypeError("cold start"))
        .mockRejectedValueOnce(new TypeError("cold start"))
        .mockReturnValueOnce(jsonResponse({ nodes: [], edges: [], preserved_blocks: [], source_revision: "revision-test" }))

      const result = await loadPipeline({ retry: { maxRetries: 5, baseDelayMs: 25 } })

      expect(result).toEqual({ nodes: [], edges: [], preserved_blocks: [], source_revision: "revision-test" })
      expect(mockFetch).toHaveBeenCalledTimes(6)
      expect(stub.capturedDelays).toHaveLength(5)
    } finally {
      stub.restore()
    }
  })

  it("keeps the short default retry budget for regular GETs", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockRejectedValue(new TypeError("still starting"))

      await expect(checkMlflow()).rejects.toBeInstanceOf(TypeError)

      expect(mockFetch).toHaveBeenCalledTimes(4)
      expect(stub.capturedDelays).toHaveLength(3)
    } finally {
      stub.restore()
    }
  })

  it("does not retry non-idempotent POSTs even when a retry policy is provided", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockRejectedValueOnce(new TypeError("connection dropped"))

      await expect(
        savePipeline(
          {
            name: "test",
            description: "",
            graph: dummyGraph,
            preamble: "",
            source_file: "pipe.py",
            base_revision: null,
            preserved_blocks: [],
          },
          // @ts-expect-error retry policies are deliberately not exposed on
          // mutation helpers until a real idempotency-key contract exists.
          { retry: { maxRetries: 5, baseDelayMs: 25 } },
        ),
      ).rejects.toBeInstanceOf(TypeError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })

  it("fails loudly when a retry policy has an invalid retry count", async () => {
    const stub = stubBackoffTimers()
    try {
      await expect(loadPipeline({ retry: { maxRetries: -1 } })).rejects.toThrow(
        "retry.maxRetries must be a non-negative integer",
      )

      expect(mockFetch).not.toHaveBeenCalled()
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })

  it("fails loudly when a retry policy has an invalid base delay", async () => {
    const stub = stubBackoffTimers()
    try {
      await expect(loadPipeline({ retry: { baseDelayMs: 0 } })).rejects.toThrow(
        "retry.baseDelayMs must be a positive finite number",
      )

      expect(mockFetch).not.toHaveBeenCalled()
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })
})

describe("retry: exponential backoff with jitter", () => {
  it("waits between retries (at least one setTimeout scheduled per retry)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse(mlflowCheckResponse(true)))

      await checkMlflow()

      // One backoff sleep per retry; two retries ⇒ two sleeps.
      expect(stub.capturedDelays).toHaveLength(2)
    } finally {
      stub.restore()
    }
  })

  it("delays grow roughly exponentially (later delay >= earlier delay)", async () => {
    // Run multiple trials to smooth out the effect of jitter.
    const trials = 10
    const firstDelays: number[] = []
    const secondDelays: number[] = []

    for (let i = 0; i < trials; i++) {
      mockFetch = vi.fn()
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse(mlflowCheckResponse(true)))
      globalThis.fetch = mockFetch as unknown as typeof fetch

      const stub = stubBackoffTimers()
      try {
        await checkMlflow()
        expect(stub.capturedDelays).toHaveLength(2)
        firstDelays.push(stub.capturedDelays[0])
        secondDelays.push(stub.capturedDelays[1])
      } finally {
        stub.restore()
      }
    }

    // Second attempt's mean delay should exceed the first's by a clear margin
    // (exponential growth dominates jitter).
    const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length
    expect(mean(secondDelays)).toBeGreaterThan(mean(firstDelays))
  })

  it("each backoff delay is bounded above by a reasonable max (< 1s)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockRejectedValue(new TypeError("boom"))

      await expect(checkMlflow()).rejects.toBeInstanceOf(TypeError)

      // All backoff delays must be finite and < 1s (3 retries × exponential
      // base ~100ms with jitter should fit comfortably below this).
      expect(stub.capturedDelays.length).toBeGreaterThan(0)
      for (const delay of stub.capturedDelays) {
        expect(delay).toBeGreaterThanOrEqual(0)
        expect(delay).toBeLessThan(1000)
      }
    } finally {
      stub.restore()
    }
  })

  it("introduces jitter (not all delays are identical across many trials)", async () => {
    const trials = 20
    const firstDelays: number[] = []

    for (let i = 0; i < trials; i++) {
      mockFetch = vi.fn()
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse(mlflowCheckResponse(true)))
      globalThis.fetch = mockFetch as unknown as typeof fetch

      const stub = stubBackoffTimers()
      try {
        await checkMlflow()
        firstDelays.push(stub.capturedDelays[0])
      } finally {
        stub.restore()
      }
    }

    const unique = new Set(firstDelays)
    // With real jitter we expect many distinct values; allow slack for
    // implementations that round, but demand more than one.
    expect(unique.size).toBeGreaterThan(1)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Total backoff budget
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: total backoff budget", () => {
  it("total sum of backoff delays stays under ~1s across all retries", async () => {
    // Average across multiple trials to smooth out jitter outliers.
    const trials = 10
    const totals: number[] = []

    for (let i = 0; i < trials; i++) {
      mockFetch = vi.fn().mockRejectedValue(new TypeError("boom"))
      globalThis.fetch = mockFetch as unknown as typeof fetch

      const stub = stubBackoffTimers()
      try {
        await expect(checkMlflow()).rejects.toBeInstanceOf(TypeError)
        const total = stub.capturedDelays.reduce((a, b) => a + b, 0)
        totals.push(total)
      } finally {
        stub.restore()
      }
    }

    // No single run should exceed ~1.5s (generous ceiling for jitter tails).
    for (const total of totals) {
      expect(total).toBeLessThan(1500)
    }

    // Mean should be well under 1s to keep user-perceived latency bounded.
    const meanTotal = totals.reduce((a, b) => a + b, 0) / totals.length
    expect(meanTotal).toBeLessThan(1000)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// POST does NOT retry by default
// ═══════════════════════════════════════════════════════════════════════════

describe("no retry: non-idempotent POST (default)", () => {
  it("does NOT retry savePipeline on network error (non-idempotent)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"))

      await expect(
        savePipeline({
          name: "test",
          description: "",
          graph: dummyGraph,
          preamble: "",
          source_file: "pipe.py",
          base_revision: null,
          preserved_blocks: [],
        }),
      ).rejects.toBeInstanceOf(TypeError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })

  it("does NOT retry commitMilestone on 503 (non-idempotent)", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch.mockReturnValueOnce(errorResponse(503, { detail: "down" }))

      await expect(commitMilestone("msg", null)).rejects.toBeInstanceOf(ApiError)

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// POST with idempotency key (optional — test accepts either behavior)
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: POST idempotency-key opt-in (optional feature)", () => {
  // This test is deliberately lenient: the implementer may choose to support
  // an `idempotencyKey` option that opts a POST into the retry loop, OR may
  // decide no such opt-in exists yet. Either answer is acceptable — the test
  // only pins that IF the feature exists, it honors the contract, and IF it
  // doesn't, the POST still fails loudly on the first attempt.
  it("behaves consistently when a caller wraps a POST with retry semantics", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse({ sha: "abc", short_sha: "abc", working_branch: "feat/x", version_label: null }))

      // The current public API has no idempotency-key parameter. If the
      // implementer adds one, this assertion should be updated to call it;
      // for now, we assert that commitMilestone without any opt-in does not
      // retry — matching the "non-idempotent POST does not retry" contract.
      await expect(commitMilestone("msg", null)).rejects.toBeInstanceOf(TypeError)
      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// Retry preserves headers and body across attempts
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: preserves request headers and body", () => {
  it("retries a DELETE with the same URL and method on each attempt", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse({ cached: false, data_path: "file.json" }))

      await deleteJsonCache("file.json")

      expect(mockFetch).toHaveBeenCalledTimes(2)
      const [url1, opts1] = mockFetch.mock.calls[0]
      const [url2, opts2] = mockFetch.mock.calls[1]
      expect(url1).toBe(url2)
      expect(url1).toBe("/api/json-cache?path=file.json")
      expect(opts1.method).toBe("DELETE")
      expect(opts2.method).toBe("DELETE")
    } finally {
      stub.restore()
    }
  })

  it("retries a GET with the same URL on each attempt", async () => {
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse({ files: [] }))

      await listUtilityFiles()

      expect(mockFetch).toHaveBeenCalledTimes(3)
      const urls = mockFetch.mock.calls.map((c) => c[0])
      expect(new Set(urls).size).toBe(1)
      expect(urls[0]).toBe("/api/utility")
    } finally {
      stub.restore()
    }
  })

  it("sends the same AbortSignal reference (or an equivalent one) on each attempt", async () => {
    // The client wraps the external signal in an internal AbortController.
    // Across retries every attempt must receive a signal (never undefined),
    // so that a user abort mid-retry still cancels the in-flight fetch.
    const stub = stubBackoffTimers()
    try {
      mockFetch
        .mockRejectedValueOnce(new TypeError("boom"))
        .mockReturnValueOnce(jsonResponse({ files: [] }))

      await listUtilityFiles()

      const signal1 = mockFetch.mock.calls[0][1].signal
      const signal2 = mockFetch.mock.calls[1][1].signal
      expect(signal1).toBeInstanceOf(AbortSignal)
      expect(signal2).toBeInstanceOf(AbortSignal)
    } finally {
      stub.restore()
    }
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// User-supplied AbortSignal cancels retry loop
// ═══════════════════════════════════════════════════════════════════════════

describe("retry: caller-supplied AbortSignal", () => {
  it("cancels during backoff — no further fetch attempts after abort", async () => {
    // Pause backoff timers so we can trigger abort DURING the sleep between
    // retry attempts, before the next fetch is issued.
    const paused = pauseBackoffTimers()
    try {
      mockFetch.mockRejectedValue(new TypeError("boom"))

      const controller = new AbortController()
      const promise = listUtilityFiles({ signal: controller.signal })

      // Swallow unhandled rejection.
      promise.catch(() => {})

      // Yield to allow the first fetch attempt to reject and the retry sleep
      // to be scheduled.
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()

      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(paused.pendingBackoffs.length).toBeGreaterThan(0)

      // Abort before the backoff timer fires.
      controller.abort()

      // Fire the backoff callback — the retry loop should notice abort and
      // refuse to issue another fetch.
      for (const cb of paused.pendingBackoffs.splice(0)) cb()

      await expect(promise).rejects.toBeDefined()

      // Still only one fetch; abort prevented subsequent retries.
      expect(mockFetch).toHaveBeenCalledTimes(1)
    } finally {
      paused.restore()
    }
  })

  it("rejects promptly when caller aborts mid-flight (pre-existing behavior)", async () => {
    const stub = stubBackoffTimers()
    try {
      // Simulate fetch that rejects when its signal aborts.
      mockFetch.mockImplementation((_url, options) => {
        return new Promise((_resolve, reject) => {
          const s = options?.signal as AbortSignal | undefined
          if (s) {
            s.addEventListener(
              "abort",
              () => reject(new DOMException("Aborted", "AbortError")),
              { once: true },
            )
          }
        })
      })

      const controller = new AbortController()
      const promise = listUtilityFiles({ signal: controller.signal })
      // Swallow the rejection so we can still assert on it.
      promise.catch(() => {})

      // Abort almost immediately.
      controller.abort()

      await expect(promise).rejects.toBeDefined()
    } finally {
      stub.restore()
    }
  })

  it("does not retry when fetch rejects with AbortError (user abort)", async () => {
    const stub = stubBackoffTimers()
    try {
      const abortErr = new DOMException("Aborted", "AbortError")
      mockFetch.mockRejectedValue(abortErr)

      await expect(listUtilityFiles()).rejects.toBeDefined()

      // AbortError signals intentional cancellation, not a transient failure —
      // retrying would defeat the user's cancel.
      expect(mockFetch).toHaveBeenCalledTimes(1)
      expect(stub.capturedDelays).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })
})
