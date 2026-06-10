import { execFileSync } from "node:child_process"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

export const repoRoot = resolve(__dirname, "..", "..")
export const e2eProjectRoot = resolve(repoRoot, ".tmp-e2e-project")

// Ceiling at the e2e root's parent so git can never discover a repository
// above the temp project (e.g. the real repo) if .tmp-e2e-project/.git vanishes.
const e2eProjectParent = resolve(e2eProjectRoot, "..")

function runGit(args: string[]): string {
  // An inherited GIT_DIR / GIT_WORK_TREE bypasses repository discovery entirely,
  // which would defeat both the ceiling and the toplevel assertion below.
  const env = { ...process.env, GIT_CEILING_DIRECTORIES: e2eProjectParent }
  delete env.GIT_DIR
  delete env.GIT_WORK_TREE
  return execFileSync("git", args, {
    cwd: e2eProjectRoot,
    env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  })
}

function comparablePath(value: string): string {
  const resolved = resolve(value)
  return process.platform === "win32" ? resolved.toLowerCase() : resolved
}

function assertGitToplevelIsE2eProject(): void {
  let toplevel: string
  try {
    toplevel = runGit(["rev-parse", "--show-toplevel"]).trim()
  } catch (error) {
    throw new Error(
      `Refusing to reset e2e project: git found no repository toplevel at ${e2eProjectRoot}`,
      { cause: error },
    )
  }
  if (comparablePath(toplevel) !== comparablePath(e2eProjectRoot)) {
    throw new Error(
      `Refusing to reset e2e project: git toplevel is ${toplevel} but expected ${e2eProjectRoot}`,
    )
  }
}

export function resetE2eProject(): void {
  assertGitToplevelIsE2eProject()
  runGit(["switch", "--force", "main"])
  runGit(["reset", "--hard", "main"])

  const branches = runGit(["for-each-ref", "--format=%(refname:short)", "refs/heads/"])
    .split(/\r?\n/)
    .map((branch) => branch.trim())
    .filter((branch) => branch && branch !== "main")

  for (const branch of branches) {
    runGit(["branch", "-D", branch])
  }

  runGit(["clean", "-fdx"])
}
