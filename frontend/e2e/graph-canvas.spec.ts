import { readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { dispatchAppShortcut } from "./browserInteractions"
import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const pipelinePath = resolve(e2eProjectRoot, "rating", "main.py")
const ON_DISK = /Pipeline changed on disk \(rating\/main\.py\) while you have unsaved changes/
const modifier = process.platform === "darwin" ? "Meta" : "Control"

async function openCanvas(page: Page, node = "enriched"): Promise<void> {
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  await expect(page.getByTestId(`node-${node}`)).toBeVisible()
}

/** The project's pipeline holding only *body*'s nodes, so the canvas draws them large. */
function seedPipeline(body: string[]): void {
  writeFileSync(
    pipelinePath,
    ['"""Pipeline: tmp_e2e_project"""', "", "import haute", "import polars as pl", "",
      'pipeline = haute.Pipeline("tmp_e2e_project")', "", ...body, ""].join("\n"),
    "utf8",
  )
}

/** `raw_rows` into a chain of Transforms, each reading the one before. */
function seedChain(names: string[]): void {
  seedPipeline([
    '@pipeline.data_input(config="config/data_input/raw_rows.json")',
    "def raw_rows(): ...",
    "",
    ...names.flatMap((name, index) => {
      const input = index === 0 ? "raw_rows" : names[index - 1]
      return ["@pipeline.polars", `def ${name}(${input}: pl.LazyFrame) -> pl.LazyFrame:`, `    df = ${input}`, "    return df", ""]
    }),
  ])
}

function edgesOf(page: Page): Locator {
  return page.locator(".react-flow__edge")
}

async function onScreen(page: Page, locator: Locator): Promise<boolean> {
  const box = await locator.boundingBox()
  const viewport = page.viewportSize()
  return box !== null && viewport !== null
    && box.x >= 0 && box.y >= 0
    && box.x + box.width <= viewport.width && box.y + box.height <= viewport.height
}

/** Zoom in as far as both *a* and *b* stay on screen, so their handles can be dragged. */
async function zoomOnto(page: Page, a: Locator, b: Locator): Promise<void> {
  const collapsePalette = page.getByTitle("Collapse palette")
  if (await collapsePalette.isVisible()) await collapsePalette.click()
  await page.getByTestId("toolbar-centre").click()
  const zoomIn = page.getByRole("button", { name: "Zoom in", exact: true })
  const zoomOut = page.getByRole("button", { name: "Zoom out", exact: true })
  for (let step = 0; step < 4; step += 1) {
    await zoomIn.click()
    if (!(await onScreen(page, a)) || !(await onScreen(page, b))) {
      await zoomOut.click()
      break
    }
  }
}

/** Drag from *from*'s output onto *to*'s input, as an analyst connects two nodes. */
async function connect(page: Page, from: string, to: string): Promise<void> {
  const source = page.getByTestId(`output-connector[0]:${from}`)
  const target = page.getByTestId(`input-connector[0]:${to}`)
  await zoomOnto(page, source, target)
  await source.hover()
  await page.mouse.down()
  try {
    await target.hover()
    await expect(target).toHaveClass(/(^|\s)connectingto(\s|$)/)
  } finally {
    await page.mouse.up()
  }
}

async function rename(page: Page, from: string, to: string): Promise<void> {
  await page.getByTestId(`node-${from}`).click()
  const label = page.locator("input.node-label-input")
  await expect(label).toHaveValue(from)
  await label.fill(to)
  await label.press("Enter")
  await expect(page.getByTestId(`node-${to}`)).toBeVisible()
}

test.describe("graph canvas", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  for (const key of ["Backspace", "Delete"]) {
    test(`deleting a connected node with ${key} is one undo step (FEG-02)`, async ({ page }) => {
      await openCanvas(page)
      const before = await edgesOf(page).count()
      await page.getByTestId("node-enriched").click()
      await page.getByTestId("toolbar-centre").click()
      await page.getByTestId("node-enriched").click()
      await page.keyboard.press(key)
      await expect(page.getByTestId("node-enriched")).toHaveCount(0)
      const after = await edgesOf(page).count()
      expect(after).toBeLessThan(before)

      await dispatchAppShortcut(page, "z")
      await expect(page.getByTestId("node-enriched")).toBeVisible()
      await expect(edgesOf(page)).toHaveCount(before)

      await dispatchAppShortcut(page, "y")
      await expect(page.getByTestId("node-enriched")).toHaveCount(0)
      await expect(edgesOf(page)).toHaveCount(after)
    })
  }

  for (const [size, chain] of [["two", ["loop_a", "loop_b"]], ["three", ["loop_a", "loop_b", "loop_c"]]] as const) {
    test(`a connection closing a loop of ${size} is refused at the canvas (FEG-03)`, async ({ page }) => {
      seedChain([...chain])
      await openCanvas(page, "loop_a")
      const before = await edgesOf(page).count()
      await connect(page, chain[chain.length - 1], "loop_a")
      await expect(
        page.getByRole("alert").filter({ hasText: "Connection rejected: that connection would create a cycle" }).first(),
      ).toBeVisible()
      await expect(edgesOf(page)).toHaveCount(before)
    })
  }

  test("a tab locked by a change on disk is editable again once the change is taken back (FEG-05)", async ({ page }) => {
    await openCanvas(page)
    const loaded = readFileSync(pipelinePath)
    await rename(page, "raw_rows", "raw_rows_renamed")

    // A slip in a code editor that saves as it goes.
    writeFileSync(pipelinePath, Buffer.concat([loaded, Buffer.from("\n# slip\n")]))
    await expect(page.getByText(ON_DISK)).toBeVisible()
    await expect(page.getByText("READ-ONLY").first()).toBeVisible()

    // Taken out again: the file is the one the rename was made on.
    writeFileSync(pipelinePath, loaded)
    await expect(page.getByText(ON_DISK)).toHaveCount(0)
    await expect(page.getByText("READ-ONLY")).toHaveCount(0)
    const save = page.getByRole("button", { name: "Save", exact: true })
    await expect(save).toBeEnabled()
    await save.click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ }).first()).toBeVisible()
    expect(readFileSync(pipelinePath, "utf8")).toContain("def raw_rows_renamed(")
  })

  test("a tab locked by a change on disk offers the reload its banner names (FEG-05)", async ({ page }) => {
    await openCanvas(page)
    const loaded = readFileSync(pipelinePath)
    await rename(page, "raw_rows", "raw_rows_renamed")
    // A colleague's change to a node, saved from their editor.
    writeFileSync(pipelinePath, loaded.toString("utf8").replace('pl.col("value") * 2', 'pl.col("value") * 3'))
    await expect(page.getByText(ON_DISK)).toBeVisible()

    // The node's panel says what holds the document, not load diagnostics.
    await page.getByTestId("node-enriched").click()
    const inspector = page.getByTestId("node-document-readonly-inspector")
    await expect(inspector).toContainText("Pipeline changed on disk while you have unsaved changes.")
    await expect(inspector).not.toContainText("diagnostics")

    await page.getByRole("button", { name: "Reload file" }).click()
    await expect(page.getByText(ON_DISK)).toHaveCount(0)
    await expect(page.getByTestId("node-raw_rows")).toBeVisible()
    await expect(page.getByTestId("node-raw_rows_renamed")).toHaveCount(0)
    await expect(page.getByText("READ-ONLY")).toHaveCount(0)
  })

  test("nodes pasted together read each other (FEG-06)", async ({ page }) => {
    await openCanvas(page)
    await page.getByTestId("node-raw_rows").click()
    await page.getByTestId("node-enriched").click({ modifiers: [modifier] })
    await dispatchAppShortcut(page, "c")
    await dispatchAppShortcut(page, "v")
    await expect(page.getByRole("alert").filter({ hasText: "Pasted 2 nodes" }).first()).toBeVisible()

    const pasted = page.getByRole("button", { name: /enriched[ _]copy/ }).first()
    await expect(pasted).toBeVisible()
    await pasted.click()
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("value_doubled", { exact: true })).toBeVisible()
    await expect(page.getByText(/is not defined/)).toHaveCount(0)

    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ }).first()).toBeVisible()
  })

  test("an Edge Join dropped on an optimiser's input edge takes its chosen input with it (FEG-07)", async ({ page }) => {
    // The fixture's ratebook optimiser, which names browser_optimiser_rows its data input.
    seedPipeline([
      '@pipeline.data_input(config="config/data_input/browser_optimiser_rows.json")',
      "def browser_optimiser_rows(): ...",
      "",
      '@pipeline.data_input(config="config/data_input/browser_ratebook_quotes.json")',
      "def browser_ratebook_quotes(): ...",
      "",
      '@pipeline.banding(config="config/banding/browser_ratebook_banding.json")',
      "def browser_ratebook_banding(browser_ratebook_quotes): ...",
      "",
      '@pipeline.optimiser(config="config/optimisation/browser_ratebook.json")',
      "def browser_ratebook(browser_optimiser_rows, browser_ratebook_banding): ...",
      "",
    ])
    await openCanvas(page, "browser_ratebook")
    const edge = page.locator('.react-flow__edge[data-id*="browser_optimiser_rows"][data-id*="browser_ratebook"]').first()
    await expect(edge).toBeAttached()
    const path = edge.locator("path.react-flow__edge-path").first()
    const source = page.getByTestId("output-connector[0]:browser_ratebook_quotes")
    await zoomOnto(page, source, path)
    await source.hover()
    await page.mouse.down()
    // A point of the edge's line that no node or handle covers.
    const point = await path.evaluate((element: SVGPathElement) => {
      const matrix = element.getScreenCTM()
      if (!matrix) return null
      const length = element.getTotalLength()
      for (let step = 1; step < 50; step += 1) {
        const local = element.getPointAtLength(length * (step / 50))
        const screen = new DOMPoint(local.x, local.y).matrixTransform(matrix)
        const top = document.elementsFromPoint(screen.x, screen.y).find((candidate) =>
          candidate.closest(".react-flow__handle, .react-flow__node, .react-flow__edge[data-id]"))
        if (top?.closest(".react-flow__edge[data-id]") === element.closest(".react-flow__edge[data-id]")) {
          return { x: screen.x, y: screen.y }
        }
      }
      return null
    })
    if (!point) throw new Error("No visible point on the optimiser's input edge")
    await page.mouse.move(point.x, point.y, { steps: 12 })
    await expect(page.getByRole("status", { name: "Release to insert an Edge Join on this connection" })).toBeAttached()
    await page.mouse.up()
    await expect(page.getByRole("button", { name: /edge_join/ }).first()).toBeVisible()
    // A new join is keyed before it can be saved; both tables hold quote_id.
    const key = page.getByLabel("Same-name key 1")
    await expect(key).toBeVisible()
    if (await key.evaluate((element) => element.tagName === "SELECT")) {
      await key.selectOption("quote_id")
    } else {
      await key.fill("quote_id")
      await key.press("Tab")
    }

    const saved = page.waitForResponse((response) =>
      response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
    )
    await page.getByRole("button", { name: "Save", exact: true }).click()
    expect((await saved).status()).toBe(200)
    const request = (await saved).request().postDataJSON() as {
      graph: { nodes: Array<{ id: string; data: { config?: Record<string, unknown> } }> }
    }
    const optimiser = request.graph.nodes.find((node) => node.id === "browser_ratebook")
    expect(optimiser?.data.config?.data_input).toMatch(/^edge_join/)
  })
})
