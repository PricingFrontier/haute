import { expect, test, type Locator } from "@playwright/test"

import { waitForSettledViewport } from "./browserInteractions"
import { resetE2eProject } from "./projectIsolation"

type Point = { x: number; y: number }

async function centre(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox()
  if (!box) throw new Error("expected a rendered element with a bounding box")
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test.describe("box selection", () => {
  test.beforeEach(async ({ page }) => {
    resetE2eProject()
    await page.goto("/")
    await expect(page.getByTestId("rf__node-raw_rows")).toBeVisible()
    await waitForSettledViewport(page)
  })

  test("a node dropped after a box selection drags and opens its context menu at once", async ({ page }) => {
    // A left-drag from empty canvas into raw_rows box-selects it.
    const rawRows = page.getByTestId("rf__node-raw_rows")
    const rawRowsBox = await rawRows.boundingBox()
    if (!rawRowsBox) throw new Error("expected raw_rows to be rendered")
    const rawRowsCentre = await centre(rawRows)
    await page.mouse.move(rawRowsBox.x - 40, rawRowsBox.y - 40)
    await page.mouse.down()
    await page.mouse.move(rawRowsCentre.x, rawRowsCentre.y, { steps: 10 })
    await page.mouse.up()
    await expect(rawRows).toHaveClass(/(^|\s)selected(\s|$)/)

    await page.getByTestId("node-palette-item-polars").dragTo(page.locator(".react-flow"), {
      targetPosition: { x: 260, y: 160 },
    })
    const dropped = page.getByLabel(/Transform node: Transform/i)
    await expect(dropped).toBeVisible()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    await waitForSettledViewport(page)

    // Raw pointer input, as the user's hand gives it: locator actions would
    // wait for whatever covers the node to move out of the way.
    const start = await centre(dropped)
    await page.mouse.move(start.x, start.y)
    await page.mouse.down()
    await page.mouse.move(start.x + 120, start.y + 80, { steps: 12 })
    await page.mouse.up()
    const end = await centre(dropped)
    expect(end.x - start.x, "the dropped node follows the first drag").toBeGreaterThan(60)
    expect(end.y - start.y, "the dropped node follows the first drag").toBeGreaterThan(40)

    await page.mouse.click(end.x, end.y, { button: "right" })
    await expect(page.getByTestId("context-menu")).toHaveAttribute("aria-label", /^Actions for Transform \d+$/)
  })
})
