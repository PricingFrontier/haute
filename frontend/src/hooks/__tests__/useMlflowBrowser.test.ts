import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useMlflowBrowser } from "../useMlflowBrowser"
import {
  getExperiments,
  getRuns,
  getModels,
  getModelVersions,
  ApiError,
} from "../../api/client"

vi.mock("../../api/client", () => ({
  getExperiments: vi.fn(),
  getRuns: vi.fn(),
  getModels: vi.fn(),
  getModelVersions: vi.fn(),
  ApiError: class ApiError extends Error {
    detail?: string
    status: number
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  },
}))

const mockGetExperiments = getExperiments as ReturnType<typeof vi.fn>
const mockGetRuns = getRuns as ReturnType<typeof vi.fn>
const mockGetModels = getModels as ReturnType<typeof vi.fn>
const mockGetModelVersions = getModelVersions as ReturnType<typeof vi.fn>

const fakeExperiments = [
  { experiment_id: "1", name: "Experiment A" },
  { experiment_id: "2", name: "Experiment B" },
]

const fakeRuns = [
  { run_id: "r1", run_name: "run-1", metrics: { rmse: 0.5 }, artifacts: ["model.pkl"] },
]

const fakeModels = [
  { name: "model-a", latest_versions: [{ version: "1", status: "READY", run_id: "r1" }] },
]

const fakeVersions = [
  { version: "1", run_id: "r1", status: "READY", description: "First version" },
  { version: "2", run_id: "r2", status: "READY", description: "Second version" },
]

describe("useMlflowBrowser", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // ─── Initial state ────────────────────────────────────────────

  it("initial state: all arrays empty, all loading false, all errors empty", () => {
    const { result } = renderHook(() => useMlflowBrowser())

    expect(result.current.experiments).toEqual([])
    expect(result.current.runs).toEqual([])
    expect(result.current.models).toEqual([])
    expect(result.current.modelVersions).toEqual([])
    expect(result.current.modelVersionsFor).toBe("")
    expect(result.current.loadingExperiments).toBe(false)
    expect(result.current.loadingRuns).toBe(false)
    expect(result.current.loadingModels).toBe(false)
    expect(result.current.loadingVersions).toBe(false)
    expect(result.current.errorExperiments).toBe("")
    expect(result.current.errorRuns).toBe("")
    expect(result.current.errorModels).toBe("")
    expect(result.current.errorVersions).toBe("")
  })

  it("browseExpId initializes from initialExpId option", () => {
    const { result } = renderHook(() => useMlflowBrowser({ initialExpId: "42" }))
    expect(result.current.browseExpId).toBe("42")
  })

  it("browseExpId defaults to empty string when no initialExpId", () => {
    const { result } = renderHook(() => useMlflowBrowser())
    expect(result.current.browseExpId).toBe("")
  })

  // ─── refreshExperiments ───────────────────────────────────────

  it("refreshExperiments: resolves with data and sets experiments array", async () => {
    mockGetExperiments.mockResolvedValue(fakeExperiments)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshExperiments() })

    await waitFor(() => {
      expect(result.current.loadingExperiments).toBe(false)
    })
    expect(result.current.experiments).toEqual(fakeExperiments)
    expect(mockGetExperiments).toHaveBeenCalledOnce()
  })

  it("refreshExperiments: called twice only makes one API call (fetch guard)", async () => {
    mockGetExperiments.mockResolvedValue(fakeExperiments)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => {
      result.current.refreshExperiments()
      result.current.refreshExperiments()
    })

    await waitFor(() => {
      expect(result.current.loadingExperiments).toBe(false)
    })
    expect(mockGetExperiments).toHaveBeenCalledOnce()
  })

  it("refreshExperiments: API error sets errorExperiments message", async () => {
    mockGetExperiments.mockRejectedValue(new Error("Network failure"))
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshExperiments() })

    await waitFor(() => {
      expect(result.current.errorExperiments).toBe("Network failure")
    })
    expect(result.current.experiments).toEqual([])
    expect(result.current.loadingExperiments).toBe(false)
  })

  it("refreshExperiments: ApiError with detail uses detail as error message", async () => {
    const apiErr = new ApiError("generic", 500, "Detailed error info")
    mockGetExperiments.mockRejectedValue(apiErr)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshExperiments() })

    await waitFor(() => {
      expect(result.current.errorExperiments).toBe("Detailed error info")
    })
  })

  it("refreshExperiments: error resets fetch guard so retry works", async () => {
    mockGetExperiments.mockRejectedValueOnce(new Error("fail"))
    mockGetExperiments.mockResolvedValueOnce(fakeExperiments)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshExperiments() })
    await waitFor(() => {
      expect(result.current.errorExperiments).toBe("fail")
    })

    // Retry should now work because guard was reset on error
    act(() => { result.current.refreshExperiments() })
    await waitFor(() => {
      expect(result.current.experiments).toEqual(fakeExperiments)
    })
    expect(mockGetExperiments).toHaveBeenCalledTimes(2)
  })

  // ─── refreshModels ────────────────────────────────────────────

  it("refreshModels: resolves with data and sets models array", async () => {
    mockGetModels.mockResolvedValue(fakeModels)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshModels() })

    await waitFor(() => {
      expect(result.current.loadingModels).toBe(false)
    })
    expect(result.current.models).toEqual(fakeModels)
    expect(mockGetModels).toHaveBeenCalledOnce()
  })

  it("refreshModels: fetch guard prevents duplicate calls", async () => {
    mockGetModels.mockResolvedValue(fakeModels)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => {
      result.current.refreshModels()
      result.current.refreshModels()
    })

    await waitFor(() => {
      expect(result.current.loadingModels).toBe(false)
    })
    expect(mockGetModels).toHaveBeenCalledOnce()
  })

  it("refreshModels: API error resets guard so retry works", async () => {
    mockGetModels.mockRejectedValueOnce(new Error("models fail"))
    mockGetModels.mockResolvedValueOnce(fakeModels)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshModels() })
    await waitFor(() => {
      expect(result.current.errorModels).toBe("models fail")
    })

    // Guard was reset on error, so retry should work
    act(() => { result.current.refreshModels() })
    await waitFor(() => {
      expect(result.current.models).toEqual(fakeModels)
    })
    expect(mockGetModels).toHaveBeenCalledTimes(2)
  })

  // ─── refreshRuns ──────────────────────────────────────────────

  it("refreshRuns: resolves with data and sets runs array", async () => {
    mockGetRuns.mockResolvedValue(fakeRuns)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshRuns("exp-1") })

    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })
    expect(result.current.runs).toEqual(fakeRuns)
    expect(mockGetRuns).toHaveBeenCalledWith("exp-1", undefined)
  })

  it("refreshRuns: same expId twice only makes one API call", async () => {
    mockGetRuns.mockResolvedValue(fakeRuns)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => {
      result.current.refreshRuns("exp-1")
    })
    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })

    act(() => {
      result.current.refreshRuns("exp-1")
    })

    expect(mockGetRuns).toHaveBeenCalledOnce()
  })

  it("refreshRuns: different expId makes new API call", async () => {
    mockGetRuns.mockResolvedValue(fakeRuns)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshRuns("exp-1") })
    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })

    act(() => { result.current.refreshRuns("exp-2") })
    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })

    expect(mockGetRuns).toHaveBeenCalledTimes(2)
    expect(mockGetRuns).toHaveBeenCalledWith("exp-2", undefined)
  })

  it("refreshRuns: empty expId makes no API call", () => {
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshRuns("") })

    expect(mockGetRuns).not.toHaveBeenCalled()
  })

  it("refreshRuns: passes runTag option to getRuns", async () => {
    mockGetRuns.mockResolvedValue(fakeRuns)
    const { result } = renderHook(() => useMlflowBrowser({ runTag: "optimiser" }))

    act(() => { result.current.refreshRuns("exp-1") })

    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })
    expect(mockGetRuns).toHaveBeenCalledWith("exp-1", "optimiser")
  })

  // ─── resetRunsGuard ───────────────────────────────────────────

  it("resetRunsGuard: allows re-fetch of same expId", async () => {
    mockGetRuns.mockResolvedValue(fakeRuns)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshRuns("exp-1") })
    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })
    expect(mockGetRuns).toHaveBeenCalledOnce()

    // Reset the guard
    act(() => { result.current.resetRunsGuard() })

    // Now the same expId should trigger a new fetch
    act(() => { result.current.refreshRuns("exp-1") })
    await waitFor(() => {
      expect(result.current.loadingRuns).toBe(false)
    })
    expect(mockGetRuns).toHaveBeenCalledTimes(2)
  })

  // ─── refreshVersions ─────────────────────────────────────────

  it("refreshVersions: resolves with data and sets modelVersions array", async () => {
    mockGetModelVersions.mockResolvedValue(fakeVersions)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshVersions("model-a") })

    await waitFor(() => {
      expect(result.current.loadingVersions).toBe(false)
    })
    expect(result.current.modelVersions).toEqual(fakeVersions)
    expect(result.current.modelVersionsFor).toBe("model-a")
    expect(mockGetModelVersions).toHaveBeenCalledWith("model-a")
  })

  it("refreshVersions: same modelName twice only makes one call", async () => {
    mockGetModelVersions.mockResolvedValue(fakeVersions)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshVersions("model-a") })
    await waitFor(() => {
      expect(result.current.loadingVersions).toBe(false)
    })

    act(() => { result.current.refreshVersions("model-a") })

    expect(mockGetModelVersions).toHaveBeenCalledOnce()
  })

  it("refreshVersions: different modelName makes new API call", async () => {
    mockGetModelVersions.mockResolvedValue(fakeVersions)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshVersions("model-a") })
    await waitFor(() => {
      expect(result.current.loadingVersions).toBe(false)
    })

    act(() => { result.current.refreshVersions("model-b") })
    expect(result.current.modelVersions).toEqual([])
    expect(result.current.modelVersionsFor).toBe("model-b")
    await waitFor(() => {
      expect(result.current.loadingVersions).toBe(false)
    })

    expect(mockGetModelVersions).toHaveBeenCalledTimes(2)
  })

  it("refreshVersions: empty modelName makes no API call", () => {
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshVersions("") })

    expect(mockGetModelVersions).not.toHaveBeenCalled()
  })

  it("refreshVersions: error resets guard so retry works", async () => {
    mockGetModelVersions.mockRejectedValueOnce(new Error("versions fail"))
    mockGetModelVersions.mockResolvedValueOnce(fakeVersions)
    const { result } = renderHook(() => useMlflowBrowser())

    act(() => { result.current.refreshVersions("model-a") })
    await waitFor(() => {
      expect(result.current.errorVersions).toBe("versions fail")
    })

    // Guard was reset on error, so retry should work
    act(() => { result.current.refreshVersions("model-a") })
    await waitFor(() => {
      expect(result.current.modelVersions).toEqual(fakeVersions)
    })
    expect(mockGetModelVersions).toHaveBeenCalledTimes(2)
  })
})
