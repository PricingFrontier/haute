/**
 * An operation that replaces the project's files, refused over files changed outside Haute,
 * asks whether to record them or put them back and is asked for again with that choice; and
 * every call of such an operation goes through that one answer.
 */
import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../api/client"
import useQuestionStore from "../../stores/useQuestionStore"
import { clearingTheWay } from "../clearingTheWay"

const MESSAGE =
  "rating/files/data/rates.csv has changed on disk outside Haute since the last save. Before switching to 'other', record it as a save on this branch, or put it back as version history has it."

function refusedOver(files: string[]): ApiError {
  const detail = { error_code: "unrecorded_files", message: MESSAGE, files, token: "token-1" }
  // As the client throws a structured refusal: its detail parsed, beside its text.
  return new ApiError("HTTP 409", 409, JSON.stringify(detail), { detail }, detail)
}

afterEach(() => {
  useQuestionStore.getState().answer(null)
})

describe("clearingTheWay", () => {
  it.each([
    ["other", "record"],
    ["confirm", "put-back"],
  ] as const)("answered %s, asks for the operation again with %s and the files", async (answer, choice) => {
    const files = ["rating/files/data/rates.csv"]
    const operation = vi.fn().mockRejectedValueOnce(refusedOver(files)).mockResolvedValueOnce("switched")

    const done = clearingTheWay(operation)
    await vi.waitFor(() => expect(useQuestionStore.getState().question).not.toBeNull())
    const question = useQuestionStore.getState().question
    expect(question).toMatchObject({ text: MESSAGE, list: files, confirm: "Put back", other: "Record", danger: true })
    useQuestionStore.getState().answer({ choice: answer, reason: "" })

    await expect(done).resolves.toBe("switched")
    expect(operation.mock.calls).toEqual([[], [{ settle: { choice, files, token: "token-1" } }]])
  })

  it("cancelled, leaves the refusal standing and asks nothing again", async () => {
    const refusal = refusedOver(["rates.csv"])
    const operation = vi.fn().mockRejectedValue(refusal)

    const done = clearingTheWay(operation)
    await vi.waitFor(() => expect(useQuestionStore.getState().question).not.toBeNull())
    useQuestionStore.getState().answer(null)

    await expect(done).rejects.toBe(refusal)
    expect(operation).toHaveBeenCalledTimes(1)
  })

  it("passes any other failure through without a question", async () => {
    const failure = new ApiError("Operation failed", 400, "{}")
    const operation = vi.fn().mockRejectedValue(failure)

    await expect(clearingTheWay(operation)).rejects.toBe(failure)
    expect(useQuestionStore.getState().question).toBeNull()
  })
})

// ── every call of an operation that replaces the files is answered the one way ─────────────

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const REPLACING_FILES = new Set([
  "setWorkingBranch",
  "createWorkingBranch",
  "gitArchiveBranch",
  "gitFastForward",
  "gitBranchAway",
  "pullGitUpstream",
])

function sourceFiles(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(folder, entry.name)
    if (entry.isDirectory()) return entry.name === "__tests__" || entry.name === "generated" ? [] : sourceFiles(path)
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : []
  })
}

it("every call of an operation that replaces the project's files goes through clearingTheWay", () => {
  const outside: string[] = []
  for (const path of sourceFiles(SRC)) {
    const name = relative(SRC, path)
    if (name === "api/client.ts") continue
    const file = ts.createSourceFile(name, readFileSync(path, "utf-8"), ts.ScriptTarget.Latest, true)
    const visit = (node: ts.Node, cleared: boolean) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
        const callee = node.expression.text
        if (REPLACING_FILES.has(callee) && !cleared) outside.push(`${name}: ${callee}`)
        if (callee === "clearingTheWay") {
          node.arguments.forEach((argument) => visit(argument, true))
          return
        }
      }
      ts.forEachChild(node, (child) => visit(child, cleared))
    }
    visit(file, false)
  }
  expect(outside).toEqual([])
})
