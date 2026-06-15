/**
 * E2E: whole-node link targets + dead band + node-wins-over-edge
 * (edge-targeting design §5.4 — full-chain tier, chromium, NOT @smoke).
 *
 * Real-pointer choreography over the live app: connection drags start with a
 * mousedown on a connector dot and end on node bodies, dead bands, or covered
 * edges. All geometry is read from live bounding boxes — the starter pipeline
 * has no position sidecar, so nothing is coordinate-hardcoded.
 *
 * DEFERRED (design §5.4 item 8): multi-port disambiguation driven through the
 * ApiInputEditor 1↔2 emit toggle. The e2e project's quotes.json is
 * `{"path": ...}` with no v2 `tables` config and no per-port parquet cache,
 * so the toggle flow does not exist in this fixture. Multi-port nearest-
 * connector resolution is covered at the vitest tier instead
 * (utils/__tests__/dropResolver.test.ts geometric tables +
 * __tests__/App.integration.edgeTargeting.test.tsx with a 2-emit apiInput).
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import {
  bodyPoint,
  connectorCentre,
  dragConnection,
  dragNodeCentreTo,
  fitAllNodesIntoView,
  type Point,
} from "./_connectionDrag"
import { e2eProjectRoot, resetE2eProject } from "../projectIsolation"

const gitMainPath = resolve(e2eProjectRoot, "rating", "main.py")

/** Starter graph: raw_rows→enriched→priced, raw_rows→browser_model,
 * browser_optimiser_rows→browser_optimiser→browser_apply; quotes is edgeless.
 * The parser reports 5 edges but only 4 render: `optimiser` is a sink-only
 * node type (no output connector), so xyflow drops browser_optimiser→
 * browser_apply at render time. */
const STARTER_EDGE_COUNT = 4
const STARTER_NODE_COUNT = 8
const CONSUMER = "edge_target_consumer"

const EDGE_SELECTOR = ".react-flow__edge"
const NODE_SELECTOR = ".react-flow__node"
const JOIN_MARKER_SELECTOR = '[data-testid="edge-join-marker"]'

async function appReady(page: Page): Promise<void> {
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  await expect(page.getByTestId("rf__node-quotes")).toBeVisible()
  await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT)
}

/**
 * Find a screen point whose surrounding node-sized region is clear of every
 * existing node, zooming out when the fit-view layout leaves no room.
 */
async function findEmptyCanvasSpot(page: Page): Promise<Point> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const spot = await page.evaluate(() => {
      const pane = document.querySelector(".react-flow__pane")
      if (!pane) return null
      const paneRect = pane.getBoundingClientRect()
      const nodeRects = Array.from(document.querySelectorAll(".react-flow__node")).map(
        (element) => element.getBoundingClientRect(),
      )
      const dropWidth = 240
      const dropHeight = 110
      for (let y = paneRect.top + 110; y + dropHeight < paneRect.bottom - 24; y += 60) {
        for (let x = paneRect.left + 30; x + dropWidth < paneRect.right - 24; x += 80) {
          const clear = nodeRects.every(
            (rect) =>
              x + dropWidth < rect.left - 16 ||
              x > rect.right + 16 ||
              y + dropHeight < rect.top - 16 ||
              y > rect.bottom + 16,
          )
          if (clear) return { x, y }
        }
      }
      return null
    })
    if (spot) return spot
    await page.getByRole("button", { name: "Zoom out" }).click()
  }
  throw new Error("No empty canvas spot found for the palette drop")
}

/** Palette-drag a Polars consumer node onto empty canvas and rename it. */
async function paletteDropConsumer(page: Page): Promise<void> {
  const spot = await findEmptyCanvasSpot(page)
  const dataTransfer = await page.evaluateHandle(() => new DataTransfer())
  await page.dispatchEvent('[data-testid="node-palette-item-polars"]', "dragstart", {
    dataTransfer,
  })
  await page.dispatchEvent(".react-flow", "drop", {
    dataTransfer,
    clientX: spot.x,
    clientY: spot.y,
  })
  await expect(page.locator('[data-testid^="rf__node-polars_"]')).toBeVisible()

  // The drop selects the node and opens its panel — rename to a stable,
  // codegen-friendly label, then close the panel out of the way.
  const labelInput = page.locator("input.node-label-input")
  await expect(labelInput).toHaveValue(/Polars \d+/)
  await labelInput.fill(CONSUMER)
  await expect(page.getByTestId(`node-${CONSUMER}`)).toBeVisible()
  await page.getByTitle("Close").click()
}

function trackPageErrors(page: Page): string[] {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(String(error)))
  return errors
}

/**
 * HARNESS NOTE (resolved 2026-06-15): xyflow connection drags DO start under
 * Playwright. The Handle binds `onMouseDown`; `XYHandle.onPointerDown` then
 * listens on `document` for `mousemove`/`mouseup` and starts the connection
 * once the pointer moves past `connectionDragThreshold` (1px) — exactly the
 * trusted events `page.mouse` emits. The earlier "harness limitation" was a
 * misdiagnosis: the app's mount-time `fitView` lands at `scale(2)` (maxZoom),
 * pushing most starter nodes (incl. `quotes`, the source these gestures use)
 * off the right edge of the 1280px window, so `mouse.down()` hit nothing.
 *
 * The fix is `fitAllNodesIntoView` (see `_connectionDrag.ts`): click the
 * toolbar Centre button so every node is inside the visible pane, then read
 * bounding boxes. The reusable choreography lives in `_connectionDrag.ts` for
 * future canvas-gesture specs.
 *
 * The same arbiter is also covered at the unit tier in
 * `src/hooks/__tests__/useEdgeHandlers.test.ts` (criticalCoverage gate
 * 98/95/94/98) and through a non-mocked `onConnectEnd` + save round-trip in
 * `src/__tests__/App.integration.edgeTargeting.test.tsx`.
 */
test.describe("edge targeting — whole-node drop targets", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("body drop connects, dead band no-ops, edge persists, node wins over a hidden edge", async ({
    page,
  }) => {
    test.slow()
    const pageErrors = trackPageErrors(page)

    await appReady(page)

    // §5.4 step 1 — palette-drag a fresh consumer node.
    await paletteDropConsumer(page)
    await expect(page.locator(NODE_SELECTOR)).toHaveCount(STARTER_NODE_COUNT + 1)

    // The mount-time fitView lands at maxZoom with `quotes` off-screen, and the
    // palette drop may have zoomed further; re-fit so every node — source
    // connectors included — is inside the visible pane before any drag.
    await fitAllNodesIntoView(page)

    // §5.4 step 2 — whole-node drop: drag from the quotes output connector
    // onto the consumer's body centre-left (well inside the connect zone,
    // ≥G clear of the output-end dead band) → exactly one new edge.
    const quotesOutput = await connectorCentre(page, "output-connector[0]:quotes")
    await dragConnection(page, quotesOutput, await bodyPoint(page, CONSUMER, 0.3, 0.5))
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 1)
    await expect(page.locator(JOIN_MARKER_SELECTOR)).toHaveCount(0)

    // §5.4 step 3 — dead band: aim ≤G inside the consumer's output (right)
    // end, vertically offset from the output connector → no edge, no join
    // marker, silent no-op.
    const rawRowsOutput = await connectorCentre(page, "output-connector[0]:raw_rows")
    await dragConnection(page, rawRowsOutput, await bodyPoint(page, CONSUMER, 0.96, 0.78))
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 1)
    await expect(page.locator(JOIN_MARKER_SELECTOR)).toHaveCount(0)

    // §5.4 step 4 — persist + rewalk: save, assert the generated .py wires
    // the consumer to quotes (whole-node drop shape: named source, default
    // target), reload, edge still renders.
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect
      .poll(() => readFileSync(gitMainPath, "utf8"))
      .toMatch(new RegExp(`def ${CONSUMER}\\([^)]*quotes`))

    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await expect(page.getByTestId(`node-${CONSUMER}`)).toBeVisible()
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 1)

    // Reload resets the viewport to the mount-time maxZoom fit; re-fit again.
    await fitAllNodesIntoView(page)

    // §5.4 step 5 — no-double-edge: repeating the body-drop gesture is a
    // silent duplicate reject; the count must not move.
    const quotesOutputReloaded = await connectorCentre(page, "output-connector[0]:quotes")
    await dragConnection(page, quotesOutputReloaded, await bodyPoint(page, CONSUMER, 0.3, 0.5))
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 1)

    // §5.4 step 6 — node wins over a hidden edge: park the consumer's body
    // over the midpoint of the raw_rows→enriched edge, then drop a new
    // connection on the body directly above the covered edge → a plain edge
    // to the consumer, no edgeJoin splice, no new node.
    const coveredEdgeMidpoint = await (async () => {
      const a = await connectorCentre(page, "output-connector[0]:raw_rows")
      const b = await connectorCentre(page, "input-connector[0]:enriched")
      return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    })()
    await dragNodeCentreTo(page, CONSUMER, coveredEdgeMidpoint)

    const optimiserRowsOutput = await connectorCentre(
      page,
      "output-connector[0]:browser_optimiser_rows",
    )
    // Drop at body-centre-right (0.65), NOT the exact body centre: at this
    // fitted zoom the raw_rows→enriched edge is short and its midpoint sits
    // right on raw_rows's output connector. The consumer's body centre lands
    // on that connector too, so a (0.5,0.5) drop is an EXACT connector hit and
    // (correctly) fires the output-onto-output join arm instead of the body
    // drop. 0.65 keeps the drop over the consumer body and the covered edge
    // while clearing every connector — exercising the node-wins-over-hidden-
    // edge arm as intended. Still left of the output-end dead band (≥0.75).
    await dragConnection(page, optimiserRowsOutput, await bodyPoint(page, CONSUMER, 0.65, 0.5))
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 2)
    await expect(page.locator(JOIN_MARKER_SELECTOR)).toHaveCount(0)
    await expect(page.locator(NODE_SELECTOR)).toHaveCount(STARTER_NODE_COUNT + 1)

    expect(pageErrors).toEqual([])
  })

  test("body drop still targets nodes at compact zoom", async ({ page }) => {
    const pageErrors = trackPageErrors(page)

    await appReady(page)

    // Centre first so the zoom-out below converges on a fit where every node
    // is on-screen rather than zooming out from the off-screen maxZoom fit.
    await fitAllNodesIntoView(page)

    // §5.4 step 7 — the literal backlog sentence: port targeting at distance.
    // Zoom out until the compact bucket CSS class is applied (zoom ≤ 0.3).
    for (let click = 0; click < 14; click += 1) {
      if ((await page.locator(".react-flow.zoom-compact").count()) > 0) break
      await page.getByRole("button", { name: "Zoom out" }).click()
    }
    await expect(page.locator(".react-flow.zoom-compact")).toHaveCount(1)

    // quotes→enriched does not exist in the starter graph; a body drop on the
    // tiny compact-rendered enriched node must still connect.
    const quotesOutput = await connectorCentre(page, "output-connector[0]:quotes")
    await dragConnection(page, quotesOutput, await bodyPoint(page, "enriched", 0.3, 0.5))
    await expect(page.locator(EDGE_SELECTOR)).toHaveCount(STARTER_EDGE_COUNT + 1)
    await expect(page.locator(JOIN_MARKER_SELECTOR)).toHaveCount(0)

    expect(pageErrors).toEqual([])
  })
})
