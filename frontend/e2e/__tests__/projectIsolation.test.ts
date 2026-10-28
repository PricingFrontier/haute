import { resolve } from "node:path"

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const execFileSyncMock = vi.hoisted(() => vi.fn())
const mkdirSyncMock = vi.hoisted(() => vi.fn())
const rmSyncMock = vi.hoisted(() => vi.fn())
const writeFileSyncMock = vi.hoisted(() => vi.fn())

vi.mock("node:child_process", () => ({
  default: { execFileSync: execFileSyncMock },
  execFileSync: execFileSyncMock,
}))

// The reset helper writes the seeded .haute/state.json itself — mock the fs
// layer so unit runs never touch the real .tmp-e2e-project directory.
vi.mock("node:fs", () => ({
  default: { mkdirSync: mkdirSyncMock, rmSync: rmSyncMock, writeFileSync: writeFileSyncMock },
  mkdirSync: mkdirSyncMock,
  rmSync: rmSyncMock,
  writeFileSync: writeFileSyncMock,
}))

import {
  e2eGit,
  e2eProjectRoot,
  e2eWorkingBranch,
  repoRoot,
  resetE2eProject,
  unsetWorkingBranch,
} from "../projectIsolation"

const e2eLedgerBranch = `${e2eWorkingBranch}-save`
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
  hauteRefs = "",
}: {
  toplevel: string
  branches?: string
  hauteRefs?: string
}): void {
  execFileSyncMock.mockImplementation((_file: string, args: string[]) => {
    if (args[0] === "rev-parse") return `${toplevel}\n`
    if (args[0] === "for-each-ref") return args.at(-1) === "refs/haute/" ? hauteRefs : branches
    return ""
  })
}

function gitCalls(): string[][] {
  return execFileSyncMock.mock.calls.map((call) => call[1] as string[])
}

function fullResetSequence(branchDeletes: string[][], refDeletes: string[][] = []): string[][] {
  return [
    ["rev-parse", "--show-toplevel"],
    ["switch", "--force", "main"],
    ["reset", "--hard", "main"],
    ["for-each-ref", "--format=%(refname:short)", "refs/heads/"],
    ...branchDeletes,
    // Version-label tag scrub (the mocked list reports none to delete).
    ["tag", "--list", "version/*"],
    // Deleted branches' trash pins.
    ["for-each-ref", "--format=%(refname)", "refs/haute/"],
    ...refDeletes,
    // The store's process-liveness marker is held open by the running backend
    // for its whole life, so the scrub must exclude it or it can never pass.
    ["clean", "-fdx", "-e", "/.haute_cache/inputs/.processes/"],
    // Healthy-clone reseed: working branch + its ledger, HEAD on the ledger.
    ["branch", e2eWorkingBranch, "main"],
    ["branch", e2eLedgerBranch, "main"],
    ["switch", "--force", e2eLedgerBranch],
  ]
}

describe("resetE2eProject", () => {
  beforeEach(() => {
    execFileSyncMock.mockReset()
    mkdirSyncMock.mockReset()
    rmSyncMock.mockReset()
    writeFileSyncMock.mockReset()
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

  it("deletes the pins a deleted branch left under refs/haute/", () => {
    mockGit({
      toplevel: e2eProjectRoot,
      hauteRefs:
        "refs/haute/trash/old@20261028T071200Z/ledger\nrefs/haute/trash/old@20261028T071200Z/working\n",
    })

    resetE2eProject()

    expect(gitCalls()).toEqual(
      fullResetSequence(
        [],
        [
          ["update-ref", "-d", "refs/haute/trash/old@20261028T071200Z/ledger"],
          ["update-ref", "-d", "refs/haute/trash/old@20261028T071200Z/working"],
        ],
      ),
    )
  })

  it("records the seeded working branch in .haute/state.json", () => {
    mockGit({ toplevel: e2eProjectRoot })

    resetE2eProject()

    expect(mkdirSyncMock).toHaveBeenCalledWith(resolve(e2eProjectRoot, ".haute"), {
      recursive: true,
    })
    expect(writeFileSyncMock).toHaveBeenCalledWith(
      resolve(e2eProjectRoot, ".haute", "state.json"),
      JSON.stringify({ workingBranch: e2eWorkingBranch }, null, 2) + "\n",
    )
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
    expect(writeFileSyncMock).not.toHaveBeenCalled()
  })

  it("throws without issuing destructive commands when rev-parse finds no repository", () => {
    execFileSyncMock.mockImplementation(() => {
      throw new Error("fatal: not a git repository (or any of the parent directories): .git")
    })

    expect(() => resetE2eProject()).toThrow(e2eProjectRoot)
    expect(gitCalls()).toEqual([["rev-parse", "--show-toplevel"]])
    expect(writeFileSyncMock).not.toHaveBeenCalled()
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

  it("retries the scrub while the previous test's server work still holds a file", () => {
    let cleanAttempts = 0
    execFileSyncMock.mockImplementation((_file: string, args: string[]) => {
      if (args[0] === "rev-parse") return e2eProjectRoot
      if (args[0] === "for-each-ref") return "main"
      if (args[0] === "clean") {
        cleanAttempts += 1
        if (cleanAttempts < 3) {
          throw new Error("warning: failed to remove .haute_cache/working/.json_x.build.lock: Invalid argument")
        }
      }
      return ""
    })
    const wait = vi.spyOn(Atomics, "wait").mockReturnValue("timed-out")

    resetE2eProject()

    const cleans = gitCalls().filter((args) => args[0] === "clean")
    expect(cleans).toHaveLength(3)
    expect(wait).toHaveBeenCalledTimes(2)
    expect(gitCalls().slice(-3)).toEqual([
      ["branch", e2eWorkingBranch, "main"],
      ["branch", e2eLedgerBranch, "main"],
      ["switch", "--force", e2eLedgerBranch],
    ])
    wait.mockRestore()
  })

  it("keeps retrying every 250 ms and fails with git's error at the 30 s deadline", () => {
    const lockError = new Error("warning: failed to remove .build.lock: Invalid argument")
    let now = 0
    const cleanTimes: number[] = []
    execFileSyncMock.mockImplementation((_file: string, args: string[]) => {
      if (args[0] === "rev-parse") return e2eProjectRoot
      if (args[0] === "for-each-ref") return "main"
      if (args[0] === "clean") {
        cleanTimes.push(now)
        throw lockError
      }
      return ""
    })
    const clock = vi.spyOn(Date, "now").mockImplementation(() => now)
    const wait = vi
      .spyOn(Atomics, "wait")
      .mockImplementation((_array, _index, _value, timeout) => {
        now += timeout ?? 0
        return "timed-out"
      })

    let caught: unknown
    try {
      resetE2eProject()
    } catch (error) {
      caught = error
    }

    expect(cleanTimes[0]).toBe(0)
    expect(cleanTimes.at(-1)).toBe(30_000)
    expect(cleanTimes).toHaveLength(121)
    expect(wait.mock.calls.every((call) => call[3] === 250)).toBe(true)
    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toContain("git clean -fdx")
    expect((caught as Error).cause).toBe(lockError)
    expect(gitCalls().some((args) => args[0] === "branch" && args[1] === e2eWorkingBranch)).toBe(false)
    wait.mockRestore()
    clock.mockRestore()
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

describe("e2eGit", () => {
  beforeEach(() => {
    execFileSyncMock.mockReset()
  })

  it("runs the command in the e2e project after verifying the toplevel", () => {
    mockGit({ toplevel: e2eProjectRoot })

    e2eGit(["switch", "--detach", "main"])

    expect(gitCalls()).toEqual([
      ["rev-parse", "--show-toplevel"],
      ["switch", "--detach", "main"],
    ])
  })

  it("refuses to run anything when git finds another repository", () => {
    mockGit({ toplevel: repoRoot })

    expect(() => e2eGit(["switch", "--detach", "main"])).toThrow(e2eProjectRoot)
    expect(gitCalls()).toEqual([["rev-parse", "--show-toplevel"]])
  })
})

describe("unsetWorkingBranch", () => {
  beforeEach(() => {
    execFileSyncMock.mockReset()
    rmSyncMock.mockReset()
  })

  it("returns the clone to the never-configured first-run state", () => {
    mockGit({ toplevel: e2eProjectRoot })

    unsetWorkingBranch()

    expect(gitCalls()).toEqual([
      ["rev-parse", "--show-toplevel"],
      ["switch", "--force", "main"],
      ["branch", "-D", e2eWorkingBranch],
      ["branch", "-D", e2eLedgerBranch],
    ])
    expect(rmSyncMock).toHaveBeenCalledWith(resolve(e2eProjectRoot, ".haute", "state.json"), {
      force: true,
    })
  })

  it("verifies the toplevel before deleting anything", () => {
    mockGit({ toplevel: repoRoot })

    expect(() => unsetWorkingBranch()).toThrow(e2eProjectRoot)
    expect(gitCalls()).toEqual([["rev-parse", "--show-toplevel"]])
    expect(rmSyncMock).not.toHaveBeenCalled()
  })
})
