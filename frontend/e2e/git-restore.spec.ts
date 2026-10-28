import { expect, type Page } from "@playwright/test"

import {
  branchExists,
  canvasNode,
  checkedOutBranch,
  commitMilestone,
  createBranchInManager,
  expectOnBranch,
  ledgerOf,
  openGitPanel,
  pipelineOnDisk,
  pipelineSavedOn,
  renameAndSave,
  renameNode,
  saveSubjects,
  toast,
  test,
} from "./gitJourneys"
import { e2eGit, e2eWorkingBranch, resetE2eProject } from "./projectIsolation"

// Restoring a version and starting a branch from one (GIT-R09, GIT-R11), with
// the mistakes an analyst can make on the way: unsaved edits at the prompt,
// changing their mind, clicking twice, restoring the wrong version, and branch
// names that cannot be used. Each test builds its history through the editor
// from the seeded root ("Initial scaffold", where the node is "priced").

const work = e2eWorkingBranch

function pendingRows(page: Page) {
  return page.getByTestId("git-panel-pending-save")
}

function rootMilestone(page: Page) {
  return page.getByTestId("git-panel-milestone").last()
}

async function restoreFrom(row: ReturnType<Page["getByTestId"]>, page: Page): Promise<void> {
  await row.getByTestId("git-panel-restore").click()
  await expect(page.getByTestId("restore-confirm-modal")).toBeVisible()
}

test.describe("restoring a version", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("an earlier save comes back as a new save on the same branch", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameAndSave(page, "priced_v1", "priced_v2")

    await openGitPanel(page)
    await expect(page.getByTestId("git-panel-pending")).toContainText(
      "Saves since the last milestone (2)",
    )
    await expect(pendingRows(page)).toHaveCount(2)
    // Plain words: none of git's own vocabulary the panel used to show.
    await expect(page.getByTestId("git-panel")).not.toContainText(/detached|ledger|\brepo\b|\bfold/i)
    // The newest save is the version open now: nothing to restore there.
    await expect(pendingRows(page).first().getByTestId("git-panel-restore")).toHaveCount(0)

    await restoreFrom(pendingRows(page).last(), page)
    await expect(page.getByTestId("restore-confirm-modal")).toContainText(
      "Your branch carries on from this version",
    )
    await page.getByTestId("restore-confirm").click()

    // The page reloads onto the restored files and says what came back.
    await expect(toast(page, /^Restored .+ as a new save\.$/)).toBeVisible()
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
    await expect(canvasNode(page, "priced_v2")).toHaveCount(0)
    expect(pipelineOnDisk()).toContain("def priced_v1(")
    // Still on the branch's own history, not an earlier version: no new branch
    // and no reconnecting needed.
    expect(checkedOutBranch()).toBe(ledgerOf(work))
    expect(saveSubjects(work)[0]).toMatch(/^Restored [0-9a-f]{8}/)

    await openGitPanel(page)
    await expect(pendingRows(page)).toHaveCount(3)
    await expect(pendingRows(page).first()).toContainText("Restored")
  })

  test("a save folded into a milestone restores from the row menu", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameAndSave(page, "priced_v1", "priced_v2")
    await commitMilestone(page, "Two renames")

    await openGitPanel(page)
    await page.getByTestId("git-panel-milestone").first().click()
    const folded = page.getByTestId("git-panel-save")
    await expect(folded).toHaveCount(2)
    // The newest folded save is the current version.
    await expect(folded.first().getByTestId("git-panel-restore")).toHaveCount(0)

    await folded.last().click({ button: "right" })
    await expect(page.getByTestId("git-panel-fork-menu")).toBeVisible()
    await page.getByTestId("git-panel-menu-restore").click()
    await page.getByTestId("restore-confirm").click()

    await expect(toast(page, /^Restored .+ as a new save\.$/)).toBeVisible()
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
    await expectOnBranch(page, work)
  })

  test("the current version offers no restore, and neither does another branch", async ({
    page,
  }) => {
    await page.goto("/")
    await openGitPanel(page)

    // Only the root so far: it is the version open now.
    await expect(rootMilestone(page).getByTestId("git-panel-restore")).toHaveCount(0)
    await rootMilestone(page).click({ button: "right" })
    await expect(page.getByTestId("git-panel-fork-menu")).toBeVisible()
    await expect(page.getByTestId("git-panel-menu-restore")).toHaveCount(0)
    await page.locator(".fixed.inset-0").first().click()
    await expect(page.getByTestId("git-panel-fork-menu")).toHaveCount(0)

    // Looking at another branch's history: no restore and no new branch there.
    await createBranchInManager(page, "elsewhere")
    await page
      .getByTestId("branch-manager-branch")
      .filter({ hasText: "elsewhere" })
      .getByTitle("View this branch's history")
      .click()
    await expect(page.getByTestId("git-panel-peeking")).toContainText("elsewhere")
    await expect(page.getByTestId("git-panel-restore")).toHaveCount(0)
    await expect(page.getByTestId("git-panel-start-branch")).toHaveCount(0)
    await rootMilestone(page).click({ button: "right" })
    await expect(page.getByTestId("git-panel-menu-view")).toBeVisible()
    await expect(page.getByTestId("git-panel-menu-restore")).toHaveCount(0)
    await expect(page.getByTestId("git-panel-fork-switch")).toHaveCount(0)
  })

  test("Cancel, Escape and a click outside the prompt leave everything as it was", async ({
    page,
  }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    const before = saveSubjects(work)

    await openGitPanel(page)
    const modal = page.getByTestId("restore-confirm-modal")

    await restoreFrom(rootMilestone(page), page)
    await modal.getByRole("button", { name: "Cancel" }).click()
    await expect(modal).toHaveCount(0)

    await restoreFrom(rootMilestone(page), page)
    await page.keyboard.press("Escape")
    await expect(modal).toHaveCount(0)

    await restoreFrom(rootMilestone(page), page)
    await modal.click({ position: { x: 5, y: 5 } })
    await expect(modal).toHaveCount(0)

    await expect(page.getByTestId("git-panel")).toBeVisible()
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
    expect(pipelineOnDisk()).toContain("def priced_v1(")
    expect(saveSubjects(work)).toEqual(before)
  })

  test("Save & restore keeps the unsaved edits as a save before restoring", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameNode(page, "priced_v1", "priced_unsaved")

    await openGitPanel(page)
    await restoreFrom(rootMilestone(page), page)
    await expect(page.getByTestId("restore-dirty-warning")).toBeVisible()
    await expect(page.getByTestId("restore-confirm")).toHaveCount(0)
    await page.getByTestId("restore-save").click()

    await expect(toast(page, /^Restored Initial scaffold as a new save\.$/)).toBeVisible()
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(pipelineOnDisk()).not.toContain("priced_unsaved")
    // The edit is in the history, one save below the restore.
    expect(e2eGit(["show", `${ledgerOf(work)}~1:rating/main.py`])).toContain(
      "def priced_unsaved(",
    )
  })

  test("Discard & restore throws the unsaved edits away for good", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameNode(page, "priced_v1", "priced_thrown_away")

    await openGitPanel(page)
    await restoreFrom(rootMilestone(page), page)
    await page.getByTestId("restore-discard").click()

    await expect(toast(page, /^Restored Initial scaffold as a new save\.$/)).toBeVisible()
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(e2eGit(["log", "-S", "priced_thrown_away", "--oneline", ledgerOf(work)])).toBe("")

    // Nothing brings them back on the next load either.
    await page.reload()
    await expect(canvasNode(page, "priced")).toBeVisible()
    await expect(canvasNode(page, "priced_thrown_away")).toHaveCount(0)
  })

  test("restoring the wrong version is put right by restoring the one before it", async ({
    page,
  }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")

    await openGitPanel(page)
    await restoreFrom(rootMilestone(page), page)
    await page.getByTestId("restore-confirm").click()
    await expect(canvasNode(page, "priced")).toBeVisible()

    // The save made before the mistaken restore is still in the history.
    await openGitPanel(page)
    await expect(pendingRows(page)).toHaveCount(2)
    await restoreFrom(pendingRows(page).last(), page)
    await page.getByTestId("restore-confirm").click()

    await expect(canvasNode(page, "priced_v1")).toBeVisible()
    expect(pipelineOnDisk()).toContain("def priced_v1(")
    const [newest, second, third] = saveSubjects(work)
    expect(newest).toMatch(/^Restored /)
    expect(second).toMatch(/^Restored /)
    expect(third).toMatch(/^Updated/)
  })

  test("a double click on Restore makes one restore", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    const before = saveSubjects(work).length

    await openGitPanel(page)
    await restoreFrom(rootMilestone(page), page)
    await page.getByTestId("restore-confirm").dblclick()

    await expect(toast(page, /^Restored Initial scaffold as a new save\.$/)).toBeVisible()
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(saveSubjects(work).length).toBe(before + 1)
  })

  test("after a restore the branch commits a milestone as usual", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await openGitPanel(page)
    await restoreFrom(rootMilestone(page), page)
    await page.getByTestId("restore-confirm").click()
    await expect(canvasNode(page, "priced")).toBeVisible()

    await commitMilestone(page, "Back to the scaffold")

    await expectOnBranch(page, work)
    await openGitPanel(page)
    await expect(page.getByTestId("git-panel-pending")).toHaveCount(0)
    await expect(page.getByTestId("git-panel-milestone").first()).toContainText(
      "Back to the scaffold",
    )
  })
})

test.describe("starting a branch from a version", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  const forkDialog = (page: Page) => page.getByTestId("git-panel-fork-dialog")
  const forkName = (page: Page) => page.getByTestId("git-panel-fork-name")
  const forkCreate = (page: Page) => page.getByTestId("git-panel-fork-create")

  test("the row button opens a new branch at that version and leaves the old one as it was", async ({
    page,
  }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")

    await openGitPanel(page)
    await rootMilestone(page).getByTestId("git-panel-start-branch").click()
    await expect(forkDialog(page)).toContainText("Start a branch from this version")
    await expect(forkCreate(page)).toHaveText("Create & open")
    await expect(forkCreate(page)).toBeDisabled()
    await forkName(page).fill("from-root")
    await forkCreate(page).click()

    await expectOnBranch(page, "from-root")
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(pipelineOnDisk()).toContain("def priced(")
    // The branch left behind keeps its save.
    expect(pipelineSavedOn(work)).toContain("def priced_v1(")
  })

  test("the row menu starts one from a pending save", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameAndSave(page, "priced_v1", "priced_v2")

    await openGitPanel(page)
    await pendingRows(page).last().click({ button: "right" })
    await page.getByTestId("git-panel-fork-switch").click()
    await forkName(page).fill("from-v1")
    await forkCreate(page).click()

    await expectOnBranch(page, "from-v1")
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
    expect(pipelineSavedOn(work)).toContain("def priced_v2(")
  })

  test("with unsaved edits, Cancel keeps them and Save first keeps them on the old branch", async ({
    page,
  }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await renameNode(page, "priced_v1", "priced_draft")

    await openGitPanel(page)
    await rootMilestone(page).getByTestId("git-panel-start-branch").click()
    await forkName(page).fill("after-draft")
    await forkCreate(page).click()
    const confirm = page.getByTestId("git-navigation-confirm")
    await expect(confirm).toBeVisible()

    await page.getByTestId("git-navigation-cancel").click()
    await expect(confirm).toHaveCount(0)
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_draft")).toBeVisible()
    expect(branchExists("after-draft")).toBe(false)

    await forkCreate(page).click()
    await page.getByTestId("git-navigation-save").click()

    await expectOnBranch(page, "after-draft")
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(pipelineSavedOn(work)).toContain("def priced_draft(")
  })

  test("Discard opens the new branch without the unsaved edits", async ({ page }) => {
    await page.goto("/")
    await renameNode(page, "priced", "priced_draft")

    await openGitPanel(page)
    // The root is the current version: a branch can still start from it.
    await rootMilestone(page).getByTestId("git-panel-start-branch").click()
    await forkName(page).fill("clean-start")
    await forkCreate(page).click()
    await page.getByTestId("git-navigation-discard").click()

    await expectOnBranch(page, "clean-start")
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(e2eGit(["log", "--all", "-S", "priced_draft", "--oneline"])).toBe("")
  })

  test("names that cannot be used are refused, and a corrected name goes through", async ({
    page,
  }) => {
    await page.goto("/")
    await openGitPanel(page)
    await createBranchInManager(page, "taken")

    await rootMilestone(page).getByTestId("git-panel-start-branch").click()
    await forkName(page).fill("  ")
    await expect(forkCreate(page)).toBeDisabled()

    const refusals: [string, RegExp][] = [
      ["motor pricing", /'motor pricing' can't be used as a branch name\. Use letters/],
      ["main", /'main' is a protected branch/],
      ["taken", /Branch 'taken' already exists/],
      ["taken-save", /'taken-save' ends in '-save', which Haute keeps for each branch's saves/],
    ]
    const refusal = page.getByTestId("git-panel-fork-error")
    for (const [name, reason] of refusals) {
      await forkName(page).fill(name)
      await expect(refusal).toHaveCount(0) // a changed name clears the last refusal
      await forkCreate(page).click()
      // Said in the name box, which stays open with the name, ready to be put
      // right, and leaves Create uncovered.
      await expect(refusal).toContainText(/^Could not create branch: /)
      await expect(refusal).toContainText(reason)
      await expect(forkName(page)).toHaveValue(name)
      await expectOnBranch(page, work)
    }
    expect(branchExists("motor pricing")).toBe(false)

    await forkName(page).fill("good-name")
    await forkCreate(page).click()
    await expectOnBranch(page, "good-name")
  })
})
