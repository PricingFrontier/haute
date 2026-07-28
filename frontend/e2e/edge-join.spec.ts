import { mkdirSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page, type Request } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const pipelinePath = resolve(ratingDir, "main.py")
const dataInputDir = resolve(ratingDir, "config", "data_input")
const lookupDataPath = resolve(ratingDir, "data", "lookup.csv")
const quoteConfigPath = resolve(ratingDir, "config", "quote_input", "quotes.json")
const quoteDataPath = resolve(e2eProjectRoot, "data", "quotes", "sample_quote.json")

type Point = { x: number; y: number }
type GraphNode = {
  id: string
  data: { label?: string; nodeType?: string; config?: Record<string, unknown> }
}
type GraphEdge = {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}
type GraphEnvelope = {
  nodes?: GraphNode[]
  edges?: GraphEdge[]
  graph?: { nodes?: GraphNode[]; edges?: GraphEdge[] }
}

type NormalizedGraph = { nodes: GraphNode[]; edges: GraphEdge[] }

function graphEnvelope(payload: GraphEnvelope): NormalizedGraph {
  return {
    nodes: payload.graph?.nodes ?? payload.nodes ?? [],
    edges: payload.graph?.edges ?? payload.edges ?? [],
  }
}

function findEdge(graph: NormalizedGraph, source: string, target: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.source === source && candidate.target === target)
  if (!edge) throw new Error(`Missing graph edge ${source} -> ${target}`)
  return edge
}

function findEdgeJoin(graph: NormalizedGraph, joinSource: string): GraphNode {
  const node = graph.nodes.find(
    (candidate) =>
      candidate.data.nodeType === "edgeJoin" && candidate.data.config?.joinInput === joinSource,
  )
  if (!node) throw new Error(`Missing Edge Join whose joinInput is ${joinSource}`)
  return node
}

function seedPipeline(): void {
  mkdirSync(dataInputDir, { recursive: true })
  mkdirSync(resolve(ratingDir, "data"), { recursive: true })
  writeFileSync(
    resolve(dataInputDir, "raw_rows.json"),
    `${JSON.stringify({
      inputType: "file",
      format: "parquet",
      mode: "scan",
      path: "data/sample.parquet",
      arguments: {},
    }, null, 2)}\n`,
    "utf8",
  )
  writeFileSync(
    resolve(dataInputDir, "lookup_rows.json"),
    `${JSON.stringify({
      inputType: "file",
      format: "csv",
      mode: "scan",
      path: "data/lookup.csv",
      arguments: {
        schema: {
          id: "int64",
          lookup_value: "str",
        },
      },
    }, null, 2)}\n`,
    "utf8",
  )
  writeFileSync(lookupDataPath, "id,lookup_value\n1,lookup\n2,lookup\n", "utf8")
  writeFileSync(
    quoteDataPath,
    `${JSON.stringify([{ id: 1, api_score: 0.25 }, { id: 2, api_score: 0.5 }], null, 2)}\n`,
    "utf8",
  )
  writeFileSync(
    quoteConfigPath,
    `${JSON.stringify({
      path: "../data/quotes/sample_quote.json",
      contract: "opaque",
      tables: [{
        path: "$[:]",
        label: "api_lookup",
        displayPath: null,
        emit: true,
        row_id_column: null,
        columns: [
          { name: "id", path: "$[:].id", type: "int", status: "Inferred", selected: true, levels: null },
          { name: "api_score", path: "$[:].api_score", type: "float", status: "Inferred", selected: true, levels: null },
        ],
      }],
    }, null, 2)}\n`,
    "utf8",
  )
  writeFileSync(
    pipelinePath,
    [
      '"""Small deterministic pipeline for Edge Join browser coverage."""',
      "",
      "from pathlib import Path",
      "",
      "import polars as pl",
      "",
      "import haute",
      "",
      'pipeline = haute.Pipeline("edge_join_e2e")',
      "",
      '@pipeline.data_input(config="config/data_input/raw_rows.json")',
      "def raw_rows() -> pl.LazyFrame:",
      "    from haute.graph_utils import resolve_data_input_from_config",
      "    df = resolve_data_input_from_config(",
      '        "config/data_input/raw_rows.json",',
      "        base_dir=Path(__file__).parent,",
      "    )",
      "    return df",
      "",
      '@pipeline.data_input(config="config/data_input/lookup_rows.json")',
      "def lookup_rows() -> pl.LazyFrame:",
      "    from haute.graph_utils import resolve_data_input_from_config",
      "    df = resolve_data_input_from_config(",
      '        "config/data_input/lookup_rows.json",',
      "        base_dir=Path(__file__).parent,",
      "    )",
      "    return df",
      "",
      '@pipeline.api_input(config="config/quote_input/quotes.json")',
      "def quotes() -> pl.LazyFrame:",
      "    return pl.LazyFrame()",
      "",
      "@pipeline.polars",
      "def enriched(raw_rows: pl.LazyFrame) -> pl.LazyFrame:",
      "    df = raw_rows",
      '    df = df.with_columns((pl.col("value") * 2).alias("value_doubled"))',
      "    return df",
      "",
      'pipeline.connect("raw_rows", "enriched")',
      "",
    ].join("\n"),
    "utf8",
  )
}

async function captureInitialGraph(page: Page): Promise<NormalizedGraph> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  const response = await responsePromise
  expect(response.status(), "initial pipeline request succeeds").toBe(200)
  return graphEnvelope((await response.json()) as GraphEnvelope)
}

async function reloadAndCaptureGraph(page: Page): Promise<NormalizedGraph> {
  const graphResponse = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.reload()
  const response = await graphResponse
  expect(response.status(), "reloaded pipeline request succeeds").toBe(200)
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  return graphEnvelope((await response.json()) as GraphEnvelope)
}

async function sourceHandle(page: Page, nodeId: string, handleId?: string): Promise<Locator> {
  const node = page.getByTestId(`rf__node-${nodeId}`)
  await expect(node, `source node ${nodeId} is visible`).toBeVisible()
  const handle = handleId
    ? node.locator(`[data-handleid="${handleId}"]`)
    : node.getByTestId(`output-connector[0]:${nodeId}`)
  await expect(handle, `source handle ${handleId ?? "default"} on ${nodeId} is visible`).toBeVisible()
  return handle
}

async function visibleEdgePoint(page: Page, edgeId: string): Promise<Point> {
  const path = page.getByTestId(`rf__edge-${edgeId}`).locator("path.react-flow__edge-path").first()
  await expect(path, `rendered path for ${edgeId} is attached`).toBeAttached()
  await expect
    .poll(() => path.evaluate((element: SVGPathElement) => element.getTotalLength()))
    .toBeGreaterThan(0)
  const point = await path.evaluate((element: SVGPathElement, expectedEdgeId) => {
    const matrix = element.getScreenCTM()
    if (!matrix) return null
    const length = element.getTotalLength()
    for (let step = 1; step < 50; step += 1) {
      const local = element.getPointAtLength(length * (step / 50))
      const screen = new DOMPoint(local.x, local.y).matrixTransform(matrix)
      const topRelevantElement = document.elementsFromPoint(screen.x, screen.y).find((candidate) =>
        candidate.closest(".react-flow__handle, .react-flow__node, .react-flow__edge[data-id]"),
      )
      const visibleEdge = topRelevantElement?.closest(".react-flow__edge[data-id]")
      if (visibleEdge?.getAttribute("data-id") === expectedEdgeId) {
        return { x: screen.x, y: screen.y }
      }
    }
    return null
  }, edgeId)
  if (!point) throw new Error(`Could not find a visible live SVG point for edge ${edgeId}`)
  return point
}

async function dragSourceToEdge(page: Page, source: Locator, edgeId: string): Promise<void> {
  await page.getByTestId("toolbar-centre").click()
  await expect(source).toBeInViewport()
  await source.hover()
  const targetPoint = await visibleEdgePoint(page, edgeId)
  await page.mouse.down()
  await page.mouse.move(targetPoint.x, targetPoint.y, { steps: 12 })
  const insertionStatus = page.getByRole("status", {
    name: "Release to insert an Edge Join on this connection",
  })
  await expect(insertionStatus).toBeAttached()
  await expect(page.getByTestId(`rf__edge-${edgeId}`)).toHaveClass(/edge-join-insertion-candidate/)
  await page.mouse.up()
  await expect(insertionStatus).toHaveCount(0)
}

async function setSameNameKey(page: Page, key: string): Promise<void> {
  const input = page.getByLabel("Same-name key 1")
  await expect(input).toBeVisible()
  if (await input.evaluate((element) => element.tagName === "SELECT")) {
    await input.selectOption(key)
  } else {
    await input.fill(key)
    await input.press("Tab")
  }
}

async function saveAndCapture(page: Page): Promise<{ request: Request; graph: NormalizedGraph }> {
  const requestPromise = page.waitForRequest((request) =>
    request.method() === "POST" && request.url().includes("/api/pipeline/save"),
  )
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
  await page.getByRole("button", { name: "Save", exact: true }).click()
  const [request, response] = await Promise.all([requestPromise, responsePromise])
  expect(response.status(), "pipeline save succeeds").toBe(200)
  return { request, graph: graphEnvelope(request.postDataJSON() as GraphEnvelope) }
}

function expectJoinTopology(graph: NormalizedGraph, join: GraphNode, base: string, source: string, downstream: string): void {
  const config = join.data.config
  expect(config, `${join.id} has edge join config`).toMatchObject({ baseInput: base, joinInput: source, how: "left", on: ["id"] })
  const incoming = graph.edges.filter((edge) => edge.target === join.id)
  expect(incoming, `${join.id} has exactly its base and join role inputs`).toHaveLength(2)
  expect(incoming.find((edge) => edge.source === base && edge.targetHandle === "base"), "base role edge").toBeDefined()
  expect(incoming.find((edge) => edge.source === source && edge.targetHandle === "join"), "join role edge").toBeDefined()
  expect(graph.edges.find((edge) => edge.source === join.id && edge.target === downstream), "split downstream edge").toBeDefined()
}

test.describe.configure({ mode: "serial" })

test.describe("Edge Join insertion workflow", () => {
  test.beforeEach(() => {
    resetE2eProject()
    seedPipeline()
  })

  test("inserts lookup and named API joins on rendered edges, persists them, and traces both", async ({ page }) => {
    test.slow()
    const initialGraph = await captureInitialGraph(page)
    const rawToEnriched = findEdge(initialGraph, "raw_rows", "enriched")

    await dragSourceToEdge(page, await sourceHandle(page, "lookup_rows"), rawToEnriched.id)
    const firstJoinNode = page.getByLabel(/Edge Join node: Edge Join 1/i)
    await expect(firstJoinNode).toBeVisible()
    await expect(firstJoinNode).toHaveAttribute("aria-label", /Edge Join node: Edge Join 1/i)
    await expect(firstJoinNode.getByTestId("edge-join-base-handle")).toBeVisible()
    await expect(firstJoinNode.getByTestId("edge-join-join-handle")).toBeVisible()
    await setSameNameKey(page, "id")
    await page.getByRole("button", { name: "Refresh" }).click()
    const firstPreview = page.getByRole("table").first()
    await expect(firstPreview.getByText("id", { exact: true })).toBeVisible()
    await expect(firstPreview.getByText("value", { exact: true })).toBeVisible()
    await expect(firstPreview.getByText("lookup_value", { exact: true })).toBeVisible()
    await expect(firstPreview.getByRole("cell", { name: "lookup" }).first()).toBeVisible()

    const firstSave = await saveAndCapture(page)
    const firstSavedJoin = findEdgeJoin(firstSave.graph, "lookup_rows")
    expectJoinTopology(firstSave.graph, firstSavedJoin, "raw_rows", "lookup_rows", "enriched")
    const afterFirstReload = await reloadAndCaptureGraph(page)
    const reloadedFirstJoin = findEdgeJoin(afterFirstReload, "lookup_rows")
    expectJoinTopology(afterFirstReload, reloadedFirstJoin, "raw_rows", "lookup_rows", "enriched")
    await expect(page.getByTestId(`rf__node-${reloadedFirstJoin.id}`)).toBeVisible()
    await expect(page.getByTestId(`rf__node-${reloadedFirstJoin.id}`).getByTestId("edge-join-base-handle")).toBeVisible()

    const firstJoinToEnriched = findEdge(afterFirstReload, reloadedFirstJoin.id, "enriched")
    await dragSourceToEdge(page, await sourceHandle(page, "quotes", "api_lookup"), firstJoinToEnriched.id)
    const renderedEdgeJoins = page.locator(".react-flow__node-edgeJoin")
    await expect(renderedEdgeJoins).toHaveCount(2)
    const renderedEdgeJoinIds = await renderedEdgeJoins.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("data-id")).filter((id): id is string => id !== null),
    )
    const secondJoinId = renderedEdgeJoinIds.find((id) => id !== reloadedFirstJoin.id)
    if (!secondJoinId) throw new Error("Could not identify the newly inserted second Edge Join")
    const secondJoinNode = page.getByTestId(`rf__node-${secondJoinId}`)
    await expect(secondJoinNode).toBeVisible()
    await expect(secondJoinNode.getByLabel(/Edge Join node:/i)).toBeVisible()
    await expect(secondJoinNode.getByTestId("edge-join-base-handle")).toBeVisible()
    await expect(secondJoinNode.getByTestId("edge-join-join-handle")).toBeVisible()
    await setSameNameKey(page, "id")
    await page.getByRole("button", { name: "Refresh" }).click()
    const secondPreview = page.getByRole("table").first()
    await expect(secondPreview.getByText("api_score", { exact: true })).toBeVisible()
    await expect(secondPreview.getByRole("cell", { name: "0.25" }).first()).toBeVisible()

    const secondSave = await saveAndCapture(page)
    const secondSavedJoin = findEdgeJoin(secondSave.graph, "quotes")
    expectJoinTopology(secondSave.graph, secondSavedJoin, reloadedFirstJoin.id, "quotes", "enriched")
    expect(
      secondSave.graph.edges.find((edge) => edge.target === secondSavedJoin.id && edge.targetHandle === "join"),
      "API join role edge preserves the emitted frame handle",
    ).toMatchObject({ source: "quotes", sourceHandle: "api_lookup" })

    const finalGraph = await reloadAndCaptureGraph(page)
    const finalFirstJoin = findEdgeJoin(finalGraph, "lookup_rows")
    const finalSecondJoin = findEdgeJoin(finalGraph, "quotes")
    expectJoinTopology(finalGraph, finalFirstJoin, "raw_rows", "lookup_rows", finalSecondJoin.id)
    expectJoinTopology(finalGraph, finalSecondJoin, finalFirstJoin.id, "quotes", "enriched")
    const apiJoinRole = finalGraph.edges.find((edge) => edge.target === finalSecondJoin.id && edge.targetHandle === "join")
    expect(apiJoinRole, "reloaded API join role edge").toMatchObject({ source: "quotes", sourceHandle: "api_lookup" })

    await page.getByTestId("rf__node-enriched").click()
    const enrichedPreview = page.getByRole("table").first()
    await expect(enrichedPreview.getByText("value_doubled", { exact: true })).toBeVisible()
    const traceResponse = page.waitForResponse((response) => response.url().includes("/api/pipeline/trace"))
    await enrichedPreview.getByRole("cell", { name: "22" }).first().click()
    const response = await traceResponse
    expect(response.status(), "trace request succeeds").toBe(200)
    const tracePayload = await response.json() as {
      trace?: { steps?: Array<{ node_id?: string }> }
    }
    const tracedNodeIds = tracePayload.trace?.steps?.map((step) => step.node_id) ?? []
    expect(tracedNodeIds, "trace retains both Edge Join ancestors").toEqual(
      expect.arrayContaining([finalFirstJoin.id, finalSecondJoin.id]),
    )
    await expect(page.getByRole("complementary", { name: /node properties/i })).toContainText(/Trace:/)
    await expect(
      page.getByTestId("rf__node-enriched").getByLabel(/Polars node:.*trace active/i),
    ).toBeVisible()

    for (const join of [finalFirstJoin, finalSecondJoin]) {
      const tracedJoin = page.getByTestId(`rf__node-${join.id}`).getByLabel(/Edge Join node:/i)
      await expect(tracedJoin, `${join.id} remains visible in the trace path`).toBeVisible()
      await expect(tracedJoin, `${join.id} is not dimmed as unrelated`).toHaveCSS("opacity", "1")
    }

    for (const edge of [
      findEdge(finalGraph, finalFirstJoin.id, finalSecondJoin.id),
      findEdge(finalGraph, finalSecondJoin.id, "enriched"),
    ]) {
      const tracedPath = page
        .getByTestId(`rf__edge-${edge.id}`)
        .locator("path.react-flow__edge-path")
        .first()
      await expect(tracedPath, `${edge.id} is highlighted as part of the trace path`).toHaveAttribute(
        "style",
        /stroke-width:\s*2\.5/,
      )
    }
  })
})
