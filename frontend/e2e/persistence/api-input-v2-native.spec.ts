/** Persistence-layer e2e for the canonical apiInput flow. */
import { existsSync, readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "../projectIsolation"

const quotesConfigPath = resolve(
  e2eProjectRoot,
  "rating",
  "config",
  "quote_input",
  "quotes.json",
)
const quotesDataPath = resolve(
  e2eProjectRoot,
  "data",
  "quotes",
  "sample_quote.json",
)

test.describe.configure({ mode: "serial" })

test.describe("apiInput persistence", () => {
  test.beforeEach(() => {
    resetE2eProject()
    // Sanity-check the harness scaffolded what we need. If these fail the
    // harness extension didn't run; tests below would fail with confusing
    // selector errors rather than a clear "fixture missing" message.
    expect(existsSync(quotesConfigPath), "harness wrote empty quotes.json").toBe(true)
    expect(existsSync(quotesDataPath), "harness copied sample_quote.json").toBe(true)
    const initialCfg = JSON.parse(readFileSync(quotesConfigPath, "utf8"))
    expect(initialCfg, "starting state has a path and no inferred schema").toEqual(
      { path: "data/quotes/sample_quote.json" },
    )
  })

  test("Infer Tables → Preview → optional Cache round-trips through disk and reload", async ({
    page,
  }) => {
    const consoleErrors: string[] = []
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text())
      }
    })
    const failedResponses: { url: string; status: number }[] = []
    page.on("response", (response) => {
      if (response.status() >= 500) {
        failedResponses.push({ url: response.url(), status: response.status() })
      }
    })

    await page.goto("/")
    await expect(
      page.getByRole("toolbar", { name: /pipeline toolbar/i }),
    ).toBeVisible()

    await test.step("1. Open apiInput node panel", async () => {
      await page.locator('[data-testid="rf__node-quotes"]').click()
      await expect(page.locator('[data-testid="api-input-editor"]')).toBeVisible()
    })

    await test.step("2. Infer Tables produces ≥2 tables with canonical array paths", async () => {
      // Path is already set by the harness fixture. This test stays focused
      // on Infer → direct Preview → optional Cache wiring.
      //
      // Click Infer Tables.
      const inferBtn = page.locator('[data-testid="api-input-infer-btn"]')
      await expect(inferBtn, "Infer Tables button is visible").toBeVisible({
        timeout: 5000,
      })
      const inferResponsePromise = page.waitForResponse((r) =>
        r.url().includes("/api/json-cache/infer"),
      )
      await inferBtn.click()
      const inferResponse = await inferResponsePromise
      expect(
        inferResponse.status(),
        "infer responds 200",
      ).toBe(200)

      // After Infer, tables should be present. Match the row container
      // testid exactly (api-input-table-<ti>) — a prefix selector would
      // also count every child element (-emit, -label, -col-0-name, …)
      // and pass with a single table.
      const tableRows = page.getByTestId(/^api-input-table-\d+$/)
      const tableCount = await tableRows.count()
      expect(
        tableCount,
        "Infer Tables produces at least 2 tables (arrays as child tables, not flat indexed columns)",
      ).toBeGreaterThanOrEqual(2)

      // Scan all column-path inputs (api-input-table-<ti>-col-<ci>-path);
      // table-level
      // path inputs (api-input-table-<ti>-path) have no -col- segment.
      const allColumnPathInputs = page.locator(
        '[data-testid="api-input-tables"] [data-testid*="-col-"][data-testid$="-path"]',
      )
      expect(
        await allColumnPathInputs.count(),
        "at least one column-path input rendered after Infer (guards the scan below against going vacuous)",
      ).toBeGreaterThan(0)
      const allPaths = await allColumnPathInputs.evaluateAll((els) =>
        els.map((el) => (el as HTMLInputElement).value),
      )
      const indexedPaths = allPaths.filter((p) => /\.\d+\./.test(p))
      expect(
        indexedPaths,
        "arrays produce child tables rather than indexed columns",
      ).toEqual([])
    })

    await test.step("3. Preview works before optional Cache as Parquet prewarm", async () => {
      // Runtime must be usable immediately after schema inference: with no
      // parquet yet, the backend shreds the JSON directly for this preview.
      // Request the preview after the inferred schema has been committed to
      // graph state; the preview that ran when the node was first selected
      // intentionally predates inference and cannot represent this schema.
      const previewResponsePromise = page.waitForResponse("**/api/pipeline/preview")
      await page.getByTitle("Refresh preview").click()
      const previewResponse = await previewResponsePromise
      expect(previewResponse.status(), "uncached preview responds 200").toBe(200)
      await expect(
        page.getByTestId("data-preview-table"),
        "bottom preview renders before any cache build",
      ).toBeVisible({ timeout: 10000 })

      const cacheBtn = page.getByRole("button", { name: /cache as parquet/i })
      await expect(cacheBtn, "optional performance-cache button is visible").toBeVisible({
        timeout: 5000,
      })
      const cacheResponsePromise = page.waitForResponse((r) =>
        r.url().includes("/api/json-cache/build"),
      )
      await cacheBtn.click()
      const cacheResponse = await cacheResponsePromise
      // Capture the response body for diagnosis if it fails.
      let cacheBody = ""
      try {
        cacheBody = await cacheResponse.text()
      } catch {
        cacheBody = "<could not read>"
      }
      expect(
        cacheResponse.status(),
        `cache build responds 200 (actual body: ${cacheBody.slice(0, 500)})`,
      ).toBe(200)

      // Optional prewarming must not disturb the already-working preview.
      await expect(
        page.locator(
          "text=API Input data hasn't been cached for the current schema",
        ),
        "bottom preview remains usable after optional prewarm",
      ).toHaveCount(0)
    })

    await test.step("4. Canonical table schema persists on disk", async () => {
      // The editor's writes auto-save in some paths and require explicit
      // save in others. To pin the disk shape we issue a save explicitly
      // here (via Ctrl/Cmd+S) and wait for the API response.
      const saveResponsePromise = page.waitForResponse((r) =>
        r.url().includes("/api/pipeline/save"),
      )
      const isMac = process.platform === "darwin"
      await page.keyboard.press(isMac ? "Meta+s" : "Control+s")
      const saveResponse = await saveResponsePromise
      expect(saveResponse.status(), "pipeline save responds 200").toBe(200)

      const cfg = JSON.parse(readFileSync(quotesConfigPath, "utf8"))
      expect(cfg, "on-disk config has tables[]").toHaveProperty("tables")
      expect(
        cfg.tables.length,
        ">=2 tables persisted (root + ≥1 child)",
      ).toBeGreaterThanOrEqual(2)
    })

    await test.step("5. Reload renders the persisted tables", async () => {
      await page.reload()
      await expect(
        page.getByRole("toolbar", { name: /pipeline toolbar/i }),
      ).toBeVisible()
      await page.locator('[data-testid="rf__node-quotes"]').click()
      await expect(page.locator('[data-testid="api-input-editor"]')).toBeVisible()
      expect(await page.getByTestId(/^api-input-table-\d+$/).count()).toBeGreaterThanOrEqual(2)
    })

    // Capture any console errors or 5xx responses that fired during the run.
    expect(
      consoleErrors,
      `no console errors during the flow (saw: ${consoleErrors.join(" | ")})`,
    ).toEqual([])
    expect(
      failedResponses,
      `no 5xx responses during the flow (saw: ${JSON.stringify(failedResponses)})`,
    ).toEqual([])
  })
})
