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
    // Capture the session token from the app's own first API request — the
    // vite dev harness bakes it into the bundle (import.meta.env), so it is
    // not readable from the page context directly.
    const firstApiRequest = page.waitForRequest((r) => r.url().includes("/api/pipeline"))
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const sessionToken = (await firstApiRequest).headers()["x-haute-session-token"]

    // Extend the seeded graph through the same save route the editor uses.
    const saveStatus = await page.evaluate(async (token) => {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (token) headers["x-haute-session-token"] = token
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
    }, sessionToken)
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

    // The editor renders the saved config and a capability-driven selector:
    // options come from GET /api/formats, with engine-gated formats flagged
    // by reason rather than hidden.
    const formatSelect = page.getByLabel(/format/i).first()
    await expect(formatSelect).toHaveValue("parquet")
    const optionLabels = await formatSelect.locator("option").allTextContents()
    expect(optionLabels.some((t) => /Delta Lake.*needs one of: deltalake/.test(t))).toBe(true)
    expect(optionLabels.some((t) => /Text lines \(unstable\)/.test(t))).toBe(true)

    // The saved path round-tripped through sidecar + codegen + parse.
    await expect(page.getByLabel(/path/i).first()).toHaveValue("data/sample.parquet")
  })
})
