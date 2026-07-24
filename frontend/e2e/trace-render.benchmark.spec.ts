import { expect, test, type Locator, type Page } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

const TRACE_COLUMN = "value_doubled"
const TRACE_VALUE = 22
const ITERATIONS = 6
const RENDER_P95_BUDGET_MS = 500

type TraceShape = "linear" | "join-multi-frame"

function previewResponse(nodeId: string): string {
  return JSON.stringify({
    status: "ok",
    node_id: nodeId,
    row_count: 1,
    column_count: 2,
    columns: [
      { name: "value", dtype: "Int64" },
      { name: TRACE_COLUMN, dtype: "Int64" },
    ],
    available_columns: [
      { name: "value", dtype: "Int64" },
      { name: TRACE_COLUMN, dtype: "Int64" },
    ],
    preview: [{ value: 11, [TRACE_COLUMN]: TRACE_VALUE }],
    preview_row_count: 1,
    preview_row_limit: 1,
    preview_truncated: false,
    error: null,
    timings: [],
    memory: [],
    schema_warnings: [],
    node_statuses: { [nodeId]: "ok" },
  })
}

function pipelineResponse(): string {
  return JSON.stringify({
    pipeline_name: "trace_render_benchmark",
    pipeline_description: "",
    source_file: "rating/main.py",
    preamble: "",
    submodels: {},
    sources: ["live"],
    active_source: "live",
    nodes: [
      {
        id: "raw_rows",
        type: "dataInput",
        position: { x: 0, y: 0 },
        data: {
          label: "raw_rows",
          description: "",
          nodeType: "dataInput",
          config: {},
        },
      },
      {
        id: "enriched",
        type: "polars",
        position: { x: 280, y: 0 },
        data: {
          label: "enriched",
          description: "",
          nodeType: "polars",
          config: { code: 'df = df.with_columns(value_doubled=pl.col("value") * 2)' },
        },
      },
    ],
    edges: [
      {
        id: "raw-to-enriched",
        source: "raw_rows",
        target: "enriched",
      },
    ],
  })
}

function traceRow(stepIndex: number): Record<string, number | string> {
  const row: Record<string, number | string> = {
    policy_id: `P${stepIndex.toString().padStart(5, "0")}`,
    [TRACE_COLUMN]: TRACE_VALUE + stepIndex / 10,
  }
  for (let index = 0; index < 30; index += 1) {
    row[`feature_${index.toString().padStart(2, "0")}`] =
      (stepIndex + 1) * (index + 1) / 7
  }
  return row
}

function traceStep(shape: TraceShape, index: number, count: number) {
  const isFirst = index === 0
  const isTarget = index === count - 1
  const nodeId = isTarget ? "enriched" : `${shape}-step-${index}`
  const nodeType = shape === "join-multi-frame" && isFirst
    ? "apiInput"
    : shape === "join-multi-frame" && index === Math.floor(count / 2)
      ? "edgeJoin"
      : "polars"
  const output = traceRow(index)
  if (isTarget) output[TRACE_COLUMN] = TRACE_VALUE

  return {
    node_id: nodeId,
    node_name: isTarget ? "enriched" : `Stage ${index + 1}`,
    node_type: nodeType,
    schema_diff: {
      columns_added: isFirst ? [TRACE_COLUMN] : [],
      columns_removed: [],
      columns_modified: isFirst ? [] : [TRACE_COLUMN],
      columns_passed: [],
    },
    input_values: isFirst ? {} : traceRow(index - 1),
    output_values: output,
    topological_rank: index,
    column_relevant: true,
    expression: null,
    calculation: null,
    node_detail: null,
    row_lineage_type: nodeType === "edgeJoin" ? "joined" : "passthrough",
  }
}

function traceResponse(shape: TraceShape): string {
  const stepCount = shape === "linear" ? 24 : 16
  const steps = Array.from(
    { length: stepCount },
    (_, index) => traceStep(shape, index, stepCount),
  )
  return JSON.stringify({
    status: "ok",
    trace: {
      target_node_id: "enriched",
      row_index: 0,
      column: TRACE_COLUMN,
      output_value: TRACE_VALUE,
      steps,
      omissions: [],
      row_id_column: "policy_id",
      row_id_value: "P00000",
      total_nodes_in_pipeline: stepCount,
      nodes_in_trace: stepCount,
      execution_ms: 1,
      waterfall: null,
      correlation_diagnostics: [],
      generated_at: "2026-07-23T12:00:00+00:00",
      pipeline_source: "rating/main.py",
      execution_origin: "trace_cache",
    },
  })
}

async function installTraceBenchmarkRoutes(
  page: Page,
): Promise<{ setShape: (shape: TraceShape) => void }> {
  let shape: TraceShape = "linear"

  await page.route(/\/api\/pipeline(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: pipelineResponse(),
    })
  })
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    })
  })
  await page.route("**/api/pipeline/preview", async (route) => {
    const body = route.request().postDataJSON() as { node_id?: unknown }
    if (typeof body.node_id !== "string") {
      throw new Error(`Trace benchmark expected node_id, received ${String(body.node_id)}`)
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: previewResponse(body.node_id),
    })
  })

  await page.route("**/api/pipeline/trace", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: traceResponse(shape),
    })
  })

  return {
    setShape(nextShape: TraceShape) {
      shape = nextShape
    },
  }
}

async function measureTraceRender(
  cell: Locator,
  targetStepId: string,
): Promise<number> {
  return cell.evaluate(
    (element, expectedStepId) =>
      new Promise<number>((resolve, reject) => {
        const startedAt = performance.now()
        const timeout = window.setTimeout(() => {
          observer.disconnect()
          reject(new Error(`Trace panel did not render ${expectedStepId}`))
        }, 10_000)
        const finish = () => {
          window.clearTimeout(timeout)
          observer.disconnect()
          requestAnimationFrame(() => {
            requestAnimationFrame(() => resolve(performance.now() - startedAt))
          })
        }
        const observer = new MutationObserver(() => {
          if (
            document.querySelector(
              `[data-testid="trace-step-card-${expectedStepId}"]`,
            )
          ) {
            finish()
          }
        })
        observer.observe(document.body, { childList: true, subtree: true })
        element.dispatchEvent(
          new MouseEvent("click", {
            bubbles: true,
            cancelable: true,
            composed: true,
          }),
        )
      }),
    targetStepId,
  )
}

function percentile(values: readonly number[], percentileValue: number): number {
  const sorted = [...values].sort((left, right) => left - right)
  const index = Math.min(
    sorted.length - 1,
    Math.ceil((percentileValue / 100) * sorted.length) - 1,
  )
  return sorted[index] ?? 0
}

test.describe("trace panel render benchmark", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@benchmark keeps ordinary linear and join/multi-frame traces visually immediate", async ({
    page,
  }) => {
    const routes = await installTraceBenchmarkRoutes(page)
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    const enrichedNode = page.getByRole("button", { name: /enriched/i })
    await enrichedNode.click()
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    const traceCell = previewTable.getByRole("cell", { name: String(TRACE_VALUE) }).first()
    await expect(traceCell).toBeVisible()

    const evidence: Record<TraceShape, { samplesMs: number[]; p95Ms: number }> = {
      linear: { samplesMs: [], p95Ms: 0 },
      "join-multi-frame": { samplesMs: [], p95Ms: 0 },
    }

    for (const shape of ["linear", "join-multi-frame"] as const) {
      routes.setShape(shape)
      const samples: number[] = []
      for (let iteration = 0; iteration < ITERATIONS; iteration += 1) {
        const elapsed = await measureTraceRender(traceCell, "enriched")
        await expect(page.getByTestId("trace-panel")).toBeVisible()
        await expect(page.getByTestId("trace-state-panel")).toHaveCount(0)
        samples.push(elapsed)
        await page.getByRole("button", { name: "Close trace" }).click()
        await expect(page.getByTestId("trace-panel")).toHaveCount(0)
      }
      const steadyStateSamples = samples.slice(1)
      evidence[shape] = {
        samplesMs: steadyStateSamples.map((value) => Number(value.toFixed(3))),
        p95Ms: Number(percentile(steadyStateSamples, 95).toFixed(3)),
      }
    }

    const summary = JSON.stringify(
      {
        iterations: ITERATIONS,
        discardedWarmupSamples: 1,
        budgetMs: RENDER_P95_BUDGET_MS,
        evidence,
      },
      null,
      2,
    )
    await test.info().attach("trace-render-metrics.json", {
      body: summary,
      contentType: "application/json",
    })
    console.info(`trace render benchmark metrics:\n${summary}`)

    expect(evidence.linear.p95Ms, summary).toBeLessThanOrEqual(RENDER_P95_BUDGET_MS)
    expect(evidence["join-multi-frame"].p95Ms, summary).toBeLessThanOrEqual(
      RENDER_P95_BUDGET_MS,
    )
  })
})
