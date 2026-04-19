/**
 * Tests for useConfigEstimate — the shared hook that loads a per-node
 * estimate (RAM/VRAM for training, or anything else) from a configurable
 * endpoint and re-fetches whenever the config hash changes.
 *
 * Extracted from the inline RAM-estimate block in ModellingConfig.tsx so
 * OptimiserConfig (and any future config panel) can share the same
 * lifecycle — mount load, config-change refetch, AbortError-silent
 * cleanup, toast-on-network-error.
 *
 * Contract under test (new hook — no implementation yet):
 *
 *   const { estimate, loading, error } = useConfigEstimate(
 *     nodeId,
 *     configHash,
 *     endpoint,   // (payload, { signal }) => Promise<TEstimate>
 *   )
 *
 * The hook is responsible for:
 *   - calling `endpoint` once on mount with an AbortSignal
 *   - calling `endpoint` again whenever `configHash` changes
 *   - cancelling the in-flight request when the hook unmounts or the
 *     configHash changes mid-flight
 *   - swallowing AbortError silently (no toast, no error state)
 *   - routing any non-Abort error through useToastStore as a warning
 *     AND surfacing the message as `error`
 *   - NOT firing when nodeId is empty
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, waitFor, cleanup, act } from "@testing-library/react"
import { useConfigEstimate } from "../useConfigEstimate"
import useToastStore from "../../stores/useToastStore"

interface FakeEstimate {
  estimated_mb: number
  available_mb: number
}

const sampleEstimate: FakeEstimate = { estimated_mb: 1024, available_mb: 4096 }

beforeEach(() => {
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("useConfigEstimate", () => {
  it("loads the estimate on mount and exposes it on the return value", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)

    const { result } = renderHook(() =>
      useConfigEstimate<FakeEstimate>("node_1", "hash_A", endpoint),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(endpoint).toHaveBeenCalledTimes(1)
    expect(result.current.estimate).toEqual(sampleEstimate)
    expect(result.current.error).toBeNull()
    // Hook must supply an AbortSignal so requests are cancellable
    const opts = endpoint.mock.calls[0][1] as { signal?: AbortSignal }
    expect(opts.signal).toBeInstanceOf(AbortSignal)
  })

  it("does not fetch when nodeId is empty (no active node)", async () => {
    const endpoint = vi.fn().mockResolvedValue(sampleEstimate)

    renderHook(() =>
      useConfigEstimate<FakeEstimate>("", "hash_A", endpoint),
    )

    await act(async () => {})
    expect(endpoint).not.toHaveBeenCalled()
  })

  it("refetches when configHash changes but not when it stays the same", async () => {
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockResolvedValueOnce({ estimated_mb: 100, available_mb: 1000 })
      .mockResolvedValueOnce({ estimated_mb: 500, available_mb: 1000 })

    const { result, rerender } = renderHook(
      ({ hash }: { hash: string }) =>
        useConfigEstimate<FakeEstimate>("node_1", hash, endpoint),
      { initialProps: { hash: "hash_A" } },
    )

    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(100))
    expect(endpoint).toHaveBeenCalledTimes(1)

    // Re-render with the same hash — no new fetch
    rerender({ hash: "hash_A" })
    await act(async () => {})
    expect(endpoint).toHaveBeenCalledTimes(1)

    // Change the hash — triggers a refetch
    rerender({ hash: "hash_B" })
    await waitFor(() => expect(result.current.estimate?.estimated_mb).toBe(500))
    expect(endpoint).toHaveBeenCalledTimes(2)
  })

  it("aborts the in-flight fetch when configHash changes mid-request", async () => {
    // `as AbortSignal | null` annotation here — without it, the TS control
    // flow analyser can't tell the mock callback mutates the local and
    // narrows the type to `never` after the initial `null` assignment.
    let firstSignal = null as AbortSignal | null
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockImplementationOnce((_p, opts) => {
        firstSignal = opts.signal
        return new Promise<FakeEstimate>(() => {}) // hangs
      })
      .mockResolvedValueOnce({ estimated_mb: 999, available_mb: 9999 })

    const { rerender } = renderHook(
      ({ hash }: { hash: string }) =>
        useConfigEstimate<FakeEstimate>("node_1", hash, endpoint),
      { initialProps: { hash: "hash_A" } },
    )

    await waitFor(() => expect(firstSignal).not.toBeNull())
    expect(firstSignal?.aborted).toBe(false)

    rerender({ hash: "hash_B" })
    await waitFor(() => expect(firstSignal?.aborted).toBe(true))
  })

  it("aborts the in-flight request on unmount and does not toast AbortError", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError")
    // Same narrowing workaround as above — see that test for the rationale.
    let captured = null as AbortSignal | null
    const endpoint = vi
      .fn<(payload: unknown, opts: { signal: AbortSignal }) => Promise<FakeEstimate>>()
      .mockImplementation((_p, opts) => {
        captured = opts.signal
        return Promise.reject(abortError)
      })

    const { unmount, result } = renderHook(() =>
      useConfigEstimate<FakeEstimate>("node_1", "hash_A", endpoint),
    )

    await waitFor(() => expect(endpoint).toHaveBeenCalled())
    unmount()

    // Signal is aborted on unmount
    expect(captured?.aborted).toBe(true)

    // Give the rejected promise chain a tick to settle — AbortError must be silent
    await act(async () => {})
    expect(useToastStore.getState().toasts).toHaveLength(0)
    expect(result.current.error).toBeNull()
  })

  it("raises a warning toast and surfaces the error message on network failure", async () => {
    const endpoint = vi.fn().mockRejectedValue(new Error("Server unreachable"))

    const { result } = renderHook(() =>
      useConfigEstimate<FakeEstimate>("node_1", "hash_A", endpoint),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.estimate).toBeNull()
    expect(result.current.error).toBe("Server unreachable")

    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.type === "warning" && t.text.includes("Server unreachable"))).toBe(true)
  })
})
