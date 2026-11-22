import { expect } from "@playwright/test"

import {
  canvasNode,
  checkedOutBranch,
  createBranchInManager,
  expectOnBranch,
  ledgerOf,
  openGitPanel,
  pipelineSavedOn,
  renameNode,
  switchInManager,
  test,
  toast,
} from "./gitJourneys"
import { e2eGit, e2eWorkingBranch, resetE2eProject } from "./projectIsolation"

// An open editor learns when the project changes branch, whoever changed it: a
// switch in another tab, a checkout in a terminal. A save from a tab that has not learned of
// it yet is refused naming both, writes nothing and leaves the checkout alone.

const work = e2eWorkingBranch

test.describe("an open tab and the project's branch", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a switch in one tab moves the other's toolbar without a reload", async ({ context }) => {
    const one = await context.newPage()
    const two = await context.newPage()
    await one.goto("/")
    await two.goto("/")
    await expectOnBranch(two, work)

    await openGitPanel(one)
    await createBranchInManager(one, "other")
    await switchInManager(one, "other")
    await expectOnBranch(one, "other")

    await expectOnBranch(two, "other")
  })

  test("a save from a tab that has not learned of a switch is refused naming both", async ({
    context,
  }) => {
    // Tab two hears nothing from the server, as when the switch happens just before its save.
    const two = await context.newPage()
    await two.routeWebSocket(/\/ws\/sync/, () => {})
    const one = await context.newPage()
    await two.goto("/")
    await one.goto("/")
    await expectOnBranch(two, work)
    await renameNode(two, "enriched", "enriched_unsaved")

    await openGitPanel(one)
    await createBranchInManager(one, "other")
    await switchInManager(one, "other")
    await expectOnBranch(one, "other")
    const otherBefore = pipelineSavedOn("other")

    await two.getByRole("button", { name: "Save", exact: true }).click()

    await expect(
      toast(two, `This tab shows the branch '${work}', but the project is on 'other' now, so nothing was saved.`),
    ).toBeVisible()
    await expect(canvasNode(two, "enriched_unsaved")).toBeVisible()
    expect(pipelineSavedOn("other")).toBe(otherBefore)
    expect(checkedOutBranch()).toBe(ledgerOf("other"))
    // The refusal reads the branch again: the toolbar follows.
    await expectOnBranch(two, "other")
  })

  test("a checkout in a terminal opens the branch dialog, and a new branch moves nothing", async ({
    page,
  }) => {
    await page.goto("/")
    await expectOnBranch(page, work)

    e2eGit(["switch", "-q", "-c", "mine"])

    await expect(page.getByTestId("divergence-modal")).toBeVisible()
    await expect(page.getByTestId("toolbar-branch-indicator")).toHaveAttribute(
      "data-branch-state",
      "divergent",
    )
    expect(checkedOutBranch()).toBe("mine")
  })
})
