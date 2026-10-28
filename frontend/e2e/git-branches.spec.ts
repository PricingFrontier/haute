import { expect, type Page } from "@playwright/test"

import {
  branchExists,
  branchRow,
  canvasNode,
  createBranchInManager,
  expectOnBranch,
  openGitPanel,
  pipelineOnDisk,
  pipelineSavedOn,
  renameAndSave,
  renameNode,
  switchInManager,
  toast,
  test,
} from "./gitJourneys"
import { e2eGit, e2eWorkingBranch, resetE2eProject } from "./projectIsolation"

// Switching, deleting and restoring branches (GIT-R12, GIT-R14), with the
// mistakes on the way: switching with unsaved edits, switching to a branch the
// open pipeline is not on, deleting the wrong branch, reusing a deleted name and
// deleting the branch you are on.

const work = e2eWorkingBranch

async function undo(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Undo" }).click()
}

test.describe("switching branches", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("the canvas follows a switch and comes back with it", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await openGitPanel(page)
    // A new branch starts at the latest milestone: the scaffold.
    await createBranchInManager(page, "other")

    await switchInManager(page, "other")
    await expectOnBranch(page, "other")
    await expect(canvasNode(page, "priced")).toBeVisible()
    await expect(canvasNode(page, "priced_v1")).toHaveCount(0)
    expect(pipelineOnDisk()).toContain("def priced(")

    await switchInManager(page, work)
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
  })

  test("unsaved edits at a switch: Cancel keeps them, Discard drops them for good", async ({
    page,
  }) => {
    await page.goto("/")
    await renameNode(page, "priced", "priced_draft")
    await openGitPanel(page)
    await createBranchInManager(page, "other")

    await switchInManager(page, "other")
    const confirm = page.getByTestId("git-navigation-confirm")
    await expect(confirm).toContainText("You have unsaved changes")
    await page.getByTestId("git-navigation-cancel").click()
    await expect(confirm).toHaveCount(0)
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_draft")).toBeVisible()

    await switchInManager(page, "other")
    await page.getByTestId("git-navigation-discard").click()
    await expectOnBranch(page, "other")
    await expect(canvasNode(page, "priced")).toBeVisible()

    // Undo takes the switch back, not the discarded edits.
    await undo(page)
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced")).toBeVisible()
    await expect(canvasNode(page, "priced_draft")).toHaveCount(0)
    expect(pipelineOnDisk()).not.toContain("priced_draft")
  })

  test("Save first keeps the edits on the branch being left", async ({ page }) => {
    await page.goto("/")
    await renameNode(page, "priced", "priced_kept")
    await openGitPanel(page)
    await createBranchInManager(page, "other")

    await switchInManager(page, "other")
    await page.getByTestId("git-navigation-save").click()
    await expectOnBranch(page, "other")
    await expect(canvasNode(page, "priced")).toBeVisible()
    expect(pipelineSavedOn(work)).toContain("def priced_kept(")

    await switchInManager(page, work)
    await expect(canvasNode(page, "priced_kept")).toBeVisible()
  })

  // A branch made outside Haute in which the pipeline file was deleted.
  function makeBranchWithoutThePipeline(): void {
    e2eGit(["switch", "--force", "-c", "no-pipeline-save"])
    e2eGit(["rm", "-q", "rating/main.py"])
    e2eGit(["commit", "-q", "-m", "Remove the pipeline"])
    e2eGit(["branch", "no-pipeline"])
    e2eGit(["switch", "--force", `${work}-save`])
  }

  test("a branch without the open pipeline says so, and switching back brings it back", async ({
    page,
  }) => {
    makeBranchWithoutThePipeline()

    await page.goto("/")
    await expect(canvasNode(page, "priced")).toBeVisible()
    await openGitPanel(page)
    await switchInManager(page, "no-pipeline")

    const failure = page.getByTestId("pipeline-load-failure")
    await expect(failure).toContainText("This pipeline isn't in the version now open. Switch back to see it.")
    // Saving now would write the pipeline into a branch that does not have it.
    await expect(page.getByTestId("toolbar-save")).toBeDisabled()

    // The way back is the branch name in the toolbar, as on any other page.
    await openGitPanel(page)
    await switchInManager(page, work)
    await expectOnBranch(page, work)
    await expect(failure).toHaveCount(0)
    await expect(canvasNode(page, "priced")).toBeVisible()
  })

  test("reloaded on a branch without the pipeline, switching back still brings it back", async ({
    page,
  }) => {
    makeBranchWithoutThePipeline()
    await page.goto("/")
    await openGitPanel(page)
    await switchInManager(page, "no-pipeline")
    const failure = page.getByTestId("pipeline-load-failure")
    await expect(failure).toBeVisible()

    // Mistake: reloading, hoping it comes back. The page opens on the failure,
    // once the first load's retries (6, backing off from 250 ms: about 16 s) run out.
    await page.reload()
    await expect(failure).toBeVisible({ timeout: 40_000 })

    await openGitPanel(page)
    await switchInManager(page, work)
    await expectOnBranch(page, work)
    await expect(failure).toHaveCount(0)
    await expect(canvasNode(page, "priced")).toBeVisible()
    await expect(page.getByTestId("toolbar-save")).toBeEnabled()
  })
})

test.describe("deleting and restoring branches", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  const deletedSection = (page: Page) => page.getByTestId("branch-manager-deleted")

  async function deleteBranch(page: Page, name: string): Promise<void> {
    await branchRow(page, name).getByTestId("branch-manager-delete").click()
    await expect(page.getByTestId("branch-manager-confirm")).toContainText(name)
    await page.getByTestId("branch-manager-confirm-delete").click()
  }

  async function openDeleted(page: Page): Promise<void> {
    await page.getByTestId("branch-manager-deleted-toggle").click()
  }

  test("a deleted branch is listed under Deleted and comes back with Restore", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)
    await createBranchInManager(page, "scratch")

    // Cancel first: nothing happens.
    await branchRow(page, "scratch").getByTestId("branch-manager-delete").click()
    await page.getByTestId("branch-manager-confirm").getByRole("button", { name: "Cancel" }).click()
    await expect(branchRow(page, "scratch")).toBeVisible()

    await deleteBranch(page, "scratch")
    await expect(toast(page, "Deleted scratch")).toBeVisible()
    await expect(branchRow(page, "scratch")).toHaveCount(0)
    expect(branchExists("scratch")).toBe(false)

    await expect(deletedSection(page)).toBeVisible()
    await openDeleted(page)
    const row = page.getByTestId("branch-manager-deleted-row").filter({ hasText: "scratch" })
    await expect(row).toBeVisible()
    await row.getByTestId("branch-manager-undelete").click()

    await expect(toast(page, "Restored scratch")).toBeVisible()
    await expect(branchRow(page, "scratch")).toBeVisible()
    await expect(deletedSection(page)).toHaveCount(0)
  })

  test("a deleted branch whose name was reused comes back under another name", async ({
    page,
  }) => {
    await page.goto("/")
    await openGitPanel(page)
    await createBranchInManager(page, "reused")
    await deleteBranch(page, "reused")
    await expect(branchRow(page, "reused")).toHaveCount(0)
    await createBranchInManager(page, "reused")

    // The deletion is still listed, though a branch has its name again.
    await openDeleted(page)
    const row = page.getByTestId("branch-manager-deleted-row").filter({ hasText: "reused" })
    await row.getByTestId("branch-manager-undelete").click()
    const asName = page.getByTestId("branch-manager-restore-as-input")
    await expect(asName).toHaveValue("reused-restored")

    // Mistake: the name that is taken.
    await asName.fill("reused")
    await page.getByTestId("branch-manager-restore-as-go").click()
    await expect(page.getByTestId("branch-manager-error")).toContainText(
      "A branch named 'reused' already exists. Restore it under another name.",
    )

    await asName.fill("reused-old")
    await page.getByTestId("branch-manager-restore-as-go").click()
    await expect(toast(page, "Restored reused-old")).toBeVisible()
    await expect(branchRow(page, "reused-old")).toBeVisible()
    await expect(branchRow(page, "reused")).toBeVisible()
  })

  test("toolbar Undo brings a deleted branch back", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)
    await createBranchInManager(page, "oops")
    await deleteBranch(page, "oops")
    await expect(branchRow(page, "oops")).toHaveCount(0)

    await undo(page)
    await expect(branchRow(page, "oops")).toBeVisible()
    expect(branchExists("oops")).toBe(true)
  })

  test("deleting the branch you are on keeps its work restorable", async ({ page }) => {
    await page.goto("/")
    await renameAndSave(page, "priced", "priced_v1")
    await openGitPanel(page)

    await branchRow(page, work).getByTestId("branch-manager-delete").click()
    const confirm = page.getByTestId("branch-manager-confirm")
    await expect(confirm).toContainText("You'll be returned to the branch chooser")
    await page.getByTestId("branch-manager-confirm-delete").click()

    // The page reloads on the deleted branch's last milestone and asks for a
    // branch to carry on with.
    await expect(page.getByTestId("toolbar-branch-indicator")).toHaveAttribute(
      "data-branch-state",
      "detached",
    )
    await expect(page.getByTestId("toolbar-branch-indicator")).toHaveText("Viewing an earlier version")
    await expect(page.getByTestId("working-branch-select")).toHaveValue("__create__")
    await page.getByTestId("working-branch-new").fill("carry-on")
    await page.getByTestId("working-branch-confirm").click()
    await expectOnBranch(page, "carry-on")
    await expect(canvasNode(page, "priced")).toBeVisible()

    // The deleted branch, with its save, is still there to restore and open.
    await openGitPanel(page)
    await openDeleted(page)
    await page
      .getByTestId("branch-manager-deleted-row")
      .filter({ hasText: work })
      .getByTestId("branch-manager-undelete")
      .click()
    await expect(branchRow(page, work)).toBeVisible()
    await switchInManager(page, work)
    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_v1")).toBeVisible()
  })

  test("archiving the branch you are on with unsaved edits asks first, and Cancel keeps them", async ({
    page,
  }) => {
    await page.goto("/")
    await renameNode(page, "priced", "priced_draft")
    await openGitPanel(page)

    await branchRow(page, work).getByTestId("branch-manager-archive").click()
    await expect(page.getByTestId("git-navigation-confirm")).toBeVisible()
    await page.getByTestId("git-navigation-cancel").click()

    await expectOnBranch(page, work)
    await expect(canvasNode(page, "priced_draft")).toBeVisible()
    expect(branchExists(work)).toBe(true)
  })
})
