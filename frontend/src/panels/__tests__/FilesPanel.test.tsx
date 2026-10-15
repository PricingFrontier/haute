import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import type { ProjectFileEntry, ProjectFolderResponse } from "../../api/types"
import useDataEpochStore from "../../stores/useDataEpochStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import FilesPanel from "../FilesPanel"

const { mockList, mockUpload, mockDelete, mockReport } = vi.hoisted(() => ({
  mockList: vi.fn(),
  mockUpload: vi.fn(),
  mockDelete: vi.fn(),
  mockReport: vi.fn(),
}))

vi.mock("../../api/projectFiles", () => ({
  listProjectFiles: (...args: unknown[]) => mockList(...args),
  uploadProjectFile: (...args: unknown[]) => mockUpload(...args),
  deleteProjectFile: (...args: unknown[]) => mockDelete(...args),
  projectFileDownloadUrl: (place: string, path: string) => `/download/${place}/${path}`,
}))

vi.mock("../../stores/saveCapture", () => ({
  reportSaveCapture: (...args: unknown[]) => mockReport(...args),
}))

function file(name: string, path: string, extra: Partial<ProjectFileEntry> = {}): ProjectFileEntry {
  return { name, path, kind: "file", size: 3, modified: 1_760_000_000, revision: `3-${name}`, status: null, ...extra }
}

function folder(name: string, path = name): ProjectFileEntry {
  return { name, path, kind: "folder", size: null, modified: null, revision: null, status: null }
}

function listing(place: "pipeline" | "local", path: string, entries: ProjectFileEntry[], writable = true): ProjectFolderResponse {
  return { place, path, root: place === "pipeline" ? "rating/files" : "", writable, entries }
}

function written(path: string, replaced = false) {
  return { path, size: 3, revision: "3-new", replaced, status: null, git_sha: "abc", identity_required: false, warnings: [] }
}

function dropFiles(target: HTMLElement, files: File[], folders: string[] = []) {
  const items = [
    ...files.map((f) => ({ kind: "file", getAsFile: () => f, webkitGetAsEntry: () => ({ isDirectory: false, name: f.name }) })),
    ...folders.map((name) => ({ kind: "file", getAsFile: () => null, webkitGetAsEntry: () => ({ isDirectory: true, name }) })),
  ]
  const dataTransfer = { types: ["Files"], files, items, dropEffect: "none" }
  fireEvent.dragOver(target, { dataTransfer })
  fireEvent.drop(target, { dataTransfer })
}

describe("FilesPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useToastStore.setState({ toasts: [] })
    mockUpload.mockImplementation((_place: string, path: string) => ({ promise: Promise.resolve(written(path)), abort: vi.fn() }))
  })
  afterEach(cleanup)

  it("lists a folder with its markers, sizes and download links", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", [folder("data"), folder("models"), file("lookup.csv", "lookup.csv", { status: "new" })]))

    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)

    const row = (await screen.findByText("lookup.csv")).closest("li")!
    expect(within(row).getByText("New")).toBeInTheDocument()
    expect(within(row).getByText("3 B")).toBeInTheDocument()
    expect(within(row).getByLabelText("Download lookup.csv")).toHaveAttribute("href", "/download/pipeline/lookup.csv")
    expect(screen.getByText("data")).toBeInTheDocument()
    expect(screen.getByTestId("files-crumb-root")).toHaveTextContent("rating/files")
    expect(mockList).toHaveBeenCalledWith("pipeline", "", expect.anything())
  })

  it("opens a folder and returns by the breadcrumb", async () => {
    mockList.mockImplementation((_place: string, path: string) =>
      Promise.resolve(path === "" ? listing("pipeline", "", [folder("data")]) : listing("pipeline", "data", [file("a.csv", "data/a.csv")])),
    )
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)

    fireEvent.click(await screen.findByText("data"))
    expect(await screen.findByText("a.csv")).toBeInTheDocument()

    fireEvent.click(screen.getByTestId("files-crumb-root"))
    await waitFor(() => expect(mockList).toHaveBeenLastCalledWith("pipeline", "", expect.anything()))
  })

  it("adds a dropped file to the open folder and reports its save", async () => {
    const { epoch } = useDataEpochStore.getState()
    mockList.mockResolvedValue(listing("pipeline", "", [folder("data")]))
    const clearCache = vi.spyOn(useSettingsStore.getState(), "clearFileListCache")
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("data")
    const csv = new File(["a,b"], "new.csv")

    dropFiles(screen.getByTestId("files-drop-zone"), [csv])

    await waitFor(() => expect(mockReport).toHaveBeenCalledWith(written("new.csv")))
    expect(mockUpload).toHaveBeenCalledWith("pipeline", "new.csv", csv, expect.objectContaining({ replaceRevision: null }))
    expect(clearCache).toHaveBeenCalled()
    // What was priced on the data before is stale.
    await waitFor(() => expect(useDataEpochStore.getState().epoch).toBe(epoch + 1))
  })

  it("drops on a folder row into that folder", async () => {
    mockList.mockImplementation((_place: string, path: string) =>
      Promise.resolve(path === "" ? listing("pipeline", "", [folder("data")]) : listing("pipeline", "data", [])),
    )
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    const row = (await screen.findByText("data")).closest("li")!
    const csv = new File(["x"], "rates.csv")

    dropFiles(row, [csv])

    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("pipeline", "data/rates.csv", csv, expect.anything()))
    expect(mockList).toHaveBeenCalledWith("pipeline", "data")
  })

  it("asks before replacing, then names the revision it replaces and refreshes previews", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", [file("rates.csv", "rates.csv")]))
    mockUpload.mockImplementation((_place: string, path: string) => ({ promise: Promise.resolve(written(path, true)), abort: vi.fn() }))
    const epoch = useNodeDataStore.getState().epoch
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("rates.csv")
    const csv = new File(["v2"], "rates.csv")

    dropFiles(screen.getByTestId("files-drop-zone"), [csv])

    const dialog = await screen.findByTestId("files-replace-dialog")
    expect(within(dialog).getByText("Replace rates.csv in rating/files?")).toBeInTheDocument()
    expect(within(dialog).getByText("The version it replaces stays in version history.")).toBeInTheDocument()
    expect(mockUpload).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("files-replace-confirm"))

    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("pipeline", "rates.csv", csv, expect.objectContaining({ replaceRevision: "3-rates.csv" })))
    await waitFor(() => expect(useNodeDataStore.getState().epoch).toBe(epoch + 1))
  })

  it("refuses a dropped folder and adds the files beside it", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", []))
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("Empty folder")
    const csv = new File(["x"], "loose.csv")

    dropFiles(screen.getByTestId("files-drop-zone"), [csv], ["exports_2025"])

    await waitFor(() => expect(mockUpload).toHaveBeenCalledTimes(1))
    expect(mockUpload).toHaveBeenCalledWith("pipeline", "loose.csv", csv, expect.anything())
    expect(useToastStore.getState().toasts.map((t) => t.text)).toContain("exports_2025 is a folder: drop the files inside it instead.")
  })

  it("sends a drop at Local files' root into data/", async () => {
    mockList.mockImplementation((_place: string, path: string) =>
      Promise.resolve(path === "" ? listing("local", "", [folder("data"), folder("outputs")], false) : listing("local", "data", [])),
    )
    render(<FilesPanel place="local" onClose={vi.fn()} />)
    await screen.findByText("outputs")
    expect(screen.getByText("Not versioned")).toBeInTheDocument()
    const parquet = new File(["rows"], "portfolio.parquet")

    dropFiles(screen.getByTestId("files-drop-zone"), [parquet])

    await waitFor(() => expect(mockUpload).toHaveBeenCalledWith("local", "data/portfolio.parquet", parquet, expect.anything()))
    expect(mockReport).not.toHaveBeenCalled()
  })

  it("takes no files in outputs/ and offers no Delete there", async () => {
    mockList.mockImplementation((_place: string, path: string) =>
      Promise.resolve(path === "" ? listing("local", "", [folder("data"), folder("outputs")], false) : listing("local", "outputs", [file("scored.parquet", "outputs/scored.parquet")], false)),
    )
    render(<FilesPanel place="local" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByText("outputs"))
    await screen.findByText("scored.parquet")

    dropFiles(screen.getByTestId("files-drop-zone"), [new File(["x"], "x.csv")])

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.text)).toContain("outputs only downloads: files can't be added to it."),
    )
    expect(mockUpload).not.toHaveBeenCalled()
    expect(screen.queryByLabelText("Delete scored.parquet")).not.toBeInTheDocument()
    expect(screen.getByTestId("files-upload")).toBeDisabled()
  })

  it("refuses a pipeline folder .gitignore ignores once, rather than file by file", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", [file("old.csv", "old.csv", { status: "ignored" })], false))
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("old.csv")
    expect(screen.getByTestId("files-upload")).toBeDisabled()
    expect(screen.getByText("Ignored")).toBeInTheDocument()

    dropFiles(screen.getByTestId("files-drop-zone"), [new File(["x"], "a.csv"), new File(["y"], "b.csv")])

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.text)).toEqual([
        "rating/files is ignored by .gitignore, so files added there couldn't be versioned.",
      ]),
    )
    expect(mockUpload).not.toHaveBeenCalled()
  })

  it("says a New file's version can't be recovered when it is replaced or deleted", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", [file("copied.csv", "copied.csv", { status: "new" })]))
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("copied.csv")

    dropFiles(screen.getByTestId("files-drop-zone"), [new File(["v2"], "copied.csv")])
    const replace = await screen.findByTestId("files-replace-dialog")
    expect(within(replace).getByText("The version it replaces isn't in version history yet, so it can't be recovered.")).toBeInTheDocument()
    fireEvent.click(within(replace).getByText("Cancel"))

    fireEvent.click(screen.getByLabelText("Delete copied.csv"))
    const remove = await screen.findByTestId("files-delete-dialog")
    expect(within(remove).getByText("It isn't in version history yet, so it can't be recovered.")).toBeInTheDocument()
  })

  it("marks a file edited since it was committed and says its changes can't be recovered", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", [file("edited.csv", "edited.csv", { status: "changed" })]))
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    const row = (await screen.findByText("edited.csv")).closest("li")!
    expect(within(row).getByText("Changed")).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText("Delete edited.csv"))
    const remove = await screen.findByTestId("files-delete-dialog")
    expect(within(remove).getByText("It has changes that aren't in version history yet, so they can't be recovered.")).toBeInTheDocument()
  })

  it("asks before deleting and deletes the revision shown", async () => {
    const { epoch } = useDataEpochStore.getState()
    mockList.mockResolvedValue(listing("pipeline", "data", [file("old.csv", "data/old.csv")]))
    mockDelete.mockResolvedValue({ path: "data/old.csv", git_sha: "def", identity_required: false, warnings: [] })
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)

    fireEvent.click(await screen.findByLabelText("Delete old.csv"))
    const dialog = await screen.findByTestId("files-delete-dialog")
    expect(within(dialog).getByText("It stays in version history.")).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("files-delete-confirm"))

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("pipeline", "data/old.csv", "3-old.csv"))
    await waitFor(() => expect(mockReport).toHaveBeenCalledWith(expect.objectContaining({ git_sha: "def" })))
    expect(useDataEpochStore.getState().epoch).toBe(epoch + 1)
  })

  it("shows a failed upload's message from the server", async () => {
    mockList.mockResolvedValue(listing("pipeline", "", []))
    const message = "rating/files/big.parquet is 412.0 MiB."
    const refusal = Object.assign(new Error("HTTP 413"), { status: 413, detail: message, rawDetail: message })
    mockUpload.mockImplementation(() => ({ promise: Promise.reject(refusal), abort: vi.fn() }))
    render(<FilesPanel place="pipeline" onClose={vi.fn()} />)
    await screen.findByText("Empty folder")

    dropFiles(screen.getByTestId("files-drop-zone"), [new File(["x"], "big.parquet")])

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.text)).toContain("big.parquet: rating/files/big.parquet is 412.0 MiB."),
    )
    expect(mockReport).not.toHaveBeenCalled()
  })
})
