import { expect, test, type Locator, type Page } from "@playwright/test"

import { dispatchAppShortcut } from "./browserInteractions"
import { resetE2eProject } from "./projectIsolation"

type Box = { x: number; y: number; width: number; height: number }

const desktopViewport = { width: 1440, height: 900 }

async function box(locator: Locator): Promise<Box> {
  const measured = await locator.boundingBox()
  if (!measured) throw new Error("expected a rendered element with a bounding box")
  return measured
}

function contains(outer: Box, inner: Box): boolean {
  return inner.x >= outer.x
    && inner.y >= outer.y
    && inner.x + inner.width <= outer.x + outer.width
    && inner.y + inner.height <= outer.y + outer.height
}

/** React Flow fits the view once nodes are measured; placing nodes before that lands would race it. */
async function waitForInitialFit(page: Page) {
  const viewport = page.locator(".react-flow__viewport")
  let previous: string | null = null
  await expect.poll(async () => {
    const current = await viewport.evaluate((element) => (element as HTMLElement).style.transform)
    const settled = current === previous
    previous = current
    return settled
  }, { intervals: [300] }).toBe(true)
}

async function viewportZoom(page: Page): Promise<number> {
  return page.locator(".react-flow__viewport").evaluate(
    (element) => new DOMMatrixReadOnly(getComputedStyle(element).transform).a,
  )
}

/** Right-drag the empty pane so the node's bottom-right corner sits `inset` px inside the canvas corner. */
async function panNodeToBottomRight(page: Page, canvas: Locator, node: Locator, inset: number) {
  const canvasBox = await box(canvas)
  const nodeBox = await box(node)
  const dx = canvasBox.x + canvasBox.width - inset - (nodeBox.x + nodeBox.width)
  const dy = canvasBox.y + canvasBox.height - inset - (nodeBox.y + nodeBox.height)
  const start = {
    x: dx > 0 ? canvasBox.x + 12 : canvasBox.x + canvasBox.width - 12,
    y: dy > 0 ? canvasBox.y + 12 : canvasBox.y + canvasBox.height - 12,
  }
  await page.mouse.move(start.x, start.y)
  await page.mouse.down({ button: "right" })
  await page.mouse.move(start.x + dx, start.y + dy, { steps: 12 })
  await page.mouse.up({ button: "right" })
}

async function selectNode(page: Page, node: Locator) {
  await expect(async () => {
    await node.click()
    await expect(page.getByTestId("node-panel")).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 15_000 })
}

test.describe("active node visibility", () => {
  test.beforeEach(async ({ page }) => {
    resetE2eProject()
    await page.setViewportSize(desktopViewport)
    await page.goto("/")
    await expect(page.getByTestId("rf__node-raw_rows")).toBeVisible()
    await waitForInitialFit(page)
  })

  test("moves a clicked node out from under its inspector and preview pane without changing zoom", async ({ page }) => {
    const canvas = page.locator(".react-flow")
    const node = page.getByTestId("rf__node-raw_rows")

    await panNodeToBottomRight(page, canvas, node, 24)
    const canvasBefore = await box(canvas)
    const nodeBefore = await box(node)
    expect(contains(canvasBefore, nodeBefore)).toBe(true)
    const zoomBefore = await viewportZoom(page)

    await selectNode(page, node)

    // The inspector narrows the canvas and the preview pane shortens it, over where the node was.
    await expect.poll(async () => {
      const canvasAfter = await box(canvas)
      return canvasAfter.width < nodeBefore.x + nodeBefore.width - canvasBefore.x
        && canvasAfter.height < nodeBefore.y + nodeBefore.height - canvasBefore.y
    }).toBe(true)
    await expect.poll(async () => contains(await box(canvas), await box(node))).toBe(true)
    expect(await viewportZoom(page)).toBeCloseTo(zoomBefore, 5)
  })

  test("centres a node chosen in node search within the canvas the inspector leaves", async ({ page }) => {
    const canvas = page.locator(".react-flow")
    const node = page.getByTestId("rf__node-enriched")

    await dispatchAppShortcut(page, "k")
    await page.getByPlaceholder("Search nodes by name or type...").fill("enriched")
    await page.keyboard.press("Enter")
    await expect(page.getByTestId("node-panel")).toBeVisible()

    await expect.poll(async () => {
      const canvasBox = await box(canvas)
      const nodeBox = await box(node)
      return Math.abs(nodeBox.x + nodeBox.width / 2 - (canvasBox.x + canvasBox.width / 2))
    }).toBeLessThan(2)
    expect(contains(await box(canvas), await box(node))).toBe(true)
    expect(await viewportZoom(page)).toBeCloseTo(0.8, 5)
  })
})
