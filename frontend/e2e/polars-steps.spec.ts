import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const sidecarPath = resolve(e2eProjectRoot, "rating", "config", "polars", "browser_steps.json")
const mainPath = resolve(e2eProjectRoot, "rating", "main.py")

async function openApp(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  const response = await responsePromise
  expect(response.status(), "initial pipeline request succeeds").toBe(200)
}

async function connect(page: Page, source: Locator, target: Locator): Promise<void> {
  await page.getByRole("button", { name: "Layout", exact: true }).click()
  await page.getByTestId("toolbar-centre").click()
  await expect(source).toBeVisible()
  await expect(target).toBeVisible()
  await source.hover({ timeout: 10_000 })
  await page.mouse.down()
  await target.hover({ timeout: 10_000 })
  await page.mouse.up()
}

async function save(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await responsePromise).status(), "pipeline save succeeds").toBe(200)
}

/** The node list as the save route wants it, from the editor document. */
type SeedNode = { id: string; type: string; position: unknown; data: Record<string, unknown> }

/**
 * Save the current pipeline with `mutate` applied to its node list, so a spec
 * can put a node into step mode (or add one) through the real save route.
 */
async function seedGraph(page: Page, mutate: (nodes: SeedNode[]) => void): Promise<void> {
  const status = await page.evaluate(async (mutateSource: string) => {
    const headers: Record<string, string> = { "Content-Type": "application/json" }
    const graphRes = await fetch("/api/pipeline", { headers })
    if (!graphRes.ok) throw new Error(`GET /api/pipeline ${graphRes.status}`)
    const document = await graphRes.json()
    const nodes = document.nodes.map(
      (node: {
        recovery_id: string
        label: string
        decorator_name: string
        node_type: string | null
        description: string
        display_position: { x: number; y: number }
        config: Record<string, unknown> | null
      }) => ({
        id: node.recovery_id,
        type: node.node_type ?? node.decorator_name,
        position: node.display_position,
        data: {
          label: node.label,
          description: node.description,
          nodeType: node.node_type ?? node.decorator_name,
          ...(node.config === null ? {} : { config: node.config }),
        },
      }),
    )
    const edges = document.edges.map(
      (edge: {
        recovery_id: string
        source_recovery_id: string
        target_recovery_id: string
        source_handle: string | null
        target_handle: string | null
        source_port: string | null
        target_port: string | null
      }) => ({
        id: edge.recovery_id,
        source: edge.source_recovery_id,
        target: edge.target_recovery_id,
        sourceHandle: edge.source_handle,
        targetHandle: edge.target_handle,
        ...(edge.source_port === null ? {} : { sourcePort: edge.source_port }),
        ...(edge.target_port === null ? {} : { targetPort: edge.target_port }),
      }),
    )
    new Function("nodes", mutateSource)(nodes)
    const res = await fetch("/api/pipeline/save", {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: document.pipeline_name ?? "main",
        description: document.pipeline_description ?? "",
        source_file: document.source_file,
        base_revision: document.source_revision,
        preamble: document.preamble ?? "",
        preserved_blocks: document.preserved_blocks,
        sources: document.sources,
        active_source: document.active_source ?? "live",
        graph: { nodes, edges },
      }),
    })
    if (!res.ok) throw new Error(`POST /api/pipeline/save ${res.status} ${await res.text()}`)
    return (await res.json()).status
  }, `(${mutate.toString()})(nodes)`)
  expect(status).toBe("saved")
  await page.reload()
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
}

/** Add a Limit step of `n` rows on the open node's Polars tab and wait for its rendering. */
async function addLimitStep(panel: Locator, n: number): Promise<Locator> {
  const editor = panel.getByTestId("polars-steps-editor")
  await expect(editor).toBeVisible()
  await expect(editor.getByLabel("Start from input")).toHaveCount(0)
  await editor.getByRole("button", { name: "Add step" }).click()
  await editor.getByRole("menu", { name: "Add step" }).getByRole("menuitem", { name: "Limit rows" }).click()
  const rowLimit = editor.getByLabel("Row limit")
  await rowLimit.fill(String(n))
  await rowLimit.press("Enter")
  await expect(editor.getByTestId("polars-generated-code")).toContainText(`df = df.head(${n})`)
  return editor
}

test.describe.configure({ mode: "serial" })

test.describe("Transform step builder journey", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("builds a stepped transform, previews it, saves its sidecar and reopens it", async ({ page }) => {
    test.slow()
    await openApp(page)

    // A new Transform starts in step mode with no input: it asks for one.
    const canvas = page.locator(".react-flow")
    await expect(canvas).toBeVisible()
    await page.getByTestId("node-palette-item-polars").dragTo(canvas, {
      targetPosition: { x: 250, y: 150 },
    })
    const newNode = page.getByLabel(/Polars node: Polars/i)
    await expect(newNode).toBeVisible()
    await newNode.click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await expect(panel.getByText(/Connect an input to start building steps/)).toBeVisible()
    const label = page.getByTestId("node-panel-label-input")
    await label.fill("browser_steps")
    await label.press("Enter")
    await expect(label).toHaveValue("browser_steps")
    await page.getByTestId("node-panel-close").click()

    // Connecting the only input seeds the start step and renders `df = raw_rows`.
    const stepsNode = page.getByLabel(/Polars node: browser_steps/i)
    await expect(stepsNode).toBeVisible()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      stepsNode.getByTestId("input-connector[0]:browser_steps"),
    )
    await stepsNode.click()
    await expect(panel).toBeVisible()
    const editor = panel.getByTestId("polars-steps-editor")
    await expect(editor).toBeVisible()
    await expect(editor.getByLabel("Start from input")).toHaveValue("raw_rows")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = raw_rows")

    // Adding a step from the chooser opens its card; editing it re-renders the code.
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Limit rows" }).click()
    await expect(editor.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toHaveAttribute("aria-expanded", "true")
    const rowLimit = editor.getByLabel("Row limit")
    await rowLimit.fill("3")
    await rowLimit.press("Enter")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = df.head(3)")

    // Free code uses the current frame, and later low-code steps keep working.
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Free code" }).click()
    const snippet = 'df = df.with_columns(\n    pl.lit("custom").alias("step_marker")\n)'
    await editor.locator(".cm-content").fill(snippet)
    await expect(editor.getByText("df holds the current frame.", { exact: false })).toHaveCount(0)
    const codeBox = editor.getByTestId("free-code-box")
    await codeBox.scrollIntoViewIfNeeded()
    const initialBox = await codeBox.boundingBox()
    expect(initialBox).not.toBeNull()
    expect(initialBox!.height).toBeLessThanOrEqual(122)
    // Drag the native resize handle; CodeMirror fills the expanded box.
    await page.mouse.move(initialBox!.x + initialBox!.width - 4, initialBox!.y + initialBox!.height - 4)
    await page.mouse.down()
    await page.mouse.move(initialBox!.x + initialBox!.width - 4, initialBox!.y + initialBox!.height + 96, { steps: 8 })
    await page.mouse.up()
    await expect.poll(async () => (await codeBox.boundingBox())!.height).toBeGreaterThan(200)
    const expandedBox = (await codeBox.boundingBox())!
    const editorBox = (await codeBox.locator(".cm-editor").boundingBox())!
    expect(Math.abs(expandedBox.height - editorBox.height)).toBeLessThanOrEqual(2)
    await expect(editor.getByTestId("polars-generated-code")).toContainText('pl.lit("custom").alias("step_marker")')
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Limit rows" }).click()
    await editor.getByLabel("Row limit").fill("2")
    await editor.getByLabel("Row limit").press("Enter")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = df.head(2)")

    // The preview runs the rendered program.
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.getByRole("cell").first()).toBeVisible()
    await expect(previewTable.getByRole("columnheader", { name: /step_marker/ })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "custom", exact: true })).toHaveCount(2)

    // Saving writes the steps to the node's sidecar and the rendered body to the module.
    await save(page)
    await expect.poll(() => existsSync(sidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(sidecarPath, "utf8")) as { steps: Array<{ kind: string }> }
    expect(sidecar.steps.map((step) => step.kind)).toEqual(["source", "limit", "free_code", "limit"])
    expect(sidecar.steps[2]).toMatchObject({ code: snippet })
    const main = readFileSync(mainPath, "utf8")
    expect(main).toContain('config="config/polars/browser_steps.json"')
    expect(main).toContain("def browser_steps(raw_rows: pl.LazyFrame)")
    expect(main).toContain("df = df.head(3)")

    // Reopening parses the sidecar back into the same step cards.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByLabel(/Polars node: browser_steps/i).click()
    await expect(panel).toBeVisible()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
    await expect(panel.getByTestId("polars-generated-code")).toContainText("df = df.head(3)")
    await expect(panel.getByRole("button", { name: "Step 3: Limit rows", exact: true })).toBeVisible()
    await panel.getByRole("button", { name: "Step 2: Free code", exact: true }).click()
    await expect(panel.locator(".cm-content .cm-line")).toHaveText(snippet.split("\n"))
  })

  test("authors a Data Input's post-load steps on its Polars tab, saves them to its sidecar and reopens them", async ({ page }) => {
    test.slow()
    await openApp(page)

    // Seed a Data Input in step mode (an empty list) over the fixture's sample data.
    await seedGraph(page, (nodes) => {
      nodes.push({
        id: "stepped_in",
        type: "custom",
        position: { x: 60, y: 520 },
        data: {
          label: "stepped_in",
          nodeType: "dataInput",
          config: {
            inputType: "file",
            format: "parquet",
            mode: "scan",
            path: "data/sample.parquet",
            arguments: {},
            steps: [],
          },
        },
      })
    })

    // The Polars tab shows the step builder in frame mode: no start card, no input selector.
    await page.getByRole("button", { name: /Data Input node: stepped_in/i }).click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    const editor = panel.getByTestId("polars-steps-editor")
    await expect(editor).toBeVisible()
    await expect(editor.getByText("Start from")).toHaveCount(0)
    await expect(editor.getByLabel("Start from input")).toHaveCount(0)

    // Join and concat are withheld (nothing to reference); a Limit step renders against the frame.
    await editor.getByRole("button", { name: "Add step" }).click()
    const menu = editor.getByRole("menu", { name: "Add step" })
    await expect(menu.getByRole("menuitem", { name: "Join another input" })).toHaveCount(0)
    await expect(menu.getByRole("menuitem", { name: "Group and aggregate" })).toBeVisible()
    await menu.getByRole("menuitem", { name: "Limit rows" }).click()
    await expect(editor.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toHaveAttribute("aria-expanded", "true")
    const rowLimit = editor.getByLabel("Row limit")
    await rowLimit.fill("2")
    await rowLimit.press("Enter")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = df.head(2)")

    // The preview runs the stepped source: the Limit leaves exactly the first two sample rows.
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.locator("tbody tr")).toHaveCount(2)
    await expect(previewTable.getByRole("cell", { name: "11", exact: true })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "23", exact: true })).toBeVisible()

    // Saving writes the steps into the Data Input's own sidecar and the rendering after the load scaffold.
    await save(page)
    const inputSidecarPath = resolve(e2eProjectRoot, "rating", "config", "data_input", "stepped_in.json")
    await expect.poll(() => existsSync(inputSidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(inputSidecarPath, "utf8")) as { steps: Array<Record<string, unknown>>; code?: string }
    expect(sidecar.steps).toEqual([expect.objectContaining({ kind: "limit", n: 2 })])
    expect(sidecar.code).toBeUndefined()
    const main = readFileSync(mainPath, "utf8")
    expect(main).toContain("def stepped_in() -> pl.LazyFrame")
    expect(main).toContain("df = df.head(2)")

    // Reopening parses the sidecar back into the step card.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByRole("button", { name: /Data Input node: stepped_in/i }).click()
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
    await expect(panel.getByTestId("polars-generated-code")).toContainText("df = df.head(2)")
  })

  test("authors a Rating Step's post-rating steps on its Polars tab and saves them to its sidecar", async ({ page }) => {
    test.slow()
    await openApp(page)

    // Put the fixture's rating step into step mode (an empty list) through the save route.
    await seedGraph(page, (nodes) => {
      const rating = nodes.find((node) => node.id === "browser_rating")
      if (!rating) throw new Error("browser_rating missing from the fixture")
      const config = rating.data.config as { tables: Array<Record<string, unknown>> }
      // The fixture table carries no entries, and a table without one produces
      // no output column at all, so its preview would fail its own contract
      // for reasons unrelated to steps. One entry is enough: unmatched rows
      // take the table default.
      const tables = config.tables.map((table, index) =>
        index === 0
          ? {
              ...table,
              entries: [
                {
                  proposer_age_band: "unmatched",
                  channel_band: "unmatched",
                  vehicle_age_band: "unmatched",
                  value: "2.5",
                },
              ],
            }
          : table,
      )
      rating.data.config = { ...config, tables, steps: [] }
    })

    // The Rating Step's Polars tab is the same step builder in frame mode: the rated frame is df.
    await page.getByRole("button", { name: /Rating Step node: browser_rating/i }).click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    const editor = await addLimitStep(panel, 2)
    await editor.getByRole("button", { name: "Add step" }).click()
    await expect(
      editor.getByRole("menu", { name: "Add step" }).getByRole("menuitem", { name: "Join another input" }),
    ).toHaveCount(0)
    await editor.getByRole("button", { name: "Close" }).click()

    // The preview runs the rated frame through the steps: the Limit leaves two rows.
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.locator("tbody tr")).toHaveCount(2)

    // Saving writes the steps into the rating step's own sidecar and the rendering after the rating scaffold.
    await save(page)
    const ratingSidecarPath = resolve(e2eProjectRoot, "rating", "config", "rating_step", "browser_rating.json")
    await expect.poll(() => existsSync(ratingSidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(ratingSidecarPath, "utf8")) as { steps: Array<Record<string, unknown>>; code?: string }
    expect(sidecar.steps).toEqual([expect.objectContaining({ kind: "limit", n: 2 })])
    expect(sidecar.code).toBeUndefined()
    const main = readFileSync(mainPath, "utf8")
    expect(main).toMatch(/def browser_rating\([\s\S]*?apply_rating_step_from_config\([\s\S]*?df = df\.head\(2\)/)

    // Reopening parses the sidecar back into the same step card.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByRole("button", { name: /Rating Step node: browser_rating/i }).click()
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
    await expect(panel.getByTestId("polars-generated-code")).toContainText("df = df.head(2)")
  })

  test("authors a Scenario Expander's post-expansion steps, previews them and reopens them", async ({ page }) => {
    test.slow()
    await openApp(page)

    // Seed a Scenario Expander over the fixture's rows, in step mode.
    await seedGraph(page, (nodes) => {
      nodes.push({
        id: "browser_grid",
        type: "custom",
        position: { x: 60, y: 620 },
        data: {
          label: "browser_grid",
          nodeType: "scenarioExpander",
          config: {
            quote_id: "id",
            column_name: "scenario_value",
            min_value: 0.8,
            max_value: 1.2,
            stepCount: 3,
            step_column: "scenario_index",
            steps: [],
          },
        },
      })
    })
    const gridNode = page.getByLabel(/Expander node: browser_grid/i)
    await expect(gridNode).toBeVisible()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      gridNode.getByTestId("input-connector[0]:browser_grid"),
    )

    // The expanded grid is df: the steps run after the expansion.
    await gridNode.click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    await addLimitStep(panel, 4)

    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.locator("tbody tr")).toHaveCount(4)
    await expect(previewTable.getByRole("columnheader", { name: /scenario_value/ })).toBeVisible()

    await save(page)
    const gridSidecarPath = resolve(e2eProjectRoot, "rating", "config", "expander", "browser_grid.json")
    await expect.poll(() => existsSync(gridSidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(gridSidecarPath, "utf8")) as { steps: Array<Record<string, unknown>>; stepCount: number }
    expect(sidecar.steps).toEqual([expect.objectContaining({ kind: "limit", n: 4 })])
    expect(sidecar.stepCount).toBe(3)
    expect(readFileSync(mainPath, "utf8")).toMatch(
      /def browser_grid\([\s\S]*?expand_scenarios_from_config\([\s\S]*?df = df\.head\(4\)/,
    )

    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByLabel(/Expander node: browser_grid/i).click()
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
  })

  test("authors an External File's steps over its loaded object and connected input", async ({ page }) => {
    test.slow()
    // The loaded object is a JSON file the fixture can carry without a Python artifact.
    writeFileSync(resolve(e2eProjectRoot, "rating", "factors.json"), JSON.stringify({ factor: 2 }), "utf8")
    await openApp(page)

    await seedGraph(page, (nodes) => {
      nodes.push({
        id: "browser_external",
        type: "custom",
        position: { x: 60, y: 720 },
        data: {
          label: "browser_external",
          nodeType: "externalFile",
          config: { path: "factors.json", fileType: "json", steps: [] },
        },
      })
    })
    const externalNode = page.getByLabel(/Load File node: browser_external/i)
    await expect(externalNode).toBeVisible()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      externalNode.getByTestId("input-connector[0]:browser_external"),
    )

    // The first input is df; unlike the other frame surfaces, its steps may join the others.
    await externalNode.click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    const editor = panel.getByTestId("polars-steps-editor")
    await expect(editor).toBeVisible()
    await editor.getByRole("button", { name: "Add step" }).click()
    await expect(
      editor.getByRole("menu", { name: "Add step" }).getByRole("menuitem", { name: "Join another input" }),
    ).toBeVisible()
    await editor.getByRole("button", { name: "Close" }).click()
    await addLimitStep(panel, 2)

    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.locator("tbody tr")).toHaveCount(2)

    await save(page)
    const externalSidecarPath = resolve(e2eProjectRoot, "rating", "config", "load_file", "browser_external.json")
    await expect.poll(() => existsSync(externalSidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(externalSidecarPath, "utf8")) as { steps: Array<Record<string, unknown>> }
    expect(sidecar.steps).toEqual([expect.objectContaining({ kind: "limit", n: 2 })])
    expect(readFileSync(mainPath, "utf8")).toMatch(
      /def browser_external\([\s\S]*?load_external_object_from_config\([\s\S]*?df = df\.head\(2\)/,
    )

    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByLabel(/Load File node: browser_external/i).click()
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^polars$/i }).click()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
  })
})
