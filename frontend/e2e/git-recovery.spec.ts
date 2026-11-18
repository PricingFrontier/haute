import { spawn } from "node:child_process"
import { closeSync, existsSync, openSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, type Page } from "@playwright/test"

import {
  canvasNode,
  checkedOutBranch,
  createBranchInManager,
  expectOnBranch,
  ledgerOf,
  openGitPanel,
  pipelineOnDisk,
  pipelinePath,
  pipelineSavedOn,
  renameAndSave,
  renameNode,
  switchInManager,
  toast,
  test,
} from "./gitJourneys"
import { e2eGit, e2eProjectRoot, e2eWorkingBranch, resetE2eProject } from "./projectIsolation"

// Getting back on track after something happened outside Haute (GIT-R10,
// GIT-R13): a checkout of an earlier version in a terminal, a switch to
// another branch, and files another program held open through a git action.
// The mistakes covered are the ones the screens warn about: choosing the
// existing branch when the version open should be kept, closing the chooser,
// saving before choosing, unusable names, and saving or committing while an
// operation the server stopped part-way is still recorded.

const work = e2eWorkingBranch
const indicator = (page: Page) => page.getByTestId("toolbar-branch-indicator")
const chooser = (page: Page) => page.getByTestId("working-branch-modal")
const branchSelect = (page: Page) => page.getByTestId("working-branch-select")
const newBranchName = (page: Page) => page.getByTestId("working-branch-new")
const useThisBranch = (page: Page) => page.getByTestId("working-branch-confirm")

test.describe("a project left on an earlier version outside Haute", () => {
  test.beforeEach(async ({ page }) => {
    resetE2eProject()
    await page.goto("/")
    await renameAndSave(page, "enriched", "enriched_v1")
    // In a terminal: git checkout of the first version.
    e2eGit(["switch", "--force", "--detach", "main"])
    await page.reload()
  })

  test("the toolbar says so and the chooser keeps that version on a new branch", async ({
    page,
  }) => {
    await expect(indicator(page)).toHaveAttribute("data-branch-state", "detached")
    await expect(indicator(page)).toHaveText("Viewing an earlier version")
    await expect(chooser(page)).toBeVisible()
    // A new branch is the choice that keeps the version open now.
    await expect(branchSelect(page)).toHaveValue("__create__")
    await expect(page.getByTestId("working-branch-detached")).toContainText(
      "the version open now is kept only by a new branch",
    )
    await expect(chooser(page)).not.toContainText(/detached|ledger|\brepo\b/i)
    await expect(useThisBranch(page)).toBeDisabled()

    await newBranchName(page).fill("from-the-first")
    await useThisBranch(page).click()

    await expect(toast(page, "Working branch set to from-the-first")).toBeVisible()
    await expectOnBranch(page, "from-the-first")
    await expect(canvasNode(page, "enriched")).toBeVisible()
    expect(pipelineOnDisk()).toContain("def enriched(")
    expect(checkedOutBranch()).toBe(ledgerOf("from-the-first"))
    expect(pipelineSavedOn(work)).toContain("def enriched_v1(")
  })

  test("choosing the existing branch instead opens its latest save", async ({ page }) => {
    await branchSelect(page).selectOption(work)
    await useThisBranch(page).click()

    await expectOnBranch(page, work)
    await expect(canvasNode(page, "enriched_v1")).toBeVisible()
    expect(pipelineOnDisk()).toContain("def enriched_v1(")
  })

  test("closing the chooser leaves the warning in the toolbar, which opens it again", async ({
    page,
  }) => {
    await chooser(page).getByRole("button", { name: "Cancel" }).click()
    await expect(chooser(page)).toHaveCount(0)
    await expect(indicator(page)).toHaveText("Viewing an earlier version")
    await expect(canvasNode(page, "enriched")).toBeVisible()

    await indicator(page).click()
    await expect(chooser(page)).toBeVisible()
  })

  test("saving before choosing asks for a branch, then saves onto it", async ({ page }) => {
    await chooser(page).getByRole("button", { name: "Cancel" }).click()
    await renameNode(page, "enriched", "enriched_late")
    await page.getByRole("button", { name: "Save", exact: true }).click()

    await expect(chooser(page)).toBeVisible()
    await newBranchName(page).fill("late-work")
    await useThisBranch(page).click()

    await expectOnBranch(page, "late-work")
    await expect.poll(() => pipelineSavedOn("late-work")).toContain("def enriched_late(")
  })

  test("an unusable name is refused and the chooser stays open to put it right", async ({
    page,
  }) => {
    await newBranchName(page).fill("   ")
    await expect(useThisBranch(page)).toBeDisabled()

    await newBranchName(page).fill("motor pricing")
    await useThisBranch(page).click()
    await expect(
      toast(
        page,
        "Could not set working branch: 'motor pricing' can't be used as a branch name: it contains a space.",
      ),
    ).toBeVisible()
    await expect(chooser(page)).toBeVisible()
    await expect(newBranchName(page)).toHaveValue("motor pricing")

    await newBranchName(page).fill("motor-pricing")
    await useThisBranch(page).click()
    await expectOnBranch(page, "motor-pricing")
  })
})

test.describe("a project switched to another branch outside Haute", () => {
  test.beforeEach(async ({ page }) => {
    resetE2eProject()
    await page.goto("/")
    await renameAndSave(page, "enriched", "enriched_v1")
    // In a terminal: git switch main.
    e2eGit(["switch", "--force", "main"])
    await page.reload()
  })

  const modal = (page: Page) => page.getByTestId("divergence-modal")

  test("Go back returns to the working branch and its latest save", async ({ page }) => {
    await expect(indicator(page)).toHaveAttribute("data-branch-state", "divergent")
    await expect(modal(page)).toContainText(`Your working branch is ${work}, but the project is`)
    await expect(modal(page)).toContainText("currently on main")
    // main can't be a working branch, so staying there isn't offered.
    await expect(modal(page).getByRole("radio").nth(1)).toBeDisabled()

    await page.getByTestId("divergence-confirm").click()

    await expect(toast(page, `Working branch set to ${work}`)).toBeVisible()
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "enriched_v1")).toBeVisible()
  })

  test("Open the branch manager hands the choice to the git panel", async ({ page }) => {
    await modal(page).getByText("Open the branch manager").click()
    await page.getByTestId("divergence-confirm").click()

    await expect(modal(page)).toHaveCount(0)
    await expect(page.getByTestId("git-panel")).toBeVisible()
  })
})

test.describe("an operation the server stopped part-way", () => {
  const recordPath = resolve(e2eProjectRoot, ".haute", "operation.json")
  let current = ""
  let restored = ""

  // What a restore leaves when the server stops after its ledger moved: the ledger
  // holds the restored version of rating/main.py, the file on disk is still the one
  // before, and the restore's record is there.
  test.beforeEach(() => {
    resetE2eProject()
    current = readFileSync(pipelinePath, "utf8")
    // A preamble comment: a version a save keeps, so the document stays ready to save.
    restored = current.replace("\npipeline = haute.Pipeline(", "\n# the restored version\npipeline = haute.Pipeline(")
    if (restored === current) throw new Error("rating/main.py has no pipeline constructor to mark")
    const ledger = ledgerOf(work)
    const before = e2eGit(["rev-parse", ledger]).trim()
    writeFileSync(pipelinePath, restored)
    e2eGit(["add", "rating/main.py"])
    const tree = e2eGit(["write-tree"]).trim()
    const commit = e2eGit(["commit-tree", tree, "-p", before, "-m", "Restored"]).trim()
    e2eGit(["reset", "-q"])
    writeFileSync(pipelinePath, current)
    e2eGit(["update-ref", `refs/heads/${ledger}`, commit, before])
    writeFileSync(
      recordPath,
      JSON.stringify({
        operation: "restore",
        at: new Date().toISOString(),
        initial: { HEAD: `ref: refs/heads/${ledger}`, [`refs/heads/${ledger}`]: before },
        steps: [
          { kind: "refs", sets: { [`refs/heads/${ledger}`]: commit } },
          { kind: "files", sets: {} },
        ],
        commit_point: 1,
        files: ["rating/main.py"],
        wipes_caches: true,
      }),
    )
  })

  test("the toolbar names it, and Retry finishes it", async ({ page }) => {
    await page.goto("/")
    await expect(indicator(page)).toHaveAttribute("data-branch-state", "interrupted")
    await expect(indicator(page)).toContainText("Unfinished")
    await expect(chooser(page)).toHaveCount(0) // nothing to choose: Retry first

    const retry = page.getByTestId("branch-indicator-retry-interrupted")
    await retry.focus()
    await expect(page.getByRole("tooltip")).toContainText(
      "Restoring a version stopped part-way: rating/main.py.",
    )

    await retry.click()
    await expect(toast(page, "Back in step with version history.")).toBeVisible()
    await expectOnBranch(page, work)
    expect(readFileSync(pipelinePath, "utf8")).toBe(restored)
    expect(e2eGit(["status", "--porcelain", "--", "rating/main.py"])).toBe("")
    expect(existsSync(recordPath)).toBe(false)
  })

  test("Save and Commit name what stopped and ask for Retry, not a branch", async ({ page }) => {
    await page.goto("/")
    await expect(indicator(page)).toHaveAttribute("data-branch-state", "interrupted")
    const before = e2eGit(["rev-parse", ledgerOf(work)])
    const refusal = "Restoring a version stopped part-way: rating/main.py. Choose Retry in the toolbar"

    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(toast(page, `${refusal} before saving.`)).toBeVisible()
    await expect(chooser(page)).toHaveCount(0)

    await page.getByTestId("toolbar-save-commit").click()
    await expect(toast(page, `${refusal} before committing.`)).toBeVisible()
    await expect(chooser(page)).toHaveCount(0)
    await expect(page.getByTestId("milestone-commit-modal")).toHaveCount(0)

    // Nothing reached the history: a save now would record the version before.
    expect(e2eGit(["rev-parse", ledgerOf(work)])).toBe(before)
    expect(readFileSync(pipelinePath, "utf8")).toBe(current)
  })
})

// Hold *path* the way another program holds an open file on Windows, until the
// returned release is called: a process opens it sharing it for reading ("read",
// as some editors do) or not at all ("none", as Excel holds an open CSV).
async function holdOpen(path: string, share: "read" | "none"): Promise<() => Promise<void>> {
  const holder = spawn(
    "powershell",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      `$f = [System.IO.File]::Open('${path}', 'Open', 'Read', '${share === "read" ? "Read" : "None"}'); ` +
        "[Console]::Out.WriteLine('held'); [Console]::Out.Flush(); Start-Sleep -Seconds 600",
    ],
    { stdio: ["ignore", "pipe", "inherit"] },
  )
  await new Promise<void>((resolveHeld, reject) => {
    holder.stdout.on("data", (chunk: Buffer) => {
      if (chunk.toString().includes("held")) resolveHeld()
    })
    holder.on("exit", (code) => reject(new Error(`the file holder exited early (${code})`)))
  })
  return async () => {
    holder.kill()
    // Released once the file opens for writing again.
    await expect
      .poll(() => {
        try {
          closeSync(openSync(path, "r+"))
          return true
        } catch {
          return false
        }
      })
      .toBe(true)
  }
}

// Only on Windows does an open file stop git replacing it (the CI lane runs
// Linux, so these run on Windows machines).
test.describe("a file held open during a switch", () => {
  test.skip(process.platform !== "win32", "an open file blocks git only on Windows")

  test.beforeEach(() => {
    resetE2eProject()
  })

  for (const share of ["none", "read"] as const) {
    test(`a switch over it stops, names it and changes nothing, then goes through once it is closed (sharing ${share})`, async ({
      page,
    }) => {
      await page.goto("/")
      await renameAndSave(page, "enriched", "enriched_v1")
      await openGitPanel(page)
      await createBranchInManager(page, "other")

      const release = await holdOpen(pipelinePath, share)
      try {
        await switchInManager(page, "other")
        const refusal = toast(page, /open in another program, so Haute could not replace/)
        await expect(refusal).toContainText("rating/main.py")
        await expect(refusal).toContainText("Close")
        await expect(page.getByTestId("branch-manager-error")).toContainText("rating/main.py")
      } finally {
        await release()
      }

      if (share === "none") {
        // Held too tightly for git to read, it could not be checked: the toolbar
        // asks for Retry once it is closed.
        await expect(indicator(page)).toHaveAttribute("data-branch-state", "interrupted")
        await page.getByTestId("branch-indicator-retry-interrupted").click()
        await expect(toast(page, "Back in step with version history.")).toBeVisible()
      }

      // Nothing half-switched: the same branch and the same files.
      await expectOnBranch(page, work)
      expect(checkedOutBranch()).toBe(ledgerOf(work))
      expect(e2eGit(["status", "--porcelain", "--untracked-files=no"])).toBe("")
      await expect(canvasNode(page, "enriched_v1")).toBeVisible()

      // Closed: the same switch goes through.
      await switchInManager(page, "other")
      await expectOnBranch(page, "other")
      await expect(canvasNode(page, "enriched")).toBeVisible()
    })
  }
})
