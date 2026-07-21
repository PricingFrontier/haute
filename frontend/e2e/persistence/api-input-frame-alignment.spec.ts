/**
 * Real-browser acceptance evidence for API-input frame identity.
 *
 * JSDOM can prove that a row contains its Handle, but only a browser can
 * prove their rendered centres coincide.  These tests therefore measure the
 * live boxes and separately pin the persisted edge identity and downstream
 * frame names that make the geometry meaningful to a user.
 */
import { readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "../projectIsolation"

const quotesConfigPath = resolve(
  e2eProjectRoot,
  "rating",
  "config",
  "quote_input",
  "quotes.json",
)
const pipelinePath = resolve(e2eProjectRoot, "rating", "main.py")

const API_NODE_ID = "quotes"
const API_NODE_LABEL = "quotes"
const DOWNSTREAM_NODE_ID = "enriched"
const MAX_CENTRE_DELTA_PX = 3
const LONG_FRAME_LABEL = "policyholders_with_exceptionally_long_frame_identity_2026"
const TRACE_VALUE = "trace-frame-value"

type GraphEdge = {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
}

type GraphEnvelope = {
  edges?: GraphEdge[]
  graph?: { edges?: GraphEdge[] }
}

type FrameIdentity = {
  label: string
  sourceHandle: string
}

function selectedColumn(index: number) {
  return {
    name: `value_${index}`,
    path: `$[:].value_${index}`,
    type: "str",
    status: "Inferred",
    selected: true,
    levels: null,
  }
}

function emittedTable(label: string, index: number) {
  return {
    path: index === 0 ? "$[:]" : `$[:].frame_${index}[:]`,
    label,
    displayPath: null,
    emit: true,
    row_id_column: null,
    columns: [selectedColumn(index)],
  }
}

function frameLabels(count: number): string[] {
  return Array.from({ length: count }, (_, index) => `frame_${index + 1}`)
}

function seedFrames(labels: readonly string[]): void {
  const config = {
    path: "data/quotes/sample_quote.json",
    contract: "opaque",
    tables: labels.map(emittedTable),
  }
  writeFileSync(quotesConfigPath, `${JSON.stringify(config, null, 2)}\n`, "utf8")
}

async function openCanvas(page: Page): Promise<void> {
  await page.goto("/")
  await expect(
    page.getByRole("toolbar", { name: /pipeline toolbar/i }),
  ).toBeVisible()
  await expect(page.getByTestId(`rf__node-${API_NODE_ID}`)).toBeVisible()
}

async function ensureFullDetail(page: Page, firstLabel: string): Promise<void> {
  const firstRow = page.getByTestId(`api-input-frame-row-${firstLabel}`)
  // Drive to the maximum zoom instead of stopping at the first transient row:
  // rendering the rows resizes the node and React Flow may re-fit while zooming.
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await page.getByRole("button", { name: "Zoom in" }).click()
    await page.waitForTimeout(50)
  }
  await expect(
    firstRow,
    `full-detail frame rows become visible for ${firstLabel}`,
  ).toBeVisible()
}

async function expectFrameRowsAligned(
  page: Page,
  labels: readonly string[],
): Promise<void> {
  const node = page.getByTestId(`node-${API_NODE_LABEL}`)
  await expect(node.getByTestId(/^api-input-frame-row-/)).toHaveCount(labels.length)

  for (const [index, label] of labels.entries()) {
    const row = node.getByTestId(`api-input-frame-row-${label}`)
    const name = row.getByTestId(`api-input-body-label-${label}`)
    const handle = row.getByTestId(
      `output-connector[${index}]:${API_NODE_LABEL}`,
    )

    await expect(name).toHaveText(label)
    await expect(name).toHaveAttribute("title", label)
    await expect(
      handle,
      `frame ${label} uses its argument name as the handle id`,
    ).toHaveAttribute("data-handleid", label)

    const [rowBox, handleBox] = await Promise.all([
      row.boundingBox(),
      handle.boundingBox(),
    ])
    expect(rowBox, `frame row ${label} has measurable geometry`).not.toBeNull()
    expect(handleBox, `source handle ${label} has measurable geometry`).not.toBeNull()
    if (rowBox === null || handleBox === null) {
      throw new Error(`Could not measure frame row/handle geometry for ${label}`)
    }

    const rowCentre = rowBox.y + rowBox.height / 2
    const handleCentre = handleBox.y + handleBox.height / 2
    expect(
      Math.abs(rowCentre - handleCentre),
      `${label} row and handle vertical centres`,
    ).toBeLessThanOrEqual(MAX_CENTRE_DELTA_PX)
  }
}

function previewResponse(nodeId: string, withWarning = false): string {
  return JSON.stringify({
    status: "ok",
    node_id: nodeId,
    row_count: 1,
    column_count: 1,
    columns: [{ name: "quote_id", dtype: "Utf8" }],
    available_columns: [{ name: "quote_id", dtype: "Utf8" }],
    preview: [{ quote_id: TRACE_VALUE }],
    preview_row_count: 1,
    preview_row_limit: 1,
    preview_truncated: false,
    error: null,
    timings: [],
    memory: [],
    schema_warnings: withWarning
      ? [{ column: "quote_id", status: "missing downstream" }]
      : [],
    node_statuses: { [nodeId]: "ok" },
  })
}

async function installPreviewRoute(page: Page, withWarning = false): Promise<void> {
  await page.route("**/api/pipeline/preview", async (route) => {
    const body = route.request().postDataJSON() as { node_id?: unknown }
    if (typeof body.node_id !== "string" || body.node_id.length === 0) {
      throw new Error(`Expected preview node_id, received ${String(body.node_id)}`)
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: previewResponse(body.node_id, withWarning),
    })
  })
}

async function installTraceRoute(page: Page): Promise<void> {
  await page.route("**/api/pipeline/trace", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        trace: {
          target_node_id: API_NODE_ID,
          row_index: 0,
          column: "quote_id",
          output_value: TRACE_VALUE,
          row_id_column: null,
          row_id_value: null,
          total_nodes_in_pipeline: 1,
          nodes_in_trace: 1,
          execution_ms: 1,
          waterfall: null,
          steps: [
            {
              node_id: API_NODE_ID,
              node_name: API_NODE_LABEL,
              node_type: "apiInput",
              schema_diff: {
                columns_added: ["quote_id"],
                columns_removed: [],
                columns_modified: [],
                columns_passed: [],
              },
              input_values: {},
              output_values: { quote_id: TRACE_VALUE },
              column_relevant: true,
              execution_ms: 1,
            },
          ],
        },
      }),
    })
  })
}

function frameEdges(envelope: GraphEnvelope): GraphEdge[] {
  const edges = envelope.graph?.edges ?? envelope.edges ?? []
  return edges.filter(
    (edge) => edge.source === API_NODE_ID && edge.target === DOWNSTREAM_NODE_ID,
  )
}

function sourceHandles(envelope: GraphEnvelope): (string | null | undefined)[] {
  return frameEdges(envelope)
    .map((edge) => edge.sourceHandle)
    .sort((left, right) => String(left).localeCompare(String(right)))
}

function expectGeneratedInputIdentity(
  labels: readonly string[],
  absentLabels: readonly string[] = [],
): void {
  const source = readFileSync(pipelinePath, "utf8")
  const signature = source.match(/def\s+enriched\s*\(([\s\S]*?)\)\s*->/)
  expect(signature, "generated main.py contains the enriched signature").not.toBeNull()
  if (signature === null) {
    throw new Error("Generated main.py has no enriched function signature")
  }

  const parameterNames = signature[1]
    .split(",")
    .map((parameter) => parameter.trim().split(":", 1)[0])
  expect(
    parameterNames,
    "generated arguments preserve edge-derived names one-to-one and in edge order",
  ).toEqual(["raw_rows", ...labels])
  const expectedDefinition = `def enriched(${["raw_rows", ...labels]
    .map((name) => `${name}: pl.LazyFrame`)
    .join(", ")}) -> pl.LazyFrame:`
  expect(source, "generated main.py exposes the exact executable signature").toContain(
    expectedDefinition,
  )

  for (const label of labels) {
    expect(source, `generated connect persists source_port ${label}`).toMatch(
      new RegExp(`source_port=["']${label}["']`),
    )
  }
  for (const absentLabel of absentLabels) {
    expect(source, `stale source_port ${absentLabel} is absent`).not.toMatch(
      new RegExp(`source_port=["']${absentLabel}["']`),
    )
  }
}

function namedFrameIdentities(labels: readonly string[]): FrameIdentity[] {
  return labels.map((label) => ({ label, sourceHandle: label }))
}

function renameFrameHandle(
  envelope: GraphEnvelope,
  oldHandle: string,
  newHandle: string,
): GraphEnvelope {
  const renamedEdges = (envelope.graph?.edges ?? envelope.edges ?? []).map((edge) =>
    edge.source === API_NODE_ID &&
    edge.target === DOWNSTREAM_NODE_ID &&
    edge.sourceHandle === oldHandle
      ? { ...edge, sourceHandle: newHandle }
      : edge,
  )
  return envelope.graph === undefined
    ? { ...envelope, edges: renamedEdges }
    : { ...envelope, graph: { ...envelope.graph, edges: renamedEdges } }
}

async function extendStarterGraphWithFrameEdges(
  page: Page,
  sessionToken: string | undefined,
  sourceHandles: readonly string[],
): Promise<void> {
  const saveStatus = await page.evaluate(
    async ({ token, handles, source, target }) => {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (token) headers["x-haute-session-token"] = token
      const graphResponse = await fetch("/api/pipeline", { headers })
      if (!graphResponse.ok) {
        throw new Error(`GET /api/pipeline ${graphResponse.status}`)
      }
      const graph = await graphResponse.json()
      graph.edges = graph.edges.filter(
        (edge: { source?: string; target?: string }) =>
          edge.source !== source || edge.target !== target,
      )
      graph.edges.push(
        ...handles.map((sourceHandle: string, index: number) => ({
          id: `e_${source}_${target}_frame_${index}`,
          source,
          target,
          sourceHandle,
          targetHandle: null,
        })),
      )

      const saveResponse = await fetch("/api/pipeline/save", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: graph.pipeline_name ?? "main",
          description: graph.pipeline_description ?? "",
          source_file: graph.source_file,
          preamble: graph.preamble ?? "",
          graph: { nodes: graph.nodes, edges: graph.edges },
        }),
      })
      if (!saveResponse.ok) {
        throw new Error(`POST /api/pipeline/save ${saveResponse.status}`)
      }
      return (await saveResponse.json()).status as string
    },
    {
      token: sessionToken,
      handles: sourceHandles,
      source: API_NODE_ID,
      target: DOWNSTREAM_NODE_ID,
    },
  )
  expect(saveStatus).toBe("saved")
}

async function reloadAndCaptureGraph(page: Page): Promise<GraphEnvelope> {
  const graphResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.reload()
  const response = await graphResponse
  expect(response.status(), "pipeline reload succeeds").toBe(200)
  await expect(
    page.getByRole("toolbar", { name: /pipeline toolbar/i }),
  ).toBeVisible()
  return (await response.json()) as GraphEnvelope
}

async function downstreamFrameChips(page: Page): Promise<Locator> {
  // Full-detail zoom keeps the apiInput centred and may place its distant
  // downstream node outside the viewport. Dispatching the same DOM click
  // selects that rendered node without changing the geometry under test.
  await page.getByTestId(`rf__node-${DOWNSTREAM_NODE_ID}`).dispatchEvent("click")
  const panel = page.getByTestId("node-panel")
  await expect(panel).toBeVisible()
  return panel.locator('[data-testid^="input-source-"]')
}

async function expectDownstreamFrameNames(
  page: Page,
  envelope: GraphEnvelope,
  frames: readonly FrameIdentity[],
  absentLabels: readonly string[] = [],
): Promise<void> {
  const chips = await downstreamFrameChips(page)
  const edges = frameEdges(envelope)
  expect(edges, "one persisted edge exists for every expected frame").toHaveLength(
    frames.length,
  )

  for (const frame of frames) {
    const edge = edges.find(
      (candidate) => (candidate.sourceHandle ?? null) === frame.sourceHandle,
    )
    expect(edge, `edge exists for frame ${frame.label}`).toBeDefined()
    if (edge === undefined) {
      throw new Error(`No edge found for frame ${frame.label}`)
    }
    await expect(page.getByTestId(`input-source-${edge.id}`)).toHaveText(frame.label)
  }

  for (const absentLabel of absentLabels) {
    await expect(
      chips.filter({ hasText: absentLabel }),
      `stale downstream frame name ${absentLabel} is removed`,
    ).toHaveCount(0)
  }
}

async function expectEdgesAttachedToFrameHandles(
  page: Page,
  envelope: GraphEnvelope,
  frames: readonly FrameIdentity[],
): Promise<void> {
  const edges = frameEdges(envelope)
  expect(edges, "one rendered edge exists for every expected frame").toHaveLength(
    frames.length,
  )

  for (const [index, frame] of frames.entries()) {
    const edge = edges.find(
      (candidate) => (candidate.sourceHandle ?? null) === frame.sourceHandle,
    )
    expect(edge, `edge exists for frame ${frame.label}`).toBeDefined()
    if (edge === undefined) {
      throw new Error(`No edge found for frame ${frame.label}`)
    }

    const edgeElement = page.getByTestId(`rf__edge-${edge.id}`)
    const edgePath = edgeElement.locator("path.react-flow__edge-path").first()
    const handle = page.getByTestId(
      `output-connector[${index}]:${API_NODE_LABEL}`,
    )
    await expect(edgeElement).toBeVisible()
    await expect(edgePath).toBeVisible()

    const [pathStart, handleBox] = await Promise.all([
      edgePath.evaluate((path: SVGPathElement) => {
        const start = path.getPointAtLength(0)
        const matrix = path.getScreenCTM()
        if (matrix === null) return null
        const screenStart = new DOMPoint(start.x, start.y).matrixTransform(matrix)
        return { x: screenStart.x, y: screenStart.y }
      }),
      handle.boundingBox(),
    ])
    expect(pathStart, `edge ${edge.id} has measurable browser geometry`).not.toBeNull()
    expect(handleBox, `handle ${frame.label} has measurable browser geometry`).not.toBeNull()
    if (pathStart === null || handleBox === null) {
      throw new Error(`Could not measure edge/handle attachment for ${frame.label}`)
    }

    expect(
      Math.abs(pathStart.x - (handleBox.x + handleBox.width / 2)),
      `${frame.label} edge begins at its handle's horizontal centre`,
    ).toBeLessThanOrEqual(MAX_CENTRE_DELTA_PX)
    expect(
      Math.abs(pathStart.y - (handleBox.y + handleBox.height / 2)),
      `${frame.label} edge begins at its handle's vertical centre`,
    ).toBeLessThanOrEqual(MAX_CENTRE_DELTA_PX)
  }
}

test.describe.configure({ mode: "serial" })

test.describe("apiInput frame-row alignment and identity", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  for (const count of [1, 2, 3, 8]) {
    test(`${count} emitted frame${count === 1 ? "" : "s"} align each row with its handle`, async ({
      page,
    }) => {
      const labels = frameLabels(count)
      seedFrames(labels)

      await openCanvas(page)
      await ensureFullDetail(page, labels[0])
      await expectFrameRowsAligned(page, labels)
    })
  }

  test("status and warning dots coexist without displacing the frame handles", async ({
    page,
  }) => {
    const labels = frameLabels(3)
    seedFrames(labels)
    await installPreviewRoute(page, true)
    await openCanvas(page)
    await ensureFullDetail(page, labels[0])

    const previewResponsePromise = page.waitForResponse("**/api/pipeline/preview")
    await page.getByTestId(`rf__node-${API_NODE_ID}`).click()
    await previewResponsePromise

    const node = page.getByTestId(`node-${API_NODE_LABEL}`)
    await expect(node.getByLabel("Node ok")).toBeVisible()
    await expect(node.getByLabel("Node has schema warnings")).toBeVisible()
    await expectFrameRowsAligned(page, labels)
  })

  test("a trace-active value pill coexists above still-aligned frame rows", async ({
    page,
  }) => {
    const labels = frameLabels(3)
    seedFrames(labels)
    await installPreviewRoute(page)
    await installTraceRoute(page)
    await openCanvas(page)
    await ensureFullDetail(page, labels[0])

    await page.getByTestId(`rf__node-${API_NODE_ID}`).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("quote_id", { exact: true })).toBeVisible()
    const traceResponsePromise = page.waitForResponse("**/api/pipeline/trace")
    await previewTable.getByRole("cell", { name: TRACE_VALUE }).click()
    await traceResponsePromise

    const node = page.getByTestId(`node-${API_NODE_LABEL}`)
    await expect(node).toHaveAttribute("aria-label", /trace active/)
    await expect(node.getByText(TRACE_VALUE, { exact: true })).toBeVisible()
    await expectFrameRowsAligned(page, labels)
  })

  test("a long raw frame label truncates with its tooltip while its handle remains aligned", async ({
    page,
  }) => {
    expect(LONG_FRAME_LABEL.length).toBeGreaterThanOrEqual(40)
    const labels = [LONG_FRAME_LABEL, "short_frame"]
    seedFrames(labels)

    await openCanvas(page)
    await ensureFullDetail(page, labels[0])
    const longName = page.getByTestId(`api-input-body-label-${LONG_FRAME_LABEL}`)
    await expect(longName).toHaveAttribute("title", LONG_FRAME_LABEL)
    expect(
      await longName.evaluate((element) => element.scrollWidth > element.clientWidth),
      "the long label is actually truncated in the rendered row",
    ).toBe(true)
    await expectFrameRowsAligned(page, labels)
  })

  test("frame-bound edges and downstream names survive save/reload and an in-place rename", async ({
    page,
  }) => {
    const originalLabels = ["quotes", "drivers"]
    const renamedLabels = ["policy_records", "drivers"]
    const originalFrames = namedFrameIdentities(originalLabels)
    const renamedFrames = namedFrameIdentities(renamedLabels)
    seedFrames(originalLabels)
    await installPreviewRoute(page)

    const firstApiRequest = page.waitForRequest((request) =>
      /\/api\/pipeline(?:\?|$)/.test(request.url()),
    )
    await openCanvas(page)
    const sessionToken = (await firstApiRequest).headers()["x-haute-session-token"]
    await extendStarterGraphWithFrameEdges(page, sessionToken, originalLabels)

    const initiallyReloadedGraph = await reloadAndCaptureGraph(page)
    expect(sourceHandles(initiallyReloadedGraph)).toEqual([...originalLabels].sort())
    expectGeneratedInputIdentity(originalLabels)
    await ensureFullDetail(page, originalLabels[0])
    await expectFrameRowsAligned(page, originalLabels)
    await expectEdgesAttachedToFrameHandles(
      page,
      initiallyReloadedGraph,
      originalFrames,
    )
    await expectDownstreamFrameNames(page, initiallyReloadedGraph, originalFrames)

    await page.getByTestId(`rf__node-${API_NODE_ID}`).click()
    const firstLabel = page.getByTestId("api-input-table-0-label")
    await firstLabel.fill(renamedLabels[0])
    await firstLabel.press("Tab")
    await expect(
      page.getByTestId(`api-input-frame-row-${renamedLabels[0]}`),
    ).toBeVisible()
    await expectFrameRowsAligned(page, renamedLabels)
    const renamedInMemoryGraph = renameFrameHandle(
      initiallyReloadedGraph,
      originalLabels[0],
      renamedLabels[0],
    )
    await expectEdgesAttachedToFrameHandles(page, renamedInMemoryGraph, renamedFrames)
    await expectDownstreamFrameNames(
      page,
      renamedInMemoryGraph,
      renamedFrames,
      [originalLabels[0]],
    )

    const saveRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" && request.url().includes("/api/pipeline/save"),
    )
    const saveResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().includes("/api/pipeline/save"),
    )
    await page.getByRole("button", { name: "Save", exact: true }).click()
    const [saveRequest, saveResponse] = await Promise.all([
      saveRequestPromise,
      saveResponsePromise,
    ])
    expect(saveResponse.status(), "pipeline save succeeds").toBe(200)
    const savedGraph = saveRequest.postDataJSON() as GraphEnvelope
    expect(sourceHandles(savedGraph)).toEqual([...renamedLabels].sort())
    expectGeneratedInputIdentity(renamedLabels, [originalLabels[0]])

    const graphAfterRenameReload = await reloadAndCaptureGraph(page)
    expect(sourceHandles(graphAfterRenameReload)).toEqual([...renamedLabels].sort())
    await ensureFullDetail(page, renamedLabels[0])
    await expectFrameRowsAligned(page, renamedLabels)
    await expectEdgesAttachedToFrameHandles(
      page,
      graphAfterRenameReload,
      renamedFrames,
    )
    await expectDownstreamFrameNames(
      page,
      graphAfterRenameReload,
      renamedFrames,
      [originalLabels[0]],
    )
  })

  test("a sole frame keeps its labelled edge, exact argument name, and generated signature across save/reload", async ({
    page,
  }) => {
    const labels = ["single_frame"]
    const frames = namedFrameIdentities(labels)
    seedFrames(labels)
    await installPreviewRoute(page)

    const firstApiRequest = page.waitForRequest((request) =>
      /\/api\/pipeline(?:\?|$)/.test(request.url()),
    )
    await openCanvas(page)
    const sessionToken = (await firstApiRequest).headers()["x-haute-session-token"]
    await extendStarterGraphWithFrameEdges(page, sessionToken, labels)

    const reloadedGraph = await reloadAndCaptureGraph(page)
    expect(sourceHandles(reloadedGraph)).toEqual(labels)
    expectGeneratedInputIdentity(labels, [API_NODE_LABEL])
    await ensureFullDetail(page, labels[0])
    await expectFrameRowsAligned(page, labels)
    await expectEdgesAttachedToFrameHandles(page, reloadedGraph, frames)
    await expectDownstreamFrameNames(page, reloadedGraph, frames, [API_NODE_LABEL])
  })
})
