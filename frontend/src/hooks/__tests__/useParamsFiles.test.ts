/**
 * The params folder's CSVs and their headers (specs/frontend-shared): listed once and again
 * on Retry, each file's header read once and kept, and why a read failed handed back while
 * the caller still asks for that file.
 */
import { act, renderHook, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../api/client"
import { listProjectFiles } from "../../api/projectFiles"
import type { ProjectFolderResponse } from "../../api/types"
import { fetchParamFile } from "../../api/workbench"
import { useParamsFiles } from "../useParamsFiles"

vi.mock("../../api/projectFiles", () => ({ listProjectFiles: vi.fn() }))
vi.mock("../../api/workbench", () => ({ fetchParamFile: vi.fn() }))

/** The params folder holding `names`, a name without a dot being a folder. */
const paramsFolder = (...names: string[]): ProjectFolderResponse => ({
  place: "pipeline",
  path: "params",
  root: "rating/files",
  writable: true,
  entries: names.map((name) => ({
    name,
    path: `params/${name}`,
    kind: name.includes(".") ? "file" : "folder",
    modified: null,
    revision: null,
    size: null,
    status: null,
  })),
})

/** The hook asking for `file`, its header problems collected in `problems`. */
function renderParamsFiles(file: string, problems: string[] = []) {
  return renderHook(({ file: asked }) => useParamsFiles(asked, (message) => problems.push(message)), {
    initialProps: { file },
  })
}

describe("useParamsFiles", () => {
  afterEach(() => {
    vi.mocked(listProjectFiles).mockReset()
    vi.mocked(fetchParamFile).mockReset()
  })

  it("lists the params folder's CSVs, and on Retry lists it again after saying why it could not", async () => {
    vi.mocked(listProjectFiles)
      .mockRejectedValueOnce(new ApiError("HTTP 409", 409, "The pipeline sits at the project root."))
      .mockResolvedValueOnce(paramsFolder("regions.csv", "notes.txt", "old", "ZONES.CSV"))
    const { result } = renderParamsFiles("")
    expect(result.current.files).toBeNull()

    await waitFor(() => expect(result.current.unlisted).toBe("The pipeline sits at the project root."))
    expect(listProjectFiles).toHaveBeenCalledWith("pipeline", "params")
    act(() => result.current.retry())
    expect(result.current.unlisted).toBeNull()

    await waitFor(() => expect(result.current.files).toEqual(["regions.csv", "ZONES.CSV"]))
    expect(listProjectFiles).toHaveBeenCalledTimes(2)
    expect(fetchParamFile).not.toHaveBeenCalled()
  })

  it("reads a file's header once, keeping it when the file is asked for again", async () => {
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("a.csv", "b.csv"))
    vi.mocked(fetchParamFile).mockImplementation(async (file) => ({ columns: [`${file}:code`], values: [] }))
    const { result, rerender } = renderParamsFiles("a.csv")

    await waitFor(() => expect(result.current.header).toEqual(["a.csv:code"]))
    rerender({ file: "b.csv" })
    await waitFor(() => expect(result.current.header).toEqual(["b.csv:code"]))
    rerender({ file: "a.csv" })

    expect(result.current.header).toEqual(["a.csv:code"])
    expect(vi.mocked(fetchParamFile).mock.calls).toEqual([["a.csv"], ["b.csv"]])
  })

  it("keeps a header a read of values brought back, so the file's header is not read", async () => {
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("a.csv"))
    const { result, rerender } = renderParamsFiles("")

    act(() => result.current.keepHeader("a.csv", ["code", "name"]))
    rerender({ file: "a.csv" })

    expect(result.current.header).toEqual(["code", "name"])
    expect(fetchParamFile).not.toHaveBeenCalled()
  })

  it("says why a header could not be read, and reads it again when the file is asked for again", async () => {
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("a.csv", "b.csv"))
    const gone = "rating/files/params/a.csv doesn't exist."
    vi.mocked(fetchParamFile).mockRejectedValueOnce(new ApiError("HTTP 409", 409, gone))
    vi.mocked(fetchParamFile).mockResolvedValue({ columns: ["code"], values: [] })
    const problems: string[] = []
    const { result, rerender } = renderParamsFiles("a.csv", problems)

    await waitFor(() => expect(problems).toEqual([gone]))
    expect(result.current.header).toBeUndefined()
    rerender({ file: "" })
    rerender({ file: "a.csv" })

    await waitFor(() => expect(result.current.header).toEqual(["code"]))
    expect(fetchParamFile).toHaveBeenCalledTimes(2)
  })

  it("says nothing of a header read the caller moved on from before it failed", async () => {
    vi.mocked(listProjectFiles).mockResolvedValue(paramsFolder("a.csv", "b.csv"))
    let refuse: (error: unknown) => void = () => {}
    vi.mocked(fetchParamFile).mockImplementation((file) =>
      file === "a.csv"
        ? new Promise((_resolve, reject) => (refuse = reject))
        : Promise.resolve({ columns: ["code"], values: [] }),
    )
    const problems: string[] = []
    const { result, rerender } = renderParamsFiles("a.csv", problems)

    rerender({ file: "b.csv" })
    await waitFor(() => expect(result.current.header).toEqual(["code"]))
    await act(async () => refuse(new ApiError("HTTP 409", 409, "rating/files/params/a.csv doesn't exist.")))

    expect(problems).toEqual([])
  })
})
