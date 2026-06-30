import { resolve } from "node:path"

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const execFileSyncMock = vi.hoisted(() => vi.fn())

vi.mock("node:child_process", () => ({
  default: { execFileSync: execFileSyncMock },
  execFileSync: execFileSyncMock,
}))

import { e2eProjectRoot, repoRoot, resetE2eProject } from "../projectIsolation"

const realPlatform = process.platform

function stubPlatform(platform: NodeJS.Platform): void {
  Object.defineProperty(process, "platform", {
    value: platform,
    writable: false,
    enumerable: true,
    configurable: true,
  })
}

function mockGit({
  toplevel,
  branches = "main\n",
}: {
  toplevel: string
  branches?: string
}): void {
  execFileSyncMock.mockImplementation((_file: string, args: string[]) => {
    if (args[0] === "rev-parse") return `${toplevel}\n`
    if (args[0] === "for-each-ref") return branches
    return ""
  })
}

function gitCalls(): string[][] {
  return execFileSyncMock.mock.calls.map((call) => call[1] as string[])
}

function fullResetSequence(branchDeletes: string[][]): string[][] {
  return [
    ["rev-parse", "--show-toplevel"],
    ["switch", "--force", "main"],
    ["reset", "--hard", "main"],
    ["for-each-ref", "--format=%(refname:short)", "refs/heads/"],
    ...branchDeletes,
    ["clean", "-fdx"],
  ]
}

describe("resetE2eProject", () => {
  beforeEach(() => {
    execFileSyncMock.mockReset()
  })

  afterEach(() => {
    stubPlatform(realPlatform)
    vi.unstubAllEnvs()
  })

  it("verifies the toplevel before running the destructive sequence", () => {
    mockGit({ toplevel: e2eProjectRoot, branches: "main\nfeature-a\nfeature-b\n" })

    resetE2eProject()

    expect(gitCalls()).toEqual(
      fullResetSequence([
        ["branch", "-D", "feature-a"],
        ["branch", "-D", "feature-b"],
      ]),
    )
    expect(execFileSyncMock.mock.calls.every((call) => call[0] === "git")).toBe(true)
  })

  it("throws before issuing any destructive command when the toplevel is the parent repo", () => {
    const reportedToplevel = repoRoot.replaceAll("\\", "/")
    mockGit({ toplevel: reportedToplevel })

    let message = ""
    try {
      resetE2eProject()
    } catch (error) {
      message = (error as Error).message
    }

    expect(message).toContain(reportedToplevel)
    expect(message).toContain(e2eProjectRoot)
    expect(gitCalls()).toEqual([["rev-parse", "--show-toplevel"]])
  })

  it("throws without issuing destructive commands when rev-parse finds no repository", () => {
    execFileSyncMock.mockImplementation(() => {
      throw new Error("fatal: not a git repository (or any of the parent directories): .git")
    })

    expect(() => resetE2eProject()).toThrow(e2eProjectRoot)
    expect(gitCalls()).toEqual([["rev-parse", "--show-toplevel"]])
  })

  it("accepts a matching toplevel reported with forward slashes", () => {
    mockGit({ toplevel: e2eProjectRoot.replaceAll("\\", "/") })

    resetE2eProject()

    expect(gitCalls()).toEqual(fullResetSequence([]))
  })

  it("accepts a matching toplevel that differs only by case on win32", () => {
    stubPlatform("win32")
    mockGit({ toplevel: e2eProjectRoot.toUpperCase().replaceAll("\\", "/") })

    resetE2eProject()

    expect(gitCalls()).toEqual(fullResetSequence([]))
  })

  it("pins cwd and GIT_CEILING_DIRECTORIES on every git invocation", () => {
    vi.stubEnv("GIT_DIR", resolve(repoRoot, ".git"))
    vi.stubEnv("GIT_WORK_TREE", repoRoot)
    mockGit({ toplevel: e2eProjectRoot, branches: "main\nstale\n" })

    resetE2eProject()

    expect(execFileSyncMock.mock.calls.length).toBeGreaterThanOrEqual(5)
    for (const call of execFileSyncMock.mock.calls) {
      const options = call[2] as { cwd?: string; env?: Record<string, string | undefined> }
      expect(options.cwd).toBe(e2eProjectRoot)
      expect(options.env?.GIT_CEILING_DIRECTORIES).toBe(resolve(e2eProjectRoot, ".."))
      expect(options.env).not.toHaveProperty("GIT_DIR")
      expect(options.env).not.toHaveProperty("GIT_WORK_TREE")
    }
  })
})
