import { describe, it, expect, vi, afterEach } from "vitest"
import { renderHook, waitFor, cleanup, act } from "@testing-library/react"
import { useStaleConfigEstimate } from "../useStaleConfigEstimate"
import useToastStore from "../../stores/useToastStore"
import { hashConfig } from "../../stores/useNodeResultsStore"

interface FakeEstimate {
  estimated_mb: number
  available_mb: number
}

const configA = { algorithm: "catboost", gpu: false }
const configB = { algorithm: "catboost", gpu: true }
const sampleEstimate: FakeEstimate = { estimated_mb: 1024, available_mb: 4096 }

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useStaleConfigEstimate", () => {
  it("loads the estimate on mount and derives configHash and staleness", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)

    const { result } = renderHook(() =>
      useStaleConfigEstimate<FakeEstimate>(
        "node_1",
        configA,
        { configHash: "old-hash", source: "source_a", structuralVersion: 1 },
        endpoint,
        { source: "source_a", structuralVersion: 1 },
      ),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(endpoint).toHaveBeenCalledTimes(1)
    expect(result.current.estimate).toEqual(sampleEstimate)
    expect(result.current.error).toBeNull()
    expect(result.current.configHash).toBe(hashConfig(configA))
    expect(result.current.isStale).toBe(true)
    expect(endpoint.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal)
  })

  it("reports fresh when the cached result hash matches the current config", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)

    const { result } = renderHook(() =>
      useStaleConfigEstimate<FakeEstimate>(
        "node_1",
        configA,
        { configHash: hashConfig(configA), source: "source_a", structuralVersion: 1 },
        endpoint,
        { source: "source_a", structuralVersion: 1 },
      ),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isStale).toBe(false)
  })

  it("does not fetch when nodeId is empty", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)

    renderHook(() =>
      useStaleConfigEstimate<FakeEstimate>("", configA, null, endpoint, { source: "source_a", structuralVersion: 1 }),
    )

    await act(async () => {})
    expect(endpoint).not.toHaveBeenCalled()
  })

  it("refetches when the config hash changes but not when it stays the same", async () => {
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockResolvedValueOnce({ estimated_mb: 100, available_mb: 1000 })
      .mockResolvedValueOnce({ estimated_mb: 500, available_mb: 1000 })

    const { result, rerender } = renderHook(
      ({ config }: { config: Record<string, unknown> }) =>
        useStaleConfigEstimate<FakeEstimate>("node_1", config, null, endpoint, { source: "source_a", structuralVersion: 1 }),
      { initialProps: { config: configA } },
    )

    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(100))
    expect(endpoint).toHaveBeenCalledTimes(1)

    rerender({ config: { ...configA } })
    await act(async () => {})
    expect(endpoint).toHaveBeenCalledTimes(1)

    rerender({ config: configB })
    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(500))
    expect(endpoint).toHaveBeenCalledTimes(2)
  })

  it("refetches when the estimate key changes even if the config hash is stable", async () => {
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockResolvedValueOnce({ estimated_mb: 100, available_mb: 1000 })
      .mockResolvedValueOnce({ estimated_mb: 250, available_mb: 1000 })

    const { result, rerender } = renderHook(
      ({ estimateKey }: { estimateKey: string }) =>
        useStaleConfigEstimate<FakeEstimate>(
          "node_1",
          configA,
          null,
          endpoint,
          { source: "source_a", structuralVersion: 1 },
          { estimateKey },
        ),
      { initialProps: { estimateKey: "live:1" } },
    )

    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(100))
    expect(endpoint).toHaveBeenCalledTimes(1)

    rerender({ estimateKey: "batch:1" })
    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(250))
    expect(endpoint).toHaveBeenCalledTimes(2)
  })

  it("aborts the in-flight fetch when the config changes mid-request", async () => {
    let firstSignal = null as AbortSignal | null
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockImplementationOnce((_p, opts) => {
        firstSignal = opts.signal
        return new Promise<FakeEstimate>(() => {})
      })
      .mockResolvedValueOnce({ estimated_mb: 999, available_mb: 9999 })

    const { rerender } = renderHook(
      ({ config }: { config: Record<string, unknown> }) =>
        useStaleConfigEstimate<FakeEstimate>("node_1", config, null, endpoint, { source: "source_a", structuralVersion: 1 }),
      { initialProps: { config: configA } },
    )

    await waitFor(() => expect(firstSignal).not.toBeNull())
    expect(firstSignal?.aborted).toBe(false)

    rerender({ config: configB })
    await waitFor(() => expect(firstSignal?.aborted).toBe(true))
  })

  it("aborts on unmount and keeps AbortError silent", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError")
    let captured = null as AbortSignal | null
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockImplementation((_p, opts) => {
        captured = opts.signal
        return Promise.reject(abortError)
      })

    const { unmount, result } = renderHook(() =>
      useStaleConfigEstimate<FakeEstimate>("node_1", configA, null, endpoint, { source: "source_a", structuralVersion: 1 }),
    )

    await waitFor(() => expect(endpoint).toHaveBeenCalled())
    unmount()

    expect(captured?.aborted).toBe(true)
    await act(async () => {})
    expect(useToastStore.getState().toasts).toHaveLength(0)
    expect(result.current.error).toBeNull()
  })

  it("raises a warning toast and surfaces the error message on network failure", async () => {
    const endpoint = vi.fn().mockRejectedValue(new Error("Server unreachable"))

    const { result } = renderHook(() =>
      useStaleConfigEstimate<FakeEstimate>("node_1", configA, null, endpoint, { source: "source_a", structuralVersion: 1 }),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.estimate).toBeNull()
    expect(result.current.error).toBe("Server unreachable")
    expect(
      useToastStore
        .getState()
        .toasts.some((t) => t.type === "warning" && t.text.includes("Server unreachable")),
    ).toBe(true)
  })
})
