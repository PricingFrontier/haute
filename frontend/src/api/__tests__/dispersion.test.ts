/**
 * Tests for the split-chunk dispersion-estimation API module
 * (src/api/dispersion.ts) — kept out of api/client.ts so its code stays
 * outside the initial bundle (see scripts/check-bundle-size.mjs).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import {
  estimateGlmDispersion,
  getDispersionStatus,
  cancelDispersion,
  runDispersionEstimate,
} from "../dispersion"
import { ApiError } from "../client"

let mockFetch: ReturnType<typeof vi.fn>

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(body),
  })
}

const dummyGraph = {
  nodes: [{ id: "n1", type: "custom", position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

function makeDispersionStatus(overrides: Record<string, unknown> = {}) {
  return {
    status: "running",
    progress: 0.5,
    message: "Profile likelihood fit 3",
    elapsed_seconds: 1.2,
    param: "theta",
    value: null,
    llf: null,
    n_fits: null,
    error: null,
    terminal_reason: null,
    ...overrides,
  }
}

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("dispersion estimation endpoints", () => {
  it("estimateGlmDispersion posts graph, node, param and default source", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "started", job_id: "disp-1" }))
    const result = await estimateGlmDispersion({ graph: dummyGraph, node_id: "n1", param: "theta" })
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/dispersion/estimate")
    expect(opts.method).toBe("POST")
    const body = JSON.parse(opts.body)
    expect(body.node_id).toBe("n1")
    expect(body.param).toBe("theta")
    expect(body.source).toBe("live")
    expect(result.job_id).toBe("disp-1")
  })

  it("estimateGlmDispersion rejects a malformed start payload", async () => {
    mockFetch.mockReturnValue(jsonResponse({ status: "started" }))
    await expect(
      estimateGlmDispersion({ graph: dummyGraph, node_id: "n1", param: "theta" }),
    ).rejects.toThrow("unexpected payload")
  })

  it("getDispersionStatus GETs the job status", async () => {
    mockFetch.mockReturnValue(jsonResponse(makeDispersionStatus({ status: "completed", value: 2.45 })))
    const status = await getDispersionStatus("disp-1")
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/dispersion/status/disp-1")
    expect(status.status).toBe("completed")
    expect(status.value).toBe(2.45)
  })

  it("cancelDispersion posts to the cancel endpoint", async () => {
    mockFetch.mockReturnValue(jsonResponse(makeDispersionStatus({ status: "cancelled" })))
    const status = await cancelDispersion("disp-1")
    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/modelling/dispersion/cancel/disp-1")
    expect(opts.method).toBe("POST")
    expect(status.status).toBe("cancelled")
  })

  it("runDispersionEstimate polls to completion and resolves with the value", async () => {
    mockFetch
      .mockReturnValueOnce(jsonResponse({ status: "started", job_id: "disp-1" }))
      .mockReturnValueOnce(jsonResponse(makeDispersionStatus()))
      .mockReturnValueOnce(jsonResponse(makeDispersionStatus({ status: "completed", value: 2.4487, llf: -693.0, n_fits: 11 })))
    const value = await runDispersionEstimate(
      { graph: dummyGraph, node_id: "n1", param: "theta" },
      { pollIntervalMs: 0 },
    )
    expect(value).toBe(2.4487)
    expect(mockFetch).toHaveBeenCalledTimes(3)
  })

  it("runDispersionEstimate rejects with the job message on terminal failure", async () => {
    mockFetch
      .mockReturnValueOnce(jsonResponse({ status: "started", job_id: "disp-1" }))
      .mockReturnValueOnce(jsonResponse(makeDispersionStatus({ status: "contract_error", error: "no converged fit" })))
    await expect(
      runDispersionEstimate({ graph: dummyGraph, node_id: "n1", param: "theta" }, { pollIntervalMs: 0 }),
    ).rejects.toThrow("no converged fit")
  })

  it("runDispersionEstimate rejects when completed without a value", async () => {
    mockFetch
      .mockReturnValueOnce(jsonResponse({ status: "started", job_id: "disp-1" }))
      .mockReturnValueOnce(jsonResponse(makeDispersionStatus({ status: "completed", value: null })))
    const error = await runDispersionEstimate(
      { graph: dummyGraph, node_id: "n1", param: "theta" },
      { pollIntervalMs: 0 },
    ).then(
      () => null,
      (reason: unknown) => reason,
    )
    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(ApiError)
    expect((error as Error).message).toContain("without a value")
  })

  it("runDispersionEstimate aborts via signal and requests a cancel", async () => {
    const controller = new AbortController()
    mockFetch
      .mockReturnValueOnce(jsonResponse({ status: "started", job_id: "disp-1" }))
      .mockReturnValue(jsonResponse(makeDispersionStatus({ status: "cancelled" })))
    controller.abort()
    await expect(
      runDispersionEstimate(
        { graph: dummyGraph, node_id: "n1", param: "theta" },
        { pollIntervalMs: 0, signal: controller.signal },
      ),
    ).rejects.toThrow()
  })

  it("cancels the backend job when abort lands during an in-flight status request", async () => {
    const controller = new AbortController()
    mockFetch
      .mockReturnValueOnce(jsonResponse({ status: "started", job_id: "disp-in-flight" }))
      .mockImplementationOnce((_url: string, options: RequestInit) => (
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"))
          }, { once: true })
        })
      ))
      .mockReturnValueOnce(jsonResponse(makeDispersionStatus({ status: "cancelled" })))

    const run = runDispersionEstimate(
      { graph: dummyGraph, node_id: "n1", param: "theta" },
      { pollIntervalMs: 0, signal: controller.signal },
    )
    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    controller.abort()

    await expect(run).rejects.toMatchObject({ name: "AbortError" })
    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/modelling/dispersion/cancel/disp-in-flight",
        expect.objectContaining({ method: "POST" }),
      )
    })
  })
})
