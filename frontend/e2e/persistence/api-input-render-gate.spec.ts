/**
 * Render-gate e2e for the v2-native apiInput editor.
 *
 * Guards the `readV2` render-gate fix: structurally-incomplete persisted
 * entries (a table with a blank `path`, a column with a blank `name`/`path`,
 * a table with a blank `label`) must SURFACE in the editor for repair —
 * never be silently dropped on read or masked. Before the fix, `readV2`
 * did `if (!path) continue` / `if (!cname || !cpath) continue`, so such an
 * entry vanished on read and was re-serialised away on the next edit (silent
 * data loss); a blank `label` was coerced to the path (masked as valid).
 *
 * Per AGENTS.md §UI Test Assertions: drive the real browser, seed the
 * persisted config on disk, and read what the editor actually renders.
 *
 * NB: the editor renders rows from the CONFIG (classifyConfig → readV2),
 * independent of whether the paths resolve against the sample data — so the
 * fixture paths need only be grammar-valid `[:]`, not present in
 * sample_quote.json. The companion unit suite is
 * `src/__tests__/editors/apiInputSchemaReadGate.test.ts`.
 */
import { writeFileSync } from "node:fs"
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

/** A v2 column with the inference defaults; name/path are the variables. */
function col(name: string, path: string, type = "str") {
  return { name, path, type, status: "Inferred", selected: true, levels: null }
}

function table(
  path: string,
  label: string,
  columns: ReturnType<typeof col>[],
) {
  return { path, label, displayPath: null, emit: true, row_id_column: null, columns }
}

/** All-valid baseline: 2 well-formed tables, no blanks. */
const CONTROL_CONFIG = {
  path: "data/quotes/sample_quote.json",
  contract: "opaque",
  tables: [
    table("$[:]", "quotes", [
      col("quote_id", "$[:].quote_id"),
      col("premium_amount", "$[:].premium_amount", "float"),
    ]),
    table("$[:].claims[:]", "claims", [
      col("amount", "$[:].claims[:].amount", "float"),
      col("claim_date", "$[:].claims[:].claim_date"),
    ]),
  ],
}

/**
 * The baseline plus one of each "incomplete entry". Table/column indices are
 * load-bearing for the testid assertions below:
 *   table 0 "quotes": col 0 valid · col 1 blank NAME · col 2 blank PATH
 *   table 1 "claims": valid
 *   table 2 "orphan_no_path": blank table PATH (whole table dropped pre-fix)
 *   table 3: blank LABEL, path intact (label masked as path pre-fix)
 */
const RENDER_GATE_CONFIG = {
  path: "data/quotes/sample_quote.json",
  contract: "opaque",
  tables: [
    table("$[:]", "quotes", [
      col("quote_id", "$[:].quote_id"),
      col("", "$[:].premium_amount", "float"), // blank NAME, path intact
      col("channel", ""), // blank PATH, name intact
    ]),
    table("$[:].claims[:]", "claims", [
      col("amount", "$[:].claims[:].amount", "float"),
    ]),
    table("", "orphan_no_path", []), // blank table PATH
    table("$[:].addons[:]", "", [
      col("addon_code", "$[:].addons[:].code"),
    ]), // blank LABEL, path intact
  ],
}

function seedConfig(config: unknown): void {
  writeFileSync(quotesConfigPath, JSON.stringify(config, null, 2) + "\n", "utf8")
}

async function openQuotesEditor(page: import("@playwright/test").Page) {
  await page.goto("/")
  await expect(
    page.getByRole("toolbar", { name: /pipeline toolbar/i }),
  ).toBeVisible()
  await page.getByTestId("rf__node-quotes").click()
  const editor = page.getByTestId("api-input-editor")
  await expect(editor).toBeVisible()
  return editor
}

test.describe("apiInput v2 render-gate (persistence layer)", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("keeps and flags every incomplete persisted entry (no silent drop)", async ({
    page,
  }) => {
    seedConfig(RENDER_GATE_CONFIG)
    const editor = await openQuotesEditor(page)

    // 1. KEEP: all four tables render — none dropped. (Pre-fix the blank-path
    //    "orphan_no_path" table and the two blank columns would have vanished.)
    await expect(page.getByTestId(/^api-input-table-\d+$/)).toHaveCount(4)

    // 2. KEEP the blanks verbatim at their persisted positions.
    await expect(page.getByTestId("api-input-table-0-col-1-name")).toHaveValue("")
    await expect(page.getByTestId("api-input-table-0-col-1-path")).toHaveValue(
      "$[:].premium_amount",
    )
    await expect(page.getByTestId("api-input-table-0-col-2-name")).toHaveValue(
      "channel",
    )
    await expect(page.getByTestId("api-input-table-0-col-2-path")).toHaveValue("")
    await expect(page.getByTestId("api-input-table-2-path")).toHaveValue("")
    // Blank label kept verbatim — NOT coerced to its (valid) path.
    await expect(page.getByTestId("api-input-table-3-label")).toHaveValue("")
    await expect(page.getByTestId("api-input-table-3-path")).toHaveValue(
      "$[:].addons[:]",
    )

    // 3. FLAG: each blank surfaces its inline validation error (idle
    //    validation — no interaction needed).
    await expect(
      editor.getByText("this column is invalid and can't be saved", {
        exact: false,
      }),
      "blank column name + blank column path are both flagged",
    ).toHaveCount(2)
    await expect(
      editor.getByText("this table is invalid and can't be saved", {
        exact: false,
      }),
      "blank table path is flagged",
    ).toHaveCount(1)
    await expect(
      editor.getByText("it names this table's frame", { exact: false }),
      "blank label is flagged (and not masked as the path)",
    ).toHaveCount(1)
  })

  test("a fully valid config renders no validation errors (control)", async ({
    page,
  }) => {
    seedConfig(CONTROL_CONFIG)
    const editor = await openQuotesEditor(page)

    await expect(page.getByTestId(/^api-input-table-\d+$/)).toHaveCount(2)
    // No blank-field validation messages for a well-formed config — proves the
    // errors above are caused by the anomalies, not spurious flagging.
    await expect(
      editor.getByText("is invalid and can't be saved", { exact: false }),
    ).toHaveCount(0)
    await expect(
      editor.getByText("it names this table's frame", { exact: false }),
    ).toHaveCount(0)
  })
})
