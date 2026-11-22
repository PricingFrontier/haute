import { expect, type Page } from "@playwright/test"

// Opening the app and seeding its pipeline through the real routes, for specs that start
// from nodes the fixture does not have.

export async function openApp(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  const response = await responsePromise
  expect(response.status(), "initial pipeline request succeeds").toBe(200)
}

/** The node list as the save route wants it, from the editor document. */
export type SeedNode = { id: string; type: string; position: unknown; data: Record<string, unknown> }

/**
 * Save the current pipeline with `mutate` applied to its node list, so a spec
 * can put a node into step mode (or add one) through the real save route.
 */
export async function seedGraph(page: Page, mutate: (nodes: SeedNode[]) => void): Promise<void> {
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
      }) => ({
        id: edge.recovery_id,
        source: edge.source_recovery_id,
        target: edge.target_recovery_id,
        sourceHandle: edge.source_handle,
        targetHandle: edge.target_handle,
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
