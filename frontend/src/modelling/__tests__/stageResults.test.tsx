import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import type { TrainAvePerFeatureRow } from "../../api/types"
import { makeTrainResult } from "../../test-utils/factories"
import { stageResult } from "../stageResults"
import { PruningPath } from "../PruningPath"
import { StageVisibility } from "../StageVisibility"
import { useStagedViews } from "../useStagedTraining"
import useDocumentStatusStore, { documentExecutionKey } from "../../stores/useDocumentStatusStore"
import { GraphProvider } from "../../panels/GraphContext"

vi.mock("../../panels/modelling/ChartScaffold", async (original) => ({
  ...await original<typeof import("../../panels/modelling/ChartScaffold")>(),
  ResponsiveChart: ({ children }: { children: (width: number) => ReactNode }) => children(640),
}))

function result() {
  const value = makeTrainResult()
  const snapshot = {
    metrics: { rmse: .2 }, feature_importance: [], features: ["age"], cat_features: [],
    diagnostics_set: "validation", ave_per_feature: [], ave_left_out: [], tboost_tables: null,
    loss_history: [], fit_evidence: null, diagnostics_errors: [], revision: "a".repeat(64), recipe: [],
  }
  value.staged = {
    schema_version: 1, selected_stage: "pruned", diagnostics_basis: "partition", reference_fit: "validation-0", evaluation_identity: "b".repeat(64),
    fit_config_digest: "c".repeat(64), configuration: {},
    stages: { full: { ...snapshot, metrics: { rmse: .3 } }, pruned: snapshot },
    pruning_path: { table_counts: [1, 2, 3], oob_deviance: [.5, .3, .4], minimum_step: 1, oob_rows: 600 },
    path_unavailable_reason: null, recommendation: { path: { best: 1 } },
  }
  return value
}

it("renders compact cell counts and preserves per-stage shape comparisons without dense tables", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const { comparison } = await import("../stageEvidence")
  const value = result()
  value.diagnostics_set = "validation"
  const before = { tables: [{ term: "age:region", features: ["age", "region"], order: 2, cells: 16, shape_digest: "a".repeat(64) }], factored: 1 }
  const after = { tables: [{ ...before.tables[0], cells: 4, shape_digest: "b".repeat(64) }], factored: 1 }
  value.table_summary = after
  value.staged!.stages.full.table_summary = before
  value.staged!.stages.pruned.table_summary = after
  render(<PathCandidateSummary stage="banded" parent="full" candidate={value} />)
  expect(screen.getByRole("img", { name: "Interaction table cell counts" })).toBeVisible()
  expect(screen.getByText(/16 → 4 cells/)).toBeVisible()
  expect(comparison(stageResult(value, "full")!, stageResult(value, "pruned")!)).toMatchObject({
    cells: [16, 4], tables: [1, 1], changedTables: 1, factors: ["age", "region"],
  })
})

it("compares a legacy full response with its compact snapshot using the same immutable shape evidence", async () => {
  const { comparison } = await import("../stageEvidence")
  const before = result()
  before.staged!.selected_stage = "full"
  before.diagnostics_set = "validation"
  before.tboost_tables = { tables: [{ term: "age", features: ["age"], order: 1, axes: [], scores: [1, 2] }], factored: [] } as unknown as NonNullable<typeof before.tboost_tables>
  const summary = { tables: [{ term: "age", features: ["age"], order: 1, cells: 2, shape_digest: "f".repeat(64) }], factored: 0 }
  const after = structuredClone(before)
  after.tboost_tables = null
  after.table_summary = summary
  after.staged!.stages.full.table_summary = summary
  expect(comparison(before, after)).toMatchObject({ cells: [2, 2], changedTables: 0, factors: [] })
  delete after.staged!.stages.full.table_summary
  expect(comparison(before, after)).toMatchObject({ cells: [2, 2], changedTables: null, factors: [] })
})

it("borrows the Full snapshot summary when projecting an ancestor of a legacy Pruned result", async () => {
  const { comparison } = await import("../stageEvidence")
  const legacy = result()
  const fullTable = { link: "identity", base_value: 0, tables: [{ term: "age", features: ["age"], order: 1, importance: 1, axes: [{ feature: "age", type: "nominal", labels: ["a", "b"] }], scores: [1, 2], relativities: null, support: [1, 1] }], factored: [] }
  legacy.staged!.stages.full.tboost_tables = fullTable
  legacy.staged!.stages.pruned.tboost_tables = { ...fullTable, tables: [{ ...fullTable.tables[0], axes: [{ feature: "age", type: "nominal", labels: ["a"] }], scores: [1], support: [2] }] }
  const compact = structuredClone(legacy)
  compact.staged!.stages.full.table_summary = { tables: [{ term: "age", features: ["age"], order: 1, cells: 2, shape_digest: "f".repeat(64) }], factored: 0 }
  compact.staged!.stages.pruned.table_summary = { tables: [{ term: "age", features: ["age"], order: 1, cells: 1, shape_digest: "e".repeat(64) }], factored: 0 }
  expect(comparison(stageResult(legacy, "full")!, stageResult(compact, "full")!)).toMatchObject({ cells: [2, 2], changedTables: 0 })
})

describe("Saved stage views", () => {
  afterEach(() => { cleanup(); useStagedViews.setState({ models: {}, preferences: {} }) })
  it("reads each snapshot's metrics without changing the applied response", () => {
    const saved = result()
    const before = JSON.stringify(saved)
    expect(stageResult(saved, "full")?.diagnostic_metrics).toEqual({ rmse: .3 })
    expect(stageResult(saved, "pruned")?.diagnostic_metrics).toEqual({ rmse: .2 })
    expect(stageResult(saved, "banded")).toBeNull()
    expect(stageResult(saved, "pruned")).toBe(stageResult(saved, "pruned"))
    expect(JSON.stringify(saved)).toBe(before)
  })
  it("selects a path budget by keyboard without applying or training it", () => {
    const select = vi.fn()
    render(<PruningPath result={result()} selected={3} applied={1} onSelect={select} readOnly={false} />)
    for (const label of ["OOB minimum", "Recommended", "Selected", "Applied"]) expect(screen.getByText(label)).toBeVisible()
    fireEvent.keyDown(screen.getByRole("button", { name: /2 tables, OOB/ }), { key: "Enter" })
    expect(select).toHaveBeenCalledWith(2)
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument()
  })
  it("defaults to full and applied predictions and keeps unavailable stages disabled", () => {
    const key = JSON.stringify([documentExecutionKey(useDocumentStatusStore.getState()), "node#model:m1"])
    const saved = result()
    useStagedViews.getState().update(key, { state: { schema_version: 1, next_preview: null, checkpoints: [], current: {
      revision: "a".repeat(32), build_id: "b".repeat(32), parent_revision: null, size: 1,
      sha256: "c".repeat(64), config_digest: "d".repeat(64),
      result: { graph_digest: "e".repeat(64), response: saved },
    }, preview: null, final: null, job: null, stale_reason: null } })
    render(<GraphProvider allNodes={[]} edges={[]} requestScope={null}><StageVisibility nodeId="node" modelId="m1" /></GraphProvider>)
    expect(screen.getByRole("checkbox", { name: "Show Full GBM predictions" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Show Pruned predictions" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Show Banded predictions" })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox", { name: "Show Full GBM predictions" }))
    expect(useStagedViews.getState().models[key].visible).toEqual(["pruned"])
    expect(useStagedViews.getState().models[key].state?.current?.result.response).toBe(saved)
  })
})

it("compares only matching saved evaluation identities and measures changed cells", async () => {
  const { comparison, evidenceLabel, appliedChain } = await import("../stageEvidence")
  const saved = result()
  const before = stageResult(saved, "full")!
  const after = stageResult(saved, "pruned")!
  const table = { term: "age × region", features: ["age", "region"], order: 2, importance: 1,
    axes: [], scores: [[1, 2], [3, 4]], relativities: null, support: [[1, 1], [1, 1]] }
  before.tboost_tables = { link: "identity", base_value: 0, tables: [table], factored: [] }
  after.tboost_tables = { ...before.tboost_tables, tables: [{ ...table, scores: [[1], [2]] }] }
  expect(comparison(before, after)).toMatchObject({ metrics: ["rmse"], cells: [4, 2], changedTables: 1, factors: ["age", "region"] })
  expect(evidenceLabel(after)).toBe("Validation")
  expect(comparison(before, { ...after, staged: { ...after.staged!, evaluation_identity: "d".repeat(64) } })).toBeNull()
  expect(comparison(before, { ...after, diagnostics_set: "development" })).toBeNull()
  expect(evidenceLabel({ ...after, staged: { ...after.staged!, diagnostics_basis: "out_of_fold" } })).toBe("OOF validation")
  expect(appliedChain({ ...saved, staged: { ...saved.staged!, stages: { ...saved.staged!.stages,
    pruned: { ...saved.staged!.stages.pruned, recipe: [{ stage: "pruned" }] } } } })).toBe("Full GBM → Pruned")
})

it("keeps ordinary stage visibility through comparison and ordinary polls", () => {
  const key = JSON.stringify([documentExecutionKey(useDocumentStatusStore.getState()), "node#model:m1"])
  const update = useStagedViews.getState().update
  update(key, { visible: ["smoothed"], comparison: { stage: "pruned", parent: "full" } })
  expect(useStagedViews.getState().models[key].visible).toEqual(["smoothed"])
  update(key, { comparison: undefined })
  expect(useStagedViews.getState().models[key].visible).toEqual(["smoothed"])
  update(key, { comparison: { stage: "pruned", parent: "full" } })
  update(key, { state: { schema_version: 1, next_preview: null, checkpoints: [], current: null, preview: null, final: null, job: null, stale_reason: null } })
  // No revision changed; a normal poll must not clear the user's comparison.
  expect(useStagedViews.getState().models[key].comparison).toBeDefined()
})

it("inspects smoothing points by keyboard and exposes rejection without applying", async () => {
  const { PathChart } = await import("../ModelPaths")
  const select = vi.fn()
  const path = {
    id: "a".repeat(64), stage: "smoothed", parent: "banded", selected_point: "point-1",
    suggested_point: "unchanged", evidence: "validation", metric: "rmse", controls: { alpha: null },
    selection: { relative_tolerance: .001, absolute_tolerance: 1e-10 },
    reference_fit: "validation-0", parent_revisions: {}, family_ids: {}, evaluation_identity: "b".repeat(64),
    points: [
      { id: "unchanged", identity: true, status: "identity", diagnostics: {}, loss_delta: 0 },
      { id: "point-1", identity: false, status: "unchanged", diagnostics: { strength: .5 }, loss_delta: 0 },
      { id: "point-2", identity: false, status: "rejected", diagnostics: { strength: 1 }, loss_delta: .01 },
    ].map((point) => ({ ...point, stage: "smoothed", reference_status: point.status,
      revision: "c".repeat(64), settings: {}, loss: .1 + point.loss_delta, relative_loss_delta: null, fold_status: [] })),
  } as import("../../generated/api-contracts.generated").StagePathPayload
  render(<PathChart path={path} selected="point-1" applied="unchanged" onSelect={select} />)
  expect(screen.getByRole("button", { name: /0.5 × Auto.*unchanged/ })).toBeVisible()
  fireEvent.keyDown(screen.getByRole("button", { name: /1 × Auto.*rejected/ }), { key: "Enter" })
  expect(select).toHaveBeenCalledWith("point-2")
  expect(screen.queryByRole("button", { name: /apply/i })).not.toBeInTheDocument()
  cleanup()
})

it("plots equal-complexity banding points by budget and puts loss in tolerance context", async () => {
  const { PathChart } = await import("../ModelPaths")
  const path = {
    stage: "banded", selected_point: "point-1", suggested_point: "point-1", controls: {},
    selection: { relative_tolerance: .001, absolute_tolerance: 1e-10 },
    points: [
      { id: "unchanged", identity: true, loss: 2, loss_delta: 0, diagnostics: { factored_interactions: 1 } },
      { id: "point-1", identity: false, loss: 1.99, loss_delta: -.01, diagnostics: { budget_fraction: .25, interaction_cells: 6 } },
      { id: "point-2", identity: false, loss: 2.02, loss_delta: .02, diagnostics: { budget_fraction: 1, interaction_cells: 6 } },
    ].map((p) => ({ ...p, status: p.identity ? "identity" : "changed" })),
  } as unknown as import("../../generated/api-contracts.generated").StagePathPayload
  const { rerender } = render(<PathChart path={path} onSelect={vi.fn()} />)
  const unchanged = screen.getByRole("button", { name: /Keep unchanged/ })
  const quarter = screen.getByRole("button", { name: /25% of budget, 0.5% lower loss/ })
  const maximum = screen.getByRole("button", { name: /100% of budget, 1% higher loss/ })
  expect(Number(unchanged.getAttribute("cx"))).toBeLessThan(Number(quarter.getAttribute("cx")))
  expect(Number(quarter.getAttribute("cx"))).toBeLessThan(Number(maximum.getAttribute("cx")))
  expect(screen.getByText("Banding budget (% of maximum)")).toBeVisible()
  expect(screen.getByText("Loss change (%)")).toBeVisible()
  expect(screen.getByText(/Selection ceiling: -0.4%/)).toBeVisible()
  rerender(<PathChart path={{ ...path, points: path.points.map((p, i) => ({ ...p, loss: i * .01, loss_delta: i * .01 })) }} onSelect={vi.fn()} />)
  expect(screen.getByText("Loss change (absolute)")).toBeVisible()
  expect(screen.getByRole("img").innerHTML).not.toMatch(/NaN|Infinity/)
  cleanup()
})

function compactCandidate() {
  const value = result()
  value.diagnostics_set = "validation"
  const numeric = { feature: "age", type: "numeric", bins: [
    { label: "Young", exposure: 10, avg_actual: 2, avg_predicted: 1 },
    { label: "Older", exposure: 20, avg_actual: 3, avg_predicted: 4 },
    { label: "Missing", exposure: 1, avg_actual: 2, avg_predicted: 2 },
  ] }
  value.features = ["age"]
  value.ave_per_feature = [{ ...numeric, bins: numeric.bins.map((b, i) => ({ ...b, avg_predicted: i === 0 ? 2 : i === 1 ? 3 : 2 })) }]
  value.staged!.stages.full = { ...value.staged!.stages.full!, features: ["age"], ave_per_feature: [numeric] }
  return value
}

it("summarises selected banding cells against its parent, omitting main and factored grids", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const value = compactCandidate()
  const table = { term: "age × region", features: ["age", "region"], order: 2, importance: 1,
    axes: ["age", "region"].map((feature) => ({ feature, type: "nominal" as const, labels: ["a", "b", "c", "d"] })),
    scores: [[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]], relativities: null,
    support: Array.from({ length: 4 }, () => [1, 1, 1, 1]) }
  const main = { ...table, term: "age", features: ["age"], order: 1, axes: [table.axes[0]], scores: [1, 2, 3, 4], support: [1, 1, 1, 1] }
  const before = { link: "identity", base_value: 0, tables: [table, main], factored: [{ term: "big", features: ["a", "b"], importance: 1 }] }
  value.staged!.stages.full!.tboost_tables = before
  value.tboost_tables = { ...before, link: "identity", tables: [{ ...table, axes: table.axes.map((axis) => ({ ...axis, labels: ["a", "b"] })), scores: [[1, 2], [3, 4]], support: [[1, 1], [1, 1]] }, main] }
  const { rerender } = render(<PathCandidateSummary stage="banded" parent="full" candidate={value} />)
  expect(screen.getByText("16 → 4 cells · 75% fewer")).toBeVisible()
  expect(screen.getByRole("img", { name: "Interaction table cell counts" })).toBeVisible()
  expect(screen.getByLabelText("1–4 cells: before 0 tables, selected 1 tables")).toBeInTheDocument()
  expect(screen.getByLabelText("5–16 cells: before 1 tables, selected 0 tables")).toBeInTheDocument()
  expect(screen.getByText(/Factored effects excluded: 1 before, 1 selected/)).toBeVisible()
  rerender(<PathCandidateSummary stage="banded" parent="full" candidate={{ ...value, tboost_tables: null }} />)
  expect(screen.getByText("Table diagnostics unavailable for this comparison.")).toBeVisible()
  cleanup()
})

it("shows paired smoothing averages with separate missing points and no categorical lines", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const value = compactCandidate()
  const { rerender } = render(<PathCandidateSummary stage="smoothed" parent="full" candidate={value} />)
  const chart = screen.getByRole("img", { name: "Average prediction for age" })
  expect(chart.querySelector('[data-series="before"]')?.getAttribute("d")?.match(/L/g)).toHaveLength(1)
  expect(chart.querySelector('[data-series="selected"]')?.getAttribute("d")).not.toBe(chart.querySelector('[data-series="before"]')?.getAttribute("d"))
  const coordinates = (series: string) => [...chart.querySelector(`[data-series="${series}"]`)!.getAttribute("d")!.matchAll(/[ML]([^,]+),([^ ML]+)/g)]
    .map((match) => [Number(match[1]), Number(match[2])])
  const oldLine = coordinates("before"), newLine = coordinates("selected")
  // Values 1/4 before and 2/3 selected share the padded 0.76..4.24 scale.
  expect(oldLine[0][1]).toBeCloseTo(92.20689655)
  expect(oldLine[1][1]).toBeCloseTo(19.79310345)
  expect(newLine[0][1]).toBeCloseTo(68.06896552)
  expect(newLine[1][1]).toBeCloseTo(43.93103448)
  expect(oldLine.map(([x]) => x)).toEqual(newLine.map(([x]) => x))
  const marks = chart.querySelectorAll("circle")
  // Missing has value 2 in both series, aligning with the selected first bin.
  expect(Number(marks[4].getAttribute("cy"))).toBeCloseTo(newLine[0][1])
  expect(marks[4].getAttribute("cy")).toBe(marks[5].getAttribute("cy"))
  expect(screen.getByText(/Grouped predictions, not PDPs/)).toBeVisible()
  expect(screen.getByText(/Young: before 1, selected 2/)).toBeInTheDocument()
  const categorical = structuredClone(value)
  categorical.ave_per_feature[0].type = "categorical"
  const categoricalBefore = categorical.staged!.stages.full!.ave_per_feature as TrainAvePerFeatureRow[]
  categoricalBefore[0].type = "categorical"
  rerender(<PathCandidateSummary stage="smoothed" parent="full" candidate={categorical} />)
  expect(screen.getByRole("img").querySelector("path")).toBeNull()
  const mismatch = structuredClone(value)
  mismatch.ave_per_feature[0].bins[0].exposure = 100
  rerender(<PathCandidateSummary stage="smoothed" parent="full" candidate={mismatch} />)
  expect(screen.queryByRole("img")).not.toBeInTheDocument()
  expect(screen.getByText(/age.*bins differ/)).toBeVisible()
  cleanup()
})

it("keeps smoothing summaries bounded while making every fitted factor reachable", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const value = compactCandidate()
  value.features = Array.from({ length: 8 }, (_, i) => `factor${i}`)
  value.ave_per_feature = value.features.map((feature) => ({ ...value.ave_per_feature[0], feature }))
  value.staged!.stages.full!.features = value.features
  const beforeFactors = value.staged!.stages.full!.ave_per_feature as TrainAvePerFeatureRow[]
  value.staged!.stages.full!.ave_per_feature = value.features.map((feature) => ({ ...beforeFactors[0], feature }))
  render(<PathCandidateSummary stage="smoothed" parent="full" candidate={value} />)
  expect(screen.getAllByRole("img")).toHaveLength(6)
  fireEvent.click(screen.getByRole("button", { name: "Next factors" }))
  expect(screen.getAllByRole("img")).toHaveLength(2)
  expect(screen.getByRole("img", { name: "Average prediction for factor7" })).toBeVisible()
  expect(screen.getByRole("button", { name: "Next factors" })).toBeDisabled()
  cleanup()
})

it("explains empty and unavailable summaries without inventing a comparison", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const value = compactCandidate()
  const empty = { link: "identity" as const, base_value: 0, tables: [], factored: [] }
  value.tboost_tables = empty
  value.staged!.stages.full!.tboost_tables = empty
  const { rerender } = render(<PathCandidateSummary stage="banded" parent="full" candidate={value} />)
  expect(screen.getByText("No materialised interaction tables to compare.")).toBeVisible()
  expect(screen.queryByRole("img")).not.toBeInTheDocument()
  rerender(<PathCandidateSummary stage="smoothed" parent="full" candidate={{ ...value, diagnostics_set: "development" }} />)
  expect(screen.getByText("Before and selected diagnostics use different evaluation rows.")).toBeVisible()
  const missing = structuredClone(value)
  missing.ave_per_feature = []
  rerender(<PathCandidateSummary stage="smoothed" parent="full" candidate={missing} />)
  expect(screen.getByText("age · saved averages unavailable")).toBeVisible()
  delete missing.staged!.stages.full
  rerender(<PathCandidateSummary stage="smoothed" parent="full" candidate={missing} />)
  expect(screen.getByText("Predecessor diagnostics unavailable for this comparison.")).toBeVisible()
  cleanup()
})

it("keeps small smoothing changes distinguishable in axis labels and point details", async () => {
  const { default: PathCandidateSummary } = await import("../PathCandidateSummary")
  const value = compactCandidate()
  value.ave_per_feature[0].bins = value.ave_per_feature[0].bins.map((bin) => ({ ...bin, avg_predicted: 1.000001 }))
  const before = value.staged!.stages.full!.ave_per_feature as TrainAvePerFeatureRow[]
  before[0].bins = before[0].bins.map((bin) => ({ ...bin, avg_predicted: 1 }))
  render(<PathCandidateSummary stage="smoothed" parent="full" candidate={value} />)
  const labels = screen.getByRole("img").querySelectorAll("text")
  expect(labels[0].textContent).not.toBe(labels[1].textContent)
  expect(screen.getByText("Young: before 1.000000, selected 1.000001")).toBeInTheDocument()
  cleanup()
})

it("clears summaries during selection reads and resets factor paging without applying", async () => {
  const hooks = await import("../useStagedTraining")
  const { default: ModelPaths } = await import("../ModelPaths")
  const candidate = compactCandidate()
  const beforeRows = candidate.staged!.stages.full!.ave_per_feature as TrainAvePerFeatureRow[]
  candidate.features = Array.from({ length: 8 }, (_, i) => `factor${i}`)
  candidate.ave_per_feature = candidate.features.map((feature) => ({ ...candidate.ave_per_feature[0], feature }))
  candidate.staged!.stages.full!.features = candidate.features
  candidate.staged!.stages.full!.ave_per_feature = candidate.features.map((feature) => ({ ...beforeRows[0], feature }))
  const path = {
    id: "a".repeat(64), stage: "smoothed", parent: "full", selected_point: "point-1", suggested_point: "point-1",
    evidence: "validation", metric: "rmse", controls: { alpha: null }, selection: { relative_tolerance: .001, absolute_tolerance: 1e-10 },
    points: [
      { id: "unchanged", identity: true, loss: 1, loss_delta: 0, diagnostics: {} },
      { id: "point-1", identity: false, loss: .99, loss_delta: -.01, diagnostics: { strength: .5 } },
      { id: "point-2", identity: false, loss: .98, loss_delta: -.02, diagnostics: { strength: 1 } },
    ].map((point) => ({ ...point, revision: point.id, status: "changed", settings: {} })),
  } as unknown as import("../../generated/api-contracts.generated").StagePathPayload
  const applied = structuredClone(candidate)
  applied.staged!.paths = { smoothed: path }
  const state = { current: { revision: "r", result: { response: applied } }, preview: null }
  const inspection = { currentRevision: "r", artifactRevision: "r", pathId: path.id, pointId: "point-1", loading: false, response: candidate }
  const inspect = vi.fn(), apply = vi.fn()
  const view = { key: "compact-test", state, pathStage: "smoothed", inspection, inspect, applyPoint: apply, busy: false }
  const hook = vi.spyOn(hooks, "useStagedTraining").mockReturnValue(view as unknown as ReturnType<typeof hooks.useStagedTraining>)
  const fetch = vi.spyOn(globalThis, "fetch")
  const original = JSON.stringify(state)
  const element = <GraphProvider allNodes={[]} edges={[]} requestScope={null}><ModelPaths nodeId="node" modelId="m1" readOnly={false} /></GraphProvider>
  try {
    const { rerender } = render(element)
    fireEvent.click(screen.getByRole("button", { name: "Next factors" }))
    expect(screen.getByText("7–8 of 8 factors")).toBeVisible()
    hook.mockReturnValue({ ...view, inspection: { ...inspection, pointId: "point-2", loading: true, response: undefined } } as unknown as ReturnType<typeof hooks.useStagedTraining>)
    rerender(<GraphProvider allNodes={[]} edges={[]} requestScope={null}><ModelPaths nodeId="node" modelId="m1" readOnly={false} /></GraphProvider>)
    expect(screen.queryByRole("region", { name: "Selected candidate summary" })).not.toBeInTheDocument()
    hook.mockReturnValue({ ...view, inspection: { ...inspection, pointId: "point-2", loading: false, response: undefined, error: "Could not read candidate" } } as unknown as ReturnType<typeof hooks.useStagedTraining>)
    rerender(element)
    expect(screen.queryByRole("region", { name: "Selected candidate summary" })).not.toBeInTheDocument()
    const next = structuredClone(candidate)
    next.ave_per_feature.forEach((row) => { row.bins[0].avg_predicted = 3 })
    hook.mockReturnValue({ ...view, inspection: { ...inspection, pointId: "point-2", response: next } } as unknown as ReturnType<typeof hooks.useStagedTraining>)
    rerender(<GraphProvider allNodes={[]} edges={[]} requestScope={null}><ModelPaths nodeId="node" modelId="m1" readOnly={false} /></GraphProvider>)
    expect(screen.getByText("1–6 of 8 factors")).toBeVisible()
    expect(screen.getAllByText("Young: before 1, selected 3")).toHaveLength(6)
    expect(JSON.stringify(state)).toBe(original)
    expect(inspect).not.toHaveBeenCalled()
    expect(apply).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  } finally {
    cleanup(); hook.mockRestore(); fetch.mockRestore()
  }
})


it("reports thinning from the selected snapshot rather than the parent response", () => {
  const saved = result()
  saved.loss_history_truncated = true
  saved.staged!.stages.full.loss_history = [{ iteration: 1, train_deviance: 2 }, { iteration: 2, train_deviance: 1 }]
  expect(stageResult(saved, "full")!.loss_history_truncated).toBe(false)
  const compact = structuredClone(saved)
  compact.staged!.stages.full.loss_history = [{ iteration: 1, train_deviance: 2 }, { iteration: 200, train_deviance: 1 }]
  expect(stageResult(compact, "full")!.loss_history_truncated).toBe(true)
})
