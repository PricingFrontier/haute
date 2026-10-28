import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test as base, type Locator, type Page } from "@playwright/test"

import { e2eGit, e2eProjectRoot, e2eWorkingBranch } from "./projectIsolation"

// Shared steps for the git journeys (git-restore, git-branches, git-recovery):
// what an analyst does in the editor, and what the project's files and history
// read afterwards. The e2e pipeline's "priced" node is the one edited: renaming
// it renames its function in rating/main.py, so the canvas, the file on disk and
// each version's copy of it can all be read for the same change.

// Each page counts the pipeline documents the server sends it, from before it
// first loads: a save is sent back once the file watcher sees the write, and
// that document replaces the canvas (closing an open node panel), so a step
// after a save waits for it. The journeys import this `test`.
const documentFrames = new WeakMap<Page, { count: number }>()

export const test = base.extend<{ countDocumentFrames: void }>({
  countDocumentFrames: [
    async ({ page }, use) => {
      const seen = { count: 0 }
      documentFrames.set(page, seen)
      page.on("websocket", (socket) => {
        socket.on("framereceived", ({ payload }) => {
          if (typeof payload === "string" && /"type":\s*"pipeline_document_update"/.test(payload)) {
            seen.count += 1
          }
        })
      })
      await use()
    },
    { auto: true },
  ],
})

export const pipelinePath = resolve(e2eProjectRoot, "rating", "main.py")
const pipelineFile = "rating/main.py"

export function ledgerOf(branch: string): string {
  return `${branch}-save`
}

/** The pipeline file on disk now. */
export function pipelineOnDisk(): string {
  return readFileSync(pipelinePath, "utf8")
}

/** The pipeline file as a branch's latest save holds it. */
export function pipelineSavedOn(branch: string): string {
  return e2eGit(["show", `${ledgerOf(branch)}:${pipelineFile}`])
}

/** The subjects of a branch's saves, newest first, back to the shared root. */
export function saveSubjects(branch: string): string[] {
  return e2eGit(["log", "--format=%s", ledgerOf(branch)])
    .split(/\r?\n/)
    .filter(Boolean)
}

export function branchExists(branch: string): boolean {
  return e2eGit(["branch", "--list", branch]).trim() !== ""
}

/** The branch git has checked out, or "" when the project is on an earlier version. */
export function checkedOutBranch(): string {
  return e2eGit(["branch", "--show-current"]).trim()
}

export function toast(page: Page, text: RegExp | string): Locator {
  return page.getByRole("alert").filter({ hasText: text })
}

export function canvasNode(page: Page, label: string): Locator {
  // A node's accessible name reads "<type> node: <label>…"; the trailing
  // boundary keeps "priced" from matching "priced_v1".
  return page.getByRole("button", { name: new RegExp(` node: ${label}(?![\\w])`) })
}

export async function openGitPanel(page: Page): Promise<void> {
  await page.getByTestId("branch-indicator-name").click()
  await expect(page.getByTestId("git-panel")).toBeVisible()
}

export async function expectOnBranch(page: Page, branch: string): Promise<void> {
  await expect(page.getByTestId("toolbar-branch-indicator")).toHaveAttribute(
    "data-branch-state",
    "ready",
  )
  await expect(page.getByTestId("branch-indicator-name")).toHaveText(branch)
}

/** Rename a node on the canvas, leaving the edit unsaved. Clicking a node
 *  closes an open git panel, so call this before opening it. */
export async function renameNode(page: Page, from: string, to: string): Promise<void> {
  await canvasNode(page, from).click()
  const labelInput = page.locator("input.node-label-input")
  await expect(labelInput).toHaveValue(from)
  await labelInput.fill(to)
  await labelInput.press("Enter") // the label commits on Enter or blur
  await expect(canvasNode(page, to)).toBeVisible()
}

/** Toolbar Save, waiting until the save has landed on the branch's history and
 *  the server has sent the saved document back (when it does: a write the
 *  watcher has already broadcast is not sent again). */
export async function saveProject(page: Page, expectFunction: string, branch = e2eWorkingBranch): Promise<void> {
  const seen = documentFrames.get(page)
  const before = seen?.count ?? 0
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect.poll(() => pipelineSavedOn(branch)).toContain(`def ${expectFunction}(`)
  if (seen) {
    await expect
      .poll(() => seen.count, { timeout: 3_000 })
      .toBeGreaterThan(before)
      .catch(() => undefined)
  }
}

export async function renameAndSave(page: Page, from: string, to: string): Promise<void> {
  await renameNode(page, from, to)
  await saveProject(page, to)
}

export async function commitMilestone(page: Page, message: string): Promise<void> {
  await page.getByTestId("toolbar-save-commit").click()
  const modal = page.getByTestId("milestone-commit-modal")
  await expect(modal).toBeVisible()
  await page.getByTestId("milestone-message").fill(message)
  await page.getByTestId("milestone-confirm").click()
  await expect(modal).not.toBeVisible()
  await expect(toast(page, /Committed milestone/)).toBeVisible()
}

export async function createBranchInManager(page: Page, name: string): Promise<void> {
  await page.getByTestId("branch-manager-create-input").fill(name)
  await page.getByTestId("branch-manager-create").click()
  await expect(branchRow(page, name)).toBeVisible()
}

export function branchRow(page: Page, name: string): Locator {
  return page
    .getByTestId("branch-manager")
    .getByTestId("branch-manager-branch")
    .filter({ has: page.getByText(name, { exact: true }) })
}

/** Switch from the branch manager row, through the first-switch confirm. */
export async function switchInManager(page: Page, name: string): Promise<void> {
  await branchRow(page, name).getByTestId("branch-manager-switch").click()
  await expect(page.getByTestId("branch-manager-confirm-switch")).toBeVisible()
  await page.getByTestId("branch-manager-confirm-switch-go").click()
}
