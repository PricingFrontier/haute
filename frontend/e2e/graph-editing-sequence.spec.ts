import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { dispatchAppShortcut } from "./browserInteractions"
import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const gitMainPath = resolve(e2eProjectRoot, "rating", "main.py")

async function selectEdge(page: Page, edgeId: string): Promise<void> {
  const edge = page.getByTestId(`rf__edge-${edgeId}`)
  await expect(edge).toBeAttached()
  const path = edge.locator("path.react-flow__edge-path").first()
  await expect(path).toBeAttached()

  try {
    await path.click({ force: true })
  } catch {
    // ignore
  }

  const hasSelectedClass = await edge.evaluate((el) => el.classList.contains("selected"))
  if (!hasSelectedClass) {
    await path.evaluate((el) => {
      const opts = { bubbles: true, cancelable: true, view: window }
      el.dispatchEvent(new PointerEvent("pointerdown", opts))
      el.dispatchEvent(new MouseEvent("mousedown", opts))
      el.dispatchEvent(new PointerEvent("pointerup", opts))
      el.dispatchEvent(new MouseEvent("mouseup", opts))
      el.dispatchEvent(new MouseEvent("click", opts))
    })
  }

  const stillNotSelected = await edge.evaluate((el) => !el.classList.contains("selected"))
  if (stillNotSelected) {
    await edge.evaluate((el) => {
      const opts = { bubbles: true, cancelable: true, view: window }
      el.dispatchEvent(new PointerEvent("pointerdown", opts))
      el.dispatchEvent(new MouseEvent("mousedown", opts))
      el.dispatchEvent(new PointerEvent("pointerup", opts))
      el.dispatchEvent(new MouseEvent("mouseup", opts))
      el.dispatchEvent(new MouseEvent("click", opts))
    })
  }

  await expect(edge).toHaveClass(/(^|\s)selected(\s|$)/)
}

test.describe.configure({ mode: "serial" })

test.describe("graph editing sequence", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a graph editing sequence conserves structure and computed meaning through save and reopen", async ({
    page,
  }) => {
    test.slow()

    // 1. Open `/`; click `enriched`; Refresh; assert preview shows `value_doubled` and `22`.
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    const enrichedNode = page.getByTestId("node-enriched")
    await expect(enrichedNode).toBeVisible()
    await enrichedNode.click()

    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("value_doubled", { exact: true })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "22" }).first()).toBeVisible()

    // 2. GROUP + DISSOLVE first: the multi-select gesture runs on the fresh
    // selection state exactly as core-flows proves it, before any other
    // gesture; the edge locator is declared here because the dissolve
    // assertion and every later step share it.
    let edgeId = "e_raw_rows_enriched"
    let edge = page.getByTestId(`rf__edge-${edgeId}`)
    await expect(edge).toBeAttached()

    // The multi-select gesture is the one core-flows proves: click the first
    // node button, modifier-click the second, then group straight away —
    // closing the side panel in between would clear the selection.
    // Test ids, not accessible names: the open node panel also renders an
    // input chip named raw_rows, which would make the role query ambiguous.
    const rawRowsButton = page.getByTestId("node-raw_rows")
    const enrichedButton = enrichedNode
    await expect(rawRowsButton).toBeVisible()
    await expect(enrichedButton).toBeVisible()
    await rawRowsButton.click()
    const multiSelectKey = process.platform === "darwin" ? "Meta" : "Control"
    await page.keyboard.down(multiSelectKey)
    await enrichedButton.click({ modifiers: [multiSelectKey] })
    await page.keyboard.up(multiSelectKey)

    const submodelToolbarBtn = page.getByTestId("toolbar-submodel")
    await expect(submodelToolbarBtn).toHaveAttribute("aria-disabled", "false")
    await dispatchAppShortcut(page, "g")

    const dialogHeading = page.getByText("Create Submodel")
    if (!await dialogHeading.isVisible({ timeout: 2000 }).catch(() => false)) {
      await submodelToolbarBtn.click()
    }
    await expect(dialogHeading).toBeVisible()
    const nameInput = page.getByPlaceholder("e.g. model_scoring")
    await nameInput.fill("browser_group")
    await page.getByRole("button", { name: "Create" }).click()

    const submodelNode = page.getByRole("button", { name: /browser_group/i })
    await expect(submodelNode).toBeVisible()

    await submodelNode.click({ button: "right" })
    const contextMenu = page.getByTestId("context-menu")
    if (!(await contextMenu.isVisible({ timeout: 2000 }).catch(() => false))) {
      await submodelNode.dispatchEvent("contextmenu")
    }
    await expect(contextMenu).toBeVisible()
    await contextMenu.getByRole("menuitem", { name: "Dissolve Submodel" }).click()

    await expect(page.getByTestId("node-raw_rows")).toBeVisible()
    await expect(page.getByTestId("node-enriched")).toBeVisible()
    await expect(submodelNode).toHaveCount(0)
    // Dissolution is a targeted flatten (specs/submodels/high-level.md,
    // "Dissolution"): the inlined nodes carry qualified runtime ids under the
    // dissolved occurrence while their labels stay raw_rows / enriched, and
    // the connection between them is re-created under a fresh edge id. React
    // Flow labels every edge "Edge from <source id> to <target id>", so the
    // edge is re-resolved by the two node ids' trailing segments.
    const restoredTestId = await page
      .locator(".react-flow__edge")
      .evaluateAll((els) =>
        els
          .map((e) => ({
            label: e.getAttribute("aria-label") ?? "",
            testId: e.getAttribute("data-testid") ?? "",
          }))
          .filter(({ label }) => /^Edge from \S*raw_rows to \S*enriched$/.test(label))
          .map(({ testId }) => testId),
      )
    expect(restoredTestId).toHaveLength(1)
    expect(restoredTestId[0]).toMatch(/^rf__edge-/)
    edgeId = restoredTestId[0].replace(/^rf__edge-/, "")
    edge = page.getByTestId(`rf__edge-${edgeId}`)
    await expect(edge).toBeAttached()

    // 3. DISCONNECT: close side panel and collapse preview if needed, reset window scroll, recentre, select edge and press Delete.
    const panelClose = page.getByTestId("node-panel-close")
    if (await panelClose.isVisible()) {
      await panelClose.click()
    }
    const collapsePreviewBtn = page.getByRole("button", { name: "Collapse preview panel" })
    if (await collapsePreviewBtn.isVisible()) {
      await collapsePreviewBtn.click()
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.getByTestId("toolbar-centre").click()

    const beforeDisconnect = readFileSync(gitMainPath, "utf8")
    await selectEdge(page, edgeId)
    await edge.focus()
    await page.keyboard.press("Delete")
    await expect(edge).toHaveCount(0)

    const afterDisconnect = readFileSync(gitMainPath, "utf8")
    expect(afterDisconnect).toBe(beforeDisconnect)

    // 4. UNDO (Ctrl+Z via dispatchAppShortcut): assert edge is back.
    await dispatchAppShortcut(page, "z")
    await expect(edge).toBeAttached()

    // 5. REDO then UNDO again: assert removed, then restored (stack round-trips).
    await dispatchAppShortcut(page, "y")
    await expect(edge).toHaveCount(0)
    await dispatchAppShortcut(page, "z")
    await expect(edge).toBeAttached()

    // 6. COPY/PASTE: select enriched, Ctrl+C, Ctrl+V; assert new node with "enriched copy" exists alongside original.
    await page.getByTestId("toolbar-centre").click()
    await enrichedNode.click()
    await dispatchAppShortcut(page, "c")
    await dispatchAppShortcut(page, "v")

    const pastedNode = page.getByTestId("node-enriched copy")
    await expect(pastedNode).toBeVisible()
    await expect(enrichedNode).toBeVisible()

    // 7. DELETE the pasted node; assert it is gone and enriched and edge remain.
    await pastedNode.click()
    await page.keyboard.press("Delete")
    await expect(pastedNode).toHaveCount(0)
    await expect(enrichedNode).toBeVisible()
    await expect(edge).toBeAttached()

    // 8. INSTANCE: Step omitted per specification instructions.
    // Reason: `enriched` is a Polars transform node (`isSubmodel: false`). Per `ContextMenu.tsx`
    // line 48 (`isSubmodel && onCreateInstance && !isSingleton`) and `ContextMenu.test.tsx` line 35
    // ("hides Create Instance for ordinary nodes"), "Create Instance" is exclusively offered on
    // submodel occurrences and is hidden for ordinary pipeline nodes. Right-clicking `enriched`
    // cannot drive a "Create instance" action.

    // 9. SAVE; wait for `/Saved/` alert; assert rating/main.py contents.
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    const mainPy = readFileSync(gitMainPath, "utf8")
    expect(mainPy).toContain("def enriched(raw_rows")
    expect(mainPy).not.toContain("copy")
    expect(mainPy).not.toContain("browser_group")

    // 10. RELOAD; click enriched; Refresh; assert value_doubled and 22 again.
    await page.reload()
    const reloadedEnriched = page.getByTestId("node-enriched")
    await expect(reloadedEnriched).toBeVisible()
    await reloadedEnriched.click()
    await page.getByRole("button", { name: "Refresh" }).click()

    const reloadedPreviewTable = page.getByRole("table").first()
    await expect(reloadedPreviewTable.getByText("value_doubled", { exact: true })).toBeVisible()
    await expect(reloadedPreviewTable.getByRole("cell", { name: "22" }).first()).toBeVisible()
  })
})
