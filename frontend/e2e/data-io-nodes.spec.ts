import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

// dataInput / dataOutput end to end: the nodes render as proper canvas cards
// (regression pin for the nodeTypeRegistry gap found in the first live
// walkthrough, where they fell back to React Flow's unstyled default box),
// the editor's provider/format selectors derive from GET /api/io-capabilities
// and preserve provider grouping, and the saved config round-trips through the
// sidecar + generated code on reload.
test.describe("data input/output nodes", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("cards render and the editor is capability-driven", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    // Same-origin page fetches share the HttpOnly session cookie established
    // by the real application bootstrap.
    const saveStatus = await page.evaluate(async () => {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      const graphRes = await fetch("/api/pipeline", { headers })
      if (!graphRes.ok) throw new Error(`GET /api/pipeline ${graphRes.status}`)
      const graph = await graphRes.json()
      graph.nodes.push(
        {
          id: "wide_in",
          type: "custom",
          position: { x: 60, y: 420 },
          data: {
            label: "wide_in",
            nodeType: "dataInput",
            config: {
              inputType: "file",
              format: "parquet",
              mode: "scan",
              cacheMode: "direct",
              path: "data/sample.parquet",
              arguments: {},
            },
          },
        },
        {
          id: "wide_out",
          type: "custom",
          position: { x: 420, y: 420 },
          data: {
            label: "wide_out",
            nodeType: "dataOutput",
            config: {
              outputType: "file",
              format: "ndjson",
              mode: "sink",
              path: "outputs/wide.jsonl",
              arguments: {},
            },
          },
        },
      )
      graph.edges.push({ id: "e_wide_in_wide_out", source: "wide_in", target: "wide_out" })
      const res = await fetch("/api/pipeline/save", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: graph.pipeline_name ?? "main",
          source_file: graph.source_file,
          graph: { nodes: graph.nodes, edges: graph.edges },
        }),
      })
      if (!res.ok) throw new Error(`POST /api/pipeline/save ${res.status}`)
      return (await res.json()).status
    })
    expect(saveStatus).toBe("saved")

    // Reload: the graph now comes from the generated code + sidecars.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    // The registry regression pin: the two nodes created above must render
    // through their custom card components, irrespective of other seeded
    // Data Input nodes in the shared browser fixture.
    const wideIn = page.getByRole("button", { name: /Data Input node: wide_in/i })
    const wideOut = page.getByRole("button", { name: /Data Output node: wide_out/i })
    await expect(page.locator(".react-flow__node-dataInput").filter({ has: wideIn })).toHaveCount(1)
    await expect(page.locator(".react-flow__node-dataOutput").filter({ has: wideOut })).toHaveCount(
      1,
    )
    await expect(page.locator(".react-flow__node-default")).toHaveCount(0)

    await expect(wideIn).toBeVisible()
    await expect(async () => {
      await wideIn.click({ force: true })
      await expect(page.getByTestId("node-panel")).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 15_000 })

    // The editor renders the saved config and capability-driven selectors.
    // File formats stay scoped to File; Lakehouse formats appear only after
    // selecting that provider.
    const providerSelect = page.getByLabel("Provider")
    await expect(providerSelect).toHaveValue("file")
    const formatSelect = page.getByLabel(/format/i).first()
    await expect(formatSelect).toHaveValue("parquet")
    const optionLabels = await formatSelect.locator("option").allTextContents()
    expect(optionLabels.some((t) => /Text lines \(unstable\)/.test(t))).toBe(true)
    await expect(formatSelect.locator('option[value="delta"]')).toHaveCount(0)
    await expect(page.getByLabel("Mode")).toHaveCount(0)
    await expect(page.getByRole("button", { name: "Cache as Parquet" })).toHaveCount(0)

    // The saved path round-tripped through sidecar + codegen + parse.
    await expect(page.getByLabel(/path/i).first()).toHaveValue("data/sample.parquet")

    await formatSelect.selectOption("csv")
    await expect(page.getByLabel("Mode")).toHaveCount(0)
    await expect(page.getByRole("button", { name: "Cache as Parquet" })).toBeVisible()

    await providerSelect.selectOption("lakehouse")
    await expect(formatSelect.locator('option[value="delta"]')).toContainText("Delta Lake")
    await expect(formatSelect.locator('option[value="lines"]')).toHaveCount(0)
  })
})
