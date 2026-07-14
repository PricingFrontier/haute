import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

// dataInput / dataOutput end to end: the nodes render as proper canvas cards
// (regression pin for the nodeTypeRegistry gap found in the first live
// walkthrough, where they fell back to React Flow's unstyled default box),
// the editor's format selector derives from GET /api/formats (engine-gated
// formats flagged with a reason, never hidden), and the saved config
// round-trips through the sidecar + generated code on reload.
test.describe("data input/output nodes", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("cards render and the editor is capability-driven", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    // Extend the seeded graph through the same save route the editor uses.
    const saveStatus = await page.evaluate(async () => {
      const token = (window as unknown as { __HAUTE_SESSION_TOKEN__: string })
        .__HAUTE_SESSION_TOKEN__
      const headers = { "x-haute-session-token": token, "Content-Type": "application/json" }
      const graph = await (await fetch("/api/pipeline", { headers })).json()
      graph.nodes.push(
        {
          id: "wide_in",
          type: "custom",
          position: { x: 60, y: 420 },
          data: {
            label: "wide_in",
            nodeType: "dataInput",
            config: { format: "parquet", path: "data/sample.parquet", arguments: {} },
          },
        },
        {
          id: "wide_out",
          type: "custom",
          position: { x: 420, y: 420 },
          data: {
            label: "wide_out",
            nodeType: "dataOutput",
            config: { format: "ndjson", path: "outputs/wide.jsonl", arguments: {} },
          },
        },
      )
      graph.edges.push({ id: "e_wide_in_wide_out", source: "wide_in", target: "wide_out" })
      const res = await fetch("/api/pipeline/save", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: "main",
          source_file: graph.source_file,
          graph: { nodes: graph.nodes, edges: graph.edges },
        }),
      })
      return (await res.json()).status
    })
    expect(saveStatus).toBe("saved")

    // Reload: the graph now comes from the generated code + sidecars.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    // The registry regression pin: both nodes must render through the custom
    // card component (typed React Flow class), not the default box.
    await expect(page.locator(".react-flow__node-dataInput")).toHaveCount(1)
    await expect(page.locator(".react-flow__node-dataOutput")).toHaveCount(1)
    await expect(page.locator(".react-flow__node-default")).toHaveCount(0)

    const wideIn = page.getByRole("button", { name: /Data Input node: wide_in/i })
    await expect(wideIn).toBeVisible()
    await expect(async () => {
      await wideIn.click({ force: true })
      await expect(page.getByTestId("node-panel")).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 15_000 })

    // The editor renders the saved config and a capability-driven selector:
    // options come from GET /api/formats, with engine-gated formats flagged
    // by reason rather than hidden.
    const formatSelect = page.getByLabel(/format/i).first()
    await expect(formatSelect).toHaveValue("parquet")
    const optionLabels = await formatSelect.locator("option").allTextContents()
    expect(optionLabels.some((t) => /Delta Lake — needs one of: deltalake/.test(t))).toBe(true)
    expect(optionLabels.some((t) => /Text lines \(unstable\)/.test(t))).toBe(true)

    // The saved path round-tripped through sidecar + codegen + parse.
    await expect(page.getByLabel(/path/i).first()).toHaveValue("data/sample.parquet")
  })
})
