/**
 * A request is sent by its kind (specs/frontend-shared, "API client"), and no caller chooses:
 * a read is retried, every write is sent once, and a keyed write, whose body names an
 * operation key, leaves only through `sendHeld`, which holds it while its fate is unknown and
 * refuses anything newer before it. No code but the client opens a transport, so no request
 * goes round these two paths.
 */
import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  ApiError,
  cancelInputCacheJob,
  commitMilestone,
  deleteUtilityFile,
  gitDeleteBranch,
  patchPipelineSettings,
  post,
  postRawStream,
  putMlflowSettings,
  request,
  sendKeyed,
  updateUtilityFile,
} from "../client"
import { memoryHold, sendHeld, type KeyedTicket } from "../keyedWrites"
import { deleteProjectFile } from "../projectFiles"
import { deleteTestQuote, saveWorkbenchForm } from "../workbench"
import type { FormSpec } from "../types"

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
/** The client, the browser's one transport: every request is sent from it. Its upload part
 * is a module of its own so that it stays out of the initial bundle. */
const CLIENT = new Set(["frontend/src/api/client.ts", "frontend/src/api/clientUpload.ts"])

let mockFetch: ReturnType<typeof vi.fn>
const realFetch = globalThis.fetch

beforeEach(() => {
  mockFetch = vi.fn()
  globalThis.fetch = mockFetch as unknown as typeof fetch
})

afterEach(() => {
  globalThis.fetch = realFetch
})

function answered(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }))
}

/**
 * Every PUT, PATCH and DELETE wrapper, by name, with a call of it: each is sent once, whether it
 * names the revision it replaces or not (the scan below finds any wrapper missing here).
 */
const WRITE_WRAPPERS: Record<string, () => Promise<unknown>> = {
  updateUtilityFile: () => updateUtilityFile("helpers", "x = 1\n", "rev-1"),
  deleteUtilityFile: () => deleteUtilityFile("helpers", "rev-1"),
  deleteProjectFile: () => deleteProjectFile("pipeline", "data/a.csv", "rev-1"),
  saveWorkbenchForm: () => saveWorkbenchForm({ pages: [] } as unknown as FormSpec, "rev-1"),
  deleteTestQuote: () => deleteTestQuote("quote.json", "rev-1", "main"),
  patchPipelineSettings: () => patchPipelineSettings({ chunk_rows: 1000 } as never),
  putMlflowSettings: () => putMlflowSettings({} as never),
  cancelInputCacheJob: () => cancelInputCacheJob("job-1"),
  gitDeleteBranch: () => gitDeleteBranch("feature"),
}

describe("every write is sent once", () => {
  it.each([
    ...Object.entries(WRITE_WRAPPERS),
    ["commitMilestone, a POST", () => commitMilestone("msg", null)],
  ])("%s, whether its answer is lost or a server failure", async (_name, call) => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"))
    await expect(call()).rejects.toBeInstanceOf(TypeError)
    expect(mockFetch).toHaveBeenCalledTimes(1)

    mockFetch.mockReset()
    mockFetch.mockReturnValueOnce(answered({ detail: "Bad Gateway" }, 502))
    await expect(call()).rejects.toMatchObject({ status: 502 })
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it("a utility save whose answer was lost after it landed is reported as the transport's failure, never as a conflict", async () => {
    // The server applied it; the answer never reached the browser. A repeat would name a
    // revision that is gone and be refused as a change made since.
    mockFetch
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockReturnValueOnce(answered({ detail: "helpers.py changed since it was opened." }, 412))

    const failure = await updateUtilityFile("helpers", "x = 1\n", "rev-1").catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(TypeError)
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })

  it("a read is still retried", async () => {
    mockFetch
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockReturnValueOnce(answered({ files: [] }))
    await request("/api/utility")
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })
})

describe("the list of write wrappers", () => {
  it("names every PUT, PATCH and DELETE wrapper of the API modules", () => {
    const found = new Set<string>()
    const folder = resolve(SRC, "api")
    for (const entry of readdirSync(folder)) {
      if (!entry.endsWith(".ts")) continue
      const name = `api/${entry}`
      const file = ts.createSourceFile(name, readFileSync(resolve(folder, entry), "utf-8"), ts.ScriptTarget.Latest, true)
      for (const statement of file.statements) {
        if (!ts.isFunctionDeclaration(statement) || statement.name === undefined) continue
        if (!statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)) continue
        const body = statement.body?.getText(file) ?? ""
        if (/method:\s*"(PUT|PATCH|DELETE)"|\bdel</.test(body)) found.add(statement.name.text)
      }
    }
    expect([...found].sort()).toEqual(Object.keys(WRITE_WRAPPERS).sort())
  })
})

describe("a keyed write leaves only through sendHeld", () => {
  it("is refused by post and by request before anything is sent", async () => {
    expect(() => post("/api/pipeline/save", { operation: "save-1", graph: {} })).toThrow(
      "/api/pipeline/save names an operation key: a keyed write is sent through sendHeld.",
    )
    await expect(
      request("/api/modelling/mlflow/log", { method: "POST", body: JSON.stringify({ operation_id: "op-1" }) }),
    ).rejects.toThrow("names an operation key")
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("is refused by the raw stream before anything is sent", async () => {
    await expect(postRawStream("/api/pipeline/save", { operation: "save-1" })).rejects.toThrow(
      "/api/pipeline/save names an operation key: a keyed write is sent through sendHeld.",
    )
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("is refused by sendKeyed without a ticket sendHeld gave it", () => {
    expect(() => sendKeyed("/api/pipeline/save", { operation: "save-1" }, { sending: true } as KeyedTicket)).toThrow(
      "/api/pipeline/save is a keyed write: it is sent through sendHeld.",
    )
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("does not take a nested field named operation for a key", async () => {
    mockFetch.mockReturnValueOnce(answered({ ok: true }))
    await post("/api/assistant/apply", { changes: [{ operation: "update" }] })
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})

describe("sendHeld", () => {
  interface Save { operation: string; content: string }
  const send = (save: Save, ticket: KeyedTicket) => sendKeyed<unknown>("/api/pipeline/save", save, ticket)
  const first: Save = { operation: "save-first", content: "a" }

  it.each([
    ["no answer", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["an answer of 500", () => answered({ detail: "Operation failed." }, 500)],
    ["a gateway's 504 page", () => Promise.resolve(new Response("<html>Gateway Timeout</html>", { status: 504 }))],
    ["an answer that does not parse", () => Promise.resolve(new Response("<!DOCTYPE html>", { status: 200 }))],
  ])("keeps a write held after %s, and refuses one under another key before it", async (_case, answer) => {
    const hold = memoryHold<Save>((save) => save.operation, (save) => save.content)
    mockFetch.mockImplementationOnce(answer)

    await expect(sendHeld(hold, first, send)).rejects.toBeDefined()

    expect(hold.held()).toBe(first)
    await expect(sendHeld(hold, { operation: "save-newer", content: "b" }, send)).rejects.toThrow(
      "Another keyed write is held: it is sent again before anything newer.",
    )
    expect(mockFetch).toHaveBeenCalledTimes(1)

    mockFetch.mockReturnValueOnce(answered({ saved: true }))
    await expect(sendHeld(hold, first, send)).resolves.toEqual({ saved: true })
    expect(JSON.parse(mockFetch.mock.calls[1][1].body)).toEqual(first)
    expect(hold.held()).toBeNull()
  })

  it("refuses other content under the held write's key before it is sent", async () => {
    const hold = memoryHold<Save>((save) => save.operation, (save) => save.content)
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"))
    await expect(sendHeld(hold, first, send)).rejects.toBeInstanceOf(TypeError)

    await expect(sendHeld(hold, { operation: first.operation, content: "changed" }, send)).rejects.toThrow(
      "A held keyed write is sent again exactly: other content takes a new key.",
    )
    expect(mockFetch).toHaveBeenCalledTimes(1)
    expect(hold.held()).toBe(first)
  })

  it("lets a write go once an answer below 500 refuses it", async () => {
    const hold = memoryHold<Save>((save) => save.operation, (save) => save.content)
    mockFetch.mockReturnValueOnce(answered({ detail: "stale_document_revision: moved" }, 409))

    await expect(sendHeld(hold, first, send)).rejects.toBeInstanceOf(ApiError)

    expect(hold.held()).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// The scan: no transport but the client's
// ---------------------------------------------------------------------------

function sourceFiles(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(folder, entry.name)
    if (entry.isDirectory()) return entry.name === "__tests__" || entry.name === "generated" || entry.name === "testSupport" ? [] : sourceFiles(path)
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts") ? [path] : []
  })
}

/** Each place `source` opens a transport: a `fetch` call, an XMLHttpRequest, a beacon. */
function transports(source: string, name: string): string[] {
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true)
  const found: string[] = []
  const visit = (node: ts.Node) => {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1
    if (ts.isCallExpression(node)) {
      const callee = node.expression
      if (ts.isIdentifier(callee) && callee.text === "fetch") found.push(`${line}: fetch`)
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "fetch" && ts.isIdentifier(callee.expression) && ["window", "globalThis", "self"].includes(callee.expression.text)) {
        found.push(`${line}: fetch`)
      }
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "sendBeacon") found.push(`${line}: sendBeacon`)
    }
    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "XMLHttpRequest") {
      found.push(`${line}: XMLHttpRequest`)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

describe("the browser's code", () => {
  it("opens no transport but the client's", () => {
    const outside: string[] = []
    for (const path of sourceFiles(SRC)) {
      const name = relative(resolve(SRC, "../.."), path).split(sep).join("/")
      if (CLIENT.has(name)) continue
      for (const place of transports(readFileSync(path, "utf-8"), name)) outside.push(`${name}:${place}`)
    }
    expect(outside).toEqual([])
  })

  it("finds a transport opened outside the client", () => {
    const source = "const xhr = new XMLHttpRequest()\nawait fetch('/api/x', { method: 'PUT' })\nnavigator.sendBeacon('/api/y')\nthing.refetch()\n"
    expect(transports(source, "probe.ts")).toEqual(["1: XMLHttpRequest", "2: fetch", "3: sendBeacon"])
  })
})
