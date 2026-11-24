/**
 * The Modelling view (specs/frontend-modelling-optimiser-ui): the list of the pipeline's
 * interactive builds, and one open with its factors, its entry, the pane chosen and the
 * settings of the tab that shows. The node's editor is the Model Training node's own,
 * proved in its suite; here it is a stand-in that shows what the view hands it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import ModellingView from "../ModellingView"
import type { PanelGraphContextSnapshot } from "../../hooks/usePanelGraphContext"
import type { SimpleEdge, SimpleNode } from "../../panels/editors/_shared"
import useModellingViewStore from "../../stores/useModellingViewStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"
import useGraphStore from "../../stores/useGraphStore"
import useDocumentStatusStore, { documentExecutionKey } from "../../stores/useDocumentStatusStore"
import { useStagedViews } from "../useStagedTraining"
import type { Node } from "@xyflow/react"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { makeTrainResult } from "../../test-utils/factories"
import { modelResultKey } from "../interactiveModels"
import { answerQuestion, asked } from "../../test-utils/question"
import type { TBoostTables } from "../../api/types"
import { readStages } from "../../api/client"

// Real reads, except where a test holds the on-demand stage read back.
vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>()
  return { ...original, readStages: vi.fn(original.readStages) }
})

vi.mock("../../panels/ModellingConfig", () => ({
  default: (props: {
    nodeId: string
    stagedPane?: string
    panes?: readonly string[]
    reviewablePanes?: readonly string[]
    chosenFactor?: string | null
    modelId?: string
    config: Record<string, unknown>
    onReviewPane?: (pane: string) => void
    onUpdate: (key: string, value: unknown) => unknown
    onTrainAction?: (action: unknown) => void
  }) => (
    <div
      data-testid="node-editor"
      data-stage-pane={props.stagedPane}
      data-node={props.nodeId}
      data-model={props.modelId ?? ""}
      data-factors={((props.config.feature_columns as string[] | undefined) ?? []).join(",")}
      data-panes={(props.panes ?? []).join(",")}
      data-reviewable={(props.reviewablePanes ?? []).join(",")}
      data-chosen-factor={props.chosenFactor ?? ""}
    >
      <button type="button" onClick={() => props.onReviewPane?.("target")}>Go to Target</button>
      <button type="button" onClick={() => props.onReviewPane?.("features")}>Go to Factors</button>
      <button type="button" onClick={() => props.onReviewPane?.("train")}>Go to Train</button>
      {/* The Factors pane's untick, as the editor's own list sends it. */}
      <button type="button" onClick={() => props.onUpdate("feature_columns", ["age"])}>Leave out region</button>
      {/* The train action the editor reports, as it would with an issue or a failed run. */}
      <button
        type="button"
        onClick={() => props.onTrainAction?.({ training: false, busy: false, issues: ["Tick at least one factor."], error: null, train: () => {}, cancel: () => {} })}
      >
        Report an issue
      </button>
      <button
        type="button"
        onClick={() => props.onTrainAction?.({ training: false, busy: false, issues: [], error: "Out of memory", train: () => {}, cancel: () => {} })}
      >
        Report a failure
      </button>
      <input aria-label="Stand-in field" />
    </div>
  ),
}))

// The toolbar's panels are the pipeline editor's own; here each is a stand-in.
vi.mock("../../panels/GitPanel", () => ({ default: () => <div data-testid="git-panel" /> }))
vi.mock("../../panels/FilesPanel", () => ({ default: ({ place }: { place: string }) => <div data-testid="files-panel" data-place={place} /> }))
vi.mock("../../panels/assistant/AssistantPanel", () => ({
  default: (props: { isInsideSubmodel: boolean; readOnly: boolean }) => (
    <div data-testid="assistant-panel" data-read-only={String(props.readOnly)} data-inside={String(props.isInsideSubmodel)} />
  ),
}))

const node = (id: string, nodeType: string, config: Record<string, unknown>, extra: Record<string, unknown> = {}): SimpleNode => ({
  id,
  data: { label: id, description: "", nodeType, config, ...extra },
} as SimpleNode)

const quotes = node("quotes", "dataInput", {}, {
  _columns: [
    { name: "claims", dtype: "Float64" },
    { name: "exposure", dtype: "Float64" },
    { name: "age", dtype: "Float64" },
    { name: "region", dtype: "String" },
  ],
})
const frequency = node("frequency", "modelling", {
  interactive_build: true,
  target: "claims",
  offset: "exposure",
  models: [{ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age", "region"] }],
})
/** The key the frequency node's first model keeps its training and run under. */
const M1 = modelResultKey("frequency", "m1")
// A model built in its own node panel is not the view's.
const quick = node("quick_gbm", "modelling", { algorithm: "catboost", target: "claims" })
const severity = node("severity", "modelling", {
  interactive_build: true,
  models: [{ id: "m1", name: "t-boost", algorithm: "tboost" }],
})
const edges: SimpleEdge[] = [
  { id: "e1", source: "quotes", target: "frequency" },
  { id: "e2", source: "quotes", target: "quick_gbm" },
]

function snapshot(nodes: SimpleNode[]): PanelGraphContextSnapshot {
  const nodeById = new Map(nodes.map((entry) => [entry.id, entry]))
  return { allNodes: nodes, edges, nodeById, getNode: (id) => (id ? nodeById.get(id) ?? null : null) }
}

type Preview = Parameters<typeof ModellingView>[0]["preview"]

function renderView(overrides: { nodes?: SimpleNode[]; readOnly?: boolean; preview?: Preview } = {}) {
  const onUpdateNode = vi.fn(() => ({ ok: true as const }))
  const onOpenNode = vi.fn()
  const onRefreshNode = vi.fn()
  const onSaveShortcut = vi.fn()
  const onUndo = vi.fn()
  const onRedo = vi.fn()
  const view = (preview: Preview) => (
    <ModellingView
      graph={snapshot(overrides.nodes ?? [quotes, frequency, quick, severity])}
      onUpdateNode={onUpdateNode}
      onOpenNode={onOpenNode}
      onRefreshNode={onRefreshNode}
      preview={preview}
      onSave={() => Promise.resolve(true)}
      onSaveShortcut={onSaveShortcut}
      onUndo={onUndo}
      onRedo={onRedo}
      readOnly={overrides.readOnly ?? false}
      isInsideSubmodel={false}
      documentReadOnly={false}
    />
  )
  const { rerender } = render(view(overrides.preview ?? null))
  return { onUpdateNode, onOpenNode, onRefreshNode, onSaveShortcut, onUndo, onRedo, showPreview: (preview: Preview) => rerender(view(preview)) }
}

const trained = (ave: ReturnType<typeof makeTrainResult>["ave_per_feature"], tables: TBoostTables | null = null) => ({
  [M1]: {
    result: makeTrainResult({
      ave_per_feature: ave,
      tboost_tables: tables,
      loss_history: [
        { iteration: 1, train_deviance: 0.9, eval_deviance: 0.92 },
        { iteration: 2, train_deviance: 0.8, eval_deviance: 0.85 },
        { iteration: 3, train_deviance: 0.78, eval_deviance: 0.84 },
      ],
    }),
    jobId: "job_1",
    configHash: "hash",
    source: "live",
    structuralVersion: 0,
  },
})

const ageRow = {
  feature: "age",
  type: "numeric",
  bins: [
    { label: "18–30", exposure: 120.5, avg_actual: 0.21, avg_predicted: 0.2 },
    { label: "30–80", exposure: 480.25, avg_actual: 0.11, avg_predicted: 0.12 },
  ],
} as ReturnType<typeof makeTrainResult>["ave_per_feature"][number]

// One main-effect table, as a t-boost run reports it.
const AGE_TABLES: TBoostTables = {
  link: "log",
  base_value: Math.log(0.12),
  tables: [
    {
      term: "age",
      features: ["age"],
      order: 1,
      importance: 1,
      axes: [{ feature: "age", type: "continuous", labels: ["Missing", "<= 30", "> 30"], cuts: [30] }],
      scores: [0, 0.4, -0.1],
      relativities: [0, 0.4, -0.1].map(Math.exp),
      support: [3, 120, 480],
    },
  ],
  factored: [],
}

/** The node config an update sent: the last one, or the one at `index`. */
function updatedConfig(onUpdateNode: { mock: { calls: unknown[][] } }, index?: number): Record<string, unknown> {
  const call = index === undefined ? onUpdateNode.mock.calls.at(-1) : onUpdateNode.mock.calls[index]
  if (call === undefined) throw new Error("No update was sent.")
  return (call[1] as { config: Record<string, unknown> }).config
}

describe("ModellingView", () => {
  beforeEach(() => {
    useModellingViewStore.setState({ modelId: null, chosenModel: null, hiddenModels: [], panel: "features", panelWidth: 400, pane: "factor", factor: null, trainAction: null })
    useNodeResultsStore.setState({ trainResults: {}, trainJobs: {} })
    // A test's saved stages would otherwise stand in for the next one's runs.
    useStagedViews.setState({ models: {}, preferences: {} })
    useWorkbenchStore.setState({ enabled: false, activeView: "modelling" })
    useUIStore.setState({ paletteOpen: true })
  })

  afterEach(() => {
    cleanup()
    vi.mocked(readStages).mockReset()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
    useUIStore.setState({ gitOpen: false, assistantOpen: false, filesPlace: null, paletteOpen: true })
  })

  it("restores two background recipes against the latest canonical node without losing either", () => {
    const entries = ["m1", "m2", "m3"].map((id) => ({ id, name: id, algorithm: "tboost", stages: { applied_recipe: { schema_version: 1, operations: [] } } }))
    const modelNode = node("frequency", "modelling", { ...frequency.data.config, models: entries })
    const nodes = [quotes, modelNode]
    useGraphStore.setState({ nodes: nodes.map((entry) => ({ ...entry, position: { x: 0, y: 0 } })) as Node[], edges: [], focus: null })
    const document = documentExecutionKey(useDocumentStatusStore.getState())
    const recipe = { schema_version: 1, operations: [{ stage: "banded", parent: "full", settings: { tolerance: .5 } }] }
    for (const id of ["m2", "m3"]) {
      const response = makeTrainResult({ staged: {
        schema_version: 2, selected_stage: "banded", diagnostics_basis: "partition", reference_fit: "validation-0",
        configuration: { applied_recipe: recipe }, evaluation_identity: "e".repeat(64),
        fit_config_digest: "f".repeat(64), stages: { full: {}, banded: {} },
        pruning_path: null, path_unavailable_reason: null, recommendation: null,
      } })
      useStagedViews.getState().update(JSON.stringify([document, modelResultKey("frequency", id)]), { state: {
        schema_version: 1, next_preview: null, checkpoints: [], preview: null, final: null, job: null, stale_reason: null,
        current: { revision: (id === "m2" ? "a" : "b").repeat(32), build_id: "c".repeat(32),
          parent_revision: null, size: 1, sha256: "d".repeat(64), config_digest: "f".repeat(64),
          result: { graph_digest: "e".repeat(64), response } },
      } })
    }
    const { onUpdateNode } = renderView({ nodes })
    onUpdateNode.mockImplementation((...args: unknown[]) => {
      const [id, data] = args as [string, Record<string, unknown>]
      useGraphStore.setState((state) => ({ nodes: state.nodes.map((entry) => entry.id === id ? { ...entry, data: { ...entry.data, ...data } } : entry) }))
      return { ok: true as const }
    })
    fireEvent.click(screen.getByRole("button", { name: /^frequency$/ }))
    const current = useGraphStore.getState().nodes.find((entry) => entry.id === "frequency")!.data.config as { models: { id: string; stages: { applied_recipe: unknown[] } }[] }
    expect(current.models.filter((entry) => entry.id !== "m1").map((entry) => entry.stages.applied_recipe)).toEqual([recipe, recipe])
    useStagedViews.setState({ models: {}, preferences: {} })
  })

  it("lists the pipeline's interactive builds, with each one's target and last run, and no other model", () => {
    useNodeResultsStore.setState({ trainResults: trained([]) })
    renderView()
    const rows = within(screen.getByRole("main", { name: "Models" })).getAllByRole("row").slice(1)

    expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent))).toEqual([
      ["frequency", "claims", "t-boost: Trained"],
      ["severity", "Not set", "t-boost: Not trained"],
    ])
    expect(screen.queryByText("quick_gbm")).toBeNull()
  })

  it("says so when the pipeline has no interactive build, and still offers the other views", () => {
    renderView({ nodes: [quotes, quick] })

    expect(screen.getByText("No interactive builds in this pipeline.")).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Pricing" }))
    expect(useWorkbenchStore.getState().activeView).toBe("pipeline")
  })

  it("opens a model with its factors in the right-hand Features pane, as the node's columns give them", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    const factors = screen.getByRole("navigation", { name: "Factors" })
    // The target and the offset hold a role, so they are not factors.
    expect(within(screen.getByRole("list", { name: "Factor names" })).getAllByRole("button").filter((button) => button.hasAttribute("aria-pressed")).map((button) => button.textContent)).toEqual(["age", "region"])
    // Inclusion is always available; the settings icon opens fitting controls.
    expect(within(factors).getAllByRole("checkbox")).toHaveLength(2)
    expect(within(factors).queryByText("Float64")).toBeNull()
    expect(within(factors).queryByRole("button", { name: /increasing/ })).toBeNull()
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-node", "frequency")
  })

  it("expands fitting options from settings icons without changing the chart", () => {
    const { onUpdateNode } = renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    const age = screen.getByRole("button", { name: "age settings" })
    expect(age).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(age)
    expect(age).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeChecked()
    fireEvent.click(screen.getByRole("button", { name: "age: increasing" }))
    expect((updatedConfig(onUpdateNode).models as Record<string, unknown>[])[0].monotone_constraints).toEqual({ age: 1 })
    expect(useModellingViewStore.getState().factor).toBe("age")
    fireEvent.click(age)
    expect(age).toHaveAttribute("aria-expanded", "false")
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeChecked()
    expect(useModellingViewStore.getState().factor).toBe("age")
    fireEvent.click(age)
    fireEvent.click(screen.getByRole("button", { name: "region settings" }))
    expect(age).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("group", { name: "age fitting options" })).toBeNull()
    expect(screen.getByRole("checkbox", { name: "Include region" })).toBeChecked()
    expect(screen.getByRole("button", { name: "region: increasing" })).toBeDisabled()
    expect(useModellingViewStore.getState().factor).toBe("age")
  })

  it("edits only the selected model from the expanded factor row", () => {
    const first = { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"], monotone_constraints: { age: 1 } }
    const second = { id: "m2", name: "second", algorithm: "tboost", feature_columns: ["region"] }
    const two = node("frequency", "modelling", { ...frequency.data.config as Record<string, unknown>, models: [first, second] })
    const { onUpdateNode } = renderView({ nodes: [quotes, two] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(within(screen.getByRole("group", { name: "Models" })).getByRole("button", { name: "second" }))
    expect(screen.getByRole("checkbox", { name: "Include age" })).not.toBeChecked()
    expect(screen.getByRole("button", { name: "age: increasing" })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox", { name: "Include age" }))
    expect(updatedConfig(onUpdateNode).models).toEqual([first, { ...second, feature_columns: ["age", "region"] }])
  })

  it("names a factor left out of the model in muted text, and still charts it on a click", () => {
    const ageOnly = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [{ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"] }],
    })
    renderView({ nodes: [quotes, ageOnly] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const factors = screen.getByRole("navigation", { name: "Factors" })

    expect(within(factors).getByRole("button", { name: "age" }).style.color).toBe("var(--text-primary)")
    const region = within(factors).getByRole("button", { name: "region" })
    expect(region.style.color).toBe("var(--text-muted)")
    fireEvent.click(region)
    expect(region).toHaveAttribute("aria-pressed", "true")
    expect(useModellingViewStore.getState().factor).toBe("region")
  })

  it("searches the factors by name above the list", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const factors = screen.getByRole("navigation", { name: "Factors" })
    const search = within(factors).getByRole("searchbox", { name: "Search factors" })
    expect(search).not.toHaveAttribute("placeholder")
    // The names scroll in their own list, below the search rather than behind it.
    expect(within(screen.getByRole("list", { name: "Factor names" })).queryByRole("searchbox")).toBeNull()

    fireEvent.change(search, { target: { value: "REG" } })
    expect(within(screen.getByRole("list", { name: "Factor names" })).getAllByRole("button").filter((button) => button.hasAttribute("aria-pressed")).map((button) => button.textContent)).toEqual(["region"])
    fireEvent.change(within(factors).getByRole("searchbox", { name: "Search factors" }), { target: { value: "postcode" } })
    expect(within(screen.getByRole("list", { name: "Factor names" })).queryAllByRole("button")).toEqual([])
    expect(within(factors).getByText("No matching factors")).toBeInTheDocument()
  })

  it("says so when every column reaching the model holds a role", () => {
    const rolesOnly = node("quotes", "dataInput", {}, {
      _columns: [{ name: "claims", dtype: "Float64" }, { name: "exposure", dtype: "Float64" }],
    })
    const noFeatures = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [{ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: [] }],
    })
    renderView({ nodes: [rolesOnly, noFeatures] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    expect(within(screen.getByRole("navigation", { name: "Factors" })).getByText("No factors")).toBeInTheDocument()
  })

  it("collapses the shared palette without losing the model or expanded factor, and keeps view switching available", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(within(screen.getByRole("navigation", { name: "Factors" })).getByRole("button", { name: "age" }))
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    const chosen = useModellingViewStore.getState().chosenModel

    fireEvent.click(screen.getByRole("button", { name: "Collapse palette" }))
    expect(useUIStore.getState().paletteOpen).toBe(false)
    expect(screen.queryByRole("navigation", { name: "Models" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Show model palette" })).toBeInTheDocument()
    expect(within(screen.getByRole("navigation", { name: "Factors" })).getByRole("button", { name: "age settings" })).toHaveAttribute("aria-expanded", "true")
    expect(useModellingViewStore.getState().chosenModel).toBe(chosen)
    expect(useModellingViewStore.getState().factor).toBe("age")

    fireEvent.click(screen.getByRole("button", { name: "Show model palette" }))
    expect(useUIStore.getState().paletteOpen).toBe(true)
    expect(within(screen.getByRole("navigation", { name: "Models" })).getByRole("button", { name: "t-boost" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(screen.getByRole("button", { name: "Collapse palette" }))
    fireEvent.click(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Pricing" }))
    expect(useWorkbenchStore.getState().activeView).toBe("pipeline")
    expect(useUIStore.getState().paletteOpen).toBe(false)
  })

  it("lays the list of models and a model in the node palette's column, the view switcher at its foot", () => {
    renderView()
    // `.palette` is the node palette's column in Pricing; haute-ui's tokens test pins its width.
    expect(screen.getByRole("group", { name: "Views" }).parentElement).toHaveClass("palette")
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    const models = screen.getByRole("navigation", { name: "Models" })
    const column = models.closest(".palette") as HTMLElement
    expect(column).toBeInTheDocument()
    expect(within(column).getByRole("group", { name: "Views" })).toBeInTheDocument()
    expect(within(models).getByRole("button", { name: "t-boost" })).toBeInTheDocument()
    expect(within(models).getByRole("button", { name: "Add" })).toBeInTheDocument()
    expect(within(column).queryByRole("button", { name: "age" })).not.toBeInTheDocument()
    expect(screen.getByRole("complementary", { name: "t-boost settings" })).toContainElement(screen.getByRole("navigation", { name: "Factors" }))
    expect(screen.queryByRole("button", { name: "Models" })).not.toBeInTheDocument()
  })

  it("selects the model's node in the pipeline when it opens, once, as a click on it does", () => {
    const { onOpenNode, showPreview } = renderView()
    expect(onOpenNode).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    expect(onOpenNode).toHaveBeenCalledTimes(1)
    expect(onOpenNode).toHaveBeenCalledWith("frequency")

    // The preview that follows changes nothing about which model is open.
    showPreview({ nodeId: "frequency", status: "loading", error: null })
    showPreview({ nodeId: "frequency", status: "ok", error: null })
    expect(onOpenNode).toHaveBeenCalledTimes(1)
  })

  it("says the columns are loading, or why they did not arrive, and asks again on Refresh", () => {
    // A pipeline just loaded: nothing has been previewed, so no node holds its columns.
    const unpreviewed = node("quotes", "dataInput", {})
    const { onRefreshNode, showPreview } = renderView({ nodes: [unpreviewed, frequency] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const factors = screen.getByRole("navigation", { name: "Factors" })
    expect(within(factors).getByText("Columns not known yet")).toBeInTheDocument()

    // In manual calculation nothing runs until it is asked for.
    fireEvent.click(within(factors).getByRole("button", { name: "Refresh" }))
    expect(onRefreshNode).toHaveBeenCalledWith("frequency")

    showPreview({ nodeId: "frequency", status: "loading", error: null })
    expect(within(factors).getByRole("status")).toHaveTextContent("Loading columns")
    expect(within(factors).queryByRole("button", { name: "Refresh" })).toBeNull()

    showPreview({ nodeId: "frequency", status: "error", error: "quotes.parquet was not found" })
    expect(within(factors).getByRole("alert")).toHaveTextContent("quotes.parquet was not found")
    expect(within(factors).queryByText("Columns not known yet")).toBeNull()
    expect(within(factors).getByRole("button", { name: "Refresh" })).toBeInTheDocument()

    // Another node's preview says nothing about this model's columns.
    showPreview({ nodeId: "quick_gbm", status: "error", error: "another node failed" })
    expect(within(factors).queryByRole("alert")).toBeNull()
  })

  it("has no Refresh once the columns are known", () => {
    renderView({ preview: { nodeId: "frequency", status: "ok", error: null } })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull()
  })

  it("takes a factor out of the chosen model through the node's update, keeping the node's marker", () => {
    const { onUpdateNode } = renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Include region" }))

    expect(onUpdateNode).toHaveBeenCalledTimes(1)
    const [id, data] = onUpdateNode.mock.calls[0] as unknown as [string, Record<string, unknown>]
    expect(id).toBe("frequency")
    const config = data.config as Record<string, unknown>
    expect(config.models).toEqual([{ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"] }])
    expect(config.feature_columns).toBeUndefined()
    expect(config.interactive_build).toBe(true)
  })

  it("shows a model and changes nothing while the pipeline cannot be edited", () => {
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    const { onUpdateNode } = renderView({ readOnly: true })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    // The settings, the factors' among them, are inert, and an edit is refused.
    expect(screen.getByTestId("node-editor").closest("[inert]")).toBeInTheDocument()
    act(() => useModellingViewStore.getState().showPanel("train"))
    fireEvent.click(screen.getByRole("button", { name: "Leave out region" }))
    act(() => useModellingViewStore.getState().showPanel("features"))
    expect(onUpdateNode).not.toHaveBeenCalled()

    // Looking is not editing: a factor can still be charted, a model unticked and Model Setup shown.
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    expect(screen.getByRole("img", { name: "Actual vs expected for age" })).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    expect(screen.getByRole("button", { name: "age: increasing" })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox", { name: "Include age" }))
    expect(onUpdateNode).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByRole("navigation", { name: "Models" })).getByRole("button", { name: "t-boost" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show t-boost on the charts" }))
    expect(useModellingViewStore.getState().hiddenModels).toEqual(["m1"])
    act(() => useModellingViewStore.getState().showPanel("setup"))
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-panes", "target")
  })

  it("saves on Ctrl+S and undoes and redoes the pipeline's last edit from the keyboard", () => {
    const { onSaveShortcut, onUndo, onRedo } = renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    fireEvent.keyDown(window, { key: "s", ctrlKey: true })
    expect(onSaveShortcut).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: "z", ctrlKey: true })
    expect(onUndo).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: "Z", ctrlKey: true, shiftKey: true })
    fireEvent.keyDown(window, { key: "y", metaKey: true })
    expect(onRedo).toHaveBeenCalledTimes(2)

    // Typing in a field keeps Ctrl+Z for the text; a plain key is nobody's.
    fireEvent.keyDown(screen.getByRole("searchbox", { name: "Search factors" }), { key: "z", ctrlKey: true })
    act(() => useModellingViewStore.getState().showPanel("train"))
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Stand-in field" }), { key: "z", ctrlKey: true })
    fireEvent.keyDown(window, { key: "z" })
    expect(onUndo).toHaveBeenCalledTimes(1)
  })

  it("undoes nothing from the keyboard while the pipeline cannot be edited, and still saves", () => {
    const { onSaveShortcut, onUndo, onRedo } = renderView({ readOnly: true })

    fireEvent.keyDown(window, { key: "z", ctrlKey: true })
    fireEvent.keyDown(window, { key: "y", ctrlKey: true })
    fireEvent.keyDown(window, { key: "s", ctrlKey: true })
    expect(onUndo).not.toHaveBeenCalled()
    expect(onRedo).not.toHaveBeenCalled()
    expect(onSaveShortcut).toHaveBeenCalledTimes(1)
  })

  it("charts the selected factor from the model's latest run", () => {
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const chart = screen.getByRole("tabpanel", { name: "Factor" })
    expect(within(chart).getByText("No factor selected")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "age" }))
    expect(within(chart).getByRole("img", { name: "Actual vs expected for age" })).toBeInTheDocument()
    expect(within(chart).getByRole("img", { name: "Exposure for age" })).toBeInTheDocument()

    // A factor the run never saw has no chart to draw.
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    expect(within(chart).getByText("t-boost's latest run does not have region")).toBeInTheDocument()
  })

  it("charts a factor the run left out from its predictions, and says why one it left out has no chart", () => {
    const run = trained([ageRow])
    run[M1].result.ave_left_out = [{ ...ageRow, feature: "region", type: "categorical" }]
    useNodeResultsStore.setState({ trainResults: run })
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const chart = screen.getByRole("tabpanel", { name: "Factor" })

    fireEvent.click(screen.getByRole("button", { name: "region" }))
    expect(within(chart).getByText("t-boost (not in model)")).toBeInTheDocument()
    expect(within(chart).getByRole("img", { name: "Actual vs expected for region" })).toBeInTheDocument()
    // A fitted factor carries no such mark.
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    expect(within(chart).queryByText("t-boost (not in model)")).toBeNull()

    act(() => useNodeResultsStore.setState({
      trainResults: {
        [M1]: {
          ...run[M1],
          result: {
            ...run[M1].result,
            ave_left_out: [],
            diagnostics_errors: [{ diagnostic: "ave_left_out", error: "region: a Date column is not charted.", error_type: "HauteValidationError" }],
          },
        },
      },
    }))
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    expect(within(chart).getByText("t-boost has no chart of region: a Date column is not charted.")).toBeInTheDocument()
  })

  it("has no chart before the model has been boosted", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "age" }))

    expect(within(screen.getByRole("tabpanel", { name: "Factor" })).getByText("Not boosted yet")).toBeInTheDocument()
  })

  it("draws the learning curve of the latest run in its own pane, and a factor's name brings its chart back", () => {
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const panes = screen.getByRole("tablist", { name: "Panes" })
    expect(within(panes).getByRole("tab", { name: "Factor" })).toHaveAttribute("aria-selected", "true")

    fireEvent.click(within(panes).getByRole("tab", { name: "Learning curve" }))
    const curve = screen.getByRole("tabpanel", { name: "Learning curve" })
    expect(within(curve).getByRole("img", { name: "Loss history chart" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "age" }))
    expect(within(panes).getByRole("tab", { name: "Factor" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("img", { name: "Actual vs expected for age" })).toBeInTheDocument()
  })

  it("draws the curve of a boost as it runs, from its second reported round, over the last run's", () => {
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("tab", { name: "Learning curve" }))
    const curve = screen.getByRole("tabpanel", { name: "Learning curve" })

    const running = (history: Array<{ iteration: number; train_deviance: number; eval_deviance: number }>) => ({
      [M1]: {
        jobId: "job_2",
        nodeId: "frequency",
        nodeLabel: "frequency",
        progress: {
          status: "running" as const,
          progress: 0.1,
          message: "Boosting",
          iteration: history.length,
          total_iterations: 4000,
          train_loss: {},
          train_loss_history: history,
          elapsed_seconds: 1,
        },
        error: null,
        configHash: "hash",
        source: "live",
        structuralVersion: 0,
      },
    })
    act(() => useNodeResultsStore.setState({ trainJobs: running([{ iteration: 1, train_deviance: 0.9, eval_deviance: 0.92 }]) }))
    expect(within(curve).getByText("Boosting")).toBeInTheDocument()
    expect(within(curve).queryByRole("img", { name: "Loss history chart" })).toBeNull()

    act(() => useNodeResultsStore.setState({
      trainJobs: running([
        { iteration: 1, train_deviance: 0.9, eval_deviance: 0.92 },
        { iteration: 2, train_deviance: 0.85, eval_deviance: 0.88 },
      ]),
    }))
    expect(within(curve).getByRole("img", { name: "Loss history chart" })).toBeInTheDocument()
  })

  it("has no learning curve before the model has been boosted", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("tab", { name: "Learning curve" }))

    expect(within(screen.getByRole("tabpanel", { name: "Learning curve" })).getByText("Not boosted yet")).toBeInTheDocument()
  })

  it("shows Features, Parameters, Training, Model Setup and Data Split on the right as the toolbar picks them, and Go to leads to the panel that holds a pane", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const editor = () => screen.getByTestId("node-editor")
    // One mounted editor retains drafts across parameter and action tabs.
    expect(screen.getByRole("complementary", { name: "t-boost settings" })).toContainElement(editor())
    expect(editor()).toHaveAttribute("data-panes", "params")
    expect(useModellingViewStore.getState().panel).toBe("features")
    expect(screen.getByRole("heading", { name: "Features" })).toBeInTheDocument()
    expect(editor()).not.toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    const search = screen.getByRole("searchbox", { name: "Search factors" })
    fireEvent.change(search, { target: { value: "age" } })
    act(() => useModellingViewStore.getState().showPanel("train"))
    expect(editor()).toBeVisible()
    expect(screen.queryByRole("searchbox", { name: "Search factors" })).not.toBeInTheDocument()
    expect(editor()).toHaveAttribute("data-model", "m1")
    expect(editor()).toHaveAttribute("data-factors", "age,region")
    expect(editor()).toHaveAttribute("data-reviewable", "features,params,target,split")
    expect(editor()).toHaveAttribute("data-stage-pane", "train")
    expect(screen.getByRole("heading", { name: "Training" })).toBeInTheDocument()
    act(() => useModellingViewStore.getState().showPanel("params"))
    expect(editor()).toHaveAttribute("data-stage-pane", "params")
    expect(screen.getByRole("heading", { name: "Parameters" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Go to Train" }))
    expect(editor()).toHaveAttribute("data-stage-pane", "train")
    const mountedEditor = editor()

    act(() => useModellingViewStore.getState().showPanel("setup"))
    expect(screen.getByRole("complementary", { name: "Model setup" })).toContainElement(editor())
    expect(editor()).toHaveAttribute("data-panes", "target")
    act(() => useModellingViewStore.getState().showPanel("split"))
    expect(screen.getByRole("complementary", { name: "Data split" })).toContainElement(editor())
    expect(editor()).toHaveAttribute("data-panes", "split")

    act(() => useModellingViewStore.getState().showPanel("params"))
    expect(editor()).toBe(mountedEditor)
    expect(editor()).toHaveAttribute("data-panes", "params")
    fireEvent.click(screen.getByRole("button", { name: "Go to Factors" }))
    expect(useModellingViewStore.getState().panel).toBe("features")
    expect(search).toHaveValue("age")
    expect(screen.getByRole("button", { name: "age settings" })).toHaveAttribute("aria-expanded", "true")
    act(() => useModellingViewStore.getState().showPanel("train"))
    fireEvent.click(screen.getByRole("button", { name: "Go to Target" }))
    expect(useModellingViewStore.getState().panel).toBe("setup")
  })

  it("shows the settings in the pipeline's side panel: its red X closes it, its left edge resizes it, and the editor stays", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const panel = screen.getByTestId("modelling-settings-panel")
    const editor = screen.getByTestId("node-editor")
    expect(panel).toHaveClass("side-panel")
    // Its slide-in starts past the right edge; the view clips it, so no scrollbar flashes.
    expect(screen.getByTestId("modelling-view")).toHaveClass("overflow-hidden")
    expect(panel.style.width).toBe("400px")

    fireEvent.mouseDown(within(panel).getByTestId("panel-resize-handle"), { clientX: 1000 })
    fireEvent.mouseMove(window, { clientX: 900, buttons: 1 })
    fireEvent.mouseUp(window)
    expect(useModellingViewStore.getState().panelWidth).toBe(500)

    fireEvent.click(within(panel).getByRole("button", { name: "Close" }))
    expect(useModellingViewStore.getState().panel).toBeNull()
    expect(panel).not.toBeVisible()
    expect(screen.getByTestId("node-editor")).toBe(editor)

    act(() => useModellingViewStore.getState().showPanel("params"))
    expect(panel).toBeVisible()
    expect(within(panel).getByRole("heading", { name: "Parameters" })).toBeInTheDocument()
  })

  it("hands the editor the factor chosen on the left, and keeps the panel that shows", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-chosen-factor", "")
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-chosen-factor", "region")
    expect(useModellingViewStore.getState().panel).toBe("features")
  })

  it("keeps progress in the model name and selects its settings on a click", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const models = screen.getByRole("group", { name: "Models" })
    const entry = within(models).getByRole("button", { name: "t-boost" })
    expect(entry).toHaveTextContent(/^t-boost$/)
    expect(entry).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(entry)
    expect(within(models).getByRole("checkbox", { name: "Show t-boost on the charts" })).toBeChecked()

    act(() => useModellingViewStore.getState().showPanel("split"))
    fireEvent.click(entry)
    expect(useModellingViewStore.getState().panel).toBe("features")

    act(() => useNodeResultsStore.setState({
      trainJobs: {
        [M1]: {
          jobId: "job_2",
          nodeId: "frequency",
          nodeLabel: "frequency",
          progress: {
            status: "running" as const,
            progress: 0.4,
            message: "Boosting",
            iteration: 1600,
            total_iterations: 4000,
            train_loss: {},
            elapsed_seconds: 1,
          },
          error: null,
          configHash: "hash",
          source: "live",
          structuralVersion: 0,
        },
      },
    }))
    expect(within(entry).getByRole("progressbar", { name: "t-boost training" })).toHaveAttribute("aria-valuenow", "20")

    const completed = trained([])
    completed[M1].jobId = "job_2"
    completed[M1].result.job_id = "job_2"
    completed[M1].result.staged = {
      schema_version: 2, selected_stage: "full", diagnostics_basis: "partition", reference_fit: "validation-0",
      configuration: {}, evaluation_identity: "e".repeat(64), fit_config_digest: "f".repeat(64),
      stages: {}, pruning_path: null, path_unavailable_reason: null, recommendation: null,
    }
    // Status completion may arrive before any stage read identifies the continuation.
    act(() => useNodeResultsStore.setState({ trainJobs: {}, trainResults: completed }))
    expect(within(entry).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50")
    const stageKey = JSON.stringify([documentExecutionKey(useDocumentStatusStore.getState()), M1])
    act(() => {
      useStagedViews.getState().update(stageKey, { state: {
        schema_version: 1, checkpoints: [], preview: null, final: null, stale_reason: null,
        current: { revision: "a".repeat(32), build_id: "b".repeat(32), parent_revision: null,
          size: 1, sha256: "c".repeat(64), config_digest: "d".repeat(64),
          result: { graph_digest: "e".repeat(64), response: completed[M1].result } },
        job: { id: "job_2", operation: "full", automatic: false, status: "completed", message: "Completed", owner_pid: 1, expected_revision: null },
        next_preview: { status: "queued", operation: "pruned", owner_pid: 1, parent_revision: "a".repeat(32), message: "" },
      } })
      useNodeResultsStore.setState({ trainJobs: {}, trainResults: completed })
    })
    expect(within(entry).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50")
    const stageState = useStagedViews.getState().models[stageKey].state!
    act(() => useStagedViews.getState().update(stageKey, { state: {
      ...stageState, next_preview: null,
      job: { ...stageState.job!, id: "automatic", automatic: true, operation: "pruned", status: "running" },
    } }))
    expect(within(entry).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50")
    act(() => useStagedViews.getState().update(stageKey, { state: {
      ...stageState, next_preview: null,
      job: { ...stageState.job!, id: "automatic", automatic: true, operation: "pruned", status: "completed" },
    } }))
    expect(within(entry).queryByRole("progressbar")).toBeNull()
    expect(entry).toHaveTextContent(/^t-boost$/)
    act(() => useStagedViews.getState().update(stageKey, { state: {
      ...stageState, next_preview: { ...stageState.next_preview!, status: "failed", message: "Preparation could not start" },
    } }))
    expect(entry).toHaveAttribute("title", "t-boost: its last run failed")
  })

  it("keeps readiness warnings out of the chart layout and shows failures in the settings column", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    act(() => useModellingViewStore.getState().showPanel("train"))
    fireEvent.click(screen.getByRole("button", { name: "Report an issue" }))
    expect(screen.queryByText(/cannot train yet/)).not.toBeInTheDocument()
    expect(useModellingViewStore.getState().trainAction?.issues).toEqual(["Tick at least one factor."])

    fireEvent.click(screen.getByRole("button", { name: "Report a failure" }))
    expect(within(screen.getByRole("complementary", { name: "t-boost settings" })).getByRole("alert")).toHaveTextContent("t-boost failed: Out of memory")
  })

  it("draws every ticked model's expected over the actual, the chosen one heavier, and leaves an unticked one off", () => {
    const two = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [
        { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age", "region"] },
        { id: "m2", name: "short list", algorithm: "tboost", feature_columns: ["region"] },
      ],
    })
    const second = {
      ...ageRow,
      bins: ageRow.bins.map((bin) => ({ ...bin, avg_predicted: bin.avg_predicted * 1.1 })),
    }
    const runs = trained([ageRow])
    const leftOut = trained([])[M1]
    leftOut.result.ave_left_out = [second]
    useNodeResultsStore.setState({ trainResults: { ...runs, [modelResultKey("frequency", "m2")]: leftOut } })
    renderView({ nodes: [quotes, two] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    const chart = screen.getByRole("tabpanel", { name: "Factor" })

    expect(chart.querySelector('[data-series="model-m1"] path')).toHaveAttribute("stroke-width", "2.5")
    expect(chart.querySelector('[data-series="model-m2"] path')).toHaveAttribute("stroke-width", "1.5")
    // The second model leaves age out: its line is its prediction, and the legend says so.
    expect(within(chart).getByText("short list (not in model)")).toBeInTheDocument()
    expect(within(chart).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Bin", "Actual", "t-boost", "short list", "Exposure"])

    // A run on other rows keeps its levels' names but not their exposure: it is named, not drawn.
    act(() => useNodeResultsStore.setState({
      trainResults: {
        ...runs,
        [modelResultKey("frequency", "m2")]: {
          ...leftOut,
          result: { ...leftOut.result, ave_left_out: [{ ...second, bins: second.bins.map((bin) => ({ ...bin, exposure: bin.exposure * 0.8 })) }] },
        },
      },
    }))
    expect(within(chart).getByText("Binned on other rows, not drawn: short list")).toBeInTheDocument()
    expect(chart.querySelector('[data-series="model-m2"]')).toBeNull()

    fireEvent.click(within(screen.getByRole("navigation", { name: "Models" })).getByRole("button", { name: "short list" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show short list on the charts" }))
    expect(chart.querySelector('[data-series="model-m2"]')).toBeNull()
    expect(chart.querySelector('[data-series="model-m1"]')).not.toBeNull()
    fireEvent.click(within(screen.getByRole("navigation", { name: "Models" })).getByRole("button", { name: "t-boost" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Show t-boost on the charts" }))
    expect(within(chart).getByText("No model ticked")).toBeInTheDocument()
  })

  it("isolates parent or full plus the candidate and restores the actual plotted visibility", () => {
    const two = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [{ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"] },
        { id: "m2", name: "second", algorithm: "tboost", feature_columns: ["age"] }],
    })
    useGraphStore.setState({ nodes: [quotes, two].map((entry) => ({ ...entry, position: { x: 0, y: 0 } })) as Node[] })
    const snapshot = { metrics: { rmse: .2 }, feature_importance: [], features: ["age"], cat_features: [],
      diagnostics_set: "validation", ave_per_feature: [ageRow], ave_left_out: [], tboost_tables: null,
      loss_history: [], fit_evidence: null, diagnostics_errors: [], recipe: { schema_version: 1, operations: [] } }
    const response = makeTrainResult({ staged: { schema_version: 2, selected_stage: "banded", diagnostics_basis: "partition", reference_fit: "validation-0",
      configuration: { applied_recipe: { schema_version: 1, operations: [] } }, evaluation_identity: "e".repeat(64), fit_config_digest: "f".repeat(64),
      stages: { full: snapshot, pruned: snapshot, banded: snapshot }, pruning_path: null, path_unavailable_reason: null, recommendation: null } })
    const record = { revision: "a".repeat(32), build_id: "b".repeat(32), parent_revision: null, size: 1, sha256: "c".repeat(64), config_digest: "f".repeat(64),
      result: { graph_digest: "d".repeat(64), response } }
    const state = { schema_version: 1 as const, next_preview: null, checkpoints: [], current: record, preview: { ...record, revision: "c".repeat(32), result: { ...record.result, response: {
      ...response, staged: { ...response.staged!, selected_stage: "smoothed" as const, stages: { ...response.staged!.stages, smoothed: snapshot } },
    } } }, final: null, job: null, stale_reason: null }
    const document = documentExecutionKey(useDocumentStatusStore.getState())
    const key = JSON.stringify([document, modelResultKey("frequency", "m1")])
    useStagedViews.getState().update(key, { state, visible: ["pruned", "banded"] })
    useStagedViews.getState().update(JSON.stringify([document, modelResultKey("frequency", "m2")]), { state: { ...state, preview: null }, visible: ["full"] })
    renderView({ nodes: [quotes, two] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    const chart = screen.getByRole("tabpanel", { name: "Factor" })
    const lines = () => [...chart.querySelectorAll('[data-series^="model-"]')].map((element) => element.getAttribute("data-series")).sort()
    const normal = ["model-m1-banded", "model-m1-preview", "model-m1-pruned", "model-m2-full"]
    expect(lines()).toEqual(normal)
    act(() => useStagedViews.getState().update(key, { comparison: { stage: "smoothed", parent: "banded" } }))
    expect(lines()).toEqual(["model-m1-banded", "model-m1-preview"])
    act(() => useStagedViews.getState().update(key, { comparison: { stage: "smoothed", parent: "full" } }))
    expect(lines()).toEqual(["model-m1-full", "model-m1-preview"])
    fireEvent.click(screen.getByRole("button", { name: "End comparison" }))
    expect(lines()).toEqual(normal)
    act(() => useStagedViews.getState().update(key, { comparison: { stage: "smoothed", parent: "full" } }))
    act(() => useStagedViews.getState().update(key, { state: { ...state, preview: null } }))
    expect(screen.queryByRole("button", { name: "End comparison" })).not.toBeInTheDocument()
    expect(lines()).toEqual(["model-m1-banded", "model-m1-pruned", "model-m2-full"])
    useStagedViews.setState({ models: {}, preferences: {} })
  })

  it("opens model actions from the settings icon independently of names and chart checkboxes", () => {
    const two = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [
        { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age"] },
        { id: "m2", name: "short list", algorithm: "tboost", feature_columns: ["region"] },
      ],
    })
    renderView({ nodes: [quotes, two] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const models = screen.getByRole("navigation", { name: "Models" })
    const first = within(models).getByRole("button", { name: "t-boost settings" })
    const second = within(models).getByRole("button", { name: "short list settings" })
    expect(first).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(within(models).getByRole("button", { name: "short list" }))
    expect(second).toHaveAttribute("aria-expanded", "false")
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-model", "m2")
    const check = within(models).getByRole("checkbox", { name: "Show t-boost on the charts" })
    fireEvent.click(check)
    expect(first).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(first)
    expect(first).toHaveAttribute("aria-expanded", "true")
    expect(within(models).getByRole("group", { name: "t-boost controls" })).toBeInTheDocument()
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-model", "m2")
    fireEvent.click(second)
    expect(first).toHaveAttribute("aria-expanded", "false")
    expect(second).toHaveAttribute("aria-expanded", "true")
    fireEvent.click(second)
    expect(second).toHaveAttribute("aria-expanded", "false")
    expect(useModellingViewStore.getState().hiddenModels).toEqual(["m1"])
    expect(check).not.toBeChecked()
    fireEvent.click(second)
    fireEvent.click(within(models).getByRole("button", { name: "t-boost" }))
    expect(second).toHaveAttribute("aria-expanded", "false")
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-model", "m1")
  })

  it("offers t-boost in the Add picker and only creates a model after selection", () => {
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    const { onUpdateNode } = renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const add = within(screen.getByRole("group", { name: "Models" })).getByRole("button", { name: "Add" })
    fireEvent.click(add)
    expect(add).toHaveAttribute("aria-expanded", "true")
    const picker = screen.getByRole("menu", { name: "Add model" })
    expect(within(picker).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["t-boost"])
    expect(onUpdateNode).not.toHaveBeenCalled()
    fireEvent.keyDown(within(picker).getByRole("menuitem", { name: "t-boost" }), { key: "Escape" })
    expect(screen.queryByRole("menu", { name: "Add model" })).toBeNull()
    expect(add).toHaveFocus()
    expect(onUpdateNode).not.toHaveBeenCalled()
    fireEvent.click(add)
    fireEvent.click(within(screen.getByRole("menu", { name: "Add model" })).getByRole("menuitem", { name: "t-boost" }))
    expect(screen.queryByRole("menu", { name: "Add model" })).toBeNull()
    expect(onUpdateNode).toHaveBeenCalledTimes(1)

    const config = updatedConfig(onUpdateNode, 0)
    const [first, added] = config.models as Array<{ id: string }>
    expect(first).toEqual({ id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age", "region"] })
    expect(added).toEqual({ id: expect.stringMatching(/^m[0-9a-f]{8}$/), name: "t-boost 2", algorithm: "tboost", feature_columns: ["age", "region"] })
    expect(useModellingViewStore.getState().chosenModel).toBe(added.id)
  })

  it("keeps each model's run, settings and charts apart, and confirms removal from its expanded controls", async () => {
    const two = node("frequency", "modelling", {
      ...frequency.data.config as Record<string, unknown>,
      models: [
        { id: "m1", name: "t-boost", algorithm: "tboost", feature_columns: ["age", "region"] },
        { id: "m2", name: "short list", algorithm: "tboost", feature_columns: ["age"] },
      ],
    })
    useNodeResultsStore.setState({ trainResults: trained([ageRow]) })
    const { onUpdateNode } = renderView({ nodes: [quotes, two] })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const models = screen.getByRole("group", { name: "Models" })
    fireEvent.click(screen.getByRole("button", { name: "age" }))
    expect(screen.getByRole("img", { name: "Actual vs expected for age" })).toBeInTheDocument()

    // The other model has its own settings, and no run yet: the chart keeps the trained one.
    fireEvent.click(within(models).getByRole("button", { name: "short list" }))
    expect(screen.getByRole("complementary", { name: "short list settings" })).toBeInTheDocument()
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-model", "m2")
    expect(screen.getByTestId("node-editor")).toHaveAttribute("data-factors", "age")
    expect(screen.getByRole("img", { name: "Actual vs expected for age" })).toBeInTheDocument()
    expect(document.querySelector('[data-series="model-m2"]')).toBeNull()
    // Its factor list mutes what it leaves out.
    expect(within(screen.getByRole("navigation", { name: "Factors" })).getByRole("button", { name: "region" }).style.color).toBe("var(--text-muted)")

    fireEvent.click(within(models).getByRole("button", { name: "short list settings" }))
    fireEvent.click(within(models).getByRole("button", { name: "Duplicate" }))
    expect((updatedConfig(onUpdateNode).models as unknown[]).at(-1)).toEqual(
      { id: expect.stringMatching(/^m[0-9a-f]{8}$/), name: "short list copy", algorithm: "tboost", feature_columns: ["age"] },
    )

    fireEvent.click(within(models).getByRole("button", { name: "t-boost settings" }))
    fireEvent.click(within(models).getByRole("button", { name: "Rename" }))
    const dialog = screen.getByRole("dialog", { name: "Rename t-boost" })
    // A name another model has is refused, and the dialog stays.
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Model name" }), { target: { value: "short list" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }))
    expect(within(dialog).getByRole("alert")).toHaveTextContent("A model is already named short list.")
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Model name" }), { target: { value: "wide" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }))
    expect(screen.queryByRole("dialog")).toBeNull()
    const renamed = updatedConfig(onUpdateNode).models as Array<{ id: string; name: string }>
    expect(renamed.map((model) => [model.id, model.name])).toEqual([["m1", "wide"], ["m2", "short list"]])

    fireEvent.click(within(models).getByRole("button", { name: "short list" }))
    fireEvent.click(within(models).getByRole("button", { name: "short list settings" }))
    const updatesBeforeRemoval = onUpdateNode.mock.calls.length
    fireEvent.click(within(models).getByRole("button", { name: "Remove" }))
    expect(asked()).toMatchObject({ title: 'Remove model "short list"?', confirm: "Remove", danger: true })
    expect(onUpdateNode).toHaveBeenCalledTimes(updatesBeforeRemoval)
    await answerQuestion(null)
    expect(onUpdateNode).toHaveBeenCalledTimes(updatesBeforeRemoval)
    expect(within(models).getByRole("button", { name: "short list" })).toBeInTheDocument()
    fireEvent.click(within(models).getByRole("button", { name: "Remove" }))
    await answerQuestion("confirm")
    expect(onUpdateNode).toHaveBeenCalledTimes(updatesBeforeRemoval + 1)
    const removed = updatedConfig(onUpdateNode).models as Array<{ id: string }>
    expect(removed.map((model) => model.id)).toEqual(["m1"])
  })

  it("says an interactive build made before models has none, and opens nothing of it", () => {
    const old = node("frequency", "modelling", { algorithm: "tboost", interactive_build: true, target: "claims" })
    renderView({ nodes: [quotes, old] })
    expect(within(screen.getByRole("main", { name: "Models" })).getByText("No models")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))

    expect(screen.getByRole("alert")).toHaveTextContent("frequency has no models.")
    expect(screen.queryByTestId("node-editor")).toBeNull()
  })

  it("offers no Remove for the last model and disables edits in expanded read-only controls", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const models = screen.getByRole("group", { name: "Models" })
    fireEvent.click(within(models).getByRole("button", { name: "t-boost settings" }))
    expect(within(models).queryByRole("button", { name: "Remove" })).toBeNull()
    cleanup()
    useModellingViewStore.setState({ modelId: null, chosenModel: null })

    renderView({ readOnly: true })
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const readOnlyModels = screen.getByRole("group", { name: "Models" })
    expect(within(readOnlyModels).getByRole("button", { name: "Add" })).toBeDisabled()
    fireEvent.click(within(readOnlyModels).getByRole("button", { name: "t-boost settings" }))
    expect(within(readOnlyModels).getByRole("button", { name: "t-boost settings" })).toHaveAttribute("aria-expanded", "true")
    expect(within(readOnlyModels).getByRole("checkbox", { name: "Show t-boost on the charts" })).toBeEnabled()
    expect(within(readOnlyModels).getByRole("button", { name: "Duplicate" })).toBeDisabled()
    expect(within(readOnlyModels).getByRole("button", { name: "Rename" })).toBeDisabled()
  })

  it("shows the latest run's t-boost tables in their own pane", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    fireEvent.click(within(screen.getByRole("tablist", { name: "Panes" })).getByRole("tab", { name: "Tables" }))
    expect(within(screen.getByRole("tabpanel", { name: "Tables" })).getByText("Not boosted yet")).toBeInTheDocument()

    act(() => useNodeResultsStore.setState({ trainResults: trained([ageRow], AGE_TABLES) }))
    expect(within(screen.getByRole("tabpanel", { name: "Tables" })).getByRole("table", { name: "Table for age" })).toBeInTheDocument()
  })

  it("explains the latest run by importance and PDP, its factor picked from the importance list, and shows its fit", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const panes = within(screen.getByRole("tablist", { name: "Panes" }))
    fireEvent.click(panes.getByRole("tab", { name: "Explain" }))
    expect(within(screen.getByRole("tabpanel", { name: "Explain" })).getByText("Not boosted yet")).toBeInTheDocument()

    const run = trained([ageRow])
    run[M1].result = makeTrainResult({
      ...run[M1].result,
      pdp_data: [
        { feature: "age", type: "numeric", grid: [{ value: 20, avg_prediction: 0.2 }, { value: 60, avg_prediction: 0.1 }] },
        { feature: "income", type: "numeric", grid: [{ value: 1, avg_prediction: 0.15 }, { value: 2, avg_prediction: 0.16 }] },
      ],
      double_lift: [{ decile: 1, actual: 0.1, predicted: 0.12, count: 12 }],
    })
    act(() => useNodeResultsStore.setState({ trainResults: run }))
    const explain = within(screen.getByRole("tabpanel", { name: "Explain" }))
    expect(explain.getByRole("region", { name: "Feature importance" })).toBeInTheDocument()
    fireEvent.click(explain.getByRole("button", { name: "PDP" }))
    fireEvent.click(explain.getByRole("button", { name: /income/ }))
    // Picking a factor on Explain keeps the pane, and the view's factor follows.
    expect(panes.getByRole("tab", { name: "Explain" })).toHaveAttribute("aria-selected", "true")
    expect(useModellingViewStore.getState().factor).toBe("income")

    fireEvent.click(panes.getByRole("tab", { name: "Fit" }))
    const fit = within(screen.getByRole("tabpanel", { name: "Fit" }))
    expect(fit.getByRole("region", { name: "Lift" })).toBeInTheDocument()
    expect(fit.queryByText("No lift data available")).not.toBeInTheDocument()
  })

  it("picks a staged model's stage with buttons on Tables, Explain and Fit, and keeps SHAP, PDP and lift with the applied stage", async () => {
    const snapshot = { metrics: { rmse: .2 }, feature_importance: [{ feature: "age", importance: 3 }], features: ["age"], cat_features: [],
      diagnostics_set: "validation", ave_per_feature: [ageRow], ave_left_out: [], tboost_tables: null,
      loss_history: [], fit_evidence: null, diagnostics_errors: [], recipe: { schema_version: 1, operations: [] } }
    // The compact state: lift and importance, but no PDP until the panels read arrives.
    const response = makeTrainResult({
      diagnostics_view: "compact",
      double_lift: [{ decile: 1, actual: 0.1, predicted: 0.12, count: 12 }],
      staged: { schema_version: 2, selected_stage: "banded", diagnostics_basis: "partition", reference_fit: "validation-0",
        configuration: { applied_recipe: { schema_version: 1, operations: [] } }, evaluation_identity: "e".repeat(64), fit_config_digest: "f".repeat(64),
        stages: { full: snapshot, pruned: snapshot, banded: snapshot }, pruning_path: null, path_unavailable_reason: null, recommendation: null },
    })
    const record = { revision: "a".repeat(32), build_id: "b".repeat(32), parent_revision: null, size: 1, sha256: "c".repeat(64), config_digest: "f".repeat(64),
      result: { graph_digest: "d".repeat(64), response } }
    const key = JSON.stringify([documentExecutionKey(useDocumentStatusStore.getState()), M1])
    const state = { schema_version: 1 as const, next_preview: null, checkpoints: [], current: record, preview: null, final: null, job: null, stale_reason: null }
    useStagedViews.getState().update(key, { state })
    // Tables reads its result at once; the panels read is held back.
    let answer!: (value: Awaited<ReturnType<typeof readStages>>) => void
    vi.mocked(readStages).mockImplementation((args) => args.diagnostics === "panels"
      ? new Promise((resolve) => { answer = resolve }) : Promise.resolve(state as Awaited<ReturnType<typeof readStages>>))
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    const panes = within(screen.getByRole("tablist", { name: "Panes" }))

    fireEvent.click(panes.getByRole("tab", { name: "Tables" }))
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    const tableStages = within(screen.getByRole("tablist", { name: "Table stage" }))
    expect(tableStages.getByRole("tab", { name: "Banded" })).toHaveAttribute("aria-selected", "true")
    expect(tableStages.getByRole("tab", { name: "Smoothed" })).toBeDisabled()

    fireEvent.click(panes.getByRole("tab", { name: "Explain" }))
    const explain = within(screen.getByRole("tabpanel", { name: "Explain" }))
    const explainStages = within(explain.getByRole("tablist", { name: "Explain stage" }))
    expect(explainStages.getByRole("tab", { name: "Banded" })).toHaveAttribute("aria-selected", "true")
    // Importance draws from the compact state while only SHAP and PDP are read.
    expect(explain.getByRole("region", { name: "Feature importance" })).toBeInTheDocument()
    expect(explain.getByText("Loading SHAP and partial dependence…")).toBeInTheDocument()
    expect(readStages).toHaveBeenCalledWith(expect.objectContaining({ diagnostics: "panels" }))
    await act(async () => answer({ ...state, current: { ...record, result: { ...record.result, response: {
      // A separate read parses its own stage snapshots.
      ...structuredClone(response), diagnostics_view: "panels",
      pdp_data: [{ feature: "age", type: "numeric", grid: [{ value: 20, avg_prediction: 0.2 }, { value: 60, avg_prediction: 0.1 }] }],
    } } } } as Awaited<ReturnType<typeof readStages>>))
    expect(explain.getByRole("button", { name: "PDP" })).toBeInTheDocument()
    // Another stage keeps its importance, never the applied stage's PDP.
    fireEvent.click(explainStages.getByRole("tab", { name: "Pruned" }))
    expect(explain.queryByRole("button", { name: "PDP" })).not.toBeInTheDocument()
    expect(explain.getByText("SHAP and partial dependence are kept for the applied stage, Banded, only")).toBeInTheDocument()
    expect(explain.getByRole("region", { name: "Feature importance" })).toBeInTheDocument()

    fireEvent.click(panes.getByRole("tab", { name: "Fit" }))
    const fit = within(screen.getByRole("tabpanel", { name: "Fit" }))
    const fitStages = within(fit.getByRole("tablist", { name: "Fit stage" }))
    expect(fitStages.getByRole("tab", { name: "Banded" })).toHaveAttribute("aria-selected", "true")
    expect(fitStages.getByRole("tab", { name: "Pruned" })).toBeDisabled()
    expect(fit.queryByText("No lift data available")).not.toBeInTheDocument()
  })

  it("opens a toolbar panel at the right, in the settings' place, one at a time", async () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    expect(screen.getByTestId("node-editor")).toBeInTheDocument()

    act(() => useUIStore.setState({ assistantOpen: true }))
    expect(await screen.findByTestId("assistant-panel")).toHaveAttribute("data-read-only", "false")
    expect(screen.getByRole("complementary", { name: "Assistant" })).toBeInTheDocument()
    // The model's settings are hidden, not gone: its editor keeps the toolbar's Train.
    expect(screen.getByTestId("node-editor").closest("aside")).not.toBeVisible()
    // The models and chart panes stay; Features is hidden with the other settings.
    expect(screen.getByRole("navigation", { name: "Models" })).toBeInTheDocument()
    expect(screen.queryByRole("navigation", { name: "Factors" })).not.toBeInTheDocument()
    expect(screen.getByRole("tablist", { name: "Panes" })).toBeInTheDocument()

    act(() => useUIStore.setState({ gitOpen: true }))
    expect(await screen.findByTestId("git-panel")).toBeInTheDocument()
    expect(screen.queryByTestId("assistant-panel")).toBeNull()

    act(() => useUIStore.setState({ gitOpen: false, assistantOpen: false, filesPlace: "pipeline" }))
    expect(await screen.findByTestId("files-panel")).toHaveAttribute("data-place", "pipeline")

    act(() => useUIStore.setState({ filesPlace: null }))
    expect(screen.getByTestId("node-editor").closest("aside")).toBeVisible()
  })

  it("opens a toolbar panel beside the list of models too", async () => {
    renderView()
    act(() => useUIStore.setState({ gitOpen: true }))

    expect(await screen.findByTestId("git-panel")).toBeInTheDocument()
    expect(screen.getByRole("main", { name: "Models" })).toBeInTheDocument()
  })

  it("goes back to the list, and shows it when the open model has left the pipeline", () => {
    renderView()
    fireEvent.click(screen.getByRole("button", { name: "frequency" }))
    act(() => useModellingViewStore.getState().closeModel())
    expect(screen.getByRole("main", { name: "Models" })).toBeInTheDocument()

    cleanup()
    useModellingViewStore.setState({ modelId: "deleted_model" })
    renderView()
    expect(screen.getByRole("main", { name: "Models" })).toBeInTheDocument()
  })
})
