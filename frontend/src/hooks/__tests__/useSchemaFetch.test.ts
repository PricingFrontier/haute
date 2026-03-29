import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useSchemaFetch } from "../useSchemaFetch"
import { fetchSchema } from "../../api/client"

vi.mock("../../api/client", () => ({
  fetchSchema: vi.fn(),
}))

const mockFetchSchema = fetchSchema as ReturnType<typeof vi.fn>

const fakeSchema = {
  path: "data.csv",
  columns: [{ name: "col1", dtype: "Utf8" }],
  row_count: 10,
  column_count: 1,
  preview: [],
}

describe("useSchemaFetch", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("no initialPath: not loading, schema is null", () => {
    const { result } = renderHook(() => useSchemaFetch())
    expect(result.current.loading).toBe(false)
    expect(result.current.schema).toBeNull()
  })

  it("initialPath triggers auto-fetch and sets schema on success", async () => {
    mockFetchSchema.mockResolvedValue(fakeSchema)
    const { result } = renderHook(() => useSchemaFetch("data.csv"))
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.schema).toEqual(fakeSchema)
    expect(mockFetchSchema).toHaveBeenCalledWith("data.csv", { signal: expect.any(AbortSignal) })
  })

  it("fetch failure sets schema to null, loading to false, and populates error", async () => {
    mockFetchSchema.mockRejectedValue(new Error("fail"))
    const { result } = renderHook(() => useSchemaFetch("bad.csv"))
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.schema).toBeNull()
    expect(result.current.error).toBe("fail")
  })

  it("fetchForPath triggers manual fetch", async () => {
    mockFetchSchema.mockResolvedValue(fakeSchema)
    const { result } = renderHook(() => useSchemaFetch())
    act(() => {
      result.current.fetchForPath("manual.csv")
    })
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.schema).toEqual(fakeSchema)
    expect(mockFetchSchema).toHaveBeenCalledWith("manual.csv", { signal: undefined })
  })

  it("loading state is true while fetch is pending", async () => {
    let resolvePromise: (value: unknown) => void
    mockFetchSchema.mockReturnValue(new Promise((r) => { resolvePromise = r }))
    const { result } = renderHook(() => useSchemaFetch("pending.csv"))
    expect(result.current.loading).toBe(true)
    await act(async () => {
      resolvePromise!(fakeSchema)
    })
    expect(result.current.loading).toBe(false)
  })

  it("error is null when no fetch has occurred", () => {
    const { result } = renderHook(() => useSchemaFetch())
    expect(result.current.error).toBeNull()
  })

  it("error is null after successful fetch", async () => {
    mockFetchSchema.mockResolvedValue(fakeSchema)
    const { result } = renderHook(() => useSchemaFetch("data.csv"))
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeNull()
  })

  it("error is cleared when a new fetch succeeds", async () => {
    mockFetchSchema.mockRejectedValueOnce(new Error("network error"))
    const { result } = renderHook(() => useSchemaFetch())

    // First fetch fails
    act(() => {
      result.current.fetchForPath("bad.csv")
    })
    await waitFor(() => {
      expect(result.current.error).toBe("network error")
    })

    // Second fetch succeeds
    mockFetchSchema.mockResolvedValueOnce(fakeSchema)
    act(() => {
      result.current.fetchForPath("good.csv")
    })
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBeNull()
    expect(result.current.schema).toEqual(fakeSchema)
  })

  it("non-Error rejection is stringified", async () => {
    mockFetchSchema.mockRejectedValue("string error")
    const { result } = renderHook(() => useSchemaFetch("bad.csv"))
    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })
    expect(result.current.error).toBe("string error")
  })
})
