import { expect, test, type Page } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

// Regression contract for the EXISTING git sidebar: every selector and
// behaviour asserted here predates the graph-rail work, so this file must
// pass unmodified against a main-built install as well as this branch's.
// Keep it that way — no graph selectors, no harness changes.
//
// The seeded e2e project has exactly one commit (the "Initial scaffold" root,
// with the working pair at main's tip), so each test builds the history it
// needs through real UI actions: node edit → toolbar Save → Save & commit.
// Tests are independent (fresh reset in beforeEach, no shared state) and run
// sequentially under the repo's workers:1 config.

// Version labels become version/<label> tags, which resetE2eProject does NOT
// scrub (it only deletes branches) and the engine rejects duplicates — stamp
// the label per run so re-runs against a reused server don't collide.
const RUN_STAMP = Date.now().toString(36)

async function openGitPanel(page: Page): Promise<void> {
  await page.getByTestId("branch-indicator-name").click()
  await expect(page.getByTestId("git-panel")).toBeVisible()
}

// Rename the starter "priced" node and Save — the same ledger-feeding edit as
// the core-flows git test. Clicking a canvas node closes an open git panel,
// so tests always edit BEFORE opening the panel.
async function renameNodeAndSave(page: Page, newLabel: string): Promise<void> {
  const pricedNode = page.getByRole("button", { name: /priced/i })
  await expect(pricedNode).toBeVisible()
  await pricedNode.click()

  const labelInput = page.locator("input.node-label-input")
  await expect(labelInput).toHaveValue("priced")
  await labelInput.fill(newLabel)

  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
}

// Toolbar Save & commit → milestone modal → commit. Needs at least one save
// to fold (the engine refuses an empty fold), which the flow's own pre-modal
// save provides as long as the canvas holds an unsaved or just-saved edit.
async function commitMilestone(
  page: Page,
  message: string,
  versionLabel?: string,
): Promise<void> {
  await page.getByTestId("toolbar-save-menu").click()
  await page.getByTestId("toolbar-save-commit").click()

  const modal = page.getByTestId("milestone-commit-modal")
  await expect(modal).toBeVisible()
  await page.getByTestId("milestone-message").fill(message)
  if (versionLabel) {
    await page.getByTestId("milestone-version").fill(versionLabel)
  }
  await page.getByTestId("milestone-confirm").click()
  await expect(modal).not.toBeVisible()
  await expect(
    page.getByRole("alert").filter({ hasText: /Committed milestone/ }),
  ).toBeVisible()
}

async function createBranchViaManager(page: Page, name: string): Promise<void> {
  await page.getByTestId("branch-manager-create-input").fill(name)
  await page.getByTestId("branch-manager-create").click()
  await expect(
    page.getByTestId("branch-manager-branch").filter({ hasText: name }),
  ).toBeVisible()
}

test.describe("git sidebar regression", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("toolbar branch indicator opens the version-control panel", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    // The panel's three fixtures: push control strip, branch manager
    // (expanded, current pair marked), and the save-history section.
    await expect(page.getByTestId("git-panel-refresh")).toBeVisible()
    await expect(page.getByTestId("branch-manager")).toBeVisible()
    await expect(page.getByTestId("branch-manager-current")).toBeVisible()
    await expect(page.getByText("Save history in branch")).toBeVisible()
    await expect(page.getByTestId("git-panel-milestones")).toBeVisible()
  })

  test("milestone list renders the seeded root with its init chip", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(1)
    await expect(milestones.first().getByTestId("git-panel-milestone-init")).toBeVisible()

    // The root milestone folds nothing — expanding shows the explicit empty
    // message, and a second row click collapses it again.
    await milestones.first().click()
    await expect(page.getByText("No individual saves recorded")).toBeVisible()
    await milestones.first().click()
    await expect(page.getByText("No individual saves recorded")).not.toBeVisible()
  })

  test("a labelled milestone commit renders a version-label chip", async ({ page }) => {
    const label = `e2e-${RUN_STAMP}`
    await page.goto("/")
    await renameNodeAndSave(page, "priced_labelled")
    await commitMilestone(page, "Label the pricing tweak", label)

    await openGitPanel(page)
    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(2)
    await expect(milestones.first().getByTestId("git-panel-milestone-label")).toHaveText(label)
    await expect(milestones.first()).toContainText("Label the pricing tweak")
    await expect(milestones.last().getByTestId("git-panel-milestone-init")).toBeVisible()
  })

  test("milestone rows expand to their folded saves and collapse again", async ({ page }) => {
    await page.goto("/")
    await renameNodeAndSave(page, "priced_expand")
    await commitMilestone(page, "Fold the rename")

    await openGitPanel(page)
    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(2)

    await milestones.first().click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves.first()).toBeVisible()
    await expect(saves.first()).toContainText(/Updated/)
    await expect(page.getByTestId("git-panel-file").first()).toBeVisible()

    await milestones.first().click()
    await expect(saves).toHaveCount(0)
    await expect(page.getByTestId("git-panel-file")).toHaveCount(0)
  })

  test("a save without a commit surfaces the pending block, which folds into the next milestone", async ({
    page,
  }) => {
    await page.goto("/")
    await renameNodeAndSave(page, "priced_pending")

    await openGitPanel(page)
    const pending = page.getByTestId("git-panel-pending")
    await expect(pending).toBeVisible()
    await expect(page.getByTestId("git-panel-pending-save")).toHaveCount(1)
    await expect(page.getByTestId("git-panel-pending-save")).toContainText(/Updated/)

    // Committing folds the pending save into a new milestone; the open panel
    // refreshes itself and selects the just-recorded milestone (S38).
    await commitMilestone(page, "Fold pending saves")
    await expect(pending).toHaveCount(0)

    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(2)
    await expect(milestones.first()).toContainText("Fold pending saves")
    await expect(milestones.first()).toHaveAttribute("data-selected", "true")

    await milestones.first().click()
    await expect(page.getByTestId("git-panel-save").first()).toContainText(/Updated/)
  })

  test("peeking another branch shows the banner and resets expansion", async ({ page }) => {
    await page.goto("/")
    await renameNodeAndSave(page, "priced_peek")
    await commitMilestone(page, "Milestone before peeking")

    await openGitPanel(page)
    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(2)
    await milestones.first().click()
    await expect(page.getByTestId("git-panel-save").first()).toBeVisible()

    await createBranchViaManager(page, "peek-target")
    const row = page.getByTestId("branch-manager-branch").filter({ hasText: "peek-target" })
    await row.getByTitle("View this branch's history").click()

    const banner = page.getByTestId("git-panel-peeking")
    await expect(banner).toBeVisible()
    await expect(banner).toContainText("peek-target")
    // Peeking resets expansion — the milestone opened above must not survive
    // into the peeked history.
    await expect(page.getByTestId("git-panel-save")).toHaveCount(0)

    await page.getByTestId("git-panel-peek-clear").click()
    await expect(banner).toHaveCount(0)
    await expect(milestones).toHaveCount(2)
    // Returning is a peek change too — expansion stays reset.
    await expect(page.getByTestId("git-panel-save")).toHaveCount(0)
  })

  test("right-click fork menu creates a branch whose chip peeks it", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(1)
    await milestones.first().click({ button: "right" })

    await expect(page.getByTestId("git-panel-fork-menu")).toBeVisible()
    await page.getByTestId("git-panel-fork-here").click()

    const dialog = page.getByTestId("git-panel-fork-dialog")
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText("New branch from this point")
    await page.getByTestId("git-panel-fork-name").fill("fork-a")
    await page.getByTestId("git-panel-fork-create").click()

    // A parallel fork leaves you put: no reload, and after the panel's own
    // refresh the spawning commit carries the branch's back-link chip.
    const chip = page.getByTestId("git-panel-milestones").getByTestId("git-panel-fork-link")
    await expect(chip).toBeVisible()
    await expect(chip).toContainText("fork-a")

    await chip.click()
    const banner = page.getByTestId("git-panel-peeking")
    await expect(banner).toBeVisible()
    await expect(banner).toContainText("fork-a")
  })

  test("fork menu's move variant relocates work onto the new branch and switches", async ({
    page,
  }) => {
    await page.goto("/")
    await openGitPanel(page)

    // Move is only offered on the latest milestone (row index 0).
    const milestones = page.getByTestId("git-panel-milestone")
    await expect(milestones).toHaveCount(1)
    await milestones.first().click({ button: "right" })
    await expect(page.getByTestId("git-panel-fork-menu")).toBeVisible()
    await page.getByTestId("git-panel-fork-move").click()

    const dialog = page.getByTestId("git-panel-fork-dialog")
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText("move your work here")
    await page.getByTestId("git-panel-fork-name").fill("fork-move-a")
    await expect(page.getByTestId("git-panel-fork-create")).toHaveText("Create & Move")
    await page.getByTestId("git-panel-fork-create").click()

    // A move switches the clone over — the app reloads onto the new branch.
    await expect(page.getByTestId("branch-indicator-name")).toContainText("fork-move-a")
  })

  test("the eye affordance opens the read-only comparison view", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    await page.getByTestId("git-panel-view").first().click()
    await expect(page.getByTestId("comparison-view")).toBeVisible()
    const chip = page.getByTestId("comparison-chip")
    await expect(chip).toBeVisible()
    await expect(chip).toContainText("read-only")

    // The chip's × exits comparison and returns to the editor (closing the
    // VC panel with it).
    await page.getByTestId("comparison-chip-close").click()
    await expect(page.getByTestId("comparison-view")).toHaveCount(0)
    await expect(page.getByTestId("git-panel")).toHaveCount(0)
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  })

  test("the move affordance opens the pre-move confirmation and cancel backs out", async ({
    page,
  }) => {
    await page.goto("/")
    await openGitPanel(page)

    await page.getByTestId("git-panel-move").first().click()
    const modal = page.getByTestId("move-confirm-modal")
    await expect(modal).toBeVisible()
    // Clean canvas → the simple confirm variant (no save/discard fork).
    await expect(page.getByTestId("move-confirm")).toBeVisible()

    await modal.getByRole("button", { name: "Cancel" }).click()
    await expect(modal).toHaveCount(0)
    await expect(page.getByTestId("git-panel")).toBeVisible()
  })

  test("the refresh button refetches history state", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    const chips = page.getByTestId("git-panel-milestones").getByTestId("git-panel-fork-link")
    await expect(page.getByTestId("git-panel-milestone")).toHaveCount(1)
    await expect(chips).toHaveCount(0)

    // Creating a branch through the manager records its fork point, but the
    // history section doesn't listen for that — only a manual refresh (or a
    // save/commit) refetches, so the chip appearing after the click IS the
    // refresh assertion.
    await createBranchViaManager(page, "refresh-probe")
    await expect(chips).toHaveCount(0)

    await page.getByTestId("git-panel-refresh").click()
    await expect(chips).toHaveCount(1)
    await expect(chips.first()).toContainText("refresh-probe")
  })

  test("branch manager creates a branch and switching prompts a confirm", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    await createBranchViaManager(page, "bm-switch")
    const row = page.getByTestId("branch-manager-branch").filter({ hasText: "bm-switch" })
    await row.getByTestId("branch-manager-switch").click()

    await expect(page.getByTestId("branch-manager-confirm-switch")).toBeVisible()
    await page.getByTestId("branch-manager-confirm-switch-go").click()

    // Switching reloads the editor onto the new branch.
    await expect(page.getByTestId("branch-indicator-name")).toContainText("bm-switch")
  })

  test("branch manager archives a parallel branch and restores it", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    await createBranchViaManager(page, "bm-arch")
    const row = page.getByTestId("branch-manager-branch").filter({ hasText: "bm-arch" })
    await row.getByTestId("branch-manager-archive").click()

    // The manager refreshes its own list after archiving: the branch moves
    // into the Archived section, swapping Switch for Restore. (The history
    // section above may stay stale until a manual refresh — by design; don't
    // assert on it.)
    const archived = page.getByTestId("branch-manager-archived")
    await expect(archived).toBeVisible()
    const archivedRow = archived
      .getByTestId("branch-manager-branch")
      .filter({ hasText: "bm-arch" })
    await expect(archivedRow).toBeVisible()
    await expect(archivedRow.getByTestId("branch-manager-switch")).toHaveCount(0)

    await archivedRow.getByTestId("branch-manager-restore").click()
    await expect(archived).toHaveCount(0)
    await expect(row.getByTestId("branch-manager-switch")).toBeVisible()
  })

  test("the push control renders its no-remote state", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    const noRemotes = page.getByTestId("git-push-no-remotes")
    await expect(noRemotes).toBeVisible()
    await expect(noRemotes).toContainText("No remotes configured")
  })
})
