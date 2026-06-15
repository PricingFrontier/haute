/**
 * Reusable Playwright choreography for @xyflow/react connection drags.
 *
 * WHY THIS EXISTS — the harness gotcha that blocked canvas-gesture e2e:
 * xyflow drives connection drags through *mouse* events (the Handle binds
 * `onMouseDown`; `XYHandle.onPointerDown` then listens on `document` for
 * `mousemove`/`mouseup` and starts the connection once the pointer moves
 * past `connectionDragThreshold`, default 1px). Playwright's `page.mouse`
 * emits exactly those trusted events, so a connection drag DOES start under
 * Playwright — *provided the source handle is actually inside the visible
 * viewport*. The real blocker was never the input synthesis: the app's
 * mount-time `fitView` lands at `scale(2)` (maxZoom), pushing most starter
 * nodes off the right edge of the 1280px window, so `mouse.down()` at a
 * far-off-screen page coordinate hit nothing and no drag began.
 *
 * `fitAllNodesIntoView` clicks the toolbar's Centre button (which calls
 * `fitView({ padding: 0.15 })`) to re-fit every node into the pane before
 * any drag, and helpers here always read bounding boxes AFTER that fit.
 */
import { expect, type Locator, type Page } from "@playwright/test"

const CONNECTING_SELECTOR = '[data-connecting="true"]'

export type Point = { x: number; y: number }

/**
 * Re-fit so every node is inside the visible pane. The mount-time fitView
 * lands at scale(2) with most starter nodes off-screen; the Centre button
 * re-fits to a zoom where all bounding boxes are real and droppable.
 *
 * Verifies the fit actually landed by polling until every `.react-flow__node`
 * intersects the pane rect, so callers can read boxes immediately after.
 */
export async function fitAllNodesIntoView(page: Page): Promise<void> {
  await page.getByTestId("toolbar-centre").click()
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const pane = document.querySelector(".react-flow__pane")?.getBoundingClientRect()
        if (!pane) return false
        const nodes = Array.from(document.querySelectorAll(".react-flow__node"))
        if (nodes.length === 0) return false
        return nodes.every((node) => {
          const r = node.getBoundingClientRect()
          return r.right > pane.left && r.left < pane.right && r.bottom > pane.top && r.top < pane.bottom
        })
      }),
    )
    .toBe(true)
}

async function centreOf(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox()
  if (!box) throw new Error("locator has no bounding box (off-screen or not rendered)")
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** Screen centre of a connector dot by its positional test id. */
export async function connectorCentre(page: Page, testId: string): Promise<Point> {
  return centreOf(page.getByTestId(testId))
}

/** Point inside a node body, as width/height fractions of its rendered box. */
export async function bodyPoint(page: Page, label: string, fx: number, fy: number): Promise<Point> {
  const box = await page.getByTestId(`node-${label}`).boundingBox()
  if (!box) throw new Error(`node body "node-${label}" has no bounding box`)
  return { x: box.x + box.width * fx, y: box.y + box.height * fy }
}

/**
 * Drag a connection from a screen point to a screen point with real mouse
 * events. Asserts xyflow's connection actually started mid-drag (the
 * `data-connecting` hook toggles on, and the live connection line renders),
 * then resets after release — so a silently-dropped gesture fails loudly
 * here rather than as a confusing downstream count mismatch.
 */
export async function dragConnection(page: Page, from: Point, to: Point): Promise<void> {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 8 })
  await expect(page.locator(CONNECTING_SELECTOR)).toHaveCount(1)
  await expect(page.locator(".react-flow__connection")).toHaveCount(1)
  await page.mouse.move(to.x, to.y, { steps: 8 })
  await page.mouse.up()
  await expect(page.locator(CONNECTING_SELECTOR)).toHaveCount(0)
}

/** Drag a node by its body centre so that its centre lands on `target`. */
export async function dragNodeCentreTo(page: Page, label: string, target: Point): Promise<void> {
  const from = await bodyPoint(page, label, 0.5, 0.5)
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(target.x, target.y, { steps: 12 })
  await page.mouse.up()
}
