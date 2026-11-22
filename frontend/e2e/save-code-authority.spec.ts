import { readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { dispatchAppShortcut } from "./browserInteractions"
import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

// A save reads from the browser only the parts of a node that were edited (CGEN-11, CGEN-13):
// a step list or a setting the browser held has lost what JavaScript cannot hold, `1.0`
// arriving as `1`. Within a part read each number keeps its spelling: a step's as its `text`,
// a setting's from the saved settings the edit began from (CGEN-09).
const mainPath = resolve(e2eProjectRoot, "rating", "main.py")
// A Rating Step's settings file, whose default is written `1.0`.
const ratingPath = resolve(e2eProjectRoot, "rating", "config", "rating_step", "browser_rating.json")

const HAND_WRITTEN = `

@pipeline.polars
def hand_written(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    df = raw_rows
    df = df.with_columns(pl.lit(1.0).alias("relativity"))
    df = df.filter(pl.col("value") == 9007199254740993)
    return df


@pipeline.polars
def rated_rows(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    df = raw_rows
    df = df.with_columns(pl.lit(2.5).alias("rate"))
    return df
`

/** One Transform holding the four forms a browser writes otherwise. */
const FOUR_NUMBERS = `

@pipeline.polars
def four_numbers(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    df = raw_rows
    df = df.with_columns(pl.lit(1.0).alias("relativity"))
    df = df.with_columns((pl.col("value") * 2.0).alias("value"))
    df = df.with_columns(pl.col("value").fill_null(0.0))
    df = df.filter(pl.col("value") == 9007199254740993)
    return df
`

const read = () => readFileSync(mainPath, "utf8")
const readRating = () => readFileSync(ratingPath, "utf8")

/** The text of the node function *name* in *source*, up to the next decorator. */
function nodeFunction(source: string, name: string): string {
  const start = source.indexOf(`def ${name}(`)
  expect(start, `${name} is in the file`).toBeGreaterThanOrEqual(0)
  const end = source.indexOf("\n@", start)
  return source.slice(start, end < 0 ? undefined : end)
}

async function openApp(page: Page): Promise<void> {
  const loaded = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  expect((await loaded).status()).toBe(200)
}

function saveResponse(page: Page) {
  return page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
}

async function save(page: Page): Promise<void> {
  const answered = saveResponse(page)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await answered).status(), "pipeline save succeeds").toBe(200)
}

async function openNode(page: Page, name: string) {
  await page.getByLabel(new RegExp(`Transform node: ${name}\\b`, "i")).click()
  const editor = page.getByTestId("node-panel").getByTestId("polars-steps-editor")
  // The editor is a lazily loaded chunk, which a cold development server builds first.
  await expect(editor).toBeVisible({ timeout: 60_000 })
  return editor
}

async function closePanel(page: Page): Promise<void> {
  await page.getByTestId("node-panel-close").click()
  await expect(page.getByTestId("node-panel")).toHaveCount(0)
}

/** Add a Limit step of *n* rows to the open editor, without waiting for its rendering. */
async function addLimit(page: Page, n: number): Promise<void> {
  const editor = page.getByTestId("node-panel").getByTestId("polars-steps-editor")
  await editor.getByRole("button", { name: "Add step" }).click()
  await editor.getByRole("menu", { name: "Add step" }).getByRole("menuitem", { name: "Limit rows" }).click()
  const rowLimit = editor.getByLabel("Row limit").last()
  await rowLimit.fill(String(n))
  await rowLimit.press("Enter")
}

test.describe.configure({ mode: "serial" })

test.describe("A save writes only the parts that were edited", () => {
  test.beforeEach(() => {
    resetE2eProject()
    writeFileSync(mainPath, read().trimEnd() + "\n" + HAND_WRITTEN, "utf8")
  })

  test("keeps hand-written code and settings nobody edited through saves, Undo, Redo and a late rendering", async ({ page }) => {
    test.slow()
    await openApp(page)

    // Saved untouched, the hand-written numbers stay as written; saved again, nothing moves.
    await save(page)
    const first = read()
    const handWritten = nodeFunction(first, "hand_written")
    expect(handWritten).toContain('pl.lit(1.0).alias("relativity")')
    expect(handWritten).toContain('pl.col("value") == 9007199254740993')
    // A setting nobody edited keeps its spelling too.
    const rating = readRating()
    expect(rating).toContain('"default_value": 1.0')
    await save(page)
    expect(read()).toBe(first)
    expect(readRating()).toBe(rating)

    // Opening a panel on steps nobody edited edits nothing.
    const opened = await openNode(page, "hand_written")
    await expect(opened.getByTestId("polars-generated-code")).toContainText("relativity")
    await closePanel(page)
    await save(page)
    expect(read()).toBe(first)

    // An edited node is written from its steps, and no other node is.
    await openNode(page, "rated_rows")
    await addLimit(page, 5)
    await expect(page.getByTestId("polars-generated-code")).toContainText("df = df.head(5)")
    await closePanel(page)
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // Undo and Redo each save the steps on screen, and only those.
    await page.getByTestId("rf__node-rated_rows").click()
    await dispatchAppShortcut(page, "z")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).not.toContain("df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)
    await page.getByTestId("rf__node-rated_rows").click()
    await dispatchAppShortcut(page, "y")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // An edit made while a save runs is saved by the next one.
    let release!: () => void
    const held = new Promise<void>((resolve) => { release = resolve })
    await page.route("**/api/pipeline/save", async (route) => {
      await held
      await route.continue()
    })
    const answered = saveResponse(page)
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await openNode(page, "rated_rows")
    await addLimit(page, 4)
    await expect(page.getByTestId("polars-generated-code")).toContainText("df = df.head(4)")
    release()
    expect((await answered).status()).toBe(200)
    await page.unroute("**/api/pipeline/save")
    expect(nodeFunction(read(), "rated_rows")).not.toContain("df.head(4)")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(4)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // With the browser's own rendering held back, the second save leaves the file as the
    // first wrote it: the baseline holds the code the server wrote.
    await page.route("**/api/pipeline/polars-steps/render", () => {})
    await addLimit(page, 3)
    await save(page)
    const rendered = read()
    expect(nodeFunction(rendered, "rated_rows")).toContain("df = df.head(3)")
    await save(page)
    expect(read()).toBe(rendered)
    await page.unroute("**/api/pipeline/polars-steps/render")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)
    expect(readRating()).toBe(rating)
  })

  test("keeps the numbers of an edited node's other steps as they were written", async ({ page }) => {
    test.slow()
    writeFileSync(mainPath, read().trimEnd() + "\n" + FOUR_NUMBERS, "utf8")
    await openApp(page)

    await openNode(page, "four_numbers")
    await addLimit(page, 5)
    const code = page.getByTestId("polars-generated-code")
    await expect(code).toContainText("df = df.head(5)")
    await expect(code).toContainText('pl.lit(1.0).alias("relativity")')
    await closePanel(page)
    await save(page)

    const written = nodeFunction(read(), "four_numbers")
    expect(written).toContain("df = df.head(5)")
    for (const statement of [
      'pl.lit(1.0).alias("relativity")',
      '(pl.col("value") * 2.0).alias("value")',
      'pl.col("value").fill_null(0.0)',
      'pl.col("value") == 9007199254740993',
    ]) {
      expect(written).toContain(statement)
    }
  })

  test("keeps a Rating Step's other numbers when its output column is renamed", async ({ page }) => {
    test.slow()
    await openApp(page)
    expect(readRating()).toContain('"default_value": 1.0')

    await page.getByLabel("Rating Step node: browser_rating").click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await panel.getByRole("button", { name: /^browser_relativity / }).dblclick()
    const name = panel.getByLabel("Rename browser_relativity")
    await name.fill("browser_factor")
    await name.press("Enter")
    await closePanel(page)
    await save(page)

    const written = readRating()
    expect(written).toContain('"output_column": "browser_factor"')
    expect(written).toContain('"default_value": 1.0')
  })
  test("keeps a Rating Step's numbers when it is grouped into a submodel and saved", async ({ page }) => {
    test.slow()
    await openApp(page)
    expect(readRating()).toContain('"default_value": 1.0')

    // A graph change gives the node a new place; it finds its saved settings through the one
    // it came from (CGEN-09).
    await page.getByRole("button", { name: /browser_mixed_banding/i }).click()
    await page.getByRole("button", { name: /browser_rating/i }).click({
      modifiers: [process.platform === "darwin" ? "Meta" : "Control"],
    })
    await dispatchAppShortcut(page, "g")
    await expect(page.getByText("Create Submodel")).toBeVisible()
    await page.getByPlaceholder("e.g. model_scoring").fill("browser_group")
    await page.getByRole("button", { name: "Create" }).click()
    await expect(page.getByRole("button", { name: /browser_group/i })).toBeVisible()
    await save(page)

    expect(readRating()).toContain('"default_value": 1.0')
  })
})
