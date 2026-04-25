import { execFileSync } from "node:child_process"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

export const repoRoot = resolve(__dirname, "..", "..")
export const e2eProjectRoot = resolve(repoRoot, ".tmp-e2e-project")

function runGit(args: string[]): string {
  return execFileSync("git", args, {
    cwd: e2eProjectRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  })
}

export function resetE2eProject(): void {
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
