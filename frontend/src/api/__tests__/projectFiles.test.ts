import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { apiErrorMessage } from "../errors"
import { deleteProjectFile, listProjectFiles, projectFileDownloadUrl, uploadProjectFile } from "../projectFiles"

const WRITTEN = {
  path: "data/rates.csv",
  size: 4,
  revision: "4-1",
  replaced: false,
  status: null,
  git_sha: "abc",
  identity_required: false,
  warnings: [],
  uncaptured: [],
}

class FakeXHR {
  static sent: FakeXHR[] = []
  method = ""
  url = ""
  headers: Record<string, string> = {}
  body: unknown = undefined
  status = 0
  statusText = ""
  responseText = ""
  upload: {
    onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null
    onload: (() => void) | null
  } = { onprogress: null, onload: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  getResponseHeader(name: string) {
    return name.toLowerCase() === "content-type" ? "application/json" : null
  }
  send(body: unknown) {
    this.body = body
    FakeXHR.sent.push(this)
  }
  abort() {
    this.onabort?.()
  }
  respond(status: number, body: unknown) {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.onload?.()
  }
}

async function sentRequest(): Promise<FakeXHR> {
  await vi.waitFor(() => expect(FakeXHR.sent).toHaveLength(1))
  return FakeXHR.sent[0]
}

let mockFetch: ReturnType<typeof vi.fn>

describe("project files client", () => {
  beforeEach(() => {
    FakeXHR.sent = []
    vi.stubGlobal("XMLHttpRequest", FakeXHR)
    mockFetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", mockFetch)
  })
  afterEach(() => vi.unstubAllGlobals())

  it("sends the raw file with its place and path, reporting progress", async () => {
    const progress = vi.fn()
    const file = new Blob(["a,b\n"])
    const upload = uploadProjectFile("pipeline", "data/rates.csv", file, { onProgress: progress })

    const xhr = await sentRequest()
    expect(xhr.method).toBe("PUT")
    expect(xhr.url).toBe("/api/project-files/content?place=pipeline&path=data%2Frates.csv")
    expect(xhr.headers["Content-Type"]).toBe("application/octet-stream")
    expect(xhr.body).toBe(file)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 4 })
    expect(progress).toHaveBeenCalledWith(2, 4)
    xhr.respond(200, WRITTEN)

    await expect(upload.promise).resolves.toEqual(WRITTEN)
  })

  it("names the revision a replace replaces", async () => {
    uploadProjectFile("local", "data/x.csv", new Blob(["x"]), { replaceRevision: "1-99" })

    const xhr = await sentRequest()
    expect(xhr.url).toBe("/api/project-files/content?place=local&path=data%2Fx.csv&replace_revision=1-99")
  })

  it("turns a refusal into an ApiError carrying the server's message", async () => {
    const upload = uploadProjectFile("pipeline", "data/big.parquet", new Blob(["x"]))
    const xhr = await sentRequest()
    // The flat error contract: the detail is the plain message.
    xhr.respond(413, { detail: "data/big.parquet is too large." })

    const caught = await upload.promise.catch((error: unknown) => error)
    expect(caught).toMatchObject({ status: 413 })
    expect(apiErrorMessage(caught)).toBe("data/big.parquet is too large.")
  })

  it("stops on abort without replaying", async () => {
    const upload = uploadProjectFile("pipeline", "data/x.csv", new Blob(["x"]))
    await sentRequest()
    upload.abort()

    await expect(upload.promise).rejects.toMatchObject({ name: "AbortError" })
    expect(FakeXHR.sent).toHaveLength(1)
  })

  it("reports the answer to a body sent whole, a cancel after the last byte doing nothing", async () => {
    const answer = { ...WRITTEN, git_sha: null, identity_required: true, uncaptured: ["rating/files/data/x.csv"] }
    const upload = uploadProjectFile("pipeline", "data/x.csv", new Blob(["x"]))
    const xhr = await sentRequest()
    xhr.upload.onload?.()

    upload.abort()
    xhr.respond(200, answer)

    await expect(upload.promise).resolves.toEqual(answer)
  })

  it("lists a folder and deletes a file at its revision, never retrying the delete", async () => {
    const folder = { place: "pipeline", path: "data", root: "rating/files", writable: true, entries: [] }
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(folder), { status: 200, headers: { "Content-Type": "application/json" } }))
    await expect(listProjectFiles("pipeline", "data")).resolves.toEqual(folder)
    expect(mockFetch.mock.calls[0][0]).toBe("/api/project-files?place=pipeline&path=data")

    mockFetch.mockReset()
    mockFetch.mockRejectedValue(new TypeError("Failed to fetch"))
    await expect(deleteProjectFile("pipeline", "data/old.csv", "3-1")).rejects.toThrow()
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toBe("/api/project-files/content?place=pipeline&path=data%2Fold.csv&revision=3-1")
    expect(options.method).toBe("DELETE")
  })

  it("downloads from the same origin, so the session cookie goes with it", () => {
    expect(projectFileDownloadUrl("local", "outputs/scored.parquet")).toBe(
      "/api/project-files/content?place=local&path=outputs%2Fscored.parquet",
    )
  })
})
