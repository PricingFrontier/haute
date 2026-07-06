import { execFileSync } from "node:child_process"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

export const repoRoot = resolve(__dirname, "..", "..")
export const e2eProjectRoot = resolve(repoRoot, ".tmp-e2e-project")

// The healthy-clone working branch every test starts from. Mirrors what
// `set_working_branch(create=True)` would produce for the fixture's git user
// ("Haute E2E"): the working branch plus its `-save` ledger, HEAD on the
// ledger (the model's normal operating posture), and `.haute/state.json`
// recording the association. Without this the version-control startup
// readiness check (S27) sees a fresh repo on `main` with no recorded working
// branch → state "unset" → the WorkingBranchModal opens over the canvas and
// every node-interaction spec fails on "panel never appeared".
export const e2eWorkingBranch = "pricing/haute-e2e/work"
const e2eLedgerBranch = `${e2eWorkingBranch}-save`

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

// Recreate the configured working-branch pair after the scrub. Both branches
// point at main's commit, so the tree-equality invariant holds trivially and
// `working_branch_status` reports "ready" — a healthy clone fires no modal.
function seedWorkingBranch(): void {
  runGit(["branch", e2eWorkingBranch, "main"])
  runGit(["branch", e2eLedgerBranch, "main"])
  runGit(["switch", "--force", e2eLedgerBranch])
  mkdirSync(resolve(e2eProjectRoot, ".haute"), { recursive: true })
  writeFileSync(
    resolve(e2eProjectRoot, ".haute", "state.json"),
    JSON.stringify({ workingBranch: e2eWorkingBranch }, null, 2) + "\n",
  )
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

  // Version labels persist as version/* tags, which branch deletion leaves
  // behind; a leftover tag from an earlier run against a reused server would
  // make the engine reject a re-seed of the same fixed label.
  const tags = runGit(["tag", "--list", "version/*"])
    .split(/\r?\n/)
    .map((tag) => tag.trim())
    .filter(Boolean)

  for (const tag of tags) {
    runGit(["tag", "--delete", tag])
  }

  runGit(["clean", "-fdx"])
  seedWorkingBranch()
}

// Model a fresh, never-configured clone (first-run): HEAD on main, no working
// branches, no `.haute/state.json`. Used by specs that exercise the S27
// startup chooser itself. Call AFTER resetE2eProject() (the beforeEach).
export function unsetWorkingBranch(): void {
  assertGitToplevelIsE2eProject()
  runGit(["switch", "--force", "main"])
  for (const branch of [e2eWorkingBranch, e2eLedgerBranch]) {
    runGit(["branch", "-D", branch])
  }
  rmSync(resolve(e2eProjectRoot, ".haute", "state.json"), { force: true })
}
