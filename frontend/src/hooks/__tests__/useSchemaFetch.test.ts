import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@testing-library/react"
import { useSchemaFetch } from "../useSchemaFetch"
import { ApiError, fetchSchema } from "../../api/client"

vi.mock("../../api/client", () => ({
  fetchSchema: vi.fn(),
  ApiError: class ApiError extends Error {
    detail?: string
    constructor(message: string, _status: number, detail?: string) {
      super(message)
      this.detail = detail
    }
  },
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
    expect(mockFetchSchema).toHaveBeenCalledWith("data.csv", { signal: expect.any(AbortSignal) }, undefined)
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

  it("prefers an API error detail over its generic message", async () => {
    mockFetchSchema.mockRejectedValue(new ApiError("HTTP 422", 422, "Unsupported delimiter"))
    const { result } = renderHook(() => useSchemaFetch("bad.csv"))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe("Unsupported delimiter")
  })

  it("fetchForPath fetches the current path again, as a retry does", async () => {
    mockFetchSchema.mockRejectedValueOnce(new Error("busy")).mockResolvedValueOnce(fakeSchema)
    const { result } = renderHook(() => useSchemaFetch("data.csv"))
    await waitFor(() => expect(result.current.error).toBe("busy"))
    act(() => {
      result.current.fetchForPath("data.csv")
    })
    expect(result.current.error).toBeNull()
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.schema).toEqual(fakeSchema)
    expect(mockFetchSchema).toHaveBeenLastCalledWith("data.csv", { signal: undefined }, undefined)
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

  it("gives a schema only for the current path, ignoring a late reply for an earlier one", async () => {
    const replies = new Map<string, (value: unknown) => void>()
    mockFetchSchema.mockImplementation((path: string) => new Promise((resolve) => { replies.set(path, resolve) }))
    const { result, rerender } = renderHook(({ path }) => useSchemaFetch(path), { initialProps: { path: "a.csv" } })
    rerender({ path: "b.csv" })
    await act(async () => {
      replies.get("a.csv")!({ ...fakeSchema, path: "a.csv" })
    })
    expect(result.current.schema).toBeNull()
    expect(result.current.loading).toBe(true)
    await act(async () => {
      replies.get("b.csv")!({ ...fakeSchema, path: "b.csv" })
    })
    expect(result.current.schema).toEqual({ ...fakeSchema, path: "b.csv" })
  })

  it("fetches again, and withholds the earlier schema, when the reader settings change", async () => {
    mockFetchSchema.mockResolvedValue(fakeSchema)
    const { result, rerender } = renderHook(
      ({ separator }) => useSchemaFetch("data.csv", { format: "csv", arguments: { separator } }),
      { initialProps: { separator: "," } },
    )
    await waitFor(() => expect(result.current.schema).toEqual(fakeSchema))
    mockFetchSchema.mockReturnValue(new Promise(() => {}))
    rerender({ separator: ";" })
    expect(result.current.schema).toBeNull()
    expect(result.current.loading).toBe(true)
    expect(mockFetchSchema).toHaveBeenLastCalledWith(
      "data.csv", { signal: expect.any(AbortSignal) }, { format: "csv", arguments: { separator: ";" } },
    )
  })

  it("has no schema once the path is cleared", async () => {
    mockFetchSchema.mockResolvedValue(fakeSchema)
    const { result, rerender } = renderHook(({ path }) => useSchemaFetch(path), { initialProps: { path: "data.csv" as string | undefined } })
    await waitFor(() => expect(result.current.schema).toEqual(fakeSchema))
    rerender({ path: undefined })
    expect(result.current.schema).toBeNull()
    expect(result.current.loading).toBe(false)
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
